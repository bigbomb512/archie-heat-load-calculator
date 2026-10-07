"""Validated, proposal-only reviewer traces for room boundary geometry."""

from copy import deepcopy
import hashlib
import json
import math

from ai.geometry_resolution import polygon_area, polygon_is_simple, valid_point

SCHEMA_VERSION = 1
# Image-coordinate snap radius: this is only a UI convenience, not proof that a
# candidate is a room boundary. Keep it small on full-resolution plan renders.
SNAP_TOLERANCE_PX = 8.0
EDGE_BOUNDARIES = {"external", "mall", "adjacent_tenancy", "internal", "unknown"}
ROOF_EXPOSURES = {"exposed", "not_exposed", "unknown"}
NORTH_SOURCES = {"reviewer_read_north_arrow", "reviewer_typed_page_up_bearing"}
DECLARATION_SOURCES = {"reviewer", "ai_determined", "ai_fallback"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_artifact():
    artifact = {"schema_version": SCHEMA_VERSION, "records": []}
    artifact["fingerprint"] = fingerprint(artifact)
    return artifact


def validate_envelope_classification(edges, roof, edge_count):
    if not isinstance(edge_count, int) or edge_count < 1:
        raise ValueError("The traced room must contain at least one polygon edge.")
    if edges is None:
        edges = []
    if not isinstance(edges, list):
        raise ValueError("Envelope edges must be a list.")
    values = {index: "unknown" for index in range(edge_count)}
    seen = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Each envelope edge classification must be an object.")
        index = edge.get("index")
        boundary = str(edge.get("boundary", ""))
        if type(index) is not int or not 0 <= index < edge_count:
            raise ValueError("Envelope edge index must identify an edge in the traced polygon.")
        if index in seen:
            raise ValueError("Envelope edge indices must be unique.")
        if boundary not in EDGE_BOUNDARIES:
            raise ValueError("Envelope edge boundary must be external, mall, adjacent_tenancy, internal or unknown.")
        seen.add(index)
        values[index] = boundary
    roof = str(roof)
    if roof not in ROOF_EXPOSURES:
        raise ValueError("Roof exposure must be exposed, not_exposed or unknown.")
    return {"edges": [{"index": index, "boundary": values[index]} for index in range(edge_count)], "roof": roof}


def page_up_bearing_from_north_arrow(points):
    """Return true-north bearing of image-up from clicked tail-to-tip arrow points."""
    if not isinstance(points, list) or len(points) != 2 or not all(valid_point(point) for point in points):
        raise ValueError("Choose two points along the north arrow, from tail to tip.")
    dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
    if math.hypot(dx, dy) <= 0:
        raise ValueError("North-arrow points must be distinct.")
    return (-math.degrees(math.atan2(dx, -dy))) % 360


def polygon_outward_side(points):
    """Outward side of each ordered edge for image coordinates (y down)."""
    signed_twice_area = sum(points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1]
                            for i in range(len(points) - 1))
    if abs(signed_twice_area) <= 1e-9:
        raise ValueError("Cannot orient edges of a zero-area trace.")
    return "left" if signed_twice_area > 0 else "right"


def oriented_edge_cardinal(points, edge_index, plan_up_azimuth_deg):
    from ai.site_orientation import cardinal, outward_azimuth
    if type(edge_index) is not int or not 0 <= edge_index < len(points) - 1:
        raise ValueError("Edge index is outside the traced polygon.")
    side = polygon_outward_side(points)
    azimuth = outward_azimuth([points[edge_index], points[edge_index + 1]], side, plan_up_azimuth_deg)
    return cardinal(azimuth), azimuth


