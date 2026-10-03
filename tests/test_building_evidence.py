#!/usr/bin/env python3

from ai.building_evidence import build_building_evidence, deduplicate
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
    coverage = {"levels": [{"level_name": "Ground Floor", "proposed_purpose": "food retail", "purpose_status": "inferred", "conditioned_status": "unknown", "purpose_evidence": []}],
                "pages": [{"page": row["page"], "level_name": "Ground Floor", "level_status": "confirmed_by_review"} for row in pages],
                "coverage_exceptions": []}
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

    false_rooms = [
        {"page": 10, "title": "Render", "level_name": "Level 1", "sheet_classification": "render_or_photo",
         "rooms": [{"name": "Retail space including a museum and Front of house", "area": "27.9 m²"}],
         "structured_content": {"markdown": "Retail space including a museum and Front of house 27.9 m²"}},
        {"page": 11, "title": "Electrical", "level_name": "Level 1", "sheet_classification": "electrical_or_fire",
         "rooms": [{"name": "Plant room", "area": "9.6 m²"}],
         "structured_content": {"markdown": "Plant room AREA 9.6 m²"}},
        {"page": 12, "title": "General notes", "level_name": "Level 1", "sheet_classification": "notes", "rooms": [],
         "structured_content": {"markdown": "coordinate with the fitout shopfitter"}},
    ]
    false_ocr = {"pages": [
        {"page": 10, "room_label_candidates": [{"text": "Retail space including a museum and Front of house 27.9 m²", "status": "possible_room_or_area_label"}]},
        {"page": 11, "room_label_candidates": [{"text": "Plant room 9.6 m²", "status": "possible_room_or_area_label"}]},
        {"page": 12, "room_label_candidates": [{"text": "coordinate with the fitout shopfitter", "status": "possible_room_or_area_label"}]},
    ]}
    false_evidence = build_building_evidence({"source_pdf": "fixture.pdf", "drawing_set": {"pages": false_rooms}}, {}, false_ocr)
    check("render, electrical and notes text never create spaces", not false_evidence["spaces"])

    plan_rooms = [{"page": 13, "title": "GA Plan", "level_name": "Level 2", "sheet_classification": "floor_plan",
                   "rooms": [], "structured_content": {"markdown": "Office AREA 9 m²\nService Counter AREA 13 m²"}},
                  {"page": 13, "title": "GA Plan duplicate", "level_name": "Unassigned level", "sheet_classification": "floor_plan",
                   "rooms": [], "structured_content": {"markdown": ""}}]
    plan_ocr = {"pages": [{"page": 13, "room_label_candidates": [
        {"text": "Office 01.02 9 m²", "status": "possible_room_or_area_label"},
        {"text": "Service Counter 01.01 13 m²", "status": "possible_room_or_area_label"},
        {"text": "SHOPFITTER CLEAN", "status": "possible_room_or_area_label"},
        {"text": "3mm FLAT BAR IN MT3 FINISH", "status": "possible_room_or_area_label"},
        {"text": "submit shop drawings to the architect", "status": "possible_room_or_area_label"},
        {"text": "coordinate with the fitout shopfitter", "status": "possible_room_or_area_label"},
        {"text": "BASE BUILDING EXISTING KITCHEN EXCHANGE AIR DUCTWORK", "status": "possible_room_or_area_label"},
    ]}]}
    plan_coverage = {"pages": [{"page": 13, "level_name": "Level 2", "level_status": "confirmed_by_review"}]}
    plan_evidence = build_building_evidence({"source_pdf": "fixture.pdf", "drawing_set": {"pages": plan_rooms}}, plan_coverage, plan_ocr)
    spaces = plan_evidence["spaces"]
    check("plan room labels and areas are retained without phantom OCR labels", {(s["name"], s["level_name"]) for s in spaces} == {("Office", "Level 2"), ("Service Counter", "Level 2")})
    check("plan room areas survive with citations", {(s["name"], s["area"]) for s in spaces} == {("Office", "9 m²"), ("Service Counter", "13 m²")} and all(s["evidence"][0]["page"] == 13 for s in spaces))
    check("same-page Unassigned duplicate is removed", all(s["level_name"] != "Unassigned level" for s in spaces))
    duplicate_fixture = {"spaces": [
        {"name": "Office", "area": "9 m²", "level_name": "Level 2", "evidence": [{"page": 13}]},
        {"name": "Office", "area": "9 m²", "level_name": "Unassigned level", "evidence": [{"page": 13}]},
    ], "surfaces": [], "openings": [], "constructions": [], "lighting": [], "equipment": []}
    deduplicate(duplicate_fixture)
    check("same-label same-page Unassigned duplicate loses to named level", len(duplicate_fixture["spaces"]) == 1 and duplicate_fixture["spaces"][0]["level_name"] == "Level 2")

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

    stale_level = build_building_evidence(
        {"source_pdf": "fixture.pdf", "drawing_set": {"pages": [{"page": 8, "title": "Shopfront Elevation",
          "level_name": "FL 02", "sheet_classification": "elevation", "rooms": []}]}},
        {"pages": [{"page": 8, "level_name": "", "level_status": "missing"}],
         "levels": [{"level_name": "Unassigned level"}]},
        vision_response={"result": {"auto_extraction": {"entities": [{"kind": "floor", "page": 8,
          "label": "Level 2", "level_name": "Level 2", "candidate_fingerprint": "vision-floor"}]}}},
    )
    check("building evidence does not promote stale page or vision labels into floors", not stale_level["levels"])
    check("non-text floor proposal remains visible as a candidate", stale_level["level_candidates"][0]["name"] == "Level 2")


if __name__ == "__main__":
    main()
