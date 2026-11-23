"""Versioned, read-only handoff of saved cooling loads to AC system design."""

import math
from datetime import datetime, timezone

from backend import contractor_report, job_plan_service, job_summary_service, result_label_service, result_status_service


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _hour(value):
    return value if type(value) is int and 0 <= value < 24 else None


def _peak_kw(peak):
    return _number(peak.get("final_design_total_kw", peak.get("design_total_kw")))


def _profile(value):
    totals = value.get("total_kw") if isinstance(value, dict) else None
    if not isinstance(totals, list) or len(totals) != 24:
        return None
    result = [_number(item) for item in totals]
    return result if all(item is not None for item in result) else None


def _report_rooms(report):
    """Use compact saved rows, or the older saved scenario rows before room_peaks existed."""
    rows = [row for row in report.get("room_peaks") or [] if isinstance(row, dict)]
    if rows:
        return rows
    scenarios = [row for row in report.get("scenario_results") or [] if isinstance(row, dict)]
    governing = (report.get("included_scope_peak") or {}).get("scenario_id")
    day = next((row for row in scenarios if row.get("scenario_id") == governing), scenarios[0] if scenarios else {})
    identities = report.get("room_identities") or {}
    result = []
    for room in day.get("rooms") or []:
        if not isinstance(room, dict):
            continue
        peak = room.get("peak") or {}
        rid = str(room.get("room_id") or "")
        own = None
        for scenario in scenarios:
            match = next((r for r in scenario.get("rooms") or [] if isinstance(r, dict) and str(r.get("room_id")) == rid), {})
            candidate = match.get("peak") or {}
            kw = _number(candidate.get("design_total_kw"))
            if kw is not None and (own is None or kw > own["kw"]):
                own = {"kw": kw, "hour": candidate.get("display_hour", candidate.get("hour")),
                       "month": scenario.get("representative_month"),
                       "sensible_kw": candidate.get("sensible_kw"), "latent_kw": candidate.get("latent_kw"),
                       "components": candidate.get("components")}
        hours = room.get("hours") or []
        hourly = {"total_kw": [h.get("design_total_kw") if isinstance(h, dict) else None for h in hours]} if len(hours) == 24 else {}
        result.append({"source_room_id": identities.get(rid, rid), "room_id": rid,
                       "name": room.get("name"), "design_total_kw": peak.get("design_total_kw"),
                       "hour": peak.get("display_hour", peak.get("hour")), "components": peak.get("components"),
                       "own_peak": own, "hourly": hourly, "sensible_kw": peak.get("sensible_kw"),
                       "latent_kw": peak.get("latent_kw")})
    return result


def _room(row, peak, house_hour, month):
    own = peak.get("own_peak") or {}
    own_kw = _number(own.get("kw"))
    if own_kw is None:
        own_kw = _number(peak.get("design_total_kw"))
    profile = _profile(peak.get("hourly"))
    at_house = profile[house_hour] if profile is not None and house_hour is not None else None
    components = own.get("components") or (peak.get("components") if own_kw == _number(peak.get("design_total_kw")) else {}) or {}
    parts = {key: _number(value.get("total_kw") if isinstance(value, dict) else value)
             for key, value in components.items()}
    parts = {key: value for key, value in parts.items() if value is not None}
    use = row.get("use") or {}
    occupancy = row.get("occupancy") or {}
    people = row.get("people") if "people" in row else occupancy.get("value") if isinstance(occupancy, dict) else None
    result = {"id": str(row.get("id") or row.get("room_id") or ""),
              "name": str(row.get("display_name") or row.get("name") or ""),
              "level": row.get("level") or "", "area_m2": _number(row.get("area_m2")),
              "use_id": use.get("taxonomy_id") if isinstance(use, dict) else use,
              "use_label": use.get("taxonomy_label") if isinstance(use, dict) else None,
              "cooled": bool(row.get("cooled", row.get("included", row.get("kind") == "comfort"))),
              "people": _number(people), "member_ids": list(row.get("member_ids") or []),
              "peak": {"kw": own_kw, "sensible_kw": _number(own.get("sensible_kw", peak.get("sensible_kw")
                                                              if own_kw == _number(peak.get("design_total_kw")) else None)),
                       "latent_kw": _number(own.get("latent_kw", peak.get("latent_kw")
                                                            if own_kw == _number(peak.get("design_total_kw")) else None)),
                       "month": own.get("month") or (month if own_kw is not None else None),
                       "hour": _hour(own.get("hour", peak.get("hour")))},
              "at_house_peak_kw": at_house, "hourly_kw": profile}
    if parts:
        result["components_at_peak"] = parts
    return result


NOT_COUNTED = {"needs_area": "It needs an area before it can be counted.",
               "needs_use": "Its use needs choosing before it can be counted.",
               "finding_surfaces": "Its walls and windows weren't worked out from the drawings.",
               "finding_area": "Its area is still being found from the drawings."}


