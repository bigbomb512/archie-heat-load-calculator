#!/usr/bin/env python3
"""Compare two versions of Archie on copies of saved jobs, with no AI calls: what changes for a real job?

    python3 tools/compare_job_versions.py --base /path/to/main/checkout --head . --out /tmp/compare JOB_ID [JOB_ID ...]

Each job folder is copied, then each version, on its own copy, re-reads the rooms, rebuilds the model from the saved answers,
calculates Phase 1 and the full result, and rebuilds the drawing questions. AI calls raise. Saved jobs aren't touched.
Run this after any change that could reach a shop job; every difference it prints needs a reason.
A last "smoke" step runs the real drawing and supervisor steps with a fake AI that answers little: it costs nothing and
shows crashes only a job's real data reaches (each saved run's own steps never see these paths otherwise).
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKIP = shutil.ignore_patterns("*_overlays", "thumbnails", "reasoning_packet.zip", "chatgpt_packet.zip", "chatgpt_runs",
                              "ai_preliminary_input_sets", "page_analysis_cache", "drawing_reply_cache", "skill_reply_cache")


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _step(result, name, work):
    from backend.job_runner import JobCancelled     # not an Exception; only the worker has the checkout on its path
    started = time.time()
    try:
        result[name] = work()
    except (Exception, JobCancelled) as error:      # a step that fails is itself a finding, and the later steps still run
        result[name] = {"error": f"{type(error).__name__}: {error}"[:400], "trace": traceback.format_exc(limit=4)[-600:]}
    result.setdefault("seconds", {})[name] = round(time.time() - started, 1)


def _report_summary(report):
    peak = report.get("included_scope_peak") or {}
    rooms = {}
    scenario = next((row for row in report.get("scenario_results", []) if row.get("scenario_id") == peak.get("scenario_id")), {})
    for room in scenario.get("rooms", []):
        hours = room.get("hours") or []
        if isinstance(peak.get("hour"), int) and peak["hour"] < len(hours):
            components = hours[peak["hour"]].get("components") or {}
            rooms[str(room.get("name"))] = {key: round(float(row.get("total_kw") or 0), 3) for key, row in components.items()
                                            if isinstance(row, dict) and row.get("total_kw")}
    coverage = report.get("phase1_coverage") or {}
    return {"status": report.get("status"), "blocked": report.get("blocked_reasons") or [],
            "total_kw": peak.get("final_design_total_kw", peak.get("design_total_kw")), "month": peak.get("month"), "hour": peak.get("hour"),
            "components": {key: round(float(row.get("total_kw") or 0), 3) for key, row in (peak.get("components") or {}).items()
                           if isinstance(row, dict) and row.get("total_kw")},
            "rooms": rooms, "included": coverage.get("rooms_included"), "left_out": coverage.get("rooms_left_out"),
            "complete": coverage.get("complete"), "partial": coverage.get("partial_label", "")}


def worker(repo, job_dir, project_file, out_file):
    sys.path.insert(0, repo)
    from backend import ai_provider

    def no_ai(*_args, **_kwargs):
        raise RuntimeError("No AI calls in a version comparison.")
    ai_provider.get = ai_provider.recorded = no_ai
    from backend import ai_preliminary_service as model, autonomous_tasks_service as tasks, calculation_service, room_inference_service

    root = Path(job_dir)
    project = json.loads(Path(project_file).read_text(encoding="utf-8"))

    class Web:
        WEB_REVIEW = str(root.parent)

        def project_by_id(self, _project_id):
            return project

        def update_project(self, updated):
            project.update(updated)
            return project

        def safe_link(self, path):
            return str(path)

        def workflow_required_artifacts(self, *_args, **_kwargs):
            return []

    web = Web()
    result = {}

    def inference():
        room_inference_service.post(web, project, {"action": "retry"})
        deadline = time.time() + 300
        while project["id"] in room_inference_service._RUNNING and time.time() < deadline:
            time.sleep(0.2)
        job = json.loads((root / "room_inference_job.json").read_text(encoding="utf-8"))
        run = json.loads((root / "ai_preliminary_run.json").read_text(encoding="utf-8"))
        rooms = (run.get("local_room_inference_proposal") or {}).get("rooms", [])
        return {"status": job.get("status"), "rooms": sorted(f"{row.get('label')} | {row.get('level_name')}" for row in rooms)}

    def pages():
        selection = tasks._p0_page_selection(root)
        return {"selected": [[row["page"], row.get("level_name", "")] for row in selection["selected"]]}

    def phase1():
        model._assemble(web, project)
        return _report_summary(model._calculate(web, project, scope="phase1_base_room"))

    def full():
        paths = model._paths(project)
        input_set = model._read(paths["current_set"], {})
        scope = model._room_scope_state(paths, input_set)
        rows = calculation_service._rows(scope, {})
        model._confirm_room_scope(paths, {"reviewer": "Version comparison", "rows": rows,
                                          "candidate_fingerprint": scope.get("candidate_fingerprint", "")})
        return _report_summary(model._calculate(web, project, scope="full"))

    def questions():
        tasks.run_all(web, project)
        rows = {}
        for record in tasks._all_current(root):
            packet = {key: value for key, value in (record.get("packet") or {}).items() if key != "prompt_fingerprint"}
            rows[f"{record.get('task')} {record.get('target')}"] = {
                "status": record.get("status"), "prompt": _sha(record.get("prompt") or ""), "packet": _sha(packet),
                "images": _sha(record.get("image_hashes") or []), "reason": str(record.get("block_reason") or "")[:100]}
        return rows

    _step(result, "inference", inference)
    _step(result, "pages", pages)
    _step(result, "phase1", phase1)
    _step(result, "full", full)
    _step(result, "questions", questions)

    class FakeAI:
        """Says there is no north arrow, and gives every other question an answer its checker refuses."""
        model = "fake"

        def propose(self, prompt, image_paths=()):
            return ({"found": False, "north_arrow_found": False} if "north" in prompt.casefold() else {}), {"usage": {}}

    def smoke():
        from backend import drawing_tasks_service, phase1_supervisor_service
        ai_provider.configured = lambda: True
        ai_provider.get = lambda *_args, **_kwargs: FakeAI()
        ai_provider.recorded = lambda provider, *_args, **_kwargs: provider
        drawn = drawing_tasks_service.answer_all(web, project, provider_for=lambda _task: FakeAI())
        # A real job always finishes this review before the supervisor; a saved job that stopped early hasn't, and the
        # supervisor rightly stops. On this copy, mark it finished so the supervisor's own steps run.
        review = root / "skill_workflow_run.json"
        saved = json.loads(review.read_text(encoding="utf-8")) if review.exists() else {}
        review.write_text(json.dumps({**saved, "scope": "phase1_envelope", "status": "completed"}), encoding="utf-8")
        supervised = phase1_supervisor_service.run(web, project)
        return {"drawing": {key: drawn.get(key) for key in ("answered", "refused", "rounds", "calls")},
                "outcome": supervised.get("outcome"), "progress": {key: value for key, value in tasks._task_progress(root).items() if key != "marker"}}

    _step(result, "smoke", smoke)
    Path(out_file).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")


def _copy_job(job_id, output, into):
    source = output / "web_review" / job_id
    target = into / job_id
    shutil.copytree(source, target, ignore=SKIP)
    project = json.loads((output / "web_projects.json").read_text(encoding="utf-8"))[job_id]
    text = json.dumps(project).replace(json.dumps(str(source))[1:-1], json.dumps(str(target))[1:-1])
    # A copied ai_input and packet still point at the original PDF, which is only read.
    project_file = into / f"{job_id}.project.json"
    project_file.write_text(text, encoding="utf-8")
    return target, project_file


def _run_version(tool, repo, job_id, output, work, out_file):
    into = work / Path(out_file).stem
    into.mkdir(parents=True)
    job_dir, project_file = _copy_job(job_id, output, into)
    try:
        done = subprocess.run([sys.executable, tool, "--worker", "--repo", repo, "--job-dir", str(job_dir),
                               "--project", str(project_file), "--result", str(out_file)], cwd=repo, capture_output=True,
                              text=True, timeout=3600)
        if done.returncode:
            Path(out_file).write_text(json.dumps({"crashed": done.stderr[-1500:]}), encoding="utf-8")
    finally:
        shutil.rmtree(into, ignore_errors=True)


def _differences(base, head):
    """[(path, base value, head value)] for every value that differs."""
    found = []

    def walk(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                walk(left.get(key), right.get(key), f"{path}/{key}" if path else str(key))
        elif left != right and not path.endswith("/trace"):     # a trace holds each checkout's paths
            found.append((path, left, right))
    walk(base, head, "")
    return found


def summarise(job_id, base, head):
    lines = [f"\n=== {job_id}"]
    for name, row in (("base", base), ("head", head)):
        phase1, full = row.get("phase1") or {}, row.get("full") or {}
        lines.append(f"  {name}: Phase 1 {phase1.get('total_kw', phase1.get('error'))} kW {phase1.get('partial') or ''} | "
                     f"full {full.get('total_kw', full.get('error'))} kW at {full.get('month')} {full.get('hour')}:00 | "
                     f"seconds {row.get('seconds')}")
    for path, left, right in _differences({key: value for key, value in base.items() if key not in {"seconds", "questions"}},
                                          {key: value for key, value in head.items() if key not in {"seconds", "questions"}}):
        lines.append(f"  {path}: {json.dumps(left, default=str)[:160]} -> {json.dumps(right, default=str)[:160]}")
    left, right = base.get("questions") or {}, head.get("questions") or {}
    if "error" in left or "error" in right:
        lines.append(f"  questions error: base {str(left.get('error'))[:200]} | head {str(right.get('error'))[:200]}")
    else:
        for key in sorted(set(left) | set(right)):
            if key not in left:
                lines.append(f"  question added: {key} [{right[key]['status']}] {right[key]['reason']}")
            elif key not in right:
                lines.append(f"  question gone: {key} [{left[key]['status']}]")
            elif left[key] != right[key]:
                changed = [field for field in left[key] if left[key][field] != right[key][field]]
                lines.append(f"  question changed: {key} {changed} [{left[key]['status']} -> {right[key]['status']}]")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs", nargs="*")
    parser.add_argument("--base")
    parser.add_argument("--head", default=".")
    parser.add_argument("--output", help="the output folder holding the saved jobs (default: the base checkout's)")
    parser.add_argument("--out")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--repo")
    parser.add_argument("--job-dir")
    parser.add_argument("--project")
    parser.add_argument("--result")
    args = parser.parse_args()
    if args.worker:
        return worker(args.repo, args.job_dir, args.project, args.result)
    tool = str(Path(__file__).resolve())
    output = Path(args.output or Path(args.base) / "output").resolve()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="archie-compare-", dir=out) as temporary:
        work = Path(temporary)
        for job_id in args.jobs:
            files = {name: out / f"{job_id[:40]}.{name}.json" for name in ("base", "head")}
            with ThreadPoolExecutor(2) as pool:
                for name, repo in (("base", args.base), ("head", args.head)):
                    pool.submit(_run_version, tool, str(Path(repo).resolve()), job_id, output, work, files[name])
            text = summarise(job_id, json.loads(files["base"].read_text(encoding="utf-8")),
                             json.loads(files["head"].read_text(encoding="utf-8")))
            print(text, flush=True)
            with (out / "summary.txt").open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")


if __name__ == "__main__":
    main()
