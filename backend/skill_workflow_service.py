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
from backend.job_runner import BackgroundJob, JobCancelled, checkpoint, recovery, step_state, _context, _write_json
from backend.job_runner import _process_alive
from ai.skill_registry import compose_subskill_instructions, instruction_path, load_subskill_registry, subskill_instructions_fingerprint
from ai.vision_extraction import select_page_groups


_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "archie_skills_v1.json"
_SUBSKILL_PATH = Path(__file__).resolve().parents[1] / "config" / "archie_subskills_v1.json"
_INSTRUCTIONS_PATH = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.Lock] = {}
_RUNNING: set[str] = set()
_RUNNING_FOLDERS: dict[str, str] = {}
_CONTROL = BackgroundJob("skill_workflow_run.json", [("review", "Reviewing drawings")], "skill-control", "Review interrupted")
from backend.ai_provider import parallel_calls
_GEOMETRY_VECTOR_DPI = 180
LOGGER = logging.getLogger(__name__)
_ROOM_WAIT_SECONDS = 180
_TRANSIENT_PROVIDER_RETRIES = 1
_TRANSIENT_PROVIDER_RETRY_DELAY_S = 0.5
_TERMINAL = {"completed", "failed", "blocked", "stale"}
_SAFE_SUBSKILL_ERROR_CODES = {
    "subskill_execution_failed", "subskill_output_invalid", "subskill_citation_unknown_page",
    "subskill_numeric_inference_uncited", "subskill_contract_invalid", "drawing_coverage_missing",
    "room_inference_pending", "room_inference_failed", "room_evidence_missing", "codex_cli_failed",
    "codex_cli_timeout", "codex_cli_unavailable", "skill_provider_empty_output",
    "skill_provider_invalid_json", "subskill_field_type_invalid", "skill_provider_usage_limit", "skill_provider_unavailable",
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
    # Labels the model can derive or doesn't need: which file a record came from, a band restating the confidence,
    # the page classifier's label (the page index carries the page type) and run bookkeeping.
    "source_artifact", "confidence_band", "sheet_classification", "run_id",
})
_DEPENDENCY_PROMPT_FIELDS = ("status", "citations", "proposal_fields", "unresolved_fields")
_UNVALIDATED_GEOMETRY_KINDS = frozenset({"wall", "dimension"})
_LINE_ROLE_CODES = {"possible_wall_or_dimension": "W", "vector_context": "C"}
# The room-boundary task is the one task that must see a plan's full vector
# line index to cite real line IDs, so it carries a larger explicit budget.
# Room boundaries carries the plan's vector geometry, which it needs to propose outlines.
_TASK_PROMPT_BUDGET_CHARS = {"room_boundaries_areas": 180_000}
# Skills on the case-file route also carry the job section and the readings; still a hard cap (about 30,000 tokens).
_CASE_FILE_PROMPT_BUDGET_CHARS = 120_000
_ACCEPTED_GEOMETRY_STATUSES = frozenset({"geometry_confirmed", "ai_estimated"})
_VALUE_TARGET_KEYWORDS = {
    "glazing_properties": ("glazing", "window", "opening", "shgc", "solar", "u_value"),
    "construction_matching": ("construction", "u_value", "surface", "wall", "roof", "floor", "ceiling"),
}
# Workflow scopes: the two a job runs. Each is closed over its skills' prerequisites.
_WORKFLOW_SCOPES = {
    # PDF review is the "What we need to find" list (Phase 2's questions) and every skill it depends on, plus the
    # address step, which makes no AI call. The weather-source step is left out: the design weather comes from the
    # job's weather location, and that step only waited for a map-confirmed site. Skills whose results nothing reads
    # (airflow reconciliation, surface areas, shading, solar, zones, plant and coils) are left out.
    "pdf_review": frozenset({"information_needs", "address_confirmation"}),
    # Envelope inputs are produced by the drawing tasks; skills identify rooms and the site.
    "phase1_envelope": frozenset({
        "sheet_identity", "revision_scope", "page_relationships", "site_clue_extraction",
        "address_confirmation", "room_identity_use",
    }),
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
    # Every skill needs its instruction files: the shared policy, its playbook and one file per sub-skill.
    missing_files = []
    for path in [instruction_path(catalog)] + [instruction_path(catalog, skill["id"], subskill) for skill in skills
                                                for subskill in [None, *skill["subskills"]]]:
        if not path.is_file():
            missing_files.append(path.relative_to(instruction_path(catalog).parent).as_posix())
    if missing_files:
        raise ValueError("Runtime skill instructions are incomplete: " + ", ".join(missing_files))
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
        # Each sub-skill's own definition and instructions are in its input fingerprint (_execute_subskill), not here:
        # editing one skill's instructions or definition re-runs that skill and what depends on its result, not all.
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
    key = (tuple(_file_signature(path) for path in files), _fingerprint(catalog))
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
    result, stack = set(), [item for item in wanted if item in enabled]
    while stack:
        skill_id = stack.pop()
        if skill_id in result:
            continue
        result.add(skill_id)
        stack.extend(dep for dep in by_id[skill_id].get("depends_on", []) if dep in enabled)
    return result


def _new_manifest(catalog, source_fp, scope):
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
        "source_fingerprint": source_fp, "scope": scope, "status": "queued", "created_at": time.time(), "pid": os.getpid(),
        "updated_at": time.time(), "skills": skills, "subskills": subskill_states,
        "preparation": {"status": "not_started", "artifact_names": [], "error_code": "", "remediation": ""},
        "stale_reasons": [],

    }


INTERRUPTED_PROBLEM = "The server stopped while the AI was working through the drawings."
INTERRUPTED_REMEDIATION = "Retry to finish the remaining steps; finished steps are kept."


def is_running(project):
    return project["id"] in _RUNNING or str(Path(project["review_dir"]).resolve()) in _RUNNING_FOLDERS


def _orphaned(manifest, project_id, root=None):
    """True for a run recorded as queued or running that no live server is working on: the server running it stopped
    (closed, restarted or crashed). A run owned by another live server process (its pid) is still running."""
    if (manifest.get("status") not in {"queued", "running"} or project_id in _RUNNING
            or root is not None and _RUNNING_FOLDERS.get(str(Path(root).resolve())) == manifest.get("run_id")):
        return False
    pid = manifest.get("pid")
    return not (pid != os.getpid() and _process_alive(pid))


def _write_manifest(path, manifest):
    manifest["updated_at"] = time.time()
    checkpoint()
    with _CONTROL.mutation_lock({"review_dir": str(Path(path).parent)}):
        checkpoint()
        previous = _read(path, {})
        if previous.get("run_id") == manifest.get("run_id") and previous.get("restart_requested"):
            manifest.update({key: previous[key] for key in ("restart_requested", "restart_requested_at", "restart_scope") if key in previous})
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
        checkpoint()
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


# Document mapping is done in code from the page reading (pass 1 saw every page's image; these three tasks only
# ever received page data as text). Measured on Butcher Buffet, the AI versions re-listed pass 1's 38 page
# identities, found no revision on any page and accepted none of the 354 candidate page links, for about 130,000
# prompt characters over three calls.
_CODE_MAPPED_TASKS = frozenset({"sheet_identity", "revision_scope", "page_relationships"})
# Code-built tasks whose builder version is part of their input fingerprint, so a result made another way (by the AI
# before) or by an older builder is rebuilt. Every dependant's fingerprint includes its prerequisites' fingerprints,
# so a rebuild re-runs everything after the task, even when the rebuilt result is the same. The document-mapping
# tasks are therefore left out: they come before nearly every skill, and adding the marker re-ran a whole review
# (about 400,000 tokens on Butcher Buffet) to rebuild identical results. A change to their builder needs a re-read.
_CODE_BUILT_VERSIONS = {}
# A page link is kept only when a printed reference supports it (the playbook's rule); links proposed from shared
# roles or terminology are left out, as the AI version did.
_PRINTED_LINK_WORDS = ("callout", "reference", "section mark", "detail mark", "tag", "drawing index", "see dwg", "see drawing")


def _traced_rooms_proposal(subskill, project, input_fp):
    """room_boundaries_areas from the traced room outlines, without an AI call.

    On Butcher Buffet the AI version (the largest call, about 59,000 tokens with the plan's vector lines) returned no
    outline, wall, scale or area for any of 8 rooms. Areas come from tracing (by an operator or the automatic tracer),
    calibrated against printed dimensions; a room without one is left unresolved, so the needs list asks for it.
    """
    from backend import reviewer_room_geometry_service
    paths = _project_paths(project)
    traced = reviewer_room_geometry_service.current_traced_areas(paths["root"])
    room_use = _read(paths["root"] / "room_use_resolution.json", {})
    # Every room, including those outside comfort cooling (refrigeration rooms are "excluded" from that scope but
    # still rooms); only detections an operator marked as not a room are left out.
    not_rooms = reviewer_room_geometry_service._not_a_room_identities(room_use)
    rooms = [row for row in room_use.get("records", []) if isinstance(row, dict) and row.get("room_id")
             and str(row["room_id"]) not in not_rooms]
    candidates, citations, unresolved = [], [], []
    for room in rooms:
        trace = traced.get(str(room["room_id"]), {})
        calibration = trace.get("calibration") if isinstance(trace.get("calibration"), dict) else {}
        label = str(room.get("original_label") or room.get("label") or room["room_id"])
        area = trace.get("area_m2")
        candidates.append({
            "room_id": str(room["room_id"]), "label": label, "page": trace.get("page") or 0,
            "level": str(room.get("level_name") or room.get("level") or ""),
            "boundary_ref": trace.get("proof_id") or None, "boundary_points_px": [], "boundary_points_mm": [],
            "coordinate_units": "image_px", "wall_ids": [], "walls": [], "dimension_ids": [], "dimensions": [],
            "dimension_links": [], "scale_mm_per_px": round(float(calibration["mm_per_px"]), 4) if calibration.get("mm_per_px") else None,
            "area_m2": round(float(area), 2) if area else None,
            "formula": "traced outline area × (calibrated mm per px)²" if area else None,
            "calibration": {key: calibration[key] for key in ("source", "status", "printed_text") if key in calibration} or None,
            "source_crop": None, "independent_witnesses": [], "confidence": None, "conflicts": [],
            "unresolved_fields": [] if area else ["area_m2"], "alternatives": []})
        if area and trace.get("page"):
            citations.append({"page": trace["page"], "excerpt": f"{label}: traced outline, {float(area):.1f} m²"})
        elif not area:
            unresolved.append(f"{label}: no traced outline, so no area yet.")
    return {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "needs_review",
            "affected_ids": [row["room_id"] for row in candidates],
            "observations": [{"description": "Built in code from the traced room outlines; no AI call."}],
            "inferences": [], "citations": citations, "confidence": None, "alternatives": [],
            "unresolved_fields": unresolved, "remediation": [], "input_fingerprint": input_fp,
            "proposal_fields": {"geometry_candidates": candidates}, "artifact_names": []}


def _printed_tenant_finish_area(project, dependencies):
    """Sum uniquely tagged floor-finish areas for an AI-identified connected tenancy.

    This is intentionally narrow: it requires a whole-tenancy candidate, an AI
    room-identity proposal that classifies that tenant as conditioned, and at
    least two distinct TLxx area labels in the main floor-plan viewport. The
    result remains an area-only estimate and never supplies wall geometry.
    """
    paths = _project_paths(project)
    run = _read(paths["root"] / "ai_preliminary_run.json", {})
    rooms = (run.get("local_room_inference_proposal") or {}).get("rooms", [])
    tenant = next((row for row in rooms if isinstance(row, dict)
                   and row.get("zone_role") == "whole_tenancy_candidate"), None)
    if not tenant or not tenant.get("room_id"):
        return None
    identity = (dependencies.get("room_identity_use") or {}).get("proposal", {})
    identity_rows = (identity.get("proposal_fields") or {}).get("rooms", [])
    identity_row = next((row for row in identity_rows if isinstance(row, dict)
                         and row.get("room_id") == tenant["room_id"]), None)
    if not identity_row or identity_row.get("taxonomy_id") not in {"dining", "hospitality", "retail"}:
        return None
    spatial = _read(paths["spatial"], {})
    pages = {row.get("page"): row for row in spatial.get("pages", []) if isinstance(row, dict)}
    page_number = next((page for page in tenant.get("source_pages", []) if page in pages), None)
    page = pages.get(page_number, {})
    if not isinstance(page_number, int) or not page:
        return None
    bottom_bands = [row.get("bbox", [0, 0, 0, 0])[1] for row in page.get("title_blocks", [])
                    if isinstance(row, dict) and row.get("region") == "bottom_band"
                    and isinstance(row.get("bbox"), list) and len(row["bbox"]) == 4]
    bottom_y = min(bottom_bands) if bottom_bands else float(page.get("height", 0)) * 0.80
    page_width = float(page.get("width", 0) or 0)
    items = [row for row in page.get("standalone_text_items", []) if isinstance(row, dict)
             and isinstance(row.get("bbox"), list) and len(row["bbox"]) == 4]
    areas = []
    for item in items:
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*m(?:2|²)\s*", str(item.get("text", "")), re.I)
        if not match:
            continue
        bbox = item["bbox"]
        if page_width and bbox[2] > page_width * 0.82 or bbox[3] >= bottom_y:
            continue
        x_mid, y_mid = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        tags = []
        for tag in items:
            if str(tag.get("text", "")).strip().casefold() != "tl":
                continue
            tb = tag["bbox"]
            tx_mid, ty_mid = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
            if not (0 <= bbox[0] - tb[2] <= 60 and abs(y_mid - ty_mid) <= 18):
                continue
            numbers = [candidate for candidate in items if re.fullmatch(r"\s*0?\d{2}\s*", str(candidate.get("text", "")))]
            code = next((str(candidate["text"]).strip().lstrip("0") or "0" for candidate in numbers
                         if abs(((candidate["bbox"][0] + candidate["bbox"][2]) / 2) - tx_mid) <= 18
                         and abs(((candidate["bbox"][1] + candidate["bbox"][3]) / 2) - ty_mid) <= 18), None)
            if code:
                tags.append(f"TL{int(code):02d}")
        if len(set(tags)) != 1:
            continue
        areas.append({"tag": tags[0], "area_m2": float(match.group(1)), "page": page_number,
                      "excerpt": f"{tags[0]} {match.group(1)} m²", "bbox": bbox})
    unique = {row["tag"]: row for row in areas}
    if len(unique) < 2:
        return None
    if len({row["area_m2"] for row in unique.values()}) != len(unique):
        return None
    values = sorted(unique.values(), key=lambda row: row["tag"])
    return {"room_id": tenant["room_id"], "label": tenant.get("label", "Cafe"),
            "level": tenant.get("level_name", ""), "page": page_number,
            "area_m2": round(sum(row["area_m2"] for row in values), 6), "areas": values}


