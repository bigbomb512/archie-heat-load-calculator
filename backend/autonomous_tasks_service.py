"""Project-local Card P packet, manual transport and apply service."""

from copy import deepcopy
from datetime import datetime, timezone
import inspect
import hashlib
import json
import math
import re
from functools import lru_cache
from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
import uuid

from ai import autonomous_task_scoring, autonomous_tasks, reviewer_room_geometry, room_outline, kitchen_equipment
from backend import productization, reviewer_room_geometry_service, calculation_extraction_service, page_analysis_cache
from backend.vision_extraction_service import _atomic_json

ACCURACY_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "autonomous" / "accuracy.json"
ROOF_CONTRACTOR_QUESTION = "Is there a floor or another tenancy directly above this shop, or is it the roof?"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else deepcopy(default)
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(path, value)


def _root(project):
    return Path(project["review_dir"])


def _task_dir(root, task, target):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(target)).strip("._")[:100] or "project"
    return root / "ai_tasks" / task / safe


def _accuracy(task):
    row = _read(ACCURACY_PATH, {}).get("tasks", {}).get(task)
    if (not isinstance(row, dict) or not isinstance(row.get("accuracy"), (int, float))
            or isinstance(row.get("accuracy"), bool) or int(row.get("scored", 0)) < 10):
        return {"accuracy": None, "scored": 0, "auto_apply": False, "report": ""}
    accuracy = float(row["accuracy"])
    return {"accuracy": accuracy, "scored": int(row["scored"]),
            "auto_apply": accuracy >= autonomous_task_scoring.AUTO_APPLY_BAR,
            "report": "accuracy.json"}


def _current_task(run_root, task, target):
    current_file = _task_dir(run_root, task, target) / "current.json"
    pointer = _read(current_file, {})
    relative = pointer.get("run_path")
    if not relative:
        return None
    path = (current_file.parent / relative).resolve()
    try:
        path.relative_to(current_file.parent.resolve())
    except ValueError:
        return None
    return _read(path / "record.json", None)


def _load_inputs(root):
    ai_input = _read(root / "ai_input.json", {})
    spatial = _read(root / "spatial_ocr.json", {})
    building = _read(root / "building_evidence.json", {})
    return ai_input, spatial, building


def _latest_site(root):
    rows = list((root / "ai_tasks" / "P1_site").glob("*/current.json"))
    for pointer_path in rows:
        pointer = _read(pointer_path, {})
        run_path = pointer_path.parent / pointer.get("run_path", "")
        record = _read(run_path / "record.json", {})
        if record.get("status") in {"applied", "applied_fallback", "below_accuracy_bar"} and record.get("applied_value"):
            return record.get("applied_value")
    return {}


def _latest_roof(root, room_id):
    pointer_path = _task_dir(root, "P5_roof", room_id) / "current.json"
    pointer = _read(pointer_path, {})
    record = _read(pointer_path.parent / pointer.get("run_path", "") / "record.json", {})
    return record.get("applied_value", {}) if record.get("status") in {"applied", "applied_fallback", "below_accuracy_bar"} else {}


def _status_for_accuracy(source, accuracy):
    if source == "ai_fallback":
        return "applied_fallback"
    return "applied" if accuracy.get("auto_apply") else "below_accuracy_bar"


def _site_packet(root):
    ai_input, spatial, building = _load_inputs(root)
    packet, prompt = autonomous_tasks.build_site_packet(ai_input, spatial, building)
    return packet, prompt, {"ai_input": ai_input, "spatial_ocr": spatial, "building_evidence": building}


def _site_declination(root):
    """East-positive magnetic declination at the site (confirmed site position, else the weather location's), or None."""
    from ai import design_weather, magnetic
    from backend import weather_pick_service
    location = (_read(root / "site_location_resolution.json", {}) or {}).get("location") or {}
    latitude, longitude = location.get("latitude_deg"), location.get("longitude_deg")
    if latitude is None or longitude is None:
        try:
            place = weather_pick_service.effective(root)[0]
        except (OSError, ValueError, TypeError):
            place = ""
        if place in design_weather.LOCATIONS:
            latitude, longitude, _state = design_weather.LOCATIONS[place]
    try:
        return magnetic.declination_deg(latitude, longitude) if latitude is not None and longitude is not None else None
    except (ValueError, ImportError, OSError):
        return None


def _north_answer(record):
    value = (record or {}).get("validation", {})
    return value.get("result", value) if isinstance(value, dict) else {}


def _north_pages(root):
    """The pages asked for their north arrow, in stages: the main traced plan(s), as only their north is used; when
    all of those were answered without an arrow, the other floor plans and the site or location plan; only when those
    have none either, every plan page. Casa Nova (2026-10-24) asked all 22 plan-like pages (66 images, a third of the
    drawing calls) because its setout plan has no arrow."""
    ai_input = _read(root / "ai_input.json", {})
    plans = autonomous_tasks.plan_pages(ai_input)
    try:
        main = {row["page"] for row in _p0_main_geometry_pages(root)}
    except (OSError, ValueError, KeyError, TypeError):
        main = set()

    def kind(row):
        return " ".join(str(row.get(key) or "") for key in ("type", "detected_type", "sheet_classification", "title")).casefold()
    site = [row for row in (ai_input.get("drawing_set", {}) or {}).get("pages", [])
            if isinstance(row, dict) and type(row.get("page")) is int and re.search(r"site|location|locality", kind(row))]
    floor = [row for row in plans if row["page"] not in main and "floor_plan" in kind(row)
             and not re.search(r"ceiling|rcp|services?|lighting|electric|hydraulic|mechanical|power", kind(row))]
    stages = [[row for row in plans if row["page"] in main],
              sorted({row["page"]: row for row in floor + site}.values(), key=lambda row: row["page"]),
              plans]

    def found(record):
        # A project-north arrow doesn't count: keep looking for a true one (user decision 2026-10-26).
        value = _north_answer(record)
        return isinstance(value, dict) and value.get("found") and value.get("label") != "project"
    asked = []
    for stage in stages:
        new = [row for row in stage if row["page"] not in {done["page"] for done in asked}]
        if not new:
            continue
        asked += new
        records = [_current_task(root, "P2_north", f"page-{row['page']}") for row in asked]
        settled = all(record and record.get("status") in {"applied", "below_accuracy_bar", "blocked", "not_needed"} for record in records)
        if not settled or any(found(record) for record in records):
            break        # waiting for answers, or an arrow was found: no need to ask further pages
    return sorted(asked, key=lambda row: row["page"])


def _north_packets(root):
    rows = []
    for page in _north_pages(root):
        number = page["page"]
        target = f"page-{number}"
        try:
            with TemporaryDirectory(prefix="archie-north-crops-") as temporary:
                crops, paths = autonomous_tasks.make_north_crops(root, number, Path(temporary))
                packet = {"task": "P2_north", "page": number, "drawing_number": page.get("drawing_number", ""),
                          "title": page.get("title", ""), "crops": crops}
                prompt = autonomous_tasks.north_prompt(packet)
                rows.append((target, packet, prompt, [path.read_bytes() for path in paths]))
        except (OSError, ValueError) as error:
            reason = (NotNeeded("This page has no plan image to check for a north arrow; the other plan pages are checked.")
                      if "render" in str(error).casefold() else str(error))
            rows.append((target, {"task": "P2_north", "page": number, "crops": []}, "", [], reason))
    return rows


NOT_PLAN_PAGE_TYPES = {"elevation", "section", "detail"}


def _p0_page_selection(root):
    """Select one main geometry sheet per level and explain every omitted plan page."""
    ai_input, spatial, _building = _load_inputs(root)
    coverage = _read(root / "drawing_coverage.json", {})
    vector = _read(root / "vector_geometry.json", {})
    coverage_by_page = {row.get("page"): row for row in coverage.get("page_roles", []) if isinstance(row, dict)}
    vector_by_page = {row.get("page"): row for row in (vector.get("geometry_key_points") or {}).get("pages", [])
                      if isinstance(row, dict)}
    spatial_by_page = {row.get("page"): row for row in spatial.get("pages", []) if isinstance(row, dict)}
    # Page reading (pass 1) looks at each whole sheet; when it says elevation or section, a small plan inset on that
    # sheet is not the floor plan (Casa Nova 2026-10-23: a toilet inset on an interior-elevation sheet was traced).
    readings = {str(number): row for number, row in (_read(root / "page_inventory.json", {}).get("pages") or {}).items()
                if isinstance(row, dict)}
    read_types = {number: str(row.get("page_type") or "") for number, row in readings.items()}
    # The page reading's main plans per level (proposed whole-floor plans; an existing plan only when no proposed one
    # exists), when it found any: a house set has existing, demolition, proposed and set-out plans for each level, and
    # the sheet metadata can't tell them apart or name their levels (house set 18, 2026-11-01: the existing ground
    # floor and the roof plan were traced, the proposed lower ground floor wasn't).
    from ai import page_inventory
    read_main = {str(page): level for level, pages in page_inventory.main_plan_pages(
        {int(number): row for number, row in readings.items() if number.isdigit()}).items() for page in pages}
    grouped = {}
    skipped = []
    notes = []
    for page in (ai_input.get("drawing_set", {}) or {}).get("pages", []):
        if not isinstance(page, dict) or type(page.get("page")) is not int:
            continue
        number = page["page"]
        if read_types.get(str(number)) in NOT_PLAN_PAGE_TYPES:
            note = {"page": number, "status": "skipped",
                    "reason": f"Skipped: page reading shows this sheet is {'an' if read_types[str(number)][0] in 'aeiou' else 'a'} "
                              f"{read_types[str(number)].replace('_', ' ')}, not a floor plan."}
            skipped.append(note)
            notes.append(note)
            continue
        role_row = coverage_by_page.get(number, {})
        role = str(role_row.get("proposed_role") or page.get("plan_role") or "").casefold()
        kind = str(page.get("type") or page.get("sheet_classification") or "").casefold()
        if read_main and str(number) not in read_main:
            if read_types.get(str(number)) == "floor_plan" or role in {"main_floor_plan", "primary_geometry_plan", "floor_plan"}:
                reading = readings.get(str(number), {})
                what = str(reading.get("floor_plan_kind") or reading.get("page_type") or "not a floor plan").replace("_", " ")
                note = {"page": number, "status": "skipped",
                        "reason": f"Skipped: page reading shows this sheet is not a main floor plan ({what}); the proposed plan of its level is used."}
                skipped.append(note)
                notes.append(note)
            continue
        if str(number) in read_main and role not in {"main_floor_plan", "primary_geometry_plan", "floor_plan",
                                                     "supporting_geometry_plan", "uncertain_top_down_context"}:
            role = "floor_plan"
            kind = kind or "floor_plan"
        if "floor_plan" not in kind and role not in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "uncertain_top_down_context", "enlarged_plan", "floor_plan"}:
            continue
        classification = " ".join(str(page.get(key) or "") for key in
                                  ("type", "detected_type", "sheet_classification", "title")).casefold()
        coverage_scale = str(role_row.get("main_scale") or "").strip()
        page_scale = str(page.get("main_scale") or page.get("scale") or "").strip()
        scale_text = coverage_scale or page_scale
        scale_conflict = bool(coverage_scale and page_scale and
                              " ".join(coverage_scale.casefold().split()) != " ".join(page_scale.casefold().split()))
        if scale_conflict:
            notes.append({"page": number, "status": "scale_conflict",
                "reason": f"Scale sources disagree (drawing coverage {coverage_scale}; sheet metadata {page_scale}); scale alone did not exclude this page."})
        match = re.search(r"1\s*:\s*(\d+(?:\.\d+)?)", scale_text)
        skip_reasons=[]
        if any(term in classification for term in ("joinery", "cabinet", "shop drawing")):
            skip_reasons.append("joinery/shop-detail drawing")
        if "detail" in classification:
            skip_reasons.append("detail view")
        if match and float(match.group(1)) <= 20 and not scale_conflict:
            skip_reasons.append(f"large detail scale {match.group(0)}")
        if skip_reasons:
            note={"page": number, "status": "skipped", "reason": f"Skipped {' and '.join(skip_reasons)}; room outlines use the main geometry plan."}
            skipped.append(note)
            notes.append(note)
            continue
        if role not in {"main_floor_plan", "primary_geometry_plan", "floor_plan", "supporting_geometry_plan", "uncertain_top_down_context"} and "floor_plan" not in kind:
            note={"page": number, "status": "skipped", "reason": f"Skipped secondary plan role: {role or 'not classified as a main geometry plan'}."}
            skipped.append(note)
            notes.append(note)
            continue
        if role not in {"main_floor_plan", "primary_geometry_plan", "floor_plan", "supporting_geometry_plan", "uncertain_top_down_context"}:
            role = "floor_plan"
        level = str(role_row.get("level_name") or page.get("level_name") or page.get("floor_label") or "").strip()
        level_key = " ".join(level.casefold().split()) or "unassigned level"
        if read_main.get(str(number)):
            level_key = read_main[str(number)]
        vector_dims = vector_by_page.get(number, {}).get("dimension_candidates", []) or []
        spatial_dims = spatial_by_page.get(number, {}).get("dimension_candidates", []) or []
        page_dims = page.get("dimension_candidates", []) or []
        dimension_count = max(len(vector_dims), len(spatial_dims), len(page_dims))
        priority = {"main_floor_plan": 0, "primary_geometry_plan": 1, "floor_plan": 2,
                    "supporting_geometry_plan": 3, "uncertain_top_down_context": 4}.get(role, 4)
        grouped.setdefault(level_key, []).append((dimension_count, priority, number, page, role))
    # A main plan with no level and one other named level are the same floor, not two (Happy Cap 2026-11-06: its dimension
    # plan read as "Mezzanine" from "Tenancy MZ01", so the one floor was traced twice).
    named = [key for key in grouped if key != "unassigned level"]
    if ("unassigned level" in grouped and len(named) == 1
            and all(str(row[2]) in read_main for rows in grouped.values() for row in rows)):
        grouped[named[0]].extend(grouped.pop("unassigned level"))
    if not grouped:
        # No floor plan in the set (a mechanical engineer's set): trace on its scaled services plans, which are drawn
        # over the tenancy (user decision 2026-11-02, Global Exchange). The scale comes from printed areas or
        # dimensions when they can be read, else from the sheet scale alone, labelled unchecked (_p0_calibration_ready).
        from ai import vector_geometry
        for page in vector_geometry.fallback_services_plan_pages(ai_input):
            number = page["page"]
            if read_types.get(str(number)) in NOT_PLAN_PAGE_TYPES:
                continue
            role_row = coverage_by_page.get(number, {})
            level = str(role_row.get("level_name") or page.get("level_name") or page.get("floor_label") or "").strip()
            level_key = " ".join(level.casefold().split()) or "unassigned level"
            dimension_count = max(len(vector_by_page.get(number, {}).get("dimension_candidates", []) or []),
                                  len(spatial_by_page.get(number, {}).get("dimension_candidates", []) or []),
                                  len(page.get("dimension_candidates", []) or []))
            grouped.setdefault(level_key, []).append((dimension_count, 5, number, page, "fallback_services_plan"))
            notes.append({"page": number, "status": "services_plan_fallback",
                          "reason": "No architectural floor plan in this set; rooms are traced on a scaled services plan."})
    if not grouped:
        # Still nothing to trace on: use the drawing with the most linework that isn't an elevation, section or detail,
        # so Phase 1 always has a plan (labelled; the scale ladder then gives it a scale).
        def linework(number):
            counts = vector_by_page.get(number, {}).get("raw_counts") or {}
            return int(counts.get("lines", 0) or 0) + int(counts.get("curves", 0) or 0)
        pages = [page for page in (ai_input.get("drawing_set", {}) or {}).get("pages", [])
                 if isinstance(page, dict) and type(page.get("page")) is int
                 and read_types.get(str(page["page"])) not in NOT_PLAN_PAGE_TYPES and linework(page["page"])]
        if pages:
            page = max(pages, key=lambda row: (linework(row["page"]), -row["page"]))
            grouped["unassigned level"] = [(0, 6, page["page"], page, "fallback_any_plan")]
            notes.append({"page": page["page"], "status": "any_plan_fallback",
                          "reason": "No floor plan or services plan was found; rooms are traced on the drawing with the most "
                                    "linework (labelled)."})
    selected = []
    for level_key, rows in grouped.items():
        rows.sort(key=lambda row: (-row[0], row[1], row[2]))
        main_rows = [row for row in rows if row[4] in {"main_floor_plan", "primary_geometry_plan"}]
        eligible_rows = ([row for row in rows if row[4] in {"main_floor_plan", "primary_geometry_plan", "floor_plan"}]
                         if main_rows else rows)
        winner = eligible_rows[0]
        selected.append(winner[3])
        if not main_rows:
            notes.append({"page": winner[2], "status": "selected_supporting_plan",
                          "reason": "Selected supporting plan: no main plan on this level."})
        for _count, _priority, _number, page, _role in rows:
            if page["page"] == winner[2]:
                continue
            note={"page": page["page"], "status": "skipped", "reason": f"Skipped second view of {level_key}; page {winner[2]} was selected as the main geometry plan."}
            skipped.append(note)
            notes.append(note)
    # Each chosen plan carries the level its rooms are filed under (_page_level), so every step agrees on it.
    selected = [{**page, "level_name": _page_level(root, page["page"]) or page.get("level_name") or ""} for page in selected]
    return {"selected": sorted(selected, key=lambda row: row["page"]),
            "skipped": sorted(skipped, key=lambda row: row["page"]),
            "notes": sorted(notes, key=lambda row: row["page"])}


def _p0_main_geometry_pages(root):
    return _p0_page_selection(root)["selected"]


def _p0_calibration_ready(root, page, context):
    """The page's scale: printed room areas or printed dimensions agreeing with the sheet scale first; when the
    dimension readings are finished and none could set it, the sheet's printed scale alone, labelled unchecked (user
    decision 2026-10-16: areas must be found without anyone tracing)."""
    printed = _s1_calibration(root, page)
    if printed:
        return printed, ""
    dimensions = _p0_dimensions(root, page)
    if not context.get("declared_mm_per_px") and len(dimensions) < 2:
        reason = "The sheet scale is unreadable; two agreeing printed dimensions are required before outlining rooms."
    else:
        try:
            return _p0_calibration(root, page, context), ""
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = str(error)
    if context.get("declared_mm_per_px") and _p0_dimensions_settled(root, page):
        sheet = _sheet_scale_calibration(root, page, context, reason)
        if sheet:
            return sheet, ""
    if _p0_dimensions_settled(root, page) and _scale_read_settled(root, page):
        scale_record = _current_task(root, "P0_scale_read", f"page-{page}")
        scale = (scale_record or {}).get("applied_value") or {}
        if scale:
            calibration = _scale_read_calibration(scale, context, reason)
            if calibration:
                return calibration, ""
        objects_record = _current_task(root, "P0_scale_objects", f"page-{page}")
        objects = (objects_record or {}).get("applied_value") or {}
        if autonomous_tasks.usable_calibration(objects.get("status")):
            return objects, ""
        if _settled_step(root, "P0_scale_objects", page):
            return _assumed_scale_calibration(context, reason), ""
    return None, reason


def _settled_step(root, task, page):
    record = _current_task(root, task, f"page-{page}")
    return bool(record and (record.get("status") in {"applied", "below_accuracy_bar", "blocked", "not_needed"}
                            or record.get("status") == "waiting_for_reply" and _not_answered(record)))


def _scale_read_settled(root, page):
    return _settled_step(root, "P0_scale_read", page)


ASSUMED_SCALE_DENOMINATOR = 100


def _assumed_scale_calibration(context, reason=""):
    """The last step of the scale ladder: nothing printed or drawn gave a scale, so a typical 1:100 is assumed, labelled
    (the user's rule 2026-11-03: every page ends with a scale)."""
    mm_per_px = 25.4 * ASSUMED_SCALE_DENOMINATOR / float(context.get("render_dpi") or 72)
    return {"source": "assumed_scale", "status": "assumed_scale", "mm_per_px": mm_per_px,
            "label": (f"No scale could be read or estimated from this drawing; a typical 1:{ASSUMED_SCALE_DENOMINATOR} "
                      "is assumed. Check a known length to confirm the areas."), "reason": reason[:300]}


def _scale_read_calibration(value, context, reason=""):
    if autonomous_tasks.usable_calibration(value.get("status")):
        return value            # already worked out when the reply was applied
    mm_per_px = value.get("mm_per_px")
    if value.get("kind") == "notation":
        denominator = value.get("denominator")
        mm_per_px = context.get("render_dpi") and 25.4 * denominator / context["render_dpi"]
        text = value.get("scale_text", "")
        paper = re.search(r"@\s*(A[0-4])\b", text, re.I)
        bbox = context.get("page_bbox") or (0, 0, 0, 0)
        pdf_long = max(float(bbox[2])-float(bbox[0]), float(bbox[3])-float(bbox[1])) * 25.4 / 72
        if paper and pdf_long:
            mm_per_px *= PAPER_LONG_SIDE_MM[paper.group(1).upper()] / pdf_long
    else:
        text = "scale bar"
    if not mm_per_px or not math.isfinite(mm_per_px):
        return None
    left, top, right, bottom = context.get("viewport") or (0, 0, 0, 0)
    length_m = max(right-left, bottom-top) * mm_per_px / 1000
    if not 2 <= length_m <= 400:
        return None
    status = "declared_scale_only" if value.get("kind") == "notation" else "scale_bar"
    return {"source": "ai_read_printed_scale", "status": status, "mm_per_px": mm_per_px,
            "scale_text": text, "label": f"Scale read from drawing: {text}.", "reason": reason[:300]}


def _p0_dimensions_settled(root, page):
    """No dimension reading on this page is still waiting for an answer (none was offered, or each was answered or
    refused twice)."""
    for pointer in (root / "ai_tasks" / "P0_dimensions").glob(f"page-{page}-dimension-*/current.json"):
        record = _read(pointer.parent / _read(pointer, {}).get("run_path", "") / "record.json", {})
        if record.get("status") == "waiting_for_reply" and not _not_answered(record):
            return False
    return True


MAX_DIMENSION_QUESTIONS = 8
MAX_DIMENSION_LINES = 40
PAPER_LONG_SIDE_MM = {"A0": 1189, "A1": 841, "A2": 594, "A3": 420, "A4": 297}


def _sheet_scale_calibration(root, page, context, reason=""):
    """The sheet's printed scale (e.g. "1:50") as the page's scale, corrected when the scale names a paper size the PDF
    was not printed at ("1:100 @ A1" on an A3 PDF), or None when it implies an implausible plan size."""
    declared = float(context["declared_mm_per_px"])
    _ai_input, spatial, _building = _load_inputs(root)
    page_row = next((row for row in spatial.get("pages", []) if row.get("page") == page), {})
    texts = [str(item.get("text", "") if isinstance(item, dict) else item) for item in page_row.get("scale_candidates") or []]
    scale_text = next((text for text in texts if text.strip()), "")
    factor, paper_note = 1.0, ""
    bbox = context.get("page_bbox") or (0, 0, context.get("pdf_page_width") or 0, 0)
    pdf_long_mm = max(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1])) * 25.4 / 72
    stated = next((match.group(1).upper() for text in texts for match in [re.search(r"@\s*(A[0-4])\b", text, re.I)] if match), "")
    if stated and pdf_long_mm > 0 and abs(PAPER_LONG_SIDE_MM[stated] / pdf_long_mm - 1) > 0.05:
        factor = PAPER_LONG_SIDE_MM[stated] / pdf_long_mm
        paper_note = f" at {stated} (this PDF is printed on a different paper size, corrected)"
    mm_per_px = declared * factor
    left, top, right, bottom = context.get("viewport") or (0, 0, 0, 0)
    plan_long_m = max(right - left, bottom - top) * mm_per_px / 1000
    if not 2 <= plan_long_m <= 400:
        return None
    return {"source": "sheet_printed_scale", "status": "declared_scale_only", "mm_per_px": mm_per_px,
            "scale_text": scale_text, "paper_adjustment": round(factor, 4),
            "label": (f"Scaled from the sheet's printed scale {scale_text or ''}{paper_note}; no printed dimension "
                      "could be read to check it.").replace("  ", " "),
            "reason": reason[:300]}


def _s1_calibration(root, page):
    pointer = _task_dir(root, "S1_printed_areas", f"page-{page}") / "current.json"
    record = _read(pointer.parent / _read(pointer, {}).get("run_path", "") / "record.json", {})
    calibration = record.get("calibration")
    return deepcopy(calibration) if record.get("status") in {"applied", "below_accuracy_bar"} and calibration and calibration.get("status") == "agreed" else None


def _apply_s1_areas(web, project, root, record, reply):
    from ai import scan_reading
    packet = record["packet"]
    page = packet["page"]
    context = _p0_context(root, page)
    working_scale = scan_reading.mm_per_px_from_scale(packet.get("working_scale_denominator", 100), packet.get("render_dpi", 180))
    outlines = _scan_outlines(context, working_scale)
    first_pass = scan_reading.validate_area_reply(reply, packet["tiles"], packet["factors"], outlines)
    declared_scale = scan_reading.mm_per_px_from_scale(first_pass.get("scale_denominator"), packet.get("render_dpi", 180))
    if declared_scale:
        outlines = _scan_outlines(context, declared_scale)
    validated = scan_reading.validate_area_reply(reply, packet["tiles"], packet["factors"], outlines)
    if not validated["rooms"]:
        record.update({"status": "blocked", "source": "ai_determined", "applied_value": {},
                       "validation": validated,
                       "block_reason": "Scanned plan with no printed areas or dimensions; we need to enter the room areas."})
        return record
    try:
        calibration = scan_reading.calibration_from_areas(validated["rooms"], outlines,
                                                           declared_mm_per_px=declared_scale)
        calibration_error = ""
    except ValueError as error:
        calibration, calibration_error = None, str(error)
    page_context = next((row for row in _p0_main_geometry_pages(root) if row.get("page") == page), {})
    level = str(page_context.get("level_name") or page_context.get("floor_label") or "Unassigned level")
    rooms = [{"label": row["label"], "area_m2": row["area_m2"],
              "source": "printed (read from image)", "method": "printed_read_from_image",
              "printed_text": row["printed_text"], "page": page, "level_name": level,
              "outline": None, "quality_label": record.get("quality_label", "AI-determined")}
             for row in validated["rooms"]]
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"rooms": rooms, "label": record["quality_label"]},
                   "validation": validated, "calibration": calibration,
                   "calibration_reason": calibration_error, "block_reason": ""})
    return record


def _apply_s3_transcription(record, lines):
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]),
                   "source": "ai_determined", "applied_value": {"lines": lines,
                       "label": record["quality_label"]}, "validation": {"lines": lines}, "block_reason": ""})
    return record


def _p0_is_raster(root, page_number, context, calibration):
    from types import SimpleNamespace
    if "page_bbox" not in context or "pdf_images" not in context:
        raise ValueError("The cached page context is incomplete; rebuild the page context before outlining rooms.")
    page = SimpleNamespace(bbox=context["page_bbox"], images=context["pdf_images"])
    image_scale = context["image"].width / float(context["pdf_page_width"])
    mm_per_px = context.get("declared_mm_per_px") or calibration["mm_per_px"]
    return room_outline.page_is_raster(page, context["viewport"], image_scale, mm_per_px,
                                       objects=context["objects"])


_WALLS_AREAS_CACHE = OrderedDict()
_WALLS_AREAS_CACHE_LIMIT = 64


def _p0_walls_areas(root, page_number, context, calibration, wall_style_ids=None, raster_mode=False):
    mm_per_px = context.get("declared_mm_per_px") or calibration["mm_per_px"]
    active = page_analysis_cache._ACTIVE.get()
    generation = active.generation if active else page_analysis_cache.PageAnalysisCache(
        root, _page_analysis_code_fingerprint()).generation
    key = (generation, int(page_number), bool(raster_mode), tuple(sorted(wall_style_ids or [])), round(float(mm_per_px), 6))
    cached = _WALLS_AREAS_CACHE.get(key)
    if cached is not None:
        _WALLS_AREAS_CACHE.move_to_end(key)
        return cached
    if raster_mode:
        walls = room_outline.raster_wall_geometry(context["image"], context["viewport"], mm_per_px)
    else:
        walls = room_outline.wall_geometry(context["objects"], wall_style_ids or [], context["viewport"])
    areas = room_outline.enclosed_rooms(walls, context["viewport"], mm_per_px)
    if raster_mode:
        from shapely.geometry import LineString
        geometries = list(walls.geoms) if walls is not None and hasattr(walls, "geoms") else ([walls] if walls is not None else [])
        segments = []
        for geometry in geometries:
            polygons = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
            for polygon in polygons:
                if not hasattr(polygon, "exterior"):
                    continue
                for ring in [polygon.exterior, *polygon.interiors]:
                    segments.extend(LineString([start, end]) for start, end in zip(ring.coords, list(ring.coords)[1:])
                                    if math.dist(start, end) >= 10)
    else:
        objects = context["objects"]
        selected_styles = wall_style_ids or []
        segments = room_outline.drawn_segments(objects, selected_styles)
    result = walls, areas, segments
    _WALLS_AREAS_CACHE[key] = result
    while len(_WALLS_AREAS_CACHE) > _WALLS_AREAS_CACHE_LIMIT:
        _WALLS_AREAS_CACHE.popitem(last=False)
    return result


