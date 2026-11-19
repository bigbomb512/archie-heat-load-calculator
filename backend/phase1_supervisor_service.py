"""Deterministic audit and bounded repair of the inputs used by the empty-room calculation."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid

from ai import house_envelope
from backend import ai_provider, autonomous_tasks_service as tasks, drawing_tasks_service as drawings
from backend.ai_preliminary_service import HOUSE_ROOF_NOT_LINED_UP
from backend.job_runner import BackgroundJob, JobCancelled, checkpoint, current_check, read_json, write_json

JOB_FILE = "phase1_supervisor_job.json"
MAX_CYCLES = 3
MAX_ATTEMPTS = 2
_JOB = BackgroundJob(JOB_FILE, [("audit", "Checking the empty-room model"),
    ("investigate", "Checking the drawings"), ("rebuild", "Updating the empty-room model")],
    "archie-phase1-supervisor", "Checking the empty-room model was interrupted. Resume to keep the saved drawing answers.")
COMFORT = {"comfort_hvac", "comfort_hvac_with_process_exception"}
SUCCESS = {"applied", "applied_fallback", "below_accuracy_bar"}
PHASE1_TASKS = {"P0_room_outlines", "P0_room_sizes", "P3_boundaries", "P3_surroundings", "P4_storefront_wall", "P4_openings", "P5_roof", "P7_ceiling_heights"}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _name(value):
    """Use drawing labels in operator copy, never a stored identity or path."""
    value = " ".join(str(value or "").split())
    if not value or re.search(r"room[-_:]|room-use:|[/\\]|\.json|P\d[_-]", value, re.I):
        return "the room"
    return value[:100]


def _key(value):
    return " ".join(str(value or "").casefold().split())


def room_display_name(use, records):
    """The room's name as people see it: its label ("Ensuite (2)" when one name is printed on separate rooms), with the
    level added when another level has a room of the same label ("Storage (Basement)", "Storage (First Floor)")."""
    label = " ".join(str(use.get("original_label") or use.get("label") or "").split())
    level = " ".join(str(use.get("level_name") or "").split())
    others = [row for row in records if row is not use and row.get("room_id") != use.get("room_id")
              and _key(row.get("original_label") or row.get("label")) == _key(label)]
    if others and level and level.casefold() != "unassigned level":
        from ai.page_inventory import level_display_name
        return f"{label} ({level_display_name(level)})"
    return label


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _task_current(record):
    packet = {key: value for key, value in (record.get("packet") or {}).items() if key != "prompt_fingerprint"}
    expected = tasks.autonomous_tasks.fingerprint({"packet": packet, "images": record.get("image_hashes", [])})
    return record.get("input_fingerprint") == expected


def _with_surroundings(traces, surroundings):
    """The traces as the calculation sees them: an "outside" wall the site plan shows faces more of the same building
    is a wall to the mall (2026-10-26), so a room whose zero load comes from that isn't flagged as a gap."""
    result = []
    for trace in traces:
        trace = deepcopy(trace)
        for edge in trace.get("edges", []) if isinstance(trace.get("edges"), list) else []:
            if (isinstance(edge, dict) and edge.get("boundary") == "external"
                    and (surroundings.get((str(trace.get("trace_id")), edge.get("index"))) or {}).get("inside")):
                edge["boundary"] = "mall"
        result.append(trace)
    return result


def load_snapshot(project, report=None):
    """Read/recompute current evidence without writing job data or calling a provider."""
    from backend import ai_preliminary_service as model, reviewer_room_geometry_service as geometry
    root = Path(project["review_dir"])
    paths = model._paths(project)
    traces = _with_surroundings(geometry.current_records(geometry._paths(project)), tasks.surroundings_by_edge(root))
    areas = geometry.current_traced_areas(root)
    uses = model._resolve_room_uses(paths, persist=False)
    heights = model._resolve_ceiling_volumes(paths, persist=False)
    inputs = read_json(paths["current_set"])
    current = bool(inputs) and not model._stale_reasons(paths, inputs)
    stored = report if report is not None else read_json(paths["base_report"])
    report_current = report is not None or (current and stored.get("phase1_input_fingerprint") == model._phase1_input_fingerprint(inputs))
    ai_input, spatial, _building = tasks._load_inputs(root)
    ceiling, views = tasks._ceiling_pages(ai_input, spatial)
    current_tasks = []
    for record in tasks._all_current(root):
        row = deepcopy(record)
        row["current"] = _task_current(record)
        current_tasks.append(row)
    rooms = []
    material_rooms = ((inputs.get("material") or {}).get("hourly_load_model") or {}).get("rooms", []) if current else []
    # Rooms are matched by their identity (level + label, numbered when one name is printed on separate rooms). A
    # label alone matches only when no other room on any level has it: Turramurra's Basement and First Floor Storage
    # rooms are two rooms, and the first floor's must not take the basement's outline (2026-11-18).
    records = [row for row in uses.get("records", []) if isinstance(row, dict)]
    label_count = {}
    for use in records:
        key = _key(use.get("original_label") or use.get("label"))
        label_count[key] = label_count.get(key, 0) + 1
    page_levels = {}

    def page_level(page):
        if type(page) is not int:
            return None
        if page not in page_levels:
            try:
                page_levels[page] = _key(tasks._page_level(root, page) or "Unassigned level")
            except (OSError, ValueError, TypeError, KeyError):
                page_levels[page] = None
        return page_levels[page]

    for use in records:
        label = use.get("original_label") or use.get("label") or ""
        room_id = use.get("room_id")
        level = use.get("level_name") or "Unassigned level"
        unique = label_count.get(_key(label), 0) <= 1

        def named(names, page=None):
            if not any(_key(label) == _key(value) for value in names or [] if isinstance(value, str)):
                return False
            return unique or (page_level(page) is not None and page_level(page) == _key(level))

        matching = [row for row in traces if row.get("room_id") == room_id]
        area = areas.get(room_id, {})
        height = next((row for row in heights.get("records", []) if row.get("room_id") == room_id), None)
        if height is None:
            height = next((row for row in heights.get("records", []) if unique and _key(row.get("original_label")) == _key(label)), {})
        material = next((row for row in material_rooms if row.get("source_room_id") == room_id), None)
        if material is None:     # an input set from before rooms carried their identity
            material = next((row for row in material_rooms if unique and not row.get("source_room_id")
                             and _key(row.get("name")) == _key(label)), {})
        room_tasks = [row for row in current_tasks if (row.get("packet") or {}).get("room_id") == room_id
                      or named((row.get("packet") or {}).get("room_names", []), (row.get("packet") or {}).get("page"))]
        rooms.append({"id": room_id, "name": label, "display_name": room_display_name(use, records),
            "level": level, "label_unique": unique, "scope": use.get("space_scope"),
            "use_status": use.get("status"), "traces": matching, "area_m2": area.get("area_m2") or sum(
                row.get("ai_measured_area_m2", 0) or 0 for row in matching),
            "area_source": area.get("area_origin") or area.get("method") or area.get("area_source") or " ".join(str(row.get("method", "")) for row in matching),
            "height": height, "model": material, "model_current": current, "tasks": room_tasks,
            "house_roof_not_lined_up": next((str(row.get("reason") or "") for row in inputs.get("phase1_assumptions") or []
                if material and isinstance(row, dict) and row.get("room_id") == material.get("room_id")
                and row.get("component") == HOUSE_ROOF_NOT_LINED_UP), ""),
            "pages": sorted({row.get("page") for row in matching if type(row.get("page")) is int}
                            | {row.get("page") for row in use.get("evidence", []) if type(row.get("page")) is int})})
    perimeter_sides = []
    for page in {trace.get("page") for trace in traces}:
        on_page = [trace for trace in traces if trace.get("page") == page]
        perimeter = tasks._page_perimeter_data(on_page)
        for trace in on_page:
            edges = {row.get("index"): row.get("boundary", "unknown") for row in trace.get("edges", [])}
            for mapping in perimeter.get("trace_edges", {}).get(trace.get("trace_id"), []):
                if mapping.get("perimeter_run_index") is not None:
                    perimeter_sides.append(edges.get(mapping["edge_index"], "unknown"))
    dwelling = (uses.get("dwelling_context") or {}).get("is_dwelling") is True
    return {"rooms": rooms, "tasks": current_tasks, "traces": traces, "dwelling": dwelling,
        # No outside-walls question exists for such a house: its unclassified walls are set by rule (ai/house_envelope.py).
        "house_walls_by_rule": dwelling and tasks._house_without_shared_walls(root),
        "storefront_pages": [row["page"] for row in tasks._storefront_elevation_pages(ai_input)],
        "height_pages": [row["page"] for row, _labels in [*ceiling, *views]], "perimeter_sides": perimeter_sides,
        "report": stored if report_current else {}, "input_current": current,
        "model_inputs": [{"name": row.get("name"), "area_m2": row.get("area_m2"),
            "envelope": (row.get("cooling_load") or {}).get("envelope_surfaces"),
            "glazing": (row.get("cooling_load") or {}).get("glazing_surfaces")} for row in material_rooms]}


