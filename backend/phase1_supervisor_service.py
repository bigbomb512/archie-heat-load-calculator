"""Deterministic audit and bounded repair of the inputs used by the empty-room calculation."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from backend import ai_provider, autonomous_tasks_service as tasks, drawing_tasks_service as drawings
from backend.job_runner import BackgroundJob, JobCancelled, checkpoint, current_check, read_json, write_json

JOB_FILE = "phase1_supervisor_job.json"
MAX_CYCLES = 3
MAX_ATTEMPTS = 2
_JOB = BackgroundJob(JOB_FILE, [("audit", "Checking the empty-room model"),
    ("investigate", "Checking the drawings"), ("rebuild", "Updating the empty-room model")],
    "archie-phase1-supervisor", "Checking the empty-room model was interrupted. Resume to keep the saved drawing answers.")
COMFORT = {"comfort_hvac", "comfort_hvac_with_process_exception"}
SUCCESS = {"applied", "applied_fallback", "below_accuracy_bar"}
PHASE1_TASKS = {"P0_room_outlines", "P0_room_sizes", "P3_boundaries", "P4_storefront_wall", "P4_openings", "P5_roof", "P7_ceiling_heights"}


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


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _task_current(record):
    packet = {key: value for key, value in (record.get("packet") or {}).items() if key != "prompt_fingerprint"}
    expected = tasks.autonomous_tasks.fingerprint({"packet": packet, "images": record.get("image_hashes", [])})
    return record.get("input_fingerprint") == expected


def load_snapshot(project, report=None):
    """Read/recompute current evidence without writing job data or calling a provider."""
    from backend import ai_preliminary_service as model, reviewer_room_geometry_service as geometry
    root = Path(project["review_dir"])
    paths = model._paths(project)
    traces = geometry.current_records(geometry._paths(project))
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
    for use in uses.get("records", []):
        label = use.get("original_label") or use.get("label") or ""
        matching = [row for row in traces if row.get("room_id") == use.get("room_id") or _key(row.get("room_label")) == _key(label)]
        area = areas.get(use.get("room_id"), {})
        height = next((row for row in heights.get("records", []) if row.get("room_id") == use.get("room_id")
                       or _key(row.get("original_label")) == _key(label)), {})
        material = next((row for row in material_rooms if _key(row.get("name")) == _key(label)), {})
        room_tasks = [row for row in current_tasks if (row.get("packet") or {}).get("room_id") == use.get("room_id")
                      or any(_key(label) == _key(value) for value in (row.get("packet") or {}).get("room_names", []))]
        rooms.append({"id": use.get("room_id"), "name": label, "scope": use.get("space_scope"),
            "use_status": use.get("status"), "traces": matching, "area_m2": area.get("area_m2") or sum(
                row.get("ai_measured_area_m2", 0) or 0 for row in matching),
            "area_source": area.get("area_origin") or area.get("method") or " ".join(str(row.get("method", "")) for row in matching),
            "height": height, "model": material, "model_current": current, "tasks": room_tasks,
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
    return {"rooms": rooms, "tasks": current_tasks, "traces": traces,
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
    cooling = (room.get("model") or {}).get("cooling_load") or {}
    model_has_glass = bool(cooling.get("glazing_surfaces") or any(
        row.get("kind") in {"window", "glazing", "glazed_door"} and _positive(row.get("area_m2"))
        for row in cooling.get("envelope_surfaces", [])))
    return model_has_glass if room.get("model_current") else model_has_glass or any(trace.get("openings") for trace in room.get("traces", []))


def _exposed(room):
    """The room has an outside wall, an exposed roof or glass: its empty-room load can't be zero."""
    traces = room.get("traces", [])
    return (any(side == "external" for side in _boundaries(traces))
            or any(trace.get("roof") == "exposed" for trace in traces) or _has_glass(room))


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
        label = _name(room.get("name"))
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

    for room in rooms:
        label = _name(room.get("name"))
        traces = room.get("traces", [])
        area = room.get("area_m2")
        if not _positive(area) or any(term in str(room.get("area_source", "")).casefold()
                                      for term in ("room_size_estimated", "last_resort", "generic_profile", "profile_default")):
            add(1, "room_area", room, "P0_room_outlines", f"Check {label}'s outline and area; its area is missing or only a last-resort estimate.")
        if room.get("use_status") in {"unresolved", "conflict", "blocked", "needs_review", "stale"}:
            add(1, "room_use", room, "room_identity_use", f"Check how {label} is used so its empty-room calculation includes the right space.")
        related_pages = {trace.get("page") for trace in traces}
        for record in records:
            if record.get("task") not in PHASE1_TASKS:
                continue
            packet = record.get("packet") or {}
            related = (record in room.get("tasks", []) or packet.get("room_id") == room.get("id")
                       or (packet.get("page") in related_pages and record.get("task") in {"P3_boundaries", "P4_storefront_wall"})
                       or any(_key(label) == _key(value) for value in packet.get("rooms", []) if isinstance(value, str)))
            if related and (not record.get("current", True) or record.get("status") in {"failed", "blocked", "waiting_for_reply"}):
                add(2, "unanswered", room, record["task"], f"Finish the unanswered drawing check for {label}: {drawings.TASK_LABELS.get(record['task'], 'checking the room').lower()}.",
                    pages=([packet["page"]] if type(packet.get("page")) is int else None), target=record.get("target"))
        sides = _boundaries(traces)
        if storefront and any(side in {"external", "mall"} for side in sides) and not _has_glass(room):
            opening = next((row for row in records if row.get("task") == "P4_openings" and (
                (row.get("packet") or {}).get("room_id") == room.get("id") or _key((row.get("packet") or {}).get("room_label")) == _key(label))), None)
            add(3, "storefront_glass", room, "P4_openings" if opening else "P4_storefront_wall",
                f"Check {label}'s shopfront glass against the elevation; no glass reached the empty-room model.",
                pages=storefront, target=opening.get("target") if opening else None)
        if height_pages and room.get("height", {}).get("origin") == "preliminary_fallback":
            add(4, "ceiling_height", room, "P7_ceiling_heights", f"Check {label}'s ceiling height on the labelled ceiling plan, elevation or section.", pages=height_pages)
        if storefront and sides and all_internal:
            add(5, "internal_perimeter", room, "P3_boundaries", f"Check {label}'s perimeter: its walls all face other rooms or tenancies although a shopfront elevation exists.", pages=storefront + room.get("pages", []))
        if any(side == "unknown" for side in sides):
            add(6, "unknown_walls", room, "P3_boundaries", f"Check what is on the other side of {label}'s unknown walls.")
        if traces and any(trace.get("roof") in {None, "", "unknown"} or trace.get("roof_source") == "ai_fallback" for trace in traces):
            add(6, "roof", room, "P5_roof", f"Check what is above {label}; its roof or ceiling exposure is unknown or assumed.")

    report = snapshot.get("report") or {}
    if report:
        for room in rooms:
            label = _name(room.get("name"))
            coverage = report.get("phase1_coverage") or {}
            omitted = any(_key(row.get("label")) == _key(label) for row in coverage.get("rooms_left_out", []))
            if "rooms_included" in coverage and _key(label) not in {_key(name) for name in coverage["rooms_included"]}:
                omitted = True
            results = [row for scenario in report.get("scenario_results", []) for row in scenario.get("rooms", []) if _key(row.get("name")) == _key(label)]
            zero = bool(results) and all(isinstance((row.get("peak") or {}).get("total_kw"), (int, float))
                and abs(row["peak"]["total_kw"]) < 1e-9 for row in results)
            surfaces = [row for row in report.get("phase1_surface_audit", []) if _key(row.get("room_name")) == _key(label)]
            if not results and surfaces:
                zero = all(isinstance(row.get("total_kw"), (int, float)) and abs(row["total_kw"]) < 1e-9 for row in surfaces)
            # A zero is only suspicious when the room has something that should load it: an outside wall, an exposed
            # roof or shopfront glass. Walls to conditioned rooms, other tenancies and a mall are zero by rule
            # (2026-10-15), so an internal room's zero is the right answer, not a gap.
            if zero and not omitted and not _exposed(room):
                continue
            if omitted or zero:
                add(7, "omitted_room" if omitted else "zero_load", room, "P3_boundaries",
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
    return [gap for gap in gaps if attempts.get(gap["id"], 0) < MAX_ATTEMPTS]


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
        descriptions = {"room_area": "missing or estimated room outlines and areas", "room_use": "unresolved room use",
            "unanswered": "an unanswered drawing check", "storefront_glass": "no applied shopfront glass",
            "ceiling_height": "a fallback ceiling height despite printed height labels",
            "internal_perimeter": "an internal perimeter despite a shopfront elevation", "unknown_walls": "unknown wall boundaries",
            "roof": "unknown or assumed roof exposure", "zero_load": "a zero envelope load", "zero_tenancy": "a zero tenancy load",
            "omitted_room": "a room omitted from the empty-room result"}
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


def _room_use_investigation(web, project, gap):
    """Replace only a complete, validated room-use answer; partial replies never overwrite the room list."""
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
    try:
        result = skills._execute_subskill(spec, project, dependencies, manifest["source_fingerprint"],
            {"question": gap["note"] + " Return the complete room list, preserving every already evidenced room."})
        attempt = result.pop("_attempt_record", {})
        result = skills._validate_subskill_output(spec, result, registry,
            allowed_pages=skills._consented_page_ids(skills._project_paths(project)))
        prior = {row.get("room_id") for row in previous.get("proposal_fields", {}).get("rooms", []) if row.get("room_id")}
        current = {row.get("room_id") for row in result.get("proposal_fields", {}).get("rooms", []) if row.get("room_id")}
        if not prior <= current:
            raise ValueError("The targeted answer omitted previously evidenced rooms; the existing room list was kept.")
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


def run(web, project, step=lambda *args: None):
    root = Path(project["review_dir"])
    saved = read_json(root / JOB_FILE)
    durable = read_json(root / "phase1_supervisor_state.json")
    if durable.get("supervision_id") == saved.get("supervision_id"):
        saved = {**saved, **durable}
    attempts, history = deepcopy(saved.get("attempts", {})), deepcopy(saved.get("history", []))
    resolved = set(saved.get("resolved_gap_ids", []))
    cycle = int(saved.get("cycle") or 0)
    snapshot = _rebuild(web, project)
    gaps = audit(snapshot)
    initial_ids = {row["id"] for row in gaps}
    check = current_check()

    def progress(**extra):
        checkpoint()
        state = read_json(root / JOB_FILE)
        state.update(attempts=attempts, history=history, cycle=cycle, max_cycles=MAX_CYCLES,
                     gaps_found=len(initial_ids | set(attempts) | {gap["id"] for gap in gaps}), gaps_fixed=len(resolved),
                     resolved_gap_ids=sorted(resolved), gaps=deepcopy(gaps), spend=_usage(root), **extra)
        write_json(root / JOB_FILE, state)
        write_json(root / "phase1_supervisor_state.json", {key: state[key] for key in (
            "supervision_id", "attempts", "history", "cycle", "resolved_gap_ids", "exceptions", "model_fingerprint") if key in state})

    unavailable = ai_unavailable(project)
    progress(stop_reason="", not_asked=unavailable)
    while cycle < MAX_CYCLES and not unavailable:
        checkpoint()
        selected = select_investigations(gaps, attempts)
        if not selected:
            break
        cycle += 1
        before = model_fingerprint(snapshot), {row["id"] for row in gaps}
        step("audit", "Checking room areas, boundaries, glass and ceiling heights")
        for wave in range(5):
            selected = [gap for gap in select_investigations(gaps, attempts) if _investigation_wave(gap["task"]) == wave]
            if not selected:
                continue
            prepared, skills_to_run, changed = {}, [], False
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
                    except ValueError:
                        if gap["task"] != "P0_room_outlines":
                            raise
                        records = tasks.prepare_investigation(project, {**gap, "task": "P0_room_sizes"})
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
            active = {key: {"message": investigation_text(gap), "pages": gap["pages"]} for key, (_record, gap) in prepared.items()}
            progress(active_investigations=list(active.values()))
            if active:
                step("investigate", next(iter(active.values()))["message"])
            with ThreadPoolExecutor(max_workers=ai_provider.parallel_calls()) as pool:
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
                        drawings.archive_investigation_failure(project, record, error)
                        outcome = {"status": "provider_failed", "reason": str(error)[:500]}
                    changed = changed or outcome.get("status") in SUCCESS
                    history.extend({"gap_id": gap_id, "task": record["task"], "target": record["target"],
                                    "cycle": cycle, "run_id": record["run_id"], **outcome} for gap_id in gap["gap_ids"])
                    active.pop((record["task"], record["target"]), None)
                    progress(active_investigations=list(active.values()))
                    if active:
                        step("investigate", next(iter(active.values()))["message"])
            for gap in skills_to_run:
                checkpoint()
                step("investigate", investigation_text(gap))
                try:
                    outcome = _room_use_investigation(web, project, gap)
                except Exception as error:
                    outcome = {"status": "failed", "reason": str(error)[:500]}
                changed = changed or outcome.get("status") in SUCCESS
                history.extend({"gap_id": gap_id, "cycle": cycle, **outcome} for gap_id in gap["gap_ids"])
            checkpoint()
            if not changed:
                continue                 # nothing was applied: the model and its audit are unchanged (a rebuild takes 30-55 s)
            step("rebuild")
            tasks._refresh_geometry_tasks(root)
            snapshot = _rebuild(web, project)
            gaps = audit(snapshot)
        after = model_fingerprint(snapshot), {row["id"] for row in gaps}
        resolved = (resolved | (before[1] - after[1])) - after[1]
        progress(active_investigations=[])
        if before == after:
            progress(stop_reason="no_progress")
            break
    exceptions = [{**gap, "attempts": attempts.get(gap["id"], 0),
                   "tried": [row for row in history if row.get("gap_id") == gap["id"]],
                   **({"message": gap["message"].rstrip(".") + ". " + unavailable} if unavailable else {})} for gap in gaps]
    has_supported_room = any(room.get("scope") in COMFORT and _positive(room.get("area_m2")) for room in snapshot.get("rooms", []))
    if not has_supported_room and not exceptions:
        exceptions.append({"id": "no_cooled_scope", "room": "the rooms", "pages": [], "attempts": 0,
            "message": "No cooled room has usable inputs for an empty-room result. Refrigeration and excluded spaces remain separate.", "tried": []})
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
    return _fingerprint({"files": {name: _file_fingerprint(root / name, {}, use_saved=True) for name in
        ("reviewer_room_geometry.json", "room_use_resolution.json", "ceiling_volume_resolution.json", "ai_input.json", "spatial_ocr.json")},
        "tasks": [{key: row.get(key) for key in ("task", "target", "input_fingerprint", "status", "applied_value")} for row in records]})


def status(web, project):
    state = _JOB.status(project)
    state["spend"] = _usage(project["review_dir"])
    state["current"] = bool(state.get("source_fingerprint") and state["source_fingerprint"] == source_fingerprint(project))
    for key in ("history", "attempts", "resolved_gap_ids"):
        state.pop(key, None)
    if state.get("error"):
        state["error"] = "Checking the empty-room model stopped before it finished. Resume to keep the saved answers."
    if state.get("status") == "done" and not state["current"]:
        from backend.job_runner import recovery
        state.update(status="stale", step_state="waiting", recovery=recovery("stale", "The drawing inputs changed. Recheck the empty-room model."))
    # Keep internal routing in the job archive, outside operator-facing progress and exceptions.
    for key in ("gaps", "exceptions"):
        state[key] = [{name: row.get(name) for name in ("message", "room", "pages", "attempts")} for row in state.get(key, [])]
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
    fields = {key: deepcopy(previous[key]) for key in ("attempts", "history", "cycle", "resolved_gap_ids", "supervision_id") if key in previous}
    if previous.get("status") == "done" and previous.get("source_fingerprint") != source_fingerprint(project):
        fields = {}
    fields.setdefault("supervision_id", uuid.uuid4().hex)

    def work(web, project_id, step):
        current = web.project_by_id(project_id) if web is not None else project
        result = run(web, current, step)
        result["source_fingerprint"] = source_fingerprint(current)
        return result

    return _JOB.start(web, project, fields, work, action=action, expected_run_id=expected_run_id)
