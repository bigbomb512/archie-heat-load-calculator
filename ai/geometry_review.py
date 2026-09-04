#!/usr/bin/env python3

"""Translate the compact manual-vision response into existing internal evidence."""

from copy import deepcopy


PHYSICAL_WALL_CLASSES = {"existing_wall", "new_solid_wall", "new_partition"}
WALL_ROLES = {"outer_boundary_wall", "internal_partition"}


def normalise_vision(vision, candidate_review=None):
    """Keep `geometry_review` canonical while preserving downstream compatibility."""
    vision = deepcopy(vision)
    result = vision.get("result")
    if not isinstance(result, dict) and isinstance(vision.get("geometry_review"), dict):
        # prompt.md requests this compact top-level shape; accept it at the API boundary.
        result = {"geometry_review": vision.pop("geometry_review")}
        vision["result"] = result
    if not isinstance(result, dict):
        return vision
    review = result.get("geometry_review")
    if not isinstance(review, dict) or not isinstance(review.get("pages"), list):
        return vision

    coordinate_pages = []
    layered_pages = []
    for page in review["pages"]:
        coordinate, layered = normalise_page(page, groups_by_page(candidate_review or {}).get(page.get("page"), {}))
        coordinate_pages.append(coordinate)
        layered_pages.append(layered)
    result["coordinate_review"] = {"pages": coordinate_pages}
    result["layered_geometry"] = {"pages": layered_pages}
    return vision


def groups_by_page(candidate_review):
    return {
        page.get("page"): {
            group.get("wall_group_id"): group
            for group in page.get("wall_groups", [])
            if group.get("wall_group_id")
        }
        for page in candidate_review.get("pages", [])
    }


def normalise_page(page, known_groups):
    page_number = page.get("page")
    coordinate_system = page.get("coordinate_system", {})
    walls, outer, internal, fixtures, rejected = [], [], [], [], []
    wall_ids = {}

    for item in page.get("walls", page.get("wall_groups", [])):
        group_id = item.get("wall_id") or item.get("wall_group_id")
        source = known_groups.get(group_id, {})
        classification = item.get("classification")
        role = item.get("geometry_role")
        geometry = wall_geometry(item, source)
        if classification in PHYSICAL_WALL_CLASSES and role in WALL_ROLES and geometry:
            wall = {
                "wall_id": group_id,
                "candidate_id": group_id,
                "classification": role,
                "label": item.get("label", ""),
                "line_start_px": geometry["start"],
                "line_end_px": geometry["end"],
                "confidence": item.get("confidence", "low"),
                "source": item.get("source", "vector_anchored"),
                "source_candidate_ids": item.get("supporting_vector_ids", source.get("source_candidate_ids", [])),
                "geometry_type": item.get("geometry_type", "line"),
                "visible_evidence": item.get("visible_evidence", []),
            }
            if geometry.get("points"):
                wall["points_px"] = geometry["points"]
                wall["geometry_type"] = "curve_polyline"
            walls.append(wall)
            wall_ids[group_id] = wall
            (outer if role == "outer_boundary_wall" else internal).append(dict(wall))
        elif classification in {"fixture_joinery", "equipment"} and geometry:
            fixtures.append(
                {
                    "geometry_id": group_id,
                    "classification": "fixture_or_joinery",
                    "label": item.get("label", ""),
                    "line_start_px": geometry["start"],
                    "line_end_px": geometry["end"],
                    "confidence": item.get("confidence", "low"),
                    "source_candidate_ids": [group_id],
                }
            )
        elif group_id:
            rejected.append(
                {
                    "candidate_id": group_id,
                    "classification": classification or "noise",
                    "reason": "; ".join(item.get("visible_evidence", [])) or "not a physical wall target",
                }
            )

    dimensions = [normalise_dimension(item) for item in page.get("major_dimensions", [])]
    dimensions_by_id = {item["dimension_id"]: item for item in dimensions if item.get("dimension_id")}
    links = [normalise_link(item, wall_ids, dimensions_by_id) for item in page.get("dimension_wall_links", [])]
    links = [item for item in links if item]
    obstacles = [normalise_fixed_obstacle(item) for item in page.get("fixed_obstacles", [])]
    obstacles = [item for item in obstacles if item]
    coordinate = {
        "page": page_number,
        "image": page.get("image", ""),
        "coordinate_system": coordinate_system,
        "plan_viewport_bbox_px": page.get("plan_viewport_bbox_px"),
        "plan_viewport_confidence": page.get("plan_viewport_confidence", "low"),
        "plan_viewport_uncertainties": page.get("plan_viewport_uncertainties", []),
        "wall_candidates": walls,
        "dimension_candidates": [dict(item, candidate_id=item["dimension_id"], source="vision_model") for item in dimensions],
        "room_label_candidates": [],
        "opening_candidates": [],
        "fixed_obstacle_candidates": obstacles,
        "wall_dimensions": links,
    }
    layered = {
        "page": page_number,
        "image": page.get("image", ""),
        "page_role": page.get("page_role", "main_geometry_and_dimension_plan"),
        "plan_viewport_bbox_px": page.get("plan_viewport_bbox_px"),
        "geometry_readiness": page.get("geometry_readiness", "needs_more_review"),
        "outer_boundary_walls": outer,
        "internal_partitions": internal,
        "fixture_or_joinery_geometry": fixtures,
        "fixed_obstacles": obstacles,
        "columns": [],
        "openings": [],
        "dimension_candidates": dimensions,
        "dimension_wall_links": links,
        "rejected_or_noise_candidates": rejected,
        "unassigned_dimensions": page.get("unassigned_dimensions", []),
        "conflicts": page.get("conflicts", []),
    }
    return coordinate, layered


