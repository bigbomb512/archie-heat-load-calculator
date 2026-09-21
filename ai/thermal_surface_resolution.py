"""AI-proposed thermal-surface classification and deterministic eligibility.

The vision model interprets the drawing set; this module validates references,
classifies evidence, and produces the normalized surface ledger consumed by
the envelope workflow.  It never invents construction, U-values, or boundary
temperatures.
"""

from copy import deepcopy
import hashlib
import json
import math


PHYSICAL_TYPES = {
    "wall", "roof", "floor", "ceiling", "partition", "glazing",
    "shaft", "column", "non_surface",
}
THERMAL_ROLES = {
    "external", "ground_contact", "fixed_adjacent", "room_to_room",
    "roof_void", "ceiling_below_roof", "internal_floor", "unresolved",
}
BOUNDARIES = {
    "outside", "ground", "conditioned_space", "unconditioned_space",
    "corridor", "plant_room", "roof_void", "unresolved",
}
STATUSES = {"proposed", "ai_estimated", "reviewed", "blocked", "excluded"}
PRIMARY_ONLY_ROLES = {"external", "ground_contact", "room_to_room", "fixed_adjacent", "roof_void", "ceiling_below_roof", "internal_floor"}
NON_SURFACE_CLASSES = {"shaft", "column", "non_surface"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def stable_surface_id(source_fp, page, level, physical_type, geometry, label=""):
    return "surface_" + fingerprint([source_fp, page, level, physical_type, geometry, label])[:24]


def _finite_point(point):
    return isinstance(point, (list, tuple)) and len(point) == 2 and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in point
    )


def _closed_simple(points):
    if not isinstance(points, list) or len(points) < 4 or points[0] != points[-1]:
        return False
    return all(_finite_point(point) for point in points)


def _page_map(pages):
    return {page.get("page"): page for page in pages if isinstance(page, dict)}


def _candidate_rows(vision_response):
    result = (vision_response or {}).get("result", {}) if isinstance(vision_response, dict) else {}
    layered = result.get("layered_geometry", {}) if isinstance(result, dict) else {}
    rows = []
    for page in layered.get("pages", []) if isinstance(layered, dict) else []:
        for raw in page.get("thermal_surface_candidates", []) or []:
            if isinstance(raw, dict):
                rows.append({"page": page.get("page"), "page_role": page.get("page_role", ""), **deepcopy(raw)})
    return rows


def _role_requires_boundary(role):
    return role in {"external", "ground_contact", "fixed_adjacent", "room_to_room", "roof_void", "ceiling_below_roof"}


