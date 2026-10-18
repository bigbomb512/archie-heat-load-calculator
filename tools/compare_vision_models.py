#!/usr/bin/env python3
"""Compare AI models on the steps that read drawings (the drawing tasks and equipment reading), on the answer-key
cases, inside a sandbox: the app's jobs and job list are never touched, and spending stops at a hard cap.

How it works:
- prepare: for each case, the case's PDF is analysed and its pages read (pass 1, the normal DeepSeek setup) in a
  sandbox folder, once. That folder is the shared starting point ("base"); nothing after pass 1 has run in it.
- run: for each model setup, the base is cloned (copy-on-write) and the real pipeline step runs in the clone with
  that setup: the drawing tasks (drawing_tasks_service.answer_all, rounds, checkers and all) and/or equipment
  reading (page_extraction_service), with the automatic chain to later steps switched off.
- score: the drawing tasks are scored against evaluations/autonomous/<case>.json with the existing scorer; equipment
  (Butcher Buffet only) against its printed equipment schedule (page 5's text layer: 48 coded items).

Every call is logged in its clone's ai_usage.jsonl with the US$ OpenRouter charged. Before each call the total for
the whole sandbox is checked; at the cap the call is refused (UsageLimitReached), which stops that run cleanly.

  PYTHONPATH=. python3 tools/compare_vision_models.py plan  --cases caseA caseB --configs gemini-low qwen haiku
  PYTHONPATH=. python3 tools/compare_vision_models.py prepare --cases caseA caseB
  PYTHONPATH=. python3 tools/compare_vision_models.py run   --cases caseA --configs gemini qwen --what drawings
  PYTHONPATH=. python3 tools/compare_vision_models.py score
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SANDBOX = ROOT / "output" / "evaluations" / "model_compare"
DEFAULT_CAP_USD = 3.0
# Stop this far below the cap: calls already in flight (one per parallel run) still get charged.
CAP_MARGIN_USD = 0.10

# Each case: its answer key and the app job whose uploaded PDF it is (only the PDF is used, read-only).
CASES = {
    "caseA": {"label": "Butcher Buffet", "job": "20260226-butcher-buffet-_-melrose-park-construction-rev-b_2_-1792272958"},
    "caseB": {"label": "Global Exchange detail design",
              "job": "260318_global-exchange-western-sydney-airport_r2-025---detail-design-260501-1789995931"},
    "caseC": {"label": "Global Exchange air-conditioning set",
              "job": "global-exchange-wsa_air-conditioning-design-full-set-drawings_rev-3-af-260701-1788769328"},
    "caseD": {"label": "Mini-split (scanned)", "job": "mini_split_drawings_2024-08-01-1789995914"},
}

# Model setups. Prices (US$ per million tokens read / written) as listed on OpenRouter on 2026-10-18.
CONFIGS = {
    "gemini": {"model": "google/gemini-3.8-flash", "reasoning": "default", "price": (0.83, 4.12),
               "note": "the model's own thinking (the setup before 2026-10-18)"},
    "gemini-low": {"model": "google/gemini-3.8-flash", "reasoning": "low", "price": (0.83, 4.12),
                   "note": "thinking turned down (the default since 2026-10-18)"},
    "gemini-lite": {"model": "google/gemini-3.1-flash-lite", "price": (0.25, 1.50), "note": "cheaper Gemini"},
    "qwen": {"model": "qwen/qwen3.8-flash", "price": (0.15, 0.47), "note": "about 9x cheaper"},
    "haiku": {"model": "anthropic/claude-haiku-5.5", "price": (0.10, 0.50), "note": "about 8x cheaper"},
    "luna": {"model": "openai/gpt-6-luna", "price": (0.10, 0.50), "note": "about 8x cheaper"},
}
# Tried 2026-10-18 and set aside: qwen/qwen3.8-flash is served only by a host that may store prompts, so OpenRouter
# refuses it under Archie's data policy (data_collection: deny); every call came back HTTP 404 at no charge.

# Measured on Butcher Buffet's latest run (2026-10-18), current setup: tokens per step, for the cost estimate.
MEASURED_TOKENS = {"drawings": (60_000, 55_000), "equipment": (70_000, 50_000)}
CASE_SIZE = {"caseA": 1.0, "caseB": 0.6, "caseC": 0.5, "caseD": 0.4}   # rough share of Butcher Buffet's work
# The thinking setting is expected to cut written tokens; until measured, the estimate assumes half.
REASONING_OUTPUT_SHARE = {"low": 0.5}

_SPEND_LOCK = threading.Lock()
_REGISTRY_LOCK = threading.RLock()


# ---------------------------------------------------------------- sandbox and spending

def sandbox_web():
    """The app module with its job list and job folders redirected into the sandbox. The sandbox job list is read
    and written under one lock and saved atomically: parallel runs share it (round 1 once read a half-written file)."""
    from backend import web_app
    web_app.PROJECTS_FILE = SANDBOX / "projects.json"
    web_app.WEB_REVIEW = SANDBOX / "jobs"
    web_app.UPLOADS = SANDBOX / "uploads"
    (SANDBOX / "jobs").mkdir(parents=True, exist_ok=True)
    if not getattr(web_app, "_compare_sandboxed", False):
        def load_projects():
            with _REGISTRY_LOCK:
                path = web_app.PROJECTS_FILE
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

        def save_projects(projects):
            with _REGISTRY_LOCK:
                staged = web_app.PROJECTS_FILE.with_suffix(".json.stage")
                staged.write_text(json.dumps(projects, indent=2), encoding="utf-8")
                os.replace(staged, web_app.PROJECTS_FILE)

        web_app.load_projects, web_app.save_projects = load_projects, save_projects
        web_app._compare_sandboxed = True
    return web_app


def spent_usd():
    """US$ charged so far for every call made in the sandbox (from each clone's usage log)."""
    total = 0.0
    for log in SANDBOX.rglob("*ai_usage.jsonl"):        # live logs, and runs replaced by a re-run
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("cost_usd") or 0)
            except (ValueError, TypeError):
                continue
    return total


