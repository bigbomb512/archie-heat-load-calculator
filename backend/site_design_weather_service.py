"""Project lifecycle for licensed draft design-weather selection."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from ai.site_design_weather_resolution import (
    clear_selection, empty_site_design_weather_resolution, fingerprint, resolve_candidates,
    select_candidate, set_design_basis, validate_licensed_airah_pack,
    validate_site_design_weather_resolution,
)
from ai.vision_extraction import timestamp
from backend.vision_extraction_service import _atomic_json


PACK_PATH_ENV = "ARCHIE_AIRAH_DESIGN_WEATHER_PACK_PATH"


def _path(project):
    return Path(project["review_dir"]) / "site_design_weather_resolution.json"


def _location_path(project):
    return Path(project["review_dir"]) / "site_location_resolution.json"


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _licensed_pack(project):
    value = os.environ.get(PACK_PATH_ENV, "").strip()
    if not value:
        raise ValueError("No licensed AIRAH design-weather pack is configured. An administrator must import an authorised pack before design weather can be resolved.")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("The server-side licensed AIRAH design-weather pack path is unavailable.")
    try:
        path.resolve().relative_to(Path(project["review_dir"]).resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Licensed AIRAH design-weather data must be stored outside project review workspaces.")
    try:
        return validate_licensed_airah_pack(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The server-side licensed AIRAH design-weather pack could not be read.") from error


def _response(web, project):
    path = _path(project)
    artifact = validate_site_design_weather_resolution(_read(path, empty_site_design_weather_resolution()))
    location = _read(_location_path(project), {})
    if artifact.get("location_fingerprint") and artifact.get("location_fingerprint") != location.get("fingerprint"):
        artifact = deepcopy(artifact)
        artifact["status"] = "needs_review"
        artifact["stale_reasons"] = sorted(set(artifact.get("stale_reasons", []) + ["The confirmed project location changed after design weather was selected."]))
    return {"id": project["id"], "site_design_weather_resolution": artifact,
            "status": artifact["status"], "artifact_url": web.safe_link(path) if path.exists() else ""}


def get(web, project):
    return _response(web, project)


def _save(web, project, artifact, action):
    from backend import productization
    path = _path(project)
    before = _read(path, empty_site_design_weather_resolution())
    checked = validate_site_design_weather_resolution(artifact)
    _atomic_json(path, checked)
    productization.record_change_if_fingerprint_changed(
        path.parent, action="site_design_weather_" + action, target=path.name,
        previous_fingerprint=fingerprint(before), new_fingerprint=checked["fingerprint"], affected_ids=[project["id"]],
    )
    project["site_design_weather_resolution"] = str(path)
    project["updated_at"] = timestamp()
    web.update_project(project)
    return _response(web, project)


def post(web, project, data):
    current = _read(_path(project), empty_site_design_weather_resolution())
    location = _read(_location_path(project), {})
    action = str(data.get("action", ""))
    if action in {"select_candidate", "clear_selection"} and current.get("location_fingerprint") and current.get("location_fingerprint") != location.get("fingerprint"):
        raise ValueError("The confirmed project location changed. Resolve design-weather candidates again before selecting a profile.")
    if action == "set_design_basis":
        artifact = set_design_basis(current, data.get("design_basis"), location)
    elif action == "resolve_candidates":
        artifact = resolve_candidates(current, location, _licensed_pack(project))
    elif action == "select_candidate":
        artifact = select_candidate(current, data.get("scenario"), data.get("record_id"))
    elif action == "clear_selection":
        artifact = clear_selection(current, data.get("scenario"))
    else:
        raise ValueError("Design-weather action must be set_design_basis, resolve_candidates, select_candidate, or clear_selection.")
    return _save(web, project, artifact, action)
