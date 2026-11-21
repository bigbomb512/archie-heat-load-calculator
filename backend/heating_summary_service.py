"""Read a saved hourly heating report for the job Results screen."""

from math import isfinite


COMPONENT_NAMES = {
    # The Results page's words for these parts (conduction through the fabric and the windows, fresh air, leakage).
    "heating_envelope": "Walls, roof and floor conduction",
    "heating_glazing_conduction": "Window conduction",
    "heating_outside_air": "Fresh air",
    "heating_infiltration": "Air leakage",
    "heating_internal_gain_credit": "Internal gain credit",
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _governing_scenario(report, peak):
    scenarios = [row for row in report.get("scenario_results", []) if isinstance(row, dict)]
    scenario_id = peak.get("scenario_id")
    if scenario_id:
        return next((row for row in scenarios if row.get("scenario_id") == scenario_id), {})
    # Heating V1's governing peak copies the scenario peak without its identity.
    matching = next((row for row in scenarios if row.get("included_scope_peak") == peak), None)
    if matching:
        return matching
    # A project safety policy can change only the report-level final total.
    candidates = [row for row in scenarios if _number((row.get("included_scope_peak") or {}).get("design_total_kw")) is not None]
    return max(candidates, key=lambda row: _number(row["included_scope_peak"]["design_total_kw"])) if candidates else {}


def _blocked_rooms(scenario):
    names = {row.get("room_id"): row.get("name") for row in scenario.get("rooms", []) if isinstance(row, dict)}
    return [{"room_id": row.get("room_id", ""), "name": names.get(row.get("room_id")) or row.get("room_id", ""),
             "reasons": row.get("reasons") or []}
            for row in (scenario.get("scope_summary") or {}).get("blocked_rooms", []) if isinstance(row, dict)]


def get(web, project):
    """Return only recorded, current heating values; never calculate or persist them."""
    result = {"status": "not_calculated", "reason": "", "next_step": "", "gate": {},
              "draft": True, "report_status": "", "total_kw": None, "peak": {},
              "outdoor_design_c": None, "indoor_design_c": None, "temperature_note": "",
              "rooms": [], "blocked_rooms": [], "stale_reasons": []}
    if not project.get("review_dir"):
        result["reason"] = "No heating report has been calculated for this job."
        result["next_step"] = ("This version of the job workspace doesn't run the heating calculation, so a heating "
                               "report has to be calculated before heating loads can be shown. A named HVAC engineer "
                               "must also approve the heating method for a review-ready result.")
        gate = web.empty_heating_method_gate()
        summary = web.heating_gate_summary(gate)
        result["gate"] = {"status": summary["status"], "approved": summary["status"] == "approved",
                          "message": summary["message"]}
        return result
    paths = web.hourly_paths(project)
    gate = web.load_json(paths["heating_method_gate"]) if paths["heating_method_gate"].exists() else web.empty_heating_method_gate()
    summary = web.heating_gate_summary(gate)
    result["gate"] = {"status": summary["status"], "approved": summary["status"] == "approved",
                      "message": summary["message"]}
    candidate = web.existing_path(project.get("hourly_heating_load_report"), paths["heating_report"])
    if not candidate:
        result["reason"] = "No heating report has been calculated for this job."
        result["next_step"] = ("This version of the job workspace doesn't run the heating calculation, so a heating "
                               "report has to be calculated before heating loads can be shown. A named HVAC engineer "
                               "must also approve the heating method for a review-ready result.")
        return result
    report = web.load_json(candidate)
    result["report_status"] = str(report.get("status") or "")
    result["draft"] = not result["gate"]["approved"] or result["report_status"] != "review_ready"
    result["stale_reasons"] = web.heating_report_stale_reasons(project)
    if result["stale_reasons"] or not web.current_hourly_heating_load_report_path(project):
        result["status"] = "stale"
        result["reason"] = "; ".join(result["stale_reasons"]) or "The heating report is no longer current."
        result["next_step"] = "Review the changed heating inputs and recalculate the hourly heating report."
        return result
    peak = report.get("included_scope_peak") or {}
    scenario = _governing_scenario(report, peak)
    result["blocked_rooms"] = (_blocked_rooms(scenario) if scenario else
                               [item for row in report.get("scenario_results", []) for item in _blocked_rooms(row)])
    if result["report_status"] == "blocked" or not peak:
        result["status"] = "blocked"
        reasons = [str(reason) for reason in report.get("blocked_reasons", []) if reason]
        reasons += [str(reason) for row in result["blocked_rooms"] for reason in row["reasons"] if reason]
        result["reason"] = "; ".join(dict.fromkeys(reasons)) or "The heating report has no calculated included-scope peak."
        result["next_step"] = "Resolve the heating report blockers and recalculate it."
        return result
    total = _number(peak.get("final_design_total_kw"))
    if total is None:
        total = _number(peak.get("design_total_kw"))
    if total is None or not scenario:
        result["status"] = "blocked"
        result["reason"] = "The heating report does not record a usable governing design load and scenario."
        result["next_step"] = "Recalculate the hourly heating report with a complete governing peak."
        return result
    result["status"] = "current"
    result["total_kw"] = total
    hour = peak.get("display_hour", peak.get("hour"))
    result["peak"] = {"scenario_id": scenario.get("scenario_id", ""), "title": scenario.get("title", ""), "hour": hour}
    outdoor, indoor = set(), set()
    missing_temperature = False
    for room in scenario.get("rooms", []):
        if not isinstance(room, dict) or room.get("status") == "blocked":
            continue
        room_peak = room.get("peak") or {}
        room_kw = _number(room_peak.get("design_total_kw"))
        if room_kw is None:
            continue
        components = {COMPONENT_NAMES.get(key, key): value for key, item in (room_peak.get("components") or {}).items()
                      if isinstance(item, dict) and (value := _number(item.get("sensible_kw"))) is not None}
        result["rooms"].append({"room_id": room.get("room_id", ""), "name": room.get("name", ""),
                                "peak_kw": room_kw, "hour": room_peak.get("display_hour", room_peak.get("hour")),
                                "components": components})
        at_peak = next((row for row in room.get("hours", []) if row.get("hour") == hour), {})
        inputs = at_peak.get("inputs") or {}
        if _number(inputs.get("outdoor_db_c")) is None or _number(inputs.get("setpoint_c")) is None:
            missing_temperature = True
        if (value := _number(inputs.get("outdoor_db_c"))) is not None:
            outdoor.add(value)
        if (value := _number(inputs.get("setpoint_c"))) is not None:
            indoor.add(value)
    result["outdoor_design_c"] = next(iter(outdoor)) if len(outdoor) == 1 and not missing_temperature else None
    result["indoor_design_c"] = next(iter(indoor)) if len(indoor) == 1 and not missing_temperature else None
    if result["outdoor_design_c"] is None or result["indoor_design_c"] is None:
        result["temperature_note"] = ("Winter design temperatures were not recorded at the governing hour, "
                                      "or conditioned rooms use different indoor heating setpoints.")
    result["next_step"] = ("Results are draft until a named HVAC engineer approves the heating method gate."
                           if not result["gate"]["approved"] else
                           "Review the heating results and room loads before using them for design.")
    if result["draft"] and result["gate"]["approved"]:
        result["next_step"] = "Resolve draft heating inputs and recalculate for a review-ready result."
    return result