def _roof_packets(root, room_id=None):
    from backend import house_windows_service
    if house_windows_service.is_dwelling(root):
        return []
    ai_input, _spatial, _building = _load_inputs(root)
    site = _latest_site(root)
    try:
        traces = reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    except (OSError, ValueError, TypeError, KeyError):
        traces = []
    room_use = _read(root / "room_use_resolution.json", {})
    comfort_scopes = {"comfort_hvac", "comfort_hvac_with_process_exception"}
    comfort_ids = {str(row.get("room_id")) for row in room_use.get("records", [])
                   if isinstance(row, dict) and row.get("room_id") and row.get("space_scope") in comfort_scopes}
    selected = {}
    for trace in traces:
        if (trace.get("room_id") in comfort_ids and (room_id is None or trace.get("room_id") == room_id)
                and autonomous_tasks.usable_calibration(trace.get("calibration", {}).get("status"))):
            selected.setdefault(trace["room_id"], []).append(trace)
    result = []
    for room_id, parts in selected.items():
        trace=parts[0]
        packet = autonomous_tasks.build_roof_facts(ai_input, site, {
            "room_id": room_id, "room_label": trace.get("room_label"),
            "level_name": trace.get("level_name"), "page": trace.get("page"),
        })
        packet["trace_ids"]=[part.get("trace_id") for part in parts if part.get("trace_id")]
        packet["part_count"]=len(parts)
        packet["pages"]=sorted({part.get("page") for part in parts if part.get("page") is not None})
        prompt = autonomous_tasks.roof_prompt(packet)
        result.append((room_id, packet, prompt, []))
    return result