def _augment_printed_tenant_area(subskill, project, dependencies, proposal):
    """Preserve a validated sum of printed, tagged finish areas as a tenant area estimate."""
    if not isinstance(proposal, dict) or proposal.get("status") not in {"needs_review", "provisional", "resolved"}:
        return proposal
    fields = proposal.get("proposal_fields", {}) if isinstance(proposal, dict) else {}
    candidates = fields.get("geometry_candidates", []) if isinstance(fields, dict) else []
    area = _printed_tenant_finish_area(project, dependencies)
    if not area or any(isinstance(row, dict) and row.get("room_id") == area["room_id"]
                       and isinstance(row.get("area_m2"), (int, float)) and row.get("area_m2") > 0
                       for row in candidates):
        return proposal
    citations = [{"id": f"tenant-finish-{index + 1}", "page": row["page"],
                  "excerpt": row["excerpt"]} for index, row in enumerate(area["areas"])]
    values = [row["area_m2"] for row in area["areas"]]
    formula = " + ".join(f"{value:g} m²" for value in values) + f" = {area['area_m2']:g} m²"
    candidate = {
        "room_id": area["room_id"], "label": area["label"], "page": area["page"], "level": area["level"],
        "boundary_ref": None, "boundary_points_px": [], "boundary_points_mm": [], "coordinate_units": "area_only",
        "wall_ids": [], "walls": [], "dimension_ids": [], "dimensions": [], "dimension_links": [],
        "scale_mm_per_px": None, "area_m2": area["area_m2"], "formula": formula,
        "calibration": {"source": "printed_floor_finish_area_sum", "status": "provisional",
                        "assumption": "The distinct TL-tagged floor-finish regions shown on the connected cafe plan cover the whole tenancy.",
                        "area_operands": [{"tag": row["tag"], "area_m2": row["area_m2"], "page": row["page"]}
                                          for row in area["areas"]]},
        "source_crop": "Main floor plan: tagged TL finish regions", "independent_witnesses": [],
        "confidence": 0.86, "citations": citations,
        "conflicts": [], "unresolved_fields": ["closed_tenancy_perimeter", "wall_and_opening_geometry"],
        "alternatives": [],
    }
    fields["geometry_candidates"] = [*candidates, candidate]
    proposal["proposal_fields"] = fields
    proposal["affected_ids"] = list(dict.fromkeys([*proposal.get("affected_ids", []), area["room_id"]]))
    proposal["citations"] = [*proposal.get("citations", []), *citations]
    proposal.setdefault("observations", []).append({"description": "The parent cafe zone area is the sum of its uniquely tagged floor-finish areas; functional Counter, Food Preparation and Kiosk labels are not separate room areas.",
        "citation_ids": [row["id"] for row in citations]})
    proposal.setdefault("inferences", []).append({"field": f"geometry_candidates[{area['room_id']}].area_m2",
        "value": area["area_m2"], "unit": "m²", "method": "Summed the distinct printed TL finish areas shown within the AI-identified connected cafe tenancy; perimeter remains unresolved.",
        "formula": formula, "unrounded_operands": values, "confidence": 0.86,
        "citation_ids": [row["id"] for row in citations]})
    proposal["unresolved_fields"] = list(proposal.get("unresolved_fields", [])) + [
        "Whole-tenant area is available from the printed finish areas; the closed perimeter and envelope surfaces still need geometry resolution."]
    proposal["remediation"] = list(proposal.get("remediation", [])) + [
        "Use the provisional tenant area now; resolve the perimeter, exterior boundaries, and openings to complete Phase 1 envelope loads."]
    return _validate_subskill_output(subskill, proposal, load_subskill_registry(),
                                     allowed_pages=_consented_page_ids(_project_paths(project)))


def _document_map_proposal(subskill, project, input_fp):
    """sheet_identity, revision_scope or page_relationships, built from the page reading and drawing coverage."""
    from backend import page_inventory_service as pass1
    paths = _project_paths(project)
    pages = {int(page): row for page, row in (_read(paths["root"] / pass1.RESULT_FILE, {}).get("pages") or {}).items()}
    coverage = _read(paths["coverage"], {})
    covered = {row.get("page"): row for row in _page_rows(coverage) if isinstance(row, dict)}
    unresolved, observations, citations = [], [], []
    if subskill["id"] == "sheet_identity":
        identities = []
        for page, row in sorted(pages.items()):
            other = str(covered.get(page, {}).get("resolved_drawing_number") or covered.get(page, {}).get("drawing_number") or "")
            number = row.get("drawing_number") or None
            identities.append({"physical_page": page, "drawing_number": number, "title": row.get("title") or None,
                               "drawing_type": row.get("page_type") or None,
                               "alternatives": [other] if other and other != number else []})
        citations = [{"page": row["physical_page"], "excerpt": " ".join(str(part) for part in (row["drawing_number"], row["title"]) if part)}
                     for row in identities if row["drawing_number"] or row["title"]]
        missing = [str(row["physical_page"]) for row in identities if not row["drawing_number"]]
        if missing:
            unresolved.append("No drawing number was read on page(s) " + ", ".join(missing) + ".")
        fields = {"page_identities": identities}
        observations.append({"description": f"Page identities of all {len(identities)} pages, as the page reading read them."})
    elif subskill["id"] == "revision_scope":
        records = []
        for page in sorted(pages):
            row = covered.get(page, {})
            revision = row.get("revision") or row.get("resolved_revision") or None
            records.append({"page": page, "revision": revision, "issue_date": row.get("issue_date") or None,
                            "status": "read_from_title_block" if revision else "not_read", "precedence_citation_ids": []})
        citations = [{"page": row["page"], "excerpt": f"Revision {row['revision']}"} for row in records if row["revision"]]
        if not any(row["revision"] for row in records):
            unresolved.append("No revision mark was read on any page; every page is treated as current.")
        fields = {"revision_records": records}
    else:
        candidates = coverage.get("page_relationships", []) or coverage.get("cross_sheet_links", [])
        links, weak = [], 0
        for row in candidates if isinstance(candidates, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("from_page"), int) or not isinstance(row.get("to_page"), int):
                continue
            basis = row.get("basis") if isinstance(row.get("basis"), list) else [row.get("basis")]
            basis = [str(item) for item in basis if item]
            if any(word in item.casefold() for item in basis for word in _PRINTED_LINK_WORDS):
                links.append({"from_page": row["from_page"], "to_page": row["to_page"],
                              "relationship": str(row.get("relationship") or row.get("kind") or "related_page"), "evidence": basis})
                citations.append({"page": row["from_page"], "excerpt": "; ".join(basis)[:160]})
            else:
                weak += 1
        if weak:
            observations.append({"description": f"{weak} candidate link(s) rested only on shared roles or terminology, not a "
                                                "printed reference, and were left out."})
        fields = {"page_links": links}
    observations.insert(0, {"description": "Built in code from the page reading and drawing coverage; no AI call."})
    return {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "needs_review",
            "affected_ids": [], "observations": observations, "inferences": [], "citations": citations, "confidence": None,
            "alternatives": [], "unresolved_fields": unresolved, "remediation": [], "input_fingerprint": input_fp,
            "proposal_fields": fields, "artifact_names": []}


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
                    rows.extend({"source_artifact": name, **_task_view(subskill_id, name, row)} for row in records
                                if isinstance(row, dict) and _generic_record_relevant(subskill_id, name, row))
                else:
                    rows.append({"source_artifact": name, "status": value.get("status", "present"),
                                 "fingerprint": _fingerprint(value)})
        subskill = next((row for row in load_subskill_registry()["subskills"] if row["id"] == subskill_id), {})
        key = next(iter(subskill.get("proposal_fields", {})), "records")
        artifact_names = list(parent_by_subskill.get(subskill_id, ()))
    return {key: rows if isinstance(rows, list) else []}, artifact_names


# Findings operators don't review: the page register built in code from the page reading (shown under "What's on each
# page"), and the address step's bookkeeping fields (who confirmed it, when, under which consent), which are filled
# when the address is confirmed, not typed in.
_ADDRESS_BOOKKEEPING = {"confirmation_actor", "confirmation_time", "consent_ref"}


def _hidden_finding(row, case_file=True):
    if case_file and row.get("subskill_id") in _CODE_MAPPED_TASKS:
        return True
    return row.get("subskill_id") == "address_confirmation" and str(row.get("field", "")).split(".")[0] in _ADDRESS_BOOKKEEPING


def _issue_reason(row):
    """Why a skill didn't finish. A blocked skill gives its own reason (e.g. waiting for the address); the checking
    note is only a reason when the answer failed its checks ("passed" notes are never a reason)."""
    detail = str(row.get("validation_detail") or "")
    remediation = row.get("remediation") or ""
    remediation = " ".join(str(item) for item in remediation) if isinstance(remediation, list) else str(remediation)
    if row.get("status") == "blocked" or detail.startswith("Proposal passed"):
        return remediation or str(row.get("error_code", "")).replace("_", " ")
    return detail or remediation


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


_PAGE_IN_TEXT = re.compile(r"(?<![a-z0-9])p(?:age|g)?\s?\.?\s?(\d{1,4})(?![\d.])", re.IGNORECASE)


def _text_pages(text):
    """The pages a citation sentence names ("p21", "page 21", "pdf-ab12:p22"), in order, without repeats."""
    return list(dict.fromkeys(int(match) for match in _PAGE_IN_TEXT.findall(str(text))))


def _cited_pages_allowed(text, allowed_pages):
    pages = _text_pages(text)
    return bool(pages) and (allowed_pages is None or all(page in allowed_pages for page in pages))


_CASE_FILE_CITATION = re.compile(r"^\s*(?:[A-Za-z]{1,3}\d{1,3}\s*:\s*)?(task_evidence|case_file)\b", re.IGNORECASE)


def _text_citation(text):
    """A citation written as a sentence, as a citation record: its id ("c1: …"), source document ("pdf-…"), the pages
    it names and the sentence as the excerpt. A sentence citing the case file Archie supplied ("task_evidence.…",
    "case_file.…": its own defaults and readings) is a citation of that source. None when it names neither."""
    pages = _text_pages(text)
    if not pages and _CASE_FILE_CITATION.match(text):
        return {"source_document_id": "archie_case_file", "excerpt": text.strip()[:400]}
    if not pages:
        return None
    label = re.match(r"\s*([A-Za-z]{1,3}\d{1,3})\s*:", text)
    source = re.search(r"\b(pdf-[0-9a-f]{6,})\b", text)
    return {**({"id": label.group(1)} if label else {}), **({"source_document_id": source.group(1)} if source else {}),
            **({"page": pages[0]} if len(pages) == 1 else {"physical_pages": pages}), "excerpt": text.strip()[:400]}


_INFERENCE_PART = re.compile(r"\s*([a-z_]+)\s*=\s*(.*?)\s*$", re.S)


def _inference_from_text(text):
    """An inference written as "field=heights[x].height_mm | value=2600 | unit=mm | method=... | citation_ids=p22,p29"
    as an object; text that doesn't start with a field name is returned unchanged (and fails the shape check)."""
    parts = str(text).split(" | ")
    first = _INFERENCE_PART.match(parts[0])
    if not first or first.group(1) != "field":
        return text
    item, key = {}, None
    for part in parts:
        match = _INFERENCE_PART.match(part)
        if match:
            key = match.group(1)
            item[key] = match.group(2)
        elif key:                                   # a " | " inside a value: keep it with that value
            item[key] = f"{item[key]} | {part.strip()}"
    value = item.get("value")
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"null", "none", ""}:
            item["value"] = None
        else:
            try:
                number = float(lowered)
                item["value"] = int(number) if number.is_integer() and "." not in lowered else number
            except ValueError:
                pass
    if isinstance(item.get("citation_ids"), str):
        item["citation_ids"] = [entry.strip() for entry in item["citation_ids"].split(",") if entry.strip()]
    return item


def _public_text(value):
    """Text safe to show: local file paths and anything that looks like a key are removed."""
    text = str(value or "")
    text = re.sub(r"(?:/Users/|/private/|/var/folders/|/tmp/|/home/)[^\s,;]+", "[project-local source]", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)", "[redacted]", text)
    return text[:500]


# The steps of the review in the words the Drawings screen uses (others fall back to their id, spaced out).
SUBSKILL_LABELS = {
    "sheet_identity": "Drawing titles", "revision_scope": "Drawing revisions", "page_relationships": "How the pages relate",
    "site_clue_extraction": "Site clues", "address_confirmation": "Site address",
    "weather_source_matching": "Weather location", "room_identity_use": "Room names and uses",
    "room_boundaries_areas": "Room areas", "ceiling_height_volume": "Ceiling heights",
    "surface_inventory": "List of walls, roof and floor", "surface_area": "Wall, roof and floor areas",
    "construction_matching": "Wall and roof construction", "boundary_resolution": "What each surface faces",
    "cross_sheet_opening_match": "Doors and windows", "glazing_properties": "Glass properties",
    "exposure_orientation": "Orientation", "shading": "Shading",
}


def _subskill_label(skill_id):
    return SUBSKILL_LABELS.get(skill_id) or str(skill_id).replace("_", " ").capitalize()


def _failure_reason(row):
    """Why a step failed, in one plain clause, told apart by kind: the AI's answer, the connection, time, account."""
    code = str(row.get("error_code") or "")
    remediation = row.get("remediation")
    text = " ".join(remediation) if isinstance(remediation, list) else str(remediation or "")
    detail = str(row.get("validation_detail") or "")
    if code == "skill_provider_usage_limit":
        return "usage_limit", "the AI account's usage limit was reached"
    if code == "skill_workflow_interrupted":
        return "interrupted", "the server stopped while it was running"
    if "connection to the" in text and "AI service failed" in text:
        return "connection", "the connection to the AI service failed, so nothing was learned from the drawings"
    if "No reply from the AI within" in text or code == "codex_cli_timeout":
        return "timeout", "the AI didn't reply in time, so nothing was learned from the drawings"
    if code == "skill_provider_invalid_json" and "cut off" in text:
        return "cut_off", "the AI's answer was too long and was cut off, also when sent again with less thinking"
    if code == "skill_provider_invalid_json":
        return "unreadable_reply", "the AI's reply couldn't be read"
    if row.get("failure_phase") == "proposal_validation" or code.startswith("subskill_"):
        return "answer_rejected", f"the AI's answer didn't pass the checks ({_public_text(detail or text)[:200]})"
    if code.startswith("skill_provider_http_error_"):
        return "service_error", "the AI service returned an error"
    return "failed", _public_text(text or detail)[:200] or "it stopped unexpectedly"


