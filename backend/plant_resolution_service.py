"""Project-scoped AI preliminary plant and hydraulic resolution service."""

from copy import deepcopy
import json
import os
from pathlib import Path

from ai import ai_preliminary, plant_hydraulics, plant_resolution
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
        "artifact": root / "plant_resolution.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
        "ahu": root / "ahu_resolution.json",
        "ahu_systems": root / "ahu_systems.json",
        "airflow": root / "airflow_resolution.json",
        "weather": root / "site_design_weather_resolution.json",
        "schedules": root / "schedule_library.json",
        "plant_systems": root / "plant_systems.json",
        "circuits": root / "hydraulic_circuits.json",
        "gate": root / "plant_method_gate.json",
        "ahu_report": root / "hourly_ahu_load_report.json",
        "ahu_preliminary_report": root / "hourly_ai_preliminary_ahu_load_report.json",
        "preliminary_model": root / "plant_preliminary_model.json",
        "preliminary_report": root / "hourly_ai_preliminary_plant_load_report.json",
        "calculator_input_set": root / "calculator_input_set.json",
        "preliminary_input_set": root / "ai_preliminary_input_set.json",
    }


def _proposal(paths):
    from backend.room_proposal import room_proposal
    return room_proposal(_read(paths["run"], {}), paths["run"].parent)


def _known_ahu_ids(paths):
    artifact = _read(paths["ahu"], plant_resolution.empty_plant_resolution())
    ids = {str(row.get("ahu_id")) for row in artifact.get("systems", []) if isinstance(row, dict) and row.get("ahu_id")}
    if ids:
        return ids
    reviewed = _read(paths["ahu_systems"], {"systems": []})
    return {str(row.get("ahu_id")) for row in reviewed.get("systems", []) if isinstance(row, dict) and row.get("ahu_id")}


def _sources(paths):
    return {
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(_proposal(paths)),
        "ahu_resolution": ai_preliminary.fingerprint(_read(paths["ahu"], {})),
        "airflow_resolution": ai_preliminary.fingerprint(_read(paths["airflow"], {})),
        "weather": ai_preliminary.fingerprint(_read(paths["weather"], {})),
        "schedules": ai_preliminary.fingerprint(_read(paths["schedules"], {})),
        "plant_systems": ai_preliminary.fingerprint(_read(paths["plant_systems"], {})),
        "hydraulic_circuits": ai_preliminary.fingerprint(_read(paths["circuits"], {})),
        "plant_method_gate": ai_preliminary.fingerprint(_read(paths["gate"], {})),
        "preliminary_pack": ai_preliminary.fingerprint(ai_preliminary.load_pack()),
    }


def _resolve(paths, existing=None):
    return plant_resolution.validate(plant_resolution.resolve(
        vision=_read(paths["vision"], {}), proposal=_proposal(paths),
        known_ahu_ids=_known_ahu_ids(paths), pack=ai_preliminary.load_pack(),
        source_fingerprints=_sources(paths), existing=existing,
    ))


def _draft_citations(row):
    citations = deepcopy(row.get("citations", []))
    if not citations:
        citations = [{"reference": "Product-owned preliminary plant profile" if row.get("origin") == "controlled_fallback" else "AI preliminary plant resolution", "page": None, "excerpt": "Draft-only proposed input; not engineering evidence."}]
    return citations


def _add_draft_citations(material):
    for collection in (material["plant_systems"].get("systems", []), material["hydraulic_circuits"].get("circuits", [])):
        for row in collection:
            row["source"] = row.get("source") or "AI preliminary plant resolution"
            row["citations"] = _draft_citations(row)
            for key in ("pumps", "pipe_effects"):
                for child in row.get(key, []):
                    child["source"] = child.get("source") or "AI preliminary plant resolution"
                    child["citations"] = _draft_citations(child)
    return material


def _response(web, project):
    paths = _paths(project)
    artifact = plant_resolution.validate(_read(paths["artifact"], plant_resolution.empty_plant_resolution()))
    expected = _sources(paths)
    stale = [] if plant_resolution.is_current(artifact, expected) else sorted(key for key, value in expected.items() if artifact.get("source_fingerprints", {}).get(key) != value)
    display = deepcopy(artifact)
    display["stale_reasons"] = stale
    if stale and display.get("systems"):
        display["status"] = "stale"
    return {
        "id": project["id"], "plant_resolution": display, "status": display.get("status", "needs_review"),
        "stale_reasons": stale, "summary": display.get("summary", {}),
        "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else "",
        "preliminary_model_url": web.safe_link(paths["preliminary_model"]) if paths["preliminary_model"].exists() else "",
        "preliminary_report_url": web.safe_link(paths["preliminary_report"]) if paths["preliminary_report"].exists() else "",
    }


