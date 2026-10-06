#!/usr/bin/env python3
"""Card Q: identifying the kitchen equipment drawn on the plans (task P6, no heat numbers)."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import kitchen_equipment as kitchen
from ai.autonomous_task_scoring import score_case


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def expect_error(name, call, fragment):
    try:
        call()
    except ValueError as error:
        check(name, fragment in str(error))
        return
    raise AssertionError(name + " (no error raised)")


def word(text, x, y):
    return {"text": text, "bbox": [x, y, x + 6 * len(text), y + 8]}


SPATIAL = {"pages": [
    {"page": 30, "width": 1191, "height": 842, "title_blocks": [{"region": "bottom_band", "bbox": [0, 780, 1191, 842]}],
     "standalone_text_items": [
         word("1", 100, 300), word("KITCHEN", 110, 300), word("ELEVATION", 160, 300), word("1", 220, 300),
         word("RANGEHOOD", 300, 100), word("3", 500, 150), word("DOOR", 510, 150), word("FRIDGE", 500, 162),
         word("UB", 400, 170), word("FRIDGE", 420, 170), word("OVEN", 600, 180), word("SINGLE", 700, 170), word("SINK", 745, 170),
         word("PROVIDE", 100, 400), word("CEILING", 150, 400), word("MOUNTED", 200, 400), word("GPOS", 250, 400), word("FOR", 280, 400),
         word("KITCHEN", 300, 400), word("EQUIPMENT", 350, 400), word("WITH", 410, 400), word("CONDUITS", 440, 400),
         word("0430", 600, 800), word("377", 630, 800), word("302", 655, 800), word("admin@studio.example", 700, 800)]},
    {"page": 20, "width": 1191, "height": 842, "title_blocks": [], "standalone_text_items": [word("FRYER", 500, 300)]},
    {"page": 21, "width": 1191, "height": 842, "title_blocks": [], "standalone_text_items": [word("DINING", 100, 100)]},
]}
AI_INPUT = {"drawing_set": {"pages": [{"page": 30, "title": "Internal Elevation"}, {"page": 20, "title": "Dimension Plan"}]}}


def packet():
    labels = {20: kitchen.page_labels(SPATIAL, 20, (450, 250, 600, 350)), 30: kitchen.page_labels(SPATIAL, 30)}
    words = {20: kitchen.page_words(SPATIAL, 20, (450, 250, 600, 350)), 30: kitchen.page_words(SPATIAL, 30)}
    return kitchen.build_packet(20, (1100, 600, 1600, 900), [30], labels, words)


def main():
    check("kitchen elevation pages are found from their text", kitchen.find_kitchen_elevation_pages(AI_INPUT, SPATIAL) == [30])
    labels = kitchen.page_labels(SPATIAL, 30)
    check("words are joined into short label lines, and long notes and title-block text are left out",
          "RANGEHOOD" in labels and "UB FRIDGE" in labels and not any("PROVIDE" in line or "0430" in line or "admin" in line for line in labels))
    built, prompt = packet()
    check("the packet holds one plan crop and the elevation sheet within the budget",
          [image["kind"] for image in built["images"]] == ["plan_crop", "elevation_page"] and len(prompt) <= kitchen.BUDGET_CHARS)
    many = {30: [f"LABEL NUMBER {index}" for index in range(200)]}
    expect_error("labels over the budget block the task instead of being trimmed",
                 lambda: kitchen.build_packet(20, (0, 0, 1, 1), [30], many), "budget")

    reply = {"items": [
        {"type": "rangehood_canopy", "count": 1, "page": 30, "quote": "RANGEHOOD", "under_hood": False},
        {"type": "refrigerator_upright", "count": 1, "page": 30, "quote": "3 DOOR FRIDGE"},
        {"type": "refrigerator_underbench", "count": 2, "page": 30, "quote": "UB FRIDGE"},
        {"type": "oven", "count": 1, "page": 30, "quote": "OVEN", "under_hood": True},
        {"type": "range_burners", "count": 1, "page": 20, "quote": None, "symbol": "six burner grates on the island, plan crop", "under_hood": True}]}
    items = kitchen.validate_reply(reply, built)
    check("a quote that wraps over two lines is accepted", any(item["quote"] == "3 DOOR FRIDGE" for item in items))
    split = kitchen.validate_reply({"items": [{"type": "refrigerator_upright", "count": 1, "page": 30, "quote": "3 DOOR"}]}, built)
    check("a fridge quoted by the first line of its label (\"3 DOOR\") is accepted", split[0]["quote"] == "3 DOOR")
    check("an unlabelled appliance is accepted with a described symbol", items[-1]["evidence_kind"] == "symbol")
    check("determinations total each type for scoring",
          kitchen.to_determinations(items) == [{"type": "oven", "count": 1, "source": "ai_determined"},
                                              {"type": "range_burners", "count": 1, "source": "ai_determined"},
                                              {"type": "rangehood_canopy", "count": 1, "source": "ai_determined"},
                                              {"type": "refrigerator_underbench", "count": 2, "source": "ai_determined"},
                                              {"type": "refrigerator_upright", "count": 1, "source": "ai_determined"}])
    bad = lambda **change: kitchen.validate_reply({"items": [{"type": "oven", "count": 1, "page": 30, "quote": "OVEN", **change}]}, built)
    expect_error("an unknown appliance type is refused", lambda: bad(type="pizza_oven"), "Unknown kitchen equipment type")
    expect_error("an implausible count is refused", lambda: bad(count=0), "whole number")
    expect_error("a page that was not supplied is refused", lambda: bad(page=21), "not one of the supplied images")
    expect_error("an invented quote is refused", lambda: bad(quote="COMBI OVEN 10 TRAY"), "not text on page 30")
    expect_error("a sink is refused as giving no heat", lambda: bad(type="other_cooking", quote="SINGLE SINK"), "gives no heat")
    expect_error("an item with neither a quote nor a symbol is refused", lambda: bad(quote=None, symbol="box"), "needs either a quote")

    key = json.loads((ROOT / "evaluations" / "autonomous" / "caseA.json").read_text(encoding="utf-8"))
    full = {"P6_kitchen": [{"type": "rangehood_canopy", "count": 1}, {"type": "oven", "count": 1}, {"type": "refrigerator_upright", "count": 2},
                           {"type": "refrigerator_underbench", "count": 4}, {"type": "ice_machine", "count": 1},
                           {"type": "range_burners", "count": 1}, {"type": "fryer", "count": 2}]}
    scored = score_case(key, full)["tasks"]["P6_kitchen"]
    check("a complete list scores every keyed item and does not penalise the unkeyed island equipment",
          [item["status"] for item in scored] == ["correct"] * 5)


if __name__ == "__main__":
    main()
