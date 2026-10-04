"""Local persistence and source checks for reviewer-traced room boundaries."""

import hashlib
import json
import math
import re
from functools import lru_cache
from copy import deepcopy
from pathlib import Path

from ai import ai_preliminary, reviewer_room_geometry
from ai.room_use_resolution import room_identity
from ai.drawing_coverage import has_current_level_classification
from ai.geometry_resolution import fingerprint
from backend.vision_extraction_service import _atomic_json

INTERSECTION_CAP = 8000


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _paths(project):
    root = Path(project["review_dir"])
    return {"root": root, "artifact": root / "reviewer_room_geometry.json",
            "ai_input": root / "ai_input.json", "coverage": root / "drawing_coverage.json",
            "vector": root / "vector_geometry.json", "building": root / "building_evidence.json",
            "room_use": root / "room_use_resolution.json", "run": root / "ai_preliminary_run.json",
            "geometry": root / "geometry_resolution.json", "spatial": root / "spatial_ocr.json"}


def _source_pdf_fingerprint(paths):
    ai_input = _read(paths["ai_input"], {})
    source = Path(str(ai_input.get("source_pdf", "")))
    if source.is_file():
        stat = source.stat()
        return _cached_file_fingerprint(str(source), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return str(ai_input.get("source_pdf_fingerprint") or ai_input.get("source_fingerprint") or "")


@lru_cache(maxsize=16)
def _cached_file_fingerprint(path, inode, size, mtime_ns, ctime_ns):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _vector_pages(paths):
    vector = _read(paths["vector"], {})
    return vector.get("geometry_key_points", {}).get("pages", []) if isinstance(vector, dict) else []


def _page_fp(page):
    return fingerprint(page or {})


def current_records(paths, artifact=None):
    artifact = artifact if isinstance(artifact, dict) else _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    checked = reviewer_room_geometry.validate_artifact(artifact)
    active = reviewer_room_geometry.active_records(checked, _source_pdf_fingerprint(paths), _vector_pages(paths))
    rooms = _rooms(paths)
    return [resolved for row in active if (resolved := _trace_with_current_room(row, paths, rooms))]


def current_artifact_input(root):
    paths = _paths({"review_dir": str(root)})
    artifact = reviewer_room_geometry.validate_artifact(_read(paths["artifact"], reviewer_room_geometry.empty_artifact()))
    source_artifacts = {
        "ai_input": _read(paths["ai_input"], {}),
        "vector_geometry": _read(paths["vector"], {}),
        "reviewer_room_geometry": _read(paths["artifact"], {}),
        "room_use_resolution": _read(paths["room_use"], {}),
    }
    result = {"fingerprint": artifact.get("fingerprint", ""),
              "records": current_records(paths, artifact), "rooms": _rooms(paths),
              "source_artifact_fingerprints": {name: fingerprint(value) for name, value in source_artifacts.items()}}
    excluded = _not_a_room_identities(source_artifacts["room_use_resolution"])
    if excluded:
        # Only present when a reviewer has excluded a detection, so existing
        # registries (and the drafts fingerprinted from them) stay unchanged.
        result["excluded_room_identities"] = sorted(excluded)
    return result


def current_traced_areas(root):
    """Return positive areas from current, calibrated reviewer boundary proofs.

    Keys are room-use IDs. A geometry proof is eligible only when its linked
    reviewer trace is in the current trace register and the proof carries the
    same source fingerprints as that trace.
    """
    paths = _paths({"review_dir": str(root)})
    artifact_input = current_artifact_input(root)
    traces = {str(row.get("trace_id")): row for row in artifact_input.get("records", [])
              if isinstance(row, dict) and row.get("trace_id") and row.get("room_id")}
    rooms = {str(row.get("room_id")): row for row in artifact_input.get("rooms", [])
             if isinstance(row, dict) and row.get("room_id")}
    geometry = _read(paths["geometry"], {})
    proof_entities = {
        str(row.get("entity_id")): row for row in geometry.get("entities", [])
        if isinstance(row, dict) and row.get("kind") == "room_geometry_proof"
        and row.get("extraction_method") == "reviewer_traced_boundary"
    }
    areas = {}
    for proof in geometry.get("room_geometry_proofs", []):
        if not isinstance(proof, dict):
            continue
        entity = proof_entities.get(str(proof.get("proof_id", "")), {})
        value = entity.get("value") if isinstance(entity.get("value"), dict) else {}
        trace_id = str(value.get("reviewer_trace_id", ""))
        trace = traces.get(trace_id)
        if not trace:
            continue
        trace_sources = trace.get("source_fingerprints", {})
        if value.get("source_fingerprints") != trace_sources:
            continue
        calibration = proof.get("calibration") if isinstance(proof.get("calibration"), dict) else value.get("calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        try:
            area = float(proof.get("area_m2"))
            mm_per_px = float(calibration.get("mm_per_px"))
        except (TypeError, ValueError):
            continue
        status = calibration.get("status")
        if (not math.isfinite(area) or area <= 0 or not math.isfinite(mm_per_px) or mm_per_px <= 0
                or status not in {"agreed", "declared_scale_rejected"}):
            continue
        room_id = str(trace["room_id"])
        room = rooms.get(room_id, {})
        page = entity.get("source", {}).get("page") or trace.get("page")
        candidate = {
            "room_id": room_id, "area_m2": area, "trace_id": trace_id,
            "proof_id": str(proof.get("proof_id", "")), "calibration_status": status,
            "page": page, "source_fingerprints": deepcopy(trace_sources),
            "points_image_px": deepcopy(trace.get("points_image_px", [])),
            "calibration": deepcopy(calibration),
            "reviewer": str(trace.get("reviewer", "")),
            "edges": deepcopy(trace.get("edges", [])),
            "roof": str(trace.get("roof", "unknown")),
            "envelope_reviewer": str(trace.get("envelope_reviewer", "")),
            "envelope_declared_at": str(trace.get("envelope_declared_at", "")),
            "room_label": proof.get("room_label") or room.get("original_label") or room.get("name") or trace.get("room_label", ""),
            "level_name": proof.get("level_name") or room.get("level_name") or trace.get("level_name", ""),
        }
        areas.setdefault(room_id, []).append(candidate)
    # Multiple eligible traces are safe to use only when they agree within
    # the comparison allowance already used by calculator-draft tracing.
    result = {}
    for room_id, candidates in areas.items():
        candidates.sort(key=lambda row: (row["trace_id"], row["proof_id"]))
        values = [row["area_m2"] for row in candidates]
        spread = (max(values) - min(values)) / ((max(values) + min(values)) / 2.0) if len(values) > 1 else 0.0
        selected = deepcopy(candidates[0])
        if spread > 0.02:
            selected.update({"area_m2": None, "conflict": True, "supporting_traces": candidates})
        elif len(candidates) > 1:
            selected["area_m2"] = sum(values) / len(values)
            selected["supporting_traces"] = candidates
        result[room_id] = selected
    return result


def _trace_with_current_room(trace, paths, rooms=None):
    """Return a current trace view bound to the canonical room-use identity.

    Older traces may retain a building-evidence ID. Resolve them by the same
    normalized label/level identity used by the room picker, without rewriting
    the stored trace artifact.
    """
    rooms = _rooms(paths) if rooms is None else rooms
    by_id = {str(row.get("room_id")): row for row in rooms if isinstance(row, dict) and row.get("room_id")}
    trace_id = str(trace.get("room_id", ""))
    if trace_id in by_id:
        room = by_id[trace_id]
    else:
        identity = (_row_identity({"label": trace.get("room_label"), "level_name": trace.get("level_name")})
                    if trace.get("room_label") else None)
        if identity is None:
            identity_by_source_id = {}
            building = _read(paths["building"], {})
            for row in building.get("spaces", []) if isinstance(building, dict) else []:
                if isinstance(row, dict) and row.get("id"):
                    identity_by_source_id[str(row["id"])] = _row_identity({
                        "label": row.get("name", ""), "level_name": row.get("level_name", "")})
            room_use = _read(paths["room_use"], {})
            for row in room_use.get("records", []) if isinstance(room_use, dict) else []:
                if isinstance(row, dict) and row.get("room_id"):
                    identity_by_source_id[str(row["room_id"])] = _row_identity({
                        "label": row.get("original_label") or row.get("label", ""),
                        "level_name": row.get("level_name", "")})
            from backend.room_proposal import room_proposal
            proposal = room_proposal(_read(paths["run"], {}), paths["root"])
            for row in proposal.get("rooms", []):
                if isinstance(row, dict) and row.get("room_id"):
                    identity_by_source_id[str(row["room_id"])] = _row_identity({
                        "label": row.get("label", ""), "level_name": row.get("level_name") or row.get("level", "")})
            identity = identity_by_source_id.get(trace_id)
        room = next((row for row in rooms if identity is not None and _row_identity(row) == identity), None)
    if room is None:
        return None
    resolved = deepcopy(trace)
    resolved["room_id"] = str(room["room_id"])
    resolved["room_label"] = room.get("label", trace.get("room_label", ""))
    resolved["level_name"] = room.get("level_name", trace.get("level_name", ""))
    return resolved


def _scale_denominator(value):
    match = re.search(r"(?:1\s*:\s*|scale\s*1\s*:\s*)(\d+(?:\.\d+)?)", str(value or ""), re.I)
    return float(match.group(1)) if match else None


def _page_context(paths, web):
    ai_input = _read(paths["ai_input"], {})
    coverage = _read(paths["coverage"], {})
    level_method_current = has_current_level_classification(coverage)
    vector_pages = {row.get("page"): row for row in _vector_pages(paths) if isinstance(row, dict)}
    coverage_pages = {row.get("page"): row for row in coverage.get("pages", []) if isinstance(row, dict)}
    role_pages = {row.get("page"): row for row in coverage.get("page_roles", []) if isinstance(row, dict)}
    drawing_pages = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", []) if isinstance(row, dict)}
    source_pdf = str(ai_input.get("source_pdf", ""))
    source_path = Path(source_pdf) if source_pdf else None
    if source_path and source_path.is_file():
        stat = source_path.stat()
        pdf_sizes = _cached_pdf_sizes(source_pdf, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    else:
        pdf_sizes = {}
    fallback_numbers = _fallback_trace_page_numbers(role_pages, coverage_pages, drawing_pages, vector_pages)
    printed_dimensions = _printed_dimension_pages(paths) if fallback_numbers else {}
    pages = []
    for number, vector in sorted(vector_pages.items()):
        drawing = drawing_pages.get(number, {})
        coverage_page = coverage_pages.get(number, {})
        role = role_pages.get(number, {})
        proposed_role = role.get("proposed_role") or coverage_page.get("proposed_role") or drawing.get("plan_role")
        fallback_plan = number in fallback_numbers
        if proposed_role not in TRACE_FLOOR_PLAN_ROLES and not fallback_plan:
            continue
        system = vector.get("coordinate_systems", {}).get("image_px", {})
        image_width, image_height = system.get("image_width"), system.get("image_height")
        width_pt, height_pt = pdf_sizes.get(number, (None, None))
        scale_text = role.get("main_scale") or coverage_page.get("main_scale") or ""
        scale_denominator = _scale_denominator(scale_text)
        image_ref = vector.get("image")
        packet_root = paths["root"] / "chatgpt_packet"
        preview_path = (packet_root / image_ref).resolve() if isinstance(image_ref, str) else None
        try:
            if preview_path is None:
                raise ValueError("No full-resolution render")
            preview_path.relative_to((packet_root / "screenshots").resolve())
            from PIL import Image
            with Image.open(preview_path) as image:
                preview_matches = image.size == (image_width, image_height)
        except (OSError, ValueError, TypeError):
            preview_matches = False
        if not preview_matches:
            preview_path = None
        pages.append({"page": number, "title": drawing.get("title", role.get("title", "")),
                      "drawing_number": role.get("drawing_number") or coverage_page.get("drawing_number", ""),
                      "level_name": ((role.get("level_name") or coverage_page.get("level_name", ""))
                                     if level_method_current else ""),
                      "proposed_role": proposed_role, "declared_scale": scale_text,
                      "scale_denominator": scale_denominator, "pdf_width_pt": width_pt,
                      "pdf_height_pt": height_pt,
                      "image_px_per_pt": image_width / width_pt if isinstance(image_width, (int, float)) and width_pt else None,
                      "image_width_px": image_width, "image_height_px": image_height,
                      "vector_page_fingerprint": _page_fp(vector),
                      "preview_url": web.safe_link(preview_path) if preview_path else "",
                      "preview_width_px": image_width if preview_matches else None,
                      "preview_height_px": image_height if preview_matches else None,
                      "preview_matches_vector_coordinates": bool(preview_matches),
                      "preview_error": "Full-resolution plan image unavailable or does not match vector coordinates." if not preview_matches else "",
                      "fallback_plan": fallback_plan,
                      "fallback_reason": FALLBACK_TRACE_REASON if fallback_plan else "",
                      "printed_dimensions_found": bool(printed_dimensions.get(number)) if fallback_plan else None,
                      "calibration_warning": NO_DIMENSION_WARNING if fallback_plan and not printed_dimensions.get(number) else ""})
    return pages


TRACE_FLOOR_PLAN_ROLES = {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "floor_plan"}
TRACE_SERVICES_PLAN_ROLES = {"existing_hvac_plan", "services_or_lighting_plan"}
FALLBACK_TRACE_REASON = "No architectural floor plan in this set; tracing on a services plan."
# Calibration always needs a printed dimension that agrees with the declared
# scale (decided 2026-10-04: no scale-only calibration on services plans).
ARCHITECTURAL_SET_ADVICE = "Upload the architectural drawings for this tenancy to trace its rooms."
NO_DIMENSION_WARNING = ("No printed building dimensions were found on this services plan, so a room trace here cannot be "
                        "calibrated and saved. " + ARCHITECTURAL_SET_ADVICE)


def _printed_dimension_pages(paths):
    """Pages whose text layer has printed dimension candidates (spatial OCR)."""
    spatial = _read(paths["spatial"], {})
    return {page.get("page"): len(page.get("dimension_candidates") or []) for page in spatial.get("pages", [])
            if isinstance(page, dict)}


def _fallback_trace_page_numbers(role_pages, coverage_pages, drawing_pages, vector_pages):
    """Scaled services plans offered for tracing only when no floor plan exists.

    Mirrors ai.vector_geometry.fallback_services_plan_pages using the drawing
    coverage roles: any floor-plan page in the set disables the fallback.
    """
    numbers = set(role_pages) | set(coverage_pages) | set(drawing_pages)

    def role_of(number):
        return (role_pages.get(number, {}).get("proposed_role") or coverage_pages.get(number, {}).get("proposed_role")
                or drawing_pages.get(number, {}).get("plan_role") or "")

    if any(role_of(number) in TRACE_FLOOR_PLAN_ROLES for number in numbers):
        return set()
    eligible = set()
    for number in vector_pages:
        scale = role_pages.get(number, {}).get("main_scale") or coverage_pages.get(number, {}).get("main_scale") or ""
        if role_of(number) in TRACE_SERVICES_PLAN_ROLES and _scale_denominator(scale):
            eligible.add(number)
    return eligible


@lru_cache(maxsize=8)
def _cached_pdf_sizes(source_pdf, inode, size, mtime_ns, ctime_ns):
    try:
        import pdfplumber
        with pdfplumber.open(source_pdf) as pdf:
            return {index + 1: (float(page.width), float(page.height)) for index, page in enumerate(pdf.pages)}
    except Exception:
        return {}


def _rooms(paths):
    building = _read(paths["building"], {})
    room_use = _read(paths["room_use"], {})
    run = _read(paths["run"], {})
    from backend.room_proposal import room_proposal
    proposal = room_proposal(run, paths["root"])
    geometry = _read(paths["geometry"], {})
    active_area_ids = {row.get("room_source_id") for row in geometry.get("entities", [])
                       if isinstance(row, dict) and row.get("kind") == "area"
                       and row.get("geometry_status") in {"geometry_confirmed", "ai_estimated"}
                       and row.get("room_source_id")}
    rows = []
    for row in building.get("spaces", []) if isinstance(building, dict) else []:
        if isinstance(row, dict) and row.get("id") and row.get("name"):
            rows.append({"room_id": row["id"], "label": row["name"], "level_name": row.get("level_name", ""),
                         "source_pages": sorted({item.get("page") for item in row.get("evidence", []) if isinstance(item, dict) and isinstance(item.get("page"), int)}),
                         "evidence": deepcopy(row.get("evidence", [])), "source": "building_evidence"})
    for row in room_use.get("records", []) if isinstance(room_use, dict) else []:
        if isinstance(row, dict) and row.get("room_id") and (row.get("original_label") or row.get("label")):
            rows.append({"room_id": row["room_id"], "label": row.get("original_label") or row.get("label"),
                         "level_name": row.get("level_name", ""),
                         "source_pages": sorted({item.get("page") for item in row.get("evidence", []) if isinstance(item, dict) and isinstance(item.get("page"), int)}),
                         "evidence": deepcopy(row.get("evidence", [])), "source": "room_use_resolution"})
    for row in proposal.get("rooms", []) if isinstance(proposal, dict) else []:
        if isinstance(row, dict) and row.get("room_id") and row.get("label"):
            rows.append({"room_id": row["room_id"], "label": row["label"], "level_name": row.get("level_name") or row.get("level", ""),
                         "source_pages": row.get("source_pages", []) or ([row["page"]] if isinstance(row.get("page"), int) else []),
                         "evidence": deepcopy(row.get("evidence", [])), "source": "room_inference_proposal"})
    # A reviewer can mark a detected label as "not a room"; such identities are
    # never offered as trace targets or passed on through the room registry.
    excluded = _not_a_room_identities(room_use)
    reviewer_added = {str(row.get("room_id")) for row in proposal.get("rooms", [])
                      if isinstance(row, dict) and row.get("reviewer_added")}
    unique = {}
    source_priority = {"building_evidence": 0, "room_inference_proposal": 1, "room_use_resolution": 2}
    for row in rows:
        if row.get("room_id") in excluded or _row_identity(row) in excluded:
            continue
        if row.get("room_id") in reviewer_added or _row_identity(row) in reviewer_added:
            row["reviewer_added"] = True
        row["needs_trace"] = row.get("room_id") not in active_area_ids
        identity = _row_identity(row)
        existing = unique.get(identity)
        if existing is None:
            unique[identity] = row
            continue
        # Keep room-use identity and display values where available, while
        # retaining all source evidence from duplicate detector records.
        preferred = row if source_priority.get(row.get("source"), 0) > source_priority.get(existing.get("source"), 0) else existing
        merged = deepcopy(preferred)
        merged["source_pages"] = sorted({page for candidate in (existing, row)
                                         for page in candidate.get("source_pages", [])
                                         if isinstance(page, int)})
        evidence = []
        seen_evidence = set()
        for candidate in (existing, row):
            for item in candidate.get("evidence", []) if isinstance(candidate.get("evidence"), list) else []:
                if not isinstance(item, dict):
                    continue
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                if marker not in seen_evidence:
                    seen_evidence.add(marker)
                    evidence.append(deepcopy(item))
        merged["evidence"] = evidence
        merged["reviewer_added"] = bool(existing.get("reviewer_added") or row.get("reviewer_added"))
        merged["needs_trace"] = bool(existing.get("needs_trace") and row.get("needs_trace"))
        unique[identity] = merged
    return sorted(unique.values(), key=lambda row: (row["label"].casefold(), row["level_name"].casefold(), row["room_id"]))


def _row_identity(row):
    return room_identity(row.get("label", ""), row.get("level_name") or "Unassigned level")


def _not_a_room_identities(room_use):
    return {str(row.get("room_id")) for row in (room_use or {}).get("records", [])
            if isinstance(row, dict) and row.get("room_id") and row.get("space_scope") == "not_a_room"}


def _response(web, project):
    paths = _paths(project)
    artifact = reviewer_room_geometry.validate_artifact(_read(paths["artifact"], reviewer_room_geometry.empty_artifact()))
    pdf_fp = _source_pdf_fingerprint(paths)
    vector_pages = {row.get("page"): row for row in _vector_pages(paths) if isinstance(row, dict)}
    display = deepcopy(artifact)
    for row in display["records"]:
        vector = vector_pages.get(row.get("page"), {})
        source_current = reviewer_room_geometry.trace_is_current(row, pdf_fp, _page_fp(vector))
        room_current = _trace_with_current_room(row, paths) is not None
        current = source_current and room_current
        row["freshness"] = "current" if current else "stale"
        row["stale_reasons"] = [] if current else (["Room is no longer present in the current room registry."] if source_current and not room_current else ["Source PDF or vector page changed since this trace was saved."])
    rooms, pages = _rooms(paths), _page_context(paths, web)
    try:
        from ai.room_use_resolution import load_taxonomy
        uses = {key: row["label"] for key, row in load_taxonomy()["categories"].items() if key != "not_a_room"}
    except ValueError:
        uses = {}
    named_levels = {str(row.get("level_name") or "").strip() for row in [*rooms, *pages]} - {""}
    levels = sorted(named_levels | {"Unassigned level"}, key=lambda value: (value == "Unassigned level", value.casefold()))
    return {"id": project["id"], "reviewer_room_geometry": display, "source_pdf_fingerprint": pdf_fp,
            "rooms": rooms, "pages": pages, "room_uses": uses, "levels": levels,
            "artifact_url": web.safe_link(paths["artifact"]) if paths["artifact"].exists() else ""}


def get(web, project):
    return _response(web, project)


def plan_snap(web, project, page_number):
    paths = _paths(project)
    vector = next((row for row in _vector_pages(paths) if isinstance(row, dict) and row.get("page") == page_number), None)
    page_context = next((row for row in _page_context(paths, web) if row["page"] == page_number), None)
    if vector is None or page_context is None:
        raise ValueError("That page is not a supported geometry plan.")
    lines = []
    for row in vector.get("confirmation_line_candidates", []) or []:
        if not isinstance(row, dict) or not row.get("candidate_id"):
            continue
        start, end = row.get("start_px"), row.get("end_px")
        if not (isinstance(start, list) and len(start) == 2 and isinstance(end, list) and len(end) == 2):
            continue
        lines.append({"line_id": str(row["candidate_id"]), "start_px": start, "end_px": end,
                      "role_hint": row.get("candidate_role_hint", "")})
    endpoints, intersections, truncated = _cached_snap_geometry(
        _page_fp(vector), tuple((row["line_id"], tuple(row["start_px"]), tuple(row["end_px"])) for row in lines),
        tuple(vector.get("plan_viewport", {}).get("bbox_px", [])),
    )
    return {"id": project["id"], "page": page_context,
            "lines": lines, "endpoints": deepcopy(endpoints), "intersections": deepcopy(intersections),
            "intersections_truncated": truncated,
            "snap_tolerance_px": reviewer_room_geometry.SNAP_TOLERANCE_PX,
            "source_pdf_fingerprint": _source_pdf_fingerprint(paths),
            "vector_page_fingerprint": _page_fp(vector)}


@lru_cache(maxsize=32)
def _cached_snap_geometry(vector_fingerprint, line_data, viewport_data):
    lines = [{"line_id": row[0], "start_px": list(row[1]), "end_px": list(row[2])} for row in line_data]
    viewport = list(viewport_data)
    intersections = []
    for left_index, left in enumerate(lines):
        for right in lines[left_index + 1:]:
            point = _intersection(left["start_px"], left["end_px"], right["start_px"], right["end_px"])
            if point is None:
                continue
            if isinstance(viewport, list) and len(viewport) == 4 and not (viewport[0] <= point[0] <= viewport[2] and viewport[1] <= point[1] <= viewport[3]):
                continue
            intersections.append({"point_px": point, "line_ids": [left["line_id"], right["line_id"]]})
            if len(intersections) >= INTERSECTION_CAP:
                break
        if len(intersections) >= INTERSECTION_CAP:
            break
    endpoints = tuple((tuple(point), line["line_id"])
                      for line in lines for point in (line["start_px"], line["end_px"]))
    return ([{"point_px": list(point), "line_id": line_id} for point, line_id in endpoints],
            intersections, len(intersections) >= INTERSECTION_CAP)


def _intersection(a, b, c, d):
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-9:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t, u = (qx * sy - qy * sx) / denominator, (qx * ry - qy * rx) / denominator
    if -1e-8 <= t <= 1 + 1e-8 and -1e-8 <= u <= 1 + 1e-8:
        return [round(a[0] + t * rx, 4), round(a[1] + t * ry, 4)]
    return None


def _point_segment_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 0:
        return math.dist(point, start)
    ratio = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
    return math.dist(point, [start[0] + ratio * dx, start[1] + ratio * dy])


def _validate_snaps(points, snapped, vector_page):
    lines = {str(row.get("candidate_id")): row for row in vector_page.get("confirmation_line_candidates", []) or []
             if isinstance(row, dict) and row.get("candidate_id")}
    for point, line_id in zip(points, snapped):
        if line_id is None:
            continue
        line = lines.get(str(line_id))
        if line is None:
            raise ValueError("A snapped vector line is no longer present on this page.")
        if _point_segment_distance(point, line.get("start_px", []), line.get("end_px", [])) > reviewer_room_geometry.SNAP_TOLERANCE_PX:
            raise ValueError("A snapped room vertex is outside the server snap tolerance for its cited line.")


def _add_reviewer_room(paths, artifact, data):
    from ai.room_use_resolution import load_taxonomy
    label = " ".join(str(data.get("label", "")).split())
    level = " ".join(str(data.get("level_name", "")).split()) or "Unassigned level"
    taxonomy_id = str(data.get("taxonomy_id", "")).strip()
    reviewer = str(data.get("reviewer", "")).strip()
    if not label:
        raise ValueError("Enter a room name.")
    if not reviewer:
        raise ValueError("Enter your name or initials before adding a room.")
    categories = load_taxonomy()["categories"]
    if not taxonomy_id or taxonomy_id not in categories or taxonomy_id == "not_a_room":
        raise ValueError("Choose a room use for the new room.")
    room_id = room_identity(label, level)
    if any(row.get("room_id") == room_id or _row_identity(row) == room_id for row in _rooms(paths)):
        raise ValueError(f"A room called {label} already exists on {level}.")
    page = data.get("page") if isinstance(data.get("page"), int) and data.get("page") > 0 else None
    room = {"room_id": room_id, "label": label, "level_name": level, "taxonomy_id": taxonomy_id,
            "page": page, "reviewer": reviewer, "note": str(data.get("note", "")).strip(),
            "created_at": ai_preliminary.now(), "source": "reviewer_added"}
    return reviewer_room_geometry.validate_artifact({"records": artifact["records"],
                                                     "rooms": [*artifact.get("rooms", []), room]})


def _remove_reviewer_room(artifact, data):
    room_id = str(data.get("room_id", "")).strip()
    if not str(data.get("reviewer", "")).strip():
        raise ValueError("Enter your name or initials before removing a room.")
    rooms = artifact.get("rooms", [])
    if not any(row.get("room_id") == room_id for row in rooms):
        raise ValueError("Only rooms added by a reviewer can be removed here.")
    # The room's traces go with it: a trace without its room is never used.
    return reviewer_room_geometry.validate_artifact({
        "records": [row for row in artifact["records"] if row.get("room_id") != room_id],
        "rooms": [row for row in rooms if row.get("room_id") != room_id]})


def _sync_room_use(web, project, action, data, artifact):
    """Keep room-use in step: a new room gets the reviewer's chosen use."""
    from backend import room_use_resolution_service
    room_use_resolution_service.post(web, project, {"action": "resolve"})
    if action == "add_room":
        room = next(row for row in artifact.get("rooms", []) if row.get("room_id") == room_identity(
            " ".join(str(data.get("label", "")).split()), " ".join(str(data.get("level_name", "")).split()) or "Unassigned level"))
        room_use_resolution_service.post(web, project, {
            "action": "apply_override", "room_id": room["room_id"], "taxonomy_id": room["taxonomy_id"],
            "reviewer": room["reviewer"], "note": "Room use chosen when the reviewer added this room."})


def _known_room(room_id, paths):
    return next((row for row in _rooms(paths) if row.get("room_id") == room_id), None)


def post(web, project, data):
    paths = _paths(project)
    action = str(data.get("action", ""))
    artifact = reviewer_room_geometry.validate_artifact(_read(paths["artifact"], reviewer_room_geometry.empty_artifact()))
    previous_fingerprint = artifact.get("fingerprint", "")
    if action == "delete":
        trace_id = str(data.get("trace_id", ""))
        kept = [row for row in artifact["records"] if row.get("trace_id") != trace_id]
        if len(kept) == len(artifact["records"]):
            raise ValueError("Room geometry trace was not found.")
        artifact = reviewer_room_geometry.validate_artifact({"records": kept, "rooms": artifact.get("rooms", [])})
    elif action == "classify_envelope":
        trace_id = str(data.get("trace_id", "")).strip()
        trace = next((row for row in artifact["records"] if row.get("trace_id") == trace_id), None)
        if not trace:
            raise ValueError("Room geometry trace was not found.")
        current = next((row for row in current_records(paths, artifact) if row.get("trace_id") == trace_id), None)
        if not current:
            raise ValueError("Envelope can only be classified on a current room trace.")
        calibration = current.get("calibration", {})
        if calibration.get("status") not in {"agreed", "declared_scale_rejected"} or not calibration.get("mm_per_px"):
            raise ValueError("Envelope can only be classified on a calibrated room trace.")
        reviewer = str(data.get("reviewer", "")).strip()
        if not reviewer:
            raise ValueError("Enter a reviewer name for the envelope classification.")
        classification = reviewer_room_geometry.validate_envelope_classification(
            data.get("edges"), data.get("roof", "unknown"), len(trace["points_image_px"]) - 1,
        )
        trace.update(classification)
        trace["envelope_reviewer"] = reviewer
        trace["envelope_declared_at"] = ai_preliminary.now()
        artifact = reviewer_room_geometry.validate_artifact({"records": artifact["records"], "rooms": artifact.get("rooms", [])})
    elif action == "add_room":
        artifact = _add_reviewer_room(paths, artifact, data)
    elif action == "remove_room":
        artifact = _remove_reviewer_room(artifact, data)
    elif action == "save":
        room_id, page_number = str(data.get("room_id", "")).strip(), data.get("page")
        room = _known_room(room_id, paths)
        if not room:
            raise ValueError("Room geometry trace references an unknown room.")
        page_context = next((row for row in _page_context(paths, web) if row["page"] == page_number), None)
        vector_page = next((row for row in _vector_pages(paths) if isinstance(row, dict) and row.get("page") == page_number), None)
        if not page_context or not vector_page:
            raise ValueError("Room geometry trace references an unsupported plan page.")
        current_pdf_fp, current_vector_fp = _source_pdf_fingerprint(paths), _page_fp(vector_page)
        if data.get("source_pdf_fingerprint") != current_pdf_fp or data.get("vector_page_fingerprint") != current_vector_fp:
            raise ValueError("The source PDF or vector page changed. Reload the plan before saving the trace.")
        image_width, image_height = page_context.get("image_width_px"), page_context.get("image_height_px")
        if (not page_context.get("preview_matches_vector_coordinates")
                or not isinstance(image_width, (int, float)) or not isinstance(image_height, (int, float))
                or image_width <= 0 or image_height <= 0):
            raise ValueError("A matching full-resolution plan image and image dimensions are required before saving a room trace.")
        points = data.get("points_image_px")
        snapped = data.get("snapped_line_ids")
        # Schema validation provides specific shape, area, and crossing errors.
        if not isinstance(points, list) or not isinstance(snapped, list):
            raise ValueError("Room boundary points and per-vertex snap references are required.")
        if not all(reviewer_room_geometry.valid_point(point) for point in points):
            raise ValueError("Room boundary points must be finite image-pixel coordinate pairs.")
        if len(snapped) != len(points):
            raise ValueError("Each room-boundary vertex needs a snapped line ID or null.")
        _validate_snaps(points, snapped, vector_page)
        if any(point[0] < 0 or point[1] < 0 or point[0] > image_width or point[1] > image_height for point in points):
            raise ValueError("Room boundary vertices must stay inside the rendered page image.")
        dimension_points = data.get("dimension_points_image_px")
        if not isinstance(dimension_points, list) or len(dimension_points) != 2:
            raise ValueError("Choose both ends of one printed dimension before saving."
                             + (" Services plans often have none; " + ARCHITECTURAL_SET_ADVICE[0].lower() + ARCHITECTURAL_SET_ADVICE[1:]
                                if page_context.get("fallback_plan") else ""))
        dimension_value = data.get("dimension_value_mm")
        if not isinstance(dimension_value, (int, float)) or not math.isfinite(dimension_value) or dimension_value <= 0:
            raise ValueError("Enter the printed dimension as a positive value in millimetres.")
        second = None
        second_points, second_value = data.get("second_dimension_points_image_px"), data.get("second_dimension_value_mm")
        if second_points or second_value:
            second = {"points_image_px": second_points, "value_mm": second_value}
        calibration = reviewer_room_geometry.calibration(
            dimension_points, dimension_value, page_context.get("scale_denominator"),
            page_context.get("image_px_per_pt"), second_dimension=second,
        )
        record = {"trace_id": "room_trace_" + fingerprint([room_id, page_number])[:20],
                  "room_id": room_id, "room_label": room["label"], "level_name": room.get("level_name", ""),
                  "page": page_number, "points_image_px": points, "snapped_line_ids": snapped,
                  "calibration": calibration, "reviewer": str(data.get("reviewer", "")).strip(),
                  "note": str(data.get("note", "")).strip(), "status": "geometry_proposed",
                  "created_at": ai_preliminary.now(),
                  "source_fingerprints": {"source_pdf": current_pdf_fp, "vector_page": current_vector_fp}}
        if page_context.get("fallback_plan"):
            # Traced over a services plan because the set has no architectural
            # plan; keep that visible wherever the trace is used.
            record["fallback_plan"] = True
            record["fallback_reason"] = page_context.get("fallback_reason", FALLBACK_TRACE_REASON)
        if not record["reviewer"]:
            raise ValueError("Enter your name or initials as the trace reviewer.")
        new_records = [row for row in artifact["records"] if not (row.get("room_id") == room_id and row.get("page") == page_number)]
        new_records.append(record)
        artifact = reviewer_room_geometry.validate_artifact({"records": new_records, "rooms": artifact.get("rooms", [])})
    else:
        raise ValueError("Room geometry action must be save, delete, classify_envelope, add_room or remove_room.")
    _atomic_json(paths["artifact"], artifact)
    from backend import calculation_extraction_service, productization
    productization.record_change_if_fingerprint_changed(
        paths["root"], action="reviewer_room_geometry_" + action, target=paths["artifact"].name,
        previous_fingerprint=previous_fingerprint, new_fingerprint=artifact.get("fingerprint", ""),
        affected_ids=[data.get("room_id", data.get("trace_id", ""))],
    )
    if action in {"add_room", "remove_room"}:
        _sync_room_use(web, project, action, data, artifact)
    project["reviewer_room_geometry"] = str(paths["artifact"])
    project["updated_at"] = ai_preliminary.now()
    web.update_project(project)
    evidence_result = calculation_extraction_service.post(web, project, {"action": "build"})
    result = _response(web, project)
    result["calculation_input_evidence"] = evidence_result.get("calculation_input_evidence", {})
    result["geometry_resolution"] = evidence_result.get("geometry_resolution", {})
    return result
