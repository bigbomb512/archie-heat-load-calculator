"""Read-only plan and room data for the redesigned job workspace."""

from pathlib import Path

from shapely.geometry import Polygon

from backend import ai_preliminary_service, job_service, reviewer_room_geometry_service


def _number(value):
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _load_number(value):
    try:
        number = float(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


# How an area was found, in the words and line style the plan legend uses:
# solid = measured from the drawing, dashed = assumed (sheet scale only), dotted = estimate.
AREA_BASIS = {
    "typed": ("Typed by you", "solid"),
    "printed": ("Printed on the drawing", "solid"),
    "measured": ("Measured from a printed dimension", "solid"),
    "sheet_scale": ("Measured at the sheet's printed scale only", "dashed"),
    "printed_dimensions": ("Worked out from printed dimensions", "dashed"),
    "ai_outline": ("Measured from the AI's room outline", "dashed"),
    "remainder": ("What is left of the tenancy area", "dotted"),
    "estimate": ("AI estimate from the plan", "dotted"),
}
MEASURED_CALIBRATIONS = {"agreed", "declared_scale_rejected"}


def _area_basis(area_source, calibration_status="", declaration_source=""):
    """One of AREA_BASIS's keys, from the saved area source and, for an outline, its scale check."""
    source = str(area_source or "").casefold()
    if source in {"edited", "typed"}:
        return "typed"
    if source in {"printed_on_drawing", "pdf_evidence"} or source.startswith("printed (") or source == "printed":
        return "printed"
    if "remainder" in source:
        return "remainder"
    if "estimate" in source:
        return "estimate"
    if "printed dimensions" in source:
        return "printed_dimensions"
    if source == "reviewer_traced":
        return "measured"
    if source == "ai_determined" and not calibration_status:
        return "ai_outline"
    if calibration_status in MEASURED_CALIBRATIONS or (declaration_source == "reviewer" and not calibration_status):
        return "measured"
    if calibration_status == "declared_scale_only":
        return "sheet_scale"
    return "estimate"


def _kind(space_scope):
    if space_scope == "refrigeration_process":
        return "refrigeration"
    if space_scope == "not_a_room":
        return "not_a_room"
    return "comfort"


def _room_state(candidate, kind, area, load, areas_still_being_found, phase1_current):
    """Where one room is: sized_separately, left_out, needs_use, finding_area, needs_area, finding_surfaces or done.

    A room the model left out only because it has no area is not "left out": its area is still being found,
    or, once every area step has answered, it is the one thing the operators type.
    """
    if kind == "refrigeration":
        return "sized_separately"
    if kind == "not_a_room":
        return "left_out"
    if candidate.get("status") == "needs_use":
        return "needs_use"
    if area is None:
        return "finding_area" if areas_still_being_found else "needs_area"
    if candidate.get("include") is False and candidate.get("status") != "no_area":
        return "left_out"
    if load is not None and phase1_current:
        return "done"
    return "finding_surfaces"


def _label_point(points):
    try:
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area <= 0:
            return None
        point = polygon.representative_point()
        return [round(point.x, 2), round(point.y, 2)]
    except (TypeError, ValueError):
        return None


def get(web, project):
    """Assemble current plan polygons and room facts without changing evidence."""
    from ai.room_use_resolution import room_identity
    from backend import autonomous_tasks_service

    if not project.get("review_dir"):
        # A new upload whose PDF hasn't been read yet: nothing to draw, and the Drawings step is still first.
        name = str(project.get("name") or "").removesuffix(".pdf")
        from backend import job_progress
        status = {"id": project["id"], "name": name, "area_overrides": [], **job_progress.milestones(
            drawings_confirmed=False, checks={}, phase1_complete=False, result_exists=False, result_stale=False,
            rooms=[], areas_still_being_found=False, open_needs=0)}
        return {"project_id": project["id"], "status": status, "rooms": [], "outlines": [], "pages": [],
                "rooms_without_outline": [], "area_basis_labels": {key: {"label": label, "line_style": style}
                                                                   for key, (label, style) in AREA_BASIS.items()},
                "phase1_current": False, "phase1_report": {}, "final_report": {}}
    root = Path(project["review_dir"])
    status = job_service.status(web, project)
    # job_service.status has just checked freshness, so this repeat reuses the cached source fingerprints.
    model = ai_preliminary_service.get(web, project, "workspace", check_freshness=True)
    scope = model.get("room_scope") if isinstance(model.get("room_scope"), dict) else {}
    candidates = [row for row in scope.get("candidates", []) if isinstance(row, dict)]
    traces = reviewer_room_geometry_service.current_artifact_input(root).get("records", [])
    pages = reviewer_room_geometry_service._page_context(reviewer_room_geometry_service._paths(project), web)
    page_by_number = {row.get("page"): row for row in pages}
    overrides = {row["room_id"]: row for row in status.get("area_overrides", []) if row.get("room_id")}
    traced_areas = reviewer_room_geometry_service.current_traced_areas(root)
    uses = job_service._read(root / "room_use_resolution.json", {})
    use_by_id = {str(row.get("room_id")): row for row in (uses.get("records") or [])
                 if isinstance(row, dict) and row.get("room_id")} if isinstance(uses, dict) else {}
    areas_still_being_found = job_service._areas_still_being_found(autonomous_tasks_service._all_current(root))

    # A room's Phase 1 load is shown only from a current, unblocked Phase 1 result that included the room;
    # its final load only from a current full result. Anything else would show an old or blocked 0 kW.
    base = model.get("phase1_base_room_load_report") or {}
    phase1_current = bool(base and not model.get("phase1_base_room_load_report_stale")
                          and not base.get("blocked_reasons") and base.get("status") != "blocked")
    included_in_phase1 = set((base.get("phase1_coverage") or {}).get("rooms_included") or [])
    final = model.get("hourly_ai_preliminary_load_report") or {}
    final_current = bool(status.get("results_ready") and not status.get("result_stale"))

    def peaks_by_identity(report):
        return {room_identity(row.get("name", ""), "Unassigned level"): row
                for row in (report.get("room_peaks") or []) if isinstance(row, dict) and row.get("name")}
    phase1_peaks, final_peaks = peaks_by_identity(base), peaks_by_identity(final)

    def area_for(room_id, candidate):
        """(area m², basis key) from a typed area, then the current trace or area record, then the room list."""
        typed = _number(overrides.get(room_id, {}).get("area_m2"))
        if typed is not None:
            return typed, "typed"
        trace = traced_areas.get(room_id, {})
        area = _number(trace.get("area_m2"))
        if area is not None:
            return area, _area_basis(trace.get("area_source"), trace.get("calibration_status"),
                                     trace.get("declaration_source"))
        area = _number(candidate.get("area_m2"))
        if area is not None:
            return area, _area_basis(candidate.get("area_origin") or candidate.get("area_source"))
        return None, ""

    def room_row(room_id, name, level, candidate, source_pages):
        use = use_by_id.get(room_id, {})
        kind = _kind(use.get("space_scope") or candidate.get("scope"))
        area, basis = area_for(room_id, candidate)
        identity = room_identity(name, "Unassigned level")
        load = (_load_number(phase1_peaks.get(identity, {}).get("design_total_kw"))
                if phase1_current and name in included_in_phase1 else None)
        final_kw = _load_number(final_peaks.get(identity, {}).get("design_total_kw")) if final_current else None
        level = "" if not level or level == "Unassigned level" else level
        return {"room_id": room_id, "name": name, "level": level, "area_m2": area,
                "area_basis": basis, "area_label": AREA_BASIS.get(basis, ("", ""))[0],
                "phase1_kw": load, "final_kw": final_kw,
                "w_m2": round(load * 1000 / area, 1) if load is not None and area else None,
                "final_w_m2": round(final_kw * 1000 / area, 1) if final_kw is not None and area else None,
                "kind": kind, "included": kind == "comfort" and candidate.get("include") is not False,
                "state": _room_state(candidate, kind, area, load, areas_still_being_found, phase1_current),
                "source_pages": source_pages}

    rooms = [room_row(str(row.get("key", "")), row.get("label") or str(row.get("key", "")), row.get("level"),
                      row, row.get("source_pages", [])) for row in candidates if row.get("key")]
    room_by_id = {row["room_id"]: row for row in rooms}
    # Rooms the comfort list leaves out (a coolroom, a freezer) still show on the plan with their area.
    for room_id, use in use_by_id.items():
        if room_id not in room_by_id and use.get("space_scope") in {"refrigeration_process", "not_a_room"}:
            room = room_row(room_id, use.get("original_label") or room_id, use.get("level"), {}, [])
            rooms.append(room)
            room_by_id[room_id] = room

    outlines = []
    for trace in traces:
        room_id = str(trace.get("room_id", ""))
        page_number = trace.get("page")
        points = trace.get("points_image_px") or []
        if not room_id or len(points) < 3:
            continue
        room = room_by_id.get(room_id)
        if room is None:
            room = room_row(room_id, trace.get("room_label") or room_id, trace.get("level_name"), {},
                            [page_number] if isinstance(page_number, int) else [])
            rooms.append(room)
            room_by_id[room_id] = room
        calibration = trace.get("calibration") if isinstance(trace.get("calibration"), dict) else {}
        source = str(trace.get("declaration_source") or "reviewer")
        basis = _area_basis("printed_on_drawing" if trace.get("printed_area_m2") else "",
                            calibration.get("status", ""), source)
        outlines.append({"room_id": room_id, "name": room["name"], "level": room["level"],
                         "page": page_number, "points": points, "label_point": _label_point(points),
                         "line_style": AREA_BASIS[basis][1], "area_basis": basis,
                         "kind": room["kind"], "state": room["state"], "source": source})

    outlined = {row["room_id"] for row in outlines}
    for room in rooms:
        room["has_outline"] = room["room_id"] in outlined

    return {"project_id": project["id"], "status": status, "rooms": rooms,
            "outlines": outlines, "pages": pages,
            "rooms_without_outline": [row for row in rooms if row["room_id"] not in outlined],
            "area_basis_labels": {key: {"label": label, "line_style": style}
                                  for key, (label, style) in AREA_BASIS.items()},
            "phase1_current": phase1_current,
            "phase1_report": base,
            "final_report": final}
