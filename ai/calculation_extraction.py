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
EXTRACTOR_VERSION = "calculation-input-v1"
DAY_TYPES = ("weekday", "saturday", "sunday", "holiday")
SUPPORTED_UNITS = {"mm", "m", "m2", "m²", "m3", "m³", "m3/h", "m³/h", "l/s", "w", "w/m2", "w/m²", "°c", "c", "pa", "ach"}
ROLE_GROUPS = {
    "plan": {"main_floor_plan", "supporting_geometry_plan", "primary_geometry_plan", "supporting_geometry_plan"},
    "ceiling_service": {"reflected_ceiling_plan", "reflected_ceiling_or_service_plan", "services_or_lighting_plan", "architect_lighting_plan", "architect_electrical_plan"},
    "opening": {"opening_elevation", "opening_schedule", "elevation_or_section", "elevation", "section"},
    "schedule": {"schedule", "material_schedule", "equipment_schedule", "lighting_schedule", "opening_schedule"},
    "reference": {"3d_render", "3d_reference", "reference", "legend_or_general_notes", "construction_or_detail", "detail"},
}

AREA_RE = re.compile(r"(?P<label>[A-Za-z][A-Za-z0-9 /_-]{1,50})\s*(?:area|floor area|net area)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m(?:2|²))\b", re.I)
DIM_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?P<ua>mm|m)?\s*(?:x|×|by)\s*(?P<b>\d+(?:\.\d+)?)\s*(?P<ub>mm|m)?\b", re.I)
HEIGHT_RE = re.compile(r"(?:ceiling|bulkhead|floor\s*to\s*floor|head\s*height|height)\D{0,30}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|m)\b", re.I)
LIGHT_RE = re.compile(r"(?P<watts>\d+(?:\.\d+)?)\s*W\b[^\n]{0,160}?\b(?:qty|quantity)\s*[:.]?\s*(?P<qty>\d+(?:\.\d+)?)", re.I)
OCCUPANCY_RE = re.compile(r"(?:occupancy|people|persons)\D{0,30}(?P<value>\d+(?:\.\d+)?)\b", re.I)
OA_RE = re.compile(r"(?:outside\s*air|fresh\s*air|ventilation)\D{0,30}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>L/s|m3/h|m³/h|ACH)\b", re.I)
TEMP_RE = re.compile(r"(?:cooling|indoor|room|setpoint)\D{0,35}(?P<value>\d+(?:\.\d+)?)\s*°?C\b", re.I)
U_RE = re.compile(r"\bU\s*[- ]?value\D{0,15}(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>W/m(?:2|²)K)\b", re.I)
OPENING_TAG_RE = re.compile(r"\b(?P<tag>[WD]\d{1,3}[A-Z]?)\b", re.I)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _canonical_source_fingerprint(ai_input):
    """Keep candidate identities stable when the packet register is reordered."""
    source = deepcopy(ai_input or {})
    drawing = source.get("drawing_set") or {}
    pages = list(drawing.get("pages", [])) if isinstance(drawing, dict) else []
    pages.sort(key=lambda row: (row.get("page", 0), row.get("drawing_number", "")))
    source["drawing_set"] = {**drawing, "pages": pages}
    return _fingerprint({key: source.get(key) for key in ("source_pdf", "drawing_set", "confirmed_pages", "page_triage")})


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
    return str(structured.get("markdown") or structured.get("text") or page.get("text") or "")


def _role(page, coverage):
    row = next((item for item in coverage.get("page_roles", []) if item.get("page") == page.get("page")), {})
    return row.get("proposed_role") or page.get("sheet_classification") or page.get("detected_type") or "other"


