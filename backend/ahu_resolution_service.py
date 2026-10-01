"""Project-scoped orchestration for AI preliminary AHU resolution."""

from copy import deepcopy
import json
import os
from pathlib import Path

from ai import ahu_resolution as resolver
from ai import ai_preliminary
from backend import productization


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".stage")
    staged.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(staged, path)


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "artifact": root / "ahu_resolution.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
        "airflow": root / "airflow_resolution.json",
        "pack": root / "ai_preliminary_assumption_pack.json",
        "model": root / "hourly_load_model.json",
        "weather": root / "site_design_weather_resolution.json",
        "schedules": root / "schedule_library.json",
        "preliminary_model": root / "ahu_preliminary_model.json",
        "preliminary_report": root / "hourly_ai_preliminary_ahu_load_report.json",
        "calculator_input_set": root / "calculator_input_set.json",
        "preliminary_input_set": root / "ai_preliminary_input_set.json",
        "room_report": root / "hourly_load_report.json",
        "preliminary_room_report": root / "hourly_ai_preliminary_load_report.json",
    }


def _proposal(paths):
    run = _read(paths["run"], {})
    proposal = run.get("local_room_inference_proposal") or run.get("manual_placeholder_proposal", run.get("manual_placeholder_entities", {}))
    return proposal if isinstance(proposal, dict) else {"rooms": proposal if isinstance(proposal, list) else []}


def _known_ids(paths):
    model = _read(paths["model"], {})
    rooms = {str(row.get("room_id")) for row in model.get("rooms", []) if isinstance(row, dict) and row.get("room_id")}
    zones = {str(row.get("zone_id")) for row in model.get("zones", []) if isinstance(row, dict) and row.get("zone_id")}
    return rooms, zones


def _sources(paths):
    rooms, zones = _known_ids(paths)
    return {
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(_proposal(paths)),
        "airflow_resolution": ai_preliminary.fingerprint(_read(paths["airflow"], {})),
        "room_ids": ai_preliminary.fingerprint(sorted(rooms)),
        "zone_ids": ai_preliminary.fingerprint(sorted(zones)),
        "weather": ai_preliminary.fingerprint(_read(paths["weather"], {})),
        "schedules": ai_preliminary.fingerprint(_read(paths["schedules"], {})),
        "preliminary_pack": ai_preliminary.fingerprint(ai_preliminary.load_pack()),
        "air_side_method_gate": ai_preliminary.fingerprint(_read(paths["root"] / "air_side_method_gate.json", {})),
    }


def _resolve(paths, existing=None):
    rooms, zones = _known_ids(paths)
    return resolver.validate(resolver.resolve(
        vision=_read(paths["vision"], {}), proposal=_proposal(paths),
        known_zone_ids=zones, known_room_ids=rooms,
        airflow_resolution=_read(paths["airflow"], {}),
        pack=ai_preliminary.load_pack(), source_fingerprints=_sources(paths), existing=existing,
    ))


def _response(web, project):
    paths = _paths(project)
    artifact = resolver.validate(_read(paths["artifact"], resolver.empty_ahu_resolution()))
    expected = _sources(paths)
    stale = [] if resolver.is_current(artifact, expected) else sorted(key for key, value in expected.items() if artifact.get("source_fingerprints", {}).get(key) != value)
    display = deepcopy(artifact)
    display["stale_reasons"] = stale
    if stale and display.get("systems"):
        display["status"] = "stale"
    return {
        "id": project["id"], "ahu_resolution": display,
        "status": display.get("status", "needs_review"), "stale_reasons": stale,
        "summary": display.get("summary", {}),
        "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else "",
        "preliminary_model_url": web.safe_link(paths["preliminary_model"]) if paths["preliminary_model"].exists() else "",
        "preliminary_report_url": web.safe_link(paths["preliminary_report"]) if paths["preliminary_report"].exists() else "",
    }


def get(web, project):
    return _response(web, project)


def _save(paths, before, artifact, action, affected=None):
    _write(paths["artifact"], artifact)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="ahu_resolution_" + action, target=paths["artifact"].name,
        previous_fingerprint=resolver.fingerprint(before), new_fingerprint=artifact.get("fingerprint", ""),
        affected_ids=affected or [row.get("ahu_id") for row in artifact.get("systems", [])],
    )


def _draft_citations(row):
    citations = deepcopy(row.get("citations", []))
    if not citations:
        reference = "Product-owned preliminary AHU profile" if row.get("origin") == "controlled_fallback" else "AI preliminary AHU resolution"
        citations = [{"reference": reference, "page": None, "excerpt": "Draft-only proposed input; not engineering evidence."}]
    return citations


