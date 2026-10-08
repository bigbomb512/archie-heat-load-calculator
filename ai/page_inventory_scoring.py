"""Score pass-1 heat-load information lists against person-confirmed page keys."""

from ai.page_inventory import INFORMATION_KINDS
from ai.page_role_evaluation import validate_answer_sheet

CONFIRMED_STATUSES = {"confirmed_blind", "confirmed_from_draft"}


def _metrics(counts):
    tp, fp, fn = counts["true_positive"], counts["false_positive"], counts["false_negative"]
    return {
        **counts,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
    }


def score_information(sheet, readings, page_count):
    """Compare one confirmed key to AI readings. Missing/unread pages are misses, never skipped."""
    sheet = validate_answer_sheet(sheet, page_count=page_count)
    status = sheet.get("information_status")
    if status not in CONFIRMED_STATUSES:
        return {"case_id": sheet["case_id"], "status": status or "not_started", "scored": False,
                "reason": "Information has not been confirmed."}
    key = sheet["information"]
    counts = {kind: {"true_positive": 0, "false_positive": 0, "false_negative": 0} for kind in INFORMATION_KINDS}
    worst_pages = []
    for page in range(1, page_count + 1):
        truth = set(key[str(page)])
        reading = readings.get(page)
        predicted = set()
        if isinstance(reading, dict):
            for row in reading.get("information", []):
                if isinstance(row, dict) and row.get("kind") in INFORMATION_KINDS:
                    predicted.add(row["kind"])
        missed, added = sorted(truth - predicted), sorted(predicted - truth)
        for kind in INFORMATION_KINDS:
            counts[kind]["true_positive"] += int(kind in truth and kind in predicted)
            counts[kind]["false_negative"] += int(kind in truth and kind not in predicted)
            counts[kind]["false_positive"] += int(kind in predicted and kind not in truth)
        if missed or added:
            worst_pages.append({"page": page, "unread": reading is None, "missed": missed, "added": added})
    by_kind = {kind: _metrics(row) for kind, row in counts.items()}
    total = {name: sum(row[name] for row in counts.values())
             for name in ("true_positive", "false_positive", "false_negative")}
    return {"case_id": sheet["case_id"], "status": status, "scored": True,
            "pages": page_count, "by_kind": by_kind, "overall": _metrics(total), "worst_pages": worst_pages}


def _aggregate(reports):
    counts = {kind: {"true_positive": 0, "false_positive": 0, "false_negative": 0} for kind in INFORMATION_KINDS}
    for report in reports:
        if not report.get("scored"):
            continue
        for kind, row in report["by_kind"].items():
            for name in counts[kind]:
                counts[kind][name] += row[name]
    by_kind = {kind: _metrics(row) for kind, row in counts.items()}
    total = {name: sum(row[name] for row in counts.values())
             for name in ("true_positive", "false_positive", "false_negative")}
    return {"sets": sum(1 for report in reports if report.get("scored")),
            "by_kind": by_kind, "overall": _metrics(total)}


def summarise_information(reports):
    confirmed = [row for row in reports if row.get("scored")]
    blind = [row for row in confirmed if row["status"] == "confirmed_blind"]
    drafted = [row for row in confirmed if row["status"] == "confirmed_from_draft"]
    return {"confirmed_blind_sets": len(blind), "confirmed_from_draft_sets": len(drafted),
            "unconfirmed_sets": len(reports) - len(confirmed), "overall": _aggregate(confirmed),
            "blind": _aggregate(blind), "from_draft": _aggregate(drafted)}


def render_markdown(reports, summary):
    def pct(value):
        return "—" if value is None else f"{value * 100:.1f}%"

    lines = ["## What's on each page — pass 1 information score", "",
             f"{summary['confirmed_blind_sets']} confirmed blind sets (honest measure); "
             f"{summary['confirmed_from_draft_sets']} confirmed from an AI draft (likely overstates accuracy); "
             f"{summary['unconfirmed_sets']} unconfirmed sets not scored.", "",
             "| Group | Sets | Recall | Precision |", "|---|---:|---:|---:|"]
    for name, label in (("overall", "All confirmed"), ("blind", "Confirmed blind"), ("from_draft", "Confirmed from draft")):
        row = summary[name]["overall"]
        lines.append(f"| {label} | {summary[name]['sets']} | {pct(row['recall'])} | {pct(row['precision'])} |")
    lines += ["", "### By information kind", "", "| Kind | Recall | Precision | Missed | Added |", "|---|---:|---:|---:|---:|"]
    for kind, row in summary["overall"]["by_kind"].items():
        lines.append(f"| {kind} | {pct(row['recall'])} | {pct(row['precision'])} | {row['false_negative']} | {row['false_positive']} |")
    for report in reports:
        if not report.get("scored"):
            lines += ["", f"### {report['case_id']} — not scored ({report['status']})", report.get("reason", "")]
            continue
        lines += ["", f"### {report['case_id']} — {report['status']}",
                  f"Recall {pct(report['overall']['recall'])}; precision {pct(report['overall']['precision'])}."]
        for row in report["worst_pages"]:
            unread = " (AI did not read this page; counted as missed)" if row["unread"] else ""
            missed = ", ".join(row["missed"]) or "none"
            added = ", ".join(row["added"]) or "none"
            lines.append(f"- p{row['page']}{unread}: missed {missed}; added {added}.")
    return "\n".join(lines) + "\n"
