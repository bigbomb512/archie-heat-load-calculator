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
from backend import productization, reviewer_room_geometry_service, site_location_service, calculation_extraction_service
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
            selected.setdefault(trace["room_id"], trace)
    result = []
    for room_id, trace in selected.items():
        packet = autonomous_tasks.build_roof_facts(ai_input, site, {
            "room_id": room_id, "room_label": trace.get("room_label"),
            "level_name": trace.get("level_name"), "page": trace.get("page"),
        })
        prompt = autonomous_tasks.roof_prompt(packet)
        result.append((room_id, packet, prompt, []))
    return result


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
                 "No current calibrated comfort-scope Kitchen outline is available; trace and calibrate the kitchen first.")]
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
    viewport = tuple((page_meta.get("plan_viewport") or {}).get("bbox_px") or (0, 0, image.size[0], image.size[1]))
    page_ocr = next((row for row in spatial.get("pages", []) if row.get("page") == page_number), {})
    from ai.dimension_wall_matcher import page_mm_per_px
    declared_scale = page_mm_per_px(page_ocr.get("scale_candidates", []), render_dpi=72 * scale)
    summary = room_outline.style_summary(objects, viewport, declared_scale)
    return {"objects": objects, "image": image, "viewport": viewport, "declared_mm_per_px": declared_scale,
            "summary": summary, "page_meta": page_meta}


def _p0_initial_packets(root):
    from PIL import ImageDraw
    ai_input, _spatial, _building = _load_inputs(root)
    result = []
    for page in autonomous_tasks.plan_pages(ai_input):
        number = page["page"]
        try:
            context = _p0_context(root, number)
            lines = room_outline.dimension_line_candidates(context["objects"], None,
                        (0, 0, *context["image"].size), limit=3)
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
    from shapely.ops import unary_union
    result = []
    pages = autonomous_tasks.plan_pages(_read(root / "ai_input.json", {}))
    for page in pages:
        number = page["page"]
        styles = _p0_wall_style_records(root, number)
        if not styles or any(style.get("status") not in {"applied", "below_accuracy_bar"} for style in styles):
            continue
        try:
            context = _p0_context(root, number)
            calibration = _p0_calibration(root, number, context)
            wall_style_ids = sorted({style_id for style in styles for style_id in style["applied_value"].get("wall_style_ids", [])})
            if not wall_style_ids:
                result.append(("P0_room_names", f"page-{number}", {"task":"P0_room_names", "page":number}, "", [],
                               "No wall styles were identified across the complete legend; enclosed-room naming is unavailable."))
                continue
            walls = room_outline.wall_geometry(context["objects"], wall_style_ids, context["viewport"])
            areas = room_outline.enclosed_rooms(walls, context["viewport"], context["declared_mm_per_px"] or calibration["mm_per_px"])
            if not areas:
                continue
            image, transform = room_outline.render_candidate_areas(context["image"], areas, context["viewport"])
            room_names = sorted({row["label"] for row in _inferred_room_data(root)})
            naming_packet = {"task":"P0_room_names", "page":number, "area_count":len(areas), "room_names":room_names,
                             "transform":transform, "calibration":calibration, "areas_px": [list(poly.exterior.coords) for poly in areas],
                             "wall_style_ids":wall_style_ids}
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
    for page in autonomous_tasks.plan_pages(_read(root / "ai_input.json", {})):
        number = page["page"]
        names_record = _current_task(root, "P0_room_names", f"page-{number}")
        if names_record and names_record.get("status") not in {"applied", "below_accuracy_bar"}: continue
        wall_records = _p0_wall_style_records(root, number)
        if not wall_records or any(row.get("status") not in {"applied", "below_accuracy_bar"} for row in wall_records):
            continue
        wall_style_ids = sorted({style_id for row in wall_records for style_id in row.get("applied_value", {}).get("wall_style_ids", [])})
        if not wall_style_ids:
            continue
        try:
            context = _p0_context(root, number)
            calibration = _p0_calibration(root, number, context)
            walls = room_outline.wall_geometry(context["objects"], wall_style_ids, context["viewport"])
            areas = room_outline.enclosed_rooms(walls, context["viewport"], context["declared_mm_per_px"] or calibration["mm_per_px"])
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
                      "calibration":calibration, "wall_style_ids":wall_style_ids}
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


