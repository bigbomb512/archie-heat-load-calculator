"""Pass 1 of the PDF review: what is on each page, read by an AI model one page at a time.

Every page is sent as its own full-size image (a whole set drawn into one image shrinks the small print);
a few pages can share a call, each still read on its own. The model says what kind of page it is and lists the heat-load information
visible on it, with a short piece of evidence for each item.

This module holds the prompt, the checks on a reply, and how a set's page readings become
page roles (main floor plan per level, ceiling plans, pages to keep). No I/O.
"""

import re

PROMPT_VERSION = "page-inventory-v1"

PAGE_TYPES = (
    "floor_plan", "reflected_ceiling_plan", "services_plan", "elevation", "section", "detail",
    "schedule", "notes_or_specification", "site_or_location_plan", "cover_or_drawing_list",
    "render_or_photo", "other",
)
FLOOR_PLAN_KINDS = (
    "proposed_layout", "dimension_setout", "existing_or_demolition", "furniture_or_equipment",
    "finishes", "partial_or_enlarged", "outdoor_or_context", "other",
)
INFORMATION_KINDS = (
    "room_geometry", "ceiling_height", "materials_construction", "windows_glazing", "equipment_appliances",
    "lighting", "people_occupancy", "operating_hours", "airflow_ventilation", "hvac_plant", "location_orientation",
)
# Floor plans that show the proposed tenancy as a whole: candidates for the main floor plan.
MAIN_PLAN_KINDS = {"proposed_layout", "dimension_setout"}
# Pages thrown away only when they also show no heat-load information at all.
DISCARDABLE_TYPES = {"render_or_photo", "cover_or_drawing_list", "other"}

_SHAPE = """{"page_type": "...", "floor_plan_kind": "...", "whole_floor": true, "title": "...", "drawing_number": "...", "level": "...",
 "information": [{"kind": "...", "what": "...", "evidence": "..."}]}"""
_RULES = f"""page_type: one of {", ".join(PAGE_TYPES)}.
  floor_plan = a top-down plan of floor areas (layout, dimension/set-out, existing, furniture, finishes, equipment plans).
  reflected_ceiling_plan = ceiling plan (RCP). services_plan = mechanical, electrical, lighting, hydraulic or fire plan by a services consultant.
  schedule = a page that is mainly a table (finishes, equipment, door/window, lighting schedules).
floor_plan_kind: only for a floor_plan, else "". One of {", ".join(FLOOR_PLAN_KINDS)}.
  proposed_layout = the proposed general arrangement / layout / floor plan of the tenancy. dimension_setout = the proposed dimension or set-out plan.
whole_floor: for a floor_plan, true when it shows the whole tenancy floor (or the whole of one level), false for a part, an enlarged area or a context view.
title: the printed drawing title in the title block or under the view. drawing_number: the sheet number. level: the floor level shown
  (e.g. "Ground", "Level 1", "Mezzanine"), "" when not stated. Copy printed words; don't invent.
information: every item of heat-load information visible on THIS page. kind is one of {", ".join(INFORMATION_KINDS)}.
  what = the information itself with its numbers and units (e.g. "Shopfront 11,900 mm wide", "75 inch TV on wall", "6.38 mm clear laminated glass").
  evidence = a short quote or where on the page it is. List nothing that isn't visible here. An empty list is a valid answer.
"""
PROMPT = ("You are reading ONE page of an architectural drawing set for a commercial HVAC cooling heat-load calculation.\n"
          f"Reply with JSON only (no prose, no code fence), exactly this shape:\n{_SHAPE}\n\n{_RULES}")


def batch_prompt(pages):
    """Several pages read in one call: each attached image is one whole page at full size, read on its own. One call
    instead of one per page saves the fixed cost of a call (about 4,600 tokens) for every page after the first."""
    listing = ", ".join(f"image {index} is page {page}" for index, page in enumerate(pages, 1))
    return (f"You are reading {len(pages)} pages of an architectural drawing set for a commercial HVAC cooling heat-load "
            f"calculation, one image per page: {listing}. Read each page on its own, as if it were the only page.\n"
            "Reply with JSON only (no prose, no code fence), exactly this shape, with one reading per page keyed by its "
            f'page number:\n{{"pages": {{"{pages[0]}": READING, ...}}}}\nwhere each READING is exactly:\n{_SHAPE}\n\n{_RULES}')


