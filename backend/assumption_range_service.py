"""How far the saved cooling load could move with the facts the drawings don't settle (user request 2026-11-23).

For each open unknown (window glass, wall and roof construction, an AI-read north), the real calculation is run again
with that one input changed to a plausible alternative, on a throwaway copy of the job so the job itself is never
touched. The cases are in config/assumption_range_cases.json with their basis. The result is kept beside the saved
cooling result (assumption_range.json) and worked out once per result, in the background.

Nothing here is estimated: every kW comes from a full calculation. The first case is the baseline rerun on the copy;
if it doesn't reproduce the saved result, the inputs have changed since the load was calculated and the range is not
shown (the load has to be updated first). The range is one unknown at a time, not every unknown at its worst at once,
and it says so.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from backend import ai_preliminary_service, setpoint_comparison_service

VERSION = 1
CACHE_NAME = "assumption_range.json"
CASES_PATH = Path(__file__).resolve().parent.parent / "config" / "assumption_range_cases.json"
BASELINE_TOLERANCE_KW = 0.02
_RUNNING = {}
_GUARD = threading.Lock()
_ONE_AT_A_TIME = threading.Semaphore(1)      # each case is a full calculation: never two jobs' ranges at once


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def _write(path, value):
    path = Path(path)
    staging = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    staging.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(staging, path)


def load_cases():
    return _read(CASES_PATH, {"unknowns": []})


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _total(report):
    peak = (report or {}).get("included_scope_peak") or {}
    return _number(peak.get("final_design_total_kw", peak.get("design_total_kw")))


def _unit(kw, dwelling):
    from backend import contractor_report
    return contractor_report.ducted(kw, dwelling)["suggested"] if kw is not None else ""


def north_origin(root):
    """One origin for the job's north, the least certain of its plan pages': 'ai_estimate', 'other', 'edited_by_user',
    'measured_from_drawing', or '' when no plan page has a north."""
    from backend.thermal_audit_service import _north_origin
    north = _read(Path(root) / "reviewer_room_geometry.json").get("page_north") or {}
    survey = _read(Path(root) / "survey_bearings.json")
    origins = {_north_origin(row, survey) for row in north.values() if isinstance(row, dict)}
    if not origins:
        return ""
    if "ai_estimate" in origins:
        return "ai_estimate"
    if origins - {"edited_by_user", "measured_from_drawing"}:
        return "other"
    return "edited_by_user" if "edited_by_user" in origins else "measured_from_drawing"


def open_unknowns(root, report):
    """The unknowns in config/assumption_range_cases.json that are still open on this job, each with its cases.

    Windows: no glass answered and not every window from a certificate. Walls, roof: no construction answered and the
    result has such surfaces. North: the north on a plan page is the AI's reading, not measured or confirmed."""
    root = Path(root)
    glazing = _read(root / "glazing_answers.json")
    constructions = _read(root / "construction_answers.json")
    surfaces = [surface for room in ((_read(root / "ai_preliminary_input_set.json").get("material") or {})
                                     .get("hourly_load_model") or {}).get("rooms", []) if isinstance(room, dict)
                for surface in (room.get("cooling_load") or {}).get("envelope_surfaces", []) if isinstance(surface, dict)]
    windows = [surface for room in ((_read(root / "ai_preliminary_input_set.json").get("material") or {})
                                    .get("hourly_load_model") or {}).get("rooms", []) if isinstance(room, dict)
               for surface in (room.get("cooling_load") or {}).get("glazing_surfaces", []) if isinstance(surface, dict)]
    certified = bool(windows) and all("certificate" in str(row.get("glazing_source") or row.get("source") or "").casefold()
                                      for row in windows)
    has = {"wall": any(row.get("kind") == "opaque_wall" and row.get("boundary_method") == "external" for row in surfaces),
           "roof": any(row.get("kind") == "roof" for row in surfaces)}
    found = []
    for unknown in load_cases().get("unknowns", []):
        key = unknown["key"]
        if key == "windows":
            is_open = bool(windows) and not certified and not (glazing.get("all") or glazing.get("rooms"))
            answered = bool(glazing.get("all") or glazing.get("rooms"))
        elif key in {"wall", "roof"}:
            answer = constructions.get(key) or {}
            answered = bool(answer.get("all") or answer.get("rooms"))
            is_open = has[key] and not answered
        elif key == "north":
            origin = north_origin(root)
            answered = origin in {"measured_from_drawing", "edited_by_user"}
            is_open = origin == "ai_estimate"
        else:
            continue
        found.append({**unknown, "open": is_open, "answered": answered})
    return found