def _refresh_roof_tasks(root, room_id=None):
    # The AI decides the roof from the drawing facts; when they don't settle it, the labelled most-likely case is used
    # (user decision 2026-10-15). Nobody is asked: the operators can correct it on Walls & roof.
    budget = autonomous_tasks.TASKS["P5_roof"]["budget_chars"]
    for target,packet,prompt,images in _roof_packets(root, room_id=room_id):
        _create_run(root,"P5_roof",target,packet,prompt,images,
            f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened." if len(prompt)>budget else "")


def _p6_kitchen_packets(root, room_id=None):
    """Build a bounded kitchen-equipment packet from current room traces and drawing pages."""
    from backend import house_windows_service
    if house_windows_service.is_dwelling(root):
        return []
    import pdfplumber
    ai_input, spatial, _building = _load_inputs(root)
    room_use = _read(root / "room_use_resolution.json", {})
    comfort_ids = {str(row.get("room_id")) for row in room_use.get("records", [])
                   if isinstance(row, dict) and row.get("room_id")
                   and row.get("space_scope") in {"comfort_hvac", "comfort_hvac_with_process_exception"}}
    try:
        traces = reviewer_room_geometry_service.current_records(
            reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    except (OSError, ValueError, TypeError, KeyError):
        traces = []
    kitchen_traces = [row for row in traces if str(row.get("room_id")) in comfort_ids
                      and "kitchen" in str(row.get("room_label", "")).casefold()
                      and autonomous_tasks.usable_calibration(row.get("calibration", {}).get("status"))
                      and isinstance(row.get("points_image_px"), list) and len(row["points_image_px"]) >= 4]
    if room_id is not None and str(room_id) not in {str(row.get("room_id")) for row in kitchen_traces}:
        return []
    if not kitchen_traces:
        if _p0_settled(root) and not any("kitchen" in str(row.get("label", "")).casefold() for row in _inferred_room_data(root)):
            return [("P6_kitchen", "kitchen", {"task": "P6_kitchen"}, "", [],
                     NotNeeded("No kitchen in this tenancy: the room outlines are finished and none is a kitchen."))]
        return [("P6_kitchen", "kitchen", {"task": "P6_kitchen"}, "", [],
                 "Waiting for room outlines (P0).")]
    trace = kitchen_traces[0]
    page_number = trace.get("page")
    source = Path(str(ai_input.get("source_pdf", "")))
    if not source.is_file():
        return [("P6_kitchen", "kitchen", {"task": "P6_kitchen", "plan_page": page_number}, "", [],
                 "The source PDF is unavailable; the kitchen task needs a plan image.")]
    try:
        xs = [float(point[0]) for point in trace["points_image_px"]]
        ys = [float(point[1]) for point in trace["points_image_px"]]
        if not all(math.isfinite(value) for value in xs + ys):
            raise ValueError("The Kitchen trace has invalid image coordinates.")
        elevation_pages = kitchen_equipment.find_kitchen_elevation_pages(ai_input, spatial)[:2]
        with pdfplumber.open(source) as pdf:
            if type(page_number) is not int or not 1 <= page_number <= len(pdf.pages):
                raise ValueError("The Kitchen trace page is outside the source PDF.")
            plan = pdf.pages[page_number - 1]
            plan_image = plan.to_image(resolution=180).original.convert("RGB")
            scale = plan_image.width / float(plan.width)
            margin = 60
            bbox = (max(0, min(xs) - margin), max(0, min(ys) - margin),
                    min(plan_image.width, max(xs) + margin), min(plan_image.height, max(ys) + margin))
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise ValueError("The Kitchen trace does not define a usable plan crop.")
            region_pt = tuple(value / scale for value in bbox)
            labels = {page_number: kitchen_equipment.page_labels(spatial, page_number, region_pt)}
            words = {page_number: kitchen_equipment.page_words(spatial, page_number, region_pt)}
            for elevation_page in elevation_pages:
                labels[elevation_page] = kitchen_equipment.page_labels(spatial, elevation_page)
                words[elevation_page] = kitchen_equipment.page_words(spatial, elevation_page)
            packet, prompt = kitchen_equipment.build_packet(page_number, bbox, elevation_pages, labels, words)
            plan_crop = plan_image.crop(tuple(int(value) for value in bbox))
            plan_crop.thumbnail((kitchen_equipment.MAX_IMAGE_SIDE, kitchen_equipment.MAX_IMAGE_SIDE))
            images = [_png_bytes(plan_crop)]
            for elevation_page in elevation_pages:
                if not 1 <= elevation_page <= len(pdf.pages):
                    raise ValueError(f"Kitchen elevation page {elevation_page} is outside the source PDF.")
                elevation = pdf.pages[elevation_page - 1].to_image(resolution=180).original.convert("RGB")
                elevation.thumbnail((kitchen_equipment.MAX_IMAGE_SIDE, kitchen_equipment.MAX_IMAGE_SIDE))
                images.append(_png_bytes(elevation))
        return [("P6_kitchen", "kitchen", packet, prompt, images, "")]
    except (OSError, ValueError, IndexError, KeyError, TypeError, ZeroDivisionError) as error:
        return [("P6_kitchen", "kitchen", {"task": "P6_kitchen", "plan_page": page_number}, "", [],
                 f"Kitchen task packet could not be built: {error}")]


def _p0_context(root, page_number):
    """Load the same page/vector context used by the standalone P0 tool."""
    return page_analysis_cache.get_context(root, page_number, lambda: _build_p0_context(root, page_number),
                                           code_fingerprint=_page_analysis_code_fingerprint())


@lru_cache(maxsize=1)
def _page_analysis_code_fingerprint():
    """Fingerprint code that shapes cached page contexts, once per process."""
    source_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative_path in ("ai/room_outline.py", "ai/dimension_wall_matcher.py"):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((source_root / relative_path).read_text(encoding="utf-8").encode("utf-8"))
        digest.update(b"\0")
    builder_source = inspect.getsource(_build_p0_context)
    digest.update(b"backend.autonomous_tasks_service._build_p0_context\0")
    digest.update(builder_source.encode("utf-8"))
    return digest.hexdigest()


def _build_p0_context(root, page_number):
    """Parse and render a plan page once for the project page-analysis cache."""
    import pdfplumber
    ai_input, spatial, _building = _load_inputs(root)
    source = Path(str(ai_input.get("source_pdf", "")))
    if not source.is_file():
        raise ValueError("The source PDF is unavailable; P0 is blocked without a plan image.")
    vector = _read(root / "vector_geometry.json", {})
    page_meta = next((row for row in (vector.get("geometry_key_points") or {}).get("pages", [])
                      if row.get("page") == page_number), {})
    image_system = (page_meta.get("coordinate_systems") or {}).get("image_px", {})
    with pdfplumber.open(source) as pdf:
        pdf_page = pdf.pages[page_number - 1]
        scale = image_system.get("image_width", pdf_page.width * 2.5) / float(pdf_page.width)
        objects = room_outline.extract_page_objects(pdf_page, scale)
        image = pdf_page.to_image(resolution=72 * scale).original
        page_bbox = tuple(pdf_page.bbox)
        image_bounds = ("x0", "top", "x1", "bottom")
        pdf_images = [{key: image_row[key] for key in image_bounds if key in image_row}
                      for image_row in (getattr(pdf_page, "images", None) or [])]
        chars = pdf_page.chars
    viewport = tuple((page_meta.get("plan_viewport") or {}).get("bbox_px") or (0, 0, image.size[0], image.size[1]))
    page_ocr = next((row for row in spatial.get("pages", []) if row.get("page") == page_number), {})
    from ai.dimension_wall_matcher import page_mm_per_px
    declared_candidates = page_ocr.get("scale_candidates", [])
    if not declared_candidates:
        meta = next((row for row in (ai_input.get("drawing_set", {}) or {}).get("pages", []) if row.get("page") == page_number), {})
        title_scale = meta.get("main_scale") or meta.get("scale")
        declared_candidates = [{"text": title_scale}] if title_scale else []
    declared_scale = page_mm_per_px(declared_candidates, render_dpi=72 * scale)
    # Room finding needs the whole floor: grow the window over lines that run past it (lines of 1 m or more).
    viewport = room_outline.grow_plan_viewport(viewport, objects, image.size,
                                               (1000.0 / declared_scale) if declared_scale else 100.0)
    summary = room_outline.style_summary(objects, viewport, declared_scale)
    left, top, right, bottom = viewport
    origin_x, origin_y = page_bbox[:2]
    chars_in_viewport = any(
        left <= (float(char.get("x0", -1)) - origin_x) * scale <= right and
        top <= (float(char.get("top", -1)) - origin_y) * scale <= bottom
        for char in chars
    )
    return {"objects": objects, "image": image, "viewport": viewport, "image_scale": scale,
            "pdf_page_width": float(pdf_page.width),
            "page_origin": tuple(page_bbox[:2]), "page_bbox": page_bbox, "pdf_images": pdf_images,
            "pdf_chars_in_viewport": chars_in_viewport,
            "render_dpi": 72 * scale,
            "declared_scale_candidates": declared_candidates,
            "declared_mm_per_px": declared_scale,
            "summary": summary, "page_meta": page_meta}


def _page_has_text_layer(root, page_number, context=None):
    """Text in vector/OCR metadata or PDF character objects means the page is not image-only."""
    import pdfplumber
    ai_input, spatial, _building = _load_inputs(root)
    page_ocr = next((row for row in spatial.get("pages", []) if row.get("page") == page_number), {})
    for field in ("text", "plain_text"):
        raw = page_ocr.get(field)
        if isinstance(raw, str) and raw.strip():
            return True
    if context:
        left, top, right, bottom = context["viewport"]
        scale = float(context.get("image_scale", 2.5))
        origin_x, origin_y = context.get("page_origin", (0, 0))
        for field in ("word_samples", "standalone_text_items", "title_blocks"):
            for item in page_ocr.get(field, []) or []:
                if not isinstance(item, dict) or not isinstance(item.get("bbox"), list) or len(item["bbox"]) != 4:
                    if str(item).strip():
                        return True
                    continue
                x0, y0, x1, y1 = item["bbox"]
                x0, x1 = (float(x0) - origin_x) * scale, (float(x1) - origin_x) * scale
                y0, y1 = (float(y0) - origin_y) * scale, (float(y1) - origin_y) * scale
                if x1 >= left and x0 <= right and y1 >= top and y0 <= bottom:
                    return True
    if context is not None and "pdf_chars_in_viewport" in context:
        return bool(context["pdf_chars_in_viewport"])
    source = Path(str(ai_input.get("source_pdf", "")))
    if source.is_file():
        with pdfplumber.open(source) as pdf:
            pdf_page = pdf.pages[page_number - 1]
            chars = pdf_page.chars
            if context:
                left, top, right, bottom = context["viewport"]
                scale = float(context.get("image_scale", 2.5))
                origin_x, origin_y = context.get("page_origin", pdf_page.bbox[:2])
                return any(left <= (float(char.get("x0", -1)) - origin_x) * scale <= right and
                           top <= (float(char.get("top", -1)) - origin_y) * scale <= bottom for char in chars)
            return bool(chars)
    return False


def _page_is_raster_for_scan(root, page_number, context):
    try:
        from types import SimpleNamespace
        page_proxy = SimpleNamespace(bbox=context["page_bbox"], images=context["pdf_images"])
        image_scale = context["image"].width / float(context["pdf_page_width"])
        dpi = 72 * image_scale
        mm_per_px = context.get("declared_mm_per_px") or 25.4 * 100 / dpi
        return room_outline.page_is_raster(page_proxy, context["viewport"], image_scale, mm_per_px,
                                           objects=context["objects"])
    except (OSError, ValueError, IndexError, KeyError, TypeError, ZeroDivisionError):
        return False


def _page_dimension_candidates(root, page_number, context):
    _ai_input, spatial, _building = _load_inputs(root)
    vector = _read(root / "vector_geometry.json", {})
    page_ocr = next((row for row in spatial.get("pages", []) if row.get("page") == page_number), {})
    vector_page = next((row for row in (vector.get("geometry_key_points") or {}).get("pages", [])
                        if row.get("page") == page_number), {})
    return (page_ocr.get("dimension_candidates") or []
            or vector_page.get("dimension_candidates") or context.get("page_meta", {}).get("dimension_candidates") or [])


def _scan_outlines(context, mm_per_px):
    """Return raster wall outlines in page-pixel coordinates for S1 label mapping."""
    walls = room_outline.raster_wall_geometry(context["image"], context["viewport"], mm_per_px)
    areas = room_outline.enclosed_rooms(walls, context["viewport"], mm_per_px)
    return areas


def _s1_packets(root):
    from ai import scan_reading
    result = []
    ai_input, _spatial, _building = _load_inputs(root)
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
        try:
            context = _p0_context(root, number)
            has_raster_plan = _page_is_raster_for_scan(root, number, context)
            has_plan_text = _page_has_text_layer(root, number, context)
            if not (has_raster_plan or not has_plan_text) or _page_dimension_candidates(root, number, context):
                continue
            viewport = context["viewport"]
            tiles = scan_reading.area_tiles(viewport)
            factors = [scan_reading.tile_factor(tile) for tile in tiles]
            images = []
            for tile, factor in zip(tiles, factors):
                crop = context["image"].convert("RGB").crop(tuple(int(value) for value in tile))
                if factor < 1:
                    crop = crop.resize((max(1, round(crop.width * factor)), max(1, round(crop.height * factor))))
                images.append(_png_bytes(crop))
            packet = {"task": "S1_printed_areas", "page": number, "tiles": [list(tile) for tile in tiles],
                      "factors": factors, "viewport": list(viewport), "render_dpi": context["render_dpi"],
                      "working_scale_denominator": 100}
            result.append(("S1_printed_areas", f"page-{number}", packet,
                           scan_reading.area_prompt(len(tiles)), images, ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("S1_printed_areas", f"page-{number}", {"task": "S1_printed_areas", "page": number},
                           "", [], f"Scanned-area packet could not be built: {error}"))
    return result


def _image_only_pages(root):
    import pdfplumber
    ai_input, _spatial, _building = _load_inputs(root)
    source = Path(str(ai_input.get("source_pdf", "")))
    if not source.is_file():
        return []
    page_rows = (ai_input.get("drawing_set", {}) or {}).get("pages", [])
    if page_rows and all(isinstance(row, dict) and isinstance(row.get("structured_content"), dict)
                         and isinstance(row["structured_content"].get("word_count"), int)
                         for row in page_rows):
        return sorted({row["page"] for row in page_rows
                       if type(row.get("page")) is int and row["structured_content"]["word_count"] == 0})
    stat = source.stat()
    return _cached_image_only_pages(str(source), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=8)
def _cached_image_only_pages(source, inode, size, mtime_ns, ctime_ns):
    import pdfplumber
    with pdfplumber.open(source) as pdf:
        return tuple(index + 1 for index, page in enumerate(pdf.pages) if not page.chars)


def _s3_packets(root, image_pages=None):
    from ai import scan_reading
    import pdfplumber
    ai_input, _spatial, _building = _load_inputs(root)
    source = Path(str(ai_input.get("source_pdf", "")))
    if not source.is_file():
        return []
    result = []
    with pdfplumber.open(source) as pdf:
        for number in (_image_only_pages(root) if image_pages is None else image_pages):
            page = pdf.pages[number - 1]
            image = page.to_image(resolution=180).original.convert("RGB")
            for crop_info in scan_reading.title_block_crops(*image.size):
                bbox = tuple(int(value) for value in crop_info["bbox"])
                crop = image.crop(bbox)
                crop.thumbnail((1536, 1536))
                packet = {"task": "S3_title_transcription", "page": number, "region": crop_info["region"],
                          "page_bbox": list(bbox)}
                target = f"page-{number}-{crop_info['region']}"
                result.append(("S3_title_transcription", target, packet,
                               scan_reading.transcription_prompt(), [_png_bytes(crop)], ""))
    return result


def _s3_site_packet(root):
    from ai import scan_reading
    transcriptions = {}
    for pointer in (root / "ai_tasks" / "S3_title_transcription").glob("*/current.json"):
        record = _read(pointer.parent / _read(pointer, {}).get("run_path", "") / "record.json", {})
        if record.get("status") not in {"applied", "below_accuracy_bar"}:
            continue
        page = record.get("packet", {}).get("page")
        lines = record.get("applied_value", {}).get("lines")
        if type(page) is int and isinstance(lines, list):
            transcriptions.setdefault(page, []).extend(lines)
    return scan_reading.site_packet_from_transcriptions(transcriptions) if transcriptions else None


def _refresh_site_tasks(root):
    ai_input, _spatial, _building = _load_inputs(root)
    if not Path(str(ai_input.get("source_pdf", ""))).is_file():
        return
    site_packet, site_prompt, _ = _site_packet(root)
    image_pages = _image_only_pages(root)
    if not image_pages or site_packet.get("excerpts"):
        _create_run(root, "P1_site", "project", site_packet, site_prompt,
                    blocked_reason="No cited site excerpts were found." if not site_packet.get("excerpts") else
                    "Prompt exceeds the 4,000-character limit." if len(site_prompt) > 4000 else "")
        return
    for task, target, packet, prompt, images, reason in _s3_packets(root, image_pages):
        _create_run(root, task, target, packet, prompt, images, reason or
                    ("Prompt exceeds the 1,000-character limit; evidence was not shortened."
                     if len(prompt) > 1000 else ""))
    expected = [f"page-{page}-{region}" for page in image_pages
                for region in ("right_strip", "bottom_band")]
    records = [_current_task(root, "S3_title_transcription", target) for target in expected]
    if records and all(row and row.get("status") in {"applied", "below_accuracy_bar"} for row in records):
        scanned = _s3_site_packet(root)
        if scanned:
            packet, prompt = scanned
            _create_run(root, "P1_site", "project", packet, prompt,
                        blocked_reason="No cited site excerpts were found." if not packet.get("excerpts") else
                        "Prompt exceeds the 4,000-character limit." if len(prompt) > 4000 else "")


def _p0_initial_packets(root):
    from PIL import ImageDraw
    result = []
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
        try:
            context = _p0_context(root, number)
            calibration, calibration_error = _p0_calibration_ready(root, number, context)
            lines = _dimension_lines_to_ask(root, number, context) if not calibration else []
            asked_dimension_indices = {int(match.group(1)) for pointer in (root / "ai_tasks" / "P0_dimensions").glob(f"page-{number}-dimension-*/current.json")
                                       if (match := re.search(r"dimension-(\d+)$", pointer.parent.name))}
            asked_count = len(asked_dimension_indices)
            # One line at a time with a sheet scale to check against, two without; the next ones only after those are
            # answered, and at most MAX_DIMENSION_QUESTIONS per page (a detailed plan has dozens of dimension texts).
            wanted = 1 if context["declared_mm_per_px"] else 2
            dimensions_settled = _p0_dimensions_settled(root, number)
            ask_upto = (min(asked_count + wanted, MAX_DIMENSION_QUESTIONS)
                        if dimensions_settled and len(_p0_dimensions(root, number)) < wanted + 1 else asked_count)
            more_lines = len(lines) > asked_count and ask_upto > asked_count
            for index, line in enumerate(lines, start=1):
                if index <= asked_count or index > ask_upto or calibration:
                    continue
                points = line.get("tick_centres_image_px") or line["points"]
                (x1, y1), (x2, y2) = line["points"]
                source = context["image"].convert("RGB")
                ImageDraw.Draw(source).line([(x1, y1), (x2, y2)], fill=(255, 0, 0), width=5)
                pad = 160
                crop = source.crop((int(max(0, min(x1, x2) - pad)), int(max(0, min(y1, y2) - pad)),
                                    int(min(source.size[0], max(x1, x2) + pad)), int(min(source.size[1], max(y1, y2) + pad))))
                crop.thumbnail((1024, 1024))
                packet = {"task": "P0_dimensions", "page": number, "dimension_index": index,
                          "line": {"tick_centres_image_px": points, "span_px": line["span_px"]},
                          "declared_mm_per_px": context["declared_mm_per_px"]}
                result.append(("P0_dimensions", f"page-{number}-dimension-{index}", packet,
                               room_outline.dimension_prompt(), [
                                   _png_bytes(crop)
                               ], ""))
            if not calibration:
                if more_lines or not dimensions_settled:
                    continue            # printed dimensions come first; the printed scale is asked only after them
                scale_record = _current_task(root, "P0_scale_read", f"page-{number}")
                if not scale_record:
                    result.append(("P0_scale_read", f"page-{number}", {"task": "P0_scale_read", "page": number,
                        "viewport": list(context["viewport"]), "render_dpi": context["render_dpi"], "page_bbox": list(context["page_bbox"])},
                        autonomous_tasks.scale_read_prompt(), [_png_bytes(context["image"])], ""))
                elif _scale_read_settled(root, number):
                    objects_record = _current_task(root, "P0_scale_objects", f"page-{number}")
                    if not objects_record:
                        packet = {"task": "P0_scale_objects", "page": number, "viewport": list(context["viewport"])}
                        result.append(("P0_scale_objects", f"page-{number}", packet, autonomous_tasks.scale_objects_prompt(),
                                       [_png_bytes(context["image"])], ""))
                continue
            if _p0_is_raster(root, number, context, calibration):
                # Raster sheets use the detected wall mask and never require a wall-style judgement task.
                continue
            all_styles = [row for row in context["summary"] if not row["hatch"]]
            batches = [all_styles[index:index + 14] for index in range(0, len(all_styles), 14)]
            for batch_index, style_rows in enumerate(batches, start=1):
                style_image, numbered = room_outline.render_styles(context["objects"], style_rows, context["viewport"])
                target = f"page-{number}" if len(batches) == 1 else f"page-{number}-batch-{batch_index}"
                packet = {"task": "P0_wall_styles", "page": number, "summary": numbered,
                          "style_batch_index": batch_index, "style_batch_count": len(batches),
                          "style_set_fingerprint": autonomous_tasks.fingerprint(all_styles),
                          "declared_mm_per_px": context["declared_mm_per_px"]}
                result.append(("P0_wall_styles", target, packet,
                               room_outline.wall_style_prompt(numbered), [_png_bytes(style_image)], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_dimensions", f"page-{number}-dimension-1", {"task":"P0_dimensions", "page":number}, "", [],
                           f"P0 page packet could not be built: {error}"))
    return result


def _png_bytes(image):
    import io
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


def _p0_dimensions(root, page):
    rows = []
    for pointer in sorted((root / "ai_tasks" / "P0_dimensions").glob(f"page-{page}-dimension-*/current.json")):
        record = _read(pointer.parent / _read(pointer, {}).get("run_path", "") / "record.json", {})
        result = record.get("validation", {}).get("result", {}) if record.get("status") in {"applied", "below_accuracy_bar"} else {}
        if result.get("value_mm") and record.get("packet", {}).get("line", {}).get("tick_centres_image_px"):
            rows.append({"points_image_px": record["packet"]["line"]["tick_centres_image_px"],
                         "value_mm": result["value_mm"], "printed_text": result.get("printed_text", ""),
                         "run_id": record.get("run_id"), "stand_in": record.get("stand_in", False)})
    return rows


def _p0_calibration(root, page, context=None):
    dimensions = _p0_dimensions(root, page)
    if context is None:
        context = _p0_context(root, page)
    dimensions = dimensions + _text_layer_dimension_lines(root, page, context)
    calibration = autonomous_tasks.calibration_from_dimensions(dimensions, context.get("declared_mm_per_px"))
    calibration["dimension_count"] = len(dimensions)
    calibration["dimension_evidence"] = deepcopy(dimensions)
    return calibration


def _page_dimension_texts(root, page, context):
    """The dimension numbers printed in the page's text layer, with their boxes in image pixels (the vector reading has
    them; the spatial text reading's boxes are in PDF points)."""
    _ai_input, spatial, _building = _load_inputs(root)
    vector = _read(root / "vector_geometry.json", {})
    vector_page = next((row for row in (vector.get("geometry_key_points") or {}).get("pages", []) if row.get("page") == page), {})
    rows = [row for row in vector_page.get("dimension_candidates") or [] if isinstance(row, dict) and row.get("value_mm") and row.get("bbox_px")]
    if rows:
        return rows
    page_ocr = next((row for row in spatial.get("pages", []) if row.get("page") == page), {})
    factor = float(context.get("render_dpi") or 72) / 72
    left, top = (context.get("page_origin") or (0, 0))[:2]
    return [{**row, "bbox_px": [(row["bbox"][0] - left) * factor, (row["bbox"][1] - top) * factor,
                                (row["bbox"][2] - left) * factor, (row["bbox"][3] - top) * factor]}
            for row in page_ocr.get("dimension_candidates") or []
            if isinstance(row, dict) and row.get("value_mm") and isinstance(row.get("bbox"), list) and len(row["bbox"]) == 4]


_TICK_LINES_CACHE = OrderedDict()


def _tick_lines(context, limit=MAX_DIMENSION_LINES, min_length_px=300):
    """Lines drawn with ticks at both ends, whatever is printed near them. A question rebuild asks several times per
    page, and on a detailed plan each search takes seconds, so they are kept per page's drawn objects."""
    active = page_analysis_cache._ACTIVE.get()
    key = None
    if active is not None and context.get("page_meta", {}).get("page"):
        key = (active.generation, context["page_meta"]["page"], limit, min_length_px)
        if key in _TICK_LINES_CACHE:
            _TICK_LINES_CACHE.move_to_end(key)
            return _TICK_LINES_CACHE[key]
    lines = room_outline.dimension_line_candidates(context["objects"], None, (0, 0, *context["image"].size),
                                                   limit=limit, min_length_px=min_length_px)
    if key:
        _TICK_LINES_CACHE[key] = lines
        while len(_TICK_LINES_CACHE) > 32:
            _TICK_LINES_CACHE.popitem(last=False)
    return lines


DIMENSION_TEXT_MAX_DISTANCE_PX = 60     # about 8 mm on the sheet at the 180 dpi plan image


def _nearest_dimension_text(line, texts):
    """The number printed beside a line: nearest to it and alongside it, or None. (text_line_score isn't a distance
    test: a long line scores above its threshold for any text level with it, however far away.)"""
    from ai.dimension_wall_matcher import bbox_center, point_line_distance, projection_ratio
    points = line.get("tick_centres_image_px") or line["points"]
    start, end = list(points[0]), list(points[1])
    best, best_distance = None, DIMENSION_TEXT_MAX_DISTANCE_PX
    for text in texts:
        center = bbox_center(text.get("bbox_px"))
        if center and 0 <= projection_ratio(center, start, end) <= 1:
            distance = point_line_distance(center, start, end)
            if distance <= best_distance:
                best, best_distance = text, distance
    return best


def _text_layer_dimension_lines(root, page, context):
    """Each tick-ended line with the number printed beside it. calibration_from_dimensions uses these only when they
    agree with the sheet's scale, so without one there is nothing to check them against."""
    if not context.get("declared_mm_per_px"):
        return []
    texts = _page_dimension_texts(root, page, context)
    if not texts:
        return []
    result = []
    for line in _tick_lines(context, min_length_px=80):
        text = _nearest_dimension_text(line, texts)
        if text:
            points = line.get("tick_centres_image_px") or line["points"]
            result.append({"points_image_px": [list(points[0]), list(points[1])], "value_mm": float(text["value_mm"]),
                           "printed_text": str(text.get("text_seen") or text.get("text") or text["value_mm"]),
                           "source": "drawing_text"})
    return result


def _dimension_lines_to_ask(root, page, context):
    """Tick-ended lines to ask the AI to read: those with a number printed beside them first. With no number in the text
    layer at all the others are still asked (CAD text is often drawn as lines, which the AI can read)."""
    lines = _tick_lines(context) or _tick_lines(context, min_length_px=80)
    texts = _page_dimension_texts(root, page, context)
    return sorted(lines, key=lambda line: _nearest_dimension_text(line, texts) is None) if texts else lines


_INFERRED_ROOM_INPUTS = ("ai_input.json", "drawing_coverage.json", "spatial_ocr.json", "vector_geometry.json", "page_inventory.json")


# "Basement floor plan includes Garage, Lift, Bar and Bath." or "Ground floor rooms: Alfresco, Dining, Bed 2": some
# readings list a plan's rooms in one sentence (house set 15, 2026-11-02).
_READ_ROOM_LIST = re.compile(r"\b(?:includes?|rooms?(?:/services)?(?: shown)?:)\s+(.+)$", re.I)
_READ_ROOM = re.compile(r"^([A-Za-z][A-Za-z'/& -]{1,40}?(?: \d{1,2})?)(?:\s+\d{3,5}\s*[x×]\s*\d{3,5}(?:\s*mm)?)?$")


def _reading_room_names(root, page):
    """The room names the page reading listed for a plan ("Bedroom 2 3500x3940" gives "Bedroom 2"). A house's plans
    print most room names as drawn lettering, not text, so the text layer alone gave one "Bedroom" for four bedrooms
    (house set 18, 2026-11-01)."""
    reading = ((_read(root / "page_inventory.json", {}).get("pages") or {}).get(str(page))) or {}
    names = set()
    for item in reading.get("information") or []:
        if not isinstance(item, dict) or item.get("kind") != "room_geometry":
            continue
        what = " ".join(str(item.get("what") or "").split())
        listed = _READ_ROOM_LIST.search(what)
        parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", re.sub(r"\s*\(.*?\)|\.$", "", listed.group(1))) if listed else [what]
        for part in parts:
            match = _READ_ROOM.match(part.strip())
            name = match.group(1).strip() if match else ""
            if name and len(name.split()) <= 4 and not re.search(r"\b(?:dimensions?|area|total|site|mm)\b", name, re.I):
                names.add(name[0].upper() + name[1:])
    return sorted(names)


def _inferred_room_data(root):
    """The rooms named on the drawings. Read from four files only, so it is worked out again only when one of them
    changes: a round of drawing tasks asked for it about 30 times (2026-11-01, house set 18: minutes per round)."""
    root = Path(root).resolve()
    signature = []
    for name in _INFERRED_ROOM_INPUTS:
        try:
            stat = (root / name).stat()
            signature.append((stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
        except FileNotFoundError:
            signature.append(None)
    return deepcopy(_cached_inferred_room_data(root, tuple(signature)))


@lru_cache(maxsize=8)
def _cached_inferred_room_data(root, input_signature):
    """Bounded, in-memory cache; the signature identifies the files read below."""
    from ai import room_inference
    ai_input, spatial, _building = _load_inputs(root)
    inferred = room_inference.infer(ai_input, _read(root / "drawing_coverage.json", {}), spatial,
                                    _read(root / "vector_geometry.json", {}), page_levels=_main_plan_levels(root))
    return [row for row in inferred.get("rooms", []) if row.get("label")]


def _p0_followup_packets(root):
    result = []
    pages = _p0_main_geometry_pages(root)
    for page in pages:
        number = page["page"]
        styles = _p0_wall_style_records(root, number)
        try:
            context = _p0_context(root, number)
            calibration, calibration_error = _p0_calibration_ready(root, number, context)
            if not calibration:
                continue  # Dimension tasks remain available; geometry waits for the required calibration.
            raster_mode = _p0_is_raster(root, number, context, calibration)
            if raster_mode:
                wall_style_ids = []
            else:
                if not styles or any(style.get("status") not in {"applied", "below_accuracy_bar"} for style in styles):
                    continue
                wall_style_ids = sorted({style_id for style in styles for style_id in style["applied_value"].get("wall_style_ids", [])})
                if not wall_style_ids:
                    result.append(("P0_room_names", f"page-{number}", {"task":"P0_room_names", "page":number}, "", [],
                                   "No wall styles were identified across the complete legend; enclosed-room naming is unavailable."))
                    continue
                wall_style_ids, added_styles = _completed_wall_styles(root, context, calibration, wall_style_ids)
            _walls, areas, segments = _p0_walls_areas(root, number, context, calibration, wall_style_ids, raster_mode)
            if not areas:
                continue
            image, transform = room_outline.render_candidate_areas(context["image"], areas, context["viewport"])
            room_names = sorted({row["label"] for row in _inferred_room_data(root)})
            from backend import house_windows_service
            dwelling = house_windows_service.is_dwelling(root)
            read_names = _reading_room_names(root, number) if dwelling else []
            if read_names:
                room_names = sorted({*room_names, *read_names})
            notes = _note_only_rooms(root, room_names)
            naming_packet = {"task":"P0_room_names", "page":number, **({"dwelling":True} if dwelling else {}),
                             **({"read_room_names":read_names} if read_names else {}), "area_count":len(areas), "room_names":room_names,
                             **({"note_only_rooms": notes} if notes else {}),
                             "transform":transform, "calibration":calibration, "areas_px": [list(poly.exterior.coords) for poly in areas],
                             "wall_style_ids":wall_style_ids, "raster_mode":raster_mode,
                             **({"wall_styles_added": added_styles} if not raster_mode and added_styles else {})}
            result.append(("P0_room_names", f"page-{number}", naming_packet,
                           room_outline.room_naming_prompt(room_names, len(areas), notes, dwelling=dwelling), [_png_bytes(image)], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_room_names", f"page-{number}", {"task":"P0_room_names", "page":number}, "", [],
                           f"P0 enclosed-area packet is blocked: {error}"))
    # Open-plan outline packets are built after the naming replies identify
    # which rooms already received enclosed or split areas.
    return result


WALL_STYLE_COMPLETION_FILE = "wall_style_completion.json"


def _completed_wall_styles(root, context, calibration, wall_style_ids):
    """The wall styles the vision task chose, completed when they enclose fewer rooms than the plan has names
    (room_outline.complete_wall_styles). Every place that turns wall styles into enclosed areas uses this, so the
    numbered areas in the naming question are the areas its answer is applied to.

    Worked out once per page and set of inputs and saved in the job: on a detailed plan one enclosed-area pass takes
    about 12 s (Global Exchange), and the drawing tasks rebuild their questions every round."""
    expected = len({row["label"] for row in _inferred_room_data(root)})
    mm_per_px = context.get("declared_mm_per_px") or calibration["mm_per_px"]
    key = autonomous_tasks.fingerprint({"summary": context.get("summary"), "viewport": list(context["viewport"]),
                                        "chosen": sorted(wall_style_ids), "expected": expected,
                                        "mm_per_px": round(float(mm_per_px), 6),
                                        "version": [room_outline.WALL_STYLE_TRIES, room_outline.MIN_MORE_FLOOR_M2]})
    path = Path(root) / "ai_tasks" / WALL_STYLE_COMPLETION_FILE
    saved = _read(path, {})
    if isinstance(saved.get(key), dict):
        return list(saved[key]["styles"]), list(saved[key]["added"])
    styles, added = room_outline.complete_wall_styles(context["objects"], context["viewport"], mm_per_px, wall_style_ids,
                                                      expected, context.get("summary"))
    saved = _read(path, {})
    saved[key] = {"styles": styles, "added": added}
    _write(path, saved)
    return styles, added


def _p0_wall_style_records(root, page):
    """Return the complete current legend batches, ignoring obsolete batches."""
    context = _p0_context(root, page)
    all_styles = [row for row in context["summary"] if not row["hatch"]]
    expected_fingerprint = autonomous_tasks.fingerprint(all_styles)
    batch_count = max(1, (len(all_styles) + 13) // 14)
    records = []
    for pointer in sorted((root / "ai_tasks" / "P0_wall_styles").glob("*/current.json")):
        current = _read(pointer, {})
        record = _read(pointer.parent / current.get("run_path", "") / "record.json", {})
        packet = record.get("packet", {})
        if packet.get("page") == page and packet.get("style_set_fingerprint") == expected_fingerprint:
            records.append(record)
    if not records:
        return []
    selected = [row for row in records if int(row.get("packet", {}).get("style_batch_count", 1)) == batch_count]
    selected.sort(key=lambda row: int(row.get("packet", {}).get("style_batch_index", 1)))
    return selected if [int(row.get("packet", {}).get("style_batch_index", 1)) for row in selected] == list(range(1, batch_count + 1)) else []


# A first outline reply that leaves rooms out is refused and asked again; from this attempt on, the rooms drawn are
# kept and the rest are listed as not drawn (so one stubborn room can't block every other room's area).
OUTLINE_ATTEMPTS_BEFORE_PARTIAL = 2


def _p0_outline_room_names(root, names_record, area_count, requested_rooms=None, *, inferred_rooms=None, dwelling=None):
    """The existing naming/printed-area decision shared by outline and tenancy packets."""
    if inferred_rooms is None:
        inferred_rooms = _inferred_room_data(root)
    if dwelling is None:
        dwelling = bool((names_record or {}).get("packet", {}).get("dwelling"))
    if names_record and area_count:
        names, splits = room_outline.validate_naming_reply(json.loads(names_record["reply"]), area_count,
            names_record["packet"]["room_names"], names_record["packet"]["transform"], dwelling=dwelling)
        placed = {name for name in names.values() if name != room_outline.NOT_A_ROOM}
        placed.update(item["room"] for split in splits for item in split["rooms"])
        room_names = names_record["packet"]["room_names"]
    else:
        placed = set()
        room_names = sorted({row["label"] for row in inferred_rooms})
    printed = {row["label"] for row in inferred_rooms if row.get("area_m2")}
    missing = sorted(set(requested_rooms)) if requested_rooms else sorted(set(room_names) - placed - printed)
    return room_names, missing


def _tenancy_rooms_needing_outline(root, page):
    """Rooms that still use, or would need, the AI outline/corner route. None means unknown coverage."""
    inferred_rooms = _inferred_room_data(root)
    plans = _p0_main_geometry_pages(root)
    if len(plans) > 1:
        plan = next((row for row in plans if row["page"] == page), {})

        def level_name(row):
            value = " ".join(str(row.get("level_name") or row.get("floor_label") or "").casefold().split())
            return "" if value in {"unknown", "unknown level", "unassigned", "unassigned level"} else value

        level = level_name(plan)
        inferred_rooms = [row for row in inferred_rooms if page in (row.get("source_pages") or [])
                          or level_name(row) == level]
    names = _current_task(root, "P0_room_names", f"page-{page}")
    traces = [row for row in reviewer_room_geometry_service.current_records(
        reviewer_room_geometry_service._paths({"review_dir": str(root)})) if row.get("page") == page]
    labels = {row.get("room_label") for row in traces if row.get("room_label")}
    packet = (names or {}).get("packet", {})
    labels.update(packet.get("room_names", []))
    try:
        _, missing = _p0_outline_room_names(root, names if names and names.get("status") in {"applied", "below_accuracy_bar"} else None,
            packet.get("area_count", len(packet.get("areas_px", []))), inferred_rooms=inferred_rooms)
        labels.update(missing)
    except (OSError, ValueError, KeyError, TypeError):
        if not labels:
            return None
    # Include names discovered after the naming answer and pending room/corner/size work.
    labels.update(row["label"] for row in inferred_rooms)
    for record in _all_current(root):
        detail = record.get("packet", {})
        if detail.get("page") != page or record.get("task") not in {"P0_room_outlines", "P0_room_sizes", "P0_outline_corners"}:
            continue
        if record.get("status") not in {"waiting_for_reply", "blocked"}:
            continue
        labels.update(value for value in detail.get("room_names", detail.get("rooms", [])) if isinstance(value, str))
        if isinstance(detail.get("room"), str):
            labels.add(detail["room"])
    protected = {str(row.get("room_label", "")).casefold() for row in traces
                 if row.get("declaration_source") == "reviewer" or row.get("method") in {
                     "enclosed_walls", "printed_area", "printed_read_from_image"}
                 or str(row.get("method", "")).startswith("room_size_")}
    return sorted(label for label in labels if label.casefold() not in protected) if labels else None


TENANCY_NOT_NEEDED = ("Every room on this plan already has an outline from the walls the drawing encloses (or the operators), "
                      "so the tenancy outline isn't needed to trim AI-drawn rooms.")


def _p0_outline_packets(root, requested_rooms=None):
    result = []
    from backend import house_windows_service
    dwelling = house_windows_service.is_dwelling(root)
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
        if not requested_rooms and _tenancy_waiting(root, number):
            continue      # its outlines are clipped to the tenancy once that is answered
        names_record = _current_task(root, "P0_room_names", f"page-{number}")
        if names_record and names_record.get("status") not in {"applied", "below_accuracy_bar"}: continue
        wall_records = _p0_wall_style_records(root, number)
        try:
            context = _p0_context(root, number)
            calibration, _calibration_error = _p0_calibration_ready(root, number, context)
            if not calibration:
                continue
            raster_mode = _p0_is_raster(root, number, context, calibration)
            if raster_mode:
                wall_style_ids = []
            else:
                if (not wall_records or
                        any(row.get("status") not in {"applied", "below_accuracy_bar"} for row in wall_records)):
                    continue
                wall_style_ids = sorted({style_id for row in wall_records
                                         for style_id in row.get("applied_value", {}).get("wall_style_ids", [])})
                if not wall_style_ids:
                    continue
                wall_style_ids, _added = _completed_wall_styles(root, context, calibration, wall_style_ids)
            _walls, areas, _segments = _p0_walls_areas(root, number, context, calibration,
                                                        wall_style_ids, raster_mode)
            _room_names, missing = _p0_outline_room_names(root, names_record, len(areas), requested_rooms, dwelling=dwelling)
            if not missing:
                continue
            image, transform = room_outline.render_candidate_areas(context["image"], [], context["viewport"])
            references, reference_images = _p0_name_sheets(root, number, missing)
            notes = _note_only_rooms(root, missing)
            prompt = room_outline.outline_prompt(missing, references, notes, dwelling=dwelling)
            packet = {"task":"P0_room_outlines", "page":number, **({"dwelling":True} if dwelling else {}), "room_names":missing, "transform":transform,
                      "calibration":calibration, "wall_style_ids":wall_style_ids, "raster_mode":raster_mode,
                      "name_sheets":references, **({"note_only_rooms": notes} if notes else {})}
            result.append(("P0_room_outlines", f"page-{number}", packet, prompt,
                           [_png_bytes(image), *reference_images], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_room_outlines", f"page-{number}", {"task":"P0_room_outlines", "page":number}, "", [],
                           f"Open-plan outline packet is blocked: {error}"))
    return result


# Where a room's area came from when no outline could measure it (P0_room_sizes), as shown in the result.
ROOM_SIZE_SOURCES = {"printed_area": "printed (read from image)", "printed_dimensions": "printed dimensions",
                     "estimated": "AI estimate from the plan", "remainder": "tenancy area remainder"}
OUTLINE_STEPS = {"P0_dimensions", "P0_wall_styles", "P0_room_names", "P0_tenancy_outline", "P0_room_outlines", "P0_unnamed_space",
                 "P0_outline_corners", "S1_printed_areas"}


def _p0_settled(root):
    """The room-outline steps have answered (or given up): traced rooms exist and no outline question is still to send."""
    try:
        traced = reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return bool(traced) and not any(record.get("task") in OUTLINE_STEPS and record.get("status") == "waiting_for_reply"
                                    and not _not_answered(record) for record in _all_current(root))


def _note_only_rooms(root, names):
    """{name: the note line} for the listed rooms known only from a note or finish legend (every inferred row with that
    name is note-only); the AI steps are told they may not be in the tenancy."""
    rows = {}
    for row in _inferred_room_data(root):
        rows.setdefault(str(row.get("label", "")).casefold(), []).append(row)
    wanted = {str(name).casefold(): name for name in names}
    return {wanted[key]: next((row.get("note_text") for row in group if row.get("note_text")), "")
            for key, group in rows.items() if key in wanted and all(row.get("label_evidence") == "note" for row in group)}


def rooms_not_on_plan(root):
    """Rooms known only from a note or legend that the plan doesn't show: the outline step was asked to draw them (and
    has answered or given up), and no answer gives them NOTE_ONLY_MIN_M2 or more of floor. They are left out of the room
    list, labelled (user decision 2026-11-02). Worked out from the current answers each time, so a later answer that
    finds the room (the floor with no room step naming a real Shop or Kitchen) brings it back.
    Returns {name (casefolded): {"label", "note", "pages", "largest_area_m2", "reason"}}."""
    root = Path(root)
    inferred = _inferred_room_data(root)
    candidates = _note_only_rooms(root, [row["label"] for row in inferred if row.get("label")])
    if not candidates:
        return {}
    # An operator's trace or typed area always keeps the room.
    kept = {str(row.get("room_label") or row.get("label") or "").casefold()
            for row in _read(root / "reviewer_room_geometry.json", {}).get("records", [])
            if isinstance(row, dict) and row.get("declaration_source", "reviewer") == "reviewer"}
    kept |= {str(row.get("room_label") or row.get("label") or "").casefold()
             for row in _read(root / "room_area_overrides.json", {}).get("overrides", []) if isinstance(row, dict)}
    asked, areas = set(), {}
    for record in _all_current(root):
        if record.get("task") not in OUTLINE_STEPS | {"P0_room_sizes"}:
            continue
        status = record.get("status")
        answered = status in {"applied", "below_accuracy_bar"} or _not_answered(record)
        if record.get("task") == "P0_room_outlines" and answered:
            asked.update(str(name).casefold() for name in (record.get("packet") or {}).get("room_names", []))
        for row in (record.get("applied_value") or {}).get("rooms", []) if status in {"applied", "below_accuracy_bar"} else []:
            if isinstance(row, dict) and isinstance(row.get("area_m2"), (int, float)):
                key = str(row.get("label", "")).casefold()
                areas[key] = max(areas.get(key, 0.0), float(row["area_m2"]))
    result = {}
    for name, note in candidates.items():
        key = name.casefold()
        largest = areas.get(key, 0.0)
        if key not in asked or key in kept or largest >= room_outline.NOTE_ONLY_MIN_M2:
            continue
        pages = sorted({page for row in inferred if str(row.get("label", "")).casefold() == key for page in row.get("source_pages", [])})
        found = f"only {largest:.2f} m² of floor was found for it" if largest else "the plan shows no such room"
        result[key] = {"label": name, "note": note, "pages": pages, "largest_area_m2": round(largest, 2),
                       "reason": f"{name}: mentioned only in a general note or legend (page{'s' if len(pages) != 1 else ''} "
                                 f"{', '.join(map(str, pages))}: \"{note}\"), and {found}; not counted as a room."}
    return result


def _settled(record):
    """Answered, or given up on (refused twice, or blocked)."""
    return (not record or record.get("status") in {"applied", "below_accuracy_bar", "blocked", "failed", "not_needed"}
            or len(record.get("reply_attempts") or []) >= 2)


def _corner_subjects(root, page):
    """What the AI sketched on a plan page that has no corner-built outline yet: [(target, room or None, source task,
    [sketch pieces as point lists])]. The tenancy comes first; rooms only once its corners are settled."""
    subjects = []
    tenancy = _current_task(root, "P0_tenancy_outline", f"page-{page}")
    if tenancy and tenancy.get("status") in {"applied", "below_accuracy_bar"} and tenancy.get("applied_value", {}).get("points") and _tenancy_rooms_needing_outline(root, page) != []:
        subjects.append((f"tenancy-page-{page}", None, "P0_tenancy_outline", [tenancy["applied_value"]["points"]]))
    if subjects:
        corner = _current_task(root, "P0_outline_corners", f"tenancy-page-{page}")
        if corner is None or not _settled(corner):
            return subjects   # rooms are clipped to the corner-built tenancy, so they wait for it
    for task in ("P0_room_outlines", "P0_unnamed_space"):
        record = _current_task(root, task, f"page-{page}")
        if not record or record.get("status") not in {"applied", "below_accuracy_bar"}:
            continue
        value = record.get("validation")
        value = value.get("result", value) if isinstance(value, dict) else value     # the reply handler wraps it
        value = value.get("spaces", []) if isinstance(value, dict) else value
        pieces = {}
        for row in value if isinstance(value, list) else []:
            if isinstance(row, dict) and row.get("room") and len(row.get("points") or []) >= 3:
                pieces.setdefault(row["room"], []).append(row["points"])
        removed = rooms_not_on_plan(root)
        for room, sketches in sorted(pieces.items()):
            if room.casefold() in removed:
                continue        # a note-only room the plan doesn't show gets no corner question
            subjects.append((f"room-{re.sub(r'[^a-z0-9]+', '-', room.casefold()).strip('-')}-page-{page}", room, task, sketches))
    return subjects


def _p0_corner_packets(root, force=False):
    """Each AI sketch (the tenancy, open-plan rooms, floor with no room) is redrawn from the drawing's own corners: the
    plan around the sketch with its drawn corners numbered, and the AI lists them in order (2026-10-24)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    result = []
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
        subjects = _corner_subjects(root, number)
        if not subjects:
            continue
        try:
            context = _p0_context(root, number)
            calibration, _error = _p0_calibration_ready(root, number, context)
            if not calibration or _p0_is_raster(root, number, context, calibration):
                continue            # an image-only plan has no drawn corners; its sketches stay
            mm_per_px = float(context.get("declared_mm_per_px") or calibration["mm_per_px"])
            wall_style_ids = sorted({style_id for row in _p0_wall_style_records(root, number)
                                     for style_id in row.get("applied_value", {}).get("wall_style_ids", [])})
            if wall_style_ids:
                wall_style_ids, _added = _completed_wall_styles(root, context, calibration, wall_style_ids)
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_outline_corners", f"tenancy-page-{number}", {"task": "P0_outline_corners", "page": number},
                           "", [], f"The corner question is blocked: {error}"))
            continue
        for target, room, source, sketches in subjects:
            current = _current_task(root, "P0_outline_corners", target)
            if (not force and current and current.get("packet", {}).get("sketches") == sketches
                    and current.get("status") in {"applied", "below_accuracy_bar", "waiting_for_reply"}):
                continue
            shapes = [Polygon(points).buffer(0) for points in sketches]
            union = unary_union([shape for shape in shapes if not shape.is_empty])
            if union.is_empty:
                continue
            main = max(getattr(union, "geoms", [union]), key=lambda part: part.area)
            # The tenancy is the whole floor: show the whole plan, as its sketch may have left part of it out. A room is
            # shown within the corner-built tenancy (its sketch may be a fragment of it), else around its sketch.
            tenancy = _tenancy_polygon(root, number) if room is not None else None
            if room is None:
                region = tuple(context["viewport"])
            elif tenancy is not None:
                region = room_outline.corner_region(unary_union([tenancy, union]), context["viewport"], mm_per_px, margin_mm=1000.0)
            else:
                region = room_outline.corner_region(union, context["viewport"], mm_per_px)
            corners, kinds = room_outline.corner_candidates(context["objects"], wall_style_ids, region, mm_per_px, with_kinds=True)
            subject = room or "the tenancy"
            if len(corners) < 3:
                # Nothing to snap to: the AI's sketch stays as the outline, labelled (it is never left blocked).
                result.append(("P0_outline_corners", target, {"task": "P0_outline_corners", "page": number, "room": room},
                               "", [], NotNeeded(f"Fewer than three drawn corners near the sketch of {subject}; the AI's "
                                                 "sketch is kept as its outline, not snapped to drawn corners.")))
                continue
            image, transform = room_outline.render_numbered_corners(context["image"], corners, region,
                                                                    list(main.exterior.coords)[:-1], kinds=kinds)
            packet = {"task": "P0_outline_corners", "page": number, "room": room, "source_task": source,
                      "sketches": sketches, "sketch": [[round(x, 2), round(y, 2)] for x, y in list(main.exterior.coords)[:-1]],
                      "sketch_area_px": round(union.area, 1), "corners": [[round(x, 2), round(y, 2)] for x, y in corners],
                      "corner_kinds": kinds, **({"tenancy_area_px": round(tenancy.area, 1)} if tenancy is not None else {}),
                      "transform": transform, "calibration": calibration}
            result.append(("P0_outline_corners", target, packet, room_outline.corner_outline_prompt(subject, len(corners)),
                           [_png_bytes(image)], ""))
    return result


def _p0_tenancy_packets(root, force=False):
    """Ask after naming, only when current rooms need the AI-drawn outline route."""
    result = []
    from backend import house_windows_service
    dwelling = house_windows_service.is_dwelling(root)
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
        current = _current_task(root, "P0_tenancy_outline", f"page-{number}")
        needed = _tenancy_rooms_needing_outline(root, number)
        if needed == []:
            if current and current.get("status") in {"waiting_for_reply", "blocked"}:
                current.update(status="not_needed", block_reason=TENANCY_NOT_NEEDED,
                               applied_value={"note": TENANCY_NOT_NEEDED}, finished_at=_now())
                _write(_record_file(root, current), current)
            continue
        names = _current_task(root, "P0_room_names", f"page-{number}")
        if not needed or not names or names.get("status") not in {"applied", "below_accuracy_bar"}:
            continue
        if not force and current and current.get("status") in {"applied", "below_accuracy_bar", "waiting_for_reply"}:
            continue
        try:
            context = _p0_context(root, number)
            calibration, _error = _p0_calibration_ready(root, number, context)
            if not calibration:
                continue
            image, transform = room_outline.render_candidate_areas(context["image"], [], context["viewport"])
            packet = {"task": "P0_tenancy_outline", "page": number, "transform": transform, "calibration": calibration}
            result.append(("P0_tenancy_outline", f"page-{number}", packet, room_outline.tenancy_outline_prompt(dwelling=dwelling),
                           [_png_bytes(image)], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_tenancy_outline", f"page-{number}", {"task": "P0_tenancy_outline", "page": number}, "", [],
                           f"The tenancy outline is blocked: {error}"))
    return result


def _tenancy_waiting(root, page):
    record = _current_task(root, "P0_tenancy_outline", f"page-{page}")
    return bool(record and record.get("status") == "waiting_for_reply" and len(record.get("reply_attempts") or []) < 2)


def _tenancy_polygon(root, page):
    """The tenancy outline built from drawn corners the AI picked, or None (then AI-drawn rooms aren't clipped).

    The freehand tenancy sketch is not used for clipping: on Casa Nova (2026-10-24) it ran over the paving and cut
    diagonally across the dining area, and clipping to it removed about 40 m² of real floor."""
    from shapely.geometry import Polygon
    record = _current_task(root, "P0_outline_corners", f"tenancy-page-{page}")
    points = (record or {}).get("applied_value", {}).get("points") if (record or {}).get("status") in {"applied", "below_accuracy_bar"} else None
    if not points or len(points) < 3:
        return None
    polygon = Polygon(points).buffer(0)
    return polygon if polygon.geom_type == "Polygon" and not polygon.is_empty else None


def _apply_p0_outline_corners(web, project, root, record, validated, *, defer=False):
    """The outline built from the picked corners: the tenancy becomes the clipping outline; a room's AI-sketched trace
    is replaced, clipped to the tenancy, with rooms drawn inside it taken off (all traced rooms, for floor with no
    room)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    packet = record["packet"]
    page = packet["page"]
    mm_per_px = float(packet["calibration"]["mm_per_px"])
    parts = [Polygon(points).buffer(0) for points in validated["parts"]]
    parts = [part for part in parts if part.geom_type == "Polygon" and not part.is_empty]
    note = ("Built from drawn corners the AI picked"
            + ("; the outline crossed itself and was split where it crossed" if validated.get("repaired") else "")
            + (f"; sides with no drawn corner: {validated['missing_sides']}" if validated.get("missing_sides") else ""))
    if packet.get("room") is None:
        tenancy = max(parts, key=lambda part: part.area)
        area = room_outline.area_m2(tenancy, mm_per_px)
        if area < room_outline.TENANCY_MIN_M2:
            raise ValueError(f"The listed corners enclose only {area:.1f} m²; list the corners all the way around the tenancy.")
        record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                       "applied_value": {"points": [[round(float(x), 2), round(float(y), 2)] for x, y in tenancy.exterior.coords][:-1],
                                         "area_m2": round(area, 1), "method": "corners", "note": note,
                                         "label": record["quality_label"]},
                       "validation": validated, "block_reason": ""})
        return record
    room = packet["room"]
    tenancy = _tenancy_polygon(root, page)
    clipped = [piece for part in parts for piece in room_outline.clip_to_tenancy(part, tenancy, mm_per_px, min_m2=0)]
    others = [(row["room_label"], Polygon(row["points_image_px"]).buffer(0))
              for row in reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths(project))
              if row.get("page") == page and row.get("room_label") != room and len(row.get("points_image_px") or []) >= 4]
    minimum = room_outline.UNNAMED_SPACE_MIN_M2 if packet.get("source_task") == "P0_unnamed_space" else room_outline.MIN_ROOM_M2
    polygons = []
    for piece in clipped:
        if packet.get("source_task") == "P0_unnamed_space":
            net = piece.difference(unary_union([shape for _label, shape in others])) if others else piece
        else:
            net = room_outline.subtract_nested([(room, piece), *others])[0][0]
        polygons.extend({"label": room, "polygon": shape, "method": "ai_corner_outline", "note": note}
                        for shape in getattr(net, "geoms", [net])
                        if shape.geom_type == "Polygon" and not shape.is_empty and room_outline.area_m2(shape, mm_per_px) >= minimum)
    if not polygons:
        raise ValueError(f"The listed corners leave no floor for {room} inside the tenancy and outside the other rooms.")
    rows = _p0_trace_rows(root, record, polygons)
    prepared = reviewer_room_geometry_service.persist_ai_determined_traces(web, project, rows, record["run_id"],
        record["quality_label"], replace_pages=[page], replace_room_labels=[room], defer=defer)
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"rooms": _p0_result_rooms(prepared), "note": note, "label": record["quality_label"]},
                   "validation": validated, "block_reason": ""})
    return record


def _apply_p0_tenancy_outline(root, record, validated):
    """Snap the tenancy outline to the drawn lines and keep it when it is at least a small room's size."""
    from shapely.geometry import Polygon
    packet = record["packet"]
    mm_per_px = float(packet["calibration"]["mm_per_px"])
    context = _p0_context(root, packet["page"])
    styles = [row["style_id"] for row in context.get("summary", []) if not row.get("hatch")]
    segments = room_outline.drawn_segments(context["objects"], styles, min_length_px=1000.0 / mm_per_px)
    polygon, report = room_outline.snap_polygon(validated["points"], segments, 250 / mm_per_px)
    if not polygon.is_valid or polygon.is_empty:
        polygon, report = Polygon(validated["points"]), {"snapped_edges": 0, "edges": len(validated["points"])}
    area = room_outline.area_m2(polygon, mm_per_px)
    if area < room_outline.TENANCY_MIN_M2:
        raise ValueError(f"The tenancy outline encloses only {area:.1f} m²; outline the whole floor inside the shopfront and outer walls.")
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"points": [[round(float(x), 2), round(float(y), 2)] for x, y in polygon.exterior.coords][:-1],
                                     "area_m2": round(area, 1), "repaired": validated["repaired"],
                                     "evidence": validated["evidence"], "snapped_edges": report["snapped_edges"],
                                     "edges": report["edges"], "label": record["quality_label"]},
                   "validation": validated, "block_reason": ""})
    return record


