"""Idempotency keys for retry-sensitive operations (docs/DECISIONS.md D6).

``begin`` inserts the key row as the first write of the business transaction. A
concurrent transaction with the same (actor, operation, key) blocks on the unique
index until the first one finishes:

* first committed  → the second sees the stored response and replays it;
* first rolled back → the second's insert succeeds and it performs the operation.

Only successful outcomes are stored, in the same transaction as their effect, so a
stored response always describes a committed effect.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ValidationFailed
from app.models import IdempotencyKey


@dataclass(frozen=True)
class Replay:
    status_code: int
    response: dict[str, Any]


@dataclass(frozen=True)
class Reservation:
    row_id: int


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_key(key: str | None) -> str:
    if key is None:
        raise ValidationFailed(
            "The Idempotency-Key header is required for this operation",
            code="IDEMPOTENCY_KEY_REQUIRED",
        )
    if not 8 <= len(key) <= 128 or not key.isascii() or not key.isprintable():
        raise ValidationFailed(
            "Idempotency-Key must be 8–128 printable ASCII characters",
            code="IDEMPOTENCY_KEY_INVALID",
        )
    return key


async def begin(
    session: AsyncSession,
    *,
    actor_user_id: int,
    operation: str,
    key: str,
    payload: dict[str, Any],
) -> Replay | Reservation:
    digest = payload_hash(payload)
    row_id = await session.scalar(
        insert(IdempotencyKey)
        .values(actor_user_id=actor_user_id, operation=operation, key=key, request_hash=digest)
        .on_conflict_do_nothing(constraint="uq_idempotency_scope")
        .returning(IdempotencyKey.id)
    )
    if row_id is not None:
        return Reservation(row_id)
    existing = await session.scalar(
        select(IdempotencyKey)
        .where(
            IdempotencyKey.actor_user_id == actor_user_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
        .execution_options(populate_existing=True)
    )
    assert existing is not None and existing.response is not None
    if existing.request_hash != digest:
        raise ValidationFailed(
            "This Idempotency-Key was already used with a different request",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    return Replay(existing.status_code or 200, existing.response)


async def complete(
    session: AsyncSession, reservation: Reservation, status_code: int, response: dict[str, Any]
) -> None:
    row = await session.get(IdempotencyKey, reservation.row_id)
    assert row is not None
    row.status_code = status_code
    row.response = response
    await session.flush()
