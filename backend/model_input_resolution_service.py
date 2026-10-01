"""Project-scoped orchestration for the shared model-input register."""

from copy import deepcopy
import json
import logging
import threading
from pathlib import Path

from ai import model_input_resolution as shared
from ai import value_resolution
from ai.design_requirements import validate_design_requirements
from ai.hourly_loads import validate_design_day_scenarios, validate_hourly_load_model, validate_schedule_library
from backend import ai_preliminary_service, productization
from backend.vision_extraction_service import _atomic_json

LOGGER = logging.getLogger(__name__)


_RESOLVER_LOCK = threading.Lock()
_PROJECT_LOCKS = {}


class ModelInputResolutionError(Exception):
    """Safe, structured failure for the consolidated draft resolver."""

    def __init__(self, *, code, domain, artifact, message, remediation, retryable=True,
                 status_code=422, affected_component_ids=None, missing_artifacts=None,
                 stale_reasons=None):
        super().__init__(message)
        self.code = code
        self.domain = domain
        self.artifact = artifact
        self.message = message
        self.remediation = remediation
        self.retryable = bool(retryable)
        self.status_code = int(status_code)
        self.affected_component_ids = list(affected_component_ids or [])
        self.missing_artifacts = list(missing_artifacts or [])
        self.stale_reasons = list(stale_reasons or [])


def error_payload(error):
    if isinstance(error, ModelInputResolutionError):
        return {
            "error": "Model input resolution could not complete.",
            "code": error.code, "domain": error.domain, "artifact": error.artifact,
            "message": error.message, "remediation": error.remediation,
            "retryable": error.retryable,
            "affected_component_ids": error.affected_component_ids,
            "missing_artifacts": error.missing_artifacts,
            "stale_reasons": error.stale_reasons,
        }
    return {
        "error": "Model input resolution could not complete.",
        "code": "resolver_unexpected_failure", "domain": "model inputs",
        "artifact": "model_input_resolution.json",
        "message": "The resolver hit an unexpected server error. Review the model-input status and retry.",
        "remediation": "Retry Resolve model inputs. If the problem persists, open the affected advanced editor.",
        "retryable": True, "affected_component_ids": [], "missing_artifacts": [], "stale_reasons": [],
    }


def _project_lock(project_id):
    with _RESOLVER_LOCK:
        return _PROJECT_LOCKS.setdefault(project_id, threading.Lock())


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)


def _write(path, value):
    _atomic_json(path, value)


_PRIVATE_KEYS = {"api_key", "access_token", "authorization", "raw_payload", "provider_payload", "filesystem_path", "artifact_path", "review_dir"}