class Budgeted:
    """A provider that refuses to call once the sandbox's spending reaches the cap."""

    def __init__(self, provider, cap):
        self.provider, self.cap = provider, cap
        self.model = getattr(provider, "model", "")
        self.name = getattr(provider, "name", "openrouter")

    def propose(self, prompt, image_paths=()):
        from backend import ai_provider
        with _SPEND_LOCK:
            spent = spent_usd()
        if spent >= self.cap - CAP_MARGIN_USD:
            raise ai_provider.UsageLimitReached(f"The test budget of US${self.cap:.2f} is used (US${spent:.2f} spent).")
        return self.provider.propose(prompt, image_paths=image_paths)


def make_provider(config_name, step, cap):
    """An OpenRouter provider for a model setup and step, set up like ai_provider.get would, under the cap."""
    from backend import ai_provider
    config = CONFIGS[config_name]
    provider = ai_provider.OpenRouterProvider(model=config["model"])
    provider.step = step
    if step == "DRAWINGS":
        limit = ai_provider.drawing_timeout_s()
        provider.timeout, provider.deadline_s = limit, limit
    if config.get("reasoning"):
        provider.reasoning_effort = config["reasoning"]
    return Budgeted(provider, cap)


def _register(web, project):
    with _REGISTRY_LOCK:
        projects = web.load_projects()
        projects[project["id"]] = project
        web.save_projects(projects)


def _real_project(case):
    """The case's job from the app's own job list, read-only (only its PDF path and name are used)."""
    registry = json.loads((ROOT / "output" / "web_projects.json").read_text(encoding="utf-8"))
    project = registry.get(CASES[case]["job"])
    if not project or not Path(str(project.get("pdf") or "")).is_file():
        raise SystemExit(f"{case}: the job {CASES[case]['job']} or its PDF isn't on this computer.")
    return project


def _wait(job_status, label, seconds=3600):
    deadline = time.monotonic() + seconds
    while True:
        state = job_status()
        if state.get("status") not in {"queued", "running"}:
            return state
        if time.monotonic() > deadline:
            raise RuntimeError(f"{label} took longer than {seconds} s")
        time.sleep(3)


# ---------------------------------------------------------------- prepare (analysis + pass 1, once per case)

