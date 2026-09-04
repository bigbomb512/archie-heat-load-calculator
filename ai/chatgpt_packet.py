#!/usr/bin/env python3

"""Create a compact manual-ChatGPT geometry review packet."""

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from ai.ai_packet import load_json


DEFAULT_DPI = 180
INITIAL_CROPS_PER_PAGE = 4
MAX_ADAPTIVE_CROPS_PER_PAGE = 12
MAX_RAW_VECTORS_PER_PAGE = 48
MAX_DIMENSIONS_PER_PAGE = 16


def create_chatgpt_packet(ai_input_path, output_dir=None, zip_packet=True, dpi=DEFAULT_DPI, stage="full"):
    ai_input_path = Path(ai_input_path)
    ai_input = load_json(ai_input_path)
    output_dir = Path(output_dir or ai_input_path.with_name("chatgpt_packet"))
    output_dir.mkdir(parents=True, exist_ok=True)

    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    clear_pngs(screenshots_dir)
    screenshots = create_page_images(ai_input, ai_input_path.parent, screenshots_dir, dpi)

    prompt_path = output_dir / "prompt.md"
    manifest_path = output_dir / "manifest.json"
    if stage == "page_triage":
        context_path = output_dir / "ai_input.json"
        context_path.write_text(json.dumps(ai_input, indent=2), encoding="utf-8")
        evidence = screenshots
        prompt = build_page_triage_prompt(image_index(screenshots))
    else:
        context_path, evidence = create_geometry_context(ai_input, ai_input_path.parent, output_dir, screenshots)
        prompt = build_geometry_prompt(load_json(context_path))

    prompt_path.write_text(prompt, encoding="utf-8")
    manifest_path.write_text(
        json.dumps(build_manifest(ai_input, screenshots, evidence, prompt_path, context_path, stage), indent=2),
        encoding="utf-8",
    )
    result = {
        "status": "created",
        "folder": str(output_dir),
        "prompt": str(prompt_path),
        "context": str(context_path),
        "manifest": str(manifest_path),
        "screenshots": [item["packet_path"] for item in screenshots if item.get("packet_path")],
        "vision_evidence": [item["packet_path"] for item in evidence if item.get("packet_path")],
    }
    if zip_packet:
        result["zip"] = str(zip_chatgpt_packet(output_dir, include_screenshots=True))
    return result


def create_page_images(ai_input, base_dir, screenshots_dir, dpi):
    copied = []
    for image in selected_images(ai_input):
        filename = f"page_{int(image['page']):03d}_{safe_name(image.get('type', 'context'))}.png"
        target = screenshots_dir / filename
        status = render_high_res_page(ai_input, image, target, dpi)
        if not status["ok"]:
            status = copy_thumbnail(base_dir, image, target, status["reason"])
        copied.append(
            dict(
                image,
                packet_path=str(target) if target.exists() else "",
                packet_filename=f"screenshots/{filename}" if target.exists() else "",
                source_pdf=ai_input.get("source_pdf", ""),
                render_dpi=dpi,
                render_status=status["status"],
                quality=status["quality"],
                render_note=status["reason"],
            )
        )
    return copied


