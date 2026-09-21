"""API persistence for derived PDF calculation-input evidence."""

from copy import deepcopy
import json
import hashlib
from pathlib import Path

from ai.calculation_extraction import EXTRACTOR_VERSION, evidence_input_fingerprints, extract_calculation_input_evidence
from ai.calculator_draft import build_calculator_draft
from ai import component_interpretations
from ai.drawing_coverage import source_fingerprint


def _read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else deepcopy(default or {})


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + ".stage")
    stage.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    stage.replace(path)


def _paths(root):
    return {name: root / f"{name}.json" for name in (
        "ai_input", "drawing_coverage", "spatial_ocr", "vector_geometry",
        "vision_response", "building_evidence", "dimension_wall_matches",
        "geometry_confirmation", "hourly_load_model", "calculation_input_evidence",
        "window_scan_register",
        "window_scan_reviews",
        "component_interpretations",
    )}


def _build(root):
    paths = _paths(root)
    if not paths["ai_input"].exists():
        raise ValueError("Analyse the architect PDF before extracting calculation inputs.")
    ai_input = _read(paths["ai_input"])
    return extract_calculation_input_evidence(
        ai_input,
        _read(paths["drawing_coverage"]),
        _read(paths["spatial_ocr"]),
        _read(paths["vector_geometry"]),
        _read(paths["vision_response"]),
        _read(paths["building_evidence"]),
        _read(paths["dimension_wall_matches"]),
        _read(paths["geometry_confirmation"]),
        window_scan=_read(paths["window_scan_register"]),
        window_reviews=_read(paths["window_scan_reviews"]),
    )


def input_artifacts_current(root, evidence):
    """Detect changed AI or drawing evidence before reusing a derived register."""
    expected = evidence.get("input_artifact_fingerprints")
    if not isinstance(expected, dict):
        return None  # Historical registers predate this dependency contract.
    paths = _paths(Path(root))
    current = evidence_input_fingerprints(*(_read(paths[name]) for name in (
        "ai_input", "drawing_coverage", "spatial_ocr", "vector_geometry", "vision_response",
        "building_evidence", "dimension_wall_matches", "geometry_confirmation",
    )))
    scan_current = evidence.get("opening_register", {}).get("window_scan_fingerprint", "")
    scan_now = _read(paths["window_scan_register"], {}).get("fingerprint", "")
    reviews_current = evidence.get("opening_register", {}).get("window_reviews_fingerprint", "")
    reviews_now = _read(paths["window_scan_reviews"], {}).get("fingerprint", "")
    return current == expected and scan_current == scan_now and reviews_current == reviews_now


def _summary(evidence):
    counts = {"active": 0, "proposed": 0, "blocked": 0, "conflict": 0, "evidence_only": 0}
    rooms = set()
    for row in evidence.get("candidates", []):
        counts[row.get("status", "proposed")] = counts.get(row.get("status", "proposed"), 0) + 1
        if row.get("room_id"):
            rooms.add(row["room_id"])
    return {
        "candidate_count": len(evidence.get("candidates", [])),
        "status_counts": counts,
        "affected_room_labels": sorted(rooms),
        "issue_count": len(evidence.get("issues", [])),
        "category_counts": evidence.get("categories", {}),
        "binding_observation_count": len((evidence.get("binding") or {}).get("observations", [])),
        "binding_relationship_count": len((evidence.get("binding") or {}).get("relationships", [])),
        "binding_conflict_count": len((evidence.get("binding") or {}).get("conflicts", [])),
    }


def _display_evidence(web, root, evidence):
    """Attach safe, optional previews without changing persisted evidence IDs."""
    display = deepcopy(evidence)
    root = Path(root).resolve()
    for opening in (display.get("opening_register") or {}).get("openings", []):
        for sighting in opening.get("sightings", []):
            sighting_page = sighting.get("page")
            if isinstance(sighting_page, int) and sighting_page > 0:
                sighting_thumbnail = root / "thumbnails" / f"page_{sighting_page:03d}.png"
                if sighting_thumbnail.is_file():
                    sighting["page_preview_url"] = web.safe_link(sighting_thumbnail)
        page = opening.get("page")
        if isinstance(page, int) and page > 0:
            thumbnail = root / "thumbnails" / f"page_{page:03d}.png"
            if thumbnail.resolve().is_relative_to(root) and thumbnail.is_file():
                opening["page_preview_url"] = web.safe_link(thumbnail)
        crop = opening.get("source_crop")
        if not isinstance(crop, str) or not crop.strip():
            continue
        candidate = (root / crop).resolve()
        if candidate.is_relative_to(root) and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and candidate.is_file():
            opening["crop_preview_url"] = web.safe_link(candidate)
        opening["source_crop"] = Path(crop).name
    return display


