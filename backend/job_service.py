"""Job workspace backend: job setup, typed room areas and one status call for the tab rail.

The workspace (frontend/js/workspace.js) shows one tab at a time. This module
gives it what the rail and header need in a single request, so opening a job
does not load every artifact:

- job_setup.json: what the contractor tells us up front (job name, site address,
  building type, what is above the tenancy);
- room_area_overrides.json: areas typed by a person ("Edited by you"); they beat
  AI and traced areas (user decision 2026-10-06) and are read by
  reviewer_room_geometry_service.current_traced_areas;
- room_height_overrides.json: ceiling heights typed by a person; applied as
  contractor overrides when the ceiling heights are resolved (with_typed_heights);
- status(): per-tab state (done / check / needed / working) and the header total.
"""

from datetime import datetime, timezone
import math
import json
from pathlib import Path

BUILDING_TYPES = {"food_tenancy": "Food tenancy (café, restaurant)", "retail": "Retail shop", "office": "Office",
                  "medical": "Medical / consulting", "other": "Other"}
ABOVE_CHOICES = {"roof": "The roof", "floor": "Another floor or tenancy", "not_sure": "Not sure", "": ""}
ROOF_ANSWER = {"roof": "roof_directly_above", "floor": "floor_tenancy_above", "not_sure": "not_sure"}
MAX_AREA_M2 = 20000.0
HEIGHT_RANGE_MM = (1800.0, 15000.0)
TYPED_HEIGHT_REVIEWER = "Entered in the job workspace"


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _root(project):
    return Path(project["review_dir"])


# ---------------------------------------------------------------- job setup
def job_setup(project):
    setup = _read(_root(project) / "job_setup.json", {})
    return setup if isinstance(setup, dict) else {}


def save_job_setup(project, data):
    current = job_setup(project)
    updated = dict(current)
    for key, limit in (("name", 160), ("address", 300)):
        if key in data:
            updated[key] = " ".join(str(data.get(key) or "").split())[:limit]
    if "building_type" in data:
        value = str(data.get("building_type") or "")
        if value and value not in BUILDING_TYPES:
            raise ValueError("Choose a building type from the list.")
        updated["building_type"] = value
    if "above" in data:
        value = str(data.get("above") or "")
        if value not in ABOVE_CHOICES:
            raise ValueError("Choose what is above the tenancy: the roof, another floor, or not sure.")
        updated["above"] = value
    updated["updated_by"] = " ".join(str(data.get("edited_by") or current.get("updated_by") or "").split())[:80]
    updated["updated_at"] = _now()
    _write(_root(project) / "job_setup.json", updated)
    return updated


def apply_roof_answer(web, project, *, defer_evidence_rebuild=False):
    """Answer every open roof question with the job-level answer (one answer per tenancy, not per room)."""
    from backend import autonomous_tasks_service
    above = job_setup(project).get("above", "")
    answer = ROOF_ANSWER.get(above)
    if not answer:
        return 0
    answered = 0
    changed_envelope = False
    for record in autonomous_tasks_service._all_current(_root(project)):
        if record.get("task") == "P5_roof" and record.get("status") == "needs_contractor_answer":
            autonomous_tasks_service.post(web, project, {"action": "answer_roof", "task": "P5_roof",
                                                         "target": record.get("target"), "answer": answer},
                                           defer_evidence_rebuild=True)
            answered += 1
            changed_envelope = changed_envelope or above != "not_sure"
    # P5 applies the same job-level answer to rooms waiting on a contractor.
    # Also complete current hand traces, without replacing any reviewer decision.
    if above in {"roof", "floor"}:
        from backend import reviewer_room_geometry_service
        root = _root(project)
        artifact = _read(root / "reviewer_room_geometry.json", {})
        records = artifact.get("records", []) if isinstance(artifact, dict) else []
        for trace in reviewer_room_geometry_service.current_records(
                reviewer_room_geometry_service._paths(project), artifact):
            if trace.get("roof", "unknown") != "unknown":
                continue
            if trace.get("roof_source") == "reviewer":
                continue
            reviewer_room_geometry_service.post(web, project, {
                "action": "classify_envelope", "trace_id": trace["trace_id"],
                "edges": trace.get("edges", []), "roof": "exposed" if above == "roof" else "not_exposed",
                "openings": trace.get("openings", []),
                "openings_none_edges": trace.get("openings_none_edges", []),
                "reviewer": "Answered on the Project tab", "confirm_roof": True,
            }, _defer_evidence_rebuild=True)
            answered += 1
            changed_envelope = True
    if changed_envelope and not defer_evidence_rebuild:
        # The answer can update several parts. Rebuild shared geometry evidence once
        # after every trace has the new roof value.
        from backend import calculation_extraction_service
        calculation_extraction_service.post(web, project, {"action": "build"})
    return answered


