"""Hand-computed reference cases. Each docstring shows the arithmetic."""

import pytest

from app.allocation import CURRENT_VERSION, AllocationInputError, LotInput, RequestInput, run
from app.allocation.need_weighted import allocate_need_weighted
from app.allocation.sequential import allocate_fcfs, allocate_priority


def req(rid, qty, seq=None, *, eligible=True, weight=1, priority=5, org=None):
    return RequestInput(
        request_id=rid,
        organization_id=org if org is not None else rid,
        quantity=qty,
        accepted_seq=seq if seq is not None else rid,
        eligible=eligible,
        weight=weight,
        priority=priority,
    )


def lot(supply, *requests):
    return LotInput(lot_id=1, unit="cases", supply=supply, requests=tuple(requests))


def allocated(result):
    return {line.request_id: line.allocated for line in result.lines}


# ---------------------------------------------------------------- FCFS


def test_fcfs_fills_in_accepted_order():
    """Supply 60: #1 asks 30 → 30 (30 left); #2 asks 20 → 20 (10 left); #3 asks 20 → 10."""
    result = allocate_fcfs(lot(60, req(1, 30), req(2, 20), req(3, 20)))
    assert allocated(result) == {1: 30, 2: 20, 3: 10}
    assert result.unallocated == 0
    partial = result.lines[2]
    assert partial.explanation.startswith("Partly filled: 10 of 20 cases")
    assert "#3 of 3" in partial.explanation


def test_fcfs_uses_accepted_seq_not_request_id():
    """Request 9 was accepted first (seq 1), so it is filled before request 2 (seq 2)."""
    result = allocate_fcfs(lot(10, req(2, 10, seq=2), req(9, 10, seq=1)))
    assert allocated(result) == {9: 10, 2: 0}


def test_fcfs_ineligible_skipped_and_explained():
    result = allocate_fcfs(lot(10, req(1, 10, eligible=False), req(2, 10)))
    assert allocated(result) == {1: 0, 2: 10}
    assert "not eligible" in result.lines[0].explanation


def test_fcfs_excess_supply_is_unallocated():
    result = allocate_fcfs(lot(100, req(1, 30), req(2, 20)))
    assert allocated(result) == {1: 30, 2: 20}
    assert result.unallocated == 50


# ---------------------------------------------------------------- priority


def test_priority_serves_tier_one_first_then_accepted_order():
    """Supply 60. Order: tier1 seq2 (20), tier1 seq3 (20), tier2 seq4 (10), tier3 seq1 (30→10)."""
    result = allocate_priority(
        lot(
            60,
            req(1, 30, priority=3),
            req(2, 20, priority=1),
            req(3, 20, priority=1),
            req(4, 10, priority=2),
        )
    )
    assert allocated(result) == {1: 10, 2: 20, 3: 20, 4: 10}
    tier3 = next(line for line in result.lines if line.request_id == 1)
    assert "tier 3" in tier3.explanation and "Partly filled" in tier3.explanation


def test_priority_rejects_out_of_range_tier():
    with pytest.raises(AllocationInputError):
        allocate_priority(lot(10, req(1, 5, priority=0)))


# ---------------------------------------------------------------- need-weighted


def test_need_weighted_caps_and_redistributes():
    """Supply 100, weights 1/2/2, asks 10/80/50.
    Pass 1: total weight 5 → offers 20, 40, 40; #1 capped at 10, 90 remain.
    Pass 2: total weight 4 → 45, 45; neither capped. Result 10/45/45."""
    result = allocate_need_weighted(
        lot(100, req(1, 10, weight=1), req(2, 80, weight=2), req(3, 50, weight=2))
    )
    assert allocated(result) == {1: 10, 2: 45, 3: 45}
    assert result.lines[0].details["capped_at_request"] is True
    assert result.lines[1].details["exact_share"] == "45/1"


