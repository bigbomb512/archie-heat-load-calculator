"""Calculate a job on the server, as one background job (the workspace's Calculate button).

Steps: apply the Project-tab answers, rebuild the draft model (so typed areas, measured
rooms and heights are in it), confirm the cooled rooms, calculate. When the model inputs
are missing or out of date (the first Calculate on a job), the evidence workflow and the
model-input resolver run first, then the steps are retried once. Progress is recorded in
calculation_job.json, which the workspace polls, so closing the page doesn't stop it.
"""

import re
import math
import logging
import time
from pathlib import Path

from backend.job_runner import BackgroundJob

JOB_FILE = "calculation_job.json"
STEPS = [("answers", "Applying available project information"),
         ("evidence", "Resolving Phase 1 drawing evidence"),
         ("model", "Building the model from your rooms"),
         ("rooms", "Resolving room scope from drawing evidence"),
         ("inputs", "Resolving calculation inputs and assumptions"),
         ("calculate", "Calculating the room load")]
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-calculation",
                     "The calculation stopped before it finished (the server was restarted). Press Calculate again.")
_RUNNING = _JOB.running
NEEDS_INPUTS = re.compile(r"missing required|out of date|resolve model inputs|stale", re.IGNORECASE)
WORKFLOW_TIMEOUT_S = 600


def _phase1_drawing_problems(project):
    """Prerequisites checked before both assembly and calculation."""
    from backend import ai_preliminary_service
    from backend.workflow_recovery import JOBS, ACTIVE, TERMINAL
    if not project.get("review_dir"):
        return ["Prepare the drawing set first."]
    root = Path(project["review_dir"])
    reasons = []
    if not project.get("reasoning_packet"):
        reasons.append("Prepare the drawing set first.")
    for _kind, filename, label in JOBS:
        job = ai_preliminary_service._read(root / filename, {})
        if job.get("status") in ACTIVE or job.get("restart_requested"):
            reasons.append(f"Waiting for {label} to finish.")
    manifest = ai_preliminary_service._read(root / "skill_workflow_run.json", {})
    if (not manifest.get("run_id") or manifest.get("scope") != "phase1_envelope"
            or manifest.get("status") not in TERMINAL):
        reasons.append("The empty-room drawing review has not finished.")
    if manifest.get("source_fingerprint"):
        from backend import skill_workflow_service
        paths = skill_workflow_service._project_paths(project)
        current = skill_workflow_service._source_fingerprint(paths, skill_workflow_service.load_catalog())
        if current != manifest["source_fingerprint"]:
            reasons.append("The drawings changed after the review. Resume the envelope review first.")
    return reasons


def phase1_readiness(web, project):
    """No elapsed-time fallback: settled drawing evidence and a usable room are required."""
    from backend import ai_preliminary_service
    reasons = _phase1_drawing_problems(project)
    if reasons:
        return {"ready": False, "reasons": reasons, "usable_rooms": [], "exceptions": []}
    root = Path(project["review_dir"])
    model = ai_preliminary_service.get(web, project, "workspace", check_freshness=True)
    if model.get("status") == "stale":
        return {"ready": False, "reasons": ["Rebuilding calculation inputs from the finished drawing review is required."], "usable_rooms": [], "exceptions": []}
    candidates = (model.get("room_scope") or {}).get("candidates", [])
    inputs = ai_preliminary_service._read(root / "ai_preliminary_input_set.json", {})
    room_inputs = ((inputs.get("material") or {}).get("hourly_load_model") or {}).get("rooms", [])
    by_id = {row.get("room_id"): row for row in room_inputs}
    usable, exceptions = [], []
    for row in candidates:
        area = row.get("area_m2")
        room = by_id.get(row.get("model_room_id") or row.get("key"), {})
        envelope = room.get("cooling_load") or {}
        envelope_ready = bool(envelope.get("envelope_surfaces") or envelope.get("glazing_surfaces")
                              or envelope.get("envelope_not_applicable") is True)
        valid = (envelope_ready and isinstance(area, (int, float)) and not isinstance(area, bool) and math.isfinite(area) and area > 0
                 and row.get("status") == "calculated" and row.get("include", True))
        (usable if valid else exceptions).append(row.get("key") or row.get("name") or "Unnamed room")
    if not usable:
        reasons.append("No room has supported area, room-use and envelope inputs yet. Resume the drawing review to resolve them.")
    return {"ready": bool(usable), "reasons": reasons, "usable_rooms": usable, "exceptions": exceptions}