def _p0_unnamed_space_packets(root, force=False):
    """Floor with no room (2026-10-23): once the naming and outline steps are answered, show each main plan with its
    traced rooms tinted and ask once for any untinted room-sized floor, named by its use. Drawings often leave the main
    dining or sales space unlabelled (Casa Nova: 53 m² of rooms traced in a tenancy of about 250 m²)."""
    if not force and any(record.get("task") in OUTLINE_STEPS - {"P0_unnamed_space"} and record.get("status") == "waiting_for_reply"
                         and len(record.get("reply_attempts") or []) < 2 for record in _all_current(root)):
        return []
    from shapely.geometry import Polygon
    result = []
    from backend import house_windows_service
    dwelling = house_windows_service.is_dwelling(root)
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
        if not force and _tenancy_waiting(root, number):
            continue
        current = _current_task(root, "P0_unnamed_space", f"page-{number}")
        if not force and current and current.get("status") in {"applied", "below_accuracy_bar", "waiting_for_reply"}:
            continue
        try:
            context = _p0_context(root, number)
            calibration, _error = _p0_calibration_ready(root, number, context)
            if not calibration:
                continue
            traces = [row for row in reviewer_room_geometry_service.current_records(
                          reviewer_room_geometry_service._paths({"review_dir": str(root)}))
                      if row.get("page") == number and len(row.get("points_image_px") or []) >= 4]
            rooms = [(row["room_label"], Polygon(row["points_image_px"]).buffer(0)) for row in traces]
            image, transform = room_outline.render_traced_rooms(context["image"], [(label, polygon) for label, polygon in rooms
                                                                                   if polygon.geom_type == "Polygon"],
                                                                context["viewport"])
            names = sorted({label for label, _polygon in rooms})
            packet = {"task": "P0_unnamed_space", "page": number, **({"dwelling": True} if dwelling else {}), "traced_names": names, "transform": transform,
                      "calibration": calibration}
            result.append(("P0_unnamed_space", f"page-{number}", packet, room_outline.unnamed_space_prompt(names, dwelling=dwelling),
                           [_png_bytes(image)], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_unnamed_space", f"page-{number}", {"task": "P0_unnamed_space", "page": number}, "", [],
                           f"The floor-with-no-room check is blocked: {error}"))
    return result


def _p0_size_packets(root, requested_rooms=None):
    """The last area step (user decision 2026-10-16): once no outline step is still waiting, every room without an
    area is asked for in one call — printed area, printed dimensions, or measured off the plan at its scale."""
    pages = _p0_main_geometry_pages(root)
    if not pages:
        return []
    if not requested_rooms and any(record.get("task") in OUTLINE_STEPS and record.get("status") == "waiting_for_reply"
           and len(record.get("reply_attempts") or []) < 2 for record in _all_current(root)):
        return []
    names = sorted({row["label"] for row in _inferred_room_data(root)})
    have = {str(row.get("room_label", "")).casefold()
            for row in reviewer_room_geometry_service.current_traced_areas(root).values() if row.get("area_m2")}
    missing = list(requested_rooms) if requested_rooms else [name for name in names if name.casefold() not in have]
    if not missing:
        return []
    number = pages[0]["page"]
    try:
        context = _p0_context(root, number)
        image, transform = room_outline.render_candidate_areas(context["image"], [], context["viewport"])
        calibration, _reason = _p0_calibration_ready(root, number, context)
        scale = (calibration or {}).get("mm_per_px") or context.get("declared_mm_per_px")
        scale_label = ((calibration or {}).get("label") or ("printed dimensions" if calibration else "the sheet's printed scale"))
        references, reference_images = _p0_name_sheets(root, number, missing)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        return [("P0_room_sizes", f"page-{number}", {"task": "P0_room_sizes", "page": number}, "", [],
                 f"The plan page could not be prepared for the room sizes: {error}")]
    mm_per_image_px = round(scale / transform["factor"], 3) if scale else None
    packet = {"task": "P0_room_sizes", "page": number, "room_names": missing, "name_sheets": references,
              "mm_per_image_px": mm_per_image_px, "level_name": str(pages[0].get("level_name") or "Unassigned level")}
    prompt = autonomous_tasks.room_size_prompt(missing, references, mm_per_image_px, scale_label)
    return [("P0_room_sizes", f"page-{number}", packet, prompt, [_png_bytes(image), *reference_images], "")]


def _apply_p0_sizes(web, project, root, record, validated):
    """Save each room's area (no outline) with where it came from; one room left over takes the tenancy's printed
    total area less every other room's."""
    packet = record["packet"]
    page, level = packet.get("page"), packet.get("level_name") or "Unassigned level"
    room = lambda label, area, basis, text: {"label": label, "area_m2": round(area, 2), "source": ROOM_SIZE_SOURCES[basis],
                                             "method": f"room_size_{basis}", "printed_text": text, "page": page,
                                             "level_name": level, "outline": None,
                                             "quality_label": record.get("quality_label", "AI-determined")}
    rooms = [room(row["room"], row["area_m2"], row["basis"], row["evidence"]) for row in validated["rooms"]]
    answered = {row["label"].casefold() for row in rooms} | {row["room"].casefold() for row in validated["not_found"]}
    left = [name for name in packet.get("room_names", []) if name.casefold() not in answered]
    tenancy = validated.get("tenancy_area_m2")
    if tenancy and len(left) == 1:
        others = {str(row.get("room_label", "")).casefold(): float(row["area_m2"])
                  for row in reviewer_room_geometry_service.current_traced_areas(root).values()
                  if isinstance(row.get("area_m2"), (int, float)) and str(row.get("room_label", "")).casefold() != left[0].casefold()}
        others.update({row["label"].casefold(): row["area_m2"] for row in rooms})
        remainder = tenancy - sum(others.values())
        if remainder >= 0.5:
            rooms.append(room(left[0], remainder, "remainder",
                              f"{validated['tenancy_evidence']} less the other rooms ({sum(others.values()):.1f} m²)"))
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"rooms": rooms, "not_found": validated["not_found"], "tenancy_area_m2": tenancy,
                                     "label": record.get("quality_label", "AI-determined")},
                   "validation": validated, "block_reason": ""})
    return record


MAX_NAME_SHEETS = 2


def _p0_name_sheets(root, plan_page, rooms, max_side=1536):
    """The other sheets that print these room names (floor plans first, then ceiling plans and the rest), fewest
    first that name the most rooms, at most MAX_NAME_SHEETS: ([{"page", "title", "rooms"}], [png bytes]).
    Rooms named on the plan itself need no sheet."""
    from PIL import Image
    wanted = {name.casefold(): name for name in rooms}
    pages, named_on_plan = {}, set()
    for row in _inferred_room_data(root):
        name = wanted.get(str(row.get("label", "")).casefold())
        if not name:
            continue
        for item in row.get("evidence") or []:
            page = item.get("page") if isinstance(item, dict) else None
            if page == plan_page:
                named_on_plan.add(name)
            elif type(page) is int:
                entry = pages.setdefault(page, {"page": page, "title": str(item.get("title") or "")[:80],
                                                "floor_plan": item.get("view_type") == "floor_plan", "rooms": set()})
                entry["rooms"].add(name)
    left = set(rooms) - named_on_plan
    chosen, images = [], []
    while left and len(chosen) < MAX_NAME_SHEETS:
        best = max(pages.values(), key=lambda row: (len(row["rooms"] & left), row["floor_plan"], -row["page"]), default=None)
        if not best or not best["rooms"] & left:
            break
        source = autonomous_tasks._page_screenshot(root, best["page"])
        pages.pop(best["page"])
        if not source:
            continue
        with Image.open(source) as opened:
            sheet = opened.convert("RGB")
        sheet.thumbnail((max_side, max_side))
        names = sorted(best["rooms"] & set(rooms))
        chosen.append({"page": best["page"], "title": best["title"], "rooms": names})
        images.append(_png_bytes(sheet))
        left -= best["rooms"]
    return chosen, images


def _trace_task_image(root, trace, edge_rows=None, max_side=1536):
    from PIL import Image, ImageDraw
    page = int(trace["page"])
    source = autonomous_tasks._page_screenshot(root, page)
    if not source:
        raise ValueError(f"No rendered plan is available for page {page}.")
    with Image.open(source) as original:
        image = original.convert("RGB")
    points = trace.get("points_image_px", [])
    xs, ys = [point[0] for point in points[:-1]], [point[1] for point in points[:-1]]
    pad = 120
    box = (max(0, int(min(xs)-pad)), max(0, int(min(ys)-pad)),
           min(image.width, int(max(xs)+pad)), min(image.height, int(max(ys)+pad)))
    image = image.crop(box)
    draw = ImageDraw.Draw(image)
    shifted = [(point[0]-box[0], point[1]-box[1]) for point in points]
    draw.line(shifted, fill=(220, 35, 50), width=5, joint="curve")
    for row in edge_rows or []:
        index = row["index"]
        start, end = shifted[index], shifted[index+1]
        middle = ((start[0]+end[0])/2, (start[1]+end[1])/2)
        draw.ellipse((middle[0]-14,middle[1]-14,middle[0]+14,middle[1]+14), fill=(255,255,255), outline=(0,0,0), width=2)
        draw.text((middle[0]-5,middle[1]-8), str(index+1), fill=(0,0,0))
    image.thumbnail((max_side,max_side))
    return image


def _perimeter_task_image(root, traces, perimeter, asked, max_side=1536):
    """Draw the complete page union and numbered questions in one shared crop."""
    from PIL import Image, ImageDraw
    source = autonomous_tasks._page_screenshot(root, traces[0]["page"])
    if not source:
        raise ValueError(f"No rendered plan is available for page {traces[0]['page']}.")
    points = [point for trace in traces for point in trace["points_image_px"]]
    points += [point for segments in perimeter["run_segments"].values() for segment in segments for point in segment]
    with Image.open(source) as original:
        box = (max(0, math.floor(min(p[0] for p in points) - 120)),
               max(0, math.floor(min(p[1] for p in points) - 120)),
               min(original.width, math.ceil(max(p[0] for p in points) + 120)),
               min(original.height, math.ceil(max(p[1] for p in points) + 120)))
        image = original.convert("RGB").crop(box)
    source_size = image.size
    image.thumbnail((max_side, max_side))
    sx, sy = image.width / source_size[0], image.height / source_size[1]
    def position(point):
        return ((point[0] - box[0]) * sx, (point[1] - box[1]) * sy)
    draw = ImageDraw.Draw(image)
    for trace in traces:
        draw.line([position(p) for p in trace["points_image_px"]], fill=(40, 110, 210), width=1)
    for segments in perimeter["run_segments"].values():
        for segment in segments:
            draw.line([position(p) for p in segment], fill=(220, 35, 50), width=4)
    runs = {row["run_index"]: row for row in perimeter["runs"]}
    markers = []
    for row in asked:
        run = runs[row["index"]]
        midpoint = [(a + b) / 2 for a, b in zip(run["start_px"], run["end_px"])]
        x, y = position(midpoint)
        number = row["index"] + 1
        draw.ellipse((x-14, y-14, x+14, y+14), fill="white", outline="black", width=2)
        draw.text((x, y), str(number), fill="black", anchor="mm")
        markers.append({"run_number": number, "page_px": midpoint, "image_px": [x, y]})
    return image, {"page_bbox": list(box), "width_px": image.width, "height_px": image.height, "markers": markers}


def _p4_elevation_image(root, page_number):
    """Render one bounded elevation view, excluding the sheet title block/details."""
    from PIL import Image
    source = autonomous_tasks._page_screenshot(root, page_number)
    if source:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    else:
        import pdfplumber
        ai_input, _spatial, _building = _load_inputs(root)
        pdf_path = Path(str(ai_input.get("source_pdf", "")))
        if not pdf_path.is_file():
            raise ValueError(f"No rendered elevation image or source PDF is available for page {page_number}.")
        with pdfplumber.open(pdf_path) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                raise ValueError(f"Elevation page {page_number} is outside the source PDF.")
            image = pdf.pages[page_number - 1].to_image(resolution=180).original.convert("RGB")
    # Card P4 packets are elevation-only crops. The selected page role already
    # identifies the elevation; the upper half is the principal view on the
    # supported sheet layout and avoids title blocks and secondary details.
    width, height = image.size
    crop = image.crop((int(width * .02), int(height * .03), int(width * .98), int(height * .56)))
    crop.thumbnail((2048, 2048))
    return crop


def _comfort_room_ids(root):
    uses = _read(root / "room_use_resolution.json", {})
    return {str(row.get("room_id")) for row in uses.get("records", []) if isinstance(row, dict)
            and row.get("space_scope") in {"comfort_hvac", "comfort_hvac_with_process_exception"}}


def _geometrically_shared_edge_indices(trace, other_traces, max_wall_mm=400.0, angle_tolerance_deg=5.0,
                                       overlap_fraction=0.5):
    """Find traced edges matching another room's parallel wall across a wall thickness."""
    from shapely.geometry import LineString

    points = trace.get("points_image_px", [])
    try:
        mm_per_px = float(trace.get("calibration", {}).get("mm_per_px"))
    except (TypeError, ValueError):
        return []
    if mm_per_px <= 0 or not math.isfinite(mm_per_px):
        return []
    max_distance_px = max_wall_mm / mm_per_px
    matches = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        line = LineString([start, end])
        if line.length <= 0:
            continue
        dx, dy = end[0] - start[0], end[1] - start[1]
        angle = math.atan2(dy, dx)
        ux, uy = dx / line.length, dy / line.length
        for other in other_traces:
            if other.get("room_id") == trace.get("room_id") or other.get("page") != trace.get("page"):
                continue
            for a, b in zip(other.get("points_image_px", []), other.get("points_image_px", [])[1:]):
                candidate = LineString([a, b])
                if candidate.length <= 0 or line.distance(candidate) * mm_per_px > max_wall_mm:
                    continue
                other_angle = math.atan2(b[1] - a[1], b[0] - a[0])
                delta = abs((math.degrees(angle - other_angle) + 90.0) % 180.0 - 90.0)
                if delta > angle_tolerance_deg:
                    continue
                projections = [((point[0] - start[0]) * ux + (point[1] - start[1]) * uy) for point in (a, b)]
                overlap = max(0.0, min(line.length, max(projections)) - max(0.0, min(projections)))
                if overlap / line.length >= overlap_fraction:
                    matches.append(index)
                    break
            if index in matches:
                break
    return matches


def _trace_wall_runs(trace):
    points = trace.get("points_image_px", [])
    mm_per_px = float(trace.get("calibration", {}).get("mm_per_px") or 0)
    runs = room_outline.wall_runs(points, mm_per_px)
    edge_lengths = {index: math.dist(start, end) * mm_per_px / 1000.0
                    for index, (start, end) in enumerate(zip(points, points[1:]))}
    return [{**row, "edge_lengths_m": {str(index): edge_lengths[index] for index in row["edge_indices"]}}
            for row in runs]


def _page_perimeter_data(traces, buffer_mm=300.0):
    """Union buffered room parts and describe the tenancy's outside perimeter."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    valid=[]
    for trace in traces:
        points=trace.get("points_image_px")
        try: mm_per_px=float(trace.get("calibration",{}).get("mm_per_px"))
        except (TypeError,ValueError): continue
        if mm_per_px<=0 or not math.isfinite(mm_per_px) or not isinstance(points,list) or len(points)<4:
            continue
        polygon=Polygon(points)
        if polygon.is_valid and polygon.area>0:
            valid.append((trace,polygon,mm_per_px))
    if not valid:
        return {"mm_per_px":None,"runs":[],"run_segments":{},"trace_edges":{},"error":"No valid calibrated room outlines."}
    scale=valid[0][2]
    if any(abs(value[2]-scale)/scale>.005 for value in valid):
        return {"mm_per_px":scale,"runs":[],"run_segments":{},"trace_edges":{},"error":"Room parts on this page have conflicting calibration scales."}
    gap_buffer=buffer_mm/scale
    # Morphological closing joins room-part gaps up to one wall thickness while
    # shrinking the outside edge back to the traced tenancy footprint.
    raw_union=unary_union([polygon for _trace,polygon,_scale in valid])
    union=raw_union.buffer(gap_buffer, join_style="mitre").buffer(-gap_buffer, join_style="mitre")
    if union.is_empty:
        union=raw_union
    polygons=list(union.geoms) if hasattr(union,"geoms") else [union]
    runs=[]; run_segments={}; offset=0
    for polygon in polygons:
        if not hasattr(polygon,"exterior") or polygon.is_empty: continue
        points=[[float(x),float(y)] for x,y in polygon.exterior.coords]
        local=room_outline.wall_runs(points,scale)
        for row in local:
            edge_ids=[offset+index for index in row["edge_indices"]]
            global_index=len(runs)
            row={**row,"run_index":global_index,"edge_indices":edge_ids,
                 "start_px":list(row["start_px"]),"end_px":list(row["end_px"])}
            runs.append(row)
        for index,(start,end) in enumerate(zip(points,points[1:])):
            run=next((row["run_index"] for row in local if index in row["edge_indices"]),None)
            if run is not None: run_segments.setdefault(len(runs)-len(local)+run,[]).append((start,end))
        offset+=len(points)-1
    trace_edges={}
    tolerance_px=400.0/scale
    for trace,_polygon,_scale in valid:
        mappings=[]
        points=trace["points_image_px"]
        for edge_index,(start,end) in enumerate(zip(points,points[1:])):
            edge=LineString([start,end]); edge_len=edge.length
            best=None
            if edge_len>0:
                ex,ey=(end[0]-start[0])/edge_len,(end[1]-start[1])/edge_len
                for run_index,segments in run_segments.items():
                    overlap_total=0.0; nearest=float("inf"); max_angle=0.0
                    for a,b in segments:
                        segment=LineString([a,b])
                        seg_len=segment.length
                        if seg_len<=0: continue
                        sx,sy=(b[0]-a[0])/seg_len,(b[1]-a[1])/seg_len
                        angle=math.degrees(math.acos(min(1.0,abs(ex*sx+ey*sy))))
                        if angle>5.0: continue
                        nearest=min(nearest,edge.distance(segment))
                        projections=[(point[0]-start[0])*ex+(point[1]-start[1])*ey for point in (a,b)]
                        overlap_total+=max(0.0,min(edge_len,max(projections))-max(0.0,min(projections)))
                    fraction=min(1.0,overlap_total/edge_len)
                    if nearest<=tolerance_px and fraction>=.5:
                        score=(fraction,-nearest)
                        if best is None or score>best[0]: best=(score,run_index)
            mappings.append({"edge_index":edge_index,"perimeter_run_index":best[1] if best else None,
                             "length_m":edge_len*scale/1000.0})
        trace_edges[trace["trace_id"]]=mappings
    return {"mm_per_px":scale,"runs":runs,"run_segments":run_segments,"trace_edges":trace_edges,"error":""}


def _short_perimeter_run_inheritance(runs, run_segments):
    """Map sub-metre perimeter jogs to the nearest asked wall run."""
    from shapely.geometry import LineString
    asked=[row for row in runs if float(row.get("span_m") or 0)>=1.0]
    inheritance={}
    for row in runs:
        if row in asked:
            continue
        segments=run_segments.get(row["run_index"],[])
        geometry=LineString(segments[0]) if len(segments)==1 else __import__("shapely.geometry",fromlist=["MultiLineString"]).MultiLineString(segments)
        candidates=[]
        for candidate in asked:
            other_segments=run_segments.get(candidate["run_index"],[])
            other=LineString(other_segments[0]) if len(other_segments)==1 else __import__("shapely.geometry",fromlist=["MultiLineString"]).MultiLineString(other_segments)
            candidates.append((geometry.distance(other),candidate["run_index"]))
        if candidates:
            inheritance[str(row["run_index"])]=min(candidates)[1]
    return inheritance


def _run_neighbour(run, candidates, edge_count):
    def distance(candidate):
        return min(min(abs(first - second), edge_count - abs(first - second))
                   for first in run["edge_indices"] for second in candidate["edge_indices"])
    return min(candidates, key=lambda candidate: (distance(candidate), candidate["run_index"])) if candidates else None


def _elevation_pages(ai_input):
    """Return actual elevation/storefront sheets, preferring titled shopfront elevations."""
    pages=(ai_input or {}).get("drawing_set",{}).get("pages",[])
    matches=[]
    for row in pages:
        if not isinstance(row,dict) or type(row.get("page")) is not int:
            continue
        title=str(row.get("title") or "")
        if not re.search(r"\b(?:elevation|shopfront|storefront)\b",title,re.I):
            continue
        preferred=bool(re.search(r"\b(?:shopfront|storefront)\b",title,re.I) and re.search(r"\belevation\b",title,re.I))
        matches.append((0 if preferred else 1,row["page"],row))
    return [row for _priority,_page,row in sorted(matches,key=lambda item:(item[0],item[1]))]


def _storefront_elevation_pages(ai_input):
    """Keep only elevation sheets that can provide shopfront evidence."""
    return [row for row in _elevation_pages(ai_input)
            if re.search(r"\b(?:shopfront|storefront)\b|\bexternal\s+elevation\b",
                         str(row.get("title") or ""), re.I)]


def _match_storefront_run(runs, elevation_pages, spatial):
    """Pre-mark a storefront only when all dimension evidence identifies one run."""
    page_rows={row.get("page"):row for row in spatial.get("pages",[]) if isinstance(row,dict)}
    dimensions=[]
    for page in elevation_pages:
        ocr=page_rows.get(page.get("page"),{})
        for item in ocr.get("dimension_candidates",[]) or []:
            value=item.get("value_mm") if isinstance(item,dict) else None
            # Only numbers the text layer reads as dimensions, as the dimension-to-wall matcher does: a level marker
            # or a title-block postcode is no width (Casa Nova, 2026-10-28: a sign's "2940" level put the storefront
            # on one 2.5 m wall of a ~30 m glazed front).
            if (item.get("annotation_kind","written_dimension") in {"unknown_numeric","detail_or_sheet_reference"}
                    or item.get("dimension_eligibility","eligible") in {"vision_review","ineligible"}):
                continue
            if isinstance(value,(int,float)) and value>0:
                dimensions.append({"page":page["page"],"value_mm":float(value)})
    if not dimensions:
        return {"run_index":None,"total_mm":None,"pages":[],
                "reason":"No elevation page has dimension text; no wall run was pre-marked as storefront."}
    matches=[]
    for dimension in dimensions:
        for run in runs:
            span=float(run.get("span_m") or 0)*1000.0
            if autonomous_tasks.storefront_width_matches(dimension["value_mm"],span):
                matches.append({**dimension,"run_index":run["run_index"]})
    run_ids={row["run_index"] for row in matches}
    if len(run_ids)!=1:
        reason=("No wall run matches the elevation dimensions (2% or up to 600 mm wider); no run was pre-marked as storefront."
                if not run_ids else
                "Elevation dimension evidence matches multiple wall runs across the selected pages; no run was pre-marked as storefront.")
        return {"run_index":None,"total_mm":None,"pages":[],"reason":reason}
    values=[row["value_mm"] for row in matches]
    mean_value=sum(values)/len(values)
    if max(values)-min(values)>mean_value*.02:
        return {"run_index":None,"total_mm":None,"pages":[],
                "reason":"Elevation pages disagree on the storefront width by more than 2%; no run was pre-marked."}
    run_index=next(iter(run_ids))
    return {"run_index":run_index,"total_mm":round(mean_value,1),
            "pages":list(dict.fromkeys(row["page"] for row in matches)),
            "reason":f"Unique run {run_index+1} matches elevation dimensions (2% or up to 600 mm wider)."}


# A side with less than this share drawn as wall (a counter edge, a floor-finish change) that faces more of the
# building is open to the rest of the tenancy: when the AI can't say what it faces, it is inside the tenancy, not a
# neighbour. A side facing out of the plan (a glazed shopfront or outer wall not drawn in a wall style) keeps the
# usual fallback.
OPEN_SIDE_WALL_SHARE = 0.5


def _edge_wall_coverage(root, page, traces):
    """{trace_id: {edge_index: {"wall_coverage": share of the side with a drawn wall along it, "faces_wall": whether
    it looks onto more drawn building}}} from the page's chosen wall styles; empty when the wall styles or page
    geometry aren't available (raster pages, no wall-style answer)."""
    try:
        records = _p0_wall_style_records(root, page)
        style_ids = sorted({style_id for row in records for style_id in (row.get("applied_value") or {}).get("wall_style_ids", [])})
        if not style_ids:
            return {}
        segments = room_outline.drawn_segments(_p0_context(root, page)["objects"], style_ids)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return {}
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    tree = STRtree(segments) if segments else None
    nearby = lambda geometry: [segments[index] for index in tree.query(geometry)] if tree is not None else []
    result = {}
    for trace in traces:
        points = trace.get("points_image_px") or []
        mm_per_px = float((trace.get("calibration") or {}).get("mm_per_px") or 0)
        if mm_per_px <= 0 or len(points) < 4:
            continue
        sides = {}
        for index, (start, end) in enumerate(zip(points, points[1:])):
            share = room_outline.wall_coverage((start, end), nearby(LineString([start, end]).buffer(400 / mm_per_px)), mm_per_px)
            reach = LineString([start, end]).buffer(30_000 / mm_per_px)
            sides[index] = {"wall_coverage": share,
                            "faces_wall": share < OPEN_SIDE_WALL_SHARE
                            and room_outline.faces_drawn_wall((start, end), points, nearby(reach), mm_per_px)}
        result[trace["trace_id"]] = sides
    return result


_SHARED_WALL = re.compile(r"\b(?:party|common|shared)\s+walls?\b|\bdual\s+occupancy\b|\bsemi[- ]?detached\b|\bduplex\b|"
                          r"\battached\s+(?:dwelling|house)\b|\bterraced?\s+house\b|\bdwelling\s+[ab]\b", re.I)


def _house_without_shared_walls(root):
    """A house whose drawings never mention a wall shared with another dwelling: its outline faces outside, so the
    outside-walls question isn't asked (user decision 2026-10-30: AI kept to a minimum; ai/house_envelope.py)."""
    from backend import house_windows_service
    if not house_windows_service.is_dwelling(root):
        return False
    pages = list(_read(Path(root) / "page_inventory.json", {}).get("pages", {}).values())
    pages += [row for row in _read(Path(root) / "ai_input.json", {}).get("drawing_set", {}).get("pages", []) if isinstance(row, dict)]
    text = json.dumps([{key: page.get(key) for key in ("title", "information", "structured_content")} for page in pages], ensure_ascii=False)
    return not _SHARED_WALL.search(text)