def model_fingerprint(snapshot):
    """Only physical inputs; new citations, run IDs and timestamps never count as progress."""
    surface_keys = ("kind", "area_m2", "orientation", "boundary_condition", "boundary_method", "boundary_temperature_c",
                    "u_value_w_m2k", "shgc", "solar_heat_gain_coefficient", "solar_design_w_m2", "solar_gain_factor", "shading_factor")
    opening_keys = ("edge_index", "width_m", "sill_height_m", "head_height_m", "glazing_choice", "shading_category")
    inputs = []
    for row in snapshot.get("model_inputs", []):
        if not isinstance(row, dict):
            inputs.append(row)
            continue
        inputs.append({"name": row.get("name"), "area_m2": row.get("area_m2"), **{
            name: [{key: surface.get(key) for key in surface_keys} for surface in row.get(name) or []]
            for name in ("envelope", "glazing")}})
    return _fingerprint({"rooms": [{key: room.get(key) for key in ("id", "scope", "area_m2")}
        | {"height": room.get("height", {}).get("ceiling_height_mm"),
           "traces": [{"page": trace.get("page"), "points": trace.get("points_image_px"), "roof": trace.get("roof"),
                       "edges": [{key: edge.get(key) for key in ("index", "boundary")} for edge in trace.get("edges", [])],
                       "openings": [{key: opening.get(key) for key in opening_keys} for opening in trace.get("openings", [])]}
                      for trace in room.get("traces", [])]} for room in snapshot.get("rooms", [])], "inputs": inputs})


def _boundaries(traces):
    return [row.get("boundary", "unknown") for trace in traces for row in trace.get("edges", []) if isinstance(row, dict)]


def _has_glass(room):
    """The room's storefront glass is in: in the model, or placed on a wall the model gives no load by rule (a mall or
    internal wall: Butcher Buffet's storefront faces the mall, 2026-10-21, so its glass correctly adds nothing). Glass
    placed on an outside wall but missing from a current model is still a gap."""
    cooling = (room.get("model") or {}).get("cooling_load") or {}
    model_has_glass = bool(cooling.get("glazing_surfaces") or any(
        row.get("kind") in {"window", "glazing", "glazed_door"} and _positive(row.get("area_m2"))
        for row in cooling.get("envelope_surfaces", [])))
    if model_has_glass:
        return True
    for trace in room.get("traces", []):
        sides = {row.get("index"): row.get("boundary", "unknown") for row in trace.get("edges", []) if isinstance(row, dict)}
        for opening in trace.get("openings", []) or []:
            if not room.get("model_current") or sides.get(opening.get("edge_index")) in {"mall", "internal", "adjacent_tenancy"}:
                return True
    return False


def _exposed(room):
    """The room has an outside wall, an exposed roof or glass: its empty-room load can't be zero."""
    traces = room.get("traces", [])
    return (any(side == "external" for side in _boundaries(traces))
            or any(trace.get("roof") == "exposed" for trace in traces) or _has_outside_glass(room))


def _has_outside_glass(room):
    """Glass that loads the room: in the model, or placed on a wall that isn't to a mall, room or tenancy. Shopfront
    glass facing a mall adds nothing by rule (2026-10-15), so it doesn't make a 0 kW room suspicious (Butcher Buffet's
    Kitchen, 2026-10-26)."""
    cooling = (room.get("model") or {}).get("cooling_load") or {}
    if cooling.get("glazing_surfaces"):
        return True
    for trace in room.get("traces", []):
        sides = {row.get("index"): row.get("boundary", "unknown") for row in trace.get("edges", []) if isinstance(row, dict)}
        if any(sides.get(opening.get("edge_index")) not in {"mall", "internal", "adjacent_tenancy"}
               for opening in trace.get("openings", []) or []):
            return True
    return False


