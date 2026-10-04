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

# Opaque-envelope resolution deliberately lives beside the AI classification
# ledger.  The classification path remains the visual authority; these
# helpers only normalise areas, openings, properties, and boundary inputs.
OPAQUE_TYPES = {"wall", "roof", "floor", "ceiling", "partition"}
OPAQUE_STATUSES = {"proposed", "provisional", "needs_review", "reviewed", "blocked", "excluded"}


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


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value):
    number = _number(value)
    return number if number is not None and number > 0 else None


def _polygon_area(points):
    if not _closed_simple(points):
        return None
    area = abs(sum(points[index][0] * points[index + 1][1] - points[index + 1][0] * points[index][1]
                   for index in range(len(points) - 1))) / 2.0
    return area if math.isfinite(area) and area > 0 else None


def _record_lookup(records):
    rows = records.get("records", []) if isinstance(records, dict) else records
    if not isinstance(rows, list):
        return {}
    return {str(row.get("target_id")): row for row in rows if isinstance(row, dict) and row.get("target_id")}


def _surface_value(value_resolution, surface_id, field):
    rows = value_resolution.get("records", []) if isinstance(value_resolution, dict) else []
    for row in rows:
        if isinstance(row, dict) and row.get("target") == f"surface.{field}" and row.get("target_id") in {f"surface:{surface_id}", surface_id}:
            return row
    # Accept target-specific aliases used by older value-resolution records.
    for row in rows:
        if isinstance(row, dict) and row.get("target_id") in {f"surface:{surface_id}", surface_id} and str(row.get("target", "")).endswith(field):
            return row
    return None


def _override_value(overrides, field):
    """Read either a raw override or the service's provenance-wrapped value."""
    value = (overrides or {}).get(field) if isinstance(overrides, dict) else None
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _construction_fallback(source_pack, physical_type):
    envelope = (source_pack or {}).get("preliminary_envelope", {}) if isinstance(source_pack, dict) else {}
    records = envelope.get("opaque_constructions", {}) if isinstance(envelope, dict) else {}
    value = records.get(physical_type) or records.get("wall" if physical_type == "partition" else physical_type)
    return deepcopy(value) if isinstance(value, dict) else None


def _opening_rows(opening_register):
    if not isinstance(opening_register, dict):
        return {}
    rows = opening_register.get("openings", opening_register.get("records", []))
    if isinstance(rows, dict):
        rows = list(rows.values())
    return {str(row.get("opening_id", row.get("surface_id", row.get("record_id", "")))): row
            for row in rows if isinstance(row, dict) and row.get("opening_id", row.get("surface_id", row.get("record_id", "")))}


def _surface_area(row, ceiling_by_room=None):
    """Resolve area from explicit operands or a metre-based closed polygon."""
    gross = _positive(row.get("gross_area_m2"))
    if gross is not None:
        return gross, {"formula": "explicit gross_area_m2", "operands": {"gross_area_m2": gross}}
    # ``area_m2`` is the legacy single-area field and is treated as gross
    # only when no explicit gross/net pair exists.  Never let a supplied net
    # value silently become the gross opening-subtraction basis.
    area = _positive(row.get("area_m2"))
    if area is not None:
        return area, {"formula": "explicit area_m2", "operands": {"area_m2": area}}
    net = _positive(row.get("net_opaque_area_m2"))
    if net is not None:
        return net, {"formula": "explicit net_opaque_area_m2", "operands": {"net_opaque_area_m2": net}}
    geometry = row.get("geometry") or row.get("boundary_points_m") or []
    if row.get("geometry_units", row.get("units", "")).casefold() in {"m", "metre", "meters", "metres"}:
        area = _polygon_area(geometry)
        if area is not None:
            return area, {"formula": "shoelace(closed_surface_polygon_m)", "operands": {"polygon_m": geometry}}
    width = _positive(row.get("width_m"))
    height = _positive(row.get("height_m"))
    if width is not None and height is not None:
        quantity = _positive(row.get("quantity", 1)) or 1
        return width * height * quantity, {"formula": "width_m × height_m × quantity", "operands": {"width_m": width, "height_m": height, "quantity": quantity}}
    room_id = str(row.get("owner_room_id") or row.get("room_id") or "")
    height_mm = (ceiling_by_room or {}).get(room_id)
    length = _positive(row.get("length_m"))
    if length is not None and _positive(height_mm) is not None:
        return length * float(height_mm) / 1000.0, {"formula": "length_m × ceiling_height_mm ÷ 1000", "operands": {"length_m": length, "ceiling_height_mm": float(height_mm)}}
    return None, {}


