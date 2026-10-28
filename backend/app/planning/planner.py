"""Sample request plans: the simulated planning assistant (docs/DECISIONS.md D19).

``build_plans`` is a pure, deterministic function of a food bank's preferences,
the needs found in its documents and notes, its request history, and the open
rounds it can request from. It returns three alternative drafts and explains every
suggested quantity. Nothing here submits requests.

How a quantity is chosen for one lot:

1. lots in an avoided category are skipped;
2. a stated need that matches the lot (by name) sets the quantity;
   (without one, the food bank's current request in that round stands in for it);
3. otherwise a baseline is estimated from households served (or a small share of
   the offer when that is unknown), scaled by how the category is ranked;
4. the result is capped at ``max_share_percent`` of the offer;
5. lines are then trimmed, lowest priority first, to fit each storage capacity.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.allocation import LABELS as STRATEGY_LABELS
from app.planning.catalog import LABELS, Category, Storage, classify, normalize, storage_of
from app.schemas import (
    Plan,
    PlanInsights,
    PlanLine,
    PlanRound,
    Preferences,
)

HOUSEHOLDS_PER_UNIT = 25
NEUTRAL_WEIGHT = 0.6
FALLBACK_SHARE = 0.15
PERISHABLE_DAYS = 14
CURRENT_REQUEST = "your current request"

TIPS = {
    "fcfs": "First come, first served: requests are filled in the order they are "
    "accepted, so submitting early matters more than the amount.",
    "need_weighted": "Need-weighted: supply is shared in proportion to each food bank's "
    "need weight and never exceeds a request. Ask for what you can distribute.",
    "priority": "Priority tiers: higher tiers are filled first. Asking for more than you "
    "need can leave food banks in lower tiers short.",
}

# Words too generic to match a stated need to a lot by name.
_GENERIC = {
    "canned", "fresh", "frozen", "whole", "case", "cases", "box", "boxes", "bag", "bags",
    "size", "pack", "packs", "food", "items", "item", "the", "and", "with", "for",
}  # fmt: skip
_WORD = re.compile(r"[a-z]{3,}")


@dataclass(frozen=True)
class LotOption:
    round_id: int
    round_name: str
    strategy: str
    request_deadline: datetime
    lot_id: int
    name: str
    description: str
    unit: str
    offered: int
    best_before: date | None = None
    current_quantity: int | None = None
    current_request_id: int | None = None
    current_request_version: int | None = None


@dataclass(frozen=True)
class StatedNeed:
    item: str
    quantity: int | None
    category: Category
    source: str


@dataclass(frozen=True)
class HistoryLine:
    round_id: int
    category: Category
    storage: Storage
    offered: int
    requested: int
    allocated: int | None  # None while the round is not allocated


@dataclass(frozen=True)
class Variant:
    key: str
    title: str
    tagline: str
    include_neutral: bool  # lines with neither a stated need nor a ranked category
    scale: float  # multiplier on household-based estimates
    need_cover: float  # share of a stated need requested in a ranked category
    other_need_cover: float  # share of a stated need requested in any other category
    ranked_floor: bool = False  # ranked lines ask for max(stated need, estimate)
    priority_boost: float = 1.0
    share_factor: float = 1.0  # multiplier on the max_share_percent cap


VARIANTS = (
    Variant(
        "balanced",
        "Balanced",
        "Covers your stated needs, plus a fair share of everything else on offer.",
        include_neutral=True,
        scale=1.0,
        need_cover=1.0,
        other_need_cover=1.0,
    ),
    Variant(
        "priorities",
        "Priorities first",
        "Goes further on your top-ranked categories and halves everything else.",
        include_neutral=False,
        scale=1.0,
        need_cover=1.0,
        other_need_cover=0.5,
        ranked_floor=True,
        priority_boost=1.25,
    ),
    Variant(
        "lean",
        "Lean",
        "Smaller requests that leave more for others and fit tight storage.",
        include_neutral=False,
        scale=0.6,
        need_cover=0.75,
        other_need_cover=0.5,
        share_factor=0.5,
    ),
)


@dataclass
class _Draft:
    option: LotOption
    category: Category
    storage: Storage
    score: float
    quantity: int
    from_need: bool
    reasons: list[str] = field(default_factory=list)


def tokens(text: str) -> set[str]:
    words = {w for w in _WORD.findall(normalize(text)) if w not in _GENERIC}
    # Light stemming so "apples" matches "apple" and "diapers" matches "diaper".
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words}


def match_needs(option: LotOption, needs: Sequence[StatedNeed]) -> list[StatedNeed]:
    lot_words = tokens(option.name)
    return [n for n in needs if lot_words & tokens(n.item)]


def _rank_weight(category: Category, prefs: Preferences) -> float | None:
    """None: avoided. Rank 1 → 1.5, then 0.15 less per rank, never below 1.0."""
    if category in prefs.avoid_categories:
        return None
    if category in prefs.priority_categories:
        return max(1.0, 1.5 - 0.15 * prefs.priority_categories.index(category))
    return NEUTRAL_WEIGHT


def _draft_line(
    option: LotOption,
    prefs: Preferences,
    needs: Sequence[StatedNeed],
    variant: Variant,
) -> _Draft | None:
    category = classify(option.name, option.description)
    storage = storage_of(option.name, option.description, category)
    weight = _rank_weight(category, prefs)
    if weight is None:
        return None
    matched = match_needs(option, needs)
    quantified = [n for n in matched if n.quantity]
    from_documents = bool(quantified)
    if not quantified and option.current_quantity:
        # What the food bank already asked for is the best signal of need when
        # no document lists a quantity; never shrink it on defaults alone.
        current = StatedNeed(option.name, option.current_quantity, category, CURRENT_REQUEST)
        matched, quantified = [*matched, current], [current]
    ranked = category in prefs.priority_categories
    if not variant.include_neutral and not ranked and not matched:
        return None

    reasons: list[str] = []
    if ranked:
        rank = prefs.priority_categories.index(category) + 1
        reasons.append(f"{LABELS[category]} is priority #{rank} in your preferences")

    if prefs.households_served > 0:
        base = prefs.households_served / HOUSEHOLDS_PER_UNIT
        basis = (
            f"your {prefs.households_served:,} households "
            f"(about one {option.unit.rstrip('s')} per {HOUSEHOLDS_PER_UNIT})"
        )
    else:
        base = option.offered * FALLBACK_SHARE
        basis = (
            f"{round(FALLBACK_SHARE * 100)}% of the offer "
            "(add households served for a sharper estimate)"
        )
    boost = variant.priority_boost if ranked else 1.0
    estimate = math.ceil(base * weight * boost * variant.scale)

    if quantified:
        need = max(quantified, key=lambda n: n.quantity or 0)
        cover = variant.need_cover if ranked else variant.other_need_cover
        quantity = math.ceil((need.quantity or 0) * cover)
        if need.source == CURRENT_REQUEST:
            reasons.append(f"Based on your current request of {need.quantity}")
        else:
            reasons.append(f"“{need.item}: {need.quantity}” in {need.source}")
        if cover < 1:
            reasons.append(f"Covers {round(cover * 100)}% of the stated need")
        if ranked and variant.ranked_floor and estimate > quantity:
            quantity = estimate
            reasons.append(f"Raised to the estimate for {basis}")
        score = 3.0 + weight
    else:
        quantity = estimate
        reasons.append(f"Estimated from {basis}")
        if matched:
            reasons.append(f"Mentioned in {matched[0].source}")
            score = 2.0 + weight
        else:
            score = weight
    if not ranked and not matched:
        reasons.append(f"{LABELS[category]} is not one of your priorities, so the ask is modest")

    share = max(1, round(prefs.max_share_percent * variant.share_factor))
    cap = max(1, math.floor(option.offered * share / 100))
    if quantity > cap:
        quantity = cap
        reasons.append(f"Capped at {share}% of the {option.offered} {option.unit} offered")
    quantity = max(0, min(quantity, option.offered))
    if option.best_before is not None:
        days = (option.best_before - option.request_deadline.date()).days
        if days <= PERISHABLE_DAYS:
            when = f"{option.best_before:%b} {option.best_before.day}"
            reasons.append(f"Best before {when}; plan to distribute within {max(days, 1)} days")
    return _Draft(option, category, storage, score, quantity, from_documents, reasons)


def _fit_storage(drafts: list[_Draft], prefs: Preferences) -> list[str]:
    """Trim lowest-score lines first so each storage type fits its capacity."""
    capacity: dict[Storage, int | None] = {
        "dry": prefs.dry_storage_capacity,
        "cold": prefs.cold_storage_capacity,
        "frozen": prefs.frozen_storage_capacity,
    }
    warnings: list[str] = []
    for storage, limit in capacity.items():
        if limit is None:
            continue
        group = sorted(
            (d for d in drafts if d.storage == storage),
            key=lambda d: (-d.score, d.option.request_deadline, d.option.lot_id),
        )
        remaining = limit
        for d in group:
            if d.quantity <= remaining:
                remaining -= d.quantity
                continue
            if remaining > 0:
                d.reasons.append(f"Trimmed from {d.quantity} to fit {storage} storage ({limit})")
            else:
                warnings.append(
                    f"Left out {d.option.name}: {storage} storage ({limit}) is already full"
                )
            d.quantity = remaining
            remaining = 0
    return warnings


def build_plan(
    variant: Variant,
    org_name: str,
    prefs: Preferences,
    options: Sequence[LotOption],
    needs: Sequence[StatedNeed],
    insights: PlanInsights,
) -> Plan:
    drafts = [d for o in options if (d := _draft_line(o, prefs, needs, variant)) is not None]
    warnings = _fit_storage(drafts, prefs)
    kept = [d for d in drafts if d.quantity > 0]

    by_round: dict[int, list[_Draft]] = defaultdict(list)
    for d in kept:
        by_round[d.option.round_id].append(d)
    rounds = []
    for round_id, lines in sorted(
        by_round.items(), key=lambda kv: (kv[1][0].option.request_deadline, kv[0])
    ):
        lines.sort(key=lambda d: (-d.score, d.option.name, d.option.lot_id))
        first = lines[0].option
        rounds.append(
            PlanRound(
                round_id=round_id,
                round_name=first.round_name,
                strategy=first.strategy,
                strategy_label=STRATEGY_LABELS.get(first.strategy, first.strategy),
                request_deadline=first.request_deadline,
                tip=TIPS.get(first.strategy, ""),
                lines=[
                    PlanLine(
                        round_id=round_id,
                        lot_id=d.option.lot_id,
                        lot_name=d.option.name,
                        unit=d.option.unit,
                        category=d.category,
                        storage=d.storage,
                        offered=d.option.offered,
                        suggested=d.quantity,
                        current_quantity=d.option.current_quantity,
                        current_request_id=d.option.current_request_id,
                        current_request_version=d.option.current_request_version,
                        reasons=d.reasons,
                    )
                    for d in lines
                ],
            )
        )
    total = sum(d.quantity for d in kept)
    return Plan(
        key=variant.key,
        title=variant.title,
        tagline=variant.tagline,
        summary=_summary(variant, org_name, prefs, kept, len(rounds), total, insights),
        total_units=total,
        line_count=len(kept),
        rounds=rounds,
        warnings=warnings,
    )


def build_plans(
    org_name: str,
    prefs: Preferences,
    options: Sequence[LotOption],
    needs: Sequence[StatedNeed],
    insights: PlanInsights,
) -> list[Plan]:
    return [build_plan(v, org_name, prefs, options, needs, insights) for v in VARIANTS]


def _summary(
    variant: Variant,
    org_name: str,
    prefs: Preferences,
    kept: list[_Draft],
    round_count: int,
    total: int,
    insights: PlanInsights,
) -> str:
    if not kept:
        return (
            f"No requests suggested for {org_name}: nothing in the open rounds matches "
            "this plan's focus and your preferences."
        )
    parts = [
        f"{variant.title} plan for {org_name}: {_count(len(kept), 'request')} totalling "
        f"{total:,} units across {_count(round_count, 'open round')}."
    ]
    if prefs.priority_categories:
        top = [LABELS[c].lower() for c in prefs.priority_categories[:2]]
        parts.append(f"It leans toward {' and '.join(top)}, your top priorities.")
    if prefs.avoid_categories:
        avoided = [LABELS[c].lower() for c in prefs.avoid_categories]
        parts.append(f"It leaves out {', '.join(avoided)}, as you asked.")
    from_docs = [d for d in kept if d.from_need]
    if from_docs:
        parts.append(
            f"{_count(len(from_docs), 'line')} follow quantities from your documents and notes."
        )
    if insights.fill_rate_percent is not None and insights.rounds_participated:
        parts.append(
            f"Across your last {_count(insights.rounds_participated, 'allocated round')} you "
            f"received {insights.fill_rate_percent}% of what you requested."
        )
    return " ".join(parts)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


# ---------------------------------------------------------------- history


def insights_from(history: Sequence[HistoryLine]) -> PlanInsights:
    done = [h for h in history if h.allocated is not None]
    requested = sum(h.requested for h in done)
    allocated = sum(h.allocated or 0 for h in done)
    by_category: dict[str, int] = defaultdict(int)
    for h in history:
        by_category[h.category] += h.requested
    top = sorted(by_category, key=lambda c: (-by_category[c], c))[:3]
    return PlanInsights(
        rounds_participated=len({h.round_id for h in done}),
        requested_total=requested,
        allocated_total=allocated,
        fill_rate_percent=round(allocated * 100 / requested) if requested else None,
        top_categories=top,
    )


def suggest_preferences(
    current: Preferences,
    history: Sequence[HistoryLine],
    offered_categories: dict[Category, int],
    dietary: Sequence[str],
) -> tuple[Preferences, dict[str, str]]:
    """Simulated preferences inferred from request history and documents. Fields the
    history says nothing about keep their current values."""
    rationale: dict[str, str] = {}
    if not history:
        rationale["overall"] = "No request history yet, so your current preferences are kept."
        if dietary:
            notes = f"Mentioned in your documents: {', '.join(dietary)}."
            rationale["dietary_notes"] = "Dietary terms found in your documents and notes."
            return current.model_copy(update={"dietary_notes": notes}), rationale
        return current, rationale

    by_category: dict[Category, int] = defaultdict(int)
    per_round_storage: dict[tuple[int, Storage], int] = defaultdict(int)
    lines_per_round: dict[int, int] = defaultdict(int)
    max_share = 0.0
    for h in history:
        by_category[h.category] += h.requested
        per_round_storage[(h.round_id, h.storage)] += h.requested
        lines_per_round[h.round_id] += 1
        if h.offered:
            max_share = max(max_share, h.requested / h.offered)

    priorities = sorted(by_category, key=lambda c: (-by_category[c], c))[:3]
    rationale["priority_categories"] = "Your most-requested categories: " + ", ".join(
        f"{LABELS[c].lower()} ({by_category[c]:,})" for c in priorities
    )
    avoid = sorted(
        c for c, times in offered_categories.items() if times >= 2 and c not in by_category
    )
    if avoid:
        rationale["avoid_categories"] = (
            "Offered in at least two rounds you took part in, but you never requested them."
        )

    average_line = sum(h.requested for h in history) / len(history)
    households = int(round(average_line * HOUSEHOLDS_PER_UNIT / 50) * 50)
    rationale["households_served"] = (
        f"Your average request is about {average_line:.0f} units; at roughly one unit per "
        f"{HOUSEHOLDS_PER_UNIT} households that suggests {households:,} households."
    )

    def capacity(storage: Storage) -> int | None:
        peaks = [q for (_, s), q in per_round_storage.items() if s == storage]
        if not peaks:
            return None
        return int(math.ceil(max(peaks) * 1.25 / 5) * 5)

    # Storage is a physical fact: a stated capacity wins over one inferred from history.
    storage = {
        "dry": current.dry_storage_capacity or capacity("dry"),
        "cold": current.cold_storage_capacity or capacity("cold"),
        "frozen": current.frozen_storage_capacity or capacity("frozen"),
    }
    rationale["storage"] = (
        "Your stated capacities are kept; any missing one is your largest request of that "
        "storage type in a single round, plus 25% headroom."
    )
    share = int(min(60, max(10, math.ceil(max_share * 100 / 5) * 5)))
    rationale["max_share_percent"] = (
        f"The largest share of a single lot you have requested was {round(max_share * 100)}%."
    )
    notes = current.dietary_notes
    if dietary:
        notes = f"Mentioned in your documents: {', '.join(dietary)}."
        rationale["dietary_notes"] = "Dietary terms found in your documents and notes."
    suggested = Preferences(
        households_served=households,
        priority_categories=priorities,
        avoid_categories=avoid,
        dry_storage_capacity=storage["dry"],
        cold_storage_capacity=storage["cold"],
        frozen_storage_capacity=storage["frozen"],
        max_share_percent=share,
        dietary_notes=notes,
    )
    return suggested, rationale
