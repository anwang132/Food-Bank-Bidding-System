"""Food categories and storage types, inferred from free text.

Inventory lots have names and descriptions, not structured categories, so the
planner classifies them with keyword rules. The rules are ordered: the first
category whose keywords appear wins, so "canned tuna" is canned and "peanut butter"
is protein, not dairy."""

from __future__ import annotations

import re
from typing import Literal

Category = Literal["produce", "protein", "dairy", "grains", "canned", "baby", "hygiene", "other"]
Storage = Literal["dry", "cold", "frozen"]

CATEGORIES: tuple[Category, ...] = (
    "produce",
    "protein",
    "dairy",
    "grains",
    "canned",
    "baby",
    "hygiene",
    "other",
)

LABELS: dict[Category, str] = {
    "produce": "Fresh produce",
    "protein": "Protein",
    "dairy": "Dairy",
    "grains": "Grains and cereal",
    "canned": "Canned goods",
    "baby": "Baby care",
    "hygiene": "Hygiene",
    "other": "Other",
}

_RULES: tuple[tuple[Category, tuple[str, ...]], ...] = (
    ("baby", ("infant", "formula", "diaper", "baby", "wipes", "toddler")),
    (
        "hygiene",
        (
            "hygiene",
            "soap",
            "shampoo",
            "toothpaste",
            "toothbrush",
            "tampon",
            "sanitary",
            "deodorant",
            "detergent",
            "toilet paper",
        ),
    ),
    ("canned", ("canned", "can ", "cans", "tinned", "soup")),
    (
        "protein",
        (
            "chicken",
            "beef",
            "turkey",
            "pork",
            "fish",
            "tuna",
            "salmon",
            "egg",
            "peanut butter",
            "protein",
            "meat",
            "beans",
            "lentil",
            "tofu",
        ),
    ),
    ("dairy", ("milk", "cheese", "yogurt", "yoghurt", "dairy", "butter")),
    (
        "produce",
        (
            "produce",
            "fresh",
            "apple",
            "carrot",
            "potato",
            "onion",
            "vegetable",
            "fruit",
            "banana",
            "orange",
            "squash",
            "lettuce",
            "greens",
            "tomato",
            "pepper",
            "cabbage",
            "berries",
        ),
    ),
    (
        "grains",
        (
            "rice",
            "pasta",
            "oat",
            "cereal",
            "bread",
            "grain",
            "flour",
            "tortilla",
            "noodle",
            "quinoa",
            "cracker",
        ),
    ),
)

DIETARY_TERMS = (
    "halal",
    "kosher",
    "vegetarian",
    "vegan",
    "gluten-free",
    "low-sodium",
    "low sodium",
    "diabetic",
    "nut-free",
    "allergy",
    "lactose",
)

_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACE.sub(" ", text.lower()).strip()


def classify(name: str, description: str = "") -> Category:
    text = f" {normalize(name)} {normalize(description)} "
    for category, keywords in _RULES:
        if any(k in text for k in keywords):
            return category
    return "other"


def parse_category(value: str) -> Category | None:
    """Map a spreadsheet's category cell ("Produce", "baby care") to a category."""
    text = normalize(value)
    for category in CATEGORIES:
        if text == category or text == LABELS[category].lower():
            return category
    guessed = classify(text)
    return guessed if guessed != "other" else None


def storage_of(name: str, description: str, category: Category) -> Storage:
    text = normalize(f"{name} {description}")
    if "frozen" in text or "keep frozen" in text:
        return "frozen"
    if "shelf-stable" in text or "shelf stable" in text:
        return "dry"
    if category in ("produce", "dairy") or "refrigerat" in text:
        return "cold"
    return "dry"


def mentioned_categories(text: str) -> dict[str, int]:
    """How often each category's keywords appear in a text (for notes and documents)."""
    lowered = f" {normalize(text)} "
    counts: dict[str, int] = {}
    for category, keywords in _RULES:
        n = 0
        for keyword in keywords:
            if keyword in lowered:
                n += lowered.count(keyword)
                # Counted once: "peanut butter" must not also count as dairy.
                lowered = lowered.replace(keyword, " ")
        if n:
            counts[category] = n
    return counts


def dietary_terms(text: str) -> list[str]:
    lowered = normalize(text)
    found = {t.replace(" ", "-") for t in DIETARY_TERMS if t in lowered}
    return sorted(found)
