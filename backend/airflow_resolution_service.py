"""Project-local API orchestration for draft airflow resolution."""

import json
import os
from copy import deepcopy
from pathlib import Path

from ai import airflow_resolution as resolver
from ai import ai_preliminary
from ai import room_use_resolution
from ai import ceiling_volume_resolution
from backend import productization
from backend.ai_preliminary_service import _airflow_sources, _proposal_for_resolution


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
        "artifact": root / "airflow_resolution.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
        "room_use": root / "room_use_resolution.json",
        "ceiling_volume": root / "ceiling_volume_resolution.json",
        "site_weather": root / "site_design_weather_resolution.json",
        "research_cache": root / "research_cache.json",
        "value_resolution": root / "value_resolution.json",
        "source_pack": root / "ai_preliminary_assumption_pack.json",
    }


def _sources(paths):
    return _airflow_sources(paths)


def _resolve(paths, existing=None):
    room_use = room_use_resolution.validate(_read(paths["room_use"], room_use_resolution.empty_room_use_resolution()))
    ceiling = ceiling_volume_resolution.validate(_read(paths["ceiling_volume"], ceiling_volume_resolution.empty_ceiling_volume_resolution()))
    artifact = resolver.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths), room_use, ceiling,
        ai_preliminary.load_pack(), _sources(paths), existing,
    )
    return resolver.validate(artifact)


def _save(paths, before, artifact, action, affected=None):
    _write(paths["artifact"], artifact)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="airflow_resolution_" + action, target=paths["artifact"].name,
        previous_fingerprint=resolver.fingerprint(before), new_fingerprint=artifact.get("fingerprint", ""),
        affected_ids=affected or [row.get("airflow_id") for row in artifact.get("records", [])],
    )


def _response(web, project):
    paths = _paths(project)
    artifact = resolver.validate(_read(paths["artifact"], resolver.empty_airflow_resolution()))
    stale = _stale_reasons(paths, artifact)
    display = deepcopy(artifact)
    display["stale_reasons"] = stale
    if stale and display.get("records"):
        display["status"] = "stale"
    return {
        "id": project["id"], "airflow_resolution": display,
        "status": display.get("status", "needs_review"), "stale_reasons": stale,
        "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else "",
        "coverage": display.get("summary", {}),
    }


def _stale_reasons(paths, artifact):
    expected = _sources(paths)
    recorded = artifact.get("source_fingerprints", {}) if isinstance(artifact, dict) else {}
    return sorted(key for key in set(recorded) | set(expected) if recorded.get(key) != expected.get(key))


def get(web, project):
    return _response(web, project)


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", "resolve"))
    before = resolver.validate(_read(paths["artifact"], resolver.empty_airflow_resolution()))
    if action in {"apply_override", "clear_override"}:
        stale = _stale_reasons(paths, before)
        if stale:
            raise ValueError("Airflow inputs are stale (" + ", ".join(stale) + "). Resolve airflow inputs before editing.")
    if action in {"resolve", "rebuild"}:
        artifact = _resolve(paths, before)
        affected = [row.get("airflow_id") for row in artifact.get("records", [])]
    elif action == "apply_override":
        artifact = resolver.apply_override(before, str(data.get("room_id", "")), str(data.get("air_path_type", data.get("path_type", ""))), data.get("value"), data.get("reviewer", ""), data.get("note", ""))
        affected = [str(data.get("room_id", ""))]
    elif action == "clear_override":
        artifact = resolver.clear_override(before, str(data.get("room_id", "")), str(data.get("air_path_type", data.get("path_type", ""))))
        affected = [str(data.get("room_id", ""))]
    elif action == "queue_source_research":
        artifact = deepcopy(before)
        artifact.setdefault("research_jobs", []).append({"target": data.get("target", ""), "context": data.get("context", {}), "reason": data.get("reason", ""), "status": "queued", "consent_required": True})
        artifact["fingerprint"] = resolver.fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})
        affected = [str(data.get("target", ""))]
    else:
        raise ValueError("Airflow action must be resolve, rebuild, apply_override, clear_override, or queue_source_research.")
    _save(paths, before, artifact, action, affected)
    project["airflow_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)
