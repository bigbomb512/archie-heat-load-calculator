"""Project-local orchestration for the draft-only AI preliminary cooling path."""

import json
import os
from pathlib import Path

from ai import ai_preliminary


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".stage")
    staged.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(staged, path)


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "settings": root / "ai_preliminary_settings.json",
        "run": root / "ai_preliminary_run.json",
        "model": root / "ai_preliminary_model.json",
        "sets": root / "ai_preliminary_input_sets",
        "current_set": root / "ai_preliminary_input_set.json",
        "report": root / "hourly_ai_preliminary_load_report.json",
        "building": root / "building_evidence.json",
        "vision": root / "vision_response.json",
        "fusion": root / "architect_evidence_fusion.json",
    }


def _sources(paths):
    run = _read(paths["run"], {})
    proposal = run.get("manual_placeholder_proposal", run.get("manual_placeholder_entities", []))
    return {
        "building_evidence": ai_preliminary.fingerprint(_read(paths["building"], {})),
        "vision_response": ai_preliminary.fingerprint(_read(paths["vision"], {})),
        "evidence_fusion": ai_preliminary.fingerprint(_read(paths["fusion"], {})),
        "manual_placeholder_entities": ai_preliminary.fingerprint(run.get("manual_placeholder_entities", [])),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(proposal),
    }


def _settings(paths):
    return ai_preliminary.validate_settings(_read(paths["settings"], ai_preliminary.empty_settings()))


def _save_settings(paths, incoming):
    current = _settings(paths)
    for key in ai_preliminary.empty_settings():
        if isinstance(incoming, dict) and key in incoming:
            current[key] = incoming[key]
    current["updated_at"] = ai_preliminary.now()
    current = ai_preliminary.validate_settings(current)
    _write(paths["settings"], current)
    return current


def _stale_reasons(paths, input_set):
    if not input_set:
        return []
    current = _sources(paths)
    previous = input_set.get("dependency_fingerprints", {})
    return [name for name, value in current.items() if previous.get(name) != value]


def _response(web, project):
    paths = _paths(project)
    settings = _settings(paths)
    input_set = _read(paths["current_set"], {})
    report = _read(paths["report"], {})
    stale_reasons = _stale_reasons(paths, input_set)
    run = _read(paths["run"], {})
    return {
        "id": project["id"], "settings": settings, "run": run,
        "model": _read(paths["model"], {}), "input_set": input_set,
        "hourly_ai_preliminary_load_report": report,
        "status": "stale" if stale_reasons else ("current" if input_set else "not_calculated"),
        "stale_reasons": stale_reasons,
        "provider_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "artifact_links": {name: web.safe_link(path) for name, path in paths.items()
                           if name in {"settings", "run", "model", "current_set", "report"} and path.exists()},
    }


def get(web, project):
    return _response(web, project)


def _assemble(web, project, source="manual_placeholder"):
    paths = _paths(project)
    building, vision = _read(paths["building"], {}), _read(paths["vision"], {})
    if not building:
        raise ValueError("Analyse the PDF before assembling an AI preliminary model.")
    existing_run = _read(paths["run"], {})
    proposal = existing_run.get("manual_placeholder_proposal", existing_run.get("manual_placeholder_entities", []))
    input_set = ai_preliminary.assemble(
        building, vision, source_fingerprints=_sources(paths), preliminary_proposal=proposal
    )
    input_set["run_source"] = source
    _write(paths["sets"] / f"{input_set['input_fingerprint']}.json", input_set)
    _write(paths["current_set"], input_set)
    model = {
        "schema_version": 1, "status": "draft", "label": input_set["label"],
        "input_fingerprint": input_set["input_fingerprint"], "topology": input_set["material"]["hourly_load_model"],
        "materialized_fields": input_set["materialized_fields"], "exclusions": input_set["exclusions"],
        "review_queue": input_set["review_queue"], "pack": input_set["pack"],
        "surface_summary": input_set.get("surface_summary", {}), "excluded_spaces": input_set.get("excluded_spaces", []),
        "dependency_fingerprints": input_set["dependency_fingerprints"],
    }
    _write(paths["model"], model)
    _write(paths["run"], {"schema_version": 1, "status": "assembled", "source": source, "finished_at": ai_preliminary.now(),
                           "input_fingerprint": input_set["input_fingerprint"], "provider_payload_stored": False,
                           "manual_placeholder_entities": input_set.get("proposal", {}).get("rooms", []),
                           "manual_placeholder_proposal": input_set.get("proposal", {}),
                           "manual_placeholder_fingerprint": ai_preliminary.fingerprint(input_set.get("proposal", {})),
                           "assumptions": input_set["materialized_fields"], "unresolved_components": input_set["exclusions"]})
    return input_set


