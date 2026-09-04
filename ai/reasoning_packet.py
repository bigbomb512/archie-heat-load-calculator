#!/usr/bin/env python3

import argparse
import json
import shutil
import zipfile
from pathlib import Path

from ai.ai_packet import load_json
from ai.coordinate_review import create_coordinate_review
from ai.dimension_wall_matcher import page_mm_per_px
from ai.geometry_confirmation import create_geometry_confirmation
from ai.geometry_review import normalise_vision
from ai.design_requirements import empty_design_requirements, requirements_summary
from ai.vision_validator import validate_vision_file


def create_reasoning_packet(
    ai_input_path,
    coordinate_review_path,
    chatgpt_packet_dir=None,
    output_dir=None,
    zip_packet=True,
    vector_geometry_path=None,
    dimension_wall_matches_path=None,
    candidate_review_path=None,
    vision_response_path=None,
    vision_validation_path=None,
    geometry_confirmation_path=None,
    design_requirements_path=None,
    heat_load_report_path=None,
    ventilation_report_path=None,
    thermal_evidence_path=None,
    thermal_model_path=None,
):
    ai_input_path = Path(ai_input_path)
    coordinate_review_path = Path(coordinate_review_path)
    ai_input = load_json(ai_input_path)
    coordinate_review = load_json(coordinate_review_path)
    vector_geometry = load_json(vector_geometry_path) if vector_geometry_path else {}
    dimension_wall_matches = load_json(dimension_wall_matches_path) if dimension_wall_matches_path else {}
    candidate_review = load_json(candidate_review_path) if candidate_review_path else {}
    vision_response = load_json(vision_response_path) if vision_response_path else {}
    vision_response = normalise_vision(vision_response, candidate_review)
    vision_validation = load_json(vision_validation_path) if vision_validation_path else {}
    geometry_confirmation = load_json(geometry_confirmation_path) if geometry_confirmation_path else {}
    requirements = load_json(design_requirements_path) if design_requirements_path and Path(design_requirements_path).exists() else empty_design_requirements()
    heat_load_report = load_json(heat_load_report_path) if heat_load_report_path and Path(heat_load_report_path).exists() else {}
    ventilation_report = load_json(ventilation_report_path) if ventilation_report_path and Path(ventilation_report_path).exists() else {}
    thermal_evidence = load_json(thermal_evidence_path) if thermal_evidence_path and Path(thermal_evidence_path).exists() else {}
    thermal_model = load_json(thermal_model_path) if thermal_model_path and Path(thermal_model_path).exists() else {}
    chatgpt_packet_dir = Path(chatgpt_packet_dir or ai_input_path.with_name("chatgpt_packet"))
    output_dir = Path(output_dir or ai_input_path.with_name("reasoning_packet"))
    output_dir.mkdir(parents=True, exist_ok=True)

    screenshots = copy_folder_images(chatgpt_packet_dir / "screenshots", output_dir / "screenshots")
    overlays = copy_overlay_images(coordinate_review, output_dir / "overlays")
    vector_overlays = copy_vector_overlay_images(vector_geometry, output_dir / "vector_overlays")
    dimension_match_overlays = copy_dimension_match_overlay_images(dimension_wall_matches, output_dir / "dimension_match_overlays")
    candidate_overlays = copy_candidate_overlay_images(candidate_review, output_dir / "candidate_overlays")
    geometry_confirmation_overlays = copy_geometry_confirmation_overlay_images(geometry_confirmation, output_dir / "geometry_confirmation_overlays")

    ai_copy = output_dir / "ai_input.json"
    coordinate_copy = output_dir / "coordinate_review.json"
    vector_copy = output_dir / "vector_geometry.json"
    dimension_matches_copy = output_dir / "dimension_wall_matches.json"
    candidate_review_copy = output_dir / "candidate_review.json"
    vision_response_copy = output_dir / "vision_response.json"
    vision_validation_copy = output_dir / "vision_validation.json"
    geometry_confirmation_copy = output_dir / "geometry_confirmation.json"
    requirements_copy = output_dir / "design_requirements.json"
    heat_load_report_copy = output_dir / "heat_load_report.json"
    ventilation_report_copy = output_dir / "ventilation_report.json"
    thermal_evidence_copy = output_dir / "thermal_evidence.json"
    thermal_model_copy = output_dir / "thermal_model.json"
    prompt = output_dir / "prompt.md"
    manifest = output_dir / "manifest.json"

    ai_copy.write_text(json.dumps(ai_input, indent=2), encoding="utf-8")
    coordinate_copy.write_text(json.dumps(coordinate_review, indent=2), encoding="utf-8")
    if vector_geometry:
        vector_copy.write_text(json.dumps(vector_geometry, indent=2), encoding="utf-8")
    if dimension_wall_matches:
        dimension_matches_copy.write_text(json.dumps(dimension_wall_matches, indent=2), encoding="utf-8")
    if candidate_review:
        candidate_review_copy.write_text(json.dumps(candidate_review, indent=2), encoding="utf-8")
    if vision_response:
        vision_response_copy.write_text(json.dumps(vision_response, indent=2), encoding="utf-8")
    if vision_validation:
        vision_validation_copy.write_text(json.dumps(vision_validation, indent=2), encoding="utf-8")
    if geometry_confirmation:
        geometry_confirmation_copy.write_text(json.dumps(geometry_confirmation, indent=2), encoding="utf-8")
    requirements_copy.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
    if heat_load_report:
        heat_load_report_copy.write_text(json.dumps(heat_load_report, indent=2), encoding="utf-8")
    elif heat_load_report_copy.exists():
        heat_load_report_copy.unlink()
    if ventilation_report:
        ventilation_report_copy.write_text(json.dumps(ventilation_report, indent=2), encoding="utf-8")
    elif ventilation_report_copy.exists():
        ventilation_report_copy.unlink()
    if thermal_evidence:
        thermal_evidence_copy.write_text(json.dumps(thermal_evidence, indent=2), encoding="utf-8")
    elif thermal_evidence_copy.exists():
        thermal_evidence_copy.unlink()
    if thermal_model:
        thermal_model_copy.write_text(json.dumps(thermal_model, indent=2), encoding="utf-8")
    elif thermal_model_copy.exists():
        thermal_model_copy.unlink()
    prompt.write_text(
        build_reasoning_prompt(ai_input, coordinate_review, vector_geometry, dimension_wall_matches, screenshots, overlays, vector_overlays, dimension_match_overlays, candidate_review, candidate_overlays, vision_response, vision_validation, geometry_confirmation, requirements, heat_load_report, ventilation_report, thermal_evidence, thermal_model),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            build_manifest(
                ai_input,
                coordinate_review,
                vector_geometry,
                ai_copy,
                coordinate_copy,
                vector_copy if vector_geometry else None,
                dimension_matches_copy if dimension_wall_matches else None,
                candidate_review_copy if candidate_review else None,
                vision_response_copy if vision_response else None,
                vision_validation_copy if vision_validation else None,
                geometry_confirmation_copy if geometry_confirmation else None,
                prompt,
                screenshots,
                overlays,
                vector_overlays,
                dimension_match_overlays,
                candidate_overlays,
                geometry_confirmation_overlays,
                requirements_copy,
                heat_load_report_copy if heat_load_report else None,
                ventilation_report_copy if ventilation_report else None,
                thermal_evidence_copy if thermal_evidence else None,
                thermal_model_copy if thermal_model else None,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )

    result = {
        "status": "created",
        "folder": str(output_dir),
        "prompt": str(prompt),
        "ai_input": str(ai_copy),
        "coordinate_review": str(coordinate_copy),
        "vector_geometry": str(vector_copy) if vector_geometry else "",
        "dimension_wall_matches": str(dimension_matches_copy) if dimension_wall_matches else "",
        "candidate_review": str(candidate_review_copy) if candidate_review else "",
        "vision_response": str(vision_response_copy) if vision_response else "",
        "vision_validation": str(vision_validation_copy) if vision_validation else "",
        "geometry_confirmation": str(geometry_confirmation_copy) if geometry_confirmation else "",
        "design_requirements": str(requirements_copy),
        "heat_load_report": str(heat_load_report_copy) if heat_load_report else "",
        "ventilation_report": str(ventilation_report_copy) if ventilation_report else "",
        "thermal_evidence": str(thermal_evidence_copy) if thermal_evidence else "",
        "thermal_model": str(thermal_model_copy) if thermal_model else "",
        "manifest": str(manifest),
        "screenshots": screenshots,
        "overlays": overlays,
        "vector_overlays": vector_overlays,
        "dimension_match_overlays": dimension_match_overlays,
        "candidate_overlays": candidate_overlays,
        "geometry_confirmation_overlays": geometry_confirmation_overlays,
    }
    if zip_packet:
        result["zip"] = str(zip_packet_folder(output_dir))
    return result


def create_reasoning_packet_from_vision(
    ai_input_path,
    vision_response_path,
    chatgpt_packet_dir=None,
    output_dir=None,
    zip_packet=True,
    vector_geometry_path=None,
    dimension_wall_matches_path=None,
    candidate_review_path=None,
    design_requirements_path=None,
    heat_load_report_path=None,
    ventilation_report_path=None,
    thermal_evidence_path=None,
    thermal_model_path=None,
):
    vision_response_path = Path(vision_response_path)
    vision_validation_path = validate_vision_file(
        vision_response_path,
        vision_response_path.with_name("vision_validation.json"),
        candidate_review_path,
    )
    coordinate_review_path = create_coordinate_review(
        vision_response_path,
        vision_response_path.with_name("coordinate_review.json"),
        Path(chatgpt_packet_dir or Path(ai_input_path).with_name("chatgpt_packet")) / "screenshots",
        vision_response_path.with_name("coordinate_overlays"),
        candidate_review_path,
    )
    geometry_confirmation_path = None
    if vector_geometry_path and Path(vector_geometry_path).exists():
        geometry_confirmation_path = create_geometry_confirmation(
            vision_response_path,
            vector_geometry_path,
            vision_response_path.with_name("geometry_confirmation.json"),
            vision_response_path.with_name("geometry_confirmation_overlays"),
            Path(chatgpt_packet_dir or Path(ai_input_path).with_name("chatgpt_packet")) / "screenshots",
            candidate_review_path,
            page_scales_from_ai_input(ai_input_path),
        )
    return create_reasoning_packet(
        ai_input_path,
        coordinate_review_path,
        chatgpt_packet_dir,
        output_dir,
        zip_packet,
        vector_geometry_path,
        dimension_wall_matches_path,
        candidate_review_path,
        vision_response_path,
        vision_validation_path,
        geometry_confirmation_path,
        design_requirements_path,
        heat_load_report_path,
        ventilation_report_path,
        thermal_evidence_path,
        thermal_model_path,
    )


def copy_folder_images(source_dir, target_dir):
    target_dir.mkdir(exist_ok=True)
    copied = []
    if not source_dir.exists():
        return copied
    for source in sorted(source_dir.glob("*.png")):
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def copy_overlay_images(coordinate_review, target_dir):
    target_dir.mkdir(exist_ok=True)
    copied = []
    for item in coordinate_review.get("overlays", []):
        source = Path(item.get("path", ""))
        if item.get("status") != "created" or not source.exists():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def copy_vector_overlay_images(vector_geometry, target_dir):
    target_dir.mkdir(exist_ok=True)
    copied = []
    for item in vector_geometry.get("overlays", []):
        source = Path(item.get("path", ""))
        if item.get("status") != "created" or not source.exists():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def copy_dimension_match_overlay_images(dimension_wall_matches, target_dir):
    target_dir.mkdir(exist_ok=True)
    copied = []
    for item in dimension_wall_matches.get("overlays", []):
        source = Path(item.get("path", ""))
        if item.get("status") != "created" or not source.exists():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def copy_candidate_overlay_images(candidate_review, target_dir):
    target_dir.mkdir(exist_ok=True)
    copied = []
    for item in candidate_review.get("overlays", []):
        source = Path(item.get("path", ""))
        if item.get("status") != "created" or not source.exists():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def copy_geometry_confirmation_overlay_images(geometry_confirmation, target_dir):
    target_dir.mkdir(exist_ok=True)
    copied = []
    for item in geometry_confirmation.get("overlays", []):
        source = Path(item.get("path", ""))
        if item.get("status") != "created" or not source.exists():
            continue
        target = target_dir / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def build_manifest(
    ai_input,
    coordinate_review,
    vector_geometry,
    ai_input_path,
    coordinate_review_path,
    vector_geometry_path,
    dimension_wall_matches_path,
    candidate_review_path,
    vision_response_path,
    vision_validation_path,
    geometry_confirmation_path,
    prompt_path,
    screenshots,
    overlays,
    vector_overlays,
    dimension_match_overlays,
    candidate_overlays,
    geometry_confirmation_overlays,
    design_requirements_path,
    heat_load_report_path,
    ventilation_report_path,
    thermal_evidence_path=None,
    thermal_model_path=None,
):
    dimension_wall_matches = load_json(dimension_wall_matches_path) if dimension_wall_matches_path else {}
    vision_response = load_json(vision_response_path) if vision_response_path else {}
    vision_validation = load_json(vision_validation_path) if vision_validation_path else {}
    geometry_confirmation = load_json(geometry_confirmation_path) if geometry_confirmation_path else {}
    requirements = load_json(design_requirements_path) if design_requirements_path else empty_design_requirements()
    heat_load_report = load_json(heat_load_report_path) if heat_load_report_path else {}
    ventilation_report = load_json(ventilation_report_path) if ventilation_report_path else {}
    thermal_evidence = load_json(thermal_evidence_path) if thermal_evidence_path else {}
    thermal_model = load_json(thermal_model_path) if thermal_model_path else {}
    readiness = requirements_summary(requirements)
    summary = evidence_summary(ai_input, coordinate_review, vector_geometry, dimension_wall_matches, vision_response, vision_validation, geometry_confirmation)
    return {
        "packet_type": "reasoning_model_hvac_review",
        "ai_input": str(ai_input_path),
        "coordinate_review": str(coordinate_review_path),
        "vector_geometry": str(vector_geometry_path) if vector_geometry_path else "",
        "dimension_wall_matches": str(dimension_wall_matches_path) if dimension_wall_matches_path else "",
        "candidate_review": str(candidate_review_path) if candidate_review_path else "",
        "vision_response": str(vision_response_path) if vision_response_path else "",
        "vision_validation": str(vision_validation_path) if vision_validation_path else "",
        "geometry_confirmation": str(geometry_confirmation_path) if geometry_confirmation_path else "",
        "design_requirements_file": str(design_requirements_path),
        "heat_load_report": str(heat_load_report_path) if heat_load_report_path else "",
        "ventilation_report": str(ventilation_report_path) if ventilation_report_path else "",
        "thermal_evidence": str(thermal_evidence_path) if thermal_evidence_path else "",
        "thermal_model": str(thermal_model_path) if thermal_model_path else "",
        "prompt": str(prompt_path),
        "screenshots": screenshots,
        "overlays": overlays,
        "vector_overlays": vector_overlays,
        "dimension_match_overlays": dimension_match_overlays,
        "candidate_overlays": candidate_overlays,
        "geometry_confirmation_overlays": geometry_confirmation_overlays,
        "evidence_summary": summary,
        "geometry_verification_status": geometry_verification_status(vision_response, vision_validation),
        "coordinate_rules": coordinate_rules(),
        "design_requirements": {
            "inputs": requirements,
            "readiness": readiness,
            "zone_readiness": readiness.get("zone_readiness", []),
            "required_before_full_hvac_design": ["occupancy", "space usage", "operating hours", "design indoor and outdoor temperatures", "outside-air basis", "exhaust basis", "equipment and appliance heat loads", "ceiling height and void", "existing service constraints", "applicable code basis"],
            "instruction": "Designer-provided inputs are separate from PDF evidence. Treat provisional inputs as preliminary-brief context only; do not invent missing values.",
        },
        "heat_load_summary": {
            "status": heat_load_report.get("status", "not_calculated"),
            "project_total_kw": heat_load_report.get("project_total_kw"),
            "calculated_zone_count": heat_load_report.get("calculated_zone_count", 0),
            "blocked_zone_count": heat_load_report.get("blocked_zone_count", 0),
        },
        "ventilation_summary": {
            "status": ventilation_report.get("status", "not_calculated"),
            "total_outside_air_lps": ventilation_report.get("total_outside_air_lps"),
            "total_process_exhaust_lps": ventilation_report.get("total_process_exhaust_lps"),
            "total_required_make_up_air_lps": ventilation_report.get("total_required_make_up_air_lps"),
            "calculated_zone_count": ventilation_report.get("calculated_zone_count", 0),
            "blocked_zone_count": ventilation_report.get("blocked_zone_count", 0),
        },
        "thermal_model_summary": {
            "status": thermal_model.get("status", "not_built"),
            "direct_fact_count": thermal_model.get("evidence_summary", {}).get("direct_fact_count", 0),
            "exception_count": thermal_model.get("evidence_summary", {}).get("exception_count", 0),
        },
        "instructions": [
            "Upload prompt.md, ai_input.json, coordinate_review.json, geometry_confirmation.json, vector_geometry.json, dimension_wall_matches.json, screenshots, and overlays to the reasoning model.",
            "Use all relevant geometry, RCP/HVAC, legend/key, and schedule evidence together.",
            "Use vector_geometry.json as raw PDF geometry evidence; do not treat every vector line as a wall.",
            "Use dimension_wall_matches.json as neutral measured-span evidence; do not match dimensions by nearest text alone.",
            "Use candidate_review.json and candidate_overlays to verify the raw primitives shown to the vision model.",
            "Use vision_response.json layered_geometry as the primary vision-created wall interpretation when available.",
            "Use geometry_confirmation.json to identify vector-confirmed and CAD-ready candidate geometry.",
            "If vision_response.json is missing, geometry status is geometry_not_vision_verified; do not treat raw candidates as design-ready walls.",
            "Reject invalid or unverified candidate links.",
            "Use overlay-approved geometry for calculations. Treat validator-passed geometry as context only.",
            "Do not invent HVAC loads, duct routes, or missing design criteria.",
            "Treat design_requirements.json as designer-provided input, not PDF evidence. Final loads, sizing, routing, quantities, and CAD actions are blocked while its readiness says final_design_blocked, including when a value exists but its verification status is provisional.",
            "Use heat_load_report.json only as a preliminary, deterministic cooling-load breakdown. Do not promote provisional or blocked results into final equipment sizing or procurement decisions.",
            "Use ventilation_report.json only as a preliminary ventilation and exhaust calculation. Do not promote provisional or blocked results into final fan, hood, duct, or compliance decisions.",
        ],
    }


def build_reasoning_prompt(ai_input, coordinate_review, vector_geometry, dimension_wall_matches, screenshots, overlays, vector_overlays, dimension_match_overlays, candidate_review=None, candidate_overlays=None, vision_response=None, vision_validation=None, geometry_confirmation=None, requirements=None, heat_load_report=None, ventilation_report=None, thermal_evidence=None, thermal_model=None):
    vision_response = vision_response or {}
    vision_validation = vision_validation or {}
    geometry_confirmation = geometry_confirmation or {}
    requirements = requirements or empty_design_requirements()
    heat_load_report = heat_load_report or {}
    ventilation_report = ventilation_report or {}
    thermal_evidence = thermal_evidence or {}
    thermal_model = thermal_model or {}
    context = compact_context(ai_input, coordinate_review, vector_geometry, dimension_wall_matches, screenshots, overlays, vector_overlays, dimension_match_overlays, candidate_review or {}, candidate_overlays or [], vision_response, vision_validation, geometry_confirmation, requirements, heat_load_report, ventilation_report, thermal_evidence, thermal_model)
    return (
        "# HVAC Reasoning Review Prompt\n\n"
        "You are the reasoning model for an HVAC/mechanical design prototype.\n\n"
        "Use original screenshots to verify context, and use overlay images to check whether coordinate matches are visually correct.\n"
        "Use `vector_geometry.json` as raw PDF geometry evidence. It contains extracted lines, curves, rectangles, and dimension-text candidates.\n"
        "Do not assume every vector line is a wall. Raw vectors support the vision-created walls in `vision_response.json`; they do not independently define walls.\n"
        "Use `dimension_wall_matches.json` as neutral measured-span evidence. It identifies written dimensions, arrows/ticks, witness lines, and spans, but does not select wall targets.\n"
        "Use `candidate_review.json` and candidate overlay crops to inspect the raw primitives and focused dimension zones given to the vision model.\n"
        "Use `vision_response.json` layered_geometry as the primary geometry interpretation. It contains the vision-created outer walls, internal partitions, dimensions, and dimension-to-wall links.\n"
        "Use `geometry_confirmation.json` as the geometry readiness gate. Prefer `cad_ready_candidate`, then `scale_calibrated`, then `vector_confirmed`, then `experimental_vector_snap`; treat `vision_estimated` as context only.\n"
        "In Vision Lab, `experimental_vector_snap` is preferred working geometry with recorded alternatives, not final CAD/export geometry.\n"
        "Use geometry confirmation overlays to compare red vision lines against green selected snaps and amber alternatives before trusting geometry.\n"
        "If `vision_response.json` is missing or invalid, set geometry readiness to `geometry_not_vision_verified` and do not design from raw wall candidates.\n"
        "Never use raw vectors as primary walls until a vision-created wall cites or is compared against them.\n"
        "Do not use fixture/joinery geometry as building boundary walls; use it only for coordination and clearance context.\n"
        "Do not use the closest number to the closest wall as proof. A farther overall dimension can be the correct measurement if its arrows or witness lines span the wall endpoints.\n"
        "Machine text extraction can miss visible dimension labels. If screenshots clearly show dimensions missing from machine matches, use them as vision evidence and mark their source as screenshot-visible, not machine-extracted.\n"
        "Treat dimension spans as evidence only. A dimension-to-wall link becomes usable only after the vision-created wall and the span pass validation and confirmation.\n"
        "Reject or downgrade any match where the dimension span only covers a small portion of the selected wall.\n"
        "For curved walls, prefer curve/polyline key points from vector evidence. Output spline/polyline CAD intent unless a true arc is clearly supported.\n"
        "Use all relevant geometry evidence pages together; do not choose one best page when multiple pages describe the same floor.\n"
        "Use HVAC/RCP pages for ceiling constraints, registers, diffusers, services, ceiling heights, access panels, and coordination. "
        "Do not use them as the only wall geometry source unless no better geometry page exists and uncertainty is stated.\n"
        "Use legend/key and schedule pages to decode HVAC and RCP symbols. Never treat legend/key pages as building wall geometry.\n"
        "Cite source page numbers for every important design conclusion, symbol interpretation, coordinate match, scale, and dimension.\n"
        "`image_px` coordinates use the top-left of the full high-resolution screenshot and are for visual overlay verification.\n"
        "`plan_px` coordinates use the bottom-left of the approved plan viewport and are better for geometry reasoning.\n"
        "`local_mm` coordinates are provisional and should only exist after a trusted written dimension or scale conversion.\n"
        "Use only `overlay_approved` geometry for design calculations. Treat `validator_passed` geometry as context only, not final calculation truth.\n"
        "The product goal is minimal human review, but automation must be confidence-gated: proceed automatically only when vector evidence, screenshot evidence, dimensions, and validation agree.\n"
        "Prefer direct written dimensions where visible. Use scale only when direct dimensions are missing or insufficient, and keep scale evidence per page.\n"
        "Treat C.O.S dimensions as provisional and keep `site_confirm_required` true when present.\n"
        "Treat visible equipment such as IMDL-62Y-4(E) as drawing evidence, not final procurement truth unless contractor-confirmed.\n"
        "`design_requirements.json` contains inputs supplied by the designer. Cite them separately from PDF evidence.\n"
        "`heat_load_report.json`, when present, is a deterministic preliminary cooling-load breakdown. Cite its calculation status and warnings; it is not final equipment selection or sizing authority.\n"
        "`ventilation_report.json`, when present, is a deterministic preliminary outside-air, process-exhaust, make-up-air, and zone-balance report. Cite its selected basis, status, and warnings; it is not final fan, hood, duct, or compliance authority.\n"
        "`thermal_evidence.json` and `thermal_model.json`, when present, are cited PDF facts and an engineer-reviewable draft respectively. Never turn an exception item into a calculation assumption until it is confirmed in design_requirements.json.\n"
        "Its `zones` are designer-owned HVAC design/control areas. Source room labels are PDF suggestions only, not confirmed room boundaries. Use each zone's explicit values first and inherit omitted values from the project-wide requirements.\n"
        f"Current requirements readiness: `{requirements_summary(requirements)['status']}`. Do not produce final loads, sizing, routing, quantities, or CAD actions when it is `final_design_blocked`.\n"
        "A value is not confirmed merely because it is present. Read each critical field's verification status and source: use `confirmed` designer inputs for final engineering, `provisional` inputs for a preliminary brief only, and `missing` inputs as unresolved. `not_applicable` is valid only when the designer records a clear rationale.\n"
        "Do not invent HVAC loads, duct routes, pipe routes, occupancy, design temperatures, or code assumptions that are not provided.\n"
        "If information is missing, return it as missing information instead of guessing.\n\n"
        "Return strict JSON with: `geometry_readiness`, `design_model`, `usable_geometry`, "
        "`rejected_or_uncertain_geometry`, `hvac_context`, `legend_key_usage`, "
        "`scale_and_dimension_evidence`, `missing_design_requirements`, "
        "`recommended_next_steps`, and `design_generation_allowed`.\n\n"
        "Packet summary:\n"
        f"```json\n{json.dumps(context, indent=2)}\n```\n"
    )


def compact_context(ai_input, coordinate_review, vector_geometry, dimension_wall_matches, screenshots, overlays, vector_overlays, dimension_match_overlays, candidate_review=None, candidate_overlays=None, vision_response=None, vision_validation=None, geometry_confirmation=None, requirements=None, heat_load_report=None, ventilation_report=None, thermal_evidence=None, thermal_model=None):
    candidate_review = candidate_review or {}
    candidate_overlays = candidate_overlays or []
    vision_response = vision_response or {}
    vision_validation = vision_validation or {}
    geometry_confirmation = geometry_confirmation or {}
    heat_load_report = heat_load_report or {}
    ventilation_report = ventilation_report or {}
    thermal_evidence = thermal_evidence or {}
    thermal_model = thermal_model or {}
    return {
        "source_pdf": ai_input.get("source_pdf", ""),
        "review_status": ai_input.get("review_status", {}),
        "evidence_summary": evidence_summary(ai_input, coordinate_review, vector_geometry, dimension_wall_matches, vision_response, vision_validation, geometry_confirmation),
        "geometry_verification_status": geometry_verification_status(vision_response, vision_validation),
        "layered_geometry_pages": compact_layered_geometry_pages(vision_response),
        "building_model": ai_input.get("building_model", {}),
        "coordinate_validation": coordinate_review.get("validation", {}),
        "coordinate_rules": coordinate_rules(),
        "scale_conversions": coordinate_review.get("scale_conversions", []),
        "provisional_cad_geometry_count": len(coordinate_review.get("provisional_cad_geometry", [])),
        "geometry_key_point_pages": compact_vector_pages(vector_geometry),
        "dimension_wall_match_pages": compact_dimension_match_pages(dimension_wall_matches),
        "candidate_review_pages": compact_candidate_review_pages(candidate_review),
        "geometry_confirmation_pages": compact_geometry_confirmation_pages(geometry_confirmation),
        "screenshots": [Path(path).name for path in screenshots],
        "overlays": [Path(path).name for path in overlays],
        "vector_overlays": [Path(path).name for path in vector_overlays],
        "dimension_match_overlays": [Path(path).name for path in dimension_match_overlays],
        "candidate_overlays": [Path(path).name for path in candidate_overlays],
        "geometry_confirmation_overlays": [Path(item.get("path", "")).name for item in geometry_confirmation.get("overlays", []) if item.get("status") == "created"],
        "design_requirements": {
            "inputs": requirements or empty_design_requirements(),
            "readiness": requirements_summary(requirements),
            "zone_summary": requirements_summary(requirements).get("zone_readiness", []),
        },
        "heat_load_report": heat_load_report,
        "ventilation_report": ventilation_report,
        "thermal_evidence": thermal_evidence,
        "thermal_model": thermal_model,
    }


def compact_candidate_review_pages(candidate_review):
    pages = []
    for page in candidate_review.get("pages", []):
        pages.append(
            {
                "page": page.get("page"),
                "plan_role": page.get("plan_role", ""),
                "crop_count": len(page.get("crops", [])),
                "wall_candidate_count": len(page.get("wall_candidates", [])),
                "dimension_line_candidate_count": len(page.get("dimension_line_candidates", [])),
                "curve_candidate_count": len(page.get("curve_candidates", [])),
                "dimension_text_candidate_count": len(page.get("dimension_text_candidates", [])),
            }
        )
    return pages


def evidence_summary(ai_input, coordinate_review, vector_geometry=None, dimension_wall_matches=None, vision_response=None, vision_validation=None, geometry_confirmation=None):
    design_inputs = ai_input.get("design_inputs", {})
    vector_geometry = vector_geometry or {}
    dimension_wall_matches = dimension_wall_matches or {}
    status = geometry_verification_status(vision_response or {}, vision_validation or {})
    return {
        "geometry_readiness": geometry_readiness(status, dimension_wall_matches, geometry_confirmation),
        "geometry_evidence_pages": design_inputs.get("geometry_evidence_pages", []),
        "dimension_evidence_pages": design_inputs.get("dimension_evidence_pages", []),
        "finish_or_fitout_context_pages": design_inputs.get("finish_or_fitout_context_pages", []),
        "rcp_service_context_pages": design_inputs.get("rcp_service_context_pages", []),
        "legend_key_context_pages": design_inputs.get("legend_key_pages", []),
        "scales_by_page": scales_by_page(ai_input),
        "coordinate_evidence": {
            "wall_candidate_count": len(coordinate_review.get("wall_candidates", [])),
            "fixed_obstacle_count": len(coordinate_review.get("fixed_obstacle_candidates", [])),
            "dimension_candidate_count": len(coordinate_review.get("dimension_candidates", [])),
            "wall_dimension_link_count": len(coordinate_review.get("proposed_wall_dimension_links", [])),
            "overlay_count": len([item for item in coordinate_review.get("overlays", []) if item.get("status") == "created"]),
        },
        "vector_geometry_evidence": vector_evidence_summary(vector_geometry),
        "dimension_wall_match_evidence": dimension_match_summary(dimension_wall_matches),
        "vision_layered_geometry_evidence": layered_geometry_summary(vision_response or {}, vision_validation or {}),
        "geometry_confirmation_evidence": geometry_confirmation_summary(geometry_confirmation or {}),
    }


def page_scales_from_ai_input(ai_input_path):
    """Exact millimetres per pixel per page, from the drawing scale and the render dpi."""
    pages = load_json(ai_input_path).get("spatial_ocr", {}).get("pages", [])
    scales = {}
    for page in pages:
        mm_per_px = page_mm_per_px(page.get("scale_candidates", []))
        if mm_per_px:
            scales[page.get("page")] = mm_per_px
    return scales


def geometry_verification_status(vision_response, vision_validation):
    if not vision_response:
        return "geometry_not_vision_verified"
    # Legacy coordinate_review/wall_dimensions issues are review noise from an older
    # schema that geometry_confirmation.py does not consume. Only issues inside
    # layered_geometry should downgrade this status. Fall back to the full issue_count
    # for vision_validation reports saved before this field existed.
    gating_issue_count = vision_validation.get("layered_geometry_issue_count", vision_validation.get("issue_count", 0))
    if gating_issue_count:
        return "geometry_vision_response_has_validation_issues"
    ready = vision_validation.get("layered_geometry_ready_pages", [])
    if ready:
        return "geometry_vision_layered"
    return "geometry_vision_response_missing_layered_geometry"


def geometry_readiness(status, dimension_wall_matches, geometry_confirmation=None):
    confirmation = geometry_confirmation or {}
    confirmation_status = confirmation.get("status", "")
    if confirmation_status == "cad_ready_candidate":
        return {
            "status": "cad_ready_candidate",
            "design_use": "vector-confirmed and scale-calibrated candidates are available; use only after overlay review",
        }
    if confirmation_status == "scale_calibrated":
        return {
            "status": "scale_calibrated",
            "design_use": "vector-confirmed geometry has a page scale conversion but still needs CAD/export review",
        }
    if confirmation_status == "vector_confirmed":
        return {
            "status": "vector_confirmed",
            "design_use": "vision geometry has been snapped to PDF vectors but is not scale-calibrated yet",
        }
    if confirmation_status == "experimental_vector_snap":
        return {
            "status": "experimental_vector_snap",
            "design_use": "Vision Lab working geometry is locally snapped to PDF vectors; use it to reason, but do not export CAD from it",
        }
    if confirmation_status == "vision_estimated":
        return {
            "status": "vision_estimated",
            "design_use": "vision geometry exists but has not been confirmed against PDF vectors",
        }
    if status == "geometry_vision_layered":
        return {
            "status": "vision_verified",
            "design_use": "layered vision geometry can be used as primary evidence after overlay approval",
        }
    if dimension_wall_matches.get("pages"):
        return {
            "status": "rule_candidate_only",
            "design_use": "raw vector and rule candidates are review evidence only; do not design from them yet",
        }
    return {
        "status": "not_ready_for_design",
        "design_use": "no validated geometry evidence is available",
    }


def layered_geometry_summary(vision_response, vision_validation):
    pages = compact_layered_geometry_pages(vision_response)
    return {
        "status": geometry_verification_status(vision_response, vision_validation),
        "page_count": len(pages),
        "ready_pages": vision_validation.get("layered_geometry_ready_pages", []),
        "outer_boundary_wall_count": sum(item.get("outer_boundary_wall_count", 0) for item in pages),
        "fixed_obstacle_count": sum(item.get("fixed_obstacle_count", 0) for item in pages),
        "dimension_count": sum(item.get("dimension_count", 0) for item in pages),
        "dimension_wall_link_count": sum(item.get("dimension_wall_link_count", 0) for item in pages),
    }


def geometry_confirmation_summary(geometry_confirmation):
    pages = geometry_confirmation.get("pages", [])
    summary = geometry_confirmation.get("summary", {})
    return {
        "status": geometry_confirmation.get("status", "not_ready"),
        "page_count": len(pages),
        "vision_wall_count": summary.get("vision_wall_count", 0),
        "vector_confirmed_wall_count": summary.get("vector_confirmed_wall_count", 0),
        "experimental_vector_snap_wall_count": summary.get("experimental_vector_snap_wall_count", 0),
        "cad_ready_candidate_count": summary.get("cad_ready_candidate_count", 0),
        "scale_conversion_count": len(geometry_confirmation.get("scale_conversions", [])),
        "overlay_count": len([item for item in geometry_confirmation.get("overlays", []) if item.get("status") == "created"]),
    }


def compact_geometry_confirmation_pages(geometry_confirmation):
    pages = []
    for page in geometry_confirmation.get("pages", []):
        links = page.get("dimension_link_confirmations", [])
        pages.append(
            {
                "page": page.get("page"),
                "status": page.get("status", ""),
                "wall_confirmation_count": len(page.get("wall_confirmations", [])),
                "dimension_link_confirmation_count": len(links),
                "vector_confirmed_wall_count": sum(1 for wall in page.get("wall_confirmations", []) if wall.get("status") == "vector_confirmed"),
                "experimental_vector_snap_wall_count": sum(1 for wall in page.get("wall_confirmations", []) if wall.get("status") == "experimental_vector_snap"),
                "cad_ready_link_count": sum(1 for link in links if link.get("status") == "cad_ready_candidate"),
                "review_only_link_count": sum(1 for link in links if link.get("status") == "review_only"),
            }
        )
    return pages


def compact_layered_geometry_pages(vision_response):
    layered = vision_response.get("result", {}).get("layered_geometry", {})
    pages = layered.get("pages", []) if isinstance(layered, dict) else []
    return [
        {
            "page": page.get("page"),
            "page_role": page.get("page_role", ""),
            "geometry_readiness": page.get("geometry_readiness", ""),
            "outer_boundary_wall_count": len(page.get("outer_boundary_walls", [])),
            "internal_partition_count": len(page.get("internal_partitions", [])),
            "fixture_or_joinery_count": len(page.get("fixture_or_joinery_geometry", [])),
            "fixed_obstacle_count": len(page.get("fixed_obstacles", [])),
            "column_count": len(page.get("columns", [])),
            "opening_count": len(page.get("openings", [])),
            "dimension_count": len(page.get("dimension_candidates", [])),
            "dimension_wall_link_count": len(page.get("dimension_wall_links", [])),
        }
        for page in pages
    ]


def vector_evidence_summary(vector_geometry):
    pages = vector_geometry.get("geometry_key_points", {}).get("pages", [])
    return {
        "page_count": len(pages),
        "wall_key_point_candidate_count": sum(len(page.get("wall_candidates", [])) for page in pages),
        "line_candidate_count": sum(len(page.get("line_candidates", [])) for page in pages),
        "curve_candidate_count": sum(len(page.get("curve_candidates", [])) for page in pages),
        "dimension_text_candidate_count": sum(len(page.get("dimension_candidates", [])) for page in pages),
        "overlay_count": len([item for item in vector_geometry.get("overlays", []) if item.get("status") == "created"]),
    }


def dimension_match_summary(dimension_wall_matches):
    pages = dimension_wall_matches.get("pages", [])
    major = []
    local = []
    unassigned = []
    gaps = []
    for page in pages:
        summary = page.get("summary", {})
        major.extend(summary.get("major_boundary_dimensions", []))
        local.extend(summary.get("local_fixture_dimensions", []))
        unassigned.extend(summary.get("unassigned_dimensions", []))
        gaps.extend(summary.get("machine_extraction_gaps", []))
    return {
        "page_count": len(pages),
        "matched_dimension_count": sum(len(page.get("dimension_wall_matches", [])) for page in pages),
        "rule_candidate_count": sum(len(page.get("rule_candidates", [])) for page in pages),
        "rule_verified_count": sum(
            1
            for page in pages
            for item in page.get("rule_verified", page.get("dimension_wall_matches", []))
            if item.get("status") == "rule_verified"
        ),
        "unmatched_or_rejected_count": sum(len(page.get("rejected_or_unmatched", [])) for page in pages),
        "major_boundary_dimensions": major[:40],
        "local_fixture_dimensions": local[:40],
        "unassigned_dimensions": unassigned[:60],
        "machine_extraction_gaps": gaps[:20],
        "overlay_count": len([item for item in dimension_wall_matches.get("overlays", []) if item.get("status") == "created"]),
    }


def compact_vector_pages(vector_geometry):
    pages = []
    for page in vector_geometry.get("geometry_key_points", {}).get("pages", []):
        pages.append(
            {
                "page": page.get("page"),
                "title": page.get("title", ""),
                "plan_role": page.get("plan_role", ""),
                "raw_counts": page.get("raw_counts", {}),
                "wall_candidate_count": len(page.get("wall_candidates", [])),
                "curve_candidate_count": len(page.get("curve_candidates", [])),
                "dimension_candidate_count": len(page.get("dimension_candidates", [])),
            }
        )
    return pages


def compact_dimension_match_pages(dimension_wall_matches):
    pages = []
    for page in dimension_wall_matches.get("pages", []):
        pages.append(
            {
                "page": page.get("page"),
                "plan_role": page.get("plan_role", ""),
                "summary": page.get("summary", {}),
                "machine_extraction_gaps": page.get("summary", {}).get("machine_extraction_gaps", []),
                "rule_verified_matches": [
                    {
                        "measurement_text": item.get("measurement_text"),
                        "value_mm": item.get("value_mm"),
                        "target_wall_candidate_id": item.get("target_wall_candidate_id"),
                        "match_score": item.get("match_score"),
                        "span_coverage": item.get("span_coverage"),
                    }
                    for item in page.get("rule_verified", page.get("dimension_wall_matches", []))
                    if item.get("status") == "rule_verified"
                ][:12],
                "rule_candidate_matches": [
                    {
                        "measurement_text": item.get("measurement_text"),
                        "value_mm": item.get("value_mm"),
                        "target_wall_candidate_id": item.get("target_wall_candidate_id"),
                        "match_score": item.get("match_score"),
                        "span_coverage": item.get("span_coverage"),
                        "confidence": item.get("confidence"),
                    }
                    for item in page.get("rule_candidates", [])
                ][:12],
            }
        )
    return pages


def scales_by_page(ai_input):
    pages = []
    for bucket in ai_input.get("confirmed_pages", {}).values():
        for page in bucket:
            if page.get("scale") or page.get("written_dimensions"):
                pages.append(
                    {
                        "page": page.get("page"),
                        "title": page.get("title", ""),
                        "plan_role": page.get("plan_role", ""),
                        "scale": page.get("scale"),
                        "written_dimension_count": len(page.get("written_dimensions", [])),
                    }
                )
    return pages


def coordinate_rules():
    return {
        "coordinate_system": "image_px is full screenshot top-left; plan_px is bottom-left of the plan viewport; local_mm is provisional CAD-like geometry after scale conversion.",
        "image_px_policy": "Use image_px for overlays and visual checking.",
        "plan_px_policy": "Use plan_px for normalized geometry reasoning after the plan viewport is approved.",
        "local_mm_policy": "Use local_mm only when derived from a trusted written dimension or scale conversion.",
        "calculation_ready_status": "overlay_approved",
        "validator_passed_status": "context only until overlay-approved",
        "scale_policy": "Prefer direct written dimensions; use page-specific scale only when direct dimensions are missing or insufficient.",
        "cos_policy": "C.O.S dimensions are provisional and require site confirmation before final design.",
    }


def zip_packet_folder(output_dir):
    output_dir = Path(output_dir)
    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir))
    return zip_path


