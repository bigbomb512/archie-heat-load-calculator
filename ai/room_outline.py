"""Automatic room outlines from a plan page's vector drawing (Card P, task P0).

Deterministic first, AI only for judgement calls:

1. Every stroked or filled shape on the page is grouped by drawing style
   (line weight, stroke colour, fill colour).
2. Hatch and floor-tile grids are found automatically (many long parallel lines
   at a regular spacing) and removed.
3. Which remaining styles are *walls* differs between drawing offices, so it is
   decided per drawing by a small vision task (`wall_style_prompt`): the plan
   is rendered with each style in its own colour and the AI names the wall
   styles. Tests and offline runs may pass the wall styles directly.
4. The wall styles are merged, door-sized gaps are closed, and each open area
   enclosed by walls (not touching the plan edge) becomes a candidate room,
   with furniture islands filled in.
5. Room labels are matched to candidate rooms; the area uses a calibration
   from a printed dimension (never the declared scale alone).

Open-plan areas (no wall between two rooms, or a doorway to outside) are left
for the outline-and-snap step, which refines an AI-sketched outline onto the
drawing's real lines.
"""

from collections import Counter, defaultdict
import hashlib
import json
import math

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union


MIN_ROOM_M2 = 1.0
DEFAULT_DOOR_GAP_MM = 1000.0
HATCH_MIN_LINES = 20
HATCH_MIN_GAPS = 8
HATCH_REGULARITY = 0.5


def _grey(colour):
    if isinstance(colour, (tuple, list)) and colour:
        return round(float(colour[0]), 2)
    if isinstance(colour, (int, float)):
        return round(float(colour), 2)
    return None


def style_key(raw):
    """Stable style identity: line weight, stroke grey level, fill grey level (or None)."""
    fill = bool(raw.get("fill"))
    return (round(float(raw.get("linewidth") or 0), 2), _grey(raw.get("stroking_color")),
            _grey(raw.get("non_stroking_color")) if fill else None)


def style_id(key):
    return "style-" + hashlib.sha256(json.dumps(list(key)).encode()).hexdigest()[:10]


