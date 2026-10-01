#!/usr/bin/env python3
"""Regression checks for the opt-in, evidence-only vision handoff.

These tests intentionally use no provider and make no network request.
"""

import json
from pathlib import Path

from ai.vision_extraction import (
    selection_summary, select_page_groups, validate_provider_output,
    validate_settings, vision_response_from_extraction,
)
from ai.skill_registry import vision_guidance


PAGES = [
    {"page": 20, "drawing_number": "A-202", "title": "Dimension plan", "structured_content": {"markdown": "SHOP 01"}},
    {"page": 26, "drawing_number": "A-026", "title": "Shopfront elevation", "structured_content": {"markdown": "2400 mm"}},
]
COVERAGE = {"page_roles": [
    {"page": 20, "proposed_role": "primary_geometry_plan", "level_name": "Level 1"},
    {"page": 26, "proposed_role": "opening_elevation", "level_name": "Level 1"},
]}


def entity(witnesses):
    return {"kind": "room", "page": 20, "drawing_number": "A-202", "label": "Shop 01", "level_name": "Level 1",
            "area_m2": 20.0, "ceiling_height_mm": None, "width_mm": None, "height_mm": None, "unit": "m²",
            "geometry_status": "geometry_confirmed", "boundary_reference": "", "opening_tag": "", "surface_kind": "",
            "orientation": "", "witnesses": witnesses, "excerpt": "SHOP 01", "confidence": "high", "unresolved_fields": []}


def geometry_page():
    walls = [
        {"wall_id": "wall-1", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[0, 0], [100, 0]], "confidence": "high"},
        {"wall_id": "wall-2", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[100, 0], [100, 50]], "confidence": "high"},
        {"wall_id": "wall-3", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[100, 50], [0, 50]], "confidence": "high"},
        {"wall_id": "wall-4", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[0, 50], [0, 0]], "confidence": "high"},
    ]
    link = {"dimension_id": "dim-1", "target_wall_id": "wall-1", "reason": "arrowheads span the wall endpoints", "source_reference": "crop-20"}
    return {
        "page": 20, "walls": walls,
        "major_dimensions": [{"dimension_id": "dim-1", "value_mm": 10000, "measured_span_start_px": [0, 0], "measured_span_end_px": [100, 0], "confidence": "high"}],
        "dimension_wall_links": [link], "conflicts": [],
        "room_geometry_candidates": [{
            "room_geometry_id": "room-1", "label": "Shop 01", "room_label_bbox": [40, 20, 60, 30], "level_name": "Level 1",
            "boundary_points_px": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]], "ordered_wall_ids": [row["wall_id"] for row in walls],
            "dimension_ids": ["dim-1"], "dimension_wall_links": [link], "independent_witnesses": [],
            "confidence": "high", "confidence_score": 0.95, "scale_mm_per_px": 100,
            "source_pages": [20], "source_crop": "crop-20", "assumptions": [], "conflicts": [], "unresolved_fields": [],
        }],
    }


