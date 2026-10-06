"""Reading scanned drawing sets (Card S): pages that are images with no text layer.

There is no OCR in the project, so the AI's vision is the reader. Each reading
is checked by rules that do not need a text layer:

- S1 printed room areas: the plan is sent as overlapping tiles; a room read on
  two tiles must be read the same way, units are fixed (m² or square feet), and
  each label is placed in a room outline found by the scan wall finder.
- Area calibration (user decision 2026-10-06): when two or more rooms have a
  printed area and an outline, the scales those imply must agree within 2%;
  that sets the sheet scale. One room alone is not enough. Still no
  scale-only calibration.
- S2 imperial scales and dimensions: 1/8" = 1'-0" is 1:96; 12'-6" is 3,810 mm.
- S3 site from a scanned title block: the AI first copies the title-block text
  from an image crop; the existing site task then chooses from those lines,
  and its validator checks every quote against them.
"""

from fractions import Fraction
import json
import math
import re

from shapely.geometry import Point


MM_PER_INCH = 25.4
SQ_FT_TO_M2 = 0.09290304
AREA_UNITS = {"m2": 1.0, "sf": SQ_FT_TO_M2}
MIN_ROOM_M2, MAX_ROOM_M2 = 0.5, 5000.0
TILE_OVERLAP = 0.25
MAX_TILE_SIDE = 1536
MAX_TITLE_LINES = 80
MAX_LINE_CHARS = 120

_QUOTES = str.maketrans({"′": "'", "’": "'", "‘": "'", "´": "'", "″": '"',
                         "“": '"', "”": '"'})


def _clean(text):
    return " ".join(str(text or "").translate(_QUOTES).replace("''", '"').split())


def _inches(text):
    """'1/8', '3/16', '1 1/2', '1.5', '12' -> inches as a Fraction."""
    text = text.strip()
    whole = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", text)
    if whole:
        return int(whole.group(1)) + Fraction(int(whole.group(2)), int(whole.group(3)))
    simple = re.fullmatch(r"(\d+)/(\d+)", text)
    if simple:
        return Fraction(int(simple.group(1)), int(simple.group(2)))
    return Fraction(text) if re.fullmatch(r"\d+(?:\.\d+)?", text) else None


# ---------------------------------------------------------------- S2: scales and dimensions

def parse_scale(text):
    """Drawing scale denominator from printed scale text, or None.

    Metric '1:100' -> 100. Architectural '1/8" = 1'-0"' -> 96 (12 in / 0.125 in).
    Engineering '1" = 10'' -> 120. 'NTS'/'As indicated' -> None.
    """
    text = _clean(text)
    imperial = re.search(r"(\d+(?:\s+\d+/\d+)?|\d+/\d+|\d+(?:\.\d+)?)\s*\"\s*=\s*(\d+)\s*'\s*(?:-?\s*(\d+)\s*\")?", text)
    if imperial:
        paper = _inches(imperial.group(1))
        real = int(imperial.group(2)) * 12 + int(imperial.group(3) or 0)
        if paper and paper > 0 and real > 0:
            ratio = Fraction(real) / paper
            return int(ratio) if ratio.denominator == 1 else float(ratio)
        return None
    metric = re.search(r"\b1\s*:\s*(\d+(?:\.\d+)?)\b", text)
    if metric:
        value = float(metric.group(1))
        return int(value) if value.is_integer() else value
    return None


def parse_dimension_mm(text):
    """Millimetres from a printed dimension: '11825', '12'-6"', '12' 6 1/2"', '9'', '6"'. None if unreadable."""
    text = _clean(text)
    if re.fullmatch(r"\d{2,6}", text):
        return float(text)
    feet_inches = re.fullmatch(r"(\d+)\s*'\s*(?:-?\s*(\d+(?:\s+\d+/\d+)?|\d+/\d+)\s*\")?", text)
    if feet_inches:
        inches = int(feet_inches.group(1)) * 12 + (_inches(feet_inches.group(2)) if feet_inches.group(2) else 0)
        return float(inches) * MM_PER_INCH
    inches_only = re.fullmatch(r"(\d+(?:\s+\d+/\d+)?|\d+/\d+)\s*\"", text)
    if inches_only:
        return float(_inches(inches_only.group(1))) * MM_PER_INCH
    return None


