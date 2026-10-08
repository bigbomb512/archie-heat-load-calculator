"""Versioned, dependency-aware orchestration for Archie runtime skills.

The skill layer coordinates existing evidence and resolver services. It does
not calculate loads or write domain artifacts directly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path

from backend.vision_extraction_service import _atomic_json
from ai.skill_registry import catalog_fingerprint, compose_subskill_instructions, load_subskill_registry
from ai.vision_extraction import select_page_groups


_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "archie_skills_v1.json"
_SUBSKILL_PATH = Path(__file__).resolve().parents[1] / "config" / "archie_subskills_v1.json"
_INSTRUCTIONS_PATH = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.Lock] = {}
_RUNNING: set[str] = set()
_MAX_WORKERS = 4
_GEOMETRY_VECTOR_DPI = 180
LOGGER = logging.getLogger(__name__)
_ROOM_WAIT_SECONDS = 180
_TERMINAL = {"completed", "failed", "blocked", "stale"}
_SAFE_SUBSKILL_ERROR_CODES = {
    "subskill_execution_failed", "subskill_output_invalid", "subskill_citation_unknown_page",
    "subskill_numeric_inference_uncited", "subskill_contract_invalid", "drawing_coverage_missing",
    "room_inference_pending", "room_inference_failed", "room_evidence_missing", "codex_cli_failed",
    "codex_cli_timeout", "codex_cli_unavailable", "skill_provider_empty_output",
    "skill_provider_invalid_json", "subskill_field_type_invalid", "skill_provider_usage_limit",
}
SKILL_PROVIDER_FACTORY = None
# Tests replace this; otherwise the case-file route uses the Codex CLI signed in with ChatGPT.
CASE_FILE_PROVIDER_FACTORY = None
# Job folders whose run hit the ChatGPT plan's usage limit: their remaining sub-skills fail without a call.
_USAGE_STOPPED: dict = {}
_NO_EVIDENCE_PAGES = object()
_ATTEMPT_TEXT_LIMIT = 20_000
# Per-task prompt budget in characters (roughly 4 characters per token). A task
# whose scoped prompt is still larger is blocked before any provider call.
# ARCHIE_SKILL_PROMPT_MAX_CHARS overrides the default for every task.
_PROMPT_BUDGET_CHARS = 80_000
_PROMPT_TEXT_LIMIT = 600
_PROMPT_NOISE_KEYS = frozenset({
    "witness_ids", "source_fingerprint", "source_fingerprints", "record_fingerprint", "resolution_fingerprint",
    "evidence_fingerprint", "fingerprint", "input_fingerprint", "dependency_fingerprints", "pack_fingerprint",
    "taxonomy_fingerprint", "created_at", "updated_at", "generated_at", "started_at", "finished_at",
    "bridge_provenance", "artifact_names", "evidence_artifact_names", "attempt_ref", "output_summary",
})
_DEPENDENCY_PROMPT_FIELDS = ("status", "affected_ids", "citations", "proposal_fields", "unresolved_fields")
_UNVALIDATED_GEOMETRY_KINDS = frozenset({"wall", "dimension"})
_LINE_ROLE_CODES = {"possible_wall_or_dimension": "W", "vector_context": "C"}
# The room-boundary task is the one task that must see a plan's full vector
# line index to cite real line IDs, so it carries a larger explicit budget.
_TASK_PROMPT_BUDGET_CHARS = {"room_boundaries_areas": 120_000}
_ACCEPTED_GEOMETRY_STATUSES = frozenset({"geometry_confirmed", "ai_estimated"})
_VALUE_TARGET_KEYWORDS = {
    "glazing_properties": ("glazing", "window", "opening", "shgc", "solar", "u_value"),
    "construction_matching": ("construction", "u_value", "surface", "wall", "roof", "floor", "ceiling"),
}
# Workflow scopes. "rooms_only" runs the document-mapping and room tasks needed
# to test room identity, boundaries and ceiling heights at a fraction of the
# full workflow's provider usage.
_WORKFLOW_SCOPES = {
    "all": None,
    # PDF review covers evidence discovery for a heat-load calculation. Policy
    # approval and final model reconciliation happen after the evidence review.
    "pdf_review": frozenset({
        "sheet_identity", "revision_scope", "page_relationships", "site_clue_extraction",
        "address_confirmation", "weather_source_matching", "room_identity_use", "room_boundaries_areas",
        "ceiling_height_volume", "occupancy_seating", "lighting_evidence", "equipment_evidence",
        "schedule_evidence", "surface_inventory", "surface_area", "construction_matching",
        "boundary_resolution", "cross_sheet_opening_match", "glazing_properties", "exposure_orientation",
        "solar_source", "shading", "outside_air", "infiltration", "process_exhaust", "make_up_air",
        "airflow_deduplication", "system_detection", "zone_ownership", "air_path_reconciliation",
        "component_inputs", "coil_duty", "plant_detection", "circuit_mapping", "pump_inputs",
        "pipe_effects", "coincident_duty", "information_needs",
    }),
    "rooms_only": frozenset({"sheet_identity", "revision_scope", "page_relationships",
                             "room_identity_use", "room_boundaries_areas", "ceiling_height_volume"}),
}
_DOMAIN_ARTIFACTS = (
    "room_use_resolution.json", "geometry_resolution.json", "ceiling_volume_resolution.json",
    "internal_gains_resolution.json", "site_location_resolution.json", "site_design_weather_resolution.json",
    "thermal_surface_ledger.json", "opening_register.json", "airflow_resolution.json", "ahu_resolution.json",
    "plant_resolution.json", "safety_factor_resolution.json", "value_resolution.json",
    "model_input_resolution.json", "hourly_ai_preliminary_report.json", "hourly_ai_preliminary_ahu_load_report.json",
    "hourly_ai_preliminary_plant_load_report.json",
)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _project_paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "manifest": root / "skill_workflow_run.json",
        "ai_input": root / "ai_input.json",
        "coverage": root / "drawing_coverage.json",
        "spatial": root / "spatial_ocr.json",
        "vector": root / "vector_geometry.json",
        "vision": root / "vision_response.json",
    }


def load_catalog():
    catalog = _read(_CATALOG_PATH, {})
    validate_catalog(catalog)
    validate_subskill_registry(catalog, load_subskill_registry())
    return catalog


def validate_catalog(catalog):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("Unsupported Archie skills catalog schema.")
    skills = catalog.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError("Skills catalog must contain at least one skill.")
    contracts = catalog.get("output_contracts")
    if not isinstance(contracts, dict):
        raise ValueError("Skills catalog must declare output contracts.")
    by_id = {}
    for skill in skills:
        if not isinstance(skill, dict) or not skill.get("id") or skill["id"] in by_id:
            raise ValueError("Skills must have unique non-empty IDs.")
        if not isinstance(skill.get("version"), int) or skill["version"] < 1:
            raise ValueError(f"Skill {skill.get('id', '')} needs a positive version.")
        if not skill.get("purpose") or not skill.get("output_contract") or not skill.get("resolver_handoff"):
            raise ValueError(f"Skill {skill['id']} is missing its purpose or handoff contract.")
        if skill["output_contract"] not in contracts:
            raise ValueError(f"Skill {skill['id']} references an unknown output contract.")
        if not isinstance(skill.get("subskills"), list) or not all(isinstance(item, str) and item for item in skill["subskills"]):
            raise ValueError(f"Skill {skill['id']} must declare bounded subskills.")
        by_id[skill["id"]] = skill
    for skill in skills:
        for dependency in skill.get("depends_on", []):
            if dependency not in by_id:
                raise ValueError(f"Skill {skill['id']} references unknown dependency {dependency}.")
    visiting, visited = set(), set()

    def visit(skill_id):
        if skill_id in visiting:
            raise ValueError("Skills catalog contains a dependency cycle.")
        if skill_id in visited:
            return
        visiting.add(skill_id)
        for dependency in by_id[skill_id].get("depends_on", []):
            visit(dependency)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for skill_id in by_id:
        visit(skill_id)
    enabled_ids = catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", []))
    if not enabled_ids or any(skill_id not in by_id or not by_id[skill_id].get("enabled") for skill_id in enabled_ids):
        raise ValueError("Enabled skill IDs must refer to enabled catalog entries.")
    active = set(enabled_ids)
    def require_dependencies(skill_id):
        for dependency in by_id[skill_id].get("depends_on", []):
            if dependency not in active:
                raise ValueError(f"Enabled skill {skill_id} has inactive prerequisite {dependency}.")
            require_dependencies(dependency)
    for skill_id in enabled_ids:
        require_dependencies(skill_id)
    for skill in skills:
        instruction = skill.get("instruction_file") or catalog.get("instruction_file")
        if instruction and not (_INSTRUCTIONS_PATH / instruction).is_file():
            raise ValueError(f"Skill {skill['id']} references a missing instruction file.")
    instruction = (_INSTRUCTIONS_PATH / catalog.get("instruction_file", "")).read_text(encoding="utf-8")
    required_headings = {"## Shared control policy"}
    for skill in skills:
        required_headings.add(f"## Parent playbook: {skill['id']}")
        required_headings.update(f"### Subskill: {subskill}" for subskill in skill["subskills"])
    missing_headings = sorted(heading for heading in required_headings if heading not in instruction.splitlines())
    if missing_headings:
        raise ValueError("Runtime skill instructions are incomplete: " + ", ".join(missing_headings))
    return True


def validate_subskill_registry(catalog, registry):
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise ValueError("Unsupported Archie subskill registry schema.")
    envelope = registry.get("proposal_envelope", {}).get("required", {})
    required_envelope = {"subskill_id", "subskill_version", "status", "affected_ids", "observations", "inferences", "citations", "confidence", "alternatives", "unresolved_fields", "remediation", "input_fingerprint"}
    if not required_envelope.issubset(envelope):
        raise ValueError("Subskill proposal envelope is incomplete.")
    definitions = registry.get("subskills")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("Subskill registry must contain definitions.")
    by_id = {}
    for row in definitions:
        if not isinstance(row, dict) or not row.get("id") or row["id"] in by_id:
            raise ValueError("Subskills must have unique non-empty IDs.")
        if not isinstance(row.get("version"), int) or row["version"] < 1:
            raise ValueError(f"Subskill {row.get('id', '')} needs a positive version.")
        for key in ("parent", "task", "inputs", "depends_on", "proposal_fields", "constraints", "handoff", "failure"):
            if key not in row:
                raise ValueError(f"Subskill {row['id']} is missing {key}.")
        if not isinstance(row["proposal_fields"], dict) or not row["proposal_fields"]:
            raise ValueError(f"Subskill {row['id']} needs typed proposal fields.")
        by_id[row["id"]] = row
    catalog_ids = {item for skill in catalog["skills"] for item in skill["subskills"]}
    if set(by_id) != catalog_ids:
        missing, extra = catalog_ids - set(by_id), set(by_id) - catalog_ids
        raise ValueError(f"Subskill catalog mismatch (missing={sorted(missing)}, extra={sorted(extra)}).")
    parents = {skill["id"]: skill for skill in catalog["skills"]}
    for subskill in definitions:
        parent = parents.get(subskill["parent"])
        if parent is None or subskill["id"] not in parent["subskills"]:
            raise ValueError(f"Subskill {subskill['id']} has an invalid parent.")
        for dependency in subskill["depends_on"]:
            if dependency not in by_id:
                raise ValueError(f"Subskill {subskill['id']} references unknown dependency {dependency}.")
    visiting, visited = set(), set()

    def visit(subskill_id):
        if subskill_id in visiting:
            raise ValueError("Subskill registry contains a dependency cycle.")
        if subskill_id in visited:
            return
        visiting.add(subskill_id)
        for dependency in by_id[subskill_id]["depends_on"]:
            visit(dependency)
        visiting.remove(subskill_id)
        visited.add(subskill_id)

    for subskill_id in by_id:
        visit(subskill_id)
    enabled_parents = set(catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])))
    enabled_ids = {row["id"] for row in definitions if row["parent"] in enabled_parents}
    for subskill_id in enabled_ids:
        for dependency in by_id[subskill_id]["depends_on"]:
            if dependency not in enabled_ids:
                raise ValueError(f"Enabled subskill {subskill_id} depends on disabled subskill {dependency}.")
    return True


def _source_inputs(paths, catalog):
    return {
        "ai_input": _read(paths["ai_input"], {}),
        "drawing_coverage": _read(paths["coverage"], {}),
        "spatial_ocr": _read(paths["spatial"], {}),
        "vector_geometry": _read(paths["vector"], {}),
        "vision_response": _read(paths["vision"], {}),
        # Consent and selected evidence groups are part of the run identity.
        "vision_extraction_settings": _read(paths["root"] / "vision_extraction_settings.json", {}),
        # The case files come from pass 1 and pass 2: new readings make a new run.
        "page_inventory": _read(paths["root"] / "page_inventory.json", {}).get("pages", {}),
        "page_extraction": {kind: row.get("findings") for kind, row in _read(paths["root"] / "page_extraction.json", {}).items()},
        "catalog": catalog,
        "subskill_registry": load_subskill_registry(),
        "instructions_fingerprint": catalog_fingerprint(),
    }


_SOURCE_FP_CACHE: dict = {}
_SOURCE_FP_LOCK = threading.Lock()  # own lock: callers may already hold _LOCK


def _file_signature(path):
    try:
        stat = Path(path).stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def _source_fingerprint(paths, catalog):
    """Fingerprint of the run's source inputs, reused while none of the input files has changed.

    The inputs include the vector geometry (tens of MB on a real set), so hashing them on every
    status read or calculation costs about half a second each time.
    """
    files = [paths["ai_input"], paths["coverage"], paths["spatial"], paths["vector"], paths["vision"],
             paths["root"] / "vision_extraction_settings.json", paths["root"] / "page_inventory.json",
             paths["root"] / "page_extraction.json"]
    key = (tuple(_file_signature(path) for path in files), _fingerprint(catalog),
           _fingerprint(load_subskill_registry()), catalog_fingerprint())
    root = str(paths["root"])
    with _SOURCE_FP_LOCK:
        cached = _SOURCE_FP_CACHE.get(root)
    if cached and cached[0] == key:
        return cached[1]
    result = _fingerprint(_source_inputs(paths, catalog))
    with _SOURCE_FP_LOCK:
        _SOURCE_FP_CACHE[root] = (key, result)
    return result


def _derived_dependency_fingerprints(paths):
    names = _DOMAIN_ARTIFACTS
    return {name: _fingerprint(_read(paths["root"] / name, {})) for name in names}


def _project_lock(project_id):
    with _LOCK:
        return _PROJECT_LOCKS.setdefault(str(project_id), threading.Lock())


def _skill_map(catalog):
    return {skill["id"]: skill for skill in catalog["skills"]}


def _scoped_subskill_ids(registry, selected_parents, scope):
    """Subskills to run for a scope, closed over their prerequisites."""
    if scope not in _WORKFLOW_SCOPES:
        raise ValueError("Skill workflow scope must be one of: " + ", ".join(sorted(_WORKFLOW_SCOPES)) + ".")
    by_id = {row["id"]: row for row in registry["subskills"]}
    enabled = {row["id"] for row in registry["subskills"] if row["parent"] in selected_parents}
    wanted = _WORKFLOW_SCOPES[scope]
    if wanted is None:
        return enabled
    result, stack = set(), [item for item in wanted if item in enabled]
    while stack:
        skill_id = stack.pop()
        if skill_id in result:
            continue
        result.add(skill_id)
        stack.extend(dep for dep in by_id[skill_id].get("depends_on", []) if dep in enabled)
    return result


def _new_manifest(catalog, source_fp, scope="all"):
    skills = {}
    selected = set(catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])))
    registry = load_subskill_registry()
    in_scope = _scoped_subskill_ids(registry, selected, scope)
    for skill in catalog["skills"]:
        active = skill["id"] in selected and any(row["parent"] == skill["id"] and row["id"] in in_scope
                                                 for row in registry["subskills"])
        skills[skill["id"]] = {
            "version": skill["version"],
            "status": "queued" if active else ("not_in_scope" if skill["id"] in selected else "not_enabled"),
            "started_at": "", "finished_at": "", "output_summary": {}, "artifact_names": [],
            "error_code": "", "remediation": "",
        }
    subskills = registry["subskills"]
    subskill_states = {}
    for subskill in subskills:
        enabled = subskill["parent"] in selected
        active = subskill["id"] in in_scope
        subskill_states[subskill["id"]] = {
            "parent": subskill["parent"], "version": subskill["version"], "prerequisites": list(subskill.get("depends_on", [])),
            "status": "queued" if active else ("not_in_scope" if enabled else "not_enabled"), "input_fingerprint": "",
            "started_at": "", "finished_at": "", "output_summary": {},
            "artifact_names": [], "error_code": "", "remediation": "",
        }
    return {
        "schema_version": 1, "run_id": uuid.uuid4().hex, "workflow_id": catalog["workflow_id"],
        "catalog_id": catalog["catalog_id"], "catalog_fingerprint": _fingerprint(catalog),
        "source_fingerprint": source_fp, "scope": scope, "status": "queued", "created_at": time.time(),
        "updated_at": time.time(), "skills": skills, "subskills": subskill_states,
        "preparation": {"status": "not_started", "artifact_names": [], "error_code": "", "remediation": ""},
        "stale_reasons": [],
    }


def _write_manifest(path, manifest):
    manifest["updated_at"] = time.time()
    _atomic_json(path, manifest)


def _page_rows(coverage):
    pages = coverage.get("pages", []) if isinstance(coverage, dict) else []
    if not isinstance(pages, list) or not pages:
        pages = coverage.get("sheet_register", []) if isinstance(coverage, dict) else []
    if not isinstance(pages, list) or not pages:
        pages = coverage.get("page_roles", []) if isinstance(coverage, dict) else []
    return [row for row in pages if isinstance(row, dict)]


def _wait_room_inference(web, project):
    from backend import room_inference_service

    state = room_inference_service.get(web, project)
    status = state.get("status", "not_started")
    if status in {"not_started", "stale"}:
        state = room_inference_service.post(web, project, {"action": "retry" if status == "stale" else "start"})
        status = state.get("status", state.get("room_inference", {}).get("status", "queued"))
    deadline = time.monotonic() + _ROOM_WAIT_SECONDS
    while status in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.35)
        state = room_inference_service.get(web, project)
        status = state.get("status", state.get("room_inference", {}).get("status", "not_started"))
    if status in {"queued", "running"}:
        raise RuntimeError("room_inference_pending")
    if status != "completed":
        raise RuntimeError("room_inference_failed")
    return state


def _room_artifacts(project):
    root = Path(project["review_dir"])
    files = {
        "room_use": "room_use_resolution.json", "geometry": "geometry_resolution.json",
        "ceiling": "ceiling_volume_resolution.json", "internal_gains": "internal_gains_resolution.json",
    }
    return {key: _read(root / name, {}) for key, name in files.items()}


def _vision_room_entities(paths):
    vision = _read(paths["vision"], {})
    result = vision.get("result", {}) if isinstance(vision, dict) else {}
    rows = result.get("entities", []) if isinstance(result, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("kind") == "room"]


def _source_pages(row):
    pages = set()
    for item in _record_citations(row):
        page = item.get("page", item.get("physical_page"))
        if isinstance(page, int) and page > 0:
            pages.add(page)
    for page in row.get("source_pages", []) if isinstance(row.get("source_pages"), list) else []:
        if isinstance(page, int) and page > 0:
            pages.add(page)
    return sorted(pages)


def _entity_room_id(entity, room_use_rows):
    label = str(entity.get("label", "")).strip().casefold()
    matches = [row for row in room_use_rows if str(row.get("original_label", "")).strip().casefold() == label]
    return matches[0].get("room_id") if len(matches) == 1 else None


def _ensure_room_evidence(web, project):
    room_state = _wait_room_inference(web, project)
    artifacts = _room_artifacts(project)
    missing = [name for key, name in (("room_use", "room_use_resolution.json"), ("geometry", "geometry_resolution.json"),
                                      ("ceiling", "ceiling_volume_resolution.json"), ("internal_gains", "internal_gains_resolution.json"))
               if not artifacts[key]]
    if missing:
        raise RuntimeError("room_evidence_missing")
    return {"candidate_count": int(room_state.get("candidate_count", 0) or 0),
        "model_input": {"status": "deferred_until_geometry_proposals"}, "artifact_names": [
        "room_inference_job.json", "room_use_resolution.json", "geometry_resolution.json",
        "ceiling_volume_resolution.json", "internal_gains_resolution.json", "model_input_resolution.json",
    ]}


def _subskill_records(subskill_id, project):
    paths = _project_paths(project)
    coverage = _read(paths["coverage"], {})
    pages = _page_rows(coverage)
    root = paths["root"]
    if subskill_id in {"sheet_identity", "revision_scope"}:
        records = []
        for row in pages:
            if subskill_id == "sheet_identity":
                records.append({"physical_page": row.get("page"), "drawing_number": row.get("resolved_drawing_number") or row.get("drawing_number") or None,
                    "title": row.get("title") or None, "drawing_type": row.get("detected_type") or row.get("plan_role") or row.get("role") or None,
                    "alternatives": row.get("drawing_number_candidates", []) if isinstance(row.get("drawing_number_candidates"), list) else [],
                    "identity_status": row.get("identity_status", row.get("drawing_number_status", "missing")),
                    "evidence": row.get("classification_evidence", row.get("title_block_excerpts", []))})
            else:
                evidence = row.get("revision_evidence", row.get("title_block_excerpts", []))
                evidence_ids = [str(item.get("source_id") or item.get("excerpt") or item) for item in evidence] if isinstance(evidence, list) else []
                records.append({"page": row.get("page"), "revision": row.get("revision") or row.get("resolved_revision") or None,
                    "issue_date": row.get("issue_date") or None, "status": row.get("revision_status") or row.get("identity_status") or "needs_review",
                    "precedence_citation_ids": evidence_ids, "evidence": evidence})
        return {"page_identities" if subskill_id == "sheet_identity" else "revision_records": records}, ["drawing_coverage.json"]
    if subskill_id == "page_relationships":
        records = coverage.get("page_relationships", []) or coverage.get("cross_sheet_links", [])
        links = []
        for row in records if isinstance(records, list) else []:
            if not isinstance(row, dict):
                continue
            from_page, to_page = row.get("from_page"), row.get("to_page")
            if not isinstance(from_page, int) or not isinstance(to_page, int):
                continue
            basis = row.get("basis", row.get("relationship", ""))
            links.append({"from_page": from_page, "to_page": to_page, "relationship": row.get("relationship", row.get("kind", "related_page")),
                          "evidence": [str(basis)] if basis else []})
        return {"page_links": links}, ["drawing_coverage.json"]

    artifacts = _room_artifacts(project)
    room_use = artifacts["room_use"].get("records", [])
    proofs = artifacts["geometry"].get("room_geometry_proofs", [])
    ceilings = artifacts["ceiling"].get("records", [])
    gains = artifacts["internal_gains"].get("records", [])
    schedules = artifacts["internal_gains"].get("schedules", [])
    rows, artifact_names = [], []
    if subskill_id == "room_identity_use":
        rows = [{"room_id": row.get("room_id"), "original_label": row.get("original_label", ""), "level": row.get("level_name"),
                 "taxonomy_id": row.get("taxonomy_id"), "scope": row.get("space_scope", "unresolved"),
                 "evidence_page_ids": _source_pages(row), "status": row.get("status"), "evidence": row.get("evidence", [])}
                for row in room_use]
        artifact_names = ["room_use_resolution.json"]
        key = "rooms"
    elif subskill_id == "room_boundaries_areas":
        rows = []
        for row in proofs:
            if not isinstance(row, dict) or not isinstance(row.get("source_page"), int):
                continue
            calibration = row.get("calibration") if isinstance(row.get("calibration"), dict) else {}
            links = row.get("dimension_bindings", row.get("dimension_links", []))
            links = links if isinstance(links, list) else []
            dimensions = row.get("dimensions", [])
            if not dimensions:
                dimensions = [{"dimension_id": item.get("dimension_id"), "value_mm": item.get("value_mm"),
                    "measured_span_start_px": item.get("measured_span_start_px"),
                    "measured_span_end_px": item.get("measured_span_end_px")} for item in links if isinstance(item, dict)]
            score = row.get("confidence_score")
            if not isinstance(score, (int, float)):
                score = 0.85 if str(row.get("confidence", "")).casefold() == "high" else 0.6
            rows.append({"room_id": row.get("room_source_id") or row.get("room_geometry_id") or row.get("proof_id", ""),
                "label": row.get("room_label", ""), "page": row["source_page"], "level": row.get("level_name", ""),
                "boundary_ref": row.get("geometry_proof_id", row.get("proof_id")),
                "boundary_points_px": row.get("boundary_points_px", []),
                "wall_ids": row.get("wall_ids", row.get("ordered_wall_ids", [])),
                "walls": row.get("walls", []), "dimension_ids": row.get("dimension_ids", []),
                "dimensions": dimensions, "dimension_links": links,
                "scale_mm_per_px": calibration.get("mm_per_px"), "area_m2": row.get("area_m2"),
                "formula": row.get("formula", "shoelace_area_px2 × (mm_per_px²) ÷ 1,000,000"),
                "calibration": calibration, "source_crop": row.get("source_crop"),
                "independent_witnesses": row.get("independent_witnesses", []), "confidence": score,
                "conflicts": row.get("conflicts", []), "unresolved_fields": row.get("unresolved_fields", []),
                "alternatives": row.get("alternatives", []),
                "status": row.get("status"), "evidence": row.get("evidence", [])})
        artifact_names, key = ["geometry_resolution.json"], "geometry_candidates"
    elif subskill_id == "ceiling_height_volume":
        rows = [{"room_id": row.get("room_id"), "height_mm": row.get("ceiling_height_mm"), "scope": row.get("source_type", row.get("origin", "unresolved")),
                 "source_page": next(iter(_source_pages(row)), None), "volume_m3": row.get("volume_m3"),
                 "formula": row.get("formula", row.get("derivation", {}).get("formula", "")) if isinstance(row.get("derivation", {}), dict) else row.get("formula", ""),
                 "status": row.get("status"), "evidence": row.get("evidence", [])} for row in ceilings]
        artifact_names, key = ["ceiling_volume_resolution.json"], "heights"
    elif subskill_id in {"occupancy_seating", "lighting_evidence", "equipment_evidence"}:
        field_name = {"occupancy_seating": "occupancy_count", "lighting_evidence": "lighting_load_w", "equipment_evidence": "equipment"}[subskill_id]
        key = {"occupancy_seating": "occupancy", "lighting_evidence": "lighting", "equipment_evidence": "equipment"}[subskill_id]
        entities = _vision_room_entities(paths)
        if subskill_id == "occupancy_seating":
            rows = []
            for room in gains:
                field = room.get("fields", {}).get(field_name, {}) or {}
                entity = next((item for item in entities if _entity_room_id(item, room_use) == room.get("room_id")), {})
                count = entity.get("occupancy_count", entity.get("seat_count", entity.get("workstation_count", field.get("value"))))
                rows.append({"room_id": room.get("room_id"), "count": count if isinstance(count, int) else None,
                    "basis": "counted_pdf_evidence" if count is not None else field.get("origin", "unresolved"),
                    "density_record_id": field.get("source_id") or None, "rounding": field.get("rationale", ""), "status": room.get("status"),
                    "evidence": entity.get("evidence", field.get("evidence", room.get("evidence", []))),
                    "unresolved_fields": [] if count is not None else ["occupancy_count"]})
        elif subskill_id == "lighting_evidence":
            rows = []
            for entity in entities:
                room_id = _entity_room_id(entity, room_use)
                for fixture in entity.get("lighting_fixtures", []) if isinstance(entity.get("lighting_fixtures"), list) else []:
                    if isinstance(fixture, dict):
                        rows.append({"room_id": room_id or "", "fixture_id": fixture.get("fixture_id") or None,
                            "quantity": fixture.get("quantity"), "wattage_w": fixture.get("wattage_w"),
                            "basis": "direct_pdf_fixture_evidence", "citation_ids": [str(fixture.get("page", entity.get("page", "")))],
                            "status": "proposed", "page": fixture.get("page", entity.get("page")), "drawing_number": fixture.get("drawing_number", entity.get("drawing_number", "")),
                            "excerpt": fixture.get("excerpt", entity.get("excerpt", "")), "unresolved_fields": [key for key in ("quantity", "wattage_w") if fixture.get(key) is None]})
            for room in gains:
                if not any(item.get("room_id") == room.get("room_id") for item in rows):
                    field = room.get("fields", {}).get(field_name, {}) or {}
                    rows.append({"room_id": room.get("room_id"), "fixture_id": None, "quantity": None, "wattage_w": field.get("value"),
                        "basis": field.get("origin", "unresolved"), "citation_ids": [], "status": room.get("status"),
                        "evidence": field.get("evidence", room.get("evidence", [])), "unresolved_fields": [field_name] if field.get("value") is None else []})
        else:
            rows = []
            for entity in entities:
                room_id = _entity_room_id(entity, room_use)
                for item in entity.get("equipment", []) if isinstance(entity.get("equipment"), list) else []:
                    if isinstance(item, dict):
                        rows.append({"room_id": room_id or "", "equipment_id": item.get("equipment_id") or None,
                            "name": item.get("name", ""), "model": item.get("model") or None, "quantity": item.get("quantity"),
                            "rated_input_w": item.get("rated_input_w", item.get("watts")), "heat_to_space_factor": item.get("heat_to_space_factor"),
                            "status": "proposed", "page": item.get("page", entity.get("page")), "drawing_number": item.get("drawing_number", entity.get("drawing_number", "")),
                            "excerpt": item.get("excerpt", entity.get("excerpt", "")), "unresolved_fields": [key for key in ("rated_input_w", "heat_to_space_factor") if item.get(key) is None]})
            for room in gains:
                if not any(item.get("room_id") == room.get("room_id") for item in rows):
                    field = room.get("fields", {}).get(field_name, {}) or {}
                    values = field.get("value") if isinstance(field.get("value"), list) else []
                    for item in values:
                        if isinstance(item, dict):
                            rows.append({"room_id": room.get("room_id"), "equipment_id": item.get("equipment_id"), "name": item.get("name", ""),
                                "model": item.get("model"), "quantity": item.get("quantity"), "rated_input_w": item.get("rated_input_w"),
                                "heat_to_space_factor": item.get("heat_to_space_factor"), "status": item.get("status"), "evidence": item.get("evidence", [])})
        artifact_names = ["internal_gains_resolution.json"]
    elif subskill_id == "schedule_evidence":
        rows = []
        for schedule in schedules if isinstance(schedules, list) else []:
            profiles = schedule.get("day_profiles", {})
            for day_type, factors in profiles.items() if isinstance(profiles, dict) else []:
                rows.append({"room_id": schedule.get("room_id", ""), "day_type": day_type, "profile_id": schedule.get("schedule_id"),
                    "hours_text": schedule.get("source", ""), "hourly_factors": factors if isinstance(factors, list) else None,
                    "status": schedule.get("status"), "evidence": schedule.get("evidence", [])})
        artifact_names = ["internal_gains_resolution.json"]
        key = "schedules"
    else:
        # Non-pilot domains receive a typed, bounded evidence bundle from the
        # existing authoritative artifacts. The skill proposes; resolvers own
        # all domain writes.
        parent_by_subskill = {
            "site_clue_extraction": ("site_location_resolution.json", "building_evidence.json"),
            "address_confirmation": ("site_location_resolution.json",),
            "weather_source_matching": ("site_design_weather_resolution.json", "site_location_resolution.json"),
            "surface_inventory": ("thermal_surface_ledger.json", "geometry_resolution.json"),
            "surface_area": ("thermal_surface_ledger.json", "geometry_resolution.json", "opening_register.json"),
            "construction_matching": ("thermal_surface_ledger.json", "value_resolution.json"),
            "boundary_resolution": ("thermal_surface_ledger.json", "site_design_weather_resolution.json"),
            "cross_sheet_opening_match": ("opening_register.json", "drawing_coverage.json"),
            "glazing_properties": ("opening_register.json", "value_resolution.json"),
            "exposure_orientation": ("opening_register.json", "site_location_resolution.json"),
            "solar_source": ("site_design_weather_resolution.json", "opening_register.json"),
            "shading": ("opening_register.json",),
            "outside_air": ("airflow_resolution.json", "internal_gains_resolution.json"),
            "infiltration": ("airflow_resolution.json", "ceiling_volume_resolution.json"),
            "process_exhaust": ("airflow_resolution.json", "internal_gains_resolution.json"),
            "make_up_air": ("airflow_resolution.json",),
            "airflow_deduplication": ("airflow_resolution.json", "ahu_resolution.json"),
            "system_detection": ("ahu_resolution.json", "vision_response.json"),
            "zone_ownership": ("ahu_resolution.json", "airflow_resolution.json"),
            "air_path_reconciliation": ("ahu_resolution.json", "airflow_resolution.json"),
            "component_inputs": ("ahu_resolution.json", "value_resolution.json"),
            "coil_duty": ("ahu_resolution.json", "hourly_ai_preliminary_ahu_load_report.json"),
            "plant_detection": ("plant_resolution.json", "vision_response.json"),
            "circuit_mapping": ("plant_resolution.json", "ahu_resolution.json"),
            "pump_inputs": ("plant_resolution.json",), "pipe_effects": ("plant_resolution.json",),
            "coincident_duty": ("plant_resolution.json", "hourly_ai_preliminary_ahu_load_report.json"),
            "policy_source": ("safety_factor_resolution.json", "project_brief.json"),
            "double_application_check": ("safety_factor_resolution.json", "design_requirements.json"),
            "final_rollup": ("safety_factor_resolution.json", "hourly_ai_preliminary_report.json"),
            "shared_value_register": _DOMAIN_ARTIFACTS,
            "dependency_freshness": _DOMAIN_ARTIFACTS,
            "provenance_audit": _DOMAIN_ARTIFACTS,
            "report_readiness": _DOMAIN_ARTIFACTS,
        }
        rows = []
        for name in parent_by_subskill.get(subskill_id, ()):
            value = _read(root / name, None)
            if isinstance(value, dict) and value:
                records = value.get("records", value.get("entities", value.get("room_geometry_proofs", [])))
                if isinstance(records, list):
                    rows.extend({"source_artifact": name, **row} for row in records
                                if isinstance(row, dict) and _generic_record_relevant(subskill_id, name, row))
                else:
                    rows.append({"source_artifact": name, "status": value.get("status", "present"),
                                 "fingerprint": _fingerprint(value)})
        subskill = next((row for row in load_subskill_registry()["subskills"] if row["id"] == subskill_id), {})
        key = next(iter(subskill.get("proposal_fields", {})), "records")
        artifact_names = list(parent_by_subskill.get(subskill_id, ()))
    return {key: rows if isinstance(rows, list) else []}, artifact_names


def _record_id(row):
    return next((row.get(key) for key in ("room_id", "surface_id", "opening_id", "ahu_id", "plant_id", "circuit_id", "page", "physical_page", "id") if row.get(key) is not None), "")


def _record_citations(row):
    refs = row.get("citations", row.get("evidence", []))
    if not isinstance(refs, list):
        refs = []
    page = row.get("page") or row.get("physical_page") or row.get("source_page")
    if page:
        refs = refs + [{"page": page, "drawing_number": row.get("drawing_number", ""), "excerpt": row.get("excerpt", "")}]
    for page_key in ("from_page", "to_page"):
        if row.get(page_key):
            refs = refs + [{"page": row[page_key], "excerpt": row.get("basis", row.get("relationship", ""))}]
    for page in row.get("evidence_page_ids", []) if isinstance(row.get("evidence_page_ids"), list) else []:
        refs = refs + [{"page": page}]
    return [item for item in refs if isinstance(item, dict) and (item.get("page") or item.get("physical_page") or item.get("source_id") or item.get("url"))]


def _build_subskill_proposal(subskill, evidence_fields, artifact_names, dependencies, source_fp):
    records = next(iter(evidence_fields.values()), [])
    records = records if isinstance(records, list) else []
    ids, citations, unresolved, alternatives, remediations, inferences = [], [], set(), [], [], []
    confidence_values, statuses = [], []
    for row in records:
        if not isinstance(row, dict):
            continue
        record_id = _record_id(row)
        if record_id:
            ids.append(str(record_id))
        refs = _record_citations(row)
        citation_ids = [_fingerprint(ref)[:16] for ref in refs]
        citations.extend(refs)
        status = str(row.get("status", row.get("identity_status", "")))
        if status:
            statuses.append(status)
        unresolved.update(str(item) for item in row.get("unresolved_fields", []) if item)
        for field in ("conflicts", "alternatives"):
            value = row.get(field, [])
            if isinstance(value, list):
                alternatives.extend(value)
        remedy = row.get("remediation")
        if isinstance(remedy, str) and remedy:
            remediations.append(remedy)
        elif isinstance(remedy, list):
            remediations.extend(str(item) for item in remedy if item)
        confidence = row.get("confidence_score", row.get("confidence"))
        if isinstance(confidence, (int, float)) and 0 <= confidence <= 1:
            confidence_values.append(float(confidence))
        value = row.get("value")
        if value is not None and row.get("field"):
            inferences.append({"field": row["field"], "value": value, "unit": row.get("unit"),
                "method": row.get("origin", "existing_resolver"), "formula": row.get("formula", ""),
                "unrounded_operands": row.get("operands", {}), "confidence": confidence, "citation_ids": citation_ids})
    if not records:
        status = "needs_review"
    elif any(item in {"blocked", "needs_review", "conflict", "stale", "ambiguous", "missing"} for item in statuses) or unresolved:
        status = "needs_review"
    elif all(item == "excluded" for item in statuses if item):
        status = "excluded"
    elif any(item in {"provisional", "ai_estimated", "proposed"} for item in statuses):
        status = "provisional"
    else:
        status = "resolved"
    proposal_fingerprint = _fingerprint({"source": source_fp, "subskill": subskill, "evidence": evidence_fields,
        "dependencies": {key: value.get("input_fingerprint") for key, value in dependencies.items()}})
    return {
        "subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": status,
        "affected_ids": sorted(set(ids)), "observations": [{"record_count": len(records), "subskill": subskill["id"]}],
        "inferences": inferences, "citations": citations, "confidence": sum(confidence_values) / len(confidence_values) if confidence_values else None,
        "alternatives": alternatives, "unresolved_fields": sorted(unresolved), "remediation": sorted(set(remediations)),
        "input_fingerprint": proposal_fingerprint, "proposal_fields": evidence_fields,
        "artifact_names": artifact_names,
    }


class SubskillValidationError(ValueError):
    """A stable public error code plus safe structured validation detail."""

    def __init__(self, code, check, path="", detail=""):
        super().__init__(code)
        self.code = code
        self.check = check
        self.path = path
        self.detail = str(detail or "")[:1000]


def _validation_error(check, path, detail, code="subskill_output_invalid"):
    raise SubskillValidationError(code, check, path, detail)


def _validate_subskill_output(subskill, result, registry, allowed_pages=None):
    envelope = set(registry["proposal_envelope"]["required"])
    if not isinstance(result, dict):
        _validation_error("envelope_object", "$", "Expected a JSON object.")
    missing = sorted(envelope - set(result))
    if missing:
        _validation_error("envelope_missing_keys", missing[0] if len(missing) == 1 else "$",
                          f"Missing required keys: {', '.join(missing)}.")
    if result.get("subskill_id") != subskill["id"]:
        _validation_error("subskill_id_mismatch", "subskill_id", f"Expected {subskill['id']}; got {result.get('subskill_id')!r}.")
    if result.get("subskill_version") != subskill["version"]:
        _validation_error("subskill_version_mismatch", "subskill_version", f"Expected {subskill['version']}; got {result.get('subskill_version')!r}.")
    if result.get("status") not in {"resolved", "provisional", "needs_review", "blocked", "excluded", "not_applicable"}:
        _validation_error("status_invalid", "status", "Expected one of the declared proposal statuses.")
    for key in ("citations", "affected_ids", "observations", "inferences", "alternatives", "unresolved_fields", "remediation"):
        if not isinstance(result.get(key), list):
            _validation_error("envelope_field_type", key, "Expected a JSON array.")
    confidence = result.get("confidence")
    if confidence is not None:
        normalized, confidence_note = _normalize_confidence(confidence)
        result["confidence"] = normalized
        if confidence_note:
            result["status"] = "needs_review"
            if "confidence" not in result.setdefault("unresolved_fields", []):
                result["unresolved_fields"].append("confidence")
            result.setdefault("remediation", []).append(confidence_note)
    fingerprint = result.get("input_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        _validation_error("input_fingerprint_invalid", "input_fingerprint", "Expected a 64-character lowercase SHA-256 fingerprint.")
    for index, citation in enumerate(result["citations"]):
        if not isinstance(citation, dict):
            _validation_error("citation_object", f"citations[{index}]", "Expected a citation object.")
        # Accept the explicit citation spelling used by runtime skill prompts as
        # well as the legacy aliases used by existing resolver artifacts.
        cited_pages = [citation.get(key) for key in ("page", "physical_page", "physical_pdf_page")
                       if type(citation.get(key)) is int]
        physical_pages = citation.get("physical_pages")
        if physical_pages is not None:
            if not isinstance(physical_pages, list) or not physical_pages or any(type(page) is not int for page in physical_pages):
                _validation_error("citation_pages_invalid", f"citations[{index}].physical_pages", "Expected a non-empty list of integer physical page numbers.")
            cited_pages.extend(physical_pages)
        if not cited_pages and not any(citation.get(key) for key in ("source_id", "url")):
            _validation_error("citation_reference_missing", f"citations[{index}]", "Citation needs a physical page, source_id, or URL.")
        if allowed_pages is not None and any(page not in allowed_pages for page in cited_pages):
            unknown = sorted(page for page in cited_pages if page not in allowed_pages)
            _validation_error("citation_page_missing", f"citations[{index}]", f"Physical pages are not in the selected packet: {unknown}.", "subskill_citation_unknown_page")
    for index, inference in enumerate(result.get("inferences", [])):
        if not isinstance(inference, dict) or not inference.get("field"):
            _validation_error("inference_shape", f"inferences[{index}]", "Each inference must be an object with a field name.")
        value = inference.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not inference.get("citation_ids") or not inference.get("method"):
                _validation_error("numeric_inference_citation", f"inferences[{index}]", "Numeric inference needs citation_ids and a method.", "subskill_numeric_inference_uncited")
    def contains_numeric(value):
        if isinstance(value, dict):
            return any(contains_numeric(item) for key, item in value.items()
                       if key not in {"page", "physical_page", "source_page", "confidence"})
        if isinstance(value, list):
            return any(contains_numeric(item) for item in value)
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if contains_numeric(result.get("proposal_fields", {})) and not result.get("citations"):
        _validation_error("proposal_numeric_citation", "proposal_fields", "Numeric proposal values require at least one citation.", "subskill_numeric_inference_uncited")
    declared = set(subskill["proposal_fields"])
    values = result.get("proposal_fields", {})
    if not isinstance(values, dict):
        _validation_error("proposal_fields_object", "proposal_fields", "Expected a JSON object.")
    if declared != set(values):
        missing, unexpected = sorted(declared - set(values)), sorted(set(values) - declared)
        _validation_error("proposal_fields_mismatch", "proposal_fields", f"Missing fields: {missing}; unexpected fields: {unexpected}.")
    not_applicable = result.get("status") == "not_applicable"
    for field, descriptor in subskill["proposal_fields"].items():
        value = values[field]
        if not _matches_contract_type(value, descriptor) and not (not_applicable and descriptor.startswith("object") and value == {}):
            _validation_error("field_type_invalid", f"proposal_fields.{field}", f"Expected {descriptor}; got {type(value).__name__}.", "subskill_field_type_invalid")
        if not_applicable and descriptor.startswith("object") and value == {}:
            continue
        item_types = _contract_object_fields(descriptor)
        if item_types is not None:
            rows = value if isinstance(value, list) else [value]
            for row_index, row in enumerate(rows):
                if not isinstance(row, dict):
                    _validation_error("field_item_object", f"proposal_fields.{field}[{row_index}]", "Expected an object.", "subskill_field_type_invalid")
                for key, item_type in item_types.items():
                    if key not in row or not _matches_contract_type(row[key], item_type):
                        _validation_error("field_item_type_invalid", f"proposal_fields.{field}[{row_index}].{key}", f"Expected {item_type}; got {type(row.get(key)).__name__}.", "subskill_field_type_invalid")
    if result.get("status") == "not_applicable" and (result["affected_ids"] or result["citations"] or result["inferences"]):
        _validation_error("not_applicable_payload", "status", "not_applicable proposals cannot carry affected IDs, citations, or inferences.")
    return result


def _normalize_confidence(value):
    """Normalize common AI confidence formats; unknown forms stay unscored."""
    if isinstance(value, str):
        labels = {"high": 0.85, "medium": 0.60, "low": 0.30}
        label = value.strip().lower()
        if label in labels:
            return labels[label], "AI confidence was supplied as a qualitative label; review this classification."
        try:
            value = float(label.rstrip("%")) / (100 if label.endswith("%") else 1)
        except ValueError:
            return None, "AI confidence was not interpretable; review this classification."
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, "AI confidence was not interpretable; review this classification."
    if 0 <= value <= 1:
        return float(value), ""
    if 1 < value <= 100:
        return float(value) / 100.0, "AI confidence was expressed as a percentage and normalized; review this classification."
    return None, "AI confidence was outside the supported range; review this classification."


def _contract_object_fields(descriptor):
    if not isinstance(descriptor, str) or "<{" not in descriptor or not descriptor.endswith("}>"):
        return None
    body = descriptor.split("<{", 1)[1][:-2]
    fields = {}
    for item in body.split(","):
        key, separator, type_name = item.partition(":")
        if not separator or not key.strip() or not type_name.strip():
            raise ValueError("subskill_contract_invalid")
        fields[key.strip()] = type_name.strip()
    return fields


def _matches_contract_type(value, type_name):
    if not isinstance(type_name, str):
        return False
    if type_name.startswith("array<{"):
        return isinstance(value, list)
    if type_name.startswith("object<{"):
        return isinstance(value, dict)
    alternatives = type_name.split("|")
    if value is None:
        return "null" in alternatives
    for candidate in alternatives:
        if candidate == "null":
            continue
        if candidate.endswith("[]"):
            element_type = candidate[:-2]
            if isinstance(value, list) and all(_matches_contract_type(item, element_type) for item in value):
                return True
        elif candidate in {"array", "array<object>"} and isinstance(value, list):
            return True
        elif candidate == "object" and isinstance(value, dict):
            return True
        elif candidate == "string" and isinstance(value, str):
            return True
        elif candidate == "boolean" and isinstance(value, bool):
            return True
        elif candidate == "int" and type(value) is int:
            return True
        elif candidate == "number" and type(value) in {int, float} and math.isfinite(value):
            return True
        elif candidate == "timestamp" and isinstance(value, (str, int, float)):
            return True
    return False


def _consented_page_ids(paths):
    settings = _read(paths["root"] / "vision_extraction_settings.json", {})
    if not settings.get("owner_opt_in"):
        return set()
    groups = select_page_groups(_read(paths["ai_input"], {}), _read(paths["coverage"], {}))
    selected_ids = set(settings.get("selected_group_ids", [])) or {row["group_id"] for row in groups}
    return {page.get("page") for group in groups if group.get("group_id") in selected_ids
            for page in group.get("pages", []) if isinstance(page.get("page"), int)}


def _safe_page_index(paths, allowed_pages):
    rows = _page_rows(_read(paths["coverage"], {}))
    safe_keys = ("page", "drawing_number", "resolved_drawing_number", "title", "detected_type", "plan_role",
                 "role", "level_name", "revision", "resolved_revision", "identity_status", "classification_evidence",
                 "title_block_excerpts", "structured_text", "relevance", "related_pages")
    return [{key: row[key] for key in safe_keys if key in row} for row in rows
            if row.get("page") in allowed_pages]


def _only_cited_pages(value, allowed_pages):
    if isinstance(value, list):
        filtered = []
        for item in value:
            if isinstance(item, dict):
                page_refs = [item.get(key) for key in ("page", "physical_page", "source_page") if isinstance(item.get(key), int)]
                embedded_refs = item.get("evidence_page_ids", item.get("source_pages", []))
                if isinstance(embedded_refs, list):
                    page_refs.extend(ref for ref in embedded_refs if isinstance(ref, int))
                if page_refs and not set(page_refs).intersection(allowed_pages):
                    continue
            filtered.append(_only_cited_pages(item, allowed_pages))
        return filtered
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in {"path", "file_path", "local_path", "image_path", "source_pdf_path", "api_key", "token", "secret"}:
                continue
            if key in {"evidence_page_ids", "source_pages", "pages"} and isinstance(item, list):
                result[key] = [page for page in item if not isinstance(page, int) or page in allowed_pages]
            else:
                result[key] = _only_cited_pages(item, allowed_pages)
        return result
    return value


def _shared_evidence_packet(subskill, project):
    paths = _project_paths(project)
    relevant, artifact_names = _subskill_records(subskill["id"], project)
    vision = _read(paths["vision"], {})
    result = vision.get("result", {}) if isinstance(vision, dict) else {}
    allowed_pages = _consented_page_ids(paths)
    shared = {
        "page_index": _safe_page_index(paths, allowed_pages),
        "task_evidence": relevant,
        "vision_entities": result.get("entities", []) if isinstance(result, dict) else [],
        "room_geometry_candidates": result.get("room_geometry_candidates", []) if isinstance(result, dict) else [],
        "geometry_pages": result.get("geometry_pages", []) if isinstance(result, dict) else [],
        "air_side_candidates": result.get("air_side_candidates", []) if isinstance(result, dict) else [],
        "plant_candidates": result.get("plant_candidates", []) if isinstance(result, dict) else [],
    }
    if subskill["id"] == "room_boundaries_areas":
        # Room-boundary inference must see the detected room identities and the
        # actual dimension/vector evidence that the deterministic geometry
        # resolver will validate.  Previously this task received only existing
        # geometry proofs (an empty list on first run), so it was asked to
        # produce proofs without being given the proposed rooms or plan data.
        run = _read(paths["root"] / "ai_preliminary_run.json", {})
        room_proposal = run.get("local_room_inference_proposal", {})
        room_rows = room_proposal.get("rooms", []) if isinstance(room_proposal, dict) else []
        room_use = _read(paths["root"] / "room_use_resolution.json", {})
        room_use_by_id = {row.get("room_id"): row for row in room_use.get("records", []) if isinstance(row, dict)}
        shared["room_candidates"] = []
        for room in room_rows:
            if not isinstance(room, dict):
                continue
            classification = room_use_by_id.get(room.get("room_id"), {})
            shared["room_candidates"].append({
                "room_id": room.get("room_id"), "label": room.get("label"),
                "level_name": room.get("level_name"), "source_pages": room.get("source_pages", []),
                "evidence": room.get("evidence", []), "room_use_category": classification.get("taxonomy_id", ""),
                "preliminary_profile_id": classification.get("preliminary_profile_id", ""),
                "space_scope": classification.get("space_scope", "unresolved"),
                "room_use_status": classification.get("status", "needs_review"),
            })
        allowed = set(allowed_pages)
        coverage = _read(paths["coverage"], {})
        geometry_page_rows = [row for row in coverage.get("page_roles", [])
            if isinstance(row, dict) and row.get("page") in allowed
            and row.get("geometry_eligible") is not False
            and row.get("proposed_role") not in {"reference", "3d_render", "3d_reference", "legend_or_general_notes"}]
        room_pages = {page for room in shared["room_candidates"] for page in room.get("source_pages", []) if page in allowed}
        linked_pages = set(room_pages)
        for link in coverage.get("page_relationships", []) or coverage.get("cross_sheet_links", []):
            if not isinstance(link, dict):
                continue
            left, right = link.get("from_page"), link.get("to_page")
            if left in room_pages and right in allowed:
                linked_pages.add(right)
            if right in room_pages and left in allowed:
                linked_pages.add(left)
        role_priority = {"main_floor_plan": 0, "primary_geometry_plan": 0, "floor_plan": 1,
                         "supporting_geometry_plan": 2, "dimension_plan": 2}
        geometry_page_rows.sort(key=lambda row: (
            0 if row.get("page") in linked_pages else 1,
            role_priority.get(row.get("proposed_role"), 3),
            0 if row.get("scale_status") == "confirmed" else 1,
            row.get("page", 0),
        ))
        geometry_page_numbers = {row.get("page") for row in geometry_page_rows[:5]}
        vectors = _read(paths["vector"], {})
        vector_pages = []
        for page in (vectors.get("geometry_key_points", {}) or {}).get("pages", []):
            if not isinstance(page, dict) or page.get("page") not in geometry_page_numbers:
                continue
            # Keep stable IDs and endpoints so an AI-proposed boundary can be
            # checked against the extracted vector evidence. Do not include
            # the full page's rendering metadata or unrelated line payloads.
            lines = [row for row in page.get("line_candidates", []) if isinstance(row, dict)]
            lines.sort(key=lambda row: (
                0 if any(word in str(row.get("candidate_role_hint", "")).casefold()
                         for word in ("wall", "partition", "boundary")) else 1,
                -(float(row.get("length_px", 0) or 0)), str(row.get("candidate_id", "")),
            ))
            primary_geometry_page = any(
                item.get("page") == page.get("page") and item.get("page") in allowed
                and (item.get("proposed_role") in {"main_floor_plan", "primary_geometry_plan"}
                     or "dimension plan" in str(item.get("title", "")).casefold())
                for item in geometry_page_rows
            )
            vector_pages.append({
                "page": page.get("page"), "plan_role": page.get("plan_role", ""),
                "coordinate_frame": page.get("coordinate_systems", {}).get("image_px", {}),
                "line_schema": ("Each line is [candidate_id, x1, y1, x2, y2, role]. Coordinates are whole image pixels in the "
                                "full-page vector screenshot coordinate_frame. role: W = possible wall or dimension line, "
                                "C = neutral vector context; any other value is the extractor's role text."),
                # The dimension plan has hundreds of neutral vector candidates.
                # Include the complete primary-plan index so the AI can select
                # real IDs instead of inventing semantic wall IDs. Other linked
                # pages need only their dimensions/labels and are not used for
                # primary boundary tracing. Whole-pixel coordinates are well
                # inside the resolver's endpoint-matching tolerance.
                "line_candidates": [_compact_line(row) for row in (lines if primary_geometry_page else lines[:0])],
                "dimension_candidates": [_compact_dimension(row) for row in page.get("dimension_candidates", [])[:80]
                                         if isinstance(row, dict)],
                "room_label_candidates": page.get("room_label_candidates", [])[:100],
            })
        shared["vector_geometry_pages"] = vector_pages
        dimension_matches = _read(paths["root"] / "dimension_wall_matches.json", {})
        shared["dimension_evidence"] = [
            {key: page.get(key) for key in ("page", "summary", "dimension_span_candidates", "dimension_wall_links")}
            for page in dimension_matches.get("pages", [])
            if isinstance(page, dict) and page.get("page") in geometry_page_numbers
        ]
        spatial = _read(paths["spatial"], {})
        shared["spatial_room_evidence"] = [
            {**{key: page.get(key) for key in ("page", "detected_type", "scale_candidates", "dimension_candidates")},
             "room_label_schema": "Each label is [text, x0, y0, x1, y1] in whole PDF points from the spatial OCR page.",
             "room_label_candidates": [_compact_label(item) for item in page.get("room_label_candidates", []) or []
                                       if isinstance(item, dict)]}
            for page in spatial.get("pages", [])
            if isinstance(page, dict) and page.get("page") in geometry_page_numbers
        ]
        shared["drawing_page_evidence"] = [
            {key: row.get(key) for key in ("page", "title", "drawing_number", "proposed_role", "level_name", "scale_candidates", "scale_status", "geometry_eligible", "page_group")}
            for row in coverage.get("page_roles", [])
            if isinstance(row, dict) and row.get("page") in geometry_page_numbers
        ]
    shared = _only_cited_pages(shared, allowed_pages)
    # Keep worker context bounded. Text/crops should already have been ranked
    # by the shared evidence pass; never pass local source paths or secrets.
    encoded = json.dumps(shared, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 120_000:
        shared["vision_entities"] = shared["vision_entities"][:250]
        shared["room_geometry_candidates"] = shared["room_geometry_candidates"][:250]
        shared["geometry_pages"] = shared["geometry_pages"][:100]
        if isinstance(shared.get("vector_geometry_pages"), list):
            shared["vector_geometry_pages"] = shared["vector_geometry_pages"][:40]
    return shared, artifact_names


def _relevant_consented_images(subskill, project, limit=4, render_dir=None):
    paths = _project_paths(project)
    allowed_pages = _consented_page_ids(paths)
    if not allowed_pages:
        return []
    dimension_page_ids = set()
    floor_layout_page_ids = set()
    resolved_root = paths["root"].resolve()
    job = _read(paths["root"] / "vision_extraction_job.json", {})
    manifest_path = Path(str(job.get("manifest_path", "")))
    try:
        resolved_manifest = manifest_path.resolve(strict=True)
        manifest = _read(resolved_manifest, {}) if resolved_root in resolved_manifest.parents else {}
    except (OSError, RuntimeError, ValueError):
        manifest = {}
    # Skill review must not depend on running the separate, optional vision
    # extraction job. Build a page index from the analysed packet directly;
    # consent still limits every page that can be attached to a model request.
    if not manifest.get("groups"):
        ai_input = _read(paths["ai_input"], {})
        page_rows = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", []) if isinstance(row, dict)}
        source_images = ai_input.get("source_files", {}).get("page_images", [])
        manifest = {"groups": [{"group_id": "analysed_pdf", "pages": [
            {**page_rows.get(image.get("page"), {}), **image,
             "image_path": image.get("path", image.get("image_path", ""))}
            for image in source_images if isinstance(image, dict) and image.get("page") in allowed_pages
        ]}]}
    pinned_pages = []
    if subskill["id"] in {"room_identity_use", "room_boundaries_areas"}:
        coverage = _read(paths["coverage"], {})
        page_rows = [row for row in coverage.get("page_roles", []) if isinstance(row, dict) and row.get("page") in allowed_pages]
        source_pages = {row.get("page"): row for row in _page_rows(_read(paths["ai_input"], {}))}

        def title_text(row):
            identity = row.get("identity") or {}
            candidates = identity.get("title_candidates", [])
            titles = [row.get("title", "")]
            titles.extend(item.get("value", "") for item in candidates if isinstance(item, dict))
            source = source_pages.get(row.get("page"), {})
            titles.append((source.get("structured_content") or {}).get("markdown", ""))
            return " ".join(str(value or "") for value in titles).casefold()

        layout = sorted((row for row in page_rows if row.get("geometry_eligible") is not False
                         and any(term in title_text(row) for term in ("proposed floor layout", "existing floor layout", "shop floor layout"))),
                        key=lambda row: row.get("page", 0))
        floor_layout_page_ids = {row.get("page") for row in layout}
        dimension = sorted((row for row in page_rows if row.get("geometry_eligible") is not False
                            and "dimension plan" in title_text(row)), key=lambda row: row.get("page", 0))
        dimension_page_ids = {row.get("page") for row in dimension}
        primary = sorted((row for row in page_rows if row.get("geometry_eligible") is not False
                          and row.get("proposed_role") in {"main_floor_plan", "primary_geometry_plan"}),
                         key=lambda row: row.get("page", 0))
        for row in layout + dimension + primary:
            page = row.get("page")
            if page not in pinned_pages:
                pinned_pages.append(page)
        room_run = _read(paths["root"] / "ai_preliminary_run.json", {})
        room_proposal = room_run.get("local_room_inference_proposal", {})
        for room in room_proposal.get("rooms", []) if isinstance(room_proposal, dict) else []:
            if not isinstance(room, dict):
                continue
            for page in room.get("source_pages", []):
                if page in allowed_pages and page not in pinned_pages:
                    pinned_pages.append(page)
            for evidence in room.get("evidence", []) if isinstance(room.get("evidence"), list) else []:
                page = evidence.get("page") if isinstance(evidence, dict) else None
                if page in allowed_pages and page not in pinned_pages:
                    pinned_pages.append(page)
    needles = set(re.findall(r"[a-z0-9]+", (subskill["id"] + " " + subskill["task"] + " " + " ".join(subskill["inputs"])).lower()))
    preferred_groups = {
        "sheet_identity": ["plan_geometry", "opening_elevation", "ceiling_lighting", "visual_cross_check"],
        "room_identity_use": ["plan_geometry", "ceiling_lighting"],
        "room_boundaries_areas": ["plan_geometry", "ceiling_lighting"],
        "occupancy_seating": ["plan_geometry", "ceiling_lighting"],
        "ceiling_height_volume": ["opening_elevation", "ceiling_lighting", "plan_geometry"],
        "lighting_evidence": ["ceiling_lighting"], "equipment_evidence": ["ceiling_lighting", "visual_cross_check"],
        "schedule_evidence": ["ceiling_lighting", "plan_geometry"],
        "cross_sheet_opening_match": ["opening_elevation", "plan_geometry", "visual_cross_check"],
        "glazing_properties": ["opening_elevation"], "exposure_orientation": ["opening_elevation", "visual_cross_check"],
        "shading": ["opening_elevation", "visual_cross_check"],
    }.get(subskill["id"], [])
    preferred_titles = {
        "room_boundaries_areas": ["dimension plan", "dimensioned top view plan", "floor plan"],
        "room_identity_use": ["proposed floor layout", "existing floor layout", "floor plan", "dimension plan", "top view plan"],
        "sheet_identity": ["proposed floor layout", "dimension plan", "floor plan"],
        "occupancy_seating": ["floor plan", "dimension plan", "top view plan"],
    }.get(subskill["id"], [])
    candidates = []
    for group in manifest.get("groups", []):
        group_id = group.get("group_id", "") if isinstance(group, dict) else ""
        group_rank = preferred_groups.index(group_id) if group_id in preferred_groups else len(preferred_groups)
        for page in group.get("pages", []) if isinstance(group, dict) else []:
            page_number = page.get("page") if isinstance(page, dict) else None
            image_path = Path(str(page.get("image_path", page.get("path", "")))) if isinstance(page, dict) else None
            if page_number not in allowed_pages or image_path is None:
                continue
            try:
                resolved_image = image_path.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                continue
            if resolved_root not in resolved_image.parents or not resolved_image.is_file():
                continue
            text = " ".join(str(page.get(key, "")) for key in (
                "title", "role", "detected_type", "plan_role", "capability_map",
                "drawing_number", "drawing_number_candidates", "title_block_excerpts", "structured_text",
            ))
            words = set(re.findall(r"[a-z0-9]+", text.lower()))
            score = len(needles & words)
            title = text.casefold()
            title_rank = next((index for index, phrase in enumerate(preferred_titles) if phrase in title), len(preferred_titles))
            relevance = page.get("relevance", 0)
            if isinstance(relevance, dict):
                # Page-group selection stores a per-domain score map, while
                # older manifests used one numeric relevance score.
                relevance = max(
                    (value for value in relevance.values()
                     if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)),
                    default=0.0,
                )
            try:
                relevance = float(relevance or 0)
            except (TypeError, ValueError):
                relevance = 0.0
            candidates.append((group_rank, title_rank, score, relevance, page_number, resolved_image))
    pin_rank = {page: index for index, page in enumerate(pinned_pages)}
    candidates.sort(key=lambda row: (0 if row[4] in pin_rank else 1, pin_rank.get(row[4], len(pin_rank)),
                                    row[0], row[1], -row[2], -row[3], row[4]))
    selected = candidates[:limit]
    result = [{"page": page, "path": image} for _group_rank, _title_rank, _score, _relevance, page, image in selected]
    if render_dir:
        from ai.chatgpt_packet import render_high_res_page
        ai_input = _read(paths["ai_input"], {})
        source_images = {row.get("page"): row for row in ai_input.get("source_files", {}).get("page_images", [])
                         if isinstance(row, dict)}
        render_root = Path(render_dir)
        render_root.mkdir(parents=True, exist_ok=True)
        for row in list(result):
            source_image = source_images.get(row["page"])
            if not source_image:
                continue
            target = render_root / f"page_{int(row['page']):03d}.png"
            # Keep the 180 dpi render for legibility. The skill prompt also
            # receives the exact render-to-vector coordinate transform because
            # the indexed vector screenshot is intentionally lower resolution.
            rendered = render_high_res_page(ai_input, source_image, target, _GEOMETRY_VECTOR_DPI)
            if rendered.get("ok") and target.is_file():
                row["path"] = target
                if subskill["id"] == "room_boundaries_areas" and row["page"] in dimension_page_ids | floor_layout_page_ids:
                    page_geometry = next((item for item in _read(paths["root"] / "vector_geometry.json", {})
                                          .get("geometry_key_points", {}).get("pages", [])
                                          if item.get("page") == row["page"]), {})
                    coordinate_systems = page_geometry.get("coordinate_systems", {})
                    frame = coordinate_systems.get("image_px", {}) if isinstance(coordinate_systems, dict) else {}
                    width, height = frame.get("image_width"), frame.get("image_height")
                    if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
                        crop_bbox = [80.0, 20.0, min(565.0, float(width)), min(480.0, float(height))]
                        try:
                            from PIL import Image
                            with Image.open(target) as rendered_image:
                                sx, sy = rendered_image.width / width, rendered_image.height / height
                                crop_box = (round(crop_bbox[0] * sx), round(crop_bbox[1] * sy),
                                            round(crop_bbox[2] * sx), round(crop_bbox[3] * sy))
                                crop_path = render_root / f"page_{int(row['page']):03d}_geometry_crop.png"
                                rendered_image.crop(crop_box).save(crop_path)
                            result.insert(result.index(row) + 1, {**row, "path": crop_path,
                                "crop_bbox_canonical_px": crop_bbox,
                                "crop_description": ("dimension-plan main viewport zoom" if row["page"] in dimension_page_ids
                                                     else "proposed-floor-layout main viewport zoom")})
                        except (OSError, ValueError):
                            pass
    return result


@dataclass
class SkillProviderResult:
    proposal: dict
    raw_record: dict


class SkillProviderError(RuntimeError):
    def __init__(self, code, raw_record=None):
        super().__init__(code)
        self.raw_record = raw_record or {}


class OpenAISkillProposalProvider:
    """Focused reasoning over the shared, consented extraction package."""
    def __init__(self, api_key, model):
        self.api_key, self.model = api_key, model

    def propose(self, prompt, image_paths=()):
        started = time.monotonic()
        content = [{"type": "input_text", "text": prompt}]
        for image in image_paths:
            image_path = image.get("path") if isinstance(image, dict) else image
            if isinstance(image, dict) and image.get("page"):
                content.append({"type": "input_text", "text": f"Selected evidence image for physical PDF page {image['page']}."})
            encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"})
        body = {"model": self.model, "store": False, "text": {"format": {"type": "json_object"}},
                "input": [{"role": "user", "content": content}]}
        request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode("utf-8"),
            method="POST", headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                response_body = json.loads(response.read().decode("utf-8"))
                http_status = response.status
        except urllib.error.HTTPError as error:
            error_text = error.read().decode("utf-8", "replace")
            raw_record = {"provider": "http", "http_status": error.code, "reply_text": error_text,
                          "duration_seconds": round(time.monotonic() - started, 3)}
            raise SkillProviderError("skill_provider_http_error_" + str(error.code), raw_record) from error
        text = response_body.get("output_text")
        if not isinstance(text, str):
            for item in response_body.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        break
        if not isinstance(text, str):
            raise SkillProviderError("skill_provider_empty_output", {"provider": "http", "http_status": http_status,
                "reply_text": "", "duration_seconds": round(time.monotonic() - started, 3)})
        try:
            proposal = json.loads(text)
        except json.JSONDecodeError as error:
            raise SkillProviderError("skill_provider_invalid_json", {"provider": "http", "http_status": http_status,
                "reply_text": text, "duration_seconds": round(time.monotonic() - started, 3)}) from error
        if not isinstance(proposal, dict):
            raise SkillProviderError("skill_provider_invalid_json", {"provider": "http", "http_status": http_status,
                "reply_text": text, "duration_seconds": round(time.monotonic() - started, 3)})
        return SkillProviderResult(proposal, {"provider": "http", "http_status": http_status,
            "reply_text": text, "duration_seconds": round(time.monotonic() - started, 3)})


class CodexCliSkillProposalProvider:
    """Use the local Codex CLI for an isolated, read-only Archie test run."""

    def __init__(self, executable=None, timeout=600):
        self.executable = executable or shutil.which("codex")
        self.timeout = timeout
        if not self.executable:
            raise RuntimeError("codex_cli_unavailable")

    def propose(self, prompt, image_paths=()):
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="archie-codex-skill-") as temp_dir:
            output = Path(temp_dir) / "proposal.json"
            command = [self.executable, "exec", "--sandbox", "read-only", "--ephemeral",
                       "--ignore-user-config", "--skip-git-repo-check", "--output-last-message", str(output)]
            model = os.environ.get("ARCHIE_CODEX_MODEL", "").strip()
            if model:
                command.extend(["--model", model])
            for image in image_paths:
                image_path = image.get("path") if isinstance(image, dict) else image
                if image_path:
                    command.extend(["--image", str(image_path)])
            command.append("-")
            # Do not expose the web app's API key to the model/tool process.
            child_env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
            try:
                result = subprocess.run(command, input=str(prompt), text=True, capture_output=True,
                                        timeout=self.timeout, check=False, env=child_env)
            except subprocess.TimeoutExpired as error:
                stdout = error.stdout.decode("utf-8", "replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
                stderr = error.stderr.decode("utf-8", "replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
                raw_record = {"provider": "codex_cli", "model": model, "exit_code": None,
                    "stdout_tail": stdout[-_ATTEMPT_TEXT_LIMIT:], "stderr_tail": stderr[-_ATTEMPT_TEXT_LIMIT:],
                    "reply_text": "", "duration_seconds": round(time.monotonic() - started, 3)}
                raise SkillProviderError("codex_cli_timeout", raw_record) from error
            stdout = (result.stdout or "")[-_ATTEMPT_TEXT_LIMIT:]
            stderr = (result.stderr or "")[-_ATTEMPT_TEXT_LIMIT:]
            raw_response = output.read_text(encoding="utf-8") if output.is_file() else ""
            raw_record = {"provider": "codex_cli", "model": model, "exit_code": result.returncode,
                "stdout_tail": stdout, "stderr_tail": stderr,
                "reply_text": raw_response if raw_response else (stdout or stderr),
                "duration_seconds": round(time.monotonic() - started, 3)}
            if result.returncode != 0 or not output.is_file():
                raise SkillProviderError("codex_cli_failed", raw_record)
            response = raw_response.strip()
            if response.startswith("```"):
                response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response, flags=re.IGNORECASE)
            try:
                proposal = json.loads(response)
            except json.JSONDecodeError as error:
                raise SkillProviderError("skill_provider_invalid_json", raw_record) from error
            if not isinstance(proposal, dict):
                raise SkillProviderError("skill_provider_invalid_json", raw_record)
            return SkillProviderResult(proposal, raw_record)


def _whole_px(point):
    if isinstance(point, (list, tuple)) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point):
        return [int(round(point[0])), int(round(point[1]))]
    return [None, None]


def _compact_line(row):
    hint = str(row.get("candidate_role_hint", "") or "")
    return [row.get("candidate_id"), *_whole_px(row.get("start_px")), *_whole_px(row.get("end_px")),
            _LINE_ROLE_CODES.get(hint, hint)]


def _compact_dimension(row):
    bbox = row.get("bbox_px")
    return {"candidate_id": row.get("candidate_id"), "text_seen": row.get("text_seen"), "value_mm": row.get("value_mm"),
            "bbox_px": [int(round(value)) for value in bbox] if isinstance(bbox, list) and all(isinstance(value, (int, float)) for value in bbox) else bbox}


def _compact_label(row):
    bbox = row.get("bbox")
    coords = ([int(round(value)) for value in bbox] if isinstance(bbox, list) and len(bbox) == 4
              and all(isinstance(value, (int, float)) for value in bbox) else [None] * 4)
    return [row.get("text"), *coords]


def _generic_record_relevant(subskill_id, artifact_name, row):
    """Keep only records a generic domain task can use.

    Unvalidated vector wall and dimension candidates are geometry-resolver
    inputs, not surface or opening evidence; a single plan can produce
    thousands of them. Shared value-resolution registers are filtered to the
    targets the task owns when a target vocabulary is declared for it.
    """
    if artifact_name == "geometry_resolution.json":
        if row.get("kind") in _UNVALIDATED_GEOMETRY_KINDS:
            return row.get("geometry_status") in _ACCEPTED_GEOMETRY_STATUSES
        return True
    if artifact_name == "value_resolution.json":
        keywords = _VALUE_TARGET_KEYWORDS.get(subskill_id)
        if keywords is None:
            return True
        target = " ".join(str(row.get(key, "")) for key in ("target", "target_category", "field")).casefold()
        return any(word in target for word in keywords)
    return True


def _compact_for_prompt(value, stats):
    """Drop provenance bookkeeping and cap long text before it reaches a prompt.

    Identifiers, values, statuses, pages and citations are retained. Hashes,
    timestamps and witness lists are resolver bookkeeping the model cannot use.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in _PROMPT_NOISE_KEYS:
                stats["dropped_keys"] = stats.get("dropped_keys", 0) + 1
                continue
            result[key] = _compact_for_prompt(item, stats)
        return result
    if isinstance(value, list):
        return [_compact_for_prompt(item, stats) for item in value]
    if isinstance(value, str) and len(value) > _PROMPT_TEXT_LIMIT:
        stats["truncated_strings"] = stats.get("truncated_strings", 0) + 1
        return value[:_PROMPT_TEXT_LIMIT] + f"…[truncated {len(value) - _PROMPT_TEXT_LIMIT} chars]"
    return value


