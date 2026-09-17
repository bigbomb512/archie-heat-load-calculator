#!/usr/bin/env python3
"""Create a complete, private manual-vision handoff for Drawing 6.

This does not modify calculator artifacts. It indexes every architect page,
renders only the bounded ranked context plus a separate exception appendix, and
includes machine-readable context (structured PDF text, OCR, vector witnesses,
and existing calculation candidates). Role/capability metadata tells the
manual ChatGPT workflow what each page can legitimately prove; it does not turn
every page into primary geometry evidence.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.vision_extraction import build_ranked_context

ROLE_PURPOSES = {
    "main_floor_plan": "Primary room geometry, dimensions, labels, and wall/opening relationships.",
    "supporting_geometry_plan": "Supporting room/finish geometry and boundary evidence.",
    "reflected_ceiling_plan": "Ceiling heights, ceiling transitions, and lighting evidence.",
    "services_or_lighting_plan": "Lighting/service locations and room-linked ceiling constraints.",
    "opening_elevation": "Opening tags, elevation dimensions, and storefront relationships.",
    "elevation_or_section": "Vertical levels, ceiling heights, openings, and surface relationships.",
    "reference": "Supporting material, finish, legend, or general-note evidence only.",
    "3d_render": "Visual cross-check only; no scale or primary dimensions.",
}


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else (default or {})


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_fingerprint(ai_input):
    return fingerprint({"source_pdf": ai_input.get("source_pdf", ""), "drawing_set": ai_input.get("drawing_set", {})})


def render(pdf, page, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", str(page), "-l", str(page), "-r", "150", str(pdf), str(target.with_suffix(""))], check=True, capture_output=True)


def build(project_dir):
    project_dir = Path(project_dir)
    ai_input_path = project_dir / "ai_input.json"
    ai_input = load(ai_input_path)
    pdf = Path(ai_input.get("source_pdf", ""))
    if not pdf.exists():
        raise ValueError(f"Source PDF is missing: {pdf}")
    output = project_dir / "manual_vision_handoff"
    images = output / "pages"
    exceptions_dir = output / "exceptions"
    images.mkdir(parents=True, exist_ok=True)
    exceptions_dir.mkdir(parents=True, exist_ok=True)
    for old in images.glob("*.png"):
        old.unlink()
    for old in exceptions_dir.glob("*.png"):
        old.unlink()
    page_rows = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", [])}
    coverage = load(project_dir / "drawing_coverage.json")
    context_selection = build_ranked_context(ai_input, coverage)
    context_by_page = {row["page"]: row for row in context_selection.get("pages", [])}
    coverage_roles = {row.get("page"): row for row in coverage.get("page_roles", [])}
    spatial_ocr = load(project_dir / "spatial_ocr.json")
    ocr_by_page = {row.get("page"): row for row in spatial_ocr.get("pages", [])}
    vector = load(project_dir / "vector_geometry.json")
    vector_pages = (vector.get("geometry_key_points") or {}).get("pages", [])
    vector_by_page = {row.get("page"): row for row in vector_pages}
    existing = load(project_dir / "calculation_input_evidence.json")
    existing_candidates = existing.get("candidates", [])
    page_relationships = []
    for link in coverage.get("cross_sheet_links", []):
        linked_pages = link.get("pages", [])
        for linked_page in linked_pages:
            page_relationships.append({
                "page": linked_page,
                "related_pages": [p for p in linked_pages if p != linked_page],
                "level_name": link.get("level_name", ""),
                "thermal_role": link.get("thermal_role", ""),
            })
    relationships_by_page = {}
    for relation in page_relationships:
        relationships_by_page.setdefault(relation["page"], []).append(relation)
    selected = {}
    for row in ai_input.get("drawing_set", {}).get("pages", []):
        page = row.get("page")
        role_row = coverage_roles.get(page, {})
        context_row = context_by_page.get(page, {})
        role = context_row.get("role") or role_row.get("proposed_role", "reference")
        selected[page] = {
            "role": role,
            "purpose": ROLE_PURPOSES.get(role, "Supporting architect evidence; do not infer unsupported values."),
            "capabilities": role_row.get("capabilities", []),
            "capability_map": role_row.get("capability_map", {}),
            "relevance": role_row.get("relevance", {}),
            "selection": context_row.get("context_selection", "reference_only"),
            "selection_reasons": context_row.get("context_selection_reasons") or role_row.get("selection_reasons", []),
            "identity": role_row.get("identity", {}),
            "related_pages": role_row.get("related_pages", []),
        }
    manifest_pages = []
    for page in sorted(selected):
        meta = selected[page]
        row = page_rows.get(page, {})
        role_row = coverage_roles.get(page, {})
        ocr = ocr_by_page.get(page, {})
        vector_row = vector_by_page.get(page, {})
        image_path = ""
        if meta["selection"] in {"primary_context", "supporting_context", "cross_check_context", "ranked_exception"}:
            folder = exceptions_dir if meta["selection"] == "ranked_exception" else images
            filename = f"page_{page:03d}.png"
            target = folder / filename
            render(pdf, page, target)
            image_path = f"{folder.name}/{filename}"
        manifest_pages.append({
            "page": page,
            "drawing_number": (meta.get("identity") or {}).get("selected_drawing_number") or row.get("drawing_number", ""),
            "legacy_drawing_number": row.get("drawing_number", ""),
            "title": row.get("title", ""),
            "role": meta["role"],
            "purpose": meta["purpose"],
            "capabilities": meta.get("capabilities", []),
            "level_candidate": row.get("level_name", ""),
            "classification_evidence": role_row.get("classification_evidence", row.get("classification_evidence", [])),
            "authority_status": role_row.get("authority_status", "proposed"),
            "geometry_eligible": bool(role_row.get("geometry_eligible", False)),
            "reference_only": bool(role_row.get("reference_only", meta["role"] in {"reference", "3d_render"})),
            "related_page_groups": relationships_by_page.get(page, []),
            "capability_map": meta.get("capability_map", {}),
            "relevance": meta.get("relevance", {}),
            "selection": meta.get("selection", "reference_only"),
            "selection_reasons": meta.get("selection_reasons", []),
            "identity": meta.get("identity", {}),
            "image": image_path,
            "source_text": (row.get("structured_content") or {}).get("markdown", "")[:16000],
            "ocr": {
                "title_blocks": ocr.get("title_blocks", [])[:10],
                "room_label_candidates": ocr.get("room_label_candidates", [])[:80],
                "dimension_candidates": ocr.get("dimension_candidates", [])[:120],
                "word_samples": ocr.get("word_samples", [])[:240],
            },
            "vector": {
                "raw_counts": vector_row.get("raw_counts", {}),
                "dimension_candidates": vector_row.get("dimension_candidates", [])[:80],
                "line_candidates": vector_row.get("line_candidates", [])[:120],
                "opening_candidates": vector_row.get("openings", [])[:80],
                "note": vector_row.get("note", ""),
            },
            "existing_calculation_candidates": [
                candidate for candidate in existing_candidates
                if (candidate.get("source") or {}).get("page") == page
            ],
            "source_fingerprint": source_fingerprint(ai_input),
        })

    context = {
        "source_pdf": str(pdf),
        "source_fingerprint": source_fingerprint(ai_input),
        "context_selection": context_selection,
        "pages": manifest_pages,
        "existing_page_roles": coverage.get("page_roles", []),
        "existing_candidates": existing_candidates,
        "rules": [
            "Use only facts directly visible or explicitly printed on the cited page.",
            "Use the ranked main context for extraction; consult the separate exception appendix only when it can resolve or challenge a selected fact.",
            "Every observation must cite page, drawing number, excerpt, and coordinates or table cell when available.",
            "A 3D page is cross-check evidence only and cannot supply scale, dimensions, or room area.",
            "Do not infer occupancy, schedules, U-values, thermal boundaries, glazing performance, or equipment heat-to-space values.",
            "Keep unresolved room allocation and competing values as explicit review items.",
        ],
    }
    (output / "context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    (output / "page_index.json").write_text(json.dumps({
        "schema_version": 1,
        "source_fingerprint": source_fingerprint(ai_input),
        "context_selection": context_selection,
        "page_count": len(manifest_pages),
        "pages": manifest_pages,
    }, indent=2), encoding="utf-8")
    prompt = """# Drawing 6 manual vision evidence handoff

