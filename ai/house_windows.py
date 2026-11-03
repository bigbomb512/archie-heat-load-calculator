"""House windows from the drawings' text layer: window schedules, window tags on plans, and their walls.

No AI. All positions are PDF points of the page they come from. A schedule column only means what its printed header
says; a window is placed only from a schedule row and a tag on the plan, and anything that can't be matched is listed
with the reason, never guessed.

Supported schedule layouts (house sets 15–20, October 2026):
- row table with a header row, e.g. "Level | Mark | Width | Height | Head Height | Description | Count";
- column table: a "WINDOW NUMBER" row with one column per window, then "UNIT DIMENSIONS" (width × height, sometimes
  wrapped over two lines) and "W/D NOMINAL SILL HEIGHTS" rows.
A schedule drawn as linework (no text) yields nothing.
"""
import math
import re
from collections import defaultdict

# Window marks: W4, W12A, WG_01, WF-03, WE_10, HLW_01. Not a size such as W1200 (four digits).
MARK = re.compile(r"^(?:HLW|W[A-Z]?)[_-]?\d{1,3}[A-Z]?$", re.I)
DOOR_MARK = re.compile(r"^D[A-Z]?[_-]?\d{1,3}[A-Z]?$", re.I)
SKYLIGHT_MARK = re.compile(r"^(?:SL|SK|S)[_-]?\d{1,3}[A-Z]?$", re.I)
NUMBER = re.compile(r"^\d{1,2},\d{3}$|^\d{1,5}$")
# Assumed head height when a schedule gives a window's height but neither its sill nor its head (typical door head).
ASSUMED_HEAD_MM = 2100
ASSUMED_HEAD_NOTE = "Head height not printed: assumed 2,100 mm (typical door head height); sill = head − height."


def _text(word):
    return str(word.get("text", "")).strip()


def _x(word):
    return (float(word["x0"]) + float(word["x1"])) / 2


def _y(word):
    return (float(word["top"]) + float(word["bottom"])) / 2


def _number(text):
    text = str(text).strip()
    return int(text.replace(",", "")) if NUMBER.fullmatch(text) else None


def _rows(words, tolerance=4.5):
    """Words grouped into printed lines (by vertical centre), each sorted left to right."""
    rows = []
    for word in sorted(words, key=lambda w: (_y(w), float(w["x0"]))):
        row = next((row for row in rows if abs(row["y"] - _y(word)) <= tolerance), None)
        if row is None:
            rows.append({"y": _y(word), "words": [word]})
        else:
            row["words"].append(word)
    return [sorted(row["words"], key=lambda w: float(w["x0"])) for row in rows]


def _kind(mark, description=""):
    if SKYLIGHT_MARK.fullmatch(mark) or re.search(r"\bsky\s*light|skylight\b", description, re.I):
        return "skylight"
    if DOOR_MARK.fullmatch(mark):
        return "door"
    return "window"


# --- Row tables -----------------------------------------------------------------------------------------------------

_ROW_FIELDS = (("head_mm", r"^head(?:\s+height)?$"), ("sill_mm", r"^sill(?:\s+height)?$"), ("width_mm", r"^width$"),
               ("height_mm", r"^height$"), ("mark", r"^(?:mark|id|no\.?|number|ref)$"), ("level", r"^(?:level|floor)$"),
               ("type", r"^(?:description|type)$"), ("quantity", r"^(?:count|qty|quantity|no\. off)$"))


def _row_header(row):
    """[(field, x_left, x_right)] for a header row of a row table, or None. Two-word headers (Head Height) are joined;
    a second table on the same line (a door schedule beside the window schedule) starts where a field repeats."""
    words = [(_text(w), w) for w in row]
    columns, index = [], 0
    while index < len(words):
        text, word = words[index]
        joined = f"{text} {words[index + 1][0]}" if index + 1 < len(words) else ""
        field = next((name for name, pattern in _ROW_FIELDS if joined and re.match(pattern, joined, re.I)), None)
        width = 2 if field else 1
        field = field or next((name for name, pattern in _ROW_FIELDS if re.match(pattern, text, re.I)), None)
        if field:
            last = words[index + width - 1][1]
            columns.append((field, float(word["x0"]), float(last["x1"])))
        index += width
    names = [field for field, *_ in columns]
    if not {"mark", "width_mm", "height_mm"} <= set(names):
        return None
    tables, current = [], []
    for column in columns:
        if column[0] in {field for field, *_ in current}:
            tables.append(current)
            current = []
        current.append(column)
    tables.append(current)
    return tables


