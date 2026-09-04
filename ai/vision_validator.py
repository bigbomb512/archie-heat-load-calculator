#!/usr/bin/env python3

import argparse
import json
import math
import re
from pathlib import Path

from ai.ai_packet import load_json
from ai.geometry_review import normalise_vision


VISION_DIMENSION_ID = re.compile(r"^P(?P<page>\d+)-VDIM-VISION-\d+$")
OBSTACLE_ID = re.compile(r"^P(?P<page>\d+)-OBS-\d+$")


def validate_vision_file(path, output_path=None, candidate_review_path=None):
    path = Path(path)
    vision = load_json(path)
    candidate_review = load_json(candidate_review_path) if candidate_review_path else None
    report = validate_vision(vision, candidate_review)
    output_path = Path(output_path or path.with_name("vision_validation.json"))
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path


def validate_vision(vision, candidate_review=None):
    vision = normalise_vision(vision, candidate_review)
    result = vision.get("result", {})
    pages = vision_pages(result)
    layered_pages = vision_layered_pages(result)
    issues = []
    usable = []
    known_candidate_ids = candidate_ids_by_page(candidate_review or {})
    annotation_kinds = annotation_kinds_by_page(candidate_review or {})
    for page in pages:
        coordinate_system = page.get("coordinate_system", {})
        width = coordinate_system.get("image_width")
        height = coordinate_system.get("image_height")
        wall_ids = {item.get("candidate_id") for item in page.get("wall_candidates", []) if item.get("candidate_id")}
        if not page.get("page"):
            issues.append(issue("", {}, "page", "page is required"))
        if has_coordinate_candidates(page) and not page.get("image"):
            issues.append(issue(page.get("page"), {}, "image", "image filename is required for coordinate evidence"))
        if not valid_image_size(width, height):
            issues.append(issue(page.get("page"), {}, "coordinate_system", "image_width and image_height are required positive numbers"))
        if has_coordinate_candidates(page):
            viewport = page.get("plan_viewport_bbox_px")
            if not valid_bbox(viewport, width, height):
                issues.append(issue(page.get("page"), {}, "plan_viewport_bbox_px", "plan viewport bbox is required and must be inside image bounds"))
        for key in ["wall_candidates", "dimension_candidates", "room_label_candidates", "opening_candidates"]:
            for item in page.get(key, []):
                issues.extend(candidate_issues(page.get("page"), item, width, height))
                issues.extend(candidate_viewport_issues(page.get("page"), item, page.get("plan_viewport_bbox_px")))
                issues.extend(candidate_id_issues(page.get("page"), item, known_candidate_ids))
        for item in page.get("fixed_obstacle_candidates", []):
            issues.extend(fixed_obstacle_issues(page.get("page"), item, width, height))
        for item in page.get("wall_dimensions", []):
            item_issues = measurement_issues(page.get("page"), item, width, height, wall_ids)
            item_issues.extend(candidate_link_issues(page.get("page"), item, known_candidate_ids))
            if item_issues:
                issues.extend(item_issues)
            elif item.get("should_use_for_calculation") is True and item.get("confidence") == "high":
                usable.append(item.get("measurement_id", item.get("text_seen", "")))
    for page in layered_pages:
        issues.extend(layered_page_issues(page, known_candidate_ids, annotation_kinds))

    return {
        "source": str(vision.get("source", "")),
        "provider": vision.get("provider", ""),
        "model": vision.get("model", ""),
        "usable_measurement_ids": usable,
        "layered_geometry_page_count": len(layered_pages),
        "layered_geometry_ready_pages": layered_ready_pages(layered_pages, issues),
        "issue_count": len(issues),
        "layered_geometry_issue_count": len([item for item in issues if item.get("field", "").startswith("layered_geometry")]),
        "issues": issues,
    }


def vision_pages(result):
    if isinstance(result.get("pages"), list):
        return result.get("pages", [])
    coordinate_review = result.get("coordinate_review", {})
    if isinstance(coordinate_review, dict) and isinstance(coordinate_review.get("pages"), list):
        return coordinate_review.get("pages", [])
    return []


def vision_layered_pages(result):
    layered = result.get("layered_geometry", {})
    if isinstance(layered, dict) and isinstance(layered.get("pages"), list):
        return layered.get("pages", [])
    return []


