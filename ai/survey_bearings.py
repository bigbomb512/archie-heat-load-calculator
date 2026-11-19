"""The house's axis from the boundary bearings printed on its site and survey plans, read from the PDF's text (no AI).

A surveyed boundary bearing (e.g. 137°15'00") is the true (MGA grid) direction of that boundary, and its label is
written along the boundary. So the label's direction on the sheet and its bearing give the sheet's orientation:
sheet-up azimuth = bearing − label direction. A label can be read either way along its line, so one bearing fixes
sheet-up only to within 180°; a north arrow says which way round (backend/autonomous_tasks_service.py).

Checked against hand-worked north on three house sets (2026-11-15): 306.1° (set 15), 43.8° (set 16), 47.25° (set 18),
each within 0.1° of the north found from the same bearings by hand. MGA grid bearings differ from true north by the
grid convergence, under 2° across Sydney; that is within this method's use (refining a drawn arrow's few degrees).
"""
import math
import re

METHOD_ID = "survey-bearing-axis-v1"

# Degrees, minutes and optional seconds. Fonts often map the degree sign to "?", "º" or "˚".
BEARING = re.compile(r"(\d{1,3})\s*[°º˚?]\s*(\d{1,2})\s*['’′]\s*(?:(\d{1,2}(?:\.\d+)?)\s*[\"”″])?")
AGREE_DEG = 1.0          # labels on one sheet that agree to within 1° (mod 180) are one axis
MIN_DISTINCT = 2         # at least two different boundaries must agree, so one stray label can't set north


def labels(pdf_path, pages=None):
    """Every bearing label: page, bearing in degrees, and the sheet direction its text runs in (clockwise from up).

    Reading every character of a 30-sheet set is most of the drawing checks' time, so the pages are read side by
    side in worker processes (pdf_pipeline.page_pool); the rows come back in page order, as one loop gave them."""
    import pdfplumber
    from pdf_pipeline import page_pool
    with pdfplumber.open(str(pdf_path)) as pdf:
        count = len(pdf.pages)
    numbers = [number for number in range(1, count + 1) if pages is None or number in pages]
    per_page = page_pool.map_pdf_pages(_page_labels, str(pdf_path), [{"page": number} for number in numbers])
    return [row for rows in per_page for row in rows]


def _page_labels(page, item):
    number, rows = item["page"], []
    chars = page.chars
    text = "".join(char.get("text", "") for char in chars)
    for match in BEARING.finditer(text):
        degrees, minutes = int(match.group(1)), int(match.group(2))
        seconds = float(match.group(3) or 0)
        if degrees >= 360 or minutes >= 60 or seconds >= 60:
            continue
        first = chars[match.start()]
        a, b = (first.get("matrix") or (1, 0))[:2]
        if abs(a) < 1e-9 and abs(b) < 1e-9:
            continue
        rows.append({"page": number, "bearing_deg": round(degrees + minutes / 60 + seconds / 3600, 4),
                     "text_direction_deg": round(math.degrees(math.atan2(a, b)) % 360, 3),
                     "text": match.group(0).strip(), "x": round(first.get("x0", 0), 1), "y": round(first.get("top", 0), 1)})
    return rows


def _axis_difference(a, b):
    return abs((a - b + 90) % 180 - 90)


def axis(rows):
    """The sheet-up axis (degrees from true north, 0-180; add 180 for the other way round) that most distinct bearing
    labels agree on, or None when fewer than two different boundaries agree."""
    distinct = {}
    for row in rows:
        value = (row["bearing_deg"] - row["text_direction_deg"]) % 180
        distinct.setdefault((round(row["bearing_deg"], 3), round(row["text_direction_deg"], 1)), value)
    values = list(distinct.values())
    best = []
    for value in values:
        cluster = [other for other in values if _axis_difference(value, other) <= AGREE_DEG]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < MIN_DISTINCT:
        return None
    # Mean of axes: double the angles so 179.9° and 0.1° average to 0°.
    x = sum(math.cos(math.radians(2 * value)) for value in best)
    y = sum(math.sin(math.radians(2 * value)) for value in best)
    mean = (math.degrees(math.atan2(y, x)) / 2) % 180
    return {"axis_deg": round(mean, 3), "labels_agreeing": len(best), "labels_distinct": len(values),
            "spread_deg": round(max(_axis_difference(mean, value) for value in best), 3)}


def refine(arrow_deg, axis_deg, tolerance_deg):
    """The survey axis direction nearest an arrow's sheet-up azimuth, and how far apart they were; None when the arrow
    is more than `tolerance_deg` from the axis either way round."""
    candidates = [axis_deg % 360, (axis_deg + 180) % 360]
    nearest = min(candidates, key=lambda value: abs((value - arrow_deg + 180) % 360 - 180))
    difference = abs((nearest - arrow_deg + 180) % 360 - 180)
    return (round(nearest, 3), round(difference, 2)) if difference <= tolerance_deg else (None, round(difference, 2))