def run():
    groups = select_page_groups({"drawing_set": {"pages": PAGES}}, COVERAGE)
    assert [group["group_id"] for group in groups] == ["plan_geometry", "opening_elevation"]
    settings = validate_settings({"owner_opt_in": True, "selected_group_ids": ["plan_geometry"]})
    assert selection_summary(settings, groups)["page_count"] == 1
    all_pages = selection_summary(validate_settings({"owner_opt_in": True, "selected_group_ids": []}), groups)
    assert all_pages["page_count"] == 2
    assert all_pages["request_count"] == 2
    one = validate_provider_output({"groups": [
        {"group_id": "plan_geometry", "entities": [entity([{"page": 20, "kind": "plan", "reference": "boundary"}])], "conflicts": [], "missing_evidence": []},
        {"group_id": "opening_elevation", "entities": [], "conflicts": [], "missing_evidence": []},
    ]}, groups)
    assert one["entities"][0]["auto_activate"] is False
    two = validate_provider_output({"groups": [
        {"group_id": "plan_geometry", "entities": [entity([
            {"page": 20, "kind": "plan", "reference": "closed boundary"},
            {"page": 26, "kind": "elevation", "reference": "matching opening chain"},
        ])], "conflicts": [], "missing_evidence": []},
        {"group_id": "opening_elevation", "entities": [], "conflicts": [], "missing_evidence": []},
    ]}, groups)
    assert two["entities"][0]["auto_activate"] is True
    bad = entity([{ "page": 999, "kind": "plan", "reference": "not in packet" }])
    try:
        validate_provider_output({"groups": [
            {"group_id": "plan_geometry", "entities": [bad], "conflicts": [], "missing_evidence": []},
            {"group_id": "opening_elevation", "entities": [], "conflicts": [], "missing_evidence": []},
        ]}, groups)
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown witness pages must be rejected")
    structured = validate_provider_output({"groups": [
        {"group_id": "plan_geometry", "entities": [], "geometry_pages": [geometry_page()], "conflicts": [], "missing_evidence": []},
        {"group_id": "opening_elevation", "entities": [], "conflicts": [], "missing_evidence": []},
    ]}, groups)
    response = vision_response_from_extraction(structured, "source-fingerprint", "fixture-model")
    assert len(response["result"]["geometry_review"]["pages"]) == 1
    gains = entity([])
    gains.update({"seat_count": 140, "lighting_fixtures": [{"fixture_id": "downlight", "name": "30 W recessed LED",
        "quantity": 12, "wattage_w": 30, "page": 20, "drawing_number": "A-202", "excerpt": "12 x 30W"}],
        "equipment": [{"equipment_id": "E06", "name": "Combi oven", "model": "", "quantity": 1,
        "rated_input_w": None, "watts": None, "heat_to_space_factor": None, "diversity_factor": None,
        "page": 20, "drawing_number": "A-202", "excerpt": "E06 combi oven qty 1"}],
        "operating_hours_evidence": "No operating hours shown."})
    gains_result = validate_provider_output({"groups": [
        {"group_id": "plan_geometry", "entities": [gains], "conflicts": [], "missing_evidence": []},
        {"group_id": "opening_elevation", "entities": [], "conflicts": [], "missing_evidence": []},
    ]}, groups)
    assert gains_result["entities"][0]["seat_count"] == 140
    assert gains_result["entities"][0]["lighting_fixtures"][0]["wattage_w"] == 30
    assert gains_result["entities"][0]["equipment"][0]["rated_input_w"] is None
    guidance = vision_guidance()
    catalog = json.loads((Path(__file__).resolve().parents[1] / "config" / "archie_skills_v1.json").read_text())
    registry = json.loads((Path(__file__).resolve().parents[1] / "config" / "archie_subskills_v1.json").read_text())
    assert {row["id"] for row in guidance["skills"]} == set(catalog["enabled_skill_ids"])
    assert len(guidance["subskills"]) == len(registry["subskills"])
    assert all(row.get("task") and row.get("proposal_fields") for row in guidance["subskills"])
    assert all(row.get("inputs") and row.get("constraints") for row in registry["subskills"])
    assert "citations" in guidance["proposal_envelope"]["required"]
    assert "Never fetch the internet yourself" in guidance["instructions"]
    invalid_geometry = geometry_page()
    invalid_geometry["room_geometry_candidates"][0]["ordered_wall_ids"] = ["unknown-wall"]
    try:
        validate_provider_output({"groups": [
            {"group_id": "plan_geometry", "entities": [], "geometry_pages": [invalid_geometry], "conflicts": [], "missing_evidence": []},
            {"group_id": "opening_elevation", "entities": [], "conflicts": [], "missing_evidence": []},
        ]}, groups)
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown geometry wall links must be rejected")
    print("vision extraction tests passed")


if __name__ == "__main__":
    run()
