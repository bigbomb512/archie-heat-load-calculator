#!/usr/bin/env python3
"""Run pass 1 of the PDF review (AI reads every page) on the page-role sets and score it beside the code's page finder.

The PDFs stay where they are (output/evaluations/page_role_sources.json). Page images and the AI's replies are
cached under output/evaluations/page_inventory/<case>/, so a second run calls the AI only for pages not yet read.
Uses the Codex CLI signed in with ChatGPT.

  python3 tools/evaluate_page_inventory.py --case caseP06 --case caseP04
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.page_inventory import main_plan_pages, packet_from_readings  # noqa: E402
from ai.page_inventory_scoring import render_markdown as render_information_markdown  # noqa: E402
from ai.page_inventory_scoring import score_information, summarise_information  # noqa: E402
from ai.page_role_evaluation import score, summarise, validate_answer_sheet  # noqa: E402
from backend import page_inventory_service as service  # noqa: E402
from tools.evaluate_page_roles import analyse_fast  # noqa: E402

OUTPUT = ROOT / "output" / "evaluations"


def run_case(sheet, pdf, workers):
    folder = OUTPUT / "page_inventory" / sheet["case_id"]
    images = service.render_pages(pdf, folder / "pages")
    reader = service.ai_provider.get()  # the provider chosen by ARCHIE_AI_PROVIDER
    started = time.monotonic()

    def progress(done, total):
        print(f"\r  {sheet['case_id']}: {done}/{total} pages", end="", flush=True)

    readings, failures, calls = service.read_pages(images, folder / "replies", reader, workers=workers, on_page=progress)
    seconds = round(time.monotonic() - started, 1)
    print()
    ai = score(sheet, packet_from_readings(readings, len(images)))
    code = score(sheet, analyse_fast(pdf, OUTPUT / "page_role_cache"))
    information = score_information(sheet, readings, len(images))
    return {"case_id": sheet["case_id"], "pages": len(images), "calls": calls, "seconds": seconds,
            "failures": {str(page): reason for page, reason in failures.items()},
            "main_plans": main_plan_pages(readings), "ai": ai, "code": code, "information": information,
            "readings": {str(page): row for page, row in sorted(readings.items())}}


def render_markdown(results):
    lines = ["# Pass 1 (AI reads every page) vs the code's page finder", "",
             "| Set | Pages | AI calls | Time | AI score | Code score | AI main plans |", "|---|---|---|---|---|---|---|"]
    for row in results:
        lines.append(f"| {row['case_id']} | {row['pages']} | {row['calls']} | {row['seconds']} s | "
                     f"{row['ai']['totals']['passed']}/{row['ai']['totals']['facts']} | "
                     f"{row['code']['totals']['passed']}/{row['code']['totals']['facts']} | "
                     f"{', '.join(f'{level or 'no level'}: {pages}' for level, pages in row['main_plans'].items()) or 'none'} |")
    for name in ("ai", "code"):
        summary = summarise([row[name] for row in results])
        lines += ["", f"**{name.upper()} total:** {summary['passed']}/{summary['facts']} ({summary['accuracy_percent']}%) — "
                  + ", ".join(f"{fact} {value['passed']}/{value['facts']}" for fact, value in summary["by_fact"].items())]
    for row in results:
        missed = [f"{fact['fact']} p{fact['page']} ({fact['detail']})" for fact in row["ai"]["facts"] if not fact["passed"]]
        if missed or row["failures"]:
            lines += ["", f"## {row['case_id']}"]
            lines += [f"- AI wrong: {item}" for item in missed]
            lines += [f"- Page {page} not read: {reason}" for page, reason in row["failures"].items()]
    info_reports = [row["information"] for row in results]
    lines += ["", render_information_markdown(info_reports, summarise_information(info_reports))]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", required=True, help="Set to run (repeatable), e.g. caseP06.")
    parser.add_argument("--workers", type=int, default=service.WORKERS)
    args = parser.parse_args(argv)
    sources = json.loads((OUTPUT / "page_role_sources.json").read_text(encoding="utf-8"))
    results = []
    for case_id in args.case:
        sheet = validate_answer_sheet(json.loads((ROOT / "evaluations" / "page_roles" / f"{case_id}.json").read_text(encoding="utf-8")))
        result = run_case(sheet, sources[case_id], args.workers)
        results.append(result)
        print(f"{case_id}: AI {result['ai']['totals']['passed']}/{result['ai']['totals']['facts']}, "
              f"code {result['code']['totals']['passed']}/{result['code']['totals']['facts']}, "
              f"{result['calls']} AI calls in {result['seconds']} s, {len(result['failures'])} pages not read; "
              f"information key {result['information']['status']}" +
              (f" · recall {result['information']['overall']['recall']}, precision {result['information']['overall']['precision']}"
               if result['information']['scored'] else " · not scored"), flush=True)
    base = OUTPUT / f"page-inventory-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps({"cases": results}, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(results), encoding="utf-8")
    print(f"Report: {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