def _clone(source, target):
    """A copy of the job folder. Copy-on-write where the file system can (APFS, btrfs, XFS): instant, no extra space."""
    commands = ([["cp", "-cR", str(source), str(target)]] if sys.platform == "darwin"
                else [["cp", "-R", "--reflink=auto", str(source), str(target)]])
    for command in commands:
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=600)
            return
        except (OSError, subprocess.SubprocessError):
            shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)


def _apply(root, change):
    """Put one case's change into the copy through the same answer files the operators' answers use."""
    root = Path(root)
    record = {"source": "assumption range (what-if)", "by": "Range check", "at": time.time()}
    kind = change.get("kind")
    if kind == "glazing":
        _write(root / "glazing_answers.json", {"rooms": {}, "all": {**record, "u_value_w_m2k": change["u_value_w_m2k"],
                                                                     "shgc": change["shgc"], "glass": change["label"]}})
    elif kind == "construction":
        # A U-value typed in, as a typed answer is (no construction_id): see config/assumption_range_cases.json.
        stored = _read(root / "construction_answers.json")
        stored[change["surface"]] = {"all": {**record, "u_value_w_m2k": float(change["u_value_w_m2k"]),
                                             "construction_id": "", "construction": change.get("label", ""),
                                             "reference": ""}, "rooms": {}}
        _write(root / "construction_answers.json", stored)
    elif kind == "north":
        path = root / "reviewer_room_geometry.json"
        data = _read(path)
        for row in (data.get("page_north") or {}).values():
            if isinstance(row, dict) and isinstance(row.get("plan_up_azimuth_deg"), (int, float)):
                row["plan_up_azimuth_deg"] = (row["plan_up_azimuth_deg"] + change["rotate_deg"]) % 360
        _write(path, data)
    elif kind is not None:
        raise ValueError(f"Unknown change kind {kind}.")


def _calculate(root, source_project=None):
    """The full cooling calculation on a job folder (a copy), as "Update the final load" runs it (calculation_service
    start, phase "full"): the model assembled, the rooms confirmed with the quick-fix screen's cooled choices, then
    calculated. Returns its total kW."""
    from backend import calculation_service, job_service, quick_fix_service
    project = {**(source_project or {}), "id": "assumption-range", "review_dir": str(root),
               "reasoning_packet": "assumption-range", "name": "Assumption range"}
    # A stand-in for the web app: nothing is written to the job list or another job.
    web = SimpleNamespace(safe_link=lambda path: str(path), update_project=lambda _project: None,
                          project_by_id=lambda _id: project, load_projects=lambda: {project["id"]: project})
    paths = ai_preliminary_service._paths(project)
    if job_service.job_setup(project).get("above"):
        job_service.apply_roof_answer(web, project)
    ai_preliminary_service._assemble(web, project)
    input_set = ai_preliminary_service._read(paths["current_set"], {})
    scope = ai_preliminary_service._room_scope_state(paths, input_set)
    rows = calculation_service._rows(scope, quick_fix_service.cooled_choices(paths["root"]))
    if not any(row["include"] for row in rows):
        raise ValueError("No room has an area.")
    ai_preliminary_service._confirm_room_scope(paths, {"reviewer": "Range check", "rows": rows,
                                                        "candidate_fingerprint": scope.get("candidate_fingerprint", "")})
    ai_preliminary_service._calculate(web, project, scope="full")
    return _total(_read(paths["report"]))