def prepare(case, cap):
    from backend import new_job_service, page_extraction_service, page_inventory_service, page_preparation_service
    web = sandbox_web()
    base = SANDBOX / "jobs" / f"base-{case}"
    marker = base / "compare_base.json"
    if marker.exists():
        print(f"{case}: base ready ({base})")
        return
    if spent_usd() >= cap - CAP_MARGIN_USD:
        raise SystemExit("The test budget is used; not preparing.")
    real = _real_project(case)
    project = {key: value for key, value in real.items()
               if key in {"name", "pdf", "pages", "created_at", "owner_id"}}
    project.update({"id": f"base-{case}", "analysed": False})
    base.mkdir(parents=True, exist_ok=True)
    _register(web, project)
    print(f"{case}: analysing the PDF (no AI)...")
    os.environ["ARCHIE_PAGE_READING"] = "off"          # pass 1 is started below, without the automatic chain
    try:
        web.analyse_project(project, review_dir=base, app=web, persist_project=True)
    finally:
        os.environ.pop("ARCHIE_PAGE_READING", None)
    project = web.project_by_id(project["id"])
    pages = new_job_service.default_pages(web, project)
    print(f"{case}: preparing {len(pages)} drawing pages (no AI)...")
    page_preparation_service.start(web, project, {"pages": pages, "requested_by": "model comparison"})
    prepared = _wait(lambda: page_preparation_service.status(web, web.project_by_id(project["id"])), "page preparation")
    if prepared.get("status") != "done":
        raise SystemExit(f"{case}: page preparation stopped: {prepared.get('error')}")
    print(f"{case}: reading every page (pass 1, DeepSeek)...")
    chain = page_extraction_service.start_after_pass1
    page_extraction_service.start_after_pass1 = lambda *args, **kwargs: None    # no equipment, drawings or skills
    try:
        page_inventory_service.start(web, web.project_by_id(project["id"]), {"requested_by": "model comparison"})
        read = _wait(lambda: page_inventory_service.status(web, web.project_by_id(project["id"])), "page reading")
    finally:
        page_extraction_service.start_after_pass1 = chain
    if read.get("status") != "done":
        raise SystemExit(f"{case}: page reading stopped: {read.get('error')}")
    marker.write_text(json.dumps({"case": case, "prepared_at": datetime.now().isoformat(timespec="seconds"),
                                  "pages_read": read.get("pages_read")}, indent=2), encoding="utf-8")
    print(f"{case}: base ready; spent so far US${spent_usd():.3f}")


# ---------------------------------------------------------------- run one setup on one case

def _clone(case, config, what):
    """A copy-on-write clone of the case's base for one setup and step, registered in the sandbox job list."""
    web = sandbox_web()
    base = SANDBOX / "jobs" / f"base-{case}"
    if not (base / "compare_base.json").exists():
        raise SystemExit(f"{case}: run prepare first.")
    clone_id = f"{case}-{config}-{what}"
    clone = SANDBOX / "jobs" / clone_id
    if clone.exists():
        # Keep what the earlier run of this setup was charged: the spending total sums every log in the sandbox.
        log = clone / "ai_usage.jsonl"
        if log.exists():
            kept = SANDBOX / "spent" / f"{clone_id}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.ai_usage.jsonl"
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(log), str(kept))
        shutil.rmtree(clone)
    subprocess.run(["cp", "-cR", str(base), str(clone)], check=True)     # APFS clone: fast, no extra disk until written
    (clone / "ai_usage.jsonl").unlink(missing_ok=True)                    # pass 1's cost belongs to the base
    project = {**web.project_by_id(f"base-{case}"), "id": clone_id}
    project = json.loads(json.dumps(project).replace(str(base), str(clone)))
    _register(web, project)
    return web, project, clone


def run_drawings(case, config, cap):
    from backend import autonomous_tasks_service, drawing_tasks_service
    web, project, clone = _clone(case, config, "drawings")
    started = time.monotonic()
    try:
        result = drawing_tasks_service.answer_all(web, project, provider_for=lambda task: make_provider(config, "DRAWINGS", cap))
        stopped = ""
    except Exception as error:                   # the budget, or a fault: what was answered so far is still scored
        result, stopped = {}, str(error)[:300]
    autonomous_tasks_service._export_determinations(clone)
    summary = {"case": case, "config": config, "what": "drawings", "seconds": round(time.monotonic() - started),
               "stopped": stopped, **{key: result.get(key) for key in ("answered", "refused", "calls", "rounds")}}
    (clone / "compare_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{case} {config} drawings: {summary}")
    return summary