def _candidate(source_fp, page, target, category, value, unit, *, label="", excerpt="", method="structured_pdf", status="proposed", confidence="medium", witnesses=None, unresolved=None, room_id="", derivation=None, evidence_only=False):
    drawing = page.get("drawing_number", "")
    location = excerpt[:160]
    return {
        "candidate_id": _stable_id(source_fp, page.get("page"), drawing, label, location, target),
        "target": target, "category": category, "value": value, "raw_value": value, "unit": unit,
        "affected_id": room_id or page.get("level_name", ""), "room_id": room_id,
        "status": "evidence_only" if evidence_only else status, "extraction_method": method,
        "confidence": confidence, "source_fingerprint": source_fp,
        "source": {"page": page.get("page"), "drawing_number": drawing, "excerpt": excerpt, "kind": "architect_pdf"},
        "witness_ids": list(witnesses or []), "unresolved_fields": list(unresolved or []),
        "competing_candidates": [], "derivation": deepcopy(derivation or {}),
    }


def _room_label(page, excerpt):
    room_terms = ("room", "shop", "kitchen", "bar", "dining", "storage", "toilet", "office", "staff", "cool", "freezer")
    non_room_terms = ("tile", "tiles", "painted", "legend", "extg", "existing", "material", "finish", "colour", "color", "schedule")
    labels = [str(item.get("text", "")).strip() for item in page.get("room_label_candidates", []) if isinstance(item, dict)]
    for label in labels:
        lowered = label.casefold()
        if label and any(term in lowered for term in room_terms) and not any(term in lowered for term in non_room_terms) and lowered in excerpt.casefold():
            return label
    match = re.search(r"\b(cool\s*room|freezer\s*room|kitchen|shop|office|storage|dining|toilet|staff\s*room)\b", excerpt, re.I)
    return match.group(1) if match else ""


def _extract_plan(page, source_fp, candidates):
    text = _page_text(page)
    for match in AREA_RE.finditer(text):
        label = match.group("label").strip()
        candidates.append(_candidate(source_fp, page, f"room.{label}.area_m2", "area", _number(match.group("value")), "m²", label=label, excerpt=match.group(0), method="explicit_room_area", status="active", confidence="high", room_id=label))
    for match in DIM_RE.finditer(text):
        context = text[max(0, match.start() - 100):match.end() + 100]
        label = _room_label(page, context)
        unit = _unit(match.group("ua") or match.group("ub") or "")
        unresolved = [] if unit else ["unit"]
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.dimension", "dimension", {"a": _number(match.group("a")), "b": _number(match.group("b"))}, unit, label=label or "unresolved dimension", excerpt=match.group(0), method="plan_dimension_pair", status="proposed" if unit else "blocked", confidence="medium", room_id=label, unresolved=unresolved))
    for match in re.finditer(r"\b(external wall|outside|street frontage|shopfront|adjacent|adjoining)\b", text, re.I):
        excerpt = text[max(0, match.start() - 30):match.end() + 60].strip()
        label = _room_label(page, excerpt)
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.boundary", "boundary", match.group(1).lower(), "", label=label, excerpt=excerpt, method="explicit_boundary_text", status="proposed", room_id=label, unresolved=[] if label else ["room_allocation"]))


def _extract_ceiling_service(page, source_fp, candidates):
    text = _page_text(page)
    for match in HEIGHT_RE.finditer(text):
        label = _room_label(page, text[max(0, match.start() - 80):match.end() + 80])
        unit = _unit(match.group("unit"))
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.ceiling_height", "ceiling_height", _number(match.group("value")), unit, label=label, excerpt=match.group(0), method="ceiling_height_text", status="active" if label else "proposed", confidence="high" if label else "medium", room_id=label, unresolved=[] if label else ["room_allocation"]))
    for match in LIGHT_RE.finditer(text):
        qty, watts = _number(match.group("qty")), _number(match.group("watts"))
        label = _room_label(page, text[max(0, match.start() - 100):match.end() + 100])
        candidates.append(_candidate(source_fp, page, f"room.{label or 'unresolved'}.lighting.connected_w", "lighting", qty * watts, "W", label=label, excerpt=match.group(0), method="lighting_quantity_wattage", status="active" if label else "proposed", confidence="high", room_id=label, unresolved=[] if label else ["room_allocation"], derivation={"formula": "quantity × watts_each", "operands": {"quantity": qty, "watts_each": watts}}))