def extract_page_objects(pdf_page, image_px_per_pt):
    """Vector objects of a pdfplumber page in image pixels, with their style."""
    objects = []
    # Some PDFs place the page box away from (0, 0); shapes are reported in the
    # same absolute coordinates, so measure them from the page box origin.
    origin_x, origin_y = (float(value) for value in (getattr(pdf_page, "bbox", None) or (0, 0, 0, 0))[:2])
    for kind, group in (("line", pdf_page.lines), ("curve", pdf_page.curves), ("rect", pdf_page.rects)):
        for raw in group:
            points = [((float(x) - origin_x) * image_px_per_pt, (float(y) - origin_y) * image_px_per_pt) for x, y in raw.get("pts", [])]
            points = [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
            if len(points) < 2:
                continue
            key = style_key(raw)
            objects.append({"kind": kind, "style": key, "style_id": style_id(key), "points": points,
                            "filled": bool(raw.get("fill"))})
    return objects


def _regular_spacing(values):
    values = sorted(values)
    if len(values) < HATCH_MIN_GAPS + 2:
        return False
    gaps = [b - a for a, b in zip(values, values[1:]) if b - a > 2]
    if len(gaps) < HATCH_MIN_GAPS:
        return False
    most_common = Counter(round(gap / 2) * 2 for gap in gaps).most_common(1)[0][1]
    return most_common / len(gaps) >= HATCH_REGULARITY


def hatch_styles(objects, min_length_px=40):
    """Styles drawn as regular grids of parallel lines (floor tiles, hatching)."""
    by_style = defaultdict(list)
    for item in objects:
        if item["kind"] == "line" and len(item["points"]) == 2 and not item["filled"]:
            by_style[item["style"]].append(item["points"])
    hatch = set()
    for key, lines in by_style.items():
        if len(lines) < HATCH_MIN_LINES:
            continue
        horizontal = [(a[1] + b[1]) / 2 for a, b in lines if abs(a[1] - b[1]) < 0.5 and abs(a[0] - b[0]) > min_length_px]
        vertical = [(a[0] + b[0]) / 2 for a, b in lines if abs(a[0] - b[0]) < 0.5 and abs(a[1] - b[1]) > min_length_px]
        if _regular_spacing(horizontal) or _regular_spacing(vertical):
            hatch.add(key)
    return hatch


def style_summary(objects, viewport=None, mm_per_px=None):
    """One row per non-hatch style, for the wall-style task and for diagnostics."""
    area = box(*viewport) if viewport else None
    hatch = hatch_styles(objects)
    rows = {}
    for item in objects:
        geometry = LineString(item["points"])
        if area is not None and not geometry.intersects(area):
            continue
        row = rows.setdefault(item["style_id"], {"style_id": item["style_id"], "style": list(item["style"]),
                                                  "hatch": item["style"] in hatch, "count": 0, "length_px": 0.0})
        row["count"] += 1
        row["length_px"] += geometry.length
    result = sorted(rows.values(), key=lambda row: -row["length_px"])
    for row in result:
        row["length_px"] = round(row["length_px"], 1)
        if mm_per_px:
            row["length_m"] = round(row["length_px"] * mm_per_px / 1000.0, 1)
    return result


def wall_geometry(objects, wall_style_ids, viewport=None, line_half_width_px=1.0):
    """Union of all objects drawn in the chosen wall styles."""
    wanted = set(wall_style_ids)
    area = box(*viewport) if viewport else None
    parts = []
    for item in objects:
        if item["style_id"] not in wanted:
            continue
        line = LineString(item["points"])
        if area is not None and not line.intersects(area):
            continue
        if item["filled"] and len(item["points"]) >= 3:
            polygon = Polygon(item["points"]).buffer(0)
            if not polygon.is_empty and polygon.area > 0:
                parts.append(polygon)
                continue
        parts.append(line.buffer(line_half_width_px))
    return unary_union(parts) if parts else None


def enclosed_rooms(walls, viewport, mm_per_px, door_gap_mm=DEFAULT_DOOR_GAP_MM, min_room_m2=MIN_ROOM_M2):
    """Open areas fully enclosed by walls once door-sized gaps are closed.

    `mm_per_px` only sizes the gap closing and the minimum area here; reported
    areas are recomputed by the caller from a printed-dimension calibration.
    """
    if walls is None or walls.is_empty:
        return []
    radius = (door_gap_mm / mm_per_px) / 2.0
    closed = walls.buffer(radius, join_style=2).buffer(-radius, join_style=2)
    plan = box(*viewport)
    edge = plan.exterior.buffer(2.0)
    free = plan.difference(closed)
    rooms = []
    for part in getattr(free, "geoms", [free]):
        if part.is_empty or part.intersects(edge):
            continue
        outline = Polygon(part.exterior)  # furniture and fixtures inside are part of the floor
        if outline.area * mm_per_px ** 2 / 1e6 < min_room_m2:
            continue
        rooms.append(outline)
    return sorted(rooms, key=lambda polygon: -polygon.area)


def area_m2(polygon, mm_per_px):
    return polygon.area * mm_per_px ** 2 / 1e6


def assign_labels(rooms, labels):
    """Match each label point to the smallest room containing it.

    Returns (matched, ambiguous, unmatched): a room holding two or more labels
    is ambiguous (an open plan to be split by the outline-and-snap step).
    """
    holders = defaultdict(list)
    unmatched = []
    for label in labels:
        point = Point(label["point"])
        containing = [index for index, room in enumerate(rooms) if room.contains(point)]
        if not containing:
            unmatched.append(label)
            continue
        holders[min(containing, key=lambda index: rooms[index].area)].append(label)
    matched, ambiguous = [], []
    for index, held in holders.items():
        if len(held) == 1:
            matched.append({"label": held[0]["text"], "room_index": index, "polygon": rooms[index]})
        else:
            ambiguous.append({"labels": [row["text"] for row in held], "room_index": index, "polygon": rooms[index]})
    return matched, ambiguous, unmatched


def outline_record(label, polygon, calibration, page, evidence):
    """An outline in the shape of a reviewer trace record's geometry fields."""
    points = [[round(x, 2), round(y, 2)] for x, y in polygon.exterior.coords]
    return {"room_label": label, "page": page, "points_image_px": points,
            "area_m2": round(area_m2(polygon, calibration["mm_per_px"]), 3),
            "calibration": calibration, "method": "enclosed_walls", "evidence": evidence}


def wall_style_prompt(summary):
    """Text for the wall-style vision task; the rendered image carries the colours."""
    legend = "\n".join(f"{index + 1}. {row['style_id']} — {row['count']} shapes, {row.get('length_m', row['length_px'])} "
                       f"{'m' if 'length_m' in row else 'px'} drawn" for index, row in enumerate(summary) if not row["hatch"])
    return ("The image is one architectural floor plan with every drawing style shown in its own colour; the legend "
            "numbers match the colours. Which numbers are **walls** (including the tenancy boundary, partitions and "
            "shopfront lines)? Do not include furniture, joinery, shelving, equipment, dimensions, text, door swings "
            "or floor patterns. Reply with JSON only: {\"wall_styles\": [int], \"uncertain\": [int], \"notes\": string}.\n\n"
            + legend)


def validate_wall_style_reply(reply, summary):
    """Map the AI's legend numbers back to style IDs; reject unknown numbers."""
    if not isinstance(reply, dict) or not isinstance(reply.get("wall_styles"), list):
        raise ValueError("Wall-style reply must contain a wall_styles list.")
    numbered = [row for row in summary if not row["hatch"]]
    chosen = []
    for number in reply["wall_styles"]:
        if type(number) is not int or not 1 <= number <= len(numbered):
            raise ValueError(f"Wall-style reply names an unknown legend number: {number!r}.")
        chosen.append(numbered[number - 1]["style_id"])
    if not chosen:
        raise ValueError("Wall-style reply names no wall styles.")
    return sorted(set(chosen))


# --- AI naming and open-plan splits -------------------------------------------------

NOT_A_ROOM = "not_a_room"
COLOURS = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230), (160, 160, 0), (250, 150, 190), (0, 128, 128),
           (170, 110, 40), (128, 0, 0), (0, 0, 128), (128, 128, 128)]


