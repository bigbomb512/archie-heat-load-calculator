"""Multi-page geometry evidence resolution.

This module does not calculate loads or approve thermal inputs.  It turns the
page register and existing geometry artifacts into a deterministic evidence
graph.  Plans provide room geometry witnesses; elevations, schedules, service
pages and 3D views provide role-limited supporting evidence.
"""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math

from ai.geometry_review import normalise_vision
from ai.drawing_coverage import has_current_level_classification
from ai.thermal_surface_resolution import resolve_thermal_surfaces


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def normalise_label(value):
    """Normalise labels for matching without changing their display text."""
    return " ".join(str(value or "").casefold().split())


def stable_entity_id(source_fingerprint, page, drawing_number, level, label, kind, location=""):
    return "geometry_" + fingerprint([source_fingerprint, page, drawing_number, level, label, kind, location])[:24]


def _page_identity(page):
    identity = page.get("identity") or {}
    return identity.get("selected_drawing_number") or page.get("drawing_number", "")


def _evidence_fingerprint(ai_input, coverage, spatial_ocr, vector_geometry, dimension_matches,
                           geometry_confirmation, vision_response, building, reviewer_room_geometry=None):
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            rows = [canonical(item) for item in value]
            return sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return value

    return fingerprint({
        "ai_input": canonical(ai_input or {}), "coverage": canonical(coverage or {}),
        "spatial_ocr": canonical(spatial_ocr or {}), "vector_geometry": canonical(vector_geometry or {}),
        "dimension_matches": canonical(dimension_matches or {}),
        "geometry_confirmation": canonical(geometry_confirmation or {}),
        "vision_response": canonical(vision_response or {}), "building": canonical(building or {}),
        "reviewer_room_geometry": canonical(reviewer_room_geometry or {}),
    })


def stable_witness_id(source_fingerprint, page, drawing_number, label, kind, location=""):
    raw = [source_fingerprint, page, drawing_number, label, kind, location]
    return "witness_" + fingerprint(raw)[:20]


def valid_point(point):
    return isinstance(point, (list, tuple)) and len(point) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)


def polygon_area(points):
    """Return positive area for a closed polygon; otherwise return None."""
    if not isinstance(points, list) or len(points) < 4:
        return None
    if not all(valid_point(point) for point in points):
        return None
    if points[0] != points[-1]:
        return None
    area = sum(points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1] for i in range(len(points) - 1)) / 2.0
    return abs(area) if area > 0 else None


def segment_intersects(a, b, c, d):
    def orient(p, q, r):
        value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (value > 1e-9) - (value < -1e-9)

    def on_segment(p, q, r):
        return min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9 and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on_segment(a, c, b)) or (o2 == 0 and on_segment(a, d, b)) or (o3 == 0 and on_segment(c, a, d)) or (o4 == 0 and on_segment(c, b, d))