def _extract_openings(page, source_fp, candidates):
    text = _page_text(page)
    tags = list(OPENING_TAG_RE.finditer(text))
    for tag_match in tags:
        tag = tag_match.group("tag").upper()
        excerpt = text[max(0, tag_match.start() - 40):tag_match.end() + 120]
        pair = DIM_RE.search(excerpt)
        value = {"tag": tag}
        unresolved = []
        if pair:
            value.update({"width_mm": _number(pair.group("a")), "height_mm": _number(pair.group("b"))})
        else:
            unresolved.append("dimensions")
        candidates.append(_candidate(source_fp, page, f"opening.{tag}", "opening", value, "mm" if pair else "", label=tag, excerpt=excerpt.strip(), method="opening_tag_and_dimension", status="active" if pair else "proposed", confidence="high" if pair else "medium", unresolved=unresolved))
    for match in DIM_RE.finditer(text):
        if not OPENING_TAG_RE.search(text[max(0, match.start() - 100):match.start()]):
            continue
        # Tag-linked candidates above carry the value; this preserves an
        # explicit elevation witness without creating an untagged opening.


def _extract_schedule_and_notes(page, source_fp, candidates):
    text = _page_text(page)
    for regex, category, unit, method in ((OCCUPANCY_RE, "occupancy", "people", "explicit_occupancy"), (OA_RE, "outside_air", "", "explicit_outside_air"), (TEMP_RE, "setpoint", "°C", "explicit_setpoint"), (U_RE, "u_value", "W/m²K", "explicit_u_value")):
        for match in regex.finditer(text):
            candidates.append(_candidate(source_fp, page, f"page.{category}", category, _number(match.group("value")), _unit(match.group("unit")) if match.groupdict().get("unit") else unit, excerpt=match.group(0), method=method, status="proposed", confidence="high", unresolved=["room_allocation"]))
    for match in re.finditer(r"\b(weekday|weekdays|saturday|sunday|holiday)\b[^\n]{0,100}?\b(\d{1,2}:\d{2})\s*(?:-|to)\s*(\d{1,2}:\d{2})", text, re.I):
        candidates.append(_candidate(source_fp, page, f"schedule.{match.group(1).lower()}", "schedule", {"day_type": match.group(1).lower(), "start": match.group(2), "end": match.group(3)}, "time", excerpt=match.group(0), method="operating_hours_text", status="proposed", confidence="medium", unresolved=["hourly_profile"]))


def _extract_equipment(page, source_fp, candidates):
    text = _page_text(page)
    words = re.findall(r"\b(fridge|freezer|oven|cooktop|range|grill|fryer|dishwasher|ice maker|display fridge|coffee machine)\b", text, re.I)
    for word in sorted(set(words), key=str.casefold):
        excerpt = word
        power = re.search(rf"{re.escape(word)}[^\n]{{0,100}}?(\d+(?:\.\d+)?)\s*k?W\b", text, re.I)
        value = {"name": word.lower(), "quantity": 1, "rated_power": _number(power.group(1)) if power else None, "rated_power_unit": "kW" if power and "kw" in power.group(0).lower() else "W" if power else ""}
        candidates.append(_candidate(source_fp, page, f"equipment.{word.lower()}", "equipment", value, value["rated_power_unit"], label=word, excerpt=excerpt, method="equipment_schedule_text", status="evidence_only", confidence="medium", evidence_only=True, unresolved=["room_allocation", "heat_to_space_basis"]))


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