def get(web, project):
    return _response(web, project)


def _save(paths, before, artifact, action, affected=None):
    _write(paths["artifact"], artifact)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="plant_resolution_" + action, target=paths["artifact"].name,
        previous_fingerprint=plant_resolution.fingerprint(before), new_fingerprint=artifact.get("fingerprint", ""),
        affected_ids=affected or [row.get("plant_id") for row in artifact.get("systems", [])],
    )


def materialize_preliminary(web, project, artifact):
    paths = _paths(project)
    material = _add_draft_citations(plant_resolution.materialize(artifact, preliminary=True))
    _write(paths["preliminary_model"], material)
    response = {"id": project["id"], "plant_resolution": artifact, "preliminary_model": material, "status": "draft"}
    # Preliminary plant duty is downstream of the draft AHU result.  Prefer
    # the AI-preliminary snapshot, without promoting or rewriting reviewed data.
    ahu_report_path = paths["ahu_preliminary_report"] if paths["ahu_preliminary_report"].exists() else paths["ahu_report"]
    draft_snapshot = _read(paths["preliminary_input_set"], {}).get("input_fingerprint", "")
    snapshot = draft_snapshot or _read(paths["calculator_input_set"], {}).get("input_fingerprint", "")
    if not ahu_report_path.exists() or not snapshot:
        material["blocked_reason"] = "A current AHU report and calculator-input snapshot are required before preliminary plant duty."
        _write(paths["preliminary_model"], material)
        return response
    ahu_report = _read(ahu_report_path, {})
    expected_input_fingerprint = ahu_report.get("input_fingerprints", {}).get("calculator_input_snapshot", "")
    if expected_input_fingerprint and draft_snapshot and expected_input_fingerprint != draft_snapshot:
        material["blocked_reason"] = "The preliminary AHU report is stale relative to its input snapshot. Rebuild preliminary AHU duty before plant duty."
        _write(paths["preliminary_model"], material)
        return response
    gate = _read(paths["gate"], plant_hydraulics.empty_plant_method_gate())
    report = plant_hydraulics.calculate_plant_report(
        ahu_report, material["plant_systems"], material["hydraulic_circuits"], gate,
        snapshot_fingerprint=snapshot,
        preliminary_policy={"mode": "ai_preliminary", "allowed_review_statuses": {"confirmed", "provisional"}},
    )
    report["status"] = "draft"
    report["label"] = "AI preliminary estimate — not engineering reviewed or validated"
    report["input_fingerprints"] = {**_sources(paths), "plant_resolution": artifact.get("fingerprint", ""), "calculator_input_snapshot": snapshot}
    _write(paths["preliminary_report"], report)
    response["preliminary_report"] = report
    response["status"] = report.get("status", "draft")
    project["plant_preliminary_report"] = str(paths["preliminary_report"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return response


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", "resolve"))
    before = plant_resolution.validate(_read(paths["artifact"], plant_resolution.empty_plant_resolution()))
    if action in {"resolve", "rebuild"}:
        artifact = _resolve(paths, before)
        affected = [row.get("plant_id") for row in artifact.get("systems", [])]
    elif action == "apply_override":
        artifact = plant_resolution.apply_override(before, str(data.get("target_type", "plant")), str(data.get("target_id", "")), data.get("values", data.get("override", {})), data.get("reviewer", ""), data.get("note", ""))
        affected = [str(data.get("target_id", ""))]
    elif action == "clear_override":
        artifact = plant_resolution.clear_override(before, str(data.get("target_type", "plant")), str(data.get("target_id", "")))
        affected = [str(data.get("target_id", ""))]
    elif action == "materialize_preliminary":
        return materialize_preliminary(web, project, before)
    else:
        raise ValueError("Plant resolution action must be resolve, rebuild, apply_override, clear_override, or materialize_preliminary.")
    _save(paths, before, artifact, action, affected)
    project["plant_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)