def _failed_steps(subskills):
    """Each failed step with its reason and the steps that use its result (they are redone on retry if it changes)."""
    in_scope = {row["id"]: row for row in subskills if row.get("status") not in {"not_in_scope", "not_applicable"}}
    steps = []
    for row in in_scope.values():
        if row.get("status") != "failed":
            continue
        dependents, frontier = [], [row["id"]]
        while frontier:
            current = frontier.pop()
            for other in in_scope.values():
                if current in (other.get("prerequisites") or []) and other["id"] not in dependents:
                    dependents.append(other["id"])
                    frontier.append(other["id"])
        kind, reason = _failure_reason(row)
        steps.append({"id": row["id"], "label": _subskill_label(row["id"]), "kind": kind, "reason": reason,
                      "uses_it": [_subskill_label(item) for item in dependents]})
    return steps


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
        # Some models write a one-item list as a bare sentence ("remediation": "Provide ..."): that is the same answer,
        # so it is kept as a one-item list. Any other type is still refused.
        if isinstance(result.get(key), str):
            result[key] = [result[key]] if result[key].strip() else []
        if key == "remediation" and isinstance(result.get(key), dict):
            # A common model shape is {error_code, next_action}. Keep the actionable instruction
            # instead of rejecting an otherwise usable proposal over this envelope field.
            details = result[key]
            action = details.get("next_action") or details.get("message")
            if action:
                code = str(details.get("error_code") or "").strip()
                result[key] = [f"{code}: {action}" if code else str(action)]
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
    # A malformed citation is dropped (and noted), not allowed to fail the whole answer: one bad reference used to
    # throw away a skill's findings and block every skill depending on it. Dropped citations are never trusted.
    kept, dropped = [], []
    for index, citation in enumerate(result["citations"]):
        if isinstance(citation, str):
            # Some models write a citation as a sentence ("c1: p21: 'Bar' on the floor finish plan"): the page it
            # names makes it a citation like any other; a sentence naming no page is dropped.
            citation = _text_citation(citation)
            if citation is None:
                dropped.append(f"citation {index + 1} is a sentence naming no page")
                continue
        if not isinstance(citation, dict):
            dropped.append(f"citation {index + 1} is not an object")
            continue
        # Accept the explicit citation spelling used by runtime skill prompts as
        # well as the legacy aliases used by existing resolver artifacts.
        cited_pages = [citation.get(key) for key in ("page", "physical_page", "physical_pdf_page")
                       if type(citation.get(key)) is int]
        physical_pages = citation.get("physical_pages")
        if physical_pages is not None:
            if not isinstance(physical_pages, list) or not physical_pages or any(type(page) is not int for page in physical_pages):
                dropped.append(f"citation {index + 1} has no usable page list")
                continue
            cited_pages.extend(physical_pages)
        if not cited_pages and not any(citation.get(key) for key in ("source_id", "source_document_id", "url")):
            dropped.append(f"citation {index + 1} names no page or source")
            continue
        if allowed_pages is not None and any(page not in allowed_pages for page in cited_pages):
            unknown = sorted(page for page in cited_pages if page not in allowed_pages)
            dropped.append(f"citation {index + 1} cites pages not in the set: {unknown}")
            continue
        kept.append(citation)
    result["citations"] = kept                       # converted sentences replace the originals
    if dropped:
        note = "Dropped " + "; ".join(dropped) + "."
        result["remediation"] = list(result.get("remediation") or []) + [note] if isinstance(result.get("remediation"), list) else [note]
    kept_ids = {str(citation.get(key)) for citation in kept for key in ("id", "citation_id") if citation.get(key)}
    skills_by_id = {row["id"]: row for row in registry.get("subskills", [])
                    if isinstance(row, dict) and row.get("id")}

    def names_skill(text):
        for skill_id in skills_by_id:
            if re.match(rf"\s*{re.escape(skill_id)}[-_:][a-z0-9][a-z0-9._:-]*\s*$", text):
                # Finding IDs are generated per item (for example, "cold-display")
                # and cannot be checked against proposal field names here.
                return True
        return False
    inferences, uncited = [], []
    # A model sometimes writes each inference as one "field=... | value=... | unit=..." line instead of an object
    # (Butcher Buffet's ceiling heights, 2026-10-17, which failed the whole skill). Those lines are read into objects
    # and checked like any other inference; a line without a field name still fails below.
    if isinstance(result.get("inferences"), list):
        converted = [_inference_from_text(item) if isinstance(item, str) else item for item in result["inferences"]]
        read_as_text = sum(1 for before, after in zip(result["inferences"], converted) if before is not after)
        result["inferences"] = converted
        if read_as_text:
            result["remediation"] = list(result.get("remediation") or []) + [
                f"Read {read_as_text} inference{'s' if read_as_text != 1 else ''} written as text lines."]
    for index, inference in enumerate(result.get("inferences", [])):
        if not isinstance(inference, dict) or not inference.get("field"):
            _validation_error("inference_shape", f"inferences[{index}]", "Each inference must be an object with a field name.")
        if not inference.get("citation_ids") and isinstance(inference.get("citations"), list):
            # The same references under "citations": a kept citation's id, a sentence naming a page of this case file,
            # Archie's case file, or another skill's finding ("equipment_evidence-cold-display").
            usable = [str(entry) for entry in inference["citations"] if isinstance(entry, (str, int))
                      and (str(entry) in kept_ids or _cited_pages_allowed(str(entry), allowed_pages)
                           or _CASE_FILE_CITATION.match(str(entry)) or names_skill(str(entry)))]
            if usable:
                inference["citation_ids"] = usable
        value = inference.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and (not inference.get("citation_ids") or not inference.get("method")):
            # An uncited number is dropped (and noted), not allowed to throw away the rest of the reply: one such line
            # used to fail the whole "What we need to find" list.
            uncited.append(f"inference {index + 1} ({inference.get('field')})")
            continue
        inferences.append(inference)
    if uncited:
        result["inferences"] = inferences
        result["remediation"] = list(result.get("remediation") or []) + [
            "Dropped numbers given without a source or method: " + "; ".join(uncited) + "."]
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
    # A row that leaves a required value empty (null where text or a number is expected) means the AI couldn't tell:
    # that row is dropped and noted, not allowed to fail the whole skill (one opening with an unknown exposure used to
    # fail the orientation skill and mark the Phase 1 review failed).
    unknown_rows = []
    for field, descriptor in subskill["proposal_fields"].items():
        item_types = _contract_object_fields(descriptor)
        if item_types is None or not isinstance(values.get(field), list):
            continue
        kept = []
        for row_index, row in enumerate(values[field]):
            empty = [key for key, item_type in item_types.items()
                     if isinstance(row, dict) and row.get(key) is None and not _matches_contract_type(None, item_type)]
            if empty:
                unknown_rows.append(f"{field}[{row_index}] ({', '.join(empty)} not given)")
            else:
                kept.append(row)
        values[field] = kept
    if unknown_rows:
        result["remediation"] = list(result.get("remediation") or []) + [
            "Dropped rows with a required value left empty: " + "; ".join(unknown_rows) + "."]
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
        # With page reading on, every page is already sent to the AI one by one, so the skills may see every page.
        from backend import skill_case_file
        if skill_case_file.available({"review_dir": str(paths["root"])}):
            return {row.get("page") for row in _page_rows(_read(paths["coverage"], {})) if isinstance(row.get("page"), int)} \
                or {int(page) for page in (_read(paths["root"] / "page_inventory.json", {}).get("pages") or {})}
        return set()
    groups = select_page_groups(_read(paths["ai_input"], {}), _read(paths["coverage"], {}),
                                _read(paths["root"] / "page_inventory.json", {}))
    selected_ids = set(settings.get("selected_group_ids", [])) or {row["group_id"] for row in groups}
    allowed = {page.get("page") for group in groups if group.get("group_id") in selected_ids
               for page in group.get("pages", []) if isinstance(page.get("page"), int)}
    if "plan_geometry" in selected_ids:
        # Keep an explicitly dimensioned setout sheet available to room skills
        # even when the context ranker spends its page quota on other plans.
        # The page inventory is the source that distinguishes real plans from
        # pages whose OCR title was mistaken for a plan (renders/schedules).
        from backend import page_inventory_service
        inventory = _read(paths["root"] / page_inventory_service.RESULT_FILE, {}).get("pages", {})
        if isinstance(inventory, dict):
            for page, record in inventory.items():
                if not isinstance(record, dict) or record.get("page_type") != "floor_plan":
                    continue
                if record.get("floor_plan_kind") != "dimension_setout":
                    continue
                try:
                    allowed.add(int(page))
                except (TypeError, ValueError):
                    continue
    return allowed


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
    if subskill["id"] == "surface_area":
        # This task gets its detailed boundary, opening, and height evidence
        # from prerequisite proposals. Keep its page index to plans and
        # vertical/opening evidence; finish and equipment schedules otherwise
        # push a complete area derivation just over the provider budget.
        inventory = _read(paths["root"] / "page_inventory.json", {}).get("pages", {})
        relevant_types = {"floor_plan", "reflected_ceiling_plan", "elevation", "section", "detail",
                          "construction_or_detail", "opening_schedule", "window_schedule"}
        relevant_pages = {int(page) for page, record in inventory.items()
                          if str(page).isdigit() and isinstance(record, dict)
                          and (record.get("page_type") in relevant_types
                               or any(term in str(record.get("title", "")).casefold()
                                      for term in ("window schedule", "opening schedule")))}
        shared["page_index"] = [row for row in shared["page_index"] if row.get("page") in relevant_pages]
    indexed_fact_kinds = {
        "ceiling_height_volume": {"ceiling_height", "room_geometry"},
        "surface_area": {"ceiling_height", "room_geometry"},
        "construction_matching": {"materials_construction"},
        "cross_sheet_opening_match": {"windows_glazing"},
        "glazing_properties": {"windows_glazing", "materials_construction"},
        "exposure_orientation": {"windows_glazing", "location_orientation"},
    }.get(subskill["id"], set())
    if indexed_fact_kinds:
        inventory = _read(paths["root"] / "page_inventory.json", {}).get("pages", {})
        shared["indexed_fact_evidence"] = [
            {"page": int(page), "drawing_number": record.get("drawing_number", ""),
             "title": record.get("title", ""), "information": [
                 {"kind": item.get("kind"), "what": item.get("what"), "evidence": item.get("evidence")}
                 for item in record.get("information", []) if isinstance(item, dict)
                 and item.get("kind") in indexed_fact_kinds]}
            for page, record in inventory.items()
            if str(page).isdigit() and int(page) in allowed_pages and isinstance(record, dict)
            and any(isinstance(item, dict) and item.get("kind") in indexed_fact_kinds
                    for item in record.get("information", []))
        ]
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
        from backend import page_inventory_service
        inventory = _read(paths["root"] / page_inventory_service.RESULT_FILE, {}).get("pages", {})
        dimensioned_plan_ids = set()
        for raw_page, record in inventory.items() if isinstance(inventory, dict) else []:
            if isinstance(record, dict) and record.get("page_type") == "floor_plan" and record.get("floor_plan_kind") == "dimension_setout":
                try:
                    dimensioned_plan_ids.add(int(raw_page))
                except (TypeError, ValueError):
                    continue
        role_priority = {"main_floor_plan": 0, "primary_geometry_plan": 0, "floor_plan": 1,
                         "supporting_geometry_plan": 2, "dimension_plan": 2}
        geometry_page_rows.sort(key=lambda row: (
            0 if row.get("page") in linked_pages else 1,
            0 if row.get("page") in dimensioned_plan_ids else 1,
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
                     or item.get("page") in dimensioned_plan_ids)
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
                # Some vector plans contain thousands of dimension, furniture,
                # and hatch strokes. The attached plan image supplies complete
                # visual context; keep the strongest 350 indexed wall/context
                # lines per primary sheet so room-boundary prompts stay within
                # budget without losing stable vector IDs for validation.
                "line_candidates": [_compact_line(row) for row in (lines[:350] if primary_geometry_page else lines[:0])],
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
        vector_dimension_pages = {
            page.get("page") for page in vector_pages
            if isinstance(page.get("dimension_candidates"), list) and page.get("dimension_candidates")
        }
        shared["spatial_room_evidence"] = [
            {**{key: page.get(key) for key in ("page", "detected_type", "scale_candidates")},
             # The vector index and dimension-wall matches already carry the
             # dimension values and spans for these pages. Repeating the OCR
             # dimension list here doubled the largest part of the prompt on
             # multi-sheet plans and could block the entire geometry task.
             **({"dimension_candidates": page.get("dimension_candidates", [])}
                if page.get("page") not in vector_dimension_pages else {}),
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
    elif subskill["id"] == "surface_inventory":
        # Surface rows must anchor to the same page coordinate frame as the AI
        # room traces. Without the stable vector IDs and frame, the model can
        # see an edge but cannot register its geometry_ref to the boundary.
        from backend import page_inventory_service
        inventory = _read(paths["root"] / page_inventory_service.RESULT_FILE, {}).get("pages", {})
        geometry_pages = []
        for raw_page, record in inventory.items() if isinstance(inventory, dict) else []:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if page not in allowed_pages or not isinstance(record, dict):
                continue
            if record.get("page_type") == "floor_plan" and (
                    record.get("floor_plan_kind") == "proposed_layout"
                    or record.get("drawing_number") == "01.01"
                    or "layout plan" in str(record.get("title", "")).casefold()):
                geometry_pages.append((0, page, record))
            elif record.get("page_type") == "floor_plan" and record.get("floor_plan_kind") == "dimension_setout":
                geometry_pages.append((1, page, record))
        geometry_pages.sort(key=lambda row: (row[0], row[1]))
        vectors = _read(paths["vector"], {})
        vector_by_page = {row.get("page"): row for row in
                          (vectors.get("geometry_key_points", {}).get("pages", []) or []) if isinstance(row, dict)}
        shared["surface_geometry_pages"] = []
        # The boundary solver already gets complete dimension evidence from
        # the setout sheets. Surface inventory needs stable physical-edge IDs
        # on the primary plan, so keep that vector index small enough to fit
        # beside its full inventory and attached image frames.
        for _rank, page, record in geometry_pages[:1]:
            vector = vector_by_page.get(page, {})
            lines = [row for row in vector.get("line_candidates", []) if isinstance(row, dict)]
            lines.sort(key=lambda row: (
                0 if any(word in str(row.get("candidate_role_hint", "")).casefold()
                         for word in ("wall", "partition", "boundary")) else 1,
                -(float(row.get("length_px", 0) or 0)), str(row.get("candidate_id", "")),
            ))
            shared["surface_geometry_pages"].append({
                "page": page, "drawing_number": record.get("drawing_number", ""),
                "title": record.get("title", ""),
                "coordinate_frame": vector.get("coordinate_systems", {}).get("image_px", {}),
                "line_schema": "Each line is [candidate_id, x1, y1, x2, y2, role] in full-page canonical image pixels; use candidate IDs verbatim.",
                "line_candidates": [_compact_line(row) for row in lines[:120]],
            })
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
    if subskill["id"] in {"room_identity_use", "room_boundaries_areas"} and any(
            isinstance(group, dict) and group.get("group_id") == "plan_geometry" for group in manifest.get("groups", [])):
        # The selected plan-geometry group can omit a true dimensioned sheet
        # when the global page quota is filled by other plans. Supplement it
        # from page inventory, which distinguishes plans from render/schedule
        # sheets that happen to share an OCR title.
        from backend import page_inventory_service
        ai_input = _read(paths["ai_input"], {})
        page_rows = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", []) if isinstance(row, dict)}
        source_images = {row.get("page"): row for row in ai_input.get("source_files", {}).get("page_images", [])
                         if isinstance(row, dict)}
        inventory = _read(paths["root"] / page_inventory_service.RESULT_FILE, {}).get("pages", {})
        existing = {page.get("page") for group in manifest.get("groups", []) if isinstance(group, dict)
                    for page in group.get("pages", []) if isinstance(page, dict)}
        supplemental = []
        for raw_page, record in inventory.items() if isinstance(inventory, dict) else []:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if (page not in allowed_pages or page in existing or not isinstance(record, dict)
                    or record.get("page_type") != "floor_plan" or record.get("floor_plan_kind") != "dimension_setout"):
                continue
            image = source_images.get(page, {})
            if image:
                supplemental.append({**page_rows.get(page, {}), **image, "page": page,
                                     "image_path": image.get("path", image.get("image_path", "")),
                                     "title": record.get("title") or page_rows.get(page, {}).get("title", "")})
        if supplemental:
            plan_group = next(group for group in manifest["groups"] if group.get("group_id") == "plan_geometry")
            plan_group["pages"] = [*plan_group.get("pages", []), *supplemental]
    if subskill["id"] == "surface_inventory":
        # Surface inventory is the initializer for the thermal surface ledger,
        # so it must see the evidence types needed to create that inventory.
        # Page-group quotas often retain only the setout sheet; supplement the
        # consented dimension, ceiling, façade, and section pages from inventory.
        from backend import page_inventory_service
        ai_input = _read(paths["ai_input"], {})
        page_rows = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", []) if isinstance(row, dict)}
        source_images = {row.get("page"): row for row in ai_input.get("source_files", {}).get("page_images", [])
                         if isinstance(row, dict)}
        inventory = _read(paths["root"] / page_inventory_service.RESULT_FILE, {}).get("pages", {})
        def surface_page_rank(item):
            page, record = item
            kind = record.get("page_type")
            floor_kind = record.get("floor_plan_kind")
            number = str(record.get("drawing_number") or "")
            title = str(record.get("title") or "").casefold()
            if (kind == "floor_plan" and
                    (floor_kind == "proposed_layout" or "layout plan" in title or number == "01.01")):
                return (0, page)
            if floor_kind == "dimension_setout":
                return (1, page)
            if kind == "reflected_ceiling_plan":
                return (2, page)
            if kind == "elevation" and ("shopfront" in title or number == "02.01"):
                return (3, page)
            if kind == "section" and number in {"02.02", "02.03"}:
                return (4, page)
            return (9, page)
        surface_pages = []
        for raw_page, record in inventory.items() if isinstance(inventory, dict) else []:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if page in allowed_pages and isinstance(record, dict) and surface_page_rank((page, record))[0] < 9:
                surface_pages.append((page, record))
        surface_pages.sort(key=surface_page_rank)
        existing = {page.get("page") for group in manifest.get("groups", []) if isinstance(group, dict)
                    for page in group.get("pages", []) if isinstance(page, dict)}
        supplemental = []
        for page, record in surface_pages:
            image = source_images.get(page, {})
            if page in existing or not image:
                continue
            supplemental.append({**page_rows.get(page, {}), **image, "page": page,
                                 "image_path": image.get("path", image.get("image_path", "")),
                                 "title": record.get("title") or page_rows.get(page, {}).get("title", "")})
        if supplemental:
            manifest.setdefault("groups", []).append({"group_id": "surface_inventory_context", "pages": supplemental})
    pinned_pages = []
    if subskill["id"] in {"surface_area", "cross_sheet_opening_match", "glazing_properties"}:
        # Opening matching needs both a plan host edge and its elevation/schedule
        # witness. The broad consent groups can otherwise spend the image quota
        # on unrelated sections (which previously gave the matcher only pages
        # 41–42 and left every host ID null).
        from backend import page_inventory_service
        ai_input = _read(paths["ai_input"], {})
        page_rows = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", [])
                     if isinstance(row, dict)}
        source_images = {row.get("page"): row for row in ai_input.get("source_files", {}).get("page_images", [])
                         if isinstance(row, dict)}
        inventory = _read(paths["root"] / page_inventory_service.RESULT_FILE, {}).get("pages", {})
        rank_rows = []
        for raw_page, record in inventory.items() if isinstance(inventory, dict) else []:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if page not in allowed_pages or not isinstance(record, dict):
                continue
            title = str(record.get("title") or "").casefold()
            page_type = record.get("page_type")
            info = record.get("information", [])
            has_opening_schedule = page_type in {"schedule", "window_schedule", "opening_schedule"} and any(isinstance(item, dict) and
                (item.get("kind") == "windows_glazing" or
                 (item.get("kind") == "room_geometry" and "door" in str(item.get("what", "")).casefold()))
                for item in info)
            if subskill["id"] == "surface_area" and page_type == "floor_plan" and ("layout plan" in title or record.get("drawing_number") == "01.01"):
                rank = 0
            elif subskill["id"] == "surface_area" and page_type == "floor_plan" and record.get("floor_plan_kind") == "dimension_setout":
                # Prefer the 01.02 setout page. The 01.03 dimensions sheet can
                # carry a conflicting scale annotation for its main viewport;
                # the shopfront elevation has its own independently confirmed
                # scale and is more useful for opening heights in this packet.
                rank = 1 if str(record.get("drawing_number") or "") == "01.02" else 4
            elif subskill["id"] == "surface_area" and page_type == "reflected_ceiling_plan":
                rank = 2
            elif subskill["id"] == "surface_area" and page_type == "elevation" and ("shopfront" in title or record.get("drawing_number") == "02.01"):
                rank = 3
            elif subskill["id"] == "surface_area" and page_type == "section" and record.get("drawing_number") in {"02.02", "02.03"}:
                rank = 4
            elif page_type == "floor_plan" and ("layout plan" in title or record.get("drawing_number") == "01.01"):
                rank = 0
            elif page_type == "floor_plan" and record.get("floor_plan_kind") == "dimension_setout":
                rank = 1 if subskill["id"] == "cross_sheet_opening_match" else 2
            elif page_type == "elevation" and ("shopfront" in title or record.get("drawing_number") == "02.01"):
                rank = 2 if subskill["id"] == "cross_sheet_opening_match" else 0
            elif has_opening_schedule:
                rank = (3 if subskill["id"] == "cross_sheet_opening_match" else
                        5 if subskill["id"] == "surface_area" else 1)
            elif subskill["id"] == "cross_sheet_opening_match" and page_type == "section" and record.get("drawing_number") in {"02.02", "02.03"}:
                rank = 4
            else:
                continue
            image = source_images.get(page, {})
            image_path = Path(str(image.get("path", image.get("image_path", "")))) if image else None
            if image_path is not None and not image_path.is_absolute():
                image_path = (paths["root"] / image_path).resolve()
            if image_path is None or not image_path.is_file():
                continue
            rank_rows.append((rank, page, record))
        rank_rows.sort(key=lambda row: (row[0], row[1]))
        wanted = rank_rows[:(5 if subskill["id"] == "cross_sheet_opening_match" else 4)]
        existing = {page.get("page") for group in manifest.get("groups", []) if isinstance(group, dict)
                    for page in group.get("pages", []) if isinstance(page, dict)}
        supplemental = []
        for rank, page, record in wanted:
            if page not in pinned_pages:
                pinned_pages.append(page)
            image = source_images.get(page, {})
            if page in existing or not image:
                continue
            image_path = Path(str(image.get("path", image.get("image_path", ""))))
            if not image_path.is_absolute():
                image_path = (paths["root"] / image_path).resolve()
            supplemental.append({**page_rows.get(page, {}), **image, "page": page,
                "image_path": str(image_path),
                "title": record.get("title") or page_rows.get(page, {}).get("title", ""),
                "relevance": 1.0})
        if supplemental:
            manifest.setdefault("groups", []).append({"group_id": "opening_match_context", "pages": supplemental})
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
                            and any(term in title_text(row) for term in ("dimension plan", "dimensioned top view plan"))),
                           key=lambda row: row.get("page", 0))
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
    if subskill["id"] == "surface_inventory":
        inventory = _read(paths["root"] / "page_inventory.json", {}).get("pages", {})
        ranked_pages = []
        for raw_page, record in inventory.items() if isinstance(inventory, dict) else []:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if page not in allowed_pages or not isinstance(record, dict):
                continue
            kind, title, number = record.get("page_type"), str(record.get("title") or "").casefold(), str(record.get("drawing_number") or "")
            rank = ((0, page) if kind == "floor_plan" and
                    (record.get("floor_plan_kind") == "proposed_layout" or "layout plan" in title or number == "01.01") else
                    (1, page) if record.get("floor_plan_kind") == "dimension_setout" else
                    (2, page) if kind == "reflected_ceiling_plan" else
                    (3, page) if kind == "elevation" and ("shopfront" in title or number == "02.01") else
                    (4, page) if kind == "section" and number in {"02.02", "02.03"} else None)
            if rank is not None:
                ranked_pages.append((rank, page))
        for _rank, page in sorted(ranked_pages):
            if page not in pinned_pages:
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