Review the ranked architect evidence pages and return **one JSON object only**.
The page index retains every architect page; the primary images are selected
for relevance and the exceptions directory contains likely-useful ambiguous
pages. Inspect an exception page when it can resolve or challenge a selected
fact, but do not treat every page as equally authoritative.
This is evidence extraction, not load calculation. Do not invent dimensions,
areas, schedules, U-values, thermal boundaries, occupancy, or equipment heat.
Use page numbers, drawing numbers, role/capability metadata, and the supplied
source text/OCR/vector evidence from context.json. Pages with reference-only
or 3D roles are still useful context, but cannot provide primary dimensions.
3D/render pages are cross-check-only. Reference-only pages remain indexed but
are not part of the main context unless a later targeted review requests them.

Return this shape:

```json
{
  "observations": [
    {
      "observation_id": "stable-source-based-id",
      "page": 20,
      "drawing_number": "202",
      "type": "room_label|boundary|dimension|area|ceiling_height|lighting|opening|equipment|cross_check",
      "label": "Cool Room",
      "value": null,
      "unit": "",
      "coordinates": null,
      "table_cell": null,
      "excerpt": "exact visible text or a concise visual witness",
      "extraction_method": "pdf_text|ocr|vector|vision",
      "confidence": "low|medium|high",
      "status": "evidence_only|proposed|conflict",
      "witness_ids": [],
      "unresolved_fields": []
    }
  ],
  "relationships": [],
  "conflicts": [],
  "missing_evidence": []
}
```

A relationship must state its basis (exact label/tag, compatible dimension,
or explicit geometry witness) and cite every supporting page. If an opening
cannot be uniquely matched to a plan element, create a conflict. If a ceiling
height cannot be allocated to a room, keep it unresolved. Use stable IDs based
on source page/drawing/entity/location, never array position. Do not output
any cooling or heating result.
"""
    (output / "prompt.md").write_text(prompt, encoding="utf-8")
    manifest = {
        "artifact": "manual_vision_handoff",
        "schema_version": 2,
        "source_pdf": str(pdf),
        "source_fingerprint": source_fingerprint(ai_input),
        "context_selection": context_selection,
        "page_count": len(manifest_pages),
        "architect_page_count": len(manifest_pages),
        "pages": manifest_pages,
        "context": "context.json",
        "page_index": "page_index.json",
        "prompt": "prompt.md",
        "selected_page_count": len(context_selection.get("main_context_pages", [])),
        "exception_page_count": len([row for row in manifest_pages if row.get("image", "").startswith("exceptions/")]),
        "instructions": [
            "Upload prompt.md, context.json, page_index.json, pages/, and exceptions/ to the manual ChatGPT workflow.",
            "Use the ranked selection and capability map; page_index.json retains every architect page.",
            "Save the returned JSON as response.json; it remains evidence-only until local validation.",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    args = parser.parse_args()
    print(build(args.project_dir))