def audit(snapshot):
    """Rank material gaps in the real model. Ordinary skill unresolved_fields are intentionally ignored."""
    gaps = []
    storefront = snapshot.get("storefront_pages", [])
    height_pages = snapshot.get("height_pages", [])
    rooms = [row for row in snapshot.get("rooms", []) if row.get("scope") in COMFORT]
    records = snapshot.get("tasks", [])
    tenancy_sides = snapshot.get("perimeter_sides") or _boundaries([trace for room in rooms for trace in room.get("traces", [])])
    all_internal = bool(tenancy_sides) and all(side in {"internal", "adjacent_tenancy"} for side in tenancy_sides)

    def add(rank, kind, room, task, sentence, pages=None, target=None):
        label = _name(room.get("display_name") or room.get("name"))
        plan_page = next((trace.get("page") for trace in room.get("traces", []) if trace.get("page")), None)
        pages = sorted(set(page for page in (pages or room.get("pages", [])) if type(page) is int))
        identity = f"{kind}:{room.get('id') or _key(label)}" + (f":{task}:{target or ''}" if kind == "unanswered" else "")
        gaps.append({"id": identity, "rank": rank, "kind": kind, "room_id": room.get("id"), "room": label,
            "task": task, "target": target, "plan_page": plan_page, "pages": pages, "message": sentence,
            "note": sentence + (" Check drawing pages " + ", ".join(map(str, pages)) + "." if pages else " Check the current drawing set.")})

    if not snapshot.get("rooms"):
        add(1, "room_use", {"id": "project", "name": "the rooms", "pages": []}, "room_identity_use",
            "Find the cooled rooms named on the drawings; none is represented in the empty-room model.")
    for candidate in snapshot.get("rooms", []):
        if candidate.get("scope") in {None, "", "unresolved"}:
            add(1, "room_use", candidate, "room_identity_use",
                f"Check how {_name(candidate.get('name'))} is used so the model can resolve whether it is cooled.")

    # Floor no traced room covers: the check for it is per plan page, not per room (2026-10-24: Casa Nova's Dining
    # answer was refused and nothing reported the untraced dining area).
    for record in records:
        if record.get("status") == "not_needed":
            continue
        if record.get("task") == "P0_unnamed_space" and (not record.get("current", True)
                                                          or record.get("status") in {"failed", "blocked", "waiting_for_reply"}):
            page = (record.get("packet") or {}).get("page")
            add(1, "missing_room", {"id": f"floor:{record.get('target')}", "name": "the untraced floor",
                                    "pages": [page] if type(page) is int else []},
                "P0_unnamed_space", f"Check plan page {page} for floor that no traced room covers (drawings often leave "
                "the main dining or sales area unlabelled); that check wasn't answered.", target=record.get("target"))

    def same_room(room, value):
        """A drawing check's room name is this room's when the label is unique, or the check names it by identity."""
        return _key(value) == _key(room.get("name")) and room.get("label_unique", True)

    for room in rooms:
        label = _name(room.get("display_name") or room.get("name"))
        traces = room.get("traces", [])
        area = room.get("area_m2")
        # An area-only room's source is the room-size step's wording ("AI estimate from the plan", "tenancy area
        # remainder": autonomous_tasks_service.ROOM_SIZE_SOURCES), not its method name; both are last-resort areas.
        if not _positive(area) or any(term in str(room.get("area_source", "")).casefold()
                                      for term in ("room_size_estimated", "room_size_remainder", "last_resort",
                                                   "generic_profile", "profile_default",
                                                   tasks.ROOM_SIZE_SOURCES["estimated"].casefold(),
                                                   tasks.ROOM_SIZE_SOURCES["remainder"].casefold())):
            add(1, "room_area", room, "P0_room_outlines", f"Check {label}'s outline and area; its area is missing or only a last-resort estimate.")
        if room.get("use_status") in {"unresolved", "conflict", "blocked", "needs_review", "stale"}:
            add(1, "room_use", room, "room_identity_use", f"Check how {label} is used so its empty-room calculation includes the right space.")
        related_pages = {trace.get("page") for trace in traces}
        for record in records:
            if record.get("status") == "not_needed" or record.get("task") not in PHASE1_TASKS:
                continue
            packet = record.get("packet") or {}
            related = (record in room.get("tasks", []) or packet.get("room_id") == room.get("id")
                       or (packet.get("page") in related_pages and record.get("task") in {"P3_boundaries", "P3_surroundings", "P4_storefront_wall"})
                       or any(same_room(room, value) for value in packet.get("rooms", []) if isinstance(value, str)))
            if related and (not record.get("current", True) or record.get("status") in {"failed", "blocked", "waiting_for_reply"}):
                add(2, "unanswered", room, record["task"], f"Finish the unanswered drawing check for {label}: {drawings.TASK_LABELS.get(record['task'], 'checking the room').lower()}.",
                    pages=([packet["page"]] if type(packet.get("page")) is int else None), target=record.get("target"))
        sides = _boundaries(traces)
        if storefront and any(side in {"external", "mall"} for side in sides) and not _has_glass(room):
            opening = next((row for row in records if row.get("task") == "P4_openings" and (
                (row.get("packet") or {}).get("room_id") == room.get("id") or same_room(room, (row.get("packet") or {}).get("room_label")))), None)
            add(3, "storefront_glass", room, "P4_openings" if opening else "P4_storefront_wall",
                f"Read {label}'s shopfront glass panel by panel from the elevation; the empty-room model needs its glazed widths.",
                pages=storefront, target=opening.get("target") if opening else None)
        if height_pages and room.get("height", {}).get("origin") == "preliminary_fallback":
            add(4, "ceiling_height", room, "P7_ceiling_heights", f"Check {label}'s ceiling height on the labelled ceiling plan, elevation or section.", pages=height_pages)
        if storefront and sides and all_internal:
            add(5, "internal_perimeter", room, "P3_boundaries", f"Check {label}'s perimeter: its walls all face other rooms or tenancies although a shopfront elevation exists.", pages=storefront + room.get("pages", []))
        # A house that shares no wall with another dwelling is never asked what its walls face: an unclassified wall is
        # outside unless a traced room is beyond it (user decision 2026-10-30), a labelled assumption of the result. Job
        # A's five "unknown walls" checks had no question to ask and ended "unavailable" (2026-11-19).
        if any(side == "unknown" for side in sides) and not snapshot.get("house_walls_by_rule"):
            add(6, "unknown_walls", room, "P3_boundaries", f"Check what is on the other side of {label}'s unknown walls.")
        # In a house what is over a room follows from its level (the top storey is under the roof, a lower one under the
        # storey above: ai_preliminary_service._house_level_roof), and no roof question is asked (Turramurra, 2026-11-18:
        # every room was flagged and sent a tenancy roof check that a house never gets).
        house_level = snapshot.get("dwelling") and house_envelope.level_rank(room.get("level")) is not None
        if (traces and not house_level
                and any(trace.get("roof") in {None, "", "unknown"} or trace.get("roof_source") == "ai_fallback" for trace in traces)):
            add(6, "roof", room, "P5_roof", f"Check what is above {label}; its roof or ceiling exposure is unknown or assumed.")
        elif house_level and room.get("house_roof_not_lined_up"):
            # The plans couldn't settle what is over it (roof or the storey above): said, not guessed (2026-11-18).
            note = room["house_roof_not_lined_up"]
            why = note.split(" — ", 1)[1].split(". Whether", 1)[0] if " — " in note else ""
            add(6, "roof", room, "P5_roof", f"Check what is above {label} — the roof plan couldn't be lined up with the "
                f"{room.get('level') or 'room'}'s plan" + (f" ({why})" if why else "") + "; it is taken as under the storey above.")

    report = snapshot.get("report") or {}
    if report:
        for room in rooms:
            label = _name(room.get("display_name") or room.get("name"))
            name = room.get("name")       # the model's and the report's room name is the room's label
            model_id = (room.get("model") or {}).get("room_id")
            coverage = report.get("phase1_coverage") or {}
            omitted = any(_key(row.get("label")) == _key(name) for row in coverage.get("rooms_left_out", []))
            if "rooms_included" in coverage and _key(name) not in {_key(value) for value in coverage["rooms_included"]}:
                omitted = True
            results = [row for scenario in report.get("scenario_results", []) for row in scenario.get("rooms", [])
                       if ((row.get("room_id") == model_id) if model_id
                           else (room.get("label_unique", True) and _key(row.get("name")) == _key(name)))]
            zero = bool(results) and all(isinstance((row.get("peak") or {}).get("total_kw"), (int, float))
                and abs(row["peak"]["total_kw"]) < 1e-9 for row in results)
            surfaces = [row for row in report.get("phase1_surface_audit", [])
                        if ((row.get("room_id") == model_id) if model_id and row.get("room_id")
                            else (room.get("label_unique", True) and _key(row.get("room_name")) == _key(name)))]
            if not results and surfaces:
                zero = all(isinstance(row.get("total_kw"), (int, float)) and abs(row["total_kw"]) < 1e-9 for row in surfaces)
            # A zero is only suspicious when the room has something that should load it: an outside wall, an exposed
            # roof or shopfront glass. Walls to conditioned rooms, other tenancies and a mall are zero by rule
            # (2026-10-15), so an internal room's zero is the right answer, not a gap.
            if zero and not omitted and not _exposed(room):
                continue
            if omitted or zero:
                # A room with no outline has no walls to check yet: its outline comes first (Job A's Kitchen, 2026-11-19).
                add(7, "omitted_room" if omitted else "zero_load", room,
                    "P3_boundaries" if room.get("traces") else "P0_room_outlines",
                    f"Check {label}'s envelope: it was left out of the empty-room result." if omitted else f"Check {label}'s envelope: its calculated load is zero, so verify every boundary and opening.")
        peak = (report.get("included_scope_peak") or {}).get("total_kw")
        if (rooms and any(_exposed(room) for room in rooms) and isinstance(peak, (float, int)) and abs(peak) < 1e-9
                and not any(row["kind"] == "zero_load" for row in gaps)):
            add(7, "zero_tenancy", rooms[0], "P3_boundaries", "Check the tenancy envelope: its total empty-room load is zero.")
    return sorted({row["id"]: row for row in gaps}.values(), key=lambda row: (row["rank"], _key(row["room"]), row["id"]))


