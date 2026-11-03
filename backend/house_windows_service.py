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
from pathlib import Path

from ai import house_windows
from backend import reviewer_room_geometry_service
from backend.job_runner import read_json

RESULT_FILE = "house_windows.json"
VERSION = "house-windows-text-v1"
REVIEWER = "Archie drawing text (windows)"
GLAZING_CHOICE = "residential"      # preliminary pack glazing (U 5.8, SHGC 0.45) until certificates are read (W7)
SHADING = "unshaded"
COOLED = {"comfort_hvac", "comfort_hvac_with_process_exception"}


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
    from backend.autonomous_tasks_service import room_ceiling_height
    paths = reviewer_room_geometry_service._paths({"review_dir": str(root)})
    records = [row for row in reviewer_room_geometry_service.current_records(paths)
               if row.get("calibration", {}).get("status") in {"agreed", "declared_scale_rejected", "declared_scale_only"}
               and float(row.get("calibration", {}).get("mm_per_px") or 0) > 0]
    scopes = {row.get("room_id"): row.get("space_scope") for row in read_json(root / "room_use_resolution.json").get("records", [])
              if isinstance(row, dict)}
    cooled = [row for row in records if scopes.get(row.get("room_id")) in COOLED]
    systems = {page.get("page"): page.get("coordinate_systems", {}).get("image_px", {})
               for page in read_json(root / "vector_geometry.json").get("geometry_key_points", {}).get("pages", [])}
    source = read_json(root / "ai_input.json").get("source_pdf")
    schedule_pages = _schedule_pages(root)
    inputs = {"version": VERSION, "pdf": str(source), "schedule_pages": schedule_pages,
              "traces": [{key: row.get(key) for key in ("trace_id", "room_id", "page", "points_image_px", "calibration")}
                         for row in cooled],
              "scopes": scopes}
    if source and Path(source).exists():
        stat = Path(source).stat()
        inputs["pdf_version"] = [stat.st_size, stat.st_mtime_ns]
    fingerprint = _fingerprint(inputs)
    saved = read_json(root / RESULT_FILE)
    if saved.get("fingerprint") == fingerprint:
        return saved
    result = {"schema_version": 1, "version": VERSION, "fingerprint": fingerprint, "schedule_pages": schedule_pages,
              "windows_in_schedule": 0, "skylights": [], "doors_not_counted": [], "unread_schedule_rows": [],
              "placed": [], "unplaced": [], "walls_taken_as_outside": []}
    close = None
    if read_page is None:
        if not source or not Path(source).exists():
            result["blocked"] = "The source PDF isn't available; windows weren't read."
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
            result["unplaced"].append({"reason": "No window schedule could be read as text; windows need the drawing step."})
        by_page = {}
        for trace in cooled:
            by_page.setdefault(trace.get("page"), []).append(trace)
        run_id = f"house-windows-{fingerprint[:12]}"
        for page, traces in sorted(by_page.items()):
            system = systems.get(page) or {}
            width_pt, height_pt, words = read_page(page)
            if not windows:
                break
            if not system.get("image_width") or not width_pt:
                result["unplaced"].append({"page": page, "reason": "No image frame for this plan page; its windows weren't placed."})
                continue
            sx, sy = system["image_width"] / width_pt, system["image_height"] / height_pt
            level = traces[0].get("level_name") or None
            tags = house_windows.find_window_tags(words, page, level)
            rooms, scale = [], None
            for trace in traces:
                mm_per_px = float(trace["calibration"]["mm_per_px"])
                scale = mm_per_px * sx
                rooms.append({"trace_id": trace["trace_id"], "points": [[x / sx, y / sy] for x, y in trace["points_image_px"]],
                              "outside_edges": _outside_edges(trace, records, mm_per_px)})
            placed = house_windows.place_windows(tags, windows, rooms, scale)
            result["unplaced"] += placed["unplaced"]
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
                             "edge_index": row["edge_index"], "width_m": row["width_m"], "sill_height_m": row["sill_height_m"],
                             "head_height_m": row["head_height_m"], "elevation_page": int(row["schedule_page"]),
                             "glazing_choice": GLAZING_CHOICE, "shading_category": SHADING, "mark": row["mark"],
                             "declaration_source": "drawing_text", "ai_run_id": run_id, "evidence": [row["evidence"]],
                             "assumptions": row["assumptions"]}
                            for index, row in enumerate(kept)],
                        "declaration_source": "drawing_text", "ai_run_id": run_id}
                try:
                    reviewer_room_geometry_service.post(web, project, body)
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
    reviewer_room_geometry_service._atomic_json(root / RESULT_FILE, result)
    return result
