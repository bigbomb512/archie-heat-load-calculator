#!/usr/bin/env python3

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.ai_packet import build_ai_packet
from ai.page_triage import validate_page_triage
from pdf_pipeline.extractors import extract_level_name


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def page(number, title, level=""):
    return {
        "page": number,
        "type": "floor_plan" if number != 44 else "reflected_ceiling_plan",
        "importance": "essential",
        "title": title,
        "confidence": 0.9,
        "thumbnail_path": f"thumbnails/page_{number:03d}.png",
        "extracted": {"level_name": level, "written_dimensions": [], "rooms": [], "ceiling_constraints": [], "hvac_terms": []},
    }


def decisions(*numbers):
    return {"pages": [{"page": number, "decision": "Confirm as detected", "scale_confirmed": False} for number in numbers]}


def triage():
    return {
        "result": {
            "page_triage": {
                "pages": [
                    {"page": 15, "disposition": "exclude", "page_role": "3d_render", "floor_label": "", "evidence": ["Perspective view, not an orthographic plan."]},
                    {"page": 26, "disposition": "core_geometry", "page_role": "main_floor_plan", "floor_label": "Lower Ground Floor", "evidence": ["Title states LOWER GROUND FLOOR."]},
                    {"page": 28, "disposition": "core_geometry", "page_role": "main_floor_plan", "floor_label": "Ground Floor", "evidence": ["Title states GROUND FLOOR."]},
                    {"page": 44, "disposition": "support_context", "page_role": "reflected_ceiling_plan", "floor_label": "Lower Ground Floor", "evidence": ["RCP title states LOWER GROUND FLOOR."]},
                ]
            }
        }
    }


def main():
    packet = {
        "pdf": "/tmp/example.pdf",
        "primary_pages": [
            page(15, "3D Images Perspective"),
            page(26, "Partition Plan", "Ground Floor"),
            page(28, "Floor Finishes Plan", "Ground Floor"),
            page(44, "Reflected Ceiling Plan", "Ground Floor"),
        ],
        "reference_pages": [],
        "kept_pages": [],
        "structured_pages": [],
    }
    review = decisions(15, 26, 28, 44)
    packet["kept_pages"] = list(packet["primary_pages"])
    validation = validate_page_triage(triage(), build_ai_packet(packet, review))
    check("page triage validates", validation["issue_count"], 0)

    ai_input = build_ai_packet(packet, review, page_triage=triage())
    included = [item["page"] for bucket in ai_input["confirmed_pages"].values() for item in bucket]
    check("3D render is excluded", 15 in included, False)
    floors = ai_input["building_model"]["floors"]
    check("separate lower ground and ground floors", [item["label"] for item in floors], ["Lower Ground Floor", "Ground Floor"])
    lower_ground = next(item for item in floors if item["label"] == "Lower Ground Floor")
    check("RCP joins lower ground", 44 in lower_ground["source_pages"], True)
    check("render is absent from source geometry", 15 in lower_ground["source_pages"], False)
    check("render is absent from high-confidence context", 15 in [item["page"] for item in ai_input["high_confidence_context"]["pages"]], False)

    check("lower ground extraction wins over generic ground", extract_level_name("PARTITION PLAN - LOWER GROUND FLOOR"), "Lower Ground Floor")
    check("basement room number is not a floor label", extract_level_name("Room Basement 01\nPARTITION PLAN - BASEMENT FLOOR"), "Basement")


if __name__ == "__main__":
    main()