# ---------------------------------------------------------------- typed room areas
def area_overrides(root):
    data = _read(Path(root) / "room_area_overrides.json", {})
    rows = data.get("overrides", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("room_id")]


def save_area_override(project, data):
    """Set or clear (area_m2 null) the typed area of a room (found on the drawings or added in the workspace)."""
    from ai.room_use_resolution import room_identity
    label = " ".join(str(data.get("label") or "").split())[:80]
    level = " ".join(str(data.get("level_name") or "Unassigned level").split())[:80] or "Unassigned level"
    room_id = str(data.get("room_id") or "") or (room_identity(label, level) if label else "")
    if not room_id:
        raise ValueError("Give the room a name.")
    area = data.get("area_m2")
    if area is not None:
        if type(area) not in (int, float) or not 0 < float(area) <= MAX_AREA_M2:
            raise ValueError("Enter the room area in m², between 0 and 20,000.")
        area = round(float(area), 2)
    root = _root(project)
    rows = [row for row in area_overrides(root) if row.get("room_id") != room_id]
    previous = next((row for row in area_overrides(root) if row.get("room_id") == room_id), {})
    if area is not None:
        rows.append({"room_id": room_id, "room_label": label or previous.get("room_label", ""), "level_name": level,
                     "area_m2": area,
                     "edited_by": " ".join(str(data.get("edited_by") or "").split())[:80], "edited_at": _now()})
    _write(root / "room_area_overrides.json", {"schema_version": 1, "overrides": rows})
    return {"room_id": room_id, "area_m2": area, "overrides": rows}


# ---------------------------------------------------------------- typed ceiling heights
def height_overrides(root):
    data = _read(Path(root) / "room_height_overrides.json", {})
    rows = data.get("overrides", []) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("room_id")]


def save_height_override(project, data):
    """Set or clear (ceiling_height_mm null) the typed ceiling height of a room, by its name and level.

    Stored under the ceiling resolver's room identity ("room:<level>:<name>"), which differs from the
    room list's key ("room-use:..."); room_key keeps the latter for the workspace.
    """
    from ai.ceiling_volume_resolution import room_identity
    label = " ".join(str(data.get("label") or "").split())[:80]
    level = " ".join(str(data.get("level_name") or "Unassigned level").split())[:80] or "Unassigned level"
    if not label:
        raise ValueError("Choose the room.")
    room_id = room_identity(label, level)
    height = data.get("ceiling_height_mm")
    if height is not None:
        if type(height) not in (int, float) or not HEIGHT_RANGE_MM[0] <= float(height) <= HEIGHT_RANGE_MM[1]:
            raise ValueError("Enter the ceiling height in metres, between 1.8 and 15.")
        height = round(float(height))
    root = _root(project)
    rows = [row for row in height_overrides(root) if row.get("room_id") != room_id]
    if height is not None:
        rows.append({"room_id": room_id, "room_key": str(data.get("room_key") or "")[:200], "room_label": label,
                     "level_name": level, "ceiling_height_mm": height,
                     "edited_by": " ".join(str(data.get("edited_by") or "").split())[:80], "edited_at": _now()})
    _write(root / "room_height_overrides.json", {"schema_version": 1, "overrides": rows})
    return {"room_id": room_id, "ceiling_height_mm": height, "overrides": rows}


