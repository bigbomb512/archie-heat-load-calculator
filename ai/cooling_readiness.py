"""Readiness and scope rules for the authoritative hourly cooling workflow."""

from copy import deepcopy


COMPONENT_LABELS = {
    "infiltration": "Infiltration",
    "minimum_supply_air": "Minimum supply air",
    "extract_air": "Extract air",
    "spill_air": "Spill air",
    "transfer_air": "Transfer air",
    "make_up_air": "Make-up air",
    "vapour_gain": "Vapour gain",
    "steam_gain": "Steam gain",
    "process_latent_load": "Process latent load",
}


def topology_issues(model, requirements_updated_at=""):
    """Return explicit hierarchy issues without attempting to infer topology."""
    issues = []
    if requirements_updated_at and model.get("source_requirements_updated_at") != requirements_updated_at:
        issues.append(issue("blocked", "project", "project", "Hourly load model is stale because design requirements changed after the model was saved.", "hourly_load_model"))
    floors = {floor.get("floor_id"): floor for floor in model.get("floors", [])}
    zones = {zone.get("zone_id"): zone for zone in model.get("zones", [])}
    if not model.get("rooms"):
        issues.append(issue("blocked", "project", "project", "Hourly room model has no rooms.", "hourly_load_model"))
    for floor_id, floor in floors.items():
        if floor.get("verification_status") != "confirmed":
            issues.append(issue("draft", "floor", floor_id, "Floor topology remains provisional.", "hourly_load_model", floor.get("citations", [])))
    for zone_id, zone in zones.items():
        if zone.get("verification_status") != "confirmed":
            issues.append(issue("draft", "zone", zone_id, "Zone topology remains provisional.", "hourly_load_model", zone.get("citations", [])))
        if zone.get("floor_id") not in floors:
            issues.append(issue("blocked", "zone", zone_id, "Zone references an unknown floor.", "hourly_load_model", zone.get("citations", [])))
    for room in model.get("rooms", []):
        room_id = room.get("room_id", "")
        if room.get("zone_id") not in zones:
            issues.append(issue("blocked", "room", room_id, "Room references an unknown zone.", "hourly_load_model", room.get("citations", [])))
        if room.get("mapping_status") != "confirmed" or room.get("verification_status") != "confirmed":
            issues.append(issue("draft", "room", room_id, "Room mapping or review remains provisional.", "hourly_load_model", room.get("citations", [])))
    return issues


def room_component_issues(model):
    """Return non-blocking evidence issues for components with no approved method."""
    issues = []
    for room in model.get("rooms", []):
        room_id = room.get("room_id", "")
        for component in room.get("unapproved_components", []):
            label = COMPONENT_LABELS.get(component.get("component_type", ""), component.get("component_type", "component"))
            state = component.get("calculation_status", "not_assessed")
            if state == "stored_not_calculated":
                value = component.get("value")
                unit = component.get("unit", "")
                issues.append(issue(
                    "draft", "room", room_id,
                    f"{label} ({value:g} {unit}) is stored but excluded until an approved calculation method exists.",
                    "hourly_load_model", component.get("citations", []), component.get("source", ""),
                ))
            elif state == "not_assessed":
                issues.append(issue(
                    "draft", "room", room_id,
                    f"Assess {label}: confirm it is not present or add a cited stored input.",
                    "hourly_load_model", component.get("citations", []), component.get("source", ""),
                ))
    return issues


