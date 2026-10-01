#!/usr/bin/env python3
"""Focused checks for automatic local room inference."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import room_inference


def check(name, value):
    if not value:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    pages = [
        {"page": 2, "title": "Floor plan", "detected_type": "floor_plan", "plan_role": "main_floor_plan",
         "level_name": "Ground", "scale": "1:100", "structured_content": {"markdown": "KITCHEN DINING BAR COOL ROOM"}},
        {"page": 3, "title": "Kitchen section", "detected_type": "section", "plan_role": "reference_context",
         "structured_content": {"markdown": "KITCHEN"}},
    ]
    ai_input = {"drawing_set": {"pages": pages}}
    coverage = {"pages": [{"page": 2, "level_name": "Level 2", "level_status": "confirmed_by_text"},
                          {"page": 3, "level_name": "", "level_status": "missing"}],
                "page_relationships": [{"from_page": 2, "to_page": 3}]}
    first = room_inference.infer(ai_input, coverage)
    second = room_inference.infer({"drawing_set": {"pages": list(reversed(pages))}}, coverage)
    check("ranked plan creates source-linked room candidates", len(first["rooms"]) == 4 and first["selected_pages"] == [2, 3])
    check("linked context is selected but cannot originate a room", all(3 not in row["source_pages"] for row in first["rooms"]))
    check("low-confidence candidates remain visible and provisional", all(row["confidence_band"] == "medium" and row["unresolved_fields"] for row in first["rooms"]))
    check("room inference uses only the selected coverage level", all(row["level_name"] == "Level 2" for row in first["rooms"]))
    check("candidate IDs survive evidence reordering", [row["room_id"] for row in first["rooms"]] == [row["room_id"] for row in second["rooms"]])
    geometry_page = {"page": 4, "title": "Ground floor plan", "detected_type": "floor_plan", "plan_role": "main_floor_plan",
                     "level_name": "Ground", "room_geometry_candidates": [{
                         "label": "Shop", "boundary_points_px": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]],
                         "scale_mm_per_px": 100, "confidence": "high", "source_pages": [4]
                     }]}
    structured = room_inference.infer({"drawing_set": {"pages": [geometry_page]}}, {})
    room = structured["rooms"][0]
    check("structured plan geometry is carried into the room proposal", room["geometry"]["boundary_points_px"][-1] == [0, 0] and room["geometry"]["scale_mm_per_px"] == 100)
    vision = {"result": {"geometry_review": {"pages": [{"page": 4, "room_geometry_candidates": [{
        "label": "Vision shop", "level_name": "Ground", "boundary_points_px": [[0, 0], [80, 0], [80, 40], [0, 40], [0, 0]],
        "scale_mm_per_px": 100, "confidence": "high", "source_pages": [4]
    }]}]}}}
    from_vision = room_inference.infer({"drawing_set": {"pages": [{**geometry_page, "room_geometry_candidates": []}]}}, {}, vision_response=vision)
    check("provider vision geometry is carried into the persisted room proposal", from_vision["rooms"][0]["label"] == "Vision shop" and from_vision["rooms"][0]["geometry"]["scale_mm_per_px"] == 100)


if __name__ == "__main__":
    main()
