#!/usr/bin/env python3

from ai.drawing_coverage import build_drawing_coverage


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print("PASS - " + name)


def page(number, classification, role, level="Ground Floor", title="", rooms=None):
    return {
        "page": number,
        "title": title,
        "drawing_number": f"A{number}.01",
        "sheet_classification": classification,
        "thermal_role": role,
        "level_name": level,
        "confidence": 0.9,
        "confirmed_decision": "Confirm as detected",
        "classification_evidence": "Matched drawing title.",
        "rooms": rooms or [],
    }


def main():
    pages = [
        page(1, "floor_plan", "primary_geometry", title="Ground Floor Retail Plan", rooms=[{"name": "Shop", "area": "30 m²"}]),
        page(2, "elevation", "surface_confirmation", title="Shopfront Elevation"),
        page(3, "section", "surface_confirmation", title="Building Section"),
        page(4, "site_plan", "site_orientation_or_shading", level="Unassigned level", title="Site Plan"),
        page(5, "perspective_or_3d", "visual_context", title="3D Perspective"),
        page(6, "cover_or_drawing_list", "not_calculation_evidence", level="Unassigned level", title="Drawing List"),
    ]
    reviewed_levels = {"pages": [{"page": row["page"], "floor_label": row["level_name"]}
                                 for row in pages if row["level_name"] != "Unassigned level"]}
    coverage = build_drawing_coverage({"source_pdf": "/tmp/set.pdf", "drawing_set": {"pages": pages},
                                       "page_triage": reviewed_levels})
    check("coverage carries level-classification method version", coverage["method_versions"]["level_classification"], 2)
    check("every page indexed once", [item["page"] for item in coverage["sheet_register"]], [1, 2, 3, 4, 5, 6])
    check("elevation retains surface role", coverage["sheet_register"][1]["thermal_role"], "surface_confirmation")
    check("3d view retains visual context", coverage["sheet_register"][4]["thermal_role"], "visual_context")
    ground = next(level for level in coverage["levels"] if level["level_name"] == "Ground Floor")
    check("floor purpose stays inferred", ground["purpose_status"], "inferred")
    check("floor purpose proposed", ground["proposed_purpose"], "food retail / food preparation")
    check("complete fixture avoids surface warning", any(item["item_id"].startswith("surface_views_missing-ground_floor") for item in coverage["coverage_exceptions"]), False)
    check("coverage preserves site evidence", any(link["thermal_role"] == "site_orientation_or_shading" for link in coverage["cross_sheet_links"]), True)
    elevation_role = coverage["page_roles"][1]
    check("shopfront elevation has opening geometry capability", elevation_role["opening_geometry_eligible"], True)
    check("shopfront elevation remains outside room geometry", elevation_role["geometry_eligible"], False)

    missing = build_drawing_coverage({"drawing_set": {"pages": [pages[0]]}})
    check("missing elevation is flagged", missing["coverage_exceptions"][0]["item_id"], "surface_views_missing-ground_floor")
    check("missing site is flagged", any(item["item_id"] == "site_context_missing-project" for item in missing["coverage_exceptions"]), True)

    # Title-block OCR is authoritative for sheet identity; dates and legacy
    # flattened metadata must not become drawing numbers.
    identity_pages = [page(20, "floor_plan", "primary_geometry", title="including amendments of the relevant Building Code of", rooms=[])]
    identity_pages[0]["drawing_number"] = "26.02"
    identity_pages[0]["structured_content"] = {"markdown": "DIMENSION PLAN\n202 SCALE1:100\nGeneral Notes: date 26.02.26"}
    identity = build_drawing_coverage({"source_pdf": "/tmp/set.pdf", "drawing_set": {"pages": identity_pages}}, {
        "pages": [{"page": 20, "title_blocks": [{"text_excerpt": "DRAWING DWG NO JOB NO DIMENSION PLAN 202 SCALE1:100 DATE 26.02.26"}], "dimension_candidates": []}]
    })["page_roles"][0]["identity"]
    check("title-block drawing number beats date metadata", identity["selected_drawing_number"], "202")
    check("date metadata is not retained as drawing number", all(candidate["value"] != "26.02" for candidate in identity["drawing_number_candidates"]), True)

    service = build_drawing_coverage({"source_pdf": "/tmp/set.pdf", "drawing_set": {"pages": [
        page(23, "architect_lighting_plan", "services_or_internal_load", title="Service Plan - Lighting"),
        page(16, "render_or_photo", "visual_context", title="3D Render"),
    ]}})
    service_roles = {row["page"]: row for row in service["page_roles"]}
    check("service page gets lighting capability", "lighting" in service_roles[23]["capability_map"], True)
    check("3D page gets cross-check capability", "3d_cross_check" in service_roles[16]["capability_map"], True)

    # Finish legends and detail labels are not building floors. Preserve their
    # exact source text and classification, while title-block/page-title levels
    # can be selected only when they agree.
    synthetic = {"source_pdf": "fixture.pdf", "drawing_set": {"pages": [{"page": 1,
        "title": "Finish Plan", "structured_content": {"markdown":
            "FLOOR TILING AT FL 02 BUFFET AREA EDGE\nTILE FL 02 2MM EPOXY\nDoor Battens - Level 3 Detail"}}]},
        "spatial_ocr": {}}
    finish_ocr = {"pages": [{"page": 1, "title_blocks": [{"text_excerpt": "International Kiosk, Level 2, Sydney"}]}]}
    classified = build_drawing_coverage(synthetic, finish_ocr)["pages"][0]
    finish_only = build_drawing_coverage(synthetic)["pages"][0]
    check("finish-only text proposes no building level",
          [row["kind"] for row in finish_only["level_candidates"] if row["kind"] == "building_level"], [])
    schedule = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1,
        "detected_type": "material_or_finish_schedule",
        "structured_content": {"markdown": "PT02"}}]}})["pages"][0]
    check("finish schedule classification keeps isolated finish tags out of floors",
          schedule["level_candidates"][0]["kind"], "finish_or_tag_code")
    check("body-text finish codes do not become building levels",
          [row["raw_text"] for row in classified["level_candidates"]
           if row["source"] == "body_text" and row["kind"] == "building_level"], [])
    check("finish legend wording is retained",
          [row["raw_text"] for row in classified["level_candidates"] if row["kind"] == "finish_or_tag_code"],
          ["FL 02", "FL 02"])
    check("door-batten detail label is classified separately",
          next(row["kind"] for row in classified["level_candidates"] if row["raw_text"] == "Level 3"), "detail_label")
    check("title-block address level is selected despite other text", classified["level_name"], "Level 2")
    check("selected title-block level status", classified["level_status"], "confirmed_by_text")
    check("title-block source text remains verbatim",
          next(row["raw_text"] for row in classified["level_candidates"] if row["kind"] == "building_level"), "Level 2")
    address = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1,
        "structured_content": {"markdown": "Project title\nInternational Kiosk, Level 2, Western Sydney Airport"}}]}})["pages"][0]
    check("address-shaped body text identifies a building level", address["level_name"], "Level 2")
    check("address source is explicit", next(row["source"] for row in address["level_candidates"] if row["kind"] == "building_level"), "address")
    room_mention = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1,
        "structured_content": {"markdown": "Dining Room, Level 2"}}]}})["pages"][0]
    check("room-label phrase in body text does not become a floor", room_mention["level_name"] == ""
          and room_mention["level_candidates"][0]["kind"] == "body_text_level", True)

    ground = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1, "title": "Retail Plan"}]},
        "page_triage": {"pages": [{"page": 1, "floor_label": "Ground Floor"}]}})["pages"][0]
    check("reviewed triage label takes precedence", ground["level_name"], "Ground Floor")
    triage_over_conflict = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1, "title": "Level 2 Plan"}]},
        "page_triage": {"pages": [{"page": 1, "floor_label": "Ground Floor"}]}},
        {"pages": [{"page": 1, "title_blocks": [{"text_excerpt": "Project Address, Level 1"}]}]})["pages"][0]
    check("reviewed triage overrides conflicting detected levels", triage_over_conflict["level_name"], "Ground Floor")
    check("reviewed triage selection has explicit status", triage_over_conflict["level_status"], "confirmed_by_review")
    title_ground = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1, "title": "GROUND FLOOR"}]}})["pages"][0]
    check("title-block named ground floor is detected", title_ground["level_name"], "Ground Floor")
    conflict = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1, "title": "Level 2 Plan"}]},
        "spatial_ocr": {}, "page_triage": {"pages": []}},
        {"pages": [{"page": 1, "title_blocks": [{"text_excerpt": "Job Address, Level 1"}]}]})["pages"][0]
    check("conflicting title and body text does not select a floor", conflict["level_name"], "")
    check("conflicting building-level text marks page ambiguous", conflict["level_status"], "ambiguous")
    body_level = build_drawing_coverage({"drawing_set": {"pages": [{"page": 1,
        "structured_content": {"markdown": "Level 1 notes"}}]}})["pages"][0]
    check("body text level remains non-authoritative", body_level["level_candidates"][0]["kind"], "body_text_level")


if __name__ == "__main__":
    main()
