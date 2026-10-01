"""Project context and provenance service for automatic AU ventilation rules."""

from copy import deepcopy
import json
from pathlib import Path

from ai import au_ventilation_rules, room_use_resolution
from ai.vision_extraction import timestamp
from backend.vision_extraction_service import _atomic_json


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "context": root / "project_regulatory_context.json",
        "resolution": root / "ventilation_rules_resolution.json",
        "location": root / "site_location_resolution.json",
        "requirements": root / "design_requirements.json",
    }


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)


def _context(project):
    paths = _paths(project)
    return au_ventilation_rules.validate_regulatory_context(
        _read(paths["context"], au_ventilation_rules.empty_regulatory_context())
    )


def _zone_room_use(zone):
    label = str(zone.get("usage") or "").strip()
    taxonomy = room_use_resolution.load_taxonomy()
    matches = room_use_resolution._label_matches(label, taxonomy) if label else []
    return matches[0] if len(matches) == 1 else ""


def resolve_project(project, requirements=None, context=None):
    paths = _paths(project)
    context = au_ventilation_rules.validate_regulatory_context(context if context is not None else _context(project))
    location = _read(paths["location"], {})
    requirements = requirements if requirements is not None else _read(paths["requirements"], {"zones": []})
    zones = [{"zone_id": zone.get("zone_id", ""), "room_use_category": _zone_room_use(zone)}
             for zone in requirements.get("zones", [])]
    resolution = au_ventilation_rules.resolve(location.get("location", {}), context, zones)
    resolution["source_fingerprints"] = {
        "location": location.get("fingerprint", ""),
        "context": au_ventilation_rules.fingerprint(context),
        "requirements": au_ventilation_rules.fingerprint(requirements),
        "ruleset": resolution["ruleset_fingerprint"],
    }
    resolution["fingerprint"] = au_ventilation_rules.fingerprint(resolution)
    return resolution


def get(web, project):
    paths = _paths(project)
    context = _context(project)
    requirements = _read(paths["requirements"], {"zones": []})
    resolution = resolve_project(project, requirements, context)
    return {
        "id": project["id"], "project_regulatory_context": context,
        "ventilation_rules_resolution": resolution,
        "ruleset_status": au_ventilation_rules.load_ruleset().get("status", "candidate"),
    }


def post(web, project, data):
    paths = _paths(project)
    context = au_ventilation_rules.validate_regulatory_context(data.get("project_regulatory_context", {}))
    before = _read(paths["context"], au_ventilation_rules.empty_regulatory_context())
    requirements = _read(paths["requirements"], {"zones": []})
    _atomic_json(paths["context"], context)
    project["project_regulatory_context"] = str(paths["context"])
    if paths["requirements"].exists():
        refreshed = refresh_requirements_for_context(web, project)
        requirements = refreshed["requirements"]
        resolution = refreshed["ventilation_rules_resolution"]
    else:
        resolution = resolve_project(project, requirements, context)
        resolution["source_fingerprints"] = {
            "location": _read(paths["location"], {}).get("fingerprint", ""),
            "context": au_ventilation_rules.fingerprint(context),
            "requirements": au_ventilation_rules.fingerprint(requirements),
            "ruleset": resolution["ruleset_fingerprint"],
        }
        resolution["fingerprint"] = au_ventilation_rules.fingerprint(resolution)
        _atomic_json(paths["resolution"], resolution)
    project["ventilation_rules_resolution"] = str(paths["resolution"])
    project["updated_at"] = timestamp()
    web.update_project(project)
    return {"id": project["id"], "project_regulatory_context": context,
            "ventilation_rules_resolution": resolution,
            "ruleset_status": au_ventilation_rules.load_ruleset().get("status", "candidate"),
            "changed": before != context}


def refresh_requirements_for_context(web, project):
    """Re-evaluate saved rules after location/context edits and stale auto-values safely."""
    paths = _paths(project)
    from ai.design_requirements import validate_design_requirements
    before = validate_design_requirements(_read(paths["requirements"], {"zones": []}))
    updated, resolution = apply_to_requirements(project, before)
    updated = validate_design_requirements(updated)
    if updated != before:
        _atomic_json(paths["requirements"], updated)
        if getattr(project, "get", None) and project.get("vision_response"):
            rebuilt = web.rebuild_reasoning_packet(project, paths["requirements"])
            project["reasoning_packet"] = rebuilt["reasoning_packet_raw"]
        project["design_requirements"] = str(paths["requirements"])
        project["updated_at"] = timestamp()
        web.update_project(project)
    return {"requirements": updated, "ventilation_rules_resolution": resolution}


def apply_to_requirements(project, requirements):
    """Apply only unique released rules into blank inputs; keep the resolution auditable."""
    paths = _paths(project)
    context = _context(project)
    location = _read(paths["location"], {})
    pack = au_ventilation_rules.load_ruleset()
    zones = [{"zone_id": zone.get("zone_id", ""), "room_use_category": _zone_room_use(zone)}
             for zone in requirements.get("zones", [])]
    resolution = au_ventilation_rules.resolve(location.get("location", {}), context, zones, pack)
    updated, outcomes = au_ventilation_rules.apply_matches(requirements, resolution, pack)
    resolution["application_outcomes"] = outcomes
    resolution["source_fingerprints"] = {
        "location": location.get("fingerprint", ""),
        "context": au_ventilation_rules.fingerprint(context),
        "requirements": au_ventilation_rules.fingerprint(requirements),
        "ruleset": resolution["ruleset_fingerprint"],
    }
    resolution["fingerprint"] = au_ventilation_rules.fingerprint(resolution)
    _atomic_json(paths["resolution"], resolution)
    project["ventilation_rules_resolution"] = str(paths["resolution"])
    return updated, resolution


def sync_hourly_model(model, requirements):
    return au_ventilation_rules.sync_hourly_model_outside_air(model, requirements)
