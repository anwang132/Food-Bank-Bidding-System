"""Redis pub/sub between API processes.

* ``publish`` runs only after a transaction has committed. Failure is logged and
  counted; the committed write is never affected. On failure the event is still
  delivered to sockets on this process.
* ``run_subscriber`` keeps one subscription per process, reconnecting with capped
  backoff. After every (re)subscription it tells local sockets to ``resync``,
  because anything published while it was disconnected was missed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.realtime.hub import Hub
from app.services.common import Event

CHANNEL = "foodbank:events"
log = logging.getLogger("app.realtime")


@dataclass
class BusStats:
    subscriber_connected: bool = False
    subscriber_connects: int = 0
    last_message_at: float | None = None
    last_error: str | None = None
    published: int = 0
    publish_failures: int = 0
    received: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "subscriber_connected": self.subscriber_connected,
            "subscriber_connects": self.subscriber_connects,
            "seconds_since_last_message": (
                None
                if self.last_message_at is None
                else round(time.monotonic() - self.last_message_at, 1)
            ),
            "last_error": self.last_error,
            "published": self.published,
            "publish_failures": self.publish_failures,
            "received": self.received,
        }


class Bus:
    def __init__(self, redis_url: str, hub: Hub, instance_id: str, timeout: float) -> None:
        self._url = redis_url
        self._hub = hub
        self._instance = instance_id
        self._timeout = timeout
        self._pub = Redis.from_url(
            redis_url, socket_timeout=timeout, socket_connect_timeout=timeout
        )
        self.stats = BusStats()
        self._stopping = asyncio.Event()

    async def publish(self, events: Iterable[Event]) -> None:
        for event in events:
            message = event.to_message() | {"origin": self._instance}
            try:
                await asyncio.wait_for(
                    self._pub.publish(CHANNEL, json.dumps(message)), self._timeout
                )
                self.stats.published += 1
            except Exception as exc:  # notification failure must not fail the request
                self.stats.publish_failures += 1
                self.stats.last_error = type(exc).__name__
                log.warning(
                    "event publish failed; delivering locally only",
                    extra={
                        "event_type": event.type,
                        "topic_kind": event.topic.split(":")[0],
                        "round": event.round_id,
                        "error": type(exc).__name__,
                    },
                )
                self._hub.dispatch(message)

    async def ping(self) -> bool:
        try:
            return bool(await asyncio.wait_for(self._pub.ping(), self._timeout))
        except Exception:
            return False

    async def run_subscriber(self) -> None:
        backoff = 0.25
        while not self._stopping.is_set():
            client = Redis.from_url(self._url, socket_connect_timeout=2, health_check_interval=5)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.subscribe(CHANNEL)
                self.stats.subscriber_connected = True
                self.stats.subscriber_connects += 1
                self.stats.last_error = None
                backoff = 0.25
                log.info(
                    "redis subscriber connected", extra={"connects": self.stats.subscriber_connects}
                )
                self._hub.broadcast_resync("subscriber_connected")
                while not self._stopping.is_set():
                    msg = await pubsub.get_message(timeout=1.0)
                    if msg is None or msg.get("type") != "message":
                        continue
                    self.stats.received += 1
                    self.stats.last_message_at = time.monotonic()
                    try:
                        payload = json.loads(msg["data"])
                    except (ValueError, TypeError):
                        log.warning("ignoring malformed event")
                        continue
                    self._hub.dispatch(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.stats.subscriber_connected:
                    log.warning(
                        "redis subscriber disconnected", extra={"error": type(exc).__name__}
                    )
                self.stats.subscriber_connected = False
                self.stats.last_error = type(exc).__name__
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), backoff)
                backoff = min(backoff * 2, 5.0)
            finally:
                self.stats.subscriber_connected = False
                with contextlib.suppress(Exception):
                    await pubsub.aclose()  # type: ignore[no-untyped-call]
                with contextlib.suppress(Exception):
                    await client.aclose()

    async def close(self) -> None:
        self._stopping.set()
        with contextlib.suppress(Exception):
            await self._pub.aclose()