def _resolve_surface_area(row, openings, ceiling_by_room=None):
    gross, derivation = _surface_area(row, ceiling_by_room)
    if gross is None:
        return None, None, {}, ["surface_area_unresolved"]
    linked_ids = [str(value) for value in row.get("opening_ids", row.get("linked_opening_ids", [])) if value]
    coverage = str(row.get("opening_coverage_status", row.get("opening_coverage", "not_applicable"))).casefold()
    if coverage == "complete":
        coverage = "confirmed"
    if not linked_ids and isinstance(row.get("openings"), list):
        linked_ids = [str(item.get("opening_id", item.get("surface_id", ""))) for item in row["openings"] if isinstance(item, dict) and item.get("opening_id", item.get("surface_id"))]
    if not linked_ids and coverage in {"not_applicable", "none", "confirmed_no_openings"}:
        return gross, gross, {**derivation, "gross_area_m2": gross, "net_area_m2": gross}, []
    if not linked_ids:
        net = _positive(row.get("net_opaque_area_m2"))
        if net is not None:
            return gross, net, {**derivation, "gross_area_m2": gross, "net_area_m2": net}, []
        return gross, None, derivation, ["opening_coverage_missing"]
    if coverage not in {"confirmed", "reviewer_entered"}:
        return gross, None, derivation, ["opening_coverage_incomplete"]
    area_rows, missing = [], []
    for opening_id in linked_ids:
        opening = openings.get(opening_id)
        if not opening:
            missing.append(opening_id)
            continue
        area = _positive(opening.get("opening_area_m2", opening.get("area_m2")))
        if area is None:
            width, height = _positive(opening.get("width_m")), _positive(opening.get("height_m"))
            area = width * height * (_positive(opening.get("quantity", 1)) or 1) if width and height else None
        if area is None:
            missing.append(opening_id)
        else:
            area_rows.append({"opening_id": opening_id, "opening_area_m2": area})
    if missing:
        return gross, None, derivation, ["opening_geometry_missing:" + ",".join(sorted(missing))]
    opening_total = sum(item["opening_area_m2"] for item in area_rows)
    net = gross - opening_total
    if net <= 0:
        return gross, None, derivation, ["opening_area_exceeds_gross_area"]
    return gross, net, {**derivation, "gross_area_m2": gross, "net_area_m2": net, "openings": area_rows,
                        "formula": "gross_surface_area − sum(confirmed_opening_areas)"}, []