def investigation_text(gap):
    pages = gap.get("pages", [])
    where = f" (page{'' if len(pages) == 1 else 's'} {', '.join(map(str, pages))})" if pages else ""
    return gap["message"].rstrip(".") + where + "."


def select_investigations(gaps, attempts):
    """Queue every eligible material gap, ranked; concurrency bounds execution, never the queue length."""
    eligible = [gap for gap in gaps if attempts.get(gap["id"], 0) < MAX_ATTEMPTS]
    return sorted(eligible, key=lambda gap: (gap.get("rank", 99), 0 if gap.get("kind") == "room_area" else 1,
                                             _key(gap.get("room")), gap["id"]))


def _investigation_wave(task):
    # Rebuild between dependent tasks: a corrected outline must reach the wall packet,
    # and printed heights must reach the storefront opening packet before it is asked.
    if task.startswith("P0_") or task == "room_identity_use":
        return 0
    if task == "P3_boundaries":
        return 1
    if task in {"P5_roof", "P7_ceiling_heights"}:
        return 2
    return 3 if task == "P4_storefront_wall" else 4


def group_investigations(gaps):
    """Combine checks sharing one packet before creating runs, so one reply cannot supersede a sibling."""
    grouped = {}
    for gap in gaps:
        task = gap["task"]
        target = gap.get("target")
        plan = gap.get("plan_page")
        if task in {"P3_boundaries", "P4_storefront_wall"}:
            match = re.search(r"page-(\d+)", target or "")
            identity = int(match.group(1)) if match else plan
        elif task in {"P0_room_outlines", "P0_room_sizes"}:
            identity = plan or next(iter(gap.get("pages", [])), None)
        elif task in {"P7_ceiling_heights", "room_identity_use"}:
            identity = task
        else:
            identity = target or gap.get("room_id")
        key = task, identity
        if key not in grouped:
            grouped[key] = {**deepcopy(gap), "gap_ids": [], "room_names": [], "notes": [], "kinds": []}
            if task in {"P7_ceiling_heights", "P0_room_outlines", "P0_room_sizes"}:
                grouped[key]["target"] = None
        item = grouped[key]
        item["gap_ids"].append(gap["id"])
        item["room_names"] = sorted(set([*item["room_names"], gap["room"]]))
        item["pages"] = sorted(set([*item["pages"], *gap["pages"]]))
        item["notes"].append(gap["note"])
        item["kinds"].append(gap["kind"])
        # Worded as what to look at, never as a finding: an AI read "no applied shopfront glass" as a fact about the
        # drawing and excluded the whole shopfront (Casa Nova, 2026-10-24).
        descriptions = {"room_area": "the room outline and area", "room_use": "how the room is used",
            "unanswered": "the drawing check that wasn't answered", "storefront_glass": "the shopfront glass, panel by panel, on the elevation",
            "ceiling_height": "the ceiling height printed on the drawings",
            "internal_perimeter": "which walls face outside (a shopfront elevation exists)", "unknown_walls": "what each unclassified wall faces",
            "roof": "what is above the room", "zero_load": "whether the room has an outside wall, roof or glass",
            "zero_tenancy": "whether the tenancy has an outside wall, roof or glass",
            "omitted_room": "why the room is missing from the empty-room result",
            "missing_room": "floor that no traced room covers"}
        item["note"] = ("For " + ", ".join(item["room_names"]) + ", check "
            + "; ".join(descriptions[kind] for kind in dict.fromkeys(item["kinds"])) + "."
            + (" Start with drawing pages " + ", ".join(map(str, item["pages"])) + "." if item["pages"] else ""))
    return list(grouped.values())


