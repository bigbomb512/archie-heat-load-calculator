"""Bind PDF, OCR, vector, table, and vision observations to one another.

The calculation extractor deliberately finds candidates conservatively.  This
module adds the missing identity layer: it records why an observation belongs
to a room/opening and refuses to resolve ambiguous matches.  It does not
calculate loads or approve engineering inputs.
"""

from copy import deepcopy
import hashlib
import json
import re


BINDING_SCHEMA_VERSION = 1
BINDING_VERSION = "evidence-binding-v1"


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _normalise(value):
    return " ".join(str(value or "").casefold().split())


def _tag(value):
    return str(value or "").strip().upper()


def _location(value):
    if isinstance(value, (list, tuple)):
        return tuple(round(float(item), 3) for item in value[:4] if isinstance(item, (int, float)))
    if isinstance(value, dict):
        return tuple(round(float(value.get(key)), 3) for key in ("x", "y", "width", "height") if isinstance(value.get(key), (int, float)))
    return ()


def _stable_id(source_fp, *parts):
    return "bind_" + _fingerprint([source_fp, *parts])[:24]


def _pages(values):
    return sorted({value for value in values if value is not None}, key=lambda value: (isinstance(value, str), str(value)))


def _page_roles(evidence, coverage):
    roles = {row.get("page"): row for row in (coverage or {}).get("page_roles", [])}
    for row in evidence.get("pages", []):
        roles.setdefault(row.get("page"), row)
    return roles


def _is_plan(role):
    return str(role or "") in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}


def _is_elevation(role):
    return str(role or "") in {"opening_elevation", "opening_schedule", "elevation", "elevation_or_section", "section"}


def _is_reference(role):
    return str(role or "") in {"3d_render", "3d_reference", "reference", "legend_or_general_notes"}


def _candidate_observation(source_fp, row):
    source = row.get("source") or {}
    return {
        "observation_id": row.get("candidate_id") or _stable_id(source_fp, source.get("page"), row.get("category"), row.get("target")),
        "type": row.get("category", "unknown"),
        "target": row.get("target", ""),
        "value": deepcopy(row.get("value")),
        "unit": row.get("unit", ""),
        "source": deepcopy(source),
        "extraction_method": row.get("extraction_method", "unknown"),
        "confidence": row.get("confidence", "unknown"),
        "candidate_id": row.get("candidate_id", ""),
        "room_id": row.get("room_id", ""),
        "witness_ids": list(row.get("witness_ids") or []),
    }


def _raw_observations(source_fp, spatial_ocr, ai_input):
    """Retain table/image/OCR witnesses even when they do not form candidates."""
    observations = []
    pages = {row.get("page"): row for row in (ai_input or {}).get("drawing_set", {}).get("pages", [])}
    for page_row in (spatial_ocr or {}).get("pages", []):
        page = page_row.get("page")
        for index, word in enumerate(page_row.get("word_samples", []) or []):
            text = word.get("text", "") if isinstance(word, dict) else str(word)
            if not text:
                continue
            loc = _location((word or {}).get("bbox") if isinstance(word, dict) else None)
            observations.append({
                "observation_id": _stable_id(source_fp, page, "ocr_word", text, loc, index),
                "type": "ocr_word", "raw_value": text, "source": {"page": page, "drawing_number": pages.get(page, {}).get("drawing_number", ""), "coordinates": loc, "kind": "architect_pdf"},
                "extraction_method": "spatial_ocr", "confidence": (word or {}).get("confidence", "unknown") if isinstance(word, dict) else "unknown",
            })
        for key in ("table_cells", "tables"):
            for index, cell in enumerate(page_row.get(key, []) or []):
                if not isinstance(cell, dict):
                    continue
                text = cell.get("text") or cell.get("value") or ""
                loc = _location(cell.get("bbox") or cell.get("coordinates"))
                observations.append({
                    "observation_id": _stable_id(source_fp, page, "table_cell", cell.get("table_id", ""), cell.get("row", cell.get("row_index", "")), cell.get("column", cell.get("column_index", "")), text, loc, index),
                    "type": "table_cell", "raw_value": text, "table_id": cell.get("table_id", ""),
                    "row": cell.get("row", cell.get("row_index")), "column": cell.get("column", cell.get("column_index")),
                    "source": {"page": page, "drawing_number": pages.get(page, {}).get("drawing_number", ""), "coordinates": loc, "kind": "architect_pdf"},
                    "extraction_method": "table_ocr", "confidence": cell.get("confidence", "unknown"),
                })
        for index, witness in enumerate(page_row.get("image_witnesses", []) or []):
            if not isinstance(witness, dict):
                continue
            label = witness.get("label") or witness.get("tag") or witness.get("kind", "image")
            loc = _location(witness.get("bbox") or witness.get("coordinates"))
            observations.append({
                "observation_id": _stable_id(source_fp, page, "image", label, loc, index),
                "type": "image_witness", "raw_value": label,
                "source": {"page": page, "drawing_number": pages.get(page, {}).get("drawing_number", ""), "coordinates": loc, "kind": "architect_pdf"},
                "extraction_method": witness.get("extraction_method", "vision"), "confidence": witness.get("confidence", "unknown"),
                "cross_check_only": bool(witness.get("cross_check_only", True)),
            })
    return observations


