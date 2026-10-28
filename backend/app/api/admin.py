"""Administrator endpoints. Every route depends on ``AdminActor``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import AdminActor, Db, commit_and_publish
from app.schemas import (
    AllocationOut,
    AuditOut,
    CancelIn,
    LotCreate,
    LotOut,
    LotUpdate,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    ParticipantIn,
    ReservationUpdate,
    ReserveIn,
    RoundCreate,
    RoundDetail,
    RoundUpdate,
    Strategy,
    TransitionIn,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import allocation, directory, idempotency, inventory, queries, rounds
from app.services.common import Changes

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------- organizations / users


@router.get("/organizations", response_model=list[OrganizationOut])
async def list_organizations(db: Db, actor: AdminActor) -> list[OrganizationOut]:
    return await queries.list_organizations(db)


@router.post("/organizations", response_model=OrganizationOut, status_code=201)
async def create_organization(
    body: OrganizationCreate, request: Request, db: Db, actor: AdminActor
) -> OrganizationOut:
    changes = Changes()
    org = await directory.create_organization(db, actor, changes, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return queries.org_out(org)


@router.patch("/organizations/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: int, body: OrganizationUpdate, request: Request, db: Db, actor: AdminActor
) -> OrganizationOut:
    changes = Changes()
    org = await directory.update_organization(
        db, actor, changes, org_id, **body.model_dump(exclude_unset=True)
    )
    await commit_and_publish(request, db, changes)
    return queries.org_out(org)


@router.get("/users", response_model=list[UserOut])
async def list_users(db: Db, actor: AdminActor) -> list[UserOut]:
    return await queries.list_users(db)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, request: Request, db: Db, actor: AdminActor) -> UserOut:
    changes = Changes()
    user = await directory.create_user(db, actor, changes, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return queries.user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, body: UserUpdate, request: Request, db: Db, actor: AdminActor
) -> UserOut:
    changes = Changes()
    data = body.model_dump(exclude_unset=True)
    clear = "organization_id" in data and data["organization_id"] is None
    user = await directory.update_user(
        db, actor, changes, user_id, clear_organization=clear, **{"reason": None} | data
    )
    await commit_and_publish(request, db, changes)
    return queries.user_out(user)


# ---------------------------------------------------------------- inventory


@router.get("/lots", response_model=list[LotOut])
async def list_lots(db: Db, actor: AdminActor) -> list[LotOut]:
    return await queries.list_lots(db)


@router.post("/lots", response_model=LotOut, status_code=201)
async def create_lot(body: LotCreate, request: Request, db: Db, actor: AdminActor) -> LotOut:
    changes = Changes()
    lot = await inventory.create_lot(db, actor, changes, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return queries.lot_out(lot, None)


@router.patch("/lots/{lot_id}", response_model=LotOut)
async def update_lot(
    lot_id: int, body: LotUpdate, request: Request, db: Db, actor: AdminActor
) -> LotOut:
    changes = Changes()
    lot = await inventory.update_lot(
        db, actor, changes, lot_id, **{"reason": None} | body.model_dump(exclude_unset=True)
    )
    await commit_and_publish(request, db, changes)
    return await queries.get_lot(db, lot.id)


# ---------------------------------------------------------------- rounds


async def _detail(db: Db, actor: AdminActor, round_id: int) -> RoundDetail:
    return await queries.round_detail(db, actor, round_id)


@router.post("/rounds", response_model=RoundDetail, status_code=201)
async def create_round(
    body: RoundCreate, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    rnd = await rounds.create_round(db, actor, changes, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, rnd.id)


@router.patch("/rounds/{round_id}", response_model=RoundDetail)
async def update_round(
    round_id: int, body: RoundUpdate, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.update_round(
        db, actor, changes, round_id, **{"reason": None} | body.model_dump(exclude_unset=True)
    )
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.post("/rounds/{round_id}/lots", response_model=RoundDetail, status_code=201)
async def reserve_lot(
    round_id: int, body: ReserveIn, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.reserve_lot(db, actor, changes, round_id, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.patch("/rounds/{round_id}/lots/{lot_id}", response_model=RoundDetail)
async def change_reservation(
    round_id: int, lot_id: int, body: ReservationUpdate, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.change_reservation(db, actor, changes, round_id, lot_id, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.delete("/rounds/{round_id}/lots/{lot_id}", response_model=RoundDetail)
async def release_lot(
    round_id: int, lot_id: int, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.release_lot(db, actor, changes, round_id, lot_id)
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.put("/rounds/{round_id}/participants/{org_id}", response_model=RoundDetail)
async def set_participant(
    round_id: int, org_id: int, body: ParticipantIn, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.set_participant(db, actor, changes, round_id, org_id, **body.model_dump())
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.post("/rounds/{round_id}/open", response_model=RoundDetail)
async def open_round(
    round_id: int, body: TransitionIn, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.open_round(db, actor, changes, round_id, version=body.version)
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.post("/rounds/{round_id}/close", response_model=RoundDetail)
async def close_round(
    round_id: int, body: TransitionIn, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.close_round(db, actor, changes, round_id, version=body.version)
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.post("/rounds/{round_id}/cancel", response_model=RoundDetail)
async def cancel_round(
    round_id: int, body: CancelIn, request: Request, db: Db, actor: AdminActor
) -> RoundDetail:
    changes = Changes()
    await rounds.cancel_round(db, actor, changes, round_id, reason=body.reason)
    await commit_and_publish(request, db, changes)
    return await _detail(db, actor, round_id)


@router.get("/rounds/{round_id}/preview", response_model=AllocationOut)
async def preview(
    round_id: int, db: Db, actor: AdminActor, strategy: Strategy | None = None
) -> AllocationOut:
    comp = await allocation.preview(db, round_id, strategy)
    snap = await allocation.get_snapshot(db, round_id)
    return queries.computation_out(
        comp, round_id, snap.strategy, await queries.org_names(db), is_preview=True
    )


@router.post("/rounds/{round_id}/finalize", response_model=AllocationOut)
async def finalize(
    round_id: int,
    request: Request,
    db: Db,
    actor: AdminActor,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    key = idempotency.validate_key(idempotency_key)
    slot = await idempotency.begin(
        db,
        actor_user_id=actor.user_id,
        operation="round.finalize",
        key=key,
        payload={"round_id": round_id},
    )
    if isinstance(slot, idempotency.Replay):
        await db.rollback()
        return JSONResponse(
            slot.response, status_code=slot.status_code, headers={"Idempotent-Replayed": "true"}
        )
    changes = Changes()
    comp = await allocation.finalize(db, actor, changes, round_id)
    out = queries.computation_out(
        comp, round_id, comp.strategy, await queries.org_names(db), is_preview=False
    )
    await idempotency.complete(db, slot, 200, out.model_dump(mode="json"))
    await commit_and_publish(request, db, changes)
    return out


# ---------------------------------------------------------------- audit


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(
    db: Db,
    actor: AdminActor,
    round_id: int | None = None,
    target_type: Annotated[str | None, Query(max_length=32)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditOut]:
    return await queries.list_audit(
        db,
        round_id=round_id,
        target_type=target_type,
        action=action,
        before_id=before_id,
        limit=limit,
    )