def _public(value, *, key=""):
    """Remove host paths and provider secrets from browser-facing responses."""
    if isinstance(value, dict):
        return {name: _public(item, key=name) for name, item in value.items() if name not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [_public(item, key=key) for item in value]
    if isinstance(value, str) and key in {"source", "source_reference", "path", "url"} and value.startswith(("/", "~")):
        return "project-local source"
    return value


def _paths(project):
    root = Path(project["review_dir"])
    paths = ai_preliminary_service._paths(project)
    paths["register"] = root / "model_input_resolution.json"
    paths.update({
        "geometry_resolution": root / "geometry_resolution.json",
        "requirements": root / "design_requirements.json",
        "schedules": root / "schedule_library.json",
        "scenarios": root / "design_day_scenarios.json",
        "model": root / "hourly_load_model.json",
    })
    return paths


def _citation(row):
    citations = row.get("citations", row.get("evidence", [])) if isinstance(row, dict) else []
    if isinstance(citations, dict):
        citations = [citations]
    return citations if isinstance(citations, list) else []


def _domain_row(target, target_id, row, *, domain, value=None, unit="", field="", source_fingerprints=None):
    row = row if isinstance(row, dict) else {}
    origin = row.get("origin", "unresolved")
    status = row.get("status", "needs_review")
    confidence = row.get("confidence", row.get("confidence_score", row.get("confidence_band", 0.0)))
    derivation = row.get("derivation", {}) if isinstance(row.get("derivation", {}), dict) else {}
    source = row.get("source", row.get("source_reference", ""))
    return shared.normalize_record({
        "record_id": f"{domain}:{target}:{target_id}:{field or target}",
        "target": target, "target_id": target_id, "value": value,
        "unit": unit, "origin": origin, "status": status,
        "rationale": row.get("rationale", row.get("reason", f"{domain} resolver record.")),
        "confidence": confidence, "citation": {"source_reference": source, "citation": row.get("citation", ""), "excerpt": row.get("excerpt", ""), "content_hash": row.get("content_hash", "")},
        "citations": deepcopy(_citation(row)), "derivation": derivation,
        "formula": row.get("formula", derivation.get("formula", "")),
        "operands": row.get("operands", derivation.get("operands", {})),
        "affected_component_ids": row.get("affected_component_ids") or [target_id],
        "source_fingerprints": source_fingerprints or {},
        "remediation": row.get("remediation", row.get("remediation_action", "")),
    }, domain=domain, component_ids=row.get("affected_component_ids") or [target_id], source_fingerprints=source_fingerprints)


def _collect_domain_records(paths, source_fingerprints):
    records = []
    run = _read(paths["run"], {})
    proposal = run.get("manual_placeholder_proposal", {})
    if isinstance(proposal, list):
        proposal = {"rooms": proposal}

    # Keep the normalized values already produced by the existing resolver.
    base = _read(paths["value_resolution"], value_resolution.empty_value_resolution())
    records.extend(base.get("records", []))

    room_use = _read(paths["room_use"], {})
    for row in room_use.get("records", []):
        rid = row.get("room_id", "")
        records.append(_domain_row("room.profile", rid, row, domain="rooms and geometry", value=row.get("preliminary_profile_id", row.get("taxonomy_id")), unit="profile", field="profile", source_fingerprints=source_fingerprints))
        records.append(_domain_row("room.scope", rid, row, domain="rooms and geometry", value=row.get("space_scope"), unit="scope", field="scope", source_fingerprints=source_fingerprints))

    ceiling = _read(paths["ceiling_volume"], {})
    for row in ceiling.get("records", []):
        rid = row.get("room_id", "")
        if row.get("ceiling_height_mm") is not None:
            records.append(_domain_row("room.ceiling_height_mm", rid, row, domain="rooms and geometry", value=row.get("ceiling_height_mm"), unit="mm", field="ceiling_height_mm", source_fingerprints=source_fingerprints))
        if row.get("volume_m3") is not None:
            records.append(_domain_row("room.volume_m3", rid, row, domain="rooms and geometry", value=row.get("volume_m3"), unit="m3", field="volume_m3", source_fingerprints=source_fingerprints))

    internal = _read(paths["internal_gains"], {})
    for row in internal.get("records", []):
        rid = row.get("room_id", "")
        fields = row.get("fields", {}) if isinstance(row.get("fields", {}), dict) else {}
        for field_name, field_value in fields.items():
            if isinstance(field_value, dict):
                value, field_row = field_value.get("value"), {**row, **field_value}
            else:
                value, field_row = field_value, row
            if value is not None:
                records.append(_domain_row(f"room.{field_name}", rid, field_row, domain="internal gains and schedules", value=value, field=field_name, source_fingerprints=source_fingerprints))
        if row.get("schedule_id"):
            records.append(_domain_row("schedule.profile", rid, row, domain="internal gains and schedules", value=row.get("schedule_id"), unit="profile", field="schedule", source_fingerprints=source_fingerprints))

    surface_artifact = _read(paths["thermal_surface_resolution"], {})
    for row in surface_artifact.get("surfaces", surface_artifact.get("records", [])):
        sid = row.get("surface_id", row.get("candidate_id", ""))
        if not sid:
            continue
        for field_name, unit in (("construction_id", "assembly"), ("u_value_w_m2k", "W/m2K"), ("boundary_temperature_c", "C"), ("net_opaque_area_m2", "m2")):
            if row.get(field_name) is not None:
                records.append(_domain_row(f"surface.{field_name}", sid, row, domain="opaque envelope", value=row.get(field_name), unit=unit, field=field_name, source_fingerprints=source_fingerprints))

    for row in proposal.get("openings", []) if isinstance(proposal, dict) else []:
        oid = row.get("candidate_id", row.get("opening_id", row.get("label", "")))
        if not oid:
            continue
        for field_name, unit in (("opening_area_m2", "m2"), ("glass_area_m2", "m2"), ("u_value_w_m2k", "W/m2K"), ("shgc", ""), ("azimuth_deg", "deg"), ("external_shading_factor", "")):
            if row.get(field_name) is not None:
                records.append(_domain_row(f"opening.{field_name}", oid, row, domain="openings, glazing, and solar", value=row.get(field_name), unit=unit, field=field_name, source_fingerprints=source_fingerprints))

    airflow = _read(paths["airflow"], {})
    for row in airflow.get("records", []):
        aid = row.get("airflow_id", row.get("path_id", ""))
        kind = row.get("air_path_type", "airflow")
        target = {"outside_air": "airflow.outside_air_lps", "infiltration": "airflow.infiltration", "process_exhaust": "airflow.process_exhaust_lps", "make_up_air": "airflow.make_up_air_lps", "transfer_air": "airflow.transfer_air_lps", "supply": "airflow.supply_lps"}.get(kind, "airflow.path")
        value = row.get("airflow_lps", row.get("value"))
        if aid:
            records.append(_domain_row(target, aid, row, domain="ventilation, infiltration, and process air", value=value, unit="L/s", field=kind, source_fingerprints=source_fingerprints))

    ahu = _read(paths["ahu_resolution"], {})
    for row in ahu.get("systems", []) + ahu.get("airflow_records", []):
        aid = row.get("ahu_id", row.get("airflow_id", row.get("system_id", "")))
        if aid:
            records.append(_domain_row("ahu.system", aid, row, domain="AHU and air-side", value=row.get("system_type", row.get("airflow_lps")), unit="profile" if row.get("system_type") else "L/s", field="system", source_fingerprints=source_fingerprints))

    plant = _read(paths["plant_resolution"], {})
    for row in plant.get("systems", []) + plant.get("circuits", []) + plant.get("mappings", []):
        pid = row.get("plant_id", row.get("circuit_id", row.get("mapping_id", "")))
        if pid:
            records.append(_domain_row("plant.system", pid, row, domain="plant and hydraulics", value=row.get("capacity_kw", row.get("circuit_type", row.get("plant_type"))), unit="kW" if row.get("capacity_kw") is not None else "profile", field="system", source_fingerprints=source_fingerprints))

    safety = _read(paths["safety_factor_resolution"], {})
    for mode, row in (safety.get("policies", {}) or {}).items():
        records.append(_domain_row(f"safety.{mode}", mode, row, domain="final design policy", value=row.get("factor"), unit="multiplier", field=mode, source_fingerprints=source_fingerprints))
    # Keep the consolidated response useful even when a domain artifact has
    # not been created yet. These are explicit review placeholders, not
    # fabricated calculation values.
    domains = {
        "project context", "rooms and geometry", "internal gains and schedules",
        "opaque envelope", "openings, glazing, and solar",
        "ventilation, infiltration, and process air", "AHU and air-side",
        "plant and hydraulics", "final design policy",
    }
    present = {row.get("target_category") for row in records}
    for domain in sorted(domains - present):
        marker = domain.replace(" ", "-").replace(",", "")
        records.append(_domain_row(
            f"domain.{marker}", "project", {
                "origin": "unresolved", "status": "needs_review",
                "rationale": f"{domain} has not produced a normalized resolver record yet.",
                "remediation": f"Run or complete the {domain} resolver.",
            }, domain=domain, field="coverage", source_fingerprints=source_fingerprints,
        ))
    return records


def _dependencies(paths):
    return ai_preliminary_service._sources(paths)


def _room_area_coverage(paths):
    """Match in-scope rooms to explicit or active geometry-derived areas."""
    room_use = _read(paths["room_use"], {"records": []})
    geometry = _read(paths["geometry_resolution"], {})
    building = _read(paths["building"], {})
    run = _read(paths["run"], {})

    def key(label, level):
        return (" ".join(str(label or "").casefold().split()),
                " ".join(str(level or "").casefold().split()))

    area_keys = set()
    for entity in geometry.get("entities", []):
        if not isinstance(entity, dict) or entity.get("kind") != "area" or entity.get("geometry_status") not in {"ai_estimated", "geometry_confirmed"}:
            continue
        value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
        try:
            area = float(value.get("area_m2"))
        except (TypeError, ValueError):
            area = 0
        if area > 0:
            area_keys.add(key(entity.get("label"), entity.get("level_candidate")))
    for row in building.get("spaces", []):
        if not isinstance(row, dict):
            continue
        try:
            area = float(row.get("area_m2", row.get("area")))
        except (TypeError, ValueError):
            area = 0
        if area > 0:
            area_keys.add(key(row.get("name"), row.get("level_name")))
    proposal = run.get("local_room_inference_proposal") or run.get("manual_placeholder_proposal") or {}
    rows = proposal.get("rooms", []) if isinstance(proposal, dict) else proposal if isinstance(proposal, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            area = float(row.get("area_m2"))
        except (TypeError, ValueError):
            area = 0
        if area > 0:
            area_keys.add(key(row.get("label"), row.get("level_name")))

    eligible = [row for row in room_use.get("records", []) if isinstance(row, dict)
                and row.get("space_scope") in {"comfort_hvac", "comfort_hvac_with_process_exception"}
                and row.get("status") != "excluded"]
    missing = [row for row in eligible if key(row.get("original_label"), row.get("level_name")) not in area_keys]
    return eligible, missing


_ARTIFACT_SPECS = {
    "design_requirements": ("requirements", validate_design_requirements, "designRequirementsForm", "Complete the project inputs before calculating."),
    "schedule_library": ("schedule_library", validate_schedule_library, "calculatorInputSection", "Review the provisional schedule library."),
    "design_day_scenarios": ("design_day_scenarios", validate_design_day_scenarios, "siteDesignWeatherSection", "Review the provisional design-day weather scenario."),
    "hourly_load_model": ("hourly_load_model", validate_hourly_load_model, "roomsSection", "Review the provisional room and zone model."),
}


def _hydrate_preliminary_artifacts(web, project, paths, input_set):
    """Create missing legacy artifacts from a validated draft input set only."""
    material = input_set.get("material", {}) if isinstance(input_set, dict) else {}
    fingerprint = input_set.get("input_fingerprint", "")
    hydrated = []
    missing = []
    for key, (material_key, validator, target_id, remediation) in _ARTIFACT_SPECS.items():
        path_key = {"design_requirements": "requirements", "schedule_library": "schedules", "design_day_scenarios": "scenarios", "hourly_load_model": "model"}[key]
        path = paths.get(path_key)
        exists = bool(path and path.exists())
        item = {"key": key, "exists": exists, "hydrated": False, "provisional": False,
                "source": "existing" if exists else "ai_preliminary_input_set", "input_fingerprint": fingerprint,
                "can_hydrate": False, "target_id": target_id, "action_label": f"Open {key.replace('_', ' ').title()}",
                "remediation": remediation}
        if exists:
            item["status"] = "current"
            hydrated.append(item)
            continue
        raw = material.get(material_key)
        try:
            checked = validator(deepcopy(raw))
            if key == "design_requirements":
                eligible = bool(checked.get("zones"))
            elif key == "schedule_library":
                eligible = bool(checked.get("schedules"))
            elif key == "design_day_scenarios":
                eligible = bool(checked.get("scenarios"))
            else:
                eligible = bool(checked.get("rooms")) and bool(checked.get("zones"))
            if not eligible:
                raise ValueError("No eligible room or zone scope was materialized.")
            checked["verification"] = checked.get("verification", {})
            checked["provisional"] = True
            checked["verification_status"] = "provisional"
            checked["source"] = "ai_preliminary_input_set"
            checked["input_fingerprint"] = fingerprint
            _atomic_json(path, checked)
            project[{"design_requirements": "design_requirements", "schedule_library": "schedule_library", "design_day_scenarios": "design_day_scenarios", "hourly_load_model": "hourly_load_model"}[key]] = str(path)
            item.update({"exists": True, "hydrated": True, "provisional": True, "can_hydrate": True, "status": "hydrated"})
        except (ValueError, TypeError, KeyError, OSError) as error:
            item.update({"status": "missing", "can_hydrate": False,
                         "remediation": f"{remediation} The draft input set is not complete for this artifact."})
            item["message"] = "Materialized preliminary content is incomplete for this artifact."
            missing.append(item)
        hydrated.append(item)
    if any(item.get("hydrated") for item in hydrated):
        project["updated_at"] = ai_preliminary_service.ai_preliminary.now()
        try:
            web.update_project(project)
        except Exception:
            pass
    return hydrated, missing


def _row_depends_on(row, changed):
    category = (row.get("target_category") or "").casefold()
    dependency_keys = {
        "project context": {"building_evidence", "site_location_resolution", "site_design_weather_resolution"},
        "rooms and geometry": {"building_evidence", "vision_response", "preliminary_geometry_resolution", "room_use_resolution", "ceiling_volume_resolution"},
        "internal gains and schedules": {"building_evidence", "vision_response", "ai_preliminary_proposal", "internal_gains_resolution"},
        "opaque envelope": {"building_evidence", "vision_response", "thermal_surface_resolution", "preliminary_geometry_resolution"},
        "openings, glazing, and solar": {"vision_response", "ai_preliminary_proposal", "thermal_surface_resolution", "site_location_resolution", "site_design_weather_resolution"},
        "ventilation, infiltration, and process air": {"airflow_resolution", "room_use_resolution", "ceiling_volume_resolution", "site_design_weather_resolution"},
        "AHU and air-side": {"ahu_resolution", "airflow_resolution", "site_design_weather_resolution"},
        "plant and hydraulics": {"plant_resolution", "ahu_resolution", "airflow_resolution", "site_design_weather_resolution"},
        "final design policy": {"safety_factor_resolution", "building_evidence", "ai_preliminary_proposal"},
    }
    keys = dependency_keys.get(category, set(changed))
    return bool(keys.intersection(changed))


def _build(web, project):
    paths = _paths(project)
    try:
        from backend import room_inference_service
        room_state = room_inference_service.get(web, project).get("room_inference", {})
        if room_state.get("status") in {"queued", "running"}:
            raise ModelInputResolutionError(
                code="room_inference_pending", domain="rooms and geometry", artifact="room_inference_job.json",
                message="Room detection is still running, so model inputs are not ready yet.",
                remediation="Wait for room detection to finish, then retry Resolve model inputs.",
                retryable=True, status_code=202,
            )
    except ModelInputResolutionError:
        raise
    except (ImportError, AttributeError, KeyError, TypeError):
        pass
    # This call runs the existing room/geometry/ceiling/internal/airflow path.
    try:
        ai_preliminary_service._resolve_from_packs(paths, project)
    except Exception as error:
        raise ModelInputResolutionError(
            code="resolver_domain_failed", domain="rooms and geometry", artifact="room_use_resolution.json",
            message="Room and geometry inputs could not be normalized from the current evidence.",
            remediation="Open the room and geometry editor, resolve the listed boundary issue, then retry.",
            retryable=True, affected_component_ids=[],
        ) from error
    # Domain resolvers remain authoritative and are allowed to return empty or
    # needs_review artifacts when their evidence is absent.
    for module_name in ("ahu_resolution_service", "plant_resolution_service", "safety_factor_resolution_service"):
        try:
            module = __import__(f"backend.{module_name}", fromlist=["post"])
            module.post(web, project, {"action": "resolve"})
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as error:
            domain = {"ahu_resolution_service": ("AHU and air-side", "ahu_resolution.json", "Open the AHU and air-side editor and resolve the listed ownership issue."),
                      "plant_resolution_service": ("plant and hydraulics", "plant_resolution.json", "Open the plant editor and resolve the listed circuit or ownership issue."),
                      "safety_factor_resolution_service": ("final design policy", "safety_factor_resolution.json", "Open the final design-policy editor and provide a cited factor.")}[module_name]
            raise ModelInputResolutionError(code="resolver_domain_failed", domain=domain[0], artifact=domain[1],
                                            message=f"{domain[0]} could not be normalized from the current evidence.",
                                            remediation=domain[2], retryable=True) from error
    dependencies = _dependencies(paths)
    records = _collect_domain_records(paths, dependencies)
    register = shared.build_register(records, dependency_fingerprints=dependencies)
    current = value_resolution.validate_value_resolution(_read(paths["value_resolution"], value_resolution.empty_value_resolution()))
    current["records"] = register["records"]
    current["coverage_summary"] = register["coverage_summary"]
    current["review_queue"] = register["review_queue"]
    current["affected_component_ids"] = register["affected_component_ids"]
    current["dependency_fingerprints"] = dependencies
    current["source_fingerprints"] = dependencies
    current["updated_at"] = ai_preliminary_service.ai_preliminary.now()
    current["fingerprint"] = value_resolution.fingerprint({key: value for key, value in current.items() if key != "fingerprint"})
    previous_register = _read(paths["register"], shared.empty_register())
    _write(paths["value_resolution"], current)
    _write(paths["register"], register)
    productization.record_change_if_fingerprint_changed(paths["root"], action="model_input_resolution_rebuilt", target=paths["register"].name, previous_fingerprint=previous_register.get("fingerprint", ""), new_fingerprint=register["fingerprint"], affected_ids=register["affected_component_ids"])
    eligible_rooms, rooms_missing_area = _room_area_coverage(paths)
    if eligible_rooms and len(rooms_missing_area) == len(eligible_rooms):
        labels = [str(row.get("original_label") or row.get("room_id")) for row in rooms_missing_area]
        raise ModelInputResolutionError(
            code="room_area_unresolved", domain="rooms and geometry", artifact="geometry_resolution.json",
            message="Room identities were detected, but no comfort-scope room has a validated area. No heat-load report was generated.",
            remediation=("Complete the cited inside-face boundaries and dimension chains for " + ", ".join(labels) +
                         ". Do not use the drawing scale; then rebuild model inputs."),
            retryable=True, status_code=422,
            affected_component_ids=[str(row.get("room_id")) for row in rooms_missing_area if row.get("room_id")],
            missing_artifacts=["hourly_load_model"],
        )
    try:
        input_set = ai_preliminary_service._assemble(web, project)
        hydrated, missing = _hydrate_preliminary_artifacts(web, project, paths, input_set)
    except ModelInputResolutionError:
        raise
    except Exception as error:
        LOGGER.exception("preliminary input-set assembly failed")
        detail = str(error).casefold()
        if "schedule" in detail or "citation" in detail:
            failed_domain, failed_artifact, failed_remediation = ("internal gains and schedules", "schedule_library.json", "Open the schedule editor and resolve the cited schedule or operating-hours issue.")
        elif "scenario" in detail or "weather" in detail:
            failed_domain, failed_artifact, failed_remediation = ("project context", "design_day_scenarios.json", "Open the design-weather editor and resolve the current draft scenario.")
        elif "room" in detail or "zone" in detail or "geometry" in detail:
            failed_domain, failed_artifact, failed_remediation = ("rooms and geometry", "hourly_load_model.json", "Open the room and geometry editor and resolve the affected room or zone.")
        else:
            failed_domain, failed_artifact, failed_remediation = ("calculation readiness", "ai_preliminary_input_set.json", "Review the room, geometry, schedule, weather, and design-input exceptions, then retry.")
        raise ModelInputResolutionError(
            code="resolver_domain_failed", domain=failed_domain, artifact=failed_artifact,
            message="The preliminary model could not validate one of the required calculation inputs.",
            remediation=failed_remediation,
            retryable=True, missing_artifacts=[item["key"] for item in _required_artifact_fallback(paths)],
        ) from error
    register["required_artifacts"] = hydrated
    register["missing_artifacts"] = [item["key"] for item in missing]
    _write(paths["register"], register)
    return register, current


def _required_artifact_fallback(paths):
    return [{"key": key} for key in _ARTIFACT_SPECS if not paths[{"design_requirements": "requirements", "schedule_library": "schedules", "design_day_scenarios": "scenarios", "hourly_load_model": "model"}[key]].exists()]


def _response(web, project):
    paths = _paths(project)
    register = _read(paths["register"], shared.empty_register())
    current_dependencies = _dependencies(paths)
    previous = register.get("dependency_fingerprints", {})
    changed = sorted(key for key, value in current_dependencies.items() if previous.get(key) != value)
    stale = bool(changed and register.get("records"))
    affected = sorted({item for row in register.get("records", []) if _row_depends_on(row, changed) for item in row.get("affected_component_ids", [])})
    resolution = _read(paths["value_resolution"], value_resolution.empty_value_resolution())
    required_artifacts = web.workflow_required_artifacts(project, paths) if hasattr(web, "workflow_required_artifacts") else []
    return _public({
        "id": project["id"], "status": "stale" if stale else ("current" if register.get("records") else "not_resolved"),
        "stale_reasons": changed, "affected_component_ids": affected,
        "model_input_resolution": register, "value_resolution": resolution,
        "coverage_summary": register.get("coverage_summary", {}), "review_queue": register.get("review_queue", []),
        "required_artifacts": required_artifacts,
        "preliminary_status": ai_preliminary_service.get(web, project).get("status", "not_calculated"),
        "artifact_links": {
            "value_resolution": web.safe_link(paths["value_resolution"]) if paths["value_resolution"].exists() else "",
            "model_input_resolution": web.safe_link(paths["register"]) if paths["register"].exists() else "",
        },
    })


def get(web, project):
    return _response(web, project)


def post(web, project, data):
    action = str(data.get("action", "resolve"))
    paths = _paths(project)
    if action in {"resolve", "rebuild_preliminary_model"}:
        lock = _project_lock(project["id"])
        if not lock.acquire(blocking=False):
            raise ModelInputResolutionError(
                code="resolver_concurrent", domain="model inputs", artifact="model_input_resolution.json",
                message="Another model-input resolution is already running for this project.",
                remediation="Wait for the current resolution to finish, then retry.",
                retryable=True, status_code=409,
            )
        try:
            register, _resolution = _build(web, project)
        finally:
            lock.release()
        if action == "rebuild_preliminary_model":
            ai_preliminary_service.post(web, project, {"action": "rebuild_preliminary_model"})
        return {**_response(web, project), "model_input_resolution": register}
    if action == "queue_research":
        result = ai_preliminary_service.post(web, project, {"action": "queue_missing_source_research", "research_consent": bool(data.get("research_consent"))})
        return {**_response(web, project), "value_resolution": result.get("value_resolution", {})}
    if action == "accept_research_candidate":
        current = value_resolution.validate_value_resolution(_read(paths["value_resolution"], value_resolution.empty_value_resolution()))
        updated = value_resolution.accept_research_candidate(current, data.get("candidate", data.get("research_candidate", {})))
        _write(paths["value_resolution"], updated)
        with _project_lock(project["id"]):
            _build(web, project)
        return _response(web, project)
    raise ValueError("Model-input resolution action must be resolve, queue_research, accept_research_candidate, or rebuild_preliminary_model.")
