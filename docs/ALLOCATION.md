# Allocation strategies

Implementation: [backend/app/allocation/](../backend/app/allocation/). All strategies are
pure functions over one lot at a time: `LotInput(supply, requests) → LotResult(lines)`.
They run on the frozen snapshot written when a round closes, never on live tables.

## Common rules

| Topic | Rule |
|---|---|
| Units | Whole units of the lot's single unit (cases, boxes…). No fractional output. |
| Bounds | `0 ≤ allocated ≤ requested` per request; `Σ allocated ≤ supply` per lot. |
| Work-conserving | Every strategy allocates exactly `min(supply, eligible demand)`. |
| Ineligible requests | Receive 0 and the explanation "your organization was not eligible". They do not affect anyone else's share or order. |
| Zero supply | Everyone receives 0 ("Not filled"). |
| Empty input | No lines; the whole supply is returned to inventory. |
| Excess supply | Every eligible request is filled; the remainder is returned to inventory at finalization. |
| Determinism | Output depends only on the input values, never on input order. Ties use accepted order, then request id. |
| Invalid input | Negative supply, non-positive quantity, duplicate request ids or accepted positions, weight ≤ 0 (need-weighted), or tier outside 1–10 (priority) raise `AllocationInputError`. The API and DB constraints prevent these from being stored. |
| Accepted order | Assigned by the server under the round lock (see DECISIONS D3). Editing keeps the position. Withdrawing and resubmitting gets a new, later position. |

## 1. First come, first served (`fcfs`, v1)

Fill eligible requests in accepted order until supply runs out. The request that
exhausts supply may be partly filled; later requests get nothing.

*Example:* supply 60; requests #1 = 30, #2 = 20, #3 = 20 → 30, 20, 10.

*Who benefits:* organizations that respond quickly, often the best-staffed. *Behaviour
it encourages:* submitting early and asking for the maximum. It ignores need.

## 2. Need-weighted share (`need_weighted`, v1)

Weighted water-filling with exact rational arithmetic (`fractions.Fraction`):

1. If supply ≥ total eligible demand, fill everyone.
2. Otherwise repeat: offer each still-unsatisfied request
   `remaining_supply × weight ÷ total weight of unsatisfied requests`. Cap every request
   whose offer reaches its requested quantity. Remove those requests and return their
   excess to `remaining_supply`. When no request is capped, the offers are final.
3. **Largest remainder:** floor every exact share. Hand out the leftover units
   (`supply − Σ floors`), one each, to the largest fractional parts. Ties go to the earlier
   accepted request, then the lower request id.

The sum of the fractional parts equals the leftover count and each part is < 1. That
guarantees enough candidates, and the +1 never pushes a request above its demand.

*Examples (hand-computed in
[test_allocation_examples.py](../backend/tests/unit/test_allocation_examples.py)):*

* Supply 100, weights 1/2/2, requests 10/80/50. Pass 1 offers 20/40/40, so #1 is
  capped at 10. Pass 2 shares 90 by 2:2, giving 45/45. **Result: 10/45/45.**
* Supply 10 split equally among three: 3⅓ each, floors 3/3/3, one leftover unit, all
  remainders tied. The earliest request gets it. **Result: 4/3/3.**

*Who benefits:* organizations the administrator assigns higher need weights. Timing
does not matter. *Behaviour it encourages:* accurate weights matter most. Inflating a
request only helps until the request's cap is reached. Weights are an administrative
judgement, so they are validated (1–1000), require a reason, and are audited.

## 3. Priority tiers (`priority`, v1)

Tiers are integers 1–10. **Tier 1 is the highest priority and is served first.** Tiers
are served in ascending number. Within a tier, requests are served in accepted order (FCFS).

*Example:* supply 60. Tier-1 requests for 20 and 20, a tier-2 request for 10, and a
tier-3 request for 30 → 20, 20, 10, 10.

*Who benefits:* top-tier organizations, completely, before anyone else. *Behaviour it
encourages:* lower tiers may receive nothing in scarce rounds. Use it when strict
precedence is intended, such as infant formula for organizations serving infants.

## Comparing strategies

The demo seeds three finalized rounds with identical input: 60 cases, three requests
of 40, submitted Eastside → Riverside → Northside. Weights are E2/R1/N3 and tiers are
N1/E2/R3:

| Strategy | Eastside | Riverside | Northside |
|---|---|---|---|
| First come, first served | 40 | 20 | 0 |
| Need-weighted | 20 | 10 | 30 |
| Priority tiers | 20 | 0 | 40 |

No strategy is universally fair. Each one encodes a different value: responsiveness,
proportional need, or explicit precedence. Administrators can preview a closed round
under every strategy as a labelled what-if. Finalization always uses the strategy frozen
at close.

## Explanations

Each result line stores a plain-language `explanation` and structured `details`, for
example the position in order, the weight and total weight, the exact share as a
fraction, whether it was capped, and whether it received a remainder unit. Members see only
their own lines. Explanations for members contain aggregates such as "earlier requests
received 50 of the 60 cases" but never another organization's identity or individual values.

## Versioning

Strategies are registered as `(name, version)`. The snapshot records both, and the
`allocation_results` rows are the persisted outcome; they are never recomputed. Changing
behaviour requires a new version, and old versions must stay registered while any
unfinalized snapshot references them.

## Verification

* Hand-computed cases: [test_allocation_examples.py](../backend/tests/unit/test_allocation_examples.py).
* Property-based tests (Hypothesis, ~1,300 generated lots per run):
  [test_allocation_properties.py](../backend/tests/unit/test_allocation_properties.py). They check
  conservation, bounds, eligibility, input-order independence, the FCFS prefix property,
  priority tier precedence, and need-weighted shares against an independently computed
  weighted max-min oracle (each allocation is within one unit of the exact share).