def validate_artifact(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("records", []), list):
        raise ValueError("Reviewer room geometry must contain a records list.")
    records = []
    seen = set()
    for row in raw.get("records", []):
        if not isinstance(row, dict):
            raise ValueError("Every room geometry trace must be an object.")
        trace_id = str(row.get("trace_id", "")).strip()
        room_id = str(row.get("room_id", "")).strip()
        page = row.get("page")
        points = row.get("points_image_px")
        if not trace_id or trace_id in seen:
            raise ValueError("Room geometry trace IDs must be present and unique.")
        seen.add(trace_id)
        if not room_id:
            raise ValueError("Room geometry trace needs a room ID.")
        if not isinstance(page, int) or page <= 0:
            raise ValueError("Room geometry trace needs a positive source page.")
        if not isinstance(points, list) or len(points) < 4 or not all(valid_point(point) for point in points):
            raise ValueError("Room boundary must contain at least three finite vertices and a closing point.")
        points = [[float(value) for value in point] for point in points]
        if points[0] != points[-1]:
            raise ValueError("Room boundary polygon must be explicitly closed.")
        if not polygon_is_simple(points):
            raise ValueError("Room boundary polygon self-intersects.")
        if polygon_area(points) is None:
            raise ValueError("Room boundary polygon has zero area.")
        snapped = row.get("snapped_line_ids", [])
        if not isinstance(snapped, list) or len(snapped) != len(points):
            raise ValueError("Each room-boundary vertex needs a snapped line ID or null.")
        if snapped[0] != snapped[-1]:
            raise ValueError("The closing vertex must use the same snap reference as the first vertex.")
        calibration = row.get("calibration")
        if not isinstance(calibration, dict):
            raise ValueError("Room geometry trace needs a calibration record.")
        dim_points = calibration.get("dimension_points_image_px")
        if not isinstance(dim_points, list) or len(dim_points) != 2 or not all(valid_point(point) for point in dim_points):
            raise ValueError("Calibration needs two finite points marking the printed dimension span.")
        dim_value = calibration.get("dimension_value_mm")
        if not isinstance(dim_value, (int, float)) or not math.isfinite(dim_value) or dim_value <= 0:
            raise ValueError("Printed calibration dimension must be positive millimetres.")
        declaration_source = row.get("declaration_source", "reviewer")
        allowed_calibration_sources = {"reviewer": "reviewer_read_printed_dimension",
                                       "ai_determined": "ai_read_printed_dimension",
                                       "ai_fallback": "ai_read_printed_dimension"}
        if calibration.get("source") != allowed_calibration_sources.get(declaration_source):
            raise ValueError("Calibration source must match the trace declaration source.")
        ai_measured_area = row.get("ai_measured_area_m2")
        if ai_measured_area is not None:
            if (declaration_source not in {"ai_determined", "ai_fallback"}
                    or not isinstance(ai_measured_area, (int, float)) or isinstance(ai_measured_area, bool)
                    or not math.isfinite(ai_measured_area) or ai_measured_area <= 0):
                raise ValueError("AI-measured room area must be positive and belong to an AI-determined trace.")
        if str(row.get("reviewer", "")).strip() == "":
            raise ValueError("Room geometry trace requires a reviewer name.")
        if not isinstance(row.get("source_fingerprints"), dict):
            raise ValueError("Room geometry trace needs source fingerprints.")
        checked = deepcopy(row)
        edge_count = len(points) - 1
        classification = validate_envelope_classification(checked.get("edges", []), checked.get("roof", "unknown"), edge_count)
        checked.update(classification)
        roof_source = checked.get("roof_source")
        if roof_source is not None:
            if roof_source not in DECLARATION_SOURCES or checked["roof"] == "unknown":
                raise ValueError("A roof source must identify a classified roof value.")
            checked["roof_source"] = roof_source
        else:
            checked.pop("roof_source", None)
        edge_sources = checked.get("edge_sources")
        if edge_sources is not None:
            if not isinstance(edge_sources, dict):
                raise ValueError("Envelope edge sources must be an object keyed by edge index.")
            sources = {}
            boundaries = {edge["index"]: edge["boundary"] for edge in checked["edges"]}
            for key, source in edge_sources.items():
                if not str(key).isdigit() or int(key) not in boundaries or boundaries[int(key)] == "unknown":
                    raise ValueError("An edge source must identify a classified envelope edge.")
                if source not in DECLARATION_SOURCES:
                    raise ValueError("Envelope edge source must be reviewer, ai_determined or ai_fallback.")
                sources[str(int(key))] = source
            if sources:
                checked["edge_sources"] = sources
            else:
                checked.pop("edge_sources", None)
        else:
            checked.pop("edge_sources", None)
        declaration_reviewer = str(checked.get("envelope_reviewer", "")).strip()
        declared_at = str(checked.get("envelope_declared_at", "")).strip()
        has_classification = checked["roof"] != "unknown" or any(edge["boundary"] != "unknown" for edge in checked["edges"])
        if has_classification and (not declaration_reviewer or not declared_at):
            raise ValueError("Envelope classifications require a reviewer and declaration timestamp.")
        checked["envelope_reviewer"] = declaration_reviewer
        checked["envelope_declared_at"] = declared_at
        declaration_source = checked.get("declaration_source")
        ai_run_id = checked.get("ai_run_id")
        if declaration_source is not None and declaration_source not in DECLARATION_SOURCES:
            raise ValueError("Envelope declaration source must be reviewer, ai_determined or ai_fallback.")
        if ai_run_id is not None and (not isinstance(ai_run_id, str) or not ai_run_id.strip()):
            raise ValueError("AI envelope declarations need a non-empty run ID.")
        if declaration_source is not None:
            checked["declaration_source"] = declaration_source
        else:
            checked.pop("declaration_source", None)
        if ai_run_id is not None:
            checked["ai_run_id"] = ai_run_id.strip()
        else:
            checked.pop("ai_run_id", None)
        openings = checked.get("openings", [])
        if not isinstance(openings, list):
            raise ValueError("Trace openings must be a list.")
        opening_ids = set()
        for opening in openings:
            if not isinstance(opening, dict) or not str(opening.get("opening_id", "")).strip():
                raise ValueError("Each trace opening needs an ID.")
            if opening["opening_id"] in opening_ids or type(opening.get("edge_index")) is not int or not 0 <= opening["edge_index"] < edge_count:
                raise ValueError("Trace opening IDs and edge references must be unique and valid.")
            opening_ids.add(opening["opening_id"])
        openings_none_edges = checked.get("openings_none_edges", [])
        if not isinstance(openings_none_edges, list):
            raise ValueError("Edges with no glazing must be a list.")
        if (any(type(index) is not int or not 0 <= index < edge_count for index in openings_none_edges)
                or len(set(openings_none_edges)) != len(openings_none_edges)):
            raise ValueError("Edges with no glazing must contain unique valid edge indices.")
        if openings_none_edges:
            edge_values = {edge["index"]: edge["boundary"] for edge in checked["edges"]}
            opening_edges = {opening["edge_index"] for opening in openings}
            if any(edge_values.get(index) not in {"external", "mall"} for index in openings_none_edges):
                raise ValueError("No-glazing decisions can only be recorded on external or mall walls.")
            if set(openings_none_edges) & opening_edges:
                raise ValueError("A wall cannot have both a window and a no-glazing decision.")
            checked["openings_none_edges"] = sorted(openings_none_edges)
        else:
            checked.pop("openings_none_edges", None)
        records.append(checked)
    page_north = raw.get("page_north", {})
    if not isinstance(page_north, dict):
        raise ValueError("Page north declarations must be an object keyed by page number.")
    checked_north = {}
    for key, declaration in page_north.items():
        if not isinstance(declaration, dict):
            raise ValueError("Each page north declaration must be an object.")
        page = declaration.get("page")
        if not str(key).isdigit() or type(page) is not int or page <= 0 or int(key) != page:
            raise ValueError("Page north declaration needs a matching positive page number.")
        bearing = declaration.get("plan_up_azimuth_deg")
        if type(bearing) not in (int, float) or not math.isfinite(bearing) or not 0 <= bearing < 360:
            raise ValueError("Plan-up bearing must be between 0 and under 360 degrees.")
        if declaration.get("source") not in NORTH_SOURCES:
            raise ValueError("Page north source must be a reviewer-read arrow or typed bearing.")
        if not str(declaration.get("reviewer", "")).strip() or not str(declaration.get("declared_at", "")).strip():
            raise ValueError("Page north declarations need a reviewer and timestamp.")
        declaration_source = declaration.get("declaration_source")
        ai_run_id = declaration.get("ai_run_id")
        if declaration_source is not None and declaration_source not in DECLARATION_SOURCES:
            raise ValueError("North declaration source must be reviewer, ai_determined or ai_fallback.")
        if ai_run_id is not None and (not isinstance(ai_run_id, str) or not ai_run_id.strip()):
            raise ValueError("AI north declarations need a non-empty run ID.")
        arrow = declaration.get("north_arrow_points_image_px")
        if declaration["source"] == "reviewer_read_north_arrow":
            if not isinstance(arrow, list) or len(arrow) != 2 or not all(valid_point(point) for point in arrow) or math.dist(*arrow) <= 0:
                raise ValueError("North-arrow declarations need two distinct image points.")
        checked_declaration = deepcopy(declaration)
        if declaration_source is None:
            checked_declaration.pop("declaration_source", None)
        if ai_run_id is None:
            checked_declaration.pop("ai_run_id", None)
        checked_north[str(page)] = checked_declaration
    result = {"schema_version": SCHEMA_VERSION, "records": records}
    if checked_north:
        result["page_north"] = checked_north
    rooms = validate_reviewer_rooms(raw.get("rooms", []))
    if rooms:
        # Only present once a reviewer adds a room, so existing artifacts keep
        # their fingerprints.
        result["rooms"] = rooms
    # Card I's explicit defaults are useful to downstream consumers, but are
    # not new evidence and must not change a legacy record's fingerprint.
    fingerprint_basis = deepcopy(result)
    for record in fingerprint_basis["records"]:
        if not any(edge["boundary"] != "unknown" for edge in record.get("edges", [])):
            record.pop("edges", None)
        if record.get("roof") == "unknown":
            record.pop("roof", None)
        if not record.get("envelope_reviewer"):
            record.pop("envelope_reviewer", None)
        if not record.get("envelope_declared_at"):
            record.pop("envelope_declared_at", None)
        if not record.get("roof_source"):
            record.pop("roof_source", None)
        if not record.get("edge_sources"):
            record.pop("edge_sources", None)
        if not record.get("openings_none_edges"):
            record.pop("openings_none_edges", None)
    result["fingerprint"] = fingerprint(fingerprint_basis)
    return result


