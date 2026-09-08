"""Persistence for the architect evidence-fusion review artifact."""

from copy import deepcopy
from pathlib import Path
import json

from ai.drawing_coverage import source_fingerprint, timestamp
from ai.evidence_fusion import build_evidence_fusion
from ai.fact_registry import activate_safe_facts, validate_registry, fingerprint as fact_fingerprint


def _read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else deepcopy(default or {})


def _write(path, value):
    stage = path.with_name(path.name + ".stage")
    stage.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    stage.replace(path)


def _paths(root):
    return {name: root / (name + ".json") for name in ("ai_input", "drawing_coverage", "building_evidence", "spatial_ocr", "vector_geometry", "vision_response", "research_cache")}


def _current_source_fingerprint(root):
    paths = _paths(root)
    return source_fingerprint(_read(paths["ai_input"], {}))


def get(web, project):
    root = Path(project["review_dir"])
    path = root / "architect_evidence_fusion.json"
    fusion = _read(path, {"schema_version": 2, "status": "not_built"})
    current = _current_source_fingerprint(root) if paths_exist(root) else ""
    status = "current" if fusion.get("source_fingerprint") == current and current else ("stale" if path.exists() else "not_built")
    return {"id": project["id"], "architect_evidence_fusion": fusion, "status": status,
            "artifact_url": web.safe_link(path) if path.exists() else ""}


def paths_exist(root):
    return (root / "ai_input.json").exists()


def post(web, project, data):
    root = Path(project["review_dir"])
    paths = _paths(root)
    path = root / "architect_evidence_fusion.json"
    action = data.get("action", "build")
    if action in {"build", "build_ai_extraction"}:
        if not paths["ai_input"].exists() or not paths["building_evidence"].exists():
            raise ValueError("Build the architect evidence artifacts first.")
        fusion = build_evidence_fusion(_read(paths["ai_input"]), _read(paths["drawing_coverage"]), _read(paths["building_evidence"]), _read(paths["spatial_ocr"]), _read(paths["vector_geometry"]), _read(paths["vision_response"]))
        _write(path, fusion)
    else:
        fusion = _read(path)
        if not fusion:
            raise ValueError("Build the evidence-fusion artifact first.")
        if data.get("expected_revision") != fusion.get("fingerprint"):
            raise ValueError("Evidence-fusion revision changed; reload before saving review decisions.")
        if fusion.get("source_fingerprint") != _current_source_fingerprint(root):
            raise ValueError("Source evidence changed; rebuild the evidence-fusion artifact before reviewing.")
        if action == "save_extraction":
            incoming = data.get("facts") or (data.get("extraction") or {}).get("facts") or []
            if not isinstance(incoming, list):
                raise ValueError("Extraction facts must be a list.")
            known_pages = {row.get("page") for row in fusion.get("pages", [])}
            registry = validate_registry({"schema_version": 2, "facts": incoming, "conflicts": fusion.get("conflicts", []), "review_items": fusion.get("review_items", [])}, known_pages)
            by_id = {row.get("fact_id"): row for row in fusion.get("facts", [])}
            for fact in registry["facts"]:
                fact["source_type"] = fact.get("source_type") or "vision_extraction"
                by_id[fact["fact_id"]] = fact
            fusion["facts"] = sorted(by_id.values(), key=lambda row: row["fact_id"])
        elif action == "validate_facts":
            registry = validate_registry({"schema_version": 2, "facts": fusion.get("facts", []), "conflicts": fusion.get("conflicts", []), "review_items": fusion.get("review_items", [])}, {row.get("page") for row in fusion.get("pages", [])})
            return {"id": project["id"], "valid": True, "fact_registry": registry, "status": "current"}
        elif action == "apply_safe_facts":
            registry = activate_safe_facts({"schema_version": 2, "facts": fusion.get("facts", []), "conflicts": fusion.get("conflicts", []), "review_items": fusion.get("review_items", [])})
            fusion["facts"] = registry["facts"]
            fusion["fact_registry"] = {"schema_version": 2, "revision": registry.get("revision", 0), "fact_count": len(registry["facts"]), "fingerprint": fact_fingerprint(registry)}
        elif action not in {"save_review"}:
            raise ValueError("Unsupported evidence-fusion action: " + str(action))
        decisions = data.get("decisions", {})
        for collection in (fusion.get("pages", []), fusion.get("entities", []), fusion.get("conflicts", [])):
            for row in collection:
                decision = decisions.get(row.get("entity_id") or row.get("conflict_id") or f"page_{row.get('page')}")
                if decision:
                    row["review_decision"] = decision.get("decision", "pending")
                    row["reviewer"] = decision.get("reviewer", "")
                    row["reviewed_at"] = decision.get("reviewed_at") or timestamp()
        fusion["fingerprint"] = __import__("hashlib").sha256(json.dumps({key: value for key, value in fusion.items() if key != "fingerprint"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        fusion["updated_at"] = timestamp()
        _write(path, fusion)
    return {"id": project["id"], "architect_evidence_fusion": fusion, "artifact_url": web.safe_link(path), "status": "current"}
