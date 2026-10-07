#!/usr/bin/env python3
"""Score the page finder's page roles against the answer sheets in evaluations/page_roles/.

The PDFs stay where they are. A local, git-ignored file maps each case to its PDF:

  output/evaluations/page_role_sources.json   {"caseP03": "/absolute/path/to/set.pdf", ...}

Each set is analysed in a temporary folder (deleted afterwards) and scored.
Reports go to output/evaluations/page-roles-<time>.json and .md.

  PYTHONPATH=. python3 tools/evaluate_page_roles.py
  PYTHONPATH=. python3 tools/evaluate_page_roles.py --case caseP03 --case caseP09
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.page_role_evaluation import render_markdown, score, summarise, validate_answer_sheet


def analyse(pdf_path):
    from backend import web_app
    with tempfile.TemporaryDirectory(prefix="archie-page-roles-") as temporary:
        review = Path(temporary) / "review"
        web_app.create_review_packet(Path(pdf_path), review, include_structure=True)
        return json.loads((review / "packet.json").read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report-only page-role scorecard across architect sets.")
    parser.add_argument("--cases", default=str(ROOT / "evaluations" / "page_roles"))
    parser.add_argument("--sources", default=str(ROOT / "output" / "evaluations" / "page_role_sources.json"))
    parser.add_argument("--case", action="append", help="Score only this case (repeatable).")
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluations"))
    args = parser.parse_args(argv)

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8")) if Path(args.sources).is_file() else {}
    sheets = [validate_answer_sheet(json.loads(path.read_text(encoding="utf-8")))
              for path in sorted(Path(args.cases).glob("*.json"))]
    if args.case:
        sheets = [sheet for sheet in sheets if sheet["case_id"] in set(args.case)]
    reports, skipped = [], []
    for sheet in sheets:
        pdf = sources.get(sheet["case_id"])
        if not pdf or not Path(pdf).is_file():
            skipped.append(sheet["case_id"])
            continue
        started = time.perf_counter()
        report = score(sheet, analyse(pdf))
        report["seconds"] = round(time.perf_counter() - started, 1)
        reports.append(report)
        print(f"{report['case_id']}: {report['totals']['passed']}/{report['totals']['facts']} "
              f"(main plan chosen: {report['main_plan_pages'] or 'none'})", flush=True)
    summary = summarise(reports)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"page-roles-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps({"summary": summary, "cases": reports, "skipped": skipped}, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(reports, summary), encoding="utf-8")
    print(f"Total: {summary['passed']}/{summary['facts']} facts ({summary['accuracy_percent']}%) over {summary['cases']} sets"
          + (f"; skipped (no local PDF): {', '.join(skipped)}" if skipped else ""))
    print(f"Report: {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