def layered_ready_pages(pages, issues):
    blocked = {item.get("page") for item in issues if item.get("field", "").startswith("layered_geometry")}
    return [
        page.get("page")
        for page in pages
        if page.get("page") not in blocked and page.get("geometry_readiness") == "vision_layered"
    ]


def layered_page_issues(page, known_candidate_ids=None, annotation_kinds=None):
    issues = []
    page_number = page.get("page")
    role = page.get("page_role", "")
    if not page_number:
        issues.append(issue("", {}, "layered_geometry.page", "layered geometry page is required"))
    if role in {"main_geometry_and_dimension_plan", "supporting_geometry_plan"}:
        for key in [
            "outer_boundary_walls",
            "internal_partitions",
            "fixture_or_joinery_geometry",
            "fixed_obstacles",
            "columns",
            "openings",
            "dimension_candidates",
            "dimension_wall_links",
            "rejected_or_noise_candidates",
        ]:
            if key not in page or not isinstance(page.get(key), list):
                issues.append(issue(page_number, {}, f"layered_geometry.{key}", f"{key} must be present as a list"))
        for wall in page.get("outer_boundary_walls", []):
            issues.extend(layered_line_issues(page_number, wall, "outer_boundary_wall", "wall_id", known_candidate_ids))
        for wall in page.get("internal_partitions", []):
            issues.extend(layered_line_issues(page_number, wall, "internal_partition", "wall_id", known_candidate_ids))
        for item in page.get("fixture_or_joinery_geometry", []):
            issues.extend(layered_line_issues(page_number, item, "fixture_or_joinery", "geometry_id", known_candidate_ids))
        for item in page.get("fixed_obstacles", []):
            issues.extend(fixed_obstacle_issues(page_number, item, image_width(page), image_height(page)))
        for item in page.get("dimension_candidates", []):
            issues.extend(layered_dimension_issues(page_number, item, (annotation_kinds or {}).get(page_number, {})))
        wall_ids = {item.get("wall_id") for item in page.get("outer_boundary_walls", []) + page.get("internal_partitions", []) if item.get("wall_id")}
        fixture_ids = {item.get("geometry_id") for item in page.get("fixture_or_joinery_geometry", []) if item.get("geometry_id")}
        dimension_ids = {item.get("dimension_id") for item in page.get("dimension_candidates", []) if item.get("dimension_id")}
        for link in page.get("dimension_wall_links", []):
            issues.extend(layered_link_issues(page_number, link, wall_ids, fixture_ids, dimension_ids))
        if page.get("geometry_readiness") == "vision_layered" and not page.get("outer_boundary_walls"):
            issues.append(issue(page_number, {}, "layered_geometry.outer_boundary_walls", "vision-layered geometry requires at least one outer boundary wall"))
        if page.get("geometry_readiness") == "vision_layered" and not page.get("dimension_candidates"):
            issues.append(issue(page_number, {}, "layered_geometry.dimension_candidates", "vision-layered geometry requires visible dimensions or an explicit lower readiness"))
    return issues


def layered_line_issues(page_number, item, expected_classification, id_field, known_candidate_ids=None):
    issues = []
    if not item.get(id_field):
        issues.append(issue(page_number, item, f"layered_geometry.{id_field}", f"{id_field} is required"))
    if item.get("classification") != expected_classification:
        issues.append(issue(page_number, item, "layered_geometry.classification", f"classification must be {expected_classification}"))
    if "line_start_px" in item or "line_end_px" in item:
        if not plain_point(item.get("line_start_px")) or not plain_point(item.get("line_end_px")):
            issues.append(issue(page_number, item, "layered_geometry.line", "line_start_px and line_end_px are required valid points"))
    if item.get("confidence") not in {"low", "medium", "high"}:
        issues.append(issue(page_number, item, "layered_geometry.confidence", "confidence must be low, medium, or high"))
    issues.extend(layered_source_candidate_issues(page_number, item, known_candidate_ids))
    return issues


def layered_source_candidate_issues(page_number, item, known_candidate_ids):
    if not known_candidate_ids:
        return []
    valid_ids = known_candidate_ids.get(page_number, set())
    issues = []
    sources = item.get("source_candidate_ids") or []
    if item.get("source") == "vector_anchored" and not sources:
        issues.append(issue(page_number, item, "layered_geometry.source_candidate_ids", "vector-anchored walls need at least one supporting raw vector id"))
    if item.get("source") == "image_proposed" and sources:
        issues.append(issue(page_number, item, "layered_geometry.source_candidate_ids", "image-proposed walls must not claim unsupported vector ids"))
    for candidate_id in sources:
        if candidate_id not in valid_ids:
            issues.append(
                issue(
                    page_number,
                    item,
                    "layered_geometry.source_candidate_ids",
                    f"source candidate id {candidate_id} is not present in candidate_review.json for this page",
                )
            )
    return issues


