"""Score the autonomous AI tasks (Card P) against answer keys.

An AI run produces one "determinations" document per project: the values the
AI applied for rooms, site, north, wall boundaries, shopfront glazing and roof
exposure. Each applied value is scored against the case's answer key as
correct, wrong or missing. Values that came from the "most likely for the
building type" fallback are scored like any other applied value and also
counted separately, so the report shows how much of the result was inferred
from the drawings and how much was assumed.

A task may apply its values automatically only when its accuracy on the
answer-key set reaches the auto-apply bar (user decision 2026-10-04: 85 %).
"""

import math

AUTO_APPLY_BAR = 0.85
TASKS = ("P0_rooms", "P1_site", "P2_north", "P3_boundaries", "P4_openings", "P5_roof")
PENDING = "pending_user"


def _norm(text):
    return " ".join(str(text or "").casefold().split())


def _close(value, expected, fraction):
    return isinstance(value, (int, float)) and math.isfinite(value) and abs(value - expected) <= abs(expected) * fraction


def _outcome(item, status, detail, source=""):
    return {"item": item, "status": status, "detail": detail, "fallback": source == "fallback"}


def score_rooms(key, applied):
    tolerance = key.get("area_tolerance_fraction", 0.05)
    applied = [row for row in applied or [] if isinstance(row, dict)]
    by_label = {_norm(row.get("label")): row for row in applied}
    results = []
    for room in key.get("rooms", []):
        row = by_label.pop(_norm(room["label"]), None)
        if room.get("status") == PENDING:
            continue  # answer not settled yet; neither scored nor counted as a false room
        if row is None:
            results.append(_outcome(room["label"], "missing", "room not found"))
        elif _close(row.get("area_m2"), room["area_m2"], tolerance):
            results.append(_outcome(room["label"], "correct", f"{row['area_m2']} m² vs {room['area_m2']} m²", row.get("source", "")))
        else:
            results.append(_outcome(room["label"], "wrong", f"{row.get('area_m2')} m² vs {room['area_m2']} m² (±{tolerance:.0%})", row.get("source", "")))
    for label, row in by_label.items():
        results.append(_outcome(row.get("label", label), "wrong", "room not in the answer key (false room)", row.get("source", "")))
    return results


def score_site(key, applied):
    if not key:
        return []
    applied = applied or {}
    text = _norm(applied.get("site_text"))
    if not text:
        return [_outcome("site", "missing", "no site determined")]
    if any(_norm(bad) in text for bad in key.get("must_not_choose", [])):
        return [_outcome("site", "wrong", "chose a consultant's address", applied.get("source", ""))]
    any_of = key.get("site_must_contain_any", [])
    if all(_norm(good) in text for good in key.get("site_must_contain", [])) and (not any_of or any(_norm(good) in text for good in any_of)):
        return [_outcome("site", "correct", applied.get("site_text", ""), applied.get("source", ""))]
    return [_outcome("site", "wrong", f"site text {applied.get('site_text')!r} does not name the site", applied.get("source", ""))]


def score_north(key, applied):
    tolerance = key.get("tolerance_deg", 5)
    by_page = {row.get("page"): row for row in applied or [] if isinstance(row, dict)}
    results = []
    for page in key.get("pages", []):
        row = by_page.get(page["page"])
        value = (row or {}).get("plan_up_azimuth_deg")
        if row is None or value is None:
            results.append(_outcome(f"page {page['page']}", "missing", "no north applied"))
            continue
        difference = abs((value - page["plan_up_azimuth_deg"] + 180) % 360 - 180)
        status = "correct" if difference <= tolerance else "wrong"
        results.append(_outcome(f"page {page['page']}", status, f"{value}° vs {page['plan_up_azimuth_deg']}° (off by {difference:.1f}°)", row.get("source", "")))
    return results


def score_boundaries(key, applied):
    tolerance = key.get("length_tolerance_fraction", 0.03)
    applied = [row for row in applied or [] if isinstance(row, dict)]
    results = []
    for edge in key.get("edges", []):
        if edge.get("boundary") == PENDING:
            continue
        match = next((row for row in applied if _norm(row.get("room")) == _norm(edge["room"])
                      and _close(row.get("edge_length_m"), edge["edge_length_m"], tolerance)), None)
        name = f"{edge['room']} {edge['edge_length_m']} m edge"
        if match is None or match.get("boundary") in (None, "", "unknown"):
            results.append(_outcome(name, "missing", "edge not classified"))
        else:
            status = "correct" if match["boundary"] == edge["boundary"] else "wrong"
            results.append(_outcome(name, status, f"{match['boundary']} vs {edge['boundary']}", match.get("source", "")))
    return results


