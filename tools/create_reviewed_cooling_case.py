#!/usr/bin/env python3
"""Prepare a private, evidence-backed cooling workflow case.

This command never invents engineering inputs.  It reuses a reviewed evidence
packet, writes derived evidence artifacts to a local output directory, and
requires a named reviewer manifest before it will mark the case as approved.
The source PDF is referenced by path only and is never copied into the repo.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.building_evidence import build_building_evidence
from ai.calculator_draft import build_calculator_draft
from ai.drawing_coverage import build_drawing_coverage
from ai.thermal_model import build_thermal_evidence, build_thermal_model


def load(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else ({} if default is None else default)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def validate_manifest(manifest, source_pdf):
    required = ("project_id", "reviewer", "reviewed_at", "approved_scope", "unresolved_inputs", "exclusions")
    missing = [key for key in required if not str(manifest.get(key, "")).strip() and key not in {"unresolved_inputs", "exclusions"}]
    if missing:
        raise ValueError("Review manifest requires: " + ", ".join(missing))
    if not isinstance(manifest.get("unresolved_inputs"), list) or not isinstance(manifest.get("exclusions"), list):
        raise ValueError("Review manifest unresolved_inputs and exclusions must be lists.")
    if not source_pdf:
        raise ValueError("The evidence packet does not identify a source PDF.")
    if manifest.get("source_pdf") and Path(manifest["source_pdf"]).resolve() != Path(source_pdf).resolve():
        raise ValueError("Review manifest source_pdf does not match the evidence packet.")
    result = dict(manifest)
    result["source_pdf"] = source_pdf
    result["created_at"] = result.get("created_at") or timestamp()
    result["status"] = "reviewed_scope_recorded"
    return result


def prepare(source_dir, output_dir, manifest_path):
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    ai_input = load(source_dir / "ai_input.json")
    if not ai_input:
        raise ValueError(f"Missing ai_input.json in evidence packet: {source_dir}")
    spatial_ocr = load(source_dir / "spatial_ocr.json")
    vision_response = load(source_dir / "vision_response.json")
    coverage = load(source_dir / "drawing_coverage.json") or build_drawing_coverage(ai_input)
    building = build_building_evidence(ai_input, coverage, spatial_ocr, vision_response)
    thermal_evidence = build_thermal_evidence(ai_input, spatial_ocr, vision_response, coverage, building)
    thermal_model = build_thermal_model(thermal_evidence)
    manifest = validate_manifest(load(Path(manifest_path)), ai_input.get("source_pdf", ""))
    draft = build_calculator_draft(
        thermal_model, building, coverage, source_artifacts={
            name: str(source_dir / (name + ".json")) for name in ("thermal_model", "building_evidence", "drawing_coverage", "thermal_evidence")
        }, thermal_evidence=thermal_evidence,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "drawing_coverage.json": coverage,
        "building_evidence.json": building,
        "thermal_evidence.json": thermal_evidence,
        "thermal_model.json": thermal_model,
        "calculator_draft.json": draft,
        "review_manifest.json": manifest,
    }
    for name, value in artifacts.items():
        (output_dir / name).write_text(json.dumps(value, indent=2), encoding="utf-8")
    return {"output_dir": str(output_dir), "source_pdf": ai_input.get("source_pdf", ""),
            "review_manifest": manifest, "evidence_summary": {
                key: len(building.get(key, [])) for key in ("spaces", "levels", "surfaces", "openings", "constructions", "lighting", "equipment")
            }, "thermal_model_status": thermal_model.get("status", "review_required"),
            "next_action": "Review calculator_draft.json; unresolved candidates remain excluded until explicitly accepted."}


def main():
    parser = argparse.ArgumentParser(description="Prepare a private reviewed cooling workflow case from an evidence packet.")
    parser.add_argument("--source-dir", required=True, help="Local reviewed evidence packet containing ai_input.json")
    parser.add_argument("--output-dir", required=True, help="Ignored local output directory for derived artifacts")
    parser.add_argument("--review-manifest", required=True, help="JSON manifest with named reviewer and approved scope")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source_dir, args.output_dir, args.review_manifest), indent=2))


if __name__ == "__main__":
    main()
