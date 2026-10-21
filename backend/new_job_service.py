"""Start a newly uploaded job on the server: read the PDF, then prepare its drawing pages.

The old upload screen read the PDF in one long browser request and then had the browser choose the pages and
start their preparation, so closing the tab part-way left the job stuck. This runs the same steps in a server
thread: the PDF analysis (analyse_project), then page preparation (page_preparation_service) with the pages the
analysis selects by default, the choice the contractor workspace already makes for a new job. Page reading by
the AI starts from the analysis itself, as before.
"""

from pathlib import Path

from backend import page_preparation_service
from backend.job_runner import BackgroundJob, recovery, read_json, step_state

JOB_FILE = "new_job_job.json"
STEPS = [("analyse", "Reading the PDF: page titles, drawing types and previews"),
         ("pages", "Choosing the drawing pages")]
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-new-job",
                     "Reading the PDF stopped before it finished (the server was restarted). Start it again.")


def _with_folder(web, project):
    """The project with its review folder: a new upload has none until the analysis makes it."""
    if project.get("review_dir"):
        return project
    folder = Path(web.WEB_REVIEW) / project["id"]
    folder.mkdir(parents=True, exist_ok=True)
    return {**project, "review_dir": str(folder)}


def default_pages(web, project):
    """Page decisions for the pages the analysis selects by default, as the workspace sends them."""
    sheets = web.analysis_response(project).get("sheets", [])
    picked = [sheet for sheet in sheets if sheet.get("selected_by_default") or sheet.get("relevant")]
    return [{"page": sheet["page"], "detected_type": sheet.get("type", ""),
             "decision": ("Confirm as floor plan" if sheet.get("plan_role") == "main_floor_plan"
                          else "Confirm as RCP" if sheet.get("type") == "reflected_ceiling_plan"
                          else "Confirm as detected" if sheet.get("relevant") else "Keep as reference"),
             "scale_confirmed": False, "note": ""} for sheet in picked]


def status(web, project):
    """Where the new job is: this job's own run, then the page preparation it started."""
    folder_project = _with_folder(web, project)
    job = _JOB.status(folder_project)
    preparation = page_preparation_service.status(web, folder_project)
    analysed = bool(project.get("analysed") and project.get("packet"))
    ready = bool(project.get("reasoning_packet"))
    if any(row.get("status") == "stopping" for row in (job, preparation)):
        state = "stopping"
    elif job.get("status") in {"queued", "running"} or preparation.get("status") in {"queued", "running"}:
        state = "running"
    elif job.get("status") in {"failed", "interrupted"} or preparation.get("status") in {"failed", "interrupted"}:
        state = "failed"
    elif ready:
        state = "done"
    else:
        state = "not_started"
    running = preparation if preparation.get("status") in {"queued", "running", "stopping"} else job
    error = next((row.get("error") for row in (preparation, job)
                  if row.get("status") in {"failed", "interrupted"} and row.get("error")), "")
    return {"id": project["id"], "state": state, "step_state": step_state(state), "analysed": analysed, "drawings_ready": ready,
            "pages": project.get("pages"), "step_label": running.get("step_label", "") if state == "running" else "",
            "error": error if state == "failed" else "", "job": job, "preparation": preparation,
            "recovery_run_id": running.get("job_id"),
            "recovery": running.get("recovery", recovery(state)) if state == "stopping" else recovery(state)}


def start(web, project, data=None):
    """Read or safely resume preparation; joins active work unless restart is requested."""
    data = data or {}
    if data.get("action", "start") not in {"start", "retry", "resume", "restart"}:
        raise ValueError("Preparation action must be start, retry, resume or restart.")
    if data.get("action") == "restart":
        folder_project = _with_folder(web, project)
        owner = page_preparation_service._JOB if page_preparation_service._JOB.status(folder_project).get("status") in {"running", "queued", "stopping"} else _JOB
        if owner.request_restart(folder_project, data.get("expected_run_id")) is not None:
            return status(web, project)
    folder_project = _with_folder(web, project)
    parent_state = _JOB.status(folder_project).get("status")
    preparation = page_preparation_service.status(web, folder_project)
    if preparation.get("status") in {"failed", "interrupted"}:
        saved_pages = read_json(Path(folder_project["review_dir"]) / "reviewed_decisions.json").get("pages")
        page_preparation_service.start(web, folder_project, {"pages": saved_pages or default_pages(web, project),
                                                            "action": data.get("action", "resume")})
        return status(web, project)
    if project.get("reasoning_packet") and parent_state not in {"running", "queued", "stopping", "failed", "interrupted"}:
        return status(web, project)
    if page_preparation_service.status(web, _with_folder(web, project)).get("status") in {"queued", "running", "stopping"}:
        page_preparation_service.start(web, project, {"pages": default_pages(web, project), "action": data.get("action", "resume")})
        return status(web, project)

    def work(web, project_id, step):
        current = web.project_by_id(project_id)
        if not (current.get("analysed") and current.get("packet")):
            step("analyse")
            web.analyse_project(current)
            current = web.project_by_id(project_id)
        if current.get("reasoning_packet"):
            return {}
        step("pages")
        pages = default_pages(web, current)
        if not pages:
            raise RuntimeError("No drawing pages were found in this PDF.")
        started = page_preparation_service.start(web, current, {"pages": pages, "requested_by": "New job"})
        return {"preparation_job_id": started.get("job_id", "")}

    started = _JOB.start(web, _with_folder(web, project), {"requested_by": "New job"}, work, action=data.get("action", "start"), expected_run_id=data.get("expected_run_id"))
    return {**status(web, project), "deduplicated": bool(started.get("deduplicated"))}