def status(web, project):
    job = _JOB.status(project)
    readiness = phase1_readiness(web, project)
    from backend.job_runner import read_json
    auto = read_json(Path(project["review_dir"]) / "phase1_automatic_start.json")
    manifest = read_json(Path(project["review_dir"]) / "skill_workflow_run.json")
    offer = bool(readiness["ready"] and auto.get("error") and auto.get("run_id") == manifest.get("run_id")
                 and job.get("status") not in {"running", "queued", "stopping", "failed", "interrupted"})
    # A current report is never replaced by a recovery button.
    if offer:
        from backend import ai_preliminary_service
        model = ai_preliminary_service.get(web, project, "workspace", check_freshness=True)
        report = model.get("phase1_base_room_load_report") or {}
        offer = not report or report.get("status") in {"not_calculated", "blocked"} or model.get("phase1_base_room_load_report_stale", False)
    return {**job, "readiness": readiness, "offer_start": bool(offer), "automatic_start_problem": auto.get("error", "") if offer else ""}


def maybe_start_phase1(web, project):
    from backend import ai_preliminary_service
    from backend.job_runner import read_json, write_json
    if web is None:
        return None
    current = web.project_by_id(project["id"]) if hasattr(web, "project_by_id") else project
    if _phase1_drawing_problems(current):
        return None
    # Serialize automatic handoffs from different finishing jobs and browser recovery requests.
    with _JOB.mutation_lock(current):
        model = ai_preliminary_service.get(web, current, "workspace", check_freshness=True)
        if model.get("status") in {"stale", "not_calculated"}:
            ai_preliminary_service._assemble(web, current, source="automatic_phase1_handoff")
            from backend import skill_workflow_service
            manifest = read_json(Path(current["review_dir"]) / "skill_workflow_run.json")
            skill_workflow_service.take_in_downstream(current, manifest.get("run_id"))
            model = ai_preliminary_service.get(web, current, "workspace", check_freshness=True)
        ready = phase1_readiness(web, current)
        if not ready["ready"]:
            return None
    base = model.get("phase1_base_room_load_report") or {}
    if base and base.get("status") not in {"not_calculated", "blocked"} and not model.get("phase1_base_room_load_report_stale"):
        return None
    root = Path(current["review_dir"])
    run_id = read_json(root / "skill_workflow_run.json").get("run_id")
    marker = root / "phase1_automatic_start.json"
    # Failed automatic startup stays actionable until the operator retries it or a new review finishes.
    previous = read_json(marker)
    if previous.get("run_id") == run_id and (previous.get("error") or _JOB.status(current).get("status") in {"failed", "interrupted"}):
        return None
    try:
        result = start(web, current, {"phase": "phase1_base_room", "reviewer": "Automatic, after the drawing review"})
        write_json(marker, {"run_id": run_id, "status": "started"})
        return result
    except Exception as error:
        write_json(marker, {"run_id": run_id, "error": str(error) or type(error).__name__})
        return None


def reconcile_finished_reviews(web):
    """Recover a missed handoff after server restart, without restarting stopped AI jobs."""
    from backend.job_runner import read_json
    seen = set()
    for project in web.load_projects().values():
        if not isinstance(project, dict) or not project.get("id") or not project.get("reasoning_packet"):
            continue
        root = project.get("review_dir")
        if not root or str(Path(root).resolve()) in seen:
            continue
        seen.add(str(Path(root).resolve()))
        # Interrupted/failed calculation jobs require deliberate operator recovery.
        if _JOB.status(project).get("status") in {"running", "queued", "stopping", "failed", "interrupted"}:
            continue
        manifest = read_json(Path(root) / "skill_workflow_run.json")
        if manifest.get("scope") != "phase1_envelope" or manifest.get("status") not in {"completed", "needs_review", "provisional", "failed", "blocked"}:
            continue
        try:
            maybe_start_phase1(web, project)
        except Exception:
            logging.getLogger(__name__).exception("Could not recover the completed drawing review handoff", extra={"project_id": project.get("id")})


def _include_map(data):
    include = data.get("include") or {}
    if not isinstance(include, dict) or any(not isinstance(key, str) or type(value) is not bool for key, value in include.items()):
        raise ValueError("Room choices must map room keys to true or false.")
    return include


def _rows(scope, include):
    """Include AI-resolved comfort rooms with area; unresolved use/area stays an exception."""
    rows = []
    for row in scope.get("candidates") or []:
        calculable = row.get("status") == "calculated" and row.get("area_m2") is not None
        chosen = include.get(row.get("key"))
        cooled = calculable and (chosen if chosen is not None else bool(row.get("include", True)))
        reason = "" if cooled else ("AI could not resolve a supported room use or area." if not calculable else "Excluded by room selection.")
        rows.append({"key": row.get("key"), "include": cooled, "reason": reason})
    return rows


