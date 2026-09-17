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
from ai.evidence_fusion import build_evidence_fusion
from ai.calculator_draft import build_calculator_draft
from ai.drawing_coverage import build_drawing_coverage, evidence_fingerprint, source_fingerprint, timestamp
from ai.thermal_model import build_thermal_evidence, build_thermal_model
from ai.calculator_inputs import empty_overrides, empty_project_context, validate_overrides, validate_project_context
from ai.design_requirements import empty_design_requirements, empty_zone_ventilation_requirements
from ai.envelope import empty_envelope_library, empty_envelope_model
from ai.envelope_method_gates import (
    empty_ground_contact_method_gate,
    empty_dynamic_thermal_mass_method_gate,
    empty_solar_radiation_method_gate,
)
from ai.room_coupling import empty_room_coupling_method_gate
from ai.solar_radiation import empty_solar_radiation_source
from ai.glazing_gate import empty_glazing_method_gate
from ai.hourly_loads import build_hourly_load_model, empty_design_day_scenarios, empty_hourly_load_model, empty_schedule_library, validate_hourly_load_model
from ai.infiltration_gate import empty_infiltration_method_gate
from ai.research_cache import empty_research_cache
from ai.shading_gate import empty_shading_method_gate


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


def _citation_rows(candidate, document):
    rows = []
    for row in candidate.get("citations", []):
        if not isinstance(row, dict):
            continue
        rows.append({
            "reference": str(row.get("reference") or document),
            "page": row.get("page"),
            "excerpt": str(row.get("excerpt", "")),
        })
    return rows


