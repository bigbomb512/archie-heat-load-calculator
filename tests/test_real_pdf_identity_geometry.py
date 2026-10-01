"""Focused identity/scale/AI-geometry regression checks.

These are deliberately small deterministic checks; they do not use a private
PDF and do not create calculator-ready geometry from an AI response.
"""

from ai.drawing_coverage import build_drawing_coverage
from ai.geometry_resolution import build_geometry_resolution
from ai.room_inference import select_pages
from ai.vector_geometry import geometry_page_refs


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

    # A flattened footer can become the legacy page title even when the
    # structured text still contains the actual drawing title block.
    layout_page = {
        "page": 19,
        "title": "including amendments of the relevant Building Code of",
        "drawing_number": "26.02",
        "detected_type": "architectural_detail_noise",
        "plan_role": "reference_context",
        "structured_content": {"markdown": "PROPOSED FLOOR LAYOUT 4. Studio Hiyaku notes. 201 SCALE1:100 General Notes:"},
    }
    layout_input = {"source_pdf": "fixture.pdf", "drawing_set": {"pages": [layout_page]}}
    layout_coverage = build_drawing_coverage(layout_input)
    layout_role = layout_coverage["page_roles"][0]
    check("structured title block recovers the proposed floor layout", layout_role["proposed_role"] == "main_floor_plan")
    check("structured title block recovers sheet number 201", layout_role["drawing_number"] == "201")
    check("recovered floor layout is selected for geometry", layout_role["geometry_eligible"] is True)
    check("room inference selects the recovered layout", 19 in {item["page"] for item in select_pages(layout_input, layout_coverage)})
    check("vector extraction selects the recovered layout", 19 in {item["page"] for item in geometry_page_refs(layout_input)})

    level_page = dict(page)
    level_page["page"] = 21
    level_page["structured_content"] = {"markdown": "FLOOR FINISH PLAN 203 02 FL SCALE1:100"}
    level_coverage = build_drawing_coverage({"drawing_set": {"pages": [level_page]}}, {"pages": []})
    check("floor-finish code stays evidence and is not selected as a building level",
          level_coverage["pages"][0]["level_name"] == ""
          and any(row["kind"] == "finish_or_tag_code" for row in level_coverage["pages"][0]["level_candidates"]))

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

    # For preliminary mode only, a simple high-confidence boundary tied to a
    # confirmed main-viewport scale may produce an explicitly AI-estimated
    # area. Missing vector confirmation, a named floor, and a second witness
    # stay visible as review warnings; the reviewed path remains strict.
    scaled_building = {"spaces": [{"id": "dining", "name": "Dining", "level_name": "Unassigned level",
                                    "evidence": [{"page": 20, "excerpt": "Dining"}]}]}
    scaled_coverage = {"pages": [{"page": 20, "proposed_role": "main_floor_plan", "drawing_number": "202"}]}
    scaled_candidate = {"room_id": "room-dining", "label": "Dining", "level_name": "Unassigned level", "page": 20,
        "source_pages": [20], "confidence_score": 0.87,
        "unresolved_fields": ["level", "precise_vector_boundary", "independent_dimension_cross_check"],
        "geometry": {"boundary_points_px": [[10, 10], [30, 10], [30, 20], [10, 20], [10, 10]],
            "scale_mm_per_px": 50.0, "scale_source": "confirmed_main_viewport_scale",
            "calibration": {"confirmed_main_viewport_scale": "1:100", "canonical_image_width_px": 745,
                            "pdf_physical_page_width_mm": 420.1583333333}}}
    preliminary_geometry = build_geometry_resolution(
        ai, scaled_coverage, scaled_building, ocr, {}, resolution_mode="preliminary_ai_estimate",
        room_proposals=[scaled_candidate],
    )
    estimated = [item for item in preliminary_geometry["entities"]
                 if item.get("kind") == "ai_room_geometry" and item.get("geometry_status") == "ai_estimated"]
    check("confirmed-scale high-confidence AI boundary creates draft area", bool(estimated) and estimated[0]["value"]["area_m2"] == 0.5)
    check("draft geometry keeps level/vector/dimension gaps as warnings", bool(estimated) and {"level", "precise_vector_boundary", "independent_dimension_cross_check"}.issubset(set(estimated[0].get("review_warnings", []))))
    reviewed_geometry = build_geometry_resolution(
        ai, scaled_coverage, scaled_building, ocr, {}, resolution_mode="engineering_reviewed",
        room_proposals=[scaled_candidate],
    )
    check("same AI boundary cannot activate reviewed area", not any(item.get("kind") == "ai_room_geometry" and item.get("geometry_status") == "geometry_confirmed" for item in reviewed_geometry["entities"]))

    # A dimension-chain polygon in millimetres must not depend on viewport
    # scaling. Every edge is tied to its own cited written dimension, so the
    # resolver can recompute the area while the reviewed path still requires
    # its separate witness policy.
    mm_points = [[0, 0], [4000, 0], [4000, 3000], [0, 3000], [0, 0]]
    wall_ids = ["W1", "W2", "W3", "W4"]
    dimension_ids = ["D1", "D2", "D3", "D4"]
    walls, dimensions, links = [], [], []
    for index, (start, end, wall_id, dimension_id) in enumerate(zip(mm_points, mm_points[1:], wall_ids, dimension_ids)):
        value_mm = 4000 if index in {0, 2} else 3000
        walls.append({"wall_id": wall_id, "line_start_mm": start, "line_end_mm": end})
        dimensions.append({"dimension_id": dimension_id, "value_mm": value_mm, "unit": "mm", "page": 20})
        links.append({"dimension_id": dimension_id, "target_wall_id": wall_id, "value_mm": value_mm,
                      "reason": "Printed witness dimension terminates at the two inside-face endpoints.", "source": "page:20 crop:dimension-chain"})
    mm_candidate = {"room_id": "room-dining-mm", "label": "Dining", "level_name": "Unassigned level", "page": 20,
        "source_pages": [20], "confidence_score": .92, "unresolved_fields": [], "conflicts": [],
        "geometry": {"coordinate_units": "mm", "boundary_points_mm": mm_points, "wall_ids": wall_ids,
            "walls": walls, "dimension_ids": dimension_ids, "dimensions": dimensions, "dimension_wall_links": links}}
    dimensioned = build_geometry_resolution(
        ai, scaled_coverage, scaled_building, ocr, {}, resolution_mode="preliminary_ai_estimate",
        room_proposals=[mm_candidate],
    )
    measured = [item for item in dimensioned["entities"] if item.get("kind") == "ai_room_geometry" and item.get("geometry_status") == "ai_estimated"]
    check("dimension-space polygon calculates area without scaling the PDF", bool(measured) and measured[0]["value"]["area_m2"] == 12.0)
    check("dimension-space area preserves operands and units", bool(measured) and measured[0]["value"]["derivation"]["operands"] == {"polygon_area_mm2": 12000000.0, "coordinate_units": "mm"})
    reviewed_mm = build_geometry_resolution(
        ai, scaled_coverage, scaled_building, ocr, {}, resolution_mode="engineering_reviewed",
        room_proposals=[mm_candidate],
    )
    check("dimension-space AI estimate cannot activate reviewed area", not any(item.get("kind") == "ai_room_geometry" and item.get("geometry_status") == "geometry_confirmed" for item in reviewed_mm["entities"]))


if __name__ == "__main__":
    main()