def resolve_opaque_envelope(ledger, opening_register=None, value_resolution=None, source_pack=None,
                            resolution_mode="engineering_reviewed", ceiling_by_room=None,
                            contractor_overrides=None, weather_available=False):
    """Resolve opaque ledger rows into calculation-ready records.

    This function never discovers geometry. It validates and normalises the
    AI-owned ledger, resolves controlled properties, and fails closed for
    incomplete opening coverage or non-outdoor boundary temperatures.
    """
    ledger = deepcopy(ledger or {})
    rows = ledger.get("surfaces", []) if isinstance(ledger, dict) else []
    openings = _opening_rows(opening_register)
    overrides = contractor_overrides or {}
    result, issues, seen = [], [], set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        surface_id = str(item.get("surface_id", "")).strip()
        physical = str(item.get("physical_type", item.get("kind", ""))).casefold()
        if physical == "opaque_wall":
            physical = "wall"
        if surface_id in seen:
            item["status"] = "blocked"
            item.setdefault("unresolved_fields", []).append("duplicate_surface_id")
        seen.add(surface_id)
        item["physical_type"] = physical
        item.setdefault("origin", "ai_geometry")
        item.setdefault("citations", deepcopy(item.get("evidence_refs", [])))
        item.setdefault("area_basis", "resolved_geometry")
        item.setdefault("opening_coverage_status", item.get("opening_coverage", "not_applicable"))
        item.setdefault("linked_opening_ids", list(item.get("opening_ids", [])))
        item.setdefault("boundary_method", "external" if item.get("thermal_role") == "external" else "")
        blockers = list(item.get("unresolved_fields", []))
        if physical not in OPAQUE_TYPES:
            item["status"] = "excluded"
            item["thermal_eligible"] = False
            item["exclusion_reason"] = "non_opaque_surface"
            result.append(item)
            continue
        override = overrides.get(surface_id, {}) if isinstance(overrides, dict) else {}
        # Apply overrides before any derived area/boundary/property work so
        # the resolver, service, and preliminary assembler share the same
        # precedence semantics.
        for field in ("gross_area_m2", "net_opaque_area_m2", "opening_coverage_status",
                      "linked_opening_ids", "boundary_temperature_c", "boundary_temperature_profile",
                      "construction_id", "u_value_w_m2k"):
            value = _override_value(override, field)
            if value is not None:
                item[field] = deepcopy(value)
        gross, net, derivation, area_issues = _resolve_surface_area(item, openings, ceiling_by_room)
        item["gross_area_m2"], item["net_opaque_area_m2"], item["area_derivation"] = gross, net, derivation
        blockers.extend(area_issues)
        if net is not None:
            item["area_m2"] = net
        construction = item.get("construction_id")
        u_value = _positive(item.get("u_value_w_m2k"))
        source = "contractor_override" if any(_override_value(override, field) is not None for field in ("construction_id", "u_value_w_m2k", "boundary_temperature_c", "boundary_temperature_profile")) else item.get("construction_source", "")
        construction_record = _surface_value(value_resolution, surface_id, "construction_id")
        if construction_record and not construction:
            construction = construction_record.get("value")
            source = construction_record.get("origin", "value_resolution")
        value_record = _surface_value(value_resolution, surface_id, "u_value_w_m2k")
        if value_record and u_value is None:
            u_value = _positive(value_record.get("value"))
            source = value_record.get("origin", "value_resolution")
        fallback = _construction_fallback(source_pack, physical)
        if u_value is None and fallback and resolution_mode == "preliminary_ai_estimate":
            construction = construction or fallback.get("construction_id", f"preliminary-{physical}")
            u_value = _positive(fallback.get("u_value_w_m2k"))
            source = "preliminary_fallback"
            item.setdefault("assumptions", []).append(fallback.get("construction_id", f"preliminary-{physical}"))
        if not construction:
            blockers.append("construction_id_missing")
        if u_value is None:
            blockers.append("u_value_missing")
        boundary = str(item.get("boundary_condition", "")).casefold()
        role = str(item.get("thermal_role", "")).casefold()
        if role == "external" or boundary == "outside":
            item["boundary_method"] = "external"
            if not weather_available and resolution_mode == "engineering_reviewed":
                # Reviewed models may still use the normal selected design-day
                # weather at calculation time; do not require a static value.
                pass
        elif role == "ground_contact" or boundary == "ground":
            item["boundary_method"] = "ground_contact"
            if item.get("boundary_temperature_c") in (None, "") and not item.get("boundary_temperature_profile"):
                blockers.append("ground_temperature_missing")
        elif role in {"fixed_adjacent", "roof_void", "ceiling_below_roof"} or boundary in {"corridor", "plant_room", "roof_void", "unconditioned_space"}:
            item["boundary_method"] = "fixed_adjacent_temperature"
            if item.get("boundary_temperature_c") in (None, "") and not item.get("boundary_temperature_profile"):
                boundary_record = _surface_value(value_resolution, surface_id, "boundary_temperature_c")
                if boundary_record and _number(boundary_record.get("value")) is not None:
                    item["boundary_temperature_c"] = boundary_record["value"]
                else:
                    blockers.append("boundary_temperature_missing")
        elif role == "room_to_room":
            item["boundary_method"] = "room_to_room_dynamic"
            if not item.get("adjacent_room_id"):
                blockers.append("adjacent_room_missing")
        else:
            blockers.append("boundary_method_unresolved")
        item["construction_id"], item["u_value_w_m2k"], item["property_origin"] = construction, u_value, source
        item["thermal_eligible"] = not blockers and bool(item.get("owner_room_id") or item.get("owner_zone_id"))
        if item.get("thermal_eligible"):
            item["status"] = "reviewed" if resolution_mode == "engineering_reviewed" and item.get("status") == "reviewed" else "provisional" if resolution_mode == "preliminary_ai_estimate" else item.get("status", "proposed")
        else:
            item["status"] = "blocked" if item.get("status") not in {"excluded"} else item["status"]
        item["unresolved_fields"] = sorted(set(blockers))
        item["resolution_fingerprint"] = fingerprint({key: item.get(key) for key in ("surface_id", "gross_area_m2", "net_opaque_area_m2", "construction_id", "u_value_w_m2k", "boundary_method", "boundary_temperature_c", "linked_opening_ids", "unresolved_fields")})
        if not item["thermal_eligible"]:
            item["remediation"] = "Resolve the affected surface area, opening coverage, construction/U-value, or boundary temperature before calculation."
            issues.append({"exception_id": "opaque_surface_issue_" + fingerprint([surface_id, item["unresolved_fields"]])[:16], "affected_id": surface_id, "field": "opaque_envelope", "status": "blocked", "reason": "; ".join(item["unresolved_fields"]), "remediation": item["remediation"]})
        result.append(item)
    result.sort(key=lambda row: row.get("surface_id", ""))
    summary = {"surface_count": len(result), "included_count": sum(bool(row.get("thermal_eligible")) for row in result),
               "blocked_count": sum(row.get("status") == "blocked" for row in result), "excluded_count": sum(row.get("status") == "excluded" for row in result),
               "complete_project": bool(result) and all(row.get("thermal_eligible") or row.get("status") == "excluded" for row in result)}
    artifact = {"schema_version": 2, "source_fingerprint": ledger.get("source_fingerprint", ""), "resolution_mode": resolution_mode,
                "surfaces": result, "issues": sorted(issues, key=lambda row: row["exception_id"]), "summary": summary}
    artifact["fingerprint"] = fingerprint({key: artifact[key] for key in ("source_fingerprint", "resolution_mode", "surfaces", "issues", "summary")})
    return artifact