def _calculate(web, project):
    paths = _paths(project)
    input_set = _read(paths["current_set"], {})
    if not input_set:
        raise ValueError("Assemble an AI preliminary model before calculating.")
    stale = _stale_reasons(paths, input_set)
    if stale:
        raise ValueError("AI preliminary inputs are stale because " + ", ".join(stale) + " changed. Assemble again before calculating.")
    report = ai_preliminary.calculate(input_set)
    _write(paths["report"], report)
    return report


def _auto_start_provider(web, project):
    """Start the existing bounded provider job only when all consent guards pass."""
    paths = _paths(project)
    settings = _settings(paths)
    configured = bool(os.environ.get("OPENAI_API_KEY"))
    status = ai_preliminary.provider_eligibility(settings, configured)
    if status != "eligible":
        _write(paths["run"], {"schema_version": 1, "status": status, "updated_at": ai_preliminary.now(),
                               "message": "No provider request was created."})
        return status
    # The evidence-only provider remains authoritative for transport, budget,
    # request manifests, and input redaction. The preliminary layer only asks
    # it to run using its already-bounded page selection.
    from backend import vision_extraction_service
    state = vision_extraction_service.get(web, project)
    group_ids = [item["group_id"] for item in state.get("available_groups", [])]
    if not group_ids:
        _write(paths["run"], {"schema_version": 1, "status": "awaiting_provider", "updated_at": ai_preliminary.now(),
                               "message": "No eligible ranked evidence groups are available."})
        return "awaiting_provider"
    try:
        vision_extraction_service.post(web, project, {"action": "start", "settings": {
            "owner_opt_in": True, "max_budget_aud": settings["maximum_provider_budget_aud"], "selected_group_ids": group_ids,
        }})
    except Exception as error:
        _write(paths["run"], {"schema_version": 1, "status": "provider_pending", "updated_at": ai_preliminary.now(), "message": str(error)[:500]})
        return "provider_pending"
    _write(paths["run"], {"schema_version": 1, "status": "provider_running", "updated_at": ai_preliminary.now(),
                           "message": "The bounded evidence-only provider job has started; preliminary assembly follows successful validation."})
    return "provider_running"


def after_pdf_analysis(web, project):
    """Called after local PDF analysis. It is deliberately a no-op by default."""
    if not project.get("review_dir"):
        return "not_available"
    return _auto_start_provider(web, project)


def provider_completed(web, project):
    """Called only after the existing provider result is validated/materialized."""
    paths = _paths(project)
    settings = _settings(paths)
    if not settings["automatic_analysis_enabled"]:
        return "disabled"
    _assemble(web, project, source="provider")
    _calculate(web, project)
    return "completed"


def post(web, project, data):
    paths = _paths(project)
    action = data.get("action", "get")
    # The UI submits the current settings with every action. Persisting them
    # here prevents a stale form from silently starting a provider job under
    # older consent or budget values.
    if action != "get" and isinstance(data.get("settings"), dict):
        _save_settings(paths, data["settings"])
    if action == "save_settings":
        pass
    elif action == "assemble":
        _assemble(web, project)
    elif action == "calculate":
        _calculate(web, project)
    elif action == "run":
        _auto_start_provider(web, project)
    elif action in {"save_manual_placeholder", "save_placeholder_proposal"}:
        raw_proposal = data.get("placeholder_proposal") if action == "save_placeholder_proposal" else data.get("manual_placeholder_entities")
        proposal = ai_preliminary.validate_placeholder_proposal(raw_proposal)
        prior = _read(paths["run"], {})
        _write(paths["run"], {"schema_version": 1, "status": "manual_placeholder_ready", "source": "manual_placeholder",
                               "updated_at": ai_preliminary.now(), "manual_placeholder_entities": proposal["rooms"],
                               "manual_placeholder_proposal": proposal,
                               "manual_placeholder_fingerprint": ai_preliminary.fingerprint(proposal),
                               "previous_input_fingerprint": prior.get("input_fingerprint", "")})
    else:
        raise ValueError("AI preliminary action must be save_settings, save_manual_placeholder, save_placeholder_proposal, run, assemble, or calculate.")
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)