def layered_dimension_issues(page_number, item, annotation_kinds):
    issues = []
    if not item.get("dimension_id"):
        issues.append(issue(page_number, item, "layered_geometry.dimension_id", "dimension_id is required"))
    if not isinstance(item.get("value_mm"), (int, float)):
        issues.append(issue(page_number, item, "layered_geometry.value_mm", "value_mm is required"))
    if not item.get("text_seen"):
        issues.append(issue(page_number, item, "layered_geometry.text_seen", "text_seen is required"))
    if item.get("confidence") not in {"low", "medium", "high"}:
        issues.append(issue(page_number, item, "layered_geometry.confidence", "confidence must be low, medium, or high"))
    source = item.get("source", "pdf_extracted")
    source_id = item.get("source_annotation_id") or item.get("dimension_id")
    kind = annotation_kinds.get(source_id)
    if kind == "detail_or_sheet_reference":
        issues.append(issue(page_number, item, "layered_geometry.source_annotation_id", "detail or sheet reference annotations cannot become dimensions"))
    if kind == "unknown_numeric" and not item.get("visible_evidence"):
        issues.append(issue(page_number, item, "layered_geometry.visible_evidence", "vision-promoted unknown numeric annotations need visible dimension evidence"))
    if source == "screenshot_visible":
        issues.extend(screenshot_dimension_issues(page_number, item))
    elif source != "pdf_extracted":
        issues.append(issue(page_number, item, "layered_geometry.source", "source must be pdf_extracted or screenshot_visible"))
    return issues


def screenshot_dimension_issues(page_number, item):
    issues = []
    match = VISION_DIMENSION_ID.fullmatch(str(item.get("dimension_id", "")))
    if not match or int(match.group("page")) != page_number:
        issues.append(issue(page_number, item, "layered_geometry.dimension_id", "screenshot-visible dimensions need a page-local P{page}-VDIM-VISION-{number} id"))
    if item.get("source_annotation_id"):
        issues.append(issue(page_number, item, "layered_geometry.source_annotation_id", "screenshot-visible dimensions cannot claim an extracted annotation id"))
    if not valid_bbox_shape(item.get("bbox_px")):
        issues.append(issue(page_number, item, "layered_geometry.bbox_px", "screenshot-visible dimensions need a valid text bbox"))
    for field in ["dimension_line_start_px", "dimension_line_end_px", "measured_span_start_px", "measured_span_end_px"]:
        if not plain_point(item.get(field)):
            issues.append(issue(page_number, item, f"layered_geometry.{field}", f"{field} is required for screenshot-visible dimensions"))
    has_arrows = plain_point(item.get("arrowhead_start_px")) and plain_point(item.get("arrowhead_end_px"))
    has_witness = any(
        isinstance(line, list) and len(line) == 2 and plain_point(line[0]) and plain_point(line[1])
        for line in item.get("witness_lines_px", [])
    )
    if not has_arrows and not has_witness:
        issues.append(issue(page_number, item, "layered_geometry.dimension_evidence", "screenshot-visible dimensions need arrow/tick positions or a witness line"))
    if not item.get("visible_evidence"):
        issues.append(issue(page_number, item, "layered_geometry.visible_evidence", "screenshot-visible dimensions need positive visible evidence"))
    return issues


def valid_bbox_shape(bbox):
    return isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(value, (int, float)) for value in bbox) and bbox[0] < bbox[2] and bbox[1] < bbox[3]


