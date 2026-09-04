#!/usr/bin/env python3

import json
import shutil
import zipfile
from pathlib import Path

from ai.ai_packet import load_json
from ai.candidate_review import create_candidate_review


ALLOWED_CLASSIFICATIONS = {
    "existing_wall", "new_solid_wall", "new_partition", "demolished_wall",
    "lease_line", "dimension_line", "witness_line", "door_opening", "column",
    "fixture_joinery", "equipment", "annotation", "noise",
}
PHYSICAL_WALL_CLASSIFICATIONS = {"existing_wall", "new_solid_wall", "new_partition"}
CONFIDENCE_VALUES = {"low", "medium", "high"}


def create_wall_classification_packet(ai_input_path, vector_geometry_path, screenshots_dir, output_dir=None, zip_packet=True):
    """Create the first manual review packet: classify drawing candidates before matching."""
    ai_input_path = Path(ai_input_path)
    vector_geometry_path = Path(vector_geometry_path)
    screenshots_dir = Path(screenshots_dir)
    review_dir = ai_input_path.parent
    output_dir = Path(output_dir or review_dir / "wall_classification_packet")
    candidate_review_path = review_dir / "wall_classification_candidate_review.json"
    overlays_dir = review_dir / "wall_classification_overlays"

    create_candidate_review(vector_geometry_path, None, candidate_review_path, overlays_dir, screenshots_dir)
    candidate_review = load_json(candidate_review_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    copy_tree_files(screenshots_dir, output_dir / "screenshots")
    copy_tree_files(overlays_dir, output_dir / "candidate_overlays")
    copies = {
        "ai_input.json": ai_input_path,
        "vector_geometry.json": vector_geometry_path,
        "candidate_review.json": candidate_review_path,
    }
    for name, source in copies.items():
        if source.exists():
            shutil.copy2(source, output_dir / name)

    prompt_path = output_dir / "prompt.md"
    manifest_path = output_dir / "manifest.json"
    prompt_path.write_text(build_wall_classification_prompt(candidate_review), encoding="utf-8")
    manifest = {
        "packet_type": "manual_wall_classification",
        "candidate_review": "candidate_review.json",
        "screenshots": sorted(str(path.relative_to(output_dir)) for path in (output_dir / "screenshots").glob("*.png")),
        "candidate_overlays": sorted(str(path.relative_to(output_dir)) for path in (output_dir / "candidate_overlays").glob("*.png")),
        "instructions": [
            "Upload prompt.md, candidate_review.json, screenshots, and candidate overlays to ChatGPT.",
            "Return strict JSON only. This pass classifies candidates; it does not match dimensions to walls.",
            "Only existing_wall, new_solid_wall, and new_partition may become physical wall targets.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result = {
        "status": "created",
        "folder": str(output_dir),
        "prompt": str(prompt_path),
        "manifest": str(manifest_path),
        "candidate_review": str(candidate_review_path),
        "screenshots": [str(path) for path in sorted((output_dir / "screenshots").glob("*.png"))],
    }
    if zip_packet:
        result["zip"] = str(zip_packet_folder(output_dir))
    return result


def build_wall_classification_prompt(candidate_review):
    pages = [
        {
            "page": page.get("page"),
            "image": page.get("image", ""),
            "candidates": classification_candidates(page),
            "plan_role": page.get("plan_role", ""),
            "crops": page.get("crops", []),
        }
        for page in candidate_review.get("pages", [])
    ]
    return """# HVAC Drawing Candidate Classification

You are the first vision pass. Use the attached screenshots, labelled overlays, candidate_review.json, and any visible wall legend.

`major_dimension_zone` and `dimension_band_candidate` crops are enlarged views for reading crowded dimension context. Their `bbox_px`, candidate IDs, and values remain in the original full-screenshot `image_px` coordinate system. A dimension-band crop may have no extracted value; do not infer one without reading it visibly.

Classify each reviewed candidate using only visible drawing evidence. Do not match dimensions to walls in this pass. Do not invent candidate IDs or geometry.

Allowed classifications: existing_wall, new_solid_wall, new_partition, demolished_wall, lease_line, dimension_line, witness_line, door_opening, column, fixture_joinery, equipment, annotation, noise.

Only existing_wall, new_solid_wall, and new_partition are physical wall targets for the later dimension pass. Lease lines remain boundary context. Counters, fixtures, dimensions, notes, and symbols must never be classified as walls.

Every physical-wall decision needs at least one concise visible-evidence statement. Use legend_pages when a visible legend supports the line style. Without a legend reference, do not use high confidence for a wall-style classification.

Return strict JSON only in this shape:

```json
{
  "provider": "chatgpt_manual",
  "model": "manual_wall_classification",
  "result": {
    "wall_classification": {
      "pages": [
        {
          "page": 1,
          "candidate_decisions": [
            {
              "candidate_id": "P1-VLINE-001",
              "classification": "existing_wall",
              "confidence": "medium",
              "visible_evidence": ["Heavy double line forms the tenancy perimeter."],
              "legend_pages": [7]
            }
          ],
          "unclassified_candidate_ids": []
        }
      ]
    }
  }
}
```

Candidate pages:

```json
""" + json.dumps(pages, indent=2) + "\n```\n"


def validate_wall_classification(vision, candidate_review):
    known = candidate_ids_by_page(candidate_review)
    result = vision.get("result", {}) if isinstance(vision, dict) else {}
    classification = result.get("wall_classification", {}) if isinstance(result, dict) else {}
    pages = classification.get("pages", []) if isinstance(classification, dict) else []
    issues = []
    decisions = []
    for page in pages:
        page_number = page.get("page")
        seen_ids = set()
        if page_number not in known:
            issues.append(issue(page_number, "page", "page is not present in candidate_review.json"))
            continue
        for decision in page.get("candidate_decisions", []):
            candidate_id = decision.get("candidate_id")
            category = decision.get("classification")
            if candidate_id in seen_ids:
                issues.append(issue(page_number, "candidate_id", f"{candidate_id} was classified more than once"))
                continue
            seen_ids.add(candidate_id)
            if candidate_id not in known[page_number]:
                issues.append(issue(page_number, "candidate_id", f"{candidate_id} is not present on this page"))
                continue
            if category not in ALLOWED_CLASSIFICATIONS:
                issues.append(issue(page_number, "classification", f"{category} is not an allowed classification"))
                continue
            evidence = decision.get("visible_evidence")
            if category in PHYSICAL_WALL_CLASSIFICATIONS and (not isinstance(evidence, list) or not evidence):
                issues.append(issue(page_number, "visible_evidence", "physical wall classifications require visible evidence"))
                continue
            confidence = decision.get("confidence")
            if confidence not in CONFIDENCE_VALUES:
                issues.append(issue(page_number, "confidence", "confidence must be low, medium, or high"))
                continue
            normalised = dict(decision, page=page_number)
            if category in PHYSICAL_WALL_CLASSIFICATIONS and confidence == "high" and not decision.get("legend_pages"):
                normalised["effective_confidence"] = "medium"
                normalised["validation_note"] = "High confidence reduced to medium because no wall legend page was cited."
            else:
                normalised["effective_confidence"] = confidence
            decisions.append(normalised)
    return {
        "status": "valid" if not issues else "validation_issues",
        "issue_count": len(issues),
        "issues": issues,
        "decisions": decisions,
        "approved_wall_candidates_by_page": approved_wall_candidates(decisions),
    }


def validate_wall_classification_file(path, candidate_review_path, output_path=None):
    path = Path(path)
    report = validate_wall_classification(load_json(path), load_json(candidate_review_path))
    output_path = Path(output_path or path.with_name("wall_classification_validation.json"))
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path


def approved_wall_candidates(decisions):
    pages = {}
    for decision in decisions:
        if decision.get("classification") in PHYSICAL_WALL_CLASSIFICATIONS:
            pages.setdefault(str(decision["page"]), []).append(decision)
    return pages


def candidate_ids_by_page(candidate_review):
    return {
        page.get("page"): {item.get("candidate_id") for item in classification_candidates(page) if item.get("candidate_id")}
        for page in candidate_review.get("pages", [])
    }


def classification_candidates(page):
    candidates = page.get("wall_groups") or (page.get("wall_candidates", []) + page.get("curve_candidates", []))
    return [
        {
            "candidate_id": item.get("wall_group_id") or item.get("candidate_id"),
            "review_role": item.get("review_role", ""),
            "classification_reasons": item.get("classification_reasons", []),
            "source_candidate_ids": item.get("source_candidate_ids", []),
            "points_px": item.get("points_px", []),
            "start_px": item.get("line_start_px") or item.get("start_px"),
            "end_px": item.get("line_end_px") or item.get("end_px"),
        }
        for item in candidates
        if item.get("wall_group_id") or item.get("candidate_id")
    ]


def issue(page, field, message):
    return {"page": page, "field": field, "message": message}


def copy_tree_files(source_dir, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    if not source_dir.exists():
        return
    for source in source_dir.glob("*.png"):
        shutil.copy2(source, target_dir / source.name)


def zip_packet_folder(folder):
    target = folder.with_suffix(".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder))
    return target
