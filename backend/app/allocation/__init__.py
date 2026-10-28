"""Pure allocation strategies, registered by (name, version).

A persisted snapshot names the strategy and version it was frozen with. Changing a
strategy's behaviour requires registering a new version and keeping the old one for
any unfinalized snapshot that references it; finalized results are never recomputed.
"""

from __future__ import annotations

from collections.abc import Callable

from app.allocation.need_weighted import allocate_need_weighted
from app.allocation.sequential import allocate_fcfs, allocate_priority
from app.allocation.types import (
    AllocationInputError,
    Line,
    LotInput,
    LotResult,
    RequestInput,
)

Strategy = Callable[[LotInput], LotResult]

REGISTRY: dict[tuple[str, int], Strategy] = {
    ("fcfs", 1): allocate_fcfs,
    ("need_weighted", 1): allocate_need_weighted,
    ("priority", 1): allocate_priority,
}

CURRENT_VERSION: dict[str, int] = {"fcfs": 1, "need_weighted": 1, "priority": 1}

LABELS: dict[str, str] = {
    "fcfs": "First come, first served",
    "need_weighted": "Need-weighted share",
    "priority": "Priority tiers",
}


def run(strategy: str, version: int, lots: list[LotInput]) -> list[LotResult]:
    try:
        fn = REGISTRY[(strategy, version)]
    except KeyError:
        raise AllocationInputError(f"unknown strategy {strategy!r} version {version}") from None
    return [fn(lot) for lot in sorted(lots, key=lambda lot: lot.lot_id)]


__all__ = [
    "CURRENT_VERSION",
    "LABELS",
    "REGISTRY",
    "AllocationInputError",
    "Line",
    "LotInput",
    "LotResult",
    "RequestInput",
    "run",
]