def layered_link_issues(page_number, item, wall_ids, fixture_ids, dimension_ids):
    issues = []
    target = item.get("target_wall_id")
    target_class = item.get("target_wall_classification")
    if not item.get("measurement_id"):
        issues.append(issue(page_number, item, "layered_geometry.measurement_id", "measurement_id is required"))
    if item.get("dimension_id") not in dimension_ids:
        issues.append(issue(page_number, item, "layered_geometry.dimension_id", "dimension link must reference an existing dimension candidate"))
    if target_class in {"outer_boundary_wall", "internal_partition"} and target not in wall_ids:
        issues.append(issue(page_number, item, "layered_geometry.target_wall_id", "dimension link must reference an existing wall"))
    if target_class == "fixture_or_joinery" and target not in fixture_ids:
        issues.append(issue(page_number, item, "layered_geometry.target_wall_id", "fixture dimension link must reference existing fixture/joinery geometry"))
    for field in ["dimension_line_start_px", "dimension_line_end_px", "target_wall_start_px", "target_wall_end_px"]:
        if not plain_point(item.get(field)):
            issues.append(issue(page_number, item, f"layered_geometry.{field}", f"{field} is required as a point"))
    if item.get("confidence") not in {"low", "medium", "high"}:
        issues.append(issue(page_number, item, "layered_geometry.confidence", "confidence must be low, medium, or high"))
    if item.get("should_use_for_calculation") is True and item.get("confidence") != "high":
        issues.append(issue(page_number, item, "layered_geometry.should_use_for_calculation", "only high-confidence layered links may be calculation-ready"))
    return issues


def candidate_ids_by_page(candidate_review):
    ids = {}
    for page in candidate_review.get("pages", []):
        page_number = page.get("page")
        page_ids = set(page.get("candidate_ids", []))
        for key in [
            "wall_groups",
            "wall_candidates",
            "dimension_line_candidates",
            "witness_line_candidates",
            "curve_candidates",
            "dimension_text_candidates",
            "rejected_or_context_candidates",
        ]:
            for item in page.get(key, []):
                item_id = item.get("wall_group_id") or item.get("candidate_id") or item.get("wall_id")
                if item_id:
                    page_ids.add(item_id)
        ids[page_number] = page_ids
    return ids


def annotation_kinds_by_page(candidate_review):
    return {
        page.get("page"): {
            item.get("candidate_id"): item.get("annotation_kind")
            for key in ["dimension_text_candidates", "unknown_numeric_annotations"]
            for item in page.get(key, [])
            if item.get("candidate_id")
        }
        for page in candidate_review.get("pages", [])
    }


def candidate_id_issues(page_number, item, known_candidate_ids):
    if not known_candidate_ids:
        return []
    item_id = item.get("source_candidate_id") or item.get("candidate_id")
    if item.get("source") in {"vision_model", "vector_anchored", "image_proposed"} and not item.get("source_candidate_id"):
        return []
    if item_id and item_id not in known_candidate_ids.get(page_number, set()):
        return [issue(page_number, item, "candidate_id", "candidate id is not present in candidate_review.json for this page")]
    return []


def candidate_link_issues(page_number, item, known_candidate_ids):
    if not known_candidate_ids:
        return []
    issues = []
    for field in ["target_wall_candidate_id", "dimension_line_candidate_id", "dimension_text_candidate_id"]:
        value = item.get(field)
        if value and value not in known_candidate_ids.get(page_number, set()) and not str(value).startswith((f"P{page_number}-WALL", f"P{page_number}-VWALL")):
            issues.append(issue(page_number, item, field, "linked candidate id is not present in candidate_review.json for this page"))
    return issues


def measurement_issues(page_number, item, width, height, wall_ids=None):
    issues = []
    item = normalised_measurement_item(item)
    required = [
        "measurement_id",
        "value_mm",
        "dimension_text_bbox_px",
        "dimension_line_start_px",
        "dimension_line_end_px",
        "target_wall_candidate_id",
        "target_wall_start_px",
        "target_wall_end_px",
        "confidence",
        "should_use_for_calculation",
    ]
    for field in required:
        if field not in item:
            issues.append(issue(page_number, item, field, "missing required field"))
    if issues:
        return issues

    if item.get("should_use_for_calculation") is True and item.get("applies_to") and not item.get("target_wall_candidate_id"):
        issues.append(issue(page_number, item, "target_wall_candidate_id", "calculation-ready measurements need wall coordinates, not only applies_to text"))
    if item.get("target_wall_candidate_id") not in (wall_ids or set()):
        issues.append(issue(page_number, item, "target_wall_candidate_id", "target wall candidate must exist on the same page"))

    for field in ["dimension_line_start_px", "dimension_line_end_px", "target_wall_start_px", "target_wall_end_px"]:
        if not valid_point(item[field], width, height):
            issues.append(issue(page_number, item, field, "point is outside image bounds or invalid"))

    if not valid_bbox(item["dimension_text_bbox_px"], width, height):
        issues.append(issue(page_number, item, "dimension_text_bbox_px", "bbox is outside image bounds or invalid"))

    if line_length(item["dimension_line_start_px"], item["dimension_line_end_px"]) < 5:
        issues.append(issue(page_number, item, "dimension_line", "dimension line is too short"))
    if line_length(item["target_wall_start_px"], item["target_wall_end_px"]) < 5:
        issues.append(issue(page_number, item, "target_wall", "target wall is too short"))
    if not roughly_parallel(item["dimension_line_start_px"], item["dimension_line_end_px"], item["target_wall_start_px"], item["target_wall_end_px"]):
        issues.append(issue(page_number, item, "relationship", "dimension line and target wall are not roughly parallel"))
    if item.get("should_use_for_calculation") is True and item.get("confidence") != "high":
        issues.append(issue(page_number, item, "should_use_for_calculation", "only high-confidence measurements may be calculation-ready"))

    return issues