def _attach_to_fusion(root, evidence):
    """Expose the normalized register to the existing fusion/draft path."""
    path = root / "architect_evidence_fusion.json"
    if not path.exists():
        return
    fusion = _read(path)
    fusion["calculation_input_evidence"] = deepcopy(evidence)
    fusion["fingerprint"] = hashlib.sha256(json.dumps({key: value for key, value in fusion.items() if key != "fingerprint"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _write(path, fusion)


def _interpretation_inputs(root):
    paths = _paths(root)
    return (
        _read(root / "architect_evidence_fusion.json"),
        _read(paths["calculation_input_evidence"]),
        _read(root / "calculator_draft.json"),
    )


def _interpretations_response(root, web):
    paths = _paths(root)
    artifact = _read(paths["component_interpretations"])
    fusion, evidence, draft = _interpretation_inputs(root)
    current = component_interpretations.is_current(
        artifact, fusion=fusion, calculation_evidence=evidence, calculator_draft=draft,
    )
    return {
        "component_interpretations": artifact,
        "component_interpretations_status": "current" if current else ("stale" if artifact else "not_built"),
        "component_interpretations_url": web.safe_link(paths["component_interpretations"]) if paths["component_interpretations"].exists() else "",
    }


def _refresh_interpretations(root):
    paths = _paths(root)
    fusion, evidence, draft = _interpretation_inputs(root)
    existing = _read(paths["component_interpretations"])
    updated = component_interpretations.build_artifact(
        existing, fusion=fusion, calculation_evidence=evidence, calculator_draft=draft,
    )
    if existing.get("fingerprint") != updated.get("fingerprint"):
        _write(paths["component_interpretations"], updated)
    return updated


def get(web, project):
    root = Path(project["review_dir"])
    paths = _paths(root)
    evidence = _read(paths["calculation_input_evidence"], {"schema_version": 1, "status": "not_built", "candidates": [], "issues": []})
    current = source_fingerprint(_read(paths["ai_input"], {})) if paths["ai_input"].exists() else ""
    upstream_current = input_artifacts_current(root, evidence)
    status = "current" if evidence.get("source_fingerprint") == current and current and upstream_current is not False else ("stale" if paths["calculation_input_evidence"].exists() else "not_built")
    return {
        "id": project["id"], "calculation_input_evidence": _display_evidence(web, root, evidence),
        "summary": _summary(evidence), "status": status,
        "artifact_url": web.safe_link(paths["calculation_input_evidence"]) if paths["calculation_input_evidence"].exists() else "",
        **_interpretations_response(root, web),
    }


def post(web, project, data):
    root = Path(project["review_dir"])
    paths = _paths(root)
    action = data.get("action", "build")
    if action in {"apply_component_interpretations", "save_component_interpretation_review", "unlock_component_interpretation_field"}:
        paths = _paths(root)
        artifact = _read(paths["component_interpretations"])
        fusion, evidence, draft = _interpretation_inputs(root)
        before = artifact.get("fingerprint", "")
        current_fingerprints = component_interpretations.source_artifact_fingerprints(fusion, evidence, draft)
        actor = data.get("actor") or data.get("reviewer") or ("ai" if action == "apply_component_interpretations" else "local_user")
        if action == "apply_component_interpretations":
            artifact, affected_ids, conflict_ids = component_interpretations.apply_ai_updates(
                artifact, data.get("updates", []), source_artifact_fingerprints_value=current_fingerprints, actor=actor,
            )
            audit_action = "component_interpretations_ai_updated"
            result = {"affected_component_ids": affected_ids, "competing_update_component_ids": conflict_ids}
        elif action == "save_component_interpretation_review":
            artifact, component_id = component_interpretations.save_reviewer_change(
                artifact, data.get("interpretation", data.get("review", {})), actor=actor,
            )
            audit_action = "component_interpretation_review_saved"
            result = {"affected_component_ids": [component_id], "competing_update_component_ids": []}
        else:
            artifact, component_id = component_interpretations.unlock_and_restore_ai(
                artifact, str(data.get("component_id", "")), str(data.get("field", "")), actor=actor, note=data.get("note", ""),
            )
            audit_action = "component_interpretation_field_unlocked"
            result = {"affected_component_ids": [component_id], "competing_update_component_ids": []}
        _write(paths["component_interpretations"], artifact)
        after = artifact.get("fingerprint", "")
        if before != after:
            web.productization.append_audit_event(
                root, action=audit_action, target=paths["component_interpretations"].name,
                actor=actor, previous_fingerprint=before, new_fingerprint=after,
                affected_ids=result["affected_component_ids"], result="success",
            )
        return {"id": project["id"], **_interpretations_response(root, web), **result, "status": "current"}
    if action != "build":
        raise ValueError("Calculation-input evidence supports build and component-interpretation actions.")
    evidence = _build(root)
    existing = _read(paths["calculation_input_evidence"], {})
    reused = existing.get("fingerprint") == evidence.get("fingerprint") and existing.get("extractor_version") == EXTRACTOR_VERSION
    stored = evidence if not reused else existing
    if not reused:
        _write(paths["calculation_input_evidence"], stored)
    _attach_to_fusion(root, stored)
    draft_url = ""
    draft = None
    coverage = _read(paths["drawing_coverage"])
    building = _read(paths["building_evidence"])
    thermal = _read(root / "thermal_model.json")
    if coverage and building and thermal:
        fusion = _read(root / "architect_evidence_fusion.json")
        draft = build_calculator_draft(
            thermal, building, coverage,
            source_artifacts={name: str(root / f"{name}.json") for name in ("thermal_model", "building_evidence", "drawing_coverage", "thermal_evidence")},
            thermal_evidence=_read(root / "thermal_evidence.json"),
            evidence_fusion=fusion,
        )
        draft_path = root / "calculator_draft.json"
        _write(draft_path, draft)
        draft_url = web.safe_link(draft_path)
    interpretations = _refresh_interpretations(root)
    return {
        "id": project["id"], "calculation_input_evidence": _display_evidence(web, root, stored),
        "summary": _summary(stored), "status": "current", "snapshot_reused": reused,
        "artifact_url": web.safe_link(paths["calculation_input_evidence"]), "calculator_draft": draft or {},
        "calculator_draft_url": draft_url, "component_interpretations": interpretations,
        "component_interpretations_status": "current",
        "component_interpretations_url": web.safe_link(paths["component_interpretations"]),
    }
