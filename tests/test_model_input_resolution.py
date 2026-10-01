"""Focused checks for the shared cross-domain value register."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import model_input_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    dependencies = {"source_pdf": "pdf-fp", "ai_response": "ai-fp"}
    register = model_input_resolution.build_register([
        {
            "record_id": "fallback-room",
            "target": "room.occupancy_people",
            "target_id": "room-1",
            "value": 5,
            "unit": "people",
            "origin": "preliminary_fallback",
            "status": "provisional",
            "confidence": 0.2,
            "estimated_load_kw": 2,
            "formula": "area * density",
            "operands": {"area_m2": 50, "density": 0.1},
        },
        {
            "record_id": "project-room",
            "target": "room.occupancy_people",
            "target_id": "room-1",
            "value": 8,
            "unit": "people",
            "origin": "project_evidence",
            "status": "resolved",
            "confidence": 0.95,
            "citation": {"citation": "Furniture plan p. 2"},
        },
        {
            "record_id": "wall-gap",
            "target": "surface.u_value_w_m2k",
            "target_id": "wall-1",
            "value": None,
            "unit": "W/m2K",
            "origin": "unresolved",
            "status": "excluded",
            "confidence": 0.0,
            "affected_component_ids": ["wall-1"],
        },
    ], dependency_fingerprints=dependencies)
    rows = register["records"]
    occupancy = next(row for row in rows if row["target_id"] == "room-1")
    check("precedence keeps one canonical target", sum(row["target_id"] == "room-1" for row in rows) == 1)
    check("direct evidence outranks fallback", occupancy["value"] == 8 and occupancy["origin"] == "project_evidence")
    check("losing candidate remains auditable", occupancy["alternatives"][0]["record_id"] == "fallback-room")
    check("dependency fingerprints are attached", occupancy["source_fingerprints"] == dependencies)
    check("impact ranking prioritises unresolved affected components", register["review_queue"][0]["target_id"] == "wall-1")

    report = {
        "scenario_results": [{"rooms": [{"room_id": "room-1", "hours": [{"hour": 14, "components": {"people": {"total_kw": 1.2}}}]}]}]
    }
    provenance = model_input_resolution.build_report_provenance(report, register)
    check("report drill-down links load component to register", provenance["governing_components"][0]["provenance"][0]["record_id"] == "project-room")


if __name__ == "__main__":
    main()