def _compact_dependencies(dependencies):
    """Pass prerequisites as their validated proposal content only."""
    result = {}
    for key, value in (dependencies or {}).items():
        proposal = value.get("proposal", {}) if isinstance(value, dict) else {}
        proposal = proposal if isinstance(proposal, dict) else {}
        result[key] = {"status": value.get("status", proposal.get("status", "")) if isinstance(value, dict) else "",
                       "proposal": {field: proposal.get(field) for field in _DEPENDENCY_PROMPT_FIELDS if field in proposal}}
    return result


def _prompt_budget_chars(subskill_id=""):
    raw = os.environ.get("ARCHIE_SKILL_PROMPT_MAX_CHARS", "").strip()
    default = _TASK_PROMPT_BUDGET_CHARS.get(subskill_id, _PROMPT_BUDGET_CHARS)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(1_000, value)


def _bounded_proposal_prompt(subskill, dependencies, evidence, image_frames=()):
    """Build a scoped prompt and report whether it fits the per-task budget.

    Over-budget prompts are never truncated into a different task: the caller
    blocks the task without contacting a provider and records the breakdown.
    """
    stats = {}
    compact_evidence = _compact_for_prompt(evidence, stats)
    compact_dependencies = _compact_for_prompt(_compact_dependencies(dependencies), stats)
    prompt = _proposal_prompt(subskill, compact_dependencies, compact_evidence, image_frames=image_frames)
    budget = _prompt_budget_chars(subskill["id"])
    sections = {"prerequisites": len(json.dumps(compact_dependencies, ensure_ascii=False, allow_nan=False))}
    for key, item in compact_evidence.items():
        sections["evidence." + key] = len(json.dumps(item, ensure_ascii=False, allow_nan=False))
    task_evidence = compact_evidence.get("task_evidence", {})
    if isinstance(task_evidence, dict):
        for key, item in task_evidence.items():
            sections["evidence.task_evidence." + key] = len(json.dumps(item, ensure_ascii=False, allow_nan=False))
    largest = sorted(sections.items(), key=lambda pair: -pair[1])[:5]
    report = {"prompt_chars": len(prompt), "budget_chars": budget,
              "status": "within_budget" if len(prompt) <= budget else "over_budget",
              "dropped_provenance_keys": stats.get("dropped_keys", 0),
              "truncated_strings": stats.get("truncated_strings", 0),
              "largest_sections": [{"section": name, "chars": size} for name, size in largest]}
    return prompt, report


