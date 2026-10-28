"""Planner and document parser: pure functions, no database."""

import io
import zipfile
from datetime import UTC, date, datetime, timedelta

from openpyxl import Workbook

from app.planning import parsing
from app.planning.catalog import classify, mentioned_categories, storage_of
from app.planning.planner import (
    HistoryLine,
    LotOption,
    StatedNeed,
    build_plans,
    insights_from,
    suggest_preferences,
)
from app.schemas import PlanInsights, Preferences

DEADLINE = datetime(2026, 11, 2, 17, tzinfo=UTC)
NO_HISTORY = PlanInsights(
    rounds_participated=0,
    requested_total=0,
    allocated_total=0,
    fill_rate_percent=None,
    top_categories=[],
)


def lot(lot_id, name, offered, *, description="", unit="cases", round_id=1, best_before=None):
    return LotOption(
        round_id=round_id,
        round_name=f"Round {round_id}",
        strategy="need_weighted",
        request_deadline=DEADLINE,
        lot_id=lot_id,
        name=name,
        description=description,
        unit=unit,
        offered=offered,
        best_before=best_before,
    )


def plan(key, plans):
    return next(p for p in plans if p.key == key)


def lines(p):
    return {line.lot_name: line for r in p.rounds for line in r.lines}


# ---------------------------------------------------------------- catalog


def test_classifier_uses_rule_order():
    assert classify("Canned tuna") == "canned"
    assert classify("Peanut butter") == "protein"
    assert classify("Shelf-stable milk") == "dairy"
    assert classify("Fresh apples") == "produce"
    assert classify("Infant formula") == "baby"
    assert classify("Hygiene kits") == "hygiene"
    assert classify("Mystery pallet") == "other"
    assert storage_of("Frozen chicken thighs", "", "protein") == "frozen"
    assert storage_of("Shelf-stable milk", "", "dairy") == "dry"
    assert storage_of("Fresh apples", "", "produce") == "cold"
    assert mentioned_categories("peanut butter and peanut butter") == {"protein": 2}


# ---------------------------------------------------------------- parsing


def test_markdown_note_lines_and_tables_become_needs():
    a = parsing.analyze_text(
        "Gaps:\n- Infant formula: 30 cases\n1. Rice — 40\n12 boxes of fresh apples\n"
        "Halal and low sodium please.\n\n| Item | Cases needed |\n|---|---|\n| Diapers | 15 |\n"
    ).as_dict()
    needs = {(n["item"], n["quantity"], n["category"]) for n in a["needs"]}
    assert needs == {
        ("Infant formula", 30, "baby"),
        ("Rice", 40, "grains"),
        ("fresh apples", 12, "produce"),
        ("Diapers", 15, "baby"),
    }
    assert a["columns"] == ["Item", "Cases needed"]
    assert a["dietary"] == ["halal", "low-sodium"]


def test_csv_with_semicolons_and_category_column():
    a = parsing.analyze_file(
        "needs.csv", b"Product;Type;Qty\nBlack beans;Protein;1,200\nOats;;8\n;;\n"
    )
    assert a.format == "csv"
    assert [(n.item, n.quantity, n.category) for n in a.needs] == [
        ("Black beans", 1200, "protein"),
        ("Oats", 8, "grains"),
    ]


def test_xlsx_workbook_is_parsed_across_sheets():
    wb = Workbook()
    ws = wb.active
    ws.title = "Cover"
    ws.append(["Northside needs"])
    sheet = wb.create_sheet("Needs")
    sheet.append(["Item", "Cases needed"])
    sheet.append(["Peanut butter", 24.0])
    sheet.append(["Canned tomatoes", 10])
    buf = io.BytesIO()
    wb.save(buf)
    a = parsing.analyze_file("Needs.XLSX", buf.getvalue())
    assert a.format == "xlsx" and a.sheets == ["Cover", "Needs"]
    assert [(n.item, n.quantity) for n in a.needs] == [
        ("Peanut butter", 24),
        ("Canned tomatoes", 10),
    ]
    assert a.columns == ["Item", "Cases needed"]


