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
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.page_role_evaluation import render_markdown, score, summarise, validate_answer_sheet


def cached_extraction(pdf_path, cache_dir):
    """Page text, visual features and document title for one PDF, extracted once and kept in cache_dir.

    The cache is keyed by the PDF's size and modification time; it holds no images.
    """
    from pdf_pipeline import page_finder
    pdf_path = Path(pdf_path)
    stat = pdf_path.stat()
    key = f"{pdf_path.name}|{stat.st_size}|{stat.st_mtime_ns}"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json")
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"key": key, "texts": list(page_finder.extract_text_pages(pdf_path)),
                "title": page_finder.extract_pdf_title(pdf_path),
                "visual": {str(page): row for page, row in page_finder.safe_visual_features(pdf_path).items()}}
        path.write_text(json.dumps(data), encoding="utf-8")
    return data["texts"], data["title"], {int(page): row for page, row in data["visual"].items()}


def analyse_fast(pdf_path, cache_dir):
    """Page roles only: the page finder run on cached text and visual features (seconds, not minutes).

    Gives the same primary/reference/kept/discarded groups as a full analysis, which also renders
    thumbnails and extracts structure that the page-role scorecard doesn't use.
    """
    from unittest.mock import patch
    from pdf_pipeline import page_finder
    texts, title, visual = cached_extraction(pdf_path, cache_dir)
    with patch.object(page_finder, "extract_text_pages", return_value=texts), \
            patch.object(page_finder, "extract_pdf_title", return_value=title):
        return page_finder.analyze_pages(pdf_path, visual_features=visual or {0: {}})


def compare(previous_path, reports):
    """Per case: facts that changed between a saved scorecard and this run."""
    previous = {row["case_id"]: row for row in json.loads(Path(previous_path).read_text(encoding="utf-8")).get("cases", [])}
    lines = []
    for report in reports:
        before = previous.get(report["case_id"])
        if not before:
            continue
        old = {(fact["fact"], json.dumps(fact["page"])): fact["passed"] for fact in before["facts"]}
        for fact in report["facts"]:
            was = old.get((fact["fact"], json.dumps(fact["page"])))
            if was is not None and was != fact["passed"]:
                lines.append(f"{report['case_id']} {fact['fact']} p{fact['page']}: {'fixed' if fact['passed'] else 'REGRESSED'} ({fact['detail']})")
    return lines


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
    parser.add_argument("--fast", action="store_true",
                        help="Re-run only the page finder on cached page text and visual features (output/evaluations/page_role_cache).")
    parser.add_argument("--cache-dir", default=str(ROOT / "output" / "evaluations" / "page_role_cache"))
    parser.add_argument("--compare", help="A previous page-roles JSON report; list facts that were fixed or regressed.")
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
        report = score(sheet, analyse_fast(pdf, args.cache_dir) if args.fast else analyse(pdf))
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
    if args.compare:
        changes = compare(args.compare, reports)
        print("Changes since the compared report:" if changes else "No fact changed since the compared report.")
        for line in changes:
            print("  " + line)
    print(f"Report: {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
