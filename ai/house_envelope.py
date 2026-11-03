"""A house's outside walls, and its walls and floors next to spaces that aren't cooled — no AI.

User decisions (30 October 2026): garages/carports, laundries, walk-in robes, bathrooms/ensuites/powder rooms and lift
shafts aren't cooled; outdoor spaces are outside; an outside wall the AI can't classify faces outside; a reviewer's
own classification always stands. Positions here are in millimetres in each plan page's own frame
(image pixels × the trace's calibrated mm per pixel).
"""
import math
import re
from statistics import median

# Not cooled, indoors: the space beyond the wall is a buffer (the method's outdoor-minus-3 K estimate).
INDOOR_NOT_COOLED = {"house_wet", "house_service", "garage", "lift_shaft"}
OUTDOOR = {"outdoor"}
COOLED_SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception"}
_LEVELS = ((r"\b(?:sub[- ]?)?basement\b|\bcellar\b", -2), (r"\blower\s+ground\b|\blgf?\b", -1),
           (r"\bupper\s+ground\b", 0.5), (r"\bground\b|\bgf\b|\blevel\s*0\b", 0),
           (r"\bfirst\b|\b1st\b|\blevel\s*1\b", 1), (r"\bsecond\b|\b2nd\b|\blevel\s*2\b", 2),
           (r"\bthird\b|\b3rd\b|\blevel\s*3\b", 3), (r"\bfourth\b|\b4th\b|\blevel\s*4\b", 4))


def level_rank(name):
    """Height order of a level name (basement -2 … ground 0 … first 1); None when the name doesn't say."""
    text = str(name or "").casefold()
    for pattern, rank in _LEVELS:
        if re.search(pattern, text):
            return rank
    return None


def wall_boundary(boundary, source, neighbour):
    """(boundary, note) for one wall of a cooled room in a house.

    neighbour: None, or {"label", "category", "scope"} of the traced room the wall runs along. A reviewer's choice
    stands; otherwise a wall to an indoor space that isn't cooled is "unconditioned", one to an outdoor space or to no
    room at all (an unclassified wall) faces outside, and one to a cooled room keeps its class (or becomes internal).
    """
    if source == "reviewer" or boundary not in {"unknown", "internal"}:
        return boundary, ""
    if neighbour and neighbour.get("category") in INDOOR_NOT_COOLED and neighbour.get("scope") not in COOLED_SCOPES:
        return "unconditioned", (f"Wall to the {neighbour.get('label') or 'next space'}, which isn't air-conditioned in a house "
                                 "(user decision 2026-10-30): counted against the space beyond at the buffer temperature.")
    if neighbour and neighbour.get("category") in OUTDOOR:
        return "external", f"Wall to the {neighbour.get('label') or 'outdoor space'}: an outdoor space, so the wall faces outside."
    if boundary == "unknown" and not neighbour:
        return "external", ("What this wall faces wasn't found on the drawings and no traced room is beyond it: "
                            "taken as an outside wall, the most likely case for a house (labelled assumption).")
    if boundary == "unknown":
        return "internal", ""
    return boundary, ""


def _polygon(points):
    from shapely.geometry import Polygon
    polygon = Polygon(points)
    return polygon if polygon.is_valid else polygon.buffer(0)


def _is_vertical_link(room):
    label = str(room.get("label", "")).casefold()
    return room.get("category") == "lift_shaft" or bool(re.search(r"\b(?:stairs?|staircase|lift)\b", label))


def stacked_offset(upper, lower, tolerance_mm=300):
    """(dx_mm, dy_mm, evidence) that moves the upper plan onto the lower one, from stairs or lifts traced on both
    levels (they stack), or None. upper/lower: [{"label", "category", "points_mm"}]."""
    pairs = []
    for top in (room for room in upper if _is_vertical_link(room)):
        top_shape = _polygon(top["points_mm"])
        family = "lift" if "lift" in str(top.get("label", "")).casefold() or top.get("category") == "lift_shaft" else "stair"
        for bottom in (room for room in lower if _is_vertical_link(room)):
            bottom_family = "lift" if "lift" in str(bottom.get("label", "")).casefold() or bottom.get("category") == "lift_shaft" else "stair"
            bottom_shape = _polygon(bottom["points_mm"])
            if family != bottom_family or not top_shape.area or not 0.67 <= bottom_shape.area / top_shape.area <= 1.5:
                continue
            pairs.append((bottom_shape.centroid.x - top_shape.centroid.x, bottom_shape.centroid.y - top_shape.centroid.y,
                          f"{top.get('label')} above {bottom.get('label')}"))
    if not pairs:
        return None
    dx, dy = median(row[0] for row in pairs), median(row[1] for row in pairs)
    agreeing = [row for row in pairs if math.hypot(row[0] - dx, row[1] - dy) <= tolerance_mm]
    if len(pairs) > 1 and len(agreeing) < 2:
        return None                       # several candidates that don't agree: no reliable match
    return dx, dy, "; ".join(row[2] for row in agreeing)


def floor_split(upper_points_mm, lower_rooms, min_area_m2=0.5):
    """What lies under an upper room's floor, as shares of its area.

    upper_points_mm is already moved into the lower plan's frame. lower_rooms: [{"label", "category", "scope",
    "points_mm"}]. Returns [{"below": "cooled" | "not_cooled" | "outdoor" | "open", "fraction", "rooms": [labels]}]
    largest first; "open" is floor area over nothing traced (an open space, or a room not traced).
    """
    top = _polygon(upper_points_mm)
    total = top.area
    if not total:
        return []
    shares = {}
    covered = None
    for room in lower_rooms:
        shape = _polygon(room["points_mm"])
        overlap = top.intersection(shape)
        if overlap.is_empty or overlap.area <= 0:
            continue
        kind = ("cooled" if room.get("scope") in COOLED_SCOPES else
                "outdoor" if room.get("category") in OUTDOOR else "not_cooled")
        row = shares.setdefault(kind, {"below": kind, "area": 0.0, "rooms": []})
        row["area"] += overlap.area
        row["rooms"].append(room.get("label", ""))
        covered = overlap if covered is None else covered.union(overlap)
    open_area = total - (covered.area if covered is not None else 0.0)
    if open_area > 0:
        shares["open"] = {"below": "open", "area": open_area, "rooms": []}
    rows = [row for row in shares.values() if row["area"] / 1e6 >= min_area_m2]
    kept = sum(row["area"] for row in rows) or total
    return sorted(({"below": row["below"], "fraction": row["area"] / kept, "rooms": sorted(set(row["rooms"]))} for row in rows),
                  key=lambda row: -row["fraction"])