def _transient_provider_failure(error):
    """Only retry transport/server failures that are safe to repeat unchanged."""
    code = str(getattr(error, "code", ""))
    if code == "skill_provider_usage_limit":
        return False
    if code.startswith("skill_provider_http_error_"):
        try:
            return int(code.rsplit("_", 1)[-1]) in {408, 429, 500, 502, 503, 504}
        except ValueError:
            return False
    message = " ".join(str(error).split()).casefold()
    return any(marker in message for marker in (
        "remote end closed connection", "remote disconnected", "connection reset",
        "connection aborted", "connection closed", "timed out", "temporary failure",
        "name or service not known", "network is unreachable",
    ))


def _provider_call_with_retry(call, attempt_record):
    """Retry one interrupted request once; leave auth, budget, and content errors alone."""
    retries = 0
    while True:
        checkpoint()
        try:
            result = call()
            if retries:
                attempt_record["transport_retries"] = retries
            return result
        except Exception as error:
            if retries >= _TRANSIENT_PROVIDER_RETRIES or not _transient_provider_failure(error):
                if retries:
                    attempt_record["transport_retries"] = retries
                raise
            retries += 1
            time.sleep(_TRANSIENT_PROVIDER_RETRY_DELAY_S)


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


# An airflow task sees the airflow records of its own kind (all 17 records went to each, about 12,000 characters),
# and of each room's internal gains only the field it uses (people, lighting and diversity records were 19,000 more).
_AIRFLOW_TYPES = {"outside_air": {"outside_air"}, "process_exhaust": {"process_exhaust", "make_up_air"},
                  "infiltration": {"infiltration"}}
_GAINS_FIELDS = {"outside_air": ("occupancy_count",), "process_exhaust": ("equipment",)}


def _task_view(subskill_id, artifact_name, row):
    """A record as one task needs it: an internal-gains room keeps only the fields that task uses."""
    if artifact_name == "internal_gains_resolution.json" and subskill_id in _GAINS_FIELDS and isinstance(row.get("fields"), dict):
        return {**row, "fields": {key: row["fields"][key] for key in _GAINS_FIELDS[subskill_id] if key in row["fields"]}}
    if artifact_name == "geometry_resolution.json" and subskill_id == "surface_area":
        # Area derivation needs the actual closed room trace and its scale
        # operands, not unrelated OCR room labels, witness IDs, or resolver
        # bookkeeping. This also leaves prompt room for matched opening areas.
        value = row.get("value", {}) if isinstance(row.get("value"), dict) else {}
        if row.get("kind") == "ai_room_geometry":
            geometry_keys = ("room_identity_id", "label", "boundary_points_px", "coordinate_units",
                             "area_px2", "area_m2", "source_page", "level_name", "proof_status",
                             "confidence_score", "resolution_mode", "calibration", "derivation")
            return {"source_artifact": artifact_name, "entity_id": row.get("entity_id"),
                    "kind": row.get("kind"), "label": row.get("label"),
                    "value": {key: value[key] for key in geometry_keys if key in value},
                    "source": row.get("source"), "confidence": row.get("confidence"),
                    "unresolved_fields": row.get("unresolved_fields", [])}
        return {key: row[key] for key in ("source_artifact", "entity_id", "kind", "label", "value",
                 "geometry_status", "source", "confidence", "unresolved_fields") if key in row}
    return row


def _generic_record_relevant(subskill_id, artifact_name, row):
    """Keep only records a generic domain task can use.

    Unvalidated vector wall and dimension candidates are geometry-resolver
    inputs, not surface or opening evidence; a single plan can produce
    thousands of them. Shared value-resolution registers are filtered to the
    targets the task owns when a target vocabulary is declared for it.
    """
    if artifact_name == "geometry_resolution.json":
        if subskill_id == "surface_area":
            return row.get("kind") == "ai_room_geometry"
        if row.get("kind") in _UNVALIDATED_GEOMETRY_KINDS:
            return row.get("geometry_status") in _ACCEPTED_GEOMETRY_STATUSES
        return True
    if artifact_name == "airflow_resolution.json" and subskill_id in _AIRFLOW_TYPES:
        return row.get("air_path_type") in _AIRFLOW_TYPES[subskill_id]
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
        hourly = _hourly_schedule_text(value)
        if hourly is not None:
            return hourly
        result = {}
        for key, item in value.items():
            if key in _PROMPT_NOISE_KEYS:
                stats["dropped_keys"] = stats.get("dropped_keys", 0) + 1
                continue
            item = _compact_for_prompt(item, stats)
            # An empty or null field says nothing the model can use; leaving it out is most of a prompt's saving.
            if item is None or item == "" or item == [] or item == {}:
                stats["dropped_empty"] = stats.get("dropped_empty", 0) + 1
                continue
            result[key] = item
        return result
    if isinstance(value, list):
        if _is_hourly(value):
            return _hours_text(value)
        result = []
        for item in (_compact_for_prompt(item, stats) for item in value):
            if isinstance(item, dict) and item in result:
                stats["dropped_duplicates"] = stats.get("dropped_duplicates", 0) + 1
                continue
            result.append(item)
        return result
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        # Pixel coordinates and fitted ratios carry 12+ digits the model can't use: keep 4 significant figures
        # (whole numbers from 1,000 up), enough for any measurement on a drawing.
        return float(round(value)) if abs(value) >= 1000 else float(f"{value:.4g}")
    if isinstance(value, str) and len(value) > _PROMPT_TEXT_LIMIT:
        stats["truncated_strings"] = stats.get("truncated_strings", 0) + 1
        return value[:_PROMPT_TEXT_LIMIT] + f"…[truncated {len(value) - _PROMPT_TEXT_LIMIT} chars]"
    return value


def _is_hourly(value):
    return (isinstance(value, list) and len(value) == 24
            and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value))


def _hours_text(values):
    """24 hourly fractions as runs, e.g. "00-10:0 10-23:1 23-24:0"."""
    runs, start = [], 0
    for hour in range(1, 25):
        if hour == 24 or values[hour] != values[start]:
            runs.append(f"{start:02d}-{hour:02d}:{values[start]:g}")
            start = hour
    return " ".join(runs)


def _hourly_schedule_text(value):
    """A day-type schedule ({weekday: [24], saturday: [24], ...}) as runs, with identical day types merged."""
    if not value or not all(_is_hourly(item) for item in value.values()):
        return None
    grouped = {}
    for day, hours in value.items():
        grouped.setdefault(_hours_text(hours), []).append(day)
    return {",".join(days): text for text, days in grouped.items()}


def _citation_line(citation):
    """A prerequisite's citation as "p<page>: <what it shows>"; the page is what a later task needs to look again."""
    if not isinstance(citation, dict):
        return citation
    page = next((citation.get(key) for key in ("physical_page", "page", "source_page") if citation.get(key) is not None), "?")
    text = next((str(citation[key]) for key in ("excerpt", "visual_evidence", "detail", "locator") if citation.get(key)), "")
    return f"p{page}: {text[:160]}"


# The needs list draws on every prerequisite's values and open items; their citations (about 7,000 characters) are
# for review, not for deciding what is still missing.
_PREREQUISITES_WITHOUT_CITATIONS = frozenset({"information_needs"})


def _compact_dependencies(dependencies, citations=True, consumer_id=None):
    """Pass prerequisites as their validated proposal content only, with citations shortened to page and quote."""
    result = {}
    for key, value in (dependencies or {}).items():
        proposal = value.get("proposal", {}) if isinstance(value, dict) else {}
        proposal = proposal if isinstance(proposal, dict) else {}
        content = {field: proposal.get(field) for field in _DEPENDENCY_PROMPT_FIELDS if field in proposal}
        if not citations:
            content.pop("citations", None)
        elif isinstance(content.get("citations"), list):
            content["citations"] = [_citation_line(citation) for citation in content["citations"]]
        if consumer_id == "surface_area":
            # Area derivation needs IDs, ownership, geometry references, and
            # evidence locations from its prerequisites. Exclude review prose
            # and unrelated glazing ratings so long drawings stay within a
            # responsive AI request while preserving every source reference.
            fields = content.get("proposal_fields")
            if isinstance(fields, dict):
                row_keys = {
                    "surface_inventory": ("surfaces", ("surface_id", "type", "thermal_role", "owner_ids",
                        "adjacent_space", "geometry_ref", "confidence", "citations")),
                    "cross_sheet_opening_match": ("matches", ("opening_id", "sighting_ids", "room_id",
                        "host_wall_id", "match_basis", "confidence", "citations")),
                    "glazing_properties": ("properties", ("opening_id", "opening_area_m2", "confidence", "citations")),
                }
                if key in row_keys:
                    list_name, keep = row_keys[key]
                    rows = fields.get(list_name)
                    fields = {list_name: [
                        {name: row[name] for name in keep if name in row}
                        for row in rows if isinstance(row, dict)]} if isinstance(rows, list) else {list_name: []}
                    content["proposal_fields"] = fields
                    content.pop("citations", None)
                    content.pop("unresolved_fields", None)
                    if key == "glazing_properties" and proposal.get("inferences"):
                        content["inferences"] = proposal["inferences"]
        result[key] = {"status": value.get("status", proposal.get("status", "")) if isinstance(value, dict) else "",
                       "proposal": content}
    return result


def _prompt_budget_chars(subskill_id="", case_file=False):
    raw = os.environ.get("ARCHIE_SKILL_PROMPT_MAX_CHARS", "").strip()
    default = max(_TASK_PROMPT_BUDGET_CHARS.get(subskill_id, _PROMPT_BUDGET_CHARS), _CASE_FILE_PROMPT_BUDGET_CHARS if case_file else 0)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(1_000, value)


def _bounded_proposal_prompt(subskill, dependencies, evidence, image_frames=(), case_file=False):
    """Build a scoped prompt and report whether it fits the per-task budget.

    Over-budget prompts are never truncated into a different task: the caller
    blocks the task without contacting a provider and records the breakdown.
    """
    stats = {}
    compact_evidence = _compact_for_prompt(evidence, stats)
    compact_dependencies = _compact_for_prompt(_compact_dependencies(
        dependencies, citations=subskill["id"] not in _PREREQUISITES_WITHOUT_CITATIONS,
        consumer_id=subskill["id"]), stats)
    prompt = _proposal_prompt(subskill, compact_dependencies, compact_evidence, image_frames=image_frames)
    budget = _prompt_budget_chars(subskill["id"], case_file=case_file)
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
              "dropped_empty_fields": stats.get("dropped_empty", 0),
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