def normalise_fixed_obstacle(item):
    obstacle_id = item.get("obstacle_id")
    geometry_type = item.get("geometry_type")
    if not obstacle_id or geometry_type not in {"circle", "polygon"}:
        return {}
    obstacle = {
        "obstacle_id": obstacle_id,
        "candidate_id": obstacle_id,
        "classification": item.get("classification", "unknown_fixed_obstacle"),
        "geometry_type": geometry_type,
        "related_dimensions_mm": item.get("related_dimensions_mm", []),
        "routing_constraint": item.get("routing_constraint", "do_not_route_through"),
        "visible_evidence": item.get("visible_evidence", []),
        "confidence": item.get("confidence", "low"),
        "source": "vision_model",
    }
    if geometry_type == "circle":
        obstacle["centre_px"] = item.get("centre_px")
        obstacle["radius_px"] = item.get("radius_px")
    else:
        obstacle["points_px"] = item.get("points_px", [])
    return obstacle


def wall_geometry(item, source):
    points = item.get("points_px") or source.get("points_px") or []
    start = item.get("line_start_px") or source.get("line_start_px") or source.get("start_px")
    end = item.get("line_end_px") or source.get("line_end_px") or source.get("end_px")
    if len(points) >= 2:
        start, end = start or points[0], end or points[-1]
    if not valid_point(start) or not valid_point(end):
        return {}
    return {"start": start, "end": end, "points": points if len(points) >= 3 else []}


def normalise_dimension(item):
    dimension_id = item.get("dimension_id") or item.get("candidate_id")
    source = item.get("source") or ("screenshot_visible" if is_vision_dimension_id(dimension_id) else "pdf_extracted")
    return {
        "dimension_id": dimension_id,
        "source": source,
        "value_mm": item.get("value_mm"),
        "text_seen": item.get("text_seen", ""),
        "dimension_kind": item.get("dimension_kind", "unknown"),
        "source_annotation_id": item.get("source_annotation_id") or (None if source == "screenshot_visible" else dimension_id),
        "bbox_px": item.get("bbox_px"),
        "dimension_line_start_px": item.get("dimension_line_start_px"),
        "dimension_line_end_px": item.get("dimension_line_end_px"),
        "arrowhead_start_px": item.get("arrowhead_start_px"),
        "arrowhead_end_px": item.get("arrowhead_end_px"),
        "witness_lines_px": item.get("witness_lines_px", []),
        "measured_span_start_px": item.get("measured_span_start_px"),
        "measured_span_end_px": item.get("measured_span_end_px"),
        "confidence": item.get("confidence", "low"),
        "site_confirm_required": item.get("site_confirm_required", False),
        "visible_evidence": item.get("visible_evidence", []),
    }


def is_vision_dimension_id(dimension_id):
    parts = str(dimension_id or "").split("-")
    return len(parts) == 4 and parts[0].startswith("P") and parts[1:3] == ["VDIM", "VISION"] and parts[3].isdigit()


def normalise_link(item, walls, dimensions_by_id):
    target = item.get("target_wall_id") or item.get("target_wall_group_id")
    wall = walls.get(target)
    if not wall:
        return None
    dimension_id = item.get("dimension_id")
    dimension = dimensions_by_id.get(dimension_id)
    if not dimension:
        return None
    return {
        "measurement_id": item.get("measurement_id"),
        "dimension_id": dimension_id,
        "value_mm": dimension.get("value_mm"),
        "dimension_text_bbox_px": dimension.get("bbox_px"),
        "dimension_line_start_px": dimension.get("dimension_line_start_px"),
        "dimension_line_end_px": dimension.get("dimension_line_end_px"),
        "arrowhead_start_px": dimension.get("arrowhead_start_px"),
        "arrowhead_end_px": dimension.get("arrowhead_end_px"),
        "witness_lines_px": dimension.get("witness_lines_px", []),
        "measured_span_start_px": dimension.get("measured_span_start_px"),
        "measured_span_end_px": dimension.get("measured_span_end_px"),
        "target_wall_id": target,
        "target_wall_candidate_id": target,
        "target_wall_classification": wall["classification"],
        "target_wall_start_px": wall["line_start_px"],
        "target_wall_end_px": wall["line_end_px"],
        "confidence": item.get("confidence", "low"),
        "should_use_for_calculation": item.get("should_use_for_calculation", False),
        "site_confirm_required": item.get("site_confirm_required", False),
        "source": "vision_model",
    }


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)
