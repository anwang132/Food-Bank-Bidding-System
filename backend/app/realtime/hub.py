"""Per-process registry of WebSocket connections and topic routing.

Each connection has a bounded queue. Messages are invalidation hints, so on overflow
the queue is collapsed into a single ``resync`` instead of growing or dropping the
client: the client then refetches state and converges anyway."""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("app.realtime")

_SCOPES = {"public": "public", "admin": "admin"}


def scope_of(topic: str) -> str | None:
    if topic in _SCOPES:
        return _SCOPES[topic]
    if topic.startswith("org:"):
        return "org"
    return None


def topics_for(user_id: int, role: str, organization_id: int | None) -> frozenset[str]:
    topics = {"public", f"user:{user_id}"}
    if role == "admin":
        topics.update(("admin", "chat"))
    elif role == "member" and organization_id is not None:
        topics.update((f"org:{organization_id}", "chat"))
    return frozenset(topics)


@dataclass(eq=False)
class Connection:
    id: int
    user_id: int
    topics: frozenset[str]
    queue: asyncio.Queue[dict[str, Any]]
    revalidate: asyncio.Event = field(default_factory=asyncio.Event)
    overflows: int = 0

    def offer(self, message: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.overflows += 1
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "resync", "reason": "slow_consumer"})
            log.warning(
                "ws queue overflow collapsed to resync",
                extra={"conn_id": self.id, "overflows": self.overflows},
            )


class Hub:
    def __init__(self, queue_size: int) -> None:
        self._queue_size = queue_size
        self._ids = itertools.count(1)
        self.connections: dict[int, Connection] = {}
        self.delivered = 0

    def register(self, user_id: int, topics: frozenset[str]) -> Connection:
        conn = Connection(next(self._ids), user_id, topics, asyncio.Queue(self._queue_size))
        self.connections[conn.id] = conn
        return conn

    def unregister(self, conn: Connection) -> None:
        self.connections.pop(conn.id, None)

    def count_for_user(self, user_id: int) -> int:
        return sum(1 for c in self.connections.values() if c.user_id == user_id)

    def dispatch(self, message: dict[str, Any]) -> None:
        """Route one event to local connections subscribed to its topic. Only the
        audience-appropriate fields are forwarded."""
        topic = message.get("topic")
        if not isinstance(topic, str):
            return
        if topic.startswith("user:"):
            for conn in self.connections.values():
                if topic in conn.topics:
                    conn.revalidate.set()
            return
        if topic == "chat":
            self._dispatch_chat(message)
            return
        scope = scope_of(topic)
        if scope is None:
            return
        out = {"type": message.get("type"), "scope": scope}
        for key in ("round_id", "version"):
            if key in message:
                out[key] = message[key]
        for conn in self.connections.values():
            if topic in conn.topics:
                conn.offer(out)
                self.delivered += 1

    def _dispatch_chat(self, message: dict[str, Any]) -> None:
        """Chat events name their recipients; everyone else never sees them."""
        recipients = message.get("recipients")
        channel_id = message.get("channel_id")
        if not isinstance(recipients, list) or not isinstance(channel_id, int):
            return
        allowed = set(recipients)
        out = {"type": message.get("type"), "scope": "chat", "channel_id": channel_id}
        for conn in self.connections.values():
            if conn.user_id in allowed and "chat" in conn.topics:
                conn.offer(out)
                self.delivered += 1

    def broadcast_resync(self, reason: str) -> None:
        for conn in self.connections.values():
            conn.offer({"type": "resync", "reason": reason})