def _scenario_value(value):
    """Unwrap preliminary scenario values without changing their provenance."""
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _attach_preliminary_weather(room_report, input_set):
    """Give the Stage 9 adapter its weather series from the same draft snapshot.

    The room-load report intentionally stores only room results; outdoor state
    lives in the materialized design-day scenario. Merge it into an isolated
    copy so reviewed reports and the source artifacts remain untouched.
    """
    report = deepcopy(room_report)
    material = input_set.get("material", {}) if isinstance(input_set, dict) else {}
    scenario_doc = material.get("design_day_scenarios", {}) if isinstance(material, dict) else {}
    scenario_rows = scenario_doc.get("scenarios", []) if isinstance(scenario_doc, dict) else []
    by_id = {str(row.get("scenario_id")): row for row in scenario_rows if isinstance(row, dict) and row.get("scenario_id")}
    for result in report.get("scenario_results", []):
        source = by_id.get(str(result.get("scenario_id")))
        if not source:
            continue
        result["atmospheric_pressure_kpa"] = _scenario_value(source.get("atmospheric_pressure_kpa"))
        result["hours"] = [{
            "hour": row.get("hour"),
            "outdoor_dry_bulb_c": _scenario_value(row.get("outdoor_dry_bulb_c")),
            "outdoor_wet_bulb_c": _scenario_value(row.get("outdoor_wet_bulb_c")),
        } for row in source.get("hours", []) if isinstance(row, dict)]
    return report


def materialize_preliminary(web, project, artifact):
    paths = _paths(project)
    project["ahu_resolution"] = str(paths["artifact"])
    project["ahu_preliminary_model"] = str(paths["preliminary_model"])
    material = resolver.materialize(artifact, preliminary=True)
    for system in material["systems"]["systems"]:
        system["citations"] = _draft_citations(system)
    for row in material["model"]["airflow_records"]:
        row["citations"] = _draft_citations(row)
    for kind in resolver.COMPONENT_TYPES:
        for row in material["model"][kind]:
            row["citations"] = _draft_citations(row)
    _write(paths["preliminary_model"], material)
    response = {"id": project["id"], "ahu_resolution": artifact, "preliminary_model": material, "status": "draft"}
    # Draft AHU assembly must consume the separate AI-preliminary room report
    # and snapshot when available; reviewed artifacts remain untouched.
    draft_report = paths["preliminary_room_report"]
    room_report_path = draft_report if draft_report.exists() else paths["room_report"] if paths["room_report"].exists() else None
    draft_snapshot = _read(paths["preliminary_input_set"], {}).get("input_fingerprint", "")
    snapshot = draft_snapshot or _read(paths["calculator_input_set"], {}).get("input_fingerprint", "")
    if not room_report_path or not snapshot:
        material["blocked_reason"] = "A current hourly room-load report and calculator-input snapshot are required before preliminary AHU duty."
        _write(paths["preliminary_model"], material)
        project["updated_at"] = ai_preliminary.now()
        web.update_project(project)
        return response
    from ai.ahu_airside import calculate_ahu_report
    room_report = _read(room_report_path, {})
    expected_input_fingerprint = room_report.get("preliminary_input_set_fingerprint", "")
    if expected_input_fingerprint and draft_snapshot and expected_input_fingerprint != draft_snapshot:
        material["blocked_reason"] = "The preliminary room report is stale relative to its input snapshot. Rebuild the preliminary calculation before AHU duty."
        _write(paths["preliminary_model"], material)
        return response
    if room_report_path == draft_report:
        room_report = _attach_preliminary_weather(room_report, _read(paths["preliminary_input_set"], {}))
    gate = _read(paths["root"] / "air_side_method_gate.json", {})
    report = calculate_ahu_report(
        room_report, material["systems"], material["model"], gate,
        snapshot_fingerprint=snapshot,
        preliminary_policy={"mode": "ai_preliminary", "allowed_review_statuses": {"confirmed", "provisional"}},
    )
    report["status"] = "draft"
    report["label"] = "AI preliminary estimate — not engineering reviewed or validated"
    report["input_fingerprints"] = {**_sources(paths), "ahu_resolution": artifact.get("fingerprint", ""), "calculator_input_snapshot": snapshot}
    _write(paths["preliminary_report"], report)
    response["preliminary_report"] = report
    response["status"] = report.get("status", "draft")
    project["ahu_preliminary_report"] = str(paths["preliminary_report"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return response


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", "resolve"))
    before = resolver.validate(_read(paths["artifact"], resolver.empty_ahu_resolution()))
    if action in {"resolve", "rebuild"}:
        artifact = _resolve(paths, before)
        affected = [row.get("ahu_id") for row in artifact.get("systems", [])]
    elif action == "apply_override":
        artifact = resolver.apply_override(before, str(data.get("target_type", "system")), str(data.get("target_id", "")), data.get("values", data.get("override", {})), data.get("reviewer", ""), data.get("note", ""))
        affected = [str(data.get("target_id", ""))]
    elif action == "clear_override":
        artifact = resolver.clear_override(before, str(data.get("target_type", "system")), str(data.get("target_id", "")))
        affected = [str(data.get("target_id", ""))]
    elif action == "materialize_preliminary":
        return materialize_preliminary(web, project, before)
    else:
        raise ValueError("AHU resolution action must be resolve, rebuild, apply_override, clear_override, or materialize_preliminary.")
    _save(paths, before, artifact, action, affected)
    project["ahu_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)
