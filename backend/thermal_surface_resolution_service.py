"""Project-local opaque-envelope resolution over the shared thermal ledger."""

import json
import os
from copy import deepcopy
from pathlib import Path

from ai import ai_preliminary
from ai import thermal_surface_resolution as resolver
from ai import value_resolution
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
        "artifact": root / "thermal_surface_resolution.json",
        "geometry": root / "geometry_resolution.json",
        "value_resolution": root / "value_resolution.json",
        "proposal": root / "ai_preliminary_run.json",
        "input_evidence": root / "calculation_input_evidence.json",
        "weather": root / "site_design_weather_resolution.json",
    }


def _sources(paths):
    pack = ai_preliminary.load_pack()
    return {
        "geometry_resolution": ai_preliminary.fingerprint(_read(paths["geometry"], {})),
        "value_resolution": ai_preliminary.fingerprint(_read(paths["value_resolution"], {})),
        "ai_preliminary_proposal": ai_preliminary.fingerprint(_read(paths["proposal"], {})),
        "site_design_weather_resolution": ai_preliminary.fingerprint(_read(paths["weather"], {})),
        "preliminary_pack": ai_preliminary.fingerprint(pack),
    }


def _ledger(paths):
    geometry = _read(paths["geometry"], {})
    ledger = geometry.get("thermal_surface_ledger", {}) if isinstance(geometry, dict) else {}
    if isinstance(ledger, dict) and ledger.get("surfaces"):
        return ledger
    # Keep the endpoint useful for local placeholder-AI runs that have not yet
    # persisted geometry_resolution.json.
    proposal = _read(paths["proposal"], {}).get("manual_placeholder_proposal", {})
    surfaces = proposal.get("surfaces", []) if isinstance(proposal, dict) else []
    return {"schema_version": 1, "source_fingerprint": ai_preliminary.fingerprint(proposal), "surfaces": surfaces, "issues": []}


def _opening_register(paths):
    evidence = _read(paths["input_evidence"], {})
    return evidence.get("opening_register", {}) if isinstance(evidence, dict) else {}


def _resolve(paths, existing=None):
    sources = _sources(paths)
    artifact = resolver.resolve_opaque_envelope(
        _ledger(paths), _opening_register(paths),
        value_resolution=_read(paths["value_resolution"], value_resolution.empty_value_resolution()),
        source_pack=ai_preliminary.load_pack(), resolution_mode="preliminary_ai_estimate",
        weather_available=bool(_read(paths["weather"], {}).get("cooling", {}).get("selected")),
    )
    artifact["source_fingerprints"] = sources
    artifact["stale_reasons"] = []
    artifact["status"] = "draft" if artifact["summary"].get("included_count") else "blocked"
    artifact["fingerprint"] = resolver.fingerprint({key: artifact.get(key) for key in ("schema_version", "source_fingerprint", "resolution_mode", "surfaces", "issues", "summary", "source_fingerprints")})
    return artifact


def _current(paths, artifact):
    expected = _sources(paths)
    previous = artifact.get("source_fingerprints", {}) if isinstance(artifact, dict) else {}
    stale = [key for key, value in expected.items() if previous.get(key) != value]
    return expected, stale


def _response(web, project):
    paths = _paths(project)
    artifact = _read(paths["artifact"], {"surfaces": [], "issues": [], "summary": {}, "status": "not_resolved"})
    expected, stale = _current(paths, artifact)
    display = deepcopy(artifact)
    display["stale_reasons"] = stale
    display["status"] = "stale" if stale and display.get("surfaces") else display.get("status", "not_resolved")
    included = [row for row in display.get("surfaces", []) if row.get("thermal_eligible")]
    display["included_scope"] = {"surface_count": len(included), "area_m2": round(sum(float(row.get("net_opaque_area_m2") or 0) for row in included), 6)}
    return {
        "id": project["id"], "thermal_surface_resolution": display,
        "status": display.get("status", "not_resolved"), "stale_reasons": stale,
        "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else "",
        "source_fingerprints": expected,
    }


def get(web, project):
    return _response(web, project)