def _over_budget_proposal(subskill, input_fp, report):
    largest = ", ".join(f"{row['section']} ({row['chars']:,} chars)" for row in report["largest_sections"][:3])
    return {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "blocked",
            "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": list(subskill["proposal_fields"]),
            "remediation": [f"Prompt is {report['prompt_chars']:,} characters, over the {report['budget_chars']:,}-character task budget; "
                            f"no AI provider was contacted. Largest sections: {largest}. Narrow this task's evidence before retrying."],
            "input_fingerprint": input_fp, "proposal_fields": _empty_fields(subskill), "artifact_names": []}


def _not_applicable_proposal(subskill, input_fp):
    return {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "not_applicable",
            "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": input_fp,
            "proposal_fields": _empty_fields(subskill), "artifact_names": []}


def _proposal_prompt(subskill, dependencies, evidence, image_frames=()):
    from ai.skill_registry import load_catalog
    catalog = load_catalog()
    parent = next(skill for skill in catalog["skills"] if skill["id"] == subskill["parent"])
    instructions = compose_subskill_instructions(subskill["parent"], subskill)
    return (
        "Perform exactly one bounded Archie runtime subskill. Return one JSON object only; no markdown.\n"
        "The object must contain status, affected_ids, observations, inferences, citations, confidence, alternatives, unresolved_fields, remediation, and proposal_fields. "
        "proposal_fields must contain every declared key with the declared type. confidence must be a JSON number from 0.0 to 1.0, or null when unassessed. "
        "Use not_applicable only when supplied evidence establishes the domain does not apply. "
        "Do not emit a calculation result, mutate an artifact, approve a value, or promote reviewed status.\n"
        f"Parent skill: {parent['id']} — {parent['purpose']}\n"
        f"Subskill: {subskill['id']} — {subskill['task']}\n"
        f"Declared proposal fields: {json.dumps(subskill['proposal_fields'], ensure_ascii=False)}\n"
        f"Allowed inputs: {json.dumps(subskill['inputs'], ensure_ascii=False)}\n"
        f"Validated prerequisite proposals: {json.dumps(dependencies, ensure_ascii=False, allow_nan=False)}\n"
        f"Instructions:\n{instructions}\n"
        f"Attached image coordinate frames: {json.dumps(list(image_frames), ensure_ascii=False, allow_nan=False)}\n"
        f"Shared evidence package:\n{json.dumps(evidence, ensure_ascii=False, allow_nan=False)}"
    )