def validate_reviewer_rooms(raw):
    """Rooms a reviewer added because detection could not find them.

    Each room needs a label, level, controlled room use and reviewer; the room
    ID is the room-use identity so traces, room-use, the area gate and the
    room confirmation all refer to the same room.
    """
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise ValueError("Reviewer-added rooms must be a list.")
    rooms, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Every reviewer-added room must be an object.")
        room_id = str(row.get("room_id", "")).strip()
        label = str(row.get("label", "")).strip()
        if not room_id or room_id in seen:
            raise ValueError("Reviewer-added room IDs must be present and unique.")
        if not label or len(label) > 60:
            raise ValueError("A reviewer-added room needs a name of at most 60 characters.")
        if not str(row.get("level_name", "")).strip():
            raise ValueError("A reviewer-added room needs a level.")
        if not str(row.get("taxonomy_id", "")).strip():
            raise ValueError("A reviewer-added room needs a room use.")
        if not str(row.get("reviewer", "")).strip():
            raise ValueError("A reviewer-added room needs a reviewer name.")
        if row.get("source") != "reviewer_added":
            raise ValueError("Reviewer-added rooms must have source reviewer_added.")
        seen.add(room_id)
        rooms.append(deepcopy(row))
    return rooms


def calibration(dimension_points, dimension_value_mm, scale_denominator, image_px_per_pt,
                second_dimension=None, tolerance=0.02):
    """Compare a reviewer-measured print dimension with the declared page scale."""
    distance = math.dist(dimension_points[0], dimension_points[1])
    if not math.isfinite(distance) or distance <= 0:
        return {"status": "unresolved", "reason": "calibration_span_zero_length", "mm_per_px": None}
    measured = float(dimension_value_mm) / distance
    result = {
        "source": "reviewer_read_printed_dimension", "dimension_points_image_px": deepcopy(dimension_points),
        "dimension_value_mm": float(dimension_value_mm), "measured_mm_per_px": measured,
        "scale_denominator": scale_denominator, "consistency_tolerance": tolerance,
    }
    if not isinstance(scale_denominator, (int, float)) or not math.isfinite(scale_denominator) or scale_denominator <= 0:
        return {**result, "status": "unresolved", "reason": "declared_scale_missing", "mm_per_px": None,
                "difference_percent": None}
    if not isinstance(image_px_per_pt, (int, float)) or not math.isfinite(image_px_per_pt) or image_px_per_pt <= 0:
        return {**result, "status": "unresolved", "reason": "page_pixel_to_point_ratio_missing", "mm_per_px": None,
                "difference_percent": None}
    declared = float(scale_denominator) * (25.4 / 72.0) / float(image_px_per_pt)
    difference = abs(measured - declared) / declared
    result.update({"declared_mm_per_px": declared, "difference_percent": difference * 100.0})
    if difference <= tolerance:
        return {**result, "status": "agreed", "reason": "dimension_agrees_with_declared_scale", "mm_per_px": measured}
    if not isinstance(second_dimension, dict):
        return {**result, "status": "unresolved", "reason": "dimension_disagrees_with_declared_scale", "mm_per_px": None}
    points = second_dimension.get("points_image_px")
    value = second_dimension.get("value_mm")
    if not isinstance(points, list) or len(points) != 2 or not all(valid_point(point) for point in points) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return {**result, "status": "unresolved", "reason": "second_dimension_invalid", "mm_per_px": None}
    second_distance = math.dist(points[0], points[1])
    if second_distance <= 0:
        return {**result, "status": "unresolved", "reason": "second_dimension_span_zero_length", "mm_per_px": None}
    second_measured = float(value) / second_distance
    cross_difference = abs(measured - second_measured) / ((measured + second_measured) / 2.0)
    result["second_dimension"] = {"points_image_px": deepcopy(points), "value_mm": float(value), "measured_mm_per_px": second_measured,
                                   "difference_from_first_percent": cross_difference * 100.0}
    if cross_difference > tolerance:
        return {**result, "status": "unresolved", "reason": "reviewer_dimensions_disagree", "mm_per_px": None}
    override = (measured + second_measured) / 2.0
    return {**result, "status": "declared_scale_rejected", "reason": "two_reviewer_dimensions_agree_declared_scale_rejected",
            "mm_per_px": override}


def trace_is_current(row, source_pdf_fingerprint, vector_page_fingerprint):
    sources = row.get("source_fingerprints", {}) if isinstance(row, dict) else {}
    return (sources.get("source_pdf") == source_pdf_fingerprint
            and sources.get("vector_page") == vector_page_fingerprint)


def active_records(artifact, source_pdf_fingerprint, vector_pages):
    """Return only records whose PDF and page geometry have not changed."""
    rows = []
    by_page = {row.get("page"): row for row in vector_pages if isinstance(row, dict)}
    for row in (artifact or {}).get("records", []):
        page = by_page.get(row.get("page"), {})
        if trace_is_current(row, source_pdf_fingerprint, fingerprint(page)):
            rows.append(deepcopy(row))
    return rows
