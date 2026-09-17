"""API persistence for derived PDF calculation-input evidence."""

from copy import deepcopy
import json
import hashlib
from pathlib import Path

from ai.calculation_extraction import EXTRACTOR_VERSION, extract_calculation_input_evidence
from ai.calculator_draft import build_calculator_draft
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
    )


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


def _attach_to_fusion(root, evidence):
    """Expose the normalized register to the existing fusion/draft path."""
    path = root / "architect_evidence_fusion.json"
    if not path.exists():
        return
    fusion = _read(path)
    fusion["calculation_input_evidence"] = deepcopy(evidence)
    fusion["fingerprint"] = hashlib.sha256(json.dumps({key: value for key, value in fusion.items() if key != "fingerprint"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _write(path, fusion)


def get(web, project):
    root = Path(project["review_dir"])
    paths = _paths(root)
    evidence = _read(paths["calculation_input_evidence"], {"schema_version": 1, "status": "not_built", "candidates": [], "issues": []})
    current = source_fingerprint(_read(paths["ai_input"], {})) if paths["ai_input"].exists() else ""
    status = "current" if evidence.get("source_fingerprint") == current and current else ("stale" if paths["calculation_input_evidence"].exists() else "not_built")
    return {"id": project["id"], "calculation_input_evidence": evidence, "summary": _summary(evidence), "status": status, "artifact_url": web.safe_link(paths["calculation_input_evidence"]) if paths["calculation_input_evidence"].exists() else ""}


def post(web, project, data):
    root = Path(project["review_dir"])
    paths = _paths(root)
    action = data.get("action", "build")
    if action != "build":
        raise ValueError("Calculation-input evidence supports only the build action in this slice.")
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
    return {"id": project["id"], "calculation_input_evidence": stored, "summary": _summary(stored), "status": "current", "snapshot_reused": reused, "artifact_url": web.safe_link(paths["calculation_input_evidence"]), "calculator_draft": draft or {}, "calculator_draft_url": draft_url}
