"""Versioned evidence review and additive calculator changes. No new physics."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from ai.site_design_conditions import validate_citations
from ai.hourly_loads import (
    DAY_TYPES, empty_hourly_load_model, empty_schedule_library,
    validate_hourly_load_model, validate_schedule_library,
)
from ai.envelope import (
    empty_envelope_library, empty_envelope_model, validate_envelope_library,
    validate_envelope_model, CONSTRUCTION_KINDS,
)
from ai.drawing_coverage import has_current_level_classification
from ai.room_use_resolution import room_identity

DECISIONS = {"accept", "edit", "reject", "needs_evidence", "pending"}
GROUPS = ("floors", "zones", "rooms", "room_inputs", "schedules", "envelope")
AREA_ROUNDING_TOLERANCE_M2 = 0.000001
AREA_AGREEMENT_RELATIVE_TOLERANCE = 0.02


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class DraftConflict(ValueError):
    def __init__(self, message, code="revision_conflict"):
        super().__init__(message)
        self.code = code


def empty_calculator_draft():
    return {"schema_version": 2, "revision": 0, "status": "not_built", "updated_at": "",
            "candidates": {group: [] for group in GROUPS}, "review_items": [],
            "source_artifacts": {}, "source_fingerprints": {}, "decisions": {},
            "review_history": [], "application_receipts": [], "page_roles": [],
            "evidence_summary": {}, "evidence_fusion": {}, "geometry_review": {},
            "readiness": {"status": "blocked", "issues": []}}


def all_candidates(draft):
    return [item for group in GROUPS for item in draft.get("candidates", {}).get(group, [])]


def geometry_review_summary(draft):
    """Return the compact, frontend-facing topology review read model.

    Candidate records remain authoritative; this projection only groups their
    evidence so the UI can show the useful page context without interpreting
    geometry a second time.
    """
    rooms = draft.get("candidates", {}).get("rooms", [])
    inputs = draft.get("candidates", {}).get("room_inputs", [])
    pages = draft.get("page_roles", [])
    groups = {}
    for page in pages:
        key = page.get("page_group") or page.get("proposed_role") or "unassigned"
        groups[key] = groups.get(key, 0) + 1
    room_rows = []
    for room in rooms:
        value = room.get("value", {})
        room_id = value.get("room_id")
        area = next((row for row in inputs if row.get("kind") == "area" and row.get("value", {}).get("room_id") == room_id), None)
        ceiling = next((row for row in inputs if row.get("kind") == "ceiling" and row.get("value", {}).get("room_id") == room_id), None)
        room_rows.append({
            "candidate_id": room.get("candidate_id"),
            "room_id": room_id,
            "name": value.get("name", ""),
            "floor_id": value.get("floor_id", ""),
            "zone_id": value.get("zone_id", ""),
            "geometry_status": value.get("geometry_status", "label_detected"),
            "geometry_reference": value.get("geometry_reference"),
            "area_m2": area.get("value", {}).get("area_m2") if area else value.get("area_m2"),
            "ceiling_height_mm": ceiling.get("value", {}).get("ceiling_height_mm") if ceiling else None,
            "unresolved_fields": value.get("unresolved_fields", []),
            "citations": room.get("citations", []),
            "confidence": room.get("confidence", "unknown"),
            "decision": draft.get("decisions", {}).get(room.get("candidate_id"), {}).get("decision", "pending"),
        })
    return {"page_groups": groups, "rooms": room_rows,
            "floor_candidate_count": len(draft.get("candidates", {}).get("floors", [])),
            "zone_candidate_count": len(draft.get("candidates", {}).get("zones", [])),
            "architect_page_count": len(pages)}


def citations(evidence, document):
    rows = [{"reference": item.get("reference") or document,
             "page": item.get("page"), "excerpt": item.get("excerpt", "")}
            for item in evidence or [] if isinstance(item, dict)]
    checked = validate_citations(rows, "Proposal") if rows else []
    return sorted(checked, key=lambda row: (str(row.get("reference", "")), row.get("page") or 0, row.get("excerpt", "")))


def supported_citations(rows):
    return bool(rows) and all(row.get("reference") and row.get("excerpt") for row in rows)


def quantity(raw, unit):
    """Accept explicit units; never extract a number from an arbitrary label."""
    if isinstance(raw, dict) and raw.get("unit") == unit:
        value = raw.get("value")
    elif isinstance(raw, str):
        patterns = {"m2": r"m(?:2|²)", "mm": "mm", "W": "W"}
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*" + patterns[unit] + r"\s*", raw, re.I)
        value = float(match[1]) if match else None
    else:
        value = None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return value


def displayed_area_rounding_tolerance(raw, area_m2, evidence=()):
    """Half of the last displayed area digit, with numeric precision fallback."""
    raw_value = raw.get("value") if isinstance(raw, dict) else raw
    candidates = [str(raw_value)] if raw_value is not None else []
    candidates.extend(str(row.get("excerpt", "")) for row in evidence if isinstance(row, dict))
    for text in candidates:
        for match in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*m(?:2|²))?", text, re.I):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if abs(value - area_m2) > AREA_ROUNDING_TOLERANCE_M2:
                continue
            decimals = len(match.group(1).partition(".")[2])
            return 0.5 * (10 ** -decimals)
    return 0.5


def trace_areas_agree(areas):
    if len(areas) < 2:
        return True
    low, high = min(areas), max(areas)
    relative_difference = (high - low) / ((high + low) / 2.0)
    return relative_difference <= AREA_AGREEMENT_RELATIVE_TOLERANCE


def build_calculator_draft(thermal_model, building_evidence, drawing_coverage, previous=None,
                           source_artifacts=None, thermal_evidence=None, evidence_fusion=None,
                           calculation_input_evidence=None, room_registry=None):
    previous = previous or {}
    if not has_current_level_classification(drawing_coverage):
        raise ValueError("Drawing-level classification changed. Rebuild drawing coverage and building evidence before building the calculator draft.")
    thermal_evidence = thermal_evidence or {}
    draft = empty_calculator_draft()
    source_fingerprints = {
        "thermal_model": thermal_model, "building_evidence": building_evidence,
        "drawing_coverage": drawing_coverage, "thermal_evidence": thermal_evidence,
        "evidence_fusion": evidence_fusion or {},
    }
    if calculation_input_evidence is not None:
        source_fingerprints["calculation_input_evidence"] = calculation_input_evidence
    if room_registry is not None:
        source_fingerprints["room_registry"] = {key: value for key, value in room_registry.items()
                                                  if key != "source_artifact_fingerprints"}
    draft.update(revision=previous.get("revision", 0) + 1, status="review_required", updated_at=timestamp(),
                 source_artifacts=source_artifacts or {},
                 source_fingerprints={name: fingerprint(value) for name, value in source_fingerprints.items()},
                 review_history=deepcopy(previous.get("review_history", [])),
                 application_receipts=deepcopy(previous.get("application_receipts", [])),
                 page_roles=deepcopy((evidence_fusion or {}).get("pages") or drawing_coverage.get("page_roles", [])),
                 evidence_fusion={"schema_version": (evidence_fusion or {}).get("schema_version"), "fingerprint": (evidence_fusion or {}).get("fingerprint", ""),
                                  "facts": deepcopy((evidence_fusion or {}).get("facts", [])),
                                  "relationships": deepcopy((evidence_fusion or {}).get("relationships", [])),
                                  "conflicts": deepcopy((evidence_fusion or {}).get("conflicts", [])),
                                  "geometry_resolution": deepcopy((evidence_fusion or {}).get("geometry_resolution", {})),
                                  "fact_registry": deepcopy((evidence_fusion or {}).get("fact_registry", {}))},
                 evidence_summary={key: len(building_evidence.get(key, [])) for key in
                                   ("spaces", "levels", "surfaces", "openings", "constructions", "lighting", "equipment")})
    draft["source_fingerprints"].update((room_registry or {}).get("source_artifact_fingerprints", {}))
    document = building_evidence.get("source_pdf") or thermal_model.get("source_pdf") or "building_evidence.json"

    def add(group, kind, identity, value, evidence, reason, evidence_ids=(), dependencies=(), confidence="unknown"):
        candidate_id = kind + "_" + fingerprint([document, identity])[:16]
        row = {"candidate_id": candidate_id, "kind": kind, "value": value,
               "source": document, "citations": citations(evidence, document),
               "evidence_ids": sorted(str(i) for i in evidence_ids if i), "confidence": confidence,
               "dependencies": list(dependencies), "proposal_status": "pending_review", "reason": reason,
               "target_artifact": "hourly_load_model" if group in GROUPS[:4] else
                   "schedule_library" if group == "schedules" else
                   "envelope_model" if kind == "surface" else "envelope_library"}
        row["fingerprint"] = fingerprint(row)
        draft["candidates"][group].append(row)
        return row

    def issue(identity, reason, evidence=(), affected_id="project"):
        item = {"item_id": "issue_" + fingerprint([document, identity])[:16],
            "scope": "room" if affected_id != "project" else "project", "affected_id": affected_id,
            "reason": reason, "status": "needs_evidence", "source": document,
            "citations": citations(evidence, document), "evidence_ids": [], "confidence": "unknown",
            "effect": "blocks_room" if affected_id != "project" else "blocks_project",
            "remediation": "Provide source-backed evidence or make an explicit engineer review decision."}
        draft["review_items"].append(item)

    # Preserve source-level exceptions as actionable review items. They are
    # evidence findings, not approvals or calculated inputs.
    seen_source_issues = set()
    for source_issue in list(building_evidence.get("exceptions", [])) + list(drawing_coverage.get("coverage_exceptions", [])):
        if isinstance(source_issue, dict):
            item = deepcopy(source_issue)
            item.setdefault("item_id", "source_issue_" + fingerprint(item)[:16])
            issue_key = item.get("item_id") or fingerprint(item)
            if issue_key in seen_source_issues:
                continue
            seen_source_issues.add(issue_key)
            item.setdefault("scope", "project")
            item.setdefault("affected_id", item.get("level_name", "project"))
            item.setdefault("reason", item.get("question", "Source evidence requires review."))
            item.setdefault("status", "needs_evidence")
            item.setdefault("source", document)
            item.setdefault("citations", [])
            item.setdefault("effect", "blocks_project")
            item.setdefault("remediation", "Review the cited source and provide the missing relationship or value.")
            draft["review_items"].append(item)

    # Fusion findings retain page/entity provenance and remain review-only;
    # they are never interpreted as calculator facts here.
    for fusion_issue in (evidence_fusion or {}).get("review_items", []):
        item = deepcopy(fusion_issue)
        item.setdefault("scope", "project")
        item.setdefault("source", "architect_evidence_fusion.json")
        item.setdefault("citations", [])
        item.setdefault("effect", "blocks_project")
        draft["review_items"].append(item)

    floors = {}
    # Vision/evidence fusion may identify a real level before the legacy
    # coverage register has been explicitly reviewed.  Keep that level as a
    # proposal so room candidates can depend on it, but do not treat it as an
    # authored or calculation-ready floor.  The coverage register remains the
    # preferred source when both artifacts contain the same level.
    coverage_levels = list(drawing_coverage.get("levels", []))
    coverage_names = {
        str(level.get("level_name", "")).strip().casefold()
        for level in coverage_levels
        if str(level.get("level_name", "")).strip()
    }
    for level in building_evidence.get("levels", []):
        name = str(level.get("name", level.get("level_name", ""))).strip()
        if not name or name.casefold() in coverage_names:
            continue
        evidence = level.get("evidence", [])
        if not evidence:
            continue
        coverage_levels.append({
            "level_name": name,
            "proposed_purpose": level.get("proposed_purpose", ""),
            "purpose_status": level.get("status", "missing"),
            "purpose_evidence": evidence,
            "conditioned_status": level.get("conditioned_status", "unknown"),
            "page_numbers": sorted({item.get("page") for item in evidence if item.get("page")}),
        })
    all_level_names = [str(level.get("level_name", "")).strip()
                       for level in coverage_levels if isinstance(level, dict)]
    all_level_names.extend(str(level.get("name", level.get("level_name", ""))).strip()
                           for level in building_evidence.get("levels", []) if isinstance(level, dict))
    all_level_names.extend(str(space.get("level_name", "")).strip()
                           for space in building_evidence.get("spaces", []) if isinstance(space, dict))
    registry_rooms_for_levels = [row for row in (room_registry or {}).get("rooms", []) if isinstance(row, dict)]
    all_level_names.extend(str(row.get("level_name", "")).strip() for row in registry_rooms_for_levels)
    named_levels_exist = any(name and not name.casefold().startswith("unassigned") for name in all_level_names)
    unassigned_only = bool(all_level_names) and not named_levels_exist
    assumed_floor = None
    if unassigned_only:
        evidence = []
        for level in coverage_levels:
            evidence.extend({"page": page, "excerpt": "No building level stated on this plan."}
                            for page in level.get("page_numbers", []) if page)
        for space in building_evidence.get("spaces", []):
            evidence.extend(space.get("evidence", []))
        for room in registry_rooms_for_levels:
            evidence.extend(room.get("evidence", []))
        seen = set()
        evidence = [row for row in evidence if isinstance(row, dict)
                    and not (fingerprint(row) in seen or seen.add(fingerprint(row)))]
        floor_id = "floor_" + fingerprint([document, "assumed_single_level"])[:12]
        assumed_floor = add("floors", "floor", ["assumed_single_level"],
            {"floor_id": floor_id, "name": "Single level (assumed — no level stated in drawings)", "elevation_m": None},
            evidence, "No building level is stated; confirm this single-level assumption.", confidence="assumed")
        floors["unassigned level"] = assumed_floor
        floors[""] = assumed_floor
    for level in sorted(coverage_levels, key=lambda r: r.get("level_name", "")):
        name = level.get("level_name", "").strip()
        if not name or name.lower().startswith("unassigned"):
            reason = ("No building level is stated; confirm the proposed single-level assumption."
                      if assumed_floor else "Confirm the drawing level; no real floor was identified.")
            issue("floor_unknown", reason,
                  evidence=level.get("purpose_evidence", []), affected_id="project")
            continue
        evidence = [{"page": page, "excerpt": name} for page in level.get("page_numbers", [])]
        evidence = evidence or level.get("purpose_evidence", [])
        floor_id = "floor_" + fingerprint([document, name.casefold()])[:12]
        floors[name.casefold()] = add("floors", "floor", [name.casefold()],
            {"floor_id": floor_id, "name": name, "elevation_m": None}, evidence,
            "Confirm this drawing level; elevation remains optional.", confidence="inferred")

    spaces = {}
    # Detections a reviewer marked "not a room" never become draft candidates.
    not_a_room = set((room_registry or {}).get("excluded_room_identities", []))
    for space in building_evidence.get("spaces", []):
        if space.get("name"):
            if room_identity(space["name"], str(space.get("level_name", "")).strip() or "Unassigned level") in not_a_room:
                continue
            key = (str(space.get("level_name", "")).strip().casefold(), space["name"].casefold())
            spaces.setdefault(key, []).append(space)
    room_id_aliases = {}
    registry_rooms = [row for row in (room_registry or {}).get("rooms", [])
                      if isinstance(row, dict) and row.get("room_id") and row.get("label")]
    drawing_pages = {row.get("page"): row for row in drawing_coverage.get("pages", []) if isinstance(row, dict)}
    registry_by_label = {}
    for row in registry_rooms:
        key = (str(row.get("level_name", "")).strip().casefold(), str(row.get("label", "")).strip().casefold())
        registry_by_label.setdefault(key, []).append(row)
    for key, registry_matches in registry_by_label.items():
        if len(registry_matches) > 1 and len({row.get("room_id") for row in registry_matches}) > 1:
            issue([key, "room_registry_duplicate"],
                  "Room sources contain distinct IDs for the same exact label and level. Resolve the identity conflict before drafting either room.",
                  [e for row in registry_matches for e in row.get("evidence", [])])
            continue
        registry_room = registry_matches[0]
        label, level_name = str(registry_room.get("label", "")).strip(), str(registry_room.get("level_name", "")).strip()
        exact_id_matches = [(space_key, space) for space_key, rows in spaces.items() for space in rows
                            if str(space.get("id", "")) == str(registry_room["room_id"])]
        label_matches = [(space_key, space) for space_key, rows in spaces.items() for space in rows
                         if space_key == key]
        matches = exact_id_matches or label_matches
        if len(matches) > 1:
            issue([registry_room["room_id"], "room_registry_ambiguous"],
                  "A room-registry entry matches multiple building-evidence spaces. Resolve the duplicate identity before drafting.",
                  registry_room.get("evidence", []))
            continue
        if matches:
            room_id_aliases[str(registry_room["room_id"])] = str(matches[0][1].get("id", ""))
            continue
        if key[0].startswith("unassigned") or not key[0]:
            level_name = "Unassigned level"
        cited_evidence = []
        for citation in registry_room.get("evidence", []):
            if not isinstance(citation, dict):
                continue
            page_number = citation.get("page")
            page_record = drawing_pages.get(page_number, {})
            excerpt = citation.get("excerpt", "")
            reference = citation.get("reference") or " ".join(part for part in (
                page_record.get("drawing_number", ""), f"page {page_number}" if page_number else "") if part)
            cited_evidence.append({**citation, "reference": reference or citation.get("reference", ""),
                                   "excerpt": excerpt})
        pseudo_space = {"id": registry_room["room_id"], "name": label, "level_name": level_name,
                        "geometry_status": "label_detected", "evidence": cited_evidence,
                        "room_source": registry_room.get("source", "room_registry"),
                        "confidence": registry_room.get("confidence", "unknown"),
                        "unresolved_fields": ["area", "geometry"]}
        spaces.setdefault((level_name.casefold(), label.casefold()), []).append(pseudo_space)
        room_id_aliases[str(registry_room["room_id"])] = str(registry_room["room_id"])

    proof_by_source_room = {}
    registry_records = {(str(row.get("room_id")), str(row.get("trace_id"))): row
                        for row in (room_registry or {}).get("records", []) if isinstance(row, dict)}
    geometry_entities = ((calculation_input_evidence or {}).get("geometry_resolution") or {}).get("entities", [])
    for proof in geometry_entities:
        if not isinstance(proof, dict) or proof.get("kind") != "room_geometry_proof" or proof.get("extraction_method") != "reviewer_traced_boundary":
            continue
        value = proof.get("value") or {}
        trace_id, source_room_id = str(value.get("reviewer_trace_id", "")), str(proof.get("room_source_id", ""))
        trace = registry_records.get((source_room_id, trace_id))
        calibration = value.get("calibration") if isinstance(value.get("calibration"), dict) else {}
        area = value.get("area_m2")
        if (not trace or not proof.get("entity_id") or proof.get("geometry_status") != "geometry_proposed"
                or calibration.get("status") not in {"agreed", "declared_scale_rejected"}
                or not isinstance(area, (int, float)) or not math.isfinite(area) or area <= 0
                or value.get("source_fingerprints") != trace.get("source_fingerprints")):
            continue
        page = drawing_pages.get(proof.get("source", {}).get("page"), {})
        dimension = calibration.get("dimension_value_mm")
        difference = calibration.get("difference_percent")
        difference_text = f"; difference {difference:.2f}%" if isinstance(difference, (int, float)) else ""
        reason = "Declared-scale calibration agrees" if calibration.get("status") == "agreed" else "Two reviewer dimensions agree; declared scale rejected"
        citation = {"reference": page.get("drawing_number") or f"Page {proof.get('source', {}).get('page')}",
                    "page": proof.get("source", {}).get("page"),
                    "excerpt": f"Reviewer {trace.get('reviewer')}; trace {trace_id}; calibration dimension {dimension:g} mm; {reason}{difference_text}."}
        proof_row = {
            "proof_id": proof["entity_id"], "area_m2": area, "calibration": deepcopy(calibration),
            "source_fingerprints": deepcopy(value.get("source_fingerprints", {})),
            "trace_id": trace_id, "reviewer": trace.get("reviewer", ""), "page": proof.get("source", {}).get("page"),
            "drawing_number": page.get("drawing_number", ""), "citations": [citation],
        }
        draft_room_id = room_id_aliases.get(source_room_id, source_room_id)
        proof_by_source_room.setdefault(draft_room_id, []).append(proof_row)
    room_lookup = {}
    for key, matches in sorted(spaces.items()):
        space = matches[0]
        evidence = [e for row in matches for e in row.get("evidence", [])]
        if len(matches) != 1:
            issue([key, "duplicate"], "Ambiguous repeated room identity. Resolve duplicate rooms and conflicting areas before drafting topology.", evidence)
            continue
        floor = floors.get(key[0])
        if not floor and assumed_floor and key[0].strip().startswith("unassigned"):
            floor = assumed_floor
        if not floor:
            issue([key, "floor"], "Room evidence is present but its drawing level is unresolved. Map it to a reviewed floor.", evidence)
        # Candidate identity is anchored to the evidence record and sheet/page,
        # not the extracted value. A corrected area/excerpt must revisit the
        # same proposal rather than silently creating a new room.
        location = sorted((space.get("id", ""), e.get("page")) for e in evidence)
        source_room_id = str(space.get("id", ""))
        identity = ["room_id", source_room_id] if source_room_id in room_id_aliases.values() else [key, location]
        suffix = fingerprint([document, identity])[:12]
        zone_id, room_id = "zone_" + suffix, "room_" + suffix
        zone = add("zones", "zone", identity, {"zone_id": zone_id, "name": space["name"],
            "floor_id": floor["value"]["floor_id"] if floor else "",
            "floor_status": "proposed" if floor else "unresolved"}, evidence,
            "Review the proposed one-room zone and floor mapping.", [space.get("id")], [floor["candidate_id"]] if floor else [], space.get("confidence", "unknown"))
        area = quantity(space.get("area"), "m2")
        reviewer_proofs = sorted(proof_by_source_room.get(source_room_id, []), key=lambda row: row["proof_id"])
        reviewer_proof = None
        if reviewer_proofs:
            trace_areas = [row["area_m2"] for row in reviewer_proofs]
            if not trace_areas_agree(trace_areas):
                relative_difference = (max(trace_areas) - min(trace_areas)) / ((max(trace_areas) + min(trace_areas)) / 2.0) * 100.0
                issue([source_room_id, "room_trace_conflict"],
                      f"Current calibrated traces differ by {relative_difference:.2f}%, above the {AREA_AGREEMENT_RELATIVE_TOLERANCE:.0%} comparison tolerance. No traced area or geometry confirmation was proposed.",
                      [citation for row in reviewer_proofs for citation in row["citations"]], source_room_id)
            else:
                reviewer_proof = deepcopy(reviewer_proofs[0])
                reviewer_proof["area_m2"] = sum(trace_areas) / len(trace_areas)
                reviewer_proof["supporting_proofs"] = [deepcopy(row) for row in reviewer_proofs]
                reviewer_proof["citations"] = [citation for row in reviewer_proofs for citation in row["citations"]]
                reviewer_proof["comparison_tolerance"] = {
                    "relative_tolerance_percent": AREA_AGREEMENT_RELATIVE_TOLERANCE * 100,
                    "method": "maximum pairwise trace-area spread divided by the mean of the extremes",
                    "engineering_accuracy_claim": False,
                }
        if reviewer_proof and area is not None:
            rounding_tolerance = displayed_area_rounding_tolerance(space.get("area"), area, evidence)
            comparison_tolerance = rounding_tolerance + reviewer_proof["area_m2"] * AREA_AGREEMENT_RELATIVE_TOLERANCE
            difference = abs(area - reviewer_proof["area_m2"])
            reviewer_proof["comparison_tolerance"].update({
                "printed_rounding_tolerance_m2": rounding_tolerance,
                "combined_tolerance_m2": comparison_tolerance,
                "method": "printed half-last-digit rounding plus 2% of traced area; comparison only, not measurement uncertainty",
            })
            if difference > comparison_tolerance:
                issue([source_room_id, "room_trace_area_conflict"],
                      f"The cited room area differs from the current trace by {difference:.3f} m², above the combined printed-rounding and {AREA_AGREEMENT_RELATIVE_TOLERANCE:.0%} comparison allowance ({comparison_tolerance:.3f} m²). No traced area or geometry confirmation was proposed.",
                      evidence + reviewer_proof["citations"], source_room_id)
                reviewer_proof = None
        existing_geometry_status = space.get("geometry_status", "label_detected")
        geometry_status = "geometry_proposed" if reviewer_proof else existing_geometry_status
        geometry_reference = reviewer_proof["proof_id"] if reviewer_proof else space.get("geometry_reference")
        room = add("rooms", "room", identity, {"room_id": room_id, "name": space["name"], "zone_id": zone_id,
            "floor_id": floor["value"]["floor_id"] if floor else "", "geometry_status": geometry_status,
            "geometry_reference": geometry_reference, "unresolved_fields": list(space.get("unresolved_fields", []))},
            evidence, "Confirm room identity and mapping; missing load inputs remain missing.",
            [space.get("id")], [zone["candidate_id"]], space.get("confidence", "unknown"))
        room["room_source"] = space.get("room_source", "building_evidence")
        if reviewer_proof:
            room["reviewer_geometry_proof"] = deepcopy(reviewer_proof)
        room["fingerprint"] = fingerprint({key: value for key, value in room.items() if key != "fingerprint"})
        room_lookup[space.get("id")] = room
        if area is not None:
            # A room label alone does not evidence an area.
            area_evidence = [e for e in evidence if re.search(r"\b" + re.escape(str(area).removesuffix(".0")) + r"(?:\.0)?\s*m[2²]", e.get("excerpt", ""), re.I)]
            add("room_inputs", "area", identity, {"room_id": room_id, "area_m2": area}, area_evidence,
                "Confirm the cited room area.", [space.get("id")], [room["candidate_id"]], space.get("confidence", "unknown"))
        else:
            issue([identity, "area"], "Supply a cited positive room area with explicit m² units.", evidence, room_id)
        if reviewer_proof and area is not None:
            matching_area = next((candidate for candidate in draft["candidates"]["room_inputs"]
                                  if candidate["kind"] == "area" and candidate["value"].get("room_id") == room_id), None)
            if matching_area:
                citations_by_fingerprint = {fingerprint(row): row for row in
                                            matching_area["citations"] + reviewer_proof["citations"]}
                matching_area["citations"] = list(citations_by_fingerprint.values())
                trace_evidence_ids = [value for proof_row in reviewer_proof["supporting_proofs"]
                                      for value in (proof_row["trace_id"], proof_row["proof_id"])]
                matching_area["evidence_ids"] = sorted(set(matching_area["evidence_ids"] + trace_evidence_ids))
                matching_area["reviewer_geometry_proof"] = deepcopy(reviewer_proof)
                matching_area["fingerprint"] = fingerprint({key: value for key, value in matching_area.items() if key != "fingerprint"})
        elif reviewer_proof:
            add("room_inputs", "area", ["reviewer_trace", source_room_id, reviewer_proof["trace_id"]],
                {"room_id": room_id, "area_m2": reviewer_proof["area_m2"]}, reviewer_proof["citations"],
                "Review and accept the current calibrated reviewer trace; the area remains a proposal until applied.",
                [reviewer_proof["trace_id"], reviewer_proof["proof_id"]], [room["candidate_id"]], "reviewed")
            area_candidate = draft["candidates"]["room_inputs"][-1]
            area_candidate["reviewer_geometry_proof"] = deepcopy(reviewer_proof)
            area_candidate["fingerprint"] = fingerprint({key: value for key, value in area_candidate.items() if key != "fingerprint"})
        for field in space.get("unresolved_fields", []):
            if field not in {"area", "geometry", "floor"}:
                continue
            issue([identity, field], f"Resolve the room {field} from source-backed evidence before activation.", evidence, room_id)
        if not floor:
            issue([identity, "floor"], "Map the room to a reviewed floor before activation.", evidence, room_id)
        if space.get("geometry_status") not in {"geometry_confirmed"}:
            issue([identity, "geometry"], "Confirm a room boundary or geometry witness; a label alone cannot activate a room.", evidence, room_id)
        issue([identity, "schedules"], "Assign reviewed schedules for each non-zero supported room load in the room editor.", evidence, room_id)

    # Only explicit source-room links permit assignment. Same name/page/level is insufficient.
    for fact in thermal_evidence.get("facts", []):
        if fact.get("field") != "ceiling_height_mm":
            continue
        room = room_lookup.get(fact.get("building_evidence_id") or fact.get("space_id"))
        add("room_inputs", "ceiling", [fact.get("fact_id"), fact.get("evidence", [])],
            {"room_id": room["value"]["room_id"] if room else "", "ceiling_height_mm": quantity({"value": fact.get("value"), "unit": fact.get("unit")}, "mm")},
            fact.get("evidence", []), "Confirm room allocation and ceiling evidence; height is metadata only.",
            [fact.get("fact_id")], [room["candidate_id"]] if room else [], fact.get("confidence", "unknown"))
    for family, kind in (("lighting", "lighting"), ("equipment", "equipment")):
        for item in building_evidence.get(family, []):
            room = room_lookup.get(item.get("space_id"))
            value = {"room_id": room["value"]["room_id"] if room else "", "schedule_id": "",
                     "diversity_factor": None}
            if kind == "lighting":
                value.update(connected_w=item.get("connected_w"), lighting_w_m2=None)
            else:
                value.update(name=item.get("name", ""), quantity=item.get("quantity"), watts=item.get("watts"),
                             kind=item.get("kind", ""), heat_input_basis="", space_gain_factor=None)
            identity = [kind, item.get("level_name"), item.get("name", item.get("fixture_tag")), item.get("evidence", [])]
            add("room_inputs", kind, identity, value, item.get("evidence", []),
                "Confirm room allocation, heat basis, factors, and schedule before applying this load.",
                [item.get("id")], [room["candidate_id"]] if room else [], item.get("confidence", "unknown"))
    for item in thermal_model.get("schedule_candidates", []):
        if not isinstance(item, dict):
            raise ValueError("Schedule evidence must be an object.")
        profiles = item.get("day_profiles", {})
        complete = {day: profile for day, profile in profiles.items() if day in DAY_TYPES and
                    isinstance(profile, dict) and len(profile.get("values", [])) == 24}
        if not complete or len(complete) != len(profiles):
            issue(["schedule", item.get("schedule_id")], "Supply exactly 24 values and citations for each declared schedule day type.", item.get("evidence", []))
            continue
        add("schedules", "schedule", [item.get("schedule_id"), item.get("evidence", [])],
            {"schedule_id": item.get("schedule_id", ""), "title": item.get("title", ""), "day_profiles": complete},
            item.get("evidence", []), "Review the declared day profiles; other day types remain missing.", [item.get("id")], confidence=item.get("confidence", "unknown"))
    for family, kind in (("constructions", "construction"), ("openings", "window"), ("surfaces", "surface")):
        for item in building_evidence.get(family, []):
            evidence_location = sorted((e.get("page"), e.get("kind", "")) for e in item.get("evidence", []))
            identity = [family, item.get("id", ""), item.get("level_name"), item.get("tag", item.get("reference", item.get("adjacency"))), evidence_location]
            suffix = fingerprint([document, identity])[:12]
            value = {"title": item.get("reference") or item.get("tag") or item.get("adjacency") or kind}
            if kind == "construction":
                value.update(record_id="construction_" + suffix, kind=item.get("kind", ""),
                    u_value_w_m2k=(item.get("thermal_performance") or {}).get("u_value_w_m2k"), absorptivity=None)
            elif kind == "window":
                value.update(record_id="window_" + suffix, opening_kind=item.get("kind", ""),
                    u_value_w_m2k=(item.get("performance") or {}).get("u_value_w_m2k"),
                    geometry={
                        "dimensions": deepcopy(item.get("dimensions")),
                        "evidence": deepcopy(item.get("geometry") or {}),
                    })
            else:
                value.update(surface_id="surface_" + suffix, owner_room_id="", owner_zone_id="", kind=item.get("kind", ""),
                    area_m2=None, orientation="", boundary_method="", construction_id="", window_id="", adjacent_temperature_c=None)
                if item.get("geometry"):
                    value["geometry_evidence"] = deepcopy(item["geometry"])
            add("envelope", kind, identity, value, item.get("evidence", []),
                "Supply missing reviewed properties and geometry. Envelope activation requires a separate editor action.",
                [item.get("id")], confidence=item.get("confidence", "unknown"))

    # Reordering never carries an approval to different content or dependencies.
    lookup = {row["candidate_id"]: row for row in all_candidates(draft)}
    for row in all_candidates(draft):
        row["fingerprint"] = fingerprint([row["fingerprint"], [lookup[d]["fingerprint"] for d in row["dependencies"]]])
    for cid, decision in previous.get("decisions", {}).items():
        row = lookup.get(cid)
        if previous.get("schema_version") == 2 and row and decision.get("candidate_fingerprint") == row["fingerprint"]:
            draft["decisions"][cid] = deepcopy(decision)
        else:
            draft["review_history"].append({"candidate_id": cid, "decision": decision, "reason": "Evidence changed, removed, or legacy approval requires review."})
    topology_ready = bool(draft["candidates"]["floors"] and draft["candidates"]["rooms"])
    if not topology_ready:
        draft["status"] = "blocked"
        draft["readiness"] = {"status": "blocked", "issues": [
            {"status": "blocked", "affected_id": "project", "source_artifact": "building_evidence.json",
             "reason": "No source-backed floor and room topology candidates are available.",
             "effect": "blocks_project", "remediation": "Provide an identifiable plan page and room evidence."}
        ]}
    else:
        draft["readiness"] = {"status": "review_required", "issues": deepcopy(draft["review_items"])}
    draft["geometry_review"] = geometry_review_summary(draft)
    return draft


def save_review(draft, changes, expected_revision):
    check_revision(draft, expected_revision)
    if not isinstance(changes, dict):
        raise ValueError("Review decisions must be an object.")
    result = deepcopy(draft)
    lookup = {row["candidate_id"]: row for row in all_candidates(draft)}
    issues = {row["item_id"]: row for row in draft["review_items"]}
    for cid, raw in changes.items():
        row = lookup.get(cid) or issues.get(cid)
        if row is None or not isinstance(raw, dict) or raw.get("decision") not in DECISIONS:
            raise ValueError("Unknown candidate or invalid review decision: " + cid)
        decision = deepcopy(raw)
        if decision["decision"] != "pending" and not str(decision.get("reviewer", "")).strip():
            raise ValueError("Reviewer attribution is required: " + cid)
        if decision["decision"] == "edit":
            if not isinstance(decision.get("value"), dict) or not str(decision.get("source", "")).strip():
                raise ValueError("Edited values need a field object and engineering review source: " + cid)
            if row.get("kind") == "room" and {"geometry_status", "geometry_reference"}.intersection(decision["value"]):
                raise ValueError("Room geometry status and reference cannot be edited by hand: " + cid)
            if not supported_citations(validate_citations(decision.get("citations", []), cid)):
                raise ValueError("Edited values need a citation and supporting excerpt: " + cid)
        decision.update(candidate_fingerprint=row.get("fingerprint", fingerprint(row)), reviewed_at=timestamp())
        result["decisions"][cid] = decision
    result.update(revision=draft["revision"] + 1, updated_at=timestamp())
    result["geometry_review"] = geometry_review_summary(result)
    return result


def check_revision(draft, expected):
    if draft.get("schema_version") != 2:
        raise DraftConflict("Rebuild this legacy draft and review its evidence again.", "legacy_draft")
    if expected != draft.get("revision"):
        raise DraftConflict("The draft changed. Reload and review the latest version.")


def apply_calculator_draft(draft, decisions=None, hourly_model=None, schedule_library=None,
                           envelope_library=None, envelope_model=None, source_requirements_updated_at=""):
    """Pure preview: returns validated artifacts and exact additive changes."""
    check_revision(draft, draft.get("revision"))
    originals = {"hourly_load_model": hourly_model or empty_hourly_load_model(),
                 "schedule_library": schedule_library or empty_schedule_library(),
                 "envelope_library": envelope_library or empty_envelope_library(),
                 "envelope_model": envelope_model or empty_envelope_model()}
    model = validate_hourly_load_model(originals["hourly_load_model"])
    if not hourly_model:
        model["source_requirements_updated_at"] = source_requirements_updated_at
    artifacts = {"hourly_load_model": model,
                 "schedule_library": validate_schedule_library(originals["schedule_library"]),
                 "envelope_library": validate_envelope_library(originals["envelope_library"])}
    artifacts["envelope_model"] = validate_envelope_model(originals["envelope_model"], artifacts["envelope_library"])
    for name, artifact in artifacts.items():
        artifact["updated_at"] = originals[name].get("updated_at", "")
    summary = {name: [] for name in ("created", "populated_fields", "already_present", "skipped_conflicts", "missing_dependencies", "unresolved", "reports_marked_stale")}
    success = set()
    modified = set()
    decisions = draft.get("decisions", {}) if decisions is None else decisions
    order = {kind: i for i, kind in enumerate(("floor", "zone", "room", "area", "ceiling", "schedule", "lighting", "equipment", "construction", "window", "surface"))}
    for item in sorted(all_candidates(draft), key=lambda r: order[r["kind"]]):
        cid = item["candidate_id"]
        decision = decisions.get(cid, {})
        action = decision.get("decision", "pending")
        if action not in {"accept", "edit"}:
            if action != "reject":
                summary["unresolved"].append({"candidate_id": cid, "reason": item["reason"], "decision": action})
            continue
        if decision.get("candidate_fingerprint") != item["fingerprint"]:
            raise DraftConflict("Approval no longer matches proposal " + cid, "evidence_changed")
        value = deepcopy(item["value"])
        supplied = decision.get("value", {}) if action == "edit" else {}
        if set(supplied) - set(value):
            raise ValueError("Unknown editable fields for " + cid)
        if item["kind"] == "room" and {"geometry_status", "geometry_reference"}.intersection(supplied):
            raise ValueError("Room geometry status and reference cannot be edited by hand: " + cid)
        # Stable target IDs are immutable; relationships are explicitly editable.
        id_key = {"floor": "floor_id", "zone": "zone_id", "room": "room_id", "schedule": "schedule_id",
                  "construction": "record_id", "window": "record_id", "surface": "surface_id"}.get(item["kind"])
        if id_key in supplied and supplied[id_key] != value[id_key]:
            raise ValueError("Target ID cannot be edited: " + cid)
        value.update(supplied)
        # A label-only proposal is useful evidence, but it is not a calculator
        # room.  Topology activation requires an explicitly reviewed floor and
        # geometry witness; the bridge must never turn proximity into geometry.
        if item["kind"] == "zone" and not value.get("floor_id"):
            summary["unresolved"].append({"candidate_id": cid, "reason": "A zone cannot be applied without a reviewed floor assignment."})
            continue
        if item["kind"] == "room":
            if not value.get("floor_id"):
                summary["unresolved"].append({"candidate_id": cid, "reason": "A room cannot be applied without a reviewed floor assignment."})
                continue
            proof = item.get("reviewer_geometry_proof")
            calibration = (proof or {}).get("calibration", {})
            proof_current = bool(
                proof and proof.get("proof_id") == value.get("geometry_reference")
                and isinstance(proof.get("area_m2"), (int, float)) and math.isfinite(proof["area_m2"]) and proof["area_m2"] > 0
                and calibration.get("status") in {"agreed", "declared_scale_rejected"}
                and isinstance(calibration.get("mm_per_px"), (int, float)) and calibration["mm_per_px"] > 0
                and proof.get("source_fingerprints", {}).get("source_pdf")
                and proof.get("source_fingerprints", {}).get("vector_page")
            )
            if value.get("geometry_status") != "geometry_confirmed" and not proof_current:
                summary["unresolved"].append({"candidate_id": cid, "reason": "Room geometry must be explicitly reviewed and confirmed before activation."})
                continue
            if proof_current:
                value["geometry_status"] = "geometry_confirmed"
        evidence = validate_citations(item["citations"] + decision.get("citations", []), cid)
        if not supported_citations(evidence):
            summary["unresolved"].append({"candidate_id": cid, "reason": "Supply supporting citations and excerpts."})
            continue
        dependencies = [d for d in item["dependencies"] if d not in success]
        # An explicit edited parent mapping may reference an existing authored record.
        parent = {"zone": ("floor_id", "floors"), "room": ("zone_id", "zones"),
                  "area": ("room_id", "rooms"), "ceiling": ("room_id", "rooms"),
                  "lighting": ("room_id", "rooms"), "equipment": ("room_id", "rooms")}.get(item["kind"])
        mapped = parent and parent[0] in supplied and any(r[parent[0]] == value[parent[0]] for r in model[parent[1]])
        if dependencies and not mapped:
            rejected_parent = next((parent_id for parent_id in dependencies
                                    if decisions.get(parent_id, {}).get("decision") == "reject"), None)
            if item["kind"] == "zone" and rejected_parent:
                summary["unresolved"].append({"candidate_id": cid, "reason": "A zone cannot be applied without a reviewed floor assignment."})
            else:
                summary["missing_dependencies"].append({"candidate_id": cid, "dependencies": dependencies, "reason": "Review the parent proposals or explicitly map to an existing parent."})
            continue
        trial = deepcopy(artifacts)
        provenance = {"candidate_id": cid, "candidate_fingerprint": item["fingerprint"], "evidence_ids": item["evidence_ids"],
                      "original_citations": item["citations"], "reviewer": decision.get("reviewer"), "reviewed_at": decision.get("reviewed_at"),
                      "source": decision.get("source") or item["source"], "citations": evidence}
        if item["kind"] == "room" and item.get("reviewer_geometry_proof") and proof_current and value.get("geometry_status") == "geometry_confirmed":
            proof = item["reviewer_geometry_proof"]
            if proof.get("proof_id") == item.get("value", {}).get("geometry_reference"):
                provenance["geometry_status"] = "geometry_confirmed"
                provenance["geometry_acceptance"] = {"proof_id": proof["proof_id"],
                    "reviewer": str(decision.get("reviewer", "")).strip(),
                    "decided_at": decision.get("reviewed_at") or timestamp(),
                    "trace_id": proof.get("trace_id"), "calibration": deepcopy(proof.get("calibration")),
                    "comparison_tolerance": deepcopy(proof.get("comparison_tolerance", {})),
                    "source_fingerprints": deepcopy(proof.get("source_fingerprints", {})),
                    "supporting_proofs": deepcopy(proof.get("supporting_proofs", [proof]))}
        try:
            target, bucket, key, record, fill = prepare_record(item["kind"], value, trial, provenance)
            rows = trial[target][bucket]
            existing = next((r for r in rows if r[key] == record[key]), None)
            changes, conflicts = {}, {}
            if existing:
                for field, proposed in record.items():
                    if field in ({key, "source", "citations", "verification_status", "review_status", "mapping_status", "status", "revision", "bridge_provenance", "floor_status", "geometry_status", "geometry_reference", "unresolved_fields"} | ({"floor_id"} if item["kind"] == "room" else set())):
                        continue
                    current = existing.get(field)
                    if isinstance(proposed, dict) and fill:
                        for subkey, subvalue in proposed.items():
                            old = (current or {}).get(subkey)
                            if old == subvalue:
                                continue
                            if old in (None, ""):
                                changes.setdefault(field, deepcopy(current or {}))[subkey] = subvalue
                            else:
                                conflicts[field + "." + subkey] = {"current": old, "proposed": subvalue}
                    elif current != proposed and proposed is not None:
                        if fill and current in (None, ""):
                            changes[field] = proposed
                        else:
                            conflicts[field] = {"current": current, "proposed": proposed}
                if conflicts:
                    summary["skipped_conflicts"].append({"candidate_id": cid, "fields": conflicts, "source": existing.get("source"), "proposed_source": provenance["source"], "reason": "Authored values are preserved; resolve in the detailed editor."})
                    continue
                if not changes:
                    summary["already_present"].append({"candidate_id": cid, "id": record[key]})
                    success.add(cid)
                    continue
                existing.update(changes)
                existing.setdefault("bridge_provenance", {})[cid] = provenance
                existing["citations"] = list(existing.get("citations", [])) + [c for c in evidence if c not in existing.get("citations", [])]
            else:
                rows.append(record)
            validate_artifacts(trial)
        except (ValueError, KeyError, TypeError) as error:
            summary["unresolved"].append({"candidate_id": cid, "reason": str(error), "editor": item["target_artifact"]})
            continue
        artifacts = trial
        model = artifacts["hourly_load_model"]
        modified.add(target)
        summary["populated_fields" if existing else "created"].append({"candidate_id": cid, "artifact": target, "id": record[key], "fields": sorted(changes) if existing else sorted(record), "target_fingerprint": fingerprint(existing or record)})
        success.add(cid)
    summary["unresolved"].extend({"item_id": row["item_id"], "reason": row["reason"], "affected_id": row["affected_id"]} for row in draft["review_items"])
    for name in modified:
        artifacts[name]["updated_at"] = timestamp()
    return {**artifacts, "changed": {name: name in modified for name in originals}, "summary": summary}


def require(value, *fields):
    missing = [field for field in fields if value.get(field) in (None, "")]
    if missing:
        raise ValueError("Missing reviewed fields: " + ", ".join(missing))


def number(value, field, low=0, high=1e8):
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or not low <= raw <= high:
        raise ValueError(f"{field} must be a reviewed number between {low} and {high}.")
    return raw


def prepare_record(kind, value, artifacts, provenance):
    common = {"source": provenance["source"] + " — engineer review: " + provenance["reviewer"],
              "citations": provenance["citations"], "bridge_provenance": {provenance["candidate_id"]: provenance}}
    model = artifacts["hourly_load_model"]
    if kind in {"floor", "zone", "room"}:
        record = {**value, **common, "verification_status": "confirmed"}
        if kind == "room":
            record["mapping_status"] = "confirmed"
        return "hourly_load_model", kind + "s", kind + "_id", record, False
    if kind in {"area", "ceiling", "lighting", "equipment"}:
        require(value, "room_id")
        room = next((r for r in model["rooms"] if r["room_id"] == value["room_id"]), None)
        if not room:
            raise ValueError("Select an accepted or existing room.")
        record = {"room_id": value["room_id"], **common}
        if kind in {"area", "ceiling"}:
            field = "area_m2" if kind == "area" else "ceiling_height_mm"
            record[field] = number(value, field, 0.001, 1000000)
        else:
            require(value, "schedule_id", "diversity_factor")
            number(value, "diversity_factor", 0, 1)
            schedule = next((s for s in artifacts["schedule_library"]["schedules"] if s["schedule_id"] == value["schedule_id"]), None)
            if not schedule or schedule["status"] != "confirmed" or not any(p["status"] == "confirmed" and len(p["values"]) == 24 and supported_citations(p["citations"]) for p in schedule["day_profiles"].values()):
                raise ValueError("Select a confirmed cited schedule with a complete declared profile.")
            if kind == "lighting":
                density = value.get("lighting_w_m2")
                if density is None:
                    require(room, "area_m2")
                    watts = number(value, "connected_w")
                    density = watts / number(room, "area_m2", 0.001)
                    provenance["derivation"] = {"formula": "connected_w / area_m2", "connected_w": watts, "area_m2": room["area_m2"]}
                else:
                    number(value, "lighting_w_m2")
                record.update(cooling_load={"lighting_w_m2": density, "lighting_diversity_factor": value["diversity_factor"]}, schedule_assignments={"lighting": value["schedule_id"]})
            else:
                require(value, "name", "heat_input_basis", "kind")
                for field in ("quantity", "watts", "space_gain_factor"):
                    number(value, field, 0.001 if field == "quantity" else 0, 1 if field == "space_gain_factor" else 1e8)
                source_id = "equipment_" + provenance["candidate_id"].split("_")[-1]
                source = {**{k: value[k] for k in ("name", "quantity", "watts", "kind", "diversity_factor", "space_gain_factor")}, **common,
                          "source_id": source_id, "verification_status": "confirmed", "heat_input_basis": value["heat_input_basis"]}
                prior = next((s for s in room["heat_sources"] if s["source_id"] == source_id), None)
                if prior and any(prior.get(k) != source[k] for k in ("name", "quantity", "watts", "kind", "diversity_factor", "space_gain_factor")):
                    raise ValueError("Equipment conflict: an authored heat-source value differs.")
                if not prior:
                    room["heat_sources"].append(source)
                assignments = room["schedule_assignments"]["equipment"]
                if assignments.get(source_id) not in (None, "", value["schedule_id"]):
                    raise ValueError("Equipment schedule conflict; use the detailed editor.")
                # Return a room patch; changes to its children remain part of this trial.
                record["schedule_assignments"] = {"equipment": {**assignments, source_id: value["schedule_id"]}}
        return "hourly_load_model", "rooms", "room_id", record, True
    if kind == "schedule":
        require(value, "schedule_id", "title", "day_profiles")
        profiles = deepcopy(value["day_profiles"])
        if not profiles or set(profiles) - set(DAY_TYPES):
            raise ValueError("Declare supported schedule day types.")
        for day, profile in profiles.items():
            if len(profile.get("values", [])) != 24:
                raise ValueError("Each declared schedule profile requires exactly 24 values.")
            cites = validate_citations(profile.get("citations") or common["citations"], day)
            if not supported_citations(cites):
                raise ValueError("Each declared profile requires source citations.")
            profile.update(status="confirmed", source=common["source"], citations=cites)
        return "schedule_library", "schedules", "schedule_id", {**value, **common, "day_profiles": profiles, "status": "confirmed"}, False
    if kind in {"construction", "window"}:
        require(value, "record_id", "title")
        if kind == "construction":
            if value["kind"] not in CONSTRUCTION_KINDS:
                raise ValueError("Select a supported construction kind.")
            number(value, "u_value_w_m2k", 0.0001)
        elif value.get("opening_kind") not in {"window_or_glazing", "glazing", "window"}:
            raise ValueError("Resolve opening type; a door cannot be assumed to be glazing.")
        return "envelope_library", "constructions" if kind == "construction" else "windows", "record_id", {**value, **common, "revision": 1, "review_status": "confirmed"}, False
    if kind == "surface":
        if artifacts["envelope_model"]["active_for_calculation"]:
            raise ValueError("Envelope is active. Integrate this surface explicitly in the envelope editor.")
        require(value, "owner_room_id", "owner_zone_id", "orientation", "boundary_method", "area_m2")
        room = next((r for r in model["rooms"] if r["room_id"] == value["owner_room_id"]), None)
        if not room or room["zone_id"] != value["owner_zone_id"]:
            raise ValueError("Surface owner must match an existing room and its zone.")
        return "envelope_model", "surfaces", "surface_id", {**value, **common, "review_status": "confirmed"}, False
    raise ValueError("Unsupported proposal type.")


def validate_artifacts(artifacts):
    validators = {"hourly_load_model": validate_hourly_load_model, "schedule_library": validate_schedule_library,
                  "envelope_library": validate_envelope_library}
    for name, validator in validators.items():
        old = artifacts[name].get("updated_at", "")
        artifacts[name] = validator(artifacts[name])
        artifacts[name]["updated_at"] = old
    old = artifacts["envelope_model"].get("updated_at", "")
    artifacts["envelope_model"] = validate_envelope_model(artifacts["envelope_model"], artifacts["envelope_library"])
    artifacts["envelope_model"]["updated_at"] = old
