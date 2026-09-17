"""Presentation-only exports of existing benchmark comparisons.

No tolerance evaluation, acceptance decision, or load calculation occurs here.
Missing values remain missing; independent room/zone peaks are never summed.
"""

import csv
from html import escape
from io import StringIO
import json
import math


COLUMNS = ("Scope", "Entity", "Result", "Reference kW", "Archie kW",
           "Difference kW", "Difference %", "Status", "Reference source", "Archie source")
TOTALS = ("sensible_kw", "latent_kw", "total_kw", "design_total_kw")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value):
    if value is None:
        return "Not provided"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def result_rows(report):
    """Flatten reported totals/components, preserving missing-side entities."""
    rows = []
    for scope in ("rooms", "zones", "floors", "project"):
        entities = report.get(scope) or []
        if isinstance(entities, dict):
            entities = [entities]
        for entity in entities:
            identifier = entity.get("entity_id", "Not provided")
            reference, actual = entity.get("reference") or {}, entity.get("archie") or {}
            for metric in TOTALS:
                expected, observed = reference.get(metric), actual.get(metric)
                if expected is None and observed is None:
                    continue
                valid = _number(expected) and _number(observed)
                difference = round(observed - expected, 4) if valid else None
                percent = round((observed - expected) / expected * 100, 3) if valid and expected != 0 else None
                status = "compared" if valid else "archie_only" if expected is None else "camel_only" if observed is None else "not_comparable"
                rows.append((scope, identifier, metric, expected, observed, difference, percent, status, "", ""))
            components = entity.get("components") or []
            for component in components:
                rows.append((scope, identifier, component["name"],
                    component.get("reference_kw", component.get("reference")),
                    component.get("archie_kw", component.get("archie")),
                    component.get("difference_kw"), component.get("difference_percent"),
                    component.get("status", "not_comparable"), component.get("reference_source", ""),
                    component.get("archie_source", "")))
            # The comparator retains unpaired entities verbatim, without a
            # components comparison list. Expose those values without inventing
            # comparison results.
            if not components and entity.get("status") in {"camel_only", "archie_only"}:
                for side, payload in (("reference", reference), ("archie", actual)):
                    for name, value in (payload.get("components") or {}).items():
                        amount = value.get("total_kw") if isinstance(value, dict) else value
                        source = value.get("source", "") if isinstance(value, dict) else ""
                        rows.append((scope, identifier, name, amount if side == "reference" else None,
                            amount if side == "archie" else None, None, None, entity["status"],
                            source if side == "reference" else "", source if side == "archie" else ""))
            if not components and not any(key in reference or key in actual for key in TOTALS) and not reference.get("components") and not actual.get("components"):
                rows.append((scope, identifier, "No numerical results", None, None, None, None, entity.get("status", "not_comparable"), "", ""))
    return rows