def assess_cooling_readiness(report, model, requirements_updated_at="", coverage=None, envelope_input=None):
    """Classify a calculated report as blocked, draft, or review-ready.

    `validated` is deliberately never emitted here; it belongs to the later
    authorised benchmark gate rather than normal input completion.
    """
    coverage = coverage or {}
    issues = topology_issues(model, requirements_updated_at) + room_component_issues(model)
    for reason in report.get("blocked_reasons", []):
        issues.append(issue("blocked", "project", "project", reason, "hourly_cooling_report"))
    active_room_ids = [room.get("room_id", "") for room in model.get("rooms", [])]
    included_room_ids, blocked_rooms = set(), {}
    floor_rollups, zone_rollups = {}, {}

    for scenario in report.get("scenario_results", []):
        if scenario.get("status") == "blocked":
            for reason in scenario.get("blocked_reasons", []):
                issues.append(issue("blocked", "scenario", scenario.get("scenario_id", ""), reason, "design_day_scenarios"))
        elif scenario.get("status") == "draft":
            issues.append(issue("draft", "scenario", scenario.get("scenario_id", ""), "Selected cooling scenario or its hourly inputs remain provisional.", "design_day_scenarios"))
        for room in scenario.get("rooms", []):
            room_id = room.get("room_id", "")
            if room.get("status") == "blocked":
                blocked_rooms.setdefault(room_id, []).extend(room.get("blocked_reasons", []))
                for reason in room.get("blocked_reasons", []):
                    issues.append(issue("blocked", "room", room_id, reason, "hourly_load_model"))
            else:
                included_room_ids.add(room_id)
                if room.get("status") == "draft":
                    issues.append(issue("draft", "room", room_id, "Room calculation uses provisional inputs.", "hourly_load_model"))
        scenario_zone_rollups = zone_rollups.setdefault(scenario.get("scenario_id", ""), {})
        for zone in scenario.get("zones", []):
            scenario_zone_rollups[zone.get("zone_id", "")] = {
                "floor_id": zone.get("floor_id", ""),
                "included_room_ids": list(zone.get("room_ids", [])),
                "peak": deepcopy(zone.get("peak", {})),
            }
        scenario_floor_rollups = floor_rollups.setdefault(scenario.get("scenario_id", ""), {})
        for floor in scenario.get("floors", []):
            scenario_floor_rollups[floor.get("floor_id", "")] = {
                "included_room_ids": list(floor.get("room_ids", [])),
                "zone_ids": list(floor.get("zone_ids", [])),
                "peak": deepcopy(floor.get("peak", {})),
            }

    if envelope_input:
        for row in envelope_input.get("blocked", []):
            issues.append(issue("blocked", "surface", row.get("surface_id", ""), row.get("reason", "Envelope surface is blocked."), "envelope_model"))
        for row in envelope_input.get("stored_not_calculated", []):
            issues.append(issue("blocked", "surface", row.get("surface_id", ""), "Stored-not-calculated envelope data prevents a complete reviewed-envelope result.", "envelope_model"))

    if coverage.get("coverage_exceptions"):
        for row in coverage["coverage_exceptions"]:
            issues.append(issue("draft", "project", "project", row.get("question", "Drawing coverage requires review."), "drawing_coverage"))

    room_input_coverage = []
    for room in model.get("rooms", []):
        stored = []
        unassessed = []
        confirmed_absent = []
        for component in room.get("unapproved_components", []):
            item = {
                "component_id": component.get("component_id", ""), "component_type": component.get("component_type", ""),
                "value": component.get("value"), "unit": component.get("unit", ""), "source": component.get("source", ""),
                "citations": deepcopy(component.get("citations", [])), "source_room_id": component.get("source_room_id", ""),
            }
            if component.get("calculation_status") == "stored_not_calculated":
                stored.append(item)
            elif component.get("calculation_status") == "not_assessed":
                unassessed.append(item)
            else:
                confirmed_absent.append(item)
        room_input_coverage.append({
            "room_id": room.get("room_id", ""), "stored_not_calculated": stored,
            "not_assessed": unassessed, "not_present_confirmed": confirmed_absent,
            "status": "complete" if not stored and not unassessed else "incomplete",
        })
    incomplete_component_rooms = [row["room_id"] for row in room_input_coverage if row["status"] != "complete"]
    complete_scope = bool(active_room_ids) and set(active_room_ids) == included_room_ids and not blocked_rooms and not incomplete_component_rooms
    if not included_room_ids:
        status = "blocked"
    elif any(item["status"] == "blocked" for item in issues):
        # A blocked room still permits a useful included-scope draft result.
        status = "draft" if included_room_ids else "blocked"
    elif not complete_scope or any(item["status"] == "draft" for item in issues):
        status = "draft"
    else:
        status = "review_ready"

    return {
        "status": status,
        "issues": issues,
        "scope_summary": {
            "active_room_ids": active_room_ids,
            "included_room_ids": sorted(included_room_ids),
            "blocked_rooms": [{"room_id": room_id, "reasons": reasons} for room_id, reasons in sorted(blocked_rooms.items())],
            "complete_scope": complete_scope,
            "floor_rollups": floor_rollups,
            "zone_rollups": zone_rollups,
            "room_input_coverage": room_input_coverage,
            "incomplete_component_room_ids": incomplete_component_rooms,
        },
    }


def issue(status, scope, affected_id, reason, source_artifact, citations=None, input_source=""):
    return {
        "status": status,
        "scope": scope,
        "affected_id": affected_id,
        "reason": reason,
        "source_artifact": source_artifact,
        "citations": deepcopy(citations or []),
        "input_source": input_source,
        "blocks_calculation": status == "blocked",
    }