def run_equipment(case, config, cap):
    from backend import page_extraction_service, page_inventory_service
    web, project, clone = _clone(case, config, "equipment")
    started = time.monotonic()
    factory, chain = page_inventory_service.PROVIDER_FACTORY, page_extraction_service._start_skills
    page_inventory_service.PROVIDER_FACTORY = lambda: make_provider(config, "PASS2", cap)
    page_extraction_service._start_skills = lambda *args, **kwargs: None       # no drawing tasks or skills after
    try:
        page_extraction_service.start(web, project, {"kinds": ["equipment_appliances"]})
        state = _wait(lambda: page_extraction_service.status(web, web.project_by_id(project["id"]), "equipment_appliances"),
                      "equipment reading")
    finally:
        page_inventory_service.PROVIDER_FACTORY, page_extraction_service._start_skills = factory, chain
    summary = {"case": case, "config": config, "what": "equipment", "seconds": round(time.monotonic() - started),
               "stopped": state.get("error", "") if state.get("status") != "done" else ""}
    (clone / "compare_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{case} {config} equipment: {summary}")
    return summary


# ---------------------------------------------------------------- scoring

def schedule_key(pdf_path):
    """The printed equipment schedule from the PDF's text layer, {code: name} (no AI): the same reader the app uses."""
    from ai.page_extraction import printed_schedule
    texts = {}
    for page in range(1, 400):
        result = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf_path), "-"],
                                capture_output=True, text=True)
        if result.returncode != 0:
            break
        texts[page] = result.stdout
    schedule = {}
    for page, text in texts.items():
        schedule.update(printed_schedule({page: text}))
    return {code: row["name"] for code, row in schedule.items()}


def _words(text):
    return {word for word in re.findall(r"[a-z0-9]+", str(text).casefold()) if len(word) > 1}


def score_equipment(clone, key):
    """Codes read against the printed schedule. Benches, sinks and shelving give off no heat, so the prompt leaves
    them out: "codes_found" is not a recall over every row. What matters: codes that aren't on the schedule (misread)
    and items without a code (extras, or plan labels counted twice)."""
    findings = json.loads((clone / "page_extraction.json").read_text(encoding="utf-8")).get("equipment_appliances", {}).get("findings", [])
    found, uncoded, unknown = {}, 0, []
    for row in findings:
        value = row.get("value") or {}
        code = str(value.get("code") or "").upper()
        if code in key:
            found[code] = value.get("name", "")
        elif code:
            unknown.append(code)
        else:
            uncoded += 1
    named_right = sum(1 for code, name in found.items() if _words(name) & _words(key[code]))
    return {"schedule_items": len(key), "codes_found": len(found), "names_match": named_right,
            "codes_not_on_schedule": unknown, "items_without_a_schedule_code": uncoded, "items_total": len(findings)}


def _usage(clone):
    calls = cost = written = thinking = failed = 0
    log = clone / "ai_usage.jsonl"
    for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
        row = json.loads(line)
        calls += 1
        cost += float(row.get("cost_usd") or 0)
        written += int(row.get("output_tokens") or 0)
        thinking += int(row.get("reasoning_output_tokens") or 0)
        failed += 0 if row.get("ok") else 1
    return {"calls": calls, "cost_usd": round(cost, 4), "written_tokens": written, "thinking_tokens": thinking,
            "failed_calls": failed}