def _prompt_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _proposal_prompt(subskill, dependencies, evidence, image_frames=()):
    from ai.skill_registry import load_catalog
    catalog = load_catalog()
    parent = next(skill for skill in catalog["skills"] if skill["id"] == subskill["parent"])
    instructions = compose_subskill_instructions(subskill["parent"], subskill)
    return (
        "Perform exactly one bounded Archie runtime subskill. Return one JSON object only; no markdown.\n"
        "The object must contain status, affected_ids, observations, inferences, citations, confidence, alternatives, unresolved_fields, remediation, and proposal_fields. "
        "Envelope types are strict: affected_ids is string[], observations/inferences/citations/alternatives are arrays, "
        "unresolved_fields and remediation are arrays, confidence is a number from 0.0 to 1.0 or null, and proposal_fields is an object. "
        "For remediation, return string[]; if you draft a structured object with next_action, put that action in a one-item string array. "
        "proposal_fields must contain every declared key with the declared type. "
        "For each non-empty object in a proposal_fields list, include item-level confidence (0.0-1.0) and row-specific citations with page and excerpt; do not copy one page citation across unrelated rows. "
        "Use not_applicable only when supplied evidence establishes the domain does not apply. "
        "Do not emit a calculation result, mutate an artifact, approve a value, or promote reviewed status.\n"
        f"Parent skill: {parent['id']} — {parent['purpose']}\n"
        f"Subskill: {subskill['id']} — {subskill['task']}\n"
        f"Declared proposal fields: {json.dumps(subskill['proposal_fields'], ensure_ascii=False)}\n"
        f"Allowed inputs: {json.dumps(subskill['inputs'], ensure_ascii=False)}\n"
        f"Validated prerequisite proposals: {_prompt_json(dependencies)}\n"
        f"Instructions:\n{instructions}\n"
        f"Attached image coordinate frames: {json.dumps(list(image_frames), ensure_ascii=False, allow_nan=False)}\n"
        f"Shared evidence package:\n{_prompt_json(evidence)}"
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
        # Fresh analyses may not have vector_geometry.json yet. The selected skill image is
        # then the full-page page-inventory render (not a crop), so it can serve as its own
        # canonical frame and preserve the printed-page calibration for draft estimates.
        if not all(isinstance(value, (int, float)) and value > 0 for value in (width, height)):
            width, height = render_width, render_height
            frame_source = "attached_full_page_render"
        else:
            frame_source = "vector_geometry"
        crop_bbox = image.get("crop_bbox_canonical_px")
        crop_bbox = crop_bbox if isinstance(crop_bbox, list) and len(crop_bbox) == 4 else [0.0, 0.0, float(width), float(height)]
        frame = {
            "physical_page": image["page"],
            "frame_source": frame_source,
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
        "http_status", "duration_seconds", "recovered_reply", "cached_reply_ref") if key in raw_record}
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
    image_limit = (7 if subskill["id"] == "surface_inventory" else
                   4 if subskill["id"] == "surface_area" else
                   5 if subskill["id"] == "cross_sheet_opening_match" else
                   3 if subskill["id"] == "glazing_properties" else
                   4 if subskill["id"] in {"room_identity_use", "room_boundaries_areas"} else 2)
    with tempfile.TemporaryDirectory(prefix="archie-skill-pages-") as render_dir:
        images = _relevant_consented_images(subskill, project, limit=image_limit, render_dir=render_dir)
        settings = _read(Path(project["review_dir"]) / "vision_extraction_settings.json", {})
        attempt_record["evidence_pages"] = sorted({row.get("page") for row in images if isinstance(row, dict)
                                                    and isinstance(row.get("page"), int)} |
                                                   _evidence_page_ids(evidence))
        if not images and not _has_page_evidence(evidence):
            attempt_record["raw_record"] = {"provider": "none", "reply_text": ""}
            return _NO_EVIDENCE_PAGES
        frames = (_attached_image_coordinate_frames(images, project)
                  if subskill["id"] in {"room_boundaries_areas", "surface_inventory", "surface_area",
                                        "cross_sheet_opening_match", "glazing_properties"} else ())
        prompt, budget = _bounded_proposal_prompt(subskill, dependencies, evidence, image_frames=frames)
        model = settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")
        attempt_record.update({"prompt": prompt, "images": images, "prompt_budget": budget,
                              "provider": provider_label, "model": model})
        if budget["status"] != "within_budget":
            attempt_record["raw_record"] = {"provider": "none_prompt_over_budget", "reply_text": ""}
            return None
        provider = provider_factory()
        try:
            provider_result = _provider_call_with_retry(
                lambda: provider.propose(prompt, image_paths=images), attempt_record)
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


_REUSABLE_STATUSES = {"needs_review", "provisional", "resolved", "completed", "not_applicable", "excluded"}


def _reuse_table(paths, previous):
    """Skills of the previous run that finished, with the inputs they ran on: a new run reuses a result instead
    of calling the AI again when that skill's inputs are unchanged (same readings, definition, case file and
    prerequisite results). Failed, blocked and over-budget skills are always run again."""
    run_id = (previous or {}).get("run_id")
    # Results the previous run could have reused but never reached (it was interrupted, or stopped at the usage
    # limit) stay on the list: they are still on disk and are matched by fingerprint, so nothing stale comes back.
    carried = ((previous or {}).get("reuse_from") or {}).get("subskills") or {}
    table = {skill_id: entry for skill_id, entry in carried.items() if isinstance(entry, dict)}
    for skill_id, row in ((previous or {}).get("subskills") or {}).items():
        raw_path = paths["root"] / str(row.get("attempt_ref") or "") / "raw_output.txt" if isinstance(row, dict) and row.get("attempt_ref") else None
        attempt_fp = _read(raw_path.parent / "attempt.json", {}).get("input_fingerprint") if raw_path is not None else None
        if (isinstance(row, dict) and row.get("status") == "failed" and row.get("failure_phase") == "proposal_validation"
                and attempt_fp and raw_path.is_file()):
            # The AI answered but the answer failed checking: re-check that reply (the checks may have been fixed)
            # before paying for a new one. The attempt records the inputs the reply was made from.
            table[skill_id] = {"run_id": run_id, "input_fingerprint": attempt_fp,
                               "raw_reply": str(raw_path.relative_to(paths["root"])), "status": "failed"}
            continue
        proposal_path = paths["root"] / "skill_workflow_runs" / str(run_id) / "proposals" / f"{skill_id}.json"
        if (run_id and isinstance(row, dict) and row.get("status") in _REUSABLE_STATUSES and row.get("input_fingerprint")
                and row.get("error_code") != "prompt_over_budget" and proposal_path.is_file()):
            entry = {"run_id": row.get("reused_from") or run_id, "input_fingerprint": row["input_fingerprint"],
                     "proposal": str(proposal_path.relative_to(paths["root"])), "status": row["status"],
                     "evidence_reviewed": row.get("evidence_reviewed", {})}
            older = table.get(skill_id)
            if older and older.get("input_fingerprint") != entry["input_fingerprint"]:
                # An older result made from other inputs stays as a fallback: a retry whose inputs match it again
                # (e.g. after an interrupted run with different inputs) reuses it instead of paying for a new answer.
                entry["fallback"] = {key: value for key, value in older.items() if key != "fallback"}
            table[skill_id] = entry
    return {"run_id": run_id, "subskills": table} if table else {}


def _reused_proposal(subskill, project, input_fp):
    """The previous run's result for this skill when its inputs are unchanged, else None."""
    paths = _project_paths(project)
    entry = (_read(paths["manifest"], {}).get("reuse_from") or {}).get("subskills", {}).get(subskill["id"])
    if entry and entry.get("input_fingerprint") != input_fp:
        entry = entry.get("fallback")
    if not entry or entry.get("input_fingerprint") != input_fp:
        return None
    if entry.get("raw_reply"):
        from backend import ai_provider
        try:
            raw = ai_provider.parse_json_reply((paths["root"] / entry["raw_reply"]).read_text(encoding="utf-8"))
        except (OSError, RuntimeError):
            return None
        proposal = _proposal_from_raw(subskill, raw, input_fp)
        try:  # still failing the checks: ask the AI again rather than fail on the same reply
            allowed = {row.get("page") for row in _page_rows(_read(paths["coverage"], {})) if isinstance(row.get("page"), int)}
            _validate_subskill_output(subskill, deepcopy(proposal), load_subskill_registry(), allowed_pages=allowed or None)
        except Exception:
            return None
        proposal.update({"provider_kind": "rechecked_previous_reply",
                         "_attempt_record": {"prompt": "", "images": [], "started_at": time.time(),
                                             "raw_record": {"provider": "rechecked_previous_reply", "run_id": entry["run_id"],
                                                            "reply_text": json.dumps(raw, ensure_ascii=False)[-20000:]}}})
        return proposal
    proposal = _read(paths["root"] / entry["proposal"], {})
    if not isinstance(proposal, dict) or not proposal.get("proposal_fields"):
        return None
    proposal = deepcopy(proposal)
    proposal.update({"provider_kind": "reused_from_previous_run", "reused_from": entry["run_id"],
                     "_attempt_record": {"prompt": "", "images": [], "started_at": time.time(),
                                         "raw_record": {"provider": "reused_from_previous_run", "run_id": entry["run_id"], "reply_text": ""},
                                         "evidence_pages": (entry.get("evidence_reviewed") or {}).get("pages", [])}})
    return proposal


def _case_file_route(project):
    """Skills read their case file (pass 1 and 2's findings plus key pages) through the Codex CLI with the
    ChatGPT sign-in: on when the job's page reading is on and pass 1 has read the pages."""
    from backend import skill_case_file
    if os.environ.get("ARCHIE_PAGE_READING", "").strip().lower() == "off":
        return False
    from backend import ai_provider
    if CASE_FILE_PROVIDER_FACTORY is None and not ai_provider.configured():
        return False
    return skill_case_file.available(project)


def _worth_a_call(subskill, project):
    from backend import skill_case_file
    return skill_case_file.worth_a_call(subskill["id"], project)


def _call_case_file_provider(subskill, project, dependencies, evidence, attempt_record):
    """Ask the AI for one sub-skill with its case file. Returns the raw proposal, or None when over budget."""
    from backend import skill_case_file
    case, images = skill_case_file.build(subskill["id"], project)
    image_rows = [{"page": page, "path": str(path)} for page, path in zip(case["attached_pages"], images)]
    frames = (_attached_image_coordinate_frames(image_rows, project)
              if subskill["id"] in {"room_boundaries_areas", "surface_inventory", "surface_area",
                                    "cross_sheet_opening_match", "glazing_properties"} else ())
    # The case file carries the page index, so the shared evidence's copy of it is left out (every call is smaller).
    evidence = {key: value for key, value in evidence.items() if key != "page_index"}
    prompt, budget = _bounded_proposal_prompt(subskill, dependencies, {**evidence, "case_file": case},
                                               image_frames=frames, case_file=True)
    # Still over the cap: shrink the case file step by step (the job section and the key pages always stay).
    trimmed = []
    for part in ("readings", "page_index", "extracted"):
        if budget["status"] == "within_budget":
            break
        if case.get(part):
            case = {**case, part: [] if isinstance(case[part], list) else {}}
            trimmed.append(part)
            prompt, budget = _bounded_proposal_prompt(subskill, dependencies, {**evidence, "case_file": case},
                                                       image_frames=frames, case_file=True)
    if trimmed:
        budget["case_file_trimmed"] = trimmed
    attempt_record.update({"prompt": prompt, "images": [{"page": page, "path": str(path)} for page, path in zip(case["attached_pages"], images)],
                           "prompt_budget": budget, "provider": "codex_case_file",
                           "evidence_pages": sorted(set(case["attached_pages"]) | {int(row[1:].split(" ", 1)[0]) for row in case["readings"]})})
    if budget["status"] != "within_budget":
        attempt_record["raw_record"] = {"provider": "none_prompt_over_budget", "reply_text": ""}
        return None
    return _send_case_file_prompt(project, subskill.get("_usage_purpose") or f"skill:{subskill['id']}", prompt, images, attempt_record)


def _send_case_file_prompt(project, purpose, prompt, images, attempt_record):
    """Send one case-file prompt (one sub-skill, or a group) and return the reply; records the model and raw reply in
    attempt_record. After the plan's usage limit, nothing more is sent for this job until a new run."""
    from backend import page_inventory_service
    key = str(Path(project["review_dir"]).resolve())
    model = os.environ.get("ARCHIE_CODEX_MODEL", "").strip() or "codex default"
    attempt_record["model"] = model
    if key in _USAGE_STOPPED:
        attempt_record["raw_record"] = {"provider": "codex_case_file", "model": model, "reply_text": ""}
        error = SkillProviderError("skill_provider_usage_limit", attempt_record["raw_record"])
        error.detail = _USAGE_STOPPED[key]
        raise error
    from backend import ai_provider
    try:
        provider = CASE_FILE_PROVIDER_FACTORY() if CASE_FILE_PROVIDER_FACTORY is not None else ai_provider.get(purpose)
    except ai_provider.ProviderUnavailable as error:
        unavailable = SkillProviderError("skill_provider_unavailable", attempt_record.get("raw_record", {}))
        unavailable.detail = str(error)
        raise unavailable from error
    model = getattr(provider, "model", "") or model
    attempt_record["model"] = model
    recorded = ai_provider.recorded(provider, project["review_dir"], purpose)
    try:
        try:
            result = _provider_call_with_retry(
                lambda: recorded.propose(prompt, image_paths=[str(path) for path in images]), attempt_record)
        except ai_provider.InvalidProviderReply as error:
            if not ai_provider.cut_off(error.raw_record):
                raise
            # Cut off at the output cap: send it once more with less thinking and more room (ai_provider).
            retry = CASE_FILE_PROVIDER_FACTORY() if CASE_FILE_PROVIDER_FACTORY is not None else ai_provider.get(purpose)
            retry.reasoning_effort = ai_provider.CUT_OFF_RETRY_REASONING
            retry.max_tokens_override = ai_provider.CUT_OFF_RETRY_MAX_OUTPUT_TOKENS
            usage = error.raw_record.get("usage") or {}
            attempt_record["cut_off_retry"] = {"first_output_tokens": usage.get("output_tokens"),
                                               "first_reasoning_tokens": usage.get("reasoning_output_tokens"),
                                               "reasoning": retry.reasoning_effort, "max_tokens": retry.max_tokens_override}
            again = ai_provider.recorded(retry, project["review_dir"], purpose)
            result = _provider_call_with_retry(
                lambda: again.propose(prompt, image_paths=[str(path) for path in images]), attempt_record)
    except ai_provider.InvalidProviderReply as error:
        raw_record = {**error.raw_record, "provider": error.raw_record.get("provider", getattr(provider, "name", "provider")),
                      "model": error.raw_record.get("model") or model}
        attempt_record["raw_record"] = raw_record
        rejected = SkillProviderError("skill_provider_invalid_json", raw_record)
        rejected.detail = (
            "The AI's answer was too long and was cut off, also when sent again with less thinking. Its response was "
            "saved; retry this review step."
            if ai_provider.cut_off(raw_record)
            else "The AI reply was not valid JSON. Its response was saved; retry this review step."
        )
        rejected.attempt_record = attempt_record
        raise rejected from error
    except ai_provider.UsageLimitReached as error:
        _USAGE_STOPPED[key] = str(error)
        attempt_record["raw_record"] = {"provider": "codex_case_file", "model": model, "reply_text": ""}
        stopped = SkillProviderError("skill_provider_usage_limit", attempt_record["raw_record"])
        stopped.detail = str(error)
        raise stopped from error
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
    if isinstance(result, tuple) and len(result) == 2:
        attempt_record["raw_record"] = {**(result[1] or {}), "provider": "codex_case_file", "model": model}
        return result[0]
    if isinstance(result, SkillProviderResult):
        attempt_record["raw_record"] = {**result.raw_record, "provider": "codex_case_file", "model": model}
        return result.proposal
    attempt_record["raw_record"] = {"provider": "codex_case_file", "model": model, "reply_text": json.dumps(result, ensure_ascii=False)}
    return result


# Sub-skills answered together in one call. Each call carries the same fixed cost (about 4,600 tokens of Codex
# overhead) plus the shared rules, job details and page index, and members of a group read the same pages, so a group
# pays for these once. Each member's answer is still checked, stored and fingerprinted on its own, so reuse and edits
# work per sub-skill as before. A group only contains sub-skills whose dependencies outside the group come earlier.
_SKILL_GROUPS = {
    "rooms_and_site": ("room_identity_use", "site_clue_extraction"),
    "internal_gains": ("occupancy_seating", "lighting_evidence", "equipment_evidence", "schedule_evidence", "outside_air"),
    "heights_and_exhaust": ("ceiling_height_volume", "process_exhaust"),
    "opaque_envelope": ("surface_inventory", "construction_matching"),
    "openings": ("cross_sheet_opening_match", "glazing_properties", "exposure_orientation"),
}
_GROUP_OF = {member: group for group, members in _SKILL_GROUPS.items() for member in members}
# On the case-file route these never call the AI.
_NO_CALL_TASKS = frozenset(_CODE_MAPPED_TASKS | set(_CODE_BUILT_VERSIONS) | {"address_confirmation", "weather_source_matching"})
_GROUP_PROMPT_BUDGET_CHARS = 130_000
_GROUP_REPLIES: dict = {}
_GROUP_FAILED: dict = {}      # a group call that failed: its other members fail with it in this run, not ask again
_GROUP_LOCKS: dict = {}
_GROUP_LOCKS_LOCK = threading.Lock()


