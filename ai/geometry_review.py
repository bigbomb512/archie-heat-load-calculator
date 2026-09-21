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
    room_geometry = [normalise_room_geometry(item, page_number, index + 1) for index, item in enumerate(
        page.get("room_geometry_candidates", page.get("room_boundaries", page.get("rooms", []))) or []
    )]
    room_geometry = [item for item in room_geometry if item]
    thermal_surfaces = [normalise_thermal_surface(item, page_number, index + 1) for index, item in enumerate(
        page.get("thermal_surface_candidates", page.get("surface_candidates", [])) or []
    )]
    thermal_surfaces = [item for item in thermal_surfaces if item]
    openings = [normalise_opening(item, page_number) for item in (page.get("opening_candidates", []) or [])]
    openings = [item for item in openings if item]
    room_labels = [normalise_room_label(item) for item in (
        page.get("room_label_candidates", page.get("room_labels", [])) or []
    )]
    room_labels = [item for item in room_labels if item]
    coordinate = {
        "page": page_number,
        "image": page.get("image", ""),
        "coordinate_system": coordinate_system,
        "plan_viewport_bbox_px": page.get("plan_viewport_bbox_px"),
        "plan_viewport_confidence": page.get("plan_viewport_confidence", "low"),
        "plan_viewport_uncertainties": page.get("plan_viewport_uncertainties", []),
        "wall_candidates": walls,
        "dimension_candidates": [dict(item, candidate_id=item["dimension_id"], source="vision_model") for item in dimensions],
        "room_label_candidates": room_labels,
        "room_geometry_candidates": room_geometry,
        "thermal_surface_candidates": thermal_surfaces,
        "opening_candidates": openings,
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
        "openings": openings,
        "dimension_candidates": dimensions,
        "dimension_wall_links": links,
        "room_geometry_candidates": room_geometry,
        "thermal_surface_candidates": thermal_surfaces,
        "room_label_candidates": room_labels,
        "rejected_or_noise_candidates": rejected,
        "unassigned_dimensions": page.get("unassigned_dimensions", []),
        "conflicts": page.get("conflicts", []),
    }
    return coordinate, layered


def normalise_room_label(item):
    """Normalize a room-label witness without treating prose as geometry."""
    if isinstance(item, str):
        return {"text": item, "status": "room_label"}
    if not isinstance(item, dict):
        return {}
    text = item.get("text") or item.get("label") or item.get("name")
    if not text:
        return {}
    return {
        "text": str(text),
        "status": item.get("status", "room_label"),
        "bbox": item.get("bbox") or item.get("bbox_px"),
        "level_name": item.get("level_name") or item.get("level") or "",
        "source": item.get("source", "vision_model"),
    }


def normalise_opening(item, page_number):
    """Preserve AI opening links as evidence, never as an authored surface."""
    if not isinstance(item, dict) or not item.get("tag"):
        return {}
    result = {key: deepcopy(item.get(key)) for key in (
        "opening_id", "tag", "drawing_number", "level_name", "owner_room_id", "owner_zone_id", "host_wall_id",
        "facade", "width_m", "height_m", "quantity", "opening_bbox_px", "source_crop",
        "source_excerpt", "window_properties",
        "elevation_refs", "section_refs", "schedule_refs", "competing_matches",
        "confidence", "confidence_score", "source_pages", "citations", "assumptions", "unresolved_fields",
    )}
    result["page"] = page_number
    result["status"] = "proposed"
    return result


def normalise_thermal_surface(item, page_number, ordinal):
    """Normalize an AI thermal-surface candidate without activating it."""
    if not isinstance(item, dict):
        return {}
    surface_id = item.get("surface_id") or item.get("thermal_surface_id") or f"P{page_number}-VSURFACE-{ordinal:03d}"
    return {
        "surface_id": str(surface_id),
        "physical_type": item.get("physical_type") or item.get("classification") or "unresolved",
        "thermal_role": item.get("thermal_role") or "unresolved",
        "boundary_condition": item.get("boundary_condition") or item.get("boundary") or "unresolved",
        "label": str(item.get("label") or item.get("surface_label") or ""),
        "level_name": item.get("level_name") or item.get("level") or item.get("floor") or "",
        "owner_room_id": item.get("owner_room_id") or item.get("room_id") or "",
        "owner_zone_id": item.get("owner_zone_id") or item.get("zone_id") or "",
        "adjacent_room_id": item.get("adjacent_room_id") or "",
        "adjacent_space_id": item.get("adjacent_space_id") or item.get("adjacent_space") or "",
        "boundary_points_px": item.get("boundary_points_px") or item.get("points_px") or [],
        "wall_ids": [str(value) for value in item.get("wall_ids", []) if value],
        "opening_ids": [str(value) for value in item.get("opening_ids", []) if value],
        "evidence_refs": item.get("evidence_refs") or item.get("source_pages") or [],
        "source_crop": item.get("source_crop") or item.get("crop") or "",
        "confidence": item.get("confidence", "low"),
        "confidence_score": item.get("confidence_score", item.get("confidence_numeric")),
        "independent_witnesses": item.get("independent_witnesses") or [],
        "assumptions": item.get("assumptions", []),
        "conflicts": item.get("conflicts", []),
        "construction_id": item.get("construction_id") or "",
        "u_value_w_m2k": item.get("u_value_w_m2k"),
        "boundary_temperature_c": item.get("boundary_temperature_c"),
        "source": item.get("source", "vision_model"),
    }


def normalise_room_geometry(item, page_number, ordinal):
    """Normalize the AI's room-boundary contract into the existing page schema."""
    if not isinstance(item, dict):
        return {}
    label = item.get("label") or item.get("room_label") or item.get("name")
    candidate_id = item.get("room_geometry_id") or item.get("room_id") or item.get("candidate_id")
    if not candidate_id:
        candidate_id = f"P{page_number}-VROOM-{ordinal:03d}"
    points = item.get("boundary_points_px") or item.get("polygon_points_px") or item.get("points_px") or item.get("boundary_points") or []
    return {
        "room_geometry_id": candidate_id,
        "label": str(label or ""),
        "level_name": item.get("level_name") or item.get("level") or item.get("floor") or "",
        "boundary_points_px": points,
        "wall_ids": [str(value) for value in (item.get("wall_ids") or item.get("ordered_wall_ids") or []) if value],
        "dimension_ids": [str(value) for value in (item.get("dimension_ids") or []) if value],
        "dimension_wall_links": item.get("dimension_wall_links") or [],
        "independent_witnesses": item.get("independent_witnesses") or item.get("witnesses") or [],
        "independent_witness_page": item.get("independent_witness_page"),
        "confidence": item.get("confidence", "low"),
        "confidence_score": item.get("confidence_score", item.get("confidence_numeric")),
        "source_pages": item.get("source_pages") or [page_number],
        "source_crop": item.get("source_crop") or item.get("crop") or "",
        "assumptions": item.get("assumptions", []),
        "conflicts": item.get("conflicts", []),
        "unresolved_fields": item.get("unresolved_fields", []),
        "scale_mm_per_px": item.get("scale_mm_per_px") or item.get("mm_per_px"),
        "area_m2": item.get("area_m2"),
        "source": item.get("source", "vision_model"),
    }


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
        "reason": item.get("reason") or item.get("match_basis") or item.get("basis", ""),
        "source_reference": item.get("source_reference") or item.get("source_crop") or item.get("source", "vision_model"),
        "should_use_for_calculation": item.get("should_use_for_calculation", False),
        "site_confirm_required": item.get("site_confirm_required", False),
        "source": "vision_model",
    }


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)