def _attached_image_coordinate_frames(images, project):
    """Describe the rendered image-to-vector transform for geometry proposals."""
    paths = _project_paths(project)
    vector = _read(paths["root"] / "vector_geometry.json", {})
    geometry = vector.get("geometry_key_points", {}) if isinstance(vector, dict) else {}
    pages = geometry.get("pages", []) if isinstance(geometry, dict) else []
    by_page = {row.get("page"): row for row in pages if isinstance(row, dict)}
    coverage = _read(paths["coverage"], {})
    coverage_pages = {row.get("page"): row for row in _page_rows(coverage) if isinstance(row, dict)}
    source_pdf = _read(paths["ai_input"], {}).get("source_files", {}).get("pdf")
    page_sizes_pt = {}
    if source_pdf:
        try:
            import pdfplumber
            with pdfplumber.open(source_pdf) as document:
                for row in images:
                    page_number = row.get("page") if isinstance(row, dict) else None
                    if isinstance(page_number, int) and 0 < page_number <= len(document.pages):
                        page = document.pages[page_number - 1]
                        page_sizes_pt[page_number] = (page.width, page.height)
        except (OSError, RuntimeError, ValueError):
            page_sizes_pt = {}
    frames = []
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("page"), int):
            continue
        image_path = Path(str(image.get("path", "")))
        try:
            header = image_path.read_bytes()[:24]
            if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
                continue
            import struct
            render_width, render_height = struct.unpack(">II", header[16:24])
        except OSError:
            continue
        page = by_page.get(image["page"], {})
        systems = page.get("coordinate_systems", {}) if isinstance(page, dict) else {}
        canonical = systems.get("image_px", {}) if isinstance(systems, dict) else {}
        width, height = canonical.get("image_width"), canonical.get("image_height")
        if not all(isinstance(value, (int, float)) and value > 0 for value in (width, height, render_width, render_height)):
            continue
        crop_bbox = image.get("crop_bbox_canonical_px")
        crop_bbox = crop_bbox if isinstance(crop_bbox, list) and len(crop_bbox) == 4 else [0.0, 0.0, float(width), float(height)]
        frame = {
            "physical_page": image["page"],
            "attached_render_px": [render_width, render_height],
            "canonical_vector_image_px": [width, height],
            "canonical_crop_bbox_px": crop_bbox,
            "attached_to_canonical_scale": [(crop_bbox[2] - crop_bbox[0]) / render_width,
                                              (crop_bbox[3] - crop_bbox[1]) / render_height],
            "coordinate_transform": "canonical_x = crop_x0 + attached_x * scale_x; canonical_y = crop_y0 + attached_y * scale_y",
            "canonical_origin": "top_left",
            "canonical_boundary_field": "boundary_points_px",
            "wall_endpoint_fields": "Use supplied vector candidate IDs and their start_px/end_px verbatim.",
        }
        scale_text = str(coverage_pages.get(image["page"], {}).get("main_scale") or "")
        scale_match = re.search(r"1\s*:\s*(\d+(?:\.\d+)?)", scale_text)
        page_size = page_sizes_pt.get(image["page"])
        if scale_match and page_size and width:
            scale_ratio = float(scale_match.group(1))
            sheet_width_mm = page_size[0] * 25.4 / 72.0
            frame["confirmed_main_viewport_scale"] = scale_text
            frame["pdf_physical_page_width_mm"] = sheet_width_mm
            frame["canonical_image_width_px"] = width
            frame["scale_mm_per_canonical_px"] = sheet_width_mm * scale_ratio / width
            frame["scale_method"] = "PDF physical page width × confirmed drawing scale ÷ canonical vector image width"
        frames.append(frame)
    return frames


