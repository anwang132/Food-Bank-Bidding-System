"""Need-weighted allocation: weighted water-filling with exact fractions, then
largest-remainder rounding.

1. If supply covers all eligible demand, every eligible request is filled.
2. Otherwise, repeat: give each still-unsatisfied request the share
   ``remaining_supply * weight / total_weight_of_unsatisfied``. Every request whose
   share reaches its requested quantity is capped at that quantity and removed;
   its excess goes back into ``remaining_supply`` for the next pass. When no request
   is capped, the shares are final.
3. Floor every exact share. Hand out the leftover whole units one at a time to the
   largest fractional remainders; ties go to the earlier accepted request, then the
   lower request id.

All arithmetic before step 3 uses ``fractions.Fraction``; there is no floating point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from app.allocation.types import (
    AllocationInputError,
    Line,
    LotInput,
    LotResult,
    RequestInput,
    accepted_order_key,
    finish,
    fmt_qty,
    ineligible_line,
    validate,
)


@dataclass
class _Share:
    exact: Fraction
    capped: bool
    pass_number: int
    weight_total: int


def _water_fill(supply: int, requests: list[RequestInput]) -> dict[int, _Share]:
    shares: dict[int, _Share] = {}
    remaining = Fraction(supply)
    active = sorted(requests, key=accepted_order_key)
    pass_number = 0
    while active:
        pass_number += 1
        total_weight = sum(r.weight for r in active)
        offered = {r.request_id: remaining * r.weight / total_weight for r in active}
        capped = [r for r in active if offered[r.request_id] >= r.quantity]
        if not capped:
            for r in active:
                shares[r.request_id] = _Share(
                    offered[r.request_id], False, pass_number, total_weight
                )
            break
        for r in capped:
            shares[r.request_id] = _Share(Fraction(r.quantity), True, pass_number, total_weight)
            remaining -= r.quantity
        capped_ids = {r.request_id for r in capped}
        active = [r for r in active if r.request_id not in capped_ids]
    return shares


def allocate_need_weighted(lot: LotInput) -> LotResult:
    validate(lot)
    eligible = [r for r in lot.requests if r.eligible]
    for r in eligible:
        if r.weight <= 0:
            raise AllocationInputError(f"request {r.request_id}: weight must be positive")
    lines = [ineligible_line(r) for r in lot.requests if not r.eligible]
    unit = lot.unit
    demand = sum(r.quantity for r in eligible)

    if lot.supply >= demand:
        for r in eligible:
            lines.append(
                Line(
                    request_id=r.request_id,
                    organization_id=r.organization_id,
                    requested=r.quantity,
                    allocated=r.quantity,
                    explanation=(
                        f"Fully filled: {r.quantity} of {r.quantity} {unit}. Total eligible "
                        f"requests ({demand} {unit}) did not exceed the supply "
                        f"({lot.supply} {unit}), so no weighting was needed."
                    ),
                    details={"eligible": True, "weight": r.weight, "supply_covered_demand": True},
                )
            )
        return finish(lot, lines)

    shares = _water_fill(lot.supply, eligible)
    floors = {rid: math.floor(s.exact) for rid, s in shares.items()}
    leftover = lot.supply - sum(floors.values())
    by_id = {r.request_id: r for r in eligible}
    ranked = sorted(
        (rid for rid, s in shares.items() if s.exact != floors[rid]),
        key=lambda rid: (
            -(shares[rid].exact - floors[rid]),
            by_id[rid].accepted_seq,
            rid,
        ),
    )
    # Sum of fractional parts equals `leftover`, and each part is < 1, so there are
    # always enough candidates and no request is pushed above its demand.
    assert 0 <= leftover <= len(ranked)
    bonus = set(ranked[:leftover])

    for r in eligible:
        s = shares[r.request_id]
        give = floors[r.request_id] + (1 if r.request_id in bonus else 0)
        if s.capped:
            text = (
                f"Fully filled: {give} of {r.quantity} {unit}. Supply is shared in proportion "
                f"to need weight; your weight of {r.weight} out of {s.weight_total} (total "
                f"weight of requests still needing supply) gave a share at least as large as "
                f"your request, so you received all of it and the excess was shared among the "
                f"other requests."
            )
        else:
            status = "Partly filled" if give > 0 else "Not filled"
            text = (
                f"{status}: {give} of {r.quantity} {unit}. Supply is shared in proportion to "
                f"need weight; after fully filling smaller requests, your weight of {r.weight} "
                f"out of {s.weight_total} gave an exact share of {fmt_qty(s.exact)} {unit}. "
                f"Whole units are assigned by rounding down"
            )
            if r.request_id in bonus:
                text += (
                    ", and your request received one of the leftover units because its "
                    "fractional part was among the largest."
                )
            elif s.exact != floors[r.request_id]:
                text += (
                    "; leftover units went to requests with larger fractional parts "
                    "(ties go to the earlier request)."
                )
            else:
                text += "."
        lines.append(
            Line(
                request_id=r.request_id,
                organization_id=r.organization_id,
                requested=r.quantity,
                allocated=give,
                explanation=text,
                details={
                    "eligible": True,
                    "weight": r.weight,
                    "weight_total": s.weight_total,
                    "exact_share": f"{s.exact.numerator}/{s.exact.denominator}",
                    "exact_share_display": fmt_qty(s.exact),
                    "capped_at_request": s.capped,
                    "pass": s.pass_number,
                    "rounded_down": floors[r.request_id],
                    "remainder_unit": r.request_id in bonus,
                },
            )
        )
    return finish(lot, lines)