def with_typed_heights(existing, root):
    """The previous ceiling artifact with typed heights as contractor overrides, for the ceiling resolver.

    A typed height replaces an earlier override for that room. Rooms the resolver no longer lists are
    dropped afterwards by drop_typed_height_stubs, so a typed height never leaves a half-empty record.
    """
    typed = {row["room_id"]: row for row in height_overrides(root)}
    if not typed:
        return existing
    base = dict(existing) if isinstance(existing, dict) else {}
    records = [dict(row) for row in base.get("records", []) if isinstance(row, dict)]
    known = {row.get("room_id") for row in records}
    records += [{"room_id": room_id, "typed_height_stub": True} for room_id in typed if room_id not in known]
    for row in records:
        if row.get("room_id") in typed:
            entry = typed[row["room_id"]]
            row["override"] = {"height_mm": float(entry["ceiling_height_mm"]), "reviewer": entry.get("edited_by") or TYPED_HEIGHT_REVIEWER,
                               "note": "Ceiling height entered in the job workspace.", "updated_at": entry.get("edited_at", "")}
    base["records"] = records
    return base


def drop_typed_height_stubs(artifact):
    """Remove typed heights for rooms the resolver no longer lists, then restate status and fingerprint as resolve does."""
    from ai import ceiling_volume_resolution
    records = [row for row in artifact.get("records", []) if not row.get("typed_height_stub")]
    if len(records) == len(artifact.get("records", [])):
        return artifact
    result = {key: value for key, value in artifact.items() if key not in {"fingerprint", "stale_reasons"}}
    result["records"] = records
    result["status"] = ("needs_review" if any(row["status"] in {"conflict", "needs_review", "stale"} for row in records)
                        else "draft_ready" if records else "needs_review")
    retained = [row for row in records if row.get("status") == "stale"]
    if retained:
        result["stale_reasons"] = [f"{len(retained)} room(s) no longer appear in the current proposal; "
                                   "saved ceiling overrides were retained for review."]
    result["fingerprint"] = ceiling_volume_resolution.fingerprint(result)
    return result


# ---------------------------------------------------------------- status for the rail
def _tab(state, detail="", count=None):
    result = {"state": state, "detail": detail}
    if count is not None:
        result["count"] = count
    return result


def traced_rooms(root):
    """Area of each room traced on a plan, from the saved outline and its printed-dimension scale.

    Cheap enough for the status call (no evidence rebuild); the calculation itself uses the
    geometry proofs, which are rebuilt when a trace is saved.
    """
    data = _read(Path(root) / "reviewer_room_geometry.json", {})
    rooms = {}
    for row in data.get("records", []) if isinstance(data, dict) else []:
        if not isinstance(row, dict) or not row.get("room_id"):
            continue
        calibration = row.get("calibration") if isinstance(row.get("calibration"), dict) else {}
        points = row.get("points_image_px") or []
        scale = calibration.get("mm_per_px")
        if calibration.get("status") not in {"agreed", "declared_scale_rejected"} or not isinstance(scale, (int, float)) or len(points) < 4:
            continue
        try:
            twice = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))
        except (TypeError, IndexError):
            continue
        area = abs(twice) / 2 * scale * scale / 1e6
        if area <= 0:
            continue
        room = rooms.setdefault(row["room_id"], {"area_m2": 0.0, "pages": [], "source": "traced"})
        room["area_m2"] = round(room["area_m2"] + area, 2)
        room["pages"].append(row.get("page"))
        if row.get("declaration_source") in {"ai_determined", "ai_fallback"}:
            room["source"] = "ai_determined"
    return rooms