def _persist_skill_attempt(root, run_id, subskill_id, *, attempt_record, input_fingerprint,
                           outcome="received", failure_phase="", error_code="", validation=None):
    attempts_root = Path(root) / "skill_workflow_runs" / run_id / "attempts" / subskill_id
    attempts_root.mkdir(parents=True, exist_ok=True)
    number = max((int(path.name) for path in attempts_root.iterdir() if path.is_dir() and path.name.isdigit()), default=0) + 1
    attempt_dir = attempts_root / str(number)
    attempt_dir.mkdir()
    raw_record = attempt_record.get("raw_record", {}) if isinstance(attempt_record, dict) else {}
    raw_output = raw_record.get("reply_text", "")
    if not isinstance(raw_output, str):
        raw_output = json.dumps(raw_output, ensure_ascii=False, indent=2)
    images = attempt_record.get("images", []) if isinstance(attempt_record, dict) else []
    safe_images = [{"page": image.get("page"), "path": str(image.get("path", "")),
                    "source_path": str(image.get("source_path", ""))} if isinstance(image, dict)
                   else {"page": None, "path": str(image)} for image in images]
    prompt = attempt_record.get("prompt", "") if isinstance(attempt_record, dict) else ""
    cli = {key: raw_record[key] for key in ("provider", "model", "exit_code", "stdout_tail", "stderr_tail",
        "http_status", "duration_seconds") if key in raw_record}
    (attempt_dir / "prompt.txt").write_text(str(prompt), encoding="utf-8")
    (attempt_dir / "images.json").write_text(json.dumps(safe_images, indent=2, ensure_ascii=False), encoding="utf-8")
    (attempt_dir / "raw_output.txt").write_text(raw_output, encoding="utf-8")
    (attempt_dir / "cli.json").write_text(json.dumps(cli, indent=2, ensure_ascii=False), encoding="utf-8")
    now = time.time()
    validation = validation or ({"check": "proposal_valid", "path": "", "detail": "Proposal passed schema and citation validation."} if outcome == "accepted" else {})
    metadata = {"schema_version": 1, "run_id": run_id, "subskill_id": subskill_id, "attempt": number,
        "outcome": outcome, "failure_phase": failure_phase, "error_code": error_code,
        "validation_check": (validation or {}).get("check", ""),
        "validation_path": (validation or {}).get("path", ""),
        "validation_detail": (validation or {}).get("detail", ""),
        "input_fingerprint": input_fingerprint, "started_at": attempt_record.get("started_at", now),
        "finished_at": now, "provider": raw_record.get("provider", ""),
        "prompt_budget": attempt_record.get("prompt_budget", {}) if isinstance(attempt_record, dict) else {}}
    (attempt_dir / "attempt.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return attempt_dir.relative_to(Path(root)).as_posix(), metadata


def _empty_fields(subskill):
    result = {}
    for key, descriptor in subskill["proposal_fields"].items():
        if isinstance(descriptor, str) and descriptor.startswith("array"):
            result[key] = []
        elif isinstance(descriptor, str) and descriptor.startswith("object"):
            result[key] = {}
        else:
            result[key] = None
    return result


def _evidence_page_ids(value):
    pages = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"page", "physical_page", "physical_pdf_page", "source_page", "page_number"} and type(item) is int and item > 0:
                pages.add(item)
            elif key in {"pages", "source_pages", "evidence_page_ids"} and isinstance(item, list):
                pages.update(page for page in item if type(page) is int and page > 0)
            else:
                pages.update(_evidence_page_ids(item))
    elif isinstance(value, list):
        for item in value:
            pages.update(_evidence_page_ids(item))
    return pages


def _has_page_evidence(evidence):
    return bool(_evidence_page_ids(evidence) or
                (isinstance(evidence, dict) and (evidence.get("vision_entities") or evidence.get("task_evidence"))))


def _evidence_review_record(attempt_record, attempt_ref, attempt_metadata):
    raw = attempt_record.get("raw_record", {}) if isinstance(attempt_record, dict) else {}
    provider = raw.get("provider") or attempt_record.get("provider", "")
    sent = provider not in {"none", "none_prompt_over_budget", ""}
    pages = attempt_record.get("evidence_pages", []) if sent else []
    return {"pages": sorted(set(page for page in pages if type(page) is int and page > 0)),
            "provider": provider, "model": raw.get("model") or attempt_record.get("model", ""),
            "started_at": attempt_record.get("started_at", ""),
            "finished_at": attempt_metadata.get("finished_at", time.time()), "attempt_ref": attempt_ref}


def _call_skill_provider(subskill, project, dependencies, evidence, attempt_record, provider_factory, provider_label):
    """Render images, build a bounded prompt and call the provider.

    Returns the raw provider proposal, or None when the scoped prompt is over
    budget; in that case no provider object is created and nothing is sent.
    """
    image_limit = 4 if subskill["id"] in {"room_identity_use", "room_boundaries_areas"} else 2
    with tempfile.TemporaryDirectory(prefix="archie-skill-pages-") as render_dir:
        images = _relevant_consented_images(subskill, project, limit=image_limit, render_dir=render_dir)
        settings = _read(Path(project["review_dir"]) / "vision_extraction_settings.json", {})
        attempt_record["evidence_pages"] = sorted({row.get("page") for row in images if isinstance(row, dict)
                                                    and isinstance(row.get("page"), int)} |
                                                   _evidence_page_ids(evidence))
        if not images and not _has_page_evidence(evidence):
            attempt_record["raw_record"] = {"provider": "none", "reply_text": ""}
            return _NO_EVIDENCE_PAGES
        frames = _attached_image_coordinate_frames(images, project) if subskill["id"] == "room_boundaries_areas" else ()
        prompt, budget = _bounded_proposal_prompt(subskill, dependencies, evidence, image_frames=frames)
        model = settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")
        attempt_record.update({"prompt": prompt, "images": images, "prompt_budget": budget,
                              "provider": provider_label, "model": model})
        if budget["status"] != "within_budget":
            attempt_record["raw_record"] = {"provider": "none_prompt_over_budget", "reply_text": ""}
            return None
        provider = provider_factory()
        try:
            provider_result = provider.propose(prompt, image_paths=images)
        except Exception as error:
            attempt_record["raw_record"] = getattr(error, "raw_record", {}) or {"provider": provider_label, "reply_text": ""}
            attempt_record["raw_record"].setdefault("provider", provider_label)
            attempt_record["raw_record"].setdefault("model", model)
            setattr(error, "attempt_record", attempt_record)
            raise
        if isinstance(provider_result, SkillProviderResult):
            attempt_record["raw_record"] = provider_result.raw_record
            attempt_record["raw_record"].setdefault("provider", provider_label)
            attempt_record["raw_record"].setdefault("model", model)
            return provider_result.proposal
        attempt_record["raw_record"] = {"provider": provider_label, "model": model,
                                         "reply_text": json.dumps(provider_result, ensure_ascii=False)}
        return provider_result


def _case_file_route(project):
    """Skills read their case file (pass 1 and 2's findings plus key pages) through the Codex CLI with the
    ChatGPT sign-in: on when the job's page reading is on and pass 1 has read the pages."""
    from backend import skill_case_file
    if os.environ.get("ARCHIE_PAGE_READING", "").strip().lower() == "off":
        return False
    if CASE_FILE_PROVIDER_FACTORY is None and not shutil.which("codex"):
        return False
    return skill_case_file.available(project)


def _call_case_file_provider(subskill, project, dependencies, evidence, attempt_record):
    """Ask the AI for one sub-skill with its case file. Returns the raw proposal, or None when over budget."""
    from backend import page_inventory_service, skill_case_file
    key = str(Path(project["review_dir"]).resolve())
    case, images = skill_case_file.build(subskill["id"], project)
    evidence = {**evidence, "case_file": case}
    prompt, budget = _bounded_proposal_prompt(subskill, dependencies, evidence)
    model = os.environ.get("ARCHIE_CODEX_MODEL", "").strip() or "codex default"
    attempt_record.update({"prompt": prompt, "images": [{"page": page, "path": str(path)} for page, path in zip(case["attached_pages"], images)],
                           "prompt_budget": budget, "provider": "codex_case_file", "model": model,
                           "evidence_pages": sorted(set(case["attached_pages"]) | {row["page"] for row in case["readings"]})})
    if budget["status"] != "within_budget":
        attempt_record["raw_record"] = {"provider": "none_prompt_over_budget", "reply_text": ""}
        return None
    if key in _USAGE_STOPPED:
        attempt_record["raw_record"] = {"provider": "codex_case_file", "model": model, "reply_text": ""}
        error = SkillProviderError("skill_provider_usage_limit", attempt_record["raw_record"])
        error.detail = _USAGE_STOPPED[key]
        raise error
    provider = CASE_FILE_PROVIDER_FACTORY() if CASE_FILE_PROVIDER_FACTORY is not None else CodexCliSkillProposalProvider()
    try:
        result = provider.propose(prompt, image_paths=[str(path) for path in images])
    except SkillProviderError as error:
        raw = getattr(error, "raw_record", {}) or {}
        limit = page_inventory_service._usage_limit_message(f"{raw.get('stderr_tail', '')} {raw.get('stdout_tail', '')} {raw.get('reply_text', '')}")
        attempt_record["raw_record"] = {**raw, "provider": "codex_case_file", "model": model}
        if limit:
            _USAGE_STOPPED[key] = limit
            stopped = SkillProviderError("skill_provider_usage_limit", attempt_record["raw_record"])
            stopped.detail = limit
            raise stopped from error
        raise
    if isinstance(result, SkillProviderResult):
        attempt_record["raw_record"] = {**result.raw_record, "provider": "codex_case_file", "model": model}
        return result.proposal
    attempt_record["raw_record"] = {"provider": "codex_case_file", "model": model, "reply_text": json.dumps(result, ensure_ascii=False)}
    return result


def _proposal_from_raw(subskill, raw, input_fp):
    if not isinstance(raw, dict):
        _validation_error("provider_proposal_object", "$", "Provider response must be a JSON object.")
    proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
        "status": raw.get("status", "needs_review"), "affected_ids": raw.get("affected_ids", []),
        "observations": raw.get("observations", []), "inferences": raw.get("inferences", []),
        "citations": raw.get("citations", []), "confidence": raw.get("confidence"),
        "alternatives": raw.get("alternatives", []), "unresolved_fields": raw.get("unresolved_fields", []),
        "remediation": raw.get("remediation", []), "input_fingerprint": input_fp,
        "proposal_fields": raw.get("proposal_fields", {}), "artifact_names": []}
    if proposal["status"] == "resolved":
        proposal["status"] = "provisional"
    return proposal


