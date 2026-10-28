"""Property-based checks for all strategies: conservation, bounds, eligibility,
determinism, plus strategy-specific ordering properties and an independent oracle
for the need-weighted exact shares."""

import math
import random
from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

from app.allocation import CURRENT_VERSION, LotInput, RequestInput, run


@st.composite
def lots(draw):
    n = draw(st.integers(min_value=0, max_value=12))
    seqs = draw(st.lists(st.integers(1, 10_000), min_size=n, max_size=n, unique=True))
    requests = tuple(
        RequestInput(
            request_id=i + 1,
            organization_id=i + 1,
            quantity=draw(st.integers(1, 200)),
            accepted_seq=seqs[i],
            eligible=draw(st.booleans()) if draw(st.integers(0, 4)) == 0 else True,
            weight=draw(st.integers(1, 20)),
            priority=draw(st.integers(1, 10)),
        )
        for i in range(n)
    )
    supply = draw(st.integers(0, 600))
    return LotInput(lot_id=7, unit="boxes", supply=supply, requests=requests)


STRATEGIES = sorted(CURRENT_VERSION)


def _alloc(strategy, lot):
    [result] = run(strategy, CURRENT_VERSION[strategy], [lot])
    return result


@settings(max_examples=300, deadline=None)
@given(lot=lots(), strategy=st.sampled_from(STRATEGIES))
def test_conservation_bounds_and_eligibility(lot, strategy):
    result = _alloc(strategy, lot)
    eligible_demand = sum(r.quantity for r in lot.requests if r.eligible)
    by_id = {r.request_id: r for r in lot.requests}
    # Every request gets exactly one line.
    assert sorted(line.request_id for line in result.lines) == sorted(by_id)
    # Work-conserving: allocate min(supply, eligible demand); the rest is unallocated.
    assert result.allocated_total == min(lot.supply, eligible_demand)
    assert result.allocated_total + result.unallocated == lot.supply
    for line in result.lines:
        r = by_id[line.request_id]
        assert 0 <= line.allocated <= r.quantity
        if not r.eligible:
            assert line.allocated == 0
        assert line.explanation


@settings(max_examples=200, deadline=None)
@given(lot=lots(), strategy=st.sampled_from(STRATEGIES), seed=st.integers(0, 2**32))
def test_deterministic_regardless_of_input_order(lot, strategy, seed):
    shuffled = list(lot.requests)
    random.Random(seed).shuffle(shuffled)
    a = _alloc(strategy, lot)
    b = _alloc(strategy, LotInput(lot.lot_id, lot.unit, lot.supply, tuple(shuffled)))
    assert a == b


@settings(max_examples=200, deadline=None)
@given(lot=lots())
def test_fcfs_prefix_property(lot):
    """Once a request is short-changed, every later eligible request gets nothing."""
    result = _alloc("fcfs", lot)
    by_id = {r.request_id: r for r in lot.requests}
    ordered = sorted(
        (line for line in result.lines if by_id[line.request_id].eligible),
        key=lambda line: by_id[line.request_id].accepted_seq,
    )
    short = False
    for line in ordered:
        if short:
            assert line.allocated == 0
        if line.allocated < line.requested:
            short = True


@settings(max_examples=200, deadline=None)
@given(lot=lots())
def test_priority_never_serves_lower_tier_before_higher_tier_is_full(lot):
    result = _alloc("priority", lot)
    by_id = {r.request_id: r for r in lot.requests}
    eligible = [line for line in result.lines if by_id[line.request_id].eligible]
    for a in eligible:
        for b in eligible:
            ra, rb = by_id[a.request_id], by_id[b.request_id]
            if ra.priority < rb.priority and b.allocated > 0:
                assert a.allocated == a.requested


def _oracle_shares(supply, requests):
    """Independent weighted max-min fair shares: find λ with Σ min(q, λw) = supply."""
    demand = sum(r.quantity for r in requests)
    if supply >= demand:
        return {r.request_id: Fraction(r.quantity) for r in requests}
    remaining = Fraction(supply)
    weight = sum(r.weight for r in requests)
    lam = Fraction(0)
    for r in sorted(requests, key=lambda r: Fraction(r.quantity, r.weight)):
        cap = Fraction(r.quantity, r.weight)
        if remaining <= cap * weight:
            lam = remaining / weight
            break
        remaining -= r.quantity
        weight -= r.weight
    return {r.request_id: min(Fraction(r.quantity), lam * r.weight) for r in requests}


@settings(max_examples=300, deadline=None)
@given(lot=lots())
def test_need_weighted_matches_independent_oracle_within_one_unit(lot):
    result = _alloc("need_weighted", lot)
    eligible = [r for r in lot.requests if r.eligible]
    exact = _oracle_shares(lot.supply, eligible)
    for line in result.lines:
        if line.request_id in exact:
            share = exact[line.request_id]
            assert math.floor(share) <= line.allocated <= math.ceil(share)
            if "exact_share" in line.details:
                num, den = map(int, line.details["exact_share"].split("/"))
                assert Fraction(num, den) == share