def score():
    from ai.autonomous_task_scoring import score_case
    rows = []
    for clone in sorted((SANDBOX / "jobs").glob("case*-*-*")):
        run = json.loads((clone / "compare_run.json").read_text(encoding="utf-8")) if (clone / "compare_run.json").exists() else {}
        if not run:
            continue
        row = {**run, **_usage(clone)}
        if run["what"] == "drawings":
            answer_key = json.loads((ROOT / "evaluations" / "autonomous" / f"{run['case']}.json").read_text(encoding="utf-8"))
            private_path = ROOT / "output" / "evaluations" / "private" / f"{run['case']}.json"
            private = json.loads(private_path.read_text(encoding="utf-8")) if private_path.exists() else None
            determinations = json.loads((clone / "ai_tasks" / "determinations.json").read_text(encoding="utf-8")) \
                if (clone / "ai_tasks" / "determinations.json").exists() else {}
            report = score_case(answer_key, determinations, private)
            row["tasks"] = {task: {status: sum(1 for item in items if item["status"] == status)
                                   for status in ("correct", "wrong", "missing")}
                            for task, items in report["tasks"].items()}
            row["detail"] = {task: [f"{item['item']}: {item['status']} ({item['detail']})" for item in items]
                             for task, items in report["tasks"].items()}
        else:
            pdf = json.loads((clone.parent.parent / "projects.json").read_text(encoding="utf-8"))[clone.name]["pdf"]
            row["equipment"] = score_equipment(clone, schedule_key(pdf))
        rows.append(row)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = SANDBOX / f"report-{stamp}.json"
    out.write_text(json.dumps({"spent_usd": round(spent_usd(), 4), "runs": rows}, indent=2), encoding="utf-8")
    lines = [f"# Model comparison ({stamp})", "", f"Spent in the sandbox: US${spent_usd():.3f}", "",
             "| Case | Setup | Step | Correct / wrong / missing | Calls | Failed | US$ | Written tokens | Thinking | Time | Stopped |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        if row["what"] == "drawings":
            totals = [sum(task[status] for task in row["tasks"].values()) for status in ("correct", "wrong", "missing")]
            result = " / ".join(map(str, totals))
        else:
            eq = row["equipment"]
            result = (f"{eq['codes_found']} scheduled items ({eq['names_match']} names right), "
                      f"{len(eq.get('codes_not_on_schedule', []))} codes not on the schedule, "
                      f"{eq['items_without_a_schedule_code']} without a code")
        lines.append(f"| {row['case']} | {row['config']} | {row['what']} | {result} | {row['calls']} | {row['failed_calls']} | "
                     f"{row['cost_usd']:.3f} | {row['written_tokens']:,} | {row['thinking_tokens']:,} | "
                     f"{row['seconds'] // 60} min | {row['stopped'][:60]} |")
    for row in rows:
        if row["what"] == "drawings":
            lines += ["", f"## {row['case']} · {row['config']}"]
            for task, counts in row["tasks"].items():
                lines.append(f"- {task}: {counts['correct']} correct, {counts['wrong']} wrong, {counts['missing']} missing")
    (SANDBOX / f"report-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nReport: {out.with_suffix('.md')}")


# ---------------------------------------------------------------- plan (no AI)

def plan(cases, configs, what, prepared):
    total = 0.0
    lines = []
    for case in cases:
        if case not in prepared:
            lines.append(f"{case}: prepare (pass 1 with DeepSeek) about US${0.13 * CASE_SIZE[case]:.2f}")
            total += 0.13 * CASE_SIZE[case]
        for config in configs:
            price_in, price_out = CONFIGS[config]["price"]
            share = REASONING_OUTPUT_SHARE.get(CONFIGS[config].get("reasoning", ""), 1.0)
            for step in what:
                if step == "equipment" and case != "caseA":
                    continue
                tokens_in, tokens_out = MEASURED_TOKENS[step]
                cost = CASE_SIZE[case] * (tokens_in * price_in + tokens_out * share * price_out) / 1e6
                total += cost
                lines.append(f"{case} {config:12s} {step:10s} about US${cost:.3f}")
    print("\n".join(lines))
    print(f"Estimated total: US${total:.2f} (already spent in the sandbox: US${spent_usd():.3f})")
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "prepare", "run", "score"])
    parser.add_argument("--cases", nargs="+", default=["caseA", "caseB"], choices=sorted(CASES))
    parser.add_argument("--configs", nargs="+", default=["gemini", "gemini-low", "gemini-lite", "haiku", "luna"],
                        choices=sorted(CONFIGS))
    parser.add_argument("--what", nargs="+", default=["drawings", "equipment"], choices=["drawings", "equipment"])
    parser.add_argument("--cap", type=float, default=DEFAULT_CAP_USD, help="hard spending cap for the whole sandbox, US$")
    args = parser.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY") and args.command in {"prepare", "run"}:
        raise SystemExit("OPENROUTER_API_KEY isn't set.")
    prepared = {case for case in CASES if (SANDBOX / "jobs" / f"base-{case}" / "compare_base.json").exists()}
    if args.command == "plan":
        plan(args.cases, args.configs, args.what, prepared)
    elif args.command == "prepare":
        for case in args.cases:
            prepare(case, args.cap)
    elif args.command == "run":
        if "drawings" in args.what:
            with ThreadPoolExecutor(max_workers=len(args.configs)) as pool:     # one clone per setup, in parallel
                list(pool.map(lambda job: run_drawings(job[0], job[1], args.cap),
                              [(case, config) for case in args.cases for config in args.configs]))
        if "equipment" in args.what:
            for config in args.configs:                                          # one at a time (shared provider hook)
                if "caseA" in args.cases:
                    run_equipment("caseA", config, args.cap)
        print(f"Spent in the sandbox: US${spent_usd():.3f} of US${args.cap:.2f}")
    else:
        score()


if __name__ == "__main__":
    main()