def score_openings(key, applied):
    width_tolerance = key.get("width_tolerance_fraction", 0.02)
    height_tolerance = key.get("height_tolerance_mm", 50)
    applied = [row for row in applied or [] if isinstance(row, dict)]
    results = []
    for elevation in key.get("elevations", []):
        found = next((row for row in applied if row.get("page") == elevation["page"]), None)
        label = f"p. {elevation['page']}"
        if found is None:
            results.append(_outcome(f"{label} elevation", "missing", "elevation not read"))
            continue
        panels = [row for row in found.get("glazed_panels", []) if isinstance(row, dict)]
        for expected in elevation.get("glazed_panels", []):
            match = next((row for row in panels if _close(row.get("width_mm"), expected["width_mm"], width_tolerance)), None)
            name = f"{label} glazing {expected['width_mm']} mm"
            if match is None:
                results.append(_outcome(name, "missing", "glazed panel not found"))
                continue
            panels.remove(match)
            heights_ok = all(isinstance(match.get(field), (int, float)) and abs(match[field] - expected[field]) <= height_tolerance
                             for field in ("sill_mm", "head_mm"))
            results.append(_outcome(name, "correct" if heights_ok else "wrong",
                                    f"sill {match.get('sill_mm')} / head {match.get('head_mm')} vs {expected['sill_mm']} / {expected['head_mm']}",
                                    match.get("source", "")))
        for extra in panels:
            reason = next((row["why"] for row in elevation.get("not_glazing", [])
                           if _close(extra.get("width_mm"), row["width_mm"], width_tolerance)), "not in the answer key")
            results.append(_outcome(f"{label} extra {extra.get('width_mm')} mm", "wrong", f"counted as glazing: {reason}", extra.get("source", "")))
    return results


def score_roof(key, applied):
    by_room = {_norm(row.get("room")): row for row in applied or [] if isinstance(row, dict)}
    results = []
    for room in key.get("rooms", []):
        row = by_room.get(_norm(room["room"]))
        if row is None or row.get("roof") in (None, "", "unknown"):
            results.append(_outcome(room["room"], "missing", "roof exposure not applied"))
        else:
            status = "correct" if row["roof"] == room["roof"] else "wrong"
            results.append(_outcome(room["room"], status, f"{row['roof']} vs {room['roof']}", row.get("source", "")))
    return results


SCORERS = {"P0_rooms": score_rooms, "P1_site": score_site, "P2_north": score_north,
           "P3_boundaries": score_boundaries, "P4_openings": score_openings, "P5_roof": score_roof}


def score_case(answer_key, determinations, private_facts=None):
    """Score one project's applied values against its answer key."""
    tasks = dict(answer_key.get("tasks", {}))
    if private_facts and private_facts.get("P1_site"):
        tasks["P1_site"] = private_facts["P1_site"]
    report = {"case_id": answer_key.get("case_id", ""), "tasks": {}}
    for task in TASKS:
        if task not in tasks:
            continue
        items = SCORERS[task](tasks[task], (determinations or {}).get(task))
        report["tasks"][task] = items
    return report


def summarise(case_reports, bar=AUTO_APPLY_BAR):
    """Per-task accuracy across cases, and whether each task may auto-apply."""
    summary = {}
    for task in TASKS:
        items = [item for report in case_reports for item in report["tasks"].get(task, [])]
        if not items:
            continue
        counts = {status: sum(item["status"] == status for item in items) for status in ("correct", "wrong", "missing")}
        accuracy = counts["correct"] / len(items)
        summary[task] = {**counts, "scored": len(items), "accuracy": round(accuracy, 3),
                         "confident_wrong_rate": round(counts["wrong"] / len(items), 3),
                         "from_fallback": sum(item["fallback"] for item in items),
                         "auto_apply": accuracy >= bar,
                         "small_sample": len(items) < 20}
    return summary