def _cases(unknowns):
    rows = [{"unknown": "", "case_id": "baseline", "label": "As calculated", "change": {}}]
    for unknown in unknowns:
        if unknown["open"]:
            rows += [{"unknown": unknown["key"], "case_id": case["id"], "label": case["label"], "change": case["change"]}
                     for case in unknown["cases"]]
    return rows


def _key(report):
    return {"version": VERSION, **setpoint_comparison_service._report_key(report)}


def run(project):
    """Work out the range for the job's saved cooling result now (blocking). Writes assumption_range.json."""
    paths = ai_preliminary_service._paths(project)
    root = Path(paths["root"])
    report = _read(paths["report"])
    saved = _total(report)
    if saved is None:
        return {"status": "not_calculated", "reason": "No final cooling load has been calculated yet."}
    key = _key(report)
    unknowns = open_unknowns(root, report)
    cases = _cases(unknowns)
    state = {"key": key, "status": "running", "started_at": time.time(), "saved_kw": saved,
             "unknowns": [{key_: row[key_] for key_ in ("key", "label") if key_ in row} for row in unknowns if row["open"]],
             "cases": [], "total_cases": len(cases)}
    _write(root / CACHE_NAME, state)
    with _ONE_AT_A_TIME:
        work = Path(tempfile.mkdtemp(prefix=".assumption-range-", dir=root.parent))
        try:
            # One snapshot of the job for every case, so an update saved while the range runs can't mix into it.
            base = work / "base"
            _clone(root, base)
            (base / CACHE_NAME).unlink(missing_ok=True)
            for case in cases:
                copy = work / "case"
                shutil.rmtree(copy, ignore_errors=True)
                started = time.time()
                row = {key_: case[key_] for key_ in ("unknown", "case_id", "label")}
                try:
                    _clone(base, copy)
                    _apply(copy, case["change"])
                    row["kw"] = _calculate(copy, project)
                except Exception as error:           # one case failing doesn't lose the others
                    logging.getLogger(__name__).exception("Assumption range case failed", extra={"case": case["case_id"]})
                    row["error"] = f"{type(error).__name__}: {str(error)[:300]}"
                row["seconds"] = round(time.time() - started, 1)
                state["cases"].append(row)
                if case["case_id"] == "baseline":
                    baseline = row.get("kw")
                    if baseline is None or abs(baseline - saved) > BASELINE_TOLERANCE_KW:
                        why = (f" (recalculating it today gives {baseline:.2f} kW, not {saved:.2f} kW)" if baseline is not None
                               else f" (recalculating it today stops: {row.get('error', '').split(': ', 1)[-1]})")
                        state.update({"status": "inputs_changed", "baseline_kw": baseline, "finished_at": time.time(),
                                      "reason": "The inputs have changed since this load was calculated" + why
                                                + ", so a what-if on today's inputs wouldn't compare with it. Update "
                                                "the load, then the range is worked out again."})
                        _write(root / CACHE_NAME, state)
                        return state
                _write(root / CACHE_NAME, state)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    state.update({"status": "calculated", "finished_at": time.time()})
    _write(root / CACHE_NAME, state)
    return state


def _start(project):
    root = str(Path(project["review_dir"]))
    with _GUARD:
        thread = _RUNNING.get(root)
        if thread and thread.is_alive():
            return False
        thread = threading.Thread(target=_run_logged, args=(project,), daemon=True, name="assumption-range")
        _RUNNING[root] = thread
        thread.start()
        return True


def _run_logged(project):
    try:
        run(project)
    except Exception:
        logging.getLogger(__name__).exception("Assumption range failed", extra={"project_id": project.get("id")})


def _running(project):
    with _GUARD:
        thread = _RUNNING.get(str(Path(project["review_dir"])))
        return bool(thread and thread.is_alive())


