"""WebSocket endpoint.

Handshake: the Origin must be allow-listed and the session cookie valid; otherwise
the socket is closed with 4403/4401 before any data is sent. After acceptance:

* ``hello`` is sent first; the client then (re)loads its snapshot over HTTP;
* events for the connection's topics are forwarded from the bounded queue;
* the session is re-validated every ``ws_revalidate_seconds``, at expiry, and
  immediately on a ``user:<id>`` control event (logout, deactivation, role or
  organization change). Invalid session → close 4401. Changed audience → close
  4409 so the client reconnects with the correct topics.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import Settings
from app.realtime.hub import Connection, Hub, topics_for
from app.security import SESSION_COOKIE
from app.services import auth

router = APIRouter()
log = logging.getLogger("app.realtime")

CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_AUDIENCE_CHANGED = 4409
CLOSE_TOO_MANY = 4429


async def _resolve(ws: WebSocket, token: str | None) -> auth.ResolvedSession | None:
    async with ws.app.state.sessionmaker() as db:
        return await auth.resolve(db, token)


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    settings: Settings = ws.app.state.settings
    hub: Hub = ws.app.state.hub
    if ws.headers.get("origin") not in settings.allowed_origins:
        await ws.close(code=CLOSE_FORBIDDEN_ORIGIN, reason="origin not allowed")
        return
    token = ws.cookies.get(SESSION_COOKIE)
    resolved = await _resolve(ws, token)
    if resolved is None:
        await ws.close(code=CLOSE_UNAUTHENTICATED, reason="unauthenticated")
        return
    user = resolved.user
    if hub.count_for_user(user.id) >= settings.ws_max_connections_per_user:
        await ws.close(code=CLOSE_TOO_MANY, reason="too many connections")
        return
    topics = topics_for(user.id, user.role, user.organization_id)
    await ws.accept()
    conn = hub.register(user.id, topics)
    log.info("ws connected", extra={"conn_id": conn.id, "user_id": user.id, "role": user.role})
    try:
        await ws.send_json(
            {
                "type": "hello",
                "instance": settings.instance_id,
                "connection_id": conn.id,
                "role": user.role,
                "resync_interval_seconds": settings.resync_interval_seconds,
                "session_expires_at": resolved.session.expires_at.isoformat(),
            }
        )
        # Whichever finishes first ends the connection: the client leaves (receiver),
        # a send fails (sender), or the session stops being valid (revalidator).
        revalidator = asyncio.create_task(_revalidator(ws, conn, token, resolved, settings))
        tasks = {
            asyncio.create_task(_sender(ws, conn)),
            asyncio.create_task(_receiver(ws)),
            revalidator,
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if revalidator in done and revalidator.exception() is None:
            with contextlib.suppress(Exception):
                await ws.close(code=revalidator.result())
    finally:
        hub.unregister(conn)
        log.info("ws disconnected", extra={"conn_id": conn.id, "overflows": conn.overflows})


async def _sender(ws: WebSocket, conn: Connection) -> None:
    while True:
        message = await conn.queue.get()
        await ws.send_json(message)


async def _receiver(ws: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get("type") == "ping":
                await ws.send_json({"type": "pong"})


async def _revalidator(
    ws: WebSocket,
    conn: Connection,
    token: str | None,
    resolved: auth.ResolvedSession,
    settings: Settings,
) -> int:
    expires_at = resolved.session.expires_at
    while True:
        until_expiry = (expires_at - datetime.now(UTC)).total_seconds()
        wait = max(0.0, min(settings.ws_revalidate_seconds, until_expiry))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(conn.revalidate.wait(), wait)
        conn.revalidate.clear()
        current = await _resolve(ws, token)
        if current is None:
            log.info("ws session no longer valid", extra={"conn_id": conn.id})
            return CLOSE_UNAUTHENTICATED
        u = current.user
        if topics_for(u.id, u.role, u.organization_id) != conn.topics:
            log.info("ws audience changed", extra={"conn_id": conn.id})
            return CLOSE_AUDIENCE_CHANGED