# Friendly aliases for service/tests that use the terminology from the plan.
resolve_opaque_envelope_ledger = resolve_opaque_envelope
resolve_surface_values = resolve_opaque_envelope


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
            "construction_source": str(raw.get("construction_source") or raw.get("source", "")),
            "construction_layers": deepcopy(raw.get("construction_layers", raw.get("layers", []))),
            "gross_area_m2": raw.get("gross_area_m2", raw.get("area_m2")),
            "net_opaque_area_m2": raw.get("net_opaque_area_m2"),
            "area_basis": str(raw.get("area_basis") or "resolved_geometry"),
            "area_derivation": deepcopy(raw.get("area_derivation", {})),
            "opening_coverage_status": str(raw.get("opening_coverage_status", raw.get("opening_coverage", "not_applicable"))).casefold(),
            "linked_opening_ids": [str(value) for value in raw.get("linked_opening_ids", raw.get("opening_ids", [])) if value],
            "boundary_method": str(raw.get("boundary_method") or ""),
            "boundary_temperature_source": str(raw.get("boundary_temperature_source") or ""),
            "boundary_temperature_profile": deepcopy(raw.get("boundary_temperature_profile", {})),
            "property_origin": str(raw.get("property_origin") or ""),
            "citations": deepcopy(raw.get("citations", [])),
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
