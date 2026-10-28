"""First-come-first-served and priority-tier strategies.

Both fill requests greedily in a fixed order until supply runs out; they differ only
in the order. Priority tiers: 1 is the highest priority and is served first."""

from __future__ import annotations

from app.allocation.types import (
    AllocationInputError,
    Line,
    LotInput,
    LotResult,
    RequestInput,
    accepted_order_key,
    finish,
    ineligible_line,
    validate,
)


def _fill_status(allocated: int, requested: int) -> str:
    if allocated == requested:
        return "Fully filled"
    if allocated == 0:
        return "Not filled"
    return "Partly filled"


def allocate_fcfs(lot: LotInput) -> LotResult:
    validate(lot)
    lines = [ineligible_line(r) for r in lot.requests if not r.eligible]
    ordered = sorted((r for r in lot.requests if r.eligible), key=accepted_order_key)
    remaining = lot.supply
    unit = lot.unit
    for position, r in enumerate(ordered, start=1):
        before = remaining
        give = min(remaining, r.quantity)
        remaining -= give
        used_earlier = lot.supply - before
        status = _fill_status(give, r.quantity)
        text = (
            f"{status}: {give} of {r.quantity} {unit}. First come, first served — your "
            f"request was #{position} of {len(ordered)} in accepted order; earlier requests "
            f"received {used_earlier} of the {lot.supply} {unit}, leaving {before}."
        )
        lines.append(
            Line(
                request_id=r.request_id,
                organization_id=r.organization_id,
                requested=r.quantity,
                allocated=give,
                explanation=text,
                details={
                    "eligible": True,
                    "position": position,
                    "eligible_requests": len(ordered),
                    "available_when_reached": before,
                    "used_by_earlier": used_earlier,
                },
            )
        )
    return finish(lot, lines)


def _priority_key(r: RequestInput) -> tuple[int, int, int]:
    return (r.priority, r.accepted_seq, r.request_id)


def allocate_priority(lot: LotInput) -> LotResult:
    validate(lot)
    for r in lot.requests:
        if r.eligible and not 1 <= r.priority <= 10:
            raise AllocationInputError(f"request {r.request_id}: priority must be between 1 and 10")
    lines = [ineligible_line(r) for r in lot.requests if not r.eligible]
    ordered = sorted((r for r in lot.requests if r.eligible), key=_priority_key)
    tier_sizes: dict[int, int] = {}
    for r in ordered:
        tier_sizes[r.priority] = tier_sizes.get(r.priority, 0) + 1
    tier_position: dict[int, int] = {}
    remaining = lot.supply
    unit = lot.unit
    for r in ordered:
        tier_position[r.priority] = tier_position.get(r.priority, 0) + 1
        pos = tier_position[r.priority]
        before = remaining
        give = min(remaining, r.quantity)
        remaining -= give
        used_earlier = lot.supply - before
        status = _fill_status(give, r.quantity)
        tier_label = f"tier {r.priority}" + (" (highest priority)" if r.priority == 1 else "")
        text = (
            f"{status}: {give} of {r.quantity} {unit}. Priority tiers are served from tier 1 "
            f"down; your organization was in {tier_label}, and your request was "
            f"#{pos} of {tier_sizes[r.priority]} in that tier by accepted order. Higher tiers "
            f"and earlier requests in your tier received {used_earlier} of the {lot.supply} "
            f"{unit}, leaving {before}."
        )
        lines.append(
            Line(
                request_id=r.request_id,
                organization_id=r.organization_id,
                requested=r.quantity,
                allocated=give,
                explanation=text,
                details={
                    "eligible": True,
                    "priority": r.priority,
                    "position_in_tier": pos,
                    "tier_requests": tier_sizes[r.priority],
                    "available_when_reached": before,
                    "used_by_earlier": used_earlier,
                },
            )
        )
    return finish(lot, lines)
