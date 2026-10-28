"""Read models. Each function shapes its output for the caller's role, so private
fields never leave the server for an unauthorized audience."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.allocation import LABELS
from app.errors import Forbidden, InvalidState, NotFound
from app.models import (
    AllocationRequest,
    AllocationResult,
    AuditEntry,
    InventoryLot,
    Organization,
    Round,
    RoundLot,
    RoundParticipant,
    RoundSnapshot,
    User,
)
from app.schemas import (
    AllocationOut,
    AuditOut,
    LotOut,
    LotOutcome,
    OrganizationOut,
    ParticipantOut,
    PublicLot,
    PublicRound,
    RequestOut,
    ResultLine,
    RoundDetail,
    RoundLotOut,
    RoundSummary,
    SnapshotOut,
    UserOut,
)
from app.services.allocation import Computation
from app.services.common import Actor

VISIBLE_LOT_STATUSES = ("reserved", "consumed")


def _visible_rounds(actor: Actor) -> Select[Round]:
    stmt = select(Round)
    if not actor.is_admin:
        stmt = stmt.where(Round.status != "draft")
    return stmt


async def _now(db: AsyncSession) -> datetime:
    value: datetime | None = await db.scalar(select(func.now()))
    assert value is not None
    return value


async def _get_visible_round(db: AsyncSession, actor: Actor, round_id: int) -> Round:
    rnd = await db.scalar(
        _visible_rounds(actor).where(Round.id == round_id).execution_options(populate_existing=True)
    )
    if rnd is None:
        raise NotFound("Round not found")
    return rnd


async def _participant(db: AsyncSession, round_id: int, org_id: int) -> RoundParticipant | None:
    return await db.get(RoundParticipant, (round_id, org_id), populate_existing=True)


def _summary(
    rnd: Round,
    now: datetime,
    actor: Actor,
    *,
    lot_count: int,
    total: int,
    org_version: int | None = None,
    my_requests: int | None = None,
) -> dict[str, Any]:
    return {
        "id": rnd.id,
        "name": rnd.name,
        "description": rnd.description,
        "status": rnd.status,
        "strategy": rnd.strategy,
        "strategy_label": LABELS[rnd.strategy],
        "request_deadline": rnd.request_deadline,
        "deadline_passed": rnd.request_deadline <= now,
        "public_version": rnd.public_version,
        "version": rnd.version if actor.is_admin else None,
        "org_version": org_version,
        "lot_count": lot_count,
        "total_quantity": total,
        "my_request_count": my_requests,
        "opened_at": rnd.opened_at,
        "closed_at": rnd.closed_at,
        "allocated_at": rnd.allocated_at,
        "cancelled_at": rnd.cancelled_at,
    }


async def list_rounds(db: AsyncSession, actor: Actor) -> list[RoundSummary]:
    if actor.role == "viewer":
        raise Forbidden("Viewers can see public round summaries only")
    rounds = (await db.scalars(_visible_rounds(actor).order_by(Round.id.desc()))).all()
    ids = [r.id for r in rounds]
    lot_stats: dict[int, tuple[int, int]] = {}
    if ids:
        for rid, count, total in await db.execute(
            select(RoundLot.round_id, func.count(), func.coalesce(func.sum(RoundLot.quantity), 0))
            .where(RoundLot.round_id.in_(ids), RoundLot.status.in_(VISIBLE_LOT_STATUSES))
            .group_by(RoundLot.round_id)
        ):
            lot_stats[rid] = (count, total)
    org_versions: dict[int, int] = {}
    my_counts: dict[int, int] = {}
    if actor.organization_id is not None and ids:
        for rid, version in await db.execute(
            select(RoundParticipant.round_id, RoundParticipant.version).where(
                RoundParticipant.organization_id == actor.organization_id,
                RoundParticipant.round_id.in_(ids),
            )
        ):
            org_versions[rid] = version
        for rid, count in await db.execute(
            select(AllocationRequest.round_id, func.count())
            .where(
                AllocationRequest.organization_id == actor.organization_id,
                AllocationRequest.status == "active",
                AllocationRequest.round_id.in_(ids),
            )
            .group_by(AllocationRequest.round_id)
        ):
            my_counts[rid] = count
    now = await _now(db)
    member = actor.role == "member"
    return [
        RoundSummary(
            **_summary(
                r,
                now,
                actor,
                lot_count=lot_stats.get(r.id, (0, 0))[0],
                total=lot_stats.get(r.id, (0, 0))[1],
                org_version=org_versions.get(r.id, 0) if member else None,
                my_requests=my_counts.get(r.id, 0) if member else None,
            )
        )
        for r in rounds
    ]


async def round_detail(db: AsyncSession, actor: Actor, round_id: int) -> RoundDetail:
    if actor.role == "viewer":
        raise Forbidden("Viewers can see public round summaries only")
    rnd = await _get_visible_round(db, actor, round_id)
    lots = [
        RoundLotOut(
            lot_id=lot.id,
            name=lot.name,
            description=lot.description,
            unit=lot.unit,
            best_before=lot.best_before,
            quantity=rl.quantity,
            status=rl.status,
            allocated_quantity=rl.allocated_quantity,
        )
        for rl, lot in await db.execute(
            select(RoundLot, InventoryLot)
            .join(InventoryLot, InventoryLot.id == RoundLot.lot_id)
            .where(RoundLot.round_id == round_id, RoundLot.status.in_(VISIBLE_LOT_STATUSES))
            .order_by(InventoryLot.name, InventoryLot.id)
        )
    ]
    req_stmt = (
        select(AllocationRequest, Organization.name)
        .join(Organization, Organization.id == AllocationRequest.organization_id)
        .where(AllocationRequest.round_id == round_id)
        .order_by(AllocationRequest.accepted_seq)
    )
    if not actor.is_admin:
        req_stmt = req_stmt.where(AllocationRequest.organization_id == actor.organization_id)
    requests = [
        RequestOut(
            id=r.id,
            round_id=r.round_id,
            lot_id=r.lot_id,
            organization_id=r.organization_id,
            organization_name=name,
            quantity=r.quantity,
            status=r.status,
            version=r.version,
            accepted_seq=r.accepted_seq if actor.is_admin else None,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r, name in await db.execute(req_stmt.execution_options(populate_existing=True))
    ]
    participants_rows = (
        await db.execute(
            select(RoundParticipant, Organization)
            .join(Organization, Organization.id == RoundParticipant.organization_id)
            .where(RoundParticipant.round_id == round_id)
            .order_by(Organization.name)
            .execution_options(populate_existing=True)
        )
    ).all()

    def to_out(p: RoundParticipant, org: Organization) -> ParticipantOut:
        return ParticipantOut(
            organization_id=org.id,
            organization_name=org.name,
            organization_active=org.is_active,
            eligible=p.eligible,
            weight=p.weight,
            priority=p.priority,
            version=p.version,
        )

    participation = None
    org_version = None
    if actor.role == "member":
        own = [to_out(p, o) for p, o in participants_rows if o.id == actor.organization_id]
        participation = own[0] if own else None
        org_version = participation.version if participation else 0
    snapshot = None
    snap = await db.get(RoundSnapshot, round_id, populate_existing=True)
    if snap is not None:
        snapshot = SnapshotOut(
            strategy=snap.strategy,
            strategy_version=snap.strategy_version,
            input_hash=snap.input_hash,
            result_hash=snap.result_hash,
            created_at=snap.created_at,
            finalized_at=snap.finalized_at,
        )
    now = await _now(db)
    active = [r for r in requests if r.status == "active"]
    return RoundDetail(
        **_summary(
            rnd,
            now,
            actor,
            lot_count=len(lots),
            total=sum(lot.quantity for lot in lots),
            org_version=org_version,
            my_requests=len(active) if actor.role == "member" else None,
        ),
        lots=lots,
        requests=requests,
        participation=participation,
        participants=[to_out(p, o) for p, o in participants_rows] if actor.is_admin else None,
        snapshot=snapshot,
    )


async def org_names(db: AsyncSession) -> dict[int, str]:
    return {i: n for i, n in await db.execute(select(Organization.id, Organization.name))}


def computation_out(
    comp: Computation, round_id: int, frozen: str, names: dict[int, str], *, is_preview: bool
) -> AllocationOut:
    return AllocationOut(
        round_id=round_id,
        is_preview=is_preview,
        strategy=comp.strategy,
        strategy_label=LABELS[comp.strategy],
        strategy_version=comp.strategy_version,
        frozen_strategy=frozen,
        input_hash=comp.input_hash,
        result_hash=comp.result_hash,
        lots=[
            LotOutcome(
                lot_id=lot.lot_id,
                name=comp.lot_meta[lot.lot_id]["name"],
                unit=comp.lot_meta[lot.lot_id]["unit"],
                supply=lot.supply,
                allocated_total=lot.allocated_total,
                unallocated=lot.unallocated,
                lines=[
                    ResultLine(
                        request_id=line.request_id,
                        organization_id=line.organization_id,
                        organization_name=names.get(line.organization_id),
                        requested=line.requested,
                        allocated=line.allocated,
                        explanation=line.explanation,
                        details=line.details,
                    )
                    for line in lot.lines
                ],
            )
            for lot in comp.lots
        ],
    )


async def results(db: AsyncSession, actor: Actor, round_id: int) -> AllocationOut:
    """Persisted (final) results. Members see only their organization's lines."""
    if actor.role == "viewer":
        raise Forbidden("Viewers can see public round summaries only")
    rnd = await _get_visible_round(db, actor, round_id)
    if rnd.status != "allocated":
        raise InvalidState(
            "Results are available after the round is finalized",
            code="NOT_FINALIZED",
            details={"status": rnd.status},
        )
    snap = await db.get(RoundSnapshot, round_id)
    assert snap is not None and snap.result_hash is not None
    names = await org_names(db)
    stmt = select(AllocationResult).where(AllocationResult.round_id == round_id)
    if not actor.is_admin:
        stmt = stmt.where(AllocationResult.organization_id == actor.organization_id)
    lines: dict[int, list[ResultLine]] = defaultdict(list)
    seq = {
        r["request_id"]: r["accepted_seq"] for lot in snap.input["lots"] for r in lot["requests"]
    }
    for row in sorted(await db.scalars(stmt), key=lambda r: seq.get(r.request_id, 0)):
        lines[row.lot_id].append(
            ResultLine(
                request_id=row.request_id,
                organization_id=row.organization_id,
                organization_name=names.get(row.organization_id),
                requested=row.requested_quantity,
                allocated=row.allocated_quantity,
                explanation=row.explanation,
                details=row.details,
            )
        )
    rls = {
        rl.lot_id: rl
        for rl in await db.scalars(
            select(RoundLot).where(RoundLot.round_id == round_id, RoundLot.status == "consumed")
        )
    }
    lots = []
    for lot in snap.input["lots"]:
        rl = rls[lot["lot_id"]]
        allocated = rl.allocated_quantity or 0
        lots.append(
            LotOutcome(
                lot_id=lot["lot_id"],
                name=lot["name"],
                unit=lot["unit"],
                supply=rl.quantity,
                allocated_total=allocated,
                unallocated=rl.quantity - allocated,
                lines=lines.get(lot["lot_id"], []),
            )
        )
    return AllocationOut(
        round_id=round_id,
        is_preview=False,
        strategy=snap.strategy,
        strategy_label=LABELS[snap.strategy],
        strategy_version=snap.strategy_version,
        frozen_strategy=snap.strategy,
        input_hash=snap.input_hash,
        result_hash=snap.result_hash,
        lots=lots,
    )


