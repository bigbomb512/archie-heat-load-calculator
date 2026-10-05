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
ACCURACY_PATH = ROOT / "evaluations" / "autonomous" / "accuracy.json"


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


def _contains_stand_in(value):
    if isinstance(value, dict):
        return value.get("stand_in") is True or any(_contains_stand_in(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_stand_in(item) for item in value)
    return False


def _answer_key_paths():
    result = {}
    for path in sorted((ROOT / "evaluations" / "autonomous").glob("case*.json")):
        key = _read(path)
        case_id = key.get("case_id", path.stem)
        if case_id in result:
            raise ValueError(f"Duplicate canonical answer-key case ID: {case_id}.")
        result[case_id] = path.resolve()
    return result


def _recordable_accuracy(reports, summary, bar):
    """Return the trusted accuracy registry, or raise if this isn't a full real evaluation."""
    expected = {}
    for path in sorted((ROOT / "evaluations" / "autonomous").glob("case*.json")):
        key = _read(path)
        for task in key.get("tasks", {}):
            expected.setdefault(task, set()).add(key.get("case_id", path.stem))

    scored_cases = {}
    for report in reports:
        for task, items in report.get("tasks", {}).items():
            if items:
                scored_cases.setdefault(task, set()).add(report.get("case_id", ""))

    rows = {}
    for task, case_ids in expected.items():
        seen = scored_cases.get(task, set())
        missing = sorted(case_ids - seen)
        summary_row = summary.get(task, {})
        scored = int(summary_row.get("scored", 0))
        if missing:
            raise ValueError(f"Cannot record {task}: answer-key case(s) not scored: {', '.join(missing)}.")
        if scored < 10:
            raise ValueError(f"Cannot record {task}: requires at least 10 scored items; found {scored}.")
        correct = int(summary_row.get("correct", 0))
        accuracy = correct / scored
        rows[task] = {"accuracy": accuracy, "scored": scored, "correct": correct,
                      "wrong": int(summary_row.get("wrong", 0)),
                      "missing": int(summary_row.get("missing", 0)),
                      "auto_apply": accuracy >= bar, "case_ids": sorted(case_ids)}
    return {"schema_version": 1, "recorded_at": datetime.now().astimezone().isoformat(),
            "bar": bar, "tasks": rows}


def main():
    parser = argparse.ArgumentParser(description="Score autonomous AI task results against answer keys.")
    parser.add_argument("pairs", nargs="+", help="answer_key.json=determinations.json")
    parser.add_argument("--bar", type=float, default=AUTO_APPLY_BAR)
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluations"))
    parser.add_argument("--record", action="store_true",
                        help="record auto-apply accuracy only after full answer-key coverage, real determinations, and >=10 items per task")
    args = parser.parse_args()
    reports = []
    case_ids = set()
    canonical_keys = _answer_key_paths() if args.record else {}
    for pair in args.pairs:
        key_path, _, result_path = pair.partition("=")
        key = _read(key_path)
        if not key_path or not result_path:
            parser.error("Each input must be answer_key.json=determinations.json")
        if key.get("case_id") in case_ids:
            parser.error(f"Duplicate answer-key case: {key.get('case_id')}")
        case_ids.add(key.get("case_id"))
        determinations = _read(result_path)
        if args.record and _contains_stand_in(determinations):
            parser.error(f"Refusing --record: determinations for {key.get('case_id')} are marked stand_in.")
        if args.record and Path(key_path).resolve() != canonical_keys.get(key.get("case_id")):
            parser.error(f"Refusing --record: {key.get('case_id')} must use its checked-in canonical answer key.")
        private_path = ROOT / key["private_facts_file"] if key.get("private_facts_file") else None
        private = _read(private_path) if private_path and private_path.exists() else None
        reports.append(score_case(key, determinations, private))
    summary = summarise(reports, args.bar)
    if args.record:
        try:
            accuracy = _recordable_accuracy(reports, summary, args.bar)
        except ValueError as error:
            parser.error(str(error))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"autonomous-tasks-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps({"bar": args.bar, "summary": summary, "cases": reports}, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(reports, summary, args.bar), encoding="utf-8")
    for task, row in summary.items():
        print(f"{task}: {row['accuracy']:.0%} ({row['correct']}/{row['scored']}) · auto-apply {'yes' if row['auto_apply'] else 'no'}")
    print(f"Report: {base.with_suffix('.md')}")
    if args.record:
        ACCURACY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACCURACY_PATH.write_text(json.dumps(accuracy, indent=2) + "\n", encoding="utf-8")
        print(f"Recorded auto-apply accuracy: {ACCURACY_PATH}")


if __name__ == "__main__":
    main()
