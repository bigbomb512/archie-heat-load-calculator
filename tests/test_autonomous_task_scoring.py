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
        "P0_rooms": [{"label": "Bar", "area_m2": 31.2}, {"label": "Kitchen", "area_m2": 97.0}, {"label": "Shop", "area_m2": 229.0},
                     {"label": "Coolroom", "area_m2": 10.7}, {"label": "Freezer", "area_m2": 7.6}],
        "P1_site": {"site_text": "Tenancy MZ01, Central Precinct"},
        "P2_north": [{"page": 20, "plan_up_azimuth_deg": 358}],
        "P3_boundaries": [{"room": "Shop", "edge_length_m": 11.97, "boundary": "external"}],
        "P4_openings": [{"page": 26, "glazed_panels": [{"width_mm": 2025, "sill_mm": 1100, "head_mm": 2700}]}],
        "P5_roof": [{"room": "Bar", "roof": "not_exposed"}, {"room": "Kitchen", "roof": "not_exposed", "source": "fallback"},
                    {"room": "Shop", "roof": "not_exposed"}],
    }


def main():
    report = score_case(KEY, perfect(), SITE)
    statuses = [item["status"] for items in report["tasks"].values() for item in items]
    check("a run matching the answer key scores every scored item correct", statuses and set(statuses) == {"correct"})
    check("north 2° off still counts within the 5° tolerance, across 0°/360°", report["tasks"]["P2_north"][0]["status"] == "correct")
    check("an answer still pending from the user is not scored", report["tasks"]["P3_boundaries"] == [])
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
    check("rooms whose answer is pending the user are not scored", "Bar" not in rooms and "Shop" not in rooms)
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
    summary = summarise([report])
    check("a task below the 85% bar may not auto-apply", AUTO_APPLY_BAR == 0.85 and not summary["P0_rooms"]["auto_apply"])


if __name__ == "__main__":
    main()