def _p3_packets(root):
    result = []
    paths = reviewer_room_geometry_service._paths({"review_dir":str(root)})
    try:
        traces = reviewer_room_geometry_service.current_records(paths)
    except (OSError, ValueError, TypeError, KeyError):
        traces = []
    selected = [row for row in traces if row.get("room_id") in _comfort_room_ids(root)
                and row.get("calibration", {}).get("status") in {"agreed", "declared_scale_rejected"}]
    spatial = _read(root / "spatial_ocr.json", {})
    ai_input,_,_=_load_inputs(root)
    elevation_rows=[row for row in ai_input.get("drawing_set",{}).get("pages",[]) if isinstance(row,dict)
                    and re.search(r"elev|shopfront|storefront",str(row.get("title","")),re.I)]
    all_traces = {row.get("trace_id"): row for row in selected}
    for trace in selected:
        target = f"{trace['room_id']}-page-{trace['page']}-trace-{trace['trace_id']}"
        if _current_task(root,"P3_boundaries",target) and _current_task(root,"P3_boundaries",target).get("status") in {"applied","below_accuracy_bar"}:
            continue
        points, mm_per_px = trace["points_image_px"], trace["calibration"]["mm_per_px"]
        all_edges = []
        for index,(start,end) in enumerate(zip(points,points[1:])):
            length = math.dist(start, end) * mm_per_px / 1000
            all_edges.append({"index":index,"length_m":length})
        shared = _geometrically_shared_edge_indices(trace, selected)
        edges = [row for row in all_edges if row["index"] not in shared]
        cues=[]
        page_row=next((row for row in spatial.get("pages",[]) if row.get("page")==trace["page"]),{})
        raw_text=page_row.get("text", page_row.get("plain_text", ""))
        cue_pattern = re.compile(r"\b(?:shopfront|storefront|street frontage|external wall|party wall|boundary line|"
                                 r"adjacent tenancy|enclosed mall walkway)\b", re.I)
        if isinstance(raw_text,str):
            for line in raw_text.splitlines():
                if cue_pattern.search(line): cues.append(line.strip())
        if not cues:
            for field in ("word_samples","standalone_text_items"):
                items=page_row.get(field,[])
                if isinstance(items,list):
                    for item in items:
                        text=str(item.get("text",item)) if isinstance(item,dict) else str(item)
                        if cue_pattern.search(text): cues.append(text)
        cue_text="\n".join(dict.fromkeys(cues))
        storefront_index=None; storefront_total=None
        for elevation in elevation_rows:
            elevation_ocr=next((row for row in spatial.get("pages",[]) if row.get("page")==elevation.get("page")),{})
            dimension_values=[]
            for item in elevation_ocr.get("dimension_candidates",[]) or []:
                value=item.get("value_mm") if isinstance(item,dict) else None
                if isinstance(value,(int,float)) and value>0: dimension_values.append(float(value))
            matches=[edge for edge in edges if any(abs(value-edge["length_m"]*1000)/(edge["length_m"]*1000)<=.02 for value in dimension_values)]
            if len(matches)==1:
                storefront_index=matches[0]["index"]
                storefront_total=next(value for value in dimension_values if abs(value-edges[storefront_index]["length_m"]*1000)/(edges[storefront_index]["length_m"]*1000)<=.02)
                break
        prompt=("Classify every numbered edge not listed as geometrically shared: external, mall, adjacent_tenancy, internal or unknown. Quote drawing evidence "
                "for every non-unknown answer. Geometrically shared edges are classified as internal automatically. The edge matching the supplied "
                "storefront elevation total within 2% is the shopfront; never call it internal or adjacent_tenancy. If the set shows "
                "an enclosed shopping centre, default an otherwise unknown shopfront to mall; otherwise external. Default other "
                "unknown perimeter edges to adjacent_tenancy and label each ‘Assumed (typical for a tenancy in a centre)’. "
                "Return edge_number exactly as shown in the image (starting at 1). JSON only: {\"edges\":[{\"edge_number\":int,\"boundary\":\"external|mall|adjacent_tenancy|internal|unknown\",\"evidence\":string}]}\n\n"
                + json.dumps({"room":trace.get("room_label"),"page":trace.get("page"),
                              "edges":[[row["index"] + 1,round(row["length_m"],2)] for row in edges],
                              "shared_edge_numbers":[index + 1 for index in shared],
                              "storefront_edge_number":storefront_index + 1 if storefront_index is not None else None,
                              "storefront_elevation_total_width_mm":storefront_total,"nearby_text":cue_text},ensure_ascii=False,separators=(",",":")))
        if len(prompt)>1500:
            result.append(("P3_boundaries",target,{"task":"P3_boundaries","room":trace.get("room_label")},"",[],
                           "Boundary prompt exceeds 1,500 characters; evidence was not shortened.")); continue
        packet={"task":"P3_boundaries","room_id":trace["room_id"],"room_label":trace.get("room_label"),
                "trace_id":trace["trace_id"],"page":trace["page"],"edges":edges,"shared_edges":shared,
                "shared_edge_lengths":{str(row["index"]):row["length_m"] for row in all_edges if row["index"] in shared},
                "nearby_text":cue_text,"mm_per_px":mm_per_px,"storefront_edge_index":storefront_index,
                "storefront_total_width_mm":storefront_total}
        try: image=_trace_task_image(root,trace,edges,1536)
        except (OSError,ValueError) as error:
            result.append(("P3_boundaries",target,packet,"",[],str(error))); continue
        result.append(("P3_boundaries",target,packet,prompt,[_png_bytes(image)],""))
    return result