async def public_rounds(db: AsyncSession) -> list[PublicRound]:
    """Aggregates only: no organization names, requests, weights or priorities."""
    rounds = (
        await db.scalars(select(Round).where(Round.status != "draft").order_by(Round.id.desc()))
    ).all()
    ids = [r.id for r in rounds]
    lots: dict[int, list[PublicLot]] = defaultdict(list)
    served: dict[tuple[int, int], int] = {}
    if ids:
        for rid, lot_id, count in await db.execute(
            select(
                AllocationResult.round_id,
                AllocationResult.lot_id,
                func.count(func.distinct(AllocationResult.organization_id)),
            )
            .where(AllocationResult.round_id.in_(ids), AllocationResult.allocated_quantity > 0)
            .group_by(AllocationResult.round_id, AllocationResult.lot_id)
        ):
            served[(rid, lot_id)] = count
        for rl, lot in await db.execute(
            select(RoundLot, InventoryLot)
            .join(InventoryLot, InventoryLot.id == RoundLot.lot_id)
            .where(RoundLot.round_id.in_(ids), RoundLot.status.in_(VISIBLE_LOT_STATUSES))
            .order_by(InventoryLot.name)
        ):
            done = rl.status == "consumed"
            lots[rl.round_id].append(
                PublicLot(
                    name=lot.name,
                    unit=lot.unit,
                    quantity=rl.quantity,
                    allocated_total=rl.allocated_quantity if done else None,
                    organizations_served=served.get((rl.round_id, rl.lot_id), 0) if done else None,
                )
            )
    return [
        PublicRound(
            id=r.id,
            name=r.name,
            description=r.description,
            status=r.status,
            strategy_label=LABELS[r.strategy],
            request_deadline=r.request_deadline,
            public_version=r.public_version,
            lots=lots.get(r.id, []),
        )
        for r in rounds
    ]