def _row_table(rows, start, header, page):
    """Records under one row-table header, assigning each word to the header column whose span it falls under."""
    left, right = header[0][1] - 40, header[-1][2] + 60
    def column_of(word):
        centre = _x(word)
        best = min(header, key=lambda column: 0 if column[1] - 6 <= centre <= column[2] + 6
                   else min(abs(centre - column[1]), abs(centre - column[2])))
        return best[0]
    records, pending = [], None
    for row in rows[start + 1:]:
        cells = defaultdict(list)
        for word in row:
            if left <= _x(word) <= right:
                cells[column_of(word)].append(_text(word))
        if not cells:
            continue
        mark = next((text for text in cells.get("mark", []) if MARK.fullmatch(text) or DOOR_MARK.fullmatch(text)
                     or SKYLIGHT_MARK.fullmatch(text)), None)
        if mark is None:
            # A line wrapped from the row above (a level name or a size printed a line lower) completes it.
            if pending is not None:
                for field in ("level", "width_mm", "height_mm", "head_mm", "sill_mm", "type"):
                    if cells.get(field) and pending.get(field) in (None, ""):
                        pending[field] = (" ".join(cells[field]) if field in {"level", "type"}
                                          else _number(cells[field][0]))
                        pending["evidence"] += " | " + " ".join(" ".join(values) for values in cells.values())
            if re.search(r"schedule|issue|drawing", " ".join(" ".join(v) for v in cells.values()), re.I):
                pending = None
                if records:
                    break
            continue
        record = {"mark": mark, "page": page, "layout": "row_table",
                  "level": " ".join(cells.get("level", [])) or None,
                  "width_mm": _number(cells["width_mm"][0]) if cells.get("width_mm") else None,
                  "height_mm": _number(cells["height_mm"][0]) if cells.get("height_mm") else None,
                  "head_mm": _number(cells["head_mm"][0]) if cells.get("head_mm") else None,
                  "sill_mm": _number(cells["sill_mm"][0]) if cells.get("sill_mm") else None,
                  "type": " ".join(cells.get("type", [])) or None,
                  "quantity": _number(cells["quantity"][0]) if cells.get("quantity") else None,
                  "evidence": " ".join(" ".join(values) for values in cells.values())}
        records.append(record)
        pending = record
    return records


# --- Column tables --------------------------------------------------------------------------------------------------

_SIZE = re.compile(r"^(\d{1,2},\d{3}|\d{2,5})\s*[×xX]\s*(\d{1,2},\d{3}|\d{2,5})?$")


