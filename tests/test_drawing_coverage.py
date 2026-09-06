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
    coverage = build_drawing_coverage({"source_pdf": "/tmp/set.pdf", "drawing_set": {"pages": pages}})
    check("every page indexed once", [item["page"] for item in coverage["sheet_register"]], [1, 2, 3, 4, 5, 6])
    check("elevation retains surface role", coverage["sheet_register"][1]["thermal_role"], "surface_confirmation")
    check("3d view retains visual context", coverage["sheet_register"][4]["thermal_role"], "visual_context")
    ground = next(level for level in coverage["levels"] if level["level_name"] == "Ground Floor")
    check("floor purpose stays inferred", ground["purpose_status"], "inferred")
    check("floor purpose proposed", ground["proposed_purpose"], "food retail / food preparation")
    check("complete fixture avoids surface warning", any(item["item_id"].startswith("surface_views_missing-ground_floor") for item in coverage["coverage_exceptions"]), False)
    check("coverage preserves site evidence", any(link["thermal_role"] == "site_orientation_or_shading" for link in coverage["cross_sheet_links"]), True)

    missing = build_drawing_coverage({"drawing_set": {"pages": [pages[0]]}})
    check("missing elevation is flagged", missing["coverage_exceptions"][0]["item_id"], "surface_views_missing-ground_floor")
    check("missing site is flagged", any(item["item_id"] == "site_context_missing-project" for item in missing["coverage_exceptions"]), True)


if __name__ == "__main__":
    main()
