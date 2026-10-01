"""Opening-to-solar normalization for AI-proposed glazing evidence.

The window scan remains the visual discovery layer and the reviewed envelope
model remains the calculation authority.  This module adds the small,
deterministic bridge between them: geometry/provenance, property resolution,
orientation/exposure, shading, and hourly solar accounting.
"""

from copy import deepcopy
import hashlib
import json
from math import isfinite

from ai.glazing_calculation import corrected_glass_area, glazing_conduction, manual_solar_transmission, opening_area
from ai.solar_radiation import facade_irradiance


SCHEMA_VERSION = 1
EXPOSURES = {"external", "internal", "unresolved"}
STATUSES = {"proposed", "ai_estimated", "provisional", "reviewed", "blocked", "excluded", "conflict"}
SHADING_CATEGORIES = {"unshaded", "partial", "deep"}
SHADING_FACTORS = {
    # Product-owned draft categories.  They are intentionally explicit and
    # never applied when a cited geometric/manual factor is present.
    "unshaded": {"direct": 1.0, "diffuse": 1.0},
    "partial": {"direct": 0.7, "diffuse": 0.9},
    "deep": {"direct": 0.35, "diffuse": 0.75},
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def stable_opening_id(source_fingerprint, level, tag, geometry, location=""):
    """Create an ID independent of provider/evidence array order or name."""
    return "opening_" + fingerprint([source_fingerprint, str(level or "").casefold(), str(tag or "").upper(), geometry, location])[:16]


def _number(value, label="value", positive=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number) or (positive and number <= 0):
        return None
    return number


def _citation_list(value):
    if not isinstance(value, list):
        return []
    return [deepcopy(item) for item in value if isinstance(item, dict) and str(item.get("reference", "")).strip()]


def _property_candidate(row, value_resolution=None, source_pack=None, mode="preliminary_ai_estimate"):
    candidates = []
    for key in ("window_properties", "proposed_window_properties", "glazing_properties", "property_resolution"):
        value = row.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    candidates.append(row)
    opening_id = str(row.get("opening_id", "") or "")
    # Value-resolution records are already precedence-normalized by the
    # project resolver.  Accept them as cited/project/research/fallback
    # proposals, but keep their origin and provenance on the opening record.
    resolution_rows = value_resolution.get("records", []) if isinstance(value_resolution, dict) else []
    by_field = {}
    for resolution in resolution_rows:
        if not isinstance(resolution, dict):
            continue
        target_id = str(resolution.get("target_id", ""))
        if target_id not in {opening_id, "opening:" + opening_id}:
            continue
        target = str(resolution.get("target", ""))
        if target.startswith("opening."):
            by_field[target.rsplit(".", 1)[-1]] = resolution
    if by_field:
        candidate = {
            "u_value_w_m2k": by_field.get("u_value_w_m2k", {}).get("value"),
            "shgc": by_field.get("shgc", {}).get("value"),
            "solar_transmission_factor": by_field.get("solar_transmission_factor", {}).get("value"),
            "source": next((row.get("source", row.get("source_reference", "")) for row in by_field.values() if row.get("source") or row.get("source_reference")), ""),
            "citations": next((row.get("citations", []) for row in by_field.values() if row.get("citations")), []),
            "origin": next((row.get("origin", "") for row in by_field.values() if row.get("origin")), "released_source_pack"),
            "rationale": next((row.get("rationale", "") for row in by_field.values() if row.get("rationale")), ""),
            "source_fingerprint": next((row.get("source_fingerprint", "") for row in by_field.values() if row.get("source_fingerprint")), ""),
        }
        candidates.append(candidate)
    # The product-owned pack is a last-resort draft category.  It may be
    # selected by room profile, but never used for engineering-reviewed mode.
    pack = source_pack if isinstance(source_pack, dict) else {}
    profile_id = row.get("room_profile_id", row.get("room_use_profile", row.get("profile_id", "")))
    profile = (pack.get("profiles", {}) or {}).get(profile_id, {}) if profile_id else {}
    if profile and mode != "engineering_reviewed":
        candidates.append({
            "u_value_w_m2k": profile.get("glazing_u_w_m2k"), "shgc": profile.get("shgc"),
            "solar_transmission_factor": None, "source": pack.get("title", "Product-owned preliminary assumption pack"),
            "citations": [], "origin": "preliminary_fallback", "rationale": "Selected controlled room-use glazing profile.",
            "source_fingerprint": fingerprint(pack),
        })
    normalized = []
    for candidate in candidates:
        u_value = _number(candidate.get("u_value_w_m2k"), "U-value", positive=True)
        shgc = _number(candidate.get("shgc"), "SHGC")
        transmission = _number(candidate.get("solar_transmission_factor"), "solar transmission")
        if shgc is not None and not 0 <= shgc <= 1:
            shgc = None
        if transmission is not None and not 0 <= transmission <= 1:
            transmission = None
        if u_value is None and shgc is None and transmission is None:
            continue
        citations = _citation_list(candidate.get("citations", row.get("citations", [])))
        source = str(candidate.get("source", row.get("source", "")) or "").strip()
        visual_only = bool(candidate.get("visual_only") or candidate.get("estimated_from_appearance"))
        origin = str(candidate.get("origin", "") or "").strip() or ("ai_estimated" if visual_only else "project_evidence")
        normalized.append({
            "u_value_w_m2k": u_value, "shgc": shgc, "solar_transmission_factor": transmission,
            "source": source, "citations": citations, "visual_only": visual_only, "origin": origin,
            "rationale": str(candidate.get("rationale", "") or "").strip(),
            "source_fingerprint": str(candidate.get("source_fingerprint", "") or ""),
            "solar_property_conflict": shgc is not None and transmission is not None,
        })
    if normalized:
        selected = {"u_value_w_m2k": None, "shgc": None, "solar_transmission_factor": None,
                    "source": "", "citations": [], "visual_only": False, "origin": "unresolved",
                    "rationale": "", "source_fingerprint": "", "solar_property_conflict": False}
        # Resolve each field by the common precedence order while ensuring only
        # one solar property survives when lower-precedence sources disagree.
        for field in ("u_value_w_m2k", "shgc", "solar_transmission_factor"):
            for candidate in normalized:
                if candidate.get(field) is not None:
                    selected[field] = candidate[field]
                    if not selected["source"]:
                        selected.update({key: candidate[key] for key in ("source", "citations", "origin", "rationale", "source_fingerprint")})
                    selected["visual_only"] = selected["visual_only"] or candidate["visual_only"]
                    break
        # A direct SHGC outranks any lower-precedence transmission record (and
        # vice versa); never expose both as active properties.
        solar_field = next((field for field in ("shgc", "solar_transmission_factor") if selected[field] is not None), None)
        if solar_field == "shgc":
            selected["solar_transmission_factor"] = None
        elif solar_field == "solar_transmission_factor":
            selected["shgc"] = None
        selected["solar_property_count"] = int(selected["shgc"] is not None) + int(selected["solar_transmission_factor"] is not None)
        selected["solar_property_conflict"] = any(candidate.get("solar_property_conflict") for candidate in normalized)
        return selected
    return {"u_value_w_m2k": None, "shgc": None, "solar_transmission_factor": None,
            "solar_property_count": 0, "source": "", "citations": [], "visual_only": False,
            "origin": "unresolved", "rationale": "", "source_fingerprint": "", "solar_property_conflict": False}


def _orientation_for(row, site_orientation):
    if not isinstance(site_orientation, dict):
        return {}
    opening_id = row.get("opening_id", "")
    host_wall = row.get("host_wall_id", row.get("host_surface_id", ""))
    for facade in site_orientation.get("facades", []) or []:
        if not isinstance(facade, dict):
            continue
        if opening_id in (facade.get("opening_ids") or []) or host_wall and facade.get("host_surface_id") == host_wall:
            return facade
    return {}


def _shading_for(row):
    category = str(row.get("shading_category", row.get("shading", "")) or "").strip().casefold()
    direct = _number(row.get("external_shading_factor", row.get("direct_shading_factor")))
    diffuse = _number(row.get("diffuse_shading_factor"))
    internal = _number(row.get("internal_shading_factor"))
    if direct is not None and not 0 <= direct <= 1:
        direct = None
    if diffuse is not None and not 0 <= diffuse <= 1:
        diffuse = None
    if internal is not None and not 0 <= internal <= 1:
        internal = None
    if category not in SHADING_CATEGORIES:
        category = "unshaded"
        category_origin = "draft_fallback"
    else:
        category_origin = "ai_or_project_evidence"
    factors = SHADING_FACTORS[category]
    return {
        "category": category,
        "direct_factor": direct if direct is not None else factors["direct"],
        "diffuse_factor": diffuse if diffuse is not None else factors["diffuse"],
        "internal_factor": internal if internal is not None else (internal if internal is not None else 1.0),
        "origin": category_origin if direct is None and diffuse is None else "cited_or_reviewed_factor",
        "geometry": deepcopy(row.get("shading_geometry", row.get("geometry", {}))) if isinstance(row.get("shading_geometry", row.get("geometry", {})), dict) else {},
        "citations": _citation_list(row.get("shading_citations", row.get("citations", []))),
    }


def resolve_opening_solar_record(row, *, known_pages=None, site_orientation=None,
                                 radiation_source=None, scenario_id="", mode="preliminary_ai_estimate",
                                 value_resolution=None, source_pack=None,
                                 known_room_ids=None, known_wall_ids=None, known_levels=None):
    """Normalize one opening without activating reviewed envelope records."""
    item = deepcopy(row or {})
    issues = list(item.get("unresolved_fields", [])) if isinstance(item.get("unresolved_fields"), list) else []
    opening_id = str(item.get("opening_id", item.get("surface_id", "")) or "").strip()
    if not opening_id:
        issues.append("opening_id is missing")
    page = item.get("page")
    if known_pages is not None and page not in set(known_pages):
        issues.append("source page is missing")
    owner_room = str(item.get("owner_room_id", item.get("room_id", "")) or "").strip()
    host_wall = str(item.get("host_wall_id", item.get("host_surface_id", "")) or "").strip()
    level_name = str(item.get("level_name", item.get("level", "")) or "").strip()
    if not owner_room:
        issues.append("opening owner is unresolved")
    elif known_room_ids is not None and owner_room not in set(known_room_ids):
        issues.append("opening owner room is not in the current topology")
    if not host_wall:
        issues.append("host wall is unresolved")
    elif known_wall_ids is not None and host_wall not in set(known_wall_ids):
        issues.append("host wall is not in the current surface ledger")
    if not level_name:
        issues.append("opening level is unresolved")
    elif known_levels is not None and level_name not in set(known_levels):
        issues.append("opening level is not in the current level register")
    width = _number(item.get("width_m", item.get("opening_width_m")), "opening width", positive=True)
    height = _number(item.get("height_m", item.get("opening_height_m")), "opening height", positive=True)
    quantity = _number(item.get("quantity", item.get("opening_quantity", 1)), "opening quantity", positive=True)
    if quantity is not None and not quantity.is_integer():
        quantity = None
        issues.append("opening quantity must be a whole number")
    if width is None or height is None or quantity is None:
        issues.append("positive opening width, height and quantity are required")
        opening_m2 = None
    else:
        opening_m2 = opening_area(width, height, int(quantity))
    explicit_glass = _number(item.get("explicit_glass_area_m2", item.get("glass_area_m2")), "glass area", positive=True)
    frame_fraction = _number(item.get("frame_fraction"), "frame fraction")
    if frame_fraction is not None and not 0 <= frame_fraction < 1:
        frame_fraction = None
        issues.append("frame fraction must be between 0 and 1")
    glass_m2 = explicit_glass
    glass_formula = "explicit_glass_area_m2"
    if glass_m2 is None and opening_m2 is not None and frame_fraction is not None:
        glass_m2 = opening_m2 * (1 - frame_fraction)
        glass_formula = "opening_area × (1 − frame_fraction)"
    if glass_m2 is None:
        issues.append("glass area or a cited frame fraction is required")

    orientation = _orientation_for(item, site_orientation)
    exposure = str(item.get("external_exposure", item.get("exposure", "")) or "").strip().casefold()
    if exposure not in EXPOSURES:
        exposure = str(orientation.get("exposure", "unresolved") or "unresolved").casefold()
    if exposure not in EXPOSURES:
        exposure = "unresolved"
    azimuth = item.get("azimuth_deg", item.get("facade_azimuth_deg"))
    if azimuth in (None, ""):
        azimuth = orientation.get("azimuth_deg")
    azimuth = _number(azimuth, "façade azimuth")
    if azimuth is not None and not 0 <= azimuth < 360:
        azimuth = None
    facade = str(item.get("facade", item.get("orientation", "")) or "").strip().upper()
    if not facade and orientation.get("cardinal_label"):
        facade = str(orientation["cardinal_label"]).upper()
    if exposure == "external" and azimuth is None and not facade:
        issues.append("external façade azimuth or cited orientation is required for solar")
    if exposure == "unresolved":
        issues.append("external versus internal exposure is unresolved")

    properties = _property_candidate(item, value_resolution=value_resolution, source_pack=source_pack, mode=mode)
    if properties["u_value_w_m2k"] is None:
        issues.append("overall-window U-value is unresolved")
    if properties["solar_property_count"] != 1:
        issues.append("exactly one SHGC or solar-transmission value is required")
    if properties.get("solar_property_conflict"):
        issues.append("a single glazing source cannot supply both SHGC and solar-transmission values")
    if properties["visual_only"] and mode == "engineering_reviewed":
        issues.append("visual-only glazing properties cannot activate reviewed calculation")

    shading = _shading_for(item)
    source = radiation_source if isinstance(radiation_source, dict) else {}
    solar_source_match = bool(source) and (not scenario_id or source.get("scenario_id") == scenario_id)
    solar_issues = []
    if exposure == "internal":
        solar_eligible = False
        solar_issues.append("internal exposure excludes outdoor solar")
    elif exposure != "external":
        solar_eligible = False
        solar_issues.append("external exposure is unresolved")
    elif azimuth is None and not facade:
        solar_eligible = False
        solar_issues.append("façade orientation is unresolved")
    elif not solar_source_match:
        solar_eligible = False
        solar_issues.append("cited solar source or matching design scenario is missing")
    else:
        solar_eligible = True
    if properties["u_value_w_m2k"] is None or opening_m2 is None:
        conduction_eligible = False
        conduction_issues = ["opening geometry or U-value is unresolved"]
    else:
        conduction_eligible = True
        conduction_issues = []
    if glass_m2 is None or properties["solar_property_count"] != 1:
        solar_eligible = False
        solar_issues.append("glass area or solar property is unresolved")

    original_status = str(item.get("status", "proposed"))
    if original_status not in STATUSES:
        original_status = "proposed"
    structural_blockers = [issue for issue in issues if issue in {
        "opening_id is missing", "source page is missing", "positive opening width, height and quantity are required",
        "opening quantity must be a whole number", "opening owner is unresolved", "host wall is unresolved",
        "opening level is unresolved",
    }]
    # The dedicated all-page scan is intentionally a discovery register.  A
    # proposed/unmatched cluster remains proposed until the reviewer supplies
    # room-owned geometry; it is not converted into a blocked calculation
    # record merely because the scan did not claim dimensions.
    discovery_only = str(item.get("source", "")) == "all_page_window_scan" and original_status in {"proposed", "conflict"}
    if structural_blockers and not discovery_only:
        status = "blocked"
    elif mode == "engineering_reviewed" and original_status == "reviewed" and not properties["visual_only"] and not issues:
        status = "reviewed"
    elif properties["visual_only"] or properties["origin"] in {"ai_estimated", "research_candidate", "preliminary_fallback"}:
        status = "provisional" if conduction_eligible or solar_eligible else original_status
    else:
        status = original_status
    result = {
        **item,
        "opening_id": opening_id,
        "owner_room_id": owner_room,
        "host_wall_id": host_wall,
        "level_name": level_name,
        "width_m": width,
        "height_m": height,
        "quantity": int(quantity) if quantity is not None else item.get("quantity"),
        "opening_area_m2": opening_m2,
        "glass_area_m2": glass_m2,
        "glass_area_formula": glass_formula,
        "frame_fraction": frame_fraction,
        "external_exposure": exposure,
        "facade": facade,
        "azimuth_deg": azimuth,
        "orientation_source_fingerprint": orientation.get("orientation_fingerprint", orientation.get("source_fingerprint", "")),
        "properties": properties,
        "u_value_w_m2k": properties["u_value_w_m2k"],
        "shgc": properties["shgc"],
        "solar_transmission_factor": properties["solar_transmission_factor"],
        "shading": shading,
        "solar_source_id": source.get("source_id", "") if solar_source_match else "",
        "solar_source_fingerprint": source.get("fingerprint", "") if solar_source_match else "",
        "opening_solar_status": "eligible" if solar_eligible else "excluded",
        "conduction_status": "eligible" if conduction_eligible else "excluded",
        "solar_unresolved_fields": sorted(set(solar_issues)),
        "conduction_unresolved_fields": sorted(set(conduction_issues)),
        "unresolved_fields": sorted(set(issues + solar_issues + conduction_issues)),
        "status": status,
        "resolution_fingerprint": fingerprint({
            "opening_id": opening_id, "opening_area_m2": opening_m2, "glass_area_m2": glass_m2,
            "properties": properties, "external_exposure": exposure, "facade": facade,
            "azimuth_deg": azimuth, "shading": shading, "solar_source": source.get("fingerprint", ""),
        }),
    }
    return result


def resolve_opening_solar_register(register, *, known_pages=None, site_orientation=None,
                                   radiation_source=None, scenario_id="", mode="preliminary_ai_estimate",
                                   value_resolution=None, source_pack=None,
                                   known_room_ids=None, known_wall_ids=None, known_levels=None):
    """Enrich the existing opening register in stable ID order."""
    source = deepcopy(register or {})
    rows = []
    for row in source.get("openings", []) if isinstance(source, dict) else []:
        rows.append(resolve_opening_solar_record(
            row, known_pages=known_pages, site_orientation=site_orientation,
            radiation_source=radiation_source, scenario_id=scenario_id, mode=mode,
            value_resolution=value_resolution, source_pack=source_pack,
            known_room_ids=known_room_ids, known_wall_ids=known_wall_ids, known_levels=known_levels,
        ))
    source["schema_version"] = max(int(source.get("schema_version", 1) or 1), SCHEMA_VERSION)
    source["openings"] = sorted(rows, key=lambda item: item.get("opening_id", ""))
    source["opening_solar_policy"] = "design_day_weather_facade_v1"
    source["orientation_fingerprint"] = (site_orientation or {}).get("fingerprint", "") if isinstance(site_orientation, dict) else ""
    source["solar_source_fingerprint"] = (radiation_source or {}).get("fingerprint", "") if isinstance(radiation_source, dict) else ""
    source["fingerprint"] = fingerprint({key: value for key, value in source.items() if key != "fingerprint"})
    return source


def calculate_opening_solar(record, solar_source, *, hour, scenario_id, indoor_temperature_c,
                            boundary_temperature_c, solar_basis="weather_facade"):
    """Return separate conduction and direct/diffuse/ground solar components."""
    row = resolve_opening_solar_record(record, radiation_source=solar_source, scenario_id=scenario_id)
    if row.get("external_exposure") == "internal":
        return {"status": "excluded", "reason": "internal exposure excludes outdoor solar", "record": row}
    if row.get("conduction_status") != "eligible":
        return {"status": "blocked", "unresolved_requirements": row.get("conduction_unresolved_fields", []), "record": row}
    conduction = glazing_conduction(row["u_value_w_m2k"], row["opening_area_m2"], boundary_temperature_c, indoor_temperature_c)
    if solar_basis == "manual":
        incident = record.get("incident_solar_w_m2", record.get("solar_design_w_m2"))
        if _number(incident, "incident solar") is None:
            return {"status": "blocked", "unresolved_requirements": ["manual incident solar is unresolved"], "record": row}
        factors = row["shading"]
        solar = manual_solar_transmission(_number(incident, "incident solar"), row["glass_area_m2"], row["shgc"] if row["shgc"] is not None else row["solar_transmission_factor"], factors["direct_factor"], factors["internal_factor"])
        components = {"direct_kw": solar, "sky_diffuse_kw": 0.0, "ground_diffuse_kw": 0.0}
    else:
        orientation = row.get("azimuth_deg") if row.get("azimuth_deg") is not None else row.get("facade")
        incident = facade_irradiance(solar_source, hour, orientation, scenario_id)
        property_value = row["shgc"] if row["shgc"] is not None else row["solar_transmission_factor"]
        area = corrected_glass_area(row["glass_area_m2"], _number(record.get("glass_area_correction", 1.0), "glass-area correction") or 1.0)
        factors = row["shading"]
        direct = incident["direct_w_m2"] * area * property_value * factors["direct_factor"] * factors["internal_factor"] / 1000
        diffuse_factor = factors["diffuse_factor"] * factors["internal_factor"]
        sky = incident["sky_diffuse_w_m2"] * area * property_value * diffuse_factor / 1000
        ground = incident["ground_diffuse_w_m2"] * area * property_value * diffuse_factor / 1000
        components = {"direct_kw": round(direct, 6), "sky_diffuse_kw": round(sky, 6), "ground_diffuse_kw": round(ground, 6), "facade_irradiance": incident}
        solar = round(direct + sky + ground, 6)
    return {
        "status": "calculated", "review_status": "provisional" if row.get("status") in {"provisional", "ai_estimated"} else row.get("status"),
        "opening_id": row.get("opening_id", ""), "opening_area_m2": row.get("opening_area_m2"), "glass_area_m2": row.get("glass_area_m2"),
        "conduction_kw": conduction, "solar_kw": solar, "components": components,
        "formulas": {"conduction": "U-value × opening area × ΔT ÷ 1000", "solar": "glass area × solar property × incident components × shading ÷ 1000"},
        "source_fingerprints": {"opening": row.get("resolution_fingerprint", ""), "solar": (solar_source or {}).get("fingerprint", "")},
    }


# Descriptive aliases for callers and external integrations.
normalize_opening_solar_register = resolve_opening_solar_register
validate_opening_solar_record = resolve_opening_solar_record