def _column_table(rows, start, page):
    """Records of a column table whose WINDOW/DOOR/SKYLIGHT NUMBER row is rows[start]."""
    header = rows[start]
    marks = [word for word in header if MARK.fullmatch(_text(word)) or DOOR_MARK.fullmatch(_text(word))
             or SKYLIGHT_MARK.fullmatch(_text(word))]
    if not marks:
        return []
    centres = [_x(word) for word in marks]
    spacing = min((b - a for a, b in zip(centres, centres[1:])), default=120)
    def column(word):
        index = min(range(len(centres)), key=lambda i: abs(centres[i] - _x(word)))
        return index if abs(centres[index] - _x(word)) <= max(spacing * 0.6, 30) else None
    fields = {index: {} for index in range(len(marks))}
    label_rows = {}
    for number, row in enumerate(rows[start + 1:start + 12], start + 1):
        label = " ".join(_text(word) for word in row if float(word["x0"]) < centres[0] - spacing * 0.6).upper()
        if re.search(r"\b(WINDOW|DOOR|SKYLIGHT)\s+NUMBER\b", " ".join(_text(w) for w in row).upper()):
            break                                       # the next table on the sheet
        if "UNIT DIMENSIONS" in label:
            label_rows["size"] = number
        elif "SILL" in label:
            label_rows["sill"] = number
        elif re.search(r"\bHEAD\b", label):
            label_rows["head"] = number
    size_row = label_rows.get("size")
    if size_row is not None:
        # Sizes wrap: "1,290×" a line above the row and "1,300" a line below belong to the same column.
        for row in rows[max(start + 1, size_row - 1):size_row + 2]:
            for word in row:
                index = column(word)
                if index is None:
                    continue
                text = _text(word)
                match = _SIZE.match(text)
                cell = fields[index]
                if match and match.group(2):
                    cell["width_mm"], cell["height_mm"] = _number(match.group(1)), _number(match.group(2))
                elif match:
                    cell["width_mm"] = _number(match.group(1))
                elif _number(text) is not None and cell.get("width_mm") and not cell.get("height_mm"):
                    cell["height_mm"] = _number(text)
                elif _number(text) is not None:
                    cell.setdefault("_wrapped_height", _number(text))
        for cell in fields.values():
            if cell.get("width_mm") and not cell.get("height_mm") and cell.get("_wrapped_height"):
                cell["height_mm"] = cell["_wrapped_height"]
            cell.pop("_wrapped_height", None)
    for name, key in (("sill", "sill_mm"), ("head", "head_mm")):
        if name in label_rows:
            for word in rows[label_rows[name]]:
                index = column(word)
                if index is not None and _number(_text(word)) is not None:
                    fields[index][key] = _number(_text(word))
    records = []
    for index, word in enumerate(marks):
        cell = fields[index]
        records.append({"mark": _text(word), "page": page, "layout": "column_table", "column": index, "level": None,
                        "width_mm": cell.get("width_mm"), "height_mm": cell.get("height_mm"),
                        "sill_mm": cell.get("sill_mm"), "head_mm": cell.get("head_mm"), "type": None, "quantity": 1,
                        "evidence": f"{_text(word)}: " + ", ".join(f"{key} {value}" for key, value in sorted(cell.items()))})
    return records


# --- Schedules ------------------------------------------------------------------------------------------------------

def read_schedules(words, page):
    """{"windows": [...], "doors": [...], "skylights": [...], "unread": [...]} from one page's words.

    Each record: mark, page, layout, level, width_mm, height_mm, sill_mm, head_mm, type, quantity, evidence.
    Records missing a width or height are listed under "unread" with the reason, not returned as windows.
    """
    rows = _rows(words)
    result = {"windows": [], "doors": [], "skylights": [], "unread": []}
    records = []
    for index, row in enumerate(rows):
        joined = " ".join(_text(word) for word in row).upper()
        if re.search(r"\b(WINDOW|DOOR|SKYLIGHT)\s+NUMBER\b", joined):
            records.extend(_column_table(rows, index, page))
            continue
        tables = _row_header(row)
        if tables:
            for header in tables:
                records.extend(_row_table(rows, index, header, page))
    seen = set()
    for record in records:
        key = (record["mark"].upper(), record.get("column"), record["page"], record.get("level"))
        if key in seen:
            continue
        seen.add(key)
        kind = _kind(record["mark"], record.get("type") or "")
        if kind == "door" and re.search(r"\b(?:glass|glazed)\b", record.get("type") or "", re.I):
            kind = "window"                             # glazed doors listed as doors still let in sun
        if not record.get("width_mm") or not record.get("height_mm"):
            result["unread"].append({**record, "reason": "width or height not printed as text in the schedule"})
            continue
        result[{"window": "windows", "door": "doors", "skylight": "skylights"}[kind]].append(record)
    return result


def sill_and_head(record):
    """(sill_mm, head_mm, assumption or None) from a schedule record."""
    height, sill, head = record.get("height_mm"), record.get("sill_mm"), record.get("head_mm")
    if sill is not None:
        return sill, sill + height, None
    if head is not None:
        if height > head:
            return 0, height, f"Printed head height {head} mm is below the window's height {height} mm (a double-height window?): taken as glass from the floor to {height} mm; check."
        return head - height, head, None
    head = max(ASSUMED_HEAD_MM, height)
    return head - height, head, ASSUMED_HEAD_NOTE


