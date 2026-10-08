#!/usr/bin/env python3
"""Run pass 2 of the PDF review (read values from the pages pass 1 flagged) on training sets, and report.

There is no answer key for values yet, so the report gives what can be checked without one:
- how many items and findings each set gives, and how many pages disagree (conflicting findings);
- how often a power rating is printed;
- the text check: for pages with a text layer, is each item's name actually printed on the page it cites
  (a reading not found in the text is a likely misread or invention).
Sets are marked development or held back: build and adjust on development sets only, then check held-back ones.

Pass 1 is run first for any set not yet read (same cache as tools/evaluate_page_inventory.py). Everything is
cached under the ignored output/evaluations/, so a re-run only calls the AI for what is new. Uses the Codex CLI
signed in with ChatGPT.

  python3 tools/evaluate_page_extraction.py --dev caseP04 --dev caseP06 --held caseP10
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

from ai.page_extraction import EXTRACTORS, merge  # noqa: E402
from backend import page_extraction_service as pass2, page_inventory_service as pass1  # noqa: E402

OUTPUT = ROOT / "output" / "evaluations"


def pass1_readings(case_id, pdf, reader):
    folder = OUTPUT / "page_inventory" / case_id
    images = pass1.render_pages(pdf, folder / "pages")
    readings, failures, calls = pass1.read_pages(images, folder / "replies", reader)
    if failures:
        raise RuntimeError(f"{case_id}: pass 1 couldn't read pages {sorted(failures)}: {failures[min(failures)]}")
    return readings, calls


def run_case(case_id, pdf, kind, reader, role):
    started = time.monotonic()
    readings, pass1_calls = pass1_readings(case_id, pdf, reader)
    extractor = EXTRACTORS[kind]
    pages = sorted(page for page, row in readings.items() if any(item["kind"] == kind for item in row["information"]))
    folder = OUTPUT / "page_extraction" / case_id
    images = pass2.page_images(pdf, folder / "pages", pages)
    texts = pass2.page_texts(pdf, folder / "text", pages)

    def progress(done, total):
        print(f"\r  {case_id}: {done}/{total} sections", end="", flush=True)

    sections, failures, calls = pass2.read_sections(extractor, images, folder / "replies" / kind, reader, on_done=progress)
    print()
    findings = merge(extractor, sections, texts, {page: row["page_type"] for page, row in readings.items()})
    items = sum(len(found) for _page, _section, found in sections)
    checked = [row["text_match"] for row in findings if row["text_match"] is not None]
    return {"case_id": case_id, "role": role, "kind": kind, "pages": pages, "sections": len(images),
            "pass1_calls": pass1_calls, "calls": calls, "seconds": round(time.monotonic() - started, 1),
            "failures": failures, "items": items, "findings": len(findings),
            "conflicting": sum(1 for row in findings if row["conflicts"]),
            "power_printed": sum(1 for row in findings if row["value"].get("rated_power")),
            "text_checked": len(checked), "text_found": sum(1 for match in checked if match),
            "rows": findings}


def render_markdown(results):
    lines = ["# Pass 2: values read from the pages pass 1 flagged", "",
             "| Set | Role | Pages | Sections | AI calls | Items | Findings | Conflicting | Power printed | Name found in page text |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in results:
        text = f"{row['text_found']}/{row['text_checked']}" if row["text_checked"] else "no text layer"
        lines.append(f"| {row['case_id']} | {row['role']} | {len(row['pages'])} | {row['sections']} | {row['calls']} | "
                     f"{row['items']} | {row['findings']} | {row['conflicting']} | {row['power_printed']} | {text} |")
    for row in results:
        lines += ["", f"## {row['case_id']} ({row['role']})", "",
                  "| Item | Qty | Size | Model | Power | Under hood | Pages | Text | Conflicts |", "|---|---|---|---|---|---|---|---|---|"]
        for finding in row["rows"]:
            value = finding["value"]
            conflicts = "; ".join(f"{field}: " + " / ".join(f"{option['value']} (p{','.join(map(str, option['pages']))})"
                                                          for option in options) for field, options in finding["conflicts"].items())
            lines.append(f"| {value.get('code') + ' ' if value.get('code') else ''}{value['name']} | {value.get('quantity') if value.get('quantity') is not None else ''} | "
                         f"{value.get('size', '')} | {value.get('model', '')} | {value.get('rated_power', '')} | "
                         f"{'' if value.get('under_hood') is None else value['under_hood']} | {', '.join(map(str, finding['pages']))} | "
                         f"{'' if finding['text_match'] is None else ('yes' if finding['text_match'] else 'NOT FOUND')} | {conflicts} |")
        for key, reason in row["failures"].items():
            lines.append(f"\n- Section {key} not read: {reason}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dev", action="append", default=[], help="Development set (repeatable).")
    parser.add_argument("--held", action="append", default=[], help="Held-back set: check only, don't adjust for it.")
    parser.add_argument("--kind", default="equipment_appliances", choices=sorted(EXTRACTORS))
    args = parser.parse_args(argv)
    sources = json.loads((OUTPUT / "page_role_sources.json").read_text(encoding="utf-8"))
    reader = pass1.CodexCliPageReader()
    reader.check_signed_in()
    results = []
    for case_id, role in [(case, "development") for case in args.dev] + [(case, "held back") for case in args.held]:
        result = run_case(case_id, sources[case_id], args.kind, reader, role)
        results.append(result)
        print(f"{case_id} ({role}): {result['findings']} findings from {len(result['pages'])} pages, {result['conflicting']} conflicting, "
              f"power printed for {result['power_printed']}, name in page text {result['text_found']}/{result['text_checked']}, "
              f"{result['pass1_calls'] + result['calls']} AI calls, {result['seconds']} s, {len(result['failures'])} sections not read", flush=True)
    base = OUTPUT / f"page-extraction-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps({"cases": results}, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(results), encoding="utf-8")
    print(f"Report: {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