def build_prompt():
    return PROMPT


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def validate_reply(reply):
    """Check and clean one page's reply. Raises ValueError with the reason when it doesn't fit."""
    if not isinstance(reply, dict):
        raise ValueError("The reply is not a JSON object.")
    page_type = _text(reply.get("page_type"), 40).lower()
    if page_type not in PAGE_TYPES:
        raise ValueError(f"Unknown page_type {page_type!r}.")
    kind = _text(reply.get("floor_plan_kind"), 40).lower() if page_type == "floor_plan" else ""
    if page_type == "floor_plan" and kind not in FLOOR_PLAN_KINDS:
        raise ValueError(f"Unknown floor_plan_kind {kind!r}.")
    information = reply.get("information", [])
    if not isinstance(information, list):
        raise ValueError("information must be a list.")
    items = []
    for row in information:
        if not isinstance(row, dict):
            raise ValueError("Each information item must be an object.")
        info_kind = _text(row.get("kind"), 40).lower()
        if info_kind not in INFORMATION_KINDS:
            raise ValueError(f"Unknown information kind {info_kind!r}.")
        what = _text(row.get("what"), 400)
        if not what:
            raise ValueError("An information item has no 'what'.")
        items.append({"kind": info_kind, "what": what, "evidence": _text(row.get("evidence"), 400)})
    return {
        "page_type": page_type, "floor_plan_kind": kind,
        "whole_floor": bool(reply.get("whole_floor")) if page_type == "floor_plan" else False,
        "title": _text(reply.get("title"), 160), "drawing_number": _text(reply.get("drawing_number"), 40),
        "level": _text(reply.get("level"), 40), "information": items,
    }


def level_key(level):
    """Group spellings of one level together: "Ground Floor", "GF" and "ground" are one level."""
    text = re.sub(r"[^a-z0-9 ]", " ", str(level or "").lower())
    text = re.sub(r"\b(floor|level|plan|lvl)\b", " ", text)
    text = " ".join(text.split())
    if text in {"", "gf", "g", "ground", "00", "0"}:
        return "ground" if text else ""
    if text in {"mezz", "mezzanine", "mz"}:
        return "mezzanine"
    match = re.fullmatch(r"(?:l)?0*(\d+)", text)
    return f"level {match.group(1)}" if match else text


def main_plan_pages(readings):
    """Main floor plans per level: proposed whole-floor plans; an existing plan only when no proposed one exists.

    readings = {page: validated reading}. Returns {level key: [pages]}.
    """
    floors = {page: row for page, row in readings.items() if row and row.get("page_type") == "floor_plan" and row.get("whole_floor")}
    proposed = {page: row for page, row in floors.items() if row.get("floor_plan_kind") in MAIN_PLAN_KINDS}
    chosen = proposed or {page: row for page, row in floors.items() if row.get("floor_plan_kind") == "existing_or_demolition"}
    levels = {}
    for page, row in sorted(chosen.items()):
        levels.setdefault(level_key(row.get("level")), []).append(page)
    return levels


def packet_from_readings(readings, page_count):
    """Page roles in the analysis-packet shape the page-role scorecard reads.

    readings = {page: validated reading, or None when the page couldn't be read}. An unread page is
    kept, never thrown away.
    """
    main = {page for pages in main_plan_pages(readings).values() for page in pages}
    primary, reference, kept, discarded = [], [], [], []
    for page in range(1, page_count + 1):
        row = readings.get(page)
        if not row:
            kept.append({"page": page, "type": "unread", "plan_role": None, "title": ""})
            continue
        base = {"page": page, "title": row["title"], "level": row["level"],
                "information_kinds": sorted({item["kind"] for item in row["information"]})}
        if page in main:
            primary.append({**base, "type": "floor_plan", "plan_role": "main_floor_plan"})
        elif row["page_type"] == "floor_plan":
            primary.append({**base, "type": "floor_plan", "plan_role": row["floor_plan_kind"] or "floor_plan"})
        elif row["page_type"] == "reflected_ceiling_plan":
            primary.append({**base, "type": "reflected_ceiling_plan", "plan_role": "reflected_ceiling_plan"})
        elif row["page_type"] in DISCARDABLE_TYPES and not row["information"]:
            discarded.append({**base, "type": row["page_type"], "plan_role": None})
        else:
            reference.append({**base, "type": row["page_type"], "plan_role": "reference_context"})
    return {"primary_pages": primary, "reference_pages": reference, "kept_pages": kept, "discarded_pages": discarded}