def _bootstrap_hourly_model(draft, source_pdf):
    """Create a valid provisional overlay from source-backed draft topology.

    This is intentionally conservative: candidates become topology records only;
    geometry, areas, schedules, envelope properties, and load values stay empty.
    """
    floors = []
    floor_by_id = {}
    for candidate in draft.get("candidates", {}).get("floors", []):
        value = candidate.get("value") or {}
        floor_id = str(value.get("floor_id", "")).strip().lower()
        if not floor_id or floor_id in floor_by_id:
            continue
        row = {
            "floor_id": floor_id,
            "name": str(value.get("name") or floor_id).strip(),
            "elevation_m": value.get("elevation_m"),
            "verification_status": "provisional",
            "source": source_pdf,
            "citations": _citation_rows(candidate, source_pdf),
            "bridge_provenance": {
                "candidate_id": candidate.get("candidate_id", ""),
                "proposal_status": candidate.get("proposal_status", "pending_review"),
                "reason": candidate.get("reason", ""),
            },
        }
        floors.append(row)
        floor_by_id[floor_id] = row

    zones = []
    zone_by_id = {}
    for candidate in draft.get("candidates", {}).get("zones", []):
        value = candidate.get("value") or {}
        zone_id = str(value.get("zone_id", "")).strip().lower()
        floor_id = str(value.get("floor_id", "")).strip().lower()
        if not zone_id or zone_id in zone_by_id or floor_id not in floor_by_id:
            continue
        row = {
            "zone_id": zone_id,
            "name": str(value.get("name") or zone_id).strip(),
            "floor_id": floor_id,
            "ceiling_height_mm": None,
            "verification_status": "provisional",
            "source": source_pdf,
            "citations": _citation_rows(candidate, source_pdf),
            "bridge_provenance": {
                "candidate_id": candidate.get("candidate_id", ""),
                "proposal_status": candidate.get("proposal_status", "pending_review"),
                "reason": candidate.get("reason", ""),
            },
        }
        zones.append(row)
        zone_by_id[zone_id] = row

    # Build the full room shape through the existing model builder, then bind
    # only explicit draft room identities. This keeps all hourly-model defaults
    # and validation rules in one place.
    requirements = empty_design_requirements()
    requirements["zones"] = [{
        "zone_id": zone["zone_id"], "name": zone["name"], "usage": "",
        "source_room_labels": [], "area_m2": None, "occupancy": None,
        "heat_sources": [], "cooling_load": {},
        "ventilation_requirements": empty_zone_ventilation_requirements(),
    } for zone in zones]
    model = build_hourly_load_model(requirements) if zones else empty_hourly_load_model()
    model["floors"] = floors
    model["zones"] = zones
    room_candidates = {str((row.get("value") or {}).get("room_id", "")).strip().lower(): row
                       for row in draft.get("candidates", {}).get("rooms", [])
                       if str((row.get("value") or {}).get("room_id", "")).strip().lower()}
    area_candidates = {}
    for candidate in draft.get("candidates", {}).get("room_inputs", []):
        value = candidate.get("value") or {}
        room_id = str(value.get("room_id", "")).strip().lower()
        area = value.get("area_m2")
        if candidate.get("kind") == "area" and room_id and isinstance(area, (int, float)) and area > 0:
            area_candidates.setdefault(room_id, candidate)
    rooms = []
    for generated in model.get("rooms", []):
        zone_id = generated["zone_id"]
        candidate = next((row for row in room_candidates.values() if (row.get("value") or {}).get("zone_id") == zone_id), None)
        value = candidate.get("value") if candidate else {}
        room_id = str(value.get("room_id") or generated["room_id"]).strip().lower()
        generated["room_id"] = room_id
        generated["name"] = str(value.get("name") or generated["name"]).strip()
        generated["mapping_status"] = "inferred"
        generated["verification_status"] = "provisional"
        generated["source"] = source_pdf
        generated["citations"] = _citation_rows(candidate, source_pdf) if candidate else []
        area_candidate = area_candidates.get(room_id)
        generated["area_m2"] = (area_candidate.get("value", {}).get("area_m2")
                                 if area_candidate else None)
        generated["ceiling_height_mm"] = None
        generated["bridge_provenance"] = {
            "candidate_id": candidate.get("candidate_id", "") if candidate else "",
            "geometry_status": value.get("geometry_status", "label_detected") if candidate else "label_detected",
            "geometry_reference": value.get("geometry_reference", []) if candidate else [],
            "unresolved_fields": value.get("unresolved_fields", ["geometry", "area_m2", "floor_id"]) if candidate else ["geometry", "area_m2", "floor_id"],
            "proposal_status": candidate.get("proposal_status", "pending_review") if candidate else "pending_review",
        }
        if area_candidate:
            generated["bridge_provenance"]["area_candidate_id"] = area_candidate.get("candidate_id", "")
            generated["bridge_provenance"]["area_citations"] = _citation_rows(area_candidate, source_pdf)
        rooms.append(generated)
    model["rooms"] = rooms
    model["source_requirements_updated_at"] = ""
    return validate_hourly_load_model(model)


