#!/usr/bin/env python3

import argparse
import json
from datetime import datetime
from pathlib import Path

from ai.evaluation import evaluate_packet, render_markdown


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Create a report-only Vision Lab packet scorecard.")
    parser.add_argument("case", help="An anonymised file from evaluations/cases/.")
    parser.add_argument("packet", help="A local review or reasoning-packet folder.")
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluations"))
    args = parser.parse_args()

    report = evaluate_packet(args.case, args.packet)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = output_dir / f"{report['case_id']}-{stamp}"
    json_path = base.with_suffix(".json")
    markdown_path = base.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    totals = report["scorecard"]["totals"]
    accuracy = "-" if totals["accuracy_percent"] is None else f"{totals['accuracy_percent']}%"
    print(f"Evaluation: {report['case_id']}")
    print(f"Passed: {totals['passed']}  Failed: {totals['failed']}  Not evaluated: {totals['not_evaluated']}  Accuracy: {accuracy}")
    print(f"JSON: {json_path}")
    print(f"Report: {markdown_path}")


if __name__ == "__main__":
    main()
