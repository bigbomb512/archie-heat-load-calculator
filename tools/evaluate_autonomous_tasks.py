#!/usr/bin/env python3
"""Score autonomous AI task results (Card P) against answer keys.

Usage:
    PYTHONPATH=. python3 tools/evaluate_autonomous_tasks.py \
        evaluations/autonomous/caseA.json=/path/to/caseA_determinations.json [more pairs]

Each determinations file holds the values one AI run applied for that case.
Reports go to the ignored output/evaluations/ folder.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from ai.autonomous_task_scoring import AUTO_APPLY_BAR, score_case, summarise


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_markdown(case_reports, summary, bar):
    lines = ["# Autonomous AI task scorecard", "", f"Auto-apply bar: {bar:.0%} correct per task.", "",
             "| Task | Correct | Wrong | Missing | Accuracy | From fallback | Auto-apply |", "|---|---|---|---|---|---|---|"]
    for task, row in summary.items():
        note = " (small sample)" if row["small_sample"] else ""
        lines.append(f"| {task} | {row['correct']} | {row['wrong']} | {row['missing']} | {row['accuracy']:.0%}{note} | "
                     f"{row['from_fallback']} | {'yes' if row['auto_apply'] else 'no'} |")
    for report in case_reports:
        lines += ["", f"## {report['case_id']}"]
        for task, items in report["tasks"].items():
            for item in items:
                lines.append(f"- {task} · {item['item']}: **{item['status']}**{' (fallback)' if item['fallback'] else ''} — {item['detail']}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Score autonomous AI task results against answer keys.")
    parser.add_argument("pairs", nargs="+", help="answer_key.json=determinations.json")
    parser.add_argument("--bar", type=float, default=AUTO_APPLY_BAR)
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluations"))
    args = parser.parse_args()
    reports = []
    for pair in args.pairs:
        key_path, _, result_path = pair.partition("=")
        key = _read(key_path)
        private_path = ROOT / key["private_facts_file"] if key.get("private_facts_file") else None
        private = _read(private_path) if private_path and private_path.exists() else None
        reports.append(score_case(key, _read(result_path), private))
    summary = summarise(reports, args.bar)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"autonomous-tasks-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps({"bar": args.bar, "summary": summary, "cases": reports}, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(reports, summary, args.bar), encoding="utf-8")
    for task, row in summary.items():
        print(f"{task}: {row['accuracy']:.0%} ({row['correct']}/{row['scored']}) · auto-apply {'yes' if row['auto_apply'] else 'no'}")
    print(f"Report: {base.with_suffix('.md')}")


if __name__ == "__main__":
    main()
