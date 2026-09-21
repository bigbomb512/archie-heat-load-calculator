"""Cited site/tenancy alignment and outward façade azimuths.

Address points are lookup candidates only.  Solar-facing angles require a
separate reviewed mapping from an architect host surface to a site façade.
"""

from copy import deepcopy
from datetime import date
import hashlib
import json
import math

METHOD_ID = "reviewed_site_orientation_v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_site_orientation():
    return {"schema_version": 1, "method_id": METHOD_ID, "status": "placeholder", "site_address": "",
            "address_confirmed": False, "state": "", "address_lookup": {}, "selected_address_object_id": None,
            "imagery_lookup": {}, "map_evidence": {},
            "tenancy_id": "", "alignment": {}, "facades": [], "source": "", "citations": [], "fingerprint": ""}


def _point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
        type(part) not in (int, float) or not math.isfinite(part) for part in value
    ):
        raise ValueError(f"{label} needs a finite [x,y] point.")
    return tuple(float(part) for part in value)


def _angle(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 360:
        raise ValueError(f"{label} must be a finite true-north azimuth from 0 to under 360 degrees.")
    return float(value)


def cardinal(azimuth):
    return ("N", "E", "S", "W")[int((azimuth + 45) // 90) % 4]


def outward_azimuth(line_px, outward_side, plan_up_azimuth_deg):
    """Image x points right, y down; side is relative to ordered line."""
    if not isinstance(line_px, (list, tuple)) or len(line_px) != 2:
        raise ValueError("Façade line needs two ordered image points.")
    first, second = _point(line_px[0], "Façade endpoint"), _point(line_px[1], "Façade endpoint")
    dx, dy = second[0] - first[0], second[1] - first[1]
    if math.hypot(dx, dy) < 1e-6 or outward_side not in {"left", "right"}:
        raise ValueError("Façade needs a nonzero line and reviewed outward side.")
    nx, ny = (dy, -dx) if outward_side == "left" else (-dy, dx)
    relative = math.degrees(math.atan2(nx, -ny))
    return round((relative + _angle(plan_up_azimuth_deg, "Plan-up bearing")) % 360, 6)


def _landmark_alignment(rows):
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("Map alignment needs at least two distinct cited tenancy landmarks.")
    points = []
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("landmark_id") or row["landmark_id"] in ids or not row.get("citation"):
            raise ValueError("Map landmarks need distinct IDs and citations.")
        ids.add(row["landmark_id"])
        points.append((_point(row.get("plan_px"), "Plan landmark"), _point(row.get("map_east_north_m"), "Mapped landmark")))
    anchor_plan, anchor_map = points[0]
    bearings = []
    for plan, mapped in points[1:]:
        pdx, pdy = plan[0] - anchor_plan[0], plan[1] - anchor_plan[1]
        mdx, mdy = mapped[0] - anchor_map[0], mapped[1] - anchor_map[1]
        if math.hypot(pdx, pdy) < 1 or math.hypot(mdx, mdy) < 1:
            raise ValueError("Alignment landmarks are not sufficiently distinct.")
        plan_bearing = math.degrees(math.atan2(pdx, -pdy))
        map_bearing = math.degrees(math.atan2(mdx, mdy))
        bearings.append((map_bearing - plan_bearing) % 360)
    base = bearings[0]
    if any(abs((value - base + 180) % 360 - 180) > 5 for value in bearings[1:]):
        raise ValueError("Mapped landmarks yield conflicting plan rotations.")
    return round(base, 6)


def validate_site_orientation(raw):
    if not isinstance(raw, dict):
        raise ValueError("Site orientation must be an object.")
    result = {**empty_site_orientation(), **deepcopy(raw)}
    if result.get("method_id") != METHOD_ID or result.get("status") not in {"placeholder", "proposed", "reviewed", "conflict"}:
        raise ValueError("Unsupported site-orientation method or status.")
    if not isinstance(result.get("facades"), list) or not isinstance(result.get("alignment"), dict):
        raise ValueError("Site orientation needs alignment and façade records.")
    alignment = result["alignment"]
    plan_bearing = None
    if alignment.get("kind") in {"north_arrow", "survey_bearing"}:
        if not alignment.get("source") or not alignment.get("citations"):
            raise ValueError("North-arrow/survey alignment needs a cited source.")
        plan_bearing = _angle(alignment.get("plan_up_azimuth_deg"), "Plan-up bearing")
    elif alignment.get("kind") == "mapped_landmarks":
        if not alignment.get("source") or not alignment.get("citations"):
            raise ValueError("Landmark alignment needs a cited map or survey.")
        plan_bearing = _landmark_alignment(alignment.get("landmarks"))
    elif alignment:
        raise ValueError("Unsupported plan-to-map alignment basis.")
    facades, seen_hosts, seen_openings = [], set(), set()
    for row in result["facades"]:
        if not isinstance(row, dict) or not row.get("host_surface_id") or row["host_surface_id"] in seen_hosts:
            raise ValueError("Each façade needs one unique host surface ID.")
        seen_hosts.add(row["host_surface_id"])
        openings = row.get("opening_ids", [])
        if not isinstance(openings, list) or not all(isinstance(value, str) and value for value in openings) or set(openings) & seen_openings:
            raise ValueError("Opening IDs must map to at most one façade.")
        seen_openings.update(openings)
        exposure = row.get("exposure", "unresolved")
        if exposure not in {"external", "internal", "unresolved"}:
            raise ValueError("Façade exposure must be external, internal or unresolved.")
        facade = deepcopy(row)
        facade["status"] = "proposed"
        facade["azimuth_deg"] = None
        facade["cardinal"] = ""
        facade["issues"] = []
        if plan_bearing is None:
            facade["issues"].append("cited plan-to-true-north alignment is missing")
        if not result.get("tenancy_id") or row.get("tenancy_id") != result["tenancy_id"]:
            facade["issues"].append("exact tenancy ownership is unconfirmed")
        landmarks = row.get("mapping_landmark_ids")
        if (not row.get("source") or not row.get("citations") or not isinstance(landmarks, list)
                or len(set(landmarks)) < 2 or any(not isinstance(value, str) or not value for value in landmarks)):
            facade["issues"].append("façade-to-tenancy mapping needs cited distinct landmarks")
        if exposure == "unresolved":
            facade["issues"].append("external versus internal exposure is unresolved")
        if plan_bearing is not None:
            try:
                facade["azimuth_deg"] = outward_azimuth(row.get("line_px"), row.get("outward_side"), plan_bearing)
                facade["cardinal"] = cardinal(facade["azimuth_deg"])
            except ValueError as error:
                facade["issues"].append(str(error))
        map_date = (result.get("map_evidence") or {}).get("imagery_date", "")
        design_date = alignment.get("design_revision_date", "")
        for label, value in (("imagery date", map_date), ("design revision date", design_date)):
            if value:
                try:
                    date.fromisoformat(value)
                except (TypeError, ValueError):
                    facade["issues"].append(f"{label} must be an ISO calendar date")
        if map_date and design_date and map_date < design_date and not row.get("imagery_date_reviewed"):
            facade["issues"].append("aerial imagery predates design revision; confirm unchanged tenancy façade")
        if result["status"] == "reviewed" and not facade["issues"] and row.get("review_status") == "confirmed":
            facade["status"] = "reviewed"
        elif result["status"] == "conflict" or row.get("conflicts"):
            facade["status"] = "conflict"
        facades.append(facade)
    result["facades"] = sorted(facades, key=lambda row: row["host_surface_id"])
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key not in {"fingerprint", "updated_at"}})
    return result


def facade_for_opening(orientation, opening_id, host_surface_id):
    for facade in orientation.get("facades", []):
        if facade.get("host_surface_id") == host_surface_id and opening_id in facade.get("opening_ids", []):
            return facade
    return None