def _usage(root):
    rows = []
    path = Path(root) / ai_provider.USAGE_FILE
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("purpose", "")).startswith("supervisor:"):
                rows.append(row)
    return {"calls": len(rows), "cost_usd": round(sum(row.get("cost_usd") or 0 for row in rows), 6),
            "unpriced_calls": sum(1 for row in rows if row.get("cost_usd") is None)}


def _rebuild(web, project):
    """Reassemble and locally calculate a preview; the normal calculation job publishes after checks settle."""
    from backend import ai_preliminary_service as model, skill_workflow_service as skills
    checkpoint()
    from backend import reviewer_room_geometry_service
    if reviewer_room_geometry_service.split_repeated_room_traces(project["review_dir"]):
        _build_calculation_evidence(web, project)     # separate same-named rooms in an older job: new room identities
    paths = model._paths(project)
    inputs = read_json(paths["current_set"])
    if not inputs or model._stale_reasons(paths, inputs):
        inputs = model._assemble(web, project, source="phase1_model_check")
        manifest = read_json(Path(project["review_dir"]) / "skill_workflow_run.json")
        skills.take_in_downstream(project, manifest.get("run_id"))
    report = read_json(paths["base_report"])
    if not report or report.get("phase1_input_fingerprint") != model._phase1_input_fingerprint(inputs):
        try:
            report = model._calculate(web, project, scope="phase1_base_room", persist=False)
        except ValueError:
            report = None
    return load_snapshot(project, report=report)


def room_areas(snapshot):
    """{room id: {"name", "area_m2"}} for the rooms of a snapshot (area rounded to 0.1 m², None when it has none)."""
    result = {}
    for room in snapshot.get("rooms", []):
        if not room.get("id"):
            continue
        area = room.get("area_m2")
        result[str(room["id"])] = {"name": room.get("name") or "", "area_m2": round(float(area), 1) if _positive(area) else None}
    return result


def area_changes(before, after):
    """Rooms whose area this check changed: [{"room", "before_m2", "after_m2"}]. A difference under 0.1 m² is rounding."""
    changes = []
    for room_id, start in sorted((before or {}).items(), key=lambda item: str(item[1].get("name", ""))):
        now = (after or {}).get(room_id)
        if not now or start.get("area_m2") == now.get("area_m2"):
            continue
        if start.get("area_m2") is not None and now.get("area_m2") is not None and abs(start["area_m2"] - now["area_m2"]) < 0.1:
            continue
        changes.append({"room": now.get("name") or start.get("name"), "before_m2": start.get("area_m2"), "after_m2": now.get("area_m2")})
    return changes


def _take_in_review(project):
    """The fixes this check applied (traced rooms, wall faces, room uses) refine the drawing review's answers; they
    are downstream of it, so they must not make "Review room names and site" look out of date. Changes that don't
    come through this check (a new PDF, other pages, new page readings or instructions) still do."""
    from backend import skill_workflow_service as skills
    manifest = read_json(Path(project["review_dir"]) / "skill_workflow_run.json")
    skills.take_in_downstream(project, manifest.get("run_id"))


def _room_use_investigation(web, project, gap):
    """Apply a validated answer only to the rooms named by this investigation."""
    from backend import skill_workflow_service as skills
    root = Path(project["review_dir"])
    manifest = read_json(root / "skill_workflow_run.json")
    registry = skills.load_subskill_registry()
    spec = deepcopy(next(row for row in registry["subskills"] if row["id"] == "room_identity_use"))
    dependencies_run_id = manifest["run_id"]
    directory = root / "skill_workflow_runs" / manifest["run_id"] / "proposals"
    dependencies = {sid: {"proposal": read_json(directory / f"{sid}.json")} for sid in spec.get("depends_on", [])}
    spec["_usage_purpose"] = "supervisor:room_identity_use"
    result, attempt = {}, {}
    prior_calls = _usage(root)["calls"]

    def record_unlogged_attempt(ok, error=""):
        raw = attempt.get("raw_record", {}) or {}
        if (not raw or raw.get("recovered_reply") or str(raw.get("provider", "")).startswith("none")
                or _usage(root)["calls"] != prior_calls):
            return
        ai_provider.record_usage(root, {"at": ai_provider._now(), "purpose": "supervisor:room_identity_use",
            "provider": raw.get("provider", ""), "model": raw.get("model", ""), "ok": ok,
            "seconds": raw.get("seconds", 0), "error": error, **(raw.get("usage") or {})})

    previous = read_json(directory / "room_identity_use.json")
    target_ids = {gap_id.removeprefix("room_use:") for gap_id in gap.get("gap_ids", [])
                  if gap_id.startswith("room_use:")}
    target_ids.discard("project")
    if not target_ids and gap.get("room_id") not in (None, "project"):
        target_ids = {gap["room_id"]}
    try:
        result = skills._execute_subskill(spec, project, dependencies, manifest["source_fingerprint"],
            {"question": gap["note"] + (" Answer only for these room IDs: " + ", ".join(sorted(target_ids)) + "."
                                           if target_ids else " Return the complete room list, preserving every already evidenced room.")})
        attempt = result.pop("_attempt_record", {})
        result = skills._validate_subskill_output(spec, result, registry,
            allowed_pages=skills._consented_page_ids(skills._project_paths(project)))
        old_rows = previous.get("proposal_fields", {}).get("rooms", [])
        new_rows = result.get("proposal_fields", {}).get("rooms", [])
        prior = {row["room_id"]: row for row in old_rows if row.get("room_id")}
        current = {row["room_id"]: row for row in new_rows if row.get("room_id")}
        if len(current) != len(new_rows):
            raise ValueError("The room-use answer has missing or duplicate room IDs.")
        if target_ids:
            if not target_ids <= current.keys():
                raise ValueError("The targeted answer omitted a requested room; the existing room list was kept.")
            # Only the asked-about rooms are taken from the answer. Anything it says about other rooms (a reply that
            # repeats the whole list, with small differences) is ignored rather than throwing the answer away.
            result["affected_ids"] = [room_id for room_id in result.get("affected_ids", []) if room_id in target_ids]
            result["proposal_fields"]["rooms"] = [current[row["room_id"]] if row.get("room_id") in target_ids else row
                                                  for row in old_rows]
            result["proposal_fields"]["rooms"].extend(current[room_id] for room_id in sorted(target_ids - prior.keys()))
            for key in ("affected_ids", "observations", "inferences", "citations", "alternatives", "unresolved_fields", "remediation"):
                result[key] = list(previous.get(key, [])) + list(result.get(key, []))
        elif not prior.keys() <= current.keys():
            raise ValueError("The room-use answer omitted previously evidenced rooms; the existing room list was kept.")
    except Exception as error:
        attempt = getattr(error, "attempt_record", attempt)
        record_unlogged_attempt(False, str(error)[:300])
        if result.get("input_fingerprint"):
            (root / "skill_reply_cache" / f"{result['input_fingerprint']}.json").unlink(missing_ok=True)
        skills._persist_skill_attempt(root, manifest["run_id"], spec["id"], attempt_record=attempt,
            input_fingerprint=result.get("input_fingerprint", ""), outcome="rejected",
            validation={"check": "room_list_incomplete", "detail": str(error)})
        raise
    record_unlogged_attempt(True)
    checkpoint()
    from backend.job_progress import stage_blocker
    if stage_blocker(project, "supervisor") or read_json(root / "skill_workflow_run.json").get("run_id") != manifest["run_id"]:
        raise JobCancelled()
    skills._persist_skill_attempt(root, manifest["run_id"], spec["id"], attempt_record=attempt,
        input_fingerprint=result.get("input_fingerprint", ""), outcome="accepted")
    # The same lock as the review's own writers: read the run file fresh inside it, then update this one step.
    with skills._project_lock(project["id"]):
        manifest = read_json(root / "skill_workflow_run.json")
        if manifest.get("run_id") != dependencies_run_id:
            raise JobCancelled()
        write_json(directory / "room_identity_use.json", result)
        manifest["subskills"][spec["id"]].update(status=result["status"], input_fingerprint=result["input_fingerprint"])
        write_json(root / "skill_workflow_run.json", manifest)
    skills._auto_review_confident_findings(root, manifest)
    return {"status": "applied"}