def test_need_weighted_cascading_caps():
    """Supply 30, weights 5/3/2, asks 5/12/40.
    Pass 1 (W=10): 15, 9, 6 → #1 capped at 5; 25 remain.
    Pass 2 (W=5): 15, 10 → #2 capped at 12; 13 remain.
    Pass 3 (W=2): 13 → #3 gets 13."""
    result = allocate_need_weighted(
        lot(30, req(1, 5, weight=5), req(2, 12, weight=3), req(3, 40, weight=2))
    )
    assert allocated(result) == {1: 5, 2: 12, 3: 13}
    assert [line.details["pass"] for line in result.lines] == [1, 2, 3]


def test_need_weighted_largest_remainder_tie_goes_to_earlier_request():
    """Supply 10 split equally among 3: 10/3 each → floors 3/3/3, 1 leftover.
    All remainders are 1/3; the earliest accepted request (seq 1) gets it."""
    result = allocate_need_weighted(
        lot(10, req(1, 10, seq=3), req(2, 10, seq=1), req(3, 10, seq=2))
    )
    assert allocated(result) == {1: 3, 2: 4, 3: 3}
    winner = next(line for line in result.lines if line.request_id == 2)
    assert winner.details["remainder_unit"] is True
    assert winner.details["exact_share_display"] == "3.33"


def test_need_weighted_largest_remainder_prefers_larger_fraction():
    """Supply 7, weights 2/1: 14/3 = 4.67, 7/3 = 2.33 → floors 4/2, leftover to 0.67 → 5/2."""
    result = allocate_need_weighted(lot(7, req(1, 10, weight=2), req(2, 10, weight=1)))
    assert allocated(result) == {1: 5, 2: 2}


def test_need_weighted_supply_covers_demand():
    result = allocate_need_weighted(lot(100, req(1, 30, weight=1), req(2, 20, weight=9)))
    assert allocated(result) == {1: 30, 2: 20}
    assert result.unallocated == 50


def test_need_weighted_ineligible_excluded_from_weights():
    """The ineligible weight-100 request does not dilute the others: 10 split 1:1 → 5/5."""
    result = allocate_need_weighted(
        lot(10, req(1, 10, weight=1), req(2, 10, weight=1), req(3, 10, weight=100, eligible=False))
    )
    assert allocated(result) == {1: 5, 2: 5, 3: 0}


def test_need_weighted_invalid_weight_rejected():
    with pytest.raises(AllocationInputError):
        allocate_need_weighted(lot(10, req(1, 5, weight=0)))


# ---------------------------------------------------------------- edge cases, all strategies


@pytest.mark.parametrize("strategy", sorted(CURRENT_VERSION))
def test_zero_supply(strategy):
    [result] = run(strategy, 1, [lot(0, req(1, 5), req(2, 5))])
    assert allocated(result) == {1: 0, 2: 0}
    assert all(line.explanation.startswith("Not filled") for line in result.lines)


@pytest.mark.parametrize("strategy", sorted(CURRENT_VERSION))
def test_empty_input(strategy):
    [result] = run(strategy, 1, [lot(25)])
    assert result.lines == ()
    assert result.unallocated == 25


@pytest.mark.parametrize("strategy", sorted(CURRENT_VERSION))
def test_invalid_inputs_rejected(strategy):
    with pytest.raises(AllocationInputError):
        run(strategy, 1, [lot(-1, req(1, 5))])
    with pytest.raises(AllocationInputError):
        run(strategy, 1, [lot(5, req(1, 0))])
    with pytest.raises(AllocationInputError):
        run(strategy, 1, [lot(5, req(1, 5, seq=1), req(2, 5, seq=1))])


def test_unknown_strategy_version_rejected():
    with pytest.raises(AllocationInputError):
        run("fcfs", 99, [lot(5)])


def test_strategies_differ_on_same_input():
    """Same lot under each strategy — the seeded demo uses this scenario."""
    requests = (
        req(1, 40, weight=1, priority=3),  # earliest, low need, low priority
        req(2, 40, weight=3, priority=1),
        req(3, 40, weight=2, priority=2),
    )
    fcfs, nw, prio = (
        run(s, 1, [lot(60, *requests)])[0] for s in ("fcfs", "need_weighted", "priority")
    )
    assert allocated(fcfs) == {1: 40, 2: 20, 3: 0}
    assert allocated(nw) == {1: 10, 2: 30, 3: 20}
    assert allocated(prio) == {1: 0, 2: 40, 3: 20}
