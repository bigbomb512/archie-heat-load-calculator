"""Deterministic draft seating evidence. No licensed seated-occupancy basis exists in the repo.

See docs/australian_ventilation_rules.md: seats are assumed design occupants, not an AS 1668.2 compliance claim.
"""
from collections import Counter
from copy import deepcopy
import math
import re

POLICY = "seating-v2-reviewed"
BASIS = "Assumption: one design occupant per seat; no reviewed seated-occupancy rule is available in the repository."


def printed_seating(text):
    """Read one seating schedule or total; return rejected-row diagnostics as well as totals.

    Small QTY/SEATS pairs can mean either row totals or places per table. In
    particular QTY 2 SEATS 4 cannot safely select between four and eight people.
    """
    text = str(text).upper()
    result = {"totals": [], "warnings": [], "kind": "none"}
    # Outdoor/mixed capacities cannot establish an indoor total.
    if re.search(r"\b(OUTDOOR|TERRACE|EXTERNAL|APPROX(?:IMATELY)?)\b", text):
        return result
    number = r"(\d{1,4})(?![\d.])"
    invalid_after = r"^\s*(?:MM\b|H\b|HIGH\b|HEIGHT\b|PER\b|SEATER\b)"
    def valid(match, nearby=True):
        return (0 < int(match.group(1)) <= 5000
                and not re.search(invalid_after, text[match.end():])
                and (not nearby or not re.search(r"\b(?:PER|SEATER)\b", text[max(0, match.start()-20):match.end()+20])))
    explicit_patterns = (r"\bTOTAL\s+SEATS\s*[:=]?\s*"+number,
                         r"\bSEATING\s+CAPACITY\s*[:=]?\s*"+number,
                         r"\b"+number+r"\s+SEATS\s+TOTAL\b", r"\bPAX\s*[:=]?\s*"+number)
    explicit = {int(m.group(1)) for pattern in explicit_patterns for m in re.finditer(pattern,text) if valid(m, nearby=False)}
    if explicit:
        return {**result, "totals": sorted(explicit), "kind": "explicit_total"}
    quantities = list(re.finditer(r"\b(?:QTY|QUANTITY)\s*[:=]?\s*(\d{1,4})\b",text))
    if quantities:
        rows = []
        for index, quantity in enumerate(quantities):
            end = quantities[index+1].start() if index+1<len(quantities) else len(text)
            segment = text[quantity.end():end]
            seats = re.search(r"\bSEATS\s*[:=]?\s*"+number,segment)
            q = int(quantity.group(1)); value = int(seats.group(1)) if seats else 0
            if not q or not seats or not value or re.search(invalid_after,segment[seats.end():]):
                result["warnings"].append("Seating schedule row has no unambiguous quantity and seat value.")
            elif value < q and value <= 12:
                rows.append(q*value)
            elif value >= q and value % q == 0 and 1 <= value/q <= 12 and (value>12 or q==1):
                rows.append(value)
            else:
                result["warnings"].append(f"Ambiguous seating schedule row QTY {q} SEATS {value}; not applied.")
        if len(re.findall(r"\bSEATING\s+SCHEDULE\b",text))>1:
            result["warnings"].append("Multiple seating schedules cannot be summed as one indoor total.")
        if not result["warnings"] and rows and sum(rows)<=5000:
            result.update(totals=[sum(rows)],kind="schedule_sum")
        return result
    # Bare totals are line-scoped: a table detail elsewhere on the page must not
    # turn its capacity into a tenancy total or suppress a legitimate total.
    patterns = (r"\bSEATS\s*[:=]?\s*"+number, r"\b"+number+r"\s+SEATS\b")
    bare = set()
    for line in text.splitlines():
        if re.search(r"\b(TABLE|PER|SEATER)\b",line):
            continue
        for pattern in patterns:
            for match in re.finditer(pattern,line):
                if 0<int(match.group(1))<=5000 and not re.search(invalid_after,line[match.end():]):
                    bare.add(int(match.group(1)))
    return {**result,"totals":sorted(bare),"kind":"printed_total" if bare else "none"}