def _raw_vector_observations(source_fp, vector_geometry, required_ids=None):
    """Retain named vector witnesses without treating raw primitives as walls."""
    observations = []
    pages = ((vector_geometry or {}).get("geometry_key_points") or {}).get("pages", [])
    required_ids = {str(value) for value in (required_ids or set())}
    for page_row in pages:
        page = page_row.get("page")
        retained_by_collection = {}
        for collection, kind in (("dimension_candidates", "vector_dimension"), ("line_candidates", "vector_line"), ("curve_candidates", "vector_curve"), ("opening_candidates", "vector_opening")):
            for item in page_row.get(collection, []) or []:
                if not isinstance(item, dict):
                    continue
                raw_id = item.get("candidate_id") or item.get("id")
                if not raw_id:
                    continue
                # Raw vector files can contain tens of thousands of drawing
                # primitives.  Keep all named dimensions/openings and explicit
                # witnesses, but only a bounded set of high-confidence wall
                # candidates for inspection.  This keeps the derived artifact
                # useful in the browser without discarding cited witnesses.
                try:
                    score = float(item.get("confidence_score", 0) or 0)
                except (TypeError, ValueError):
                    score = 0
                high_confidence = str(item.get("confidence", "")).casefold() == "high" or score >= 70
                keep = str(raw_id) in required_ids or collection in {"dimension_candidates", "opening_candidates"} or high_confidence
                if not keep or retained_by_collection.get(collection, 0) >= 400:
                    continue
                retained_by_collection[collection] = retained_by_collection.get(collection, 0) + 1
                observations.append({
                    "observation_id": raw_id,
                    "type": kind,
                    "raw_value": item.get("text_seen") or item.get("value_mm") or item.get("geometry_type") or item.get("tag", ""),
                    "source": {"page": page, "coordinates": _location(item.get("bbox_px") or item.get("bbox")), "kind": "pdf_vector", "vector_id": raw_id},
                    "extraction_method": item.get("source", "pdf_vector"),
                    "confidence": item.get("confidence", "unknown"),
                    "needs_review": bool(item.get("needs_review", True)),
                    "cross_check_only": kind != "vector_dimension",
                })
    return observations


def _value_key(row):
    return json.dumps(row.get("value"), sort_keys=True, separators=(",", ":"))


def _candidate_label(row):
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    return _normalise(row.get("room_id") or row.get("label") or value.get("tag") or value.get("name"))


