"""A long job for one project, run in a server thread with its progress in a JSON file.

The workspace starts a job, polls its status, and can be closed or reloaded meanwhile.
Used by page_preparation_service and calculation_service.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import traceback
import uuid


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.stage")
    stage.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(stage, path)


def _process_alive(pid):
    """True when a process with this id is running (another server process may own a job)."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BackgroundJob:
    """One job file per project; at most one run per project at a time (per server process)."""

    def __init__(self, file_name, steps, thread_name, interrupted_message):
        self.file_name = file_name
        self.steps = list(steps)  # [(key, label)]
        self.labels = dict(self.steps)
        self.thread_name = thread_name
        self.interrupted_message = interrupted_message
        self.lock = threading.Lock()
        self.running = set()

    def path(self, project):
        return Path(project["review_dir"]) / self.file_name

    def status(self, project):
        job = read_json(self.path(project))
        if not job:
            return {"id": project["id"], "status": "none"}
        if (job.get("status") in {"queued", "running"} and project["id"] not in self.running
                and not (job.get("pid") != os.getpid() and _process_alive(job.get("pid")))):
            # The server stopped while the job ran (restart or crash). Say so; the workspace offers to start again.
            job = {**job, "status": "interrupted", "error": self.interrupted_message}
        return {"id": project["id"], **job}

    def start(self, web, project, fields, work):
        """Start work(web, project_id, step) in a thread, or join the run already going.

        work calls step(key) as it moves on, and returns a dict merged into the finished job.
        """
        with self.lock:
            current = read_json(self.path(project))
            # Join a run in this process, or one still owned by another live server process on the same folder.
            if current.get("status") in {"queued", "running"} and (
                    project["id"] in self.running
                    or (current.get("pid") != os.getpid() and _process_alive(current.get("pid")))):
                return {**self.status(project), "deduplicated": True}
            first = self.steps[0][0]
            job = {"schema_version": 1, "job_id": uuid.uuid4().hex, "status": "running", "step": first,
                   "step_label": self.labels[first], "started_at": now(), "pid": os.getpid(), **fields}
            write_json(self.path(project), job)
            self.running.add(project["id"])
        threading.Thread(target=self._run, args=(web, project, job["job_id"], work),
                         daemon=True, name=self.thread_name).start()
        return self.status(project)

    def _run(self, web, project, job_id, work):
        started = time.monotonic()
        path = self.path(project)

        def step(key, label=None):
            job = read_json(path)
            if job.get("job_id") != job_id:
                raise RuntimeError("A newer run replaced this one.")
            job.update({"step": key, "step_label": label or self.labels[key]})
            write_json(path, job)

        try:
            extra = work(web, project["id"], step) or {}
            job = read_json(path)
            if job.get("job_id") == job_id:
                job.update({"status": "done", "finished_at": now(), "seconds": round(time.monotonic() - started, 1), **extra})
                job.pop("error", None)
                write_json(path, job)
        except Exception as error:  # reported to the person, with the step it failed on
            traceback.print_exc()
            job = read_json(path)
            if job.get("job_id") == job_id:
                job.update({"status": "failed", "finished_at": now(), "seconds": round(time.monotonic() - started, 1),
                            "error": str(error) or error.__class__.__name__})
                write_json(path, job)
        finally:
            with self.lock:
                self.running.discard(project["id"])