def envelope_rooms(root):
    """Read current envelope decisions directly from the geometry artifact."""
    artifact = _read(Path(root) / "reviewer_room_geometry.json", {})
    current = artifact.get("records", []) if isinstance(artifact, dict) else []
    rooms = {}
    for row in current:
        room_id = row.get("room_id")
        if not room_id:
            continue
        calibration = row.get("calibration") if isinstance(row.get("calibration"), dict) else {}
        scale = calibration.get("mm_per_px")
        points = row.get("points_image_px") or []
        edges = []
        for edge in row.get("edges", []):
            index = edge.get("index")
            if type(index) is not int or index + 1 >= len(points):
                continue
            try:
                length = math.hypot(points[index + 1][0] - points[index][0],
                                    points[index + 1][1] - points[index][1]) * float(scale) / 1000
            except (TypeError, ValueError, IndexError):
                length = None
            source = (row.get("edge_sources", {}).get(str(index)) or row.get("envelope_declaration_source")
                      or row.get("declaration_source") or "")
            edges.append({"trace_id": row.get("trace_id"), "page": row.get("page"), "index": index,
                          "boundary": edge.get("boundary", "unknown"),
                          "length_m": round(length, 3) if length is not None else None, "source": source})
        source = row.get("roof_source") or row.get("envelope_declaration_source") or row.get("declaration_source") or ""
        room = rooms.setdefault(room_id, {"traces": [], "edges": [], "openings": [], "no_glazing_edges": [],
                                           "roof": "unknown", "roof_source": ""})
        room["traces"].append({"trace_id": row.get("trace_id"), "page": row.get("page"),
                               "ai_quality_label": row.get("ai_quality_label", ""),
                               "edge_sources": row.get("edge_sources", {}),
                               "roof_source": row.get("roof_source", "")})
        room["edges"].extend(edges)
        room["openings"].extend([{**opening, "trace_id": row.get("trace_id"), "page": row.get("page"),
                                  "source": opening.get("source") or row.get("envelope_declaration_source")
                                  or row.get("declaration_source", "")}
                                 for opening in row.get("openings", [])])
        room["no_glazing_edges"].extend({"trace_id": row.get("trace_id"), "index": index}
                                        for index in row.get("openings_none_edges", []))
        if row.get("roof", "unknown") != "unknown":
            room["roof"], room["roof_source"] = row["roof"], source
    for room in rooms.values():
        room["has_outline"] = bool(room["traces"])
        room["window_count"] = len(room["openings"])
        room["below_accuracy_bar"] = any(
            "below accuracy bar" in str(trace.get("ai_quality_label", "")).lower()
            or any(source == "ai_fallback" for source in trace.get("edge_sources", {}).values())
            or trace.get("roof_source") == "ai_fallback"
            for trace in room["traces"])
    return rooms


def _typed_since_result(root):
    """True when a typed area or height, or a room trace, was saved after the last result (the next Calculate uses it)."""
    report = root / "hourly_ai_preliminary_load_report.json"
    if not report.exists():
        return False
    typed = [root / "room_area_overrides.json", root / "room_height_overrides.json", root / "reviewer_room_geometry.json"]
    return any(path.exists() and path.stat().st_mtime > report.stat().st_mtime for path in typed)


