"""Persisted local room-inference job and bridge to existing resolvers."""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path

from ai import ai_preliminary, room_inference
from ai.geometry_resolution import build_geometry_resolution
from backend.vision_extraction_service import _atomic_json


_LOCK = threading.Lock()
_RUNNING = set()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root, "job": root / "room_inference_job.json", "ai_input": root / "ai_input.json",
        "coverage": root / "drawing_coverage.json", "spatial": root / "spatial_ocr.json",
        "vector": root / "vector_geometry.json",
        "building": root / "building_evidence.json", "vision": root / "vision_response.json",
        "run": root / "ai_preliminary_run.json", "room_use": root / "room_use_resolution.json",
        "geometry": root / "geometry_resolution.json",
    }


def _inputs(paths):
    return {
        "ai_input": _read(paths["ai_input"], {}),
        "coverage": _read(paths["coverage"], {}),
        "spatial": _read(paths["spatial"], {}),
        "vector": _read(paths.get("vector"), {}) if paths.get("vector") else {},
        "vision": _read(paths.get("vision"), {}) if paths.get("vision") else {},
    }


def _source_fingerprint(paths):
    values = _inputs(paths)
    return room_inference.fingerprint({"inputs": values, "version": room_inference.VERSION})


def _response(web, project):
    paths = _paths(project)
    job = _read(paths["job"], {"status": "not_started", "candidate_count": 0, "selected_pages": []})
    current = _source_fingerprint(paths) if paths["ai_input"].exists() else ""
    stale = bool(job.get("source_fingerprint") and current and job.get("source_fingerprint") != current)
    result = deepcopy(job)
    if result.get("status") == "completed" and not _completed_result_usable(paths):
        stale = True
        result["status"] = "stale"
        result["stale_reasons"] = ["The previous completed job did not persist source-linked room candidates. Retry room detection."]
    elif stale and result.get("status") == "completed":
        result["status"] = "stale"
        result["stale_reasons"] = ["PDF analysis, selected pages, or the local room-inference version changed. Retry room detection."]
    result.update({"id": project["id"], "source_fingerprint": job.get("source_fingerprint", ""), "current": not stale})
    result.pop("error_details", None)  # browser receives safe remediation only
    return {"id": project["id"], "room_inference": result, "status": result.get("status", "not_started"),
            "candidate_count": int(result.get("candidate_count", 0) or 0),
            "job_id": result.get("job_id", ""),
            "selected_pages": result.get("selected_pages", []), "affected_room_ids": result.get("affected_room_ids", []),
            "stale_reasons": result.get("stale_reasons", []), "remediation": result.get("remediation", []),
            "artifact_url": web.safe_link(paths["job"]) if paths["job"].exists() else ""}


def get(web, project):
    return _response(web, project)


def _write_run(paths, proposal):
    run = _read(paths["run"], {})
    run["local_room_inference_proposal"] = proposal
    run["local_room_inference_version"] = room_inference.VERSION
    run["updated_at"] = ai_preliminary.now()
    _atomic_json(paths["run"], run)


def _current_job(paths, job_id):
    job = _read(paths["job"], {})
    return job if job.get("job_id") == job_id else None


def _completed_result_usable(paths):
    """Reject legacy completed jobs that never persisted source-linked rooms."""
    run = _read(paths["run"], {})
    proposal = run.get("local_room_inference_proposal") or run.get("manual_placeholder_proposal", {})
    rooms = proposal.get("rooms", []) if isinstance(proposal, dict) else []
    if not isinstance(rooms, list):
        return False
    def has_source_page(row):
        if not isinstance(row, dict):
            return False
        if isinstance(row.get("page"), int) and row.get("page") > 0:
            return True
        source_pages = row.get("source_pages")
        if isinstance(source_pages, list) and any(isinstance(page, int) and page > 0 for page in source_pages):
            return True
        evidence = row.get("evidence")
        return isinstance(evidence, list) and any(
            isinstance(item, dict) and isinstance(item.get("page"), int) and item.get("page") > 0
            for item in evidence
        )

    return all(has_source_page(row) for row in rooms)