def _save(web, project, paths, before, artifact, action, affected=None):
    checked = deepcopy(artifact)
    _write(paths["artifact"], checked)
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="thermal_surface_resolution_" + action,
        target=paths["artifact"].name, previous_fingerprint=resolver.fingerprint(before),
        new_fingerprint=checked.get("fingerprint", ""), affected_ids=affected or [],
    )
    project["thermal_surface_resolution"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    return _response(web, project)


def _apply_override(artifact, data):
    surface_id = str(data.get("surface_id", "")).strip()
    row = next((item for item in artifact.get("surfaces", []) if item.get("surface_id") == surface_id), None)
    if row is None:
        raise ValueError("Opaque-envelope override references an unknown surface.")
    field = str(data.get("field", "")).strip()
    if field not in {"gross_area_m2", "net_opaque_area_m2", "construction_id", "u_value_w_m2k", "boundary_temperature_c", "boundary_temperature_profile", "opening_coverage_status"}:
        raise ValueError("Unsupported opaque-envelope override field.")
    row.setdefault("overrides", {})[field] = {"value": deepcopy(data.get("value")), "reviewer": str(data.get("reviewer", "")).strip(), "note": str(data.get("note", "")).strip(), "updated_at": ai_preliminary.now()}
    if not row["overrides"][field]["reviewer"]:
        raise ValueError("Opaque-envelope override requires a reviewer.")
    row[field] = deepcopy(data.get("value"))
    row.setdefault("unresolved_fields", [])
    row["unresolved_fields"] = [item for item in row["unresolved_fields"] if item not in {"surface_area_unresolved", "construction_id_missing", "u_value_missing", "boundary_temperature_missing", "opening_coverage_missing", "opening_coverage_incomplete"}]
    row["status"] = "reviewed"
    row["thermal_eligible"] = not row["unresolved_fields"]
    return artifact


def _propose_envelope_model(artifact):
    surfaces = []
    for row in artifact.get("surfaces", []):
        if not row.get("thermal_eligible") or row.get("physical_type") not in resolver.OPAQUE_TYPES:
            continue
        kind = "opaque_wall" if row["physical_type"] == "wall" else row["physical_type"]
        surfaces.append({
            "surface_id": row["surface_id"], "kind": kind,
            "owner_room_id": row.get("owner_room_id", ""), "owner_zone_id": row.get("owner_zone_id", ""),
            "area_m2": row.get("net_opaque_area_m2"), "area_basis": "net_opaque",
            "construction_id": row.get("construction_id", ""), "boundary_method": row.get("boundary_method", "external"),
            "boundary_temperature_c": row.get("boundary_temperature_c"), "adjacent_room_id": row.get("adjacent_room_id", ""),
            "review_status": "provisional", "geometry_mode": "preliminary_ai_estimate",
            "source": "thermal_surface_resolution proposal", "citations": row.get("citations", []),
            "physical_type": row.get("physical_type", ""), "thermal_role": row.get("thermal_role", ""),
            "classification_status": row.get("status", "proposed"), "classification_fingerprint": row.get("resolution_fingerprint", ""),
        })
    return {"active_for_calculation": False, "surfaces": surfaces, "status": "proposed", "source_fingerprint": artifact.get("fingerprint", "")}


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", "resolve"))
    before = _read(paths["artifact"], {"surfaces": [], "issues": [], "summary": {}})
    if action == "resolve":
        artifact = _resolve(paths, before)
        affected = [row.get("surface_id") for row in artifact.get("surfaces", [])]
    elif action == "apply_override":
        expected, stale = _current(paths, before)
        if stale:
            raise ValueError("Opaque-envelope evidence is stale. Resolve surfaces again before changing an override.")
        artifact = _apply_override(deepcopy(before), data)
        artifact["source_fingerprints"] = expected
        artifact["fingerprint"] = resolver.fingerprint(artifact)
        affected = [str(data.get("surface_id", ""))]
    elif action == "clear_override":
        surface_id = str(data.get("surface_id", "")).strip()
        field = str(data.get("field", "")).strip()
        if not any(item.get("surface_id") == surface_id for item in before.get("surfaces", [])):
            raise ValueError("Opaque-envelope override references an unknown surface.")
        # Rebuild from source evidence so the cleared field cannot retain a
        # stale materialised value or eligibility flag.
        artifact = _resolve(paths, before)
        affected = [surface_id]
    elif action == "propose_envelope_model":
        artifact = deepcopy(before)
        artifact["envelope_model_proposal"] = _propose_envelope_model(artifact)
        artifact["fingerprint"] = resolver.fingerprint(artifact)
        affected = []
    else:
        raise ValueError("Thermal-surface resolution action must be resolve, apply_override, clear_override, or propose_envelope_model.")
    return _save(web, project, paths, before, artifact, action, affected)