def create_geometry_context(ai_input, base_dir, output_dir, screenshots):
    review_path = base_dir / "candidate_review.json"
    review = load_json(review_path) if review_path.exists() else {"pages": []}
    evidence_dir = output_dir / "vision_evidence"
    evidence_dir.mkdir(exist_ok=True)
    clear_pngs(evidence_dir)
    screenshot_by_page = {item.get("page"): item for item in screenshots}
    pages, evidence = [], []

    for review_page in review.get("pages", []):
        if not is_main_geometry_page(review_page):
            continue
        screenshot = screenshot_by_page.get(review_page.get("page"))
        if not screenshot or not screenshot.get("packet_path"):
            continue
        full_target = evidence_dir / Path(screenshot["packet_path"]).name
        shutil.copy2(screenshot["packet_path"], full_target)
        page_evidence = [{"kind": "full_plan", "packet_path": str(full_target), "file": f"vision_evidence/{full_target.name}"}]
        crop_ids = set()
        crops = []
        for crop in review_page.get("crops", []):
            if crop.get("label") not in {"major_dimension_zone", "dimension_band_candidate"}:
                continue
            if len(crops) >= MAX_ADAPTIVE_CROPS_PER_PAGE:
                continue
            overlay = crop_overlay(review, review_page.get("page"), crop.get("crop_id"))
            if not overlay:
                continue
            source = Path(overlay["path"])
            if not source.exists():
                continue
            target = evidence_dir / source.name
            shutil.copy2(source, target)
            crop_data = {
                "crop_id": crop.get("crop_id"),
                "label": crop.get("label"),
                "bbox_px": crop.get("bbox_px"),
                "raw_vector_ids": crop.get("raw_vector_ids", [])[:12],
                "dimension_candidate_ids": crop.get("dimension_candidate_ids", [])[:6],
                "dimension_line_candidate_ids": crop.get("dimension_line_candidate_ids", [])[:12],
                "witness_line_candidate_ids": crop.get("witness_line_candidate_ids", [])[:12],
                "render_dpi": overlay.get("render_dpi"),
                "render_source": overlay.get("render_source", "packet_image_fallback"),
                "source_pdf_page": overlay.get("source_pdf_page"),
                "image_width": overlay.get("image_width"),
                "image_height": overlay.get("image_height"),
                "file": f"vision_evidence/{target.name}",
            }
            crops.append(crop_data)
            crop_ids.update(crop_data["raw_vector_ids"])
            page_evidence.append({"kind": "dimension_crop", "packet_path": str(target), "file": crop_data["file"]})
        raw_vectors = raw_vectors_for_page(review_page, crop_ids)
        dimensions = dimensions_for_crops(review_page, crops)
        dimensions += unread_dimension_slots(review_page.get("page"), crops, len(dimensions), MAX_DIMENSIONS_PER_PAGE)
        pages.append(compact_geometry_page(review_page, screenshot, raw_vectors, dimensions, crops))
        evidence.extend(page_evidence)

    context = {
        "packet_type": "compact_geometry_vision_context",
        "geometry_pages": pages,
        "legend_and_rcp_context": relevant_context(ai_input),
        "drawing_set_context": drawing_set_context(ai_input),
        "rules": {
            "units": "mm unless the drawing explicitly states otherwise",
            "coordinates": "All coordinates are full-image image_px. Do not convert them.",
            "wall_targets": ["existing_wall", "new_solid_wall", "new_partition"],
            "crop_strategy": {
                "initial_crop_limit": INITIAL_CROPS_PER_PAGE,
                "adaptive_crop_limit": MAX_ADAPTIVE_CROPS_PER_PAGE,
                "raw_vector_limit_per_crop": 12,
                "dimension_limit_per_crop": 6,
            },
        },
    }
    context_path = output_dir / "vision_context.json"
    context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return context_path, evidence


def compact_geometry_page(page, screenshot, raw_vectors, dimensions, crops):
    return {
        "page": page.get("page"),
        "title": page.get("title", ""),
        "plan_role": page.get("plan_role", ""),
        "image": f"vision_evidence/{Path(screenshot['packet_path']).name}",
        "coordinate_system": page.get("coordinate_systems", {}).get("image_px", {"units": "image_px"}),
        "plan_viewport_bbox_px": page.get("plan_viewport", {}).get("bbox_px"),
        "plan_viewport_confidence": page.get("plan_viewport", {}).get("confidence", "low"),
        "raw_vectors": [compact_raw_vector(item) for item in raw_vectors],
        "major_dimensions": [compact_dimension(item) for item in dimensions],
        "unknown_numeric_annotations": [compact_dimension(item) for item in page.get("unknown_numeric_annotations", [])],
        "crops": crops,
    }


