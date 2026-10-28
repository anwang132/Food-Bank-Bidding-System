"""Shared service primitives: the acting user, change events, audit records, and
row-locking helpers that implement the lock order in docs/DECISIONS.md (D3):

    idempotency key → rounds → inventory_lots (ascending id) → round children
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidState, NotFound
from app.logs import request_id_var
from app.models import AuditEntry, InventoryLot, Round, RoundParticipant


@dataclass(frozen=True)
class Actor:
    user_id: int
    role: str
    organization_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class Event:
    """A committed-change hint. Carries ids and versions only, never private data."""

    topic: str  # "public" | "admin" | "org:<id>" | "user:<id>" | "chat"
    type: str
    round_id: int | None = None
    version: int | None = None
    # "chat" events only: the conversation and the users allowed to read it.
    channel_id: int | None = None
    recipients: tuple[int, ...] = ()

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"topic": self.topic, "type": self.type}
        if self.round_id is not None:
            msg["round_id"] = self.round_id
        if self.version is not None:
            msg["version"] = self.version
        if self.channel_id is not None:
            msg["channel_id"] = self.channel_id
            msg["recipients"] = list(self.recipients)
        return msg


@dataclass
class Changes:
    """Events collected during a transaction; published only after commit."""

    events: list[Event] = field(default_factory=list)

    def add(self, event: Event) -> None:
        if event not in self.events:
            self.events.append(event)

    def round_changed(
        self,
        rnd: Round,
        *,
        public: bool = False,
        participants: list[RoundParticipant] | None = None,
    ) -> None:
        """Bump round counters and record matching events.

        ``public`` changes are visible to every role; ``participants`` lists the
        organizations whose private view of the round changed."""
        rnd.version += 1
        self.add(Event("admin", "round.changed", rnd.id, rnd.version))
        if public:
            rnd.public_version += 1
            self.add(Event("public", "round.changed", rnd.id, rnd.public_version))
        for p in participants or []:
            p.version += 1
            self.add(Event(f"org:{p.organization_id}", "round.changed", rnd.id, p.version))


async def db_now(session: AsyncSession) -> datetime:
    """Wall-clock time from the database (not transaction start), so a transaction
    that waited on a lock still evaluates deadlines at the moment it proceeds."""
    value: datetime | None = await session.scalar(select(func.clock_timestamp()))
    assert value is not None
    return value


async def lock_round(session: AsyncSession, round_id: int) -> Round:
    rnd = await session.scalar(
        select(Round)
        .where(Round.id == round_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rnd is None:
        raise NotFound("Round not found")
    return rnd


async def lock_lots(session: AsyncSession, lot_ids: list[int]) -> dict[int, InventoryLot]:
    if not lot_ids:
        return {}
    rows = await session.scalars(
        select(InventoryLot)
        .where(InventoryLot.id.in_(lot_ids))
        .order_by(InventoryLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    lots = {lot.id: lot for lot in rows}
    missing = set(lot_ids) - set(lots)
    if missing:
        raise NotFound("Inventory lot not found", details={"lot_ids": sorted(missing)})
    return lots


def require_status(rnd: Round, *allowed: str, action: str) -> None:
    if rnd.status not in allowed:
        raise InvalidState(
            f"Cannot {action} while the round is {rnd.status}",
            details={"status": rnd.status, "allowed": list(allowed)},
        )


def audit(
    session: AsyncSession,
    actor: Actor | None,
    action: str,
    target_type: str,
    target_id: object,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    round_id: int | None = None,
) -> None:
    session.add(
        AuditEntry(
            actor_user_id=actor.user_id if actor else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            round_id=round_id,
            before=_jsonable(before),
            after=_jsonable(after),
            reason=reason,
            request_id=request_id_var.get(),
        )
    )


def _jsonable(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime | date) else v) for k, v in value.items()}


def fields(obj: object, *names: str) -> dict[str, Any]:
    return {n: getattr(obj, n) for n in names}
