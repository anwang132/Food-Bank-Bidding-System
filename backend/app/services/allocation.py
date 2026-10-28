"""Preview and finalization. Both read only the frozen snapshot and call the same
registered strategy implementation, so a preview of the frozen strategy equals
what finalization persists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.allocation import CURRENT_VERSION, LotInput, LotResult, RequestInput, run
from app.errors import InvalidState, NotFound
from app.models import AllocationResult, Round, RoundLot, RoundParticipant, RoundSnapshot
from app.services.common import Actor, Changes, audit, db_now, lock_lots, lock_round
from app.services.rounds import canonical_hash


@dataclass(frozen=True)
class Computation:
    strategy: str
    strategy_version: int
    input_hash: str
    result_hash: str
    lots: list[LotResult]
    lot_meta: dict[int, dict[str, Any]]


def lots_from_snapshot(data: dict[str, Any]) -> list[LotInput]:
    return [
        LotInput(
            lot_id=lot["lot_id"],
            unit=lot["unit"],
            supply=lot["supply"],
            requests=tuple(RequestInput(**r) for r in lot["requests"]),
        )
        for lot in data["lots"]
    ]


def result_hash(lots: list[LotResult]) -> str:
    return canonical_hash(
        {
            "lots": [
                {
                    "lot_id": lot.lot_id,
                    "lines": [[line.request_id, line.allocated] for line in lot.lines],
                }
                for lot in lots
            ]
        }
    )


def compute(snapshot: RoundSnapshot, strategy: str | None = None) -> Computation:
    """Run the frozen strategy (or, for a labelled what-if comparison, another
    strategy's current version) over the snapshot input."""
    if strategy is None or strategy == snapshot.strategy:
        name, version = snapshot.strategy, snapshot.strategy_version
    else:
        name, version = strategy, CURRENT_VERSION[strategy]
    lots = run(name, version, lots_from_snapshot(snapshot.input))
    meta = {
        lot["lot_id"]: {"name": lot["name"], "unit": lot["unit"]} for lot in snapshot.input["lots"]
    }
    return Computation(name, version, snapshot.input_hash, result_hash(lots), lots, meta)


async def get_snapshot(db: AsyncSession, round_id: int) -> RoundSnapshot:
    snap = await db.get(RoundSnapshot, round_id, populate_existing=True)
    if snap is None:
        raise NotFound("This round has no frozen allocation input yet")
    return snap


async def preview(db: AsyncSession, round_id: int, strategy: str | None) -> Computation:
    rnd = await db.get(Round, round_id)
    if rnd is None:
        raise NotFound("Round not found")
    if rnd.status != "closed":
        raise InvalidState(
            "Preview is available only for a closed round", details={"status": rnd.status}
        )
    return compute(await get_snapshot(db, round_id), strategy)


async def finalize(db: AsyncSession, actor: Actor, changes: Changes, round_id: int) -> Computation:
    rnd = await lock_round(db, round_id)
    if rnd.status == "allocated":
        raise InvalidState("This round has already been finalized", code="ALREADY_FINALIZED")
    if rnd.status != "closed":
        raise InvalidState(
            f"Cannot finalize while the round is {rnd.status}", details={"status": rnd.status}
        )
    snap = await get_snapshot(db, round_id)
    comp = compute(snap)
    supply = {lot["lot_id"]: lot["supply"] for lot in snap.input["lots"]}
    eligible = {
        r["request_id"]: r["eligible"] for lot in snap.input["lots"] for r in lot["requests"]
    }

    lots = await lock_lots(db, sorted(supply))
    rls = {
        rl.lot_id: rl
        for rl in await db.scalars(
            select(RoundLot)
            .where(RoundLot.round_id == round_id, RoundLot.status == "reserved")
            .execution_options(populate_existing=True)
        )
    }
    if set(rls) != set(supply):
        raise InvalidState(
            "Reserved inventory no longer matches the frozen input", code="SNAPSHOT_MISMATCH"
        )
    now = await db_now(db)
    for result in comp.lots:
        rl, lot = rls[result.lot_id], lots[result.lot_id]
        # Defensive re-check of the invariants before anything is written.
        if rl.quantity != supply[result.lot_id] or result.allocated_total > rl.quantity:
            raise InvalidState("Allocation exceeds reserved supply", code="SNAPSHOT_MISMATCH")
        for line in result.lines:
            assert line.allocated == 0 or eligible[line.request_id]
            db.add(
                AllocationResult(
                    round_id=round_id,
                    lot_id=result.lot_id,
                    request_id=line.request_id,
                    organization_id=line.organization_id,
                    requested_quantity=line.requested,
                    allocated_quantity=line.allocated,
                    explanation=line.explanation,
                    details=line.details,
                )
            )
        # Consume what was allocated; release the whole reservation (the
        # unallocated remainder stays on hand for future rounds).
        lot.quantity_on_hand -= result.allocated_total
        lot.quantity_reserved -= rl.quantity
        lot.version += 1
        rl.status, rl.allocated_quantity, rl.released_at = "consumed", result.allocated_total, now
    snap.result_hash, snap.finalized_by, snap.finalized_at = comp.result_hash, actor.user_id, now
    rnd.status, rnd.allocated_at = "allocated", now
    participants = (
        await db.scalars(select(RoundParticipant).where(RoundParticipant.round_id == round_id))
    ).all()
    audit(
        db,
        actor,
        "round.finalize",
        "round",
        round_id,
        round_id=round_id,
        before={"status": "closed"},
        after={
            "status": "allocated",
            "strategy": comp.strategy,
            "strategy_version": comp.strategy_version,
            "input_hash": comp.input_hash,
            "result_hash": comp.result_hash,
            "allocated": {str(r.lot_id): r.allocated_total for r in comp.lots},
        },
    )
    changes.round_changed(rnd, public=True, participants=list(participants))
    await db.flush()
    return comp