def _execute_subskill(subskill, project, dependencies, source_fp):
    evidence, artifact_names = _shared_evidence_packet(subskill, project)
    registry = load_subskill_registry()
    input_fp = _fingerprint({"source": source_fp, "subskill": subskill, "evidence": evidence,
        "prerequisites": {key: {"status": value.get("status", ""), "input_fingerprint": value.get("input_fingerprint", ""),
            "proposal_fingerprint": _fingerprint(value.get("proposal", {}))} for key, value in dependencies.items()}})
    settings_path = Path(project["review_dir"]) / "vision_extraction_settings.json"
    settings = _read(settings_path, {})
    test_mode = os.environ.get("ARCHIE_ENV") == "test" and os.environ.get("ARCHIE_TEST_MODE") == "1"
    codex_test_mode = test_mode and os.environ.get("ARCHIE_TEST_CODEX") == "1" and project.get("test_run") is True
    has_evidence = bool(evidence.get("task_evidence", {}).get(next(iter(subskill["proposal_fields"]), ""), [])) or bool(evidence.get("vision_entities"))
    raw = None
    attempt_record = {"prompt": "", "images": [], "raw_record": {}, "started_at": time.time()}
    provider_kind = "existing_project_evidence"
    if subskill["id"] == "address_confirmation":
        candidate = dependencies.get("site_clue_extraction", {}).get("proposal", {})
        candidate_fields = candidate.get("proposal_fields", {}) if isinstance(candidate, dict) else {}
        citations = candidate.get("citations", []) if isinstance(candidate, dict) else []
        proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "needs_review",
            "affected_ids": candidate.get("affected_ids", []) if isinstance(candidate, dict) else [],
            "observations": candidate.get("observations", []) if isinstance(candidate, dict) else [], "inferences": [],
            "citations": citations, "confidence": candidate.get("confidence") if isinstance(candidate, dict) else None,
            "alternatives": candidate.get("alternatives", []) if isinstance(candidate, dict) else [],
            "unresolved_fields": ["confirmed_address", "confirmation_actor", "confirmation_time", "consent_ref"],
            "remediation": ["A contractor must confirm or correct the project address before any location or weather lookup."],
            "input_fingerprint": input_fp, "proposal_fields": {"confirmed_address": None, "confirmation_actor": None,
                "confirmation_time": None, "consent_ref": None}, "artifact_names": []}
        provider_kind = "user_confirmation_required"
    elif subskill["id"] == "weather_source_matching" and not dependencies.get("address_confirmation", {}).get("proposal", {}).get("proposal_fields", {}).get("confirmed_address"):
        proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "blocked",
            "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": ["confirmed_address", "design_weather_record"],
            "remediation": ["Confirm the project address and design basis, then run the location resolver."],
            "input_fingerprint": input_fp, "proposal_fields": {"weather_candidates": []}, "artifact_names": []}
        provider_kind = "confirmation_dependency"
    elif codex_test_mode:
        raw = _call_skill_provider(subskill, project, dependencies, evidence, attempt_record,
                                   CodexCliSkillProposalProvider, "codex_cli")
        if raw is _NO_EVIDENCE_PAGES:
            proposal = _not_applicable_proposal(subskill, input_fp)
            provider_kind = "no_evidence_pages"
        elif raw is None:
            proposal = _over_budget_proposal(subskill, input_fp, attempt_record["prompt_budget"])
            provider_kind = "prompt_budget_blocked"
        else:
            if not isinstance(raw, dict):
                _validation_error("provider_proposal_object", "$", "Provider response must be a JSON object.")
            proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
                "status": raw.get("status", "needs_review"), "affected_ids": raw.get("affected_ids", []),
                "observations": raw.get("observations", []), "inferences": raw.get("inferences", []),
                "citations": raw.get("citations", []), "confidence": raw.get("confidence"),
                "alternatives": raw.get("alternatives", []), "unresolved_fields": raw.get("unresolved_fields", []),
                "remediation": raw.get("remediation", []), "input_fingerprint": input_fp,
                "proposal_fields": raw.get("proposal_fields", {}), "artifact_names": []}
            provider_kind = "local_codex_cli_test"
    elif test_mode:
        if subskill["id"] in {"sheet_identity", "revision_scope", "page_relationships", "room_identity_use", "room_boundaries_areas",
                               "ceiling_height_volume", "occupancy_seating", "lighting_evidence", "equipment_evidence", "schedule_evidence"}:
            fields, existing_artifacts = _subskill_records(subskill["id"], project)
            proposal = _build_subskill_proposal(subskill, fields, existing_artifacts, dependencies, source_fp)
            if not proposal["affected_ids"] and not proposal["citations"]:
                proposal = _not_applicable_proposal(subskill, input_fp)
        else:
            proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
                "status": "needs_review" if has_evidence else "not_applicable", "affected_ids": [],
                "observations": [], "inferences": [], "citations": [], "confidence": None,
                "alternatives": [], "unresolved_fields": list(subskill["proposal_fields"]) if has_evidence else [],
                "remediation": ["No deterministic fixture record is available for this domain task."] if has_evidence else [],
                "input_fingerprint": input_fp, "proposal_fields": _empty_fields(subskill), "artifact_names": []}
        provider_kind = "local_test_fixture"
    elif _case_file_route(project):
        raw = _call_case_file_provider(subskill, project, dependencies, evidence, attempt_record)
        if raw is None:
            proposal = _over_budget_proposal(subskill, input_fp, attempt_record["prompt_budget"])
            provider_kind = "prompt_budget_blocked"
        else:
            proposal = _proposal_from_raw(subskill, raw, input_fp)
            provider_kind = "codex_case_file"
    elif settings.get("owner_opt_in") and _consented_page_ids(_project_paths(project)) and (SKILL_PROVIDER_FACTORY is not None or os.environ.get("OPENAI_API_KEY")):
        model = settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")

        def provider_factory():
            if SKILL_PROVIDER_FACTORY is not None:
                return SKILL_PROVIDER_FACTORY(model)
            return OpenAISkillProposalProvider(os.environ["OPENAI_API_KEY"], model)

        raw = _call_skill_provider(subskill, project, dependencies, evidence, attempt_record, provider_factory, "http")
        if raw is _NO_EVIDENCE_PAGES:
            proposal = _not_applicable_proposal(subskill, input_fp)
            provider_kind = "no_evidence_pages"
        elif raw is None:
            proposal = _over_budget_proposal(subskill, input_fp, attempt_record["prompt_budget"])
            provider_kind = "prompt_budget_blocked"
        else:
            if not isinstance(raw, dict):
                _validation_error("provider_proposal_object", "$", "Provider response must be a JSON object.")
            proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
                "status": raw.get("status", "needs_review"), "affected_ids": raw.get("affected_ids", []),
                "observations": raw.get("observations", []), "inferences": raw.get("inferences", []),
                "citations": raw.get("citations", []), "confidence": raw.get("confidence"),
                "alternatives": raw.get("alternatives", []), "unresolved_fields": raw.get("unresolved_fields", []),
                "remediation": raw.get("remediation", []), "input_fingerprint": input_fp,
                "proposal_fields": raw.get("proposal_fields", {}), "artifact_names": []}
            if proposal["status"] == "resolved":
                proposal["status"] = "provisional"
            provider_kind = "consented_openai"
    else:
        proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
            "status": "needs_review" if has_evidence else "not_applicable", "affected_ids": [],
            "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": list(subskill["proposal_fields"]) if has_evidence else [],
            "remediation": ["Enable the already-consented AI extraction before asking this skill to interpret project evidence."] if has_evidence else [],
            "input_fingerprint": input_fp, "proposal_fields": _empty_fields(subskill), "artifact_names": []}
    proposal["input_fingerprint"] = input_fp
    if subskill["id"] == "policy_source":
        for policy in proposal.get("proposal_fields", {}).get("policy", []):
            if isinstance(policy, dict):
                policy["approval_status"] = "proposed"
    if proposal.get("status") == "resolved":
        proposal["status"] = "provisional"
    proposal["artifact_names"] = []
    proposal["provider_kind"] = provider_kind
    proposal["evidence_artifact_names"] = artifact_names
    if not attempt_record["raw_record"]:
        attempt_record["raw_record"] = {"provider": provider_kind, "reply_text": json.dumps(proposal, ensure_ascii=False)}
    proposal["_attempt_record"] = attempt_record
    return proposal


def _summary(proposal):
    return {"status": proposal["status"], "record_count": sum(len(value) for value in proposal["proposal_fields"].values() if isinstance(value, list)),
        "affected_count": len(proposal["affected_ids"]), "citation_count": len(proposal["citations"]),
        "unresolved_count": len(proposal["unresolved_fields"]), "provider_kind": proposal.get("provider_kind", ""),
        "artifact_names": proposal["artifact_names"]}


def _handoff_room_geometry_skill(web, project, run_path):
    """Send saved area proposals through the existing deterministic pipeline."""
    from backend import calculation_extraction_service, model_input_resolution_service

    extraction = calculation_extraction_service.post(web, project, {"action": "build"})
    geometry = extraction.get("geometry_resolution", {})
    geometry_summary = geometry.get("summary", {}) if isinstance(geometry, dict) else {}
    handoff = {
        "status": "geometry_rebuilt",
        "active_area_count": geometry_summary.get("active_room_area_count", 0),
        "ai_geometry_candidate_count": geometry_summary.get("ai_geometry_candidate_count", 0),
        "proof_count": geometry_summary.get("room_geometry_proof_count", 0),
        "issue_count": len(geometry.get("review_items", [])) if isinstance(geometry, dict) else 0,
        "artifact_names": ["calculation_input_evidence.json", "geometry_resolution.json"],
        "model_input": {},
    }
    deadline = time.monotonic() + 45
    while True:
        try:
            model_state = model_input_resolution_service.post(web, project, {"action": "resolve"})
            handoff["model_input"] = {
                "status": model_state.get("status", "not_resolved"),
                "coverage_summary": model_state.get("coverage_summary", {}),
                "stale_reasons": model_state.get("stale_reasons", []),
            }
            break
        except model_input_resolution_service.ModelInputResolutionError as error:
            if error.code != "resolver_concurrent" or time.monotonic() >= deadline:
                handoff["model_input"] = {
                    "status": "needs_review", "error_code": error.code,
                    "domain": error.domain, "artifact": error.artifact,
                    "message": error.message, "remediation": error.remediation,
                }
                break
            time.sleep(0.2)
    with _project_lock(project["id"]):
        manifest = _read(run_path, {})
        manifest.setdefault("preparation", {})["geometry_handoff"] = handoff
        _write_manifest(run_path, manifest)
    return handoff


def _read_scanned_printed_areas(project, run_id):
    """Run the bounded S1 scan-reading validator for consented raster plans.

    Results stay as skill proposals. The usual S1 apply function is called only
    after an operator accepts the page finding.
    """
    from ai import scan_reading
    from backend import autonomous_tasks_service

    paths = _project_paths(project)
    allowed_pages = _consented_page_ids(paths)
    if not allowed_pages:
        return None
    settings = _read(paths["root"] / "vision_extraction_settings.json", {})
    model = settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")
    provider_factory = (lambda: SKILL_PROVIDER_FACTORY(model)) if SKILL_PROVIDER_FACTORY is not None else (
        lambda: OpenAISkillProposalProvider(os.environ["OPENAI_API_KEY"], model))
    page_proposals, raw_replies, blocked, failed = [], {}, [], []
    for task, target, packet, prompt, image_bytes, reason in autonomous_tasks_service._s1_packets(paths["root"]):
        page = packet.get("page")
        if page not in allowed_pages:
            continue
        record = autonomous_tasks_service._create_run(paths["root"], task, target, packet, prompt, image_bytes, reason)
        if reason:
            page_proposals.append({"page": page, "status": "blocked", "rooms": [], "remediation": reason})
            blocked.append(reason)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="archie-scan-reading-") as folder:
                images = []
                for index, content in enumerate(image_bytes):
                    image_path = Path(folder) / f"tile-{index + 1}.png"
                    image_path.write_bytes(content)
                    images.append({"page": page, "path": image_path})
                provider_result = provider_factory().propose(prompt, image_paths=images)
            reply = provider_result.proposal if isinstance(provider_result, SkillProviderResult) else provider_result
            if not isinstance(reply, dict):
                raise ValueError("The scan-reading provider reply must be a JSON object.")
            tiles = packet.get("tiles", [])
            factors = packet.get("factors", [])
            validated = scan_reading.validate_area_reply(reply, tiles, factors)
            if not validated["rooms"]:
                message = "Scanned plan with no printed areas or dimensions; room areas need the contractor."
                page_proposals.append({"page": page, "status": "blocked", "rooms": [], "remediation": message})
                blocked.append(message)
                raw_replies[str(page)] = reply
                continue
            context = autonomous_tasks_service._p0_context(paths["root"], page)
            working_scale = scan_reading.mm_per_px_from_scale(packet.get("working_scale_denominator", 100), packet.get("render_dpi", 180))
            outlines = autonomous_tasks_service._scan_outlines(context, working_scale)
            validated = scan_reading.validate_area_reply(reply, tiles, factors, outlines)
            declared_scale = scan_reading.mm_per_px_from_scale(validated.get("scale_denominator"), packet.get("render_dpi", 180))
            try:
                calibration = scan_reading.calibration_from_areas(validated["rooms"], outlines, declared_mm_per_px=declared_scale)
                calibration_reason = ""
            except ValueError as error:
                calibration, calibration_reason = None, str(error)
            mapped = [{"name": row["label"], "number": row.get("number"), "area_m2": row["area_m2"],
                       "printed_text": row["printed_text"], "page": page} for row in validated["rooms"]]
            page_proposals.append({"page": page, "status": "proposed", "rooms": mapped,
                "scale_text": validated.get("scale_text"), "calibration": calibration,
                "calibration_reason": calibration_reason, "remediation": []})
            raw_replies[str(page)] = reply
            record["skill_review_run_id"] = run_id
            autonomous_tasks_service._update_record(paths["root"], record)
        except Exception as error:
            message = str(error)[:500] or "Printed areas could not be validated from the scanned plan."
            page_proposals.append({"page": page, "status": "failed", "rooms": [], "remediation": message})
            failed.append(message)
    if not page_proposals:
        return None
    proposal = {"subskill_id": "scanned_printed_areas", "status": "failed" if failed else "blocked" if blocked else "provisional",
        "proposal_fields": {"pages": page_proposals},
        "citations": [{"page": row["page"], "excerpt": "; ".join(room["printed_text"] for room in row.get("rooms", []))
                       or row.get("remediation", "Scanned room plan")} for row in page_proposals],
        "inferences": [], "unresolved_fields": ["room areas"] if blocked else [],
        "remediation": [*blocked, *failed], "raw_replies": raw_replies}
    proposal_path = paths["root"] / "skill_workflow_runs" / run_id / "proposals" / "scanned_printed_areas.json"
    _atomic_json(proposal_path, proposal)
    return proposal


def _update_parent_stages(manifest, catalog, registry):
    active_parents = set(catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])))
    for parent in active_parents:
        child_ids = [row["id"] for row in registry["subskills"] if row["parent"] == parent
                     and manifest["subskills"][row["id"]].get("status") != "not_in_scope"]
        if not child_ids:
            manifest["skills"][parent].update({"status": "not_in_scope", "output_summary": {"subskill_count": 0}})
            continue
        statuses = [manifest["subskills"][child].get("status") for child in child_ids]
        terminal = _TERMINAL | {"completed", "needs_review", "excluded", "provisional", "resolved", "not_applicable"}
        status = ("failed" if "failed" in statuses else "blocked" if "blocked" in statuses else
                  "running" if any(item in {"queued", "running"} for item in statuses) else
                  "needs_review" if "needs_review" in statuses or "stale" in statuses else
                  "provisional" if "provisional" in statuses else "completed")
        manifest["skills"][parent].update({"status": status, "finished_at": time.time() if all(item in terminal for item in statuses) else "",
            "output_summary": {"subskill_count": len(child_ids), "completed_count": sum(item in {"completed", "needs_review", "resolved", "provisional", "excluded", "not_applicable"} for item in statuses),
                               "needs_review_count": statuses.count("needs_review"), "failed_count": statuses.count("failed"), "blocked_count": statuses.count("blocked")},
            "artifact_names": sorted({name for child in child_ids for name in manifest["subskills"][child].get("artifact_names", [])})})