def normalised_measurement_item(item):
    aliases = {
        "dimension_text_bbox": "dimension_text_bbox_px",
        "dimension_line_start": "dimension_line_start_px",
        "dimension_line_end": "dimension_line_end_px",
        "target_wall_start": "target_wall_start_px",
        "target_wall_end": "target_wall_end_px",
    }
    clean = dict(item)
    for old, new in aliases.items():
        if new not in clean and old in clean:
            clean[new] = clean[old]
    return clean


def candidate_issues(page_number, item, width, height):
    issues = []
    if not item.get("candidate_id"):
        issues.append(issue(page_number, item, "candidate_id", "candidate_id is required"))
    if item.get("source") not in {"vision_model", "vector_anchored", "image_proposed", "spatial_ocr", "manual_review", "pdf_vector"}:
        issues.append(issue(page_number, item, "source", "source must identify the vision or vector evidence source"))
    if item.get("confidence") not in {"low", "medium", "high"}:
        issues.append(issue(page_number, item, "confidence", "confidence must be low, medium, or high"))

    if "line_start_px" in item or "line_end_px" in item:
        if not valid_point(item.get("line_start_px"), width, height):
            issues.append(issue(page_number, item, "line_start_px", "point is outside image bounds or invalid"))
        if not valid_point(item.get("line_end_px"), width, height):
            issues.append(issue(page_number, item, "line_end_px", "point is outside image bounds or invalid"))
        if valid_point(item.get("line_start_px"), width, height) and valid_point(item.get("line_end_px"), width, height):
            if line_length(item["line_start_px"], item["line_end_px"]) < 5:
                issues.append(issue(page_number, item, "line", "candidate line is too short"))

    if "bbox_px" in item and not valid_bbox(item["bbox_px"], width, height):
        issues.append(issue(page_number, item, "bbox_px", "bbox is outside image bounds or invalid"))
    return issues


def candidate_viewport_issues(page_number, item, viewport):
    if not isinstance(viewport, list) or len(viewport) != 4:
        return []
    issues = []
    if "line_start_px" in item or "line_end_px" in item:
        start_inside = point_in_bbox(item.get("line_start_px"), viewport)
        end_inside = point_in_bbox(item.get("line_end_px"), viewport)
        if not start_inside and not end_inside:
            issues.append(issue(page_number, item, "plan_viewport_bbox_px", "candidate line sits outside the plan viewport"))
    if "bbox_px" in item and not bbox_intersects(item.get("bbox_px"), viewport):
        issues.append(issue(page_number, item, "plan_viewport_bbox_px", "candidate bbox sits outside the plan viewport"))
    return issues


def valid_image_size(width, height):
    return number(width) and number(height) and width > 0 and height > 0


def has_coordinate_candidates(page):
    return any(page.get(key) for key in ["wall_candidates", "dimension_candidates", "room_label_candidates", "opening_candidates", "fixed_obstacle_candidates"])


