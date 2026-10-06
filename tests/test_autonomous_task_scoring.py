#!/usr/bin/env python3
"""Card P: scoring autonomous AI task results against an answer key."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.autonomous_task_scoring import AUTO_APPLY_BAR, score_case, summarise


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


KEY = json.loads((ROOT / "evaluations" / "autonomous" / "caseA.json").read_text(encoding="utf-8"))
SITE = {"P1_site": {"site_must_contain": ["CENTRAL PRECINCT"], "must_not_choose": ["CONSULTANT ROAD"]}}


def perfect():
    return {
        "P0_rooms": [{"label": "Bar", "area_m2": 31.2}, {"label": "Kitchen", "area_m2": 97.0}, {"label": "Shop", "area_m2": 216.0},
                     {"label": "Coolroom", "area_m2": 10.7}, {"label": "Freezer", "area_m2": 7.6}],
        "P1_site": {"site_text": "Tenancy MZ01, Central Precinct"},
        "P2_north": [{"page": 20, "plan_up_azimuth_deg": 358}] + [{"page": page, "plan_up_azimuth_deg": 0} for page in range(21, 26)],
        "P3_boundaries": [{"room": "Shop", "edge_length_m": 11.97, "boundary": "mall"},
                          {"room": "Bar", "edge_length_m": 7.47, "boundary": "internal"}, {"room": "Bar", "edge_length_m": 4.36, "boundary": "internal"}],
        "P4_openings": [{"page": 26, "glazed_panels": [{"width_mm": 2025, "sill_mm": 1100, "head_mm": 2700}]}],
        "P6_kitchen": [{"type": "rangehood_canopy", "count": 1}, {"type": "oven", "count": 1}, {"type": "refrigerator_upright", "count": 2},
                       {"type": "refrigerator_underbench", "count": 4}, {"type": "ice_machine", "count": 1}, {"type": "range_burners", "count": 1}],
        "P5_roof": [{"room": "Bar", "roof": "not_exposed"}, {"room": "Kitchen", "roof": "not_exposed", "source": "fallback"},
                    {"room": "Shop", "roof": "not_exposed"}],
    }


def main():
    report = score_case(KEY, perfect(), SITE)
    statuses = [item["status"] for items in report["tasks"].values() for item in items]
    check("a run matching the answer key scores every scored item correct", statuses and set(statuses) == {"correct"})
    check("north 2° off still counts within the 5° tolerance, across 0°/360°", report["tasks"]["P2_north"][0]["status"] == "correct")
    check("the storefront facing the enclosed mall and the internal Bar walls are scored",
          [item["status"] for item in report["tasks"]["P3_boundaries"]] == ["correct"] * 3)
    bar_outside = score_case(KEY, {**perfect(), "P3_boundaries": [{"room": "Bar", "edge_length_m": 7.47, "boundary": "adjacent_tenancy"}]}, SITE)
    check("a Bar wall classed as anything but internal is wrong",
          [item["status"] for item in bar_outside["tasks"]["P3_boundaries"] if item["item"].startswith("Bar")] == ["wrong"])
    pending_key = {"case_id": "x", "tasks": {"P3_boundaries": {"edges": [{"room": "Shop", "edge_length_m": 5.0, "boundary": "pending_user"}]}}}
    check("an answer still pending from the user is not scored",
          score_case(pending_key, {"P3_boundaries": [{"room": "Shop", "edge_length_m": 5.0, "boundary": "external"}]})["tasks"]["P3_boundaries"] == [])
    wrong = score_case(KEY, {**perfect(), "P3_boundaries": [{"room": "Shop", "edge_length_m": 11.9, "boundary": "external"}]}, SITE)
    check("calling the mall storefront external is wrong",
          [item["status"] for item in wrong["tasks"]["P3_boundaries"] if item["item"].startswith("Shop")] == ["wrong"])
    summary = summarise([report])
    check("a fallback answer is counted separately", summary["P5_roof"]["from_fallback"] == 1)
    check("every task in a perfect run may auto-apply", all(row["auto_apply"] for row in summary.values()))
    check("small samples are flagged", summary["P0_rooms"]["small_sample"])

    bad = perfect()
    bad["P0_rooms"] = bad["P0_rooms"][:3] + [{"label": "Counter", "area_m2": 4}]
    bad["P0_rooms"][1]["area_m2"] = 140
    bad["P1_site"] = {"site_text": "1 Consultant Road, Sydney NSW 2000"}
    bad["P2_north"] = [{"page": 20, "plan_up_azimuth_deg": None}]
    bad["P4_openings"] = [{"page": 26, "glazed_panels": [{"width_mm": 2025, "sill_mm": 950, "head_mm": 2700},
                                                         {"width_mm": 3250, "sill_mm": 0, "head_mm": 2700}]}]
    bad["P5_roof"] = [{"room": "Bar", "roof": "exposed"}]
    report = score_case(KEY, bad, SITE)
    rooms = {item["item"]: item["status"] for item in report["tasks"]["P0_rooms"]}
    check("a wrong area, a missed room and a false room are all scored",
          rooms["Kitchen"] == "wrong" and rooms["Coolroom"] == "missing" and rooms["Counter"] == "wrong")
    check("all caseA rooms are now keyed and scored", "Shop" in rooms and "Bar" in rooms)
    check("choosing the consultant's address is wrong", report["tasks"]["P1_site"][0]["status"] == "wrong")
    check("no north applied is missing, not correct", report["tasks"]["P2_north"][0]["status"] == "missing")
    openings = {item["item"]: item for item in report["tasks"]["P4_openings"]}
    check("a wrong sill height fails the panel", openings["p. 26 glazing 2025 mm"]["status"] == "wrong")
    check("counting the roller-shutter doorway as glazing is wrong, with the reason",
          openings["p. 26 extra 3250 mm"]["status"] == "wrong" and "Roller-shutter" in openings["p. 26 extra 3250 mm"]["detail"])
    roof = {item["item"]: item["status"] for item in report["tasks"]["P5_roof"]}
    check("an exposed roof where there are apartments above is wrong, and unanswered rooms are missing",
          roof == {"Bar": "wrong", "Kitchen": "missing", "Shop": "missing"})
    any_key = {"case_id": "x", "tasks": {}}
    any_site = {"P1_site": {"site_must_contain_any": ["NORTH AIRPORT", "NAP"], "must_not_choose": ["CONSULTANT ROAD"]}}
    check("any one of several accepted site names is enough",
          score_case(any_key, {"P1_site": {"site_text": "Kiosk NAP Level 2"}}, any_site)["tasks"]["P1_site"][0]["status"] == "correct"
          and score_case(any_key, {"P1_site": {"site_text": "Kiosk Level 2"}}, any_site)["tasks"]["P1_site"][0]["status"] == "wrong")
    case_b = json.loads((ROOT / "evaluations" / "autonomous" / "caseB.json").read_text(encoding="utf-8"))
    north = score_case(case_b, {"P2_north": [{"page": 5, "plan_up_azimuth_deg": None}, {"page": 7, "plan_up_azimuth_deg": 90}]})["tasks"]["P2_north"]
    check("on a sheet with no north arrow, applying none is correct and inventing one is wrong",
          [item["status"] for item in north] == ["correct", "wrong", "missing", "missing"])
    null_export = score_case(case_b, {"P2_north": [{"page": 5, "plan_up_azimuth_deg": None}]})["tasks"]["P2_north"]
    check("a checked no-arrow page exported with a null bearing scores correct",
          null_export[0]["status"] == "correct" and null_export[0]["detail"] == "no north arrow, none applied")
    walls = score_case(case_b, {"P3_boundaries": [{"room": "Service Counter", "edge_length_m": 3.1, "boundary": "mall"},
                                                  {"room": "Service Counter", "edge_length_m": 2.0, "boundary": "external"}]})["tasks"]["P3_boundaries"]
    check("room-wide wall rules: mall is right, external is wrong inside the terminal, an unclassified room is missing",
          [item["status"] for item in walls] == ["missing", "correct", "wrong"])
    kitchen = score_case(KEY, {"P6_kitchen": [{"type": "rangehood_canopy", "count": 1}, {"type": "oven", "count": 2},
                                              {"type": "refrigerator_underbench", "count": 4}, {"type": "fryer", "count": 2},
                                              {"type": "dishwasher", "count": 1}]})["tasks"]["P6_kitchen"]
    statuses = {item["item"]: item["status"] for item in kitchen}
    check("kitchen items are scored on their count, unlisted keyed items are missing, unkeyed island items are not penalised, others are wrong",
          statuses == {"rangehood_canopy": "correct", "oven": "wrong", "refrigerator_upright": "missing", "refrigerator_underbench": "correct",
                       "ice_machine": "missing", "dishwasher": "wrong"})
    summary = summarise([report])
    check("a task below the 85% bar may not auto-apply", AUTO_APPLY_BAR == 0.85 and not summary["P0_rooms"]["auto_apply"])


if __name__ == "__main__":
    main()