async def versions(db: AsyncSession, actor: Actor) -> list[dict[str, int]]:
    """Current version counters of every round visible to the caller, in the
    counter(s) that caller's notifications use."""
    if actor.is_admin:
        return [
            {"round_id": i, "version": v, "public_version": p}
            for i, v, p in await db.execute(select(Round.id, Round.version, Round.public_version))
        ]
    rows = await db.execute(select(Round.id, Round.public_version).where(Round.status != "draft"))
    out = [{"round_id": i, "public_version": p} for i, p in rows]
    if actor.role == "member" and actor.organization_id is not None:
        org = {
            i: v
            for i, v in await db.execute(
                select(RoundParticipant.round_id, RoundParticipant.version).where(
                    RoundParticipant.organization_id == actor.organization_id
                )
            )
        }
        for item in out:
            item["org_version"] = org.get(item["round_id"], 0)
    return out


async def list_lots(db: AsyncSession) -> list[LotOut]:
    reserved_by = {
        lot_id: rid
        for lot_id, rid in await db.execute(
            select(RoundLot.lot_id, RoundLot.round_id).where(RoundLot.status == "reserved")
        )
    }
    return [
        lot_out(lot, reserved_by.get(lot.id))
        for lot in await db.scalars(
            select(InventoryLot)
            .order_by(InventoryLot.name, InventoryLot.id)
            .execution_options(populate_existing=True)
        )
    ]