def polygon_is_simple(points):
    if not isinstance(points, list) or len(points) < 4 or points[0] != points[-1]:
        return False
    edges = list(zip(points, points[1:]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j <= i or j in {i - 1, i + 1} or (i == 0 and j == len(edges) - 1):
                continue
            if segment_intersects(a, b, c, d):
                return False
    return True


def _confidence_is_high(item):
    value = str(item.get("confidence", "")).casefold()
    score = item.get("confidence_score")
    return value == "high" or (isinstance(score, (int, float)) and math.isfinite(score) and score >= 0.80)


def _ai_geometry_pages(vision_response):
    """Return normalized AI room candidates from either supported response shape."""
    if not isinstance(vision_response, dict):
        return []
    normalized = normalise_vision(vision_response)
    result = normalized.get("result", {}) if isinstance(normalized, dict) else {}
    layered = result.get("layered_geometry", {}) if isinstance(result, dict) else {}
    rows = []
    for page in layered.get("pages", []) if isinstance(layered, dict) else []:
        for item in page.get("room_geometry_candidates", []) or []:
            row = deepcopy(item)
            row["page"] = page.get("page")
            row["page_role"] = page.get("page_role", "")
            row["walls"] = page.get("outer_boundary_walls", []) + page.get("internal_partitions", [])
            row["dimensions"] = page.get("dimension_candidates", [])
            row["dimension_wall_links"] = page.get("dimension_wall_links", [])
            rows.append(row)
    # Compatibility for the earlier structured extraction contract.
    if not rows:
        rows = [deepcopy(item) for item in result.get("auto_extraction", {}).get("entities", [])
                if isinstance(item, dict) and item.get("kind") == "room" and (
                    item.get("boundary_points_px") or item.get("wall_ids")
                )]
    return rows


def _ai_boundary_from_walls(item):
    """Build a closed ordered polygon only when the AI supplied ordered wall IDs."""
    coordinate_units = str(item.get("coordinate_units", "px")).casefold()
    points = item.get("boundary_points_mm" if coordinate_units == "mm" else "boundary_points_px") or []
    if points:
        return points
    by_id = {str(row.get("wall_id")): row for row in item.get("walls", []) if row.get("wall_id")}
    ordered = [by_id.get(str(value)) for value in item.get("wall_ids", [])]
    if not ordered or any(not row for row in ordered):
        return []
    path = []
    start_key, end_key = (("line_start_mm", "line_end_mm") if coordinate_units == "mm"
                          else ("line_start_px", "line_end_px"))
    for index, wall in enumerate(ordered):
        start, end = wall.get(start_key), wall.get(end_key)
        if not (valid_point(start) and valid_point(end)):
            return []
        if not path:
            path = [list(start), list(end)]
            continue
        previous = path[-1]
        if _distance(previous, start) <= 2.0:
            path.append(list(end))
        elif _distance(previous, end) <= 2.0:
            path.append(list(start))
        else:
            return []
    if path and _distance(path[-1], path[0]) <= 2.0:
        path[-1] = list(path[0])
    return path


def _ai_scale_from_links(item):
    """Derive scale only from an explicitly linked measured span."""
    dimensions = {str(row.get("dimension_id")): row for row in item.get("dimensions", []) if row.get("dimension_id")}
    factors = []
    for link in item.get("dimension_wall_links", []) or []:
        dimension = dimensions.get(str(link.get("dimension_id")), {})
        value = link.get("value_mm", dimension.get("value_mm"))
        start = link.get("measured_span_start_px") or dimension.get("measured_span_start_px")
        end = link.get("measured_span_end_px") or dimension.get("measured_span_end_px")
        if isinstance(value, (int, float)) and value > 0 and valid_point(start) and valid_point(end):
            length = _distance(start, end)
            if length > 1e-9:
                factors.append(float(value) / length)
    if not factors:
        return None
    reference = sum(factors) / len(factors)
    if any(abs(value - reference) / reference > 0.05 for value in factors):
        return None
    return reference


def _wall_endpoints(wall):
    if not isinstance(wall, dict):
        return None, None
    points = wall.get("points_px") or []
    start = wall.get("line_start_px") or (points[0] if len(points) >= 2 else None)
    end = wall.get("line_end_px") or (points[-1] if len(points) >= 2 else None)
    return start, end


def _boundary_matches_ordered_walls(points, wall_ids, walls, tolerance=2.0, coordinate_units="px"):
    """Verify an AI polygon describes the same ordered wall loop it cites.

    This is a structural check only: it never searches for a nearby wall or
    snaps an endpoint.  A provider may submit either a polygon or wall order,
    but if it supplies both, they must agree.
    """
    if not wall_ids:
        return True
    if len(wall_ids) != len(set(str(value) for value in wall_ids)) or len(points) - 1 != len(wall_ids):
        return False
    for index, wall_id in enumerate(wall_ids):
        wall = walls.get(str(wall_id))
        if coordinate_units == "mm" and isinstance(wall, dict):
            start, end = wall.get("line_start_mm"), wall.get("line_end_mm")
        else:
            start, end = _wall_endpoints(wall)
        if not (valid_point(start) and valid_point(end)):
            return False
        left, right = points[index], points[index + 1]
        forward = _distance(left, start) <= tolerance and _distance(right, end) <= tolerance
        reverse = _distance(left, end) <= tolerance and _distance(right, start) <= tolerance
        if not (forward or reverse):
            return False
    return True


def _validate_ai_room(item, resolution_mode, page_by_number, room_entities):
    """Validate AI geometry without attempting to rediscover it."""
    reasons = list(item.get("unresolved_fields", []) or [])
    coordinate_units = str(item.get("coordinate_units", "px")).casefold()
    if coordinate_units not in {"px", "mm"}:
        reasons.append("geometry_coordinate_units")
        coordinate_units = "px"
    points = _ai_boundary_from_walls(item)
    if not polygon_is_simple(points):
        reasons.append("validated_boundary_shape")
    area = polygon_area(points)
    if not area or area <= 0:
        reasons.append("positive_area")
    label = normalise_label(item.get("label") or item.get("room_label") or item.get("name"))
    if not label:
        reasons.append("room_label")
    level = item.get("level_name") or item.get("level") or item.get("floor") or ""
    if not level:
        reasons.append("floor_identity")
    page = page_by_number.get(item.get("page"), {})
    if page.get("proposed_role") in {"3d_render", "3d_reference", "legend_or_general_notes", "reference"}:
        reasons.append("primary_geometry_page")
    walls = {str(row.get("wall_id")): row for row in item.get("walls", []) if row.get("wall_id")}
    dimensions = {str(row.get("dimension_id")): row for row in item.get("dimensions", []) if row.get("dimension_id")}
    for wall_id in item.get("wall_ids", []) or []:
        if str(wall_id) not in walls:
            reasons.append("unknown_wall_reference")
    if item.get("wall_ids") and not _boundary_matches_ordered_walls(points, item.get("wall_ids", []), walls, coordinate_units=coordinate_units):
        reasons.append("boundary_wall_alignment")
    for dimension_id in item.get("dimension_ids", []) or []:
        dimension = dimensions.get(str(dimension_id))
        if not dimension or not isinstance(dimension.get("value_mm"), (int, float)) or dimension.get("value_mm") <= 0:
            reasons.append("invalid_dimension_reference")
    dimension_links = item.get("dimension_wall_links", []) or []
    for link in dimension_links:
        if link.get("target_wall_id") not in walls or link.get("dimension_id") not in dimensions:
            reasons.append("invalid_dimension_wall_link")
        value = link.get("value_mm", dimensions.get(link.get("dimension_id"), {}).get("value_mm"))
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            reasons.append("invalid_dimension_value")
    if item.get("dimension_ids") and not {str(link.get("dimension_id")) for link in dimension_links}.issuperset({str(value) for value in item.get("dimension_ids", [])}):
        reasons.append("dimension_wall_binding")
    for link in dimension_links:
        if not link.get("reason") and not link.get("match_basis") and not link.get("basis"):
            reasons.append("dimension_wall_link_reason")
        # The containing page and dimension ID are an acceptable source
        # reference when the AI does not provide a separate crop identifier.
        if not (link.get("source") or link.get("source_reference") or item.get("source_crop") or item.get("page") is not None):
            reasons.append("dimension_wall_link_source")
    if item.get("conflicts"):
        reasons.append("geometry_conflict")
    if not item.get("source_pages"):
        reasons.append("source_page")
    if str(item.get("confidence", "")).casefold() not in {"low", "medium", "high"} and not isinstance(item.get("confidence_score"), (int, float)):
        reasons.append("confidence")
    if coordinate_units == "mm":
        wall_ids = [str(value) for value in item.get("wall_ids", [])]
        if not wall_ids or len(wall_ids) != len(points) - 1:
            reasons.append("dimensioned_boundary_edge_coverage")
        linked_walls = {str(link.get("target_wall_id")) for link in dimension_links
                        if isinstance(link, dict) and link.get("dimension_id") and
                        (link.get("reason") or link.get("match_basis") or link.get("basis"))}
        if not set(wall_ids).issubset(linked_walls):
            reasons.append("dimensioned_boundary_edge_coverage")
        for link in dimension_links:
            wall = walls.get(str(link.get("target_wall_id")))
            value = link.get("value_mm", dimensions.get(str(link.get("dimension_id")), {}).get("value_mm"))
            start = wall.get("line_start_mm") if isinstance(wall, dict) else None
            end = wall.get("line_end_mm") if isinstance(wall, dict) else None
            if not valid_point(start) or not valid_point(end) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                if link.get("target_wall_id"):
                    reasons.append("dimension_chain_geometry_mismatch")
                continue
            if abs(_distance(start, end) - float(value)) / float(value) > 0.02:
                reasons.append("dimension_chain_geometry_mismatch")
    else:
        # Pixel geometry requires an explicit eligible calibration. Never use
        # visual proportion alone as a measurement.
        scale = item.get("scale_mm_per_px")
        if scale is not None and (not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0):
            reasons.append("scale_calibration")
        if scale is None:
            reasons.append("scale_calibration")
        if scale is not None:
            for link in dimension_links:
                wall = walls.get(str(link.get("target_wall_id")))
                value = link.get("value_mm", dimensions.get(str(link.get("dimension_id")), {}).get("value_mm"))
                start, end = _wall_endpoints(wall)
                expected = _distance(start, end) * scale if valid_point(start) and valid_point(end) else 0
                if expected and isinstance(value, (int, float)) and value > 0 and abs(expected - value) / value > 0.20:
                    reasons.append("dimension_consistency")
    if label and level:
        matches = [entity for entity in room_entities if normalise_label(entity.get("label")) == label and normalise_label(entity.get("level_candidate")) == normalise_label(level)]
        if len(matches) > 1:
            reasons.append("room_identity_conflict")
        elif not matches:
            reasons.append("room_identity")
        else:
            label_bbox = item.get("room_label_bbox") or item.get("label_bbox") or item.get("label_bounding_box")
            label_point = _bbox_center(label_bbox)
            if label_point and not _point_in_polygon(label_point, points):
                reasons.append("room_label_spatial_link")
            if not label_point:
                room_value = matches[0].get("value") if isinstance(matches[0].get("value"), dict) else {}
                evidence_pages = {row.get("page") for row in room_value.get("evidence", []) if isinstance(row, dict)}
                if item.get("page") not in evidence_pages and item.get("page") not in (item.get("source_pages") or []):
                    reasons.append("room_label_spatial_link")
    independent = item.get("independent_witnesses") or ([] if item.get("independent_witness_page") is None else [{"page": item.get("independent_witness_page")}])
    # A second extraction of the same page is not an independent witness.
    # 3D/reference pages may cross-check a relationship but cannot prove area.
    filtered_independent = []
    seen_independent = set()
    primary_page = item.get("page")
    for witness in independent:
        witness = witness if isinstance(witness, dict) else {"page": witness}
        page = witness.get("page")
        source = witness.get("source_fingerprint") or witness.get("source_artifact") or ""
        role = str(witness.get("page_role") or witness.get("role") or "").casefold()
        if role in {"3d_render", "3d_reference", "reference", "legend_or_general_notes"}:
            continue
        if page == primary_page and not source:
            continue
        key = (page, source, witness.get("reference") or witness.get("kind") or "")
        if key in seen_independent:
            continue
        seen_independent.add(key)
        filtered_independent.append(witness)
    independent = filtered_independent
    if resolution_mode == "engineering_reviewed" and not independent:
        reasons.append("independent_geometry_witness")
    high = _confidence_is_high(item)
    if resolution_mode == "preliminary_ai_estimate" and not high:
        reasons.append("high_confidence_ai_required")
    # A closed, high-confidence polygon on an explicitly scaled main viewport
    # is useful as a draft geometry estimate even when vector-loop extraction
    # or an independent dimension witness is unavailable. Keep those gaps in
    # the artifact as review warnings; they are not evidence-backed measured
    # dimensions and can never activate the reviewed path.
    calibration = item.get("calibration") if isinstance(item.get("calibration"), dict) else {}
    confirmed_viewport_scale = coordinate_units == "px" and bool(
        calibration.get("confirmed_main_viewport_scale")
        and calibration.get("canonical_image_width_px")
        and calibration.get("pdf_physical_page_width_mm")
    )
    provisional_warnings = set()
    if (resolution_mode == "preliminary_ai_estimate" and high and confirmed_viewport_scale
            and polygon_is_simple(points) and area and area > 0 and not item.get("conflicts")):
        provisional_warnings = {"precise_vector_boundary", "independent_dimension_cross_check",
                                "exact_wall_vector_provenance", "resolver_area"}
        # An unassigned floor is permitted only as an explicit draft identity
        # when the source drawing set has not established a named level.
        if str(level).strip().casefold() in {"unassigned level", "", "unassigned"}:
            provisional_warnings.update({"level", "verified_level"})
    review_warnings = sorted(set(reasons).intersection(provisional_warnings))
    if "resolver_area" in review_warnings:
        review_warnings[review_warnings.index("resolver_area")] = "area_recomputed_by_resolver"
        review_warnings = sorted(set(review_warnings))
    reasons = sorted(set(reasons).difference(provisional_warnings))
    return points, area, reasons, independent, review_warnings, coordinate_units


def geometry_status_for_room(room):
    geometry = room.get("geometry") or room.get("geometry_reference")
    if room.get("geometry_status") == "geometry_confirmed":
        return "geometry_confirmed"
    if isinstance(geometry, dict) and polygon_area(geometry.get("points") or geometry.get("polygon")) and geometry.get("scale_witnesses", 0) >= 2:
        return "geometry_proposed"
    return room.get("geometry_status") or "label_detected"


def _distance(left, right):
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _bbox_center(raw):
    """Return a label centre from the common OCR/vector rectangle shapes."""
    if isinstance(raw, dict):
        raw = raw.get("bbox") or raw.get("bbox_px") or raw.get("coordinates")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        x1, y1, x2, y2 = (float(raw[index]) for index in range(4))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        return None
    return [(x1 + x2) / 2.0, (y1 + y2) / 2.0]


def _point_in_polygon(point, polygon):
    """Strict enough for label-to-room matching; a boundary point is accepted."""
    if not valid_point(point) or not polygon_is_simple(polygon):
        return False
    x, y = point
    inside = False
    for left, right in zip(polygon, polygon[1:]):
        if abs((right[0] - left[0]) * (y - left[1]) - (right[1] - left[1]) * (x - left[0])) < 1e-7:
            if min(left[0], right[0]) - 1e-7 <= x <= max(left[0], right[0]) + 1e-7 and min(left[1], right[1]) - 1e-7 <= y <= max(left[1], right[1]) + 1e-7:
                return True
        if (left[1] > y) != (right[1] > y):
            crossing = (right[0] - left[0]) * (y - left[1]) / (right[1] - left[1]) + left[0]
            if x < crossing:
                inside = not inside
    return inside


def _wall_line_decision(row):
    start, end = row.get("start_px"), row.get("end_px")
    if not (valid_point(start) and valid_point(end)):
        return "invalid_or_missing_endpoints"
    if _distance(start, end) <= 1e-6:
        return "zero_length"
    hint = str(row.get("candidate_role_hint", "")).casefold()
    identifier = str(row.get("candidate_id", "")).casefold()
    if any(value in hint or value in identifier for value in ("dimension", "fixture", "joinery", "annotation")):
        return "dimension_fixture_joinery_or_annotation"
    if hint and not any(value in hint for value in ("wall", "partition", "boundary", "physical")):
        return "role_hint_not_wall_like"
    if not row.get("candidate_id"):
        return "missing_stable_candidate_id"
    return "accepted_wall_candidate"


def _wall_lines(page, diagnostics=None):
    """Return the existing plausible-wall subset and optionally explain every decision."""
    lines = []
    decisions = []
    for row in page.get("line_candidates", []) or []:
        reason = _wall_line_decision(row)
        decisions.append({"candidate_id": row.get("candidate_id", ""), "accepted": reason == "accepted_wall_candidate", "reason": reason})
        if reason != "accepted_wall_candidate":
            continue
        lines.append({"wall_id": row.get("candidate_id", ""), "start": list(row["start_px"]), "end": list(row["end_px"]), "raw": row})
    if diagnostics is not None:
        diagnostics.extend(decisions)
    return lines


def _closed_wall_loops(lines, tolerance=1.0, diagnostics=None):
    """Find unambiguous closed wall components by shared vector endpoints.

    This intentionally does not bridge visual gaps. A branch, open chain, or
    uncertain snap is evidence only, not a room boundary.
    """
    nodes = []
    indexed = []

    def node_for(point):
        for index, existing in enumerate(nodes):
            if _distance(existing, point) <= tolerance:
                return index
        nodes.append(list(point))
        return len(nodes) - 1

    adjacency = defaultdict(list)
    for line in lines:
        start, end = node_for(line["start"]), node_for(line["end"])
        if start == end:
            continue
        edge = {**line, "start_node": start, "end_node": end}
        indexed.append(edge)
        adjacency[start].append(edge)
        adjacency[end].append(edge)

    loops, visited = [], set()
    for edge in indexed:
        edge_id = edge["wall_id"]
        if edge_id in visited:
            continue
        component_edges, stack, component_nodes = [], [edge], set()
        while stack:
            current = stack.pop()
            current_id = current["wall_id"]
            if current_id in {item["wall_id"] for item in component_edges}:
                continue
            component_edges.append(current)
            component_nodes.update((current["start_node"], current["end_node"]))
            for node in (current["start_node"], current["end_node"]):
                stack.extend(item for item in adjacency[node] if item["wall_id"] not in {row["wall_id"] for row in component_edges})
        visited.update(item["wall_id"] for item in component_edges)
        # A simple loop has exactly two incident wall segments at every node.
        degrees = {str(node): len(adjacency[node]) for node in component_nodes}
        if len(component_edges) < 3 or any(degree != 2 for degree in degrees.values()):
            if diagnostics is not None:
                diagnostics.append({"wall_ids": sorted(item["wall_id"] for item in component_edges),
                                   "node_degrees": sorted(degrees.values()),
                                   "reason": "component_has_branch_or_open_node" if any(degree != 2 for degree in degrees.values()) else "component_has_fewer_than_three_segments"})
            continue
        path, previous, node = [], None, next(iter(component_nodes))
        while True:
            path.append(nodes[node])
            candidates = [item for item in adjacency[node] if item is not previous]
            if not candidates:
                break
            current = candidates[0]
            next_node = current["end_node"] if current["start_node"] == node else current["start_node"]
            previous, node = current, next_node
            if node == next(iter(component_nodes)):
                path.append(nodes[node])
                break
            if len(path) > len(component_edges) + 1:
                break
        if len(path) == len(component_edges) + 1 and path[0] == path[-1] and polygon_is_simple(path):
            loops.append({"points_px": path, "wall_ids": sorted(item["wall_id"] for item in component_edges)})
        elif diagnostics is not None:
            diagnostics.append({"wall_ids": sorted(item["wall_id"] for item in component_edges),
                                "node_degrees": sorted(degrees.values()), "reason": "closed_walk_invalid_or_self_intersecting"})
    return loops


def _label_points(spatial_ocr, room, page_number):
    wanted = normalise_label(room.get("name"))
    points = []
    for page in (spatial_ocr or {}).get("pages", []):
        if page.get("page") != page_number:
            continue
        for item in page.get("room_label_candidates", []) or []:
            if not isinstance(item, dict) or normalise_label(item.get("text")) != wanted:
                continue
            status = str(item.get("status", "room_label")).casefold()
            if status and status not in {"room_label", "room", "candidate", "confirmed"}:
                continue
            point = _bbox_center(item)
            if point:
                points.append({"point": point, "raw": item})
    for evidence in room.get("evidence", []) or []:
        if not isinstance(evidence, dict) or evidence.get("page") != page_number:
            continue
        point = _bbox_center(evidence)
        if point:
            points.append({"point": point, "raw": evidence})
    return points


def _label_diagnostic_candidates(spatial_ocr, page_number):
    accepted, rejected = [], []
    for page in (spatial_ocr or {}).get("pages", []):
        if page.get("page") != page_number:
            continue
        for item in page.get("room_label_candidates", []) or []:
            if not isinstance(item, dict):
                rejected.append({"text": "", "reason": "candidate_not_object"})
                continue
            status = str(item.get("status", "room_label")).casefold()
            point = _bbox_center(item)
            row = {"text": item.get("text", ""), "status": status}
            if status and status not in {"room_label", "room", "candidate", "confirmed"}:
                rejected.append({**row, "reason": "status_filtered"})
            elif point is None:
                rejected.append({**row, "reason": "missing_or_invalid_label_bbox"})
            else:
                accepted.append({**row, "point": point})
    return {"accepted": accepted, "rejected": rejected}


def _page_dimension_links(dimension_matches, page_number):
    rows = []
    for page in (dimension_matches or {}).get("pages", []):
        if page.get("page") == page_number:
            rows.extend(deepcopy(page.get("dimension_wall_links", []) or []))
    return rows


def _scale_from_dimension_links(page_number, lines, dimension_matches):
    """Calibrate pixels to millimetres from explicit dimension-to-wall links."""
    walls = {row["wall_id"]: row for row in lines}
    dimensions = {}
    links = []
    for page in (dimension_matches or {}).get("pages", []):
        if page.get("page") != page_number:
            continue
        for row in page.get("dimension_span_candidates", []) or []:
            identifier = row.get("dimension_candidate_id") or row.get("dimension_id")
            if identifier and isinstance(row.get("value_mm"), (int, float)) and row["value_mm"] > 0:
                dimensions[identifier] = row["value_mm"]
        for row in page.get("dimension_wall_links", []) or []:
            dimension_id = row.get("dimension_candidate_id") or row.get("dimension_id")
            wall_id = row.get("target_wall_id") or row.get("wall_id")
            value = row.get("value_mm", dimensions.get(dimension_id))
            wall = walls.get(wall_id)
            if not wall or not isinstance(value, (int, float)) or value <= 0:
                continue
            length_px = _distance(wall["start"], wall["end"])
            if length_px <= 1e-6:
                continue
            links.append({"dimension_id": dimension_id, "wall_id": wall_id, "value_mm": float(value), "length_px": length_px, "mm_per_px": float(value) / length_px})
    if not links:
        return None, []
    factors = [row["mm_per_px"] for row in links]
    reference = sum(factors) / len(factors)
    # Multiple printed dimensions must agree. One valid directly-linked
    # dimension is sufficient for calibration; a disagreement blocks it.
    if any(abs(value - reference) / reference > 0.02 for value in factors):
        return None, links
    return reference, links


def build_geometry_resolution(ai_input, coverage=None, building=None, spatial_ocr=None, vector_geometry=None,
                              dimension_matches=None, geometry_confirmation=None, vision_response=None,
                              resolution_mode=None, room_proposals=None, reviewer_room_geometry=None):
    """Create page capabilities, witnesses, relationships and review issues."""
    coverage = coverage or {}
    building = building or {}
    resolution_mode = resolution_mode or (ai_input or {}).get("geometry_resolution_mode", "engineering_reviewed")
    if resolution_mode not in {"preliminary_ai_estimate", "engineering_reviewed"}:
        resolution_mode = "engineering_reviewed"
    if vision_response:
        # Keep the original response available to callers, while ensuring the
        # geometry resolver sees the same normalized page contract as the
        # confirmation and validation services.
        vision_response = normalise_vision(vision_response)
    level_method_current = has_current_level_classification(coverage)
    pages = list(coverage.get("page_roles", []))
    if not pages:
        pages = [
            {**row, "proposed_role": row.get("proposed_role") or row.get("sheet_classification") or row.get("detected_type", "")}
            for row in (ai_input.get("drawing_set", {}).get("pages", []) if isinstance(ai_input, dict) else [])
        ]
    if not level_method_current:
        pages = [{**row, "level_name": "", "level_candidates": [], "level_status": "missing"} for row in pages]
    source_fp = coverage.get("source_fingerprint", fingerprint(ai_input))
    evidence_fp = _evidence_fingerprint(ai_input, coverage, spatial_ocr, vector_geometry,
                                         dimension_matches, geometry_confirmation, vision_response, building,
                                         reviewer_room_geometry)
    if room_proposals:
        evidence_fp = fingerprint({"base": evidence_fp, "room_proposals": room_proposals})
    page_by_number = {row.get("page"): row for row in pages}
    witnesses = []
    relationships = []
    conflicts = []
    review_items = []
    entities = []

    def add_entity(page_number, label, kind, value, status, method, confidence="unknown",
                   location="", unresolved=None, witness_ids=None, level=""):
        page = page_by_number.get(page_number, {})
        entity = {
            "entity_id": stable_entity_id(evidence_fp, page_number, _page_identity(page), level,
                                           label, kind, location),
            "kind": kind, "label": label, "value": value,
            "geometry_status": status, "source": {
                "page": page_number, "drawing_number": _page_identity(page),
                "kind": "architect_pdf" if page_number is not None else "derived",
            },
            "extraction_method": method, "confidence": confidence,
            "level_candidate": level, "witness_ids": sorted(set(str(item) for item in (witness_ids or []) if item)),
            "unresolved_fields": sorted(set(unresolved or [])),
        }
        entities.append(entity)
        return entity

    def add_witness(page_number, label, kind, evidence, method, confidence="unknown", location="", role=None):
        page = page_by_number.get(page_number, {})
        witness = {
            "witness_id": stable_witness_id(source_fp, page_number, page.get("drawing_number", ""), label, kind, location),
            "page": page_number,
            "drawing_number": page.get("drawing_number", ""),
            "role": role or page.get("proposed_role", ""),
            "kind": kind,
            "label": label,
            "evidence": evidence,
            "extraction_method": method,
            "confidence": confidence,
            "location": location,
            "independent_group": page.get("page_group", ""),
        }
        if witness["witness_id"] not in {row["witness_id"] for row in witnesses}:
            witnesses.append(witness)
        return witness

    # Every page is a witness candidate, even when it cannot provide primary
    # geometry.  This prevents useful 3D/elevation/schedule evidence being
    # discarded by downstream consumers.
    for page in pages:
        caps = page.get("capabilities", [])
        add_witness(page.get("page"), page.get("title", ""), "page_capability", caps,
                    "page_role_classification", page.get("confidence", "unknown"), role=page.get("proposed_role", ""))

    # Preserve the complete page identity/capability contract in the geometry
    # artifact. This is metadata, not calculator eligibility.
    page_register = []
    for page in pages:
        identity_status = (page.get("identity") or {}).get("status", page.get("drawing_number_status", "missing"))
        state_chain = ["page_discovered"]
        if identity_status == "confirmed":
            state_chain.append("page_identity_resolved")
        elif identity_status == "ambiguous":
            state_chain.append("page_identity_conflict")
        if page.get("selection") in {"primary_context", "supporting_context", "cross_check_context"}:
            state_chain.append("page_selected")
        if page.get("text_available") or page.get("ocr_available") or page.get("vector_available"):
            state_chain.append("evidence_extracted")
        if page.get("proposed_role") in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan"}:
            state_chain.append("geometry_proposed")
        page_register.append({
            "page": page.get("page"),
            "drawing_number": _page_identity(page),
            "drawing_number_candidates": (page.get("identity") or {}).get("drawing_number_candidates", []),
            "title": page.get("title", ""),
            "title_candidates": (page.get("identity") or {}).get("title_candidates", []),
            "identity_status": identity_status,
            "resolution_state": state_chain[-1],
            "state_chain": state_chain,
            "proposed_role": page.get("proposed_role", ""),
            "capabilities": page.get("capabilities", []),
            "capability_map": page.get("capability_map", {}),
            "relevance": page.get("relevance", {}),
            "selection": page.get("selection", "reference_only"),
            "level_candidate": page.get("level_name", ""),
            "level_candidates": page.get("level_candidates", []),
            "level_status": page.get("level_status", "missing"),
            "scale_candidates": page.get("scale_candidates", []),
            "main_scale": page.get("main_scale", ""),
            "scale_status": page.get("scale_status", "missing"),
            "text_available": bool(page.get("text_available", True)),
            "ocr_available": bool(page.get("ocr_available", False)),
            "vector_available": bool(page.get("vector_available", False)),
            "source_fingerprint": source_fp,
        })

    room_entities = []
    room_entities_by_source_id = {}
    for level in building.get("levels", []):
        evidence = level.get("evidence") or []
        page = next((item.get("page") for item in evidence if isinstance(item, dict) and item.get("page") is not None), None)
        entity = add_entity(page, level.get("name", ""), "floor", deepcopy(level),
                            "geometry_proposed" if level.get("name") else "geometry_review_required",
                            level.get("extraction_method", "structured_pdf"), level.get("confidence", "unknown"),
                            witness_ids=[item.get("page") for item in evidence if isinstance(item, dict)],
                            unresolved=[] if evidence else ["floor_identity"])

    for room in building.get("spaces", []):
        evidence = room.get("evidence", [])
        level = room.get("level_name", "")
        room_status = geometry_status_for_room(room)
        room_entity = add_entity(
            next((item.get("page") for item in evidence if isinstance(item, dict) and item.get("page") is not None), None),
            room.get("name", ""), "room", deepcopy(room), room_status,
            room.get("extraction_method", "structured_pdf"), room.get("confidence", "unknown"),
            witness_ids=[item.get("page") for item in evidence if isinstance(item, dict)],
            unresolved=room.get("unresolved_fields", []), level=level,
        )
        room_entities.append(room_entity)
        room_entities_by_source_id[room.get("id", room_entity["entity_id"])] = room_entity
        for item in evidence:
            page = item.get("page")
            witness = add_witness(page, room.get("name", ""), "room", item.get("excerpt", ""), room.get("extraction_method", "structured_pdf"), room.get("confidence", "unknown"), item.get("bbox_px", ""), page_by_number.get(page, {}).get("proposed_role"))
            relationships.append({"relationship_id": "room_witness_" + fingerprint([room.get("id"), witness["witness_id"]])[:16], "kind": "room_to_page_witness", "entity_id": room.get("id", ""), "witness_ids": [witness["witness_id"]], "status": "proposed"})
        status = room_status
        # An explicit, uniquely allocated area is eligible as an evidence
        # value even when the room boundary itself is not confirmed. It is
        # never inferred from another room or from visual proportions.
        raw_area = room.get("area_m2", room.get("area"))
        try:
            numeric_area = float(raw_area)
        except (TypeError, ValueError):
            numeric_area = 0
        if numeric_area > 0 and evidence:
            add_entity(
                room_entity["source"]["page"], room.get("name", ""), "area",
                {"area_m2": numeric_area, "room_entity_id": room_entity["entity_id"]},
                "geometry_confirmed", "explicit_room_area", room.get("confidence", "unknown"),
                witness_ids=[room_entity["entity_id"]], level=level,
            )
        if status != "geometry_confirmed":
            review_items.append({
                "item_id": "geometry_issue_" + fingerprint([room.get("id"), "geometry"])[:16],
                "affected_id": room.get("id", ""), "status": "blocked", "field": "geometry",
                "source_artifact": "building_evidence.json", "page": (evidence[0].get("page") if evidence else None),
                "reason": "Room identity is present, but no closed, calibrated, independently witnessed boundary is available.",
                "remediation": "Link the room label to a closed plan boundary and a second independent witness; otherwise keep the room excluded.",
            })

    # Local placeholder-AI room inference may provide a source-linked label
    # before the building-evidence extractor has produced formal spaces. Keep
    # those candidates in the authoritative geometry artifact as provisional
    # entities; do not invent a polygon or promote an area without proof.
    known_room_keys = {(normalise_label(row.get("label")), normalise_label(row.get("level_candidate"))) for row in room_entities}
    for proposal in room_proposals or []:
        if not isinstance(proposal, dict):
            continue
        label = normalise_label(proposal.get("label") or proposal.get("room_label"))
        level = normalise_label(proposal.get("level_name") or proposal.get("level")) or "unassigned level"
        if not label or (label, level) in known_room_keys:
            continue
        page = (proposal.get("source_pages") or [None])[0]
        evidence = proposal.get("evidence") or ([{"page": page, "excerpt": proposal.get("label", "")}] if page is not None else [])
        entity = add_entity(page, proposal.get("label", ""), "room", deepcopy(proposal), "geometry_review_required",
                            "local_placeholder_ai_room_inference", proposal.get("confidence_band", "medium"),
                            witness_ids=[item.get("page") for item in evidence if isinstance(item, dict)],
                            unresolved=proposal.get("unresolved_fields") or ["closed_boundary", "room_area"], level=proposal.get("level_name", "Unassigned level"))
        room_entities.append(entity)
        known_room_keys.add((label, level))
        for item in evidence:
            if not isinstance(item, dict) or item.get("page") is None:
                continue
            witness = add_witness(item.get("page"), proposal.get("label", ""), "room", item.get("excerpt", ""),
                                  "local_placeholder_ai_room_inference", proposal.get("confidence_band", "medium"),
                                  role=page_by_number.get(item.get("page"), {}).get("proposed_role", ""))
            relationships.append({"relationship_id": "room_inference_witness_" + fingerprint([entity["entity_id"], witness["witness_id"]])[:16],
                                  "kind": "room_to_page_witness", "entity_ids": [entity["entity_id"]],
                                  "witness_ids": [witness["witness_id"]], "status": "proposed"})
        review_items.append({"item_id": "geometry_issue_" + fingerprint([entity["entity_id"], "inference"])[:16],
                             "affected_id": proposal.get("room_id", entity["entity_id"]), "status": "blocked",
                             "field": "geometry", "source_artifact": "room_inference_job", "page": page,
                             "reason": "Room-use evidence was found, but a closed calibrated boundary is not yet proven.",
                             "remediation": "Open the geometry editor and link the room label to a closed plan boundary and area witness.",
                             })

    # Level-aware duplicate detection. Same labels on different levels are
    # distinct; competing boundaries on the same level are conflicts.
    room_groups = defaultdict(list)
    for entity in room_entities:
        room_groups[(normalise_label(entity.get("label")), normalise_label(entity.get("level_candidate")))].append(entity)
    for (label, level), matches in room_groups.items():
        if label and len(matches) > 1:
            conflict_id = "geometry_conflict_" + fingerprint(["room", label, level, [row["entity_id"] for row in matches]])[:16]
            conflicts.append({
                "conflict_id": conflict_id, "kind": "same_level_duplicate_room",
                "entity_ids": [row["entity_id"] for row in matches],
                "pages": sorted({row["source"].get("page") for row in matches if row["source"].get("page") is not None}),
                "status": "review_required",
                "reason": "The same room label has competing geometry records on the same level.",
            })
            review_items.append({
                "item_id": conflict_id, "affected_id": label, "status": "blocked",
                "field": "room_identity", "source_artifact": "geometry_resolution",
                "reason": "Same-level room identity is ambiguous; select the correct boundary.",
                "remediation": "Choose one compatible boundary or rename the room records before activation.",
            })
    # Link the same room label across plan/finish/ceiling pages. A shared label
    # is a relationship witness, not proof of geometry.
    for left in room_entities:
        for right in room_entities:
            if left["entity_id"] >= right["entity_id"]:
                continue
            if normalise_label(left.get("label")) and normalise_label(left.get("label")) == normalise_label(right.get("label")) and normalise_label(left.get("level_candidate")) == normalise_label(right.get("level_candidate")):
                relationships.append({
                    "relationship_id": "room_cross_page_" + fingerprint([left["entity_id"], right["entity_id"]])[:16],
                    "kind": "room_cross_page_label_match", "entity_ids": [left["entity_id"], right["entity_id"]],
                    "pages": sorted({left["source"].get("page"), right["source"].get("page")}),
                    "basis": "exact_normalized_room_label_and_level", "status": "proposed",
                    "independent_witness": False, "cross_check_only": False,
                })

    # Vector pages are referenced by witness IDs, but raw vectors remain neutral
    # evidence; only geometry-confirmation output can promote them.
    for page in (vector_geometry or {}).get("geometry_key_points", {}).get("pages", []):
        number = page.get("page")
        role = page_by_number.get(number, {}).get("proposed_role", page.get("plan_role", ""))
        for candidate in page.get("dimension_candidates", [])[:300]:
            witness = add_witness(number, candidate.get("text_seen", ""), "dimension", candidate, "pdf_vector_dimension", candidate.get("confidence", "unknown"), candidate.get("bbox_px", ""), role)
            raw_value = candidate.get("value_mm")
            try:
                valid_dimension = float(raw_value) > 0
            except (TypeError, ValueError):
                valid_dimension = False
            add_entity(number, candidate.get("text_seen", ""), "dimension", deepcopy(candidate),
                       "geometry_proposed" if valid_dimension else "geometry_review_required",
                       "pdf_vector_dimension", candidate.get("confidence", "unknown"),
                       location=candidate.get("bbox_px", ""), witness_ids=[witness["witness_id"]],
                       unresolved=[] if valid_dimension else ["dimension_value"])
        for candidate in page.get("line_candidates", [])[:300]:
            witness = add_witness(number, candidate.get("candidate_id", ""), "vector_geometry", candidate, "pdf_vector_geometry", candidate.get("confidence", "unknown"), candidate.get("bbox_px", ""), role)
            points = [candidate.get("start_px"), candidate.get("end_px")]
            valid_line = all(valid_point(point) for point in points)
            add_entity(number, candidate.get("candidate_id", ""), "wall", deepcopy(candidate),
                       "geometry_proposed" if valid_line else "geometry_review_required",
                       "pdf_vector_geometry", candidate.get("confidence", "unknown"),
                       location=candidate.get("bbox_px", ""), witness_ids=[witness["witness_id"]],
                       unresolved=[] if valid_line else ["line_endpoints"])

    for page in (dimension_matches or {}).get("pages", []):
        number = page.get("page")
        role = page_by_number.get(number, {}).get("proposed_role", page.get("plan_role", ""))
        for span in page.get("dimension_span_candidates", [])[:300]:
            witness = add_witness(number, span.get("text_seen", ""), "dimension_span", span, "dimension_wall_matcher", span.get("confidence", "unknown"), span.get("bbox_px", ""), role)
            relationships.append({"relationship_id": "dimension_witness_" + fingerprint([number, span.get("dimension_candidate_id"), witness["witness_id"]])[:16], "kind": "dimension_span_witness", "witness_ids": [witness["witness_id"]], "status": "proposed", "value_mm": span.get("value_mm")})

    # Elevation/opening witnesses are linked by explicit tag or a unique
    # dimensioned opening record.  Ambiguity stays a conflict.
    openings_by_tag = defaultdict(list)
    for opening in building.get("openings", []):
        tag = str(opening.get("tag", "")).upper().strip()
        if tag:
            openings_by_tag[tag].append(opening)
    for opening in building.get("openings", []):
        evidence = opening.get("evidence", [])
        page = evidence[0].get("page") if evidence else None
        role = page_by_number.get(page, {}).get("proposed_role", "")
        if role != "opening_elevation":
            continue
        tag = str(opening.get("tag", "")).upper().strip()
        candidates = openings_by_tag.get(tag, []) if tag else []
        if len(candidates) == 1 and candidates[0] is not opening:
            relationships.append({"relationship_id": "opening_witness_" + fingerprint([opening.get("id"), candidates[0].get("id")])[:16], "kind": "opening_plan_elevation", "entity_ids": [opening.get("id"), candidates[0].get("id")], "pages": sorted({row.get("page") for row in evidence + candidates[0].get("evidence", []) if row.get("page") is not None}), "basis": "unique_explicit_opening_tag", "status": "matched"})
        elif tag:
            conflicts.append({"conflict_id": "geometry_conflict_" + fingerprint(["opening", opening.get("id"), tag, len(candidates)])[:16], "kind": "opening_mapping_ambiguous" if candidates else "opening_plan_mapping_missing", "entity_ids": [opening.get("id")] + [row.get("id") for row in candidates], "pages": [page], "reason": "Elevation opening cannot be uniquely linked to a plan opening.", "status": "review_required"})

    # 3D pages create explicit cross-check links but never primary dimensions.
    render_pages = [row for row in pages if row.get("proposed_role") == "3d_render"]
    for page in render_pages:
        relationships.append({"relationship_id": "3d_crosscheck_" + fingerprint([page.get("page"), page.get("page_group")])[:16], "kind": "3d_visual_crosscheck", "from_page": page.get("page"), "status": "proposed", "basis": "visual_presence_or_level_relationship", "primary_dimension_source": False})

    # Preserve geometry-confirmation outputs as independent witness metadata.
    for page in (geometry_confirmation or {}).get("pages", []):
        number = page.get("page")
        for wall in page.get("wall_confirmations", []):
            if wall.get("status") in {"vector_confirmed", "scale_calibrated", "cad_ready_candidate"}:
                witness = add_witness(number, wall.get("wall_id", ""), "confirmed_wall", wall, "geometry_confirmation", wall.get("confidence", "unknown"), role=page_by_number.get(number, {}).get("proposed_role"))
                relationships.append({"relationship_id": "wall_witness_" + fingerprint([wall.get("wall_id"), witness["witness_id"]])[:16], "kind": "wall_vector_confirmation", "witness_ids": [witness["witness_id"]], "status": "confirmed"})

    # Explicit dimension-to-wall links produced by the existing matcher are
    # preserved as generic relationships. Raw proximity-only candidates remain
    # proposed and never promote a room boundary.
    for page in (dimension_matches or {}).get("pages", []):
        number = page.get("page")
        for link in page.get("dimension_wall_links", []) or []:
            dimension_id = link.get("dimension_candidate_id") or link.get("dimension_id")
            wall_id = link.get("target_wall_id") or link.get("wall_id")
            if not dimension_id or not wall_id:
                continue
            relationships.append({
                "relationship_id": "dimension_wall_" + fingerprint([source_fp, number, dimension_id, wall_id])[:16],
                "kind": "dimension_to_wall", "page": number,
                "dimension_id": dimension_id, "wall_id": wall_id,
                "value_mm": link.get("value_mm"),
                "basis": link.get("match_basis") or link.get("basis") or "explicit_dimension_wall_match",
                "status": "matched" if link.get("status") in {None, "matched", "confirmed"} else link.get("status"),
                "independent_witness": True, "cross_check_only": False,
            })

    # Resolve a derived room area only from an actual closed wall loop,
    # explicit linked dimensions, a spatially linked room label, and a second
    # independent architect witness.  This is deliberately separate from the
    # pre-existing explicit-room-area path above.
    vector_pages = {
        row.get("page"): row for row in ((vector_geometry or {}).get("geometry_key_points") or {}).get("pages", [])
    }
    deterministic_page_diagnostics = {}
    for page_meta in pages:
        number = page_meta.get("page")
        if page_meta.get("proposed_role") not in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}:
            continue
        vector_page = vector_pages.get(number, {})
        line_decisions = []
        lines = _wall_lines(vector_page, diagnostics=line_decisions)
        component_rejections = []
        loops = _closed_wall_loops(lines, diagnostics=component_rejections)
        scale, scale_links = _scale_from_dimension_links(number, lines, dimension_matches)
        labels = _label_diagnostic_candidates(spatial_ocr, number)
        deterministic_page_diagnostics[number] = {
            "page": number,
            "wall_lines": {"accepted_count": len(lines), "accepted_ids": sorted(row["wall_id"] for row in lines),
                           "accepted": sorted((row for row in line_decisions if row["accepted"]), key=lambda row: row["candidate_id"]),
                           "rejected_count": len([row for row in line_decisions if not row["accepted"]]),
                           "rejected": sorted((row for row in line_decisions if not row["accepted"]), key=lambda row: row["candidate_id"])},
            "closed_loops_found": len(loops), "closed_loops": deepcopy(loops),
            "rejected_components": component_rejections,
            "label_points": {"accepted_count": len(labels["accepted"]), "accepted": labels["accepted"],
                             "status_filtered_count": len(labels["rejected"]), "rejected": labels["rejected"]},
            "dimension_links": {"matcher_link_count": len(_page_dimension_links(dimension_matches, number)),
                                "calibration_link_count": len(scale_links), "accepted": scale_links,
                                "mm_per_px": scale},
        }
    vision_rooms = [
        row for row in ((vision_response or {}).get("result", {}).get("auto_extraction", {}).get("entities", []) or [])
        if isinstance(row, dict) and row.get("kind") == "room"
    ]

    ai_room_candidates = _ai_geometry_pages(vision_response)
    # Room inference can run locally before a provider vision response exists.
    # Carry any structured PDF/vector geometry candidates into the same
    # validation path rather than treating them as a second room model.  Text
    # labels without a polygon/wall loop are deliberately left in the
    # provisional room ledger and do not reach this list.
    for proposal in room_proposals or []:
        if not isinstance(proposal, dict):
            continue
        geometry = proposal.get("geometry") if isinstance(proposal.get("geometry"), dict) else {}
        points = (geometry.get("boundary_points_mm") or geometry.get("boundary_points_px") or
                  geometry.get("polygon_points_px") or geometry.get("points_px") or [])
        coordinate_units = str(geometry.get("coordinate_units", "mm" if geometry.get("boundary_points_mm") else "px")).casefold()
        wall_ids = geometry.get("wall_ids") or geometry.get("ordered_wall_ids") or []
        if not points and not wall_ids:
            continue
        source_pages = proposal.get("source_pages") or ([proposal.get("page")] if proposal.get("page") is not None else [])
        primary_page = source_pages[0] if source_pages else proposal.get("page")
        label = proposal.get("label") or proposal.get("room_label") or ""
        if any(
            str(existing.get("label", "")).casefold() == str(label).casefold()
            and existing.get("page") == primary_page
            for existing in ai_room_candidates
        ):
            continue
        ai_room_candidates.append({
            "room_geometry_id": proposal.get("room_id") or proposal.get("room_geometry_id") or "",
            "label": label,
            "level_name": proposal.get("level_name") or proposal.get("level") or "",
            "page": primary_page,
            "source_pages": source_pages,
            "coordinate_units": coordinate_units,
            "boundary_points_px": geometry.get("boundary_points_px") or points if coordinate_units != "mm" else [],
            "boundary_points_mm": points if coordinate_units == "mm" else [],
            "wall_ids": wall_ids,
            "dimension_ids": geometry.get("dimension_ids") or [],
            "dimension_wall_links": geometry.get("dimension_wall_links") or [],
            "walls": geometry.get("walls") or [],
            "dimensions": geometry.get("dimensions") or [],
            "scale_mm_per_px": geometry.get("scale_mm_per_px"),
            "scale_source": geometry.get("scale_source", "linked_dimension_or_pdf_scale"),
            "calibration": deepcopy(geometry.get("calibration", {})) if isinstance(geometry.get("calibration"), dict) else {},
            "room_label_bbox": proposal.get("room_label_bbox"),
            "confidence": proposal.get("confidence", "medium"),
            "confidence_score": proposal.get("confidence_score"),
            "independent_witnesses": geometry.get("independent_witnesses") or proposal.get("independent_witnesses") or [],
            "source_crop": geometry.get("source_crop", ""),
            "area_m2": geometry.get("area_m2", proposal.get("area_m2")),
            "confidence": proposal.get("confidence_band", "medium"),
            "assumptions": proposal.get("assumptions", []),
            "conflicts": proposal.get("conflicts", []),
            "unresolved_fields": proposal.get("unresolved_fields", []),
        })
    # AI geometry is a proposed witness only.  Preserve a validated shape (or
    # ordered wall sequence) in the graph so reviewers can inspect it, but do
    # not let it bypass the deterministic vector-loop, scale, level, and
    # independent-witness gates below.
    for item in vision_rooms:
        points = item.get("boundary_points_px") or item.get("boundary_points") or []
        wall_ids = [str(value) for value in item.get("wall_ids", []) if value]
        valid_shape = polygon_is_simple(points) if points else bool(wall_ids)
        unresolved = list(item.get("unresolved_fields", []) or [])
        if not valid_shape:
            unresolved.append("validated_boundary_shape")
        if not item.get("level_name"):
            unresolved.append("floor_identity")
        if not item.get("dimension_ids"):
            unresolved.append("dimension_wall_binding")
        proposal = add_entity(
            item.get("page"), item.get("label", ""), "room_geometry_proposal",
            {"boundary_points_px": points, "wall_ids": wall_ids,
             "dimension_ids": item.get("dimension_ids", []),
             "independent_witness_page": item.get("independent_witness_page")},
            "geometry_proposed" if valid_shape else "geometry_review_required",
            "manual_vision_response", item.get("confidence", "unknown"),
            location="|".join(wall_ids) or str(item.get("boundary_reference", "")),
            unresolved=sorted(set(unresolved)), level=item.get("level_name", ""),
            witness_ids=[str(row.get("reference")) for row in item.get("witnesses", []) if isinstance(row, dict) and row.get("reference")],
        )
        relationships.append({
            "relationship_id": "vision_geometry_proposal_" + fingerprint([proposal["entity_id"], item.get("independent_witness_page")])[:16],
            "kind": "vision_geometry_proposal", "entity_ids": [proposal["entity_id"]],
            "pages": sorted({value for value in (item.get("page"), item.get("independent_witness_page")) if value is not None}),
            "basis": "validated_ai_geometry_schema", "status": "proposed",
            "independent_witness": bool(item.get("independent_witness_page")), "cross_check_only": False,
        })

    # AI-primary room geometry.  This is intentionally separate from the
    # legacy auto_extraction path: the AI identifies walls and measurements;
    # this layer validates references and decides whether the result is an
    # estimate or a reviewed proof.
    ai_geometry_entities = []
    room_geometry_proofs = []
    # Two different AI boundaries on the same page for the same room/level
    # are a conflict. Boundaries on different pages may instead be independent
    # witnesses and are evaluated through the witness gate below.
    ai_scope_candidates = defaultdict(list)
    for candidate in ai_room_candidates:
        ai_scope_candidates[(normalise_label(candidate.get("label")), normalise_label(candidate.get("level_name")), candidate.get("page"))].append(candidate)
    for candidates in ai_scope_candidates.values():
        if len(candidates) > 1:
            for candidate in candidates:
                candidate.setdefault("conflicts", []).append("competing_same_page_boundary")
    for item in ai_room_candidates:
        if not item.get("scale_mm_per_px"):
            inferred_scale = _ai_scale_from_links(item)
            if inferred_scale:
                item["scale_mm_per_px"] = inferred_scale
                item["scale_source"] = "dimension_wall_link"
        points, polygon_area_units2, unresolved, independent, review_warnings, coordinate_units = _validate_ai_room(
            item, resolution_mode, page_by_number, room_entities
        )
        label = item.get("label", "")
        level = item.get("level_name", "")
        matched_rooms = [entity for entity in room_entities
                         if normalise_label(entity.get("label")) == normalise_label(label)
                         and normalise_label(entity.get("level_candidate")) == normalise_label(level)]
        room_entity = matched_rooms[0] if len(matched_rooms) == 1 else None
        high_confidence = _confidence_is_high(item)
        source_page = item.get("page")
        location = "|".join(str(value) for value in item.get("wall_ids", [])) or str(item.get("room_geometry_id", ""))
        derived_area_m2 = None
        if coordinate_units == "mm" and polygon_area_units2:
            derived_area_m2 = round(polygon_area_units2 / 1_000_000.0, 6)
        elif polygon_area_units2 and isinstance(item.get("scale_mm_per_px"), (int, float)) and math.isfinite(item["scale_mm_per_px"]):
            derived_area_m2 = round(polygon_area_units2 * item["scale_mm_per_px"] ** 2 / 1_000_000.0, 6)
        reported_area_m2 = item.get("area_m2")
        if isinstance(reported_area_m2, (int, float)) and derived_area_m2:
            if not math.isfinite(reported_area_m2) or reported_area_m2 <= 0 or abs(reported_area_m2 - derived_area_m2) / derived_area_m2 > 0.05:
                unresolved.append("reported_area_conflict")
        # AI output is never itself an engineering-reviewed proof.  The
        # reviewed path continues through the existing closed-loop plus
        # independent-witness/vector-confirmation logic below.  Calculate
        # the status only after every structural/area check, so a conflicting
        # reported area can never remain active by accident.
        active = not unresolved and resolution_mode in {"preliminary_ai_estimate", "engineering_reviewed"}
        status = "ai_estimated" if active and resolution_mode == "preliminary_ai_estimate" else (
            "geometry_confirmed" if active else "geometry_review_required"
        )
        value = {
            "boundary_points_px": points if coordinate_units == "px" else [],
            "boundary_points_mm": points if coordinate_units == "mm" else [],
            "coordinate_units": coordinate_units,
            "wall_ids": item.get("wall_ids", []),
            "dimension_ids": item.get("dimension_ids", []),
            "area_px2": polygon_area_units2 if coordinate_units == "px" else None,
            "area_mm2": polygon_area_units2 if coordinate_units == "mm" else None,
            # Calculation input comes from deterministic polygon area. A
            # dimension-space polygon needs no visual scale or pixel conversion.
            "area_m2": derived_area_m2,
            "reported_area_m2": reported_area_m2,
            "level_name": level,
            "independent_witnesses": independent,
            "source_crop": item.get("source_crop", ""),
            "room_label": label,
            "room_label_bbox": item.get("room_label_bbox") or item.get("label_bbox") or item.get("label_bounding_box"),
            "wall_bindings": [
                {"wall_id": link.get("target_wall_id"), "reason": link.get("reason") or link.get("match_basis") or link.get("basis"),
                 "source": link.get("source") or link.get("source_reference") or item.get("source_crop") or f"page:{source_page}"}
                for link in item.get("dimension_wall_links", []) or []
            ],
            "dimension_bindings": [
                {"dimension_id": link.get("dimension_id"), "wall_id": link.get("target_wall_id"),
                 "value_mm": link.get("value_mm"), "measured_span_start_px": link.get("measured_span_start_px"),
                 "measured_span_end_px": link.get("measured_span_end_px"),
                 "reason": link.get("reason") or link.get("match_basis") or link.get("basis"),
                 "source": link.get("source") or link.get("source_reference") or item.get("source_crop") or f"page:{source_page}"}
                for link in item.get("dimension_wall_links", []) or []
            ],
            "assumptions": item.get("assumptions", []),
            "review_warnings": review_warnings,
            "confidence_score": item.get("confidence_score"),
            "resolution_mode": resolution_mode,
            "scale_source": item.get("scale_source", "ai_cited_scale"),
            "derivation": {
                "formula": ("shoelace_area_mm2 ÷ 1,000,000" if coordinate_units == "mm" else
                            "shoelace_area_px2 × (mm_per_px²) ÷ 1,000,000"),
                "operands": ({"polygon_area_mm2": polygon_area_units2, "coordinate_units": "mm"} if coordinate_units == "mm" else
                             {"polygon_area_px2": polygon_area_units2, "mm_per_px": item.get("scale_mm_per_px")}),
                "rounding": "6 decimal places",
            },
        }
        if not isinstance(value.get("area_m2"), (int, float)) or value.get("area_m2", 0) <= 0:
            unresolved.append("area_m2")
            status = "geometry_review_required"
        entity = add_entity(
            source_page, label, "ai_room_geometry", value, status,
            "ai_geometry_review", "high" if high_confidence else item.get("confidence", "unknown"),
            location=location, unresolved=unresolved,
            witness_ids=[], level=level,
        )
        if review_warnings:
            entity["review_warnings"] = review_warnings
        ai_witness = add_witness(
            source_page, label, "ai_room_geometry", item, "ai_geometry_review",
            "high" if high_confidence else item.get("confidence", "unknown"),
            location=location, role=page_by_number.get(source_page, {}).get("proposed_role"),
        )
        independent_witness_records = [
            add_witness(row.get("page"), label, row.get("kind", "independent_geometry"), row,
                        row.get("method", "ai_independent_witness"), row.get("confidence", "unknown"),
                        role=page_by_number.get(row.get("page"), {}).get("proposed_role"))
            for row in independent if row.get("page") is not None
        ]
        entity["witness_ids"] = sorted(set(entity.get("witness_ids", []) + [ai_witness["witness_id"]] + [
            row["witness_id"] for row in independent_witness_records
        ]))
        # Reattach the normalized source identity after the witness is created.
        entity["source"]["page"] = source_page
        entity["source"]["drawing_number"] = _page_identity(page_by_number.get(source_page, {}))
        entity["source"]["source_crop"] = item.get("source_crop", "")
        entity["witness_ids"] = sorted(set(entity.get("witness_ids", [])))
        entity["ai_geometry_id"] = item.get("room_geometry_id", "")
        entity["resolution_mode"] = resolution_mode
        entity["room_entity_id"] = room_entity["entity_id"] if room_entity else ""
        entity["room_source_id"] = next((row.get("id") for row in building.get("spaces", [])
                                          if row.get("id") and room_entity and normalise_label(row.get("name")) == normalise_label(label)), "")
        entity["geometry_status"] = status
        value["independent_witness_ids"] = [row["witness_id"] for row in independent_witness_records]
        value["independent_witnesses"] = independent
        value["proof_status"] = status
        value["tolerances"] = {"calibration": "existing_geometry_tolerance", "dimension_consistency": "existing_geometry_tolerance"}
        entity["value"] = value
        ai_geometry_entities.append(entity)
        room_geometry_proofs.append({
            "proof_id": entity["entity_id"],
            "room_geometry_id": entity.get("ai_geometry_id", ""),
            "room_source_id": entity.get("room_source_id", ""),
            "room_label": label,
            "level_name": level,
            "source_page": source_page,
            "drawing_number": entity["source"].get("drawing_number", ""),
            "label_bbox": value.get("room_label_bbox"),
            "coordinate_units": coordinate_units,
            "boundary_points_px": points if coordinate_units == "px" else [],
            "boundary_points_mm": points if coordinate_units == "mm" else [],
            "wall_ids": item.get("wall_ids", []),
            "dimension_ids": item.get("dimension_ids", []),
            "wall_bindings": value.get("wall_bindings", []),
            "dimension_bindings": value.get("dimension_bindings", []),
            "calibration": {"mm_per_px": item.get("scale_mm_per_px"), "source": value.get("scale_source"),
                            "operands": value.get("derivation", {}).get("operands", {})},
            "area_m2": value.get("area_m2"),
            "independent_witness_ids": value.get("independent_witness_ids", []),
            "independent_witnesses": independent,
            "confidence": entity.get("confidence"),
            "confidence_score": item.get("confidence_score"),
            "conflicts": item.get("conflicts", []),
            "unresolved_fields": sorted(set(unresolved)),
            "resolution_mode": resolution_mode,
            "status": status,
        })
        if status == "ai_estimated" and room_entity:
            # A valid preliminary AI boundary supersedes the generic
            # "building evidence has no geometry" blocker for this room.
            review_items[:] = [item for item in review_items if not (
                item.get("affected_id") in {room_entity.get("value", {}).get("id"), entity.get("room_source_id")}
                and item.get("field") == "geometry"
            )]
        if resolution_mode == "preliminary_ai_estimate" and not independent:
            entity.setdefault("review_warnings", []).append("independent_geometry_witness")
            review_items.append({
                "item_id": "geometry_warning_" + fingerprint([entity["entity_id"], "independent_geometry_witness"])[:16],
                "affected_id": label or entity["entity_id"], "status": "warning", "field": "independent_geometry_witness",
                "source_artifact": "geometry_resolution", "page": source_page,
                "reason": "Preliminary AI geometry is active without an independent second witness.",
                "remediation": "Link a finish plan, area schedule, section, or other independent witness before engineering review.",
                "blocks_calculation": False,
            })
        relationships.append({
            "relationship_id": "ai_room_geometry_" + fingerprint([entity["entity_id"], resolution_mode])[:16],
            "kind": "ai_room_geometry_binding",
            "entity_ids": [entity["entity_id"]] + ([room_entity["entity_id"]] if room_entity else []),
            "pages": sorted(set([source_page] + [row.get("page") for row in independent if isinstance(row, dict) and row.get("page") is not None])),
            "basis": "ai_wall_dimension_interpretation",
            "status": "active" if status in {"ai_estimated", "geometry_confirmed"} else "blocked",
            "independent_witness": bool(independent),
            "cross_check_only": False,
            "resolution_mode": resolution_mode,
        })
        for link in item.get("dimension_wall_links", []) or []:
            relationships.append({
                "relationship_id": "ai_dimension_wall_" + fingerprint([
                    source_page, link.get("dimension_id"), link.get("target_wall_id")
                ])[:16],
                "kind": "ai_dimension_to_wall",
                "page": source_page,
                "dimension_id": link.get("dimension_id"),
                "wall_id": link.get("target_wall_id"),
                "value_mm": link.get("value_mm"),
                "basis": link.get("reason") or link.get("match_basis") or "ai_visual_wall_dimension_assignment",
                "status": "active" if status == "ai_estimated" else "proposed",
                "independent_witness": bool(independent),
                "cross_check_only": False,
                "resolution_mode": resolution_mode,
            })
        if status in {"ai_estimated", "geometry_confirmed"}:
            add_entity(
                source_page, label, "area", {**value, "room_geometry_entity_id": entity["entity_id"],
                                             "room_source_id": entity.get("room_source_id", ""),
                                             "geometry_proof_id": entity["entity_id"]},
                status, "ai_derived_room_area", "high" if high_confidence else "medium",
                location=entity["entity_id"], witness_ids=entity.get("witness_ids", []), level=level,
            )
        else:
            review_items.append({
                "item_id": "geometry_issue_" + fingerprint([entity["entity_id"], sorted(set(unresolved))])[:16],
                "affected_id": label or entity["entity_id"], "status": "blocked", "field": "geometry_area",
                "source_artifact": "geometry_resolution", "page": source_page,
                "reason": "AI geometry did not pass activation checks: " + ", ".join(sorted(set(unresolved))),
                "remediation": "Correct the AI wall/dimension links or provide the missing scale, level, room mapping, and witness evidence.",
            })
    def room_pages(room):
        return {item.get("page") for item in room.get("evidence", []) if isinstance(item, dict) and item.get("page") is not None}

    def independent_room_witnesses(room, primary_page, level):
        witnesses = []
        # A different plan/finish/section page is independent only when it
        # carries an explicit geometry/region/area witness, rather than merely
        # repeating the room name in a legend or note.
        for item in room.get("evidence", []) or []:
            if not isinstance(item, dict) or item.get("page") == primary_page:
                continue
            candidate_page = page_by_number.get(item.get("page"), {})
            role = candidate_page.get("proposed_role", "")
            if role in {"3d_render", "3d_reference", "reference", "legend_or_general_notes"}:
                continue
            explicit_geometry = any(item.get(key) for key in ("geometry_reference", "boundary_reference", "region_reference", "area_m2", "area"))
            if explicit_geometry:
                witnesses.append({"page": item.get("page"), "kind": "cross_page_geometry", "evidence": item,
                                 "method": "building_evidence_cross_page", "confidence": room.get("confidence", "unknown")})
        for item in vision_rooms:
            if normalise_label(item.get("label")) != normalise_label(room.get("name")) or item.get("page") == primary_page:
                continue
            if level and item.get("level_name") and normalise_label(item.get("level_name")) != normalise_label(level):
                continue
            candidate_page = page_by_number.get(item.get("page"), {})
            if candidate_page.get("proposed_role") in {"3d_render", "3d_reference", "reference"}:
                continue
            if item.get("geometry_status") == "geometry_confirmed" and (item.get("boundary_reference") or item.get("witnesses")):
                witnesses.append({"page": item.get("page"), "kind": "validated_vision_geometry", "evidence": item,
                                 "method": "manual_vision_response", "confidence": item.get("confidence", "unknown")})
        return witnesses

    for room in building.get("spaces", []):
        source_room_id = room.get("id")
        room_entity = room_entities_by_source_id.get(source_room_id)
        if not room_entity:
            continue
        level = room.get("level_name") or room_entity.get("level_candidate", "")
        candidate_pages = room_pages(room)
        # A room can be labelled on a finish plan while its vector boundary is
        # on a separate dimension plan. Include only plan pages carrying the
        # exact spatial OCR label and a compatible level; never add pages from
        # page order or visual resemblance.
        for page_number, page_meta in page_by_number.items():
            if page_meta.get("proposed_role") not in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}:
                continue
            page_level = page_meta.get("level_name", "")
            if level and page_level and normalise_label(level) != normalise_label(page_level):
                continue
            if _label_points(spatial_ocr, room, page_number):
                candidate_pages.add(page_number)
        for page_number in sorted(candidate_pages):
            page_meta = page_by_number.get(page_number, {})
            if page_meta.get("proposed_role") not in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}:
                continue
            vector_page = vector_pages.get(page_number, {})
            lines = _wall_lines(vector_page)
            loops = _closed_wall_loops(lines)
            scale, scale_links = _scale_from_dimension_links(page_number, lines, dimension_matches)
            label_points = _label_points(spatial_ocr, room, page_number)
            matched = []
            for loop in loops:
                labels_inside = [item for item in label_points if _point_in_polygon(item["point"], loop["points_px"])]
                if len(labels_inside) == 1:
                    matched.append((loop, labels_inside[0]))
            if len(matched) > 1:
                conflict_id = "geometry_conflict_" + fingerprint([source_fp, "room_loop", room.get("name"), level, page_number, [row[0]["wall_ids"] for row in matched]])[:16]
                conflicts.append({"conflict_id": conflict_id, "kind": "room_boundary_ambiguous", "entity_ids": [room_entity["entity_id"]],
                                  "pages": [page_number], "status": "review_required",
                                  "reason": "The room label is inside more than one compatible closed wall loop."})
                continue
            if not matched:
                continue
            loop, label_witness = matched[0]
            unresolved = []
            if not level:
                unresolved.append("floor_identity")
            if scale is None:
                unresolved.append("scale_calibration")
            independent = independent_room_witnesses(room, page_number, level)
            if not independent:
                unresolved.append("independent_geometry_witness")
            wall_witnesses = [
                add_witness(page_number, wall_id, "boundary_wall", {"wall_id": wall_id}, "pdf_vector_geometry",
                            role=page_meta.get("proposed_role"))
                for wall_id in loop["wall_ids"]
            ]
            label_record = add_witness(page_number, room.get("name", ""), "room_label_in_boundary", label_witness["raw"],
                                       "spatial_ocr", role=page_meta.get("proposed_role"))
            dimension_witnesses = [
                add_witness(page_number, row.get("dimension_id", ""), "dimension_to_wall", row, "dimension_wall_matcher",
                            role=page_meta.get("proposed_role"))
                for row in scale_links
            ]
            supporting_witnesses = [
                add_witness(row["page"], room.get("name", ""), row["kind"], row["evidence"], row["method"], row["confidence"],
                            role=page_by_number.get(row["page"], {}).get("proposed_role"))
                for row in independent
            ]
            all_witness_ids = [row["witness_id"] for row in wall_witnesses + [label_record] + dimension_witnesses + supporting_witnesses]
            area_m2 = round(polygon_area(loop["points_px"]) * scale * scale / 1_000_000.0, 6) if scale else None
            proof_value = {
                "room_entity_id": room_entity["entity_id"], "room_source_id": source_room_id,
                "points_px": loop["points_px"], "boundary_wall_ids": loop["wall_ids"],
                "calibration": {"mm_per_px": scale, "dimension_links": scale_links, "consistency_tolerance": "2%"},
                "label_witness_id": label_record["witness_id"], "independent_witness_ids": [row["witness_id"] for row in supporting_witnesses],
                "area_m2": area_m2,
                "derivation": {
                    "formula": "shoelace_area_px2 × (mm_per_px²) ÷ 1,000,000",
                    "operands": {"polygon_area_px2": polygon_area(loop["points_px"]), "mm_per_px": scale},
                    "rounding": "6 decimal places",
                },
            }
            proof_status = "geometry_confirmed" if not unresolved and area_m2 and area_m2 > 0 else "geometry_review_required"
            proof = add_entity(page_number, room.get("name", ""), "room_geometry_proof", proof_value, proof_status,
                               "calibrated_closed_wall_loop", "high" if proof_status == "geometry_confirmed" else "medium",
                               location="|".join(loop["wall_ids"]), witness_ids=all_witness_ids, unresolved=unresolved, level=level)
            relationships.append({"relationship_id": "room_boundary_proof_" + fingerprint([proof["entity_id"], room_entity["entity_id"]])[:16],
                                  "kind": "room_to_calibrated_boundary", "entity_ids": [room_entity["entity_id"], proof["entity_id"]],
                                  "pages": [page_number], "basis": "closed_wall_loop+label_inside+dimension_calibration",
                                  "status": "confirmed" if proof_status == "geometry_confirmed" else "proposed",
                                  "independent_witness": bool(independent), "cross_check_only": False})
            if proof_status == "geometry_confirmed":
                room_entity["geometry_status"] = "geometry_confirmed"
                room_entity["witness_ids"] = sorted(set(room_entity.get("witness_ids", []) + all_witness_ids))
                room_entity.setdefault("value", {})["geometry_proof_id"] = proof["entity_id"]
                review_items[:] = [
                    item for item in review_items
                    if not (item.get("affected_id") == room.get("id", "") and item.get("field") == "geometry")
                ]
                add_entity(page_number, room.get("name", ""), "area", {**proof_value, "geometry_proof_id": proof["entity_id"]},
                           "geometry_confirmed", "derived_room_area", "high", location=proof["entity_id"],
                           witness_ids=all_witness_ids, level=level)
            else:
                review_items.append({
                    "item_id": "geometry_issue_" + fingerprint([room.get("id"), page_number, "proof", sorted(unresolved)])[:16],
                    "affected_id": room.get("id", ""), "status": "blocked", "field": "geometry_area",
                    "source_artifact": "geometry_resolution", "page": page_number,
                    "reason": "Closed room boundary found, but " + ", ".join(unresolved) + " is required before its area can be used.",
                    "remediation": "Add the missing cited witness or keep this room geometry proposed.",
                })

    room_diagnostics = []
    for room in building.get("spaces", []):
        source_room_id = room.get("id")
        room_entity = room_entities_by_source_id.get(source_room_id)
        if not room_entity:
            continue
        level = room.get("level_name") or room_entity.get("level_candidate", "")
        attempts = []
        for number, page_meta in sorted(page_by_number.items(), key=lambda item: (item[0] is None, item[0])):
            if page_meta.get("proposed_role") not in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}:
                continue
            if level and page_meta.get("level_name") and normalise_label(level) != normalise_label(page_meta.get("level_name")):
                continue
            diag = deterministic_page_diagnostics.get(number, {})
            matching_labels = _label_points(spatial_ocr, room, number)
            if not diag.get("wall_lines", {}).get("accepted_count"):
                reason = "no_wall_lines_accepted_by_existing_role_filter"
            elif not diag.get("closed_loops_found"):
                reason = "no_closed_loops; inspect rejected_components for branch_or_open_nodes"
            elif not matching_labels:
                reason = "no_matching_room_label_passed_existing_status_filter"
            elif not any(sum(1 for label in matching_labels if _point_in_polygon(label["point"], loop["points_px"])) == 1
                         for loop in diag.get("closed_loops", [])):
                reason = "accepted_room_label_not_inside_a_unique_closed_loop"
            else:
                scale_data = diag.get("dimension_links", {})
                reason = "boundary_candidate_found_but_scale_or_independent_witness_is_missing" if not scale_data.get("mm_per_px") else "boundary_candidate_found"
            attempts.append({"page": number, "reason": reason, "matching_label_count": len(matching_labels),
                             "accepted_wall_count": diag.get("wall_lines", {}).get("accepted_count", 0),
                             "closed_loop_count": diag.get("closed_loops_found", 0)})
        room_diagnostics.append({"room_id": source_room_id, "room_label": room.get("name", ""),
                                 "proof_created": any(row.get("room_source_id") == source_room_id for row in room_geometry_proofs),
                                 "attempts": attempts,
                                 "reason": "proof_created" if any(row.get("room_source_id") == source_room_id for row in room_geometry_proofs)
                                          else ("no_compatible_plan_page" if not attempts else "no_automatic_room_geometry_proof")})

    diagnosed_room_ids = {row.get("room_id") for row in room_diagnostics}
    for registry_room in (reviewer_room_geometry or {}).get("rooms", []) if isinstance(reviewer_room_geometry, dict) else []:
        if not isinstance(registry_room, dict) or not registry_room.get("room_id") or registry_room.get("room_id") in diagnosed_room_ids:
            continue
        attempts = []
        for number, page_meta in sorted(page_by_number.items(), key=lambda item: (item[0] is None, item[0])):
            if page_meta.get("proposed_role") not in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}:
                continue
            if registry_room.get("level_name") and page_meta.get("level_name") and normalise_label(registry_room["level_name"]) != normalise_label(page_meta.get("level_name")):
                continue
            diag = deterministic_page_diagnostics.get(number, {})
            if not diag.get("wall_lines", {}).get("accepted_count"):
                reason = "no_wall_lines_accepted_by_existing_role_filter"
            elif not diag.get("closed_loops_found"):
                reason = "no_closed_loops; inspect rejected_components for branch_or_open_nodes"
            elif not diag.get("label_points", {}).get("accepted_count"):
                reason = "no_matching_room_label_passed_existing_status_filter"
            else:
                reason = "no_unique_room_label_inside_automatic_loop_or_missing_calibration"
            attempts.append({"page": number, "reason": reason,
                             "accepted_wall_count": diag.get("wall_lines", {}).get("accepted_count", 0),
                             "closed_loop_count": diag.get("closed_loops_found", 0)})
        room_diagnostics.append({"room_id": registry_room["room_id"], "room_label": registry_room.get("label", ""),
                                 "proof_created": any(row.get("room_source_id") == registry_room["room_id"] for row in room_geometry_proofs),
                                 "attempts": attempts,
                                 "reason": "proof_created" if any(row.get("room_source_id") == registry_room["room_id"] for row in room_geometry_proofs)
                                          else ("no_compatible_plan_page" if not attempts else "no_automatic_room_geometry_proof")})
    for trace in (reviewer_room_geometry or {}).get("records", []) if isinstance(reviewer_room_geometry, dict) else []:
        if not isinstance(trace, dict):
            continue
        room_id = trace.get("room_id", "")
        room_matches = [entity for entity in room_entities if entity.get("room_source_id") == room_id]
        if not room_matches:
            room_matches = [entity for entity in room_entities
                            if normalise_label(entity.get("label")) == normalise_label(trace.get("room_label"))
                            and normalise_label(entity.get("level_candidate")) == normalise_label(trace.get("level_name"))]
        if not room_matches:
            registry_matches = [row for row in (reviewer_room_geometry or {}).get("rooms", [])
                                if isinstance(row, dict) and row.get("room_id") == room_id]
            if len(registry_matches) == 1:
                registry_room = registry_matches[0]
                room_entity = add_entity(trace.get("page"), registry_room.get("label", trace.get("room_label", "")),
                                         "room", {"id": room_id, "name": registry_room.get("label", ""),
                                                  "level_name": registry_room.get("level_name", ""),
                                                  "room_registry_source": registry_room.get("source", "room_use_resolution")},
                                         "geometry_proposed", "reviewer_room_geometry_room_registry", "reviewer_traced",
                                         location=room_id, unresolved=["independent_review_acceptance"],
                                         level=registry_room.get("level_name", ""))
                room_entity["room_source_id"] = room_id
                room_entities.append(room_entity)
                room_entities_by_source_id[room_id] = room_entity
                room_matches = [room_entity]
        if len(room_matches) != 1:
            continue
        room_entity = room_matches[0]
        room_entity["room_source_id"] = room_id
        page_number = trace.get("page")
        points = trace.get("points_image_px", [])
        if not polygon_is_simple(points) or polygon_area(points) is None:
            continue
        calibration = trace.get("calibration", {})
        mm_per_px = calibration.get("mm_per_px")
        if not isinstance(mm_per_px, (int, float)) or not math.isfinite(mm_per_px) or mm_per_px <= 0:
            mm_per_px = None
        pixel_area = polygon_area(points)
        area_m2 = round(pixel_area * mm_per_px * mm_per_px / 1_000_000.0, 6) if mm_per_px else None
        unresolved = []
        if area_m2 is None:
            unresolved.extend(["scale_calibration", calibration.get("reason", "calibration_unresolved")])
        unresolved.append("independent_review_acceptance")
        trace_id = str(trace.get("trace_id", ""))
        value = {
            "room_source_id": room_entity.get("room_source_id", ""),
            "reviewer_trace_id": trace_id,
            "points_image_px": deepcopy(points),
            "boundary_wall_ids": [item for item in trace.get("snapped_line_ids", []) if item],
            "snapped_line_ids": deepcopy(trace.get("snapped_line_ids", [])),
            "coordinate_units": "image_px",
            "calibration": deepcopy(calibration),
            "area_m2": area_m2,
            "derivation": {"formula": "shoelace_area_px2 × (mm_per_px²) ÷ 1,000,000",
                           "operands": {"polygon_area_px2": pixel_area, "mm_per_px": mm_per_px},
                           "rounding": "6 decimal places"},
            "reviewer": trace.get("reviewer", ""), "note": trace.get("note", ""),
            "source_fingerprints": deepcopy(trace.get("source_fingerprints", {})),
            "calculation_eligibility": "proposal_only_until_separate_review",
        }
        entity = add_entity(page_number, room_entity.get("label", trace.get("room_label", "")),
                            "room_geometry_proof", value, "geometry_proposed", "reviewer_traced_boundary",
                            "reviewer_traced", location=trace_id, unresolved=unresolved,
                            witness_ids=[trace_id], level=room_entity.get("level_candidate", trace.get("level_name", "")))
        entity["room_source_id"] = room_entity.get("room_source_id", "")
        entity["room_entity_id"] = room_entity.get("entity_id", "")
        page_meta = page_by_number.get(page_number, {})
        trace_witness = add_witness(page_number, trace.get("room_label", entity["label"]),
                                    "reviewer_traced_boundary", {"trace_id": trace_id, "note": trace.get("note", "")},
                                    "reviewer_room_geometry", trace.get("reviewer", ""), location=trace_id,
                                    role=page_meta.get("proposed_role"))
        entity["witness_ids"] = [trace_witness["witness_id"]]
        relationships.append({"relationship_id": "reviewer_room_trace_" + fingerprint([trace_id, room_entity["entity_id"]])[:16],
                              "kind": "room_to_reviewer_traced_boundary", "entity_ids": [room_entity["entity_id"], entity["entity_id"]],
                              "pages": [page_number], "basis": "named_reviewer_closed_trace_with_scale_crosscheck",
                              "status": "proposed", "independent_witness": False, "cross_check_only": False})
        room_geometry_proofs.append({"proof_id": entity["entity_id"], "room_geometry_id": trace_id,
                                     "room_source_id": room_entity.get("room_source_id", ""),
                                     "room_label": entity["label"], "level_name": entity["level_candidate"],
                                     "source_page": page_number, "coordinate_units": "image_px",
                                     "boundary_points_px": deepcopy(points), "wall_ids": value["boundary_wall_ids"],
                                     "snapped_line_ids": deepcopy(trace.get("snapped_line_ids", [])),
                                     "calibration": deepcopy(calibration), "area_m2": area_m2,
                                     "status": "geometry_proposed", "reviewer": trace.get("reviewer", ""),
                                     "unresolved_fields": unresolved})
        if area_m2 is None:
            calibration_reason = calibration.get("reason", "calibration_unresolved")
            calibration_remediation = {
                "declared_scale_missing": "A declared plan scale is required for this calibration. Add or confirm the page scale and a matching printed dimension.",
                "dimension_disagrees_with_declared_scale": "The printed dimension disagrees with the declared scale. Add a second printed dimension; both reviewer measurements must agree within 2%.",
                "reviewer_dimensions_disagree": "The two reviewer-measured dimensions disagree by more than 2%. Recheck the selected spans and printed values.",
                "page_pixel_to_point_ratio_missing": "The rendered page size could not be matched to its PDF page. Rebuild the page render and vector geometry before calibration.",
            }
            review_items.append({"item_id": "geometry_issue_" + fingerprint([trace_id, "calibration"])[:16],
                                 "affected_id": room_id, "status": "blocked", "field": "geometry_area",
                                 "source_artifact": "reviewer_room_geometry", "page": page_number,
                                 "reason": "Traced room boundary is retained, but calibration is unresolved: " + str(calibration_reason),
                                 "remediation": calibration_remediation.get(calibration_reason, "Review the calibration inputs and provide a valid declared scale and printed dimension.")})

    # A page with no scale can still contribute labels and dimensions, but it
    # cannot produce a derived area or confirmed boundary.
    for page in page_register:
        if page.get("proposed_role") in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan"} and not page.get("scale_candidates"):
            ai_scale_available = any(
                entity.get("geometry_status") == "ai_estimated"
                and entity.get("source", {}).get("page") == page.get("page")
                and entity.get("value", {}).get("scale_source") in {"ai_cited_scale", "dimension_wall_link"}
                for entity in ai_geometry_entities
            )
            if ai_scale_available:
                continue
            review_items.append({
                "item_id": "geometry_issue_" + fingerprint(["scale", page.get("page")])[:16],
                "affected_id": page.get("page"), "status": "blocked", "field": "scale",
                "source_artifact": "geometry_resolution", "page": page.get("page"),
                "reason": "Plan geometry is available, but a proven scale/calibration witness is missing.",
                "remediation": "Provide a printed scale or calibration witness before deriving room geometry or area.",
            })

    thermal_surface_ledger = resolve_thermal_surfaces(
        vision_response,
        pages,
        source_fp,
        resolution_mode=resolution_mode,
        room_ids=[row.get("id") for row in building.get("spaces", [])],
        zone_ids=[row.get("id") or row.get("zone_id") for row in building.get("zones", [])],
    )
    for issue in thermal_surface_ledger.get("issues", []):
        review_items.append({
            **issue,
            "item_id": issue.get("exception_id") or "thermal_surface_issue_" + fingerprint(issue)[:16],
            "field": issue.get("field", "thermal_surface_classification"),
        })
    relationships = sorted({row["relationship_id"]: row for row in relationships}.values(), key=lambda row: row["relationship_id"])
    conflicts = sorted({row["conflict_id"]: row for row in conflicts}.values(), key=lambda row: row["conflict_id"])
    review_items = sorted({row["item_id"]: row for row in review_items}.values(), key=lambda row: row["item_id"])
    for trace in (reviewer_room_geometry or {}).get("records", []) if isinstance(reviewer_room_geometry, dict) else []:
        if isinstance(trace, dict) and trace.get("page") in deterministic_page_diagnostics:
            deterministic_page_diagnostics[trace["page"]].setdefault("reviewer_trace_count", 0)
            deterministic_page_diagnostics[trace["page"]]["reviewer_trace_count"] += 1
    return {
        "schema_version": 2,
        "resolution_mode": resolution_mode,
        "source_fingerprint": source_fp,
        "evidence_fingerprint": evidence_fp,
        "pages": sorted(page_register, key=lambda row: (row.get("page") is None, row.get("page"))),
        "entities": sorted(entities, key=lambda row: row["entity_id"]),
        "witnesses": sorted(witnesses, key=lambda row: row["witness_id"]),
        "relationships": relationships,
        "conflicts": conflicts,
        "review_items": review_items,
        "room_geometry_proofs": sorted(room_geometry_proofs, key=lambda row: row.get("proof_id", "")),
        "deterministic_proof_diagnostics": {
            "pages": [deterministic_page_diagnostics[key] for key in sorted(deterministic_page_diagnostics)],
            "rooms": room_diagnostics,
        },
        "thermal_surface_ledger": thermal_surface_ledger,
        "status": "blocked" if conflicts or any(item.get("status") == "blocked" for item in review_items) else "review_required",
        "summary": {
            "page_count": len(page_register),
            "entity_count": len(entities),
            "witness_count": len(witnesses),
            "relationship_count": len(relationships),
            "conflict_count": len(conflicts),
            "review_item_count": len(review_items),
            "three_d_crosscheck_pages": [row.get("page") for row in render_pages],
            "confirmed_room_geometry_count": len([row for row in entities if row.get("kind") in {"room_geometry_proof", "ai_room_geometry"} and row.get("geometry_status") == "geometry_confirmed"]),
            "ai_estimated_room_geometry_count": len([row for row in entities if row.get("kind") == "ai_room_geometry" and row.get("geometry_status") == "ai_estimated"]),
            "ai_geometry_candidate_count": len(ai_geometry_entities),
            "room_geometry_proof_count": len(room_geometry_proofs),
            "active_room_area_count": len([row for row in entities if row.get("kind") == "area" and row.get("geometry_status") in {"ai_estimated", "geometry_confirmed"}]),
            "thermal_surface_count": thermal_surface_ledger["summary"]["surface_count"],
            "thermal_surface_eligible_count": thermal_surface_ledger["summary"]["thermal_eligible_count"],
        },
    }