def render_candidate_areas(page_image, areas, viewport, max_side=1536):
    """Plan image with each candidate area tinted and numbered (1-based)."""
    from PIL import ImageDraw
    image = page_image.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    for index, polygon in enumerate(areas):
        red, green, blue = COLOURS[index % len(COLOURS)]
        draw.polygon(list(polygon.exterior.coords), fill=(red, green, blue, 70), outline=(red, green, blue, 255))
        point = polygon.representative_point()
        draw.rectangle([point.x - 22, point.y - 18, point.x + 22, point.y + 18], fill=(255, 255, 255, 230), outline=(0, 0, 0, 255))
        draw.text((point.x - 8, point.y - 8), str(index + 1), fill=(0, 0, 0, 255))
    left, top, right, bottom = viewport
    crop = image.crop((int(left), int(top), int(right), int(bottom)))
    factor = min(1.0, max_side / max(crop.size))
    if factor < 1.0:
        crop = crop.resize((int(crop.size[0] * factor), int(crop.size[1] * factor)))
    return crop, {"offset_px": [left, top], "factor": factor}


def room_naming_prompt(room_names, area_count):
    rooms = ", ".join(room_names)
    return ("The image is one architectural floor plan. Enclosed areas are tinted and numbered 1 to "
            f"{area_count}. This tenancy's rooms are: {rooms}. Name each numbered area using that list, judging from "
            "what is drawn in it (equipment, furniture, doors, signage, text). If an area is not a room (a void, "
            "island, planter, duct or outside space) say \"not_a_room\". If one numbered area contains **two** of the "
            "rooms with no wall between them, give a split: a line in image pixels drawn along the drawn feature that "
            "separates them (counter edge, floor-finish change, bulkhead), and one point inside each room. "
            "A room may cover more than one numbered area; give each of them the same name. "
            "Use only the room names given. Reply with JSON only:\n"
            "{\"areas\": [{\"number\": int, \"room\": string}],\n"
            " \"splits\": [{\"number\": int, \"line_px\": [[x, y], ...], \"rooms\": [{\"room\": string, \"point_px\": [x, y]}],"
            " \"feature\": string}]}")


