"""Multi-page geometry evidence resolution.

This module does not calculate loads or approve thermal inputs.  It turns the
page register and existing geometry artifacts into a deterministic evidence
graph.  Plans provide room geometry witnesses; elevations, schedules, service
pages and 3D views provide role-limited supporting evidence.
"""

from collections import defaultdict
import hashlib
import json
import math


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def stable_witness_id(source_fingerprint, page, drawing_number, label, kind, location=""):
    raw = [source_fingerprint, page, drawing_number, label, kind, location]
    return "witness_" + fingerprint(raw)[:20]


def valid_point(point):
    return isinstance(point, (list, tuple)) and len(point) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)


def polygon_area(points):
    """Return positive area for a closed polygon; otherwise return None."""
    if not isinstance(points, list) or len(points) < 4:
        return None
    if not all(valid_point(point) for point in points):
        return None
    if points[0] != points[-1]:
        return None
    area = sum(points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1] for i in range(len(points) - 1)) / 2.0
    return abs(area) if area > 0 else None


def segment_intersects(a, b, c, d):
    def orient(p, q, r):
        value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (value > 1e-9) - (value < -1e-9)

    def on_segment(p, q, r):
        return min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9 and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on_segment(a, c, b)) or (o2 == 0 and on_segment(a, d, b)) or (o3 == 0 and on_segment(c, a, d)) or (o4 == 0 and on_segment(c, b, d))


