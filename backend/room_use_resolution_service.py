"""Project lifecycle for draft-only AI room-use resolution."""

import json
from copy import deepcopy
from pathlib import Path

from ai import room_use_resolution
from ai import ai_preliminary
from backend.ai_preliminary_service import _proposal_for_resolution, _room_use_sources
from backend.vision_extraction_service import _atomic_json


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "artifact": root / "room_use_resolution.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
    }


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _sources(paths):
    return _room_use_sources(paths)


def _resolve(paths, existing=None):
    return room_use_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths), _sources(paths), existing,
    )


def _response(web, project):
    paths = _paths(project)
    raw = _read(paths["artifact"], room_use_resolution.empty_room_use_resolution())
    artifact = room_use_resolution.validate(raw)
    if artifact.get("records") and not room_use_resolution.is_current(artifact, _sources(paths)):
        artifact = deepcopy(artifact)
        artifact["status"] = "stale"
        artifact["stale_reasons"] = ["Drawing evidence, AI proposal, or room-use taxonomy changed. Resolve room uses again."]
    return {"id": project["id"], "room_use_resolution": artifact, "status": artifact.get("status", "needs_review"),
            "taxonomy": {key: value["label"] for key, value in room_use_resolution.load_taxonomy()["categories"].items()},
            "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else ""}


def get(web, project):
    return _response(web, project)


def _save(web, project, artifact, action, affected_ids=None):
    from backend import productization
    paths = _paths(project)
    before = _read(paths["artifact"], room_use_resolution.empty_room_use_resolution())
    checked = room_use_resolution.validate(artifact)
    _atomic_json(paths["artifact"], checked)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="room_use_resolution_" + action, target=paths["artifact"].name,
        previous_fingerprint=room_use_resolution.fingerprint(before), new_fingerprint=checked["fingerprint"],
        affected_ids=affected_ids or [],
    )
    project["room_use_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", ""))
    current = _read(paths["artifact"], room_use_resolution.empty_room_use_resolution())
    if action == "resolve":
        artifact = _resolve(paths, current)
        affected = [row["room_id"] for row in artifact.get("records", [])]
    elif action in {"apply_override", "clear_override"}:
        if current.get("records") and not room_use_resolution.is_current(room_use_resolution.validate(current), _sources(paths)):
            raise ValueError("Room-use evidence is stale. Resolve room uses again before changing a classification.")
        room_id = str(data.get("room_id", "")).strip()
        records = current.get("records", [])
        target = next((row for row in records if isinstance(row, dict) and row.get("room_id") == room_id), None)
        if target is None:
            raise ValueError("Room-use action references an unknown room.")
        if action == "apply_override":
            target["override"] = {"taxonomy_id": data.get("taxonomy_id"), "reviewer": str(data.get("reviewer", "")).strip(),
                                  "note": str(data.get("note", "")).strip(), "updated_at": ai_preliminary.now()}
            if not target["override"]["reviewer"]:
                raise ValueError("A room-use override needs a reviewer.")
        else:
            target["override"] = None
        artifact = _resolve(paths, current)
        affected = [room_id]
    else:
        raise ValueError("Room-use action must be resolve, apply_override, or clear_override.")
    return _save(web, project, artifact, action, affected)
