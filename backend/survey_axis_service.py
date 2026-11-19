"""The drawing set's orientation from its boundary bearings (ai/survey_bearings.py), read once per PDF per job.

Reading every character of every sheet for bearing labels is most of the drawing checks' time, and it needs only
the PDF. So page preparation starts warm() beside its own steps, and the north check (autonomous_tasks_service.
_survey_axis) then finds the answer kept in survey_bearings.json. A job reads its bearings once: a check that
arrives while a reading is under way waits for it instead of reading the PDF a second time.
"""

import json
from pathlib import Path
import threading

from backend.vision_extraction_service import _atomic_json

FILE_NAME = "survey_bearings.json"
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock(root):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(Path(root).resolve()), threading.Lock())


def _cached(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def axis_for_pdf(root, pdf):
    """The survey axis (survey_bearings.axis) for this PDF, from the job's kept answer when it is for this version of
    the PDF (its size and modification time), else read now and kept; None when no bearings agree."""
    from ai import survey_bearings
    root, pdf = Path(root), Path(pdf)
    with _lock(root):
        stat = pdf.stat()
        key = f"{survey_bearings.METHOD_ID}:{stat.st_size}:{stat.st_mtime_ns}"
        cached = _cached(root / FILE_NAME)
        if isinstance(cached, dict) and cached.get("key") == key:
            return cached.get("axis")
        try:
            rows = survey_bearings.labels(pdf)
        except Exception as error:      # any PDF library failure: north comes from the arrows alone
            print(f"Couldn't read the survey bearings: {type(error).__name__}: {error}", flush=True)
            rows = []
        result = survey_bearings.axis(rows)
        root.mkdir(parents=True, exist_ok=True)
        _atomic_json(root / FILE_NAME, {"key": key, "method_id": survey_bearings.METHOD_ID, "axis": result,
                                        "labels": rows[:200]})
        return result


def warm(root, pdf):
    """Read the bearings ahead of the drawing checks (page preparation runs this in a background thread)."""
    pdf = Path(pdf)
    if pdf.is_file():
        axis_for_pdf(root, pdf)