def _p3_packets(root, room_id=None, page_number=None, force=False):
    result=[]
    if _house_without_shared_walls(root):
        return result
    try: traces=reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir":str(root)}))
    except (OSError,ValueError,TypeError,KeyError): traces=[]
    comfort=_comfort_room_ids(root)
    selected=[row for row in traces if autonomous_tasks.usable_calibration(row.get("calibration",{}).get("status"))]
    affected_pages = ({page_number} if page_number is not None else
                      {row.get("page") for row in selected if row.get("room_id") == room_id}
                      if room_id is not None else None)
    grouped={}
    for trace in selected: grouped.setdefault(trace.get("page"),[]).append(trace)
    ai_input,spatial,_building=_load_inputs(root)
    dwelling = __import__("backend.house_windows_service", fromlist=["is_dwelling"]).is_dwelling(root)
    elevation_rows=[] if dwelling else _storefront_elevation_pages(ai_input)
    for page,traces_on_page in sorted(grouped.items()):
        if affected_pages is not None and page not in affected_pages: continue
        comfort_traces=[row for row in traces_on_page if row.get("room_id") in comfort]
        if not comfort_traces: continue
        target=f"page-{page}"
        current=_current_task(root,"P3_boundaries",target)
        if not force and room_id is None and current and current.get("status") in {"applied","below_accuracy_bar"}: continue
        perimeter=_page_perimeter_data(traces_on_page)
        if perimeter["error"]:
            result.append(("P3_boundaries",target,{"task":"P3_boundaries","page":page},"",[],perimeter["error"]))
            continue
        runs=perimeter["runs"]
        room_edges=[]
        coverage=_edge_wall_coverage(root,page,comfort_traces)
        for trace in comfort_traces:
            for mapping in perimeter["trace_edges"].get(trace["trace_id"],[]):
                edge={"trace_id":trace["trace_id"],"room_id":trace["room_id"],
                    "room_label":trace.get("room_label"),"edge_index":mapping["edge_index"],
                    "length_m":round(mapping["length_m"],4),"perimeter_run_index":mapping["perimeter_run_index"]}
                side=coverage.get(trace["trace_id"],{}).get(mapping["edge_index"])
                if side: edge.update({"wall_coverage":round(side["wall_coverage"],2),"faces_wall":side["faces_wall"]})
                room_edges.append(edge)
        storefront_match=_match_storefront_run(runs,elevation_rows,spatial) if not dwelling else {"run_index":None,"total_mm":None,"pages":[],"reason":"House boundary task; shopfront matching disabled."}
        placed=_current_task(root,"P4_storefront_wall",f"page-{page}")
        placed_runs=sorted({index for row in ((placed or {}).get("applied_value") or {}).get("elevations",{}).values()
                            for index in row.get("run_indices",[])}) if placed and placed.get("status") in {"applied","below_accuracy_bar"} else []
        if placed_runs and storefront_match["run_index"] is None:
            storefront_match={"run_index":placed_runs[0],"total_mm":None,"pages":[],
                              "reason":f"The storefront step placed the storefront on wall(s) {[index+1 for index in placed_runs]}."}
        cues=[]
        page_row=next((row for row in spatial.get("pages",[]) if row.get("page")==page),{})
        cue_pattern=re.compile(r"\b(?:shopfront|storefront|street frontage|external wall|party wall|boundary line|adjacent tenancy|enclosed mall walkway)\b",re.I)
        raw_text=page_row.get("text",page_row.get("plain_text",""))
        if isinstance(raw_text,str): cues.extend(line.strip() for line in raw_text.splitlines() if cue_pattern.search(line))
        cue_text="\n".join(dict.fromkeys(cues))
        comfort_runs={row["perimeter_run_index"] for row in room_edges}
        relevant_runs=[row for row in runs if row["run_index"] in comfort_runs]
        short_inheritance=_short_perimeter_run_inheritance(relevant_runs,perimeter["run_segments"])
        asked=[{"index":row["run_index"],"length_m":row["span_m"],"edge_indices":row["edge_indices"]}
               for row in relevant_runs if row["span_m"]>=1.0]
        if runs and not asked:
            result.append(("P3_boundaries",target,{"task":"P3_boundaries","page":page},"",[],
                "No perimeter wall run is at least 1 m; short runs cannot inherit a boundary answer."))
            continue
        packet={"task":"P3_boundaries","page":page,**({"dwelling":True} if dwelling else {}),"edges":asked}
        prompt_packet={"page":page,"room_parts":[row.get("room_label") for row in traces_on_page],
            "perimeter_runs":[[row["index"]+1,round(row["length_m"],2)] for row in asked],
            "short_run_inheritance":{str(int(key)+1):value+1 for key,value in short_inheritance.items()},
            "storefront_run_number":None,"storefront_elevation_total_width_mm":None,
            "storefront_match_reason":"House boundary classification." if dwelling else storefront_match["reason"],
            "nearby_text":cue_text}
        shop_prompt_prefix = "Classify each numbered run on the outside perimeter of the union of the traced tenancy rooms: external, mall, adjacent_tenancy, internal or unknown. "

        def boundary_prompt(room_parts, nearby, digits):
            evidence = {**prompt_packet, "room_parts": room_parts, "nearby_text": nearby,
                        "perimeter_runs": [[row["index"]+1, round(row["length_m"], digits)] for row in asked]}
            if dwelling:
                return autonomous_tasks.house_boundaries_prompt(evidence)
            return (shop_prompt_prefix +
                "Do not classify interior partition edges; unmapped room edges become internal automatically. Quote drawing evidence for each non-unknown answer. "
                "Runs under 1 m inherit the nearest numbered run's classification. "
                "Storefront totals may exceed inside-face spans by 0–600 mm or differ by <=2%; never classify a matched run internal/adjacent_tenancy. "
                "JSON only: {\"runs\":[{\"run_number\":int,\"boundary\":\"external|mall|adjacent_tenancy|internal|unknown\",\"evidence\":string}]}\n\n"
                +json.dumps({"page":page,"room_parts":room_parts,"perimeter_runs":evidence["perimeter_runs"],
                    "short_run_inheritance":{str(int(key)+1):value+1 for key,value in short_inheritance.items()},
                    "storefront_run_number":storefront_match["run_index"]+1 if storefront_match["run_index"] is not None else None,
                    "storefront_elevation_total_width_mm":storefront_match["total_mm"],
                    "storefront_match_reason":storefront_match["reason"],"nearby_text":nearby},
                    ensure_ascii=False,separators=(",",":")))

        # A large plan (house set 15: 26 room parts, 63 runs) is shortened, never blocked: room names once each, then
        # less of the nearby text, then lengths to 0.1 m. Still over the limit, it is asked anyway (recorded).
        all_parts = [row.get("room_label") for row in traces_on_page]
        unique_parts = list(dict.fromkeys(all_parts))
        for room_parts, nearby, digits in ((all_parts, cue_text, 2), (unique_parts, cue_text, 2),
                                           (unique_parts, cue_text[:300], 2), (unique_parts, "", 2), (unique_parts, "", 1)):
            prompt = boundary_prompt(room_parts, nearby, digits)
            if len(prompt) <= 1500:
                break
        packet={"task":"P3_boundaries","page":page,**({"dwelling":True} if dwelling else {}),"trace_ids":[row["trace_id"] for row in comfort_traces],
            "trace_id":comfort_traces[0]["trace_id"] if len(comfort_traces)==1 else None,
            "room_id":comfort_traces[0].get("room_id"),"room_label":comfort_traces[0].get("room_label"),
            "mm_per_px":perimeter["mm_per_px"],"edges":asked,"runs":runs,"room_edges":room_edges,
            "short_run_inheritance":short_inheritance,
            "storefront_run_index":storefront_match["run_index"],"storefront_run_indices":placed_runs,
            "storefront_total_width_mm":storefront_match["total_mm"],
            "storefront_match_reason":storefront_match["reason"],"nearby_text":cue_text}
        try:
            image,packet["image_transform"]=_perimeter_task_image(root,traces_on_page,perimeter,asked)
        except (OSError,ValueError) as error:
            result.append(("P3_boundaries",target,packet,"",[],str(error))); continue
        result.append(("P3_boundaries",target,packet,prompt,[_png_bytes(image)],""))
    return result


CEILING_KIND_WORDS = {"ceiling_plan": "ceiling plan", "elevation": "elevation or section"}


def _ceiling_pages(ai_input, spatial):
    """(ceiling-plan pages, elevation/section pages), each [(page row, printed height labels)], labelled pages only."""
    by_page = {row.get("page"): row for row in spatial.get("pages", []) if isinstance(row, dict)}
    pages = (ai_input.get("drawing_set") or {}).get("pages", [])
    def labelled(rows, kind):
        found = []
        for row in rows:
            labels = autonomous_tasks.height_labels(by_page.get(row.get("page"), {}).get("standalone_text_items", []), kind)
            if labels:
                found.append((row, labels))
        return found
    ceiling = [row for row in pages if str(row.get("type") or row.get("detected_type") or "") == "reflected_ceiling_plan"
               or re.search(r"\bceiling\s+plan\b|\brcp\b", str(row.get("title") or ""), re.I)]
    views = [row for row in pages if row not in ceiling and re.search(r"\b(?:elevation|section)s?\b", str(row.get("title") or ""), re.I)]
    return labelled(ceiling, "ceiling_plan"), labelled(views, "elevation")


def _height_task_image(root, page, labels, page_size):
    """The page with each printed height label boxed and numbered (H1, H2...) where it is printed."""
    from PIL import Image, ImageDraw
    source = autonomous_tasks._page_screenshot(root, page)
    if not source:
        raise ValueError(f"No rendered image is available for page {page}.")
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    scale = image.width / float(page_size[0] or image.width)
    draw = ImageDraw.Draw(image)
    for label in labels:
        if not label.get("center"):
            continue
        x0, y0, x1, y1 = [value * scale for value in label.get("box") or [*label["center"], *label["center"]]]
        draw.rectangle([x0 - 12, y0 - 12, x1 + 12, y1 + 12], outline=(220, 35, 50), width=5)
        draw.text((x1 + 16, (y0 + y1) / 2 - 22), label["id"], fill=(220, 35, 50), font_size=44)
    image.thumbnail((2048, 2048))
    return image


def _p7_packets(root, requested_rooms=None):
    """Ceiling heights from the printed labels (P7): the ceiling plan first; elevations and sections only for the rooms
    its labels don't cover (and once the ceiling-plan answers are in). The height is read from the label by code; the
    AI says which label is which room's ceiling. A room keeps the labelled preliminary height only when no sheet
    prints one."""
    try:
        traces = reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    except (OSError, ValueError, TypeError, KeyError):
        return []
    comfort = _comfort_room_ids(root)
    rooms = sorted({str(t.get("room_label")) for t in traces if t.get("room_id") in comfort and t.get("room_label")})
    rooms = list(requested_rooms) if requested_rooms else rooms
    if not rooms:
        return []
    ai_input, spatial, _building = _load_inputs(root)
    sizes = {row.get("page"): (row.get("width"), row.get("height")) for row in spatial.get("pages", []) if isinstance(row, dict)}
    result, resolved = [], set()
    for kind, rows in zip(("ceiling_plan", "elevation"), _ceiling_pages(ai_input, spatial)):
        waiting = False
        for row, labels in rows:
            page = int(row["page"])
            record = _current_task(root, "P7_ceiling_heights", f"page-{page}")
            if not requested_rooms and record and record.get("status") in {"applied", "below_accuracy_bar"}:
                resolved |= {name.casefold() for name in (record.get("applied_value") or {}).get("heights", {})}
                continue
            missing = [name for name in rooms if name.casefold() not in resolved]
            if not missing:
                continue
            waiting = True
            packet = {"task": "P7_ceiling_heights", "page": page, "kind": kind, "rooms": missing,
                      "labels": labels, "evidence": {"page": page}}
            words = CEILING_KIND_WORDS[kind]
            prompt = (f"The image is the {words} on page {page} ({row.get('title')}). The printed "
                      + ("ceiling heights" if kind == "ceiling_plan" else "heights above floor level")
                      + " are boxed in red and numbered H1, H2... For each room below, give the label(s) of "
                      + ("its ceiling" if kind == "ceiling_plan" else
                         "the underside of its ceiling (not a bulkhead, shelf, sign, joinery or door head)")
                      + ". Use the room names and walls on the sheet. If a room has two ceiling heights, list both with "
                        "the share of its floor under each. If this sheet doesn't show a room's ceiling, say why in "
                        "not_shown. JSON only: {\"rooms\":[{\"room\":string,\"label_ids\":[string],\"shares\":[number]|null,"
                        "\"not_shown\":string|null,\"evidence\":string}]}\n\n"
                      + json.dumps({"rooms": missing, "labels": [[label["id"], label["text"]] for label in labels]},
                                   ensure_ascii=False, separators=(",", ":")))
            try:
                image = _height_task_image(root, page, labels, sizes.get(page) or (0, 0))
            except (OSError, ValueError) as error:
                result.append(("P7_ceiling_heights", f"page-{page}", packet, "", [], str(error)))
                continue
            result.append(("P7_ceiling_heights", f"page-{page}", packet, prompt, [_png_bytes(image)], ""))
        if waiting:
            break
    return result


def drawing_ceiling_heights(root):
    """{room label (casefold): {"height_mm", "labels", "page", "kind", "evidence"}} from the answered P7 tasks; a
    ceiling plan wins over an elevation or section for the same room."""
    rows = [record for record in _all_current(Path(root)) if record.get("task") == "P7_ceiling_heights"
            and record.get("status") in {"applied", "below_accuracy_bar"}]
    rows.sort(key=lambda record: (record.get("applied_value", {}).get("kind") != "ceiling_plan", record.get("applied_value", {}).get("page", 0)))
    heights = {}
    for record in rows:
        value = record.get("applied_value") or {}
        for name, row in value.get("heights", {}).items():
            heights.setdefault(name.casefold(), {**row, "page": value.get("page"), "kind": value.get("kind")})
    return heights


def room_ceiling_height(root, room_id, label, level):
    """A room's ceiling height in mm, the one rule every step uses: a height typed in the workspace, else the height
    printed on the drawings (P7), else the ceiling resolver's saved value (which may be the labelled preliminary
    fallback), else None. The saved value is only rebuilt with the model, so on a new job it still held the 2,700 mm
    fallback while the drawings said 3,622 mm, and the Shop's storefront glass was refused (Butcher Buffet, 2026-10-22).
    Before the resolver has run there is no saved value: the same labelled fallback, so glass is never refused for want
    of a height (2026-11-06)."""
    from ai import ceiling_volume_resolution
    from backend import job_service
    root = Path(root)
    identity = ceiling_volume_resolution.room_identity(label or "", level or "")
    typed = next((row for row in job_service.height_overrides(root) if row.get("room_id") == identity), None)
    if typed and isinstance(typed.get("ceiling_height_mm"), (int, float)):
        return float(typed["ceiling_height_mm"])
    printed = drawing_ceiling_heights(root).get(str(label or "").strip().casefold())
    if printed and isinstance(printed.get("height_mm"), (int, float)):
        return float(printed["height_mm"])
    saved = ceiling_volume_resolution.values_by_room(_read(root / "ceiling_volume_resolution.json", {}))
    value = (saved.get(room_id) or saved.get(identity) or {}).get("ceiling_height_mm")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    from ai import ai_preliminary
    return float(ai_preliminary.load_pack()["preliminary_defaults"]["ceiling_height_mm"])


def _apply_p7(record, validated):
    packet = record["packet"]
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
        "applied_value": {"page": packet["page"], "kind": packet["kind"], "heights": validated["heights"],
                          "not_shown": validated["not_shown"], "label": record["quality_label"]},
        "validation": validated, "block_reason": ""})
    return record


def _storefront_runs(root, plan_page, eligible, run_geometry, elevation_pages, spatial, traces, perimeter, result,
                     candidates=None):
    """{elevation page: ([perimeter run indices], reason)} for the storefront on this plan page, or None after asking.

    The printed elevation width settles it when it matches exactly one outside wall; one outside wall settles it
    alone. Otherwise the AI is asked (P4_storefront_wall) which numbered wall each elevation shows, from the plan with
    the walls numbered and the elevation itself. A storefront is never left out because the width text isn't in the
    PDF's text layer (Butcher Buffet, 2026-10-20: three outside walls, the width drawn as outlines)."""
    record=_current_task(root,"P4_storefront_wall",f"page-{plan_page}")
    if record and record.get("status") in {"applied","below_accuracy_bar"}:
        chosen={int(page):(row["run_indices"],f"The AI placed it on wall(s) {[index+1 for index in row['run_indices']]}: {row['evidence']}")
                for page,row in (record.get("applied_value") or {}).get("elevations",{}).items()
                if row.get("run_indices") and int(page) in {item["page"] for item in elevation_pages}}
        offered=set(candidates if candidates is not None else eligible)
        if chosen and all(index in offered for indices,_ in chosen.values() for index in indices):
            return chosen
        answered=(record.get("applied_value") or {}).get("elevations",{})
        if answered and not chosen and not eligible:
            return {}                    # the AI said the storefront isn't on this plan: nothing to place here
    if eligible:
        runs=[{**run_geometry[index],"run_index":index} for index in eligible]
        match=_match_storefront_run(runs,elevation_pages,spatial)
        if match["run_index"] is not None:
            return {match["pages"][0]:([match["run_index"]],match["reason"])}
        if len(eligible)==1:
            return {elevation_pages[0]["page"]:([eligible[0]],"Only one outside or mall wall remains for these rooms after P3.")}
    asked=[{"index":index,"length_m":float(run_geometry[index]["span_m"])} for index in (candidates if candidates is not None else eligible)]
    pages=elevation_pages[:2]
    # With outside or mall walls on this plan the storefront must be placed; with none (another level, or every wall
    # classed internal) the AI may say it isn't on this plan, with its evidence.
    allow_none=not eligible
    packet={"task":"P4_storefront_wall","page":plan_page,"elevation_pages":[row["page"] for row in pages],"allow_none":allow_none,
            "elevation_titles":{str(row["page"]):str(row.get("title") or "") for row in pages},
            "run_spans_m":{str(row["index"]+1):round(row["length_m"],3) for row in asked},"evidence":{"page":plan_page}}
    prompt=("Image 1 is the tenancy plan: its perimeter walls are numbered in red. The other images are elevations "
            f"({', '.join(f'page {row['page']}: {row.get('title')}' for row in pages)}). For each elevation, which numbered "
            "wall(s) does it show, and does that front face a street (outside) or the inside of a shopping centre (mall)? "
            "Use the elevation tags and entry doors drawn on the plan, the street or mall names, gridlines and the "
            "elevation's overall width. "
            + ("If the storefront isn't on this plan (e.g. it is another level), give no run numbers and say why. " if allow_none
               else "A storefront elevation always shows at least one of these walls. ")
            + "Read the overall "
            "width from the elevation if printed, else null. JSON only: {\"elevations\":[{\"page\":int,"
            "\"run_numbers\":[int],\"faces\":\"street|mall\",\"overall_width_mm\":number|null,\"evidence\":string}]}\n\n"
            +json.dumps({"walls_m":{row["index"]+1:round(row["length_m"],2) for row in asked}},separators=(",",":")))
    try:
        plan_image,packet["image_transform"]=_perimeter_task_image(root,traces,perimeter,[{"index":row["index"]} for row in asked])
        images=[_png_bytes(plan_image),*[_png_bytes(_p4_elevation_image(root,int(row["page"]))) for row in pages]]
    except (OSError,ValueError,IndexError) as error:
        result.append(("P4_storefront_wall",f"page-{plan_page}",packet,"",[],str(error)))
        return None
    result.append(("P4_storefront_wall",f"page-{plan_page}",packet,prompt,images,""))
    return None


def _p4_packets(root, only_room_id=None):
    result=[]
    from backend import house_windows_service
    if house_windows_service.is_dwelling(root):
        return result
    try: traces=reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir":str(root)}))
    except (OSError,ValueError,TypeError,KeyError): traces=[]
    ai_input,spatial,_building=_load_inputs(root)
    elevation_pages=_storefront_elevation_pages(ai_input)
    if not elevation_pages: return result
    comfort=_comfort_room_ids(root)
    assumption=__import__("ai.ai_preliminary",fromlist=["load_pack"]).load_pack()
    glazing="retail"; shading="unshaded"
    selected=[trace for trace in traces if autonomous_tasks.usable_calibration(trace.get("calibration",{}).get("status"))]
    by_page={}
    for trace in selected: by_page.setdefault(trace.get("page"),[]).append(trace)
    for plan_page,page_traces in sorted(by_page.items()):
        p3_record=_current_task(root,"P3_boundaries",f"page-{plan_page}")
        if not p3_record or p3_record.get("status") not in {"applied","below_accuracy_bar"}: continue
        page_perimeter=_page_perimeter_data(page_traces)
        if page_perimeter["error"]: continue
        run_geometry={row["run_index"]:row for row in page_perimeter["runs"]}
        p3_runs={row.get("index"):row.get("boundary") for row in p3_record.get("applied_value",{}).get("runs",[])}
        page_by_room={}
        for trace in page_traces:
            if trace.get("room_id") in comfort:
                page_by_room.setdefault(trace.get("room_id"),[]).append(trace)
        mappings_by_room={room_id:[{"trace_id":trace["trace_id"],**edge}
                                   for trace in room_traces
                                   for edge in page_perimeter["trace_edges"].get(trace["trace_id"],[])
                                   if edge.get("perimeter_run_index") is not None]
                          for room_id,room_traces in page_by_room.items()}
        eligible=sorted({row["perimeter_run_index"] for rows in mappings_by_room.values() for row in rows
                         if p3_runs.get(row["perimeter_run_index"]) in {"external","mall"}})
        # Every perimeter wall of the cooled rooms can be the storefront: the wall-facing step can be wrong (Butcher
        # Buffet, 2026-10-20: the 11.9 m storefront wall was classed "adjacent tenancy" and wasn't offered).
        candidates=sorted({row["perimeter_run_index"] for rows in mappings_by_room.values() for row in rows
                           if float(run_geometry[row["perimeter_run_index"]]["span_m"])>=1.0})
        if not candidates:
            continue
        choice=_storefront_runs(root,plan_page,eligible,run_geometry,elevation_pages,spatial,page_traces,page_perimeter,result,
                                candidates=candidates)
        if choice is None:
            continue                     # the AI is asked which wall it is; the rooms follow once it answers
        for elevation_page,(run_indices,storefront_reason) in choice.items():
          elevation=next(page for page in elevation_pages if page["page"]==elevation_page)
          run_span=sum(float(run_geometry[index]["span_m"]) for index in run_indices)
          # Rooms sharing the storefront wall each take their share of its glass, in proportion to their length of it.
          run_total=sum(float(row["length_m"]) for rows in mappings_by_room.values() for row in rows
                        if row["perimeter_run_index"] in run_indices)
          run_label="+".join(str(index) for index in run_indices)
          for room_id,room_traces in page_by_room.items():
            if only_room_id is not None and str(room_id) != str(only_room_id): continue
            part_edges=[{key:row[key] for key in ("trace_id","edge_index","length_m")}
                        for row in mappings_by_room[room_id] if row["perimeter_run_index"] in run_indices]
            if not part_edges: continue
            run_index=run_indices[0]
            trace=room_traces[0]
            target=f"{room_id}-run-{run_label}-page-{elevation['page']}-trace-{trace['trace_id']}"
            old=_current_task(root,"P4_openings",target)
            if only_room_id is None and old and old.get("status") in {"applied","below_accuracy_bar"}: continue
            ceiling=room_ceiling_height(root,room_id,trace.get("room_label",""),trace.get("level_name",""))
            if not isinstance(ceiling,(int,float)) or ceiling<=0:
                result.append(("P4_openings",target,{"task":"P4_openings","room":trace.get("room_label"),"page":elevation["page"]},"",[],
                    "Resolve ceiling height before applying elevation glazing; no head-height fallback is available.")); continue
            page=int(elevation["page"])
            try: image=_p4_elevation_image(root,page)
            except (OSError,ValueError,IndexError) as error:
                result.append(("P4_openings",target,{"task":"P4_openings","room":trace.get("room_label"),"page":page},"",[],str(error))); continue
            page_ocr=next((row for row in spatial.get("pages",[]) if row.get("page")==page),{})
            text_layer=[]
            for field in ("text","plain_text","word_samples","dimension_candidates","standalone_text_items","title_blocks"):
                raw=page_ocr.get(field,[])
                if isinstance(raw,str): text_layer.extend(raw.splitlines())
                elif isinstance(raw,list): text_layer.extend(str(item.get("text",item)) if isinstance(item,dict) else str(item) for item in raw)
            # The question must fit its 2,000-character budget: the room's wall pieces go as one length (the code keeps the
            # list), and the elevation's text goes dimension-like first, trimmed until it fits. A room spanning several
            # shopfront stretches was refused outright (Casa Nova, 2026-10-24).
            text_layer=sorted(dict.fromkeys(item.strip() for item in text_layer if item and item.strip()),
                              key=lambda item:(not re.search(r"\d{3,}",item), len(item)))[:60]
            def opening_prompt(texts):
              return ("The image is a shopfront/external elevation for the selected outside wall run. List each glazed panel, excluding signage, solid panels and open doorways. "
                "Use printed dimensions only; quote exact text. Return null for unprinted sill/head/width. Missing sill is assumed 0 (glass to floor); missing head is assumed ceiling height. "
                "For vector-outline text, list each glazed panel and each excluded solid part (roller shutter, signage, or door), with width_mm when readable. Glazed widths must not exceed the total; glazed plus stated excluded widths must sum to the total within 5% (unreadable excluded width: null). Elevation total vs plan span: 0 to +600 mm (wall thicknesses) or within 2%. "
                "shade: any awning/verandah/canopy/eave/balcony over this wall; depth_mm = projection from wall (printed or on a section), underside_mm = above floor; null if unprinted. "
                "JSON only: {\"total_width_mm\":number,\"total_width_text\":string,\"panels\":[{\"label\":string,\"width_mm\":number|null,\"sill_mm\":number|null,\"head_mm\":number|null,\"printed_text\":[string],\"source\":\"printed_text|read_from_image\"}],\"excluded\":[{\"label\":string,\"width_mm\":number|null,\"why\":string}],"
                "\"shade\":{\"kind\":\"awning|verandah|canopy|eave|balcony|none\",\"depth_mm\":n|null,\"underside_mm\":n|null,\"full_width\":bool,\"printed_text\":[]}}\n\n"
                +json.dumps({"page":page,"room":trace.get("room_label"),"perimeter_run_numbers":[index+1 for index in run_indices],
                    "room_length_on_run_m":round(sum(float(row["length_m"]) for row in part_edges),2),
                    "perimeter_run_span_m":run_span,"ceiling_height_mm":ceiling,
                    "text_layer":texts},ensure_ascii=False,separators=(",",":")))
            kept=len(text_layer)
            prompt=opening_prompt(text_layer)
            while len(prompt)>2000 and kept>0:
                kept-=1
                prompt=opening_prompt(text_layer[:kept])
            packet={"task":"P4_openings","room_id":room_id,"room_label":trace.get("room_label"),
                "trace_id":trace["trace_id"],"trace_ids":[t["trace_id"] for t in room_traces],
                "part_edges":part_edges,"edge_index":part_edges[0]["edge_index"],
                "edge_indices":list(dict.fromkeys(row["edge_index"] for row in part_edges)),
                "wall_run_index":run_index,"wall_run_indices":run_indices,"wall_run_span_m":run_span,"edge_length_mm":run_span*1000,
                "run_total_length_m":run_total,
                "page":page,"ceiling_height_mm":ceiling,"text_layer":text_layer[:60],
                "storefront_match_reason":storefront_reason,
                "glazing_choice":glazing,"shading_category":shading,
                "glazing_option":f"Preliminary single glazing (pack au-preliminary-v3): U {assumption['profiles'][glazing]['glazing_u_w_m2k']}, SHGC {assumption['profiles'][glazing]['shgc']}",
                "frame_fraction":assumption.get("preliminary_envelope",{}).get("glazing_frame_fraction"),"evidence":{"page":page}}
            result.append(("P4_openings",target,packet,prompt,[_png_bytes(image)],""))
    return result


CONTEXT_TITLE = re.compile(r"\b(?:site|location|locality|context|streetscape|street\s+elevation|neighbour|surrounding)", re.I)


def _context_pages(root, plan_page):
    """Up to two sheets that can show what stands around the building: site/location/context plans first, then
    streetscape or context elevations (pass 1 page reading and the sheet titles)."""
    ai_input = _read(root / "ai_input.json", {})
    inventory = _read(root / "page_inventory.json", {}).get("pages") or {}
    plans, elevations = [], []
    for row in (ai_input.get("drawing_set", {}) or {}).get("pages", []):
        if not isinstance(row, dict) or type(row.get("page")) is not int or row["page"] == plan_page:
            continue
        reading = inventory.get(str(row["page"])) or {}
        title = " ".join(str(value or "") for value in (row.get("title"), reading.get("title"), row.get("type")))
        if reading.get("page_type") in {"render_or_photo", "existing_or_demolition"}:
            continue
        if (reading.get("page_type") == "site_or_location_plan" or reading.get("floor_plan_kind") == "outdoor_or_context"
                or (reading.get("page_type") in {"floor_plan", ""} and CONTEXT_TITLE.search(title))):
            plans.append({"page": row["page"], "title": str(reading.get("title") or row.get("title") or "site plan")})
        elif reading.get("page_type") == "elevation" and CONTEXT_TITLE.search(title):
            elevations.append({"page": row["page"], "title": str(reading.get("title") or row.get("title") or "street elevation")})
    return (plans + elevations)[:2]


def _context_page_image(root, page_number):
    """One whole sheet, at most 2,048 px, for reading what surrounds the building."""
    from PIL import Image
    source = autonomous_tasks._page_screenshot(root, page_number)
    if source:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    else:
        import pdfplumber
        ai_input, _spatial, _building = _load_inputs(root)
        pdf_path = Path(str(ai_input.get("source_pdf", "")))
        if not pdf_path.is_file():
            raise ValueError(f"No rendered image or source PDF is available for page {page_number}.")
        with pdfplumber.open(pdf_path) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                raise ValueError(f"Page {page_number} is outside the source PDF.")
            image = pdf.pages[page_number - 1].to_image(resolution=150).original.convert("RGB")
    image.thumbnail((2048, 2048))
    return image


def _tenancy_closeup(image):
    """A close-up of a site/location plan around the tenancy, which these sheets mark in red (an outline or hatch):
    on the whole sheet it is only a couple of hundred pixels across (Butcher Buffet, 2026-10-26: its south side was
    misread). None when no red marking is found."""
    import numpy
    pixels = numpy.asarray(image.convert("RGB")).astype(int)
    red = (pixels[:, :, 0] > 170) & (pixels[:, :, 1] < 100) & (pixels[:, :, 2] < 100)
    if red.sum() < 150:
        return None
    rows, cols = numpy.nonzero(red)
    top, bottom = numpy.percentile(rows, [1, 99]).astype(int)
    left, right = numpy.percentile(cols, [1, 99]).astype(int)
    pad = int(max(bottom - top, right - left) * 0.8) + 40
    box = (max(0, left - pad), max(0, top - pad), min(image.width, right + pad), min(image.height, bottom + pad))
    crop = image.crop(box)
    scale = 1400 / max(crop.size)
    return crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))) if scale > 1 else crop


def _run_outward_azimuth(union, run, plan_up_bearing):
    """The compass bearing a perimeter wall run faces outwards: the side of its midpoint outside the rooms' union."""
    from ai import site_orientation
    from shapely.geometry import Point
    (x0, y0), (x1, y1) = run["start_px"], run["end_px"]
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return None
    mid, normal = ((x0 + x1) / 2, (y0 + y1) / 2), ((y1 - y0) / length, -(x1 - x0) / length)   # "left" of start→end
    step = max(2.0, length * 0.02)
    left_outside = not union.buffer(0).contains(Point(mid[0] + normal[0] * step, mid[1] + normal[1] * step))
    return site_orientation.outward_azimuth([run["start_px"], run["end_px"]], "left" if left_outside else "right", plan_up_bearing)