def test_untrusted_files_are_bounded(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_XLSX_UNCOMPRESSED", 1000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/huge.xml", "a" * 10_000)  # compresses to a few bytes
    assert "too large" in parsing.analyze_file("bomb.xlsx", buf.getvalue()).warnings[0]
    assert "not a valid" in parsing.analyze_file("fake.xlsx", b"not a zip").warnings[0]
    binary = parsing.analyze_file("photo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    assert binary.format == "binary" and not binary.needs
    text_with_nul = parsing.analyze_file("notes.txt", b"abc\x00def")
    assert text_with_nul.format == "binary"
    latin = parsing.analyze_file("notes.txt", "Jalapeño peppers: 5".encode("cp1252"))
    assert latin.needs[0].quantity == 5 and "Windows-1252" in latin.warnings[0]


# ---------------------------------------------------------------- planner


def test_plans_are_deterministic_and_respect_preferences():
    prefs = Preferences(
        households_served=500,
        priority_categories=["protein"],
        avoid_categories=["hygiene"],
        max_share_percent=40,
    )
    options = [
        lot(1, "Peanut butter", 100),
        lot(2, "Hygiene kits", 100),
        lot(3, "Brown rice", 100),
    ]
    needs = [StatedNeed("Brown rice", 30, "grains", "needs.csv")]
    first = build_plans("Alpha", prefs, options, needs, NO_HISTORY)
    assert first == build_plans("Alpha", prefs, options, needs, NO_HISTORY)

    balanced = lines(plan("balanced", first))
    assert "Hygiene kits" not in balanced  # avoided
    assert balanced["Brown rice"].suggested == 30  # stated need
    # 500 households / 25 × rank-1 weight 1.5 = 30.
    assert balanced["Peanut butter"].suggested == 30
    priorities = lines(plan("priorities", first))
    assert priorities["Brown rice"].suggested == 15  # non-priority need halved
    assert priorities["Peanut butter"].suggested == 38  # 30 × 1.25 boost
    lean = plan("lean", first)
    assert lean.total_units < plan("priorities", first).total_units


def test_share_cap_storage_trim_and_perishables():
    prefs = Preferences(
        households_served=10_000,
        priority_categories=["produce", "dairy"],
        cold_storage_capacity=50,
        max_share_percent=40,
    )
    soon = DEADLINE.date() + timedelta(days=5)
    options = [
        lot(1, "Fresh apples", 100, best_before=soon),
        lot(2, "Yogurt cups", 100),
        lot(3, "Fresh carrots", 100, round_id=2),
    ]
    balanced = plan("balanced", build_plans("Alpha", prefs, options, [], NO_HISTORY))
    got = lines(balanced)
    assert got["Fresh apples"].suggested == 40  # capped at 40% of 100
    assert any("Best before" in r for r in got["Fresh apples"].reasons)
    # Cold storage 50: produce (priority #1) fills first; dairy (#2) is left out.
    assert sum(line.suggested for line in got.values()) == 50
    assert balanced.warnings == ["Left out Yogurt cups: cold storage (50) is already full"]


def test_without_households_the_estimate_is_a_small_share_of_the_offer():
    prefs = Preferences()
    p = plan("balanced", build_plans("Alpha", prefs, [lot(1, "Brown rice", 200)], [], NO_HISTORY))
    line = p.rounds[0].lines[0]
    assert line.suggested == 18  # 15% of 200 × neutral weight 0.6
    assert "add households served" in line.reasons[0]


def test_no_open_rounds_gives_empty_plans_with_explanation():
    for p in build_plans("Alpha", Preferences(), [], [], NO_HISTORY):
        assert p.total_units == 0 and p.rounds == []
        assert p.summary.startswith("No requests suggested")


def test_suggested_preferences_follow_history():
    history = [
        HistoryLine(1, "protein", "dry", offered=100, requested=40, allocated=30),
        HistoryLine(1, "produce", "cold", offered=60, requested=20, allocated=20),
        HistoryLine(2, "protein", "frozen", offered=50, requested=10, allocated=None),
    ]
    current = Preferences(dry_storage_capacity=500)
    prefs, why = suggest_preferences(current, history, {"hygiene": 3, "protein": 2}, ["halal"])
    assert prefs.priority_categories == ["protein", "produce"]
    assert prefs.avoid_categories == ["hygiene"]
    assert prefs.dry_storage_capacity == 500  # stated capacity kept
    assert prefs.cold_storage_capacity == 25  # 20 × 1.25
    assert prefs.max_share_percent == 40
    assert prefs.dietary_notes == "Mentioned in your documents: halal."
    assert set(why) >= {"priority_categories", "avoid_categories", "households_served"}
    insights = insights_from(history)
    assert insights.fill_rate_percent == 83 and insights.rounds_participated == 1

    unchanged, why = suggest_preferences(current, [], {}, [])
    assert unchanged == current and "No request history" in why["overall"]


def test_best_before_is_formatted_portably():
    prefs = Preferences(priority_categories=["produce"])
    option = lot(1, "Fresh apples", 10, best_before=date(2026, 11, 4))
    reasons = build_plans("Alpha", prefs, [option], [], NO_HISTORY)[0].rounds[0].lines[0].reasons
    assert "Best before Nov 4; plan to distribute within 2 days" in reasons


def test_current_request_anchors_lines_without_a_stated_need():
    existing = LotOption(
        round_id=1,
        round_name="Round 1",
        strategy="fcfs",
        request_deadline=DEADLINE,
        lot_id=1,
        name="Peanut butter",
        description="",
        unit="cases",
        offered=80,
        current_quantity=25,
        current_request_id=9,
        current_request_version=2,
    )
    plans = build_plans("Alpha", Preferences(), [existing], [], NO_HISTORY)
    balanced = lines(plan("balanced", plans))["Peanut butter"]
    assert balanced.suggested == 25 and balanced.current_request_version == 2
    assert balanced.reasons[0] == "Based on your current request of 25"
    # Neutral categories still appear in focused plans when the food bank asked for them.
    assert lines(plan("priorities", plans))["Peanut butter"].suggested == 13
    # A document quantity wins over the current request.
    stated = [StatedNeed("Peanut butter", 30, "protein", "needs.csv")]
    with_doc = build_plans("Alpha", Preferences(), [existing], stated, NO_HISTORY)
    assert lines(plan("balanced", with_doc))["Peanut butter"].suggested == 30
    assert "lines follow quantities" not in plan("balanced", plans).summary
