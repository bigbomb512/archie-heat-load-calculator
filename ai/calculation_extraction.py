"""PDF-derived calculator-input evidence.

This module extracts *evidence candidates*, not engineering assumptions.  It is
deliberately separate from the hourly calculator: a candidate is only usable
when its page, units, target, and witness relationships pass validation.
"""

from copy import deepcopy
import hashlib
import json
import math
import re

from ai.drawing_coverage import timestamp
from ai.evidence_binding import bind_calculation_evidence
from ai.geometry_resolution import build_geometry_resolution


SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "calculation-input-v2"
DAY_TYPES = ("weekday", "saturday", "sunday", "holiday")
SUPPORTED_UNITS = {"mm", "m", "m2", "m²", "m3", "m³", "m3/h", "m³/h", "l/s", "w", "w/m2", "w/m²", "°c", "c", "pa", "ach", "people", "w/m2k", "count", "w/m²k"}
ROLE_GROUPS = {
    "plan": {"floor_plan", "main_floor_plan", "supporting_geometry_plan", "primary_geometry_plan"},
    "ceiling_service": {"reflected_ceiling_plan", "reflected_ceiling_or_service_plan", "services_or_lighting_plan", "architect_lighting_plan", "architect_electrical_plan"},
    "opening": {"opening_elevation", "opening_schedule", "elevation_or_section", "elevation", "section"},
    "schedule": {"schedule", "material_schedule", "equipment_schedule", "lighting_schedule", "opening_schedule"},
    "reference": {"3d_render", "3d_reference", "reference", "legend_or_general_notes", "construction_or_detail", "detail"},
}

AREA_RE = re.compile(r"(?P<label>[A-Za-z][A-Za-z0-9 /_-]{1,50})\s*(?:area|floor area|net area)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m(?:2|²))\b", re.I)
DIM_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?P<ua>mm|m)?\s*(?:x|×|by)\s*(?P<b>\d+(?:\.\d+)?)\s*(?P<ub>mm|m)?\b", re.I)
HEIGHT_RE = re.compile(r"(?:ceiling|bulkhead|floor\s*to\s*floor|head\s*height|height)[^0-9\n]{0,30}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m)\b", re.I)
LIGHT_RE = re.compile(r"(?P<watts>\d+(?:\.\d+)?)\s*W\b[^\n]{0,160}?\b(?:qty|quantity)\s*[:.]?\s*(?P<qty>\d+(?:\.\d+)?)", re.I)
OCCUPANCY_RE = re.compile(r"(?:occupancy|people|persons)[^0-9\n]{0,30}(?P<value>\d+(?:\.\d+)?)\b", re.I)
OA_RE = re.compile(r"(?:outside\s*air|fresh\s*air|ventilation)[^0-9\n]{0,30}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>L/s|m3/h|m³/h|ACH)\b", re.I)
TEMP_RE = re.compile(r"(?:cooling|indoor|room|setpoint)[^0-9\n]{0,35}(?P<value>\d+(?:\.\d+)?)\s*°?C\b", re.I)
U_RE = re.compile(r"\bU\s*[- ]?value[^0-9\n]{0,15}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>W/m(?:2|²)K)\b", re.I)
OPENING_TAG_RE = re.compile(r"\b(?P<tag>[WD]\d{1,3}[A-Z]?)\b", re.I)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _canonical_source_fingerprint(ai_input):
    """Keep candidate identities stable when the packet register is reordered."""
    return _fingerprint(_canonical_evidence({
        key: (ai_input or {}).get(key)
        for key in ("source_pdf", "source_fingerprint", "drawing_set", "confirmed_pages", "page_triage")
    }))