def compact(state, report, unknowns, dwelling):
    """What the Results page shows: the range, and each unknown's swing with whether it changes the unit."""
    saved = _total(report)
    base_unit = _unit(saved, dwelling)
    rows = []
    for unknown in unknowns:
        cases = []
        for case in [row for row in state.get("cases", []) if row.get("unknown") == unknown["key"]]:
            if case.get("kw") is None:
                cases.append({"case_id": case["case_id"], "label": case["label"], "status": "not_calculated",
                              "reason": case.get("error") or "The calculation didn't finish."})
                continue
            unit = _unit(case["kw"], dwelling)
            cases.append({"case_id": case["case_id"], "label": case["label"], "status": "calculated",
                          "kw": round(case["kw"], 2), "delta_kw": round(case["kw"] - saved, 2),
                          "delta_pct": round((case["kw"] - saved) / saved * 100, 1) if saved else None,
                          "unit": unit, "changes_unit": unit != base_unit})
        values = [case["kw"] for case in cases if case["status"] == "calculated"]
        rows.append({"key": unknown["key"], "label": unknown["label"], "assumed": unknown.get("assumed", ""),
                     "open": unknown["open"], "answered": unknown["answered"], "cases": cases,
                     "low_kw": min([saved, *values]) if values else None, "high_kw": max([saved, *values]) if values else None,
                     "swing_kw": round(max(abs(value - saved) for value in values), 2) if values else None,
                     "changes_unit": any(case.get("changes_unit") for case in cases)})
    rows.sort(key=lambda row: (not row["changes_unit"], -(row["swing_kw"] or 0)))
    values = [value for row in rows for value in (row["low_kw"], row["high_kw"]) if value is not None]
    return {"saved_kw": round(saved, 2), "unit": base_unit,
            "low_kw": round(min(values), 2) if values else None, "high_kw": round(max(values), 2) if values else None,
            "unknowns": rows, "basis": "One unknown changed at a time, each through the full calculation; two unknowns "
                                       "at their worst together could go further than this range."}


def get(project, *, start=True):
    """The range for the job's saved cooling result: worked out in the background the first time it's asked for."""
    paths = ai_preliminary_service._paths(project)
    root = Path(paths["root"])
    report = _read(paths["report"])
    if _total(report) is None:
        return {"status": "not_calculated", "reason": "No final cooling load has been calculated yet."}
    from backend import house_windows_service
    dwelling = house_windows_service.is_dwelling(root)
    unknowns = open_unknowns(root, report)
    state = _read(root / CACHE_NAME)
    current = state.get("key") == _key(report)
    if not any(row["open"] for row in unknowns) and not (current and state.get("cases")):
        return {"status": "nothing_open", "saved_kw": round(_total(report), 2), "unit": _unit(_total(report), dwelling),
                "reason": "The windows, walls, roof and north are answered or read from the drawings, so there is no "
                          "range to work out for them."}
    if current and state.get("status") in {"calculated", "inputs_changed"}:
        if state["status"] == "inputs_changed":
            return {"status": "inputs_changed", "reason": state.get("reason", "")}
        # The unknowns as they were when the range was worked out, marked answered if they have been since.
        now = {row["key"]: row for row in unknowns}
        worked = [{**(next((row for row in load_cases()["unknowns"] if row["key"] == item["key"]), item)),
                   "open": now.get(item["key"], {}).get("open", False),
                   "answered": now.get(item["key"], {}).get("answered", False)} for item in state.get("unknowns", [])]
        return {"status": "calculated", **compact(state, report, worked, dwelling)}
    if current and state.get("status") == "running" and _running(project):
        return {"status": "running", "done": len(state.get("cases", [])), "total": state.get("total_cases", 0)}
    if start and os.environ.get("ARCHIE_ASSUMPTION_RANGE", "").lower() != "off":
        _start(project)
        total = len(_cases(unknowns))
        return {"status": "running", "done": 0, "total": total}
    return {"status": "not_started", "reason": "The range hasn't been worked out for this result yet."}
