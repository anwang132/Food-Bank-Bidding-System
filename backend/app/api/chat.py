"""Messaging endpoints. Viewers are rejected by the service layer (``require_chat``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import CurrentActor, Db, SettingsDep, commit_and_publish
from app.schemas import (
    ChannelOut,
    DirectIn,
    GroupIn,
    MembersIn,
    MessageIn,
    MessageOut,
    MessagePage,
    Person,
    ReadIn,
)
from app.services import chat
from app.services.common import Changes

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChannelRef(BaseModel):
    id: int


class ReadOut(BaseModel):
    last_read_message_id: int


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(db: Db, actor: CurrentActor) -> list[ChannelOut]:
    chat.require_chat(actor)
    # Network channels are provisioned on first use (idempotent inserts).
    await chat.ensure_network_channels(db, actor)
    await db.commit()
    return await chat.list_channels(db, actor)


@router.get("/people", response_model=list[Person])
async def list_people(db: Db, actor: CurrentActor) -> list[Person]:
    return await chat.list_people(db, actor)


@router.get("/channels/{channel_id}/messages", response_model=MessagePage)
async def list_messages(
    channel_id: int,
    db: Db,
    actor: CurrentActor,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagePage:
    return await chat.list_messages(db, actor, channel_id, before=before, limit=limit)


@router.post("/channels/{channel_id}/messages", response_model=MessageOut, status_code=201)
async def post_message(
    channel_id: int,
    body: MessageIn,
    request: Request,
    db: Db,
    actor: CurrentActor,
    settings: SettingsDep,
) -> MessageOut | JSONResponse:
    changes = Changes()
    message, created = await chat.post_message(
        db,
        actor,
        changes,
        channel_id,
        body=body.body,
        client_id=body.client_id,
        per_minute=settings.chat_messages_per_minute,
    )
    out = await chat.message_out(db, message)
    await commit_and_publish(request, db, changes)
    if not created:
        return JSONResponse(
            out.model_dump(mode="json"), status_code=200, headers={"Idempotent-Replayed": "true"}
        )
    return out


@router.delete("/messages/{message_id}", response_model=MessageOut)
async def delete_message(
    message_id: int, request: Request, db: Db, actor: CurrentActor
) -> MessageOut:
    changes = Changes()
    message = await chat.delete_message(db, actor, changes, message_id)
    out = await chat.message_out(db, message)
    await commit_and_publish(request, db, changes)
    return out


@router.post("/channels/{channel_id}/read", response_model=ReadOut)
async def mark_read(
    channel_id: int, body: ReadIn, request: Request, db: Db, actor: CurrentActor
) -> ReadOut:
    changes = Changes()
    last = await chat.mark_read(db, actor, changes, channel_id, body.message_id)
    await commit_and_publish(request, db, changes)
    return ReadOut(last_read_message_id=last)


@router.post("/direct", response_model=ChannelRef)
async def open_direct(body: DirectIn, request: Request, db: Db, actor: CurrentActor) -> ChannelRef:
    changes = Changes()
    channel = await chat.open_direct(db, actor, changes, body.user_id)
    await commit_and_publish(request, db, changes)
    return ChannelRef(id=channel.id)


@router.post("/groups", response_model=ChannelRef, status_code=201)
async def create_group(body: GroupIn, request: Request, db: Db, actor: CurrentActor) -> ChannelRef:
    changes = Changes()
    channel = await chat.create_group(
        db, actor, changes, name=body.name, member_ids=body.member_ids
    )
    await commit_and_publish(request, db, changes)
    return ChannelRef(id=channel.id)


@router.post("/channels/{channel_id}/members", response_model=ChannelRef)
async def add_members(
    channel_id: int, body: MembersIn, request: Request, db: Db, actor: CurrentActor
) -> ChannelRef:
    changes = Changes()
    channel = await chat.add_members(db, actor, changes, channel_id, body.user_ids)
    await commit_and_publish(request, db, changes)
    return ChannelRef(id=channel.id)


@router.post("/channels/{channel_id}/leave", status_code=204)
async def leave_group(channel_id: int, request: Request, db: Db, actor: CurrentActor) -> Response:
    changes = Changes()
    await chat.leave_group(db, actor, changes, channel_id)
    await commit_and_publish(request, db, changes)
    return Response(status_code=204)
