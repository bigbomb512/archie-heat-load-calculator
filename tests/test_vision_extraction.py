#!/usr/bin/env python3
"""Regression checks for the opt-in, evidence-only vision handoff.

These tests intentionally use no provider and make no network request.
"""

from ai.vision_extraction import estimate, select_page_groups, validate_provider_output, validate_settings


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


def run():
    groups = select_page_groups({"drawing_set": {"pages": PAGES}}, COVERAGE)
    assert [group["group_id"] for group in groups] == ["plan_geometry", "opening_elevation"]
    settings = validate_settings({"owner_opt_in": True, "max_budget_aud": 2, "selected_group_ids": ["plan_geometry"]})
    assert estimate(settings, groups, 1.25)["within_budget"] is True
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
    print("vision extraction tests passed")


if __name__ == "__main__":
    run()
