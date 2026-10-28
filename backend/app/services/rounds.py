"""Round lifecycle, lot reservations and participant settings.

Every function here starts with ``lock_round`` and, when inventory is touched, then
locks lots in ascending id order (docs/DECISIONS.md D3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.allocation import CURRENT_VERSION
from app.errors import Conflict, InvalidState, NotFound, ValidationFailed
from app.models import (
    AllocationRequest,
    InventoryLot,
    Organization,
    Round,
    RoundLot,
    RoundParticipant,
    RoundSnapshot,
)
from app.services.common import (
    Actor,
    Changes,
    audit,
    db_now,
    fields,
    lock_lots,
    lock_round,
    require_status,
)

ROUND_FIELDS = ("name", "description", "strategy", "request_deadline", "status")
EDITABLE = ("draft", "open")


def _check_round_version(rnd: Round, expected: int | None) -> None:
    if expected is not None and rnd.version != expected:
        raise Conflict(
            "This round was changed by someone else. Reload and try again.",
            code="VERSION_CONFLICT",
            details={"current_version": rnd.version},
        )


async def create_round(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    *,
    name: str,
    description: str,
    strategy: str,
    request_deadline: datetime,
) -> Round:
    rnd = Round(
        name=name.strip(),
        description=description.strip(),
        status="draft",
        strategy=strategy,
        request_deadline=request_deadline,
        next_request_seq=1,
        version=1,
        public_version=1,
        created_by=actor.user_id,
    )
    db.add(rnd)
    await db.flush()
    orgs = (await db.scalars(select(Organization).where(Organization.is_active.is_(True)))).all()
    for org in orgs:
        db.add(
            RoundParticipant(
                round_id=rnd.id,
                organization_id=org.id,
                eligible=True,
                weight=org.default_weight,
                priority=org.default_priority,
                version=1,
            )
        )
    audit(
        db,
        actor,
        "round.create",
        "round",
        rnd.id,
        round_id=rnd.id,
        after=fields(rnd, *ROUND_FIELDS) | {"participants": len(orgs)},
    )
    changes.round_changed(rnd)
    return rnd


async def update_round(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    round_id: int,
    *,
    version: int,
    reason: str | None,
    name: str | None = None,
    description: str | None = None,
    strategy: str | None = None,
    request_deadline: datetime | None = None,
) -> Round:
    rnd = await lock_round(db, round_id)
    # Settings edits are checked against the public version, so unrelated member
    # activity (which bumps rounds.version) does not invalidate the admin's form.
    if rnd.public_version != version:
        raise Conflict(
            "This round's settings were changed by someone else. Reload and try again.",
            code="VERSION_CONFLICT",
            details={"current_version": rnd.public_version},
        )
    require_status(rnd, *EDITABLE, action="edit the round")
    before = fields(rnd, *ROUND_FIELDS)
    if name is not None:
        rnd.name = name.strip()
    if description is not None:
        rnd.description = description.strip()
    if strategy is not None:
        rnd.strategy = strategy
    if request_deadline is not None:
        rnd.request_deadline = request_deadline
    audit(
        db,
        actor,
        "round.update",
        "round",
        rnd.id,
        round_id=rnd.id,
        before=before,
        after=fields(rnd, *ROUND_FIELDS),
        reason=reason,
    )
    changes.round_changed(rnd, public=True)
    return rnd


async def _active_round_lot(db: AsyncSession, round_id: int, lot_id: int) -> RoundLot:
    rl = await db.scalar(
        select(RoundLot)
        .where(RoundLot.round_id == round_id, RoundLot.lot_id == lot_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rl is None or rl.status != "reserved":
        raise NotFound("This lot is not part of the round")
    return rl


async def reserve_lot(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    round_id: int,
    *,
    lot_id: int,
    quantity: int,
) -> RoundLot:
    rnd = await lock_round(db, round_id)
    require_status(rnd, *EDITABLE, action="add inventory")
    lot = (await lock_lots(db, [lot_id]))[lot_id]
    other = await db.scalar(
        select(RoundLot.round_id).where(RoundLot.lot_id == lot_id, RoundLot.status == "reserved")
    )
    if other is not None:
        raise Conflict(
            f"This lot is already reserved by round {other}",
            code="LOT_ALREADY_RESERVED",
            details={"round_id": other},
        )
    available = lot.quantity_on_hand - lot.quantity_reserved
    if quantity > available:
        raise InvalidState(
            f"Only {available} {lot.unit} are available to reserve",
            code="INSUFFICIENT_STOCK",
            details={"available": available},
        )
    existing = await db.scalar(
        select(RoundLot)
        .where(RoundLot.round_id == round_id, RoundLot.lot_id == lot_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:  # previously released from this round; reuse the row
        existing.status, existing.quantity, existing.released_at = "reserved", quantity, None
        rl = existing
    else:
        rl = RoundLot(round_id=round_id, lot_id=lot_id, quantity=quantity, status="reserved")
        db.add(rl)
    lot.quantity_reserved += quantity
    lot.version += 1
    try:
        await db.flush()
    except IntegrityError:  # partial unique index: defence in depth behind the lot lock
        raise Conflict(
            "This lot is already reserved by another round", code="LOT_ALREADY_RESERVED"
        ) from None
    audit(
        db,
        actor,
        "round.lot_reserve",
        "round_lot",
        f"{round_id}:{lot_id}",
        round_id=round_id,
        after={"lot_id": lot_id, "quantity": quantity},
    )
    changes.round_changed(rnd, public=rnd.status != "draft")
    return rl


async def change_reservation(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    round_id: int,
    lot_id: int,
    *,
    quantity: int,
    reason: str | None,
) -> RoundLot:
    rnd = await lock_round(db, round_id)
    require_status(rnd, *EDITABLE, action="change reserved inventory")
    lot = (await lock_lots(db, [lot_id]))[lot_id]
    rl = await _active_round_lot(db, round_id, lot_id)
    available = lot.quantity_on_hand - lot.quantity_reserved + rl.quantity
    if quantity > available:
        raise InvalidState(
            f"Only {available} {lot.unit} can be reserved",
            code="INSUFFICIENT_STOCK",
            details={"available": available},
        )
    before = {"quantity": rl.quantity}
    lot.quantity_reserved += quantity - rl.quantity
    lot.version += 1
    rl.quantity = quantity
    audit(
        db,
        actor,
        "round.lot_change",
        "round_lot",
        f"{round_id}:{lot_id}",
        round_id=round_id,
        before=before,
        after={"quantity": quantity},
        reason=reason,
    )
    changes.round_changed(rnd, public=rnd.status != "draft")
    return rl


async def release_lot(
    db: AsyncSession, actor: Actor, changes: Changes, round_id: int, lot_id: int
) -> None:
    rnd = await lock_round(db, round_id)
    require_status(rnd, *EDITABLE, action="remove inventory")
    lot = (await lock_lots(db, [lot_id]))[lot_id]
    rl = await _active_round_lot(db, round_id, lot_id)
    active = await db.scalar(
        select(AllocationRequest.id)
        .where(
            AllocationRequest.round_id == round_id,
            AllocationRequest.lot_id == lot_id,
            AllocationRequest.status == "active",
        )
        .limit(1)
    )
    if active is not None:
        raise InvalidState(
            "Organizations have active requests for this lot; it cannot be "
            "removed while the round is open",
            code="LOT_HAS_REQUESTS",
        )
    lot.quantity_reserved -= rl.quantity
    lot.version += 1
    rl.status = "released"
    rl.released_at = await db_now(db)
    audit(
        db,
        actor,
        "round.lot_release",
        "round_lot",
        f"{round_id}:{lot_id}",
        round_id=round_id,
        before={"quantity": rl.quantity},
    )
    changes.round_changed(rnd, public=rnd.status != "draft")


async def set_participant(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    round_id: int,
    organization_id: int,
    *,
    eligible: bool,
    weight: int,
    priority: int,
    reason: str,
) -> RoundParticipant:
    rnd = await lock_round(db, round_id)
    require_status(rnd, *EDITABLE, action="change eligibility, weights or priorities")
    if await db.get(Organization, organization_id) is None:
        raise NotFound("Organization not found")
    p = await db.scalar(
        select(RoundParticipant)
        .where(
            RoundParticipant.round_id == round_id,
            RoundParticipant.organization_id == organization_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    before = fields(p, "eligible", "weight", "priority") if p else None
    if p is None:
        p = RoundParticipant(
            round_id=round_id,
            organization_id=organization_id,
            version=1,
            eligible=eligible,
            weight=weight,
            priority=priority,
        )
        db.add(p)
        await db.flush()
    else:
        p.eligible, p.weight, p.priority = eligible, weight, priority
    audit(
        db,
        actor,
        "round.participant_set",
        "participant",
        f"{round_id}:{organization_id}",
        round_id=round_id,
        before=before,
        after=fields(p, "eligible", "weight", "priority"),
        reason=reason,
    )
    changes.round_changed(rnd, participants=[p])
    return p


async def open_round(
    db: AsyncSession, actor: Actor, changes: Changes, round_id: int, *, version: int | None
) -> Round:
    rnd = await lock_round(db, round_id)
    _check_round_version(rnd, version)
    require_status(rnd, "draft", action="open the round")
    now = await db_now(db)
    if rnd.request_deadline <= now:
        raise ValidationFailed(
            "The request deadline must be in the future to open the round", code="DEADLINE_PASSED"
        )
    lots = await db.scalar(
        select(RoundLot.lot_id)
        .where(RoundLot.round_id == round_id, RoundLot.status == "reserved")
        .limit(1)
    )
    if lots is None:
        raise InvalidState("Add at least one inventory lot before opening", code="NO_LOTS")
    rnd.status, rnd.opened_at = "open", now
    audit(
        db,
        actor,
        "round.open",
        "round",
        rnd.id,
        round_id=rnd.id,
        before={"status": "draft"},
        after={"status": "open"},
    )
    changes.round_changed(rnd, public=True)
    return rnd


def canonical_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def build_snapshot_input(db: AsyncSession, rnd: Round) -> dict[str, Any]:
    """Frozen allocation input. Called with the round row locked, so no request,
    participant or reservation of this round can change concurrently."""
    lots = (
        await db.execute(
            select(RoundLot, InventoryLot)
            .join(InventoryLot, InventoryLot.id == RoundLot.lot_id)
            .where(RoundLot.round_id == rnd.id, RoundLot.status == "reserved")
            .order_by(RoundLot.lot_id)
        )
    ).all()
    participants = {
        p.organization_id: (p, org)
        for p, org in (
            await db.execute(
                select(RoundParticipant, Organization)
                .join(Organization, Organization.id == RoundParticipant.organization_id)
                .where(RoundParticipant.round_id == rnd.id)
            )
        ).all()
    }
    requests = (
        await db.scalars(
            select(AllocationRequest)
            .where(AllocationRequest.round_id == rnd.id, AllocationRequest.status == "active")
            .order_by(AllocationRequest.accepted_seq)
        )
    ).all()
    out_lots = []
    for rl, lot in lots:
        reqs = []
        for r in requests:
            if r.lot_id != rl.lot_id:
                continue
            p, org = participants[r.organization_id]
            reqs.append(
                {
                    "request_id": r.id,
                    "organization_id": r.organization_id,
                    "quantity": r.quantity,
                    "accepted_seq": r.accepted_seq,
                    "eligible": bool(p.eligible and org.is_active),
                    "weight": p.weight,
                    "priority": p.priority,
                }
            )
        out_lots.append(
            {
                "lot_id": lot.id,
                "name": lot.name,
                "unit": lot.unit,
                "supply": rl.quantity,
                "requests": reqs,
            }
        )
    return {
        "round_id": rnd.id,
        "strategy": rnd.strategy,
        "strategy_version": CURRENT_VERSION[rnd.strategy],
        "lots": out_lots,
    }


async def close_round(
    db: AsyncSession, actor: Actor, changes: Changes, round_id: int, *, version: int | None
) -> RoundSnapshot:
    rnd = await lock_round(db, round_id)
    _check_round_version(rnd, version)
    require_status(rnd, "open", action="close the round")
    data = await build_snapshot_input(db, rnd)
    snap = RoundSnapshot(
        round_id=rnd.id,
        strategy=data["strategy"],
        strategy_version=data["strategy_version"],
        input=data,
        input_hash=canonical_hash(data),
        created_by=actor.user_id,
    )
    db.add(snap)
    rnd.status, rnd.closed_at = "closed", await db_now(db)
    participants = (
        await db.scalars(select(RoundParticipant).where(RoundParticipant.round_id == rnd.id))
    ).all()
    audit(
        db,
        actor,
        "round.close",
        "round",
        rnd.id,
        round_id=rnd.id,
        before={"status": "open"},
        after={
            "status": "closed",
            "input_hash": snap.input_hash,
            "requests": sum(len(lot["requests"]) for lot in data["lots"]),
        },
    )
    changes.round_changed(rnd, public=True, participants=list(participants))
    await db.flush()
    return snap


async def cancel_round(
    db: AsyncSession, actor: Actor, changes: Changes, round_id: int, *, reason: str
) -> Round:
    rnd = await lock_round(db, round_id)
    require_status(rnd, "draft", "open", "closed", action="cancel the round")
    rls = (
        await db.scalars(
            select(RoundLot)
            .where(RoundLot.round_id == round_id, RoundLot.status == "reserved")
            # Protected by the round lock; lots are locked next, in id order.
            .order_by(RoundLot.lot_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    lots = await lock_lots(db, [rl.lot_id for rl in rls])
    now = await db_now(db)
    for rl in rls:
        lot = lots[rl.lot_id]
        lot.quantity_reserved -= rl.quantity
        lot.version += 1
        rl.status, rl.released_at = "released", now
    before = rnd.status
    rnd.status, rnd.cancelled_at = "cancelled", now
    participants = (
        await db.scalars(select(RoundParticipant).where(RoundParticipant.round_id == rnd.id))
    ).all()
    audit(
        db,
        actor,
        "round.cancel",
        "round",
        rnd.id,
        round_id=rnd.id,
        reason=reason,
        before={"status": before},
        after={"status": "cancelled", "released": {rl.lot_id: rl.quantity for rl in rls}},
    )
    changes.round_changed(rnd, public=True, participants=list(participants))
    return rnd