def _group_member_reply(subskill, project, dependencies, evidence, attempt_record, source_fp):
    """One group member's raw proposal: from a single call answering every member still waiting, or from its own
    call when it is the only one waiting, the group prompt is over budget, or the reply left it out."""
    group = _GROUP_OF[subskill["id"]]
    paths = _project_paths(project)
    manifest = _read(paths["manifest"], {})
    key = (str(paths["root"].resolve()), manifest.get("run_id"), group)
    with _GROUP_LOCKS_LOCK:
        lock = _GROUP_LOCKS.setdefault(key, threading.Lock())
    with lock:
        answered = _GROUP_REPLIES.get(key, {})
        failed = _GROUP_FAILED.get(key)
        if failed and subskill["id"] in failed[0]:
            attempt_record.update(deepcopy(failed[1]))
            raise failed[2]
        if subskill["id"] not in answered:
            members = _group_members_to_ask(group, subskill["id"], project, manifest, answered, source_fp)
            if len(members) > 1:
                try:
                    replies = _ask_group(group, members, project, manifest, attempt_record)
                except SkillProviderError as error:
                    _GROUP_FAILED[key] = (set(members), {k: v for k, v in attempt_record.items() if k != "started_at"}, error)
                    raise
                if replies:
                    answered = {**answered, **replies}
                    for other in [item for item in _GROUP_REPLIES if item[0] == key[0] and item[1] != key[1]]:
                        _GROUP_REPLIES.pop(other, None)       # replies of an older run of this job are no longer needed
                    _GROUP_REPLIES[key] = answered
        entry = answered.get(subskill["id"])
    if entry is None:
        return _call_case_file_provider(subskill, project, dependencies, evidence, attempt_record)
    attempt_record.update(deepcopy(entry["attempt"]))
    return deepcopy(entry["raw"])


def _group_members_to_ask(group, asking, project, manifest, answered, source_fp):
    """The members one group call should answer: those in scope, not answered yet, whose dependencies outside the
    group have finished, and that can't reuse an earlier result. `asking` (which couldn't) is always included."""
    registry = {row["id"]: row for row in load_subskill_registry()["subskills"]}
    rows = manifest.get("subskills", {})
    reuse = (manifest.get("reuse_from") or {}).get("subskills", {})
    waiting = [member for member in _SKILL_GROUPS[group] if member not in answered
               and rows.get(member, {}).get("status") in {"queued", "running"}]
    finished = lambda skill_id: rows.get(skill_id, {}).get("status") not in {"queued", "running", None}

    def unchanged(skill_id):
        row = rows.get(skill_id, {})
        entry = reuse.get(skill_id) or {}
        return bool(row.get("reused_from")) or (finished(skill_id) and row.get("input_fingerprint") in
                                                 {entry.get("input_fingerprint"), (entry.get("fallback") or {}).get("input_fingerprint")})

    def likely_reused(member, seen=()):
        # A member that will find its earlier result: one exists, and everything it depends on is unchanged
        # (finished and reused this run, or a group member that will itself be reused). A wrong guess costs a solo
        # call later, or an unused answer; never a wrong result, since reuse is still checked by fingerprint.
        if member == asking or member in seen or not reuse.get(member):
            return False
        for dep in registry[member].get("depends_on", []):
            if dep in waiting and not finished(dep):
                if not likely_reused(dep, (*seen, member)):
                    return False
            elif not unchanged(dep):
                return False
        return True

    members = [member for member in waiting if member == asking or not likely_reused(member)]
    # Dependencies outside the call must be finished, or about to be reused unchanged (the prompt then carries their
    # earlier result); drop members until that holds.
    changed = True
    while changed:
        changed = False
        for member in list(members):
            deps = registry[member].get("depends_on", [])
            if member != asking and any(dep not in members and not finished(dep) and not likely_reused(dep) for dep in deps):
                members.remove(member)
                changed = True
    return [member for member in _SKILL_GROUPS[group] if member in members]


def _ask_group(group, members, project, manifest, attempt_record):
    """One call for several sub-skills. Returns {member: {"raw", "attempt"}}, or None when the prompt is over budget."""
    from backend import skill_case_file
    paths = _project_paths(project)
    registry = {row["id"]: row for row in load_subskill_registry()["subskills"]}
    run_id = manifest.get("run_id")
    outside = [dep for member in members for dep in registry[member].get("depends_on", []) if dep not in members]
    reuse = (manifest.get("reuse_from") or {}).get("subskills", {})

    def proposal_of(dep):
        # This run's result, or (for a prerequisite about to be reused unchanged) the earlier result it will reuse.
        current = paths["root"] / "skill_workflow_runs" / str(run_id) / "proposals" / f"{dep}.json"
        if current.is_file():
            return _read(current, {})
        entry = reuse.get(dep) or {}
        return _read(paths["root"] / str(entry.get("proposal") or ""), {}) if entry.get("proposal") else {}
    dependencies = {dep: {**manifest.get("subskills", {}).get(dep, {}), "proposal": proposal_of(dep)}
                    for dep in dict.fromkeys(outside)}
    evidence, task_evidence = {}, {}
    for member in members:
        packet, _ = _shared_evidence_packet(registry[member], project)
        task_evidence[member] = packet.get("task_evidence", {})
        evidence.update({key: value for key, value in packet.items() if key not in {"task_evidence", "page_index"}})
    case, images = skill_case_file.build_for(members, project)
    if set(members) & {"surface_inventory", "surface_area", "cross_sheet_opening_match", "glazing_properties"}:
        image_rows = [{"page": page, "path": str(path)} for page, path in zip(case.get("attached_pages", []), images)]
        evidence["attached_image_coordinate_frames"] = _attached_image_coordinate_frames(image_rows, project)
    build = lambda case: _group_prompt([registry[member] for member in members], _compact_dependencies(dependencies),
                                       {**evidence, "task_evidence": task_evidence, "case_file": case})
    prompt, trimmed = build(case), []
    for part in ("readings", "page_index", "extracted"):
        if len(prompt) <= _GROUP_PROMPT_BUDGET_CHARS:
            break
        if case.get(part):
            case = {**case, part: [] if isinstance(case[part], list) else {}}
            trimmed.append(part)
            prompt = build(case)
    if len(prompt) > _GROUP_PROMPT_BUDGET_CHARS:
        return None
    record = {"prompt": prompt, "images": [{"page": page, "path": str(path)} for page, path in zip(case["attached_pages"], images)],
              "prompt_budget": {"prompt_chars": len(prompt), "budget_chars": _GROUP_PROMPT_BUDGET_CHARS, "status": "within_budget",
                                **({"case_file_trimmed": trimmed} if trimmed else {})},
              "provider": "codex_case_file_group", "group": group, "group_members": members,
              "evidence_pages": sorted(set(case["attached_pages"]) | {int(row[1:].split(" ", 1)[0]) for row in case["readings"]})}
    try:
        reply = _send_case_file_prompt(project, f"skill-group:{group}", prompt, images, record)
    finally:
        attempt_record.update({key: value for key, value in record.items() if key != "prompt"})
    results = reply.get("results") if isinstance(reply, dict) and isinstance(reply.get("results"), dict) else {}
    answered = {}
    for member in members:
        part = results.get(member)
        if isinstance(part, dict):
            # Each member keeps its own reply, so a reply that fails checking can be re-checked on its own later.
            raw_record = {**record.get("raw_record", {}), "reply_text": json.dumps(part, ensure_ascii=False),
                          "group": group, "group_members": members}
            answered[member] = {"raw": part, "attempt": {**{key: value for key, value in record.items() if key != "raw_record"},
                                                         "raw_record": raw_record}}
    return answered


def _group_prompt(subskills, dependencies, evidence):
    from ai.skill_registry import compose_group_instructions
    from ai.skill_registry import load_catalog
    parents = {skill["id"]: skill for skill in load_catalog()["skills"]}
    listing = "\n".join(
        f"- Subskill: {row['id']} — {row['task']}\n  Parent skill: {row['parent']} — {parents[row['parent']]['purpose']}\n"
        f"  Declared proposal fields: {json.dumps(row['proposal_fields'], ensure_ascii=False)}\n"
        f"  Allowed inputs: {json.dumps(row['inputs'], ensure_ascii=False)}"
        for row in subskills)
    ids = [row["id"] for row in subskills]
    stats = {}
    return (
        f"Perform these {len(subskills)} bounded Archie runtime subskills together, from the one evidence package below. "
        'Return one JSON object only; no markdown: {"results": {"<subskill id>": <answer>, ...}} with an answer for each of '
        f"{', '.join(ids)}.\n"
        "Each answer must contain status, affected_ids, observations, inferences, citations, confidence, alternatives, unresolved_fields, remediation, and proposal_fields. "
        "proposal_fields must contain every declared key of that subskill with the declared type. confidence must be a JSON number from 0.0 to 1.0, or null when unassessed. "
        "For each non-empty object in a proposal_fields list, include item-level confidence (0.0-1.0) and row-specific citations with page and excerpt; do not copy one page citation across unrelated rows. "
        "Use not_applicable only when supplied evidence establishes the domain does not apply. "
        "Do not emit a calculation result, mutate an artifact, approve a value, or promote reviewed status. "
        "Where a subskill's prerequisite is another subskill in this list, use your own answer for it.\n"
        f"{listing}\n"
        f"Validated prerequisite proposals: {_prompt_json(_compact_for_prompt(dependencies, stats))}\n"
        f"Instructions:\n{compose_group_instructions(subskills)}\n"
        f"Shared evidence package:\n{_prompt_json(_compact_for_prompt(evidence, stats))}"
    )


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


def _execute_subskill(subskill, project, dependencies, source_fp, investigation=None):
    paths = _project_paths(project)
    evidence, artifact_names = _shared_evidence_packet(subskill, project)
    if isinstance(investigation, dict):
        # The targeted question is part of the task input and therefore its cache key.
        # A targeted retry cannot accidentally reuse the untargeted baseline answer.
        evidence = {**evidence, "supervisor_investigation": deepcopy(investigation)}
    registry = load_subskill_registry()
    built_by = _CODE_BUILT_VERSIONS.get(subskill["id"]) if _case_file_route(project) else None
    input_fp = _fingerprint({"source": source_fp, "subskill": subskill, "evidence": evidence,
        "instructions": subskill_instructions_fingerprint(subskill), **({"built_by": built_by} if built_by else {}),
        # The attempt reference says where a result is stored, not what it says: leave it out, or a prerequisite that
        # is re-produced unchanged (e.g. a deterministic one) would make everything after it look changed on a retry.
        "prerequisites": {key: {"status": value.get("status", ""), "input_fingerprint": value.get("input_fingerprint", ""),
            "proposal_fingerprint": _fingerprint({k: v for k, v in (value.get("proposal") or {}).items() if k != "attempt_ref"})}
            for key, value in dependencies.items()}})
    checkpoint()
    recovery_cache = Path(project["review_dir"]) / "skill_reply_cache" / f"{input_fp}.json"
    settings_path = Path(project["review_dir"]) / "vision_extraction_settings.json"
    settings = _read(settings_path, {})
    has_evidence = bool(evidence.get("task_evidence", {}).get(next(iter(subskill["proposal_fields"]), ""), [])) or bool(evidence.get("vision_entities"))
    raw = None
    attempt_record = {"prompt": "", "images": [], "raw_record": {}, "started_at": time.time()}
    provider_kind = "existing_project_evidence"
    reused = _reused_proposal(subskill, project, input_fp)
    if reused is not None:
        return reused
    cached = _read(recovery_cache, {})
    if cached.get("input_fingerprint") == input_fp and cached.get("status") not in {"failed", "blocked"}:
        cached.setdefault("_attempt_record", {}).setdefault("raw_record", {}).update(
            recovered_reply=True, cached_reply_ref=str(recovery_cache.relative_to(Path(project["review_dir"]))))
        return cached
    if subskill["id"] == "address_confirmation" and subskill.get("_phase1_autonomous"):
        candidate = dependencies.get("site_clue_extraction", {}).get("proposal", {})
        fields = candidate.get("proposal_fields", {}) if isinstance(candidate, dict) else {}
        candidates = fields.get("site_candidates", []) if isinstance(fields, dict) else []
        unique = {}
        for row in candidates if isinstance(candidates, list) else []:
            if not isinstance(row, dict):
                continue
            address = str(row.get("address") or "").strip()
            if address:
                unique.setdefault(" ".join(address.casefold().split()), (address, row))
        citations = deepcopy(candidate.get("citations", [])) if isinstance(candidate, dict) else []
        selected = next(iter(unique.values())) if len(unique) == 1 else None
        address = selected[0] if selected else None
        citation_ids = [str(row.get("id") or row.get("citation_id")) for row in citations if isinstance(row, dict)
                        and (row.get("id") or row.get("citation_id"))]
        proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
            "status": "provisional" if address and citations else "needs_review",
            "affected_ids": candidate.get("affected_ids", []) if isinstance(candidate, dict) else [],
            "observations": deepcopy(candidate.get("observations", [])) if isinstance(candidate, dict) else [],
            "inferences": ([{"field": "confirmed_address", "value": address,
                "method": "AI selected the sole cited project-address candidate from the drawing evidence; no person confirmation is represented.",
                "citation_ids": citation_ids, "confidence": candidate.get("confidence")}]
                if address and citation_ids else []),
            "citations": citations, "confidence": candidate.get("confidence") if isinstance(candidate, dict) else None,
            "alternatives": deepcopy(candidate.get("alternatives", [])) if isinstance(candidate, dict) else [],
            "unresolved_fields": [] if address and citations else ["confirmed_address"],
            "remediation": [] if address and citations else ["No single cited project address was established; location-dependent inputs remain unresolved."],
            "input_fingerprint": input_fp,
            "proposal_fields": {"confirmed_address": address, "confirmation_actor": None,
                "confirmation_time": None, "consent_ref": None}, "artifact_names": []}
        provider_kind = "ai_inferred_pdf_site_candidate"
    elif subskill["id"] == "address_confirmation":
        candidate = dependencies.get("site_clue_extraction", {}).get("proposal", {})
        candidate_fields = candidate.get("proposal_fields", {}) if isinstance(candidate, dict) else {}
        citations = candidate.get("citations", []) if isinstance(candidate, dict) else []
        proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "needs_review",
            "affected_ids": candidate.get("affected_ids", []) if isinstance(candidate, dict) else [],
            "observations": candidate.get("observations", []) if isinstance(candidate, dict) else [], "inferences": [],
            "citations": citations, "confidence": candidate.get("confidence") if isinstance(candidate, dict) else None,
            "alternatives": candidate.get("alternatives", []) if isinstance(candidate, dict) else [],
            "unresolved_fields": ["confirmed_address", "confirmation_actor", "confirmation_time", "consent_ref"],
            "remediation": ["We need to confirm or correct the project address before any location or weather lookup."],
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
    elif _case_file_route(project) and subskill["id"] in _CODE_MAPPED_TASKS and not investigation:
        proposal = _document_map_proposal(subskill, project, input_fp)
        provider_kind = "page_reading_by_code"
    elif _case_file_route(project) and not investigation and not _worth_a_call(subskill, project):
        # No page shows anything this skill works on: not applicable, without spending an AI call.
        proposal = _not_applicable_proposal(subskill, input_fp)
        proposal["observations"] = [{"description": "No page of the drawing set shows the kinds of information this skill works on; "
                                                    "it was skipped without an AI call."}]
        provider_kind = "skipped_no_relevant_pages"
    elif _case_file_route(project):
        if subskill["id"] in _GROUP_OF and not investigation:
            raw = _group_member_reply(subskill, project, dependencies, evidence, attempt_record, source_fp)
        else:
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
    if subskill["id"] == "room_boundaries_areas":
        proposal = _augment_printed_tenant_area(subskill, project, dependencies, proposal)
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
    if proposal.get("status") not in {"failed", "blocked"}:
        _write_json(recovery_cache, json.loads(json.dumps(proposal, default=str)))
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
        checkpoint()
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
                message = "Scanned plan with no printed areas or dimensions; we need to enter the room areas."
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


