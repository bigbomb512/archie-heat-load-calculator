"""Immutable, evidence-first assembly for supported hourly cooling inputs.

The hourly engine remains the sole load calculator. This module resolves
project evidence, cited overrides and approved scoped defaults, then creates a
fully traceable in-memory payload without editing the authored artefacts.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from ai.hourly_loads import (
    DAY_TYPES,
    empty_day_profile,
    room_static_missing,
    scenario_ready,
    validate_design_day_scenarios,
    validate_hourly_load_model,
    validate_schedule_library,
)
from ai.infiltration_gate import gate_is_approved, validate_infiltration_method_gate
from ai.research_cache import eligible_bindings, validate_cache
from ai.site_design_conditions import validate_citations


SNAPSHOT_SCHEMA_VERSION = 2
POLICY_VERSION = "au-cooling-v1"
OVERRIDE_SCHEMA_VERSION = 1
CONTEXT_SCHEMA_VERSION = 1


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _stable(value):
    if isinstance(value, list):
        return [_stable(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: _stable(item) for key, item in value.items() if key not in {"updated_at", "created_at"}}


def empty_project_context():
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "revision": 0,
        "site": {"country": "AU", "locality": "", "state": "", "climate_zone": "", "source": "", "citations": []},
        "building_use": "",
        "room_uses": {},
        "conditioned_scope": {"status": "missing", "mode": "room_ids", "room_ids": [], "source": "", "citations": []},
        "reviewer": "",
        "default_policy_version": POLICY_VERSION,
        "updated_at": "",
    }


def validate_project_context(raw):
    source = deepcopy(raw or empty_project_context())
    if not isinstance(source, dict) or source.get("schema_version", CONTEXT_SCHEMA_VERSION) != CONTEXT_SCHEMA_VERSION:
        raise ValueError("Unsupported project context schema.")
    result = empty_project_context()
    site = source.get("site") or {}
    if not isinstance(site, dict):
        raise ValueError("Project context site must be an object.")
    result["site"] = {key: str(site.get(key, "")).strip() for key in ("country", "locality", "state", "climate_zone", "source")}
    result["site"]["country"] = result["site"]["country"] or "AU"
    result["site"]["citations"] = validate_citations(site.get("citations", []), "Project context site")
    if result["site"]["country"] != "AU":
        raise ValueError("Cooling V1 source packs support Australia-only project context.")
    room_uses = source.get("room_uses", {})
    if not isinstance(room_uses, dict):
        raise ValueError("Project context room uses must be an object.")
    for room_id, value in room_uses.items():
        if not isinstance(value, dict) or not str(value.get("use", "")).strip():
            raise ValueError(f"Project context room use '{room_id}' needs a use.")
        result["room_uses"][str(room_id)] = {
            "use": str(value["use"]).strip().lower(), "source": str(value.get("source", "")).strip(),
            "citations": validate_citations(value.get("citations", []), f"Project context room use {room_id}"),
        }
    scope = source.get("conditioned_scope") or {}
    if not isinstance(scope, dict):
        raise ValueError("Conditioned scope must be an object.")
    status = str(scope.get("status", "missing"))
    mode = str(scope.get("mode", "room_ids"))
    if status not in {"missing", "confirmed"} or mode not in {"room_ids", "all_rooms", "room_uses"}:
        raise ValueError("Invalid conditioned scope status or mode.")
    room_ids = scope.get("room_ids", [])
    if not isinstance(room_ids, list) or not all(isinstance(item, str) and item.strip() for item in room_ids):
        raise ValueError("Conditioned scope room IDs must be a list of non-empty IDs.")
    result["conditioned_scope"] = {
        "status": status, "mode": mode, "room_ids": sorted(set(item.strip() for item in room_ids)),
        "source": str(scope.get("source", "")).strip(),
        "citations": validate_citations(scope.get("citations", []), "Conditioned scope"),
    }
    if status == "confirmed" and not result["conditioned_scope"]["source"]:
        raise ValueError("A confirmed conditioned scope needs a source.")
    result["building_use"] = str(source.get("building_use", "")).strip().lower()
    result["reviewer"] = str(source.get("reviewer", "")).strip()
    result["default_policy_version"] = str(source.get("default_policy_version", POLICY_VERSION)).strip() or POLICY_VERSION
    result["revision"] = int(source.get("revision", 0))
    result["updated_at"] = str(source.get("updated_at", ""))
    result["fingerprint"] = _fingerprint(_stable(result))
    return result


def empty_overrides():
    return {"schema_version": OVERRIDE_SCHEMA_VERSION, "revision": 0, "records": [], "updated_at": "", "fingerprint": ""}


def validate_overrides(raw):
    source = deepcopy(raw or empty_overrides())
    if not isinstance(source, dict) or source.get("schema_version", OVERRIDE_SCHEMA_VERSION) != OVERRIDE_SCHEMA_VERSION:
        raise ValueError("Unsupported calculator input overrides schema.")
    records, ids = [], set()
    for index, row in enumerate(source.get("records", []), start=1):
        if not isinstance(row, dict):
            raise ValueError("Calculator input override must be an object.")
        override_id, target = str(row.get("override_id", "")).strip(), str(row.get("target", "")).strip()
        if not override_id or not target or override_id in ids:
            raise ValueError(f"Override {index} needs a unique ID and target.")
        if row.get("value") in (None, "") or not str(row.get("unit", "")).strip():
            raise ValueError(f"Override {override_id} needs a value and unit.")
        if not str(row.get("source", "")).strip() or not str(row.get("reviewer", "")).strip():
            raise ValueError(f"Override {override_id} needs a source and reviewer.")
        citations = validate_citations(row.get("citations", []), f"Override {override_id}")
        if not citations:
            raise ValueError(f"Override {override_id} needs at least one citation.")
        records.append({
            "override_id": override_id, "target": target, "value": deepcopy(row["value"]), "unit": str(row["unit"]).strip(),
            "source": str(row["source"]).strip(), "citations": citations, "reviewer": str(row["reviewer"]).strip(),
            "created_at": str(row.get("created_at", "")),
        })
        ids.add(override_id)
    result = {"schema_version": OVERRIDE_SCHEMA_VERSION, "revision": int(source.get("revision", 0)), "records": records, "updated_at": str(source.get("updated_at", ""))}
    result["fingerprint"] = _fingerprint(_stable(result))
    return result


def upsert_override(raw, record):
    current = validate_overrides(raw)
    row = deepcopy(record)
    row.setdefault("created_at", timestamp())
    checked = validate_overrides({"schema_version": OVERRIDE_SCHEMA_VERSION, "records": [row]})["records"][0]
    current["records"] = [item for item in current["records"] if item["override_id"] != checked["override_id"]] + [checked]
    current["revision"] += 1
    current["updated_at"] = timestamp()
    return validate_overrides(current)


def _value_present(value):
    return value is not None and value != ""


def _set_path(row, dotted, value):
    current = row
    parts = dotted.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _room_target(room_id, field):
    return f"rooms.{room_id}.{field}"


def _source_record(state, target, value, unit, *, source_id="", source="", citations=None, confidence="", rule="", derivation=None, candidates=None):
    return {
        "input_id": "input-" + hashlib.sha256(target.encode()).hexdigest()[:16], "target": target,
        "value": deepcopy(value), "unit": unit, "resolution_status": state,
        "source_ids": [source_id] if source_id else [], "source": source, "citations": deepcopy(citations or []),
        "confidence": confidence, "policy_rule": rule, "derivation": deepcopy(derivation or {}),
        "candidates": deepcopy(candidates or []),
    }


def _conflicting(rows):
    return len({json.dumps(row.get("value"), sort_keys=True) for row in rows}) > 1


def _fact_candidates(fusion, target):
    facts = list((fusion or {}).get("facts", [])) + list(((fusion or {}).get("fact_registry") or {}).get("facts", []))
    rows = []
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("validation_status") != "valid" or fact.get("activation_status") != "active":
            continue
        value = fact.get("value")
        fact_target = fact.get("target_path") or (value.get("target_path") if isinstance(value, dict) else "")
        if fact_target != target:
            continue
        extracted = value.get("value") if isinstance(value, dict) and "value" in value else value
        if _value_present(extracted):
            rows.append({"id": fact.get("fact_id", ""), "value": extracted, "unit": fact.get("unit", ""), "source": fact.get("source", {}), "citations": fact.get("citations", []), "confidence": fact.get("extraction_confidence", "")})
    return rows


def _resolve_field(target, current_value, unit, *, current_source, current_citations, fusion, cache, scope, overrides, default_target=None):
    override_rows = [row for row in overrides["records"] if row["target"] == target]
    if len(override_rows) > 1 and _conflicting(override_rows):
        return _source_record("blocked", target, None, unit, rule="conflicting project overrides", candidates=override_rows), None
    if override_rows:
        row = override_rows[-1]
        return _source_record("project_override", target, row["value"], row["unit"], source_id=row["override_id"], source=row["source"], citations=row["citations"], rule="cited project override"), row["value"]
    facts = _fact_candidates(fusion, target)
    if _conflicting(facts):
        return _source_record("blocked", target, None, unit, rule="conflicting explicit project evidence", candidates=facts), None
    if facts:
        fact = facts[0]
        reference = fact.get("source", {}).get("reference") or fact.get("source", {}).get("drawing_number") or "Architect evidence"
        return _source_record("project_evidence", target, fact["value"], fact.get("unit") or unit, source_id=fact["id"], source=str(reference), citations=fact.get("citations", []), confidence=fact.get("confidence", ""), rule="explicit validated evidence"), fact["value"]
    if _value_present(current_value):
        return _source_record("project_evidence", target, current_value, unit, source=current_source, citations=current_citations, rule="authored project input"), current_value
    bindings = eligible_bindings(cache, default_target or target, scope)
    if _conflicting(bindings):
        return _source_record("blocked", target, None, unit, rule="conflicting approved defaults", candidates=bindings), None
    if bindings:
        binding = bindings[0]
        citation = {"reference": binding.get("citation", binding["record_id"]), "page": None, "excerpt": binding.get("excerpt", "")}
        return _source_record("approved_default", target, binding["value"], binding.get("unit") or unit, source_id=binding["record_id"], source=binding.get("publisher", "Approved source pack"), citations=[citation], rule="approved scoped Australia-first default"), binding["value"]
    return _source_record("blocked", target, None, unit, rule="no project evidence, override, or eligible default"), None


def _scope_for_room(context, room_id):
    use = (context.get("room_uses", {}).get(room_id) or {}).get("use") or context.get("building_use", "")
    return {"country": "AU", "locality": context["site"].get("locality", ""), "state": context["site"].get("state", ""), "climate_zone": context["site"].get("climate_zone", ""), "room_use": use}


def _is_active_room(context, room_id):
    scope = context["conditioned_scope"]
    if scope["status"] != "confirmed":
        return False
    if scope["mode"] == "all_rooms":
        return True
    if scope["mode"] == "room_uses":
        return room_id in context.get("room_uses", {})
    return room_id in scope["room_ids"]


def _default_schedule(library, room, component, scenario, binding):
    raw = binding.get("value")
    values = raw.get("values") if isinstance(raw, dict) else raw
    if not isinstance(values, list) or len(values) != 24 or any(not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 1 for value in values):
        return None
    schedule_id = re.sub(r"[^a-z0-9_-]", "-", f"default-{binding['record_id']}-{room['room_id']}-{component}".lower()).strip("-")
    if any(row["schedule_id"] == schedule_id for row in library["schedules"]):
        return schedule_id
    citation = {"reference": binding.get("citation", binding["record_id"]), "page": None, "excerpt": binding.get("excerpt", "")}
    profiles = {day_type: empty_day_profile() for day_type in DAY_TYPES}
    profiles[scenario["day_type"]] = {"values": values, "status": "confirmed", "source": f"Approved default {binding['record_id']}", "citations": [citation]}
    library["schedules"].append({"schedule_id": schedule_id, "title": f"Default {component} schedule", "description": "Approved scoped default; only the selected scenario day type is supplied.", "status": "confirmed", "source": f"Approved default {binding['record_id']}", "citations": [citation], "day_profiles": profiles})
    return schedule_id


def _record_issues(records, issues, room_id):
    for record in records:
        if record["resolution_status"] == "blocked":
            issues.append({"status": "blocked", "affected_id": room_id, "reason": f"{record['target']}: {record['policy_rule']}", "source_artifact": "calculator_input_set.json", "input_id": record["input_id"]})


def _coverage_summary(model, included, excluded, issues, records, context):
    """Build the small room-scope projection used by the API and report UI."""
    room_ids = {row.get("room_id") for row in model.get("rooms", []) if row.get("room_id")}
    scoped = {row["room_id"] for row in model.get("rooms", []) if _is_active_room(context, row.get("room_id", ""))}
    blocked = {row.get("affected_id") for row in issues if row.get("status") == "blocked" and row.get("affected_id") in room_ids}
    draft_only = {row.get("affected_id") for row in issues if row.get("status") == "draft" and row.get("affected_id") in room_ids}
    defaults = {row.get("target", "").split(".")[1] for row in records
                if row.get("resolution_status") == "approved_default" and row.get("target", "").startswith("rooms.")}
    unsupported = {}
    for room in model.get("rooms", []):
        room_id = room.get("room_id")
        if not room_id:
            continue
        labels = [item.get("component_type") or item.get("component_id") or "unsupported component"
                  for item in room.get("unapproved_components", [])
                  if item.get("calculation_status") not in {"not_present_confirmed", "calculated"}]
        if labels:
            unsupported[room_id] = sorted(set(labels))
    outside_scope = sorted(room_ids - scoped)
    active_excluded = sorted(set(excluded) & scoped)
    complete_scope = bool(scoped) and set(scoped) == set(included) and not blocked and not draft_only and not active_excluded
    return {
        "active_room_ids": sorted(scoped),
        "included_room_ids": sorted(set(included)),
        "excluded_room_ids": sorted(set(excluded)),
        "outside_scope_room_ids": outside_scope,
        "blocked_room_ids": sorted(blocked),
        "draft_only_room_ids": sorted(draft_only),
        "default_backed_room_ids": sorted(defaults),
        "unsupported_components_by_room": unsupported,
        "complete_scope": complete_scope,
        "project_blockers": [row for row in issues if row.get("affected_id") in {"project", None}],
    }


def _apply_room_defaults(model, library, scenarios, fusion, cache, context, overrides, selected, infiltration_gate):
    resolved, issues, included, excluded = [], [], [], []
    scenario_map = {row["scenario_id"]: row for row in scenarios["scenarios"]}
    zones = {zone["zone_id"]: zone for zone in model["zones"]}
    primary = scenario_map.get(selected[0]) if selected else None
    if context["conditioned_scope"]["status"] != "confirmed":
        issues.append({"status": "blocked", "affected_id": "project", "reason": "Conditioned scope is missing. Confirm the project-level cooled room scope before assembling inputs.", "source_artifact": "project_context.json"})
    for room in model["rooms"]:
        room_id = room["room_id"]
        if not _is_active_room(context, room_id):
            excluded.append(room_id)
            resolved.append(_source_record("excluded", _room_target(room_id, "active_scope"), False, "", source=context["conditioned_scope"].get("source", ""), citations=context["conditioned_scope"].get("citations", []), rule="room is outside the confirmed conditioned scope"))
            continue
        scope = _scope_for_room(context, room_id)
        if not scope["room_use"]:
            issues.append({"status": "blocked", "affected_id": room_id, "reason": "Room use is not source-backed; no use-specific default may be selected.", "source_artifact": "project_context.json"})
            excluded.append(room_id)
            continue
        records = []
        fields = [
            ("area_m2", "m2", room.get("area_m2"), room.get("source", ""), room.get("citations", [])),
            ("indoor_cooling_setpoint_c", "C", room.get("indoor_cooling_setpoint_c"), room.get("source", ""), room.get("citations", [])),
            ("cooling_load.people_sensible_w_per_person", "W/person", room["cooling_load"].get("people_sensible_w_per_person"), room["cooling_load"].get("source", ""), room.get("citations", [])),
            ("cooling_load.people_latent_w_per_person", "W/person", room["cooling_load"].get("people_latent_w_per_person"), room["cooling_load"].get("source", ""), room.get("citations", [])),
            ("cooling_load.people_diversity_factor", "", room["cooling_load"].get("people_diversity_factor"), room["cooling_load"].get("source", ""), room.get("citations", [])),
            ("cooling_load.lighting_w_m2", "W/m2", room["cooling_load"].get("lighting_w_m2"), room["cooling_load"].get("source", ""), room.get("citations", [])),
            ("cooling_load.lighting_diversity_factor", "", room["cooling_load"].get("lighting_diversity_factor"), room["cooling_load"].get("source", ""), room.get("citations", [])),
            ("cooling_load.safety_factor", "", room["cooling_load"].get("safety_factor"), room["cooling_load"].get("source", ""), room.get("citations", [])),
            ("cooling_load_conditions.indoor_cooling_wet_bulb_c", "C", room["cooling_load_conditions"].get("indoor_cooling_wet_bulb_c"), room["cooling_load_conditions"].get("source", ""), room.get("citations", [])),
        ]
        for field, unit, current, source, citations in fields:
            target = _room_target(room_id, field)
            record, value = _resolve_field(target, current, unit, current_source=source, current_citations=citations, fusion=fusion, cache=cache, scope=scope, overrides=overrides, default_target="room." + field.split(".")[-1])
            records.append(record)
            if value is not None:
                _set_path(room, field, value)
                if record["resolution_status"] == "approved_default":
                    if field.startswith("cooling_load_conditions"):
                        room["cooling_load_conditions"].update({"verification_status": "confirmed", "source": record["source"]})
                    elif field.startswith("cooling_load"):
                        room["cooling_load"].update({"verification_status": "confirmed", "source": record["source"]})
                    else:
                        room.update({"verification_status": "confirmed", "source": record["source"]})
        occupancy_target = _room_target(room_id, "occupancy")
        occupancy_record, occupancy = _resolve_field(occupancy_target, room.get("occupancy"), "people", current_source=room.get("source", ""), current_citations=room.get("citations", []), fusion=fusion, cache=cache, scope=scope, overrides=overrides, default_target="room.occupancy")
        if occupancy is None:
            density = eligible_bindings(cache, "room.occupancy_density_per_m2", scope)
            if len(density) == 1 and _value_present(room.get("area_m2")) and isinstance(density[0]["value"], (int, float)):
                occupancy = round(float(room["area_m2"]) * float(density[0]["value"]), 4)
                citation = {"reference": density[0].get("citation", density[0]["record_id"]), "page": None, "excerpt": density[0].get("excerpt", "")}
                occupancy_record = _source_record("derived_evidence", occupancy_target, occupancy, "people", source_id=density[0]["record_id"], source=density[0].get("publisher", "Approved source pack"), citations=[citation], rule="approved occupancy density multiplied by resolved room area", derivation={"formula": "occupancy = area_m2 × occupancy_density_per_m2", "operands": {"area_m2": room["area_m2"], "occupancy_density_per_m2": density[0]["value"]}, "rounding": "4 decimal places"})
        records.append(occupancy_record)
        if occupancy is not None:
            room["occupancy"] = occupancy
        flow_target = _room_target(room_id, "cooling_load.outside_air_lps")
        flow_record, flow = _resolve_field(flow_target, room["cooling_load"].get("outside_air_lps"), "L/s", current_source=room["cooling_load"].get("source", ""), current_citations=room.get("citations", []), fusion=fusion, cache=cache, scope=scope, overrides=overrides, default_target="room.outside_air_lps")
        if flow is None:
            per_person, per_area = eligible_bindings(cache, "room.outside_air_lps_per_person", scope), eligible_bindings(cache, "room.outside_air_lps_per_m2", scope)
            if len(per_person) <= 1 and len(per_area) <= 1 and (per_person or per_area) and occupancy is not None and room.get("area_m2") is not None:
                pp, pa = float(per_person[0]["value"]) if per_person else 0.0, float(per_area[0]["value"]) if per_area else 0.0
                flow = round(pp * float(occupancy) + pa * float(room["area_m2"]), 4)
                binding = (per_person or per_area)[0]
                citation = {"reference": binding.get("citation", binding["record_id"]), "page": None, "excerpt": binding.get("excerpt", "")}
                flow_record = _source_record("derived_evidence", flow_target, flow, "L/s", source_id=binding["record_id"], source=binding.get("publisher", "Approved source pack"), citations=[citation], rule="approved outside-air rates applied to resolved room occupancy and area", derivation={"formula": "L/s = occupancy × L/s/person + area × L/s/m2", "operands": {"occupancy": occupancy, "area_m2": room["area_m2"], "lps_per_person": pp, "lps_per_m2": pa}, "rounding": "4 decimal places"})
        records.append(flow_record)
        if flow is not None:
            room["cooling_load"]["outside_air_lps"] = flow
        if primary:
            for component in ("people", "lighting", "outside_air"):
                if room["schedule_assignments"].get(component):
                    continue
                bindings = eligible_bindings(cache, f"schedule.{component}", {**scope, "day_type": primary["day_type"]})
                if len(bindings) == 1:
                    schedule_id = _default_schedule(library, room, component, primary, bindings[0])
                    if schedule_id:
                        room["schedule_assignments"][component] = schedule_id
                        citation = {"reference": bindings[0].get("citation", bindings[0]["record_id"]), "page": None, "excerpt": bindings[0].get("excerpt", "")}
                        records.append(_source_record("approved_default", _room_target(room_id, f"schedule_assignments.{component}"), schedule_id, "profile", source_id=bindings[0]["record_id"], source=bindings[0].get("publisher", "Approved source pack"), citations=[citation], rule="approved cited 24-hour room-use schedule"))
        timed_components = {
            "people": float(room.get("occupancy") or 0) * (float(room["cooling_load"].get("people_sensible_w_per_person") or 0) + float(room["cooling_load"].get("people_latent_w_per_person") or 0)) * float(room["cooling_load"].get("people_diversity_factor") or 0),
            "lighting": float(room.get("area_m2") or 0) * float(room["cooling_load"].get("lighting_w_m2") or 0) * float(room["cooling_load"].get("lighting_diversity_factor") or 0),
            "outside_air": float(room["cooling_load"].get("outside_air_lps") or 0),
        }
        infiltration = next((item for item in room.get("unapproved_components", []) if item.get("component_type") == "infiltration"), None)
        if infiltration and infiltration.get("calculation_status") == "calculated":
            if not gate_is_approved(infiltration_gate):
                issues.append({"status": "blocked", "affected_id": room_id, "reason": "The infiltration method gate is not approved by a named HVAC engineer.", "source_artifact": "infiltration_method_gate.json"})
            else:
                timed_components["infiltration"] = float(infiltration.get("value") or 0)
                records.append(_source_record("project_evidence", _room_target(room_id, "infiltration"), infiltration.get("value"), infiltration.get("unit", ""), source_id=infiltration.get("component_id", ""), source=infiltration.get("source", ""), citations=infiltration.get("citations", []), rule="approved project-local infiltration input"))
        for component, magnitude in timed_components.items():
            if magnitude and not room["schedule_assignments"].get(component):
                issues.append({"status": "blocked", "affected_id": room_id, "reason": f"{component} schedule assignment is missing.", "source_artifact": "schedule_library.json"})
        for component in room.get("unapproved_components", []):
            component_status = component.get("calculation_status")
            if component_status == "not_present_confirmed" or (component.get("component_type") == "infiltration" and component_status == "calculated"):
                continue
            label = component.get("component_type") or component.get("component_id") or "unsupported room component"
            issues.append({
                "status": "draft", "affected_id": room_id,
                "reason": f"{label} is {component_status or 'not assessed'} and is excluded from Cooling V1 totals.",
                "source_artifact": "hourly_load_model.json",
            })
        _record_issues(records, issues, room_id)
        if room_static_missing(room, zones.get(room["zone_id"]), infiltration_gate):
            excluded.append(room_id)
        else:
            included.append(room_id)
        resolved.extend(records)
    return resolved, issues, sorted(set(included)), sorted(set(excluded))


def _apply_weather_defaults(scenarios, selected, cache, context):
    records, issues, lookup = [], [], {row["scenario_id"]: row for row in scenarios["scenarios"]}
    for scenario_id in selected:
        scenario = lookup.get(scenario_id)
        if not scenario:
            issues.append({"status": "blocked", "affected_id": scenario_id, "reason": "Selected cooling scenario is missing.", "source_artifact": "design_day_scenarios.json"})
            continue
        scope = {"country": "AU", "locality": context["site"].get("locality", ""), "state": context["site"].get("state", ""), "climate_zone": context["site"].get("climate_zone", ""), "scenario": scenario_id}
        profile = eligible_bindings(cache, "scenario.weather_profile", scope)
        if not scenario.get("hours") and len(profile) == 1 and isinstance(profile[0]["value"], dict):
            value = profile[0]["value"]
            hours = value.get("hours", [])
            if len(hours) == 24 and _value_present(value.get("atmospheric_pressure_kpa")):
                source = f"Approved default {profile[0]['record_id']}"
                scenario["hours"] = [{"hour": item["hour"], "outdoor_dry_bulb_c": {"value": item["outdoor_dry_bulb_c"], "status": "confirmed", "source": source, "citations": []}, "outdoor_wet_bulb_c": {"value": item["outdoor_wet_bulb_c"], "status": "confirmed", "source": source, "citations": []}} for item in hours]
                scenario["atmospheric_pressure_kpa"] = {"value": value["atmospheric_pressure_kpa"], "status": "confirmed", "source": source, "citations": []}
                scenario.update({"status": "confirmed", "source": source})
                citation = {"reference": profile[0].get("citation", profile[0]["record_id"]), "page": None, "excerpt": profile[0].get("excerpt", "")}
                records.append(_source_record("approved_default", f"scenarios.{scenario_id}.weather_profile", value, "profile", source_id=profile[0]["record_id"], source=profile[0].get("publisher", "Approved source pack"), citations=[citation], rule="approved scoped design-day weather profile"))
        missing, provisional = scenario_ready(scenario)
        for reason in missing:
            issues.append({"status": "blocked", "affected_id": scenario_id, "reason": reason, "source_artifact": "design_day_scenarios.json"})
        if provisional:
            issues.append({"status": "draft", "affected_id": scenario_id, "reason": "Selected cooling scenario contains provisional inputs.", "source_artifact": "design_day_scenarios.json"})
    return records, issues


def assemble_calculator_inputs(hourly_model, schedule_library, scenarios, selected_scenario_ids=None,
                               fusion=None, research_cache=None, envelope=None, *, project_context=None,
                               overrides=None, requirements=None, infiltration_gate=None):
    """Resolve cooling inputs without mutating the supplied project artifacts."""
    fingerprints = {name: _fingerprint(_stable(value or {})) for name, value in {
        "hourly_model": hourly_model, "schedule_library": schedule_library, "scenarios": scenarios, "fusion": fusion,
        "research_cache": research_cache, "envelope": envelope, "project_context": project_context,
        "overrides": overrides, "requirements": requirements, "infiltration_method_gate": infiltration_gate,
    }.items()}
    model, library, scenario_library = validate_hourly_load_model(deepcopy(hourly_model)), validate_schedule_library(deepcopy(schedule_library)), validate_design_day_scenarios(deepcopy(scenarios))
    checked_infiltration_gate = validate_infiltration_method_gate(infiltration_gate or {})
    cache = validate_cache(research_cache or {"schema_version": 1, "revision": 0, "records": []})
    context = validate_project_context(project_context)
    # Direct callers from the pre-context API retain a useful legacy readiness
    # assessment. The web API always supplies a context artifact, so new
    # projects still require an explicit conditioned-scope declaration.
    if project_context is None:
        context["conditioned_scope"] = {"status": "confirmed", "mode": "all_rooms", "room_ids": [], "source": "Legacy hourly-model scope", "citations": []}
        context["building_use"] = "legacy"
    checked_overrides = validate_overrides(overrides)
    selected = sorted(set(selected_scenario_ids or [row["scenario_id"] for row in scenario_library["scenarios"] if row.get("mode") == "cooling"]))
    weather_records, weather_issues = _apply_weather_defaults(scenario_library, selected, cache, context)
    room_records, room_issues, included, excluded = _apply_room_defaults(model, library, scenario_library, fusion or {}, cache, context, checked_overrides, selected, checked_infiltration_gate)
    issues = weather_issues + room_issues
    if not model["rooms"]:
        issues.append({"status": "blocked", "affected_id": "project", "reason": "No rooms are available for calculation.", "source_artifact": "hourly_load_model.json"})
    coverage = _coverage_summary(model, included, excluded, issues, weather_records + room_records, context)
    status = "blocked" if not included or any(row.get("status") == "blocked" for row in issues) else ("review_ready" if coverage["complete_scope"] and not issues else "draft")
    core = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION, "policy_version": context["default_policy_version"], "selected_scenario_ids": selected,
        "source_fingerprints": fingerprints, "source_pack_version": cache.get("source_pack_version", ""),
        "project_context": {"fingerprint": context["fingerprint"], "reviewer": context["reviewer"], "conditioned_scope": context["conditioned_scope"]},
        "status": status, "included_room_ids": included, "excluded_room_ids": excluded,
        "coverage_summary": coverage,
        "resolved_inputs": sorted(weather_records + room_records, key=lambda row: row["input_id"]), "issues": issues,
        "excluded_components": ["unresolved or unsupported airflow and moisture inputs remain excluded until an approved calculation method exists"],
        "payload": {"hourly_model": model, "schedule_library": library, "scenarios": scenario_library,
                    "infiltration_method_gate": deepcopy(checked_infiltration_gate)},
    }
    core["input_fingerprint"] = _fingerprint(_stable(core))
    result = deepcopy(core)
    result.update({"created_at": timestamp(), "snapshot_path": f"calculator_input_sets/{core['input_fingerprint']}.json"})
    result["research_defaults_available"] = [{"record_id": row["record_id"], "category": row["category"], "value": row["value"], "unit": row["unit"], "citation": row["citation"], "scope": row["scope"]} for row in cache["records"] if row.get("review_status") == "approved"]
    return result


def materialize_cooling_payload(input_set):
    if not isinstance(input_set, dict) or input_set.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("Unsupported calculator input-set schema.")
    payload = input_set.get("payload") or {}
    return (
        validate_hourly_load_model(deepcopy(payload.get("hourly_model", {}))),
        validate_schedule_library(deepcopy(payload.get("schedule_library", {}))),
        validate_design_day_scenarios(deepcopy(payload.get("scenarios", {}))),
    )