def _build_calculation_evidence(web, project):
    """Rebuild the calculation inputs once after a batch of applied answers (each answer used to rebuild them itself).
    Before the PDF has been analysed there is nothing to build."""
    from backend import calculation_extraction_service
    try:
        calculation_extraction_service.post(web, project, {"action": "build"})
    except ValueError as error:
        if "Analyse the architect PDF" not in str(error):
            raise


def run(web, project, step=lambda *args: None):
    root = Path(project["review_dir"])
    saved = read_json(root / JOB_FILE)
    durable = read_json(root / "phase1_supervisor_state.json")
    if durable.get("supervision_id") == saved.get("supervision_id"):
        saved = {**saved, **durable}
    attempts, history = deepcopy(saved.get("attempts", {})), deepcopy(saved.get("history", []))
    resolved = set(saved.get("resolved_gap_ids", []))
    cycle = int(saved.get("cycle") or 0)
    failed_inputs = deepcopy(saved.get("failed_inputs", {}))
    cycle_timings = deepcopy(saved.get("cycle_timings", []))
    snapshot = _rebuild(web, project)
    gaps = audit(snapshot)
    initial_ids = {row["id"] for row in gaps}
    check = current_check()
    # Each room's area when this check started (kept across a resume), so a changed area is said, not silent.
    baseline = saved.get("area_baseline") if isinstance(saved.get("area_baseline"), dict) else room_areas(snapshot)

    def progress(**extra):
        checkpoint()
        state = read_json(root / JOB_FILE)
        state.update(attempts=attempts, history=history, cycle=cycle, max_cycles=MAX_CYCLES,
                     gaps_found=len(initial_ids | set(attempts) | {gap["id"] for gap in gaps}), gaps_fixed=len(resolved),
                     resolved_gap_ids=sorted(resolved), gaps=deepcopy(gaps), spend=_usage(root),
                     area_baseline=baseline, area_changes=area_changes(baseline, room_areas(snapshot)),
                     failed_inputs=failed_inputs, cycle_timings=cycle_timings, **extra)
        write_json(root / JOB_FILE, state)
        write_json(root / "phase1_supervisor_state.json", {key: state[key] for key in (
            "supervision_id", "attempts", "history", "cycle", "resolved_gap_ids", "exceptions", "model_fingerprint",
            "area_baseline") if key in state})

    unavailable = ai_unavailable(project)
    progress(stop_reason="", not_asked=unavailable)
    while cycle < MAX_CYCLES and not unavailable:
        checkpoint()
        selected = select_investigations(gaps, attempts)
        current_fp = model_fingerprint(snapshot)
        selected = [gap for gap in selected if failed_inputs.get(gap["id"]) != current_fp]
        if not selected:
            break
        cycle += 1
        cycle_started = time.monotonic()
        ai_seconds = rebuild_seconds = audit_seconds = 0.0
        before = model_fingerprint(snapshot), {row["id"] for row in gaps}
        changed = False
        step("audit", "Checking room areas, boundaries, glass and ceiling heights")
        for wave in range(5):
            selected = [gap for gap in select_investigations(gaps, attempts) if _investigation_wave(gap["task"]) == wave]
            if not selected:
                continue
            prepared, skills_to_run = {}, []
            for gap in selected:
                attempts[gap["id"]] = attempts.get(gap["id"], 0) + 1
            progress()
            for gap in group_investigations(selected):
                checkpoint()
                gap["previous_targets"] = [row["target"] for row in history if row.get("target")
                    and row.get("gap_id") in gap["gap_ids"] and row.get("status") in SUCCESS]
                try:
                    if gap["task"] == "room_identity_use":
                        skills_to_run.append(gap)
                        continue
                    try:
                        records = tasks.prepare_investigation(project, gap)
                    except ValueError as outline_error:
                        if gap["task"] != "P0_room_outlines":
                            raise
                        try:
                            records = tasks.prepare_investigation(project, {**gap, "task": "P0_room_sizes"})
                        except ValueError as size_error:
                            # Both reasons: the outline's names the prerequisite that held it back (2026-11-19).
                            raise ValueError(f"{outline_error} {size_error}") from size_error
                    for record in records:
                        if record.get("status") == "blocked":
                            raise ValueError(record.get("block_reason") or "This drawing check needs earlier evidence.")
                        key = (record["task"], record["target"])
                        if key in prepared:
                            _earlier, earlier_gap = prepared.pop(key)
                            gap = {**earlier_gap, "target": record["target"],
                                   "gap_ids": sorted(set([*earlier_gap["gap_ids"], *gap["gap_ids"]])),
                                   "room_names": sorted(set([*earlier_gap["room_names"], *gap["room_names"]])),
                                   "note": earlier_gap["note"] + " " + gap["note"]}
                            record = tasks.prepare_investigation(project, gap)[0]
                        prepared[key] = (record, gap)
                except Exception as error:
                    history.extend({"gap_id": gap_id, "cycle": cycle, "outcome": "unavailable", "reason": str(error)[:500]}
                                   for gap_id in gap["gap_ids"])
            # Multiple gaps for the same drawing task must not replace one another's current run.
            # Prepare all network work first, then apply the validated replies serially.
            active = {key: {"message": investigation_text(gap), "pages": gap["pages"],
                            "label": drawings.TASK_LABELS.get(_record["task"], "Drawing specialist"),
                            "started_at": time.time()} for key, (_record, gap) in prepared.items()}
            progress(active_investigations=list(active.values()))
            if active:
                step("investigate", next(iter(active.values()))["message"])
            if prepared:
                ai_started = time.monotonic()
                with ThreadPoolExecutor(max_workers=min(4, ai_provider.parallel_calls())) as pool:
                    futures = {pool.submit(drawings.request_investigation, project, record, check=check): (record, gap)
                               for record, gap in prepared.values()}
                    for future in as_completed(futures):
                        checkpoint()
                        record, gap = futures[future]
                        from backend.job_progress import stage_blocker
                        if stage_blocker(project, "supervisor"):
                            raise JobCancelled()
                        try:
                            result = future.result()
                            outcome = drawings.apply_investigation(web, project, record, result)
                        except Exception as error:
                            outcome = drawings.archive_investigation_failure(project, record, error) or {
                                "status": "superseded", "reason": "The drawing inputs changed while this check was running."}
                        changed = changed or outcome.get("status") in SUCCESS
                        if outcome.get("status") not in SUCCESS:
                            failed_inputs.update({gap_id: current_fp for gap_id in gap["gap_ids"]})
                        history.extend({"gap_id": gap_id, "task": record["task"], "target": record["target"],
                                        "cycle": cycle, "run_id": record["run_id"], **outcome} for gap_id in gap["gap_ids"])
                        active.pop((record["task"], record["target"]), None)
                        progress(active_investigations=list(active.values()))
                        if active:
                            step("investigate", next(iter(active.values()))["message"])
                ai_seconds += time.monotonic() - ai_started
            for gap in skills_to_run:
                checkpoint()
                ai_started = time.monotonic()
                step("investigate", investigation_text(gap))
                progress(active_investigations=[{"message": investigation_text(gap), "pages": gap["pages"],
                    "label": "Room-use specialist", "started_at": time.time()}])
                try:
                    outcome = _room_use_investigation(web, project, gap)
                except Exception as error:
                    outcome = {"status": "failed", "reason": str(error)[:500]}
                changed = changed or outcome.get("status") in SUCCESS
                history.extend({"gap_id": gap_id, "cycle": cycle, **outcome} for gap_id in gap["gap_ids"])
                if outcome.get("status") not in SUCCESS:
                    failed_inputs.update({gap_id: current_fp for gap_id in gap["gap_ids"]})
                ai_seconds += time.monotonic() - ai_started
            checkpoint()
        # All independent/dependent questions in this cycle have now been answered. Regenerate once only
        # when an accepted answer changed physical inputs; the pre-rebuild fingerprint is authoritative.
        if changed:
            step("rebuild")
            tasks._export_determinations(root)
            _build_calculation_evidence(web, project)
            tasks._refresh_geometry_tasks(root)
            from backend import ai_preliminary_service as model
            paths = model._paths(project)
            stored_inputs = read_json(paths["current_set"])
            if not stored_inputs or model._stale_reasons(paths, stored_inputs):
                rebuild_started = time.monotonic()
                snapshot = _rebuild(web, project)
                rebuild_seconds += time.monotonic() - rebuild_started
            audit_started = time.monotonic()
            _take_in_review(project)
            gaps = audit(snapshot)
            audit_seconds += time.monotonic() - audit_started
        else:
            failed_inputs.update({gap["id"]: current_fp for gap in select_investigations(gaps, attempts)})
        cycle_timings.append({"cycle": cycle, "ai_seconds": round(ai_seconds, 3),
                              "rebuild_seconds": round(rebuild_seconds, 3), "audit_seconds": round(audit_seconds, 3),
                              "total_seconds": round(time.monotonic() - cycle_started, 3)})
        after = model_fingerprint(snapshot), {row["id"] for row in gaps}
        resolved = (resolved | (before[1] - after[1])) - after[1]
        progress(active_investigations=[])
        if before == after or not changed:
            progress(stop_reason="no_progress")
            break
    # Questions the fixes above unlocked have no other way to be sent: answer them once (the drawing step's own limits
    # apply), so the result uses them.
    if not unavailable and drawings._waiting(root):
        step("rebuild")
        try:
            drawings.answer_all(web, project)
            snapshot = _rebuild(web, project)
            _take_in_review(project)
            gaps = audit(snapshot)
        except ai_provider.UsageLimitReached:
            pass
    exceptions = [{**gap, "attempts": attempts.get(gap["id"], 0),
                   "tried": [row for row in history if row.get("gap_id") == gap["id"]],
                   **({"message": gap["message"].rstrip(".") + ". " + unavailable} if unavailable else {})} for gap in gaps]
    has_supported_room = any(room.get("scope") in COMFORT and _positive(room.get("area_m2")) for room in snapshot.get("rooms", []))
    if not has_supported_room and not exceptions:
        exceptions.append({"id": "no_cooled_scope", "room": "the rooms", "pages": [], "attempts": 0,
            "message": "No cooled room has usable inputs for an empty-room result. Refrigeration and excluded spaces remain separate.", "tried": []})
    # What is left after the attempts: with no cooled room that has an area there is no number at all (blocking);
    # otherwise each is a labelled assumption of the result (a fallback was used or a check wasn't answered).
    for row in exceptions:
        row["blocking"] = not has_supported_room
    outcome = "needs_information" if not has_supported_room else "partial_draft" if exceptions else "draft_ready"
    result_fp = _fingerprint({"model": model_fingerprint(snapshot), "exceptions": exceptions})
    progress(exceptions=exceptions, active_investigations=[], model_fingerprint=model_fingerprint(snapshot), result_fingerprint=result_fp,
             outcome=outcome, stop_reason=("no_progress" if read_json(root / JOB_FILE).get("stop_reason") == "no_progress"
                 else "ai_unavailable" if unavailable and gaps
                 else "cycle_limit" if cycle >= MAX_CYCLES and gaps else "attempt_limit" if gaps else "ready"))
    return {"exceptions": exceptions, "outcome": outcome, "spend": _usage(root), "result_fingerprint": result_fp}


