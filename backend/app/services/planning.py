"""Planning workspace: preferences, documents and notes, and sample plans.

Members plan for their own food bank. Admins can open any food bank's workspace to
help or review; viewers have no access. Plans are computed on request from current
data (app/planning/planner.py) and are never stored or submitted automatically.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import (
    AllocationRequest,
    AllocationResult,
    InventoryLot,
    Organization,
    PlanningDocument,
    PlanningProfile,
    Round,
    RoundLot,
    RoundParticipant,
    User,
)
from app.planning import planner
from app.planning.catalog import CATEGORIES, Category, classify, storage_of
from app.planning.parsing import analyze_file, analyze_text
from app.schemas import (
    DocumentDetail,
    DocumentOut,
    NeedOut,
    PlansOut,
    Preferences,
    ProfileOut,
    SuggestedProfile,
)
from app.services.common import Actor, audit, db_now

MAX_DOCUMENTS = 200
_CONTENT_TYPE = re.compile(r"^[\w.+-]+/[\w.+-]+$")
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f]")


async def resolve_org(db: AsyncSession, actor: Actor, organization_id: int | None) -> Organization:
    if actor.role == "viewer":
        raise Forbidden("Planning is available to food bank staff and administrators")
    if actor.role == "member":
        if organization_id not in (None, actor.organization_id):
            raise Forbidden("You can only plan for your own food bank")
        organization_id = actor.organization_id
    elif organization_id is None:
        raise ValidationFailed("Choose a food bank", details={"field": "organization_id"})
    org = await db.get(Organization, organization_id)
    if org is None:
        raise NotFound("Organization not found")
    return org


# ---------------------------------------------------------------- preferences


async def get_profile(db: AsyncSession, org: Organization) -> ProfileOut:
    row = await db.execute(
        select(PlanningProfile, User.display_name)
        .outerjoin(User, User.id == PlanningProfile.updated_by)
        .where(PlanningProfile.organization_id == org.id)
        .execution_options(populate_existing=True)
    )
    found = row.first()
    if found is None:
        return ProfileOut(
            organization_id=org.id,
            organization_name=org.name,
            preferences=Preferences(),
            version=0,
            updated_at=None,
            updated_by_name=None,
        )
    profile, name = found
    return ProfileOut(
        organization_id=org.id,
        organization_name=org.name,
        preferences=Preferences.model_validate(profile.preferences),
        version=profile.version,
        updated_at=profile.updated_at,
        updated_by_name=name,
    )


async def save_profile(
    db: AsyncSession, actor: Actor, org: Organization, *, version: int, preferences: Preferences
) -> None:
    """Optimistic concurrency: ``version`` is what the editor loaded (0 = none yet)."""
    data = _clean(preferences).model_dump()
    profile = await db.scalar(
        select(PlanningProfile)
        .where(PlanningProfile.organization_id == org.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = profile.version if profile else 0
    if version != current:
        raise _stale(current)
    before = profile.preferences if profile else None
    if profile is None:
        inserted = await db.scalar(
            pg_insert(PlanningProfile)
            .values(organization_id=org.id, preferences=data, version=1, updated_by=actor.user_id)
            .on_conflict_do_nothing()
            .returning(PlanningProfile.organization_id)
        )
        if inserted is None:  # another editor created it first
            raise _stale(1)
    else:
        profile.preferences = data
        profile.version += 1
        profile.updated_by = actor.user_id
        profile.updated_at = await db_now(db)
    audit(
        db,
        actor,
        "planning.preferences_update",
        "organization",
        org.id,
        before=before,
        after=data,
    )


def _clean(prefs: Preferences) -> Preferences:
    """Drop duplicates and categories that are both preferred and avoided."""
    priorities = list(dict.fromkeys(prefs.priority_categories))
    avoid = [c for c in dict.fromkeys(prefs.avoid_categories) if c not in priorities]
    return prefs.model_copy(update={"priority_categories": priorities, "avoid_categories": avoid})


def _stale(current: int) -> Conflict:
    return Conflict(
        "These preferences were changed by someone else. Reload and try again.",
        code="VERSION_CONFLICT",
        details={"current_version": current},
    )


# ---------------------------------------------------------------- documents


async def list_documents(db: AsyncSession, org: Organization) -> list[DocumentOut]:
    rows = await db.execute(
        select(PlanningDocument, User.display_name)
        .outerjoin(User, User.id == PlanningDocument.created_by)
        .where(PlanningDocument.organization_id == org.id)
        .order_by(PlanningDocument.id.desc())
    )
    return [_document_out(doc, name) for doc, name in rows]


def _document_out(doc: PlanningDocument, created_by_name: str | None) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        kind=doc.kind,
        title=doc.title,
        filename=doc.filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
        created_at=doc.created_at,
        created_by_name=created_by_name,
        analysis=doc.analysis,
    )


async def get_document(db: AsyncSession, org: Organization, document_id: int) -> PlanningDocument:
    doc = await db.get(PlanningDocument, document_id)
    if doc is None or doc.organization_id != org.id:
        raise NotFound("Document not found")
    return doc


async def document_detail(db: AsyncSession, org: Organization, document_id: int) -> DocumentDetail:
    doc = await get_document(db, org, document_id)
    name = await db.scalar(select(User.display_name).where(User.id == doc.created_by))
    return DocumentDetail(**_document_out(doc, name).model_dump(), text=doc.text)


async def document_content(
    db: AsyncSession, org: Organization, document_id: int
) -> tuple[PlanningDocument, bytes]:
    doc = await get_document(db, org, document_id)
    content = await db.scalar(
        select(PlanningDocument.content).where(PlanningDocument.id == document_id)
    )
    if content is None:
        raise NotFound("This note has no file to download")
    return doc, content


def safe_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    base = _UNSAFE_NAME.sub("", base).strip().lstrip(".")
    return base[:255] or "upload"


async def _check_quota(db: AsyncSession, org: Organization) -> None:
    count = await db.scalar(
        select(func.count())
        .select_from(PlanningDocument)
        .where(PlanningDocument.organization_id == org.id)
    )
    if (count or 0) >= MAX_DOCUMENTS:
        raise ValidationFailed(
            f"A food bank can keep at most {MAX_DOCUMENTS} documents. Delete some first."
        )


async def upload_document(
    db: AsyncSession,
    actor: Actor,
    org: Organization,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> DocumentOut:
    if not data:
        raise ValidationFailed("The file is empty")
    await _check_quota(db, org)
    name = safe_filename(filename)
    # Parsing a large workbook is CPU-bound; keep it off the event loop.
    analysis = await asyncio.to_thread(analyze_file, name, data)
    ctype = (content_type or "").split(";")[0].strip().lower()
    doc = PlanningDocument(
        organization_id=org.id,
        kind="upload",
        title=name[:200],
        filename=name,
        content_type=ctype if _CONTENT_TYPE.match(ctype) else "application/octet-stream",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        content=data,
        text=analysis.text,
        analysis=analysis.as_dict(),
        created_by=actor.user_id,
    )
    db.add(doc)
    await db.flush()
    audit(
        db,
        actor,
        "planning.document_upload",
        "planning_document",
        doc.id,
        after={
            "organization_id": org.id,
            "filename": name,
            "size_bytes": doc.size_bytes,
            "sha256": doc.sha256,
            "format": analysis.format,
        },
    )
    await db.refresh(doc, ["created_at"])
    return _document_out(doc, await _name(db, actor.user_id))


async def add_note(
    db: AsyncSession, actor: Actor, org: Organization, *, title: str, body: str
) -> DocumentOut:
    await _check_quota(db, org)
    analysis = analyze_text(body, "markdown")
    doc = PlanningDocument(
        organization_id=org.id,
        kind="note",
        title=title,
        size_bytes=len(body.encode()),
        text=body,
        analysis=analysis.as_dict() | {"format": "note"},
        created_by=actor.user_id,
    )
    db.add(doc)
    await db.flush()
    audit(
        db,
        actor,
        "planning.note_create",
        "planning_document",
        doc.id,
        after={"organization_id": org.id, "title": title},
    )
    await db.refresh(doc, ["created_at"])
    return _document_out(doc, await _name(db, actor.user_id))


async def delete_document(
    db: AsyncSession, actor: Actor, org: Organization, document_id: int
) -> None:
    doc = await get_document(db, org, document_id)
    audit(
        db,
        actor,
        "planning.document_delete",
        "planning_document",
        doc.id,
        before={"organization_id": org.id, "title": doc.title, "kind": doc.kind},
    )
    await db.delete(doc)


async def _name(db: AsyncSession, user_id: int) -> str | None:
    return await db.scalar(select(User.display_name).where(User.id == user_id))


# ---------------------------------------------------------------- plans


async def _stated_needs(db: AsyncSession, org: Organization) -> list[planner.StatedNeed]:
    rows = await db.execute(
        select(PlanningDocument.title, PlanningDocument.analysis)
        .where(PlanningDocument.organization_id == org.id)
        .order_by(PlanningDocument.id.desc())
    )
    needs: list[planner.StatedNeed] = []
    seen: set[tuple[str, int | None]] = set()
    for title, analysis in rows:
        for n in analysis.get("needs", []):
            category = n.get("category")
            key = (str(n.get("item", "")).lower(), n.get("quantity"))
            if key in seen or category not in CATEGORIES:
                continue
            seen.add(key)
            needs.append(planner.StatedNeed(str(n["item"]), n.get("quantity"), category, title))
    return needs


async def _dietary_terms(db: AsyncSession, org: Organization) -> list[str]:
    terms: set[str] = set()
    for analysis in await db.scalars(
        select(PlanningDocument.analysis).where(PlanningDocument.organization_id == org.id)
    ):
        terms.update(analysis.get("dietary", []))
    return sorted(terms)


async def _open_options(
    db: AsyncSession, org: Organization, now: datetime
) -> tuple[list[planner.LotOption], list[str]]:
    rows = (
        await db.execute(
            select(Round, RoundLot, InventoryLot, RoundParticipant.eligible)
            .join(RoundLot, RoundLot.round_id == Round.id)
            .join(InventoryLot, InventoryLot.id == RoundLot.lot_id)
            .join(
                RoundParticipant,
                (RoundParticipant.round_id == Round.id)
                & (RoundParticipant.organization_id == org.id),
            )
            .where(
                Round.status == "open",
                Round.request_deadline > now,
                RoundLot.status == "reserved",
            )
            .order_by(Round.request_deadline, Round.id, InventoryLot.name, InventoryLot.id)
        )
    ).all()
    warnings = sorted(
        {f"{org.name} is not eligible for {rnd.name}" for rnd, _, _, ok in rows if not ok}
    )
    eligible = [(rnd, rl, lot) for rnd, rl, lot, ok in rows if ok]
    current: dict[tuple[int, int], AllocationRequest] = {}
    if eligible:
        for req in await db.scalars(
            select(AllocationRequest).where(
                AllocationRequest.organization_id == org.id,
                AllocationRequest.round_id.in_({rnd.id for rnd, _, _ in eligible}),
                AllocationRequest.status == "active",
            )
        ):
            current[(req.round_id, req.lot_id)] = req
    options = []
    for rnd, rl, lot in eligible:
        existing = current.get((rnd.id, lot.id))
        options.append(
            planner.LotOption(
                round_id=rnd.id,
                round_name=rnd.name,
                strategy=rnd.strategy,
                request_deadline=rnd.request_deadline,
                lot_id=lot.id,
                name=lot.name,
                description=lot.description,
                unit=lot.unit,
                offered=rl.quantity,
                best_before=lot.best_before,
                current_quantity=existing.quantity if existing else None,
                current_request_id=existing.id if existing else None,
                current_request_version=existing.version if existing else None,
            )
        )
    return options, warnings


async def _history(db: AsyncSession, org: Organization) -> list[planner.HistoryLine]:
    rows = await db.execute(
        select(
            Round.id,
            Round.status,
            InventoryLot.name,
            InventoryLot.description,
            RoundLot.quantity,
            AllocationRequest.quantity,
            AllocationResult.allocated_quantity,
        )
        .join(Round, Round.id == AllocationRequest.round_id)
        .join(
            RoundLot,
            (RoundLot.round_id == AllocationRequest.round_id)
            & (RoundLot.lot_id == AllocationRequest.lot_id),
        )
        .join(InventoryLot, InventoryLot.id == AllocationRequest.lot_id)
        .outerjoin(AllocationResult, AllocationResult.request_id == AllocationRequest.id)
        .where(
            AllocationRequest.organization_id == org.id,
            AllocationRequest.status == "active",
            Round.status.in_(("open", "closed", "allocated")),
        )
        .order_by(Round.id, AllocationRequest.id)
    )
    history = []
    for round_id, status, name, description, offered, requested, allocated in rows:
        category = classify(name, description)
        history.append(
            planner.HistoryLine(
                round_id=round_id,
                category=category,
                storage=storage_of(name, description, category),
                offered=offered,
                requested=requested,
                allocated=(allocated or 0) if status == "allocated" else None,
            )
        )
    return history


async def _offered_categories(db: AsyncSession, org: Organization) -> dict[Category, int]:
    """In how many allocated rounds the organization could request each category."""
    rows = await db.execute(
        select(Round.id, InventoryLot.name, InventoryLot.description)
        .join(RoundLot, RoundLot.round_id == Round.id)
        .join(InventoryLot, InventoryLot.id == RoundLot.lot_id)
        .join(
            RoundParticipant,
            (RoundParticipant.round_id == Round.id) & (RoundParticipant.organization_id == org.id),
        )
        .where(Round.status == "allocated", RoundParticipant.eligible)
    )
    seen: set[tuple[int, Category]] = {
        (round_id, classify(name, description)) for round_id, name, description in rows
    }
    counts: dict[Category, int] = {}
    for _, category in seen:
        counts[category] = counts.get(category, 0) + 1
    return counts


async def plans(db: AsyncSession, org: Organization) -> PlansOut:
    now = await db_now(db)
    profile = await get_profile(db, org)
    needs = await _stated_needs(db, org)
    options, warnings = await _open_options(db, org, now)
    insights = planner.insights_from(await _history(db, org))
    built = planner.build_plans(org.name, profile.preferences, options, needs, insights)
    for plan in built:
        plan.warnings = warnings + plan.warnings
    documents = await db.scalar(
        select(func.count())
        .select_from(PlanningDocument)
        .where(PlanningDocument.organization_id == org.id)
    )
    return PlansOut(
        organization_id=org.id,
        organization_name=org.name,
        generated_at=now,
        inputs={
            "preferences_version": profile.version,
            "documents": documents or 0,
            "needs": len(needs),
            "open_rounds": len({o.round_id for o in options}),
            "lots": len(options),
        },
        needs=[
            NeedOut(item=n.item, quantity=n.quantity, category=n.category, source=n.source)
            for n in needs
        ],
        insights=insights,
        plans=built,
    )


async def suggest(db: AsyncSession, org: Organization) -> SuggestedProfile:
    profile = await get_profile(db, org)
    history = await _history(db, org)
    preferences, rationale = planner.suggest_preferences(
        profile.preferences,
        history,
        await _offered_categories(db, org),
        await _dietary_terms(db, org),
    )
    return SuggestedProfile(
        preferences=preferences,
        rationale=rationale,
        based_on_rounds=len({h.round_id for h in history}),
    )
