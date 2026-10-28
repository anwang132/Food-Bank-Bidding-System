"""Inventory lots. Quantity changes lock only the lot row; they never lock a round,
so they cannot deadlock with round operations (which lock round, then lot)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, InvalidState
from app.models import InventoryLot
from app.services.common import Actor, Changes, Event, audit, fields, lock_lots

LOT_FIELDS = ("name", "description", "unit", "quantity_on_hand", "quantity_reserved", "best_before")


async def create_lot(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    *,
    name: str,
    description: str,
    unit: str,
    quantity_on_hand: int,
    best_before: date | None,
) -> InventoryLot:
    lot = InventoryLot(
        name=name.strip(),
        description=description.strip(),
        unit=unit.strip(),
        quantity_on_hand=quantity_on_hand,
        quantity_reserved=0,
        best_before=best_before,
        version=1,
    )
    db.add(lot)
    await db.flush()
    audit(db, actor, "lot.create", "lot", lot.id, after=fields(lot, *LOT_FIELDS))
    changes.add(Event("admin", "inventory.changed"))
    return lot


async def update_lot(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    lot_id: int,
    *,
    version: int,
    reason: str | None,
    name: str | None = None,
    description: str | None = None,
    unit: str | None = None,
    best_before: date | None = None,
    clear_best_before: bool = False,
    quantity_on_hand: int | None = None,
) -> InventoryLot:
    lot = (await lock_lots(db, [lot_id]))[lot_id]
    if lot.version != version:
        raise Conflict(
            "This lot was changed by someone else. Reload and try again.",
            code="VERSION_CONFLICT",
            details={"current_version": lot.version},
        )
    before = fields(lot, *LOT_FIELDS)
    descriptive = (name, description, unit, best_before)
    if lot.quantity_reserved > 0 and (any(v is not None for v in descriptive) or clear_best_before):
        raise InvalidState(
            "This lot is reserved by a round. Only its on-hand quantity can change until the "
            "round is finalized or cancelled.",
            code="LOT_RESERVED",
        )
    if quantity_on_hand is not None:
        if quantity_on_hand < lot.quantity_reserved:
            raise InvalidState(
                f"On-hand quantity cannot go below the {lot.quantity_reserved} {lot.unit} "
                "reserved for a round.",
                code="BELOW_RESERVED",
                details={"quantity_reserved": lot.quantity_reserved},
            )
        lot.quantity_on_hand = quantity_on_hand
    if name is not None:
        lot.name = name.strip()
    if description is not None:
        lot.description = description.strip()
    if unit is not None:
        lot.unit = unit.strip()
    if best_before is not None:
        lot.best_before = best_before
    elif clear_best_before:
        lot.best_before = None
    lot.version += 1
    await db.flush()
    audit(
        db,
        actor,
        "lot.update",
        "lot",
        lot.id,
        before=before,
        after=fields(lot, *LOT_FIELDS),
        reason=reason,
    )
    changes.add(Event("admin", "inventory.changed"))
    return lot
