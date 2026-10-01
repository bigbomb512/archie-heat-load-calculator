"""Project lifecycle for draft-only ceiling-height and room-volume resolution."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from ai import ai_preliminary, ceiling_volume_resolution
from backend.vision_extraction_service import _atomic_json
from backend.ai_preliminary_service import _ceiling_sources, _preliminary_geometry, _proposal_for_resolution


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "artifact": root / "ceiling_volume_resolution.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json",
    }


def _geometry(paths):
    return _preliminary_geometry(paths)


def _sources(paths):
    return _ceiling_sources(paths)


def _resolve(paths, existing=None):
    return ceiling_volume_resolution.resolve(
        _read(paths["building"], {}), _read(paths["vision"], {}), _proposal_for_resolution(paths), _geometry(paths),
        ai_preliminary.load_pack(), _sources(paths), existing,
    )


def _response(web, project):
    paths = _paths(project)
    artifact = ceiling_volume_resolution.validate(_read(paths["artifact"], ceiling_volume_resolution.empty_ceiling_volume_resolution()))
    if artifact.get("records") and not ceiling_volume_resolution.is_current(artifact, _sources(paths), ai_preliminary.load_pack()):
        artifact = deepcopy(artifact)
        artifact["status"] = "stale"
        artifact["stale_reasons"] = ["Drawing evidence, AI proposal, geometry, or the preliminary assumption pack changed. Resolve ceiling heights again."]
    return {
        "id": project["id"], "ceiling_volume_resolution": artifact, "status": artifact.get("status", "needs_review"),
        "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else "",
    }


def get(web, project):
    return _response(web, project)


def _save(web, project, artifact, action, affected_ids=None):
    from backend import productization
    paths = _paths(project)
    before = _read(paths["artifact"], ceiling_volume_resolution.empty_ceiling_volume_resolution())
    checked = ceiling_volume_resolution.validate(artifact)
    _atomic_json(paths["artifact"], checked)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="ceiling_volume_resolution_" + action, target=paths["artifact"].name,
        previous_fingerprint=ceiling_volume_resolution.fingerprint(before), new_fingerprint=checked["fingerprint"],
        affected_ids=affected_ids or [],
    )
    project["ceiling_volume_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)


def post(web, project, data):
    paths = _paths(project)
    current = _read(paths["artifact"], ceiling_volume_resolution.empty_ceiling_volume_resolution())
    action = str(data.get("action", ""))
    if action == "resolve":
        artifact = _resolve(paths, current)
        affected = [row["room_id"] for row in artifact.get("records", [])]
    elif action in {"apply_override", "clear_override"}:
        checked = ceiling_volume_resolution.validate(current)
        if checked.get("records") and not ceiling_volume_resolution.is_current(checked, _sources(paths), ai_preliminary.load_pack()):
            raise ValueError("Ceiling-height evidence is stale. Resolve ceiling heights again before changing an override.")
        room_id = str(data.get("room_id", "")).strip()
        target = next((row for row in checked.get("records", []) if row.get("room_id") == room_id), None)
        if target is None:
            raise ValueError("Ceiling-height action references an unknown room.")
        if action == "apply_override":
            height = data.get("ceiling_height_mm")
            try:
                height = float(height)
            except (TypeError, ValueError) as error:
                raise ValueError("Ceiling-height override needs a positive millimetre value.") from error
            reviewer = str(data.get("reviewer", "")).strip()
            if height <= 0 or not reviewer:
                raise ValueError("Ceiling-height override needs a positive millimetre value and reviewer.")
            target["override"] = {"height_mm": height, "reviewer": reviewer, "note": str(data.get("note", "")).strip(), "updated_at": ai_preliminary.now()}
        else:
            target["override"] = None
        artifact = _resolve(paths, checked)
        affected = [room_id]
    else:
        raise ValueError("Ceiling-volume action must be resolve, apply_override, or clear_override.")
    return _save(web, project, artifact, action, affected)