def _run_job(web, project, job_id, source_fp):
    paths = _paths(project)
    job = {"job_id": job_id, "source_fingerprint": source_fp, "status": "queued"}
    try:
        with _LOCK:
            current = _current_job(paths, job_id)
            if current is None:
                return
            current.update({"status": "running", "started_at": current.get("started_at", ai_preliminary.now())})
            _atomic_json(paths["job"], current)
        job = _read(paths["job"], {})
        inputs = _inputs(paths)
        proposal = room_inference.infer(
            inputs["ai_input"], inputs["coverage"], inputs["spatial"], inputs["vector"], inputs["vision"]
        )
        if _current_job(paths, job_id) is None:
            return
        _write_run(paths, proposal)
        # The established room-use service consumes the normalized local
        # proposal, so no parallel room model is introduced.
        from backend import room_use_resolution_service, model_input_resolution_service, reviewer_room_geometry_service
        room_use_resolution_service.post(web, project, {"action": "resolve"})
        # Add source-linked room entities to the existing geometry artifact.
        geometry = build_geometry_resolution(
            inputs["ai_input"], inputs["coverage"], _read(paths["building"], {}), inputs["spatial"],
            vision_response=_read(paths["vision"], {}), resolution_mode="preliminary_ai_estimate",
            room_proposals=proposal.get("rooms", []),
            reviewer_room_geometry=reviewer_room_geometry_service.current_artifact_input(paths["root"]),
        )
        _atomic_json(paths["geometry"], geometry)
        project["room_inference_job"] = str(paths["job"])
        project["geometry_resolution"] = str(paths["geometry"])
        job.update({"status": "completed", "completed_at": ai_preliminary.now(), "selected_pages": proposal.get("selected_pages", []),
                    "candidate_count": len(proposal.get("rooms", [])), "affected_room_ids": [row.get("room_id") for row in proposal.get("rooms", [])],
                    "remediation": ["Review only low-confidence or conflicting rooms; geometry remains provisional until a closed boundary is proven."] if proposal.get("rooms") else ["Review selected pages or retry room detection; no room-use clues were found."]})
        if _current_job(paths, job_id) is not None:
            _atomic_json(paths["job"], job)
            project["updated_at"] = ai_preliminary.now()
            try:
                web.update_project(project)
            except Exception:
                pass
        # When the skill workflow is active, it still needs to propose and
        # validate room boundaries. Defer shared model resolution until that
        # handoff so we do not race a label-only model build against geometry.
        skill_run = _read(paths["root"] / "skill_workflow_run.json", {})
        if skill_run.get("status") not in {"queued", "running"}:
            try:
                model_input_resolution_service.post(web, project, {"action": "resolve"})
            except Exception:
                # Resolver failures remain visible through its structured endpoint;
                # they must not turn a successful room-inference job into a false
                # room-detection failure.
                pass
        if (paths["root"] / "calculator_draft.json").exists():
            try:
                from backend import draft_service
                draft_service.post(web, project, {"action": "build"})
                job["calculator_draft_refresh"] = "current"
            except Exception:
                # Keep inference success distinct from an optional draft refresh;
                # the draft endpoint will expose its stale state and remediation.
                job["calculator_draft_refresh"] = "failed"
                job["calculator_draft_refresh_remediation"] = "Rebuild the calculator draft from current evidence."
            if _current_job(paths, job_id) is not None:
                _atomic_json(paths["job"], job)
    except Exception as error:
        job.update({"status": "failed", "failed_at": ai_preliminary.now(), "error_code": "room_inference_failed",
                    "remediation": ["Retry room detection", "Review selected pages", "Open geometry editor"]})
        try:
            from backend import model_input_resolution_service
            model_input_resolution_service.post(web, project, {"action": "resolve"})
        except Exception:
            pass
        if _current_job(paths, job_id) is not None:
            _atomic_json(paths["job"], job)
            project["updated_at"] = ai_preliminary.now()
            try:
                web.update_project(project)
            except Exception:
                pass
    finally:
        with _LOCK:
            _RUNNING.discard(project["id"])


def _start(web, project, retry=False):
    paths = _paths(project)
    if not paths["ai_input"].exists():
        raise ValueError("Analyse the PDF before starting room detection.")
    existing = _read(paths["job"], {})
    current = _source_fingerprint(paths)
    if existing.get("status") == "completed" and existing.get("source_fingerprint") and existing.get("source_fingerprint") != current:
        existing["status"] = "stale"
        existing["stale_reasons"] = ["PDF analysis, selected pages, or the local room-inference version changed. Retry room detection."]
        _atomic_json(paths["job"], existing)
    if existing.get("status") in {"queued", "running"} and project["id"] in _RUNNING:
        response = _response(web, project)
        response["deduplicated"] = True
        response["retry_after_ms"] = 900
        return response
    if not retry and existing.get("status") == "completed" and existing.get("source_fingerprint") == current:
        if _completed_result_usable(paths):
            return _response(web, project)
        existing["status"] = "stale"
        existing["stale_reasons"] = ["The previous completed job did not persist source-linked room candidates. Room detection will be rebuilt."]
        _atomic_json(paths["job"], existing)
    if retry and existing.get("status") not in {"failed", "stale"}:
        if existing.get("status") == "completed" and existing.get("source_fingerprint") == current:
            # A legacy completed job can have the current fingerprint while
            # still lacking the source-linked room proposal required by the
            # consolidated resolver.  Only reuse a completed result when it
            # passes the same structural usability check as a normal start;
            # otherwise convert it to stale and allow this explicit retry to
            # rebuild it.
            if _completed_result_usable(paths):
                return _response(web, project)
            existing["status"] = "stale"
            existing["stale_reasons"] = [
                "The previous completed job did not persist source-linked room candidates. Retry room detection."
            ]
            _atomic_json(paths["job"], existing)
        if existing.get("status") == "completed":
            raise ValueError("Room detection is not stale. Start a new analysis before retrying.")
    with _LOCK:
        # Re-check after acquiring the lock so two HTTP requests arriving at
        # the same time cannot both enqueue a worker.
        existing = _read(paths["job"], {})
        if existing.get("status") in {"queued", "running"} and project["id"] in _RUNNING:
            response = _response(web, project)
            response["deduplicated"] = True
            response["retry_after_ms"] = 900
            return response
        job_id = uuid.uuid4().hex
        job = {"schema_version": 1, "job_id": job_id, "status": "queued", "queued_at": ai_preliminary.now(), "source_fingerprint": current,
               "selected_pages": [], "candidate_count": 0, "retry_count": int(existing.get("retry_count", 0) or 0) + (1 if retry else 0),
               "attempt": int(existing.get("attempt", 0) or 0) + 1}
        _atomic_json(paths["job"], job)
        _RUNNING.add(project["id"])
        threading.Thread(target=_run_job, args=(web, project, job_id, current), daemon=True, name="archie-room-inference").start()
    response = _response(web, project)
    response["job_id"] = job_id
    return response


def post(web, project, data):
    action = str((data or {}).get("action", "start"))
    if action == "start":
        return _start(web, project)
    if action == "retry":
        return _start(web, project, retry=True)
    if action == "run_now":
        started = _start(web, project, retry=True)
        return started
    raise ValueError("Room-inference action must be start or retry.")