def _geometry_candidates(geometry, source_fp, pages_by_number):
    """Expose normalized geometry entities without activating authored inputs."""
    rows = []
    for entity in (geometry or {}).get("entities", []):
        source = entity.get("source") or {}
        page = pages_by_number.get(source.get("page"))
        if not page:
            continue
        kind = entity.get("kind")
        label = entity.get("label", "")
        # Raw vector walls and unbound dimension text belong in the geometry
        # graph. They are not calculator fields until a room/wall target is
        # explicit, otherwise title-block numbers can become false dimensions.
        if kind == "wall":
            continue
        if kind == "dimension":
            raw_dimension = entity.get("value") if isinstance(entity.get("value"), dict) else {}
            if not any(raw_dimension.get(key) for key in ("room_id", "room_label", "target_wall_id", "wall_id")):
                continue
        value = entity.get("value")
        status = "active" if kind == "area" and entity.get("geometry_status") == "geometry_confirmed" else "proposed"
        unresolved = list(entity.get("unresolved_fields", []))
        if kind == "area":
            raw = value.get("area_m2") if isinstance(value, dict) else None
            if not isinstance(raw, (int, float)) or raw <= 0:
                status = "blocked"
                unresolved.append("area_m2")
            target = f"room.{label}.area_m2"
            category = "area"
            unit = "m²"
        elif kind == "room":
            target, category, unit = f"room.{label}.identity", "room", ""
            value = label
            unresolved.extend(["geometry", "zone_id"] if not unresolved else [])
        elif kind == "floor":
            target, category, unit = f"floor.{label}.identity", "floor", ""
            value = label
            unresolved.extend(["floor_to_zone_mapping"] if not unresolved else [])
        elif kind in {"opening", "surface", "wall", "dimension"}:
            target, category = f"{kind}.{label or 'unresolved'}", kind
            unit = "mm" if kind == "dimension" else ""
        else:
            continue
        rows.append(_candidate(
            source_fp, page, target, category, deepcopy(value), unit,
            label=label, excerpt=str(entity.get("source", {}).get("excerpt", "")),
            method=entity.get("extraction_method", "geometry_resolution"), status=status,
            confidence=entity.get("confidence", "unknown"), witnesses=entity.get("witness_ids", []),
            unresolved=unresolved, room_id=label if kind in {"room", "area"} else "",
        ))
    return rows