def mm_per_px_from_scale(denominator, render_dpi):
    """Millimetres per rendered pixel for a drawing scale (same rule as dimension_wall_matcher.page_mm_per_px)."""
    return (MM_PER_INCH / float(render_dpi)) * float(denominator) if denominator and render_dpi else None


# ---------------------------------------------------------------- S1: printed areas from tiles

def area_tiles(viewport, columns=2, rows=2, overlap=TILE_OVERLAP):
    """Overlapping tile boxes (page pixels) covering the plan viewport, left-to-right, top-to-bottom."""
    left, top, right, bottom = (float(value) for value in viewport)
    width, height = (right - left) / columns, (bottom - top) / rows
    pad_x, pad_y = width * overlap / 2.0, height * overlap / 2.0
    tiles = []
    for row in range(rows):
        for column in range(columns):
            tiles.append((max(left, left + column * width - pad_x), max(top, top + row * height - pad_y),
                          min(right, left + (column + 1) * width + pad_x), min(bottom, top + (row + 1) * height + pad_y)))
    return tiles


def tile_factor(tile, max_side=MAX_TILE_SIDE):
    """Scale from page pixels to the sent tile image."""
    return min(1.0, max_side / max(tile[2] - tile[0], tile[3] - tile[1]))


def area_prompt(tile_count):
    return ("The images are overlapping tiles (numbered 1 to %d) of one scanned floor plan. List every room that has a "
            "printed floor area. For each, give the tile you read it on, the room name exactly as printed, the room "
            "number if printed, the area value and unit as printed (m2 or sf), the printed area text exactly, and the "
            "pixel position of the room name in that tile. If a room appears on two tiles, list it on both. Leave out "
            "rooms with no printed area; never estimate an area. Also copy the plan's printed scale (for example "
            "1/8\" = 1'-0\" or 1:100), or null if none is printed. Reply with JSON only:\n"
            "{\"rooms\": [{\"tile\": int, \"name\": string, \"number\": string|null, \"area_value\": number, "
            "\"unit\": \"m2\"|\"sf\", \"printed_text\": string, \"label_px\": [x, y]}], \"scale_text\": string|null}"
            % tile_count)


def _unit(value):
    unit = re.sub(r"[\s.²]", "", str(value or "").casefold()).replace("sqm", "m2").replace("sqft", "sf").replace("ft2", "sf")
    if unit in {"m", "m2"}:
        return "m2"
    if unit in {"sf", "sft"}:
        return "sf"
    return None


def _name_key(name):
    text = re.sub(r"\brm\b", "room", " ".join(str(name or "").casefold().split()))
    return re.sub(r"[^a-z0-9 ]", "", text)


