"""Input/output types shared by all allocation strategies. No I/O lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any


class AllocationInputError(ValueError):
    """Raised when a strategy receives input that violates its preconditions."""


@dataclass(frozen=True)
class RequestInput:
    request_id: int
    organization_id: int
    quantity: int
    accepted_seq: int
    eligible: bool
    weight: int
    priority: int


@dataclass(frozen=True)
class LotInput:
    lot_id: int
    unit: str
    supply: int
    requests: tuple[RequestInput, ...]


@dataclass(frozen=True)
class Line:
    request_id: int
    organization_id: int
    requested: int
    allocated: int
    explanation: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LotResult:
    lot_id: int
    supply: int
    allocated_total: int
    unallocated: int
    lines: tuple[Line, ...]


def validate(lot: LotInput) -> None:
    if lot.supply < 0:
        raise AllocationInputError(f"lot {lot.lot_id}: supply must be >= 0")
    seen_ids: set[int] = set()
    seen_seq: set[int] = set()
    for r in lot.requests:
        if r.quantity <= 0:
            raise AllocationInputError(f"request {r.request_id}: quantity must be > 0")
        if r.request_id in seen_ids:
            raise AllocationInputError(f"request {r.request_id}: duplicate request id")
        if r.accepted_seq in seen_seq:
            raise AllocationInputError(f"request {r.request_id}: duplicate accepted order")
        seen_ids.add(r.request_id)
        seen_seq.add(r.accepted_seq)


def accepted_order_key(r: RequestInput) -> tuple[int, int]:
    return (r.accepted_seq, r.request_id)


def fmt_qty(value: Fraction | int) -> str:
    """Human-readable quantity: integers exactly, fractions to 2 decimals (half-up)."""
    frac = Fraction(value)
    if frac.denominator == 1:
        return str(frac.numerator)
    dec = (Decimal(frac.numerator) / Decimal(frac.denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return format(dec.normalize(), "f")


def ineligible_line(r: RequestInput) -> Line:
    return Line(
        request_id=r.request_id,
        organization_id=r.organization_id,
        requested=r.quantity,
        allocated=0,
        explanation="Not allocated: your organization was not eligible for this round.",
        details={"eligible": False},
    )


def finish(lot: LotInput, lines: list[Line]) -> LotResult:
    by_id = {r.request_id: r for r in lot.requests}
    lines.sort(key=lambda line: accepted_order_key(by_id[line.request_id]))
    total = sum(line.allocated for line in lines)
    # Invariants every strategy must satisfy; a violation is a programming error.
    assert 0 <= total <= lot.supply, "allocated more than supply"
    for line in lines:
        req = by_id[line.request_id]
        assert 0 <= line.allocated <= req.quantity, "allocation outside [0, requested]"
        assert req.eligible or line.allocated == 0, "allocated to ineligible organization"
    return LotResult(
        lot_id=lot.lot_id,
        supply=lot.supply,
        allocated_total=total,
        unallocated=lot.supply - total,
        lines=tuple(lines),
    )