def _run_checked_subskill(check, *args):
    _context.check = check
    try:
        check()
        return _execute_subskill(*args)
    finally:
        _context.check = None


def _run_worker(web, project, run_id, source_fp, catalog):
    paths = _project_paths(project)
    run_path = paths["manifest"]
    registry = load_subskill_registry()
    subskill_defs = {row["id"]: row for row in registry["subskills"]}
    selected_parents = set(catalog.get("enabled_skill_ids", catalog.get("pilot_skill_ids", [])))
    def check():
        current = _read(run_path, {})
        if current.get("run_id") != run_id or current.get("restart_requested"):
            raise JobCancelled()
    from contextlib import contextmanager
    @contextmanager
    def guard():
        with _CONTROL.mutation_lock(project):
            check()
            yield
    _context.check, _context.guard = check, guard
    cancelled = False
    try:
        with _project_lock(project["id"]):
            manifest = _read(run_path, {})
            if manifest.get("run_id") != run_id:
                return
            selected = _scoped_subskill_ids(registry, selected_parents, manifest["scope"])
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
        case_file_route = _case_file_route(project)
        while pending:
            check()
            ready = sorted(skill_id for skill_id in pending if set(subskill_defs[skill_id].get("depends_on", [])) <= completed | failures)
            if not ready:
                raise RuntimeError("subskill_graph_blocked")
            # On the case-file route a skill has the page index, the readings and a rule for missing prerequisites,
            # so a failed or blocked prerequisite no longer blocks everything after it: the dependent runs and is told.
            runnable = [skill_id for skill_id in ready if case_file_route
                        or not (set(subskill_defs[skill_id].get("depends_on", [])) & failures)]
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
            # Steps that make no AI call (built in code, or waiting on a confirmation) finish first, in their own
            # batch: an AI group can then include every member whose outside prerequisites are such steps.
            if case_file_route:
                quick = [skill_id for skill_id in runnable if skill_id in _NO_CALL_TASKS]
                if quick and len(quick) < len(runnable):
                    runnable = quick
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
                    spec = deepcopy(subskill_defs[skill_id])
                    if manifest.get("scope") == "phase1_envelope":
                        spec["_phase1_autonomous"] = True
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
            with ThreadPoolExecutor(max_workers=min(parallel_calls(), max(1, len(runnable)))) as pool:
                futures = {pool.submit(_run_checked_subskill, check, subskill_defs[skill_id], project,
                    {dep: {**manifest.get("subskills", {}).get(dep, {}), "proposal": _read(
                        paths["root"] / "skill_workflow_runs" / run_id / "proposals" / f"{dep}.json", {})}
                     for dep in subskill_defs[skill_id].get("depends_on", [])}, source_fp): skill_id for skill_id in runnable}
            for future in as_completed(futures):
                skill_id = futures[future]
                failure_phase = "proposal_generation"
                attempt_record = {"prompt": "", "images": [], "raw_record": {"provider": "unknown", "reply_text": ""}, "started_at": time.time()}
                attempt_ref = ""
                raw_result, validated = None, False
                try:
                    raw_result = future.result()
                    check()
                    attempt_record = raw_result.pop("_attempt_record", attempt_record)
                    attempt_ref, attempt_metadata = _persist_skill_attempt(
                        paths["root"], run_id, skill_id, attempt_record=attempt_record,
                        input_fingerprint=raw_result.get("input_fingerprint", source_fp))
                    attempt_metadata["model"] = attempt_record.get("raw_record", {}).get("model", attempt_record.get("model", ""))
                    failure_phase = "proposal_validation"
                    result = _validate_subskill_output(subskill_defs[skill_id], raw_result, registry,
                        allowed_pages={row.get("page") for row in _page_rows(_read(paths["coverage"], {})) if isinstance(row.get("page"), int)})
                    validated = True
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
                    reused_from = raw_result.get("reused_from", "")
                    # A reused result is copied unchanged (with its original attempt), so the skills depending on it
                    # see exactly the same prerequisite and can be reused in turn.
                    result["attempt_ref"] = raw_result.get("attempt_ref", attempt_ref) if reused_from else attempt_ref
                    proposal_path = paths["root"] / "skill_workflow_runs" / run_id / "proposals" / f"{skill_id}.json"
                    proposal_path.parent.mkdir(parents=True, exist_ok=True)
                    check()
                    _atomic_json(proposal_path, {key: value for key, value in result.items() if key not in {"provider_kind", "reused_from"}})
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
                            "evidence_reviewed": ((_read(run_path, {}).get("reuse_from") or {}).get("subskills", {}).get(skill_id, {}).get("evidence_reviewed")
                                                  if reused_from else None) or _evidence_review_record(attempt_record, attempt_ref, attempt_metadata),
                            "error_code": attempt_metadata.get("error_code", ""),
                            "prompt_budget": attempt_metadata.get("prompt_budget", {}),
                            "remediation": result.get("remediation", []),
                            "reused_from": reused_from,
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
                    # A refused reply must not become a permanent cache hit on every deliberate retry.
                    if raw_result and not validated and failure_phase == "proposal_validation":
                        reply_fp = raw_result.get("input_fingerprint", "")
                        if isinstance(reply_fp, str) and re.fullmatch(r"[0-9a-f]{64}", reply_fp):
                            (paths["root"] / "skill_reply_cache" / f"{reply_fp}.json").unlink(missing_ok=True)
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
                    if isinstance(error, SubskillValidationError):
                        # A reply the checks refused is an expected outcome (it is kept with the reason and retried),
                        # not a crash: one line, no traceback.
                        LOGGER.warning("Archie skill %s: reply refused (%s at %s: %s)", skill_id, validation.get("check"),
                                       validation.get("path"), validation.get("detail"))
                    elif code == "skill_provider_invalid_json":
                        LOGGER.warning("Archie skill %s: provider reply was not valid JSON; response archived for retry.", skill_id)
                    else:
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
            _auto_review_confident_findings(paths["root"], manifest)
            if manifest["status"] == "completed" and _has_open_findings(paths, manifest):
                manifest["status"] = "needs_review"
            if manifest["status"] in {"needs_review", "failed", "blocked"}:
                manifest["remediation"] = "Some evidence tasks need review. Independent findings are preserved; review the exceptions before relying on draft inputs."
            manifest["dependency_fingerprints"] = _derived_dependency_fingerprints(paths)
            manifest["finished_at"] = time.time()
            _write_manifest(run_path, manifest)
    except JobCancelled:
        cancelled = True
    except Exception:
        _context.check = _context.guard = None
        with _project_lock(project["id"]):
            manifest = _read(run_path, {})
            if manifest.get("run_id") == run_id:
                manifest.update({"status": "failed", "finished_at": time.time(), "error_code": "skill_workflow_failed",
                                 "remediation": "Review the workflow status and retry the failed evidence stage."})
                _write_manifest(run_path, manifest)
    finally:
        _context.check = _context.guard = None
        queued_scope = ""
        with _project_lock(project["id"]), _CONTROL.mutation_lock(project):
            manifest = _read(run_path, {})
            queued_scope = str(manifest.get("queued_automatic_scope") or "")
            restart = manifest.get("run_id") == run_id and manifest.get("restart_requested")
            if restart:
                manifest.update(status="failed", finished_at=time.time())
                manifest.pop("restart_requested", None)
                _atomic_json(run_path, manifest)
            with _LOCK:
                root_key = str(paths["root"].resolve())
                if _RUNNING_FOLDERS.get(root_key) in {None, run_id}:
                    _RUNNING.discard(project["id"])
                    _RUNNING_FOLDERS.pop(root_key, None)
            if restart:
                post(web, project, {"action": "retry", "scope": manifest.get("restart_scope") or manifest.get("scope", "phase1_envelope")})
        if restart:
            return
        if cancelled:
            return
        elif queued_scope in _WORKFLOW_SCOPES:
            try:
                post(web, project, {"action": "start", "scope": queued_scope, "automatic": True})
            except Exception:
                with _project_lock(project["id"]):
                    manifest = _read(run_path, {})
                    manifest.pop("queued_automatic_scope", None)
                    manifest.pop("queued_automatic_source_fingerprint", None)
                    _write_manifest(run_path, manifest)
                LOGGER.exception("Queued Archie skill workflow could not start", extra={"scope": queued_scope})
        else:
            start_phase1_after_review(web, project, run_id)


def take_in_downstream(project, run_id):
    """Record that a calculation has used this review's results. The calculation rewrites the domain files the
    review lists as dependencies (room uses, ceiling heights, gains...) from the review's own proposals; without this,
    calculating Phase 1 made its own review look stale at once (Butcher Buffet, 2026-10-19). A later change that
    doesn't come through a calculation of this run still makes it stale. Returns True when recorded."""
    paths = _project_paths(project)
    with _project_lock(project["id"]):
        manifest = _read(paths["manifest"], {})
        if (not run_id or manifest.get("run_id") != run_id
                or manifest.get("status") not in {"completed", "needs_review", "provisional"}):
            return False
        manifest["dependency_fingerprints"] = _derived_dependency_fingerprints(paths)
        manifest["downstream_taken_in_at"] = time.time()
        _write_manifest(paths["manifest"], manifest)
    return True


PHASE1_AFTER_REVIEW = {"completed", "needs_review", "failed", "blocked", "provisional"}


