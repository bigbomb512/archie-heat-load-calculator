"""Read-only scorecard over saved evidence-pipeline artifacts.

Artifact paths mirror backend/web_app.hourly_paths plus service path maps:
room_inference_service._paths, room_use_resolution_service._paths and
internal_gains_resolution_service._paths. Schemas, provenance fields and
reported counts: ai_input.json (legacy/unversioned; pages, roles, review gate);
drawing_coverage.json (version/source_fingerprint; pages, levels, exceptions);
building_evidence.json (version/source_fingerprint; evidence lists);
architect_evidence_fusion.json (schema_version/fingerprint; entities, conflicts,
review_items); calculation_input_evidence.json (schema_version/
input_artifact_fingerprints; candidates, issues, categories);
room_inference_job.json or ai_preliminary_run.json (schema_version/
source_fingerprint; candidate count); room_use_resolution.json and
internal_gains_resolution.json (schema_version/source_fingerprints; records);
model_input_resolution.json (schema_version/dependency_fingerprints; records,
coverage and review queue); calculator_draft.json (schema_version/
source_fingerprints; candidates/review queue); calculator_input_set.json is a
pointer to a named content-addressed snapshot (the snapshot stores inputs and
issues); hourly_load_report.json (stored status/readiness/blockers). Missing
producer-compatible fingerprints remain freshness unknown. No producer or
calculation is called.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ai import ai_preliminary, calculation_extraction, calculator_inputs, drawing_coverage

STAGE_ORDER = (
    "page_triage", "page_review", "drawing_coverage", "building_evidence",
    "evidence_fusion", "calculation_input_evidence", "room_inference",
    "room_use_resolution", "ceiling_volume_resolution", "internal_gains_resolution",
    "airflow_resolution", "model_input_resolution",
    "calculator_draft", "calculator_inputs", "calculation_readiness",
)
LOWER_IS_BETTER_COUNTS = {
    "blockers", "conflicts", "coverage_conflicts", "floor_conflicts",
    "issues", "pages_awaiting_review", "review_queue",
}
HIGHER_IS_BETTER_COUNTS = {"reviewed"}
ALLOWED_STATES = {"present", "absent", "stale", "gated", "error"}
ARTIFACT_FILES = {
    "ai_input": "ai_input.json", "drawing_coverage": "drawing_coverage.json",
    "building_evidence": "building_evidence.json", "evidence_fusion": "architect_evidence_fusion.json",
    "calculation_input_evidence": "calculation_input_evidence.json",
    "spatial_ocr": "spatial_ocr.json", "vector_geometry": "vector_geometry.json",
    "vision_response": "vision_response.json", "dimension_wall_matches": "dimension_wall_matches.json",
    "geometry_confirmation": "geometry_confirmation.json",
    "room_inference_job": "room_inference_job.json", "ai_preliminary_run": "ai_preliminary_run.json",
    "room_use_resolution": "room_use_resolution.json", "internal_gains_resolution": "internal_gains_resolution.json",
    "model_input_resolution": "model_input_resolution.json", "calculator_draft": "calculator_draft.json",
    "calculator_input_set": "calculator_input_set.json", "hourly_load_report": "hourly_load_report.json",
    "thermal_model": "thermal_model.json", "thermal_evidence": "thermal_evidence.json",
    "research_cache": "research_cache.json", "value_resolution": "value_resolution.json",
    "site_location_resolution": "site_location_resolution.json",
    "site_design_weather_resolution": "site_design_weather_resolution.json",
    "ceiling_volume_resolution": "ceiling_volume_resolution.json",
    "thermal_surface_resolution": "thermal_surface_resolution.json",
    "airflow_resolution": "airflow_resolution.json", "ahu_resolution": "ahu_resolution.json",
    "plant_resolution": "plant_resolution.json", "safety_factor_resolution": "safety_factor_resolution.json",
}


def _read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name}: {type(exc).__name__}"
    return (value, "") if isinstance(value, dict) else (None, f"{path.name}: expected JSON object")


def _status(state, counts=None, reason="", freshness="unknown", stored_status=None):
    result = {"status": state, "counts": counts or {}, "reason": reason, "freshness": freshness}
    if stored_status is not None:
        result["stored_status"] = stored_status
    return result


def _count(data, field):
    rows = data.get(field, []) if isinstance(data, dict) else []
    return len(rows) if isinstance(rows, list) else 0


def _group(rows, field):
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            key = str(row.get(field) or "unknown")
            result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _reason_groups(rows):
    result = {}
    for row in rows if isinstance(rows, list) else []:
        text = row.get("reason", "") if isinstance(row, dict) else str(row)
        group = re.split(r"[:.;]", text.strip(), maxsplit=1)[0].strip()
        group = re.sub(r"\b(?:room|zone|floor|page|scenario)\s+[A-Za-z0-9_-]+\b", "entity", group, flags=re.I)
        if group:
            result[group[:90]] = result.get(group[:90], 0) + 1
    return dict(sorted(result.items()))


def _freshness(recorded, current):
    if not isinstance(recorded, dict) or not recorded:
        return "unknown", ""
    compared = 0
    unknown = False
    for name, old in recorded.items():
        new = current.get(name)
        if old and new:
            compared += 1
            if old != new:
                return "stale", f"upstream fingerprint changed: {name}"
        else:
            unknown = True
    return ("current", "") if compared and not unknown else ("unknown", "")


def _artifact_stage(name, data, error="", versions=None, version_field="schema_version",
                    recorded_field=None, current=None, counts=None, gated=False):
    if error:
        return _status("error", reason=error)
    if not data:
        return _status("gated" if gated else "absent",
                       reason="page review is incomplete" if gated else f"{ARTIFACT_FILES.get(name, name + '.json')} is absent")
    if versions is not None and data.get(version_field) not in versions:
        return _status("error", reason=f"{name}: unsupported {version_field} {data.get(version_field)!r}")
    recorded = data.get(recorded_field, {}) if recorded_field else {}
    if isinstance(recorded, str) and recorded_field == "source_fingerprint":
        recorded = {"ai_input": recorded}
    freshness, reason = _freshness(recorded, current or {})
    if freshness == "stale":
        return _status("stale", counts, reason, freshness, data.get("status"))
    if data.get("status") == "stale":
        return _status("stale", counts, "artifact records stale status", "stale", "stale")
    if freshness == "unknown":
        reason = "upstream freshness unknown: no comparable fingerprint recorded"
    return _status("present", counts, reason, freshness, data.get("status"))


def evaluate_page_triage(data, error=""):
    if error:
        return _status("error", reason=error)
    if not data:
        return _status("absent", reason="ai_input.json is absent")
    pages = data.get("drawing_set", {}).get("pages", [])
    triage = data.get("page_triage", {}).get("pages", [])
    return _status("present", {"pages": len(pages) if isinstance(pages, list) else 0,
                               "triage_rows": len(triage) if isinstance(triage, list) else 0,
                               "pages_by_role": _group(triage, "role")})


def evaluate_page_review(data, error=""):
    if error:
        return _status("error", reason=error)
    if not data:
        return _status("absent", reason="page review requires ai_input.json")
    pages = data.get("drawing_set", {}).get("pages", [])
    total = len(pages) if isinstance(pages, list) else 0
    if data.get("review_status", {}).get("human_reviewed") is not True:
        return _status("gated", {"pages_awaiting_review": total},
                       "human_reviewed is not true in ai_input.review_status")
    return _status("present", {"reviewed": 1, "pages": total})


def evaluate_drawing_coverage(data, error="", current=None):
    if error:
        return _status("error", reason=error)
    if not data:
        return _status("absent", reason="drawing_coverage.json is absent")
    if data.get("version") in {1, 2, 3, 4} or (data.get("version") == 5
            and not drawing_coverage.has_current_level_classification(data)):
        return _status("stale", reason="drawing_coverage predates source-aware level classification; rebuild coverage and downstream artifacts",
                       freshness="stale", stored_status=data.get("status"))
    if data.get("version") != 5:
        return _status("error", reason=f"drawing_coverage: unsupported version {data.get('version')!r}")
    levels = data.get("levels", [])
    pages = data.get("pages", data.get("sheet_register", []))
    roles = data.get("page_roles", [])
    candidates = [candidate for page in pages if isinstance(page, dict)
                  for candidate in page.get("level_candidates", []) if isinstance(candidate, dict)]
    counts = {"pages": len(pages) if isinstance(pages, list) else 0,
              "pages_by_role": _group(roles, "role"),
              "level_candidates_by_kind": _group(candidates, "kind"),
              "page_level_statuses": [{"page": row.get("page"), "status": row.get("level_status", "missing")}
                                       for row in pages if isinstance(row, dict)],
              "page_level_status_counts": _group(pages, "level_status"),
              "floors": len(levels) if isinstance(levels, list) else 0,
              "floor_conflicts": sum(row.get("status") in {"conflict", "ambiguous"} for row in levels if isinstance(row, dict)),
              "coverage_conflicts": _count(data, "coverage_exceptions")}
    freshness, reason = _freshness({"ai_input": data.get("source_fingerprint")}, current or {})
    if freshness == "stale":
        return _status("stale", counts, reason, freshness, data.get("status"))
    return _status("present", counts, reason or ("upstream freshness unknown: source fingerprint missing" if freshness == "unknown" else ""),
                   freshness, data.get("status"))


def evaluate_building_evidence(data, error="", current=None):
    fields = ("spaces", "levels", "surfaces", "openings", "constructions", "lighting", "equipment")
    counts = {key: _count(data, key) for key in fields} if data else {}
    return _artifact_stage("building_evidence", data, error, {1}, "version", "source_fingerprint", current, counts)


def evaluate_evidence_fusion(data, error="", current=None, review_gate_active=False):
    fields = ("pages", "entities", "relationships", "conflicts", "review_items")
    counts = {key: _count(data, key) for key in fields} if data else {}
    return _artifact_stage("evidence_fusion", data, error, {2}, recorded_field="source_fingerprint",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_calculation_input_evidence(data, error="", current=None, review_gate_active=False):
    rows = data.get("candidates", []) if data else []
    counts = {"candidates": len(rows) if isinstance(rows, list) else 0,
              "candidates_by_category": _group(rows, "category"), "candidates_by_status": _group(rows, "status"),
              "issues": _count(data, "issues") if data else 0}
    return _artifact_stage("calculation_input_evidence", data, error, {1}, recorded_field="input_artifact_fingerprints",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_room_inference(job, run, error="", current=None, review_gate_active=False):
    if error:
        return _status("error", reason=error)
    proposal = run.get("local_room_inference_proposal", {}) if isinstance(run, dict) else {}
    rooms = proposal.get("rooms", []) if isinstance(proposal, dict) else []
    data = job or ({"schema_version": 1, "status": run.get("status", "present"),
                    "candidate_count": len(rooms)} if run else {})
    if not data:
        return _status("gated" if review_gate_active else "absent",
                       reason="page review is incomplete" if review_gate_active else "room inference artifacts are absent")
    if data.get("schema_version") != 1:
        return _status("error", reason=f"room_inference_job: unsupported schema_version {data.get('schema_version')!r}")
    count = int(data.get("candidate_count", 0) or 0)
    freshness, reason = _freshness({"room_inference_inputs": data.get("source_fingerprint")}, current or {})
    if data.get("status") in {"failed", "error"}:
        return _status("error", {"room_candidates": count}, "stored inference status is error", freshness, data.get("status"))
    if data.get("status") == "stale" or freshness == "stale":
        return _status("stale", {"room_candidates": count}, reason or "stored inference is stale", "stale", data.get("status"))
    return _status("present", {"room_candidates": count},
                   "" if freshness == "current" else "upstream freshness unknown: no comparable fingerprint",
                   freshness, data.get("status"))


def evaluate_room_use_resolution(data, error="", current=None, review_gate_active=False):
    rows = data.get("records", []) if data else []
    counts = {"records": len(rows) if isinstance(rows, list) else 0, "records_by_status": _group(rows, "status"),
              "review_queue": sum(row.get("status") in {"needs_review", "blocked", "stale"} for row in rows if isinstance(row, dict))}
    return _artifact_stage("room_use_resolution", data, error, {1}, recorded_field="source_fingerprints",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_ceiling_volume_resolution(data, error="", current=None, review_gate_active=False):
    rows = data.get("records", []) if data else []
    counts = {"records": len(rows) if isinstance(rows, list) else 0,
              "records_by_status": _group(rows, "status"),
              "overrides": sum(bool(row.get("override")) for row in rows if isinstance(row, dict))}
    return _artifact_stage("ceiling_volume_resolution", data, error, {1},
                           recorded_field="source_fingerprints", current=current,
                           counts=counts, gated=review_gate_active)


def evaluate_internal_gains_resolution(data, error="", current=None, review_gate_active=False):
    rows = data.get("records", []) if data else []
    counts = {"records": len(rows) if isinstance(rows, list) else 0, "records_by_status": _group(rows, "status"),
              "schedules": _count(data, "schedules") if data else 0}
    return _artifact_stage("internal_gains_resolution", data, error, {1}, recorded_field="source_fingerprints",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_airflow_resolution(data, error="", current=None, review_gate_active=False):
    rows = data.get("records", []) if data else []
    counts = {"records": len(rows) if isinstance(rows, list) else 0,
              "records_by_status": _group(rows, "status"),
              "issues": _count(data, "issues") if data else 0,
              "overrides": _count(data, "overrides") if data else 0}
    return _artifact_stage("airflow_resolution", data, error, {1},
                           recorded_field="source_fingerprints", current=current,
                           counts=counts, gated=review_gate_active)


def evaluate_model_input_resolution(data, error="", current=None, review_gate_active=False):
    rows = data.get("records", []) if data else []
    counts = {"records": len(rows) if isinstance(rows, list) else 0,
              "records_by_status": _group(rows, "resolution_status"),
              "review_queue": _count(data, "review_queue") if data else 0,
              "affected_components": _count(data, "affected_component_ids") if data else 0}
    return _artifact_stage("model_input_resolution", data, error, {1}, recorded_field="dependency_fingerprints",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_calculator_draft(data, error="", current=None, review_gate_active=False):
    candidates = data.get("candidates", {}) if data else {}
    counts = {"candidates_by_group": {key: len(rows) for key, rows in candidates.items() if isinstance(rows, list)} if isinstance(candidates, dict) else {},
              "review_queue": _count(data, "review_items") if data else 0}
    return _artifact_stage("calculator_draft", data, error, {2}, recorded_field="source_fingerprints",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_calculator_inputs(data, error="", snapshot_count=0, current=None, review_gate_active=False):
    issues = data.get("issues", []) if data else []
    counts = {"resolved_inputs": _count(data, "resolved_inputs") if data else 0,
              "issues_by_status": _group(issues, "status"),
              "included_rooms": _count(data, "included_room_ids") if data else 0,
              "stored_snapshots": snapshot_count or int(bool(data))}
    return _artifact_stage("calculator_input_set", data, error, {2}, recorded_field="source_fingerprints",
                           current=current, counts=counts, gated=review_gate_active)


def evaluate_calculation_readiness(data, error="", review_gate_active=False):
    if error:
        return _status("error", reason=error)
    if not data:
        return _status("gated" if review_gate_active else "absent",
                       reason="page review is incomplete" if review_gate_active else "hourly_load_report.json is absent")
    readiness = data.get("readiness", {})
    reasons = list(data.get("blocked_reasons", []) or [])
    if isinstance(readiness, dict):
        reasons.extend(readiness.get("issues", []) or [])
    groups = _reason_groups(reasons)
    stored = data.get("status") or (readiness.get("status") if isinstance(readiness, dict) else "unknown")
    return _status("present", {"blockers": sum(groups.values()), "blocker_groups": groups},
                   "stored readiness only; no calculation or readiness function was run", "unknown", stored)


def _evaluate_expectations(case, artifacts, proposal_rooms):
    results = []
    expectations = case.get("expectations", {}) if isinstance(case, dict) else {}
    if not isinstance(expectations, dict):
        return results
    building = artifacts.get("building_evidence", {})
    extraction = artifacts.get("calculation_input_evidence", {})
    draft = artifacts.get("calculator_draft", {})
    draft_rows = [row.get("value", {}) for group in (draft.get("candidates", {}) or {}).values()
                  if isinstance(group, list) for row in group if isinstance(row, dict)]
    room_sets = [building.get("spaces", []), extraction.get("candidates", []), proposal_rooms, draft_rows]
    for expected in expectations.get("floors_expected", []) or []:
        label = expected.get("label", "") if isinstance(expected, dict) else str(expected)
        if not building:
            results.append({"status": "not_evaluated", "name": "expected floor", "expected": expected, "actual": None})
        else:
            found = any(row.get("name") == label or row.get("label") == label
                        for row in building.get("levels", []) if isinstance(row, dict))
            results.append({"status": "passed" if found else "failed", "name": "expected floor",
                            "expected": expected, "actual": found})
    for expected in expectations.get("room_labels_expected", []) or []:
        if not any(room_sets):
            results.append({"status": "not_evaluated", "name": "expected room label", "expected": expected, "actual": None})
            continue
        label, page = expected.get("label", ""), expected.get("page")
        found = any((row.get("name") or row.get("label") or
                     (row.get("value", {}).get("name") if isinstance(row.get("value"), dict) else None)) == label
                    and (page is None or row.get("page") == page or page in row.get("pages", []) or page in row.get("source_pages", []))
                    for rows in room_sets for row in rows if isinstance(row, dict))
        results.append({"status": "passed" if found else "failed", "name": "expected room label",
                        "expected": expected, "actual": found})
    for expected in expectations.get("input_candidates_expected", []) or []:
        if not extraction:
            results.append({"status": "not_evaluated", "name": "expected input candidate", "expected": expected, "actual": None})
            continue
        category, page = expected.get("category", ""), expected.get("page")
        found = any(row.get("category") == category
                    and (page is None or row.get("page") == page or page in row.get("source_pages", []))
                    for row in extraction.get("candidates", []) if isinstance(row, dict))
        results.append({"status": "passed" if found else "failed", "name": "expected input candidate",
                        "expected": expected, "actual": found})
    return results


def _load_artifacts(root):
    artifacts, errors = {}, {}
    for name, filename in ARTIFACT_FILES.items():
        path = root / filename
        if path.exists():
            artifacts[name], errors[name] = _read(path)
        else:
            artifacts[name] = {}
    snapshot_dir = root / "calculator_input_sets"
    snapshot_count = sum(1 for path in snapshot_dir.glob("*.json") if path.is_file()) if snapshot_dir.is_dir() else 0
    pointer = artifacts.get("calculator_input_set") or {}
    if pointer and "snapshot_path" in pointer:
        raw_path = pointer.get("snapshot_path")
        relative = Path(raw_path) if isinstance(raw_path, str) else Path(".")
        if (not isinstance(raw_path, str) or not raw_path or relative.is_absolute()
                or len(relative.parts) != 1 or relative.name != raw_path):
            errors["calculator_input_set"] = "calculator_input_set.json: invalid snapshot_path"
            artifacts["calculator_input_set"] = {}
        else:
            snapshot, snapshot_error = _read(snapshot_dir / relative.name)
            if snapshot_error:
                errors["calculator_input_set"] = f"calculator_input_set.json: referenced snapshot unavailable ({snapshot_error})"
                artifacts["calculator_input_set"] = {}
            elif (snapshot.get("input_fingerprint") != pointer.get("input_fingerprint")
                  or snapshot.get("schema_version") != pointer.get("schema_version")
                  or snapshot.get("source_fingerprints", {}) != pointer.get("source_fingerprints", {})):
                errors["calculator_input_set"] = "calculator_input_set.json: pointer disagrees with referenced snapshot"
                artifacts["calculator_input_set"] = {}
            else:
                artifacts["calculator_input_set"] = snapshot
    elif not pointer and snapshot_count:
        # Content-addressed snapshots are historical without the current pointer.
        artifacts["calculator_input_set"] = {}
    return artifacts, {key: val for key, val in errors.items() if val}, snapshot_count


def evaluate_project_pipeline(review_dir, *, project_id="project", case=None):
    root = Path(review_dir)
    artifacts, errors, snapshot_count = _load_artifacts(root)
    ai_input = artifacts.get("ai_input", {}) or {}
    review_ok = ai_input.get("review_status", {}).get("human_reviewed") is True
    review_gate_active = bool(ai_input) and not review_ok
    # Only compare fingerprints with the exact canonicalization used by their
    # producer. A source_fingerprint identifies an upstream dependency; it is
    # not the identity of the artifact that contains it.
    current = {}
    if ai_input:
        current["ai_input"] = drawing_coverage.source_fingerprint(ai_input)
    coverage = artifacts.get("drawing_coverage", {}) or {}
    building = artifacts.get("building_evidence", {}) or {}
    spatial = artifacts.get("spatial_ocr", {}) or {}
    vector = artifacts.get("vector_geometry", {}) or {}
    vision = artifacts.get("vision_response", {}) or {}
    dimension = artifacts.get("dimension_wall_matches", {}) or {}
    confirmation = artifacts.get("geometry_confirmation", {}) or {}
    from backend import ai_preliminary_service
    pipeline_paths = ai_preliminary_service._paths({"review_dir": str(root)})
    # These are the producer's actual canonical input fingerprints, not hashes
    # invented by the scorecard.
    calculation_fingerprints = calculation_extraction.evidence_input_fingerprints(
        ai_input, coverage, spatial, vector, vision, building, dimension, confirmation)
    current.update({key: value for key, value in calculation_fingerprints.items() if key != "ai_input"})
    if ai_input:
        current["ai_input"] = drawing_coverage.source_fingerprint(ai_input)
    calculation_evidence = artifacts.get("calculation_input_evidence", {}) or {}
    if calculation_evidence:
        current["calculation_input_evidence"] = calculator_inputs._fingerprint(
            calculator_inputs._stable(calculation_evidence))
    job, run = artifacts.get("room_inference_job", {}) or {}, artifacts.get("ai_preliminary_run", {}) or {}
    if ai_input:
        from ai.room_inference import fingerprint as room_fingerprint, VERSION as room_version
        current["room_inference_inputs"] = room_fingerprint({"inputs": {
            "ai_input": ai_input, "coverage": coverage, "spatial": spatial, "vector": vector, "vision": vision},
            "version": room_version})
    if job.get("source_fingerprint") and not current.get("room_inference_inputs"):
        current.pop("room_inference_inputs", None)
    proposal = run.get("local_room_inference_proposal", {}) if isinstance(run, dict) else {}
    proposal_rooms = proposal.get("rooms", []) if isinstance(proposal, dict) else []
    try:
        room_use_current = ai_preliminary_service._room_use_sources(pipeline_paths)
        ceiling_current = ai_preliminary_service._ceiling_sources(pipeline_paths)
        internal_current = ai_preliminary_service._internal_gains_sources(pipeline_paths)
        airflow_current = ai_preliminary_service._airflow_sources(pipeline_paths)
    except (OSError, ValueError, TypeError):
        room_use_current = ceiling_current = internal_current = airflow_current = {}
    from ai.calculator_draft import fingerprint as draft_fingerprint
    draft_current = {name: draft_fingerprint(artifacts.get(name, {}) or {}) for name in
                     ("thermal_model", "building_evidence", "drawing_coverage", "thermal_evidence",
                      "evidence_fusion", "calculation_input_evidence")}
    def saved_fingerprint(name):
        data = artifacts.get(name, {}) or {}
        return (data.get("fingerprint") or ai_preliminary.fingerprint(data)) if data else ""
    resolution = artifacts.get("value_resolution", {}) or {}
    value_state = {key: resolution.get(key) for key in (
        "research_consent", "source_pack_version", "research_jobs", "project_sources", "overrides")}
    try:
        from ai import value_resolution
        from ai.research_cache import empty_research_cache
        research_cache = artifacts.get("research_cache", {}) or empty_research_cache()
        model_current = {
            "building_evidence": ai_preliminary.fingerprint(building),
            "vision_response": ai_preliminary.fingerprint(vision),
            "evidence_fusion": ai_preliminary.fingerprint(artifacts.get("evidence_fusion", {}) or {}),
            "manual_placeholder_entities": ai_preliminary.fingerprint(run.get("manual_placeholder_entities", [])),
            "research_cache": ai_preliminary.fingerprint(research_cache),
            "value_resolution_state": value_resolution.fingerprint(value_state),
            "site_location_resolution": ai_preliminary.fingerprint(artifacts.get("site_location_resolution", {}) or {}),
            "site_design_weather_resolution": ai_preliminary.fingerprint(artifacts.get("site_design_weather_resolution", {}) or {}),
            "room_use_resolution": saved_fingerprint("room_use_resolution"),
            "ceiling_volume_resolution": saved_fingerprint("ceiling_volume_resolution"),
            "internal_gains_resolution": saved_fingerprint("internal_gains_resolution"),
            "thermal_surface_resolution": saved_fingerprint("thermal_surface_resolution"),
            "airflow_resolution": saved_fingerprint("airflow_resolution"),
            "ahu_resolution": saved_fingerprint("ahu_resolution"),
            "plant_resolution": saved_fingerprint("plant_resolution"),
            "safety_factor_resolution": saved_fingerprint("safety_factor_resolution"),
        }
    except (ImportError, AttributeError, TypeError, ValueError):
        model_current = {}
    stages = {
        "page_triage": evaluate_page_triage(ai_input, errors.get("ai_input", "")),
        "page_review": evaluate_page_review(ai_input, errors.get("ai_input", "")),
        "drawing_coverage": evaluate_drawing_coverage(coverage, errors.get("drawing_coverage", ""), {"ai_input": current.get("ai_input")}),
        "building_evidence": evaluate_building_evidence(building, errors.get("building_evidence", ""), current),
        "evidence_fusion": evaluate_evidence_fusion(artifacts.get("evidence_fusion", {}) or {}, errors.get("evidence_fusion", ""), current, review_gate_active),
        "calculation_input_evidence": evaluate_calculation_input_evidence(artifacts.get("calculation_input_evidence", {}) or {}, errors.get("calculation_input_evidence", "") or next((errors[key] for key in ("spatial_ocr", "vector_geometry", "vision_response", "dimension_wall_matches", "geometry_confirmation") if key in errors), ""), calculation_fingerprints, review_gate_active),
        "room_inference": evaluate_room_inference(job, run, errors.get("room_inference_job", "") or errors.get("ai_preliminary_run", ""), current, review_gate_active),
        "room_use_resolution": evaluate_room_use_resolution(artifacts.get("room_use_resolution", {}) or {}, errors.get("room_use_resolution", ""), room_use_current, review_gate_active),
        "ceiling_volume_resolution": evaluate_ceiling_volume_resolution(artifacts.get("ceiling_volume_resolution", {}) or {}, errors.get("ceiling_volume_resolution", ""), ceiling_current, review_gate_active),
        "internal_gains_resolution": evaluate_internal_gains_resolution(artifacts.get("internal_gains_resolution", {}) or {}, errors.get("internal_gains_resolution", ""), internal_current, review_gate_active),
        "airflow_resolution": evaluate_airflow_resolution(artifacts.get("airflow_resolution", {}) or {}, errors.get("airflow_resolution", ""), airflow_current, review_gate_active),
        "model_input_resolution": evaluate_model_input_resolution(artifacts.get("model_input_resolution", {}) or {}, errors.get("model_input_resolution", ""), model_current, review_gate_active),
        "calculator_draft": evaluate_calculator_draft(artifacts.get("calculator_draft", {}) or {}, errors.get("calculator_draft", ""), draft_current, review_gate_active),
        "calculator_inputs": evaluate_calculator_inputs(artifacts.get("calculator_input_set", {}) or {}, errors.get("calculator_input_set", ""), snapshot_count, current, review_gate_active),
        "calculation_readiness": evaluate_calculation_readiness(artifacts.get("hourly_load_report", {}) or {}, errors.get("hourly_load_report", ""), review_gate_active),
    }
    expectations = _evaluate_expectations(case or {}, artifacts, proposal_rooms)
    return {"project_id": project_id, "mode": "report_only", "stages": stages,
            "blocker_groups": stages["calculation_readiness"].get("counts", {}).get("blocker_groups", {}),
            "expectations": expectations}


def compare_scorecards(previous, current):
    old = {row.get("project_id"): row for row in previous.get("projects", [])}
    new = {row.get("project_id"): row for row in current.get("projects", [])}
    changes = []
    for project_id in sorted(set(old) | set(new)):
        for stage in STAGE_ORDER:
            before = old.get(project_id, {}).get("stages", {}).get(stage, {})
            after = new.get(project_id, {}).get("stages", {}).get(stage, {})
            a, b = before.get("counts", {}), after.get("counts", {})
            deltas = {key: b.get(key, 0) - a.get(key, 0) for key in set(a) | set(b)
                      if isinstance(a.get(key, 0), (int, float)) and isinstance(b.get(key, 0), (int, float)) and b.get(key, 0) != a.get(key, 0)}
            if before.get("status") != after.get("status") or deltas:
                count_directions = {key: (1 if key in HIGHER_IS_BETTER_COUNTS else
                                          -1 if key in LOWER_IS_BETTER_COUNTS else 0)
                                    for key in deltas}
                improved = ((before.get("status") in {"absent", "gated", "error", "stale"}
                             and after.get("status") == "present")
                            or any(delta * count_directions[key] > 0 for key, delta in deltas.items()))
                worsened = ((before.get("status") == "present"
                             and after.get("status") in {"absent", "gated", "error", "stale"})
                            or any(delta * count_directions[key] < 0 for key, delta in deltas.items()))
                kind = "improved" if improved and not worsened else "worsened" if worsened and not improved else "changed"
                changes.append({"project_id": project_id, "stage": stage, "change": kind,
                                "before": before.get("status", "absent"), "after": after.get("status", "absent"),
                                "count_deltas": dict(sorted(deltas.items())),
                                "count_directions": dict(sorted(count_directions.items()))})
    return changes


def render_portfolio_markdown(report):
    headers = ["Project", *STAGE_ORDER, "Main blocker groups"]
    lines = ["# Pipeline evaluation portfolio", "",
             "Report-only artifact coverage. This measures workflow coverage, not extraction or calculation accuracy, engineering approval or design readiness.", "",
             "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in report.get("projects", []):
        values = [row.get("project_id", "project")] + [row.get("stages", {}).get(stage, {}).get("status", "error") for stage in STAGE_ORDER]
        values.append(", ".join(f"{key} ({count})" for key, count in row.get("blocker_groups", {}).items()) or "—")
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    lines += ["", "## Level evidence coverage", ""]
    for row in report.get("projects", []):
        counts = row.get("stages", {}).get("drawing_coverage", {}).get("counts", {})
        kinds = counts.get("level_candidates_by_kind", {})
        statuses = counts.get("page_level_statuses", [])
        status_text = ", ".join(f"page {item.get('page')}: {item.get('status')}" for item in statuses) or "no page statuses"
        kind_text = ", ".join(f"{kind} {count}" for kind, count in kinds.items()) or "no candidates"
        lines.append(f"- **{row.get('project_id', 'project')}:** candidates by kind: {kind_text}; page status: {status_text}.")
    lines += ["", "## Portfolio state counts", ""]
    for stage in STAGE_ORDER:
        counts = {state: sum(row.get("stages", {}).get(stage, {}).get("status") == state for row in report.get("projects", [])) for state in sorted(ALLOWED_STATES)}
        lines.append(f"- **{stage}:** " + ", ".join(f"{key} {value}" for key, value in counts.items()))
    if "comparison" in report:
        lines += ["", "## Comparison", ""]
        project_changes = report.get("comparison_project_ids", {})
        if project_changes.get("added") or project_changes.get("removed"):
            lines.append("**Project set changed:** added IDs " + ", ".join(project_changes.get("added", []))
                         + "; removed IDs " + ", ".join(project_changes.get("removed", [])) + ".")
        if not report["comparison"]:
            lines.append("No status or directional count changes.")
        else:
            lines += ["| Project | Stage | Change | Before | After | Count deltas |", "| --- | --- | --- | --- | --- | --- |"]
            lines += [f"| {row['project_id']} | {row['stage']} | {row['change']} | {row['before']} | {row['after']} | {row['count_deltas']} |" for row in report["comparison"]]
    lines += ["", "Project IDs are pseudonymous labels, not anonymous identifiers; reports contain aggregate counts and stored statuses only.", ""]
    return "\n".join(lines)