def raw_vectors_for_page(page, crop_ids):
    vectors = page.get("wall_candidates", []) + page.get("curve_candidates", [])
    selected = [item for item in vectors if item.get("candidate_id") in crop_ids]
    return selected or vectors[:MAX_RAW_VECTORS_PER_PAGE]


def compact_raw_vector(vector):
    return {
        "vector_id": vector.get("candidate_id"),
        "geometry_type": vector.get("geometry_type", "line"),
        "start_px": vector.get("start_px"),
        "end_px": vector.get("end_px"),
        "points_px": vector.get("points_px", []),
        "stroke_width": vector.get("stroke_width"),
        "role_hint": vector.get("review_role", vector.get("candidate_role_hint", "")),
    }


def compact_dimension(dimension):
    return {
        "dimension_id": dimension.get("candidate_id") or dimension.get("dimension_id"),
        "source": dimension.get("source", "pdf_extracted"),
        "source_annotation_id": dimension.get("source_annotation_id"),
        "value_mm": dimension.get("value_mm"),
        "text_seen": dimension.get("text_seen", ""),
        "bbox_px": dimension.get("bbox_px"),
        "dimension_category": dimension.get("dimension_category", "unknown"),
        "annotation_kind": dimension.get("annotation_kind", "written_dimension"),
        "dimension_eligibility": dimension.get("dimension_eligibility", "eligible"),
        "annotation_reasons": dimension.get("annotation_reasons", []),
        "evidence_crop_id": dimension.get("evidence_crop_id", ""),
    }


def dimensions_for_crops(page, crops):
    selected_ids = {
        candidate_id
        for crop in crops
        for candidate_id in crop.get("dimension_candidate_ids", [])
    }
    all_dimensions = page.get("major_dimension_candidates", []) + page.get("dimension_text_candidates", [])
    selected = [item for item in all_dimensions if item.get("candidate_id") in selected_ids]
    major_ids = {item.get("candidate_id") for item in page.get("major_dimension_candidates", [])}
    selected.sort(key=lambda item: (item.get("candidate_id") not in major_ids, -(item.get("dimension_priority") or 0)))
    unique = {}
    for item in selected:
        unique.setdefault(item.get("candidate_id"), item)
    return list(unique.values())[:MAX_DIMENSIONS_PER_PAGE]


def unread_dimension_slots(page_number, crops, start_index, limit):
    """Reserve page-local IDs for screenshot-visible dimensions missing from PDF text."""
    slots = []
    for index, crop in enumerate(crops, start=start_index + 1):
        if len(slots) + start_index >= limit or crop.get("dimension_candidate_ids"):
            continue
        slots.append(
            {
                "dimension_id": f"P{page_number}-VDIM-VISION-{index:03d}",
                "source": "screenshot_visible",
                "source_annotation_id": None,
                "value_mm": None,
                "text_seen": "",
                "bbox_px": crop.get("bbox_px"),
                "dimension_category": "visible_value_required",
                "evidence_crop_id": crop.get("crop_id"),
            }
        )
    return slots


def relevant_context(ai_input):
    confirmed = ai_input.get("confirmed_pages", {})
    return {
        "legend_key_pages": [page_reference(item) for item in ai_input.get("design_inputs", {}).get("legend_key_pages", [])],
        "rcp_service_pages": [page_reference(item) for item in confirmed.get("reflected_ceiling_plans", [])],
    }


def drawing_set_context(ai_input):
    return [
        {
            "page": page.get("page"),
            "title": page.get("title", ""),
            "level_name": page.get("level_name", ""),
            "sheet_classification": page.get("sheet_classification", page.get("detected_type", "other")),
            "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
            "classification_evidence": page.get("classification_evidence", ""),
        }
        for page in ai_input.get("drawing_set", {}).get("pages", [])
        if page.get("thermal_role") != "not_calculation_evidence"
    ]