async def get_lot(db: AsyncSession, lot_id: int) -> LotOut:
    lot = await db.get(InventoryLot, lot_id, populate_existing=True)
    if lot is None:
        raise NotFound("Inventory lot not found")
    reserved_by = await db.scalar(
        select(RoundLot.round_id).where(RoundLot.lot_id == lot_id, RoundLot.status == "reserved")
    )
    return lot_out(lot, reserved_by)


def lot_out(lot: InventoryLot, reserved_by: int | None) -> LotOut:
    return LotOut(
        id=lot.id,
        name=lot.name,
        description=lot.description,
        unit=lot.unit,
        quantity_on_hand=lot.quantity_on_hand,
        quantity_reserved=lot.quantity_reserved,
        quantity_available=lot.quantity_on_hand - lot.quantity_reserved,
        best_before=lot.best_before,
        reserved_by_round_id=reserved_by,
        version=lot.version,
    )


def org_out(org: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        name=org.name,
        default_weight=org.default_weight,
        default_priority=org.default_priority,
        region=org.region,
        is_active=org.is_active,
        version=org.version,
    )


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        organization_id=user.organization_id,
        is_active=user.is_active,
        version=user.version,
    )


async def list_organizations(db: AsyncSession) -> list[OrganizationOut]:
    return [org_out(o) for o in await db.scalars(select(Organization).order_by(Organization.name))]


async def list_users(db: AsyncSession) -> list[UserOut]:
    return [user_out(u) for u in await db.scalars(select(User).order_by(User.email))]


async def list_audit(
    db: AsyncSession,
    *,
    round_id: int | None,
    target_type: str | None,
    action: str | None,
    before_id: int | None,
    limit: int,
) -> list[AuditOut]:
    stmt = (
        select(AuditEntry, User.email, User.display_name)
        .outerjoin(User, User.id == AuditEntry.actor_user_id)
        .order_by(AuditEntry.id.desc())
        .limit(limit)
    )
    if round_id is not None:
        stmt = stmt.where(AuditEntry.round_id == round_id)
    if target_type:
        stmt = stmt.where(AuditEntry.target_type == target_type)
    if action:
        stmt = stmt.where(AuditEntry.action == action)
    if before_id is not None:
        stmt = stmt.where(AuditEntry.id < before_id)
    return [
        AuditOut(
            id=a.id,
            created_at=a.created_at,
            actor_user_id=a.actor_user_id,
            actor_email=email,
            actor_name=actor_name,
            action=a.action,
            target_type=a.target_type,
            target_id=a.target_id,
            round_id=a.round_id,
            before=a.before,
            after=a.after,
            reason=a.reason,
            request_id=a.request_id,
        )
        for a, email, actor_name in await db.execute(stmt)
    ]