def main():
    parser = argparse.ArgumentParser(description="Create a reasoning model packet from ai_input.json and coordinate_review.json.")
    parser.add_argument("ai_input")
    parser.add_argument("coordinate_review", nargs="?")
    parser.add_argument("--chatgpt-packet-dir", help="Folder containing original packet screenshots")
    parser.add_argument("--vector-geometry", help="Optional vector_geometry.json with extracted PDF vector key points")
    parser.add_argument("--dimension-wall-matches", help="Optional dimension_wall_matches.json with rule-based measured-span matches")
    parser.add_argument("--candidate-review", help="Optional candidate_review.json with focused labelled vision review candidates")
    parser.add_argument("--vision-response", help="Optional saved vision_response.json to include in the packet")
    parser.add_argument("--vision-validation", help="Optional vision_validation.json to include in the packet")
    parser.add_argument("--geometry-confirmation", help="Optional geometry_confirmation.json to include in the packet")
    parser.add_argument("--design-requirements", help="Optional designer-provided design_requirements.json to include in the packet")
    parser.add_argument("--heat-load-report", help="Optional current heat_load_report.json to include in the packet")
    parser.add_argument("--ventilation-report", help="Optional current ventilation_report.json to include in the packet")
    parser.add_argument("--from-vision", action="store_true", help="Create vision_validation.json and coordinate_review.json from --vision-response before creating the reasoning packet")
    parser.add_argument("--output-dir", help="Output folder; defaults to reasoning_packet beside ai_input.json")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    if args.from_vision:
        if not args.vision_response:
            parser.error("--from-vision requires --vision-response")
        result = create_reasoning_packet_from_vision(
            args.ai_input,
            args.vision_response,
            args.chatgpt_packet_dir,
            args.output_dir,
            zip_packet=not args.no_zip,
            vector_geometry_path=args.vector_geometry,
            dimension_wall_matches_path=args.dimension_wall_matches,
            candidate_review_path=args.candidate_review,
            design_requirements_path=args.design_requirements,
            heat_load_report_path=args.heat_load_report,
            ventilation_report_path=args.ventilation_report,
        )
    else:
        if not args.coordinate_review:
            parser.error("coordinate_review is required unless --from-vision is used")
        result = create_reasoning_packet(
            args.ai_input,
            args.coordinate_review,
            args.chatgpt_packet_dir,
            args.output_dir,
            zip_packet=not args.no_zip,
            vector_geometry_path=args.vector_geometry,
            dimension_wall_matches_path=args.dimension_wall_matches,
            candidate_review_path=args.candidate_review,
            vision_response_path=args.vision_response,
            vision_validation_path=args.vision_validation,
            geometry_confirmation_path=args.geometry_confirmation,
            design_requirements_path=args.design_requirements,
            heat_load_report_path=args.heat_load_report,
            ventilation_report_path=args.ventilation_report,
        )
    print(f"Reasoning packet created: {result['folder']}")
    if result.get("zip"):
        print(f"Zip created: {result['zip']}")


if __name__ == "__main__":
    main()