def page_reference(page):
    return {
        key: page.get(key)
        for key in ["page", "title", "type", "packet_role", "level_name", "scale", "matched_terms"]
        if page.get(key) not in (None, "", [])
    }


def crop_overlay(review, page_number, crop_id):
    return next(
        (item for item in review.get("overlays", []) if item.get("page") == page_number and item.get("crop_id") == crop_id),
        None,
    )


def is_main_geometry_page(page):
    return page.get("plan_role") in {"main_floor_plan", "supporting_geometry_plan"}


def clear_pngs(folder):
    for path in folder.glob("*.png"):
        path.unlink()


def render_high_res_page(ai_input, image, target, dpi):
    pdf_path = Path(ai_input.get("source_pdf") or ai_input.get("source_files", {}).get("pdf", ""))
    if not pdf_path.exists():
        return render_status(False, "missing_source_pdf", "missing", f"Source PDF not found: {pdf_path}")
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-singlefile", "-f", str(image["page"]), "-l", str(image["page"]), "-r", str(dpi), str(pdf_path), str(target.with_suffix(""))],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        return render_status(False, "high_res_render_failed", "missing", str(error))
    return render_status(True, "rendered_high_res", "high_res", "")


def copy_thumbnail(base_dir, image, target, reason):
    source = (base_dir / image.get("path", "")).resolve()
    if not source.exists():
        return render_status(False, "missing_thumbnail_fallback", "missing", reason)
    shutil.copy2(source, target)
    return render_status(True, "thumbnail_fallback", "thumbnail_fallback", reason)


def render_status(ok, status, quality, reason):
    return {"ok": ok, "status": status, "quality": quality, "reason": reason}


def selected_images(ai_input):
    image_by_page = {image.get("page"): image for image in ai_input.get("source_files", {}).get("page_images", [])}
    pages = confirmed_page_numbers(ai_input)
    for sheet in ai_input.get("drawing_set", {}).get("pages", []):
        if sheet.get("thermal_role") != "not_calculation_evidence":
            pages.append(sheet.get("page"))
    return [image_by_page[page] for page in dict.fromkeys(pages) if page in image_by_page]


def confirmed_page_numbers(ai_input):
    pages = []
    for bucket in ["floor_plans", "reflected_ceiling_plans", "existing_hvac_or_services_plans", "reference_pages"]:
        pages.extend(page.get("page") for page in ai_input.get("confirmed_pages", {}).get(bucket, []))
    return list(dict.fromkeys(page for page in pages if page is not None))


def image_index(images):
    return [{key: item.get(key, "") for key in ["page", "type", "title", "level_name", "packet_filename"]} for item in images]


def build_manifest(ai_input, screenshots, evidence, prompt_path, context_path, stage):
    return {
        "packet_type": "chatgpt_manual_geometry_review",
        "stage": stage,
        "source_pdf": ai_input.get("source_pdf", ""),
        "prompt": str(prompt_path),
        "context": str(context_path),
        "screenshots_kept_for_reasoning": screenshots,
        "vision_evidence": evidence,
        "instructions": [
            "Upload prompt.md, vision_context.json, and vision_evidence/ to ChatGPT.",
            "Return the one strict geometry_review JSON object requested by prompt.md.",
        ],
    }