def printed_totals(text):
    """Compatibility view of the reader; services also retain its warnings."""
    return printed_seating(text)["totals"]


def count_symbols(objects, rooms, mm_per_unit):
    """Repeated chair-sized glyphs, deduplicated across component paths; objects/rooms share one plan frame.

    Closed rectangles/curves or detailed U-shaped chair glyphs qualify. Circles/seat details within larger table
    outlines do not. This remains a labelled vector estimate, not a verified recognition of every furniture symbol.
    """
    from shapely.geometry import Polygon, Point, box
    if not mm_per_unit or not math.isfinite(mm_per_unit) or mm_per_unit <= 0:
        return {"counts": {}, "seats": [], "excluded": 0, "reason": "No calibrated plan scale; seats not counted."}
    tables, candidates, benches = [], [], []
    for obj in objects:
        bounds = obj.get("bbox", [])
        if len(bounds) != 4:
            continue
        width, height = (bounds[2]-bounds[0])*mm_per_unit, (bounds[3]-bounds[1])*mm_per_unit
        low, high = sorted((width, height))
        if obj.get("family") in {"rect", "curve"} and 450 <= low <= 1800 and 750 <= high <= 3500:
            tables.append(box(*bounds))
        if obj.get("bench") and 350 <= low <= 800 and high >= 1100:
            benches.append(obj)
        if 350 <= low <= 620 and high / low <= 1.65 and (obj.get("closed") or obj.get("chair_glyph")):
            family = (round(low/25), round(high/25), obj.get("family", "closed"))
            candidates.append((obj, family))
    repeated = Counter(family for _, family in candidates)
    outlines = [(room["id"], Polygon(room["points"])) for room in rooms if len(room.get("points", [])) >= 4]
    seats, counts, excluded = [], {}, 0
    for obj, family in sorted(candidates, key=lambda item: tuple(item[0]["bbox"])):
        if repeated[family] < 3:
            continue
        x0,y0,x1,y1 = obj["bbox"]
        point = Point((x0+x1)/2,(y0+y1)/2)
        if not obj.get("chair_glyph") and any(table.contains(point) for table in tables):
            continue
        # Full symbol containment avoids counting outdoor chairs whose centre is near a façade.
        hits = [(rid, poly.area) for rid,poly in outlines if poly.is_valid and poly.covers(box(x0,y0,x1,y1))]
        if not hits:
            excluded += 1
            continue
        room = min(hits, key=lambda item:(item[1],item[0]))[0]
        if any(math.hypot(point.x-s["x"],point.y-s["y"])*mm_per_unit < 220 for s in seats):
            continue
        seats.append({"room_id": room, "x": point.x, "y": point.y, "kind": "chair_or_stool", "family": family})
        counts[room] = counts.get(room,0)+1
    for obj in benches:
        shape = box(*obj["bbox"])
        hits = [(rid,poly.area) for rid,poly in outlines if poly.is_valid and poly.covers(shape)]
        if not hits:
            excluded += 1
            continue
        room = min(hits,key=lambda item:(item[1],item[0]))[0]
        length = max(shape.bounds[2]-shape.bounds[0],shape.bounds[3]-shape.bounds[1])*mm_per_unit
        count = math.floor(length/600)
        if any(abs(shape.centroid.x-s["x"])*mm_per_unit<100 and abs(shape.centroid.y-s["y"])*mm_per_unit<100 for s in seats):
            continue
        counts[room] = counts.get(room,0)+count
        seats.append({"room_id":room,"x":shape.centroid.x,"y":shape.centroid.y,"kind":"bench_estimate","count":count,"length_mm":length})
    return {"counts":counts,"seats":seats,"excluded":excluded,"reason":"Repeated vector furniture symbols; bench length / 600 mm where a bench is explicitly labelled."}