def _p3_surroundings_packets(root):
    """What stands opposite each outside wall (user decision 2026-10-26): one question per traced plan page that has
    walls to the outside and a site/context sheet to read them from. A plan with no outside wall (a mall tenancy) or
    no such sheet is not asked: its walls get full sun, and the result says the surroundings weren't shown."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    result = []
    try:
        traces = reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    except (OSError, ValueError, TypeError, KeyError):
        return result
    comfort = _comfort_room_ids(root)
    north = _read(root / "reviewer_room_geometry.json", {}).get("page_north", {})
    by_page = {}
    for trace in traces:
        if trace.get("room_id") in comfort and autonomous_tasks.usable_calibration(trace.get("calibration", {}).get("status")):
            by_page.setdefault(trace.get("page"), []).append(trace)
    for plan_page, page_traces in sorted(by_page.items()):
        p3 = _current_task(root, "P3_boundaries", f"page-{plan_page}")
        bearing = (north.get(str(plan_page)) or {}).get("plan_up_azimuth_deg")
        if not p3 or p3.get("status") not in {"applied", "below_accuracy_bar"} or bearing is None:
            continue
        perimeter = _page_perimeter_data(page_traces)
        if perimeter["error"]:
            continue
        boundaries = {row.get("index"): row.get("boundary") for row in (p3.get("applied_value") or {}).get("runs", [])}
        outside = [row for row in perimeter["runs"] if boundaries.get(row["run_index"]) == "external" and float(row["span_m"]) >= 1.0]
        context = _context_pages(root, plan_page)
        if not outside or not context:
            continue
        union = unary_union([Polygon(trace["points_image_px"]) for trace in page_traces])
        walls, wall_edges = {}, {}
        for row in outside:
            azimuth = _run_outward_azimuth(union, row, float(bearing))
            if azimuth is None:
                continue
            number = row["run_index"] + 1
            walls[str(number)] = {"faces": f"{_compass8(azimuth)} ({azimuth:.0f}°)", "length_m": round(float(row["span_m"]), 2)}
            wall_edges[str(number)] = {"run_index": row["run_index"], "azimuth_deg": round(azimuth, 1),
                                       "edges": [[trace_id, edge["edge_index"]] for trace_id, edges in perimeter["trace_edges"].items()
                                                 for edge in edges if edge.get("perimeter_run_index") == row["run_index"]]}
        if not walls:
            continue
        target = f"page-{plan_page}"
        packet = {"task": "P3_surroundings", "page": plan_page, "walls": walls, "wall_edges": wall_edges,
                  "context_pages": [row["page"] for row in context], "evidence": {"page": plan_page}}
        try:
            plan_image, packet["image_transform"] = _perimeter_task_image(root, page_traces, perimeter,
                                                                          [{"index": int(number) - 1} for number in walls])
            images, titles = [_png_bytes(plan_image)], []
            for row in context:
                sheet = _context_page_image(root, row["page"])
                images.append(_png_bytes(sheet))
                titles.append(f"page {row['page']}: {row['title']}")
                closeup = _tenancy_closeup(sheet) if not re.search(r"elevation|streetscape", row["title"], re.I) else None
                if closeup is not None and len(images) < 4:
                    images.append(_png_bytes(closeup))
                    titles.append(f"a close-up of page {row['page']} around the tenancy (marked in red)")
        except (OSError, ValueError, IndexError) as error:
            result.append(("P3_surroundings", target, packet, "", [], str(error)))
            continue
        packet["context_images"] = titles
        prompt = autonomous_tasks.surroundings_prompt(walls, titles)
        result.append(("P3_surroundings", target, packet, prompt, images, ""))
    return result


def _compass8(azimuth):
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int(((azimuth % 360) + 22.5) // 45) % 8]


def _apply_p3_surroundings(record, validated):
    """Keep each wall's answer with the room edges it covers; the calculation reads it from this record."""
    packet = record["packet"]
    walls = []
    for number, answer in sorted(validated["walls"].items()):
        mapping = packet["wall_edges"].get(str(number), {})
        walls.append({"wall_number": number, "run_index": mapping.get("run_index"), "azimuth_deg": mapping.get("azimuth_deg"),
                      "edges": mapping.get("edges", []), **answer})
    record.update({"status": _status_for_accuracy("ai_determined", record.get("accuracy", {})), "source": "ai_determined",
                   "applied_value": {"page": packet["page"], "walls": walls}, "validation": validated, "block_reason": ""})
    return record


def surroundings_by_edge(root):
    """{(trace_id, edge_index): the building opposite that wall edge} from the current surroundings answers: buildings
    with a distance, and walls the site plan shows face more of the same building ({"inside": True}); open ground and
    unshown sides give full sun."""
    result = {}
    for pointer in (Path(root) / "ai_tasks" / "P3_surroundings").glob("*/current.json"):
        current = _read(pointer, {})
        record = _read(pointer.parent / current.get("run_path", "") / "record.json", {})
        if record.get("status") not in {"applied", "below_accuracy_bar"}:
            continue
        for wall in (record.get("applied_value") or {}).get("walls", []):
            if wall.get("opposite") == "inside_building":
                # The site plan shows this "outside" wall faces more of the same building: no sun on it.
                for trace_id, edge_index in wall.get("edges", []):
                    result[(str(trace_id), int(edge_index))] = {"inside": True, "evidence": wall.get("evidence", ""),
                                                                "wall_number": wall.get("wall_number")}
                continue
            if wall.get("opposite") != "building" or not wall.get("distance_m") or not wall.get("height_m"):
                continue
            for trace_id, edge_index in wall.get("edges", []):
                result[(str(trace_id), int(edge_index))] = {
                    "distance_m": wall["distance_m"], "height_m": wall["height_m"], "height_basis": wall.get("height_basis", ""),
                    "full_length": wall.get("full_length", True), "evidence": wall.get("evidence", ""),
                    "wall_number": wall.get("wall_number"), "page": (record.get("applied_value") or {}).get("page")}
    return result


def _p0_trace_rows(root, record, polygons):
    page = int(record["packet"]["page"])
    calibration = deepcopy(record["packet"]["calibration"])
    if not autonomous_tasks.usable_calibration(calibration.get("status")):
        raise ValueError("P0 cannot apply room areas until printed dimensions calibrate within 2%.")
    rooms = reviewer_room_geometry_service._rooms(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    room_ids = {}
    for room in rooms:
        room_ids.setdefault(room["label"].casefold(), room["room_id"])
    # A name on more than one level (a house's Bathroom, Corridor, Ensuite) is this page's level's room.
    from ai.page_inventory import level_key
    page_level = level_key(_page_level(root, page))
    levels = {}
    for room in rooms:
        levels.setdefault(room["label"].casefold(), {}).setdefault(level_key(room.get("level_name")), room["room_id"])
    for label, by_level in levels.items():
        if len(by_level) > 1 and page_level in by_level:
            room_ids[label] = by_level[page_level]
    printed = {row["label"].casefold(): float(row["area_m2"]) for row in _inferred_room_data(root)
               if isinstance(row.get("area_m2"), (int, float)) and row["area_m2"] > 0}
    counts = {}
    for row in polygons:
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    indexes = {}
    results = []
    for row in polygons:
        room_id = room_ids.get(row["label"].casefold())
        if not room_id:
            continue
        indexes[row["label"]] = indexes.get(row["label"], 0) + 1
        polygon = row["polygon"]
        mm_per_px = float(calibration["mm_per_px"])
        simplified = _simplify_trace_polygon(polygon, 250.0 / mm_per_px)
        coords = [[round(float(x), 2), round(float(y), 2)] for x, y in simplified.exterior.coords]
        # The reviewer trace validator's historical area check accepts only a
        # positive signed winding. Shapely's exterior may use either winding,
        # so normalize it here without changing the extracted boundary.
        signed_twice_area = sum(coords[index][0] * coords[index + 1][1]
                                - coords[index + 1][0] * coords[index][1]
                                for index in range(len(coords) - 1))
        if signed_twice_area < 0:
            coords = list(reversed(coords))
        results.append({"room_id": room_id, "room_label": row["label"], "page": page,
            "points_image_px": coords, "snapped_line_ids": [None] * len(coords),
            "calibration": calibration, "part_index": indexes[row["label"]], "part_count": counts[row["label"]],
            "ai_measured_area_m2": room_outline.area_m2(row.get("net_polygon") or polygon, mm_per_px),
            "method": row.get("method", "ai_outline_snapped"),
            "outline_simplification_max_deviation_mm": 250.0,
            "note": row.get("conflict", ""), "evidence": [{"page":page, "method":row.get("method"),
                "printed_area_m2":printed.get(row["label"].casefold())}],
            **({"printed_area_m2": printed[row["label"].casefold()]} if row["label"].casefold() in printed else {})})
    return results


def _simplify_trace_polygon(polygon, max_tolerance_px, max_area_change_fraction=0.01):
    """Reduce vector-buffer vertices while bounding outline and area change."""
    if max_tolerance_px <= 0 or polygon.area <= 0:
        return polygon
    candidate = polygon.simplify(max_tolerance_px, preserve_topology=True)
    if candidate.is_empty or candidate.area <= 0:
        return polygon
    if abs(candidate.area - polygon.area) / polygon.area <= max_area_change_fraction:
        return candidate
    low, high = 0.0, max_tolerance_px
    best = polygon
    for _ in range(16):
        middle = (low + high) / 2
        tested = polygon.simplify(middle, preserve_topology=True)
        change = abs(tested.area - polygon.area) / polygon.area if tested.area > 0 else float("inf")
        if not tested.is_empty and change <= max_area_change_fraction:
            best, low = tested, middle
        else:
            high = middle
    return best


def _apply_p0_dimensions(root, record, validated):
    record.update({"status":"applied", "source":"ai_determined", "applied_value":validated,
                   "validation":validated, "block_reason":""})
    page = record["packet"].get("page")
    try:
        calibration = _p0_calibration(root, page)
        record["calibration_status"] = calibration["status"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        record["calibration_status"] = "pending_more_dimensions"
        record["calibration_note"] = str(error)
    return record


def _apply_p0_scale_read(root, record, validated):
    page = record["packet"].get("page")
    context = _p0_context(root, page)
    calibration = _scale_read_calibration(validated, context)
    if not calibration:
        raise ValueError("The printed scale implies a plan outside 2-400 m; continue to the next scale method.")
    record.update({"status": "applied", "source": "ai_determined", "applied_value": calibration,
                   "validation": validated, "calibration_status": calibration["status"], "block_reason": ""})
    return record


def _apply_p0_scale_objects(root, record, validated):
    record.update({"status": "applied", "source": "ai_determined", "applied_value": validated,
                   "validation": validated, "calibration_status": "standard_objects", "block_reason": ""})
    return record


def _apply_p0_wall_styles(record, validated):
    wall_ids = validated.get("wall_style_ids", [])
    record.update({"status":"applied", "source":"ai_determined", "applied_value":{"wall_style_ids":wall_ids,
        "legend":deepcopy(record["packet"].get("summary", [])), "label":record["quality_label"]},
        "validation":{"wall_style_ids":wall_ids}, "block_reason":""})
    return record


def _apply_p0_room_names(web, project, root, record, validated, *, defer=False):
    from shapely.geometry import Polygon
    packet = record["packet"]
    context = _p0_context(root, packet["page"])
    walls, areas, segments = _p0_walls_areas(root, packet["page"], context, packet["calibration"],
                                             packet.get("wall_style_ids", []), packet.get("raster_mode", False))
    names, splits = validated["names"], validated["splits"]
    # Rooms named by their use must be in the room list before their outlines are saved.
    from backend import room_proposal
    level = _page_level(root, packet["page"])
    # A house's room names from the page reading become rooms when the AI gives one to an area.
    read_names = {str(name).casefold(): str(name) for name in packet.get("read_room_names", [])}
    named = sorted({read_names[str(name).casefold()] for name in names.values() if str(name).casefold() in read_names}
                   | {read_names[str(item["room"]).casefold()] for split in splits for item in split["rooms"]
                      if str(item["room"]).casefold() in read_names})
    room_proposal.save_use_named_rooms(root, "P0_room_names", packet["page"], level, validated.get("use_named_rooms", []),
                                       record["run_id"])
    room_proposal.save_use_named_rooms(root, "P0_room_names_read", packet["page"], level, named, record["run_id"],
                                       named_on_plan=True)
    snap_px = 250 / packet["calibration"]["mm_per_px"]
    by_room = {}
    for index, name in names.items():
        if name != room_outline.NOT_A_ROOM:
            by_room.setdefault(name, []).append(areas[index])
    polygons = [{"label":name, "polygon":polygon, "method":"enclosed_walls"}
                for name,parts in by_room.items() for polygon in parts]
    for split in splits:
        line, report = room_outline.snap_polyline(split["line"], segments, snap_px)
        pieces = room_outline.split_area(areas[split["area_index"]], line, split["rooms"])
        polygons.extend({"label":name,"polygon":polygon,"method":"ai_split_snapped",
                         "note":split.get("feature", "") + f"; snapped {sum(item['snapped'] for item in report)}/{len(report)} segments"}
                        for name,polygon in pieces.items())
    rows = _p0_trace_rows(root, record, polygons)
    prepared = reviewer_room_geometry_service.persist_ai_determined_traces(web, project, rows,
        record["run_id"], record["quality_label"], replace_pages=[packet["page"]], defer=defer)
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]), "source":"ai_determined",
        "applied_value":{"rooms":_p0_result_rooms(prepared),
                        "label":record["quality_label"]}, "validation":validated, "block_reason":""})
    return record


def _main_plan_levels(root):
    from ai import page_inventory
    readings = {int(number): row for number, row in (_read(Path(root) / "page_inventory.json", {}).get("pages") or {}).items()
                if str(number).isdigit() and isinstance(row, dict)}
    coverage = {row.get("page"): row.get("level_name") for row in _read(Path(root) / "drawing_coverage.json", {}).get("page_roles", [])
                if isinstance(row, dict)}
    return page_inventory.main_plan_levels(readings, coverage)


def _page_level(root, page):
    """The page's level. On a plan the page reading chose as a level's main plan, the page reading's level (it read the
    plan's title: house set 15's "FIRST FLOOR PLAN" was "Ground Floor" in drawing coverage, from "Natural ground level"
    in its legend, 2026-11-03); else drawing coverage's; else the page reading's."""
    level = _main_plan_levels(root).get(int(page))
    if level:
        return level
    return next((row.get("level_name") for row in _read(root / "drawing_coverage.json", {}).get("page_roles", [])
                 if isinstance(row, dict) and row.get("page") == page and row.get("level_name")), "") or \
        str(((_read(root / "page_inventory.json", {}).get("pages") or {}).get(str(page)) or {}).get("level") or "")


def _apply_p0_outlines(web, project, root, record, validated, *, defer=False):
    from shapely.geometry import Polygon
    from backend import room_proposal
    asked_names = {str(name).casefold() for name in record["packet"].get("room_names", [])}
    use_named = sorted({row["room"] for row in validated if row["room"].casefold() not in asked_names})
    # Rooms named by their use (a space no printed name covers) must be in the room list before their outlines are saved.
    room_proposal.save_use_named_rooms(root, "P0_room_outlines", record["packet"]["page"],
                                       _page_level(root, record["packet"]["page"]), use_named, record["run_id"])
    context = _p0_context(root, record["packet"]["page"])
    calibration = record["packet"]["calibration"]
    _walls, _areas, segments = _p0_walls_areas(root, record["packet"]["page"], context, calibration,
        record["packet"].get("wall_style_ids", []), record["packet"].get("raster_mode", False))
    snap_px = 250 / record["packet"]["calibration"]["mm_per_px"]
    polygons = []
    tenancy = _tenancy_polygon(root, record["packet"]["page"])
    mm_per_px = float(calibration["mm_per_px"])
    for row in validated:
        snapped, report = room_outline.snap_polygon(row["points"], segments, snap_px)
        note = (f"Open sides: {row.get('open_sides','')}; snapped {report['snapped_edges']}/{report['edges']} edges"
                + ("; the AI's outline crossed itself and was split where it crossed" if row.get("repaired") else ""))
        polygons.extend({"label":row["room"], "polygon":polygon, "method":"ai_outline_snapped", "note":note}
                        for polygon in room_outline.clip_to_tenancy(snapped, tenancy, mm_per_px))
    # Rooms already traced on this page by the enclosed-wall step (e.g. a walled bar island) stay, and are taken off
    # an outer room drawn around them, like the rooms in this reply.
    asked = {str(name).casefold() for name in record["packet"].get("room_names", [])}
    try:
        others = [(row["room_label"], Polygon(row["points_image_px"]))
                  for row in reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths(project))
                  if row.get("page") == record["packet"]["page"] and str(row.get("room_label", "")).casefold() not in asked
                  and isinstance(row.get("points_image_px"), list) and len(row["points_image_px"]) >= 4]
    except (OSError, ValueError, TypeError, KeyError):
        others = []
    nested = room_outline.subtract_nested([(row["label"], row["polygon"]) for row in polygons] + others)
    for row, (net, taken) in zip(polygons, nested):
        if taken:   # a room drawn inside this one (a bar in the dining area): its floor is not counted twice
            row["net_polygon"] = net
            row["conflict"] = f"Area excludes {', '.join(taken)}, drawn inside it."
    rows = _p0_trace_rows(root, record, polygons)
    trace_kwargs = {"defer": True} if defer else {}
    prepared = reviewer_room_geometry_service.persist_ai_determined_traces(web, project, rows,
        record["run_id"], record["quality_label"], replace_pages=[record["packet"]["page"]],
        replace_room_labels=[*record["packet"].get("room_names", []), *use_named], **trace_kwargs)
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"rooms":_p0_result_rooms(prepared),"label":record["quality_label"]},
        "validation":validated,"block_reason":""})
    return record


def _apply_p0_unnamed_space(web, project, root, record, validated, *, defer=False):
    """Save the untinted spaces as rooms named by their use: snapped to the drawn lines, less any traced room they
    overlap, and only when what is left is room-sized."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from backend import room_proposal
    packet = record["packet"]
    page = packet["page"]
    calibration = packet["calibration"]
    mm_per_px = float(calibration["mm_per_px"])
    context = _p0_context(root, page)
    try:    # the drawn lines the outline snaps to, as for the open-plan outlines
        raster_mode = _p0_is_raster(root, page, context, calibration)
        wall_style_ids = [] if raster_mode else sorted({style_id for row in _p0_wall_style_records(root, page)
                                                        for style_id in row.get("applied_value", {}).get("wall_style_ids", [])})
        if wall_style_ids:
            wall_style_ids, _added = _completed_wall_styles(root, context, calibration, wall_style_ids)
        _walls, _areas, segments = _p0_walls_areas(root, page, context, calibration, wall_style_ids, raster_mode)
    except (OSError, ValueError, KeyError, TypeError):
        segments = []
    traced = [Polygon(row["points_image_px"]).buffer(0) for row in reviewer_room_geometry_service.current_records(
                  reviewer_room_geometry_service._paths(project))
              if row.get("page") == page and len(row.get("points_image_px") or []) >= 4]
    taken = unary_union(traced) if traced else None
    tenancy = _tenancy_polygon(root, page)
    polygons, dropped = [], []
    for row in validated:
        polygon, report = room_outline.snap_polygon(row["points"], segments, 250 / mm_per_px) if segments else (Polygon(row["points"]), {"snapped_edges": 0, "edges": len(row["points"])})
        inside = room_outline.clip_to_tenancy(polygon, tenancy, mm_per_px, min_m2=0)
        polygon = unary_union(inside) if inside else Polygon()
        net = polygon.difference(taken) if taken is not None else polygon
        pieces = [piece for piece in getattr(net, "geoms", [net]) if piece.geom_type == "Polygon" and not piece.is_empty
                  and room_outline.area_m2(piece, mm_per_px) >= room_outline.UNNAMED_SPACE_MIN_M2]
        if not pieces:
            dropped.append({"room": row["room"], "reason": "Mostly inside rooms already traced, or smaller than a room."})
            continue
        note = (f"Named by its use: {row['reason']}; snapped {report['snapped_edges']}/{report['edges']} edges"
                + ("; the AI's outline crossed itself and was split where it crossed" if row.get("repaired") else ""))
        polygons.extend({"label": row["room"], "polygon": piece, "method": "ai_outline_snapped", "note": note}
                        for piece in pieces)
    names = sorted({row["label"] for row in polygons})
    room_proposal.save_use_named_rooms(root, "P0_unnamed_space", page, _page_level(root, page), names, record["run_id"])
    prepared = []
    if polygons:
        rows = _p0_trace_rows(root, record, polygons)
        trace_kwargs = {"defer": True} if defer else {}
        prepared = reviewer_room_geometry_service.persist_ai_determined_traces(web, project, rows,
            record["run_id"], record["quality_label"], replace_pages=[page], replace_room_labels=names, **trace_kwargs)
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"rooms": _p0_result_rooms(prepared) if prepared else [], "dropped": dropped,
                                     "label": record["quality_label"]},
                   "validation": {"spaces": validated}, "block_reason": ""})
    return record


def _p0_result_rooms(prepared):
    from shapely.geometry import Polygon
    grouped={}
    for row in prepared:
        label=row["room_label"]
        value=row.get("ai_measured_area_m2") or room_outline.area_m2(Polygon(row["points_image_px"]),row["calibration"]["mm_per_px"])
        grouped.setdefault(label,[]).append((value,row))
    output=[]
    for label,parts in sorted(grouped.items()):
        measured=sum(value for value,_ in parts)
        printed=next((row.get("printed_area_m2") for _,row in parts if row.get("printed_area_m2")),None)
        method=parts[0][1].get("method","")
        chosen=room_outline.choose_area(printed, measured if method in {"enclosed_walls","ai_split_snapped"} else None,
                                       measured if method=="ai_outline_snapped" else None)
        source=chosen["source"]
        output.append({"label":label,"area_m2":round(float(chosen["area_m2"] or measured),2),
                       "source":source,"method":"printed_area" if source=="printed_on_drawing" else method,
                       "conflict":chosen.get("conflict", ""),"part_count":len(parts)})
    return output


def _current_traces(root, trace_ids):
    """Resolve a batch of current traces with one geometry/freshness pass."""
    paths = reviewer_room_geometry_service._paths({"review_dir": str(root)})
    wanted = set(trace_ids)
    return {row.get("trace_id"): row for row in reviewer_room_geometry_service.current_records(paths)
            if row.get("trace_id") in wanted}


def _current_trace(root, trace_id):
    paths=reviewer_room_geometry_service._paths({"review_dir":str(root)})
    return next((row for row in reviewer_room_geometry_service.current_records(paths) if row.get("trace_id")==trace_id),None)


def _classify_envelope(web, project, data, *, defer=False, **kwargs):
    if defer:
        kwargs["_defer_evidence_rebuild"] = True
    return reviewer_room_geometry_service.post(web, project, data, **kwargs)


def _apply_p3(web, project, root, record, validated, *, defer=False):
    packet=record["packet"]
    if packet.get("room_edges"):
        trace_ids=packet.get("trace_ids",[])
        traces=_current_traces(root,trace_ids)
        if set(traces)!=set(trace_ids):
            raise ValueError("A room trace is stale; rebuild the page boundary task from current drawing evidence.")
        run_values={row["index"]:row["boundary"] for row in validated.get("edges",[])}
        run_evidence={row["index"]:row.get("evidence","") for row in validated.get("edges",[])}
        for short_index,source_index in packet.get("short_run_inheritance",{}).items():
            if int(source_index) in run_values:
                run_values[int(short_index)]=run_values[int(source_index)]
                run_evidence[int(short_index)]=(
                    f"Inherited from nearest perimeter run {int(source_index)+1}: "
                    f"{run_evidence.get(int(source_index), '')}"
                ).strip()
        results=[]
        p3_label="Stand-in (test)" if record.get("stand_in") else record.get("quality_label","AI-determined")
        for trace_id,trace in traces.items():
            mapping={row["edge_index"]:row for row in packet["room_edges"] if row["trace_id"]==trace_id}
            current={row["index"]:row["boundary"] for row in trace.get("edges",[])}
            values={}; sources={}; evidence={}
            for index in range(len(trace.get("points_image_px",[]))-1):
                mapped=mapping.get(index,{}).get("perimeter_run_index")
                if mapped is None:
                    boundary="internal"; source="ai_determined"; reason="Room edge does not lie on the tenancy perimeter; classified internal automatically."
                else:
                    boundary=run_values.get(mapped,"unknown"); source="ai_determined"; reason=run_evidence.get(mapped,"")
                    share=mapping.get(index,{}).get("wall_coverage")
                    if (boundary=="unknown" and mapped!=packet.get("storefront_run_index") and share is not None
                            and share<OPEN_SIDE_WALL_SHARE and mapping.get(index,{}).get("faces_wall") is True):
                        boundary="internal"; source="ai_fallback"
                        reason=(f"No wall is drawn along this side ({round(share*100)}% walled) and it looks onto more of "
                                "the building, so it is open to the rest of the tenancy (e.g. a counter edge): treated as "
                                "inside the tenancy.")
                    elif boundary=="unknown":
                        boundary=("mall" if mapped==packet.get("storefront_run_index") and
                                  re.search(r"\b(?:enclosed\s+(?:mall|shopping\s+centre)|shopping\s+centre|mall)\b",
                                      packet.get("nearby_text", ""),re.I) else
                                  "external" if mapped==packet.get("storefront_run_index") else "adjacent_tenancy")
                        source="ai_fallback"; reason="Assumed (typical for a tenancy in a centre)."
                old_source=trace.get("edge_sources",{}).get(str(index),trace.get("declaration_source","reviewer"))
                if current.get(index)!="unknown" and old_source=="reviewer":
                    boundary=current[index]; source="reviewer"; reason="Reviewer-declared boundary."
                values[index]=boundary; sources[index]=source; evidence[index]=reason
            request_edges=[{"index":index,"boundary":boundary} for index,boundary in sorted(values.items())]
            _classify_envelope(web,project,{"action":"classify_envelope","trace_id":trace_id,
                "reviewer":"Archie AI (P3)","edges":request_edges,"roof":trace.get("roof","unknown"),
                "openings":trace.get("openings",[]),"declaration_source":"ai_determined","ai_run_id":record["run_id"],
                "edge_sources":{str(index):source for index,source in sources.items()},
                "boundary_evidence":[{"index":index,"text":evidence[index],"source":sources[index],"label":p3_label}
                                     for index in values]}, defer=True,
                _validated_current_records=tuple(traces.values()))
            results.append({"trace_id":trace_id,"room_id":trace.get("room_id"),"room":trace.get("room_label"),
                "edges":[{"index":index,"edge_length_m":mapping.get(index,{}).get("length_m"),
                    "boundary":values[index],"source":sources[index],"evidence":evidence[index],"label":p3_label}
                    for index in sorted(values)]})
        if web is not None and project and not defer:
            calculation_extraction_service.post(web, project, {"action": "build"})
        record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
            "applied_value":{"page":packet.get("page"),"rooms":results,
                "runs":[{"index":index,"boundary":boundary,"evidence":run_evidence.get(index,"")}
                        for index,boundary in sorted(run_values.items())],"label":p3_label},
            "validation":validated,"block_reason":""})
        return record
    trace=_current_trace(root,packet.get("trace_id"))
    if not trace: raise ValueError("The room trace is stale; rebuild the boundary task from current drawing evidence.")
    current={row["index"]:row["boundary"] for row in trace.get("edges",[])}
    points=trace.get("points_image_px",[])
    mm_per_px=float(trace.get("calibration",{}).get("mm_per_px") or packet.get("mm_per_px") or 0)
    lengths={index:math.dist(start,end)*mm_per_px/1000.0 for index,(start,end) in enumerate(zip(points,points[1:]))}
    shared=set(packet.get("shared_edges",[])); run_values={row["index"]:row["boundary"] for row in validated["edges"]}
    run_evidence={row["index"]:row.get("evidence","") for row in validated["edges"]}
    runs=packet.get("runs",[])
    for run_id, neighbour_id in packet.get("short_run_inheritance",{}).items():
        if int(neighbour_id) in run_values:
            run_values[int(run_id)]=run_values[int(neighbour_id)]
            run_evidence[int(run_id)]=f"Inherited from adjacent wall run {int(neighbour_id)+1}: {run_evidence.get(int(neighbour_id), '')}".strip()
    values={}; evidence={}
    for run in runs:
        run_index=run["run_index"]
        if run_index not in run_values:
            continue
        for edge_index in run["edge_indices"]:
            if edge_index in shared:
                continue
            values[edge_index]=run_values[run_index]
            evidence[edge_index]=run_evidence.get(run_index,"")
    if not runs:
        values.update(run_values)
        evidence.update(run_evidence)
    for index in shared:
        values[index]="internal"
        evidence[index]="Shared boundary with another traced room in this tenancy."
    sources={}
    for index in values:
        if index in shared:
            values[index],sources[index],evidence[index]="internal","ai_determined","Shared boundary with another traced room in this tenancy."
        elif values[index]=="unknown":
            storefront_edges=({edge for row in runs if row["run_index"]==packet.get("storefront_run_index") for edge in row["edge_indices"]}
                              if runs else {packet.get("storefront_edge_index")})
            if index in storefront_edges:
                enclosed=bool(re.search(r"\b(?:enclosed\s+(?:mall|shopping\s+centre)|shopping\s+centre|mall)\b",packet.get("nearby_text","")+" "+str(packet.get("site_name","")),re.I))
                values[index]="mall" if enclosed else "external"
            else: values[index]="adjacent_tenancy"
            sources[index]="ai_fallback"
            evidence[index]="Assumed (typical for a tenancy in a centre)"
        else: sources[index]="ai_determined"
        if current.get(index)!="unknown" and trace.get("edge_sources",{}).get(str(index),trace.get("declaration_source","reviewer"))=="reviewer":
            values[index]=current[index]; sources[index]="reviewer"
    request_edges=[{"index":index,"boundary":boundary} for index,boundary in sorted(values.items())]
    _classify_envelope(web,project,{"action":"classify_envelope","trace_id":trace["trace_id"],
        "reviewer":"Archie AI (P3)","edges":request_edges,"roof":trace.get("roof","unknown"),
        "openings":trace.get("openings",[]),"declaration_source":"ai_determined","ai_run_id":record["run_id"],
        "edge_sources":{str(index):source for index,source in sources.items()},
        "boundary_evidence":[{"index":index,"text":evidence.get(index,""),"source":sources.get(index)} for index in values]},
        defer=defer)
    edges=[{"index":index,"edge_length_m":lengths.get(index),"boundary":values[index],"source":sources[index],
            "evidence":evidence.get(index,""),"label":"Assumed (typical for a tenancy in a centre)" if sources[index]=="ai_fallback" else "AI-determined"}
           for index in sorted(values)]
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"edges":edges,"label":record.get("quality_label","AI-determined")},
        "validation":validated,"block_reason":""})
    return record


def _apply_p4_wall(web, project, root, record, validated, *, defer=False):
    """Keep the answer and make the chosen walls face what the storefront faces: a storefront wall is outside (street)
    or mall, whatever the wall-facing step said. A reviewer's own classification is never overwritten."""
    faces={}
    for row in validated["elevations"].values():
        for index in row.get("run_indices", []):
            faces[index]="mall" if row.get("faces")=="mall" else "external"
    corrected=[]
    if faces and root is not None:
        page=record["packet"]["page"]
        try:
            traces=[trace for trace in reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths(
                {"review_dir":str(root)})) if trace.get("page")==page]
        except (OSError,ValueError,TypeError,KeyError):
            traces=[]
        perimeter=_page_perimeter_data(traces) if traces else {"trace_edges":{}}
        for trace in traces:
            edges={row["index"]:row.get("boundary","unknown") for row in trace.get("edges",[])}
            sources=dict(trace.get("edge_sources",{}))
            changed=False
            for mapping in perimeter.get("trace_edges",{}).get(trace["trace_id"],[]):
                index,run=mapping["edge_index"],mapping.get("perimeter_run_index")
                if run not in faces or edges.get(index)==faces[run]:
                    continue
                if sources.get(str(index),trace.get("declaration_source"))=="reviewer" and edges.get(index) not in {None,"unknown"}:
                    continue
                edges[index]=faces[run]; sources[str(index)]="ai_determined"; changed=True
                corrected.append({"room":trace.get("room_label"),"edge_index":index,"boundary":faces[run]})
            if changed:
                _classify_envelope(web,project,{"action":"classify_envelope","trace_id":trace["trace_id"],
                    "reviewer":"Archie AI (P4 storefront)","edges":[{"index":index,"boundary":value} for index,value in sorted(edges.items())],
                    "roof":trace.get("roof","unknown"),"openings":trace.get("openings",[]),"declaration_source":"ai_determined",
                    "ai_run_id":record["run_id"],"edge_sources":sources}, defer=defer)
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"elevations":{str(page):row for page,row in validated["elevations"].items()},
                         "corrected_walls":corrected,"label":record["quality_label"]},
        "validation":validated,"block_reason":""})
    return record