def source_fingerprint(project):
    """Inputs that can invalidate a settled audit, excluding its own bookkeeping and report publication."""
    root = Path(project["review_dir"])
    from backend.ai_preliminary_service import _file_fingerprint
    records = tasks._all_current(root)
    # Inputs only. room_use_resolution.json and ceiling_volume_resolution.json are rebuilt from these whenever the model
    # is assembled, so fingerprinting them made the check go out of date as soon as Phase 1 started (Butcher Buffet,
    # 2026-10-21); their sources are the room-use answer, the typed overrides and the drawing tasks below.
    review = read_json(root / "skill_workflow_run.json")
    room_uses = root / "skill_workflow_runs" / str(review.get("run_id", "")) / "proposals" / "room_identity_use.json"
    return _fingerprint({"files": {name: _file_fingerprint(root / name, {}, use_saved=True) for name in
        ("reviewer_room_geometry.json", "room_area_overrides.json", "ai_input.json", "spatial_ocr.json")},
        "overrides": {name: sorted(
            [{"room_id": row.get("room_id"), "override": row["override"]}
             for row in read_json(root / name).get("records", []) if isinstance(row, dict) and row.get("override")],
            key=lambda row: str(row["room_id"]))
            for name in ("room_use_resolution.json", "ceiling_volume_resolution.json")},
        "room_uses": {"run": review.get("run_id"), "status": (review.get("subskills", {}).get("room_identity_use") or {}).get("status"),
                      "answer": _file_fingerprint(room_uses, {}, use_saved=True)},
        "tasks": [{key: row.get(key) for key in ("task", "target", "input_fingerprint", "status", "applied_value")} for row in records]})