def build_geometry_prompt(context):
    return """# Geometry Vision Review

You are reviewing raw PDF-vector primitives and major dimensions for an HVAC drawing workflow. Extract geometry evidence only; do not design HVAC, rooms, schedules, or routes.

Use the full plan image and the adaptive crops in `vision_evidence/`. `vision_context.json` lists labelled raw vector primitives and dimensions. You create physical walls from visible evidence; code has not pre-labelled any primitive as a wall.

Use `drawing_set_context` to link plans with elevations, sections, roof/site sheets, details, and 3D views. These pages are cited context for exposure, adjacency, opening, orientation, and shading review; do not invent construction performance or numeric load assumptions from them.

Create page-local wall IDs in the exact form `P{page}-VWALL-{number}`, for example `P5-VWALL-001`. Classify each created wall as `existing_wall`, `new_solid_wall`, or `new_partition`, and assign it an `outer_boundary_wall` or `internal_partition` role. A straight wall has two `points_px`; a curved or broken wall is a `polyline` with ordered points. Use `supporting_vector_ids` when labelled raw primitives support the wall, and set `source` to `vector_anchored`. When the image clearly shows a wall but vectors are incomplete, use `source: image_proposed` and leave supporting IDs empty. Do not treat a lease line, fixture, counter, equipment edge, dimension, witness, annotation, title block, or symbol as a physical wall.

Record clear fixed built obstacles that ducts or pipes must avoid, such as columns, circular structures, or permanent built objects. Do not record furniture, loose equipment, dimensions, annotations, or symbols. Create IDs as `P{page}-OBS-{number}`. Use `unknown_fixed_obstacle` unless the drawing proves `column` or `existing_structure`. A circle needs `centre_px` and `radius_px`; a polygon needs ordered `points_px`. Keep any nearby written dimensions only as `related_dimensions_mm` context. Every obstacle must use `routing_constraint: do_not_route_through` and never becomes a wall or dimension-match target.

Do not invent IDs for supplied raw vectors. You may create wall IDs only as `P{page}-VWALL-{number}`. Prefer a supplied dimension ID whenever the readable value appears in `vision_context.json`. When a readable dimension is visible in an attached image but missing from that context, create a page-local ID as `P{page}-VDIM-VISION-{number}`, set `source` to `screenshot_visible`, and set `source_annotation_id` to `null`. A screenshot-visible dimension is valid only when you provide its readable value, text bounding box, dimension-line endpoints, measured-span endpoints, and either both arrow/tick positions or at least one witness line. Do not create an ID for a number without that evidence.

For each readable major dimension, use its actual arrows/ticks, witness lines, and measured span to link it to one of your created wall IDs. Never use nearest geometry alone. Overall dimensions may be farther from the wall than local dimensions. A local dimension must never be applied to a longer wall span. Use `unassigned_dimensions` whenever visible evidence does not prove a target.

All dimensions are millimetres unless the drawing explicitly states another unit. C.O.S. dimensions must set `site_confirm_required` to true. Coordinates remain in full-image `image_px`; do not calculate plan_px or CAD coordinates.

Never treat detail bubbles, section/elevation markers, sheet references, revision-cloud tags, or symbol tags as dimensions. Values listed as `unknown_numeric_annotations` are not dimensions yet: promote one only when the image visibly shows its own dimension line with arrows/ticks or witness lines, and record that positive visible evidence. Never promote `detail_or_sheet_reference` values.

Return valid JSON only in this exact shape:

```json
{
  "geometry_review": {
    "pages": [
      {
        "page": 5,
        "image": "vision_evidence/page_005_floor_plan.png",
        "coordinate_system": {"image_width": 0, "image_height": 0, "units": "image_px"},
        "plan_viewport_bbox_px": [0, 0, 0, 0],
        "plan_viewport_confidence": "low|medium|high",
        "plan_viewport_uncertainties": [],
        "page_role": "main_geometry_and_dimension_plan|supporting_geometry_plan|not_geometry",
        "geometry_readiness": "vision_layered|needs_more_review|not_geometry",
        "walls": [
          {"wall_id": "P5-VWALL-001", "classification": "existing_wall|new_solid_wall|new_partition", "geometry_role": "outer_boundary_wall|internal_partition", "geometry_type": "line|polyline", "points_px": [[0, 0], [0, 0]], "supporting_vector_ids": ["P5-VLINE-001"], "source": "vector_anchored|image_proposed", "visible_evidence": ["string"], "confidence": "low|medium|high"}
        ],
        "fixed_obstacles": [
          {"obstacle_id": "P5-OBS-001", "classification": "unknown_fixed_obstacle|column|existing_structure", "geometry_type": "circle|polygon", "centre_px": [0, 0], "radius_px": 0, "points_px": [], "related_dimensions_mm": [950], "routing_constraint": "do_not_route_through", "visible_evidence": ["string"], "confidence": "low|medium|high"}
        ],
        "major_dimensions": [
          {"dimension_id": "P5-VDIMTXT-001|P5-VDIM-VISION-001", "source": "pdf_extracted|screenshot_visible", "source_annotation_id": "P5-VDIMTXT-001|null", "value_mm": 4328, "text_seen": "4328", "dimension_kind": "overall|local|radius|diameter|offset|unknown", "bbox_px": [0, 0, 0, 0], "dimension_line_start_px": [0, 0], "dimension_line_end_px": [0, 0], "arrowhead_start_px": [0, 0], "arrowhead_end_px": [0, 0], "witness_lines_px": [[[0, 0], [0, 0]]], "measured_span_start_px": [0, 0], "measured_span_end_px": [0, 0], "visible_evidence": ["arrows/ticks or witness lines visibly identify this number as a dimension"], "confidence": "low|medium|high", "site_confirm_required": false}
        ],
        "dimension_wall_links": [
          {"measurement_id": "P5-WD-001", "dimension_id": "P5-VDIMTXT-001", "target_wall_id": "P5-VWALL-001", "confidence": "low|medium|high", "should_use_for_calculation": false, "site_confirm_required": false, "visible_evidence": ["dimension span and witness lines align with the wall"]}
        ],
        "unassigned_dimensions": [{"dimension_id": "P5-VDIMTXT-002", "reason": "target wall is not visually proven"}],
        "conflicts": []
      }
    ]
  }
}
```

Context:

```json
""" + json.dumps(context, indent=2) + "\n```\n"


