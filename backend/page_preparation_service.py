"""Prepare a job's drawing pages on the server, as one background job.

Before this, the browser drove the steps (save the page choice, then rebuild the
prepared drawings, then rebuild the drawing checks). Closing the page between
steps left the job half-prepared. The server now runs all steps in one thread
and records progress in page_preparation_job.json, which the workspace polls.
"""

from backend.job_runner import BackgroundJob, now

JOB_FILE = "page_preparation_job.json"
STEPS = [("pages", "Saving the page choice and reading the pages"),
         ("drawings", "Preparing the drawings"),
         ("checks", "Setting up the drawing checks")]
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-page-preparation",
                     "Preparing the pages stopped before it finished (the server was restarted). Start it again.")
_RUNNING = _JOB.running


def _pages(data):
    """The page rows to save: [{page, detected_type, decision, scale_confirmed, note}], as the engineer screen sends them."""
    rows = data.get("pages")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Choose at least one drawing page.")
    seen, clean = set(), []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("page")) is not int or row["page"] < 1 or row["page"] in seen:
            raise ValueError("Each page needs a unique positive page number.")
        seen.add(row["page"])
        clean.append({"page": row["page"], "detected_type": str(row.get("detected_type", ""))[:80],
                      "decision": str(row.get("decision", "Confirm as detected"))[:80],
                      "scale_confirmed": row.get("scale_confirmed") is True, "note": str(row.get("note", ""))[:300]})
    return clean


def status(web, project):
    return _JOB.status(project)


def start(web, project, data):
    pages = _pages(data)
    run_checks = data.get("run_checks", True) is not False
    fields = {"steps": [key for key, _ in STEPS if run_checks or key != "checks"],
              "pages": [row["page"] for row in pages],
              "requested_by": " ".join(str(data.get("requested_by") or "").split())[:80]}

    def work(web, project_id, step):
        from backend import autonomous_tasks_service
        project = web.project_by_id(project_id)
        step("pages")
        web.save_page_decisions(web.project_by_id(project_id),
                                {"source_pdf": project.get("name", ""), "reviewed_at": now(), "pages": pages})
        step("drawings")
        prepared = web.start_without_ai_evidence(web.project_by_id(project_id))
        if not prepared.get("has_reasoning_packet"):
            raise RuntimeError("The drawings could not be prepared from these pages.")
        if run_checks:
            step("checks")
            autonomous_tasks_service.run_all(web, web.project_by_id(project_id))
        return {}

    return _JOB.start(web, project, fields, work)