def _p4_panel_shade(validated, panel):
    """{"shade": …} for one applied panel from the reply's awning/verandah: depth in metres and its underside's height
    above this panel's glass head (the read head, before any ceiling cap); {} when the reply has none."""
    shade=validated.get("shade")
    if not shade: return {}
    head=float(panel.get("head_read_mm",panel["head_mm"]))
    gap=(shade["underside_mm"]-head)/1000 if shade.get("underside_mm") else None
    return {"shade":{"kind":shade["kind"],"depth_m":shade["depth_mm"]/1000 if shade.get("depth_mm") else None,
                     "gap_m":max(gap,0.0) if gap is not None else None,"full_width":shade.get("full_width",True),
                     "evidence":shade.get("printed_text",[])}}


def _apply_p4(web, project, root, record, validated, *, defer=False):
    packet=record["packet"]
    trace=_current_trace(root,packet.get("trace_id"))
    if not trace: raise ValueError("The traced shopfront edge is stale; rebuild the opening task.")
    pack=__import__("ai.ai_preliminary",fromlist=["load_pack"]).load_pack()
    choice=packet.get("glazing_choice","retail"); shade=packet.get("shading_category","unshaded")
    if choice not in pack.get("profiles",{}) or shade not in pack.get("preliminary_envelope",{}).get("shading_categories",{}):
        raise ValueError("The configured preliminary glazing or shading choice is unavailable.")
    if packet.get("part_edges"):
        trace_by_id={trace_id:_current_trace(root,trace_id) for trace_id in packet.get("trace_ids",[])}
        if any(row is None for row in trace_by_id.values()):
            raise ValueError("A room part trace is stale; rebuild the opening task.")
        part_edges=packet["part_edges"]
        # Shares of the whole storefront wall: a room on part of it takes only its part of the glass.
        total_length=float(packet.get("run_total_length_m") or 0) or sum(float(row.get("length_m") or 0) for row in part_edges)
        if total_length<=0:
            raise ValueError("The selected storefront run has no room-part edge length.")
        existing={trace_id:[row for row in trace.get("openings",[]) if row.get("declaration_source","reviewer")=="reviewer"]
                  for trace_id,trace in trace_by_id.items()}
        openings_by_trace={trace_id:[] for trace_id in trace_by_id}
        applied_panels=[]
        for panel in validated.get("panels",[]):
            panel_width=panel["width_mm"]/1000.0
            fragments=[]
            for part in part_edges:
                share=panel_width*float(part["length_m"])/total_length
                if share<=1e-9: continue
                trace=trace_by_id[part["trace_id"]]
                edge_index=part["edge_index"]
                points=trace.get("points_image_px",[])
                mm_per_px=float(trace.get("calibration",{}).get("mm_per_px") or 0)
                edge_length=math.dist(points[edge_index],points[edge_index+1])*mm_per_px/1000.0
                if share>edge_length+1e-6:
                    raise ValueError("A proportional glazing share exceeds its traced room-part edge.")
                opening={"opening_id":f"ai-{record['run_id']}-{panel['panel_index']}-{part['trace_id']}-{edge_index}",
                    "edge_index":edge_index,"width_m":share,"sill_height_m":panel["sill_mm"]/1000,
                    "head_height_m":panel["head_mm"]/1000,"elevation_page":packet["page"],
                    "glazing_choice":choice,"shading_category":shade,"declaration_source":"ai_determined",
                    **_p4_panel_shade(validated,panel),
                    "ai_run_id":record["run_id"],"evidence":panel.get("printed_text",[]),
                    "assumptions":[item for item in (panel.get("sill_assumption"),panel.get("head_assumption")) if item]}
                openings_by_trace[part["trace_id"]].append(opening)
                fragments.append({"trace_id":part["trace_id"],"edge_index":edge_index,"width_m":share})
            applied_panels.append({"width_mm":panel["width_mm"],"sill_mm":panel["sill_mm"],"head_mm":panel["head_mm"],
                "source":"ai_determined","parts":fragments,
                "sill_assumption":panel.get("sill_assumption"),"head_assumption":panel.get("head_assumption")})
        for trace_id,part_trace in trace_by_id.items():
            _classify_envelope(web,project,{"action":"classify_envelope","trace_id":trace_id,
                "reviewer":"Archie AI (P4)","edges":part_trace.get("edges",[]),"roof":part_trace.get("roof","unknown"),
                "openings":[*existing[trace_id],*openings_by_trace[trace_id]],
                "declaration_source":"ai_determined","ai_run_id":record["run_id"]}, defer=defer)
        record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
            "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"page":packet["page"],
                "total_width_mm":validated["total_width_mm"],"glazed_panels":applied_panels,
                "trace_ids":list(trace_by_id),"excluded":validated.get("excluded",[]),
                "glazing_option":packet.get("glazing_option"),"label":record["quality_label"]},
            "validation":validated,"block_reason":""})
        if web is not None and project and not defer:
            calculation_extraction_service.post(web, project, {"action": "build"})
        return record
    existing=[row for row in trace.get("openings",[]) if row.get("declaration_source","reviewer")=="reviewer"]
    openings=[]
    points=trace.get("points_image_px",[])
    mm_per_px=float(trace.get("calibration",{}).get("mm_per_px") or 0)
    edge_indices=packet.get("edge_indices") or [packet["edge_index"]]
    remaining={index:math.dist(points[index],points[index+1])*mm_per_px/1000.0 for index in edge_indices}
    applied_panels=[]
    for panel in validated.get("panels",[]):
        width_remaining=panel["width_mm"]/1000.0
        panel_edges=[]
        fragment=0
        for index in edge_indices:
            take=min(width_remaining,remaining[index])
            if take<=1e-9:
                continue
            fragment+=1
            openings.append({"opening_id":f"ai-{record['run_id']}-{panel['panel_index']}-{fragment}","edge_index":index,
                "width_m":take,"sill_height_m":panel["sill_mm"]/1000,"head_height_m":panel["head_mm"]/1000,
                "elevation_page":packet["page"],"glazing_choice":choice,"shading_category":shade,
                **_p4_panel_shade(validated,panel),
                "declaration_source":"ai_determined","ai_run_id":record["run_id"],"evidence":panel.get("printed_text",[]),
                "assumptions":[item for item in (panel.get("sill_assumption"),panel.get("head_assumption")) if item]})
            panel_edges.append(index)
            remaining[index]-=take
            width_remaining-=take
            if width_remaining<=1e-9:
                break
        if width_remaining>1e-6:
            raise ValueError("The glazing widths exceed the combined lengths of the traced wall-run edges.")
        applied_panels.append({"width_mm":panel["width_mm"],"sill_mm":panel["sill_mm"],"head_mm":panel["head_mm"],
            "source":"ai_determined","edge_indices":panel_edges,
            "sill_assumption":panel.get("sill_assumption"),"head_assumption":panel.get("head_assumption")})
    _classify_envelope(web,project,{"action":"classify_envelope","trace_id":trace["trace_id"],
        "reviewer":"Archie AI (P4)","edges":trace.get("edges",[]),"roof":trace.get("roof","unknown"),
        "openings":[*existing,*openings],"declaration_source":"ai_determined","ai_run_id":record["run_id"]},
        defer=defer)
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"page":packet["page"],
          "total_width_mm":validated["total_width_mm"],"glazed_panels":applied_panels,"excluded":validated.get("excluded",[]),
          "glazing_option":packet.get("glazing_option"),"label":record["quality_label"]},
        "validation":validated,"block_reason":""})
    return record