def resolve_thermal_surfaces(vision_response, pages, source_fp, resolution_mode="engineering_reviewed", room_ids=None, zone_ids=None):
    """Return a deterministic thermal-surface ledger and review issues."""
    room_ids = {str(value) for value in (room_ids or []) if value}
    zone_ids = {str(value) for value in (zone_ids or []) if value}
    page_by_number = _page_map(pages or [])
    ledger, issues, seen = [], [], set()
    for raw in _candidate_rows(vision_response):
        page = raw.get("page")
        level = str(raw.get("level_name") or raw.get("level") or raw.get("floor") or "")
        physical = str(raw.get("physical_type") or raw.get("classification") or "unresolved").casefold()
        role = str(raw.get("thermal_role") or "unresolved").casefold()
        boundary = str(raw.get("boundary_condition") or raw.get("boundary") or "unresolved").casefold()
        geometry = raw.get("boundary_points_px") or raw.get("points_px") or raw.get("geometry") or []
        label = str(raw.get("label") or raw.get("surface_label") or "")
        ai_surface_id = str(raw.get("surface_id") or "")
        surface_id = stable_surface_id(source_fp, page, level, physical, geometry, label)
        row = {
            "surface_id": surface_id,
            "ai_surface_id": ai_surface_id,
            "physical_type": physical,
            "thermal_role": role,
            "boundary_condition": boundary,
            "label": label,
            "page": page,
            "page_role": raw.get("page_role", page_by_number.get(page, {}).get("proposed_role", "")),
            "level_name": level,
            "owner_room_id": str(raw.get("owner_room_id") or raw.get("room_id") or ""),
            "owner_zone_id": str(raw.get("owner_zone_id") or raw.get("zone_id") or ""),
            "adjacent_room_id": str(raw.get("adjacent_room_id") or ""),
            "adjacent_space_id": str(raw.get("adjacent_space_id") or raw.get("adjacent_space") or ""),
            "geometry": deepcopy(geometry),
            "wall_ids": [str(value) for value in raw.get("wall_ids", []) if value],
            "opening_ids": [str(value) for value in raw.get("opening_ids", []) if value],
            "evidence_refs": deepcopy(raw.get("evidence_refs") or raw.get("source_pages") or []),
            "source_crop": raw.get("source_crop", ""),
            "confidence": raw.get("confidence", "low"),
            "confidence_score": raw.get("confidence_score"),
            "assumptions": deepcopy(raw.get("assumptions", [])),
            "conflicts": deepcopy(raw.get("conflicts", [])),
            "construction_id": str(raw.get("construction_id") or ""),
            "u_value_w_m2k": raw.get("u_value_w_m2k"),
            "boundary_temperature_c": raw.get("boundary_temperature_c"),
            "source_fingerprint": source_fp,
            "ai_fingerprint": fingerprint(raw),
            "status": "proposed",
            "thermal_eligible": False,
            "unresolved_fields": [],
            "remediation": "",
        }
        reasons = []
        if surface_id in seen:
            reasons.append("duplicate_surface_id")
        seen.add(surface_id)
        if physical not in PHYSICAL_TYPES:
            reasons.append("unsupported_physical_type")
        if role not in THERMAL_ROLES:
            reasons.append("unsupported_thermal_role")
        if boundary not in BOUNDARIES:
            reasons.append("unsupported_boundary_condition")
        if not isinstance(page, int) or page not in page_by_number:
            reasons.append("unknown_source_page")
        if raw.get("page_role") in {"3d_render", "3d_reference", "legend_or_general_notes", "reference", "detail"}:
            reasons.append("non_primary_surface_evidence")
        if physical in NON_SURFACE_CLASSES or physical == "non_surface":
            row["status"] = "excluded"
            row["remediation"] = "Retain as cross-check evidence; do not treat this item as a thermal surface."
        if role == "unresolved":
            reasons.append("thermal_role_unresolved")
        if _role_requires_boundary(role) and boundary == "unresolved":
            reasons.append("boundary_condition_unresolved")
        if role in {"room_to_room", "fixed_adjacent"} and not (row["adjacent_room_id"] or row["adjacent_space_id"]):
            reasons.append("adjacent_space_missing")
        if row["owner_room_id"] and room_ids and row["owner_room_id"] not in room_ids:
            reasons.append("unknown_owner_room")
        if row["owner_zone_id"] and zone_ids and row["owner_zone_id"] not in zone_ids:
            reasons.append("unknown_owner_zone")
        if physical in {"wall", "roof", "floor", "ceiling", "partition", "glazing"} and geometry and not _closed_simple(geometry) and not row["wall_ids"]:
            reasons.append("invalid_surface_geometry")
        if not row["evidence_refs"]:
            reasons.append("source_evidence_missing")
        if row["conflicts"]:
            reasons.append("classification_conflict")
        high = str(row["confidence"]).casefold() == "high" or (
            isinstance(row["confidence_score"], (int, float)) and row["confidence_score"] >= 0.8
        )
        if not reasons and resolution_mode == "preliminary_ai_estimate" and high:
            row["status"] = "ai_estimated"
        elif not reasons and resolution_mode == "engineering_reviewed" and raw.get("independent_witnesses"):
            row["status"] = "reviewed"
        elif row["status"] != "excluded":
            row["status"] = "blocked" if reasons else "proposed"
        properties_missing = []
        if physical not in NON_SURFACE_CLASSES:
            for field in ("construction_id", "u_value_w_m2k"):
                if row[field] in (None, ""):
                    properties_missing.append(field)
            if role == "ground_contact" and row["boundary_temperature_c"] in (None, ""):
                properties_missing.append("boundary_temperature_c")
            elif _role_requires_boundary(role) and row["boundary_temperature_c"] in (None, "") and boundary not in {"outside", "ground"}:
                properties_missing.append("boundary_temperature_c")
        row["unresolved_fields"] = sorted(set(reasons + properties_missing))
        if row["status"] in {"ai_estimated", "reviewed"} and not properties_missing:
            row["thermal_eligible"] = True
        elif row["status"] in {"ai_estimated", "reviewed"} and properties_missing:
            row["remediation"] = "Add cited construction, U-value, and boundary-temperature inputs before calculation."
        elif row["status"] == "blocked":
            row["remediation"] = "Resolve the classification, ownership, boundary, or source-evidence blockers."
        ledger.append(row)
        if row["status"] in {"blocked", "proposed"}:
            issues.append({
                "exception_id": "thermal_surface_issue_" + fingerprint([source_fp, surface_id, row["unresolved_fields"]])[:16],
                "affected_id": surface_id, "field": "thermal_surface_classification", "status": "blocked",
                "source_artifact": "geometry_resolution", "page": page,
                "reason": "; ".join(row["unresolved_fields"]) or "surface requires review",
                "remediation": row["remediation"] or "Provide cited cross-page classification evidence.",
            })
    ledger.sort(key=lambda row: row["surface_id"])
    issues.sort(key=lambda row: row["exception_id"])
    return {
        "schema_version": 1,
        "source_fingerprint": source_fp,
        "resolution_mode": resolution_mode,
        "surfaces": ledger,
        "issues": issues,
        "fingerprint": fingerprint({"source_fingerprint": source_fp, "resolution_mode": resolution_mode, "surfaces": ledger}),
        "summary": {
            "surface_count": len(ledger),
            "thermal_eligible_count": len([row for row in ledger if row["thermal_eligible"]]),
            "blocked_count": len([row for row in ledger if row["status"] == "blocked"]),
            "proposed_count": len([row for row in ledger if row["status"] == "proposed"]),
            "excluded_count": len([row for row in ledger if row["status"] == "excluded"]),
        },
    }
