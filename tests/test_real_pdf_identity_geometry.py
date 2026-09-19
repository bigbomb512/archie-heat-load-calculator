"""Focused identity/scale/AI-geometry regression checks.

These are deliberately small deterministic checks; they do not use a private
PDF and do not create calculator-ready geometry from an AI response.
"""

from ai.drawing_coverage import build_drawing_coverage
from ai.geometry_resolution import build_geometry_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    page = {
        "page": 20,
        "title": "Dimension Plan",
        "drawing_number": "26.02",
        "detected_type": "floor_plan",
        "plan_role": "main_floor_plan",
        "structured_content": {"markdown": "DIMENSION PLAN 202 SCALE1:100"},
    }
    ocr = {"pages": [{
        "page": 20,
        "title_blocks": [{"text_excerpt": "DIMENSION PLAN 202 SCALE1:100 DATE 26.02.26"}],
        "scale_candidates": [
            {"text": "1:100", "source": "bottom_band"},
            {"text": "1:2", "source": "bottom_right"},
        ],
        "room_label_candidates": [],
    }]}
    coverage = build_drawing_coverage({"drawing_set": {"pages": [page]}}, ocr)
    row = coverage["pages"][0]
    check("numeric title-block sheet number is selected", row["drawing_number"] == "202")
    check("date metadata is not selected", row["drawing_number"] != "26.02")
    check("main scale is preferred", row["main_scale"] == "1:100")
    check("detail scale remains detail-only", any(item.get("context") == "embedded_detail" for item in row["scale_candidates"]))

    level_page = dict(page)
    level_page["page"] = 21
    level_page["structured_content"] = {"markdown": "FLOOR FINISH PLAN 203 02 FL SCALE1:100"}
    level_coverage = build_drawing_coverage({"drawing_set": {"pages": [level_page]}}, {"pages": []})
    check("level candidate is preserved", level_coverage["pages"][0]["level_name"] == "FL 02")

    conflict_page = dict(page)
    conflict_page["structured_content"] = {"markdown": "DWG NO 202 DIMENSION PLAN 203"}
    conflict = build_drawing_coverage({"drawing_set": {"pages": [conflict_page]}}, {"pages": []})
    check("conflicting identities remain visible", conflict["pages"][0]["identity_status"] == "ambiguous")
    check("conflicting date metadata is not authoritative", conflict["sheet_register"][0]["drawing_number"] == "")

    ai = {"source_pdf": "fixture.pdf", "drawing_set": {"pages": []}}
    vision = {"result": {"auto_extraction": {"entities": [{
        "kind": "room", "page": 20, "label": "Studio", "level_name": "FL 02",
        "geometry_status": "geometry_proposed", "boundary_points_px": [[0, 0], [10, 0], [10, 10], [0, 0]],
        "wall_ids": [], "dimension_ids": [], "witnesses": [], "confidence": "medium",
        "unresolved_fields": [],
    }]}}}
    geometry = build_geometry_resolution(ai, coverage, {}, ocr, {}, vision_response=vision)
    proposals = [item for item in geometry["entities"] if item["kind"] == "room_geometry_proposal"]
    check("AI geometry response creates a proposed boundary", proposals and proposals[0]["geometry_status"] == "geometry_proposed")
    check("AI geometry does not activate area", not any(item["kind"] == "area" and item["geometry_status"] == "geometry_confirmed" for item in geometry["entities"]))


if __name__ == "__main__":
    main()
