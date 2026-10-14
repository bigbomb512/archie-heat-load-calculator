"""Calculate a job on the server, as one background job (the workspace's Calculate button).

Steps: apply the Project-tab answers, rebuild the draft model (so typed areas, measured
rooms and heights are in it), confirm the cooled rooms, calculate. When the model inputs
are missing or out of date (the first Calculate on a job), the evidence workflow and the
model-input resolver run first, then the steps are retried once. Progress is recorded in
calculation_job.json, which the workspace polls, so closing the page doesn't stop it.
"""

import re
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


def status(web, project):
    return _JOB.status(project)


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
                 and project_id in skill_workflow_service._RUNNING)
    if reviewing:
        deadline = time.monotonic() + WORKFLOW_TIMEOUT_S
        while project_id in skill_workflow_service._RUNNING:
            if time.monotonic() > deadline:
                raise RuntimeError("PDF review took longer than 10 minutes. Retry the review, then calculate again.")
            time.sleep(1)
    # Evidence exceptions are listed by the resolver itself; they don't stop it (as in the engineer screen).
    model_input_resolution_service.post(web, web.project_by_id(project_id), {"action": "resolve"})


def _phase1_evidence_state(web, project):
    """Run or join the scoped envelope review when the project has consented to AI use."""
    from backend import ai_preliminary_service, skill_workflow_service

    paths = skill_workflow_service._project_paths(project)
    manifest = ai_preliminary_service._read(paths["manifest"], {})
    settings = ai_preliminary_service._read(paths["root"] / "vision_extraction_settings.json", {})
    if not (settings.get("owner_opt_in") or skill_workflow_service._case_file_route(project)):
        return "AI drawing review was not started because project consent or an enabled case-file route is unavailable."

    current_fingerprint = skill_workflow_service._source_fingerprint(paths, skill_workflow_service.load_catalog())
    terminal = {"completed", "needs_review", "failed", "blocked", "stale", "provisional"}
    if (manifest.get("scope") == "phase1_envelope"
            and manifest.get("source_fingerprint") == current_fingerprint
            and manifest.get("status") in terminal):
        return ""

    started = skill_workflow_service.post(web, project, {
        "action": "start", "scope": "phase1_envelope", "automatic": True,
    })
    if isinstance(started, dict):
        started_manifest = started.get("manifest", {}) if isinstance(started.get("manifest"), dict) else {}
        started_scope = started_manifest.get("scope") or started.get("scope")
        started_status = started_manifest.get("status") or started.get("status")
        if started_scope == "phase1_envelope" and started_status in {"blocked", "failed"}:
            return "The Phase 1 drawing review could not run; the calculation used saved evidence and lists its exceptions."
    deadline = time.monotonic() + WORKFLOW_TIMEOUT_S
    while True:
        manifest = ai_preliminary_service._read(paths["manifest"], {})
        current_scope = manifest.get("scope")
        queued_phase1 = manifest.get("queued_automatic_scope") == "phase1_envelope"
        active = (project["id"] in skill_workflow_service._RUNNING
                  or queued_phase1
                  or (current_scope == "phase1_envelope" and manifest.get("status") in {"queued", "running"}))
        if not active:
            if current_scope == "phase1_envelope" and manifest.get("status") in terminal:
                if manifest.get("status") in {"failed", "blocked", "stale"}:
                    return "The Phase 1 drawing review had unresolved tasks; the calculation used saved evidence and lists its exceptions."
                return ""
            return "The Phase 1 drawing review did not start; the calculation used saved evidence and lists its exceptions."
        if time.monotonic() >= deadline:
            return "The Phase 1 drawing review exceeded its wait limit; the calculation used saved evidence and lists its exceptions."
        time.sleep(1)


def start(web, project, data):
    include = _include_map(data)
    reviewer = " ".join(str(data.get("reviewer") or "").split())[:80] or "Operator"
    phase = data.get("phase", "full")
    if phase not in {"phase1_base_room", "full"}:
        raise ValueError("Calculation phase must be phase1_base_room or full.")

    def work(web, project_id, step):
        from backend import ai_preliminary_service, job_service
        project = web.project_by_id(project_id)
        paths = ai_preliminary_service._paths(project)
        step("answers")
        if job_service.job_setup(project).get("above"):
            job_service.apply_roof_answer(web, project)

        phase1_note = ""
        if phase == "phase1_base_room":
            step("evidence")
            phase1_note = _phase1_evidence_state(web, project)

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
        report = ai_preliminary_service._read(paths["base_report"] if phase == "phase1_base_room" else paths["report"], {})
        if phase1_note and phase == "phase1_base_room":
            report["phase1_automation_note"] = phase1_note
            ai_preliminary_service._write(paths["base_report"], report)
        peak = report.get("included_scope_peak") or {}
        return {"phase": phase, "total_kw": peak.get("final_design_total_kw", peak.get("design_total_kw")),
                "inputs_resolved": resolved, "excluded_rooms": report.get("excluded_spaces", [])}

    return _JOB.start(web, project, {"requested_by": reviewer, "phase": phase}, work)
