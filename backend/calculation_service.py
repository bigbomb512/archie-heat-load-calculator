"""Calculate a job on the server, as one background job (the workspace's Calculate button).

Steps: apply the Project-tab answers, rebuild the draft model (so typed areas, measured
rooms and heights are in it), confirm the cooled rooms, calculate. When the model inputs
are missing or out of date (the first Calculate on a job), the evidence workflow and the
model-input resolver run first, then the steps are retried once. Progress is recorded in
calculation_job.json, which the workspace polls, so closing the page doesn't stop it.
"""

import re
import time

from backend.job_runner import BackgroundJob

JOB_FILE = "calculation_job.json"
STEPS = [("answers", "Applying your answers"),
         ("model", "Building the model from your rooms"),
         ("rooms", "Confirming the rooms to cool"),
         ("inputs", "Preparing the inputs (room uses, heights, people and equipment)"),
         ("calculate", "Calculating the cooling load")]
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
    """Cooled rooms: the person's ticks, else the drawing default; a room without an area can't be cooled."""
    rows = []
    for row in scope.get("candidates") or []:
        has_area = row.get("area_m2") is not None
        chosen = include.get(row.get("key"))
        cooled = has_area and (chosen if chosen is not None else bool(row.get("include") or has_area))
        rows.append({"key": row.get("key"), "include": cooled, "reason": "" if cooled else "Not cooled"})
    return rows


def _resolve_inputs(web, project_id):
    """The guided resolver, on the server: evidence workflow, then the model-input resolver."""
    from backend import model_input_resolution_service, skill_workflow_service
    project = web.project_by_id(project_id)
    skill_workflow_service.post(web, project, {"action": "start"})
    deadline = time.monotonic() + WORKFLOW_TIMEOUT_S
    while project_id in skill_workflow_service._RUNNING:
        if time.monotonic() > deadline:
            raise RuntimeError("Preparing the inputs took longer than 10 minutes. Press Calculate again.")
        time.sleep(1)
    # Evidence exceptions are listed by the resolver itself; they don't stop it (as in the engineer screen).
    model_input_resolution_service.post(web, web.project_by_id(project_id), {"action": "resolve"})


def start(web, project, data):
    include = _include_map(data)
    reviewer = " ".join(str(data.get("reviewer") or "").split())[:80] or "Contractor"

    def work(web, project_id, step):
        from backend import ai_preliminary_service, job_service
        project = web.project_by_id(project_id)
        paths = ai_preliminary_service._paths(project)
        step("answers")
        if job_service.job_setup(project).get("above"):
            job_service.apply_roof_answer(web, project)

        def model_and_calculate():
            step("model")
            ai_preliminary_service._assemble(web, web.project_by_id(project_id))
            step("rooms")
            input_set = ai_preliminary_service._read(paths["current_set"], {})
            scope = ai_preliminary_service._room_scope_state(paths, input_set)
            rows = _rows(scope, include)
            if not any(row["include"] for row in rows):
                raise ValueError("No room has an area yet. Add room areas on the Rooms tab, then calculate.")
            ai_preliminary_service._confirm_room_scope(paths, {"reviewer": reviewer, "rows": rows,
                                                                "candidate_fingerprint": scope.get("candidate_fingerprint", "")})
            step("calculate")
            ai_preliminary_service._calculate(web, web.project_by_id(project_id))

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
        report = ai_preliminary_service._read(paths["report"], {})
        peak = report.get("included_scope_peak") or {}
        return {"total_kw": peak.get("final_design_total_kw", peak.get("design_total_kw")), "inputs_resolved": resolved}

    return _JOB.start(web, project, {"requested_by": reviewer}, work)