def _refresh_geometry_tasks(root, room_id=None, page_number=None):
    if room_id is not None or page_number is not None:
        # A trace edit only needs geometry tasks for that room. P3 is page-scoped,
        # so its packet includes every current room part on the affected page.
        builders = (
            lambda: _p3_packets(root, room_id=room_id, page_number=page_number),
            lambda: _p4_packets(root, only_room_id=room_id),
            lambda: _p6_kitchen_packets(root, room_id=room_id),
        )
        for task, target, packet, prompt, images, reason in (row for builder in builders for row in builder()):
            budget = autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root, task, target, packet, prompt, images, reason or
                        (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened." if len(prompt) > budget else ""))
        _refresh_roof_tasks(root, room_id=room_id)
        return
    # Persist naming tasks before asking whether any rooms still need outlines.
    # If both packet lists are calculated together, the outline builder cannot
    # see the just-created naming tasks and offers an unnecessary second task.
    _refresh_site_tasks(root)
    for packet_builder in (_p0_initial_packets, _p0_followup_packets, _p0_tenancy_packets, _p0_outline_packets, _p0_unnamed_space_packets, _p0_corner_packets, _s1_packets, _p0_size_packets):
        for task,target,packet,prompt,images,reason in packet_builder(root):
            budget=autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root,task,target,packet,prompt,images,reason or
                        (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened." if len(prompt)>budget else ""))
    _refresh_roof_tasks(root)
    for packet_builder in (_p3_packets,_p3_surroundings_packets,_p7_packets,_p4_packets,_p6_kitchen_packets):
        for task,target,packet,prompt,images,reason in packet_builder(root):
            budget=autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root,task,target,packet,prompt,images,reason or
                        (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened." if len(prompt)>budget else ""))


class NotNeeded(str):
    """A builder's reason that there is nothing to ask and nothing is missing (no dimension line to read, a page with no
    plan image, no kitchen): the task is saved as not needed instead of blocked, so it doesn't count as needing a person
    (Global Exchange and Happy Cap 2026-11-02)."""


def _create_run(root, task, target, packet, prompt, image_bytes=None, blocked_reason=""):
    # A size limit keeps questions small and cheap; it never blocks one (user rule 2026-11-03: Phase 1 always
    # completes). The builders shorten their evidence first; what is still over is asked and the overrun recorded.
    if (prompt and blocked_reason and not isinstance(blocked_reason, NotNeeded)
            and re.search(r"\bexceeds\b.*\b(?:limit|characters)\b", str(blocked_reason), re.I)):
        packet = {**packet, "prompt_over_budget": {"chars": len(prompt),
                                                   "budget": autonomous_tasks.TASKS.get(task, {}).get("budget_chars")}}
        blocked_reason = ""
    accuracy = _accuracy(task)
    packet_bytes = json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    image_hashes = [hashlib.sha256(data).hexdigest() for data in (image_bytes or [])]
    input_fingerprint = autonomous_tasks.fingerprint({"packet": packet, "images": image_hashes})
    parent = _task_dir(root, task, target)
    current = _current_task(root, task, target)
    not_needed = isinstance(blocked_reason, NotNeeded)
    # The current record is kept for the same question only when both or neither are "not needed": a task now known
    # to be not needed replaces an older blocked one, and a withdrawn (not needed) question is asked again when needed.
    reusable = bool(current) and (current.get("status") == "not_needed") == not_needed
    if reusable and (current.get("input_fingerprint") == input_fingerprint or (
            not packet.get("supervisor_note") and current.get("base_input_fingerprint") == input_fingerprint)):
        return current
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = parent / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    packet["prompt_fingerprint"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    record = {
        "schema_version": 1, "run_id": run_id, "task": task, "target": target,
        "status": "not_needed" if not_needed else "blocked" if blocked_reason else "waiting_for_reply",
        "created_at": _now(), "input_fingerprint": input_fingerprint,
        "base_input_fingerprint": autonomous_tasks.fingerprint({"packet": {key: value for key, value in packet.items()
            if key not in {"supervisor_note", "prompt_fingerprint"}}, "images": image_hashes}),
        "packet_fingerprint": hashlib.sha256(packet_bytes).hexdigest(),
        "prompt_fingerprint": packet["prompt_fingerprint"], "budget": autonomous_tasks.TASKS[task],
        "packet": packet, "prompt": prompt, "image_hashes": image_hashes,
        "accuracy": accuracy, "quality_label": "AI-determined" if accuracy["auto_apply"] else "AI-determined (below accuracy bar)",
        "reply": "", "reply_hash": "", "reply_attempts": [], "validation": {},
        "applied_value": {"note": str(blocked_reason)} if not_needed else {}, "source": "",
        "retry_count": 0, "block_reason": str(blocked_reason), "cross_check": {},
    }
    for index, content in enumerate(image_bytes or []):
        (run_dir / f"image-{index + 1}.png").write_bytes(content)
    _write(run_dir / "packet.json", packet)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    _write(run_dir / "record.json", record)
    _write(parent / "current.json", {"run_path": f"runs/{run_id}", "run_id": run_id, "input_fingerprint": input_fingerprint})
    return record


def _record_file(root, record):
    parent = _task_dir(root, record["task"], record["target"])
    return parent / "runs" / record["run_id"] / "record.json"


def _public_record(web, project, record):
    row = deepcopy(record)
    parent = _task_dir(_root(project), record["task"], record["target"]) / "runs" / record["run_id"]
    row["images"] = []
    for path in sorted(parent.glob("image-*.png")):
        row["images"].append({"name": path.name, "url": f"/api/autonomous-tasks/image?project_id={quote(project['id'])}&task={quote(record['task'])}&target={quote(record['target'])}&run_id={quote(record['run_id'])}&name={quote(path.name)}"})
    row.pop("image_hashes", None)
    return row


def _all_current(root, web=None, project=None):
    records = []
    from backend import house_windows_service
    dwelling = house_windows_service.is_dwelling(root)
    for task in autonomous_tasks.TASKS:
        if dwelling and task == "P5_roof":
            continue
        for pointer in sorted((root / "ai_tasks" / task).glob("*/current.json")):
            current = _read(pointer, {})
            record = _read(pointer.parent / current.get("run_path", "") / "record.json", None)
            if record:
                records.append(_public_record(web, project, record) if web and project else record)
    return records


def _site_determination(root):
    return _read(root / "ai_task_determinations.json", {})


def get(web, project):
    root = _root(project)
    page_selection=_p0_page_selection(root)
    return {"id": project["id"], "tasks": _all_current(root, web, project), "auto_apply_bar": autonomous_task_scoring.AUTO_APPLY_BAR,
            "determinations": _site_determination(root), "supported_tasks": list(autonomous_tasks.TASKS),
            "out_of_scope_tasks": {}, "skipped_pages": page_selection["skipped"],
            "page_selection_notes": page_selection["notes"]}


def get_labels(project):
    """Small result-only view for the operator workspace; never returns prompts or replies."""
    root = _root(project)
    rows = []
    for record in _all_current(root):
        if record.get("applied_value"):
            rows.append({key: deepcopy(record.get(key)) for key in
                         ("task", "status", "source", "quality_label", "applied_value", "accuracy", "target")})
        elif record.get("task") == "P5_roof" and record.get("status") in {"needs_contractor_answer", "contractor_answered_not_sure"}:
            row = {"task": "P5_roof", "status": record["status"], "target": record.get("target"),
                   "room_label": record.get("packet", {}).get("room", {}).get("room_label") or record.get("target")}
            if record.get("status") == "needs_contractor_answer":
                row["question"] = ROOF_CONTRACTOR_QUESTION
            else:
                row["message"] = "Not sure — roof exposure remains unknown and not assessed."
            rows.append(row)
    return {"id": project["id"], "tasks": rows, "auto_apply_bar": autonomous_task_scoring.AUTO_APPLY_BAR,
            "progress": _task_progress(root)}


REFUSED_ATTEMPTS_BEFORE_GIVING_UP = 2


def _not_answered(record):
    """A question the checker refused twice: settled as not answered, so it no longer holds the job at the drawings
    stage (Happy Cap 2026-11-02: two corner questions stayed "waiting" for ever). The record keeps its status, so the
    Phase 1 supervisor can still try one targeted check, and a changed drawing input asks again (a new run)."""
    return bool(record and record.get("status") == "waiting_for_reply"
                and sum(1 for attempt in record.get("reply_attempts") or []
                        if attempt.get("outcome") in {"rejected", "provider_failed"})
                >= REFUSED_ATTEMPTS_BEFORE_GIVING_UP)


def _task_progress(root):
    """Counts over every current task (not only the ones with results), for the contractor progress screen. Once the
    Phase 1 supervisor has finished, it has had its chance to send what was waiting: what's left is "left_waiting" (offered
    as Check and resume) and doesn't hold the job (Happy Cap 2026-11-06)."""
    records = [record for record in _all_current(root) if record.get("task")]
    supervisor = _read(Path(root) / "phase1_supervisor_job.json", {})
    marker = hashlib.sha256("|".join(sorted(f"{record.get('task')}:{record.get('target')}:{record.get('status')}:{_not_answered(record)}"
                                            for record in records)).encode("utf-8")).hexdigest()[:16]
    waiting = sum(1 for record in records if record.get("status") == "waiting_for_reply" and not _not_answered(record))
    finished = isinstance(supervisor, dict) and supervisor.get("status") == "done"
    return {"total": len(records),
            "waiting": 0 if finished else waiting,
            "left_waiting": waiting if finished else 0,
            "not_answered": sum(1 for record in records if _not_answered(record)),
            "blocked": sum(1 for record in records if record.get("status") == "blocked"),
            "not_needed": sum(1 for record in records if record.get("status") == "not_needed"),
            "marker": marker}


def run_all(web, project):
    root = _root(project)
    with page_analysis_cache.operation(root, code_fingerprint=_page_analysis_code_fingerprint()):
        return _run_all(web, project, root)


PLAN_PAGE_TASKS = {"P0_dimensions", "P0_scale_read", "P0_scale_objects", "P0_wall_styles", "P0_room_names",
                   "P0_tenancy_outline", "P0_room_outlines", "P0_unnamed_space", "P0_outline_corners", "P0_room_sizes",
                   "S1_printed_areas", "P3_boundaries", "P3_surroundings"}


def _settle_unselected_pages(root):
    """Questions about a plan page that is no longer one of the traced plans (the plan choice improved, or the drawings
    changed) aren't needed: they would otherwise wait for ever (house set 15, 2026-11-03: questions on its GFA and
    shadow diagrams after the proposed plans were chosen)."""
    selected = {row["page"] for row in _p0_main_geometry_pages(root)}
    if not selected:
        return
    for record in _all_current(root):
        page = (record.get("packet") or {}).get("page")
        if (record.get("task") in PLAN_PAGE_TASKS and type(page) is int and page not in selected
                and record.get("status") in {"waiting_for_reply", "blocked"}):
            reason = f"Page {page} is no longer one of the plans the rooms are traced on."
            record.update(status="not_needed", block_reason=reason, applied_value={"note": reason}, finished_at=_now())
            _write(_record_file(root, record), record)


NORTH_WAITING_NOTE = "Waiting for a north-arrow check on plan page"


def north_waiting_on_others(record):
    """A north page whose own answer is in, waiting only for the other pages' answers: not to be asked again."""
    return (record.get("task") == "P2_north" and record.get("status") == "waiting_for_reply"
            and str(record.get("block_reason") or "").startswith(NORTH_WAITING_NOTE))


def _finish_north(web, project, root):
    """Work out north from the pages that did answer, once no page is still to answer: the last page may have had no
    arrow, or been refused or failed, and then nothing would ever settle the pages waiting for it."""
    records = [row for row in _all_current(root) if row.get("task") == "P2_north"]
    waiting = [row for row in records if north_waiting_on_others(row)]
    pending = [row for row in records if row.get("status") == "waiting_for_reply"
               and not north_waiting_on_others(row) and not _not_answered(row)]
    if not waiting or pending:
        return
    record = waiting[-1]
    validation = record.get("validation") or {}
    validated = validation["result"] if validation.get("valid") and isinstance(validation.get("result"), dict) else validation
    record = _apply_p2(web, project, root, record, validated)
    record["validation"] = {"valid": True, "result": record.get("validation", {})}
    _update_record(root, record)


def _add_main_plan_linework(root):
    """Linework for the page reading's main plans that don't have it. It is extracted before the AI reads the pages, from a
    shortlist by page text; set 15's first floor plan was sorted as a schedule, so its walls were never read and every
    outline drawn on it was refused (live check 2026-11-06). Needs no AI. A failure leaves the job as it was."""
    from ai import page_inventory, vector_geometry
    readings = {int(number): row for number, row in (_read(root / "page_inventory.json", {}).get("pages") or {}).items()
                if str(number).isdigit() and isinstance(row, dict)}
    vector = _read(root / "vector_geometry.json", {})
    rows = (vector.get("geometry_key_points") or {}).get("pages") if isinstance(vector, dict) else None
    if not isinstance(rows, list):      # a job with no extraction at all has its own path (a scan, or preparation not run)
        return []
    have = {row.get("page") for row in rows if isinstance(row, dict)}
    missing = sorted({page for pages in page_inventory.main_plan_pages(readings).values() for page in pages} - have)
    if not missing:
        return []
    refs = [{"page": page, "title": str(readings[page].get("title") or ""), "plan_role": "main_floor_plan",
             "evidence_bucket": "page_reading_main_plan"} for page in missing]
    try:
        added = vector_geometry.extract_pages(_read(root / "ai_input.json", {}), root / "chatgpt_packet" / "screenshots", refs)
    except Exception as error:      # any PDF library failure: the plan is still traced from its image
        print(f"Couldn't read the linework of main plans {missing}: {error}", flush=True)
        return []
    if added:
        _write(root / "vector_geometry.json", {**vector, "geometry_key_points": {
            **vector["geometry_key_points"], "pages": rows + added}})
    return [row["page"] for row in added]


def _run_all(web, project, root):
    _add_main_plan_linework(root)
    _settle_unselected_pages(root)
    _finish_north(web, project, root)
    _refresh_site_tasks(root)
    for target, packet, prompt, images, *error in _north_packets(root):
        reason = error[0] if error else ""
        if not reason and len(packet.get("crops", [])) > 3:
            reason = "More than three crops were generated for one plan page."
        if not reason and any(max(crop["width_px"], crop["height_px"]) > 1024 for crop in packet.get("crops", [])):
            reason = "A north-arrow crop exceeds 1,024 px."
        _create_run(root, "P2_north", target, packet, prompt, images, reason)
    _refresh_roof_tasks(root)
    for task, target, packet, prompt, images, reason in _p0_initial_packets(root):
        _create_run(root, task, target, packet, prompt, images, reason or
                    ("Prompt exceeds the 1,500-character limit; evidence was not shortened."
                     if task == "P0_dimensions" and len(prompt) > 1500 else
                     "Prompt exceeds the 3,000-character limit; evidence was not shortened."
                     if task == "P0_wall_styles" and len(prompt) > 3000 else ""))
    for packet_builder in (_p0_followup_packets, _p0_tenancy_packets, _p0_outline_packets, _p0_unnamed_space_packets, _p0_corner_packets, _s1_packets, _p0_size_packets, _p3_packets, _p3_surroundings_packets, _p7_packets, _p4_packets, _p6_kitchen_packets):
        for task, target, packet, prompt, images, reason in packet_builder(root):
            budget = autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root, task, target, packet, prompt, images,
                        reason or (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened."
                                   if len(prompt) > budget else ""))
    # A house's windows come from its window schedule and the tags on its plans, without AI (house_windows_service).
    from backend import house_windows_service
    house_windows_service.apply(web, project, root)
    return get(web, project)


def _export_determinations(root):
    result = {}
    # A question that wasn't needed holds a note, not an answer.
    answered = [record for record in _all_current(root) if record.get("status") != "not_needed"]
    p1 = _latest_site(root)
    if p1:
        result["P1_site"] = {"site_text": p1.get("site_text", ""),
                             "source": "fallback" if p1.get("source") == "ai_fallback" else "ai_determined"}
    north = {}
    for record in answered:
        if record.get("task") == "P2_north" and record.get("applied_value"):
            value = record["applied_value"]
            north[value["page"]] = {"page": value["page"], "plan_up_azimuth_deg": value["plan_up_azimuth_deg"],
                                     "source": "fallback" if record.get("source") == "ai_fallback" else "ai_determined"}
    if north:
        result["P2_north"] = [north[key] for key in sorted(north)]
    roof = {}
    for record in answered:
        if record.get("task") == "P5_roof" and record.get("applied_value"):
            value = record["applied_value"]
            roof[value["room"]] = {"room": value["room"], "roof": value["roof"],
                                    "source": {"ai_fallback": "fallback", "reviewer": "reviewer"}.get(record.get("source"), "ai_determined")}
    if roof:
        result["P5_roof"] = [roof[key] for key in sorted(roof)]
    kitchen_record = next((record for record in answered
                           if record.get("task") == "P6_kitchen" and record.get("applied_value")), None)
    if kitchen_record:
        source = "stand_in" if kitchen_record.get("stand_in") else "ai_determined"
        result["P6_kitchen"] = kitchen_equipment.to_determinations(
            kitchen_record["applied_value"].get("items", []), source=source)
    rooms = {}
    for task in ("S1_printed_areas", "P0_room_names", "P0_room_outlines", "P0_unnamed_space", "P0_outline_corners", "P0_room_sizes"):
        for record in (row for row in _all_current(root) if row.get("task") == task and row.get("applied_value")):
            for row in record["applied_value"].get("rooms", []):
                key = str(row.get("label", "")).casefold()
                if key and key not in rooms:
                    rooms[key] = {key_: deepcopy(row.get(key_)) for key_ in ("label", "area_m2", "source", "method")}
    if rooms:
        result["P0_rooms"] = [rooms[key] for key in sorted(rooms)]
    boundary_rows, opening_rows = {}, {}
    for record_index, record in enumerate(_all_current(root)):
        value = record.get("applied_value", {})
        if record.get("task") == "P3_boundaries" and value:
            # Page packets contain one room entry per trace; retain legacy single-trace packets.
            for part_index, room in enumerate(value.get("rooms", [value])):
                trace_id = room.get("trace_id") or record.get("packet", {}).get("trace_id")
                identity = trace_id or ("legacy", record.get("target", record_index), part_index)
                for edge_index, edge in enumerate(room.get("edges", [])):
                    boundary_rows[(identity, edge.get("index", edge_index))] = {
                        "room": room.get("room"), "edge_length_m": edge.get("edge_length_m"),
                        "boundary": edge.get("boundary"),
                        "source": edge.get("source", record.get("source", "ai_determined"))}
        if record.get("task") == "P4_openings" and value:
            opening_rows[record["target"]] = {"page":value.get("page"),"room":value.get("room"),
                "total_width_mm":value.get("total_width_mm"),"glazed_panels":deepcopy(value.get("glazed_panels", []))}
    if boundary_rows: result["P3_boundaries"] = list(boundary_rows.values())
    if opening_rows: result["P4_openings"] = list(opening_rows.values())
    if any(record.get("stand_in") is True for record in _all_current(root)):
        result["stand_in"] = True
    _write(root / "ai_tasks" / "determinations.json", result)
    _write(root / "ai_task_determinations.json", result)
    return result


def _update_record(root, record):
    record["updated_at"] = _now()
    _write(_record_file(root, record), record)
    return record


def _operator_seconds(value):
    """Time the operator spent on this check (from showing the task to pasting the reply), for timing the AI step."""
    if type(value) not in (int, float) or not 0 <= value <= 24 * 3600:
        return None
    return round(float(value), 1)


def _archive_reply(root, record, reply, model_note, operator_seconds=None):
    run_dir = _record_file(root, record).parent
    attempts_dir = run_dir / "reply_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    attempt_id = f"{len(list(attempts_dir.glob('*.json'))) + 1:03d}_{uuid.uuid4().hex[:8]}"
    path = attempts_dir / f"{attempt_id}.json"
    reply_hash = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    entry = {"attempt_id": attempt_id, "created_at": _now(), "reply_hash": reply_hash,
             "model_note": model_note, "outcome": "validating", "raw_reply": reply}
    if operator_seconds is not None:
        entry["operator_seconds"] = operator_seconds
    _write(path, entry)
    record.setdefault("reply_attempts", []).append({key: entry[key] for key in
                                                    ("attempt_id", "created_at", "reply_hash", "model_note", "outcome",
                                                     "operator_seconds") if key in entry})
    return path


def _apply_p1(root, project, record, validated):
    site = deepcopy(validated.get("site"))
    cross_check = autonomous_tasks.site_cross_check(record["packet"], validated)
    source = "ai_determined"
    if cross_check.get("status") == "disagrees":
        if record.get("retry_count", 0) < 1:
            candidate = cross_check.get("rule_based_candidate", {})
            record["retry_count"] = record.get("retry_count", 0) + 1
            record["cross_check"] = cross_check
            record["status"] = "waiting_for_reply"
            record["block_reason"] = f"The reply conflicts with the rule-based site clue on page {candidate.get('page')}: {candidate.get('text')}. Retry once using the same excerpts."
            record["prompt"] = record["prompt"].split("\n\nCross-check conflict:")[0] + "\n\nCross-check conflict: the rule-based candidate is " + json.dumps(candidate, ensure_ascii=False) + ". Re-check the supplied excerpts and return a corrected JSON answer."
            record["prompt_fingerprint"] = hashlib.sha256(record["prompt"].encode("utf-8")).hexdigest()
            record["packet"]["prompt_fingerprint"] = record["prompt_fingerprint"]
            return record
        candidate = record["packet"].get("rule_based_top_candidate")
        if candidate:
            site = {"text": candidate["text"], "page": candidate["page"], "kind": candidate["kind"]}
            source = "ai_fallback"
            record["fallback"] = {"reason": "AI and rule-based site identification disagreed after one retry.",
                                  "selected_candidate": deepcopy(site), "basis": "rule_based_top_candidate"}
    if not site:
        record.update({"status": "blocked", "block_reason": "The reply found no site excerpt to apply, and no rule-based candidate was available.", "validation": validated, "cross_check": cross_check})
        return record
    # The location service's cited-coordinate path requires coordinates; this
    # task records textual site identity separately and never fabricates them.
    determination = {"site_text": site["text"], "site_kind": site["kind"], "page": site["page"],
                     "source": source, "label": ("Assumed (rule-based site fallback)" +
                         ("; task below accuracy bar" if not record["accuracy"].get("auto_apply") else ""))
                         if source == "ai_fallback" else record["quality_label"], "ai_run_id": record["run_id"],
                     "reply_hash": record["reply_hash"], "input_fingerprint": record["input_fingerprint"],
                     "evidence": next(row["text"] for row in record["packet"]["excerpts"] if row["page"] == site["page"] and site["text"] in row["text"]),
                     "consultant_addresses": deepcopy(validated["consultant_addresses"])}
    cross_check = autonomous_tasks.site_cross_check(record["packet"], validated)
    determination["cross_check"] = cross_check
    reviewer_site = _read(root / "site_location_resolution.json", {}).get("confirmed_address", "")
    if reviewer_site:
        determination["reviewer_override"] = reviewer_site
        determination["applied_site_text"] = reviewer_site
        determination["applied_source"] = "reviewer"
    else:
        determination["applied_site_text"] = site["text"]
        determination["applied_source"] = source
    _write(root / "site_location_ai_determination.json", determination)
    record.update({"status": _status_for_accuracy(source, record["accuracy"]), "source": source,
                   "applied_value": determination, "validation": validated, "cross_check": cross_check, "block_reason": ""})
    return record


def _apply_p2(web, project, root, record, validated, *, defer=False):
    page = record["packet"]["page"]
    north_artifact = _read(root / "reviewer_room_geometry.json", {})
    existing_declaration = north_artifact.get("page_north", {}).get(str(page), {})
    existing_source = existing_declaration.get("declaration_source", "reviewer" if existing_declaration else "")
    if existing_source == "reviewer":
        record.update({"status": "applied", "source": "reviewer", "applied_value": {"page": page, "plan_up_azimuth_deg": existing_declaration.get("plan_up_azimuth_deg"), "overrode_by": "reviewer"}, "validation": validated})
        return record
    declination = _site_declination(root) if validated.get("label") == "magnetic" else None
    own_bearing, own_basis = autonomous_tasks.true_north(validated, declination)
    if own_bearing is None:
        record.update({"status": _status_for_accuracy("ai_determined", record.get("accuracy", {})),
                       "source": "ai_determined",
                       "applied_value": {"page": page, "plan_up_azimuth_deg": None,
                                         **({"north_basis": own_basis} if validated.get("found") else {})},
                       "block_reason": "", "validation": validated})
        return record
    candidates_by_page = {}
    completed_pages = set()
    for other_page, declaration in north_artifact.get("page_north", {}).items():
        declaration_source = declaration.get("declaration_source", "reviewer")
        if declaration_source == "reviewer":
            candidates_by_page[int(other_page)] = float(declaration["plan_up_azimuth_deg"])
            completed_pages.add(int(other_page))
    other_records = {}
    for pointer_path in (root / "ai_tasks" / "P2_north").glob("*/current.json"):
        current = _read(pointer_path, {})
        other = _read(pointer_path.parent / current.get("run_path", "") / "record.json", None)
        if not other or other.get("target") == record.get("target"):
            continue
        other_validation = other.get("validation", {})
        if other_validation.get("valid") and isinstance(other_validation.get("result"), dict):
            other_validation = other_validation["result"]
        if other_validation.get("found") is not None or other.get("status") in {"blocked", "not_needed"} or _not_answered(other):
            completed_pages.add(int(other.get("packet", {}).get("page", 0)))     # a page that can't answer doesn't hold the rest
        other_bearing, _other_basis = autonomous_tasks.true_north(
            other_validation, _site_declination(root) if other_validation.get("label") == "magnetic" else None)
        if other_bearing is not None:          # project-north arrows don't take part in the agreement
            other_page = int(other.get("packet", {}).get("page", 0))
            candidates_by_page[other_page] = other_bearing
            other_records[other_page] = other
    candidates_by_page[page] = own_bearing
    completed_pages.add(page)
    # Use the same complete plan-view list as the north packet builder and declare_north.
    plan_page_numbers = [row["page"] for row in _north_pages(root)]   # the pages asked, as the packet builder
    missing_pages = [number for number in plan_page_numbers if number not in completed_pages]
    if missing_pages:
        record.update({"status": "waiting_for_reply", "block_reason": "Waiting for a north-arrow check on plan page(s) " + ", ".join(map(str, missing_pages)) + ". All available pages must be checked before cross-page agreement is applied.", "validation": validated})
        return record
    candidates = sorted(candidates_by_page.items())
    consensus = autonomous_tasks.north_consensus(candidates)
    if consensus["status"] == "disagreement":
        if record.get("retry_count", 0) < 1:
            record["retry_count"] = record.get("retry_count", 0) + 1
            record["status"] = "waiting_for_reply"
            record["block_reason"] = "Plan pages disagree by more than 5°. Retry this page once; after that Archie will apply a strict-majority direction, else treat north as unknown."
            record["validation"] = validated
            record["cross_check"] = consensus
            return record
        # No majority after the retry: north is unknown, so the sun on each outside wall and window is averaged over
        # the four directions it could face (user decision 2026-11-03), rather than leaving the drawings stage blocked.
        record.update({"status": _status_for_accuracy("ai_determined", record.get("accuracy", {})), "source": "ai_determined",
                       "applied_value": {"page": page, "plan_up_azimuth_deg": None, "north_basis": "no_majority",
                                         "note": "The plan pages disagree on north; the sun is averaged over the four directions."},
                       "block_reason": "", "validation": validated, "cross_check": consensus})
        return record
    applied_angle = consensus["winner"]
    declaration_source = "ai_determined"
    reviewer = "Archie AI"
    task_records = {**other_records, page: record}
    primary_apply_failed = False
    for north_page in sorted(set(plan_page_numbers) | set(task_records)):   # every page asked or already answered
        declaration = _read(root / "reviewer_room_geometry.json", {}).get("page_north", {}).get(str(north_page), {})
        if declaration.get("declaration_source", "reviewer" if declaration else "") == "reviewer":
            continue
        north_record = task_records.get(north_page)
        if not north_record:
            continue
        try:
            page_answer = _north_answer(north_record) if north_record is not record else validated
            page_basis = autonomous_tasks.true_north(
                page_answer, _site_declination(root) if page_answer.get("label") == "magnetic" else None)[1]
            reviewer_room_geometry_service.post(web, project, {
                "action": "declare_north", "page": north_page, "reviewer": reviewer,
                "plan_up_azimuth_deg": applied_angle, "declaration_source": declaration_source,
                "ai_run_id": north_record["run_id"], "north_basis": page_basis,
            }, _defer_evidence_rebuild=True)
        except (ValueError, OSError, KeyError) as error:
            north_record.update({"status": "blocked", "source": "", "applied_value": {},
                                 "block_reason": f"North agreement succeeded but page {north_page} could not be applied: {error}",
                                 "cross_check": consensus})
            if north_record is record:
                primary_apply_failed = True
            else:
                _update_record(root, north_record)
            continue
        north_record.update({"status": _status_for_accuracy("ai_determined", north_record["accuracy"]),
                             "source": "ai_determined", "applied_value": {"page": north_page,
                             "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                             "evidence": (north_record.get("validation", {}).get("result", {}) or north_record.get("validation", {})).get("description", "North arrow in source crop"),
                             "ai_run_id": north_record["run_id"]}, "cross_check": consensus, "block_reason": ""})
        if north_record is not record:
            _update_record(root, north_record)
    if primary_apply_failed:
        record.update({"status": "blocked", "source": "", "applied_value": {},
                       "validation": validated, "cross_check": consensus,
                       "block_reason": f"North agreement succeeded, but page {page} could not be applied."})
        return record
    # A traced plan without its own north arrow takes the agreed north of the other sheets in the set (one set's
    # sheets share their orientation), so its rooms' walls still get compass directions. Until 2026-10-24 such a page
    # got none (Casa Nova's setout plan).
    try:
        main_pages = [row["page"] for row in _p0_main_geometry_pages(root)]
    except (OSError, ValueError, KeyError, TypeError):
        main_pages = []
    declared = _read(root / "reviewer_room_geometry.json", {}).get("page_north", {})
    for main_page in main_pages:
        if str(main_page) in declared or main_page in task_records:   # given north already (now or before)
            continue
        try:
            reviewer_room_geometry_service.post(web, project, {
                "action": "declare_north", "page": main_page, "reviewer": f"{reviewer} (north of page {page}; no arrow on this sheet)",
                "plan_up_azimuth_deg": applied_angle, "declaration_source": declaration_source,
                "ai_run_id": record["run_id"], "north_basis": own_basis,
            }, _defer_evidence_rebuild=True)
            task_records.setdefault(main_page, record)
        except (ValueError, OSError, KeyError):
            pass
    if task_records and web is not None and not defer:
        calculation_extraction_service.post(web, project, {"action": "build"})
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"page": page, "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                                     "evidence": validated.get("description", "North arrow in source crop"), "ai_run_id": record["run_id"]},
                   "validation": validated, "cross_check": consensus, "block_reason": ""})
    return record


def _apply_p5(web, project, root, record, validated, *, defer=False):
    room_id = record["target"]
    paths = reviewer_room_geometry_service._paths(project)
    artifact = _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    traces = [row for row in artifact.get("records", []) if row.get("room_id") == room_id]
    if not traces:
        record.update({"status": "blocked", "block_reason": "The current traced room no longer exists.", "validation": validated})
        return record
    selected = validated["roof"]
    source = "ai_determined"
    evidence = validated.get("evidence", "")
    if selected == "unknown":
        # The drawings don't settle it: the most likely case, labelled, instead of a question (user decision 2026-10-15).
        selected, evidence = autonomous_tasks.roof_fallback(record.get("packet", {}))
        source = "ai_fallback"
    applied=[]; reviewer_kept=[]
    for trace in traces:
        existing_roof_source = trace.get("roof_source") or (
            "reviewer" if trace.get("envelope_reviewer") and trace.get("roof") != "unknown"
            else trace.get("declaration_source", "")
        )
        if existing_roof_source == "reviewer":
            reviewer_kept.append(trace)
            continue
        reviewer_room_geometry_service.post(web, project, {
            "action": "classify_envelope", "trace_id": trace["trace_id"], "reviewer": "Archie AI (P5)",
            "edges": deepcopy(trace.get("edges", [])), "roof": selected, "openings": deepcopy(trace.get("openings", [])),
            "declaration_source": source, "ai_run_id": record["run_id"],
        }, _defer_evidence_rebuild=True)
        applied.append(trace)
    representative=reviewer_kept[0] if reviewer_kept and not applied else traces[0]
    if not applied and reviewer_kept:
        record.update({"status":"applied","source":"reviewer","applied_value":{"room":representative.get("room_label"),
            "roof":representative.get("roof"),"overrode_by":"reviewer","part_count":len(traces)},"validation":validated})
        return record
    record.update({"status": "applied_fallback" if source == "ai_fallback" else _status_for_accuracy(source, record["accuracy"]),
                   "source": source,
                   "applied_value": {"room": representative.get("room_label", room_id), "roof": selected,
                                     "part_count":len(traces),
                                     "source": source, "ai_run_id": record["run_id"], "evidence": evidence,
                                     "label": "Assumed (most likely case)" if source == "ai_fallback" else record["quality_label"]},
                   "validation": validated, "block_reason": ""})
    if applied and web is not None and project and not defer:
        calculation_extraction_service.post(web, project, {"action": "build"})   # in a batch, once per round instead
    return record


def _apply_p6(record, validated):
    """Record identified equipment only; P6 deliberately makes no heat estimate."""
    items = deepcopy(validated)
    record.update({"status": _status_for_accuracy("ai_determined", record.get("accuracy", {})),
                   "source": "ai_determined",
                   "applied_value": {
                       "label": "Kitchen equipment identified from drawings (heat not yet assessed)",
                       "heat_assessed": False, "items": items,
                       "room": record.get("packet", {}).get("room_label", "Kitchen"),
                   },
                   "validation": {"valid": True, "items": items}, "block_reason": ""})
    return record


def _answer_roof(web, project, root, data, *, defer_evidence_rebuild=False):
    if data.get("task") != "P5_roof":
        raise ValueError("This answer action is only supported for roof exposure.")
    target = str(data.get("target", ""))
    record = _current_task(root, "P5_roof", target)
    if not record or record.get("status") != "needs_contractor_answer":
        raise ValueError("This roof question is no longer waiting for your answer.")
    answer = str(data.get("answer", ""))
    answer_roof = {"floor_tenancy_above": "not_exposed", "roof_directly_above": "exposed", "not_sure": "unknown"}
    if answer not in answer_roof:
        raise ValueError("Choose floor/tenancy above, roof directly above, or not sure.")
    paths = reviewer_room_geometry_service._paths(project)
    artifact = _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    traces = [row for row in artifact.get("records", []) if row.get("room_id") == target]
    if not traces:
        raise ValueError("The current room trace was not found; refresh the project and try again.")
    for trace in traces:
        reviewer_room_geometry_service.post(web, project, {
            "action": "classify_envelope", "trace_id": trace["trace_id"],
            "reviewer": "Answered by us", "edges": deepcopy(trace.get("edges", [])),
            "roof": answer_roof[answer], "openings": deepcopy(trace.get("openings", [])),
            "confirm_roof": answer != "not_sure",
        }, _defer_evidence_rebuild=True)
    if answer == "not_sure":
        record.update({"status": "contractor_answered_not_sure", "source": "reviewer",
                       "applied_value": {}, "contractor_answer": answer,
                       "contractor_answered_by": "Answered by us",
                       "block_reason": "Roof exposure remains unknown and not assessed."})
    else:
        record.update({"status": "applied", "source": "reviewer",
                       "applied_value": {"room": traces[0].get("room_label", target), "roof": answer_roof[answer],
                                         "source": "reviewer", "label": "Answered by us","part_count":len(traces)},
                       "contractor_answer": answer, "contractor_answered_by": "Answered by us",
                       "block_reason": ""})
    _update_record(root, record)
    _export_determinations(root)
    if answer != "not_sure" and not defer_evidence_rebuild:
        from backend import calculation_extraction_service
        calculation_extraction_service.post(web, project, {"action": "build"})
    return get(web, project)


def post(web, project, data, *, defer_evidence_rebuild=False):
    with page_analysis_cache.operation(_root(project), code_fingerprint=_page_analysis_code_fingerprint()):
        return _post(web, project, data, defer_evidence_rebuild=defer_evidence_rebuild)


def _post(web, project, data, *, defer_evidence_rebuild=False):
    if defer_evidence_rebuild and not data.get("defer"):
        data = {**data, "defer": True}
    root = _root(project)
    action = data.get("action", "")
    if action in {"run_all", "build"}:
        return run_all(web, project)
    if action == "answer_roof":
        return _answer_roof(web, project, root, data, defer_evidence_rebuild=defer_evidence_rebuild)
    task, target = str(data.get("task", "")), str(data.get("target", ""))
    if task not in autonomous_tasks.TASKS or not target:
        raise ValueError("Choose a supported task and target.")
    record = _current_task(root, task, target)
    if not record:
        raise ValueError("Build the task packet before validating a reply.")
    if record.get("status") == "blocked" and record.get("block_reason") and not data.get("reply"):
        return {"applied": False, "status": record.get("status")} if defer else get(web, project)
    if action != "validate_apply":
        raise ValueError("Action must be run_all or validate_apply.")
    reply = str(data.get("reply", ""))
    defer = bool(data.get("defer"))
    if len(reply.encode("utf-8")) > 64_000:
        raise ValueError("Reply exceeds the 64 KB task limit.")
    model_note = str(data.get("model_note", ""))[:160]
    reply_stand_in = False
    try:
        reply_stand_in = autonomous_tasks.parse_json_reply(reply).get("stand_in") is True
    except ValueError:
        # Let the task-specific validator report malformed replies below.
        pass
    record["stand_in"] = data.get("stand_in") is True or reply_stand_in
    reply_path = _archive_reply(root, record, reply, model_note, _operator_seconds(data.get("operator_seconds")))
    record["reply"] = reply
    record["reply_hash"] = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    record["model_note"] = model_note
    try:
        if task == "P0_dimensions":
            validated = autonomous_tasks.validate_dimension_reply(record["packet"], reply)
            record = _apply_p0_dimensions(root, record, validated)
        elif task == "P0_scale_read":
            validated = autonomous_tasks.validate_scale_read_reply(record["packet"], reply)
            record = _apply_p0_scale_read(root, record, validated)
        elif task == "P0_scale_objects":
            validated = autonomous_tasks.validate_scale_objects_reply(record["packet"], reply)
            record = _apply_p0_scale_objects(root, record, validated)
        elif task == "P0_wall_styles":
            validated = autonomous_tasks.validate_wall_style_batch_reply(record["packet"], reply)
            record = _apply_p0_wall_styles(record, validated)
        elif task == "P0_room_names":
            validated = autonomous_tasks.validate_p0_room_names(record["packet"], reply)
            record = _apply_p0_room_names(web, project, root, record, validated, defer=defer)
        elif task == "P0_room_outlines":
            validated = autonomous_tasks.validate_p0_outlines(record["packet"], reply)
            omitted, not_drawn = room_outline.outline_omissions(autonomous_tasks.parse_json_reply(reply),
                                                                record["packet"]["room_names"], validated)
            if omitted and len(record.get("reply_attempts") or []) < OUTLINE_ATTEMPTS_BEFORE_PARTIAL:
                raise ValueError(f"The reply leaves out {', '.join(omitted)}. Every room in the list is on this plan "
                                 "(the other images show where each is named): draw each one, or put it under "
                                 "not_drawn with the reason.")
            record = (_apply_p0_outlines(web, project, root, record, validated, defer=True) if defer
                      else _apply_p0_outlines(web, project, root, record, validated))
            record["applied_value"]["not_drawn"] = ([{"room": name, "reason": "The AI left it out."} for name in omitted]
                                                    + [{"room": name, "reason": reason} for name, reason in not_drawn.items()])
        elif task == "P0_outline_corners":
            validated = autonomous_tasks.validate_p0_outline_corners(record["packet"], reply)
            record = _apply_p0_outline_corners(web, project, root, record, validated, defer=defer)
        elif task == "P0_tenancy_outline":
            validated = autonomous_tasks.validate_p0_tenancy_outline(record["packet"], reply)
            record = _apply_p0_tenancy_outline(root, record, validated)
        elif task == "P0_unnamed_space":
            validated = autonomous_tasks.validate_p0_unnamed_space(record["packet"], reply)
            record = (_apply_p0_unnamed_space(web, project, root, record, validated, defer=True) if defer
                      else _apply_p0_unnamed_space(web, project, root, record, validated))
        elif task == "P0_room_sizes":
            validated = autonomous_tasks.validate_room_size_reply(record["packet"], reply)
            record = _apply_p0_sizes(web, project, root, record, validated)
        elif task == "S1_printed_areas":
            record = _apply_s1_areas(web, project, root, record, reply)
        elif task == "S3_title_transcription":
            lines = __import__("ai.scan_reading", fromlist=["validate_transcription"]).validate_transcription(reply)
            record = _apply_s3_transcription(record, lines)
        elif task == "P3_boundaries":
            validated = autonomous_tasks.validate_boundary_reply(record["packet"], reply)
            record = _apply_p3(web, project, root, record, validated, defer=True) if defer else _apply_p3(web, project, root, record, validated)
        elif task == "P3_surroundings":
            validated = autonomous_tasks.validate_surroundings_reply(record["packet"], reply)
            record = _apply_p3_surroundings(record, validated)
        elif task == "P7_ceiling_heights":
            validated = autonomous_tasks.validate_ceiling_height_reply(record["packet"], reply)
            record = _apply_p7(record, validated)
        elif task == "P4_storefront_wall":
            validated = autonomous_tasks.validate_storefront_wall_reply(record["packet"], reply)
            record = _apply_p4_wall(web, project, root, record, validated, defer=True) if defer else _apply_p4_wall(web, project, root, record, validated)
        elif task == "P4_openings":
            validated = autonomous_tasks.validate_opening_reply(record["packet"], reply)
            record = _apply_p4(web, project, root, record, validated, defer=True) if defer else _apply_p4(web, project, root, record, validated)
        elif task == "P1_site":
            validated = autonomous_tasks.validate_site_reply(record["packet"], reply)
            record = _apply_p1(root, project, record, validated)
        elif task == "P2_north":
            validated = autonomous_tasks.validate_north_reply(record["packet"], reply)
            record = _apply_p2(web, project, root, record, validated, defer=True) if defer else _apply_p2(web, project, root, record, validated)
        elif task == "P6_kitchen":
            validated = kitchen_equipment.validate_reply(json.loads(reply), record["packet"])
            record = _apply_p6(record, validated)
        else:
            validated = autonomous_tasks.validate_roof_reply(record["packet"], reply)
            record = _apply_p5(web, project, root, record, validated, defer=True) if defer else _apply_p5(web, project, root, record, validated)
    except (ValueError, KeyError, TypeError) as error:
        if task in {"P0_dimensions", "P0_scale_read", "P0_scale_objects"}:
            # The next way of finding the scale takes over (another dimension line, the printed scale, standard
            # objects, then an assumed 1:100): this reading is no longer needed and doesn't hold the job.
            record.update({"status": "not_needed", "block_reason": f"Not usable ({error}); the next way of finding the scale is used.",
                           "validation": {"valid": False, "error": str(error)}, "applied_value": {"fallback_required": True}})
            archived = _read(reply_path, {})
            archived.update({"outcome": "rejected_fallback", "reason": str(error)})
            _write(reply_path, archived)
            record["reply_attempts"][-1].update({"outcome": "rejected_fallback", "reason": str(error)})
            _update_record(root, record)
            _refresh_geometry_tasks(root)
            return get(web, project)
        record["status"] = "waiting_for_reply"
        record["block_reason"] = str(error)
        record["validation"] = {"valid": False, "error": str(error)}
        archived = _read(reply_path, {})
        archived.update({"outcome": "rejected", "reason": str(error)})
        _write(reply_path, archived)
        record["reply_attempts"][-1].update({"outcome": "rejected", "reason": str(error)})
        _update_record(root, record)
        return get(web, project)
    archived = _read(reply_path, {})
    archived["outcome"] = record.get("status", "accepted")
    archived["reason"] = record.get("block_reason", "")
    _write(reply_path, archived)
    record["reply_attempts"][-1].update({"outcome": archived["outcome"], "reason": archived["reason"]})
    record["validation"] = {"valid": True, "result": record.get("validation", {})}
    _update_record(root, record)
    if task == "S1_printed_areas" and record.get("applied_value") and web is not None and not defer:
        from backend import room_use_resolution_service
        room_use_resolution_service.post(web, project, {"action": "resolve"})
    if not defer:
        _export_determinations(root)
        productization.record_change_if_fingerprint_changed(
            root, action="autonomous_task_applied", target=f"{task}/{target}",
            previous_fingerprint="", new_fingerprint=record["input_fingerprint"], affected_ids=[record["run_id"]])
    # A completed stage may unlock the next bounded packet. This only creates
    # local task records; the ChatGPT transport remains manual.
    if not defer:
        _refresh_geometry_tasks(root)
        return get(web, project)
    return {"applied": True, "status": record.get("status")}


def image(web, project, data):
    task, target, run_id, name = (str(data.get(key, "")) for key in ("task", "target", "run_id", "name"))
    if task not in autonomous_tasks.TASKS or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", run_id) or not re.fullmatch(r"image-\d+\.png", name):
        raise ValueError("Autonomous task image request is invalid.")
    path = _task_dir(_root(project), task, target) / "runs" / run_id / name
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(_root(project).resolve()):
        raise ValueError("Task image is unavailable.")
    return {"path": str(resolved), "content_type": "image/png"}


def prepare_investigation(project, investigation):
    """Build a focused drawing-task run using its usual evidence packet and validation contract."""
    from backend.job_runner import checkpoint
    checkpoint()
    root = _root(project)
    task = investigation["task"]
    names = investigation.get("room_names") or ([investigation["room"]] if investigation.get("room") else None)
    builders = {
        "P0_room_outlines": lambda: _p0_outline_packets(root, requested_rooms=names),
        "P0_room_sizes": lambda: _p0_size_packets(root, requested_rooms=names),
        "P0_unnamed_space": lambda: _p0_unnamed_space_packets(root, force=True),
        "P0_tenancy_outline": lambda: _p0_tenancy_packets(root, force=True),
        "P0_outline_corners": lambda: _p0_corner_packets(root, force=True),
        "P3_boundaries": lambda: _p3_packets(root, page_number=investigation.get("plan_page"), force=True),
        "P3_surroundings": lambda: _p3_surroundings_packets(root),
        "P4_storefront_wall": lambda: _p4_packets(root),
        "P4_openings": lambda: _p4_packets(root, only_room_id=investigation.get("room_id") or None),
        "P5_roof": lambda: [("P5_roof", target, packet, prompt, images, "")
                              for target, packet, prompt, images in _roof_packets(root, investigation.get("room_id") or None)],
        "P7_ceiling_heights": lambda: _p7_packets(root, requested_rooms=names),
    }
    if task not in builders:
        raise ValueError("This investigation has no supported drawing task.")
    candidates = builders[task]()
    candidates = [row for row in candidates if row[0] == task]
    target = investigation.get("target")
    if target:
        candidates = [row for row in candidates if row[1] == target]
    pages = set(investigation.get("pages") or [])
    if pages and not target:
        on_page = [row for row in candidates if row[2].get("page") in pages]
        candidates = on_page or candidates
    if not candidates:
        raise ValueError("The current drawings cannot yet provide a packet for this check; its prerequisite remains unresolved.")
    # One packet per targeted attempt. Prefer a new view after a valid answer
    # failed to resolve the same gap; rejected answers get their correction retry.
    tried_targets = set(investigation.get("previous_targets", []))
    untried = [row for row in candidates if row[1] not in tried_targets]
    candidates = (untried or candidates)[:1]
    prepared = []
    for task, target, packet, prompt, images, reason in candidates:
        packet = deepcopy(packet)
        # Stable text is part of both the prompt and input fingerprint. The same gap
        # reuses its cached reply on resume; a different question cannot reuse the baseline.
        note = investigation["note"]
        packet["supervisor_note"] = note
        prompt += ("\n\nWhy this is asked again (what to look at, not what the drawings show; answer from the drawings): "
                   + note)
        prepared.append(_create_run(root, task, target, packet, prompt, images, reason))
    return prepared