def status(web, project):
    """Header facts and tab states: done / check / needed / working / todo."""
    from backend import ai_preliminary_service, autonomous_tasks_service
    root = _root(project)
    setup = job_setup(project)
    tasks = autonomous_tasks_service._all_current(root)
    progress = autonomous_tasks_service._task_progress(root)

    site_task = next((row for row in tasks if row.get("task") == "P1_site" and row.get("applied_value")), None)
    found_site = (site_task or {}).get("applied_value", {}).get("site_text", "") if site_task else ""
    address = setup.get("address") or ""
    roof_open = [row for row in tasks if row.get("task") == "P5_roof" and row.get("status") == "needs_contractor_answer"]
    if not address and not found_site and not setup.get("above"):
        project_tab = _tab("needed", "Add the site address and what's above the tenancy.")
    elif not address or roof_open or not setup.get("above"):
        project_tab = _tab("check", "Check the address and what's above the tenancy.")
    else:
        project_tab = _tab("done")

    confirmed = bool(project.get("reasoning_packet"))
    if not confirmed:
        drawings_tab = _tab("needed", "The drawing pages haven't been prepared yet.")
    elif progress["waiting"]:
        drawings_tab = _tab("working", "Toki is reviewing the drawings.")
    elif progress["blocked"]:
        drawings_tab = _tab("check", "Some drawing checks need attention.")
    else:
        drawings_tab = _tab("done")

    model = ai_preliminary_service.get(web, project, "workspace", check_freshness=False) if confirmed else {}
    scope = model.get("room_scope") or {}
    overrides = area_overrides(root)
    typed = {row["room_id"]: row for row in overrides}
    # Typed areas count straight away; the draft model picks them up when Calculate rebuilds it.
    traced = traced_rooms(root)
    candidates = [dict(row, area_m2=typed[row.get("key")]["area_m2"], area_origin="edited", include=True)
                  if row.get("key") in typed else
                  dict(row, area_m2=traced[row.get("key")]["area_m2"], area_origin="reviewer_traced"
                       if traced[row.get("key")]["source"] == "traced" else "ai_determined", include=True)
                  if row.get("key") in traced else row for row in (scope.get("candidates") or [])]
    known = {row.get("key") for row in candidates}
    # Rooms added in the workspace have a typed area before the next Calculate rebuilds the model.
    candidates += [{"key": row["room_id"], "label": row.get("room_label", ""), "level": row.get("level_name", ""), "area_m2": row["area_m2"],
                    "area_origin": "edited", "include": True, "status": "added"}
                   for row in overrides if row["room_id"] not in known]
    included = [row for row in candidates if row.get("include")]
    with_area = [row for row in included if row.get("area_m2") is not None]
    uncertain = [row for row in included if row.get("status") == "needs_use"
                 or "below accuracy bar" in str(row.get("area_quality_label") or "")]
    if not with_area:
        rooms_tab = _tab("needed", "No room has an area yet.")
    elif len(with_area) < len(included):
        rooms_tab = _tab("check", f"{len(with_area)} of {len(included)} rooms have an area")
    elif uncertain:
        rooms_tab = _tab("check", f"{len(uncertain)} room{'s' if len(uncertain) != 1 else ''} to check")
    else:
        rooms_tab = _tab("done", f"{len(with_area)} rooms")

    envelope = envelope_rooms(root)
    outlined_included = [row for row in included if envelope.get(row.get("key"), {}).get("has_outline")]
    missing_outline = [row for row in included if not envelope.get(row.get("key"), {}).get("has_outline")]
    if missing_outline or not included:
        walls_detail = ("Select a room to cool on the Rooms tab." if not included
                        else f"Add outlines for {len(missing_outline)} cooled room(s).")
        walls_tab = _tab("needed", walls_detail)
    elif any(room.get("below_accuracy_bar") or room.get("roof") == "unknown"
             or any(edge.get("boundary") == "unknown" for edge in room.get("edges", []))
             for room in (envelope.get(row.get("key"), {}) for row in outlined_included)):
        walls_tab = _tab("check", "Check wall and roof boundaries")
    else:
        walls_tab = _tab("done")

    # Child-tab summaries give the workspace enough context to show calm,
    # specific guidance without changing calculation readiness rules.
    # Job name, address, and building type are useful context, not new
    # calculation requirements. Only ask for review when the drawings found
    # a specific site clue that could be accepted.
    site_state = "check" if found_site and not address else "done"
    site_detail = ("Review the address found on the drawings." if found_site and not address else
                   "Address saved." if address else "Optional job and site details.")
    tenancy_state = ("check" if setup.get("above") == "not_sure" or roof_open else "done" if setup.get("above") else "needed")
    tenancy_detail = ("Check the roof information." if roof_open else "Review the tenancy context." if setup.get("above") == "not_sure"
                      else "Tell us what is above the tenancy." if not setup.get("above") else "Tenancy context saved.")
    room_detail_rows = [row for row in included if row.get("status") == "needs_use"]
    if not candidates and not confirmed and progress["waiting"]:
        room_details_tab = _tab("working", "Preparing the room list.", 0)
    elif not candidates and not confirmed:
        room_details_tab = _tab("todo", "Rooms appear after the drawings are read.", 0)
    elif not candidates:
        room_details_tab = _tab("needed", "Check the drawings or add a room.", 1)
    elif room_detail_rows:
        room_details_tab = _tab("check", f"Choose a use for {len(room_detail_rows)} room(s).", len(room_detail_rows))
    else:
        room_details_tab = _tab("done", "Room details are ready.", 0)

    if not candidates and not confirmed and progress["waiting"]:
        measurements_tab = _tab("working", "Preparing room measurements.", 0)
    elif not candidates:
        measurements_tab = _tab("todo", "Measurements appear when rooms are listed.", 0)
    elif not included:
        measurements_tab = _tab("needed", "Choose which rooms are cooled.", 1)
    elif len(with_area) < len(included):
        measurements_tab = _tab("needed", f"Add areas for {len(included) - len(with_area)} room(s).", len(included) - len(with_area))
    else:
        below_area_bar = [row for row in with_area
                          if "below accuracy bar" in str(row.get("area_quality_label") or "").lower()]
        measurements_tab = (_tab("check", f"Review the area for {len(below_area_bar)} room(s).", len(below_area_bar))
                            if below_area_bar else _tab("done", "Room measurements are ready.", 0))

    if not candidates and not confirmed and progress["waiting"]:
        walls_subtab = _tab("working", "Preparing rooms before wall review.", 0)
        roof_subtab = _tab("working", "Preparing rooms before roof review.", 0)
    elif not candidates:
        walls_subtab = _tab("todo", "Walls can be reviewed after rooms are listed.", 0)
        roof_subtab = _tab("todo", "Roof details appear after rooms are listed.", 0)
    elif not included:
        walls_subtab = _tab("needed", "Choose which rooms are cooled on the Rooms tab.", 1)
        roof_subtab = _tab("todo", "Choose cooled rooms before roof review.", 0)
    elif missing_outline:
        detail = f"Add outlines for {len(missing_outline)} cooled room(s)."
        walls_subtab = _tab("needed", detail, len(missing_outline))
        roof_subtab = _tab("todo", "Add room outlines before roof review.", 0)
    else:
        unknown_walls = sum(edge.get("boundary") == "unknown"
                            for row in outlined_included for edge in envelope.get(row.get("key"), {}).get("edges", []))
        unknown_roofs = sum(envelope.get(row.get("key"), {}).get("roof", "unknown") == "unknown"
                            for row in outlined_included)
        wall_review_edges = sum(
            1 for row in outlined_included
            for edge in envelope.get(row.get("key"), {}).get("edges", [])
            if str(edge.get("source") or "").startswith("ai")
        )
        wall_review_edges += sum(
            1 for row in outlined_included
            if any("below accuracy bar" in str(trace.get("ai_quality_label") or "").lower()
                   for trace in envelope.get(row.get("key"), {}).get("traces", []))
            and not any(str(edge.get("source") or "").startswith("ai")
                        for edge in envelope.get(row.get("key"), {}).get("edges", []))
        )
        roof_review_rooms = sum(
            1 for row in outlined_included
            if str(envelope.get(row.get("key"), {}).get("roof_source") or "").startswith("ai")
        )
        walls_subtab = (_tab("needed", f"Classify {unknown_walls} wall(s).", unknown_walls) if unknown_walls else
                        _tab("check", f"Review {wall_review_edges} AI wall decision(s).", wall_review_edges) if wall_review_edges else
                        _tab("done", "Wall boundaries are ready.", 0))
        roof_subtab = (_tab("needed", f"Choose what is above {unknown_roofs} room(s).", unknown_roofs) if unknown_roofs else
                       _tab("check", f"Review AI roof decisions in {roof_review_rooms} room(s).", roof_review_rooms) if roof_review_rooms else
                       _tab("done", "Roof details are ready.", 0))
    if not outlined_included:
        windows_tab = _tab("todo", "No outlined rooms yet.")
    elif any(any(edge.get("boundary") == "unknown" for edge in envelope.get(row.get("key"), {}).get("edges", []))
             for row in outlined_included):
        windows_tab = _tab("todo", "Classify the walls first")
    elif any(edge.get("boundary") in {"external", "mall"}
             and not any(opening.get("trace_id") == edge.get("trace_id")
                         and opening.get("edge_index") == edge.get("index")
                         for opening in envelope.get(row.get("key"), {}).get("openings", []))
             and not any(mark.get("trace_id") == edge.get("trace_id") and mark.get("index") == edge.get("index")
                         for mark in envelope.get(row.get("key"), {}).get("no_glazing_edges", []))
             for row in outlined_included for edge in envelope.get(row.get("key"), {}).get("edges", [])):
        windows_tab = _tab("check", "Check glazing or confirm no glazing on each outside or mall wall")
    else:
        windows_tab = _tab("done")

    report = model.get("hourly_ai_preliminary_load_report") or {}
    peak = report.get("included_scope_peak") or {}
    total = peak.get("final_design_total_kw", peak.get("design_total_kw"))
    stale = scope.get("status") != "confirmed" or _typed_since_result(root)
    if total is None:
        results_tab = _tab("todo", "Not calculated yet.")
    elif stale:
        results_tab = _tab("check", "Inputs changed since the last calculation.")
    else:
        results_tab = _tab("done")

    # Ceiling height per room-list key: typed first, otherwise the last resolved height (drawing or default).
    from ai.ceiling_volume_resolution import room_identity as ceiling_identity
    heights = height_overrides(root)
    typed_heights = {row["room_id"]: row for row in heights}
    ceiling = _read(root / "ceiling_volume_resolution.json", {})
    resolved = {row["room_id"]: row for row in (ceiling.get("records", []) if isinstance(ceiling, dict) else [])
                if isinstance(row, dict) and row.get("room_id") and row.get("ceiling_height_mm") is not None}
    room_heights = {}
    for row in candidates:
        cid = ceiling_identity(row.get("label", ""), row.get("level") or "Unassigned level")
        if cid in typed_heights:
            room_heights[row.get("key")] = {"ceiling_height_mm": typed_heights[cid]["ceiling_height_mm"], "origin": "edited"}
        elif cid in resolved:
            room_heights[row.get("key")] = {"ceiling_height_mm": resolved[cid]["ceiling_height_mm"],
                                            "origin": resolved[cid].get("origin", "")}
    analysis = project.get("analysis") if isinstance(project.get("analysis"), dict) else {}
    return {
        "id": project["id"],
        "name": setup.get("name") or str(project.get("name") or "").removesuffix(".pdf"),
        "address": address, "found_site": found_site,
        "building_type": setup.get("building_type", ""), "above": setup.get("above", ""),
        "total_kw": total, "result_stale": bool(total is not None and stale),
        "drawings_confirmed": confirmed, "checks": progress,
        "rooms": {"total": len(candidates), "included": len(included), "with_area": len(with_area)},
        "area_overrides": overrides, "height_overrides": heights, "room_heights": room_heights,
        "traced_rooms": traced,
        "envelope": envelope,
        "subtabs": {
            "project": {"job_site": _tab(site_state, site_detail, 0 if site_state == "done" else 1),
                        "tenancy_context": _tab(tenancy_state, tenancy_detail, 0 if tenancy_state == "done" else 1)},
            "rooms": {"room_details": room_details_tab, "measurements": measurements_tab},
            "walls": {"walls": walls_subtab, "roof": roof_subtab},
        },
        "tabs": {"project": project_tab, "drawings": drawings_tab, "rooms": rooms_tab,
                 "walls": walls_tab, "windows": windows_tab, "results": results_tab},
        "pages": analysis.get("pages_analysed"),
    }
