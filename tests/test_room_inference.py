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
    spatial = {"pages": [{"page": 2, "room_label_candidates": [
        {"text": "Kitchen", "status": "possible_room_or_area_label"},
        {"text": "Dining", "status": "possible_room_or_area_label"},
        {"text": "Bar", "status": "possible_room_or_area_label"},
        {"text": "Cool Room", "status": "possible_room_or_area_label"},
        {"text": "3mm FLAT BAR IN MT3 FINISH", "status": "possible_room_or_area_label"},
        {"text": "submit shop drawings to the architect", "status": "possible_room_or_area_label"},
        {"text": "coordinate with the fitout shopfitter", "status": "possible_room_or_area_label"},
        {"text": "BASE BUILDING EXISTING KITCHEN EXCHANGE AIR DUCTWORK", "status": "possible_room_or_area_label"},
    ]}]}
    first = room_inference.infer(ai_input, coverage, spatial)
    second = room_inference.infer({"drawing_set": {"pages": list(reversed(pages))}}, coverage, spatial)
    check("ranked plan creates source-linked room candidates from standalone OCR labels", len(first["rooms"]) == 4 and first["selected_pages"] == [2, 3])
    check("linked context is selected but cannot originate a room", all(3 not in row["source_pages"] for row in first["rooms"]))
    check("low-confidence candidates remain visible and provisional", all(row["confidence_band"] == "medium" and row["unresolved_fields"] for row in first["rooms"]))
    check("room inference uses only the selected coverage level", all(row["level_name"] == "Level 2" for row in first["rooms"]))
    check("candidate IDs survive evidence reordering", [row["room_id"] for row in first["rooms"]] == [row["room_id"] for row in second["rooms"]])
    detailed = room_inference.infer({"drawing_set": {"pages": [{"page": 5, "detected_type": "floor_plan", "plan_role": "main_floor_plan", "level_name": "Level 2"}]}}, {}, {"pages": [{"page": 5, "room_label_candidates": [
        {"text": "Office 01.02 9 m²", "status": "possible_room_or_area_label"},
        {"text": "Service Counter 01.01 13 m²", "status": "possible_room_or_area_label"},
    ]}]})
    check("standalone plan labels keep identifiers and explicit areas", {(row["label"], row.get("area_m2")) for row in detailed["rooms"]} == {("Office", 9.0), ("Service Counter", 13.0)})
    non_plan = room_inference.infer({"drawing_set": {"pages": [{"page": 9, "detected_type": "electrical_or_fire", "plan_role": "main_floor_plan"}]}}, {}, {"pages": [{"page": 9, "room_label_candidates": [{"text": "Plant room 9.6 m²", "status": "possible_room_or_area_label"}]}]})
    check("page classification prevents false plan role from originating rooms", not non_plan["rooms"])
    rcp = room_inference.infer({"drawing_set": {"pages": [{"page": 12, "detected_type": "reflected_ceiling_plan", "plan_role": "reflected_ceiling_plan"}]}}, {}, {"pages": [{"page": 12, "room_label_candidates": [{"text": label, "status": "possible_room_or_area_label"} for label in ("Bar", "Kitchen", "Shop", "Coolroom", "Freezer")]}]})
    check("standalone unlabelled-plan-set RCP labels remain available", {row["label"] for row in rcp["rooms"]} == {"Bar", "Kitchen", "Shop", "Coolroom", "Freezer"})
    crowded = {"page": 14, "detected_type": "floor_plan", "plan_role": "main_floor_plan", "level_name": "Unassigned level"}
    crowded_spatial = {"pages": [{"page": 14,
        "room_label_candidates": [{"text": f"legend note fragment {index}", "status": "possible_room_or_area_label"} for index in range(80)],
        "standalone_text_items": [{"text": f"legend note fragment {index}", "bbox": [0, index * 4, 40, index * 4 + 3]} for index in range(80)] + [
            {"text": label, "bbox": [index * 50, 400, index * 50 + 30, 410]} for index, label in enumerate(("Bar", "Coolroom", "Freezer", "Kitchen", "Shop"))],
    }]}
    crowded_result = room_inference.infer({"drawing_set": {"pages": [crowded]}}, {}, crowded_spatial)
    check("full standalone text items recover labels after 80 note candidates", {row["label"] for row in crowded_result["rooms"]} == {"Bar", "Coolroom", "Freezer", "Kitchen", "Shop"})
    fragments = room_inference.room_label_items({"room_label_candidates": [
        {"text": "Service Counter", "status": "possible_room_or_area_label"},
        {"text": "Counter", "status": "possible_room_or_area_label"},
    ]})
    check("label fragment is removed when full label exists", fragments == [("Service Counter", None)])
    flat_bar = room_inference.room_label_items({"word_samples": [
        {"text": "3mm", "bbox": [0, 10, 14, 20]}, {"text": "FLAT", "bbox": [16, 10, 36, 20]},
        {"text": "BAR", "bbox": [38, 10, 50, 20]}, {"text": "IN", "bbox": [52, 10, 62, 20]},
        {"text": "MT3", "bbox": [64, 10, 82, 20]}, {"text": "FINISH", "bbox": [84, 10, 120, 20]},
    ]})
    check("standalone BAR token inside a flat-bar note is rejected using line context", not flat_bar)
    note_lines = ["3mm FLAT BAR IN MT3 FINISH", "submit shop drawings to the architect",
                  "coordinate with the fitout shopfitter", "BASE BUILDING EXISTING KITCHEN EXCHANGE AIR DUCTWORK"]
    note_items = []
    for line_index, line in enumerate(note_lines):
        for word_index, word in enumerate(line.split()):
            x0 = word_index * 45
            note_items.append({"text": word, "bbox": [x0, line_index * 20, x0 + 35, line_index * 20 + 10]})
    check("full word items in notes and legends do not create room labels", not room_inference.room_label_items({"standalone_text_items": note_items}))
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
