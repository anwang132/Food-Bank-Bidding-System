"""Request-scoped dependencies: DB session, authentication, CSRF and role checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import session_scope
from app.errors import Forbidden, Unauthenticated
from app.logs import user_id_var
from app.realtime.bus import Bus
from app.security import CSRF_HEADER, SESSION_COOKIE, constant_time_equals
from app.services import auth
from app.services.common import Actor, Changes

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in session_scope(request.app.state.sessionmaker):
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(request: Request, db: Db) -> auth.ResolvedSession:
    resolved = await auth.resolve(db, request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        raise Unauthenticated("Your session has expired or you are not signed in")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not constant_time_equals(sent, resolved.session.csrf_token):
            raise Forbidden("Missing or invalid CSRF token", code="CSRF_FAILED")
    user_id_var.set(resolved.user.id)
    return resolved


SessionDep = Annotated[auth.ResolvedSession, Depends(get_session)]


async def current_actor(resolved: SessionDep) -> Actor:
    return resolved.actor


CurrentActor = Annotated[Actor, Depends(current_actor)]


async def admin_actor(actor: CurrentActor) -> Actor:
    if not actor.is_admin:
        raise Forbidden("Administrator access is required")
    return actor


AdminActor = Annotated[Actor, Depends(admin_actor)]


async def member_actor(actor: CurrentActor) -> Actor:
    if actor.role != "member":
        raise Forbidden("Only food bank members can do this")
    return actor


MemberActor = Annotated[Actor, Depends(member_actor)]


async def commit_and_publish(request: Request, db: AsyncSession, changes: Changes) -> None:
    """Commit first; publish only what is now durable. Publish failures are absorbed
    by the bus (logged, counted, delivered locally) and never reach the client."""
    await db.commit()
    bus: Bus = request.app.state.bus
    await bus.publish(changes.events)