def _to_page(point, transform):
    factor = transform["factor"]
    return (point[0] / factor + transform["offset_px"][0], point[1] / factor + transform["offset_px"][1])


def validate_naming_reply(reply, area_count, room_names, transform):
    """Check the AI's naming/split reply and convert image points back to page pixels."""
    if not isinstance(reply, dict) or not isinstance(reply.get("areas"), list):
        raise ValueError("Naming reply must contain an areas list.")
    allowed = {name.casefold(): name for name in room_names}
    names, used = {}, Counter()
    for row in reply["areas"]:
        number = row.get("number") if isinstance(row, dict) else None
        if type(number) is not int or not 1 <= number <= area_count:
            raise ValueError(f"Naming reply refers to an unknown area: {number!r}.")
        room = str(row.get("room", "")).strip()
        if room != NOT_A_ROOM and room.casefold() not in allowed:
            raise ValueError(f"Naming reply uses a room name that is not in the room list: {room!r}.")
        names[number - 1] = NOT_A_ROOM if room == NOT_A_ROOM else allowed[room.casefold()]
        if room != NOT_A_ROOM:
            used[allowed[room.casefold()]] += 1
    splits = []
    for row in reply.get("splits", []) or []:
        number = row.get("number") if isinstance(row, dict) else None
        if type(number) is not int or not 1 <= number <= area_count:
            raise ValueError(f"Split refers to an unknown area: {number!r}.")
        line = row.get("line_px")
        rooms = row.get("rooms")
        if not isinstance(line, list) or len(line) < 2 or not isinstance(rooms, list) or len(rooms) != 2:
            raise ValueError("Each split needs a line of at least two points and exactly two rooms.")
        converted = []
        for item in rooms:
            room = str((item or {}).get("room", "")).strip()
            if room.casefold() not in allowed:
                raise ValueError(f"Split uses a room name that is not in the room list: {room!r}.")
            converted.append({"room": allowed[room.casefold()], "point": _to_page(item["point_px"], transform)})
            used[allowed[room.casefold()]] += 1
        splits.append({"area_index": number - 1, "line": [_to_page(point, transform) for point in line],
                       "rooms": converted, "feature": str(row.get("feature", ""))[:200]})
    for split in splits:
        named = names.pop(split["area_index"], None)
        if named and named != NOT_A_ROOM:
            used[named] -= 1  # the split's two rooms replace the area's single name
    # A room may span several enclosed areas (partial walls); those areas are
    # merged. A room may not be both split out of one area and named elsewhere.
    split_rooms = {item["room"] for split in splits for item in split["rooms"]}
    clashes = sorted(room for room in split_rooms if room in set(names.values()))
    if clashes or any(sum(item["room"] == room for split in splits for item in split["rooms"]) > 1 for room in split_rooms):
        raise ValueError("A room split out of one area cannot also be given to another area: " + ", ".join(clashes or sorted(split_rooms)) + ".")
    return names, splits


