"""Deterministic value resolution for the draft-only AI preliminary model.

The resolver selects numerical inputs from project sources and released packs.
It deliberately queues external research rather than fetching the internet from a
calculation request.  All candidate values remain draft-only.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import urlparse

from ai.research_cache import approved_source_pack, eligible_bindings, source_domain_allowed
from ai import model_input_resolution as shared_resolution


SCHEMA_VERSION = 1
COUNTRY = "AU"
ORIGINS = {"contractor_override", "project_evidence", "scoped_project_evidence", "released_source_pack", "research_candidate", "ai_estimated", "preliminary_fallback", "derived", "unresolved"}
STATUSES = {"resolved", "provisional", "needs_review", "excluded"}

ROOM_FIELDS = {
    "occupancy_density_people_m2": ("room.occupancy_density_per_m2", "people/m2"),
    "people_sensible_w": ("room.people_sensible_w_per_person", "W/person"),
    "people_latent_w": ("room.people_latent_w_per_person", "W/person"),
    "people_diversity": ("room.people_diversity_factor", ""),
    "lighting_w_m2": ("room.lighting_w_m2", "W/m2"),
    "lighting_diversity": ("room.lighting_diversity_factor", ""),
    "equipment_w_m2": ("room.equipment_w_m2", "W/m2"),
    "equipment_diversity": ("room.equipment_diversity_factor", ""),
    "equipment_space_gain": ("room.equipment_space_gain_factor", ""),
    "outside_air_lps_person": ("room.outside_air_lps_per_person", "L/s/person"),
    "outside_air_lps_m2": ("room.outside_air_lps_per_m2", "L/s/m2"),
    "wall_u_w_m2k": ("room.wall_u_value_w_m2k", "W/m2K"),
    "roof_u_w_m2k": ("room.roof_u_value_w_m2k", "W/m2K"),
    "glazing_u_w_m2k": ("room.glazing_u_value_w_m2k", "W/m2K"),
    "shgc": ("room.glazing_shgc", ""),
}
SCENARIO_FIELDS = {
    "indoor_dry_bulb_c": ("room.indoor_cooling_setpoint_c", "C"),
    "indoor_wet_bulb_c": ("room.indoor_cooling_wet_bulb_c", "C"),
    "safety_factor": ("room.safety_factor", ""),
}
ALLOWED_TARGETS = {target for target, _unit in ROOM_FIELDS.values()} | {target for target, _unit in SCENARIO_FIELDS.values()} | {
    "scenario.weather_profile", "room.ceiling_height_mm", "schedule.profile",
    "room.occupancy_people", "room.lighting_load_w", "room.equipment_load_w",
    "room.people_sensible_w_per_person", "room.people_latent_w_per_person",
    "room.schedule_profile", "room.internal_gains_status",
    "surface.construction_id", "surface.u_value_w_m2k", "surface.boundary_temperature_c",
    "surface.boundary_temperature_profile", "opening.external_shading_factor",
    "opening.u_value_w_m2k", "opening.shgc", "opening.solar_transmission_factor",
    "opening.frame_fraction", "opening.glass_area_m2", "opening.azimuth_deg",
    "airflow.outside_air_lps", "airflow.infiltration", "airflow.process_exhaust_lps",
    "airflow.make_up_air_lps", "airflow.transfer_air_lps", "airflow.supply_lps",
    "ahu.supply_airflow_lps", "ahu.fan_heat_kw", "plant.pump_power_kw", "plant.pipe_effect_kw",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _time(value):
    try:
        return datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _clean_for_fingerprint(value):
    if isinstance(value, dict):
        return {key: _clean_for_fingerprint(item) for key, item in value.items() if key not in {"updated_at", "created_at"}}
    if isinstance(value, list):
        return [_clean_for_fingerprint(item) for item in value]
    return value


def empty_value_resolution():
    result = {
        "schema_version": SCHEMA_VERSION,
        "research_consent": False,
        "research_jobs": [],
        "accepted_research_candidates": [],
        "project_sources": [],
        "overrides": [],
        "records": [],
        "coverage_summary": {},
        "source_fingerprints": {},
        "updated_at": "",
    }
    result["fingerprint"] = fingerprint(_clean_for_fingerprint(result))
    return result


def _text(value):
    return str(value or "").strip()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _confidence(value):
    if isinstance(value, str):
        return {"low": 0.3, "medium": 0.6, "high": 0.85}.get(value.strip().casefold(), 0.4)
    number = _number(value)
    return min(1.0, number) if number is not None else 0.4


def _valid_value(target, value):
    """Keep overrides and project sources within the physical range of V1 fields."""
    if target == "surface.construction_id":
        value = _text(value)
        return value or None
    if target in {"surface.boundary_temperature_profile", "scenario.weather_profile", "schedule.profile", "room.schedule_profile"}:
        return value if isinstance(value, dict) else None
    number = _number(value)
    if number is None:
        return None
    if target in {"room.people_diversity_factor", "room.lighting_diversity_factor",
                  "room.equipment_diversity_factor", "room.equipment_space_gain_factor",
                  "room.glazing_shgc", "opening.external_shading_factor", "opening.shgc",
                  "opening.solar_transmission_factor", "opening.frame_fraction"}:
        return number if number <= 1 else None
    if target == "opening.azimuth_deg":
        return number if number < 360 else None
    if target == "opening.u_value_w_m2k":
        return number if number > 0 else None
    if target == "room.safety_factor":
        return number if number > 0 else None
    if target == "room.ceiling_height_mm":
        return number if number >= 100 else None
    if target.startswith("room.") and target.endswith("_u_value_w_m2k"):
        return number if number > 0 else None
    return number


def _slug(value):
    return "".join(character if character.isalnum() else "-" for character in _text(value).casefold()).strip("-") or "unassigned"


def room_target_id(name, level):
    return f"room:{_slug(level)}:{_slug(name)}"


def _scope_matches(source_scope, requested_scope):
    for key, value in (requested_scope or {}).items():
        if value in (None, ""):
            continue
        available = (source_scope or {}).get(key)
        if available not in (None, "", value):
            return False
    return True


def _citation(source):
    return {
        "source_reference": _text(source.get("source_reference", source.get("url", ""))),
        "publisher": _text(source.get("publisher", "")),
        "citation": _text(source.get("citation", "")),
        "content_hash": _text(source.get("content_hash", "")),
        "excerpt": _text(source.get("excerpt", "")),
    }


def validate_project_source(raw, source_pack_version=""):
    if not isinstance(raw, dict):
        raise ValueError("Project source must be an object.")
    target = _text(raw.get("target"))
    target_id = _text(raw.get("target_id", "project"))
    url = _text(raw.get("url", raw.get("source_reference", "")))
    parsed = urlparse(url)
    if target not in ALLOWED_TARGETS:
        raise ValueError("Project source target is unsupported.")
    if not target_id or _valid_value(target, raw.get("value")) is None:
        raise ValueError("Project source needs a target ID and a valid value.")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Project source needs an HTTPS source URL.")
    if not _text(raw.get("publisher")) or not _text(raw.get("citation")) or not _text(raw.get("content_hash")):
        raise ValueError("Project source needs publisher, citation, and content hash.")
    version = _text(raw.get("source_pack_version", source_pack_version))
    if not version or not approved_source_pack(version) or not source_domain_allowed(url, version):
        raise ValueError("Project source URL is not in an approved source pack domain.")
    scope = raw.get("scope", {})
    if not isinstance(scope, dict):
        raise ValueError("Project source scope must be an object.")
    retrieved_at = _text(raw.get("retrieved_at")) or now()
    expiry = _text(raw.get("expiry"))
    if not _time(retrieved_at) or not _time(expiry) or _time(expiry) <= _time(retrieved_at):
        raise ValueError("Project source needs retrieved_at and a later ISO expiry date.")
    return {
        "source_id": _text(raw.get("source_id")) or "project-source-" + fingerprint({"target": target, "target_id": target_id, "url": url, "content_hash": raw.get("content_hash")})[:16],
        "target": target, "target_id": target_id, "value": _valid_value(target, raw["value"]), "unit": _text(raw.get("unit")),
        "url": url, "source_reference": url, "publisher": _text(raw["publisher"]), "citation": _text(raw["citation"]),
        "content_hash": _text(raw["content_hash"]), "excerpt": _text(raw.get("excerpt")), "scope": deepcopy(scope), "source_pack_version": version,
        "review_status": "accepted_for_preliminary", "retrieved_at": retrieved_at, "expiry": expiry,
        "rationale": _text(raw.get("rationale")),
    }


def validate_value_resolution(raw):
    source = deepcopy(raw or empty_value_resolution())
    if not isinstance(source, dict) or source.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("Unsupported value-resolution artifact schema.")
    source_pack_version = _text(source.get("source_pack_version", ""))
    project_sources = [validate_project_source(item, source_pack_version) for item in source.get("project_sources", [])]
    source_ids = [item["source_id"] for item in project_sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Project source IDs must be unique.")
    overrides = []
    for raw_override in source.get("overrides", []):
        if not isinstance(raw_override, dict):
            raise ValueError("Value-resolution override must be an object.")
        target, target_id = _text(raw_override.get("target")), _text(raw_override.get("target_id"))
        value = _valid_value(target, raw_override.get("value"))
        if target not in ALLOWED_TARGETS or not target_id or value is None:
            raise ValueError("Value-resolution override needs a supported target, target ID, and valid value.")
        overrides.append({"target": target, "target_id": target_id, "value": value, "unit": _text(raw_override.get("unit")),
                          "reason": _text(raw_override.get("reason")), "updated_at": _text(raw_override.get("updated_at")) or now()})
    accepted_candidates = []
    for raw_candidate in source.get("accepted_research_candidates", []):
        if not isinstance(raw_candidate, dict):
            raise ValueError("Accepted research candidate must be an object.")
        target, target_id = _text(raw_candidate.get("target")), _text(raw_candidate.get("target_id", "project"))
        if target not in ALLOWED_TARGETS or not target_id or _valid_value(target, raw_candidate.get("value")) is None:
            raise ValueError("Accepted research candidate needs a supported target, target ID, and valid value.")
        source_reference = _text(raw_candidate.get("source_reference", raw_candidate.get("url", "")))
        if not source_reference.startswith("https://") or not source_domain_allowed(source_reference, source_pack_version):
            raise ValueError("Accepted research candidate must use an HTTPS URL from the configured allowlist.")
        retrieved_at, expiry = _time(raw_candidate.get("retrieved_at")), _time(raw_candidate.get("expiry"))
        if not retrieved_at or not expiry or expiry <= retrieved_at or expiry <= datetime.now(timezone.utc):
            raise ValueError("Accepted research candidate must have a current retrieval and expiry window.")
        if not _text(raw_candidate.get("content_hash")) or not _text(raw_candidate.get("citation")):
            raise ValueError("Accepted research candidate needs a content hash and citation.")
        accepted_candidates.append({**deepcopy(raw_candidate), "target": target, "target_id": target_id, "value": _valid_value(target, raw_candidate.get("value")), "status": "accepted_for_preliminary"})
    jobs = []
    for raw_job in source.get("research_jobs", []):
        if not isinstance(raw_job, dict):
            raise ValueError("Research job must be an object.")
        target, target_id = _text(raw_job.get("target")), _text(raw_job.get("target_id"))
        if target not in ALLOWED_TARGETS or not target_id:
            raise ValueError("Research job needs a supported target and target ID.")
        job = {"job_id": _text(raw_job.get("job_id")) or "research-" + fingerprint({"target": target, "target_id": target_id, "context": raw_job.get("context", {})})[:16],
               "target": target, "target_id": target_id, "context": deepcopy(raw_job.get("context", {})),
               "allowed_domains": sorted({_text(value) for value in raw_job.get("allowed_domains", []) if _text(value)}),
               "status": _text(raw_job.get("status", "queued")) or "queued", "created_at": _text(raw_job.get("created_at")) or now(),
               "reason": _text(raw_job.get("reason"))}
        if not isinstance(job["context"], dict):
            raise ValueError("Research job context must be an object.")
        jobs.append(job)
    records = [shared_resolution.normalize_record(item) for item in source.get("records", []) if isinstance(item, dict)]
    result = {
        "schema_version": SCHEMA_VERSION, "research_consent": bool(source.get("research_consent", False)),
        "source_pack_version": source_pack_version, "research_jobs": jobs, "accepted_research_candidates": accepted_candidates, "project_sources": project_sources,
        "overrides": overrides, "records": records,
        "coverage_summary": deepcopy(source.get("coverage_summary", {})), "source_fingerprints": deepcopy(source.get("source_fingerprints", {})),
        "updated_at": _text(source.get("updated_at")),
    }
    result["fingerprint"] = fingerprint(_clean_for_fingerprint(result))
    return result


def queue_research_job(resolution, target, target_id, context, reason):
    result = validate_value_resolution(resolution)
    if not result["research_consent"]:
        raise ValueError("Approve external source research before queueing a lookup.")
    if target not in ALLOWED_TARGETS or not _text(target_id) or not isinstance(context, dict):
        raise ValueError("Research lookup needs a supported target, target ID, and context.")
    pack = approved_source_pack(result["source_pack_version"])
    if not pack:
        raise ValueError("Select an approved source-pack version before queueing research.")
    candidate = {"target": target, "target_id": target_id, "context": deepcopy(context),
                 "allowed_domains": pack.get("allowed_domains", []), "status": "queued", "reason": _text(reason)}
    candidate["job_id"] = "research-" + fingerprint({key: candidate[key] for key in ("target", "target_id", "context")})[:16]
    result["research_jobs"] = [item for item in result["research_jobs"] if item["job_id"] != candidate["job_id"]] + [candidate]
    result["updated_at"] = now()
    return validate_value_resolution(result)


def accept_research_candidate(resolution, candidate):
    """Accept one already-fetched, allowlisted candidate for this project.

    Fetching remains outside the calculation path.  The candidate must carry
    its citation/content hash so acceptance is auditable and fingerprinted.
    """
    result = validate_value_resolution(resolution)
    if not isinstance(candidate, dict):
        raise ValueError("Research candidate must be an object.")
    checked = deepcopy(candidate)
    checked.setdefault("target_id", "project")
    if not _text(checked.get("source_reference", checked.get("url", ""))).startswith("https://"):
        raise ValueError("Research candidate needs an HTTPS source reference.")
    checked["source_reference"] = _text(checked.get("source_reference", checked.get("url", "")))
    checked["citation"] = _text(checked.get("citation"))
    checked["content_hash"] = _text(checked.get("content_hash"))
    if not checked["citation"] or not checked["content_hash"]:
        raise ValueError("Research candidate needs citation and content hash.")
    if not source_domain_allowed(checked["source_reference"], result.get("source_pack_version", "")):
        raise ValueError("Research candidate source is not in the configured allowlist.")
    checked.setdefault("retrieved_at", now())
    if not checked.get("expiry"):
        raise ValueError("Research candidate needs an expiry date.")
    # Validate through the same target/unit boundary as project sources.
    validate_value_resolution({**result, "accepted_research_candidates": [*result.get("accepted_research_candidates", []), checked]})
    result["accepted_research_candidates"] = [item for item in result.get("accepted_research_candidates", []) if not (item.get("target") == checked.get("target") and item.get("target_id") == checked.get("target_id"))] + [checked]
    result["updated_at"] = now()
    return validate_value_resolution(result)


def add_project_source(resolution, source):
    result = validate_value_resolution(resolution)
    checked = validate_project_source(source, result["source_pack_version"])
    result["project_sources"] = [item for item in result["project_sources"] if item["source_id"] != checked["source_id"]] + [checked]
    result["updated_at"] = now()
    return validate_value_resolution(result)


def add_override(resolution, override):
    result = validate_value_resolution(resolution)
    checked = {"target": _text(override.get("target")), "target_id": _text(override.get("target_id")),
               "value": _valid_value(_text(override.get("target")), override.get("value")), "unit": _text(override.get("unit")), "reason": _text(override.get("reason")), "updated_at": now()}
    if checked["target"] not in ALLOWED_TARGETS or not checked["target_id"] or checked["value"] is None:
        raise ValueError("Override needs a supported target, target ID, and valid value.")
    result["overrides"] = [item for item in result["overrides"] if not (item["target"] == checked["target"] and item["target_id"] == checked["target_id"])] + [checked]
    result["updated_at"] = now()
    return validate_value_resolution(result)


def _project_source(resolution, target, target_id, context):
    rows = [item for item in resolution["project_sources"] if item["target"] == target and item["target_id"] == target_id and _scope_matches(item.get("scope", {}), context)]
    return sorted(rows, key=lambda item: item["source_id"])[0] if rows else None


def _accepted_research_candidate(resolution, target, target_id, context):
    rows = [item for item in resolution.get("accepted_research_candidates", [])
            if item.get("target") == target and item.get("target_id") == target_id
            and _scope_matches(item.get("scope", {}), context)]
    return sorted(rows, key=lambda item: (item.get("retrieved_at", ""), item.get("content_hash", "")), reverse=True)[0] if rows else None


def _override(resolution, target, target_id):
    rows = [item for item in resolution["overrides"] if item["target"] == target and item["target_id"] == target_id]
    return sorted(rows, key=lambda item: item.get("updated_at", ""), reverse=True)[0] if rows else None


def _record(target, target_id, context, *, value=None, unit="", origin, status, rationale, source=None, derivation=None, confidence=0.0):
    item = {
        "record_id": "resolution-" + fingerprint({"target": target, "target_id": target_id, "context": context, "origin": origin, "source": source or {}, "value": value})[:18],
        "target": target, "target_id": target_id, "context": deepcopy(context), "value": value, "unit": unit,
        "origin": origin, "status": status, "rationale": rationale, "confidence": float(confidence),
        "citation": _citation(source or {}), "derivation": deepcopy(derivation or {}),
    }
    return shared_resolution.normalize_record(item, component_ids=[target_id], source_fingerprints=context.get("source_fingerprints", {}))


def _normalise_weather_profile(value):
    """Translate released weather bindings into the preliminary pack shape."""
    if not isinstance(value, dict) or not isinstance(value.get("hours"), list) or len(value["hours"]) != 24:
        return None
    hours = []
    for index, row in enumerate(value["hours"]):
        if not isinstance(row, dict):
            return None
        db = _number(row.get("db", row.get("outdoor_dry_bulb_c")))
        wb = _number(row.get("wb", row.get("outdoor_wet_bulb_c")))
        if db is None or wb is None:
            return None
        hours.append({"db": db, "wb": wb})
    pressure = _number(value.get("pressure_kpa", value.get("atmospheric_pressure_kpa")))
    if pressure is None:
        return None
    return {"hours": hours, "pressure_kpa": pressure}


def _resolve(target, target_id, context, fallback, unit, resolution, cache, release_manifest=None):
    override = _override(resolution, target, target_id)
    if override:
        return _record(target, target_id, context, value=override["value"], unit=override["unit"] or unit,
                       origin="contractor_override", status="resolved", rationale=override["reason"] or "Contractor override.", confidence=1.0)
    project_source = _project_source(resolution, target, target_id, context)
    if project_source:
        return _record(target, target_id, context, value=project_source["value"], unit=project_source["unit"] or unit,
                       origin="project_evidence", status="provisional", rationale=project_source["rationale"] or "Accepted project-scoped external source.", source=project_source, confidence=0.85)
    released = eligible_bindings(cache, target, context, release_manifest=release_manifest)
    if released:
        source = released[0]
        value = _number(source.get("value"))
        if value is not None:
            return _record(target, target_id, context, value=value, unit=_text(source.get("unit")) or unit,
                           origin="released_source_pack", status="resolved", rationale="Current released source-pack value matches inferred context.", source=source, confidence=0.8)
    research = _accepted_research_candidate(resolution, target, target_id, context)
    if research:
        return _record(target, target_id, context, value=research["value"], unit=_text(research.get("unit")) or unit,
                       origin="research_candidate", status="provisional", rationale="Project-accepted allowlisted research candidate matches inferred context.", source=research, confidence=0.7)
    if fallback is not None:
        return _record(target, target_id, context, value=fallback, unit=unit, origin="preliminary_fallback", status="provisional",
                       rationale="Controlled AI preliminary assumption-pack fallback.", confidence=0.4)
    return _record(target, target_id, context, value=None, unit=unit, origin="unresolved", status="excluded",
                   rationale="No compatible project source, released source-pack record, or controlled fallback exists.", confidence=0.0)


def _proposal_rooms(building, proposal):
    by_identity = {}
    for item in (building or {}).get("spaces", []):
        if isinstance(item, dict) and _text(item.get("name")):
            by_identity[room_target_id(item.get("name"), item.get("level_name", "Unassigned level"))] = item
    rows = list((proposal or {}).get("rooms", []))
    if not rows:
        rows = [{"label": item.get("name"), "level_name": item.get("level_name", "Unassigned level"),
                 "preliminary_profile_id": "generic_conditioned_room", "confidence": item.get("confidence", 0.3),
                 "area_m2": item.get("area")} for item in by_identity.values()]
    return rows, by_identity


def _project_context(building, site_location=None):
    """Keep only stable, non-sensitive context used for pack-scope matching."""
    building = building if isinstance(building, dict) else {}
    context = {"country": _text(building.get("country")) or COUNTRY}
    for key in ("locality", "state", "climate_zone", "building_use", "building_type"):
        if _text(building.get(key)):
            context[key] = _text(building[key])
    location = (site_location or {}).get("location", {}) if isinstance(site_location, dict) else {}
    for key in ("locality", "state"):
        if _text(location.get(key)):
            context[key] = _text(location[key])
    return context


def build_value_resolution(pack, building, proposal, research_cache, resolution=None, release_manifest=None,
                           source_fingerprints=None, site_location=None, site_design_weather=None,
                           ceiling_volume_resolution=None, internal_gains_resolution=None,
                           surface_ledger=None, airflow_resolution=None):
    """Build a current resolver artifact and compact value maps for assembly."""
    state = validate_value_resolution(resolution or empty_value_resolution())
    cache = research_cache or {"schema_version": 1, "revision": 0, "source_pack_version": "", "records": []}
    rows, building_by_identity = _proposal_rooms(building, proposal)
    records, room_values, surface_values = [], {}, {}
    profiles = pack.get("profiles", {})
    ceiling_by_room = {row.get("room_id"): row for row in (ceiling_volume_resolution or {}).get("records", [])
                       if isinstance(row, dict) and row.get("status") != "stale"
                       and _number(row.get("ceiling_height_mm")) is not None}
    internal_by_room = {row.get("room_id"): row for row in (internal_gains_resolution or {}).get("records", [])
                        if isinstance(row, dict) and row.get("status") != "stale"}
    fallback_ceiling = _number((pack.get("preliminary_defaults") or {}).get("ceiling_height_mm"))
    for row in rows:
        name, level = _text(row.get("label", row.get("name"))), _text(row.get("level_name", "Unassigned level")) or "Unassigned level"
        if not name:
            continue
        target_id = room_target_id(name, level)
        profile_id = _text(row.get("preliminary_profile_id")) or "generic_conditioned_room"
        profile = profiles.get(profile_id, profiles["generic_conditioned_room"])
        context = {**_project_context(building, site_location), "room_use": profile_id, "building_use": _text((building or {}).get("building_use")) or profile_id,
                   "room_name": name, "level": level}
        source_room = building_by_identity.get(target_id, {})
        area = _number(source_room.get("area", source_room.get("area_m2"))) or _number(row.get("area_m2"))
        records.append(_record("room.area_m2", target_id, context, value=area, unit="m2",
                               origin="project_evidence" if _number(source_room.get("area")) else "preliminary_fallback" if area else "unresolved",
                               status="resolved" if _number(source_room.get("area")) else "provisional" if area else "excluded",
                               rationale="Direct PDF geometry when available; otherwise source-linked AI geometry.", confidence=_confidence(row.get("confidence", 0.4))))
        records.append(_record("room.profile", target_id, context, value=profile_id, unit="profile", origin="project_evidence",
                               status="provisional", rationale="AI-inferred room-use profile; it selects, but does not invent, controlled values.", confidence=_confidence(row.get("confidence", 0.4))))
        values = {}
        for profile_key, (target, unit) in ROOM_FIELDS.items():
            resolved = _resolve(target, target_id, context, profile.get(profile_key), unit, state, cache, release_manifest)
            records.append(resolved)
            if resolved["value"] is not None:
                values[profile_key] = resolved["value"]
        resolved_ceiling = ceiling_by_room.get(target_id)
        if resolved_ceiling:
            source = {"source_reference": "ceiling_volume_resolution.json",
                      "citation": "; ".join(_text(item.get("excerpt")) for item in resolved_ceiling.get("evidence", []) if _text(item.get("excerpt"))),
                      "content_hash": _text(resolved_ceiling.get("record_fingerprint"))}
            ceiling = _record("room.ceiling_height_mm", target_id, context,
                              value=resolved_ceiling["ceiling_height_mm"], unit="mm",
                              origin=resolved_ceiling.get("origin", "unresolved"),
                              status="resolved" if resolved_ceiling.get("status") == "resolved" else "provisional",
                              rationale=resolved_ceiling.get("rationale", "Ceiling-height resolver."), source=source,
                              confidence=_number(resolved_ceiling.get("confidence_score")) or 0.0)
            ceiling["derivation"] = deepcopy(resolved_ceiling.get("derivation", {}))
            ceiling["ceiling_resolution_record_id"] = _text(resolved_ceiling.get("record_fingerprint"))
        else:
            ceiling = _resolve("room.ceiling_height_mm", target_id, context, fallback_ceiling, "mm", state, cache, release_manifest)
        schedule = _resolve("schedule.profile", target_id, context, None, "profile", state, cache, release_manifest)
        if schedule["status"] == "excluded":
            schedule = _record("schedule.profile", target_id, context, value=profile.get("weekday_profile", profile_id), unit="profile",
                               origin="preliminary_fallback", status="provisional", rationale="Controlled preliminary room-use schedule.", confidence=0.4)
        records.extend((ceiling, schedule))
        values["ceiling_height_mm"] = ceiling["value"]
        values["ceiling_height_origin"] = ceiling["origin"]
        values["ceiling_height_confidence"] = ceiling["confidence"]
        values["ceiling_height_rationale"] = ceiling["rationale"]
        values["ceiling_height_evidence"] = deepcopy((resolved_ceiling or {}).get("evidence", []))
        values["volume_derivation"] = deepcopy((resolved_ceiling or {}).get("derivation", {}))
        values["schedule_profile"] = schedule["value"]
        if area is not None and ceiling["value"] is not None:
            records.append(_record("room.volume_m3", target_id, context, value=area * ceiling["value"] / 1000,
                                   unit="m3", origin="derived", status="provisional",
                                   rationale="Room volume derived from resolved area and ceiling height.", confidence=min(0.8, ceiling["confidence"]),
                                   derivation={"formula": "area_m2 × ceiling_height_mm / 1000", "operands": {"area_m2": area, "ceiling_height_mm": ceiling["value"]}}))
        density = values.get("occupancy_density_people_m2")
        if area is not None and density is not None:
            people = max(1, round(area * density))
            records.append(_record("room.occupancy_people", target_id, context, value=people, unit="people", origin="derived",
                                   status="provisional", rationale="Occupancy derived from the resolved density and room area.", confidence=0.6,
                                   derivation={"formula": "max(1, round(area_m2 × occupancy_density_people_m2))",
                                               "operands": {"area_m2": area, "occupancy_density_people_m2": density}}))
            outside_air = people * values.get("outside_air_lps_person", 0) + area * values.get("outside_air_lps_m2", 0)
            records.append(_record("room.outside_air_lps", target_id, context, value=outside_air, unit="L/s", origin="derived",
                                   status="provisional", rationale="Outside air derived from resolved per-person and area rates.", confidence=0.6,
                                   derivation={"formula": "occupancy_people × outside_air_lps_person + area_m2 × outside_air_lps_m2",
                                               "operands": {"occupancy_people": people, "outside_air_lps_person": values.get("outside_air_lps_person", 0),
                                                            "area_m2": area, "outside_air_lps_m2": values.get("outside_air_lps_m2", 0)}}))
        room_values[target_id] = values
        internal = internal_by_room.get(f"room-use:{_slug(level)}:{_slug(name)}")
        if internal:
            internal_fields = internal.get("fields", {})
            internal_target_values = {
                "room.occupancy_people": internal.get("occupancy_count"),
                "room.people_sensible_w_per_person": internal.get("people_sensible_w_per_person"),
                "room.people_latent_w_per_person": internal.get("people_latent_w_per_person"),
                "room.lighting_load_w": internal.get("lighting_load_w"),
                "room.equipment_load_w": sum((item.get("rated_input_w", 0) * item.get("heat_to_space_factor", 0) * item.get("diversity", 0)) for item in internal.get("equipment", []) if isinstance(item, dict)),
                "room.schedule_profile": internal.get("schedule_id"),
                "room.internal_gains_status": internal.get("status"),
            }
            for target, value in internal_target_values.items():
                if value is None:
                    continue
                field = internal_fields.get("lighting_load_w", {}) if target == "room.lighting_load_w" else {}
                origin = field.get("origin", "ai_estimated") if isinstance(field, dict) else "ai_estimated"
                confidence = field.get("confidence", internal.get("confidence_score", 0.3)) if isinstance(field, dict) else internal.get("confidence_score", 0.3)
                records.append(_record(target, target_id, context, value=value, unit="W" if target.endswith("_w") else "profile" if target.endswith("profile") else "people" if target.endswith("people") else "", origin=origin, status="provisional" if origin != "unresolved" else "excluded", rationale="Internal-gains resolver materialized field.", confidence=confidence))
    scenario_values = {}
    scenario_context = {**_project_context(building, site_location), "scenario": "summer"}
    for pack_key, (target, unit) in SCENARIO_FIELDS.items():
        resolved = _resolve(target, "project", scenario_context, pack["scenario"].get(pack_key), unit, state, cache, release_manifest)
        records.append(resolved)
        scenario_values[pack_key] = resolved["value"]
    design_weather_current = (isinstance(site_design_weather, dict) and
                              _text((site_design_weather or {}).get("location_fingerprint")) == _text((site_location or {}).get("fingerprint")) and
                              (site_design_weather or {}).get("status") == "draft_ready")
    selected_design_weather = ((site_design_weather or {}).get("cooling", {}) or {}).get("selected", {}) if design_weather_current else {}
    selected_weather = (site_location or {}).get("selected_weather_source", {}) if isinstance(site_location, dict) else {}
    if isinstance(selected_design_weather, dict) and isinstance(selected_design_weather.get("profile"), dict):
        weather = _record("scenario.weather_profile", "project", scenario_context, value=deepcopy(selected_design_weather.get("profile")),
                          unit="profile", origin="released_source_pack", status="provisional",
                          rationale="Selected current licensed AIRAH cooling design-weather profile matches the confirmed project location and basis.",
                          source=selected_design_weather, confidence=0.85)
    elif isinstance(selected_weather, dict) and selected_weather.get("target") == "scenario.weather_profile":
        weather = _record("scenario.weather_profile", "project", scenario_context, value=deepcopy(selected_weather.get("value")),
                          unit=_text(selected_weather.get("unit")) or "profile", origin="released_source_pack", status="provisional",
                          rationale="Selected current released weather source matches the confirmed project location.",
                          source=selected_weather, confidence=0.8)
    else:
        weather = _resolve("scenario.weather_profile", "project", scenario_context, None, "profile", state, cache, release_manifest)
    if weather["value"] is not None:
        normalised_weather = _normalise_weather_profile(weather["value"])
        if normalised_weather is None:
            weather = _record("scenario.weather_profile", "project", scenario_context, value=None, unit="profile", origin="unresolved", status="excluded",
                              rationale="Selected weather source does not provide a valid 24-hour dry-bulb/wet-bulb profile.", confidence=0.0)
        else:
            weather["value"] = normalised_weather
    if weather["status"] == "excluded":
        weather = _record("scenario.weather_profile", "project", scenario_context, value=deepcopy(pack["scenario"]), unit="profile",
                          origin="preliminary_fallback", status="provisional", rationale="Controlled 24-hour AI preliminary cooling weather profile.", confidence=0.35)
    records.append(weather)
    scenario_values["weather_profile"] = weather["value"]
    # The normalized geometry ledger is authoritative.  Keep the richer
    # preliminary proposal when present, but add any ledger-only surfaces so
    # their construction/U/boundary targets are still represented in the
    # value-resolution artifact.
    surface_rows = list((proposal or {}).get("surfaces", []))
    known_surface_ids = {_text(item.get("candidate_id", item.get("surface_key", item.get("surface_id", ""))))
                         for item in surface_rows if isinstance(item, dict)}
    for surface in (surface_ledger or {}).get("surfaces", []) if isinstance(surface_ledger, dict) else []:
        if not isinstance(surface, dict):
            continue
        surface_id = _text(surface.get("surface_id", surface.get("candidate_id", surface.get("surface_key"))))
        if surface_id and surface_id not in known_surface_ids:
            surface_rows.append({
                "candidate_id": surface_id,
                "label": surface.get("label", surface_id),
                "physical_type": surface.get("physical_type", "wall"),
                "thermal_role": surface.get("thermal_role", "unresolved"),
                "external_exposure": surface.get("external_exposure", ""),
                "orientation": surface.get("orientation", ""),
                "owner_room_label": surface.get("owner_room_label", ""),
                "owner_level_name": surface.get("level_name", ""),
                "gross_area_m2": surface.get("gross_area_m2", surface.get("area_m2")),
                "u_value_w_m2k": surface.get("u_value_w_m2k"),
                "construction_id": surface.get("construction_id", ""),
                "boundary_temperature_c": surface.get("boundary_temperature_c"),
                "evidence": surface.get("evidence_refs", surface.get("citations", [])),
            })
            known_surface_ids.add(surface_id)
    for surface in surface_rows:
        if not isinstance(surface, dict):
            continue
        surface_id = _text(surface.get("candidate_id", surface.get("surface_key", surface.get("label"))))
        if not surface_id:
            continue
        surface_context = {**_project_context(building, site_location), "physical_type": _text(surface.get("physical_type")),
                           "thermal_role": _text(surface.get("thermal_role")), "orientation": _text(surface.get("orientation")),
                           "external_exposure": _text(surface.get("external_exposure"))}
        physical = _text(surface.get("physical_type")) or "wall"
        opaque_fallback = ((pack.get("preliminary_envelope", {}) or {}).get("opaque_constructions", {}) or {}).get(physical)
        if not isinstance(opaque_fallback, dict) and physical == "partition":
            opaque_fallback = ((pack.get("preliminary_envelope", {}) or {}).get("opaque_constructions", {}) or {}).get("wall")
        construction_fallback = _text((opaque_fallback or {}).get("construction_id")) or None
        u_fallback = _number((opaque_fallback or {}).get("u_value_w_m2k"))
        surface_target = "surface:" + _slug(surface_id)
        construction = _resolve("surface.construction_id", surface_target, surface_context, construction_fallback, "assembly", state, cache, release_manifest)
        direct_u = _number(surface.get("u_value_w_m2k"))
        u_value = _record("surface.u_value_w_m2k", surface_target, surface_context, value=direct_u if direct_u is not None else u_fallback,
                          unit="W/m2K", origin="project_evidence" if direct_u is not None else "preliminary_fallback",
                          status="provisional" if direct_u is None else "resolved",
                          rationale="Direct cited surface value when supplied; otherwise controlled preliminary assembly fallback.",
                          confidence=0.85 if direct_u is not None else 0.4)
        boundary_direct = _number(surface.get("boundary_temperature_c"))
        boundary = _record("surface.boundary_temperature_c", surface_target, surface_context, value=boundary_direct,
                           unit="C", origin="project_evidence" if boundary_direct is not None else "unresolved",
                           status="provisional" if boundary_direct is not None else "excluded",
                           rationale="Boundary temperature requires cited evidence or an explicit project override.", confidence=0.85 if boundary_direct is not None else 0.0)
        values = {"construction_id": construction.get("value"), "u_value_w_m2k": u_value.get("value"), "boundary_temperature_c": boundary.get("value")}
        surface_values[surface_id] = values
        records.extend((construction, u_value, boundary))
    shading_fallback = {"unshaded": 1.0, "partial": 0.7, "deep": 0.4}
    for opening in (proposal or {}).get("openings", []):
        if not isinstance(opening, dict):
            continue
        opening_id = _text(opening.get("candidate_id", opening.get("opening_key", opening.get("label"))))
        if not opening_id:
            continue
        category = _text(opening.get("shading_category")).casefold()
        opening_context = {**_project_context(building, site_location), "orientation": _text(opening.get("orientation")),
                           "external_exposure": _text(opening.get("external_exposure")), "shading_category": category}
        records.append(_resolve("opening.external_shading_factor", "opening:" + _slug(opening_id), opening_context,
                               shading_fallback.get(category), "", state, cache, release_manifest))
    # Airflow is resolved by the dedicated air-path resolver.  Copy its
    # field-level provenance into the shared value artifact without replacing
    # the existing ventilation or infiltration calculation engines.
    airflow_targets = {
        "outside_air": ("airflow.outside_air_lps", "L/s"),
        "infiltration": ("airflow.infiltration", "ACH"),
        "process_exhaust": ("airflow.process_exhaust_lps", "L/s"),
        "make_up_air": ("airflow.make_up_air_lps", "L/s"),
        "transfer_air": ("airflow.transfer_air_lps", "L/s"),
        "supply": ("airflow.supply_lps", "L/s"),
    }
    for airflow in (airflow_resolution or {}).get("records", []) if isinstance(airflow_resolution, dict) else []:
        if not isinstance(airflow, dict) or airflow.get("air_path_type") not in airflow_targets:
            continue
        target, unit = airflow_targets[airflow["air_path_type"]]
        origin = airflow.get("origin", "unresolved")
        if origin == "controlled_fallback":
            origin = "preliminary_fallback"
        if origin not in ORIGINS:
            origin = "unresolved"
        value = airflow.get("value")
        records.append(_record(target, airflow.get("owner_room_id", ""),
                               {"room_id": airflow.get("owner_room_id", ""), "air_path_type": airflow.get("air_path_type")},
                               value=value, unit=airflow.get("unit", unit), origin=origin,
                               status="provisional" if airflow.get("status") in {"provisional", "needs_review"} else "resolved" if airflow.get("status") == "resolved" else "excluded",
                               rationale=airflow.get("rationale", "Airflow resolver record."),
                               source={"source_reference": airflow.get("source", ""),
                                      "source_id": airflow.get("source_id", ""),
                                      "citation": "; ".join(_text(item.get("excerpt")) for item in airflow.get("evidence", []) if isinstance(item, dict)),
                                      "excerpt": "; ".join(_text(item.get("excerpt")) for item in airflow.get("evidence", []) if isinstance(item, dict))},
                               derivation={"formula": airflow.get("formula", ""), "operands": airflow.get("operands", {})},
                               confidence=airflow.get("confidence", 0.0)))
    # Non-room systems are deliberately surfaced, not silently modelled from names.
    project_context = {"country": COUNTRY, "building_use": "inferred_project"}
    system_labels = {"ahu.supply_airflow_lps": "AHU supply airflow", "ahu.fan_heat_kw": "AHU fan heat",
                     "plant.pump_power_kw": "Plant pump power", "plant.pipe_effect_kw": "Plant pipe effect"}
    for target in system_labels:
        records.append(_resolve(target, "project", project_context, None, "", state, cache, release_manifest))
    for record in records:
        if record["target"] in system_labels and record["status"] == "excluded":
            record["rationale"] = f"{system_labels[record['target']]} requires explicit air-side or plant evidence."
    state["records"] = sorted(records, key=lambda item: (item["target_id"], item["target"], item["record_id"]))
    shared_register = shared_resolution.build_register(state["records"], dependency_fingerprints=source_fingerprints, updated_at=state.get("updated_at", ""))
    state["records"] = shared_register["records"]
    counts = {status: sum(row["status"] == status for row in state["records"]) for status in sorted(STATUSES)}
    by_category = {}
    for row in state["records"]:
        by_category.setdefault(row.get("target_category", shared_resolution.target_category(row.get("target", ""))), 0)
        by_category[row.get("target_category", shared_resolution.target_category(row.get("target", "")))] += 1
    state["coverage_summary"] = {"total": len(state["records"]), **counts, "by_category": by_category,
                                 "needs_source_lookup": sum(row["status"] == "excluded" for row in state["records"]),
                                 "high_impact_review_count": sum(row["origin"] == "preliminary_fallback" and row["target"] in {"scenario.weather_profile", "room.wall_u_value_w_m2k", "room.roof_u_value_w_m2k", "room.glazing_u_value_w_m2k", "room.glazing_shgc", "room.outside_air_lps_per_person", "room.outside_air_lps_per_m2"} for row in state["records"])}
    state["review_queue"] = shared_register["review_queue"]
    state["affected_component_ids"] = shared_register["affected_component_ids"]
    state["dependency_fingerprints"] = deepcopy(source_fingerprints or {})
    state["source_fingerprints"] = deepcopy(source_fingerprints or {})
    state["updated_at"] = now()
    state = validate_value_resolution(state)
    return state, {"rooms": room_values, "surfaces": surface_values, "scenario": scenario_values}
