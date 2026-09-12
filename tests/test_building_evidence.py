#!/usr/bin/env python3

from ai.building_evidence import build_building_evidence
from ai.thermal_model import build_thermal_evidence, build_thermal_model, apply_thermal_model


def check(name, actual):
    if not actual:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    pages = [
        {"page": 1, "title": "Ground Floor Plan", "level_name": "Ground Floor", "sheet_classification": "floor_plan", "thermal_role": "primary_geometry", "rooms": [{"name": "Kitchen", "area": "18 m²"}], "structured_content": {"markdown": "Kitchen AREA: 18 m² adjoining dining external wall W01 D01"}},
        {"page": 2, "title": "Shopfront Elevation", "level_name": "Ground Floor", "sheet_classification": "elevation", "thermal_role": "surface_confirmation", "rooms": [], "structured_content": {"markdown": "W01 glazing Door D01"}},
        {"page": 3, "title": "Wall Types", "level_name": "Ground Floor", "sheet_classification": "detail", "thermal_role": "construction_or_opening_detail", "rooms": [], "structured_content": {"markdown": "External wall type EW1 insulation glazing construction"}},
        {"page": 4, "title": "Lighting Schedule", "level_name": "Ground Floor", "sheet_classification": "schedule", "thermal_role": "services_or_internal_load", "rooms": [], "structured_content": {"markdown": "20W LED QTY: 6"}},
        {"page": 5, "title": "Equipment Schedule", "level_name": "Ground Floor", "sheet_classification": "schedule", "thermal_role": "services_or_internal_load", "rooms": [], "structured_content": {"markdown": "Oven and display fridge"}},
    ]
    coverage = {"levels": [{"level_name": "Ground Floor", "proposed_purpose": "food retail", "purpose_status": "inferred", "conditioned_status": "unknown", "purpose_evidence": []}], "coverage_exceptions": []}
    evidence = build_building_evidence({"source_pdf": "fixture.pdf", "drawing_set": {"pages": pages}}, coverage)
    for family in ("spaces", "surfaces", "openings", "constructions", "lighting", "equipment"):
        check(f"extracts {family}", evidence[family])
        check(f"{family} has citations", all(item["evidence"] and item["status"] in {"direct", "inferred", "missing"} for item in evidence[family]))
    check("proposes cross-sheet links", evidence["cross_sheet_links"])
    check("never assigns construction performance", all(item["thermal_performance"] is None for item in evidence["constructions"]))
    check("never assigns equipment watts", all(item["watts"] is None for item in evidence["equipment"]))
    thermal = build_thermal_model(build_thermal_evidence({"source_pdf": "fixture.pdf", "drawing_set": {"pages": pages}}, building_evidence=evidence))
    check("proposes a reviewed zone from each space", len(thermal["zones"]) == 1 and thermal["zones"][0]["name"] == "Kitchen")
    check("zone preserves building evidence ids", thermal["zones"][0]["building_evidence_ids"])
    check("approved adapter keeps a calculation zone", apply_thermal_model(thermal, {})["zones"][0]["area_m2"] == 18)

    # Ceiling/service legends contain room-like phrases (for example
    # "COOLROOM FREEZER") but are not room-boundary evidence.  They must
    # remain supporting evidence rather than creating topology candidates.
    rcp = [{"page": 6, "title": "Reflective Ceiling Plan", "level_name": "",
            "sheet_classification": "reflected_ceiling_plan", "thermal_role": "surface_confirmation",
            "rooms": [], "structured_content": {"markdown": "COOLROOM FREEZER LANDLORD SERVICE ENGINEER"}}]
    rcp_ocr = {"pages": [{"page": 6, "room_label_candidates": [
        {"text": "COOLROOM FREEZER", "status": "possible_room_or_area_label"},
        {"text": "LANDLORD SERVICE ENGINEER", "status": "possible_room_or_area_label"},
    ]}]}
    rcp_evidence = build_building_evidence({"source_pdf": "fixture.pdf", "drawing_set": {"pages": rcp}},
                                           {"page_roles": [{"page": 6, "proposed_role": "reflected_ceiling_plan"}]}, rcp_ocr)
    check("service legends do not create room candidates", not rcp_evidence["spaces"])

    elevation = [{"page": 7, "title": "Shopfront Elevation", "level_name": "Ground Floor",
                  "sheet_classification": "elevation", "thermal_role": "surface_confirmation", "rooms": [],
                  "structured_content": {"markdown": "W01 1200 x 2100 mm · storefront 3600 mm"}}]
    elevation_evidence = build_building_evidence(
        {"source_pdf": "fixture.pdf", "drawing_set": {"pages": elevation}},
        {"page_roles": [{"page": 7, "proposed_role": "opening_elevation", "opening_geometry_eligible": True}]},
    )
    dimensioned = next(item for item in elevation_evidence["openings"] if item.get("tag") == "W01" and item.get("dimensions"))
    check("opening elevation stores explicit dimensions", dimensioned["dimensions"] == {"width_mm": 1200.0, "height_mm": 2100.0, "unit": "mm"})
    check("dimensioned opening remains unresolved until plan matched", dimensioned["geometry"]["unique_target"] is False)


if __name__ == "__main__":
    main()
