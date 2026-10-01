"""Project-local API orchestration for internal-gains resolution."""

import json
import os
from copy import deepcopy
from pathlib import Path

from ai import internal_gains_resolution as resolver
from ai import ai_preliminary
from ai import room_use_resolution
from backend import productization
from backend.ai_preliminary_service import _internal_gains_sources, _proposal_for_resolution


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
        "artifact": root / "internal_gains_resolution.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
        "room_use": root / "room_use_resolution.json",
    }


def _sources(paths):
    return _internal_gains_sources(paths)


def _resolve(paths, existing=None):
    room_use = room_use_resolution.validate(_read(paths["room_use"], room_use_resolution.empty_room_use_resolution()))
    artifact = resolver.resolve(_read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths), room_use,
                               ai_preliminary.load_pack(), _sources(paths), existing)
    return resolver.validate(artifact)


def _save(paths, before, artifact, action, affected=None):
    _write(paths["artifact"], artifact)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action=action, target=paths["artifact"].name,
        previous_fingerprint=resolver.fingerprint(before), new_fingerprint=artifact["fingerprint"],
        affected_ids=affected or [row.get("room_id") for row in artifact.get("records", [])],
    )


def _response(web, project):
    paths = _paths(project)
    artifact = resolver.validate(_read(paths["artifact"], resolver.empty_internal_gains_resolution()))
    inputs_stale = bool(artifact.get("records")) and not resolver.is_current(artifact, _sources(paths), ai_preliminary.load_pack())
    stale_reasons = [
        "Room, building, room-use, or preliminary assumption-pack inputs changed. Resolve internal gains again before editing."
    ] if inputs_stale else list(artifact.get("stale_reasons", []))
    if inputs_stale:
        artifact = deepcopy(artifact)
        artifact["status"] = "stale"
        artifact["stale_reasons"] = stale_reasons
    return {
        "id": project["id"], "internal_gains_resolution": artifact,
        "status": "stale" if inputs_stale else artifact.get("status", "needs_review"),
        "stale_reasons": stale_reasons,
        "artifact_links": {"internal_gains_resolution": web.safe_link(paths["artifact"])} if paths["artifact"].exists() else {},
    }


def get(web, project):
    return _response(web, project)


def post(web, project, data):
    paths = _paths(project)
    action = data.get("action", "resolve")
    before = resolver.validate(_read(paths["artifact"], resolver.empty_internal_gains_resolution()))
    if action in {"resolve", "rebuild_schedules"}:
        artifact = _resolve(paths, before)
    elif action == "apply_override":
        if before.get("records") and not resolver.is_current(before, _sources(paths), ai_preliminary.load_pack()):
            raise ValueError("Internal-gains evidence is stale. Resolve internal gains again before changing an override.")
        artifact = resolver.apply_override(before, data.get("room_id", ""), data.get("field", ""), data.get("value"), data.get("reviewer", ""), data.get("note", ""))
    elif action == "clear_override":
        if before.get("records") and not resolver.is_current(before, _sources(paths), ai_preliminary.load_pack()):
            raise ValueError("Internal-gains evidence is stale. Resolve internal gains again before changing an override.")
        artifact = resolver.clear_override(before, data.get("room_id", ""), data.get("field"))
    else:
        raise ValueError("Internal-gains action must be resolve, apply_override, clear_override, or rebuild_schedules.")
    _save(paths, before, artifact, f"internal_gains_{action}", [data.get("room_id", "")] if data.get("room_id") else None)
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)