def bootstrap(source_dir, output_dir, manifest_path=None):
    """Create only missing local calculator artifacts for a reviewed packet.

    Existing files are never rewritten. The function is safe to run repeatedly
    and deliberately leaves conditioned scope and engineering inputs unresolved.
    """
    source_dir, output_dir = Path(source_dir).resolve(), Path(output_dir).resolve()
    ai_input = load(source_dir / "ai_input.json")
    if not ai_input:
        raise ValueError(f"Missing ai_input.json in evidence packet: {source_dir}")
    existing_manifest = load(output_dir / "review_manifest.json")
    manifest = load(Path(manifest_path)) if manifest_path else existing_manifest
    if manifest:
        manifest = validate_manifest(manifest, ai_input.get("source_pdf", ""))
    source_pdf = ai_input.get("source_pdf", "")
    # A bootstrap may use the source packet as its output directory, or may
    # write to a separate private case directory. Accept either layout.
    draft = load(output_dir / "calculator_draft.json") or load(source_dir / "calculator_draft.json")
    created = []
    output_dir.mkdir(parents=True, exist_ok=True)

    context_path = output_dir / "project_context.json"
    if not context_path.exists():
        context = empty_project_context()
        context["site"]["source"] = source_pdf
        context["reviewer"] = (manifest or {}).get("reviewer", "")
        context["conditioned_scope"]["source"] = ""
        context["bootstrap"] = {"source_pdf": source_pdf, "status": "provisional"}
        context_path.write_text(json.dumps(validate_project_context(context), indent=2), encoding="utf-8")
        created.append(context_path.name)

    overrides_path = output_dir / "calculator_input_overrides.json"
    if not overrides_path.exists():
        overrides_path.write_text(json.dumps(validate_overrides(empty_overrides()), indent=2), encoding="utf-8")
        created.append(overrides_path.name)

    model_path = output_dir / "hourly_load_model.json"
    if not model_path.exists():
        model = _bootstrap_hourly_model(draft, source_pdf)
        model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
        created.append(model_path.name)

    # Keep the artifact graph structurally complete without inventing any
    # schedules, weather, constructions, surfaces, or thermal properties.
    # Existing authored files are deliberately preserved byte-for-byte.
    empty_artifacts = {
        "schedule_library.json": empty_schedule_library(),
        "design_day_scenarios.json": empty_design_day_scenarios(),
        "envelope_library.json": empty_envelope_library(),
        "envelope_model.json": empty_envelope_model(),
        "research_cache.json": empty_research_cache(),
        "infiltration_method_gate.json": empty_infiltration_method_gate(),
        "glazing_method_gate.json": empty_glazing_method_gate(),
        "shading_method_gate.json": empty_shading_method_gate(),
        "ground_contact_method_gate.json": empty_ground_contact_method_gate(),
        "dynamic_thermal_mass_method_gate.json": empty_dynamic_thermal_mass_method_gate(),
        "solar_radiation_method_gate.json": empty_solar_radiation_method_gate(),
        "solar_radiation_source.json": empty_solar_radiation_source(),
        "room_to_room_coupling_method_gate.json": empty_room_coupling_method_gate(),
    }
    for name, value in empty_artifacts.items():
        path = output_dir / name
        if path.exists():
            continue
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        created.append(name)

    current_context = validate_project_context(load(context_path, empty_project_context()))
    scope = current_context.get("conditioned_scope", {})
    return {
        "output_dir": str(output_dir),
        "source_pdf": source_pdf,
        "created": created,
        "preserved": [name for name in ("project_context.json", "calculator_input_overrides.json", "hourly_load_model.json") if name not in created and (output_dir / name).exists()],
        "conditioned_scope": scope.get("status", "missing"),
        "status": "provisional",
        "message": "Bootstrap created only missing local artifacts; existing scope was preserved and engineering inputs remain unresolved.",
    }


def coverage_is_current(coverage, ai_input, spatial_ocr=None, vector_geometry=None):
    """Reject empty or derived coverage from a different source packet."""
    pages = ai_input.get("drawing_set", {}).get("pages", [])
    return (
        isinstance(coverage, dict)
        and coverage.get("version", 0) >= 4
        and coverage.get("source_pdf") == ai_input.get("source_pdf", "")
        and coverage.get("source_fingerprint") == source_fingerprint(ai_input)
        and (not (spatial_ocr or vector_geometry) or coverage.get("evidence_fingerprint") == evidence_fingerprint(ai_input, spatial_ocr, vector_geometry))
        and isinstance(coverage.get("sheet_register"), list)
        and len(coverage.get("sheet_register", [])) == len(pages)
        and isinstance(coverage.get("page_roles"), list)
        and all(isinstance(row, dict) and "capabilities" in row and "page_group" in row and "identity" in row and "capability_map" in row and "relevance" in row and "selection" in row for row in coverage.get("page_roles", []))
        and set(("version", "source_pdf", "source_fingerprint", "sheet_register", "levels", "coverage_exceptions")) <= set(coverage)
    )