# --- Tags on plans ---------------------------------------------------------------------------------------------------

def find_window_tags(words, page, level=None):
    """Window marks printed on one floor-plan page: [{"mark", "x", "y", "page", "level"}] in PDF points."""
    return [{"mark": _text(word), "x": _x(word), "y": _y(word), "page": page, "level": level}
            for word in words if MARK.fullmatch(_text(word).strip(".,:;()[]"))]


# --- Walls -----------------------------------------------------------------------------------------------------------

def _segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) if length else 0.0
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def place_windows(tags, windows, rooms, mm_per_point, reach_mm=900):
    """Put each tagged window on the outside wall it stands against.

    rooms: [{"trace_id", "points": [[x, y], ...] closed outline in this page's PDF points,
             "outside_edges": [edge indices that face outside]}].
    A tag takes the schedule row with its mark (on the tag's level when the schedule gives levels); it goes on the
    nearest outside edge within reach_mm (wall thickness plus the tag's offset from the wall). Several windows may
    share a wall as long as their widths fit its length. Returns {"openings": [{"trace_id", "edge_index", "mark",
    "width_m", "sill_height_m", "head_height_m", "assumptions", "evidence"}], "unplaced": [{"mark", "page", "reason"}]}.
    """
    by_mark = defaultdict(list)
    for window in windows:
        by_mark[window["mark"].upper()].append(window)
    candidates, unplaced = [], []
    for tag in tags:
        rows = by_mark.get(tag["mark"].upper(), [])
        if tag.get("level") and any(row.get("level") for row in rows):
            rows = [row for row in rows if (row.get("level") or "").casefold() == str(tag["level"]).casefold()] or rows
        sizes = {(row["width_mm"], row["height_mm"], row.get("sill_mm"), row.get("head_mm")) for row in rows}
        if not rows:
            unplaced.append({"mark": tag["mark"], "page": tag["page"], "reason": "no window schedule row with this mark"})
            continue
        if len(sizes) > 1:
            unplaced.append({"mark": tag["mark"], "page": tag["page"],
                             "reason": "the schedule gives this mark more than one size; which one is here isn't printed"})
            continue
        best = None
        for room in rooms:
            points = room["points"]
            for edge in room.get("outside_edges", []):
                if not 0 <= edge < len(points) - 1:
                    continue
                distance = _segment_distance((tag["x"], tag["y"]), points[edge], points[edge + 1]) * mm_per_point
                if best is None or distance < best[0]:
                    best = (distance, room, edge)
        if best is None or best[0] > reach_mm:
            unplaced.append({"mark": tag["mark"], "page": tag["page"],
                             "reason": f"no outside wall within {reach_mm} mm of the tag"})
            continue
        candidates.append((best[0], tag, rows[0], best[1], best[2]))
    used = defaultdict(float)
    openings = []
    for distance, tag, row, room, edge in sorted(candidates, key=lambda item: item[0]):
        points = room["points"]
        edge_m = math.dist(points[edge], points[edge + 1]) * mm_per_point / 1000
        width_m = row["width_mm"] / 1000
        if used[(room["trace_id"], edge)] + width_m > edge_m + 0.05:
            unplaced.append({"mark": tag["mark"], "page": tag["page"],
                             "reason": f"its wall ({edge_m:.2f} m) is already filled by other windows"})
            continue
        used[(room["trace_id"], edge)] += width_m
        sill, head, assumption = sill_and_head(row)
        openings.append({"trace_id": room["trace_id"], "edge_index": edge, "mark": tag["mark"], "width_m": width_m,
                         "schedule_page": row["page"],
                         "sill_height_m": sill / 1000, "head_height_m": head / 1000,
                         "assumptions": [assumption] if assumption else [],
                         "evidence": f"Schedule p{row['page']}: {row['evidence']}; tag on plan p{tag['page']}"})
    return {"openings": openings, "unplaced": unplaced}
