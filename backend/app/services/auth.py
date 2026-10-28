"""Login, logout, session resolution and viewer self-registration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import Conflict, TooManyRequests, Unauthenticated
from app.models import LoginFailure, User, UserSession
from app.security import (
    hash_password,
    needs_rehash,
    new_csrf_token,
    new_session_token,
    token_digest,
    verify_password,
)
from app.services.common import Actor, Changes, Event, audit, db_now


@dataclass(frozen=True)
class LoginResult:
    token: str
    session: UserSession
    user: User


@dataclass(frozen=True)
class ResolvedSession:
    session: UserSession
    user: User

    @property
    def actor(self) -> Actor:
        return Actor(self.user.id, self.user.role, self.user.organization_id)


async def login(db: AsyncSession, settings: Settings, email: str, password: str) -> LoginResult:
    email = email.strip().lower()
    now = await db_now(db)
    window_start = now - timedelta(minutes=settings.login_failure_window_minutes)
    failures = await db.scalar(
        select(func.count())
        .select_from(LoginFailure)
        .where(LoginFailure.email == email, LoginFailure.attempted_at > window_start)
    )
    if (failures or 0) >= settings.login_max_failures:
        raise TooManyRequests(
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(settings.login_failure_window_minutes * 60)},
        )
    user = await db.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        db.add(LoginFailure(email=email))
        await db.commit()
        raise Unauthenticated("Email or password is incorrect", code="INVALID_CREDENTIALS")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    await db.execute(delete(LoginFailure).where(LoginFailure.email == email))
    token = new_session_token()
    session = UserSession(
        token_hash=token_digest(token),
        csrf_token=new_csrf_token(),
        user_id=user.id,
        expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
    )
    db.add(session)
    await db.flush()
    return LoginResult(token, session, user)


async def resolve(db: AsyncSession, token: str | None) -> ResolvedSession | None:
    if not token or len(token) > 128:
        return None
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == token_digest(token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > func.now(),
                User.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        return None
    return ResolvedSession(row[0], row[1])


async def logout(db: AsyncSession, resolved: ResolvedSession, changes: Changes) -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.id == resolved.session.id)
        .values(revoked_at=func.now())
    )
    changes.add(Event(f"user:{resolved.user.id}", "session.revoked"))


async def revoke_user_sessions(db: AsyncSession, user_id: int, changes: Changes) -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    changes.add(Event(f"user:{user_id}", "session.revoked"))


async def register_viewer(db: AsyncSession, email: str, display_name: str, password: str) -> User:
    """Public self-registration. Always creates a viewer; roles are admin-assigned."""
    user = User(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role="viewer",
        organization_id=None,
        is_active=True,
        version=1,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("An account with this email already exists", code="EMAIL_TAKEN") from None
    audit(
        db,
        Actor(user.id, "viewer"),
        "user.register",
        "user",
        user.id,
        after={"email": user.email, "role": "viewer"},
    )
    return user