def start_phase1_after_review(web, project, run_id):
    """Phase 1 runs without a person: when the envelope review finishes (also when a step failed or is blocked; the
    calculation uses the saved evidence and lists what is missing), start the empty-room load on the server, unless a
    current Phase 1 result already exists. The calculation job joins a run already going, so this can't double up.

    Until 2026-10-18 only an open browser started it, so a review that finished with nobody watching never got its
    Phase 1 load (Butcher Buffet). Returns the started job, or None when nothing was started."""
    if web is None:
        return None
    manifest = _read(_project_paths(project)["manifest"], {})
    if (manifest.get("run_id") != run_id or manifest.get("scope") != "phase1_envelope"
            or manifest.get("status") not in PHASE1_AFTER_REVIEW):
        return None
    try:
        from backend import ai_preliminary_service, calculation_service
        current = web.project_by_id(project["id"]) if hasattr(web, "project_by_id") else project
        return calculation_service.maybe_start_phase1(web, current)
    except Exception:
        LOGGER.exception("Phase 1 could not start after the drawing review", extra={"project_id": project.get("id")})
        return None


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
    interrupted = _orphaned(manifest, project["id"], paths["root"])
    if interrupted:
        manifest.update({"status": "failed", "error_code": "skill_workflow_interrupted", "remediation": INTERRUPTED_REMEDIATION})
    safe = deepcopy(manifest)
    safe.pop("error_details", None)
    public_text = _public_text
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
                    if not _decision_is_current(decision, safe, current_fp, subskill_id):
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
                    if not _decision_is_current(decision, safe, current_fp, subskill_id):
                        decision = {}
                    cited_pages = []
                    if isinstance(value, dict):
                        cited_pages.extend(value.get(key) for key in ("page", "physical_page", "source_page")
                                           if isinstance(value.get(key), int))
                        for key in ("source_pages", "evidence_page_ids"):
                            cited_pages.extend(item for item in value.get(key, []) if isinstance(item, int)) if isinstance(value.get(key), list) else None
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
                    if not cited_pages:
                        cited_pages = sorted({row["page"] for row in citations if row.get("page") is not None})
                    row_alternatives = _row_alternatives(proposal.get("alternatives", []), value, len(proposal_rows))
                    evidence_label = _finding_evidence(value, field, cited_pages, matching_inferences, formula, calculation,
                                                       value.get("alternatives", row_alternatives) if isinstance(value, dict) else row_alternatives)
                    if any(_finding_unresolved(field, target, index, item) for item in proposal.get("unresolved_fields", [])):
                        evidence_label = "missing"
                    default_status = (value.get("status", "proposed") if subskill_id == "scanned_printed_areas" and isinstance(value, dict)
                                      else evidence_label if evidence_label == "missing" else "proposed")
                    input_applied = (decision.get("status") == "accepted" and (
                        subskill_id in {"room_identity_use", "room_boundaries_areas", "scanned_printed_areas"} or decision.get("applied") is True))
                    findings.append({"id": finding_id, "subskill_id": subskill_id, "field": field,
                        "value": decision.get("value", value), "status": decision.get("status", default_status),
                        "evidence": evidence_label, "missing": evidence_label == "missing", "target": target,
                        "units": _finding_units(field, value), "pages": cited_pages,
                        "citations": citations, "inferences": inference_text,
                        "input_applied": input_applied,
                        "formula": formula, "calculation": calculation,
                        "confidence": _finding_confidence(proposal, value, matching_inferences, len(proposal_rows)),
                        "alternatives": value.get("alternatives", row_alternatives) if isinstance(value, dict) else row_alternatives,
                        "unresolved_fields": proposal.get("unresolved_fields", []),
                        "applied_summary": decision.get("applied_summary", ""), "applied_room": decision.get("applied_room", ""),
                        "reviewer": decision.get("reviewer", ""), "reviewed_at": decision.get("updated_at", "")})
    equipment_proposal = _read(proposal_root / "equipment_evidence.json", {})
    mirrored_equipment_indices = _equipment_evidence_mirror_indices(paths["root"], equipment_proposal)
    if mirrored_equipment_indices:
        findings = [row for row in findings if not (
            row.get("subskill_id") == "equipment_evidence" and row.get("field") == "equipment"
            and row.get("id") in {f"equipment_evidence:equipment:{index}" for index in mirrored_equipment_indices})]
    case_file = _case_file_route(project)
    findings = [row for row in findings if not _hidden_finding(row, case_file)]
    from backend import finding_inputs_service
    if any(finding_inputs_service.applies(row) for row in findings):
        room_rows = finding_inputs_service.rooms(web, project)
        for row in findings:
            if finding_inputs_service.applies(row):
                # Accepting it sets an input; the room it names (in the app's rooms) is the default choice.
                mapped = finding_inputs_service.mapped_room(row.get("value"), room_rows)
                row.update({"sets_input": True, "room_label": row.get("applied_room") or (mapped or {}).get("label", "")})
    _mark_conflicting_findings(findings)
    consent = _read(paths["root"] / "vision_extraction_settings.json", {})
    visible_status = safe.get("status", "not_started")
    blocked_reason = ""
    read_only = not consent.get("owner_opt_in") and not _case_file_route(project)
    if read_only and visible_status not in {"running", "queued", "completed", "needs_review", "provisional"}:
        visible_status = "blocked"
        from backend.job_progress import stage_blocker
        blocked_reason = stage_blocker(project, "envelope") or "AI drawing review is not enabled for this job. Check project consent and the server's AI provider setup, then resume."
    issues = [{"name": row["id"], "status": row["status"], "reason": public_text(_issue_reason(row))}
              for row in subskills if row["status"] in {"failed", "blocked"}]
    failed_steps = _failed_steps(subskills)
    scan_state = safe.get("preparation", {}).get("scan_reading", {})
    if scan_state.get("status") in {"failed", "blocked"}:
        issues.append({"name": "Scanned plan area reading", "status": scan_state["status"],
                       "reason": public_text(scan_state.get("remediation", ""))})
    if safe.get("restart_requested") and not interrupted:
        visible_status = "stopping"
    stalled = bool(visible_status == "stopping" and time.time() - safe.get("restart_requested_at", time.time()) > 600)
    recovery_reason = ("The AI request exceeded its expected timeout. Restart is still pending; no replacement has started."
                       if stalled else blocked_reason or safe.get("blocked_reason", ""))
    return {
        "recovery": recovery(visible_status, public_text(recovery_reason)), "restart_stalled": stalled,
        "project_id": project["id"], "status": visible_status, "step_state": step_state(visible_status),
        "run_id": safe.get("run_id", ""), "source_fingerprint": safe.get("source_fingerprint", ""),
        "stale_reasons": [public_text(item) for item in safe.get("stale_reasons", [])], "remediation": public_text(safe.get("remediation", "")),
        "stages": stages, "findings": findings,
        "blocked_reason": public_text(blocked_reason or safe.get("blocked_reason", "")), "read_only": read_only,
        "subskills": subskills, "issues": issues, "failed_steps": failed_steps,
        "answers": _read(paths["root"] / ANSWERS_FILE, {}).get("answers", {}),
        "answer_options": _answer_options(web, project, findings),
        "preparation": {"status": safe.get("preparation", {}).get("status", "not_started"),
                        "candidate_count": safe.get("preparation", {}).get("candidate_count", 0),
                        "model_input": safe.get("preparation", {}).get("model_input", {}),
                        "scan_reading": safe.get("preparation", {}).get("scan_reading", {}),
                        "remediation": safe.get("preparation", {}).get("remediation", "")},
        "catalog": {"id": catalog["catalog_id"], "version": catalog["schema_version"]},
        "scope": safe.get("scope", ""), **({"problem": INTERRUPTED_PROBLEM} if interrupted else {}),
        "agentic": deepcopy(safe.get("agentic", {"enabled": False, "status": "not_enabled"})),
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


def _decision_is_current(decision, manifest, current_fp, subskill_id=""):
    """A decision belongs to this run's finding: made on this run, or on the earlier run whose unchanged result
    this run reused for that skill (same inputs, same proposal, so the same finding)."""
    if not isinstance(decision, dict):
        return False
    reused_from = ((manifest.get("subskills") or {}).get(subskill_id) or {}).get("reused_from") if subskill_id else None
    return bool(decision.get("run_id") in {manifest.get("run_id"), reused_from} - {None, ""}
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
    citation_rows = proposal.get("citations", []) if isinstance(proposal.get("citations"), list) else []
    citation_index = {}
    for citation in citation_rows:
        if isinstance(citation, dict):
            for key in ("id", "citation_id"):
                if citation.get(key):
                    citation_index.setdefault(str(citation[key]), []).append(citation)
    if isinstance(value, dict):
        source = value.get("citations", value.get("evidence", []))
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict):
                    refs.append(item)
                elif isinstance(item, (str, int)):
                    matches = citation_index.get(str(item), [])
                    if len(matches) == 1:
                        refs.extend(matches)
        citation_ids = value.get("citation_ids", [])
        if isinstance(citation_ids, list):
            for citation_id in citation_ids:
                matches = citation_index.get(str(citation_id), [])
                if len(matches) == 1:
                    refs.extend(matches)
        if value.get("excerpt"):
            refs.append({"page": value.get("page") or value.get("physical_page"), "excerpt": value["excerpt"]})
    # A citation on the proposal envelope can support a single result. For a list,
    # copying every envelope citation onto every row creates misleading evidence.
    if proposal_row_count <= 1 and not refs:
        refs.extend(citation_rows)
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


def _finding_confidence(proposal, value, matching_inferences, proposal_row_count):
    """Use item-level confidence; a whole-list score cannot certify every row."""
    candidates = [value.get("confidence")] if isinstance(value, dict) else []
    candidates.extend(item.get("confidence") for item in matching_inferences)
    if proposal_row_count == 1:
        candidates.append(proposal.get("confidence"))
    return next((float(candidate) for candidate in candidates
                 if isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
                 and math.isfinite(candidate) and 0 <= candidate <= 1), None)


def _equipment_evidence_mirror_indices(root, proposal):
    """Equipment names also reviewed in the dedicated equipment tab belong in that queue once."""
    if proposal.get("subskill_id") != "equipment_evidence":
        return set()
    from ai.page_extraction import normalise_name
    extraction = _read(Path(root) / "page_extraction.json", {}).get("equipment_appliances", {})
    names = {normalise_name(row.get("value", {}).get("name")) for row in extraction.get("findings", [])
             if isinstance(row, dict) and isinstance(row.get("value"), dict)}
    return {index for index, row in enumerate(proposal.get("proposal_fields", {}).get("equipment", []))
            if isinstance(row, dict) and normalise_name(row.get("name")) in names and normalise_name(row.get("name"))}


def _auto_review_confident_findings(root, manifest):
    """Persist safe evidence-gate decisions without treating them as engineering approval.

    Ordinary PDF-review findings retain the strict direct-reading rule. Phase 1
    envelope findings may also be auto-used when every numeric inference has a
    method and a valid citation, its row is unambiguous, and its confidence is
    at least 0.70. The resulting draft remains provisional and source-bound.
    """
    run_id = manifest.get("run_id", "")
    source_fp = manifest.get("source_fingerprint", "")
    proposal_root = Path(root) / "skill_workflow_runs" / str(run_id) / "proposals"
    if not run_id or not source_fp or not proposal_root.is_dir():
        return 0
    decisions_path = Path(root) / "skill_review_decisions.json"
    artifact = _read(decisions_path, {"schema_version": 1, "decisions": {}})
    decisions = artifact.setdefault("decisions", {})
    added = 0
    allowed_pages = {row.get("page") for row in _page_rows(
        _read(Path(root) / "drawing_coverage.json", {})) if isinstance(row.get("page"), int)}
    from backend import finding_inputs_service
    for proposal_path in proposal_root.glob("*.json"):
        proposal = _read(proposal_path, {})
        subskill_id = proposal.get("subskill_id") or proposal_path.stem
        phase1_scope = (manifest.get("scope") == "phase1_envelope"
                        and subskill_id in _WORKFLOW_SCOPES["phase1_envelope"])
        if proposal.get("status") in {"failed", "blocked", "not_applicable"}:
            continue
        mirror_indices = _equipment_evidence_mirror_indices(root, proposal)
        fields = proposal.get("proposal_fields", {})
        proposal_row_count = sum(len(raw) if isinstance(raw, list) else int(raw is not None)
                                 for raw in fields.values()) if isinstance(fields, dict) else 0
        for field, raw in fields.items() if isinstance(fields, dict) else ():
            rows = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
            for index, value in enumerate(rows):
                if not isinstance(value, dict) or not _finding_has_value(value):
                    continue
                if subskill_id == "equipment_evidence" and field == "equipment" and index in mirror_indices:
                    continue
                finding_id = f"{subskill_id}:{field}:{index}"
                existing = decisions.get(finding_id, {})
                if _decision_is_current(existing, manifest, source_fp, subskill_id):
                    continue
                if (finding_inputs_service.applies({"subskill_id": subskill_id, "field": field})
                        and not phase1_scope):
                    continue
                target = _finding_target(value)
                matching = _finding_inferences(proposal, field, target, proposal_row_count)
                if (matching or value.get("formula") or value.get("calculation")) and not phase1_scope:
                    continue
                if phase1_scope and matching:
                    # The output validator drops malformed numeric inferences;
                    # re-check the exact finding before recording an automatic
                    # decision so a dropped/partial inference can never qualify.
                    if any(not str(item.get("method") or "").strip()
                           or not isinstance(item.get("citation_ids"), list)
                           or not item.get("citation_ids") for item in matching):
                        continue
                unresolved = proposal.get("unresolved_fields", [])
                if any(_finding_unresolved(field, target, index, item) for item in unresolved):
                    continue
                alternatives = _row_alternatives(proposal.get("alternatives", []), value, proposal_row_count)
                if alternatives:
                    continue
                citations = _finding_citations(proposal, value, [], proposal_row_count)
                if not citations or not any(row.get("page") in allowed_pages and row.get("excerpt") for row in citations):
                    continue
                confidence = _finding_confidence(proposal, value, matching, proposal_row_count)
                minimum_confidence = 0.70 if phase1_scope and (matching or value.get("formula") or value.get("calculation")) else 0.85
                if confidence is None or confidence < minimum_confidence:
                    continue
                decisions[finding_id] = {"status": "accepted", "value": value, "reviewer": "AI evidence gate",
                    "updated_at": time.time(), "run_id": run_id, "source_fingerprint": source_fp,
                    "auto_review": "phase1_cited_method_and_consistency_gate" if phase1_scope and matching
                                   else "high_confidence_direct_citation",
                    "usage_scope": "phase1_draft_only" if phase1_scope else "general_evidence_review"}
                added += 1
    if added:
        _atomic_json(decisions_path, artifact)
    return added


def _row_alternatives(alternatives, value, row_count):
    """The proposal's alternative readings that concern this row: those naming one of its IDs, or (for a one-row
    proposal) those naming none. A disagreement about one item (e.g. the fryers' quantity) used to mark every item
    of the list as conflicting."""
    if not isinstance(alternatives, list):
        return []
    ids = {str(value.get(key)) for key in ("equipment_id", "fixture_id", "room_id", "surface_id", "opening_id", "component_id")
           if isinstance(value, dict) and value.get(key)}
    kept = []
    for item in alternatives:
        named = {str(item.get(key)) for key in ("component_id", "target", "equipment_id", "fixture_id", "room_id", "surface_id", "opening_id")
                 if isinstance(item, dict) and item.get(key)}
        if (named & ids) or (not named and row_count == 1):
            kept.append(item)
    return kept


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
        mirror_indices = _equipment_evidence_mirror_indices(paths["root"], proposal)
        for field, raw in proposal.get("proposal_fields", {}).items():
            rows = raw if isinstance(raw, list) else [raw]
            for index, value in enumerate(rows):
                if not _finding_has_value(value):
                    continue
                if subskill == "equipment_evidence" and field == "equipment" and index in mirror_indices:
                    continue
                decision = decisions.get(f"{subskill}:{field}:{index}", {})
                if not _decision_is_current(decision, manifest, manifest.get("source_fingerprint"), subskill) or decision.get("status") not in {"accepted", "rejected"}:
                    return True
        # Missing evidence is reported as a grouped gap, not a decision the
        # operator must accept or reject once per field.
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
    """Pass 1, pass 2 and drawing tasks finished: run the Phase 1 envelope skills."""
    if web is None or not _case_file_route(project) or not _project_paths(project)["ai_input"].exists():
        if web is None or not _project_paths(project)["ai_input"].exists():
            return None
    return post(web, project, {"action": "start", "scope": "phase1_envelope", "automatic": True})


def start_after_analysis(web, project):
    """Start the focused PDF review only when this project's owner opted in."""
    paths = _project_paths(project)
    settings = _read(paths["root"] / "vision_extraction_settings.json", {})
    if not settings.get("owner_opt_in") or not paths["ai_input"].exists():
        return _response(web, project)
    return post(web, project, {"action": "start", "scope": "pdf_review", "automatic": True})


def _answer_options(web, project, findings):
    if not any((row.get("subskill_id") == "information_needs" and row.get("field") == "needs") or row.get("sets_input")
               for row in findings):
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
    hours = data.get("hours") if isinstance(data.get("hours"), dict) else {}
    answer = " ".join(str(data.get("value") if data.get("value") not in (None, "") else data.get("answer") or
                          "; ".join(f"{day} {text}" for day, text in hours.items() if text)).split())[:500]
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
        "hours": data.get("hours") if isinstance(data.get("hours"), dict) else None,
        **({"surface": str(data.get("surface") or ""), "construction_id": str(data.get("construction_id") or "")}
           if data.get("kind") == "construction" else {}),
        "by": " ".join(str(data.get("reviewer") or "").split())[:80] or "Operator", "at": time.time()}
    _atomic_json(paths["root"] / ANSWERS_FILE, stored)
    response = _response(web, project)
    response["answer_options"] = need_answers_service.options(web, project)
    response["open_tab"] = result.get("open_tab", "")
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
    applied = {}
    from backend import finding_inputs_service
    if decision == "accepted" and finding_inputs_service.applies(finding):
        # Set the input it gives (people, lighting watts, an equipment item) before recording the decision, so a
        # finding that needs a room it doesn't name is refused with that reason rather than half-applied.
        lighting = [(row["value"], row.get("applied_room", "")) for row in current.get("findings", [])
                    if row.get("subskill_id") == "lighting_evidence" and row.get("status") == "accepted" and row["id"] != finding_id]
        result = finding_inputs_service.apply(web, project, finding, value, data.get("room"), reviewer, accepted_lighting=lighting)
        applied = {"applied": result["applied"], "applied_summary": result["summary"], "applied_room": result["room"]}
    artifact_path = paths["root"] / "skill_review_decisions.json"
    artifact = _read(artifact_path, {"schema_version": 1, "decisions": {}})
    artifact.setdefault("decisions", {})[finding_id] = {"status": decision, "value": value,
        "reviewer": reviewer, "updated_at": time.time(), "run_id": manifest["run_id"],
        "source_fingerprint": manifest["source_fingerprint"], **applied}
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
    # Serialize run creation/restart across local server processes as well as threads.
    with _CONTROL.mutation_lock(project):
        return _post(web, project, data)


def _post(web, project, data):
    action = str((data or {}).get("action", "start"))
    if action == "review_finding":
        return _review_finding(web, project, data)
    if action == "answer_need":
        return _answer_need(web, project, data)
    if action not in {"start", "retry", "resume", "restart"}:
        raise ValueError("Skill workflow action must be start, retry, resume, restart, review_finding or answer_need.")
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
        if action == "restart" and data.get("expected_run_id") and existing.get("run_id") != data["expected_run_id"]:
            return {**_response(web, project), "deduplicated": True}
        if action in {"retry", "resume", "restart"} and not explicit_scope and existing.get("scope") in _WORKFLOW_SCOPES:
            # A retry repeats the interrupted or failed run's own scope.
            scope = existing["scope"]
        if action == "restart" and (is_running(project) or
                (existing.get("status") in {"queued", "running"} and not _orphaned(existing, project["id"], paths["root"]))):
            existing.update(restart_requested=True, restart_requested_at=existing.get("restart_requested_at") or time.time(), restart_scope=scope)
            _write_manifest(paths["manifest"], existing)
            return _response(web, project)
        if action in {"resume", "restart"} and not is_running(project):
            from backend.job_progress import stage_blocker
            reason = stage_blocker(project, "envelope")
            if reason:
                response = _response(web, project)
                response["recovery"]["reason"] = reason
                return response
        if action in {"resume", "restart"}:
            action = "retry" if existing.get("status") in {"failed", "blocked", "stale", "needs_review", "queued", "running"} else "start"
        if is_running(project):
            running_manifest = _read(paths["manifest"], {})
            if (data.get("automatic") is True and scope == "phase1_envelope"
                    and (running_manifest.get("scope") != scope
                         or running_manifest.get("source_fingerprint") != source_fp)):
                running_manifest["queued_automatic_scope"] = scope
                running_manifest["queued_automatic_source_fingerprint"] = source_fp
                _write_manifest(paths["manifest"], running_manifest)
            response = _response(web, project)
            response["deduplicated"] = True
            if running_manifest.get("queued_automatic_scope"):
                response["queued_scope"] = running_manifest["queued_automatic_scope"]
            return response
        # A persisted `running` manifest without a live in-process worker is
        # an interrupted run (for example, after a local server restart). Do
        # not deduplicate forever against that orphaned state; make the
        # interruption visible and require the deliberate retry action.
        if existing.get("status") in {"queued", "running"} and not _orphaned(existing, project["id"], paths["root"]):
            response = _response(web, project)       # another live server process is running it
            response["deduplicated"] = True
            return response
        if existing.get("status") in {"queued", "running"}:
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
                and existing.get("scope") == scope and existing.get("status") in {"completed", "needs_review"}):
            return _response(web, project)
        if action == "retry" and existing.get("status") not in {"failed", "blocked", "stale", "needs_review"}:
            if existing:
                raise ValueError("Retry is available only after a failed or stale skill workflow.")
        manifest = _new_manifest(catalog, source_fp, scope)
        manifest["reuse_from"] = _reuse_table(paths, existing)
        _RUNNING_FOLDERS[str(paths["root"].resolve())] = manifest["run_id"]
        _RUNNING.add(project["id"])       # before the manifest says queued, so a status read never sees it orphaned
        try:
            _write_manifest(paths["manifest"], manifest)
        except Exception:
            _RUNNING.discard(project["id"])
            _RUNNING_FOLDERS.pop(str(paths["root"].resolve()), None)
            raise
        _USAGE_STOPPED.pop(str(paths["root"].resolve()), None)  # a new run tries again
    thread = threading.Thread(target=_run_worker, args=(web, deepcopy(project), manifest["run_id"], source_fp, catalog), daemon=True)
    thread.start()
    response = _response(web, project)
    response["deduplicated"] = False
    return response