def status(web, project):
    state = _JOB.status(project)
    state["spend"] = _usage(project["review_dir"])
    state["current"] = bool(state.get("source_fingerprint") and state["source_fingerprint"] == source_fingerprint(project))
    state["recent_activity"] = [{
        "label": drawings.TASK_LABELS.get(row.get("task"), "Room-use specialist" if row.get("task") == "room_identity_use" else "Model investigation"),
        "status": row.get("status") or row.get("outcome", "unknown"), "cycle": row.get("cycle"),
    } for row in state.get("history", [])[-8:]]
    for key in ("history", "attempts", "resolved_gap_ids", "area_baseline"):
        state.pop(key, None)
    state["area_changes"] = [{name: row.get(name) for name in ("room", "before_m2", "after_m2")}
                             for row in state.get("area_changes") or [] if isinstance(row, dict)]

    if state.get("error"):
        state["error"] = "Checking the empty-room model stopped before it finished. Resume to keep the saved answers."
    if state.get("status") == "done" and not state["current"]:
        from backend.job_runner import recovery
        state.update(status="stale", step_state="waiting", recovery=recovery("stale", "The drawing inputs changed. Recheck the empty-room model."))
    # Keep internal routing in the job archive, outside operator-facing progress and exceptions.
    for key in ("gaps", "exceptions"):
        state[key] = [{name: row.get(name) for name in ("message", "room", "pages", "attempts", "kind")} for row in state.get(key, [])]
    # Stopped at a limit with gaps left: the check is finished, but it isn't a clean "done" for the person (Turramurra
    # 2026-11-18: 56 gaps found, 9 fixed, shown as a plain green tick).
    state["gaps_remaining"] = len(state.get("exceptions") or [])
    state["stopped_at_limit"] = bool(state.get("status") == "done" and state["gaps_remaining"]
                                     and state.get("stop_reason") in {"cycle_limit", "attempt_limit", "no_progress"})
    return state


def ai_unavailable(project):
    """Why the supervisor can't ask the AI on this job, or "". It uses the drawing tasks' own consent: page reading on
    for the job (the route current jobs use), or the older per-job AI analysis opt-in. Not being able to ask never
    blocks the check itself: the gaps are listed as not re-checked and Phase 1 goes ahead."""
    import os
    from backend import page_inventory_service
    settings = read_json(Path(project["review_dir"]) / "vision_extraction_settings.json")
    page_reading = (os.environ.get("ARCHIE_PAGE_READING", "").strip().lower() != "off"
                    and page_inventory_service.enabled(project))
    if not (settings.get("owner_opt_in") or page_reading):
        return "AI drawing analysis is off for this job, so these weren't re-checked."
    if not ai_provider.configured():
        return "The AI isn't set up on this computer, so these weren't re-checked."
    return ""


def start(web, project, action="start", expected_run_id=None):
    from backend.job_progress import stage_blocker, blocked_status
    if action == "restart":
        pending = _JOB.request_restart(project, expected_run_id)
        if pending is not None:
            return status(web, project)
    reason = stage_blocker(project, "supervisor")
    if reason:
        return blocked_status(status(web, project), reason)
    # No consent or key check here: without them the check still runs, asks nothing and lists the gaps, so the
    # empty-room calculation is never held up (2026-10-21; the old opt-in check blocked every current job).
    previous = read_json(_JOB.path(project))
    if action == "start" and previous.get("status") == "done" and previous.get("source_fingerprint") == source_fingerprint(project):
        return {**status(web, project), "deduplicated": True}
    fields = {key: deepcopy(previous[key]) for key in ("attempts", "history", "cycle", "resolved_gap_ids", "supervision_id", "area_baseline") if key in previous}
    if previous.get("status") == "done" and previous.get("source_fingerprint") != source_fingerprint(project):
        fields = {}
    fields.setdefault("supervision_id", uuid.uuid4().hex)

    def work(web, project_id, step):
        current = web.project_by_id(project_id) if web is not None else project
        result = run(web, current, step)
        result["source_fingerprint"] = source_fingerprint(current)
        return result

    return _JOB.start(web, project, fields, work, action=action, expected_run_id=expected_run_id)
