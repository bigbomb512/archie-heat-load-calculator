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


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_artifact():
    artifact = {"schema_version": SCHEMA_VERSION, "records": []}
    artifact["fingerprint"] = fingerprint(artifact)
    return artifact


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
        if calibration.get("source") != "reviewer_read_printed_dimension":
            raise ValueError("Calibration source must be reviewer_read_printed_dimension.")
        if str(row.get("reviewer", "")).strip() == "":
            raise ValueError("Room geometry trace requires a reviewer name.")
        if not isinstance(row.get("source_fingerprints"), dict):
            raise ValueError("Room geometry trace needs source fingerprints.")
        records.append(deepcopy(row))
    result = {"schema_version": SCHEMA_VERSION, "records": records}
    rooms = validate_reviewer_rooms(raw.get("rooms", []))
    if rooms:
        # Only present once a reviewer adds a room, so existing artifacts keep
        # their fingerprints.
        result["rooms"] = rooms
    result["fingerprint"] = fingerprint(result)
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