def _validate_candidates(candidates, pages):
    known_pages = {page.get("page") for page in pages}
    by_target = {}
    issues = []
    for row in candidates:
        source_page = row.get("source", {}).get("page")
        if source_page not in known_pages:
            row["status"] = "blocked"; row["unresolved_fields"].append("source_page")
        if row.get("unit") and str(row["unit"]).lower() not in SUPPORTED_UNITS and row.get("category") not in {"equipment", "boundary", "schedule"}:
            row["status"] = "blocked"; row["unresolved_fields"].append("unsupported_unit")
        if isinstance(row.get("value"), (int, float)) and (not math.isfinite(row["value"]) or row["value"] <= 0):
            row["status"] = "blocked"; row["unresolved_fields"].append("positive_value")
        by_target.setdefault(row["target"], []).append(row)
    for target, rows in by_target.items():
        # Multiple dimension chains are expected on a plan; they are not
        # competing values for one calculator field.  Unresolved targets also
        # cannot establish a meaningful conflict until room allocation exists.
        if ".dimension" in target or ".unresolved" in target:
            continue
        comparable = {json.dumps(row.get("value"), sort_keys=True) for row in rows if row.get("status") != "evidence_only"}
        if len(comparable) > 1:
            for row in rows:
                row["status"] = "conflict"
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
    coverage = coverage or {}
    spatial_ocr = spatial_ocr or {}
    source_fp = _canonical_source_fingerprint(ai_input)
    pages = []
    for raw in ai_input.get("drawing_set", {}).get("pages", []):
        page = deepcopy(raw)
        ocr = next((item for item in spatial_ocr.get("pages", []) if item.get("page") == page.get("page")), {})
        page["room_label_candidates"] = ocr.get("room_label_candidates", [])
        page["text"] = _page_text(page) + "\n" + "\n".join(str(item.get("text", "")) for item in ocr.get("word_samples", []))
        page["role"] = _role(page, coverage)
        pages.append(page)
    candidates = []
    for page in pages:
        role = page["role"]
        if role in ROLE_GROUPS["plan"]:
            _extract_plan(page, source_fp, candidates)
        if role in ROLE_GROUPS["ceiling_service"]:
            _extract_ceiling_service(page, source_fp, candidates)
        if role in ROLE_GROUPS["opening"]:
            _extract_openings(page, source_fp, candidates)
        title = str(page.get("title", "")).lower()
        if role in ROLE_GROUPS["schedule"] or any(term in title for term in ("schedule", "general note", "design criteria", "requirements")) or role in ROLE_GROUPS["opening"]:
            _extract_schedule_and_notes(page, source_fp, candidates)
            _extract_equipment(page, source_fp, candidates)
    pages_by_number = {page.get("page"): page for page in pages}
    candidates.extend(_vision_candidates(vision_response, source_fp, pages_by_number))
    geometry = build_geometry_resolution(
        ai_input, coverage, building or {}, spatial_ocr, vector_geometry,
        dimension_matches=dimension_matches, geometry_confirmation=geometry_confirmation,
        vision_response=vision_response,
    )
    candidates.extend(_geometry_candidates(geometry, source_fp, pages_by_number))
    candidates, issues = _validate_candidates(candidates, pages)
    # PDF, vision, and geometry-resolution passes can describe the same fact.
    # Merge exact target/value duplicates while retaining every witness; keep
    # differing values separate so the validator can report a conflict.
    deduped = {}
    priority = {"active": 0, "proposed": 1, "blocked": 2, "conflict": 3, "evidence_only": 4}
    for row in candidates:
        key = (row.get("target", ""), json.dumps(row.get("value"), sort_keys=True, separators=(",", ":")))
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = row
            continue
        existing["witness_ids"] = sorted(set(existing.get("witness_ids", [])) | set(row.get("witness_ids", [])))
        existing["unresolved_fields"] = sorted(set(existing.get("unresolved_fields", [])) | set(row.get("unresolved_fields", [])))
        if priority.get(row.get("status"), 9) < priority.get(existing.get("status"), 9):
            existing["status"] = row.get("status")
        existing.setdefault("source_alternatives", []).append(row.get("source", {}))
    candidates = sorted(deduped.values(), key=lambda row: row["candidate_id"])
    categories = {}
    for row in candidates:
        categories.setdefault(row["category"], {"count": 0, "active": 0, "blocked": 0, "proposed": 0, "evidence_only": 0, "conflict": 0})["count"] += 1
        categories[row["category"]][row["status"]] = categories[row["category"]].get(row["status"], 0) + 1
    result = {
        "schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION, "source_pdf": ai_input.get("source_pdf", ""), "source_fingerprint": source_fp,
        "generated_from": "ai_input.json+architect_evidence", "generated_at": timestamp(),
        "candidates": candidates, "issues": issues, "categories": categories,
        "pages": [{"page": page.get("page"), "drawing_number": page.get("drawing_number", ""), "role": page.get("role", "")} for page in pages],
        "status": "blocked" if any(row.get("status") in {"blocked", "conflict"} for row in candidates) else "current",
        "fingerprint": _fingerprint({"extractor_version": EXTRACTOR_VERSION, "source_fingerprint": source_fp, "candidates": candidates, "issues": issues}),
    }
    # Bind the extracted values to room/opening/table/vision witnesses only
    # after the page-specific extractors have produced deterministic
    # candidates.  The binding layer can add conflicts and review items, but
    # never creates an engineering value or activates a thermal input.
    return bind_calculation_evidence(
        result,
        ai_input=ai_input,
        coverage=coverage,
        spatial_ocr=spatial_ocr,
        vector_geometry=vector_geometry,
        vision_response=vision_response,
    )


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
