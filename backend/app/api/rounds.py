"""Round views for members and admins, member request operations, public summaries
and the version endpoint used by the client resync protocol."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentActor, Db, MemberActor, SettingsDep, commit_and_publish
from app.models import Organization
from app.schemas import (
    AllocationOut,
    PublicRound,
    RequestCreate,
    RequestOut,
    RequestUpdate,
    RoundDetail,
    RoundSummary,
    VersionsOut,
    WithdrawIn,
)
from app.services import chat, idempotency, queries
from app.services import requests as request_service
from app.services.common import Actor, Changes

router = APIRouter(prefix="/api", tags=["rounds"])

IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


async def _request_out(db: Db, actor: Actor, req: Any) -> RequestOut:
    org = await db.get(Organization, req.organization_id)
    return RequestOut(
        id=req.id,
        round_id=req.round_id,
        lot_id=req.lot_id,
        organization_id=req.organization_id,
        organization_name=org.name if org else None,
        quantity=req.quantity,
        status=req.status,
        version=req.version,
        accepted_seq=req.accepted_seq if actor.is_admin else None,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


@router.get("/public/rounds", response_model=list[PublicRound])
async def public_rounds(db: Db, actor: CurrentActor) -> list[PublicRound]:
    return await queries.public_rounds(db)


@router.get("/rounds", response_model=list[RoundSummary])
async def list_rounds(db: Db, actor: CurrentActor) -> list[RoundSummary]:
    return await queries.list_rounds(db, actor)


@router.get("/rounds/{round_id}", response_model=RoundDetail)
async def round_detail(round_id: int, db: Db, actor: CurrentActor) -> RoundDetail:
    return await queries.round_detail(db, actor, round_id)


@router.get("/rounds/{round_id}/results", response_model=AllocationOut)
async def round_results(round_id: int, db: Db, actor: CurrentActor) -> AllocationOut:
    return await queries.results(db, actor, round_id)


@router.post("/rounds/{round_id}/requests", response_model=RequestOut, status_code=201)
async def create_request(
    round_id: int,
    body: RequestCreate,
    request: Request,
    db: Db,
    actor: MemberActor,
    idempotency_key: IdemKey = None,
) -> Any:
    key = idempotency.validate_key(idempotency_key)
    slot = await idempotency.begin(
        db,
        actor_user_id=actor.user_id,
        operation="request.create",
        key=key,
        payload={
            "round_id": round_id,
            "organization_id": actor.organization_id,
            **body.model_dump(),
        },
    )
    if isinstance(slot, idempotency.Replay):
        await db.rollback()
        return JSONResponse(
            slot.response, status_code=slot.status_code, headers={"Idempotent-Replayed": "true"}
        )
    changes = Changes()
    req = await request_service.create_request(
        db, actor, changes, round_id, lot_id=body.lot_id, quantity=body.quantity
    )
    out = await _request_out(db, actor, req)
    await idempotency.complete(db, slot, 201, out.model_dump(mode="json"))
    await commit_and_publish(request, db, changes)
    return out


@router.patch("/requests/{request_id}", response_model=RequestOut)
async def update_request(
    request_id: int, body: RequestUpdate, request: Request, db: Db, actor: MemberActor
) -> RequestOut:
    changes = Changes()
    req = await request_service.update_request(
        db, actor, changes, request_id, quantity=body.quantity, version=body.version
    )
    out = await _request_out(db, actor, req)
    await commit_and_publish(request, db, changes)
    return out


@router.post("/requests/{request_id}/withdraw", response_model=RequestOut)
async def withdraw_request(
    request_id: int, body: WithdrawIn, request: Request, db: Db, actor: MemberActor
) -> RequestOut:
    changes = Changes()
    req = await request_service.withdraw_request(
        db, actor, changes, request_id, version=body.version
    )
    out = await _request_out(db, actor, req)
    await commit_and_publish(request, db, changes)
    return out


@router.get("/sync/versions", response_model=VersionsOut)
async def sync_versions(db: Db, actor: CurrentActor, settings: SettingsDep) -> VersionsOut:
    return VersionsOut(
        rounds=await queries.versions(db, actor),
        chat=await chat.fingerprint(db, actor),
        resync_interval_seconds=settings.resync_interval_seconds,
    )