def validate_area_reply(reply, tiles, factors=None, outlines=None):
    """Check an S1 reply; returns {"rooms": [...], "scale_denominator": int|float|None, "scale_text": str|None}.

    Every room carries its area in m², its printed text, its label position in
    page pixels and the index of the outline that holds the label (or None).
    """
    if isinstance(reply, str):
        reply = json.loads(reply)
    if not isinstance(reply, dict) or not isinstance(reply.get("rooms"), list):
        raise ValueError("The area reply must contain a rooms list.")
    factors = factors or [tile_factor(tile) for tile in tiles]
    reads = []
    for row in reply["rooms"]:
        if not isinstance(row, dict):
            raise ValueError("Each room must be an object.")
        tile = row.get("tile")
        if type(tile) is not int or not 1 <= tile <= len(tiles):
            raise ValueError(f"Room {row.get('name')!r} cites tile {tile!r}, which was not sent.")
        name = " ".join(str(row.get("name") or "").split())
        if not name:
            raise ValueError("Every room needs its printed name.")
        unit = _unit(row.get("unit"))
        if unit is None:
            raise ValueError(f"{name}: unit must be m2 or sf, not {row.get('unit')!r}.")
        value = row.get("area_value")
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name}: the printed area must be a positive number.")
        area_m2 = float(value) * AREA_UNITS[unit]
        if not MIN_ROOM_M2 <= area_m2 <= MAX_ROOM_M2:
            raise ValueError(f"{name}: {value} {unit} is not a plausible room area.")
        printed = str(row.get("printed_text") or "")
        digits = re.sub(r"\D", "", printed.split(".")[0] if float(value).is_integer() else printed)
        expected = str(int(value)) if float(value).is_integer() else re.sub(r"\D", "", f"{value}")
        if not printed or expected not in digits:
            raise ValueError(f"{name}: the printed text {printed!r} does not show the value {value}.")
        point = row.get("label_px")
        if not (isinstance(point, list) and len(point) == 2 and all(type(v) in (int, float) for v in point)):
            raise ValueError(f"{name}: label_px must be [x, y].")
        left, top, right, bottom = tiles[tile - 1]
        factor = factors[tile - 1]
        width, height = (right - left) * factor, (bottom - top) * factor
        if not (0 <= point[0] <= width and 0 <= point[1] <= height):
            raise ValueError(f"{name}: label_px {point} is outside tile {tile}.")
        page_px = (left + point[0] / factor, top + point[1] / factor)
        reads.append({"name": name, "key": _name_key(name), "number": row.get("number"), "unit": unit,
                      "area_value": float(value), "area_m2": round(area_m2, 2), "printed_text": printed,
                      "tile": tile, "page_px": page_px})
    rooms = {}
    for read in reads:
        same = rooms.setdefault(read["key"], [])
        same.append(read)
    result = []
    for key, same in rooms.items():
        values = {(read["area_value"], read["unit"]) for read in same}
        if len(values) > 1:
            raise ValueError(f"{same[0]['name']} was read differently on tiles "
                             f"{', '.join(str(read['tile']) for read in same)}: "
                             f"{', '.join(f'{v} {u}' for v, u in sorted(values))}; the reading is refused.")
        x = sum(read["page_px"][0] for read in same) / len(same)
        y = sum(read["page_px"][1] for read in same) / len(same)
        outline = None
        if outlines:
            holders = [index for index, polygon in enumerate(outlines) if polygon.contains(Point(x, y))]
            outline = min(holders, key=lambda index: outlines[index].area) if holders else None
        result.append({"label": same[0]["name"], "number": same[0]["number"], "area_m2": same[0]["area_m2"],
                       "printed_text": same[0]["printed_text"], "unit": same[0]["unit"], "tiles": sorted(r["tile"] for r in same),
                       "label_page_px": [round(x, 1), round(y, 1)], "outline_index": outline,
                       "source": "printed_read_from_image"})
    shared = {}
    for room in result:
        if room["outline_index"] is not None:
            shared.setdefault(room["outline_index"], []).append(room["label"])
    for room in result:
        others = [label for label in shared.get(room["outline_index"], []) if label != room["label"]]
        room["note"] = f"Label sits in the same outline as {', '.join(others)}." if others else ""
    scale_text = reply.get("scale_text")
    denominator = parse_scale(scale_text) if scale_text else None
    return {"rooms": sorted(result, key=lambda room: room["label"]), "scale_text": scale_text, "scale_denominator": denominator}


# ---------------------------------------------------------------- calibration from printed areas