def _p4_packets(root):
    result=[]
    try:
        traces=reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir":str(root)}))
    except (OSError,ValueError,TypeError,KeyError): traces=[]
    ai_input,spatial,_building=_load_inputs(root)
    elevation_pages=[row for row in ai_input.get("drawing_set",{}).get("pages",[]) if isinstance(row,dict)
                     and re.search(r"elev|shopfront|storefront",str(row.get("title", "")),re.I)]
    if not elevation_pages: return result
    try:
        from ai import ceiling_volume_resolution
        heights=ceiling_volume_resolution.values_by_room(_read(root / "ceiling_volume_resolution.json",{}))
    except (OSError,ValueError,TypeError,KeyError): heights={}
    uses=_read(root / "room_use_resolution.json",{}); comfort=_comfort_room_ids(root)
    pack=room_outline  # keep packet code independent of assumptions; the configured glazing choice is added below.
    assumption=__import__("ai.ai_preliminary",fromlist=["load_pack"]).load_pack()
    glazing="retail"; shading="unshaded"
    for trace in traces:
        if trace.get("room_id") not in comfort or trace.get("calibration",{}).get("status") not in {"agreed","declared_scale_rejected"}: continue
        boundaries={row["index"]:row["boundary"] for row in trace.get("edges",[])}
        for edge_index,boundary in boundaries.items():
            if boundary not in {"external","mall"}: continue
            points=trace["points_image_px"]
            edge_mm=math.dist(points[edge_index],points[edge_index+1])*trace["calibration"]["mm_per_px"]
            for elevation in elevation_pages:
                page=int(elevation["page"]); target=f"{trace['room_id']}-edge-{edge_index}-page-{page}-trace-{trace['trace_id']}"
                old=_current_task(root,"P4_openings",target)
                if old and old.get("status") in {"applied","below_accuracy_bar"}: continue
                from ai import ceiling_volume_resolution
                identity=ceiling_volume_resolution.room_identity(trace.get("room_label",""),trace.get("level_name",""))
                values=heights.get(trace["room_id"]) or heights.get(identity) or {}
                ceiling=values.get("ceiling_height_mm")
                if not isinstance(ceiling,(int,float)) or ceiling<=0:
                    result.append(("P4_openings",target,{"task":"P4_openings","room":trace.get("room_label"),"page":page},"",[],
                                   "Resolve ceiling height before applying elevation glazing; no head-height fallback is available.")); continue
                try:
                    image = _p4_elevation_image(root, page)
                except (OSError, ValueError, IndexError) as error:
                    result.append(("P4_openings",target,{"task":"P4_openings","room":trace.get("room_label"),"page":page},"",[],str(error))); continue
                page_ocr=next((row for row in spatial.get("pages",[]) if row.get("page")==page),{})
                text_layer=[]
                for field in ("text","plain_text","word_samples","dimension_candidates","standalone_text_items","title_blocks"):
                    raw=page_ocr.get(field,[])
                    if isinstance(raw,str): text_layer.extend(raw.splitlines())
                    elif isinstance(raw,list): text_layer.extend(str(item.get("text",item)) if isinstance(item,dict) else str(item) for item in raw)
                prompt=("The image is an architectural shopfront elevation. List each glazed panel, excluding signage, solid panels and open doorways. "
                    "Use only printed dimensions; quote their exact text. Return null for unprinted sill/head/width. Missing sill is assumed 0 (glass to floor); "
                    "missing head is assumed ceiling height. A head above ceiling is capped at the ceiling. Missing width means the panel is excluded. "
                    "Every dimension must quote the elevation text layer, or mark source read_from_image if vector-outline text is visible. For vector-outline text, "
                    "panel widths must sum to the printed total within 2%, and the total must match the traced shopfront edge within 2%, otherwise do not apply. "
                    "JSON only: {\"total_width_mm\":number,\"total_width_text\":string,\"panels\":[{\"label\":string,\"width_mm\":number|null,\"sill_mm\":number|null,\"head_mm\":number|null,\"printed_text\":[string],\"source\":\"printed_text|read_from_image\"}],\"excluded\":[{\"label\":string,\"why\":string}]}\n\n"
                    + json.dumps({"page":page,"room":trace.get("room_label"),"edge_length_mm":round(edge_mm,1),
                                  "ceiling_height_mm":ceiling,"text_layer":text_layer[:60]},ensure_ascii=False,separators=(",",":")))
                if len(prompt)>2000:
                    result.append(("P4_openings",target,{"task":"P4_openings","room":trace.get("room_label"),"page":page},"",[],"Opening prompt exceeds 2,000 characters; evidence was not shortened.")); continue
                has_dimension_text = any(isinstance(item, dict) and isinstance(item.get("value_mm"), (int, float))
                                         for item in (page_ocr.get("dimension_candidates") or []))
                packet={"task":"P4_openings","room_id":trace["room_id"],"room_label":trace.get("room_label"),"trace_id":trace["trace_id"],
                    "edge_index":edge_index,"edge_length_mm":edge_mm,"page":page,"ceiling_height_mm":ceiling,"text_layer":text_layer[:60],
                    "allow_vector_outline_read":not has_dimension_text,"glazing_choice":glazing,"shading_category":shading,
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
    walls = room_outline.wall_geometry(context["objects"], packet["wall_style_ids"], context["viewport"])
    areas = room_outline.enclosed_rooms(walls, context["viewport"], context["declared_mm_per_px"] or packet["calibration"]["mm_per_px"])
    names, splits = validated["names"], validated["splits"]
    segments = room_outline.drawn_segments(context["objects"], [row["style_id"] for row in context["summary"] if not row["hatch"]])
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
    segments = room_outline.drawn_segments(context["objects"], record["packet"]["wall_style_ids"])
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


def _current_trace(root, trace_id):
    paths=reviewer_room_geometry_service._paths({"review_dir":str(root)})
    return next((row for row in reviewer_room_geometry_service.current_records(paths) if row.get("trace_id")==trace_id),None)


def _apply_p3(web, project, root, record, validated):
    packet=record["packet"]
    trace=_current_trace(root,packet.get("trace_id"))
    if not trace: raise ValueError("The room trace is stale; rebuild the boundary task from current drawing evidence.")
    current={row["index"]:row["boundary"] for row in trace.get("edges",[])}
    lengths={row["index"]:row["length_m"] for row in packet.get("edges",[])}
    lengths.update({int(index): float(value) for index, value in packet.get("shared_edge_lengths", {}).items()})
    shared=set(packet.get("shared_edges",[])); values={row["index"]:row["boundary"] for row in validated["edges"]}
    evidence={row["index"]:row.get("evidence","") for row in validated["edges"]}
    sources={}
    for index in shared:
        values[index] = "internal"
        evidence[index] = "Parallel room boundary inferred within the 400 mm wall-thickness and 50% overlap limits."
        sources[index] = "ai_determined"
    for index in values:
        if index in shared:
            values[index],sources[index],evidence[index]="internal","ai_determined","Shared boundary with another traced room in this tenancy."
        elif values[index]=="unknown":
            if index==packet.get("storefront_edge_index"):
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
        "applied_value":{"room":trace.get("room_label",packet.get("room_label")),"edges":edges,"label":record["quality_label"]},
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
    existing=[row for row in trace.get("openings",[]) if row.get("declaration_source","reviewer")=="reviewer"]
    openings=[]
    for panel in validated.get("panels",[]):
        openings.append({"opening_id":f"ai-{record['run_id']}-{panel['panel_index']}","edge_index":packet["edge_index"],
            "width_m":panel["width_mm"]/1000,"sill_height_m":panel["sill_mm"]/1000,"head_height_m":panel["head_mm"]/1000,
            "elevation_page":packet["page"],"glazing_choice":choice,"shading_category":shade,
            "declaration_source":"ai_determined","ai_run_id":record["run_id"],"evidence":panel.get("printed_text",[]),
            "assumptions":[item for item in (panel.get("sill_assumption"),panel.get("head_assumption")) if item]})
    reviewer_room_geometry_service.post(web,project,{"action":"classify_envelope","trace_id":trace["trace_id"],
        "reviewer":"Archie AI (P4)","edges":trace.get("edges",[]),"roof":trace.get("roof","unknown"),
        "openings":[*existing,*openings],"declaration_source":"ai_determined","ai_run_id":record["run_id"]})
    applied_panels=[{"width_mm":row["width_mm"],"sill_mm":row["sill_mm"],"head_mm":row["head_mm"],"source":"ai_determined",
                     "sill_assumption":row.get("sill_assumption"),"head_assumption":row.get("head_assumption")} for row in validated.get("panels",[])]
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
    for packet_builder in (_p0_followup_packets, _p0_outline_packets, _p3_packets, _p4_packets, _p6_kitchen_packets):
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
    return {"id": project["id"], "tasks": _all_current(root, web, project), "auto_apply_bar": autonomous_task_scoring.AUTO_APPLY_BAR,
            "determinations": _site_determination(root), "supported_tasks": list(autonomous_tasks.TASKS),
            "out_of_scope_tasks": {}}


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
    return {"id": project["id"], "tasks": rows, "auto_apply_bar": autonomous_task_scoring.AUTO_APPLY_BAR}


def run_all(web, project):
    root = _root(project)
    site_packet, site_prompt, _ = _site_packet(root)
    _create_run(root, "P1_site", "project", site_packet, site_prompt,
                blocked_reason="No cited site excerpts were found." if not site_packet.get("excerpts") else
                "Prompt exceeds the 4,000-character limit." if len(site_prompt) > 4000 else "")
    for target, packet, prompt, images, *error in _north_packets(root):
        reason = error[0] if error else ""
        if not reason and len(packet.get("crops", [])) > 3:
            reason = "More than three crops were generated for one plan page."
        if not reason and any(max(crop["width_px"], crop["height_px"]) > 1024 for crop in packet.get("crops", [])):
            reason = "A north-arrow crop exceeds 1,024 px."
        _create_run(root, "P2_north", target, packet, prompt, images, reason)
    for target, packet, prompt, images in _roof_packets(root):
        record = _create_run(root, "P5_roof", target, packet, prompt, images,
                             "Prompt exceeds the 2,000-character limit; evidence was not shortened." if len(prompt) > 2000 else "")
        if record.get("status") == "waiting_for_reply" and not _has_explicit_roof_evidence(packet):
            record.update({"status": "needs_contractor_answer", "block_reason": ROOF_CONTRACTOR_QUESTION})
            _update_record(root, record)
    for task, target, packet, prompt, images, reason in _p0_initial_packets(root):
        _create_run(root, task, target, packet, prompt, images, reason or
                    ("Prompt exceeds the 1,500-character limit; evidence was not shortened."
                     if task == "P0_dimensions" and len(prompt) > 1500 else
                     "Prompt exceeds the 3,000-character limit; evidence was not shortened."
                     if task == "P0_wall_styles" and len(prompt) > 3000 else ""))
    for packet_builder in (_p0_followup_packets, _p0_outline_packets, _p3_packets, _p4_packets, _p6_kitchen_packets):
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
    for task in ("P0_room_names", "P0_room_outlines"):
        for record in (row for row in _all_current(root) if row.get("task") == task and row.get("applied_value")):
            for row in record["applied_value"].get("rooms", []):
                key = str(row.get("label", "")).casefold()
                if key and key not in rooms:
                    rooms[key] = {key_: deepcopy(row.get(key_)) for key_ in ("label", "area_m2", "source", "method")}
    if rooms:
        result["P0_rooms"] = [rooms[key] for key in sorted(rooms)]
    boundary_rows, opening_rows = {}, {}
    for record in _all_current(root):
        value = record.get("applied_value", {})
        if record.get("task") == "P3_boundaries" and value:
            for edge in value.get("edges", []):
                boundary_rows[(str(value.get("room", "")).casefold(), round(float(edge.get("edge_length_m",0)),3))] = {
                    "room":value.get("room"),"edge_length_m":edge.get("edge_length_m"),
                    "boundary":edge.get("boundary"),"source":edge.get("source", record.get("source", "ai_determined"))}
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


def _archive_reply(root, record, reply, model_note):
    run_dir = _record_file(root, record).parent
    attempts_dir = run_dir / "reply_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    attempt_id = f"{len(list(attempts_dir.glob('*.json'))) + 1:03d}_{uuid.uuid4().hex[:8]}"
    path = attempts_dir / f"{attempt_id}.json"
    reply_hash = hashlib.sha256(reply.encode("utf-8")).hexdigest()
    entry = {"attempt_id": attempt_id, "created_at": _now(), "reply_hash": reply_hash,
             "model_note": model_note, "outcome": "validating", "raw_reply": reply}
    _write(path, entry)
    record.setdefault("reply_attempts", []).append({key: entry[key] for key in
                                                    ("attempt_id", "created_at", "reply_hash", "model_note", "outcome")})
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
    for north_page in plan_page_numbers:
        declaration = _read(root / "reviewer_room_geometry.json", {}).get("page_north", {}).get(str(north_page), {})
        if declaration.get("declaration_source", "reviewer" if declaration else "") == "reviewer":
            continue
        north_record = task_records.get(north_page)
        if not north_record:
            continue
        reviewer_room_geometry_service.post(web, project, {
            "action": "declare_north", "page": north_page, "reviewer": reviewer,
            "plan_up_azimuth_deg": applied_angle, "declaration_source": declaration_source,
            "ai_run_id": north_record["run_id"],
        })
        north_record.update({"status": _status_for_accuracy("ai_determined", north_record["accuracy"]),
                             "source": "ai_determined", "applied_value": {"page": north_page,
                             "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                             "evidence": (north_record.get("validation", {}).get("result", {}) or north_record.get("validation", {})).get("description", "North arrow in source crop"),
                             "ai_run_id": north_record["run_id"]}, "cross_check": consensus, "block_reason": ""})
        if north_record is not record:
            _update_record(root, north_record)
    record.update({"status": _status_for_accuracy("ai_determined", record["accuracy"]), "source": "ai_determined",
                   "applied_value": {"page": page, "plan_up_azimuth_deg": applied_angle, "source": declaration_source,
                                     "evidence": validated.get("description", "North arrow in source crop"), "ai_run_id": record["run_id"]},
                   "validation": validated, "cross_check": consensus, "block_reason": ""})
    return record


def _apply_p5(web, project, root, record, validated):
    room_id = record["target"]
    paths = reviewer_room_geometry_service._paths(project)
    artifact = _read(paths["artifact"], reviewer_room_geometry.empty_artifact())
    trace = next((row for row in artifact.get("records", []) if row.get("room_id") == room_id), None)
    if trace is None:
        record.update({"status": "blocked", "block_reason": "The current traced room no longer exists.", "validation": validated})
        return record
    existing_roof_source = trace.get("roof_source") or (
        "reviewer" if trace.get("envelope_reviewer") and trace.get("roof") != "unknown"
        else trace.get("declaration_source", "")
    )
    if existing_roof_source == "reviewer":
        record.update({"status": "applied", "source": "reviewer", "applied_value": {"room": trace.get("room_label"), "roof": trace.get("roof"), "overrode_by": "reviewer"}, "validation": validated})
        return record
    selected = validated["roof"]
    source = "ai_determined"
    if selected == "unknown":
        record.update({"status": "needs_contractor_answer", "block_reason": ROOF_CONTRACTOR_QUESTION,
                       "validation": validated, "applied_value": {}, "source": ""})
        return record
    edges = deepcopy(trace.get("edges", []))
    reviewer = str(trace.get("envelope_reviewer") or "Archie AI")
    reviewer_room_geometry_service.post(web, project, {
        "action": "classify_envelope", "trace_id": trace["trace_id"], "reviewer": reviewer,
        "edges": edges, "roof": selected, "openings": trace.get("openings", []),
        "declaration_source": source, "ai_run_id": record["run_id"],
    })
    record.update({"status": _status_for_accuracy(source, record["accuracy"]), "source": source,
                   "applied_value": {"room": trace.get("room_label", room_id), "roof": selected,
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
    trace = next((row for row in artifact.get("records", []) if row.get("room_id") == target), None)
    if not trace:
        raise ValueError("The current room trace was not found; refresh the project and try again.")
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
                       "applied_value": {"room": trace.get("room_label", target), "roof": answer_roof[answer],
                                         "source": "reviewer", "label": "Answered by the contractor"},
                       "contractor_answer": answer, "contractor_answered_by": "Answered by the contractor",
                       "block_reason": ""})
    _update_record(root, record)
    _export_determinations(root)
    return get(web, project)


def post(web, project, data):
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
    record["stand_in"] = data.get("stand_in") is True
    reply_path = _archive_reply(root, record, reply, model_note)
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
