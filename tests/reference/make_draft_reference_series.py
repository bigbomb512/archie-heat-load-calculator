#!/usr/bin/env python3
"""Write tests/reference/draft_reference_series.json: each reference room's stored-heat series from the draft path.

    python3 tests/reference/make_draft_reference_series.py

Run once when the rooms or the heat balance change; the worksheet (draft_reference_rooms.py) takes these numbers as
declared inputs, and tests/test_draft_reference_rooms.py checks the draft still produces them. The series themselves
are checked against published ASHRAE results in tests/test_stored_heat.py.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.test_draft_reference_rooms import SUITE, run   # noqa: E402

OUT = Path(__file__).resolve().parent / "draft_reference_series.json"


def series(case):
    model, _report = run(case)
    rooms = {}
    for room in model["material"]["hourly_load_model"]["rooms"]:
        record = room.get("stored_heat") or {}
        labels = {surface["surface_id"]: surface.get("label", "") for surface in room["cooling_load"]["envelope_surfaces"]}
        prefix = room["name"] + " "
        rooms[room["name"]] = {
            "nonsolar": record.get("nonsolar_rts"), "solar": record.get("solar_rts"),
            "cts": {labels[sid][len(prefix):] if labels[sid].startswith(prefix) else labels[sid]: values
                    for sid, values in (record.get("surface_cts") or {}).items()},
            "constructions": sorted({item["construction"] for item in record.get("constructions", [])}),
        }
    return rooms


def main():
    result = {"schema_version": 1, "method_id": "rts_heat_balance_v1",
              "purpose": "Declared stored-heat series for the hand-worked reference rooms (from ai/stored_heat_rooms.py).",
              "cases": {case["case_id"]: series(case) for case in SUITE["cases"]}}
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