def fixed_obstacle_issues(page_number, item, width, height):
    issues = []
    obstacle_id = item.get("obstacle_id") or item.get("candidate_id")
    match = OBSTACLE_ID.fullmatch(str(obstacle_id or ""))
    if not match or int(match.group("page")) != page_number:
        issues.append(issue(page_number, item, "fixed_obstacles.obstacle_id", "obstacle_id must use the page-local P{page}-OBS-{number} format"))
    if item.get("classification") not in {"unknown_fixed_obstacle", "column", "existing_structure"}:
        issues.append(issue(page_number, item, "fixed_obstacles.classification", "classification must be unknown_fixed_obstacle, column, or existing_structure"))
    if item.get("routing_constraint") != "do_not_route_through":
        issues.append(issue(page_number, item, "fixed_obstacles.routing_constraint", "routing_constraint must be do_not_route_through"))
    if item.get("confidence") not in {"low", "medium", "high"}:
        issues.append(issue(page_number, item, "fixed_obstacles.confidence", "confidence must be low, medium, or high"))
    if not item.get("visible_evidence"):
        issues.append(issue(page_number, item, "fixed_obstacles.visible_evidence", "visible evidence is required"))
    geometry_type = item.get("geometry_type")
    if geometry_type == "circle":
        centre = item.get("centre_px")
        radius = item.get("radius_px")
        if not valid_point(centre, width, height) or not number(radius) or radius <= 0:
            issues.append(issue(page_number, item, "fixed_obstacles.circle", "circle obstacles need an in-bounds centre_px and positive radius_px"))
        elif valid_image_size(width, height) and (centre[0] - radius < 0 or centre[0] + radius > width or centre[1] - radius < 0 or centre[1] + radius > height):
            issues.append(issue(page_number, item, "fixed_obstacles.circle", "circle obstacle extends outside image bounds"))
    elif geometry_type == "polygon":
        points = item.get("points_px", [])
        if not isinstance(points, list) or len(points) < 3 or any(not valid_point(point, width, height) for point in points):
            issues.append(issue(page_number, item, "fixed_obstacles.polygon", "polygon obstacles need at least three in-bounds points_px"))
    else:
        issues.append(issue(page_number, item, "fixed_obstacles.geometry_type", "geometry_type must be circle or polygon"))
    if not isinstance(item.get("related_dimensions_mm", []), list) or any(not number(value) for value in item.get("related_dimensions_mm", [])):
        issues.append(issue(page_number, item, "fixed_obstacles.related_dimensions_mm", "related_dimensions_mm must be a numeric list"))
    return issues


def image_width(page):
    return page.get("coordinate_system", {}).get("image_width")


def image_height(page):
    return page.get("coordinate_system", {}).get("image_height")


def valid_point(point, width, height):
    if not isinstance(point, list) or len(point) != 2:
        return False
    x, y = point
    if not number(x) or not number(y):
        return False
    if width and not 0 <= x <= width:
        return False
    if height and not 0 <= y <= height:
        return False
    return True


def plain_point(point):
    return (
        isinstance(point, list)
        and len(point) == 2
        and all(number(value) for value in point)
    )


def valid_bbox(bbox, width, height):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    return valid_point([x0, y0], width, height) and valid_point([x1, y1], width, height) and x1 > x0 and y1 > y0


def point_in_bbox(point, bbox):
    if not isinstance(point, list) or len(point) != 2:
        return False
    x, y = point
    left, top, right, bottom = bbox
    return left <= x <= right and top <= y <= bottom


def bbox_intersects(bbox, viewport):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    left, top, right, bottom = bbox
    v_left, v_top, v_right, v_bottom = viewport
    return left < v_right and right > v_left and top < v_bottom and bottom > v_top


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def line_length(start, end):
    return math.dist(start, end)


def roughly_parallel(a0, a1, b0, b1):
    angle_a = math.atan2(a1[1] - a0[1], a1[0] - a0[0])
    angle_b = math.atan2(b1[1] - b0[1], b1[0] - b0[0])
    diff = abs((angle_a - angle_b + math.pi / 2) % math.pi - math.pi / 2)
    return diff <= math.radians(12)


def issue(page_number, item, field, message):
    return {
        "page": page_number,
        "measurement_id": item.get("measurement_id", ""),
        "field": field,
        "message": message,
    }


def main():
    parser = argparse.ArgumentParser(description="Validate coordinate-based vision measurement output.")
    parser.add_argument("vision", help="Path to reviewed vision JSON, such as a saved ChatGPT response")
    parser.add_argument("--output", help="Output path; defaults to vision_validation.json beside the input")
    parser.add_argument("--candidate-review", help="Optional candidate_review.json for candidate ID validation")
    args = parser.parse_args()

    output = validate_vision_file(args.vision, args.output, args.candidate_review)
    print(f"Vision validation created: {output}")


if __name__ == "__main__":
    main()
