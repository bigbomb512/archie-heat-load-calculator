"""House windows from the drawings' text layer onto the traced rooms — no AI (user decision 2026-10-30: windows first,
AI kept to a minimum).

In a dwelling, each window in the window schedule is placed by its tag on the floor plan onto the outside wall it stands
against (ai.house_windows), and saved on the room trace like the storefront glass (source "drawing_text"). A wall
the AI hadn't classified that takes a window is taken as facing outside: a window is evidence of an outside wall.
Windows that can't be placed are listed in house_windows.json with the reason; walls without readable windows are
left for a later (AI) step.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from ai import house_windows
from backend import reviewer_room_geometry_service
from backend.job_runner import read_json

RESULT_FILE = "house_windows.json"
VERSION = "house-windows-text-v12"  # v12: persist plan-tag offsets along traced wall edges
REVIEWER = "Archie drawing text (windows)"
GLAZING_CHOICE = "residential"      # preliminary pack glazing (U 5.8, SHGC 0.45) until certificates are read (W7)
SHADING = "unshaded"
COOLED = {"comfort_hvac", "comfort_hvac_with_process_exception"}


def _same_plan_level(trace, plan_levels):
    """A saved trace on a different floor cannot host a plan window."""
    normalize = lambda value: re.sub(r"\s+floor$", "", " ".join(str(value or "").casefold().split()))
    expected = normalize(plan_levels.get(trace.get("page")))
    actual = normalize(trace.get("level_name"))
    return not expected or not actual or expected == actual


def _untraced_labels(spatial_page, level, traced_ids):
    """Cited plan labels that can own a provisional scheduled window."""
    from ai import room_inference
    from ai import room_use_resolution
    items = spatial_page.get("standalone_text_items") or spatial_page.get("word_samples") or []
    in_title = room_inference.title_block_test(spatial_page, items)
    labels = []
    for text, bbox in room_inference._text_item_lines(items):
        if in_title({"bbox": bbox}):
            continue
        label, _, _ = room_inference.parse_room_label_item(text)
        if not label or room_use_resolution.room_identity(label, level) in traced_ids:
            continue
        eligible = not bool(set(room_use_resolution._house_label_matches(label)) &
                            {"outdoor", "garage", "house_wet", "house_service"})
        labels.append({"label": label, "level_name": level, "x": (bbox[0] + bbox[2]) / 2,
                       "y": (bbox[1] + bbox[3]) / 2, "excerpt": text, "eligible": eligible})
    return labels


def _page_scale(root, page, records, sx):
    """mm per PDF point on a plan page: from any calibrated trace on it, else the sheet's printed scale; None if
    neither is known."""
    for row in records:
        if row.get("page") == page:
            return float(row["calibration"]["mm_per_px"]) * sx
    try:
        from backend.autonomous_tasks_service import _p0_context
        declared = _p0_context(root, page).get("declared_mm_per_px")
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    return float(declared) * sx if declared else None


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def is_dwelling(root):
    use = read_json(Path(root) / "room_use_resolution.json")
    context = use.get("dwelling_context") if isinstance(use, dict) else None
    if isinstance(context, dict):
        return context.get("is_dwelling") is True
    from backend.room_use_resolution_service import _dwelling_context
    return _dwelling_context(root)["is_dwelling"] is True


def _schedule_pages(root):
    """Pages the page reading or the drawing list call a schedule (window schedules are read only from these)."""
    pages = set()
    for number, page in read_json(Path(root) / "page_inventory.json").get("pages", {}).items():
        text = json.dumps({key: page.get(key) for key in ("page_type", "title")}, ensure_ascii=False).casefold()
        if "schedule" in text or "window" in text:
            pages.add(int(number))
    for page in read_json(Path(root) / "ai_input.json").get("drawing_set", {}).get("pages", []):
        if isinstance(page, dict) and "schedule" in str(page.get("title", "")).casefold():
            pages.add(int(page["page"]))
    return sorted(pages)


def _pdf_pages(source):
    """(close, read) where read(page) -> (width_pt, height_pt, words), parsed once per PDF version (seating's cache)."""
    import pdfplumber
    from backend import seating_service
    doc = pdfplumber.open(source)
    version = seating_service._pdf_version(source)
    def read(number):
        page = seating_service._Page(doc, number, version)
        return page.width, page.height, page.extract_words()
    flowed = {}

    def flow(number):
        """The page's words in the PDF's own text order: a tag drawn letter by letter (set 15's "WG_07") is one word
        here, where position order splits it; set 15's ground floor had 2 tags read that way, 17 this way (2026-11-15)."""
        if number not in flowed:
            flowed[number] = doc.pages[number - 1].extract_words(use_text_flow=True)
        return flowed[number]
    read.flow = flow
    return doc.close, read


def _outside_edges(trace, records, mm_per_px):
    """Edges classed outside, and unclassified edges that don't run along another traced room."""
    from backend.ai_preliminary_service import _edges_along_other_rooms, _outlines_by_page
    edges = {row.get("index"): row.get("boundary", "unknown") for row in trace.get("edges", []) if isinstance(row, dict)}
    count = len(trace.get("points_image_px", [])) - 1
    unknown = [index for index in range(count) if edges.get(index, "unknown") == "unknown"]
    along = _edges_along_other_rooms({**trace, "trace_id": trace.get("trace_id")}, unknown, _outlines_by_page(records), mm_per_px)
    return sorted([index for index in range(count) if edges.get(index) == "external"]
                  + [index for index in unknown if index not in along])


def apply(web, project, root, read_page=None):
    """Place the text-layer windows of a dwelling on its traced rooms. Returns the saved result, or None for a shop."""
    root = Path(root)
    if not is_dwelling(root):
        return None
    from backend.autonomous_tasks_service import room_ceiling_height, _p0_main_geometry_pages
    paths = reviewer_room_geometry_service._paths({"review_dir": str(root)})
    from ai.autonomous_tasks import usable_calibration
    records = [row for row in reviewer_room_geometry_service.current_records(paths)
               if usable_calibration(row.get("calibration", {}).get("status"))
               and float(row.get("calibration", {}).get("mm_per_px") or 0) > 0]
    scopes = {row.get("room_id"): row.get("space_scope") for row in read_json(root / "room_use_resolution.json").get("records", [])
              if isinstance(row, dict)}
    plan_levels = {row["page"]: row.get("level_name") for row in _p0_main_geometry_pages(root)}
    invalid_traces = [row for row in records if not _same_plan_level(row, plan_levels)]
    cooled = [row for row in records if scopes.get(row.get("room_id")) in COOLED
              and _same_plan_level(row, plan_levels)]
    systems = {page.get("page"): page.get("coordinate_systems", {}).get("image_px", {})
               for page in read_json(root / "vector_geometry.json").get("geometry_key_points", {}).get("pages", [])}
    spatial_pages = {page.get("page"): page for page in read_json(root / "spatial_ocr.json").get("pages", [])}
    source = read_json(root / "ai_input.json").get("source_pdf")
    schedule_pages = _schedule_pages(root)
    inputs = {"version": VERSION, "pdf": str(source), "schedule_pages": schedule_pages,
              "plan_levels": plan_levels,
              "traces": [{key: row.get(key) for key in ("trace_id", "room_id", "page", "points_image_px", "calibration")}
                         for row in cooled],
              "scopes": scopes, "spatial_fingerprint": _fingerprint(spatial_pages)}
    if source and Path(source).exists():
        stat = Path(source).stat()
        inputs["pdf_version"] = [stat.st_size, stat.st_mtime_ns]
    fingerprint = _fingerprint(inputs)
    saved = read_json(root / RESULT_FILE)
    if saved.get("fingerprint") == fingerprint:
        return saved
    result = {"schema_version": 1, "version": VERSION, "fingerprint": fingerprint, "schedule_pages": schedule_pages,
              "windows_in_schedule": 0, "skylights": [], "doors_not_counted": [], "unread_schedule_rows": [],
              "placed": [], "assigned_without_wall": [], "unplaced": [], "walls_taken_as_outside": [],
              "invalid_traces": [{"room": row.get("room_label"), "page": row.get("page"),
                                  "level_name": row.get("level_name"), "plan_level": plan_levels.get(row.get("page")),
                                  "reason": "Room trace is assigned to a different level than its plan page."}
                                 for row in invalid_traces]}
    close = None
    any_saved = False
    if read_page is None:
        if not source or not Path(source).exists():
            result["blocked"] = "The source PDF isn't available; windows weren't read."
            result["coverage_status"] = "unresolved"
            reviewer_room_geometry_service._atomic_json(root / RESULT_FILE, result)
            return result
        close, read_page = _pdf_pages(source)
    try:
        windows = []
        for number in schedule_pages:
            _width, _height, words = read_page(number)
            schedules = house_windows.read_schedules(words, number)
            windows += schedules["windows"]
            result["skylights"] += [{key: row.get(key) for key in ("mark", "page", "width_mm", "height_mm")} for row in schedules["skylights"]]
            result["doors_not_counted"] += [{key: row.get(key) for key in ("mark", "page", "type")} for row in schedules["doors"]]
            result["unread_schedule_rows"] += [{key: row.get(key) for key in ("mark", "page", "reason")} for row in schedules["unread"]]
        result["windows_in_schedule"] = len(windows)
        if not windows:
            result["blocked"] = "No window schedule could be read. Glazing coverage is unresolved; inspect the window schedule or elevations."
            result["unplaced"].append({"reason": result["blocked"]})
        by_page = {}
        for trace in cooled:
            by_page.setdefault(trace.get("page"), []).append(trace)
        run_id = f"house-windows-{fingerprint[:12]}"
        # Every main plan, not only those with a traced cooled room: a floor with no outlines still has windows, which
        # go to their rooms by the nearest printed label below.
        for page in sorted({*by_page, *[number for number in plan_levels if isinstance(number, int)]}):
            traces = by_page.get(page, [])
            system = systems.get(page) or {}
            width_pt, height_pt, words = read_page(page)
            if not windows:
                break
            if not system.get("image_width") or not width_pt:
                result["unplaced"].append({"page": page, "reason": "No image frame for this plan page; its windows weren't placed."})
                continue
            sx, sy = system["image_width"] / width_pt, system["image_height"] / height_pt
            level = plan_levels.get(page) or (traces[0].get("level_name") if traces else None) or None
            flow = getattr(read_page, "flow", None)
            tags = house_windows.find_window_tags(words + (flow(page) if flow else []), page, level)
            rooms, scale = [], _page_scale(root, page, records, sx)
            placement_tags = [{**tag, "x": tag["x"] * sx, "y": tag["y"] * sy} for tag in tags]
            for trace in traces:
                mm_per_px = float(trace["calibration"]["mm_per_px"])
                rooms.append({"trace_id": trace["trace_id"], "points": trace["points_image_px"],
                              "outside_edges": _outside_edges(trace, records, mm_per_px),
                              "mm_per_point": mm_per_px,
                              "existing_openings": trace.get("openings", [])})
            if scale is None:
                result["unplaced"] += [{"mark": tag["mark"], "page": page,
                                        "reason": "The plan's scale isn't known, so the window can't be matched to a room."}
                                       for tag in tags]
                continue
            # Placement runs in image pixels (each room has its own mm per pixel); `scale` stays mm per PDF point for the
            # printed-label step below, whose tags and labels are in PDF points.
            placed = (house_windows.place_windows(placement_tags, windows, rooms, scale / sx) if rooms
                      else {"openings": [], "unplaced": []})
            result["unplaced"] += placed["unplaced"]
            traced_ids = {trace.get("room_id") for trace in records if trace.get("page") == page}
            labels = _untraced_labels(spatial_pages.get(page, {}), level or plan_levels.get(page) or "Unassigned level", traced_ids)
            placed_marks = {row["mark"].upper() for row in placed["openings"]}
            for tag in tags:
                if tag["mark"].upper() in placed_marks:
                    continue
                matching = [row for row in windows if row["mark"].upper() == tag["mark"].upper()]
                if len({(row["width_mm"], row["height_mm"]) for row in matching}) != 1 or not matching or not labels:
                    continue
                nearest = sorted(labels, key=lambda row: math.dist((tag["x"], tag["y"]), (row["x"], row["y"])))
                if not nearest[0]["eligible"]:
                    continue
                distance_mm = math.dist((tag["x"], tag["y"]), (nearest[0]["x"], nearest[0]["y"])) * scale
                if distance_mm > 9000 or (len(nearest) > 1 and
                    (math.dist((tag["x"], tag["y"]), (nearest[1]["x"], nearest[1]["y"])) * scale - distance_mm) < 500):
                    continue
                sill, head, note = house_windows.sill_and_head(matching[0])
                result["assigned_without_wall"].append({"mark": tag["mark"], "page": page,
                    "room": nearest[0]["label"], "level_name": nearest[0]["level_name"],
                    "schedule_page": matching[0]["page"], "width_m": matching[0]["width_mm"] / 1000,
                    "sill_height_m": sill / 1000, "head_height_m": head / 1000,
                    "frame_group": house_windows.frame_group(matching[0].get("type") or ""),
                    "assumptions": ["Nearest printed room label on the same plan; no host wall or azimuth was established.",
                                    *([note] if note else [])],
                    "evidence": f"Window schedule p{matching[0]['page']}: {matching[0]['evidence']}; "
                                f"plan p{page}: {nearest[0]['excerpt']}"})
            by_trace = {}
            for opening in placed["openings"]:
                by_trace.setdefault(opening["trace_id"], []).append(opening)
            for trace in traces:
                new = by_trace.get(trace["trace_id"], [])
                if not new and not any(row.get("declaration_source") == "drawing_text" for row in trace.get("openings", [])):
                    continue
                ceiling = room_ceiling_height(root, trace.get("room_id"), trace.get("room_label", ""), trace.get("level_name", ""))
                kept = []
                for opening in new:
                    if not isinstance(ceiling, (int, float)) or ceiling <= 0:
                        result["unplaced"].append({"mark": opening["mark"], "page": page,
                                                   "reason": "the room has no ceiling height yet"})
                    elif opening["head_height_m"] > ceiling / 1000 + 1e-9:
                        result["unplaced"].append({"mark": opening["mark"], "page": page,
                            "reason": f"head {opening['head_height_m']:.2f} m is above the room's ceiling {ceiling / 1000:.2f} m (double-height space?)"})
                    else:
                        kept.append(opening)
                edges = {row.get("index"): row.get("boundary", "unknown") for row in trace.get("edges", []) if isinstance(row, dict)}
                newly_outside = sorted({row["edge_index"] for row in kept if edges.get(row["edge_index"], "unknown") == "unknown"})
                body = {"action": "classify_envelope", "trace_id": trace["trace_id"], "reviewer": REVIEWER,
                        "edges": [{"index": index, "boundary": "external" if index in newly_outside else edges.get(index, "unknown")}
                                  for index in range(len(trace["points_image_px"]) - 1)],
                        "edge_sources": {str(index): "drawing_text" for index in newly_outside},
                        "roof": trace.get("roof", "unknown"),
                        "openings": [row for row in trace.get("openings", []) if row.get("declaration_source") != "drawing_text"] + [
                            {"opening_id": f"text-{row['mark']}-{trace['trace_id']}-{row['edge_index']}-{index}",
                             "edge_index": row["edge_index"], "width_m": row["width_m"],
                             "offset_m": row["offset_m"], "position_basis": row["position_basis"],
                             "sill_height_m": row["sill_height_m"],
                             "head_height_m": row["head_height_m"], "elevation_page": int(row["schedule_page"]),
                             "glazing_choice": GLAZING_CHOICE, "shading_category": SHADING, "mark": row["mark"],
                             **({"internal_shade": row["internal_shade"]} if row.get("internal_shade") else {}),
                             **({"frame_group": row["frame_group"]} if row.get("frame_group") else {}),
                             "declaration_source": "drawing_text", "ai_run_id": run_id, "evidence": [row["evidence"]],
                             "assumptions": row["assumptions"]}
                            for index, row in enumerate(kept)],
                        "declaration_source": "drawing_text", "ai_run_id": run_id}
                try:
                    reviewer_room_geometry_service.post(web, project, body, _defer_evidence_rebuild=True)
                    any_saved = True
                except ValueError as error:
                    result["unplaced"] += [{"mark": row["mark"], "page": page, "reason": f"not saved on the room: {error}"} for row in kept]
                    continue
                result["placed"] += [{"mark": row["mark"], "room": trace.get("room_label"), "trace_id": trace["trace_id"],
                                      "edge_index": row["edge_index"], "width_m": row["width_m"], "sill_height_m": row["sill_height_m"],
                                      "head_height_m": row["head_height_m"], "assumptions": row["assumptions"]} for row in kept]
                result["walls_taken_as_outside"] += [{"room": trace.get("room_label"), "edge_index": index,
                                                       "reason": "a window stands in this wall"} for index in newly_outside]
    finally:
        if close:
            close()
    placed_marks = {str(row.get("mark", "")).upper() for row in result["placed"]}
    # The same mark can be read on more than one plan page or by both PDF text orders. Once it is placed,
    # don't also count another reading of that mark as a failed opening.
    # Only readings on other sheets (a schedule page, an RCP) are dropped: a house reuses a mark for identical windows,
    # so an unplaced W1 on a plan is a real window still missing even when another W1 was placed.
    result["unplaced"] = [row for row in result["unplaced"]
                          if str(row.get("mark", "")).upper() not in placed_marks or row.get("page") in plan_levels]
    assigned_counts = {}
    for row in result["assigned_without_wall"]:
        mark = row["mark"].upper()
        assigned_counts[mark] = assigned_counts.get(mark, 0) + 1
    conflicted = {mark for mark, count in assigned_counts.items() if count > 1 or mark in placed_marks}
    if conflicted:
        result["assigned_without_wall"] = [row for row in result["assigned_without_wall"]
                                            if row["mark"].upper() not in conflicted]
        result["unplaced"] += [{"mark": mark, "reason": "Window tag has competing room or wall placements across plan pages."}
                                for mark in sorted(conflicted)]
    assigned_marks = {row["mark"].upper() for row in result["assigned_without_wall"]}
    result["unplaced"] = [row for row in result["unplaced"] if str(row.get("mark", "")).upper() not in assigned_marks]
    unplaced_marks = {str(row.get("mark", "")).upper() for row in result["unplaced"]}
    for window in windows:
        mark = str(window.get("mark", "")).upper()
        if mark and mark not in placed_marks and mark not in assigned_marks and mark not in unplaced_marks:
            result["unplaced"].append({"mark": window["mark"], "page": window["page"],
                "reason": "Window is listed in the schedule but is not matched to a traced room wall."})
            unplaced_marks.add(mark)
    if any_saved and web is not None:   # once, after every room's windows (a house has many rooms; each took a rebuild)
        from backend.phase1_supervisor_service import _build_calculation_evidence
        _build_calculation_evidence(web, project)
    result["coverage_status"] = ("unresolved" if not result["windows_in_schedule"] else
                                 "needs_review" if result["unplaced"] or result["invalid_traces"] or not result["placed"] else
                                 "provisional" if result["assigned_without_wall"] else "placed")
    reviewer_room_geometry_service._atomic_json(root / RESULT_FILE, result)
    return result