def _resolve_inputs(web, project_id):
    """Wait for an active PDF review, then resolve using its accepted findings."""
    from backend import ai_preliminary_service, model_input_resolution_service, skill_workflow_service
    project = web.project_by_id(project_id)
    manifest = ai_preliminary_service._read(Path(project["review_dir"]) / "skill_workflow_run.json", {})
    reviewing = (manifest.get("scope") in {"pdf_review", "phase1_envelope"} and manifest.get("status") in {"queued", "running"}
                 and skill_workflow_service.is_running(project))
    if reviewing:
        deadline = time.monotonic() + WORKFLOW_TIMEOUT_S
        while skill_workflow_service.is_running(project):
            if time.monotonic() > deadline:
                raise RuntimeError("PDF review took longer than 10 minutes. Retry the review, then calculate again.")
            time.sleep(1)
    # Evidence exceptions are listed by the resolver itself; they don't stop it (as in the engineer screen).
    model_input_resolution_service.post(web, web.project_by_id(project_id), {"action": "resolve"})


def start(web, project, data):
    include = _include_map(data)
    reviewer = " ".join(str(data.get("reviewer") or "").split())[:80] or "Operator"
    phase = data.get("phase", "full")
    if phase not in {"phase1_base_room", "full"}:
        raise ValueError("Calculation phase must be phase1_base_room or full.")

    def work(web, project_id, step):
        from backend import ai_preliminary_service, job_service, skill_workflow_service
        project = web.project_by_id(project_id)
        paths = ai_preliminary_service._paths(project)
        step("answers")
        if job_service.job_setup(project).get("above"):
            job_service.apply_roof_answer(web, project)

        phase1_note = ""
        if phase == "phase1_base_room":
            step("evidence")
            readiness = phase1_readiness(web, project)
            if not readiness["ready"]:
                raise ValueError(" ".join(readiness["reasons"]))
            if readiness["exceptions"]:
                phase1_note = "Unresolved or excluded rooms: " + ", ".join(readiness["exceptions"]) + "."

        def model_and_calculate():
            step("model")
            ai_preliminary_service._assemble(web, web.project_by_id(project_id))
            step("rooms")
            input_set = ai_preliminary_service._read(paths["current_set"], {})
            scope = ai_preliminary_service._room_scope_state(paths, input_set)
            if phase == "full":
                rows = _rows(scope, include)
                if not any(row["include"] for row in rows):
                    raise ValueError("No room has an area yet. The AI could not resolve a room area from the drawings.")
                ai_preliminary_service._confirm_room_scope(paths, {"reviewer": reviewer, "rows": rows,
                                                                    "candidate_fingerprint": scope.get("candidate_fingerprint", "")})
            step("calculate")
            ai_preliminary_service._calculate(web, web.project_by_id(project_id), scope=phase)

        # The review whose results this calculation uses (the evidence step above may have run a new one).
        review_run = ai_preliminary_service._read(paths["root"] / "skill_workflow_run.json", {}).get("run_id")
        resolved = False
        try:
            model_and_calculate()
        except ValueError as error:
            if not NEEDS_INPUTS.search(str(error)):
                raise
            step("inputs")
            _resolve_inputs(web, project_id)
            resolved = True
            model_and_calculate()
        project = web.project_by_id(project_id)
        project["updated_at"] = __import__("ai.ai_preliminary", fromlist=["now"]).now()
        web.update_project(project)
        # The files this calculation just rewrote come from the review's own results: the review is still current
        # (only for the review run that was current when the calculation started).
        skill_workflow_service.take_in_downstream(project, review_run)
        report = ai_preliminary_service._read(paths["base_report"] if phase == "phase1_base_room" else paths["report"], {})
        if phase1_note and phase == "phase1_base_room":
            # Added to Phase 1's own note (e.g. why a fully internal room is 0 kW), not written over it.
            report["phase1_automation_note"] = " ".join(filter(None, [phase1_note, str(report.get("phase1_automation_note") or "")]))
            ai_preliminary_service._write(paths["base_report"], report)
        peak = report.get("included_scope_peak") or {}
        return {"phase": phase, "total_kw": peak.get("final_design_total_kw", peak.get("design_total_kw")),
                "inputs_resolved": resolved, "excluded_rooms": report.get("excluded_spaces", [])}

    with _JOB.mutation_lock(project):
        if phase == "phase1_base_room" and _JOB.status(project).get("status") not in {"running", "queued", "stopping"}:
            readiness = phase1_readiness(web, project)
            if not readiness["ready"]:
                raise ValueError(" ".join(readiness["reasons"]))
            from backend import ai_preliminary_service
            model = ai_preliminary_service.get(web, project, "workspace", check_freshness=True)
            base = model.get("phase1_base_room_load_report") or {}
            if base.get("status") not in {None, "", "not_calculated", "blocked"} and not model.get("phase1_base_room_load_report_stale"):
                from backend.job_runner import recovery
                return {**_JOB.status(project), "status": "done", "phase": phase, "deduplicated": True, "recovery": recovery("done")}
        return _JOB.start(web, project, {"requested_by": reviewer, "phase": phase}, work,
                          action=data.get("action", "start"), expected_run_id=data.get("expected_run_id"))
