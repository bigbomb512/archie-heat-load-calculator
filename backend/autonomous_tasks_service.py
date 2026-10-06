"""Project-local Card P packet, manual transport and apply service."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
import uuid

from ai import autonomous_task_scoring, autonomous_tasks, reviewer_room_geometry, room_outline, kitchen_equipment
from backend import productization, reviewer_room_geometry_service, site_location_service, calculation_extraction_service, page_analysis_cache
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


def _north_packets(root):
    ai_input, _spatial, _building = _load_inputs(root)
    rows = []
    for page in autonomous_tasks.plan_pages(ai_input):
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
            rows.append((target, {"task": "P2_north", "page": number, "crops": []}, "", [], str(error)))
    return rows


def _p0_page_selection(root):
    """Select one main geometry sheet per level and explain every omitted plan page."""
    ai_input, spatial, _building = _load_inputs(root)
    coverage = _read(root / "drawing_coverage.json", {})
    vector = _read(root / "vector_geometry.json", {})
    coverage_by_page = {row.get("page"): row for row in coverage.get("page_roles", []) if isinstance(row, dict)}
    vector_by_page = {row.get("page"): row for row in (vector.get("geometry_key_points") or {}).get("pages", [])
                      if isinstance(row, dict)}
    spatial_by_page = {row.get("page"): row for row in spatial.get("pages", []) if isinstance(row, dict)}
    grouped = {}
    skipped = []
    notes = []
    for page in (ai_input.get("drawing_set", {}) or {}).get("pages", []):
        if not isinstance(page, dict) or type(page.get("page")) is not int:
            continue
        number = page["page"]
        role_row = coverage_by_page.get(number, {})
        role = str(role_row.get("proposed_role") or page.get("plan_role") or "").casefold()
        kind = str(page.get("type") or page.get("sheet_classification") or "").casefold()
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
        vector_dims = vector_by_page.get(number, {}).get("dimension_candidates", []) or []
        spatial_dims = spatial_by_page.get(number, {}).get("dimension_candidates", []) or []
        page_dims = page.get("dimension_candidates", []) or []
        dimension_count = max(len(vector_dims), len(spatial_dims), len(page_dims))
        priority = {"main_floor_plan": 0, "primary_geometry_plan": 1, "floor_plan": 2,
                    "supporting_geometry_plan": 3, "uncertain_top_down_context": 4}.get(role, 4)
        grouped.setdefault(level_key, []).append((dimension_count, priority, number, page, role))
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
    return {"selected": sorted(selected, key=lambda row: row["page"]),
            "skipped": sorted(skipped, key=lambda row: row["page"]),
            "notes": sorted(notes, key=lambda row: row["page"])}


def _p0_main_geometry_pages(root):
    return _p0_page_selection(root)["selected"]


def _p0_calibration_ready(root, page, context):
    """Require one dimension with readable scale, otherwise two agreeing dimensions."""
    printed = _s1_calibration(root, page)
    if printed:
        return printed, ""
    dimensions = _p0_dimensions(root, page)
    if not context.get("declared_mm_per_px") and len(dimensions) < 2:
        return None, "The sheet scale is unreadable; two agreeing printed dimensions are required before outlining rooms."
    try:
        return _p0_calibration(root, page, context), ""
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, str(error)


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
                       "block_reason": "Scanned plan with no printed areas or dimensions; room areas need the contractor."})
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


def _p0_walls_areas(root, page_number, context, calibration, wall_style_ids=None, raster_mode=False):
    mm_per_px = context.get("declared_mm_per_px") or calibration["mm_per_px"]
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
    return walls, areas, segments


def _roof_packets(root):
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
        if (trace.get("room_id") in comfort_ids
                and trace.get("calibration", {}).get("status") in {"agreed", "declared_scale_rejected"}):
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


def _refresh_roof_tasks(root):
    for target,packet,prompt,images in _roof_packets(root):
        record=_create_run(root,"P5_roof",target,packet,prompt,images,
            "Prompt exceeds the 2,000-character limit; evidence was not shortened." if len(prompt)>2000 else "")
        if record.get("status")=="waiting_for_reply" and not _has_explicit_roof_evidence(packet):
            record.update({"status":"needs_contractor_answer","block_reason":ROOF_CONTRACTOR_QUESTION})
            _update_record(root,record)


def _p6_kitchen_packets(root):
    """Build a bounded kitchen-equipment packet from current room traces and drawing pages."""
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
                      and row.get("calibration", {}).get("status") in {"agreed", "declared_scale_rejected"}
                      and isinstance(row.get("points_image_px"), list) and len(row["points_image_px"]) >= 4]
    if not kitchen_traces:
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
    return page_analysis_cache.get_context(root, page_number, lambda: _build_p0_context(root, page_number))


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
    declared_scale = page_mm_per_px(page_ocr.get("scale_candidates", []), render_dpi=72 * scale)
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
    with pdfplumber.open(source) as pdf:
        return [index + 1 for index, page in enumerate(pdf.pages) if not page.chars]


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
            lines = room_outline.dimension_line_candidates(context["objects"], None,
                        (0, 0, *context["image"].size), limit=2 if not context["declared_mm_per_px"] else 3)
            for index, line in enumerate(lines, start=1):
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
            calibration, calibration_error = _p0_calibration_ready(root, number, context)
            if not calibration:
                if len(_p0_dimensions(root, number)) >= (1 if context["declared_mm_per_px"] else 2):
                    result.append(("P0_room_names", f"page-{number}",
                        {"task":"P0_room_names", "page":number}, "", [],
                        f"Room outlining is blocked until scale calibration is valid: {calibration_error}"))
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
            if not lines:
                result.append(("P0_dimensions", f"page-{number}-dimension-1",
                               {"task":"P0_dimensions", "page":number, "line":{}}, "", [],
                               "No eligible dimension line with two tick centres was found on this plan page."))
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
    calibration = autonomous_tasks.calibration_from_dimensions(dimensions, context.get("declared_mm_per_px"))
    calibration["dimension_count"] = len(dimensions)
    calibration["dimension_evidence"] = deepcopy(dimensions)
    return calibration


def _inferred_room_data(root):
    from ai import room_inference
    ai_input, spatial, _building = _load_inputs(root)
    inferred = room_inference.infer(ai_input, _read(root / "drawing_coverage.json", {}), spatial,
                                    _read(root / "vector_geometry.json", {}))
    rows = [row for row in inferred.get("rooms", []) if row.get("label")]
    return rows


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
            _walls, areas, segments = _p0_walls_areas(root, number, context, calibration, wall_style_ids, raster_mode)
            if not areas:
                continue
            image, transform = room_outline.render_candidate_areas(context["image"], areas, context["viewport"])
            room_names = sorted({row["label"] for row in _inferred_room_data(root)})
            naming_packet = {"task":"P0_room_names", "page":number, "area_count":len(areas), "room_names":room_names,
                             "transform":transform, "calibration":calibration, "areas_px": [list(poly.exterior.coords) for poly in areas],
                             "wall_style_ids":wall_style_ids, "raster_mode":raster_mode}
            result.append(("P0_room_names", f"page-{number}", naming_packet,
                           room_outline.room_naming_prompt(room_names, len(areas)), [_png_bytes(image)], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_room_names", f"page-{number}", {"task":"P0_room_names", "page":number}, "", [],
                           f"P0 enclosed-area packet is blocked: {error}"))
    # Open-plan outline packets are built after the naming replies identify
    # which rooms already received enclosed or split areas.
    return result


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


def _p0_outline_packets(root):
    result = []
    for page in _p0_main_geometry_pages(root):
        number = page["page"]
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
            _walls, areas, _segments = _p0_walls_areas(root, number, context, calibration,
                                                        wall_style_ids, raster_mode)
            if names_record and areas:
                names, splits = room_outline.validate_naming_reply(json.loads(names_record["reply"]), len(areas),
                    names_record["packet"]["room_names"], names_record["packet"]["transform"])
                placed = {name for name in names.values() if name != room_outline.NOT_A_ROOM}
                placed.update(item["room"] for split in splits for item in split["rooms"])
                room_names = names_record["packet"]["room_names"]
            else:
                placed, splits = set(), []
                room_names = sorted({row["label"] for row in _inferred_room_data(root)})
            printed = {row["label"] for row in _inferred_room_data(root) if row.get("area_m2")}
            missing = sorted(set(room_names) - placed - printed)
            if not missing:
                continue
            image, transform = room_outline.render_candidate_areas(context["image"], [], context["viewport"])
            prompt = room_outline.outline_prompt(missing)
            packet = {"task":"P0_room_outlines", "page":number, "room_names":missing, "transform":transform,
                      "calibration":calibration, "wall_style_ids":wall_style_ids, "raster_mode":raster_mode}
            result.append(("P0_room_outlines", f"page-{number}", packet, prompt, [_png_bytes(image)], ""))
        except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
            result.append(("P0_room_outlines", f"page-{number}", {"task":"P0_room_outlines", "page":number}, "", [],
                           f"Open-plan outline packet is blocked: {error}"))
    return result


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


def _p3_packets(root):
    result=[]
    try: traces=reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir":str(root)}))
    except (OSError,ValueError,TypeError,KeyError): traces=[]
    comfort=_comfort_room_ids(root)
    selected=[row for row in traces if row.get("calibration",{}).get("status") in {"agreed","declared_scale_rejected"}]
    grouped={}
    for trace in selected: grouped.setdefault(trace.get("page"),[]).append(trace)
    ai_input,spatial,_building=_load_inputs(root)
    elevation_rows=_storefront_elevation_pages(ai_input)
    for page,traces_on_page in sorted(grouped.items()):
        comfort_traces=[row for row in traces_on_page if row.get("room_id") in comfort]
        if not comfort_traces: continue
        target=f"page-{page}"
        current=_current_task(root,"P3_boundaries",target)
        if current and current.get("status") in {"applied","below_accuracy_bar"}: continue
        perimeter=_page_perimeter_data(traces_on_page)
        if perimeter["error"]:
            result.append(("P3_boundaries",target,{"task":"P3_boundaries","page":page},"",[],perimeter["error"]))
            continue
        runs=perimeter["runs"]
        room_edges=[]
        for trace in comfort_traces:
            for mapping in perimeter["trace_edges"].get(trace["trace_id"],[]):
                room_edges.append({"trace_id":trace["trace_id"],"room_id":trace["room_id"],
                    "room_label":trace.get("room_label"),"edge_index":mapping["edge_index"],
                    "length_m":round(mapping["length_m"],4),"perimeter_run_index":mapping["perimeter_run_index"]})
        storefront_match=_match_storefront_run(runs,elevation_rows,spatial)
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
        prompt=("Classify each numbered run on the outside perimeter of the union of the traced tenancy rooms: external, mall, adjacent_tenancy, internal or unknown. "
                "Do not classify interior partition edges; unmapped room edges become internal automatically. Quote drawing evidence for each non-unknown answer. "
                "Runs under 1 m inherit the nearest numbered run's classification. "
                "Storefront totals may exceed inside-face spans by 0–600 mm or differ by <=2%; never classify a matched run internal/adjacent_tenancy. "
                "JSON only: {\"runs\":[{\"run_number\":int,\"boundary\":\"external|mall|adjacent_tenancy|internal|unknown\",\"evidence\":string}]}\n\n"
                +json.dumps({"page":page,"room_parts":[row.get("room_label") for row in traces_on_page],
                    "perimeter_runs":[[row["index"]+1,round(row["length_m"],2)] for row in asked],
                    "short_run_inheritance":{str(int(key)+1):value+1 for key,value in short_inheritance.items()},
                    "storefront_run_number":storefront_match["run_index"]+1 if storefront_match["run_index"] is not None else None,
                    "storefront_elevation_total_width_mm":storefront_match["total_mm"],
                    "storefront_match_reason":storefront_match["reason"],"nearby_text":cue_text},
                    ensure_ascii=False,separators=(",",":")))
        packet={"task":"P3_boundaries","page":page,"trace_ids":[row["trace_id"] for row in comfort_traces],
            "trace_id":comfort_traces[0]["trace_id"] if len(comfort_traces)==1 else None,
            "room_id":comfort_traces[0].get("room_id"),"room_label":comfort_traces[0].get("room_label"),
            "mm_per_px":perimeter["mm_per_px"],"edges":asked,"runs":runs,"room_edges":room_edges,
            "short_run_inheritance":short_inheritance,
            "storefront_run_index":storefront_match["run_index"],
            "storefront_total_width_mm":storefront_match["total_mm"],
            "storefront_match_reason":storefront_match["reason"],"nearby_text":cue_text}
        if len(prompt)>1500:
            result.append(("P3_boundaries",target,packet,"",[],"Boundary prompt exceeds 1,500 characters; evidence was not shortened."))
            continue
        try:
            image,packet["image_transform"]=_perimeter_task_image(root,traces_on_page,perimeter,asked)
        except (OSError,ValueError) as error:
            result.append(("P3_boundaries",target,packet,"",[],str(error))); continue
        result.append(("P3_boundaries",target,packet,prompt,[_png_bytes(image)],""))
    return result


def _p4_packets(root):
    result=[]
    try: traces=reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir":str(root)}))
    except (OSError,ValueError,TypeError,KeyError): traces=[]
    ai_input,spatial,_building=_load_inputs(root)
    elevation_pages=_storefront_elevation_pages(ai_input)
    if not elevation_pages: return result
    try:
        from ai import ceiling_volume_resolution
        heights=ceiling_volume_resolution.values_by_room(_read(root/"ceiling_volume_resolution.json",{}))
    except (OSError,ValueError,TypeError,KeyError): heights={}
    comfort=_comfort_room_ids(root)
    assumption=__import__("ai.ai_preliminary",fromlist=["load_pack"]).load_pack()
    glazing="retail"; shading="unshaded"
    selected=[trace for trace in traces if trace.get("calibration",{}).get("status") in {"agreed","declared_scale_rejected"}]
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
        for room_id,room_traces in page_by_room.items():
            mappings=[{"trace_id":trace["trace_id"],**edge}
                      for trace in room_traces
                      for edge in page_perimeter["trace_edges"].get(trace["trace_id"],[])
                      if edge.get("perimeter_run_index") is not None]
            room_run_indices={row["perimeter_run_index"] for row in mappings}
            eligible_indices=sorted(index for index in room_run_indices if p3_runs.get(index) in {"external","mall"})
            if not eligible_indices:
                # Internal rooms have no shopfront task; don't create a red blocked card.
                continue
            eligible_runs=[{**run_geometry[index],"run_index":index} for index in eligible_indices]
            storefront_match=_match_storefront_run(eligible_runs,elevation_pages,spatial)
            if len(eligible_runs)==1:
                chosen=eligible_runs[0]
                matched_pages=set(storefront_match["pages"])
                elevation=(next((page for page in elevation_pages if page["page"] in matched_pages),None)
                           or elevation_pages[0])
                storefront_reason="Only one eligible external/mall wall run remains for this room after P3."
            elif storefront_match["run_index"] is not None:
                chosen=next(run for run in eligible_runs if run["run_index"]==storefront_match["run_index"])
                elevation=next(page for page in elevation_pages if page["page"] in set(storefront_match["pages"]))
                storefront_reason=storefront_match["reason"]
            else:
                result.append(("P4_openings",f"{room_id}-storefront-unresolved-page-{plan_page}",
                    {"task":"P4_openings","room":room_traces[0].get("room_label"),"trace_ids":[t["trace_id"] for t in room_traces],
                     "storefront_match_reason":storefront_match["reason"]},"",[],storefront_match["reason"]))
                continue
            run_index=chosen["run_index"]
            part_edges=[{key:row[key] for key in ("trace_id","edge_index","length_m")}
                        for row in mappings if row["perimeter_run_index"]==run_index]
            if not part_edges: continue
            trace=room_traces[0]
            target=f"{room_id}-run-{run_index}-page-{elevation['page']}-trace-{trace['trace_id']}"
            old=_current_task(root,"P4_openings",target)
            if old and old.get("status") in {"applied","below_accuracy_bar"}: continue
            identity=ceiling_volume_resolution.room_identity(trace.get("room_label",""),trace.get("level_name",""))
            values=heights.get(room_id) or heights.get(identity) or {}
            ceiling=values.get("ceiling_height_mm")
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
            has_dimension_text=any(isinstance(item,dict) and isinstance(item.get("value_mm"),(int,float))
                                  for item in (page_ocr.get("dimension_candidates") or []))
            run_span=float(chosen["span_m"])
            prompt=("The image is a shopfront/external elevation for the selected outside wall run. List each glazed panel, excluding signage, solid panels and open doorways. "
                "Use printed dimensions only; quote exact text. Return null for unprinted sill/head/width. Missing sill is assumed 0 (glass to floor); missing head is assumed ceiling height. "
                "For vector-outline text, list each glazed panel and each excluded solid part (roller shutter, signage, or door), with width_mm when readable. Glazed widths must not exceed the total. Glazed plus stated excluded widths must sum to the read total within 5%; if any excluded width is unreadable, leave it null and the sum check will be skipped with a note. Outlines use inside faces; elevations give overall widths. Accept 0 <= total_mm - span_mm <= 600 (two wall thicknesses) OR |total-span|/span <= 2%. "
                "JSON only: {\"total_width_mm\":number,\"total_width_text\":string,\"panels\":[{\"label\":string,\"width_mm\":number|null,\"sill_mm\":number|null,\"head_mm\":number|null,\"printed_text\":[string],\"source\":\"printed_text|read_from_image\"}],\"excluded\":[{\"label\":string,\"width_mm\":number|null,\"why\":string}]}\n\n"
                +json.dumps({"page":page,"room":trace.get("room_label"),"perimeter_run_number":run_index+1,
                    "room_part_edges":part_edges,"perimeter_run_span_m":run_span,"ceiling_height_mm":ceiling,
                    "text_layer":text_layer[:60]},ensure_ascii=False,separators=(",",":")))
            if len(prompt)>2000:
                result.append(("P4_openings",target,{"task":"P4_openings","room":trace.get("room_label"),"page":page},"",[],
                    "Opening prompt exceeds 2,000 characters; evidence was not shortened.")); continue
            packet={"task":"P4_openings","room_id":room_id,"room_label":trace.get("room_label"),
                "trace_id":trace["trace_id"],"trace_ids":[t["trace_id"] for t in room_traces],
                "part_edges":part_edges,"edge_index":part_edges[0]["edge_index"],
                "edge_indices":list(dict.fromkeys(row["edge_index"] for row in part_edges)),
                "wall_run_index":run_index,"wall_run_span_m":run_span,"edge_length_mm":run_span*1000,
                "page":page,"ceiling_height_mm":ceiling,"text_layer":text_layer[:60],
                "storefront_match_reason":storefront_reason,"allow_vector_outline_read":not has_dimension_text,
                "glazing_choice":glazing,"shading_category":shading,
                "glazing_option":f"Preliminary single glazing (pack au-preliminary-v3): U {assumption['profiles'][glazing]['glazing_u_w_m2k']}, SHGC {assumption['profiles'][glazing]['shgc']}",
                "frame_fraction":assumption.get("preliminary_envelope",{}).get("glazing_frame_fraction"),"evidence":{"page":page}}
            result.append(("P4_openings",target,packet,prompt,[_png_bytes(image)],""))
    return result


def _p0_trace_rows(root, record, polygons):
    page = int(record["packet"]["page"])
    calibration = deepcopy(record["packet"]["calibration"])
    if calibration.get("status") not in {"agreed", "declared_scale_rejected"}:
        raise ValueError("P0 cannot apply room areas until printed dimensions calibrate within 2%.")
    rooms = reviewer_room_geometry_service._rooms(reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    room_ids = {}
    for room in rooms:
        room_ids.setdefault(room["label"].casefold(), room["room_id"])
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
            "ai_measured_area_m2": room_outline.area_m2(polygon, mm_per_px),
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


def _apply_p0_wall_styles(record, validated):
    wall_ids = validated.get("wall_style_ids", [])
    record.update({"status":"applied", "source":"ai_determined", "applied_value":{"wall_style_ids":wall_ids,
        "legend":deepcopy(record["packet"].get("summary", [])), "label":record["quality_label"]},
        "validation":{"wall_style_ids":wall_ids}, "block_reason":""})
    return record


def _apply_p0_room_names(web, project, root, record, validated):
    from shapely.geometry import Polygon
    packet = record["packet"]
    context = _p0_context(root, packet["page"])
    walls, areas, segments = _p0_walls_areas(root, packet["page"], context, packet["calibration"],
                                             packet.get("wall_style_ids", []), packet.get("raster_mode", False))
    names, splits = validated["names"], validated["splits"]
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
        record["run_id"], record["quality_label"], replace_pages=[packet["page"]])
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]), "source":"ai_determined",
        "applied_value":{"rooms":_p0_result_rooms(prepared),
                        "label":record["quality_label"]}, "validation":validated, "block_reason":""})
    return record


def _apply_p0_outlines(web, project, root, record, validated):
    context = _p0_context(root, record["packet"]["page"])
    calibration = record["packet"]["calibration"]
    _walls, _areas, segments = _p0_walls_areas(root, record["packet"]["page"], context, calibration,
        record["packet"].get("wall_style_ids", []), record["packet"].get("raster_mode", False))
    snap_px = 250 / record["packet"]["calibration"]["mm_per_px"]
    polygons = []
    for row in validated:
        polygon, report = room_outline.snap_polygon(row["points"], segments, snap_px)
        polygons.append({"label":row["room"], "polygon":polygon, "method":"ai_outline_snapped",
                         "note":f"Open sides: {row.get('open_sides','')}; snapped {report['snapped_edges']}/{report['edges']} edges"})
    rows = _p0_trace_rows(root, record, polygons)
    prepared = reviewer_room_geometry_service.persist_ai_determined_traces(web, project, rows,
        record["run_id"], record["quality_label"], replace_pages=[record["packet"]["page"]])
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"rooms":_p0_result_rooms(prepared),"label":record["quality_label"]},
        "validation":validated,"block_reason":""})
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


def _apply_p3(web, project, root, record, validated):
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
                    if boundary=="unknown":
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
            reviewer_room_geometry_service.post(web,project,{"action":"classify_envelope","trace_id":trace_id,
                "reviewer":"Archie AI (P3)","edges":request_edges,"roof":trace.get("roof","unknown"),
                "openings":trace.get("openings",[]),"declaration_source":"ai_determined","ai_run_id":record["run_id"],
                "edge_sources":{str(index):source for index,source in sources.items()},
                "boundary_evidence":[{"index":index,"text":evidence[index],"source":sources[index],"label":p3_label}
                                     for index in values]}, _validated_current_records=tuple(traces.values()),
                _defer_evidence_rebuild=True)
            results.append({"trace_id":trace_id,"room_id":trace.get("room_id"),"room":trace.get("room_label"),
                "edges":[{"index":index,"edge_length_m":mapping.get(index,{}).get("length_m"),
                    "boundary":values[index],"source":sources[index],"evidence":evidence[index],"label":p3_label}
                    for index in sorted(values)]})
        if web is not None and project:
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
    reviewer_room_geometry_service.post(web,project,{"action":"classify_envelope","trace_id":trace["trace_id"],
        "reviewer":"Archie AI (P3)","edges":request_edges,"roof":trace.get("roof","unknown"),
        "openings":trace.get("openings",[]),"declaration_source":"ai_determined","ai_run_id":record["run_id"],
        "edge_sources":{str(index):source for index,source in sources.items()},
        "boundary_evidence":[{"index":index,"text":evidence.get(index,""),"source":sources.get(index)} for index in values]})
    edges=[{"index":index,"edge_length_m":lengths.get(index),"boundary":values[index],"source":sources[index],
            "evidence":evidence.get(index,""),"label":"Assumed (typical for a tenancy in a centre)" if sources[index]=="ai_fallback" else "AI-determined"}
           for index in sorted(values)]
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"edges":edges,"label":record.get("quality_label","AI-determined")},
        "validation":validated,"block_reason":""})
    return record


def _apply_p4(web, project, root, record, validated):
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
        total_length=sum(float(row.get("length_m") or 0) for row in part_edges)
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
                    "ai_run_id":record["run_id"],"evidence":panel.get("printed_text",[]),
                    "assumptions":[item for item in (panel.get("sill_assumption"),panel.get("head_assumption")) if item]}
                openings_by_trace[part["trace_id"]].append(opening)
                fragments.append({"trace_id":part["trace_id"],"edge_index":edge_index,"width_m":share})
            applied_panels.append({"width_mm":panel["width_mm"],"sill_mm":panel["sill_mm"],"head_mm":panel["head_mm"],
                "source":"ai_determined","parts":fragments,
                "sill_assumption":panel.get("sill_assumption"),"head_assumption":panel.get("head_assumption")})
        for trace_id,part_trace in trace_by_id.items():
            reviewer_room_geometry_service.post(web,project,{"action":"classify_envelope","trace_id":trace_id,
                "reviewer":"Archie AI (P4)","edges":part_trace.get("edges",[]),"roof":part_trace.get("roof","unknown"),
                "openings":[*existing[trace_id],*openings_by_trace[trace_id]],
                "declaration_source":"ai_determined","ai_run_id":record["run_id"]})
        record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
            "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"page":packet["page"],
                "total_width_mm":validated["total_width_mm"],"glazed_panels":applied_panels,
                "trace_ids":list(trace_by_id),"excluded":validated.get("excluded",[]),
                "glazing_option":packet.get("glazing_option"),"label":record["quality_label"]},
            "validation":validated,"block_reason":""})
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
    reviewer_room_geometry_service.post(web,project,{"action":"classify_envelope","trace_id":trace["trace_id"],
        "reviewer":"Archie AI (P4)","edges":trace.get("edges",[]),"roof":trace.get("roof","unknown"),
        "openings":[*existing,*openings],"declaration_source":"ai_determined","ai_run_id":record["run_id"]})
    record.update({"status":_status_for_accuracy("ai_determined",record["accuracy"]),"source":"ai_determined",
        "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"page":packet["page"],
          "total_width_mm":validated["total_width_mm"],"glazed_panels":applied_panels,"excluded":validated.get("excluded",[]),
          "glazing_option":packet.get("glazing_option"),"label":record["quality_label"]},
        "validation":validated,"block_reason":""})
    return record


def _refresh_geometry_tasks(root):
    # Persist naming tasks before asking whether any rooms still need outlines.
    # If both packet lists are calculated together, the outline builder cannot
    # see the just-created naming tasks and offers an unnecessary second task.
    _refresh_site_tasks(root)
    for packet_builder in (_p0_initial_packets, _p0_followup_packets, _p0_outline_packets, _s1_packets):
        for task,target,packet,prompt,images,reason in packet_builder(root):
            budget=autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root,task,target,packet,prompt,images,reason or
                        (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened." if len(prompt)>budget else ""))
    _refresh_roof_tasks(root)
    for packet_builder in (_p3_packets,_p4_packets,_p6_kitchen_packets):
        for task,target,packet,prompt,images,reason in packet_builder(root):
            budget=autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root,task,target,packet,prompt,images,reason or
                        (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened." if len(prompt)>budget else ""))


def _create_run(root, task, target, packet, prompt, image_bytes=None, blocked_reason=""):
    accuracy = _accuracy(task)
    packet_bytes = json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    image_hashes = [hashlib.sha256(data).hexdigest() for data in (image_bytes or [])]
    input_fingerprint = autonomous_tasks.fingerprint({"packet": packet, "images": image_hashes})
    parent = _task_dir(root, task, target)
    current = _current_task(root, task, target)
    if current and current.get("input_fingerprint") == input_fingerprint:
        return current
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = parent / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    packet["prompt_fingerprint"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    record = {
        "schema_version": 1, "run_id": run_id, "task": task, "target": target, "status": "blocked" if blocked_reason else "waiting_for_reply",
        "created_at": _now(), "input_fingerprint": input_fingerprint, "packet_fingerprint": hashlib.sha256(packet_bytes).hexdigest(),
        "prompt_fingerprint": packet["prompt_fingerprint"], "budget": autonomous_tasks.TASKS[task],
        "packet": packet, "prompt": prompt, "image_hashes": image_hashes,
        "accuracy": accuracy, "quality_label": "AI-determined" if accuracy["auto_apply"] else "AI-determined (below accuracy bar)",
        "reply": "", "reply_hash": "", "reply_attempts": [], "validation": {}, "applied_value": {}, "source": "",
        "retry_count": 0, "block_reason": blocked_reason, "cross_check": {},
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
    for task in autonomous_tasks.TASKS:
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
    """Small result-only view for contractor screens; never returns prompts or replies."""
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


def _task_progress(root):
    """Counts over every current task (not only the ones with results), for the contractor progress screen."""
    records = [record for record in _all_current(root) if record.get("task")]
    marker = hashlib.sha256("|".join(sorted(f"{record.get('task')}:{record.get('target')}:{record.get('status')}"
                                            for record in records)).encode("utf-8")).hexdigest()[:16]
    return {"total": len(records),
            "waiting": sum(1 for record in records if record.get("status") == "waiting_for_reply"),
            "blocked": sum(1 for record in records if record.get("status") == "blocked"),
            "marker": marker}


def run_all(web, project):
    root = _root(project)
    with page_analysis_cache.operation(root):
        return _run_all(web, project, root)


def _run_all(web, project, root):
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
    for packet_builder in (_p0_followup_packets, _p0_outline_packets, _s1_packets, _p3_packets, _p4_packets, _p6_kitchen_packets):
        for task, target, packet, prompt, images, reason in packet_builder(root):
            budget = autonomous_tasks.TASKS[task]["budget_chars"]
            _create_run(root, task, target, packet, prompt, images,
                        reason or (f"Prompt exceeds the {budget:,}-character limit; evidence was not shortened."
                                   if len(prompt) > budget else ""))
    return get(web, project)


def _export_determinations(root):
    result = {}
    p1 = _latest_site(root)
    if p1:
        result["P1_site"] = {"site_text": p1.get("site_text", ""),
                             "source": "fallback" if p1.get("source") == "ai_fallback" else "ai_determined"}
    north = {}
    for record in _all_current(root):
        if record.get("task") == "P2_north" and record.get("applied_value"):
            value = record["applied_value"]
            north[value["page"]] = {"page": value["page"], "plan_up_azimuth_deg": value["plan_up_azimuth_deg"],
                                     "source": "fallback" if record.get("source") == "ai_fallback" else "ai_determined"}
    if north:
        result["P2_north"] = [north[key] for key in sorted(north)]
    roof = {}
    for record in _all_current(root):
        if record.get("task") == "P5_roof" and record.get("applied_value"):
            value = record["applied_value"]
            roof[value["room"]] = {"room": value["room"], "roof": value["roof"],
                                    "source": {"ai_fallback": "fallback", "reviewer": "reviewer"}.get(record.get("source"), "ai_determined")}
    if roof:
        result["P5_roof"] = [roof[key] for key in sorted(roof)]
    kitchen_record = next((record for record in _all_current(root)
                           if record.get("task") == "P6_kitchen" and record.get("applied_value")), None)
    if kitchen_record:
        source = "stand_in" if kitchen_record.get("stand_in") else "ai_determined"
        result["P6_kitchen"] = kitchen_equipment.to_determinations(
            kitchen_record["applied_value"].get("items", []), source=source)
    rooms = {}
    for task in ("S1_printed_areas", "P0_room_names", "P0_room_outlines"):
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


def _apply_p2(web, project, root, record, validated):
    page = record["packet"]["page"]
    north_artifact = _read(root / "reviewer_room_geometry.json", {})
    existing_declaration = north_artifact.get("page_north", {}).get(str(page), {})
    existing_source = existing_declaration.get("declaration_source", "reviewer" if existing_declaration else "")
    if existing_source == "reviewer":
        record.update({"status": "applied", "source": "reviewer", "applied_value": {"page": page, "plan_up_azimuth_deg": existing_declaration.get("plan_up_azimuth_deg"), "overrode_by": "reviewer"}, "validation": validated})
        return record
    if not validated.get("found"):
        record.update({"status": _status_for_accuracy("ai_determined", record.get("accuracy", {})),
                       "source": "ai_determined",
                       "applied_value": {"page": page, "plan_up_azimuth_deg": None},
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
        if other_validation.get("found") is not None:
            completed_pages.add(int(other.get("packet", {}).get("page", 0)))
        if other_validation.get("found"):
            other_page = int(other.get("packet", {}).get("page", 0))
            candidates_by_page[other_page] = float(other_validation["plan_up_azimuth_deg"])
            other_records[other_page] = other
    candidates_by_page[page] = float(validated["plan_up_azimuth_deg"])
    completed_pages.add(page)
    # Use the same complete plan-view list as the north packet builder and declare_north.
    plan_page_numbers = [row["page"] for row in autonomous_tasks.plan_pages(_read(root / "ai_input.json", {}))]
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
            record["block_reason"] = "Plan pages disagree by more than 5°. Retry this page once; after that Archie will apply a strict-majority direction or block if there is no majority."
            record["validation"] = validated
            record["cross_check"] = consensus
            return record
        record.update({"status": "blocked", "block_reason": "The plan pages have no strict-majority north direction after one retry; façade sun remains not assessed.", "validation": validated, "cross_check": consensus})
        return record
    applied_angle = consensus["winner"]
    declaration_source = "ai_determined"
    reviewer = "Archie AI"
    task_records = {**other_records, page: record}
    primary_apply_failed = False
    for north_page in plan_page_numbers:
        declaration = _read(root / "reviewer_room_geometry.json", {}).get("page_north", {}).get(str(north_page), {})
        if declaration.get("declaration_source", "reviewer" if declaration else "") == "reviewer":
            continue
        north_record = task_records.get(north_page)
        if not north_record:
            continue
        try:
            reviewer_room_geometry_service.post(web, project, {
                "action": "declare_north", "page": north_page, "reviewer": reviewer,
                "plan_up_azimuth_deg": applied_angle, "declaration_source": declaration_source,
                "ai_run_id": north_record["run_id"],
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
    if task_records and web is not None:
        calculation_extraction_service.post(web, project, {"action": "build"})
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"page": page, "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                                     "evidence": validated.get("description", "North arrow in source crop"), "ai_run_id": record["run_id"]},
                   "validation": validated, "cross_check": consensus, "block_reason": ""})
    return record


def _apply_p5(web, project, root, record, validated):
    room_id = record["target"]
    paths = reviewer_room_geometry_service._paths(project)
    artifact = _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    traces = [row for row in artifact.get("records", []) if row.get("room_id") == room_id]
    if not traces:
        record.update({"status": "blocked", "block_reason": "The current traced room no longer exists.", "validation": validated})
        return record
    selected = validated["roof"]
    source = "ai_determined"
    if selected == "unknown":
        record.update({"status": "needs_contractor_answer", "block_reason": ROOF_CONTRACTOR_QUESTION,
                       "validation": validated, "applied_value": {}, "source": ""})
        return record
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
        })
        applied.append(trace)
    representative=reviewer_kept[0] if reviewer_kept and not applied else traces[0]
    if not applied and reviewer_kept:
        record.update({"status":"applied","source":"reviewer","applied_value":{"room":representative.get("room_label"),
            "roof":representative.get("roof"),"overrode_by":"reviewer","part_count":len(traces)},"validation":validated})
        return record
    record.update({"status": _status_for_accuracy(source, record["accuracy"]), "source": source,
                   "applied_value": {"room": representative.get("room_label", room_id), "roof": selected,
                                     "part_count":len(traces),
                                     "source": source, "ai_run_id": record["run_id"], "evidence": validated.get("evidence", ""),
                                     "label": record["quality_label"]},
                   "validation": validated, "block_reason": ""})
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


def _has_explicit_roof_evidence(packet):
    for row in packet.get("facts", []) if isinstance(packet, dict) else []:
        text = str(row.get("text", "")) if isinstance(row, dict) else ""
        if re.search(r"\b(?:roof|slab|floor|tenancy|level)\b.{0,60}\b(?:above|over|below|beneath|directly)\b|"
                     r"\b(?:above|over|below|beneath|directly)\b.{0,60}\b(?:roof|slab|floor|tenancy|level)\b",
                     text, re.I):
            return True
    return False


def _answer_roof(web, project, root, data):
    if data.get("task") != "P5_roof":
        raise ValueError("Contractor answer is only supported for roof exposure.")
    target = str(data.get("target", ""))
    record = _current_task(root, "P5_roof", target)
    if not record or record.get("status") != "needs_contractor_answer":
        raise ValueError("This roof question is no longer awaiting a contractor answer.")
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
            "reviewer": "Answered by the contractor", "edges": deepcopy(trace.get("edges", [])),
            "roof": answer_roof[answer], "openings": deepcopy(trace.get("openings", [])),
            "confirm_roof": answer != "not_sure",
        })
    if answer == "not_sure":
        record.update({"status": "contractor_answered_not_sure", "source": "reviewer",
                       "applied_value": {}, "contractor_answer": answer,
                       "contractor_answered_by": "Answered by the contractor",
                       "block_reason": "Roof exposure remains unknown and not assessed."})
    else:
        record.update({"status": "applied", "source": "reviewer",
                       "applied_value": {"room": traces[0].get("room_label", target), "roof": answer_roof[answer],
                                         "source": "reviewer", "label": "Answered by the contractor","part_count":len(traces)},
                       "contractor_answer": answer, "contractor_answered_by": "Answered by the contractor",
                       "block_reason": ""})
    _update_record(root, record)
    _export_determinations(root)
    return get(web, project)


def post(web, project, data):
    with page_analysis_cache.operation(_root(project)):
        return _post(web, project, data)


def _post(web, project, data):
    root = _root(project)
    action = data.get("action", "")
    if action in {"run_all", "build"}:
        return run_all(web, project)
    if action == "answer_roof":
        return _answer_roof(web, project, root, data)
    task, target = str(data.get("task", "")), str(data.get("target", ""))
    if task not in autonomous_tasks.TASKS or not target:
        raise ValueError("Choose a supported task and target.")
    record = _current_task(root, task, target)
    if not record:
        raise ValueError("Build the task packet before validating a reply.")
    if record.get("status") == "blocked" and record.get("block_reason") and not data.get("reply"):
        return get(web, project)
    if action != "validate_apply":
        raise ValueError("Action must be run_all or validate_apply.")
    reply = str(data.get("reply", ""))
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
        elif task == "P0_wall_styles":
            validated = autonomous_tasks.validate_wall_style_batch_reply(record["packet"], reply)
            record = _apply_p0_wall_styles(record, validated)
        elif task == "P0_room_names":
            validated = autonomous_tasks.validate_p0_room_names(record["packet"], reply)
            record = _apply_p0_room_names(web, project, root, record, validated)
        elif task == "P0_room_outlines":
            validated = autonomous_tasks.validate_p0_outlines(record["packet"], reply)
            record = _apply_p0_outlines(web, project, root, record, validated)
        elif task == "S1_printed_areas":
            record = _apply_s1_areas(web, project, root, record, reply)
        elif task == "S3_title_transcription":
            lines = __import__("ai.scan_reading", fromlist=["validate_transcription"]).validate_transcription(reply)
            record = _apply_s3_transcription(record, lines)
        elif task == "P3_boundaries":
            validated = autonomous_tasks.validate_boundary_reply(record["packet"], reply)
            record = _apply_p3(web, project, root, record, validated)
        elif task == "P4_openings":
            validated = autonomous_tasks.validate_opening_reply(record["packet"], reply)
            record = _apply_p4(web, project, root, record, validated)
        elif task == "P1_site":
            validated = autonomous_tasks.validate_site_reply(record["packet"], reply)
            record = _apply_p1(root, project, record, validated)
        elif task == "P2_north":
            validated = autonomous_tasks.validate_north_reply(record["packet"], reply)
            record = _apply_p2(web, project, root, record, validated)
        elif task == "P6_kitchen":
            validated = kitchen_equipment.validate_reply(json.loads(reply), record["packet"])
            record = _apply_p6(record, validated)
        else:
            validated = autonomous_tasks.validate_roof_reply(record["packet"], reply)
            record = _apply_p5(web, project, root, record, validated)
    except (ValueError, KeyError, TypeError) as error:
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
    if task == "S1_printed_areas" and record.get("applied_value") and web is not None:
        from backend import room_use_resolution_service
        room_use_resolution_service.post(web, project, {"action": "resolve"})
    _export_determinations(root)
    productization.record_change_if_fingerprint_changed(
        root, action="autonomous_task_applied", target=f"{task}/{target}",
        previous_fingerprint="", new_fingerprint=record["input_fingerprint"], affected_ids=[record["run_id"]])
    # A completed stage may unlock the next bounded packet. This only creates
    # local task records; the ChatGPT transport remains manual.
    _refresh_geometry_tasks(root)
    return get(web, project)


def image(web, project, data):
    task, target, run_id, name = (str(data.get(key, "")) for key in ("task", "target", "run_id", "name"))
    if task not in autonomous_tasks.TASKS or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", run_id) or not re.fullmatch(r"image-\d+\.png", name):
        raise ValueError("Autonomous task image request is invalid.")
    path = _task_dir(_root(project), task, target) / "runs" / run_id / name
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(_root(project).resolve()):
        raise ValueError("Task image is unavailable.")
    return {"path": str(resolved), "content_type": "image/png"}