def build_prompt(ai_input, copied_images, supplementary=None, stage="full"):
    """Compatibility wrapper for direct callers during the transition."""
    if stage == "page_triage":
        return build_page_triage_prompt(image_index(copied_images))
    return build_geometry_prompt(
        {
            "packet_type": "compact_geometry_vision_context",
            "geometry_pages": [],
            "legend_and_rcp_context": relevant_context(ai_input),
            "rules": {"units": "mm unless the drawing explicitly states otherwise"},
        }
    )


def build_page_triage_prompt(image_list):
    return """# HVAC Drawing Page Triage

Classify every attached page as `core_geometry`, `support_context`, or `exclude` from visible sheet evidence only. Do not extract walls or dimensions in this pass. Use explicit title-block floor labels, keeping `LOWER GROUND FLOOR`, `GROUND FLOOR`, `UPPER GROUND FLOOR`, and `BASEMENT FLOOR` separate unless the drawing says otherwise.

Return valid JSON only:

```json
{"provider":"chatgpt_manual","model":"manual_page_triage","result":{"page_triage":{"pages":[{"page":1,"disposition":"core_geometry|support_context|exclude","page_role":"main_floor_plan|supporting_geometry_plan|reflected_ceiling_plan|existing_hvac_plan|reference_context|detail_context|3d_render","floor_label":"string","evidence":["visible evidence"]}]}}}
```

Attached page index:

```json
""" + json.dumps(image_list, indent=2) + "\n```\n"


def zip_chatgpt_packet(output_dir, include_screenshots=False):
    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                if not include_screenshots and path.parent.name == "screenshots":
                    continue
                archive.write(path, path.relative_to(output_dir))
    return zip_path


def safe_name(value):
    safe = "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_")
    return safe or "context"


def main():
    parser = argparse.ArgumentParser(description="Create a manual ChatGPT geometry review packet.")
    parser.add_argument("ai_input", help="Path to ai_input.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()
    result = create_chatgpt_packet(args.ai_input, args.output_dir, not args.no_zip, args.dpi)
    print(f"ChatGPT packet created: {result['folder']}")


if __name__ == "__main__":
    main()
