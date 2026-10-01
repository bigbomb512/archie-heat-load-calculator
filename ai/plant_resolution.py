"""Draft AI resolution of plant systems and hydraulic circuits.

The resolver is deliberately separate from the reviewed Stage 10 artifacts.
It normalizes source-linked AI proposals, retains unresolved candidates, and
materializes an isolated preliminary plant model without changing reviewed
plant systems or circuits.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SCHEMA_VERSION = 1
PLANT_TYPES = {"chiller", "boiler", "package_unit"}
CIRCUIT_TYPES = {"chilled_water", "heating_water", "refrigerant", "hydraulic"}
DUTY_BASES = {"combined_equipment", "representative_per_unit"}
STATUSES = {"proposed", "provisional", "resolved", "needs_review", "blocked", "excluded", "stale"}
ORIGINS = {"project_evidence", "ai_evidence", "released_source_pack", "research_candidate", "contractor_override", "controlled_fallback", "unresolved"}
DAY_TYPES = ("weekday", "saturday", "sunday", "holiday")


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def empty_plant_resolution():
    result = {
        "schema_version": SCHEMA_VERSION,
        "systems": [], "circuits": [], "mappings": [], "pumps": [], "pipe_effects": [],
        "issues": [], "overrides": [], "source_fingerprints": {}, "status": "needs_review", "updated_at": "",
    }
    result["summary"] = {"systems": 0, "circuits": 0, "mappings": 0, "provisional_systems": 0, "blocked_systems": 0, "deferred_systems": 0, "issues": 0}
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def _text(value):
    return str(value or "").strip()


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _confidence(value, fallback=.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return max(0.0, min(1.0, float(value)))
    return {"high": .85, "medium": .65, "low": .4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= .8 else "medium" if value >= .5 else "low"


def stable_id(kind, source_fp, tag="", page="", anchor="", owner=""):
    seed = [kind, source_fp, _text(tag), _text(page), _text(anchor), _text(owner)]
    return f"{kind}_{fingerprint(seed)[:18]}"


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"


def _citation_rows(item):
    if not isinstance(item, dict):
        return []
    raw = item.get("evidence", item.get("citations", []))
    if isinstance(raw, dict):
        raw = [raw]
    rows = []
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        page = row.get("page")
        if isinstance(page, bool):
            continue
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                continue
        rows.append({"page": page, "drawing_number": _text(row.get("drawing_number")), "reference": _text(row.get("reference")), "excerpt": _text(row.get("excerpt"))})
    if not rows and item.get("page") is not None:
        rows.append({"page": item.get("page"), "drawing_number": _text(item.get("drawing_number")), "reference": _text(item.get("source", "AI plant evidence")), "excerpt": _text(item.get("excerpt", item.get("label", "")))})
    return rows


def _origin(item, fallback="unresolved"):
    value = _text(item.get("origin")) if isinstance(item, dict) else ""
    if value in ORIGINS:
        return value
    if isinstance(item, dict) and item.get("contractor_override"):
        return "contractor_override"
    if isinstance(item, dict) and item.get("research_candidate"):
        return "research_candidate"
    return "ai_evidence" if _citation_rows(item) else fallback


def _flow_lps(item):
    if not isinstance(item, dict):
        return None, {"value": None, "unit": "L/s", "conversion": "missing"}
    raw = item.get("flow_lps", item.get("flow_value", item.get("flow", item.get("value"))))
    value = _num(raw)
    if value is None or value < 0:
        return None, {"value": raw, "unit": item.get("flow_unit", item.get("unit", "L/s")), "conversion": "invalid"}
    unit = _text(item.get("flow_unit", item.get("unit", "L/s"))).casefold().replace("³", "3").replace(" ", "")
    factors = {"l/s": 1.0, "ls": 1.0, "m3/s": 1000.0, "m3s": 1000.0, "m3/h": 1000.0 / 3600.0, "m3h": 1000.0 / 3600.0}
    if unit not in factors:
        return None, {"value": value, "unit": unit, "conversion": "unsupported"}
    return value * factors[unit], {"value": value, "unit": unit, "conversion": "identity" if factors[unit] == 1 else f"multiply by {factors[unit]} to L/s"}


def _schedule(value, fallback=None):
    fallback = fallback or [1.0] * 24
    source = value if isinstance(value, dict) else {}
    result = {}
    for day in DAY_TYPES:
        values = source.get(day, fallback)
        if not isinstance(values, list) or len(values) != 24 or any(_num(item) is None or not 0 <= float(item) <= 1 for item in values):
            values = fallback
        result[day] = [float(item) for item in values]
    return result


def _profile(pack, plant_type, circuit_type):
    profiles = (pack or {}).get("plant_profiles", {}) if isinstance(pack, dict) else {}
    return profiles.get(plant_type) or profiles.get(circuit_type) or profiles.get("default") or {}


def _candidate_sources(vision, proposal):
    candidates = []
    for source in (vision, proposal):
        if not isinstance(source, dict):
            continue
        candidates.extend(source.get("plant_candidates", []))
        candidates.extend(source.get("plants", []))
        result = source.get("result", {})
        auto = result.get("auto_extraction", {}) if isinstance(result, dict) else {}
        if isinstance(auto, dict):
            candidates.extend(auto.get("plant_candidates", []))
    return [row for row in candidates if isinstance(row, dict)]


def _circuit_candidates(vision, proposal, plants):
    rows = []
    for source in (vision, proposal):
        if not isinstance(source, dict):
            continue
        rows.extend(source.get("plant_circuits", []))
        rows.extend(source.get("circuits", []))
        result = source.get("result", {})
        auto = result.get("auto_extraction", {}) if isinstance(result, dict) else {}
        if isinstance(auto, dict):
            rows.extend(auto.get("plant_circuits", []))
    for plant in plants:
        rows.extend(plant.get("circuits", plant.get("hydraulic_circuits", [])) if isinstance(plant.get("circuits", plant.get("hydraulic_circuits", [])), list) else [])
    return [row for row in rows if isinstance(row, dict)]


def _status(origin, errors):
    if errors:
        return "blocked"
    if origin in {"ai_evidence", "controlled_fallback", "research_candidate", "unresolved"}:
        return "provisional" if origin != "unresolved" else "needs_review"
    return "resolved"


def resolve(vision=None, proposal=None, known_ahu_ids=None, pack=None, source_fingerprints=None, existing=None):
    """Normalize AI/PDF plant candidates into ``plant_resolution.json``."""
    existing = existing if isinstance(existing, dict) else empty_plant_resolution()
    source_fingerprints = deepcopy(source_fingerprints or {})
    known_ahu_ids = set(known_ahu_ids or [])
    plants_raw = _candidate_sources(vision, proposal)
    circuits_raw = _circuit_candidates(vision, proposal, plants_raw)
    source_fp = source_fingerprints.get("vision_response", "")
    systems, circuits, mappings, pumps, pipes, issues = [], [], [], [], [], []
    plant_by_tag, circuit_by_tag = {}, {}
    plant_ids_seen, circuit_ids_seen, pump_ids_seen, pipe_ids_seen = set(), set(), set(), set()

    for candidate in plants_raw:
        tag = _text(candidate.get("plant_id", candidate.get("tag", candidate.get("system_id", candidate.get("name", "plant")))))
        page = candidate.get("page", (candidate.get("source_pages") or [""])[0] if isinstance(candidate.get("source_pages"), list) else "")
        plant_id = stable_id("plant", source_fp, tag, page, candidate.get("geometry_key", candidate.get("location", "")), "|".join(sorted(map(str, candidate.get("served_ahu_ids", [])))))
        duplicate_plant = plant_id in plant_ids_seen
        if duplicate_plant:
            plant_id = f"{plant_id}_dup_{len([row for row in systems if row.get('plant_id', '').startswith(plant_id)]) + 1}"
            issues.append({"type": "duplicate_plant_candidate", "plant_id": plant_id, "remediation": "Confirm which plant record is authoritative and remove the duplicate schematic reference."})
        plant_ids_seen.add(plant_id)
        plant_by_tag[tag] = plant_id
        plant_type = _text(candidate.get("plant_type", candidate.get("type", ""))).casefold()
        aliases = {"cooling": "chiller", "chilled_water": "chiller", "heat_pump": "package_unit", "package": "package_unit", "boiler_plant": "boiler"}
        plant_type = aliases.get(plant_type, plant_type)
        errors = []
        if duplicate_plant:
            errors.append("duplicate plant candidate")
        if plant_type not in PLANT_TYPES:
            errors.append("plant type is unresolved or unsupported")
        ahus = list(dict.fromkeys(_text(item) for item in candidate.get("served_ahu_ids", candidate.get("ahu_ids", [])) if _text(item)))
        unknown = sorted(set(ahus) - known_ahu_ids) if known_ahu_ids else []
        if unknown:
            errors.append("unknown served AHU IDs: " + ", ".join(unknown))
        profile = _profile(pack, plant_type, "")
        citations = _citation_rows(candidate)
        cited = bool(citations)
        number_off = _num(candidate.get("number_off")) if cited else None
        if number_off is None:
            number_off = _num(profile.get("number_off", 1))
        if number_off is None or number_off <= 0 or not number_off.is_integer():
            number_off = 1
            errors.append("number-off is unresolved")
        duty_basis = _text(candidate.get("duty_basis", "combined_equipment")) or "combined_equipment"
        if duty_basis not in DUTY_BASES:
            errors.append("unsupported duty basis")
            duty_basis = "combined_equipment"
        diversity = _num(candidate.get("diversity_factor")) if cited else None
        origin = _origin(candidate)
        if diversity is None and profile.get("diversity_factor") is not None:
            diversity, origin = _num(profile.get("diversity_factor")), "controlled_fallback"
        if diversity is None or not 0 <= diversity <= 1:
            errors.append("diversity factor is unresolved")
            diversity = None
        capacity = _num(candidate.get("rated_capacity_kw", candidate.get("capacity_kw"))) if cited else None
        if capacity is not None and capacity < 0:
            errors.append("rated capacity cannot be negative")
            capacity = None
        if not circuits_raw and candidate.get("circuit_ids"):
            errors.append("plant references circuits that were not provided")
        explicit_circuit_tags = list(dict.fromkeys(_text(item) for item in candidate.get("circuit_ids", []) if _text(item)))
        systems.append({
            "plant_id": plant_id, "source_tag": tag, "name": _text(candidate.get("name", tag)) or plant_id,
            "plant_type": plant_type, "number_off": int(number_off), "duty_basis": duty_basis,
            "circuit_ids": [], "served_ahu_ids": ahus, "diversity_factor": diversity, "rated_capacity_kw": capacity,
            "review_status": _status(origin, errors), "status": _status(origin, errors), "origin": origin,
            "confidence": round(_confidence(candidate.get("confidence_score", candidate.get("confidence"))), 4),
            "confidence_band": confidence_band(_confidence(candidate.get("confidence_score", candidate.get("confidence")))),
            "source": _text(candidate.get("source", "AI mechanical plant evidence")), "citations": citations,
            "source_pages": sorted({row["page"] for row in citations if isinstance(row.get("page"), int)}),
            "rationale": _text(candidate.get("rationale", "AI proposed plant system.")),
            "assumptions": list(candidate.get("assumptions", [])) if isinstance(candidate.get("assumptions", []), list) else [],
            "conflicts": list(candidate.get("conflicts", [])) if isinstance(candidate.get("conflicts", []), list) else [],
            "unresolved_fields": sorted(set(errors + [str(value) for value in candidate.get("unresolved_fields", [])])),
            "remediation": "Provide cited plant type, AHU ownership, circuit mapping, and diversity evidence." if errors else "",
            "profile_id": f"{(pack or {}).get('plant_profile_pack_version', 'au-preliminary-v5')}-{plant_type}" if origin == "controlled_fallback" else "",
            "preliminary_assumption": origin == "controlled_fallback", "source_circuit_tags": explicit_circuit_tags,
        })

    for raw in circuits_raw:
        tag = _text(raw.get("circuit_id", raw.get("tag", raw.get("name", "circuit"))))
        page = raw.get("page", (raw.get("source_pages") or [""])[0] if isinstance(raw.get("source_pages"), list) else "")
        plant_tag = _text(raw.get("plant_id", raw.get("owner_plant_id", "")))
        plant_id = plant_by_tag.get(plant_tag, plant_tag if plant_tag in {row["plant_id"] for row in systems} else "")
        circuit_id = stable_id("circuit", source_fp, tag, page, raw.get("geometry_key", raw.get("location", "")), plant_id)
        duplicate_circuit = circuit_id in circuit_ids_seen
        if duplicate_circuit:
            circuit_id = f"{circuit_id}_dup_{len([row for row in circuits if row.get('circuit_id', '').startswith(circuit_id)]) + 1}"
            issues.append({"type": "duplicate_circuit_candidate", "circuit_id": circuit_id, "remediation": "Confirm the single physical circuit represented by duplicate schematic references."})
        circuit_ids_seen.add(circuit_id)
        circuit_by_tag[tag] = circuit_id
        circuit_type = _text(raw.get("circuit_type", raw.get("type", ""))).casefold()
        aliases = {"chilled": "chilled_water", "chw": "chilled_water", "heating": "heating_water", "hw": "heating_water", "refrigerant_line": "refrigerant"}
        circuit_type = aliases.get(circuit_type, circuit_type)
        errors = []
        if duplicate_circuit:
            errors.append("duplicate circuit candidate")
        if circuit_type not in CIRCUIT_TYPES:
            errors.append("circuit type is unresolved or unsupported")
        if not plant_id:
            errors.append("owning plant is unresolved")
        ahus = list(dict.fromkeys(_text(item) for item in raw.get("served_ahu_ids", raw.get("ahu_ids", [])) if _text(item)))
        unknown = sorted(set(ahus) - known_ahu_ids) if known_ahu_ids else []
        if unknown:
            errors.append("unknown served AHU IDs: " + ", ".join(unknown))
        citations = _citation_rows(raw)
        flow, flow_basis = _flow_lps(raw) if citations else (None, {"value": None, "unit": "L/s", "conversion": "uncited numeric excluded"})
        profile = _profile(pack, "", circuit_type)
        origin = _origin(raw)
        if flow is None and profile.get("flow_lps") is not None:
            flow, flow_basis, origin = _num(profile.get("flow_lps")), {"value": profile.get("flow_lps"), "unit": "L/s", "conversion": "controlled profile"}, "controlled_fallback"
        if flow is None:
            errors.append("circuit flow is unresolved")
        supply_temperature = _num(raw.get("supply_temperature_c")) if citations else None
        return_temperature = _num(raw.get("return_temperature_c")) if citations else None
        if circuit_type == "chilled_water" and (supply_temperature is None or return_temperature is None):
            default_state = profile.get("temperatures", {}) if isinstance(profile.get("temperatures"), dict) else {}
            supply_temperature = supply_temperature if supply_temperature is not None else _num(default_state.get("supply_c"))
            return_temperature = return_temperature if return_temperature is not None else _num(default_state.get("return_c"))
            if supply_temperature is not None and return_temperature is not None:
                origin = "controlled_fallback"
        if circuit_type == "chilled_water" and (supply_temperature is None or return_temperature is None):
            errors.append("chilled-water supply/return temperatures are unresolved")
        status = _status(origin, errors)
        row = {
            "circuit_id": circuit_id, "source_tag": tag, "name": _text(raw.get("name", tag)) or circuit_id,
            "plant_id": plant_id, "circuit_type": circuit_type, "served_ahu_ids": ahus,
            "flow_lps": flow, "flow_basis": flow_basis, "supply_temperature_c": supply_temperature,
            "return_temperature_c": return_temperature, "review_status": status, "status": status, "origin": origin,
            "confidence": round(_confidence(raw.get("confidence_score", raw.get("confidence"))), 4),
            "confidence_band": confidence_band(_confidence(raw.get("confidence_score", raw.get("confidence")))),
            "source": _text(raw.get("source", "AI hydraulic circuit evidence")), "citations": citations,
            "source_pages": sorted({row["page"] for row in citations if isinstance(row.get("page"), int)}),
            "schedule": _schedule(raw.get("schedule"), profile.get("schedule")),
            "rationale": _text(raw.get("rationale", "AI proposed hydraulic circuit.")),
            "assumptions": list(raw.get("assumptions", [])) if isinstance(raw.get("assumptions", []), list) else [],
            "conflicts": list(raw.get("conflicts", [])) if isinstance(raw.get("conflicts", []), list) else [],
            "unresolved_fields": sorted(set(errors + [str(value) for value in raw.get("unresolved_fields", [])])),
            "remediation": "Provide a cited circuit type, plant owner, AHU mapping, flow, and state-point evidence." if errors else "",
            "profile_id": f"{(pack or {}).get('plant_profile_pack_version', 'au-preliminary-v5')}-{circuit_type}" if origin == "controlled_fallback" else "",
            "preliminary_assumption": origin == "controlled_fallback",
        }
        circuits.append(row)
        systems_for_plant = [system for system in systems if system["plant_id"] == plant_id]
        if not systems_for_plant and plant_id:
            issues.append({"type": "unknown_plant_owner", "circuit_id": circuit_id, "remediation": "Map the circuit to a discovered plant system."})
        for system in systems_for_plant:
            if circuit_id not in system["circuit_ids"]:
                system["circuit_ids"].append(circuit_id)
        mappings.append({"mapping_id": stable_id("mapping", source_fp, tag, page, raw.get("geometry_key", raw.get("location", "")), f"{plant_id}|{','.join(sorted(ahus))}"), "plant_id": plant_id, "circuit_id": circuit_id, "ahu_ids": ahus, "status": status, "origin": origin, "citations": citations, "rationale": _text(raw.get("mapping_rationale", raw.get("rationale", "AI proposed AHU-to-circuit-to-plant mapping."))), "unresolved_fields": deepcopy(row["unresolved_fields"]), "remediation": row["remediation"]})
        pump_rows = raw.get("pumps", []) if isinstance(raw.get("pumps", []), list) else []
        for pump in pump_rows:
            if not isinstance(pump, dict):
                continue
            pump_id = stable_id("pump", source_fp, pump.get("pump_id", pump.get("tag", "pump")), pump.get("page", page), pump.get("location", ""), circuit_id)
            if pump_id in pump_ids_seen:
                pump_id = f"{pump_id}_dup_{len([row for row in pumps if row.get('pump_id', '').startswith(pump_id)]) + 1}"
                pump_errors = ["duplicate pump candidate"]
                issues.append({"type": "duplicate_pump_candidate", "pump_id": pump_id, "remediation": "Confirm the single physical pump represented by duplicate records."})
            else:
                pump_errors = []
            pump_ids_seen.add(pump_id)
            pump_citations = _citation_rows(pump) or citations
            power = _num(pump.get("power_kw")) if pump_citations else None
            pump_origin = _origin(pump, origin)
            pump_profile = profile.get("pump", {}) if isinstance(profile.get("pump"), dict) else {}
            if power is None and pump_profile.get("power_kw") is not None:
                power, pump_origin = _num(pump_profile.get("power_kw")), "controlled_fallback"
            if power is None:
                pump_errors.append("pump power is unresolved")
            pump_status = _status(pump_origin, pump_errors)
            pumps.append({"pump_id": pump_id, "circuit_id": circuit_id, "number_off": int(_num(pump.get("number_off")) or pump_profile.get("number_off", 1)), "power_kw": power, "schedule": _schedule(pump.get("schedule"), pump_profile.get("schedule")), "status": pump_status, "review_status": pump_status, "origin": pump_origin, "source": _text(pump.get("source", raw.get("source", "AI pump evidence"))), "citations": _citation_rows(pump) or citations, "unresolved_fields": pump_errors, "profile_id": f"{(pack or {}).get('plant_profile_pack_version', 'au-preliminary-v5')}-pump" if pump_origin == "controlled_fallback" else "", "preliminary_assumption": pump_origin == "controlled_fallback"})
        pipe_rows = raw.get("pipe_effects", raw.get("pipes", [])) if isinstance(raw.get("pipe_effects", raw.get("pipes", [])), list) else []
        for pipe in pipe_rows:
            if not isinstance(pipe, dict):
                continue
            pipe_id = stable_id("pipe", source_fp, pipe.get("pipe_id", pipe.get("tag", "pipe")), pipe.get("page", page), pipe.get("location", ""), circuit_id)
            if pipe_id in pipe_ids_seen:
                pipe_id = f"{pipe_id}_dup_{len([row for row in pipes if row.get('pipe_id', '').startswith(pipe_id)]) + 1}"
                pipe_errors = ["duplicate pipe-effect candidate"]
                issues.append({"type": "duplicate_pipe_candidate", "pipe_id": pipe_id, "remediation": "Confirm the single physical pipe effect represented by duplicate records."})
            else:
                pipe_errors = []
            pipe_ids_seen.add(pipe_id)
            pipe_citations = _citation_rows(pipe) or citations
            effect = _num(pipe.get("effect_kw")) if pipe_citations else None
            pipe_origin = _origin(pipe, origin)
            pipe_profile = profile.get("pipe", {}) if isinstance(profile.get("pipe"), dict) else {}
            if effect is None and pipe_profile.get("effect_kw") is not None:
                effect, pipe_origin = _num(pipe_profile.get("effect_kw")), "controlled_fallback"
            if effect is None:
                pipe_errors.append("pipe effect is unresolved")
            pipe_status = _status(pipe_origin, pipe_errors)
            pipes.append({"pipe_id": pipe_id, "circuit_id": circuit_id, "effect_kw": effect, "schedule": _schedule(pipe.get("schedule"), pipe_profile.get("schedule")), "status": pipe_status, "review_status": pipe_status, "origin": pipe_origin, "source": _text(pipe.get("source", raw.get("source", "AI pipe-effect evidence"))), "citations": _citation_rows(pipe) or citations, "unresolved_fields": pipe_errors, "profile_id": f"{(pack or {}).get('plant_profile_pack_version', 'au-preliminary-v5')}-pipe" if pipe_origin == "controlled_fallback" else "", "preliminary_assumption": pipe_origin == "controlled_fallback"})

    # Resolve explicit plant circuit tag references now that all IDs are known.
    for system in systems:
        for tag in system.pop("source_circuit_tags", []):
            circuit_id = circuit_by_tag.get(tag)
            if circuit_id and circuit_id not in system["circuit_ids"]:
                system["circuit_ids"].append(circuit_id)
            elif not circuit_id:
                system["status"] = "blocked"; system["review_status"] = "blocked"; system["unresolved_fields"] = sorted(set(system["unresolved_fields"] + [f"unknown circuit reference: {tag}"]))
                issues.append({"type": "unknown_circuit_reference", "plant_id": system["plant_id"], "circuit": tag, "remediation": "Provide the referenced hydraulic circuit record."})
    # Reject duplicate ownership without silently picking a path.
    assigned_ahu = {}
    for circuit in circuits:
        for ahu_id in circuit["served_ahu_ids"]:
            if ahu_id in assigned_ahu and assigned_ahu[ahu_id] != circuit["circuit_id"]:
                issue = {"type": "duplicate_ahu_circuit_owner", "ahu_id": ahu_id, "circuit_ids": sorted({assigned_ahu[ahu_id], circuit["circuit_id"]}), "remediation": "Confirm the single hydraulic circuit serving this AHU."}
                issues.append(issue)
                circuit["status"] = circuit["review_status"] = "blocked"
            assigned_ahu[ahu_id] = circuit["circuit_id"]
    for system in systems:
        if system["plant_type"] == "chiller" and not system["circuit_ids"]:
            system["status"] = system["review_status"] = "blocked"
            system["unresolved_fields"] = sorted(set(system["unresolved_fields"] + ["chiller has no mapped circuit"]))
            issues.append({"type": "missing_chilled_water_circuit", "plant_id": system["plant_id"], "remediation": "Map the chiller to a cited chilled-water circuit."})
        if system["plant_type"] == "chiller" and not system["served_ahu_ids"]:
            system["status"] = system["review_status"] = "blocked"
            system["unresolved_fields"] = sorted(set(system["unresolved_fields"] + ["chiller has no mapped AHUs"]))
    source_fingerprints["plant_profile_pack"] = fingerprint(pack or {})
    result = {"schema_version": SCHEMA_VERSION, "systems": sorted(systems, key=lambda row: row["plant_id"]), "circuits": sorted(circuits, key=lambda row: row["circuit_id"]), "mappings": sorted(mappings, key=lambda row: row["mapping_id"]), "pumps": sorted(pumps, key=lambda row: row["pump_id"]), "pipe_effects": sorted(pipes, key=lambda row: row["pipe_id"]), "issues": issues, "overrides": deepcopy(existing.get("overrides", [])), "source_fingerprints": source_fingerprints, "status": "draft" if systems or circuits else "needs_review", "updated_at": now()}
    result["summary"] = {"systems": len(systems), "circuits": len(circuits), "mappings": len(mappings), "provisional_systems": sum(row["status"] == "provisional" for row in systems), "blocked_systems": sum(row["status"] == "blocked" for row in systems), "deferred_systems": sum(row["plant_type"] != "chiller" for row in systems), "issues": len(issues)}
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Plant resolution must be an object.")
    result = empty_plant_resolution()
    result.update({key: deepcopy(raw[key]) for key in result if key in raw})
    for key in ("systems", "circuits", "mappings", "pumps", "pipe_effects", "issues", "overrides"):
        if not isinstance(result.get(key), list):
            raise ValueError(f"Plant resolution {key} must be a list.")
    seen = set()
    for row in result["systems"]:
        if not isinstance(row, dict) or not _text(row.get("plant_id")) or row["plant_id"] in seen:
            raise ValueError("Plant system IDs must be present and unique.")
        if row.get("plant_type") not in PLANT_TYPES or row.get("status") not in STATUSES:
            raise ValueError("Plant system type or status is invalid.")
        seen.add(row["plant_id"])
    seen = set()
    for row in result["circuits"]:
        if not isinstance(row, dict) or not _text(row.get("circuit_id")) or row["circuit_id"] in seen:
            raise ValueError("Circuit IDs must be present and unique.")
        if row.get("circuit_type") not in CIRCUIT_TYPES or row.get("status") not in STATUSES:
            raise ValueError("Circuit type or status is invalid.")
        if row.get("flow_lps") is not None and (_num(row.get("flow_lps")) is None or float(row["flow_lps"]) < 0):
            raise ValueError("Circuit flow must be finite and non-negative.")
        seen.add(row["circuit_id"])
    for key, id_key in (("pumps", "pump_id"), ("pipe_effects", "pipe_id"), ("mappings", "mapping_id")):
        seen = set()
        for row in result[key]:
            if not isinstance(row, dict) or not _text(row.get(id_key)) or row[id_key] in seen:
                raise ValueError(f"{key} IDs must be present and unique.")
            if row.get("status") not in STATUSES:
                raise ValueError(f"{key} status is invalid.")
            seen.add(row[id_key])
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints):
    return isinstance(artifact, dict) and all(artifact.get("source_fingerprints", {}).get(key) == value for key, value in (source_fingerprints or {}).items())


def apply_override(artifact, target_type, target_id, values, reviewer="", note=""):
    result = validate(artifact)
    collections = {"plant": result["systems"], "system": result["systems"], "circuit": result["circuits"], "pump": result["pumps"], "pipe": result["pipe_effects"]}
    rows = collections.get(target_type)
    if rows is None:
        raise ValueError("Plant override target type is invalid.")
    id_key = {"plant": "plant_id", "system": "plant_id", "circuit": "circuit_id", "pump": "pump_id", "pipe": "pipe_id"}[target_type]
    row = next((item for item in rows if item.get(id_key) == target_id), None)
    if row is None:
        raise ValueError("Unknown plant resolution target.")
    if not isinstance(values, dict):
        raise ValueError("Plant override values must be an object.")
    for key, value in values.items():
        if key in {"diversity_factor", "rated_capacity_kw", "flow_lps", "supply_temperature_c", "return_temperature_c", "power_kw", "effect_kw", "number_off"}:
            numeric = _num(value)
            if numeric is None or numeric < 0:
                raise ValueError(f"Plant override value for {key} must be finite and non-negative.")
            row[key] = int(numeric) if key == "number_off" else numeric
        elif key in {"name", "duty_basis", "plant_type", "circuit_type"}:
            row[key] = _text(value)
    row["origin"] = "contractor_override"; row["status"] = "resolved"; row["review_status"] = "resolved"
    row["override"] = {"reviewer": _text(reviewer), "note": _text(note), "updated_at": now(), "values": deepcopy(values)}
    result["overrides"].append({"target_type": target_type, "target_id": target_id, "reviewer": _text(reviewer), "note": _text(note), "values": deepcopy(values), "updated_at": now()})
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def clear_override(artifact, target_type, target_id):
    result = validate(artifact)
    id_key = {"plant": "plant_id", "system": "plant_id", "circuit": "circuit_id", "pump": "pump_id", "pipe": "pipe_id"}.get(target_type)
    if not id_key:
        raise ValueError("Plant override target type is invalid.")
    for collection in (result["systems"], result["circuits"], result["pumps"], result["pipe_effects"]):
        for row in collection:
            if row.get(id_key) == target_id:
                row.pop("override", None); row["status"] = "needs_review"; row["review_status"] = "needs_review"; row["origin"] = "unresolved"
                row.setdefault("unresolved_fields", []).append("override cleared; resolve again")
    result["overrides"] = [item for item in result.get("overrides", []) if item.get("target_id") != target_id]
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def materialize(artifact, *, preliminary=False):
    artifact = validate(artifact)
    blocked = {"blocked", "excluded"} | (set() if preliminary else {"needs_review", "provisional"})
    plant_rows, circuit_rows, excluded = [], [], []
    for row in artifact["systems"]:
        if row.get("status") in blocked:
            excluded.append({"kind": "plant", "id": row.get("plant_id", ""), "reason": "; ".join(row.get("unresolved_fields", [])) or "Plant system is not eligible."})
            continue
        plant_rows.append({"plant_id": row["plant_id"], "name": row.get("name", row["plant_id"]), "plant_type": row["plant_type"], "number_off": row.get("number_off", 1), "duty_basis": row.get("duty_basis", "combined_equipment"), "circuit_ids": row.get("circuit_ids", []), "served_ahu_ids": row.get("served_ahu_ids", []), "diversity_factor": row.get("diversity_factor"), "rated_capacity_kw": row.get("rated_capacity_kw"), "review_status": "provisional" if preliminary else "confirmed", "source": row.get("source", ""), "citations": deepcopy(row.get("citations", [])), "preliminary_assumption": bool(row.get("preliminary_assumption"))})
    active = {row["plant_id"] for row in plant_rows}
    for row in artifact["circuits"]:
        if row.get("plant_id") not in active or row.get("status") in blocked:
            excluded.append({"kind": "circuit", "id": row.get("circuit_id", ""), "reason": "; ".join(row.get("unresolved_fields", [])) or "Circuit is not eligible."})
            continue
        circuit_rows.append({"circuit_id": row["circuit_id"], "name": row.get("name", row["circuit_id"]), "plant_id": row["plant_id"], "circuit_type": row["circuit_type"], "served_ahu_ids": row.get("served_ahu_ids", []), "flow_lps": row.get("flow_lps"), "supply_temperature_c": row.get("supply_temperature_c"), "return_temperature_c": row.get("return_temperature_c"), "pumps": [], "pipe_effects": [], "review_status": "provisional" if preliminary else "confirmed", "source": row.get("source", ""), "citations": deepcopy(row.get("citations", []))})
    circuit_lookup = {row["circuit_id"]: row for row in circuit_rows}
    for row in artifact["pumps"]:
        target = circuit_lookup.get(row.get("circuit_id"))
        if not target or row.get("status") in blocked or row.get("power_kw") is None:
            excluded.append({"kind": "pump", "id": row.get("pump_id", ""), "reason": "; ".join(row.get("unresolved_fields", [])) or "Pump input is not eligible."})
            continue
        target["pumps"].append({"pump_id": row["pump_id"], "number_off": row.get("number_off", 1), "power_kw": row["power_kw"], "schedule": row.get("schedule", {}).get("weekday", []), "review_status": "provisional" if preliminary else "confirmed", "source": row.get("source", ""), "citations": deepcopy(row.get("citations", []))})
    for row in artifact["pipe_effects"]:
        target = circuit_lookup.get(row.get("circuit_id"))
        if not target or row.get("status") in blocked or row.get("effect_kw") is None:
            excluded.append({"kind": "pipe_effect", "id": row.get("pipe_id", ""), "reason": "; ".join(row.get("unresolved_fields", [])) or "Pipe effect is not eligible."})
            continue
        target["pipe_effects"].append({"pipe_id": row["pipe_id"], "effect_kw": row["effect_kw"], "schedule": row.get("schedule", {}).get("weekday", []), "review_status": "provisional" if preliminary else "confirmed", "source": row.get("source", ""), "citations": deepcopy(row.get("citations", []))})
    return {"plant_systems": {"schema_version": 1, "updated_at": now(), "systems": plant_rows}, "hydraulic_circuits": {"schema_version": 1, "updated_at": now(), "circuits": circuit_rows}, "excluded": excluded, "policy": {"mode": "ai_preliminary" if preliminary else "reviewed", "artifact_fingerprint": artifact["fingerprint"]}}