def report_sections(report):
    peak = report.get("peak_comparison") or {}
    reference = peak.get("reference", report.get("reference_peak")) or {}
    actual = peak.get("archie", report.get("archie_peak")) or {}
    missing = ["Missing reference material: " + _text(v) for v in report.get("missing_reference_material", [])]
    missing += ["Unresolved input: " + _text(v) for v in report.get("unresolved_inputs", [])]
    missing += ["Unmapped input family: " + _text(v) for v in report.get("unmapped_input_families", [])]
    summary = report.get("summary") or {}
    return [
        ("Comparison status", [
            "Status: " + _text(report.get("status")),
            "Final parity allowed: " + _text(report.get("final_parity_allowed", False)),
            ("Accepted for this exact room/zone peak benchmark only; this does not validate other projects, floor/project totals, or the engine generally."
             if report.get("status") == "validated" and report.get("validation", {}).get("status") == "accepted"
             else "This is a comparison report, not an engineering acceptance or validated cooling result."),
            "Reason: " + _text(report.get("reason")),
        ]),
        ("Benchmark acceptance", [_text(report.get("validation") or {"status": "not_accepted"})]),
        ("Reference readiness", missing or ["No reference-readiness blockers reported. This does not establish benchmark acceptance."]),
        ("Comparison summary", [f"{key.replace('_', ' ')}: {_text(value)}" for key, value in summary.items()] or ["No comparison summary provided."]),
        ("Peak timing", [
            "Timing status: " + _text(peak.get("status")),
            f"Reference: month {_text(reference.get('month'))}, hour {_text(reference.get('hour'))}",
            f"Archie: month {_text(actual.get('month'))}, hour {_text(actual.get('hour'))}",
            "Room and zone peaks may occur at different hours; this report does not sum them into a project peak.",
        ]),
        ("Tolerance policy", [_text(report.get("tolerance_policy") or {}),
            "This export displays the supplied policy. Acceptance, when recorded, is evaluated separately against unrounded supplied results."]),
        ("Evidence and reconciliation", [
            "Authorisation declarations: " + _text(report.get("authorisation") or {}),
            "Source file references: " + _text(report.get("source_files") or {}),
        ] + [_text(item) for item in report.get("input_reconciliation", [])]),
        ("Reading the results", [
            "Difference = Archie minus reference. Positive means Archie is higher.",
            "Differences use the existing display precision: 4 decimal places in kW and 3 in percent.",
            "Not provided is not zero. Percent difference is undefined when the reference is zero.",
            "Floors and project comparisons appear only when present in the supplied comparison report; they are not synthesized.",
        ]),
    ]


def _md(value):
    return escape(_text(value), quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("`", "\\`").replace("[", "\\[").replace("]", "\\]").replace("*", "\\*").replace("_", "\\_")


def render_markdown(report):
    lines = [f"# Cooling benchmark report: {_md(report.get('case_id'))}"]
    for title, items in report_sections(report):
        lines.extend(["", f"## {title}", ""] + ["- " + _md(item) for item in items])
    lines.extend(["", "## Numerical results", "", "| " + " | ".join(COLUMNS) + " |", "| " + " | ".join(["---"] * len(COLUMNS)) + " |"])
    rows = result_rows(report)
    lines.extend("| " + " | ".join(_md(v) for v in row) + " |" for row in rows)
    if not rows:
        lines.extend(["", "No numerical comparison rows are available."])
    return "\n".join(lines) + "\n"


def render_html(report):
    title = "Cooling benchmark report: " + _text(report.get("case_id"))
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>' + escape(title) + '</title>',
        '<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#202020}h1{font-size:1.7rem}h2{font-size:1.2rem;margin-top:2rem}li{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:.875rem}th,td{text-align:left;vertical-align:top;padding:.5rem;border:1px solid #bbb;overflow-wrap:anywhere}th{background:#eee}.table-wrap{overflow-x:auto}@media print{body{max-width:none;margin:0}thead{display:table-header-group}tr{break-inside:avoid}}</style></head><body><main>',
        '<h1>' + escape(title) + '</h1>']
    for heading, items in report_sections(report):
        parts.append('<section><h2>' + escape(heading) + '</h2><ul>')
        parts.extend('<li>' + escape(_text(item)) + '</li>' for item in items)
        parts.append('</ul></section>')
    parts.append('<section><h2>Numerical results</h2><div class="table-wrap"><table><caption>Reported totals and component differences, in kW unless indicated</caption><thead><tr>')
    parts.extend('<th scope="col">' + escape(column) + '</th>' for column in COLUMNS)
    parts.append('</tr></thead><tbody>')
    rows = result_rows(report)
    for row in rows:
        parts.append('<tr>' + ''.join('<td>' + escape(_text(v)) + '</td>' for v in row) + '</tr>')
    if not rows:
        parts.append(f'<tr><td colspan="{len(COLUMNS)}">No numerical comparison rows are available.</td></tr>')
    parts.append('</tbody></table></div></section></main></body></html>')
    return '\n'.join(parts) + '\n'


def render_csv(report):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    for row in result_rows(report):
        # Keep negative numeric results numeric, but neutralize formulas in
        # externally supplied identifiers, descriptions, and source strings.
        cells = []
        for value in row:
            text = "" if value is None else _text(value)
            if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@")):
                text = "'" + text
            cells.append(text)
        writer.writerow(cells)
    return output.getvalue()
