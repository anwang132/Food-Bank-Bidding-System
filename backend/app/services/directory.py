"""Organizations and user accounts (admin-managed)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import Organization, User
from app.security import hash_password
from app.services import auth, chat
from app.services.common import Actor, Changes, Event, audit, fields

ORG_FIELDS = ("name", "default_weight", "default_priority", "region", "is_active")
USER_FIELDS = ("email", "display_name", "role", "organization_id", "is_active")


def _check_version(current: int, expected: int, what: str) -> None:
    if current != expected:
        raise Conflict(
            f"This {what} was changed by someone else. Reload and try again.",
            code="VERSION_CONFLICT",
            details={"current_version": current},
        )


def _region(value: str | None) -> str | None:
    """Collapse whitespace; an empty region means none."""
    return (" ".join(value.split()) or None) if value else None


async def create_organization(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    *,
    name: str,
    default_weight: int,
    default_priority: int,
    region: str | None = None,
) -> Organization:
    org = Organization(
        name=name.strip(),
        default_weight=default_weight,
        default_priority=default_priority,
        region=_region(region),
        is_active=True,
        version=1,
    )
    db.add(org)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("An organization with this name already exists") from None
    if org.region:
        await chat.ensure_local_channel(db, org.region)
    audit(db, actor, "organization.create", "organization", org.id, after=fields(org, *ORG_FIELDS))
    changes.add(Event("admin", "organizations.changed"))
    return org


async def update_organization(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    org_id: int,
    *,
    version: int,
    reason: str | None,
    **updates: object,
) -> Organization:
    org = await db.scalar(
        select(Organization)
        .where(Organization.id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if org is None:
        raise NotFound("Organization not found")
    _check_version(org.version, version, "organization")
    before = fields(org, *ORG_FIELDS)
    region = updates.pop("region", None)
    for key, value in updates.items():
        if value is not None:
            setattr(org, key, value.strip() if isinstance(value, str) else value)
    if isinstance(region, str):
        org.region = _region(region)
    org.version += 1
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("An organization with this name already exists") from None
    if org.region:
        await chat.ensure_local_channel(db, org.region)
    audit(
        db,
        actor,
        "organization.update",
        "organization",
        org.id,
        before=before,
        after=fields(org, *ORG_FIELDS),
        reason=reason,
    )
    changes.add(Event("admin", "organizations.changed"))
    return org


async def _validate_membership(db: AsyncSession, role: str, organization_id: int | None) -> None:
    if role == "member":
        if organization_id is None:
            raise ValidationFailed("Members must belong to an organization")
        if await db.get(Organization, organization_id) is None:
            raise ValidationFailed("Organization does not exist")
    elif organization_id is not None:
        raise ValidationFailed("Only members can belong to an organization")


async def create_user(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    *,
    email: str,
    display_name: str,
    password: str,
    role: str,
    organization_id: int | None,
) -> User:
    await _validate_membership(db, role, organization_id)
    user = User(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=role,
        organization_id=organization_id,
        is_active=True,
        version=1,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("An account with this email already exists", code="EMAIL_TAKEN") from None
    audit(db, actor, "user.create", "user", user.id, after=fields(user, *USER_FIELDS))
    changes.add(Event("admin", "users.changed"))
    return user


async def update_user(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    user_id: int,
    *,
    version: int,
    reason: str | None,
    display_name: str | None = None,
    role: str | None = None,
    organization_id: int | None = None,
    clear_organization: bool = False,
    is_active: bool | None = None,
    password: str | None = None,
) -> User:
    user = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise NotFound("User not found")
    _check_version(user.version, version, "user")
    if user.id == actor.user_id and (
        (role is not None and role != user.role) or is_active is False
    ):
        raise Forbidden("You cannot change your own role or deactivate yourself")
    before = fields(user, *USER_FIELDS)
    new_role = role or user.role
    new_org = None if clear_organization else (organization_id or user.organization_id)
    if new_role != "member":
        new_org = None
    await _validate_membership(db, new_role, new_org)
    if display_name is not None:
        user.display_name = display_name.strip()
    user.role = new_role
    user.organization_id = new_org
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        user.password_hash = hash_password(password)
    user.version += 1
    await db.flush()
    after = fields(user, *USER_FIELDS)
    if password is not None:
        after["password_reset"] = True  # record the fact, never the value
    audit(db, actor, "user.update", "user", user.id, before=before, after=after, reason=reason)
    if not user.is_active or password is not None:
        await auth.revoke_user_sessions(db, user.id, changes)
    elif before["role"] != user.role or before["organization_id"] != user.organization_id:
        # Sessions stay valid (authorization is re-read per request), but live
        # connections must re-subscribe with the new audience.
        changes.add(Event(f"user:{user.id}", "session.changed"))
    changes.add(Event("admin", "users.changed"))
    return user
