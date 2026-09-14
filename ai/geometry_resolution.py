"""Multi-page geometry evidence resolution.

This module does not calculate loads or approve thermal inputs.  It turns the
page register and existing geometry artifacts into a deterministic evidence
graph.  Plans provide room geometry witnesses; elevations, schedules, service
pages and 3D views provide role-limited supporting evidence.
"""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import re


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def normalise_label(value):
    """Normalise labels for matching without changing their display text."""
    return " ".join(str(value or "").casefold().split())


def stable_entity_id(source_fingerprint, page, drawing_number, level, label, kind, location=""):
    return "geometry_" + fingerprint([source_fingerprint, page, drawing_number, level, label, kind, location])[:24]


def _page_identity(page):
    identity = page.get("identity") or {}
    return identity.get("selected_drawing_number") or page.get("drawing_number", "")


def _evidence_fingerprint(ai_input, coverage, spatial_ocr, vector_geometry, dimension_matches,
                           geometry_confirmation, vision_response, building):
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            rows = [canonical(item) for item in value]
            return sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return value

    return fingerprint({
        "ai_input": canonical(ai_input or {}), "coverage": canonical(coverage or {}),
        "spatial_ocr": canonical(spatial_ocr or {}), "vector_geometry": canonical(vector_geometry or {}),
        "dimension_matches": canonical(dimension_matches or {}),
        "geometry_confirmation": canonical(geometry_confirmation or {}),
        "vision_response": canonical(vision_response or {}), "building": canonical(building or {}),
    })


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
    evidence_fp = _evidence_fingerprint(ai_input, coverage, spatial_ocr, vector_geometry,
                                         dimension_matches, geometry_confirmation, vision_response, building)
    page_by_number = {row.get("page"): row for row in pages}
    witnesses = []
    relationships = []
    conflicts = []
    review_items = []
    entities = []

    def add_entity(page_number, label, kind, value, status, method, confidence="unknown",
                   location="", unresolved=None, witness_ids=None, level=""):
        page = page_by_number.get(page_number, {})
        entity = {
            "entity_id": stable_entity_id(evidence_fp, page_number, _page_identity(page), level,
                                           label, kind, location),
            "kind": kind, "label": label, "value": value,
            "geometry_status": status, "source": {
                "page": page_number, "drawing_number": _page_identity(page),
                "kind": "architect_pdf" if page_number is not None else "derived",
            },
            "extraction_method": method, "confidence": confidence,
            "level_candidate": level, "witness_ids": sorted(set(str(item) for item in (witness_ids or []) if item)),
            "unresolved_fields": sorted(set(unresolved or [])),
        }
        entities.append(entity)
        return entity

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

    # Preserve the complete page identity/capability contract in the geometry
    # artifact. This is metadata, not calculator eligibility.
    page_register = []
    for page in pages:
        page_register.append({
            "page": page.get("page"),
            "drawing_number": _page_identity(page),
            "drawing_number_candidates": (page.get("identity") or {}).get("drawing_number_candidates", []),
            "title": page.get("title", ""),
            "title_candidates": (page.get("identity") or {}).get("title_candidates", []),
            "identity_status": (page.get("identity") or {}).get("status", page.get("drawing_number_status", "missing")),
            "proposed_role": page.get("proposed_role", ""),
            "capabilities": page.get("capabilities", []),
            "capability_map": page.get("capability_map", {}),
            "relevance": page.get("relevance", {}),
            "selection": page.get("selection", "reference_only"),
            "level_candidate": page.get("level_name", ""),
            "scale_candidates": page.get("scale_candidates", []),
            "text_available": bool(page.get("text_available", True)),
            "ocr_available": bool(page.get("ocr_available", False)),
            "vector_available": bool(page.get("vector_available", False)),
            "source_fingerprint": source_fp,
        })

    room_entities = []
    for level in building.get("levels", []):
        evidence = level.get("evidence") or []
        page = next((item.get("page") for item in evidence if isinstance(item, dict) and item.get("page") is not None), None)
        entity = add_entity(page, level.get("name", ""), "floor", deepcopy(level),
                            "geometry_proposed" if level.get("name") else "geometry_review_required",
                            level.get("extraction_method", "structured_pdf"), level.get("confidence", "unknown"),
                            witness_ids=[item.get("page") for item in evidence if isinstance(item, dict)],
                            unresolved=[] if evidence else ["floor_identity"])

    for room in building.get("spaces", []):
        evidence = room.get("evidence", [])
        level = room.get("level_name", "")
        room_status = geometry_status_for_room(room)
        room_entity = add_entity(
            next((item.get("page") for item in evidence if isinstance(item, dict) and item.get("page") is not None), None),
            room.get("name", ""), "room", deepcopy(room), room_status,
            room.get("extraction_method", "structured_pdf"), room.get("confidence", "unknown"),
            witness_ids=[item.get("page") for item in evidence if isinstance(item, dict)],
            unresolved=room.get("unresolved_fields", []), level=level,
        )
        room_entities.append(room_entity)
        for item in evidence:
            page = item.get("page")
            witness = add_witness(page, room.get("name", ""), "room", item.get("excerpt", ""), room.get("extraction_method", "structured_pdf"), room.get("confidence", "unknown"), item.get("bbox_px", ""), page_by_number.get(page, {}).get("proposed_role"))
            relationships.append({"relationship_id": "room_witness_" + fingerprint([room.get("id"), witness["witness_id"]])[:16], "kind": "room_to_page_witness", "entity_id": room.get("id", ""), "witness_ids": [witness["witness_id"]], "status": "proposed"})
        status = room_status
        # An explicit, uniquely allocated area is eligible as an evidence
        # value even when the room boundary itself is not confirmed. It is
        # never inferred from another room or from visual proportions.
        raw_area = room.get("area_m2", room.get("area"))
        try:
            numeric_area = float(raw_area)
        except (TypeError, ValueError):
            numeric_area = 0
        if numeric_area > 0 and evidence:
            add_entity(
                room_entity["source"]["page"], room.get("name", ""), "area",
                {"area_m2": numeric_area, "room_entity_id": room_entity["entity_id"]},
                "geometry_confirmed", "explicit_room_area", room.get("confidence", "unknown"),
                witness_ids=[room_entity["entity_id"]], level=level,
            )
        if status != "geometry_confirmed":
            review_items.append({
                "item_id": "geometry_issue_" + fingerprint([room.get("id"), "geometry"])[:16],
                "affected_id": room.get("id", ""), "status": "blocked", "field": "geometry",
                "source_artifact": "building_evidence.json", "page": (evidence[0].get("page") if evidence else None),
                "reason": "Room identity is present, but no closed, calibrated, independently witnessed boundary is available.",
                "remediation": "Link the room label to a closed plan boundary and a second independent witness; otherwise keep the room excluded.",
            })

    # Level-aware duplicate detection. Same labels on different levels are
    # distinct; competing boundaries on the same level are conflicts.
    room_groups = defaultdict(list)
    for entity in room_entities:
        room_groups[(normalise_label(entity.get("label")), normalise_label(entity.get("level_candidate")))].append(entity)
    for (label, level), matches in room_groups.items():
        if label and len(matches) > 1:
            conflict_id = "geometry_conflict_" + fingerprint(["room", label, level, [row["entity_id"] for row in matches]])[:16]
            conflicts.append({
                "conflict_id": conflict_id, "kind": "same_level_duplicate_room",
                "entity_ids": [row["entity_id"] for row in matches],
                "pages": sorted({row["source"].get("page") for row in matches if row["source"].get("page") is not None}),
                "status": "review_required",
                "reason": "The same room label has competing geometry records on the same level.",
            })
            review_items.append({
                "item_id": conflict_id, "affected_id": label, "status": "blocked",
                "field": "room_identity", "source_artifact": "geometry_resolution",
                "reason": "Same-level room identity is ambiguous; select the correct boundary.",
                "remediation": "Choose one compatible boundary or rename the room records before activation.",
            })
    # Link the same room label across plan/finish/ceiling pages. A shared label
    # is a relationship witness, not proof of geometry.
    for left in room_entities:
        for right in room_entities:
            if left["entity_id"] >= right["entity_id"]:
                continue
            if normalise_label(left.get("label")) and normalise_label(left.get("label")) == normalise_label(right.get("label")) and normalise_label(left.get("level_candidate")) == normalise_label(right.get("level_candidate")):
                relationships.append({
                    "relationship_id": "room_cross_page_" + fingerprint([left["entity_id"], right["entity_id"]])[:16],
                    "kind": "room_cross_page_label_match", "entity_ids": [left["entity_id"], right["entity_id"]],
                    "pages": sorted({left["source"].get("page"), right["source"].get("page")}),
                    "basis": "exact_normalized_room_label_and_level", "status": "proposed",
                    "independent_witness": False, "cross_check_only": False,
                })

    # Vector pages are referenced by witness IDs, but raw vectors remain neutral
    # evidence; only geometry-confirmation output can promote them.
    for page in (vector_geometry or {}).get("geometry_key_points", {}).get("pages", []):
        number = page.get("page")
        role = page_by_number.get(number, {}).get("proposed_role", page.get("plan_role", ""))
        for candidate in page.get("dimension_candidates", [])[:300]:
            witness = add_witness(number, candidate.get("text_seen", ""), "dimension", candidate, "pdf_vector_dimension", candidate.get("confidence", "unknown"), candidate.get("bbox_px", ""), role)
            raw_value = candidate.get("value_mm")
            try:
                valid_dimension = float(raw_value) > 0
            except (TypeError, ValueError):
                valid_dimension = False
            add_entity(number, candidate.get("text_seen", ""), "dimension", deepcopy(candidate),
                       "geometry_proposed" if valid_dimension else "geometry_review_required",
                       "pdf_vector_dimension", candidate.get("confidence", "unknown"),
                       location=candidate.get("bbox_px", ""), witness_ids=[witness["witness_id"]],
                       unresolved=[] if valid_dimension else ["dimension_value"])
        for candidate in page.get("line_candidates", [])[:300]:
            witness = add_witness(number, candidate.get("candidate_id", ""), "vector_geometry", candidate, "pdf_vector_geometry", candidate.get("confidence", "unknown"), candidate.get("bbox_px", ""), role)
            points = [candidate.get("start_px"), candidate.get("end_px")]
            valid_line = all(valid_point(point) for point in points)
            add_entity(number, candidate.get("candidate_id", ""), "wall", deepcopy(candidate),
                       "geometry_proposed" if valid_line else "geometry_review_required",
                       "pdf_vector_geometry", candidate.get("confidence", "unknown"),
                       location=candidate.get("bbox_px", ""), witness_ids=[witness["witness_id"]],
                       unresolved=[] if valid_line else ["line_endpoints"])

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

    # Explicit dimension-to-wall links produced by the existing matcher are
    # preserved as generic relationships. Raw proximity-only candidates remain
    # proposed and never promote a room boundary.
    for page in (dimension_matches or {}).get("pages", []):
        number = page.get("page")
        for link in page.get("dimension_wall_links", []) or []:
            dimension_id = link.get("dimension_candidate_id") or link.get("dimension_id")
            wall_id = link.get("target_wall_id") or link.get("wall_id")
            if not dimension_id or not wall_id:
                continue
            relationships.append({
                "relationship_id": "dimension_wall_" + fingerprint([source_fp, number, dimension_id, wall_id])[:16],
                "kind": "dimension_to_wall", "page": number,
                "dimension_id": dimension_id, "wall_id": wall_id,
                "value_mm": link.get("value_mm"),
                "basis": link.get("match_basis") or link.get("basis") or "explicit_dimension_wall_match",
                "status": "matched" if link.get("status") in {None, "matched", "confirmed"} else link.get("status"),
                "independent_witness": True, "cross_check_only": False,
            })

    # A page with no scale can still contribute labels and dimensions, but it
    # cannot produce a derived area or confirmed boundary.
    for page in page_register:
        if page.get("proposed_role") in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan"} and not page.get("scale_candidates"):
            review_items.append({
                "item_id": "geometry_issue_" + fingerprint(["scale", page.get("page")])[:16],
                "affected_id": page.get("page"), "status": "blocked", "field": "scale",
                "source_artifact": "geometry_resolution", "page": page.get("page"),
                "reason": "Plan geometry is available, but a proven scale/calibration witness is missing.",
                "remediation": "Provide a printed scale or calibration witness before deriving room geometry or area.",
            })

    relationships = sorted({row["relationship_id"]: row for row in relationships}.values(), key=lambda row: row["relationship_id"])
    conflicts = sorted({row["conflict_id"]: row for row in conflicts}.values(), key=lambda row: row["conflict_id"])
    review_items = sorted({row["item_id"]: row for row in review_items}.values(), key=lambda row: row["item_id"])
    return {
        "schema_version": 1,
        "source_fingerprint": source_fp,
        "evidence_fingerprint": evidence_fp,
        "pages": sorted(page_register, key=lambda row: (row.get("page") is None, row.get("page"))),
        "entities": sorted(entities, key=lambda row: row["entity_id"]),
        "witnesses": sorted(witnesses, key=lambda row: row["witness_id"]),
        "relationships": relationships,
        "conflicts": conflicts,
        "review_items": review_items,
        "status": "blocked" if conflicts or any(item.get("status") == "blocked" for item in review_items) else "review_required",
        "summary": {
            "page_count": len(page_register),
            "entity_count": len(entities),
            "witness_count": len(witnesses),
            "relationship_count": len(relationships),
            "conflict_count": len(conflicts),
            "review_item_count": len(review_items),
            "three_d_crosscheck_pages": [row.get("page") for row in render_pages],
        },
    }
