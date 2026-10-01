"""Project-scoped safety-factor policy service."""

from copy import deepcopy
import json
import os
from pathlib import Path

from ai import safety_factor_resolution
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
    return {"root": root, "artifact": root / "safety_factor_resolution.json", "requirements": root / "design_requirements.json", "context": root / "project_context.json", "pack": root / "ai_preliminary_run.json"}


def _sources(paths):
    requirements = _read(paths["requirements"], {})
    context = _read(paths["context"], {})
    run = _read(paths["pack"], {})
    sources = {"source_fingerprints": {"requirements": productization.fingerprint(requirements), "project_context": productization.fingerprint(context), "ai_preliminary_run": productization.fingerprint(run)}}
    for key in ("cooling_safety_factor", "heating_safety_factor", "safety_factor_policy", "cooling_engineer_instruction", "heating_engineer_instruction", "cooling_approved_rule", "heating_approved_rule"):
        if key in requirements:
            sources[key] = requirements[key]
        elif key in context:
            sources[key] = context[key]
        elif key in run:
            sources[key] = run[key]
    return sources


def _response(web, project):
    paths = _paths(project)
    artifact = safety_factor_resolution.validate(_read(paths["artifact"], safety_factor_resolution.empty_safety_factor_resolution()))
    return {"id": project["id"], "safety_factor_resolution": artifact, "status": artifact.get("status", "blocked"), "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else "", "policies": deepcopy(artifact.get("policies", {}))}


def get(web, project):
    return _response(web, project)


def _save(web, project, before, artifact, action):
    paths = _paths(project)
    artifact = safety_factor_resolution.validate(artifact)
    _write(paths["artifact"], artifact)
    productization.record_change_if_fingerprint_changed(paths["root"], action="safety_factor_" + action, target=paths["artifact"].name, previous_fingerprint=safety_factor_resolution.fingerprint(before), new_fingerprint=artifact["fingerprint"], affected_ids=list(artifact.get("policies", {})))
    project["safety_factor_resolution"] = str(paths["artifact"])
    project["updated_at"] = artifact.get("updated_at", "")
    web.update_project(project)
    return _response(web, project)


def post(web, project, data):
    paths = _paths(project)
    before = safety_factor_resolution.validate(_read(paths["artifact"], safety_factor_resolution.empty_safety_factor_resolution()))
    action = str(data.get("action", "resolve"))
    if action == "resolve":
        artifact = safety_factor_resolution.resolve(_sources(paths), pack_fingerprint=productization.fingerprint(_read(paths["pack"], {})))
    elif action == "apply_override":
        artifact = safety_factor_resolution.apply_override(before, str(data.get("mode", "cooling")), data.get("value", data.get("factor", data.get("percentage"))), data.get("input_kind", "percentage" if "percentage" in data else "factor"), data.get("source", "contractor override"), data.get("citations"), data.get("reviewer", ""), data.get("rationale", ""))
    elif action == "clear_override":
        mode = str(data.get("mode", "cooling"))
        # Rebuild from the current project sources so clearing an override
        # restores the newest automatic proposal (or the explicit draft
        # fallback) rather than leaving a stale locked value behind.
        artifact = safety_factor_resolution.resolve(_sources(paths), pack_fingerprint=productization.fingerprint(_read(paths["pack"], {})), mode_scope=[mode])
        for other in (set(safety_factor_resolution.MODES) - {mode}):
            artifact["policies"][other] = deepcopy(before.get("policies", {}).get(other, safety_factor_resolution.empty_safety_factor_resolution()["policies"][other]))
        artifact["fingerprint"] = safety_factor_resolution.fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"})
    elif action == "approve":
        artifact = safety_factor_resolution.approve(before, str(data.get("mode", "cooling")), data.get("engineer", data.get("reviewer", "")), data.get("approval_date", ""), data.get("scope", ""))
    else:
        raise ValueError("Safety-factor action must be resolve, apply_override, clear_override, or approve.")
    return _save(web, project, before, artifact, action)