def get(web, project):
    plan = job_plan_service.get(web, project)
    status = plan.get("status") or {}
    full = plan.get("final_report") or {}
    base = plan.get("phase1_report") or {}
    full_peak = full.get("included_scope_peak") or {}
    base_peak = base.get("included_scope_peak") or {}
    if _peak_kw(full_peak) is not None:
        state, report, peak = ("stale" if status.get("result_stale") or not status.get("results_ready") else "current"), full, full_peak
    elif _peak_kw(base_peak) is not None:
        state, report, peak = "building_only", base, base_peak
    else:
        state, report, peak = "none", {}, {}
    reasons = {"current": "The saved cooling load is current.",
               "stale": "Out of date: the saved results have changed inputs; update the load.",
               "building_only": "Only the building load is saved; calculate the full load before designing the system.",
               "none": "No cooling load has been saved yet."}
    day = (report.get("design_conditions_basis") or {}).get("design_day") or {}
    design_point = next((item.get("design_point") or {} for item in day.get("days") or []
                         if isinstance(item, dict) and (item.get("day") == "hot" or item.get("design_point"))), {})
    conditions = {"location": day.get("location"), "outdoor_db_c": _number(design_point.get("db")),
                  "outdoor_wb_c": _number(design_point.get("wb")), "indoor_db_c": None,
                  "indoor_wb_c": None, "indoor_rh": None, "source": day.get("source")}
    # Indoor setpoints are exported only if the saved result actually records them: on the peak itself, else in the
    # saved setpoint comparison's baseline (the design setpoint the result was calculated at).
    for key in ("indoor_db_c", "indoor_wb_c", "indoor_rh"):
        conditions[key] = _number(peak.get(key))
    if state in {"current", "stale"} and conditions["indoor_db_c"] is None:
        from backend import setpoint_comparison_service
        try:
            comparison = setpoint_comparison_service.cached(project) or {}
        except Exception:
            comparison = {}
        baseline = next((row for row in comparison.get("variants") or [] if row.get("baseline")), {})
        if comparison.get("status") == "calculated" and baseline:
            conditions.update({"indoor_db_c": _number(baseline.get("setpoint_c")),
                               "indoor_wb_c": _number(baseline.get("wet_bulb_c")),
                               "indoor_rh": _number(baseline.get("relative_humidity", comparison.get("relative_humidity")))})
    total_kw = _peak_kw(peak)
    suggested = f'{contractor_report.ducted(total_kw, bool(status.get("dwelling")))["suggested"]} ducted' if total_kw is not None and state != "building_only" else None
    month = peak.get("month") or peak.get("representative_month")
    house_hour = _hour(peak.get("display_hour", peak.get("hour")))
    report_rows = _report_rooms(report)
    by_id = {str(row.get("source_room_id") or row.get("room_id")): row for row in report_rows}
    by_name = {}
    for saved_row in report_rows:
        name = str(saved_row.get("name") or "").casefold()
        if name:
            by_name[name] = saved_row if name not in by_name else None
    joined = [row for row in plan.get("joined_rooms") or [] if isinstance(row, dict)]
    member_ids = {str(member) for row in joined for member in row.get("member_ids") or []}
    rows = joined + [row for row in plan.get("rooms") or [] if isinstance(row, dict)
                     and str(row.get("room_id")) not in member_ids and not row.get("join_id")]
    rooms, not_cooled, not_counted = [], [], []
    for row in rows:
        rid = str(row.get("id") or row.get("room_id") or "")
        source_id = rid if rid.startswith("join:") else f"join:{rid}" if row in joined else rid
        saved = by_id.get(source_id) or by_id.get(rid) or by_name.get(str(row.get("name") or "").casefold()) or {}
        data = _room({**row, "id": source_id}, saved, house_hour, month)
        if data["cooled"]:
            rooms.append(data)
        elif row.get("kind") == "comfort" and row.get("cooled") is not False:
            # Meant to be cooled but left out of the saved load (no area yet, walls not worked out): not "not cooled".
            not_counted.append({key: data[key] for key in ("id", "name", "level", "area_m2")}
                               | {"reason": NOT_COUNTED.get(str(row.get("state") or ""), "It isn't in the saved load yet.")})
        else:
            not_cooled.append({key: data[key] for key in ("id", "name", "level", "area_m2")}
                              | {"reason": "Marked not cooled." if row.get("kind") == "not_cooled" or row.get("cooled") is False else "Outside the comfort cooling scope."})
    rooms.sort(key=lambda row: (str(row["level"]).casefold(), row["name"].casefold()))
    not_cooled.sort(key=lambda row: (str(row["level"]).casefold(), row["name"].casefold()))
    not_counted.sort(key=lambda row: (str(row["level"]).casefold(), row["name"].casefold()))
    assumptions = list(dict.fromkeys(str(item.get("text")) for item in (result_status_service.gather(web, project).get("items") or [])
                                     if isinstance(item, dict) and item.get("kind") in {"assumed", "typical", "placeholder"} and item.get("text")))
    todos = [row for row in job_summary_service.get(web, project).get("todos") or []
             if isinstance(row, dict) and row.get("severity") in {"blocks", "changes_number"}]
    site = (report.get("design_conditions_basis") or {}).get("site") or {}
    address = (site.get("address") if site.get("confirmed") else None) or status.get("address") or status.get("found_site") or ""
    return {"schema": "toki.ac-design-input", "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project": {"id": str(project.get("id") or ""), "name": status.get("name") or str(project.get("name") or "").removesuffix(".pdf"),
                        "address": address, "dwelling": bool(status.get("dwelling"))},
            "status": {"state": state, "reason": reasons[state], "ready": state == "current"},
            "result_label": result_label_service.label(project, status=status),
            "design_conditions": conditions,
            "totals": {"peak_kw": total_kw, "sensible_kw": _number(peak.get("sensible_kw")),
                       "latent_kw": _number(peak.get("latent_kw")), "month": month,
                       "hour": house_hour, "safety_factor": _number(peak.get("safety_factor")),
                       "suggested_system": suggested,
                       "household_people": _number((peak.get("household_counted_once") or {}).get("people"))},
            "rooms": rooms, "not_cooled": not_cooled, "not_counted": not_counted, "assumptions": assumptions,
            "open_items": {"count": len(todos), "titles": [str(row.get("title") or "") for row in todos[:5]]}}


def api(web, project):
    return get(web, project)