def allocate(rooms, drawings, totals):
    """Allocate one nonconflicting printed total per level, otherwise use the best per-floor drawing count.

    Zero recognised symbols means insufficient seating evidence, never proof of zero occupants.
    """
    output = {}
    for level in sorted({room.get("level", "") for room in rooms}):
        candidates = [room for room in rooms if room.get("level", "") == level and room.get("use") in {"dining","retail"}
                      and room.get("scope") in {"comfort_hvac","comfort_hvac_with_process_exception"}]
        counts = {room["id"]:drawings.get(room["id"],{}).get("count",0) for room in candidates}
        printed = [row for row in totals if row.get("level", "") == level and not row.get("room")]
        unique = sorted({row["total"] for row in printed})
        room_totals = {room["id"]: [row for row in totals if row.get("room") == room["id"] and row.get("level", "") == level] for room in candidates}
        explicit_counts = {key: next(iter(values)) for key, rows in room_totals.items()
                           if len(values := {row["total"] for row in rows}) == 1}
        unreserved = [room for room in candidates if room["id"] not in explicit_counts]
        active = [room for room in unreserved if counts[room["id"]] > 0] or unreserved
        eligible = [room for room in active if room.get("area_m2",0)>0 and room["id"] not in explicit_counts]
        measured = sum(counts.values())
        warning = f"Conflicting printed totals {unique}; no printed total applied." if len(unique)>1 else ""
        if len(unique)==1 and measured and abs(measured-unique[0])/unique[0]>.15:
            warning = f"Printed total {unique[0]} and counted seats {measured} disagree by more than 15%; printed total takes precedence."
        assigned = {}
        remaining = unique[0]-sum(explicit_counts.values()) if len(unique)==1 else 0
        if remaining < 0:
            warning = "Room-specific seat totals exceed the tenancy total; tenancy allocation not applied."
        if len(unique)==1 and eligible and remaining >= 0:
            weighted_count = sum(counts[room["id"]] for room in eligible)
            weights = {room["id"]: counts[room["id"]] if weighted_count else room["area_m2"] for room in eligible}
            denominator = sum(weights.values())
            quotas = {key:remaining*value/denominator for key,value in weights.items()}
            assigned = {key:math.floor(value) for key,value in quotas.items()}
            remainder = remaining-sum(assigned.values())
            for key in sorted(quotas,key=lambda key:(-(quotas[key]-assigned[key]),key))[:remainder]:
                assigned[key]+=1
        for room in candidates:
            key = room["id"]
            explicit = sorted({row["total"] for row in room_totals[key]})
            row = None
            if len(explicit)==1:
                row = {"seat_count":explicit[0],"origin":"direct_project_evidence","evidence":room_totals[key],"rationale":f"{explicit[0]} printed seats assigned explicitly to this room."}
            elif len(explicit)>1:
                continue
            elif key in assigned:
                pages = sorted({row["page"] for row in printed})
                method = "counted seats" if weighted_count else "room area (no reliable per-room seat count)"
                names = ", ".join(room["name"] for room in eligible)
                reservation = f" After reserving {sum(explicit_counts.values())} explicitly assigned room seats." if explicit_counts else ""
                row = {"seat_count":assigned[key],"origin":"direct_project_evidence","evidence":printed,
                       "rationale":f"{unique[0]} seats from the seating schedule on pages {pages}, shared across {names} by {method}: {assigned[key]} seats here.{reservation}"}
            elif counts.get(key,0)>0:
                row = {"seat_count":counts[key],"origin":"project_evidence","evidence":deepcopy(drawings[key].get("evidence",[])),
                       "rationale":f"{counts[key]} drawn seats counted inside this room; vector/bench estimate."}
            if row:
                row.update(warning=warning,basis=BASIS,drawn_count=counts.get(key,0))
                output[key]=row
    return output