def _run_worker(web, project, run_id, source_fp, catalog):
    paths = _project_paths(project)
    run_path = paths["manifest"]
    registry = load_subskill_registry()
    subskill_defs = {row["id"]: row for row in registry["subskills"]}
    selected_parents = set(catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])))
    try:
        with _project_lock(project["id"]):
            manifest = _read(run_path, {})
            if manifest.get("run_id") != run_id:
                return
            selected = _scoped_subskill_ids(registry, selected_parents, manifest.get("scope", "all"))
            manifest["status"] = "running"
            _write_manifest(run_path, manifest)

        # Scan-reading is part of the consented PDF review. Keep its result as
        # a proposal until an operator accepts the page finding.
        if manifest.get("scope") == "pdf_review":
            with _project_lock(project["id"]):
                manifest = _read(run_path, {})
                manifest.setdefault("preparation", {})["scan_reading"] = {"status": "running", "started_at": time.time()}
                _write_manifest(run_path, manifest)
            try:
                scan_proposal = _read_scanned_printed_areas(project, run_id)
                scan_state = "not_applicable" if scan_proposal is None else scan_proposal.get("status", "needs_review")
                scan_error = ""
            except Exception:
                scan_state, scan_error = "failed", "Scanned plan evidence could not be reviewed. Retry PDF review."
                LOGGER.exception("Scanned-page review failed", extra={"error_code": "scan_review_failed"})
            with _project_lock(project["id"]):
                manifest = _read(run_path, {})
                manifest.setdefault("preparation", {})["scan_reading"] = {
                    "status": scan_state, "finished_at": time.time(), "remediation": scan_error}
                _write_manifest(run_path, manifest)

        pending = set(selected)
        completed = set()
        failures = set()
        room_prepared = False
        while pending:
            ready = sorted(skill_id for skill_id in pending if set(subskill_defs[skill_id].get("depends_on", [])) <= completed | failures)
            if not ready:
                raise RuntimeError("subskill_graph_blocked")
            runnable = [skill_id for skill_id in ready if not (set(subskill_defs[skill_id].get("depends_on", [])) & failures)]
            blocked = set(ready) - set(runnable)
            if blocked:
                with _project_lock(project["id"]):
                    manifest = _read(run_path, {})
                    for skill_id in blocked:
                        blockers = sorted(set(subskill_defs[skill_id].get("depends_on", [])) & failures)
                        manifest["subskills"][skill_id].update({"status": "blocked", "finished_at": time.time(), "error_code": "dependency_failed",
                            "blocker_ids": blockers, "remediation": subskill_defs[skill_id]["failure"]})
                        pending.discard(skill_id)
                        failures.add(skill_id)
                    _update_parent_stages(manifest, catalog, registry)
                    _write_manifest(run_path, manifest)
            room_ids = [item for item in runnable if subskill_defs[item]["parent"] == "rooms_geometry_gains"]
            if room_ids and not room_prepared:
                with _project_lock(project["id"]):
                    manifest = _read(run_path, {})
                    manifest["preparation"].update({"status": "running", "started_at": time.time()})
                    _write_manifest(run_path, manifest)
                try:
                    prepared = _ensure_room_evidence(web, project)
                    room_prepared = True
                    with _project_lock(project["id"]):
                        manifest = _read(run_path, {})
                        manifest["preparation"].update({"status": "completed", "finished_at": time.time(), "artifact_names": prepared["artifact_names"],
                            "candidate_count": prepared["candidate_count"], "model_input": prepared.get("model_input", {})})
                        _write_manifest(run_path, manifest)
                except Exception as error:
                    code = str(error) if str(error) in {"room_inference_pending", "room_inference_failed", "room_evidence_missing"} else "room_evidence_preparation_failed"
                    with _project_lock(project["id"]):
                        manifest = _read(run_path, {})
                        manifest["preparation"].update({"status": "failed", "finished_at": time.time(), "error_code": code, "remediation": "Retry room detection and inspect the room, geometry, ceiling, and gains review panels."})
                        for skill_id in room_ids:
                            manifest["subskills"][skill_id].update({"status": "blocked", "finished_at": time.time(), "error_code": code,
                                "blocker_ids": ["room_inference"], "remediation": manifest["preparation"]["remediation"]})
                            pending.discard(skill_id)
                            failures.add(skill_id)
                        _update_parent_stages(manifest, catalog, registry)
                        _write_manifest(run_path, manifest)
                    continue
            with _project_lock(project["id"]):
                manifest = _read(run_path, {})
                for skill_id in runnable:
                    spec = subskill_defs[skill_id]
                    dependency_outputs = {}
                    for key in spec.get("depends_on", []):
                        dependency_outputs[key] = {
                            **manifest["subskills"][key],
                            "proposal": _read(paths["root"] / "skill_workflow_runs" / run_id / "proposals" / f"{key}.json", {}),
                        }
                    input_fp = _fingerprint({"source": source_fp, "spec": spec, "dependencies": dependency_outputs})
                    manifest["subskills"][skill_id].update({"status": "running", "started_at": time.time(), "input_fingerprint": input_fp})
                    manifest["skills"][spec["parent"]].update({"status": "running", "started_at": manifest["skills"][spec["parent"]].get("started_at") or time.time()})
                _update_parent_stages(manifest, catalog, registry)
                _write_manifest(run_path, manifest)
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, max(1, len(runnable)))) as pool:
                futures = {pool.submit(_execute_subskill, subskill_defs[skill_id], project,
                    {dep: {**manifest.get("subskills", {}).get(dep, {}), "proposal": _read(
                        paths["root"] / "skill_workflow_runs" / run_id / "proposals" / f"{dep}.json", {})}
                     for dep in subskill_defs[skill_id].get("depends_on", [])}, source_fp): skill_id for skill_id in runnable}
            for future in as_completed(futures):
                skill_id = futures[future]
                failure_phase = "proposal_generation"
                attempt_record = {"prompt": "", "images": [], "raw_record": {"provider": "unknown", "reply_text": ""}, "started_at": time.time()}
                attempt_ref = ""
                try:
                    raw_result = future.result()
                    attempt_record = raw_result.pop("_attempt_record", attempt_record)
                    attempt_ref, attempt_metadata = _persist_skill_attempt(
                        paths["root"], run_id, skill_id, attempt_record=attempt_record,
                        input_fingerprint=raw_result.get("input_fingerprint", source_fp))
                    attempt_metadata["model"] = attempt_record.get("raw_record", {}).get("model", attempt_record.get("model", ""))
                    failure_phase = "proposal_validation"
                    result = _validate_subskill_output(subskill_defs[skill_id], raw_result, registry,
                        allowed_pages={row.get("page") for row in _page_rows(_read(paths["coverage"], {})) if isinstance(row.get("page"), int)})
                    status = result.get("status", "needs_review")
                    if raw_result.get("provider_kind") == "prompt_budget_blocked":
                        attempt_metadata.update({"outcome": "blocked_over_budget", "failure_phase": "prompt_budget",
                            "error_code": "prompt_over_budget", "validation_check": "prompt_budget",
                            "validation_path": "$prompt",
                            "validation_detail": (result.get("remediation") or [""])[0]})
                    else:
                        attempt_metadata.update({"outcome": "accepted", "failure_phase": failure_phase,
                            "validation_check": "proposal_valid", "validation_path": "", "validation_detail": "Proposal passed schema and citation validation."})
                    (Path(paths["root"]) / attempt_ref / "attempt.json").write_text(json.dumps(attempt_metadata, indent=2), encoding="utf-8")
                    result["attempt_ref"] = attempt_ref
                    proposal_path = paths["root"] / "skill_workflow_runs" / run_id / "proposals" / f"{skill_id}.json"
                    proposal_path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_json(proposal_path, {key: value for key, value in result.items() if key != "provider_kind"})
                    proposal_name = str(proposal_path.relative_to(paths["root"]))
                    with _project_lock(project["id"]):
                        manifest = _read(run_path, {})
                        artifact_names = [proposal_name]
                        manifest["subskills"][skill_id].update({
                            "status": "needs_review" if status == "needs_review" else status,
                            "finished_at": time.time(), "input_fingerprint": result["input_fingerprint"],
                            "output_summary": _summary({**result, "artifact_names": artifact_names}), "artifact_names": artifact_names,
                            "attempt_ref": attempt_ref, "validation_check": attempt_metadata.get("validation_check", "proposal_valid"),
                            "validation_detail": attempt_metadata.get("validation_detail", "Proposal passed schema and citation validation."),
                            "evidence_reviewed": _evidence_review_record(attempt_record, attempt_ref, attempt_metadata),
                            "error_code": attempt_metadata.get("error_code", ""),
                            "prompt_budget": attempt_metadata.get("prompt_budget", {}),
                            "remediation": result.get("remediation", []),
                        })
                        _update_parent_stages(manifest, catalog, registry)
                        _write_manifest(run_path, manifest)
                    # Keep proposals as evidence-only until an operator accepts
                    # a specific finding in the PDF review panel.
                    (failures if status in {"blocked"} else completed).add(skill_id)
                    # A successfully handled node must leave the pending set.
                    # Without this, the scheduler reruns terminal nodes forever
                    # (and can overwrite their proposal/manifest state).
                    pending.discard(skill_id)
                except Exception as error:
                    raw_code = str(error)
                    code = (raw_code if raw_code in _SAFE_SUBSKILL_ERROR_CODES or raw_code.startswith("skill_provider_http_error_")
                            else "subskill_execution_failed")
                    validation = {"check": getattr(error, "check", ""), "path": getattr(error, "path", ""),
                                  "detail": getattr(error, "detail", "")}
                    if not validation["check"]:
                        if code == "skill_provider_invalid_json":
                            validation = {"check": "provider_json_parse", "path": "$", "detail": "Provider reply was not valid JSON; see the archived raw output."}
                        elif code == "codex_cli_failed":
                            raw = getattr(error, "raw_record", {})
                            validation = {"check": "provider_exit_status", "path": "cli.exit_code",
                                "detail": f"Codex CLI exited with status {raw.get('exit_code')}; see archived CLI stderr."}
                        elif code == "codex_cli_timeout":
                            validation = {"check": "provider_timeout", "path": "duration_seconds", "detail": "Codex CLI did not finish before the configured timeout."}
                        elif code == "skill_provider_usage_limit":
                            validation = {"check": "provider_usage_limit", "path": "plan", "detail": getattr(error, "detail", "")}
                        elif code.startswith("skill_provider_http_error_"):
                            validation = {"check": "provider_http_status", "path": "http_status", "detail": "The AI provider returned an HTTP error; see archived status and output."}
                    if not attempt_ref:
                        attempt_record = getattr(error, "attempt_record", attempt_record)
                        attempt_ref, attempt_metadata = _persist_skill_attempt(
                            paths["root"], run_id, skill_id, attempt_record=attempt_record,
                            input_fingerprint=manifest.get("subskills", {}).get(skill_id, {}).get("input_fingerprint", source_fp),
                            outcome="rejected", failure_phase=failure_phase, error_code=code, validation=validation)
                    else:
                        attempt_metadata.update({"outcome": "rejected", "failure_phase": failure_phase, "error_code": code,
                            "validation_check": validation["check"], "validation_path": validation["path"],
                            "validation_detail": validation["detail"]})
                        (Path(paths["root"]) / attempt_ref / "attempt.json").write_text(json.dumps(attempt_metadata, indent=2), encoding="utf-8")
                    failure_type = type(error).__name__
                    if failure_type not in {"ValueError", "TypeError", "RuntimeError", "OSError", "TimeoutExpired", "JSONDecodeError"}:
                        failure_type = "UnexpectedError"
                    LOGGER.exception("Archie runtime subskill failed", extra={"subskill_id": skill_id, "failure_phase": failure_phase, "error_code": code})
                    attempt_metadata["model"] = attempt_record.get("raw_record", {}).get("model", attempt_record.get("model", ""))
                    (Path(paths["root"]) / attempt_ref / "attempt.json").write_text(json.dumps(attempt_metadata, indent=2), encoding="utf-8")
                    with _project_lock(project["id"]):
                        manifest = _read(run_path, {})
                        message = (getattr(error, "detail", "") or str(error))[:500] or subskill_defs[skill_id]["failure"]
                        manifest["subskills"][skill_id].update({"status": "failed",
                            "finished_at": time.time(), "error_code": code,
                            "failure_phase": failure_phase, "failure_type": failure_type,
                            "attempt_ref": attempt_ref, "validation_check": validation["check"],
                            "validation_path": validation["path"], "validation_detail": validation["detail"],
                            "blocker_ids": [], "remediation": message, "artifact_names": [],
                            "evidence_reviewed": _evidence_review_record(attempt_record, attempt_ref, attempt_metadata)})
                        _update_parent_stages(manifest, catalog, registry)
                        _write_manifest(run_path, manifest)
                    failures.add(skill_id)
                    pending.discard(skill_id)
        with _project_lock(project["id"]):
            manifest = _read(run_path, {})
            _update_parent_stages(manifest, catalog, registry)
            states = {manifest["subskills"][skill_id].get("status") for skill_id in selected}
            scan_state = manifest.get("preparation", {}).get("scan_reading", {}).get("status")
            if "failed" in states or scan_state == "failed":
                manifest["status"] = "failed"
            elif "blocked" in states or scan_state == "blocked":
                manifest["status"] = "blocked"
            else:
                manifest["status"] = "completed"
            if manifest["status"] == "completed" and _has_open_findings(paths, manifest):
                manifest["status"] = "needs_review"
            if manifest["status"] in {"needs_review", "failed", "blocked"}:
                manifest["remediation"] = "Some evidence tasks need review. Independent findings are preserved; review the exceptions before relying on draft inputs."
            manifest["dependency_fingerprints"] = _derived_dependency_fingerprints(paths)
            manifest["finished_at"] = time.time()
            _write_manifest(run_path, manifest)
    except Exception:
        with _project_lock(project["id"]):
            manifest = _read(run_path, {})
            if manifest.get("run_id") == run_id:
                manifest.update({"status": "failed", "finished_at": time.time(), "error_code": "skill_workflow_failed",
                                 "remediation": "Review the workflow status and retry the failed evidence stage."})
                _write_manifest(run_path, manifest)
    finally:
        with _LOCK:
            _RUNNING.discard(project["id"])


def _response(web, project):
    paths = _project_paths(project)
    catalog = load_catalog()
    manifest = _read(paths["manifest"], {})
    current_fp = _source_fingerprint(paths, catalog) if paths["ai_input"].exists() else ""
    source_stale = bool(manifest.get("source_fingerprint") and current_fp and manifest["source_fingerprint"] != current_fp)
    dependency_stale = False
    if manifest.get("dependency_fingerprints") and manifest.get("status") in {"completed", "needs_review", "provisional"}:
        dependency_stale = manifest["dependency_fingerprints"] != _derived_dependency_fingerprints(paths)
    stale = source_stale or dependency_stale
    if stale and manifest.get("status") in {"completed", "needs_review", "provisional"}:
        manifest["status"] = "stale"
        manifest["stale_reasons"] = ["PDF evidence, selected pages, skill instructions, or skill catalog changed." if source_stale else "A current domain artifact used by one or more skill proposals changed."]
        for row in manifest.get("subskills", {}).values():
            if row.get("status") in {"resolved", "provisional", "needs_review", "excluded"}:
                row["status"] = "stale"
    safe = deepcopy(manifest)
    safe.pop("error_details", None)
    def public_text(value):
        text = str(value or "")
        text = re.sub(r"(?:/Users/|/private/|/var/folders/|/tmp/|/home/)[^\s,;]+", "[project-local source]", text)
        text = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)", "[redacted]", text)
        return text[:500]
    labels = {
        "document_mapping": "Drawing set and page mapping", "project_location_weather": "Project location and weather",
        "rooms_geometry_gains": "Rooms, geometry, and gains", "opaque_envelope": "Opaque envelope",
        "openings_glazing_solar": "Openings, glazing, and solar", "airflow_process_air": "Airflow and process air",
        "ahu_airside": "AHU and air-side", "plant_hydraulics": "Plant and hydraulics",
        "final_design_policy": "Final design policy", "model_reconciliation_report": "Model reconciliation and report",
    }
    stages = []
    for skill_id in catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])):
        row = safe.get("skills", {}).get(skill_id, {})
        stages.append({"label": labels.get(skill_id, "Evidence review"), "status": row.get("status", "not_started"),
                       "summary": row.get("output_summary", {}), "remediation": public_text(row.get("remediation", ""))})
    scan_stage = safe.get("preparation", {}).get("scan_reading", {})
    if scan_stage and scan_stage.get("status") != "not_applicable":
        stages.append({"label": "Scanned plan area reading", "status": scan_stage.get("status", "queued"),
                       "summary": {}, "remediation": public_text(scan_stage.get("remediation", ""))})
    subskill_registry = load_subskill_registry()
    subskills = []
    for spec in subskill_registry["subskills"]:
        row = safe.get("subskills", {}).get(spec["id"], {})
        if spec["parent"] not in catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])):
            continue
        subskills.append({"id": spec["id"], "parent": spec["parent"], "status": row.get("status", "not_started"),
            "prerequisites": spec.get("depends_on", []), "blocker_ids": row.get("blocker_ids", []),
            "summary": row.get("output_summary", {}), "remediation": [public_text(item) for item in row.get("remediation", [])] if isinstance(row.get("remediation"), list) else [public_text(row.get("remediation", ""))],
            "stale": row.get("status") == "stale", "attempt_ref": row.get("attempt_ref", ""),
            "error_code": row.get("error_code", ""), "failure_phase": row.get("failure_phase", ""),
            "validation_check": row.get("validation_check", ""), "validation_path": row.get("validation_path", ""),
            "validation_detail": public_text(row.get("validation_detail", "")),
            "evidence_reviewed": deepcopy(row.get("evidence_reviewed", {})),
            "raw_output_url": web.safe_link(paths["root"] / row["attempt_ref"] / "raw_output.txt") if row.get("attempt_ref") else "",
            "prompt_url": web.safe_link(paths["root"] / row["attempt_ref"] / "prompt.txt") if row.get("attempt_ref") else ""})
    proposal_root = paths["root"] / "skill_workflow_runs" / str(safe.get("run_id", "")) / "proposals"
    decisions_path = paths["root"] / "skill_review_decisions.json"
    decisions = _read(decisions_path, {})
    findings = []
    if safe.get("run_id") and proposal_root.is_dir():
        for proposal_path in sorted(proposal_root.glob("*.json")):
            proposal = _read(proposal_path, {})
            subskill_id = proposal.get("subskill_id") or proposal_path.stem
            field_values = proposal.get("proposal_fields") or {}
            if not isinstance(field_values, dict):
                field_values = {}
            proposal_rows = [(field, value) for field, raw in field_values.items()
                             for value in (raw if isinstance(raw, list) else [raw]) if _finding_has_value(value)]
            unresolved = proposal.get("unresolved_fields", [])
            if proposal.get("status") != "not_applicable":
                for field, raw in field_values.items():
                    empty = raw is None or raw == "" or raw == [] or raw == {}
                    is_unresolved = any(_unresolved_matches(field, item) for item in unresolved)
                    if empty and not is_unresolved and proposal.get("status") not in {"blocked", "failed"}:
                        unresolved = [*unresolved, {"field": field, "reason": "No value was provided."}]
                for missing_index, missing in enumerate(unresolved):
                    missing_field = str(missing.get("field", "unknown field") if isinstance(missing, dict) else missing)
                    missing_reason = missing.get("reason", "") if isinstance(missing, dict) else ""
                    finding_id = f"{subskill_id}:missing:{missing_field}:{missing_index}"
                    decision = decisions.get("decisions", {}).get(finding_id, {})
                    if not _decision_is_current(decision, safe, current_fp):
                        decision = {}
                    findings.append({"id": finding_id, "subskill_id": subskill_id, "field": missing_field,
                        "target": "", "value": decision.get("value"), "status": decision.get("status", "missing"),
                        "evidence": "missing", "missing": True, "units": _finding_units(missing_field, None),
                        "pages": [], "citations": [], "inferences": [], "formula": "", "calculation": "",
                        "confidence": None, "alternatives": [], "unresolved_fields": [missing_field],
                        "reason": public_text(missing_reason), "input_applied": False,
                        "reviewer": decision.get("reviewer", ""), "reviewed_at": decision.get("updated_at", "")})
            for field, raw in (proposal.get("proposal_fields") or {}).items():
                rows = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
                for index, value in enumerate(rows):
                    finding_id = f"{subskill_id}:{field}:{index}"
                    decision = decisions.get("decisions", {}).get(finding_id, {})
                    if not _decision_is_current(decision, safe, current_fp):
                        decision = {}
                    cited_pages = []
                    if isinstance(value, dict):
                        cited_pages.extend(value.get(key) for key in ("page", "physical_page", "source_page")
                                           if isinstance(value.get(key), int))
                        for key in ("source_pages", "evidence_page_ids"):
                            cited_pages.extend(item for item in value.get(key, []) if isinstance(item, int)) if isinstance(value.get(key), list) else None
                    cited_pages.extend(row.get("page", row.get("physical_pdf_page", row.get("physical_page")))
                                       for row in proposal.get("citations", []) if isinstance(row, dict)
                                       and isinstance(row.get("page", row.get("physical_pdf_page", row.get("physical_page"))), int))
                    cited_pages = sorted(set(cited_pages))
                    target = _finding_target(value)
                    matching_inferences = _finding_inferences(proposal, field, target, len(proposal_rows))
                    inference_text = [public_text(item.get("method") or item.get("detail") or item.get("reason") or item.get("field", ""))
                                      for item in matching_inferences]
                    formula = value.get("formula", "") if isinstance(value, dict) else ""
                    calculation = value.get("calculation", value.get("calculation_step", "")) if isinstance(value, dict) else ""
                    if not calculation and matching_inferences:
                        calculation = matching_inferences[0].get("calculation", "")
                    citations = _finding_citations(proposal, value, cited_pages, len(proposal_rows))
                    evidence_label = _finding_evidence(value, field, cited_pages, matching_inferences, formula, calculation,
                                                       value.get("alternatives", proposal.get("alternatives", [])) if isinstance(value, dict) else proposal.get("alternatives", []))
                    if any(_finding_unresolved(field, target, index, item) for item in proposal.get("unresolved_fields", [])):
                        evidence_label = "missing"
                    default_status = (value.get("status", "proposed") if subskill_id == "scanned_printed_areas" and isinstance(value, dict)
                                      else evidence_label if evidence_label == "missing" else "proposed")
                    input_applied = (decision.get("status") == "accepted" and (
                        subskill_id in {"room_identity_use", "room_boundaries_areas", "scanned_printed_areas"}))
                    findings.append({"id": finding_id, "subskill_id": subskill_id, "field": field,
                        "value": decision.get("value", value), "status": decision.get("status", default_status),
                        "evidence": evidence_label, "missing": evidence_label == "missing", "target": target,
                        "units": _finding_units(field, value), "pages": cited_pages,
                        "citations": citations, "inferences": inference_text,
                        "input_applied": input_applied,
                        "formula": formula, "calculation": calculation,
                        "confidence": proposal.get("confidence"), "alternatives": proposal.get("alternatives", []),
                        "unresolved_fields": proposal.get("unresolved_fields", []),
                        "reviewer": decision.get("reviewer", ""), "reviewed_at": decision.get("updated_at", "")})
    _mark_conflicting_findings(findings)
    consent = _read(paths["root"] / "vision_extraction_settings.json", {})
    visible_status = safe.get("status", "not_started")
    blocked_reason = ""
    read_only = not consent.get("owner_opt_in") and not _case_file_route(project)
    if read_only:
        visible_status = "blocked"
        blocked_reason = ("Skills read the pages only after every page has been read (page reading on, and the Codex CLI "
                          "installed and signed in).")
    issues = [{"name": row["id"], "status": row["status"],
               "reason": public_text(row.get("validation_detail") or row.get("remediation", ""))}
              for row in subskills if row["status"] in {"failed", "blocked"}]
    scan_state = safe.get("preparation", {}).get("scan_reading", {})
    if scan_state.get("status") in {"failed", "blocked"}:
        issues.append({"name": "Scanned plan area reading", "status": scan_state["status"],
                       "reason": public_text(scan_state.get("remediation", ""))})
    return {
        "project_id": project["id"], "status": visible_status,
        "run_id": safe.get("run_id", ""), "source_fingerprint": safe.get("source_fingerprint", ""),
        "stale_reasons": [public_text(item) for item in safe.get("stale_reasons", [])], "remediation": public_text(safe.get("remediation", "")),
        "stages": stages, "findings": findings,
        "blocked_reason": public_text(blocked_reason or safe.get("blocked_reason", "")), "read_only": read_only,
        "subskills": subskills, "issues": issues, "answers": _read(paths["root"] / ANSWERS_FILE, {}).get("answers", {}),
        "answer_options": _answer_options(web, project, findings),
        "preparation": {"status": safe.get("preparation", {}).get("status", "not_started"),
                        "candidate_count": safe.get("preparation", {}).get("candidate_count", 0),
                        "model_input": safe.get("preparation", {}).get("model_input", {}),
                        "scan_reading": safe.get("preparation", {}).get("scan_reading", {}),
                        "remediation": safe.get("preparation", {}).get("remediation", "")},
        "catalog": {"id": catalog["catalog_id"], "version": catalog["schema_version"]},
        "artifact_links": {name: web.safe_link(paths["root"] / name) for name in {artifact for row in safe.get("skills", {}).values() for artifact in row.get("artifact_names", [])} if (paths["root"] / name).exists()},
    }