def snap_polyline(points, segments, max_offset_px, max_angle_deg=3):
    """Move each polyline segment onto the nearest drawn line running the same way.

    A segment is snapped only to a drawn line within `max_offset_px` and within
    `max_angle_deg` of its direction; otherwise it keeps the AI's position and
    is reported as unsnapped.
    """
    snapped_segments, report = [], []
    for start, end in zip(points, points[1:]):
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        middle = Point((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        best = None
        for candidate in segments:
            (x1, y1), (x2, y2) = candidate.coords[0], candidate.coords[-1]
            other = math.atan2(y2 - y1, x2 - x1)
            if abs((angle - other + math.pi / 2) % math.pi - math.pi / 2) > math.radians(max_angle_deg):
                continue
            distance = candidate.distance(middle)
            if distance <= max_offset_px and (best is None or distance < best[0]):
                best = (distance, candidate)
        if best is None:
            snapped_segments.append((start, end))
            report.append({"snapped": False})
            continue
        line = best[1]
        project = lambda point: line.interpolate(line.project(Point(point)))
        # Shift the AI segment onto the drawn line, keeping the AI's extent.
        shift_start, shift_end = project(start), project(end)
        snapped_segments.append(((shift_start.x, shift_start.y), (shift_end.x, shift_end.y)))
        report.append({"snapped": True, "offset_px": round(best[0], 1)})
    merged = [snapped_segments[0][0]] + [segment[1] for segment in snapped_segments]
    return merged, report


def split_area(polygon, line_points, room_points):
    """Cut `polygon` with a polyline extended to its boundary; name each piece by the point it contains."""
    from shapely.ops import split
    line = LineString(line_points)
    # Extend both ends well past the polygon so the cut reaches its edges.
    (x1, y1), (x2, y2) = line.coords[0], line.coords[1]
    (x3, y3), (x4, y4) = line.coords[-2], line.coords[-1]
    reach = max(polygon.bounds[2] - polygon.bounds[0], polygon.bounds[3] - polygon.bounds[1])
    def extend(ax, ay, bx, by):
        length = math.hypot(bx - ax, by - ay) or 1.0
        return (bx + (bx - ax) / length * reach, by + (by - ay) / length * reach)
    start = extend(x2, y2, x1, y1)
    end = extend(x3, y3, x4, y4)
    cut = LineString([start] + list(line.coords) + [end])
    pieces = [piece for piece in split(polygon, cut).geoms if piece.area > 0]
    if len(pieces) != 2:
        raise ValueError(f"The split line must divide the area into exactly two parts (got {len(pieces)}).")
    named = {}
    for item in room_points:
        holder = [piece for piece in pieces if piece.contains(Point(item["point"]))]
        if len(holder) != 1:
            raise ValueError(f"The point for {item['room']} is not inside exactly one part of the split area.")
        named[item["room"]] = holder[0]
    if len(set(map(id, named.values()))) != 2:
        raise ValueError("Both rooms of a split must be in different parts.")
    return named


def drawn_segments(objects, style_ids, min_length_px=10):
    """Straight segments of the given styles, for snapping."""
    wanted = set(style_ids)
    segments = []
    for item in objects:
        if item["style_id"] not in wanted:
            continue
        for start, end in zip(item["points"], item["points"][1:]):
            if math.dist(start, end) >= min_length_px:
                segments.append(LineString([start, end]))
    return segments


# --- Packets: wall styles and dimension reading -------------------------------------

def render_styles(objects, summary, viewport, max_side=1536, max_styles=14):
    """Plan redrawn on white with each non-hatch style in its own colour, plus a numbered legend."""
    from PIL import Image, ImageDraw
    numbered = [row for row in summary if not row["hatch"]][:max_styles]
    colour_of = {row["style_id"]: COLOURS[index % len(COLOURS)] for index, row in enumerate(numbered)}
    left, top, right, bottom = viewport
    factor = min(1.0, (max_side - 260) / max(right - left, bottom - top))
    width, height = int((right - left) * factor), int((bottom - top) * factor)
    image = Image.new("RGB", (width + 260, max(height, 40 + 28 * len(numbered))), "white")
    draw = ImageDraw.Draw(image)
    to_image = lambda point: ((point[0] - left) * factor, (point[1] - top) * factor)
    for item in objects:
        colour = colour_of.get(item["style_id"])
        if colour is None:
            continue
        points = [to_image(point) for point in item["points"]]
        if item["filled"] and len(points) >= 3:
            draw.polygon(points, fill=colour)
        else:
            draw.line(points, fill=colour, width=2)
    for index, row in enumerate(numbered):
        y = 20 + 28 * index
        draw.rectangle([width + 20, y, width + 50, y + 18], fill=colour_of[row["style_id"]])
        draw.text((width + 60, y + 3), f"{index + 1}  ({row['count']} shapes)", fill=(0, 0, 0))
    return image, numbered


def dimension_line_candidates(objects, walls, viewport, limit=3, min_length_px=300):
    """Long thin straight lines terminated by ticks: likely dimension lines.

    Ticks (obliques, arrows or dots, filled or stroked) are small shapes on the
    line near each end. The measured span runs between the two tick centres,
    because the drawn line usually overshoots its termination points.
    """
    left, top, right, bottom = viewport
    small = []
    for item in objects:
        xs = [point[0] for point in item["points"]]
        ys = [point[1] for point in item["points"]]
        extent = max(max(xs) - min(xs), max(ys) - min(ys))
        if 3 <= extent <= 30:
            small.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    candidates = []
    for item in objects:
        if item["filled"] or item["style"][0] > 0.3 or len(item["points"]) != 2:
            continue
        (x1, y1), (x2, y2) = item["points"]
        length = math.dist((x1, y1), (x2, y2))
        horizontal, vertical = abs(y1 - y2) < 0.5, abs(x1 - x2) < 0.5
        if length < min_length_px or not (horizontal or vertical):
            continue
        line = LineString(item["points"])
        if walls is not None and line.intersection(walls).length > 0.2 * length:
            continue
        along = (lambda point: point[0]) if horizontal else (lambda point: point[1])
        low, high = sorted((along((x1, y1)), along((x2, y2))))
        on_line = [point for point in small if line.distance(Point(point)) <= 6]
        start_ticks = [along(point) for point in on_line if abs(along(point) - low) <= 20]
        end_ticks = [along(point) for point in on_line if abs(along(point) - high) <= 20]
        if not start_ticks or not end_ticks:
            continue
        span = (sum(end_ticks) / len(end_ticks)) - (sum(start_ticks) / len(start_ticks))
        if span <= 0.8 * length:
            continue
        edge_distance = min(abs(y1 - top), abs(bottom - y1)) if horizontal else min(abs(x1 - left), abs(right - x1))
        candidates.append({"points": [(x1, y1), (x2, y2)], "length_px": length, "span_px": span,
                           "edge_distance_px": edge_distance})
    candidates.sort(key=lambda row: (-row["span_px"], row["edge_distance_px"]))
    chosen = []
    for row in candidates:
        if all(LineString(row["points"]).distance(LineString(other["points"])) > 20 for other in chosen):
            chosen.append(row)
        if len(chosen) == limit:
            break
    return chosen


def dimension_prompt():
    return ("The image is a crop of an architectural plan. One dimension line is highlighted in red. What number is "
            "printed for that dimension line (the dimension text that sits on or beside it, in millimetres)? Read only "
            "what is printed; if you cannot read it, return null. Reply with JSON only: {\"value_mm\": int|null, "
            "\"printed_text\": string}")


def calibration_from_dimension(value_mm, length_px, declared_mm_per_px, tolerance=0.02):
    """Printed-dimension calibration; must agree with the declared drawing scale within 2 %."""
    if not isinstance(value_mm, (int, float)) or value_mm <= 0 or length_px <= 0:
        raise ValueError("A positive printed dimension and line length are required.")
    mm_per_px = value_mm / length_px
    difference = abs(mm_per_px / declared_mm_per_px - 1) if declared_mm_per_px else None
    if difference is None or difference > tolerance:
        raise ValueError(f"The printed dimension gives {mm_per_px:.4f} mm/px, which does not agree with the declared "
                         f"scale ({declared_mm_per_px} mm/px) within {tolerance:.0%}.")
    return {"mm_per_px": mm_per_px, "status": "agreed", "dimension_value_mm": value_mm,
            "scale_difference": round(difference, 5), "method": "ai_read_printed_dimension"}


# --- Full-room AI outlines (open plans with no enclosing walls) ---------------------

def outline_prompt(room_names):
    rooms = ", ".join(room_names)
    return ("The image is one architectural floor plan. Draw the outline of each of these rooms: " + rooms + ". "
            "Give each outline as the corner points of the room's floor, in image pixels, in order around the room, "
            "following the inside face of walls where there are walls, and the drawn boundary (tenancy line, "
            "counter edge, floor-finish change) where there is no wall. Name the drawn feature you followed for any "
            "side without a wall. If a room is not on this plan, leave it out. Reply with JSON only:\n"
            "{\"rooms\": [{\"room\": string, \"points_px\": [[x, y], ...], \"open_sides\": string}]}")


def validate_outline_reply(reply, room_names, transform):
    if not isinstance(reply, dict) or not isinstance(reply.get("rooms"), list):
        raise ValueError("Outline reply must contain a rooms list.")
    allowed = {name.casefold(): name for name in room_names}
    outlines, seen = [], set()
    for row in reply["rooms"]:
        room = str((row or {}).get("room", "")).strip()
        if room.casefold() not in allowed:
            raise ValueError(f"Outline reply uses a room name that is not in the room list: {room!r}.")
        if room.casefold() in seen:
            raise ValueError(f"Outline reply gives {room!r} twice.")
        seen.add(room.casefold())
        points = row.get("points_px")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError(f"The outline for {room} needs at least three corner points.")
        page_points = [_to_page(point, transform) for point in points]
        polygon = Polygon(page_points)
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError(f"The outline for {room} crosses itself or has no area.")
        outlines.append({"room": allowed[room.casefold()], "points": page_points,
                         "open_sides": str(row.get("open_sides", ""))[:300]})
    return outlines


def _line_intersection(a1, a2, b1, b2):
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = a1, a2, b1, b2
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denominator
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def snap_polygon(points, segments, max_offset_px, max_angle_deg=8):
    """Snap each sketched edge to the nearest parallel drawn line, then rebuild the corners.

    Corners are where consecutive snapped edges meet, so a room sketched a little
    off takes its exact size from the drawing. Edges with no drawn line nearby
    keep the sketch and are counted as unsnapped.
    """
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    edges, report = [], []
    for start, end in zip(ring, ring[1:]):
        snapped, row = snap_polyline([start, end], segments, max_offset_px, max_angle_deg)
        edges.append((snapped[0], snapped[1]))
        report.append(row[0])
    corners = []
    for index, edge in enumerate(edges):
        previous = edges[index - 1]
        corner = _line_intersection(previous[0], previous[1], edge[0], edge[1])
        original = ring[index]
        if corner is None or math.dist(corner, original) > 4 * max_offset_px:
            corner = original  # parallel neighbours or a wild intersection: keep the sketch
        corners.append(corner)
    polygon = Polygon(corners)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return polygon, {"edges": len(report), "snapped_edges": sum(item["snapped"] for item in report)}


def choose_area(printed_m2=None, enclosed_m2=None, outline_m2=None, tolerance=0.05):
    """Area source in order of trust: printed on the drawing, enclosed by walls, AI outline.

    A computed area that disagrees with a printed one by more than the tolerance
    is reported; the printed value is used.
    """
    if printed_m2:
        computed = enclosed_m2 or outline_m2
        conflict = computed is not None and abs(computed / printed_m2 - 1) > tolerance
        return {"area_m2": printed_m2, "source": "printed_on_drawing",
                "conflict": (f"Measured {computed:.2f} m² differs from the printed {printed_m2} m²." if conflict else "")}
    if enclosed_m2:
        return {"area_m2": enclosed_m2, "source": "enclosed_walls", "conflict": ""}
    if outline_m2:
        return {"area_m2": outline_m2, "source": "ai_outline_snapped", "conflict": ""}
    return {"area_m2": None, "source": "none", "conflict": ""}
