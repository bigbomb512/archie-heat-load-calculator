#!/usr/bin/env python3
"""Export an existing comparison JSON as Markdown, HTML, and CSV.

Example (from the repository root):
    PYTHONPATH=. python3 tools/export_benchmark_report.py \
        --report /private/case/reports/parity_report.json \
        --output-dir /private/case/reports
"""

import argparse
import json
from pathlib import Path

from ai.benchmark_reporting import render_csv, render_html, render_markdown


def export_report(report_path, output_dir):
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not all(key in report for key in ("case_id", "status", "rooms", "zones")):
        raise ValueError("Expected an existing benchmark comparison report with case_id, status, rooms and zones.")
    output_dir = Path(output_dir)
    rendered = {"md": render_markdown(report), "html": render_html(report), "csv": render_csv(report)}
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for extension, content in rendered.items():
        path = output_dir / f"parity_report.{extension}"
        if path.resolve() == Path(report_path).resolve():
            raise ValueError("An export must not overwrite the input report.")
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="Existing benchmark comparison JSON")
    parser.add_argument("--output-dir", required=True, help="Destination for presentation exports")
    args = parser.parse_args()
    try:
        for path in export_report(args.report, args.output_dir):
            print(path)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Benchmark export failed: {error}\n")


if __name__ == "__main__":
    main()