def _finding_units(field, value):
    text = f"{field} {json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value}".lower()
    if any(token in text for token in ("_m2", "m²", "area_m2")):
        return "m²"
    if any(token in text for token in ("_mm", "height_mm", "width_mm")):
        return "mm"
    if any(token in text for token in ("_kw", "kw")):
        return "kW"
    if any(token in text for token in ("_w", "watt")):
        return "W"
    if "_c" in text or "temperature" in text:
        return "°C"
    if any(token in text for token in ("_l_s", "l/s", "airflow")):
        return "L/s"
    return ""


def _finding_has_value(value):
    return value is not None and value != "" and value != [] and value != {}


def _unresolved_matches(field, unresolved):
    value = unresolved.get("field", "") if isinstance(unresolved, dict) else unresolved
    value = str(value).strip()
    field = str(field)
    return bool(value) and (value == field or value.startswith((field + ".", field + "[")))


def _finding_unresolved(field, target, index, unresolved):
    value = unresolved.get("field", "") if isinstance(unresolved, dict) else unresolved
    value = str(value).strip()
    field = str(field)
    if value == field:
        return True
    match = re.match(rf"^{re.escape(field)}\[(\d+)\](?:\.|$)", value)
    if match:
        return int(match.group(1)) == index
    if target and value.startswith(field + "["):
        return str(target) in value
    return False


def _decision_is_current(decision, manifest, current_fp):
    return bool(isinstance(decision, dict) and decision.get("run_id") == manifest.get("run_id")
                and current_fp and decision.get("source_fingerprint") == current_fp
                and manifest.get("source_fingerprint") == current_fp)


def _finding_target(value):
    if not isinstance(value, dict):
        return ""
    for key in ("room_id", "room", "surface_id", "opening_id", "ahu_id", "plant_id", "page"):
        if value.get(key) is not None:
            return str(value[key])
    return ""


def _finding_inferences(proposal, field, target, proposal_row_count):
    result = []
    for item in proposal.get("inferences", []) if isinstance(proposal.get("inferences"), list) else []:
        if not isinstance(item, dict):
            continue
        inference_field = str(item.get("field", ""))
        inference_target = str(item.get("target", item.get("room_id", item.get("room", ""))))
        if inference_field == field and (not inference_target or not target or inference_target == target):
            result.append(item)
        elif not inference_field and proposal_row_count == 1:
            result.append(item)
    return result


def _finding_citations(proposal, value, pages, proposal_row_count):
    refs = []
    if isinstance(value, dict):
        source = value.get("citations", value.get("evidence", []))
        if isinstance(source, list):
            refs.extend(source)
        if value.get("excerpt"):
            refs.append({"page": value.get("page") or value.get("physical_page"), "excerpt": value["excerpt"]})
    source = proposal.get("citations", [])
    if isinstance(source, list) and (proposal_row_count <= 1 or not refs):
        refs.extend(source)
    seen, output = set(), []
    for row in refs:
        if not isinstance(row, dict):
            continue
        page = next((row.get(key) for key in ("page", "physical_page", "physical_pdf_page", "source_page")
                     if type(row.get(key)) is int), None)
        excerpt = row.get("excerpt", row.get("excerpt_or_crop", row.get("text", "")))
        normalized = {"page": page, "excerpt": str(excerpt or "")[:1000]}
        marker = (page, normalized["excerpt"])
        if marker not in seen and (page is not None or normalized["excerpt"]):
            seen.add(marker)
            output.append(normalized)
    if not output and pages:
        output = [{"page": page, "excerpt": ""} for page in pages]
    return output


def _finding_evidence(value, field, pages, inferences, formula, calculation, alternatives):
    if not _finding_has_value(value):
        return "missing"
    if isinstance(value, dict):
        proposed = value.get("value", value.get("proposed_value", value.get(field, value.get("taxonomy_id", value.get("area_m2")))))
        printed = value.get("printed_value", value.get("existing_printed_value"))
        if _finding_has_value(printed) and _finding_has_value(proposed) and proposed != printed:
            return "conflicting"
    if alternatives:
        return "conflicting"
    if inferences or formula or calculation:
        return "inferred"
    if pages:
        return "supported"
    return "missing"


def _mark_conflicting_findings(findings):
    groups = {}
    for row in findings:
        if row.get("evidence") == "missing" or not _finding_has_value(row.get("value")):
            continue
        if not row.get("target"):
            continue  # list rows without a room/surface/page aren't readings of one shared value
        groups.setdefault((row.get("field"), row.get("target")), []).append(row)
    for rows in groups.values():
        if len({_fingerprint(row.get("value")) for row in rows}) > 1:
            for row in rows:
                row["evidence"] = "conflicting"


def _has_open_findings(paths, manifest):
    run_id = manifest.get("run_id", "")
    proposal_root = paths["root"] / "skill_workflow_runs" / str(run_id) / "proposals"
    decisions = _read(paths["root"] / "skill_review_decisions.json", {}).get("decisions", {})
    for path in proposal_root.glob("*.json") if proposal_root.is_dir() else []:
        proposal = _read(path, {})
        if proposal.get("status") in {"failed", "blocked", "not_applicable"}:
            continue
        subskill = proposal.get("subskill_id", path.stem)
        for field, raw in proposal.get("proposal_fields", {}).items():
            rows = raw if isinstance(raw, list) else [raw]
            for index, value in enumerate(rows):
                if not _finding_has_value(value):
                    continue
                decision = decisions.get(f"{subskill}:{field}:{index}", {})
                if not _decision_is_current(decision, manifest, manifest.get("source_fingerprint")) or decision.get("status") not in {"accepted", "rejected"}:
                    return True
        unresolved = list(proposal.get("unresolved_fields", []))
        for field, raw in proposal.get("proposal_fields", {}).items():
            empty = raw is None or raw == "" or raw == [] or raw == {}
            if empty and not any(_unresolved_matches(field, item) for item in unresolved):
                unresolved.append({"field": field})
        for index, missing in enumerate(unresolved):
            field = str(missing.get("field", "unknown field") if isinstance(missing, dict) else missing)
            decision = decisions.get(f"{subskill}:missing:{field}:{index}", {})
            if not _decision_is_current(decision, manifest, manifest.get("source_fingerprint")) or decision.get("status") not in {"accepted", "rejected"}:
                return True
    return False


def _blocked_manifest(catalog, source_fp, scope, reason, code):
    manifest = _new_manifest(catalog, source_fp, scope)
    manifest.update({"status": "blocked", "finished_at": time.time(), "error_code": code,
                     "blocked_reason": reason, "remediation": reason})
    for row in manifest.get("subskills", {}).values():
        if row.get("status") == "queued":
            row.update({"status": "blocked", "finished_at": time.time(), "error_code": code,
                        "remediation": [reason]})
    return manifest


def start_after_reading(web, project):
    """Pass 1 (and pass 2, when it has kinds to read) finished: run the skills on their case files."""
    if web is None or not _case_file_route(project) or not _project_paths(project)["ai_input"].exists():
        return None
    return post(web, project, {"action": "start", "scope": "pdf_review", "automatic": True})


def start_after_analysis(web, project):
    """Start the focused PDF review only when this project's owner opted in."""
    paths = _project_paths(project)
    settings = _read(paths["root"] / "vision_extraction_settings.json", {})
    if not settings.get("owner_opt_in") or not paths["ai_input"].exists():
        return _response(web, project)
    return post(web, project, {"action": "start", "scope": "pdf_review", "automatic": True})


def _answer_options(web, project, findings):
    if not any(row.get("subskill_id") == "information_needs" and row.get("field") == "needs" for row in findings):
        return {}
    from backend import need_answers_service
    try:
        return need_answers_service.options(web, project)
    except Exception:
        return {}


ANSWERS_FILE = "information_answers.json"
ANSWER_SOURCES = {"spec_sheet", "supplier", "client", "site_visit", "mechanical_drawings", "landlord", "standard_or_reference", "other"}


def _answer_need(web, project, data):
    """Record the operators' answer to one item on the "What we need to find" list, and use it where it can be used.

    The answer has a kind (room area, ceiling height, people, lighting, an equipment rating, what is above the
    tenancy, or a kind kept as a note), a value and where it came from. need_answers_service puts it into the
    calculation through the existing input route; the record says whether it was used and how.
    """
    from backend import need_answers_service
    paths = _project_paths(project)
    current = _response(web, project)
    need = next((row for row in current["findings"] if row["id"] == data.get("finding_id")
                 and row["subskill_id"] == "information_needs" and row["field"] == "needs"), None)
    if not need:
        raise ValueError("That item isn't on the current list. Refresh and try again.")
    answer = " ".join(str(data.get("value") if data.get("value") not in (None, "") else data.get("answer") or "").split())[:500]
    source = str(data.get("source") or "")
    if not answer:
        raise ValueError("Type the answer before saving it.")
    if source not in ANSWER_SOURCES:
        raise ValueError("Say where the answer came from.")
    result = need_answers_service.apply(web, project, {**data, "value": data.get("value") if data.get("value") not in (None, "") else answer})
    value = need["value"] if isinstance(need["value"], dict) else {}
    stored = _read(paths["root"] / ANSWERS_FILE, {"answers": {}})
    stored.setdefault("answers", {})[need["id"]] = {
        "target": value.get("target", ""), "field": value.get("field", ""), "kind": data.get("kind") or "other",
        "room": data.get("room") or "", "equipment_id": data.get("equipment_id") or "", "answer": answer, "source": source,
        "note": " ".join(str(data.get("note") or "").split())[:300], "applied": result["applied"], "summary": result["summary"],
        "by": " ".join(str(data.get("reviewer") or "").split())[:80] or "Operator", "at": time.time()}
    _atomic_json(paths["root"] / ANSWERS_FILE, stored)
    response = _response(web, project)
    response["answer_options"] = need_answers_service.options(web, project)
    return response


def _review_finding(web, project, data):
    paths = _project_paths(project)
    consent = _read(paths["root"] / "vision_extraction_settings.json", {})
    if not consent.get("owner_opt_in") and not _case_file_route(project):
        raise ValueError("Page reading is off for this job, so its findings are read-only. Switch it on and retry the review.")
    manifest = _read(paths["manifest"], {})
    if not manifest.get("run_id") or manifest.get("status") == "stale":
        raise ValueError("Run PDF review again before recording a finding decision.")
    if manifest.get("source_fingerprint") != _source_fingerprint(paths, load_catalog()):
        raise ValueError("PDF review findings are stale. Run the review again before accepting them.")
    finding_id = str(data.get("finding_id", ""))
    decision = str(data.get("decision", ""))
    if not finding_id or decision not in {"accepted", "rejected"}:
        raise ValueError("Choose a finding and accept or reject it.")
    current = _response(web, project)
    finding = next((row for row in current.get("findings", []) if row.get("id") == finding_id), None)
    if finding is None:
        raise ValueError("The finding is not part of the current PDF review.")
    value = data.get("value", finding["value"])
    original = finding["value"]
    if decision == "accepted" and finding.get("missing") and not _finding_has_value(value):
        raise ValueError("Enter a value before saving this missing finding.")
    if decision == "accepted" and finding.get("evidence") == "conflicting" and value == original and not data.get("conflict_choice"):
        raise ValueError("Choose one of the conflicting readings or edit the value before saving.")
    if not finding.get("missing") and type(value) is not type(original) and not (isinstance(original, (int, float)) and type(value) in {int, float}):
        raise ValueError("Edited finding must keep the original value type.")
    reviewer = " ".join(str(data.get("reviewer", "")).split())[:100] or "Operator"
    if finding["subskill_id"] == "scanned_printed_areas" and decision == "accepted":
        if value != original:
            raise ValueError("Accept the validated scanned areas as shown, then edit individual room areas in Rooms.")
        existing_decision = _read(paths["root"] / "skill_review_decisions.json", {}).get("decisions", {}).get(finding_id, {})
        if not (existing_decision.get("run_id") == manifest["run_id"] and existing_decision.get("status") == "accepted"):
            from backend import autonomous_tasks_service, calculation_extraction_service, room_use_resolution_service
            page = original.get("page") if isinstance(original, dict) else None
            scan_path = paths["root"] / "skill_workflow_runs" / manifest["run_id"] / "proposals" / "scanned_printed_areas.json"
            scan_proposal = _read(scan_path, {})
            raw_reply = scan_proposal.get("raw_replies", {}).get(str(page))
            record = autonomous_tasks_service._current_task(paths["root"], "S1_printed_areas", f"page-{page}")
            if not isinstance(raw_reply, dict) or not isinstance(record, dict):
                raise ValueError("The scanned area evidence is no longer available. Retry PDF review before accepting it.")
            autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": "S1_printed_areas",
                "target": f"page-{page}", "reply": json.dumps(raw_reply, ensure_ascii=False)})
            applied = autonomous_tasks_service._current_task(paths["root"], "S1_printed_areas", f"page-{page}")
            if not applied or not applied.get("applied_value", {}).get("rooms"):
                raise ValueError("The printed areas did not pass S1 validation and were not applied.")
            room_use_resolution_service.post(web, project, {"action": "resolve"})
            calculation_extraction_service.post(web, project, {"action": "build"})
    artifact_path = paths["root"] / "skill_review_decisions.json"
    artifact = _read(artifact_path, {"schema_version": 1, "decisions": {}})
    artifact.setdefault("decisions", {})[finding_id] = {"status": decision, "value": value,
        "reviewer": reviewer, "updated_at": time.time(), "run_id": manifest["run_id"],
        "source_fingerprint": manifest["source_fingerprint"]}
    _atomic_json(artifact_path, artifact)
    # Geometry proposals are intentionally withheld from the calculation
    # evidence builder until the operator accepts them.
    if finding["subskill_id"] == "room_boundaries_areas" and decision == "accepted":
        from backend import calculation_extraction_service
        calculation_extraction_service.post(web, project, {"action": "build"})
    if finding["subskill_id"] == "room_identity_use" and decision == "accepted":
        from backend import room_use_resolution_service
        room_use_resolution_service.post(web, project, {"action": "resolve"})
    return _response(web, project)


def get(web, project):
    return _response(web, project)


def post(web, project, data):
    action = str((data or {}).get("action", "start"))
    if action == "review_finding":
        return _review_finding(web, project, data)
    if action == "answer_need":
        return _answer_need(web, project, data)
    if action not in {"start", "retry"}:
        raise ValueError("Skill workflow action must be start, retry, review_finding or answer_need.")
    explicit_scope = (data or {}).get("scope")
    scope = str(explicit_scope or "pdf_review")
    if scope not in _WORKFLOW_SCOPES:
        raise ValueError("Skill workflow scope must be one of: " + ", ".join(sorted(_WORKFLOW_SCOPES)) + ".")
    paths = _project_paths(project)
    if not paths["ai_input"].exists():
        raise ValueError("Analyse the PDF before starting the skill workflow.")
    catalog = load_catalog()
    source_fp = _source_fingerprint(paths, catalog)
    with _LOCK:
        existing = _read(paths["manifest"], {})
        if action == "retry" and not explicit_scope and existing.get("scope") in _WORKFLOW_SCOPES:
            # A retry repeats the interrupted or failed run's own scope.
            scope = existing["scope"]
        if project["id"] in _RUNNING:
            response = _response(web, project)
            response["deduplicated"] = True
            return response
        # A persisted `running` manifest without a live in-process worker is
        # an interrupted run (for example, after a local server restart). Do
        # not deduplicate forever against that orphaned state; make the
        # interruption visible and require the deliberate retry action.
        if existing.get("status") == "running":
            existing.update({"status": "failed", "finished_at": time.time(),
                "error_code": "skill_workflow_interrupted",
                "remediation": "The local skill run stopped before completion. Retry the workflow to resume from current evidence."})
            _write_manifest(paths["manifest"], existing)
            if action != "retry":
                raise ValueError("The previous skill workflow was interrupted. Retry it to continue.")
        settings = _read(paths["root"] / "vision_extraction_settings.json", {})
        case_file_route = _case_file_route(project)
        if not settings.get("owner_opt_in") and not case_file_route:
            return _response(web, project)
        allowed_pages = _consented_page_ids(paths)
        unavailable_reason = ""
        unavailable_code = ""
        if case_file_route:
            pass  # pass 1 has read the pages and the Codex CLI is the provider
        elif not allowed_pages:
            unavailable_reason, unavailable_code = "Select at least one page group covered by the project consent.", "consented_pages_unavailable"
        elif SKILL_PROVIDER_FACTORY is None and not os.environ.get("OPENAI_API_KEY"):
            unavailable_reason, unavailable_code = "The configured AI provider is unavailable. Set up provider credentials, then retry PDF review.", "skill_provider_unavailable"
        if unavailable_reason:
            blocked = _blocked_manifest(catalog, source_fp, scope, unavailable_reason, unavailable_code)
            _write_manifest(paths["manifest"], blocked)
            return _response(web, project)
        if (action == "start" and existing.get("source_fingerprint") == source_fp
                and existing.get("scope", "all") == scope and existing.get("status") in {"completed", "needs_review"}):
            return _response(web, project)
        if action == "retry" and existing.get("status") not in {"failed", "blocked", "stale", "needs_review"}:
            if existing:
                raise ValueError("Retry is available only after a failed or stale skill workflow.")
        manifest = _new_manifest(catalog, source_fp, scope)
        _write_manifest(paths["manifest"], manifest)
        _USAGE_STOPPED.pop(str(paths["root"].resolve()), None)  # a new run tries again
        _RUNNING.add(project["id"])
    thread = threading.Thread(target=_run_worker, args=(web, deepcopy(project), manifest["run_id"], source_fp, catalog), daemon=True)
    thread.start()
    response = _response(web, project)
    response["deduplicated"] = False
    return response