def polygon_is_simple(points):
    if not isinstance(points, list) or len(points) < 4 or points[0] != points[-1]:
        return False
    edges = list(zip(points, points[1:]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j <= i or j in {i - 1, i + 1} or (i == 0 and j == len(edges) - 1):
                continue
            if segment_intersects(a, b, c, d):
                return False
    return True


def geometry_status_for_room(room):
    geometry = room.get("geometry") or room.get("geometry_reference")
    if room.get("geometry_status") == "geometry_confirmed":
        return "geometry_confirmed"
    if isinstance(geometry, dict) and polygon_area(geometry.get("points") or geometry.get("polygon")) and geometry.get("scale_witnesses", 0) >= 2:
        return "geometry_proposed"
    return room.get("geometry_status") or "label_detected"


def build_geometry_resolution(ai_input, coverage=None, building=None, spatial_ocr=None, vector_geometry=None,
                              dimension_matches=None, geometry_confirmation=None, vision_response=None):
    """Create page capabilities, witnesses, relationships and review issues."""
    coverage = coverage or {}
    building = building or {}
    pages = list(coverage.get("page_roles", []))
    source_fp = coverage.get("source_fingerprint", fingerprint(ai_input))
    page_by_number = {row.get("page"): row for row in pages}
    witnesses = []
    relationships = []
    conflicts = []
    review_items = []

    def add_witness(page_number, label, kind, evidence, method, confidence="unknown", location="", role=None):
        page = page_by_number.get(page_number, {})
        witness = {
            "witness_id": stable_witness_id(source_fp, page_number, page.get("drawing_number", ""), label, kind, location),
            "page": page_number,
            "drawing_number": page.get("drawing_number", ""),
            "role": role or page.get("proposed_role", ""),
            "kind": kind,
            "label": label,
            "evidence": evidence,
            "extraction_method": method,
            "confidence": confidence,
            "location": location,
            "independent_group": page.get("page_group", ""),
        }
        if witness["witness_id"] not in {row["witness_id"] for row in witnesses}:
            witnesses.append(witness)
        return witness

    # Every page is a witness candidate, even when it cannot provide primary
    # geometry.  This prevents useful 3D/elevation/schedule evidence being
    # discarded by downstream consumers.
    for page in pages:
        caps = page.get("capabilities", [])
        add_witness(page.get("page"), page.get("title", ""), "page_capability", caps,
                    "page_role_classification", page.get("confidence", "unknown"), role=page.get("proposed_role", ""))

    for room in building.get("spaces", []):
        evidence = room.get("evidence", [])
        for item in evidence:
            page = item.get("page")
            witness = add_witness(page, room.get("name", ""), "room", item.get("excerpt", ""), room.get("extraction_method", "structured_pdf"), room.get("confidence", "unknown"), item.get("bbox_px", ""), page_by_number.get(page, {}).get("proposed_role"))
            relationships.append({"relationship_id": "room_witness_" + fingerprint([room.get("id"), witness["witness_id"]])[:16], "kind": "room_to_page_witness", "entity_id": room.get("id", ""), "witness_ids": [witness["witness_id"]], "status": "proposed"})
        status = geometry_status_for_room(room)
        if status != "geometry_confirmed":
            review_items.append({
                "item_id": "geometry_issue_" + fingerprint([room.get("id"), "geometry"])[:16],
                "affected_id": room.get("id", ""), "status": "blocked", "field": "geometry",
                "source_artifact": "building_evidence.json", "page": (evidence[0].get("page") if evidence else None),
                "reason": "Room identity is present, but no closed, calibrated, independently witnessed boundary is available.",
                "remediation": "Link the room label to a closed plan boundary and a second independent witness; otherwise keep the room excluded.",
            })

    # Vector pages are referenced by witness IDs, but raw vectors remain neutral
    # evidence; only geometry-confirmation output can promote them.
    for page in (vector_geometry or {}).get("geometry_key_points", {}).get("pages", []):
        number = page.get("page")
        role = page_by_number.get(number, {}).get("proposed_role", page.get("plan_role", ""))
        for candidate in page.get("dimension_candidates", [])[:300]:
            add_witness(number, candidate.get("text_seen", ""), "dimension", candidate, "pdf_vector_dimension", candidate.get("confidence", "unknown"), candidate.get("bbox_px", ""), role)
        for candidate in page.get("line_candidates", [])[:300]:
            add_witness(number, candidate.get("candidate_id", ""), "vector_geometry", candidate, "pdf_vector_geometry", candidate.get("confidence", "unknown"), candidate.get("bbox_px", ""), role)

    for page in (dimension_matches or {}).get("pages", []):
        number = page.get("page")
        role = page_by_number.get(number, {}).get("proposed_role", page.get("plan_role", ""))
        for span in page.get("dimension_span_candidates", [])[:300]:
            witness = add_witness(number, span.get("text_seen", ""), "dimension_span", span, "dimension_wall_matcher", span.get("confidence", "unknown"), span.get("bbox_px", ""), role)
            relationships.append({"relationship_id": "dimension_witness_" + fingerprint([number, span.get("dimension_candidate_id"), witness["witness_id"]])[:16], "kind": "dimension_span_witness", "witness_ids": [witness["witness_id"]], "status": "proposed", "value_mm": span.get("value_mm")})

    # Elevation/opening witnesses are linked by explicit tag or a unique
    # dimensioned opening record.  Ambiguity stays a conflict.
    openings_by_tag = defaultdict(list)
    for opening in building.get("openings", []):
        tag = str(opening.get("tag", "")).upper().strip()
        if tag:
            openings_by_tag[tag].append(opening)
    for opening in building.get("openings", []):
        evidence = opening.get("evidence", [])
        page = evidence[0].get("page") if evidence else None
        role = page_by_number.get(page, {}).get("proposed_role", "")
        if role != "opening_elevation":
            continue
        tag = str(opening.get("tag", "")).upper().strip()
        candidates = openings_by_tag.get(tag, []) if tag else []
        if len(candidates) == 1 and candidates[0] is not opening:
            relationships.append({"relationship_id": "opening_witness_" + fingerprint([opening.get("id"), candidates[0].get("id")])[:16], "kind": "opening_plan_elevation", "entity_ids": [opening.get("id"), candidates[0].get("id")], "pages": sorted({row.get("page") for row in evidence + candidates[0].get("evidence", []) if row.get("page") is not None}), "basis": "unique_explicit_opening_tag", "status": "matched"})
        elif tag:
            conflicts.append({"conflict_id": "geometry_conflict_" + fingerprint(["opening", opening.get("id"), tag, len(candidates)])[:16], "kind": "opening_mapping_ambiguous" if candidates else "opening_plan_mapping_missing", "entity_ids": [opening.get("id")] + [row.get("id") for row in candidates], "pages": [page], "reason": "Elevation opening cannot be uniquely linked to a plan opening.", "status": "review_required"})

    # 3D pages create explicit cross-check links but never primary dimensions.
    render_pages = [row for row in pages if row.get("proposed_role") == "3d_render"]
    for page in render_pages:
        relationships.append({"relationship_id": "3d_crosscheck_" + fingerprint([page.get("page"), page.get("page_group")])[:16], "kind": "3d_visual_crosscheck", "from_page": page.get("page"), "status": "proposed", "basis": "visual_presence_or_level_relationship", "primary_dimension_source": False})

    # Preserve geometry-confirmation outputs as independent witness metadata.
    for page in (geometry_confirmation or {}).get("pages", []):
        number = page.get("page")
        for wall in page.get("wall_confirmations", []):
            if wall.get("status") in {"vector_confirmed", "scale_calibrated", "cad_ready_candidate"}:
                witness = add_witness(number, wall.get("wall_id", ""), "confirmed_wall", wall, "geometry_confirmation", wall.get("confidence", "unknown"), role=page_by_number.get(number, {}).get("proposed_role"))
                relationships.append({"relationship_id": "wall_witness_" + fingerprint([wall.get("wall_id"), witness["witness_id"]])[:16], "kind": "wall_vector_confirmation", "witness_ids": [witness["witness_id"]], "status": "confirmed"})

    return {
        "schema_version": 1,
        "source_fingerprint": source_fp,
        "witnesses": sorted(witnesses, key=lambda row: row["witness_id"]),
        "relationships": sorted(relationships, key=lambda row: row["relationship_id"]),
        "conflicts": sorted(conflicts, key=lambda row: row["conflict_id"]),
        "review_items": sorted(review_items, key=lambda row: row["item_id"]),
        "summary": {
            "page_count": len(pages),
            "witness_count": len(witnesses),
            "relationship_count": len(relationships),
            "conflict_count": len(conflicts),
            "review_item_count": len(review_items),
            "three_d_crosscheck_pages": [row.get("page") for row in render_pages],
        },
    }