def calibration_from_areas(rooms, outlines, declared_mm_per_px=None, tolerance=0.02, min_rooms=2):
    """Sheet scale from rooms that have both a printed area and their own outline.

    Each room gives mm/px = sqrt(printed area / outline pixel area). At least
    `min_rooms` must agree within `tolerance` of their median; one room alone
    never calibrates. Raises ValueError when there is no agreement.
    """
    owners = {}
    for room in rooms:
        if room.get("outline_index") is not None:
            owners.setdefault(room["outline_index"], []).append(room)
    implied = []
    for index, held in owners.items():
        if len(held) != 1:
            continue  # two labelled rooms in one outline: the outline is not either room's floor
        pixels = outlines[index].area
        if pixels > 0:
            implied.append({"label": held[0]["label"], "outline_index": index,
                            "mm_per_px": math.sqrt(held[0]["area_m2"] * 1e6 / pixels)})
    if len(implied) < min_rooms:
        raise ValueError(f"Area calibration needs at least {min_rooms} rooms with a printed area and their own outline; "
                         f"found {len(implied)}.")
    values = sorted(row["mm_per_px"] for row in implied)
    median = values[len(values) // 2] if len(values) % 2 else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
    agreeing = [row for row in implied if abs(row["mm_per_px"] - median) / median <= tolerance]
    if len(agreeing) < min_rooms:
        raise ValueError("Printed room areas and outlines imply scales that disagree by more than "
                         f"{tolerance:.0%}: " + ", ".join(f"{row['label']} {row['mm_per_px']:.2f}" for row in implied))
    mm_per_px = sum(row["mm_per_px"] for row in agreeing) / len(agreeing)
    result = {"mm_per_px": mm_per_px, "status": "agreed", "source": "printed_room_areas",
              "rooms": [row["label"] for row in agreeing],
              "rejected": [row["label"] for row in implied if row not in agreeing]}
    if declared_mm_per_px:
        difference = abs(mm_per_px - declared_mm_per_px) / declared_mm_per_px
        result.update({"declared_mm_per_px": declared_mm_per_px, "declared_difference": round(difference, 4),
                       "declared_scale_agrees": difference <= tolerance})
    return result


# ---------------------------------------------------------------- S3: site from a scanned title block

def title_block_crops(page_width, page_height):
    """Image regions (page pixels) where title blocks usually sit: the right strip and the bottom band."""
    return [{"region": "right_strip", "bbox": (page_width * 0.78, 0, page_width, page_height)},
            {"region": "bottom_band", "bbox": (0, page_height * 0.8, page_width, page_height)}]


def transcription_prompt():
    return ("The image is the title block of one drawing sheet. Copy every line of text in it exactly as printed, top "
            "to bottom, one entry per printed line. Do not correct spelling, expand abbreviations or add anything "
            "that is not printed. Reply with JSON only: {\"lines\": [string]}")


def validate_transcription(reply):
    if isinstance(reply, str):
        reply = json.loads(reply)
    lines = reply.get("lines") if isinstance(reply, dict) else None
    if not isinstance(lines, list) or not lines:
        raise ValueError("The transcription must contain a non-empty lines list.")
    if len(lines) > MAX_TITLE_LINES:
        raise ValueError(f"A title block has at most {MAX_TITLE_LINES} lines; the reply has {len(lines)}.")
    cleaned = []
    for line in lines:
        if not isinstance(line, str):
            raise ValueError("Every transcribed line must be text.")
        line = " ".join(line.split())
        if len(line) > MAX_LINE_CHARS:
            raise ValueError(f"A transcribed line is longer than {MAX_LINE_CHARS} characters: {line[:40]!r}…")
        if line:
            cleaned.append(line)
    return cleaned


ADDRESS_HINT = re.compile(r"\d|\b(road|rd|street|st|avenue|ave|drive|dr|highway|hwy|lane|ln|base|building|bldg|"
                          r"centre|center|suite|level|tenancy|shop|nsw|vic|qld|ma|ny|ca)\b", re.I)


def site_packet_from_transcriptions(pages, max_block_lines=4):
    """Site-task packet and prompt from transcribed title blocks: {page: [lines]}.

    Consecutive address-like lines are joined into blocks of up to four lines,
    so a site written over several lines can be quoted as one excerpt. The
    reply is checked with autonomous_tasks.validate_site_reply, which requires
    every quote to be an exact substring of one excerpt.
    """
    excerpts = []
    for page, lines in sorted(pages.items()):
        block = []
        for line in lines + [""]:
            if line and ADDRESS_HINT.search(line) and len(block) < max_block_lines:
                block.append(line)
                continue
            if block:
                excerpts.append({"page": int(page), "text": "\n".join(block), "clue_type": "read_from_image"})
            block = [line] if line and ADDRESS_HINT.search(line) else []
    unique = {(row["page"], row["text"]): row for row in excerpts}
    excerpts = sorted(unique.values(), key=lambda row: (row["page"], row["text"]))
    packet = {"task": "P1_site", "excerpts": excerpts, "rule_based_top_candidate": None, "read_from_image": True}
    prompt = ("Here are excerpts copied from the title blocks of one scanned drawing set, each with its page number. "
              "Decide which excerpt names the project site (a street address, or a building, base or centre with a "
              "site), and which addresses belong to the owner or consultants (architect, engineer, client offices). "
              "Use only the excerpts given; quote the site text exactly as it appears in one excerpt. If no excerpt "
              "names the site, return \"site\": null.\n"
              'JSON only: {"site": {"text": string, "page": int, "kind": "street_address"|"tenancy_in_centre"} | null, '
              '"consultant_addresses": [{"text": string, "page": int, "why": string}]}\n\n'
              + json.dumps(excerpts, ensure_ascii=False, separators=(",", ":")))
    return packet, prompt
