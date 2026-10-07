"""Prepare a job's drawing pages on the server, as one background job.

Before this, the browser drove the steps (save the page choice, then rebuild the
prepared drawings, then rebuild the drawing checks). Closing the page between
steps left the job half-prepared. The server now runs all steps in one thread
and records progress in page_preparation_job.json, which the workspace polls.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import traceback
import uuid

JOB_FILE = "page_preparation_job.json"
STEPS = [("pages", "Saving the page choice and reading the pages"),
         ("drawings", "Preparing the drawings"),
         ("checks", "Setting up the drawing checks")]
_LOCK = threading.Lock()
_RUNNING = set()


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _path(project):
    return Path(project["review_dir"]) / JOB_FILE


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.stage")
    stage.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(stage, path)


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
    job = _read(_path(project))
    if not job:
        return {"id": project["id"], "status": "none"}
    if job.get("status") in {"queued", "running"} and project["id"] not in _RUNNING:
        # The server stopped while the job ran (restart or crash). Say so; the workspace offers to start again.
        job = {**job, "status": "interrupted",
               "error": "Preparing the pages stopped before it finished (the server was restarted). Start it again."}
    return {"id": project["id"], **job}


def start(web, project, data):
    pages = _pages(data)
    run_checks = data.get("run_checks", True) is not False
    with _LOCK:
        current = _read(_path(project))
        if current.get("status") in {"queued", "running"} and project["id"] in _RUNNING:
            return {**status(web, project), "deduplicated": True}
        job = {"schema_version": 1, "job_id": uuid.uuid4().hex, "status": "running", "step": STEPS[0][0],
               "step_label": STEPS[0][1], "steps": [key for key, _ in STEPS if run_checks or key != "checks"],
               "pages": [row["page"] for row in pages], "started_at": _now(),
               "requested_by": " ".join(str(data.get("requested_by") or "").split())[:80]}
        _write(_path(project), job)
        _RUNNING.add(project["id"])
    threading.Thread(target=_run, args=(web, project["id"], job["job_id"], pages, run_checks),
                     daemon=True, name="archie-page-preparation").start()
    return status(web, project)


def _run(web, project_id, job_id, pages, run_checks):
    from backend import autonomous_tasks_service
    started = time.monotonic()
    project = web.project_by_id(project_id)
    path = _path(project)

    def step(key):
        job = _read(path)
        if job.get("job_id") != job_id:
            raise RuntimeError("A newer page preparation replaced this one.")
        job.update({"step": key, "step_label": dict(STEPS)[key]})
        _write(path, job)

    try:
        step("pages")
        web.save_page_decisions(web.project_by_id(project_id),
                                {"source_pdf": project.get("name", ""), "reviewed_at": _now(), "pages": pages})
        step("drawings")
        prepared = web.start_without_ai_evidence(web.project_by_id(project_id))
        if not prepared.get("has_reasoning_packet"):
            raise RuntimeError("The drawings could not be prepared from these pages.")
        if run_checks:
            step("checks")
            autonomous_tasks_service.run_all(web, web.project_by_id(project_id))
        job = _read(path)
        if job.get("job_id") == job_id:
            job.update({"status": "done", "finished_at": _now(), "seconds": round(time.monotonic() - started, 1)})
            job.pop("error", None)
            _write(path, job)
    except Exception as error:  # reported to the person, with the step it failed on
        traceback.print_exc()
        job = _read(path)
        if job.get("job_id") == job_id:
            job.update({"status": "failed", "finished_at": _now(), "seconds": round(time.monotonic() - started, 1),
                        "error": str(error) or error.__class__.__name__})
            _write(path, job)
    finally:
        with _LOCK:
            _RUNNING.discard(project_id)