def prepare(source_dir, output_dir, manifest_path):
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    ai_input = load(source_dir / "ai_input.json")
    if not ai_input:
        raise ValueError(f"Missing ai_input.json in evidence packet: {source_dir}")
    spatial_ocr = load(source_dir / "spatial_ocr.json")
    vision_response = load(source_dir / "vision_response.json")
    vector_geometry = load(source_dir / "vector_geometry.json")
    dimension_matches = load(source_dir / "dimension_wall_matches.json")
    geometry_confirmation = load(source_dir / "geometry_confirmation.json")
    existing_coverage = load(source_dir / "drawing_coverage.json")
    coverage = existing_coverage if coverage_is_current(existing_coverage, ai_input, spatial_ocr, vector_geometry) else build_drawing_coverage(
        ai_input, spatial_ocr=spatial_ocr, vector_geometry=vector_geometry
    )
    if existing_coverage is not coverage:
        coverage["rebuild_reason"] = "missing, empty, stale, or schema-incomplete derived coverage artifact"
        coverage["generated_at"] = timestamp()
    building = build_building_evidence(ai_input, coverage, spatial_ocr, vision_response)
    fusion = build_evidence_fusion(ai_input, coverage, building, spatial_ocr, vector_geometry, vision_response,
                                   dimension_matches, geometry_confirmation)
    thermal_evidence = build_thermal_evidence(ai_input, spatial_ocr, vision_response, coverage, building)
    thermal_model = build_thermal_model(thermal_evidence)
    manifest = validate_manifest(load(Path(manifest_path)), ai_input.get("source_pdf", ""))
    draft = build_calculator_draft(
        thermal_model, building, coverage, source_artifacts={
            name: str(source_dir / (name + ".json")) for name in ("thermal_model", "building_evidence", "drawing_coverage", "thermal_evidence")
        }, thermal_evidence=thermal_evidence, evidence_fusion=fusion,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "drawing_coverage.json": coverage,
        "building_evidence.json": building,
        "architect_evidence_fusion.json": fusion,
        "thermal_evidence.json": thermal_evidence,
        "thermal_model.json": thermal_model,
        "calculator_draft.json": draft,
        "review_manifest.json": manifest,
        "research_cache.json": empty_research_cache(),
    }
    for name, value in artifacts.items():
        (output_dir / name).write_text(json.dumps(value, indent=2), encoding="utf-8")
    return {"output_dir": str(output_dir), "source_pdf": ai_input.get("source_pdf", ""),
            "review_manifest": manifest, "evidence_summary": {
                key: len(building.get(key, [])) for key in ("spaces", "levels", "surfaces", "openings", "constructions", "lighting", "equipment")
            }, "thermal_model_status": thermal_model.get("status", "review_required"),
            "fusion_summary": {"pages": len(fusion["pages"]), "entities": len(fusion["entities"]), "facts": len(fusion.get("facts", [])), "conflicts": len(fusion["conflicts"]), "review_items": len(fusion["review_items"])},
            "next_action": "Review calculator_draft.json; unresolved candidates remain excluded until explicitly accepted."}


def main():
    parser = argparse.ArgumentParser(description="Prepare a private reviewed cooling workflow case from an evidence packet.")
    parser.add_argument("--source-dir", required=True, help="Local reviewed evidence packet containing ai_input.json")
    parser.add_argument("--output-dir", required=True, help="Ignored local output directory for derived artifacts")
    parser.add_argument("--review-manifest", help="JSON manifest with named reviewer and approved scope")
    parser.add_argument("--bootstrap", action="store_true", help="Create only missing project context, hourly model, and overrides")
    args = parser.parse_args()
    if args.bootstrap:
        print(json.dumps(bootstrap(args.source_dir, args.output_dir, args.review_manifest), indent=2))
    else:
        if not args.review_manifest:
            parser.error("--review-manifest is required unless --bootstrap is used")
        print(json.dumps(prepare(args.source_dir, args.output_dir, args.review_manifest), indent=2))


if __name__ == "__main__":
    main()