def bind_calculation_evidence(evidence, *, ai_input=None, coverage=None, spatial_ocr=None, vector_geometry=None, vision_response=None):
    """Return evidence with deterministic cross-page bindings and conflicts."""
    result = deepcopy(evidence or {})
    source_fp = result.get("source_fingerprint", "")
    roles = _page_roles(result, coverage)
    candidates = result.get("candidates", [])
    observations = [_candidate_observation(source_fp, row) for row in candidates]
    observations.extend(_raw_observations(source_fp, spatial_ocr, ai_input))
    required_vector_ids = {str(value) for row in candidates for value in (row.get("witness_ids") or [])}
    for entity in ((vision_response or {}).get("result", {}).get("auto_extraction", {}).get("entities", []) or []):
        for item in (entity.get("witnesses") or []):
            if isinstance(item, dict) and item.get("reference"):
                required_vector_ids.add(str(item["reference"]))
            elif isinstance(item, str) and item:
                required_vector_ids.add(item)
    observations.extend(_raw_vector_observations(source_fp, vector_geometry, required_vector_ids))
    observation_by_id = {row["observation_id"]: row for row in observations}
    relationships, conflicts, review_items = [], [], []

    def link(kind, left, right, basis, *, status="matched", cross_check_only=False):
        left_source = left.get("source") or {}
        right_source = right.get("source") or {}
        relationships.append({
            "relationship_id": _stable_id(source_fp, kind, left.get("observation_id", left.get("candidate_id")), right.get("observation_id", right.get("candidate_id")), basis),
            "kind": kind, "from_observation_id": left.get("observation_id", left.get("candidate_id")),
            "to_observation_id": right.get("observation_id", right.get("candidate_id")), "status": status,
            "basis": basis, "pages": _pages((left_source.get("page"), right_source.get("page"))),
            "cross_check_only": cross_check_only,
        })

    # Vision/manual candidates can cite raw vector IDs.  Preserve those links
    # exactly; do not infer a wall or area from a vector primitive merely
    # because it is nearby.
    for row in candidates:
        left = _candidate_observation(source_fp, row)
        for witness_id in row.get("witness_ids", []) or []:
            right = observation_by_id.get(str(witness_id))
            if right:
                link("candidate_vector_witness", left, right, "explicit_witness_id", cross_check_only=right.get("cross_check_only", False))

    # Exact labels are the safest room-level binding.  This is intentionally
    # stricter than proximity: labels must be normalized-identical.
    room_labels = {}
    for row in candidates:
        label = _candidate_label(row)
        if label and row.get("category") in {"area", "ceiling_height", "lighting", "occupancy", "outside_air", "setpoint", "boundary"}:
            room_labels.setdefault(label, []).append(row)
    for label, rows in room_labels.items():
        pages = {row.get("source", {}).get("page") for row in rows}
        if len(pages) > 1:
            for left in rows:
                for right in rows:
                    if left is right or left.get("source", {}).get("page") == right.get("source", {}).get("page"):
                        continue
                    link("room_value_cross_page", _candidate_observation(source_fp, left), _candidate_observation(source_fp, right), "exact_normalized_room_label")

    # Opening tags are allowed to bind plan and elevation/schedule records only
    # when one target exists on each side.  Multiple targets remain conflicts.
    opening_rows = {}
    for row in candidates:
        if row.get("category") != "opening":
            continue
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        tag = _tag(value.get("tag") or row.get("label"))
        if tag:
            opening_rows.setdefault(tag, []).append(row)
    for tag, rows in opening_rows.items():
        plans = [row for row in rows if _is_plan(roles.get(row.get("source", {}).get("page"), {}).get("proposed_role") or roles.get(row.get("source", {}).get("page"), {}).get("role"))]
        elevations = [row for row in rows if _is_elevation(roles.get(row.get("source", {}).get("page"), {}).get("proposed_role") or roles.get(row.get("source", {}).get("page"), {}).get("role"))]
        if len(plans) == 1 and len(elevations) == 1:
            left, right = _candidate_observation(source_fp, plans[0]), _candidate_observation(source_fp, elevations[0])
            link("opening_plan_elevation_binding", left, right, "exact_opening_tag_and_unique_target")
            for row in (plans[0], elevations[0]):
                row.setdefault("witness_ids", []).extend([left["observation_id"], right["observation_id"]])
                row["binding_status"] = "matched"
                row["binding_basis"] = "exact_opening_tag_and_unique_target"
        elif elevations and (len(plans) != 1 or len(elevations) != 1):
            ids = [row.get("candidate_id") for row in rows]
            conflict_id = _stable_id(source_fp, "opening", tag, sorted(ids))
            conflicts.append({"conflict_id": conflict_id, "kind": "opening_binding_ambiguous", "label": tag, "candidate_ids": ids,
                              "pages": _pages(row.get("source", {}).get("page") for row in rows), "status": "review_required",
                              "reason": "The opening tag appears on multiple possible plan/elevation records; no unique binding was selected."})
            for row in rows:
                row["status"] = "conflict" if row.get("status") != "evidence_only" else row.get("status")
                row.setdefault("unresolved_fields", []).append("unique_opening_binding")

    # Vision and PDF candidates are independent witnesses only when they target
    # the same field and agree exactly.  A disagreement is a hard conflict.
    for row in candidates:
        if row.get("extraction_method") != "manual_vision_response":
            continue
        peers = [other for other in candidates if other is not row and other.get("target") == row.get("target") and other.get("extraction_method") != "manual_vision_response"]
        for peer in peers:
            left, right = _candidate_observation(source_fp, row), _candidate_observation(source_fp, peer)
            if _value_key(row) == _value_key(peer):
                link("vision_pdf_independent_witness", left, right, "same_target_same_value")
                row.setdefault("witness_ids", []).append(peer.get("candidate_id"))
                peer.setdefault("witness_ids", []).append(row.get("candidate_id"))
                row["binding_status"] = peer["binding_status"] = "cross_source_supported"
            else:
                conflict_id = _stable_id(source_fp, "vision_pdf_conflict", row.get("target"), row.get("candidate_id"), peer.get("candidate_id"))
                conflicts.append({"conflict_id": conflict_id, "kind": "vision_pdf_value_conflict", "target": row.get("target"),
                                  "candidate_ids": [row.get("candidate_id"), peer.get("candidate_id")],
                                  "pages": _pages((row.get("source", {}).get("page"), peer.get("source", {}).get("page"))), "status": "review_required",
                                  "reason": "Vision and PDF evidence disagree for the same calculator field."})
                for item in (row, peer):
                    item["status"] = "conflict" if item.get("status") != "evidence_only" else item.get("status")
                    item.setdefault("unresolved_fields", []).append("cross_source_conflict")

    # Render/3D observations are explicitly cross-check-only.  They can be
    # related to a same-label candidate but can never activate it.
    for row in candidates:
        role = roles.get(row.get("source", {}).get("page"), {}).get("proposed_role") or roles.get(row.get("source", {}).get("page"), {}).get("role")
        if not _is_reference(role):
            continue
        label = _candidate_label(row)
        for other in candidates:
            if other is row or _candidate_label(other) != label or not label:
                continue
            link("reference_cross_check", _candidate_observation(source_fp, row), _candidate_observation(source_fp, other), "same_normalized_label", cross_check_only=True)
            row["cross_check_only"] = True

    for conflict in conflicts:
        review_items.append({"item_id": conflict["conflict_id"], "affected_id": conflict.get("target") or conflict.get("label", ""),
                             "status": "blocked", "source_artifact": "calculation_input_evidence.json",
                             "page": (conflict.get("pages") or [None])[0], "reason": conflict["reason"],
                             "remediation": "Review the cited pages and resolve the competing evidence before assembly."})
    relationships.sort(key=lambda row: row["relationship_id"])
    conflicts.sort(key=lambda row: row["conflict_id"])
    review_items.sort(key=lambda row: row["item_id"])
    result["binding"] = {
        "schema_version": BINDING_SCHEMA_VERSION, "binding_version": BINDING_VERSION,
        "source_fingerprint": source_fp, "observations": sorted(observations, key=lambda row: row["observation_id"]),
        "relationships": relationships, "conflicts": conflicts, "review_items": review_items,
        "fingerprint": _fingerprint({"source_fingerprint": source_fp, "observations": observations, "relationships": relationships, "conflicts": conflicts}),
    }
    result.setdefault("issues", []).extend(review_items)
    result["issues"] = sorted({json.dumps(item, sort_keys=True): item for item in result["issues"]}.values(), key=lambda row: json.dumps(row, sort_keys=True))
    categories = {}
    for row in candidates:
        category = row.get("category", "unknown")
        bucket = categories.setdefault(category, {"count": 0, "active": 0, "blocked": 0, "proposed": 0, "evidence_only": 0, "conflict": 0})
        bucket["count"] += 1
        bucket[row.get("status", "proposed")] = bucket.get(row.get("status", "proposed"), 0) + 1
    result["categories"] = categories
    if conflicts:
        result["status"] = "blocked"
    result["fingerprint"] = _fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result