def _canonical_evidence(value, key=""):
    """Sort evidence collections, preserving ordered geometry and chains."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _canonical_evidence(v, k) for k, v in value.items()}
    if isinstance(value, list):
        items = [_canonical_evidence(v) for v in value]
        if key in {"pages", "page_roles", "confirmed_pages", "page_triage", "entities",
                   "records", "word_samples", "room_label_candidates", "table_cells",
                   "tables", "image_witnesses", "witnesses", "dimension_candidates",
                   "line_candidates", "curve_candidates", "opening_candidates"}:
            return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
        return items
    return value


def _stable_id(source_fp, page, drawing, label, location, target):
    return "calc_" + _fingerprint([source_fp, page, drawing, label, location, target])[:24]


def _number(raw):
    value = float(raw)
    return int(value) if value.is_integer() else value


def _unit(unit):
    value = str(unit or "").strip().lower().replace("²", "2").replace("³", "3")
    if value == "c":
        return "°C"
    if value == "l/s":
        return "L/s"
    if value in {"w/m2k", "w/m2 k"}:
        return "W/m²K"
    return value


def _page_text(page):
    structured = page.get("structured_content") or {}
    return str(page.get("extraction_text") or structured.get("markdown") or structured.get("text") or page.get("text") or "")


def _role(page, coverage):
    row = next((item for item in coverage.get("page_roles", []) if item.get("page") == page.get("page")), {})
    return row.get("proposed_role") or page.get("sheet_classification") or page.get("detected_type") or "other"


def _candidate(source_fp, page, target, category, value, unit, *, label="", excerpt="", method="structured_pdf", status="proposed", confidence="medium", witnesses=None, unresolved=None, room_id="", derivation=None, evidence_only=False, location=None):
    drawing = page.get("drawing_number", "")
    location = deepcopy(location or {"excerpt": excerpt})
    location["value_witness"] = {"value": value, "unit": unit}
    # Character offsets are textual coordinates, never drawing scale/geometry.
    text = _page_text(page)
    offsets = [m.start() for m in re.finditer(re.escape(excerpt), text)] if excerpt else []
    coordinates = location.get("coordinates") or location.get("bbox")
    if not coordinates and offsets:
        coordinates = {"space": "page_text", "ranges": [[start, start + len(excerpt)] for start in offsets]}
    return {
        "candidate_id": _stable_id(source_fp, page.get("page"), drawing, label, location, target),
        "target": target, "category": category, "label": label, "value": value, "raw_value": value, "unit": unit,
        "affected_id": room_id or page.get("level_name", ""), "room_id": room_id,
        "status": "evidence_only" if evidence_only else status, "extraction_method": method,
        "confidence": confidence, "source_fingerprint": source_fp,
        "source": {"page": page.get("page"), "drawing_number": drawing, "excerpt": excerpt, "kind": "architect_pdf", "location": deepcopy(location), "coordinates": coordinates, "vector_id": location.get("vector_id")},
        "witness_ids": list(witnesses or []), "unresolved_fields": list(unresolved or []),
        "competing_candidates": [], "derivation": deepcopy(derivation or {}),
    }


def _room_label(page, excerpt):
    labels = [str(item.get("text", "")).strip() for item in page.get("room_label_candidates", []) if isinstance(item, dict)]
    matches = [label for label in labels if label and re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", excerpt, re.I)]
    if len(set(matches)) == 1:
        return matches[0]
    # A room declaration is explicit; room-name vocabulary is not allocation.
    declared = re.match(r"\s*Room\s+([^:;|]+)[:;|]", excerpt, re.I)
    return declared.group(1).strip() if declared and not matches else ""


def _extract_plan(page, source_fp, candidates):
    text = _page_text(page)
    for item in page.get("room_label_candidates", []):
        label = str(item.get("text") or "").strip()
        if label:
            candidates.append(_candidate(source_fp, page, f"room.{label}.identity", "room", label, "",
                label=label, room_id=label, excerpt=label, location={"coordinates": item.get("bbox", [])},
                method="explicit_room_label", unresolved=[] if item.get("bbox") else ["source_location"]))
    for match in AREA_RE.finditer(text):
        label = match.group("label").strip()
        candidates.append(_candidate(source_fp, page, f"room.{label}.area_m2", "area", _number(match.group("value")), "m²", label=label, excerpt=_line_at(text, match.start()), method="explicit_room_area", status="active", confidence="high", room_id=label))
    for match in DIM_RE.finditer(text):
        context = _line_at(text, match.start())
        label = _room_label(page, context)
        unit = "mm" if match.group("ub") else ""
        values = {key: _number(match.group(key)) * (1000 if (match.group("u" + key) or match.group("ub") or "").lower() == "m" else 1) for key in ("a", "b")}
        unresolved = ([] if unit else ["unit"]) + ([] if label else ["room_allocation"])
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.dimension", "dimension", values if unit else None, unit, label=label or "unresolved dimension", excerpt=_line_at(text, match.start()), method="plan_dimension_pair", status="proposed" if unit else "blocked", confidence="medium", room_id=label, unresolved=unresolved))
    for match in re.finditer(r"\b(external wall|outside|street frontage|shopfront|adjacent|adjoining)\b", text, re.I):
        excerpt = _line_at(text, match.start())
        label = _room_label(page, excerpt)
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.boundary", "boundary", match.group(1).lower(), "", label=label, excerpt=excerpt, method="explicit_boundary_text", status="proposed", room_id=label, unresolved=[] if label else ["room_allocation"]))


def _extract_ceiling_service(page, source_fp, candidates):
    text = _page_text(page)
    for line in text.splitlines():
        label = _room_label(page, line)
        for pattern, category in ((r"ceiling type\s*[:=]\s*([^;|]+)", "ceiling_type"),
                                  (r"fixture\s+([A-Za-z][A-Za-z0-9_-]*)\s+(?:qty|quantity)\s*[:=]?\s*(\d+)", "fixture_tag")):
            match = re.search(pattern, line, re.I)
            if match:
                value = {"tag": match[1], "quantity": int(match[2])} if category == "fixture_tag" else match[1].strip()
                candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.{category}", category,
                    value, "", label=label, room_id=label, excerpt=line,
                    method="explicit_service_text", unresolved=[] if label else ["room_allocation"]))
    for match in HEIGHT_RE.finditer(text):
        if not re.match(r"ceiling\b", match.group(0), re.I):
            continue
        label = _room_label(page, _line_at(text, match.start()))
        unit = _unit(match.group("unit"))
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.ceiling_height", "ceiling_height", _number(match.group("value")), unit, label=label, excerpt=_line_at(text, match.start()), method="ceiling_height_text", status="active" if label else "proposed", confidence="high" if label else "medium", room_id=label, unresolved=[] if label else ["room_allocation"]))
    for match in LIGHT_RE.finditer(text):
        qty, watts = _number(match.group("qty")), _number(match.group("watts"))
        label = _room_label(page, _line_at(text, match.start()))
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.lighting.connected_w", "lighting", qty * watts, "W", label=label, excerpt=_line_at(text, match.start()), method="lighting_quantity_wattage", status="active" if label else "proposed", confidence="high", room_id=label, unresolved=[] if label else ["room_allocation"], derivation={"formula": "quantity × watts_each", "operands": {"quantity": qty, "watts_each": watts}}))


def _line_at(text, offset):
    return text[text.rfind("\n", 0, offset) + 1:text.find("\n", offset) if "\n" in text[offset:] else len(text)].strip()


def _extract_openings(page, source_fp, candidates):
    text = _page_text(page)
    for match in OPENING_TAG_RE.finditer(text):
        tag = match.group("tag").upper()
        excerpt = _line_at(text, match.start())
        pair = DIM_RE.search(excerpt) if len(OPENING_TAG_RE.findall(excerpt)) == 1 else None
        value, unresolved = {"tag": tag}, []
        if pair:
            ua, ub = pair.group("ua"), pair.group("ub")
            # A trailing unit applies to the pair; mixed explicit units stay independent.
            if ub:
                value.update(width_mm=_number(pair.group("a")) * (1000 if (ua or ub).lower() == "m" else 1),
                             height_mm=_number(pair.group("b")) * (1000 if ub.lower() == "m" else 1))
            else:
                unresolved.append("unit")
        else:
            unresolved.append("dimensions")
        for field in ("sill", "head"):
            level = re.search(r"\b" + field + r"\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*(mm|m)\b", excerpt, re.I)
            if level and len(OPENING_TAG_RE.findall(excerpt)) == 1:
                value[field + "_mm"] = float(level[1]) * (1000 if level[2].lower() == "m" else 1)
        candidates.append(_candidate(source_fp, page, f"opening.{tag}", "opening", value,
            "mm" if len(value) > 1 else "", label=tag, excerpt=excerpt,
            method="opening_tag_and_dimension", unresolved=unresolved,
            status="blocked" if "unit" in unresolved else "proposed"))


def _extract_schedule_and_notes(page, source_fp, candidates):
    text = _page_text(page)
    for regex, category, unit, method in ((OCCUPANCY_RE, "occupancy", "people", "explicit_occupancy"), (OA_RE, "outside_air", "", "explicit_outside_air"), (TEMP_RE, "setpoint", "°C", "explicit_setpoint"), (U_RE, "u_value", "W/m²K", "explicit_u_value")):
        for match in regex.finditer(text):
            label = _room_label(page, _line_at(text, match.start()))
            candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.{category}", category, _number(match.group("value")), _unit(match.group("unit")) if match.groupdict().get("unit") else unit, excerpt=_line_at(text, match.start()), method=method, status="proposed", confidence="high", room_id=label, label=label, unresolved=[] if label else ["room_allocation"]))
    for match in re.finditer(r"\b(weekday|weekdays|saturday|sunday|holiday)\b[^\n]{0,100}?\b(\d{1,2}:\d{2})\s*(?:-|to)\s*(\d{1,2}:\d{2})", text, re.I):
        label = _room_label(page, _line_at(text, match.start()))
        day = match[1].lower().replace("weekdays", "weekday")
        unresolved = ["hourly_profile"] + ([] if label else ["room_allocation"])
        if not _valid_hours({day: [match[2], match[3]]}):
            unresolved.append("operating_hours_format")
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.schedule.{day}", "schedule",
            {"day_type": day, "start": match[2], "end": match[3]}, "time", label=label, room_id=label,
            excerpt=_line_at(text, match.start()), method="operating_hours_text", status="proposed",
            confidence="medium", unresolved=unresolved))
    for line in text.splitlines():
        label = _room_label(page, line)
        for token, field in (("construction", "construction"), ("glazing reference", "glazing_reference")):
            match = re.search(r"\b" + token + r"\s*[:=]\s*([^;|]+)", line, re.I)
            if match:
                candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.{field}", field,
                    match[1].strip(), "", label=label, room_id=label, excerpt=line,
                    method="explicit_reference_text", unresolved=[] if label else ["room_allocation"]))


def _extract_equipment(page, source_fp, candidates):
    text = _page_text(page)
    for line in text.splitlines():
        explicit = re.match(r"\s*Equipment\s+([\w-]+)\s*:\s*([^;|]+)", line, re.I)
        if explicit:
            tag, name = explicit.groups()
            records = []
            fields = {"name": (name.strip(), "")}
            for field, pattern in {
                "quantity": r"(?:qty|quantity)\s*[:=]?\s*(\d+)",
                "model": r"model\s*[:=]?\s*([^;|]+)",
                "location": r"location\s*[:=]?\s*([^;|]+)",
                "nameplate_power": r"nameplate(?: power)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(kW|W)\b",
                "heat_to_space": r"heat[- ]to[- ]space\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(kW|W)\b",
            }.items():
                match = re.search(pattern, line, re.I)
                if match:
                    fields[field] = (int(match[1]), "count") if field == "quantity" else (float(match[1]), match[2]) if field in {"nameplate_power", "heat_to_space"} else (match[1].strip(), "")
            for field, (value, unit) in fields.items():
                records.append({"entity": "equipment", "label": tag, "field": field, "value": value,
                    "unit": unit, "excerpt": line, "room_id": fields.get("location", ("", ""))[0],
                    "coordinates": {"space": "page_text", "excerpt": line},
                    "heat_basis": "explicit heat-to-space statement" if field == "heat_to_space" else "",
                    "extraction_method": "explicit_equipment_row"})
            _extract_records({**page, "structured_content": {"records": records}, "normalized_table_cells": []}, source_fp, candidates)
            continue
        # Presence alone is retained without inventing quantity or heat gain.
        for match in re.finditer(r"\b(display fridge|fridge|freezer|oven|cooktop|range|grill|fryer|dishwasher|ice maker|coffee machine)\b", line, re.I):
            word = match[0].lower()
            powers = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(kW|W)\b", line, re.I))
            power = powers[0] if len(powers) == 1 else None
            rated_power = float(power[1]) * (1000 if power[2].lower() == "kw" else 1) if power else None
            candidates.append(_candidate(source_fp, page, f"equipment.{word}", "equipment",
                {"name": word, "quantity": None, "rated_power": rated_power, "rated_power_unit": "W" if power else ""}, "W" if power else "",
                label=word, excerpt=line, method="equipment_presence_text", evidence_only=True,
                unresolved=["quantity", "model", "equipment_power_allocation", "room_allocation", "heat_to_space_basis"] + ([] if power else ["nameplate_power"])))


def _vision_candidates(vision, source_fp, pages_by_number):
    rows = []
    extraction = (vision or {}).get("result", {}).get("auto_extraction", {})
    for entity in extraction.get("entities", []):
        page = pages_by_number.get(entity.get("page"))
        if not page:
            continue
        kind = entity.get("kind")
        label = entity.get("label", "")
        witnesses = [str(item.get("reference")) for item in entity.get("witnesses", []) if isinstance(item, dict)]
        unresolved = list(entity.get("unresolved_fields", []))
        auto_activate = bool(entity.get("auto_activate"))
        if kind == "floor":
            rows.append(_candidate(
                source_fp, page, f"floor.{label}.identity", "floor", label,
                "", label=label, excerpt=entity.get("excerpt", ""),
                method="manual_vision_response", status="active" if auto_activate else "proposed",
                confidence=entity.get("confidence", "medium"), witnesses=witnesses,
                unresolved=unresolved or ["floor_to_zone_mapping"],
            ))
        if kind == "room":
            rows.append(_candidate(
                source_fp, page, f"room.{label}.identity", "room", label,
                "", label=label, excerpt=entity.get("excerpt", ""),
                method="manual_vision_response", status="active" if auto_activate else "proposed",
                confidence=entity.get("confidence", "medium"), witnesses=witnesses,
                room_id=label, unresolved=unresolved or ["geometry", "area_m2", "zone_id"],
            ))
        if kind == "room" and entity.get("area_m2") is not None:
            rows.append(_candidate(source_fp, page, f"room.{label}.area_m2", "area", entity["area_m2"], "m²", label=label, excerpt=entity.get("excerpt", ""), method="manual_vision_response", status="active" if entity.get("auto_activate") else "proposed", confidence=entity.get("confidence", "medium"), witnesses=[str(item.get("reference")) for item in entity.get("witnesses", [])], room_id=label, unresolved=entity.get("unresolved_fields", [])))
        if kind == "room" and entity.get("ceiling_height_mm") is not None:
            rows.append(_candidate(source_fp, page, f"room.{label}.ceiling_height", "ceiling_height", entity["ceiling_height_mm"], "mm", label=label, excerpt=entity.get("excerpt", ""), method="manual_vision_response", status="active" if entity.get("auto_activate") else "proposed", confidence=entity.get("confidence", "medium"), witnesses=[str(item.get("reference")) for item in entity.get("witnesses", [])], room_id=label, unresolved=entity.get("unresolved_fields", [])))
        if kind in {"opening", "surface"}:
            target = f"{kind}.{label or entity.get('opening_tag') or 'unresolved'}"
            value = {key: entity.get(key) for key in ("opening_tag", "surface_kind", "boundary_reference", "orientation", "width_mm", "height_mm") if entity.get(key) not in (None, "")}
            rows.append(_candidate(
                source_fp, page, target, kind, value or label, "mm" if any(key in value for key in ("width_mm", "height_mm")) else "",
                label=label, excerpt=entity.get("excerpt", ""), method="manual_vision_response",
                status="active" if auto_activate else "proposed", confidence=entity.get("confidence", "medium"),
                witnesses=witnesses, unresolved=unresolved or (["unique_plan_target"] if kind == "opening" else ["boundary_method", "construction_u_value"]),
            ))
    return rows


# Normalized facts supplied by PDF/OCR/table readers. Each record represents a
# single field and carries its own location, never an array-position identity.
FIELD_UNITS = {
    "area": {"m2": ("m²", 1)},
    "dimension": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "dimension_chain": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "ceiling_height": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "floor_to_floor_height": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "width": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "height": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "sill": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "head": {"mm": ("mm", 1), "m": ("mm", 1000)},
    "occupancy": {"people": ("people", 1), "persons": ("people", 1)},
    "setpoint": {"°c": ("°C", 1), "c": ("°C", 1)},
    "outside_air": {"l/s": ("L/s", 1), "m3/h": ("L/s", 1 / 3.6), "ach": ("ach", 1)},
    "u_value": {"w/m2k": ("W/m²K", 1)},
    "nameplate_power": {"w": ("W", 1), "kw": ("W", 1000)},
    "heat_to_space": {"w": ("W", 1), "kw": ("W", 1000)},
    "quantity": {"count": ("count", 1)},
}
TEXT_FIELDS = {"identity", "ceiling_type", "fixture_tag", "name", "model", "location",
               "construction", "glazing_reference", "operating_hours", "wall",
               "partition", "room_surface", "room_service", "vertical_boundary"}


def _valid_hours(value):
    if not isinstance(value, dict) or not value:
        return False
    for day, interval in value.items():
        if day not in DAY_TYPES or not isinstance(interval, list) or len(interval) != 2:
            return False
        if not all(isinstance(t, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in interval):
            return False
    return True


def _extract_records(page, source_fp, candidates):
    records = list((page.get("structured_content") or {}).get("records", [])) + page.get("normalized_table_cells", [])
    for record in records:
        field = record.get("field", "")
        entity = record.get("entity", "room")
        label = str(record.get("label") or record.get("tag") or "")
        room = str(record.get("room_id") or (label if entity == "room" else ""))
        raw = deepcopy(record.get("value"))
        value, unit = deepcopy(raw), str(record.get("unit") or "")
        unresolved = list(record.get("unresolved_fields") or [])
        location = {k: deepcopy(record[k]) for k in ("coordinates", "bbox", "table_id", "row", "column", "vector_id") if k in record}
        excerpt = str(record.get("excerpt") or "")
        if not location:
            unresolved.append("source_location")
        if not excerpt and not {"table_id", "vector_id"}.intersection(location):
            unresolved.append("source_excerpt")
        if not label:
            unresolved.append("entity_allocation")
        if entity == "room" and not room:
            unresolved.append("room_allocation")
        if field in FIELD_UNITS:
            normalized = unit.strip().lower().replace("²", "2").replace("³", "3").replace(" ", "")
            conversion = FIELD_UNITS[field].get(normalized)
            values = raw if field == "dimension_chain" and isinstance(raw, list) else [raw]
            if not conversion:
                unresolved.append("unit" if not unit else "unsupported_unit")
                value = None
            elif not values or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                unresolved.append("numeric_value")
                value = None
            else:
                unit, factor = conversion
                value = [v * factor for v in values] if field == "dimension_chain" else raw * factor
        elif field not in TEXT_FIELDS:
            unresolved.append("unsupported_field")
            value = None
        if value is not None and field in FIELD_UNITS:
            numeric_values = value if isinstance(value, list) else [value]
            strictly_positive = field in {"area", "dimension", "dimension_chain", "ceiling_height", "floor_to_floor_height", "width", "height", "u_value"}
            if any((v <= 0 if strictly_positive else v < 0) for v in numeric_values) and field not in {"setpoint", "sill", "head"}:
                unresolved.append("positive_value")
                value = None
            elif field in {"quantity", "occupancy"} and any(v != int(v) for v in numeric_values):
                unresolved.append("integer_count")
                value = None
        if field in TEXT_FIELDS and raw in (None, "", {}, []):
            unresolved.append("explicit_value")
            value = None
        if field == "operating_hours" and not _valid_hours(raw):
            unresolved.append("operating_hours_format")
            value = None
        if field == "heat_to_space" and not record.get("heat_basis"):
            unresolved.append("heat_to_space_basis")
            value = None
        if field in {"wall", "partition", "room_surface", "room_service", "vertical_boundary"}:
            if not isinstance(raw, dict) or not raw.get("from") or not raw.get("to"):
                unresolved.append("relationship_endpoints")
        category = "equipment" if entity == "equipment" else field
        target_field = "area_m2" if field == "area" else field
        candidate = _candidate(source_fp, page, f"{entity}.{label or 'unresolved'}.{target_field}",
            category, value, unit, label=label, room_id=room, excerpt=excerpt,
            location=location or {"excerpt": excerpt}, method=record.get("extraction_method", "normalized_pdf_record"),
            confidence=record.get("confidence", "medium"), unresolved=unresolved,
            witnesses=record.get("witness_ids", []) + ([record["vector_id"]] if record.get("vector_id") else []),
            evidence_only=entity == "equipment", status="blocked" if unresolved else "proposed")
        candidate.update(raw_value=raw, field=field, label=label, entity=entity)
        candidate["candidate_id"] = _stable_id(source_fp, page.get("page"), page.get("drawing_number", ""), label, {"location": location, "raw_value": raw, "raw_unit": record.get("unit", "")}, candidate["target"])
        candidate["derivation"] = {"explicit_unit": record.get("unit", ""), "heat_basis": record.get("heat_basis", "")}
        if entity == "equipment" and not room:
            candidate["unresolved_fields"].append("room_allocation")
        if entity == "equipment" and field != "heat_to_space":
            candidate["unresolved_fields"].append("heat_to_space_basis")
        candidates.append(candidate)


def _extract_unresolved_text(page, source_fp, candidates):
    fields = {"area": "area", "ceiling": "ceiling_height", "outside air": "outside_air",
              "setpoint": "setpoint", "U-value": "u_value"}
    for line in _page_text(page).splitlines():
        for token, field in fields.items():
            match = re.search(r"\b" + re.escape(token) + r"\s*(?:height)?\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*([^;|]*)", line, re.I)
            if not match or any(r["source"]["page"] == page.get("page") and r["category"] == field and r["source"]["excerpt"] == line.strip() for r in candidates):
                continue
            label = _room_label(page, line)
            candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.{field}", field,
                None, "", label=label, room_id=label, excerpt=line, method="unresolved_explicit_text",
                status="blocked", unresolved=["unit"] + ([] if label else ["room_allocation"])))
            candidates[-1]["raw_value"] = match[0]


def _extract_vertical(page, source_fp, candidates):
    text = _page_text(page)
    for match in re.finditer(r"floor[- ]to[- ]floor\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|m)\b", _page_text(page), re.I):
        candidates.append(_candidate(source_fp, page, "level.unresolved.floor_to_floor_height",
            "floor_to_floor_height", float(match[1]), match[2].lower(), excerpt=match[0],
            unresolved=["level_allocation"], method="explicit_vertical_dimension"))
    # Only the ceiling keyword can supply a ceiling height from a section.
    text = _page_text(page)
    for line in text.splitlines():
        if re.search(r"\bceiling\b", line, re.I):
            copy = {**page, "structured_content": {"text": line, "extraction_text": line}, "text": line, "extraction_text": line}
            _extract_ceiling_service(copy, source_fp, candidates)


def _enforce_contract(candidates, pages):
    by_page = {p.get("page"): p for p in pages}
    for row in candidates:
        page = by_page.get(row["source"].get("page"), {})
        if page.get("role") in {"3d_render", "3d_reference", "render_or_photo"}:
            row["cross_check_only"] = True
            row["extraction_method"] = "reference_cross_check"
            row["status"] = "evidence_only"
            row["unresolved_fields"].append("primary_evidence_required")
            row["value"] = None
        if row["extraction_method"] == "manual_vision_response" and row["category"] in {"area", "dimension", "ceiling_height", "opening", "surface"}:
            row["unresolved_fields"].append("explicit_dimension_witness_required")
            if not row.get("cross_check_only"):
                row["status"] = "blocked"
        if row["category"] == "occupancy" and re.search(r"(?:people|persons?)\s*(?:/|per\b)", row["source"].get("excerpt", ""), re.I):
            row["value"] = None
            row["unresolved_fields"].append("occupancy_count_basis")
            row["status"] = "blocked"
        if row["category"] in {"ceiling_height", "floor_to_floor_height"} and row.get("unit") == "m" and isinstance(row.get("value"), (int, float)):
            row["value"] *= 1000
            row["unit"] = "mm"
            row["derivation"] = {"formula": "explicit metres × 1000", "raw_unit": "m"}
        if row["unresolved_fields"] and row["status"] == "active":
            row["status"] = "blocked"
        row["unresolved_fields"] = sorted(set(row["unresolved_fields"]))


def _add_record_relationships(result):
    """Reference equal explicit tags across plan/elevation/schedule citations.

    These links express co-reference only. They never assert a unique physical
    opening, fill missing dimensions, or activate a candidate.
    """
    groups = {}
    for row in result["candidates"]:
        if row.get("entity") == "opening" or row["category"] == "opening":
            tag = row.get("label") or (row.get("value") or {}).get("tag")
            if tag:
                groups.setdefault(str(tag).casefold(), []).append(row)
    for tag, rows in groups.items():
        for left in rows:
            for right in rows:
                if left["candidate_id"] >= right["candidate_id"] or left["source"]["page"] == right["source"]["page"]:
                    continue
                relation = {
                    "kind": "opening_tag_cross_reference",
                    "from_observation_id": left["candidate_id"], "to_observation_id": right["candidate_id"],
                    "status": "reference_only", "basis": "exact_explicit_tag",
                    "pages": sorted([left["source"]["page"], right["source"]["page"]]),
                    "citations": [deepcopy(left["source"]), deepcopy(right["source"])],
                    "cross_check_only": bool(left.get("cross_check_only") or right.get("cross_check_only")),
                }
                relation["relationship_id"] = "calc_link_" + _fingerprint(relation)[:24]
                result["binding"]["relationships"].append(relation)


def _finalize_contract(result, pages):
    """Keep binding compatibility while removing positional observation IDs."""
    binding = result["binding"]
    replacements = {}
    for observation in binding["observations"]:
        if observation.get("candidate_id") or observation.get("source", {}).get("vector_id"):
            continue
        source = observation.get("source", {})
        old = observation["observation_id"]
        identity = {k: v for k, v in observation.items() if k != "observation_id"}
        new = _stable_id(result["source_fingerprint"], source.get("page"), source.get("drawing_number", ""),
            observation.get("raw_value"), identity, observation.get("type"))
        observation["observation_id"] = new
        replacements[old] = new
    for relation in binding["relationships"]:
        for key in ("from_observation_id", "to_observation_id"):
            relation[key] = replacements.get(relation.get(key), relation.get(key))
    _enforce_contract(result["candidates"], pages)
    _add_record_relationships(result)
    available_witnesses = {o["observation_id"] for o in binding["observations"]}
    # Geometry-proof witnesses are stable IDs owned by geometry_resolution;
    # they are not raw OCR/vector observations but have already passed the
    # closed-loop, scale, and independent-witness checks there.
    available_witnesses.update(
        row.get("witness_id") for row in (result.get("geometry_resolution", {}).get("witnesses", []) or [])
        if row.get("witness_id")
    )
    for row in result["candidates"]:
        if any(w not in available_witnesses for w in row["witness_ids"]):
            row["unresolved_fields"] = sorted(set(row["unresolved_fields"] + ["missing_witness"]))
            if row["status"] not in {"evidence_only", "conflict"}:
                row["status"] = "blocked"
            result["issues"].append({"type": "candidate_review", "candidate_id": row["candidate_id"], "target": row["target"], "status": row["status"], "reason": "missing_witness"})
        row["witness_ids"] = sorted(set(replacements.get(w, w) for w in row["witness_ids"]))
    binding["observations"] = sorted({o["observation_id"]: o for o in binding["observations"]}.values(), key=lambda o: o["observation_id"])
    binding["relationships"].sort(key=lambda row: row["relationship_id"])
    categories = {}
    for row in result["candidates"]:
        bucket = categories.setdefault(row["category"], {"count": 0, "active": 0, "blocked": 0, "proposed": 0, "evidence_only": 0, "conflict": 0})
        bucket["count"] += 1
        bucket[row["status"]] += 1
    result["categories"] = categories
    if any(row["status"] in {"blocked", "conflict"} or row["competing_candidates"] for row in result["candidates"]):
        result["status"] = "blocked"
    binding["fingerprint"] = _fingerprint({k: v for k, v in binding.items() if k != "fingerprint"})
    result["issues"].sort(key=lambda row: json.dumps(row, sort_keys=True))
    result["fingerprint"] = _fingerprint({k: v for k, v in result.items() if k not in {"fingerprint", "generated_at"}})
    return result


def _geometry_candidates(geometry, source_fp, pages_by_number):
    """Expose normalized geometry entities without activating authored inputs."""
    rows = []
    for entity in (geometry or {}).get("entities", []):
        source = entity.get("source") or {}
        page = pages_by_number.get(source.get("page"))
        if not page:
            continue
        kind, label = entity.get("kind"), entity.get("label", "")
        if kind == "wall":
            continue
        if kind == "dimension":
            raw = entity.get("value") if isinstance(entity.get("value"), dict) else {}
            if not any(raw.get(key) for key in ("room_id", "room_label", "target_wall_id", "wall_id")):
                continue
        value = entity.get("value")
        entity_value = deepcopy(value) if isinstance(value, dict) else {}
        status = "active" if kind == "area" and entity.get("geometry_status") == "geometry_confirmed" else "proposed"
        unresolved = list(entity.get("unresolved_fields", []))
        if kind == "area":
            area = value.get("area_m2") if isinstance(value, dict) else None
            if not isinstance(area, (int, float)) or area <= 0:
                status, area = "blocked", None
                unresolved.append("area_m2")
            target, category, unit, value = f"room.{label}.area_m2", "area", "m²", area
        elif kind == "room":
            target, category, unit, value = f"room.{label}.identity", "room", "", label
            unresolved.extend(["geometry", "zone_id"] if not unresolved else [])
        elif kind == "floor":
            target, category, unit, value = f"floor.{label}.identity", "floor", "", label
            unresolved.extend(["floor_to_zone_mapping"] if not unresolved else [])
        elif kind in {"opening", "surface", "dimension"}:
            target, category, unit = f"{kind}.{label or 'unresolved'}", kind, "mm" if kind == "dimension" else ""
        else:
            continue
        row = _candidate(
            source_fp, page, target, category, deepcopy(value), unit,
            label=label, excerpt=str(source.get("excerpt", "")),
            method=entity.get("extraction_method", "geometry_resolution"), status=status,
            confidence=entity.get("confidence", "unknown"), witnesses=entity.get("witness_ids", []),
            unresolved=unresolved, room_id=label if kind in {"room", "area"} else "",
            derivation=deepcopy(entity_value.get("derivation", {})) if kind == "area" else None,
        )
        if kind == "area" and entity_value.get("geometry_proof_id"):
            row["geometry_proof_id"] = entity_value["geometry_proof_id"]
            row["room_geometry_entity_id"] = entity_value.get("room_entity_id", "")
            row["calibration"] = deepcopy(entity_value.get("calibration", {}))
        rows.append(row)
    return rows


def _validate_candidates(candidates, pages):
    known_pages = {page.get("page") for page in pages}
    by_target = {}
    issues = []
    for row in candidates:
        source_page = row.get("source", {}).get("page")
        if source_page not in known_pages:
            row["status"] = "blocked"; row["unresolved_fields"].append("source_page")
        if not row.get("field") and row.get("unit") and str(row["unit"]).lower() not in SUPPORTED_UNITS and row.get("category") not in {"equipment", "boundary", "schedule"}:
            row["status"] = "blocked"; row["unresolved_fields"].append("unsupported_unit")
        if isinstance(row.get("value"), (int, float)) and (not math.isfinite(row["value"]) or (row["value"] < 0 and row.get("category") not in {"setpoint", "sill", "head"})):
            row["status"] = "evidence_only" if row["category"] == "equipment" else "blocked"
            row["unresolved_fields"].append("positive_value")
        by_target.setdefault(row["target"], []).append(row)
    for target, rows in by_target.items():
        # Multiple dimension chains are expected on a plan; they are not
        # competing values for one calculator field.  Unresolved targets also
        # cannot establish a meaningful conflict until room allocation exists.
        if ".dimension" in target or ".unresolved" in target:
            continue
        comparable = {json.dumps([row.get("value"), row.get("unit")], sort_keys=True) for row in rows if not row.get("cross_check_only")}
        if rows[0].get("category") == "opening":
            keys = {key for row in rows for key in (row.get("value") or {}) if key != "tag"}
            comparable = {"conflict", "values"} if any(len({json.dumps(row["value"][key]) for row in rows if isinstance(row.get("value"), dict) and key in row["value"]}) > 1 for key in keys) else set()
        if len(comparable) > 1:
            for row in rows:
                row["status"] = "evidence_only" if row["category"] == "equipment" else "conflict"
                row["unresolved_fields"].append("conflicting_values")
                row["competing_candidates"] = [other["candidate_id"] for other in rows if other is not row]
            issues.append({"type": "conflict", "target": target, "candidate_ids": [row["candidate_id"] for row in rows], "reason": "Conflicting PDF values target the same calculator field."})
    for row in candidates:
        if row.get("status") in {"proposed", "blocked", "conflict", "evidence_only"} or row.get("unresolved_fields"):
            issues.append({
                "type": "candidate_review", "candidate_id": row["candidate_id"], "target": row["target"],
                "status": row.get("status"), "page": row.get("source", {}).get("page"),
                "reason": "; ".join(row.get("unresolved_fields", [])) or "Candidate requires review before calculator use.",
            })
    return candidates, issues


def extract_calculation_input_evidence(ai_input, coverage=None, spatial_ocr=None, vector_geometry=None, vision_response=None,
                                       building=None, dimension_matches=None, geometry_confirmation=None):
    ai_input = _canonical_evidence(ai_input)
    coverage = _canonical_evidence(coverage or {})
    spatial_ocr = _canonical_evidence(spatial_ocr or {})
    vector_geometry = _canonical_evidence(vector_geometry or {})
    vision_response = _canonical_evidence(vision_response or {})
    building = _canonical_evidence(building or {})
    dimension_matches = _canonical_evidence(dimension_matches or {})
    geometry_confirmation = _canonical_evidence(geometry_confirmation or {})
    source_fp = _canonical_source_fingerprint(ai_input)
    pages = []
    for raw in ai_input.get("drawing_set", {}).get("pages", []):
        page = deepcopy(raw)
        ocr = next((item for item in spatial_ocr.get("pages", []) if item.get("page") == page.get("page")), {})
        page["room_label_candidates"] = ocr.get("room_label_candidates", page.get("room_label_candidates", []))
        page["extraction_text"] = _page_text(page) + "\n" + "\n".join(str(item.get("text", "")) for item in ocr.get("word_samples", []))
        page["normalized_table_cells"] = [cell for cell in ocr.get("table_cells", []) if isinstance(cell, dict) and cell.get("field")]
        page["role"] = _role(page, coverage)
        pages.append(page)
    candidates = []
    for page in pages:
        _extract_records(page, source_fp, candidates)
        role = page["role"]
        if role in {"3d_render", "3d_reference", "render_or_photo"}:
            continue
        if role in ROLE_GROUPS["plan"]:
            _extract_plan(page, source_fp, candidates)
            _extract_openings(page, source_fp, candidates)
        if role in ROLE_GROUPS["ceiling_service"]:
            _extract_ceiling_service(page, source_fp, candidates)
        if role in ROLE_GROUPS["opening"]:
            _extract_openings(page, source_fp, candidates)
        if role in {"section", "elevation", "elevation_or_section"}:
            _extract_vertical(page, source_fp, candidates)
        title = str(page.get("title", "")).lower()
        if role in ROLE_GROUPS["schedule"] or any(term in title for term in ("schedule", "general note", "design criteria", "requirements")) or role in ROLE_GROUPS["opening"] or role in {"legend_or_general_notes", "general_notes", "construction_or_detail", "detail"}:
            _extract_schedule_and_notes(page, source_fp, candidates)
            _extract_equipment(page, source_fp, candidates)
        _extract_unresolved_text(page, source_fp, candidates)
    pages_by_number = {page.get("page"): page for page in pages}
    candidates.extend(_vision_candidates(vision_response, source_fp, pages_by_number))
    geometry = build_geometry_resolution(
        ai_input, coverage, building, spatial_ocr, vector_geometry,
        dimension_matches=dimension_matches,
        geometry_confirmation=geometry_confirmation,
        vision_response=vision_response,
    )
    # Keep the proof graph beside the candidate list so the current evidence
    # view can show why a derived area is active or blocked.
    result_geometry = deepcopy(geometry)
    candidates.extend(_geometry_candidates(geometry, source_fp, pages_by_number))
    _enforce_contract(candidates, pages)
    candidates.sort(key=lambda row: (row["candidate_id"], _fingerprint(row)))
    candidates, issues = _validate_candidates(candidates, pages)
    # Keep records from separate architect witnesses distinct.  Candidate IDs
    # include source location, so only an exact repeated extraction collapses.
    deduped = {row["candidate_id"]: row for row in candidates}
    candidates = sorted(deduped.values(), key=lambda row: row["candidate_id"])
    categories = {}
    for row in candidates:
        categories.setdefault(row["category"], {"count": 0, "active": 0, "blocked": 0, "proposed": 0, "evidence_only": 0, "conflict": 0})["count"] += 1
        categories[row["category"]][row["status"]] = categories[row["category"]].get(row["status"], 0) + 1
    result = {
        "schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION, "source_pdf": ai_input.get("source_pdf", ""), "source_fingerprint": source_fp,
        "generated_from": "ai_input.json+architect_evidence", "generated_at": timestamp(),
        "candidates": candidates, "issues": issues, "categories": categories,
        "geometry_resolution": result_geometry,
        "pages": [{"page": page.get("page"), "drawing_number": page.get("drawing_number", ""), "role": page.get("role", "")} for page in pages],
        "status": "blocked" if any(row.get("status") in {"blocked", "conflict"} for row in candidates) else "current",
        "fingerprint": _fingerprint({"extractor_version": EXTRACTOR_VERSION, "source_fingerprint": source_fp, "candidates": candidates, "issues": issues}),
    }
    # Bind the extracted values to room/opening/table/vision witnesses only
    # after the page-specific extractors have produced deterministic
    # candidates.  The binding layer can add conflicts and review items, but
    # never creates an engineering value or activates a thermal input.
    result = bind_calculation_evidence(
        result,
        ai_input=ai_input,
        coverage=coverage,
        spatial_ocr=spatial_ocr,
        vector_geometry=vector_geometry,
        vision_response=vision_response,
    )
    return _finalize_contract(result, pages)



def _room_id_for_label(model, label):
    wanted = " ".join(str(label or "").casefold().split())
    for room in model.get("rooms", []):
        names = {" ".join(str(room.get(key, "")).casefold().split()) for key in ("room_id", "name")}
        if wanted and wanted in names:
            return room.get("room_id", "")
    return ""


def normalise_for_hourly_model(evidence, model):
    """Attach active extracted candidates to stable hourly-model targets.

    Extraction uses human-readable room labels because that is what drawings
    provide.  The hourly model uses stable room IDs.  This adapter performs
    only exact label/ID matches; unresolved labels stay proposals.
    """
    result = deepcopy(evidence or {})
    for row in result.get("candidates", []):
        if row.get("status") != "active":
            continue
        room_id = _room_id_for_label(model, row.get("room_id"))
        if not room_id:
            row["status"] = "proposed"
            row.setdefault("unresolved_fields", []).append("hourly_room_mapping")
            continue
        category = row.get("category")
        field = {"area": "area_m2", "ceiling_height": "ceiling_height_mm", "occupancy": "occupancy", "setpoint": "indoor_cooling_setpoint_c"}.get(category)
        if not field:
            if category == "outside_air":
                field = "cooling_load.outside_air_lps" if str(row.get("unit", "")).lower() == "l/s" else None
            elif category == "lighting" and row.get("unit") == "W":
                # Connected watts are retained as evidence.  Conversion to
                # W/m² belongs to the assembler once reviewed area exists.
                field = None
        if not field:
            row["status"] = "evidence_only" if category == "equipment" else "proposed"
            row.setdefault("unresolved_fields", []).append("calculator_field_mapping")
            continue
        row["target_path"] = f"rooms.{room_id}.{field}"
        row["affected_id"] = room_id
        if category == "ceiling_height" and str(row.get("unit", "")).lower() == "m":
            row["value"] = float(row["value"]) * 1000
            row["unit"] = "mm"
            row["derivation"] = {"formula": "height_m × 1000", "operands": {"height_m": row["value"] / 1000}, "rounding": "exact unit conversion"}
    result["hourly_model_fingerprint"] = _fingerprint(model)
    result["fingerprint"] = _fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result
