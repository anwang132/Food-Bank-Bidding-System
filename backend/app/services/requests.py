"""Organization requests (the product's "bids").

Protocol for every path (create, edit, withdraw):
1. lock the round row (``FOR UPDATE``);
2. require ``status == 'open'`` and DB clock before the deadline;
3. act; on create, take ``accepted_seq`` from ``rounds.next_request_seq``.

Because close also locks the round row first, a request racing close either commits
before close reads the snapshot (and is included) or waits and then observes
``closed`` (and is rejected). Since the sequence is taken under the same lock and
held until commit, accepted order equals commit order within a round.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, Forbidden, InvalidState, NotFound
from app.models import AllocationRequest, Organization, Round, RoundLot, RoundParticipant
from app.services.common import Actor, Changes, audit, db_now, lock_round, require_status


async def _guard_open(db: AsyncSession, rnd: Round) -> None:
    require_status(rnd, "open", action="change requests")
    if await db_now(db) >= rnd.request_deadline:
        raise InvalidState("The request deadline for this round has passed", code="DEADLINE_PASSED")


async def _participant(db: AsyncSession, round_id: int, org_id: int) -> RoundParticipant:
    row = (
        await db.execute(
            select(RoundParticipant, Organization)
            .join(Organization, Organization.id == RoundParticipant.organization_id)
            .where(
                RoundParticipant.round_id == round_id, RoundParticipant.organization_id == org_id
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None or not row[0].eligible or not row[1].is_active:
        raise Forbidden(
            "Your organization is not eligible to request from this round", code="NOT_ELIGIBLE"
        )
    return row[0]


def _member_org(actor: Actor) -> int:
    if actor.role != "member" or actor.organization_id is None:
        raise Forbidden("Only food bank members can submit requests")
    return actor.organization_id


async def create_request(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    round_id: int,
    *,
    lot_id: int,
    quantity: int,
) -> AllocationRequest:
    org_id = _member_org(actor)
    rnd = await lock_round(db, round_id)
    await _guard_open(db, rnd)
    participant = await _participant(db, round_id, org_id)
    rl = await db.scalar(
        select(RoundLot).where(
            RoundLot.round_id == round_id, RoundLot.lot_id == lot_id, RoundLot.status == "reserved"
        )
    )
    if rl is None:
        raise NotFound("This lot is not offered in the round")
    existing = await db.scalar(
        select(AllocationRequest.id).where(
            AllocationRequest.round_id == round_id,
            AllocationRequest.lot_id == lot_id,
            AllocationRequest.organization_id == org_id,
            AllocationRequest.status == "active",
        )
    )
    if existing is not None:
        raise Conflict(
            "Your organization already has an active request for this lot; edit it instead",
            code="REQUEST_EXISTS",
            details={"request_id": existing},
        )
    seq = rnd.next_request_seq
    rnd.next_request_seq += 1
    req = AllocationRequest(
        round_id=round_id,
        lot_id=lot_id,
        organization_id=org_id,
        quantity=quantity,
        status="active",
        accepted_seq=seq,
        version=1,
        created_by=actor.user_id,
        updated_by=actor.user_id,
    )
    db.add(req)
    try:
        await db.flush()
    except IntegrityError:  # partial unique index backs up the check above
        raise Conflict(
            "Your organization already has an active request for this lot", code="REQUEST_EXISTS"
        ) from None
    audit(
        db,
        actor,
        "request.create",
        "request",
        req.id,
        round_id=round_id,
        after={
            "lot_id": lot_id,
            "organization_id": org_id,
            "quantity": quantity,
            "accepted_seq": seq,
        },
    )
    changes.round_changed(rnd, participants=[participant])
    await db.flush()
    return req


async def _load_own(db: AsyncSession, actor: Actor, request_id: int) -> AllocationRequest:
    org_id = _member_org(actor)
    req = await db.get(AllocationRequest, request_id)
    # Same response for "missing" and "another organization's" to avoid disclosure.
    if req is None or req.organization_id != org_id:
        raise NotFound("Request not found")
    return req


async def _relock(db: AsyncSession, request_id: int) -> AllocationRequest:
    req = await db.scalar(
        select(AllocationRequest)
        .where(AllocationRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert req is not None
    return req


def _check_version(req: AllocationRequest, version: int) -> None:
    if req.version != version:
        raise Conflict(
            "This request was changed elsewhere (another tab or a colleague). "
            "Review the current quantity and try again.",
            code="VERSION_CONFLICT",
            details={
                "current_version": req.version,
                "current_quantity": req.quantity,
                "status": req.status,
            },
        )


async def update_request(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    request_id: int,
    *,
    quantity: int,
    version: int,
) -> AllocationRequest:
    req = await _load_own(db, actor, request_id)
    rnd = await lock_round(db, req.round_id)
    await _guard_open(db, rnd)
    req = await _relock(db, request_id)
    _check_version(req, version)
    if req.status != "active":
        raise InvalidState("This request was withdrawn", code="REQUEST_WITHDRAWN")
    participant = await _participant(db, req.round_id, req.organization_id)
    before = {"quantity": req.quantity}
    req.quantity = quantity  # accepted_seq is unchanged: edits keep their place
    req.version += 1
    req.updated_by = actor.user_id
    req.updated_at = await db_now(db)
    audit(
        db,
        actor,
        "request.update",
        "request",
        req.id,
        round_id=req.round_id,
        before=before,
        after={"quantity": quantity},
    )
    changes.round_changed(rnd, participants=[participant])
    await db.flush()
    return req


async def withdraw_request(
    db: AsyncSession, actor: Actor, changes: Changes, request_id: int, *, version: int
) -> AllocationRequest:
    req = await _load_own(db, actor, request_id)
    rnd = await lock_round(db, req.round_id)
    await _guard_open(db, rnd)
    req = await _relock(db, request_id)
    _check_version(req, version)
    if req.status != "active":
        raise InvalidState("This request was already withdrawn", code="REQUEST_WITHDRAWN")
    participant = await db.scalar(
        select(RoundParticipant)
        .where(
            RoundParticipant.round_id == req.round_id,
            RoundParticipant.organization_id == req.organization_id,
        )
        .execution_options(populate_existing=True)
    )
    assert participant is not None
    now = await db_now(db)
    req.status, req.withdrawn_at, req.updated_at = "withdrawn", now, now
    req.version += 1
    req.updated_by = actor.user_id
    audit(
        db,
        actor,
        "request.withdraw",
        "request",
        req.id,
        round_id=req.round_id,
        before={"quantity": req.quantity, "status": "active"},
        after={"status": "withdrawn"},
    )
    changes.round_changed(rnd, participants=[participant])
    await db.flush()
    return req
