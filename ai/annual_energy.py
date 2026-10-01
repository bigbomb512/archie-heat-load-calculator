"""Gated 8,760-hour annual energy analysis.

This module is an orchestration layer.  It reuses the reviewed room and
heating component functions and keeps annual results separate from the
existing design-day reports.  Missing annual evidence fails closed.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
import io
import json
import math

from ai.hourly_loads import (
    DAY_TYPES,
    room_contributions,
    resolved_profiles,
    room_static_missing,
    hour_total,
    validate_hourly_load_model,
    validate_schedule_library,
)
from ai.heating_loads import (
    heating_conduction,
    heating_glazing_conduction,
    heating_air_load,
    heating_infiltration_load,
    heating_internal_gain_credit,
)
from ai.heat_loads import (
    ashrae_saturation_pressure_kpa,
    humidity_ratio_from_db_wb,
    humidity_ratio_from_vapour_pressure,
    saturation_pressure_kpa,
)
from ai.heating_gate import empty_heating_method_gate, heating_gate_is_approved, validate_heating_method_gate
from ai.infiltration_gate import empty_infiltration_method_gate, validate_infiltration_method_gate
from ai.glazing_gate import empty_glazing_method_gate, validate_glazing_method_gate
from ai.shading_gate import empty_shading_method_gate, validate_shading_method_gate
from ai.site_design_conditions import validate_citations


HOURS_PER_YEAR = 8760
MONTHS = tuple(range(1, 13))
METHOD_ID = "annual_energy_analysis_v1"
STATUSES = {"missing", "provisional", "confirmed", "not_applicable"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_annual_weather():
    return {"schema_version": 1, "weather_id": "", "updated_at": "", "source": "", "citations": [], "location": "", "timezone": "", "records": []}


def empty_annual_calendar():
    return {"schema_version": 1, "calendar_id": "", "year": None, "timezone": "", "source": "", "citations": [], "holidays": [], "dates": []}


def empty_annual_radiation():
    return {"schema_version": 1, "radiation_id": "", "updated_at": "", "location": "", "timezone": "", "method": "surface_plane_incident", "source": "", "citations": [], "surfaces": []}


def empty_annual_method_gate():
    return {
        "schema_version": 1, "method_id": METHOD_ID, "method_version": "1.0",
        "approval_status": "placeholder", "engineer_name": "", "engineer_credential": "",
        "approved_at": "", "method_citation": "", "scope": "Full-year hourly cooling, heating, AHU, and plant energy orchestration.",
        "citations": [],
    }


def validate_annual_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Annual method gate must be an object.")
    result = {**empty_annual_method_gate(), **deepcopy(raw)}
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Annual method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in {"placeholder", "approved"}:
        raise ValueError("Annual method gate approval status must be placeholder or approved.")
    for key in ("method_version", "engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[key] = str(result.get(key, "") or "").strip()
    result["citations"] = validate_citations(result.get("citations", []), "Annual method gate")
    if result["approval_status"] == "approved":
        required = ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope")
        if any(not result[key] for key in required) or not result["citations"]:
            raise ValueError("Approved annual method gate requires engineer metadata, scope, and citations.")
    result["fingerprint"] = fingerprint(result)
    return result


def annual_gate_is_approved(gate):
    return bool(gate and gate.get("method_id") == METHOD_ID and gate.get("approval_status") == "approved")


def _number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number.")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}.")
    return value


def _citation_list(value, label):
    return validate_citations(value or [], label)


def validate_annual_weather(raw):
    if not isinstance(raw, dict):
        raise ValueError("Annual weather must be an object.")
    result = {**empty_annual_weather(), **deepcopy(raw)}
    result["weather_id"] = str(result.get("weather_id", "") or "").strip()
    result["location"] = str(result.get("location", "") or "").strip()
    result["timezone"] = str(result.get("timezone", "") or "").strip()
    result["source"] = str(result.get("source", "") or "").strip()
    result["citations"] = _citation_list(result.get("citations"), "Annual weather")
    records = result.get("records")
    if not result["weather_id"] or not result["location"] or not result["timezone"] or not result["source"] or not result["citations"]:
        raise ValueError("Annual weather requires ID, location, timezone, source, and citations.")
    if not isinstance(records, list) or len(records) != HOURS_PER_YEAR:
        raise ValueError("Annual weather requires exactly 8,760 hourly records.")
    checked = []
    previous_wall_time = None
    source_utc_offset = None
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f"Annual weather hour {index} must be an object.")
        hour_index = row.get("hour_index", index)
        if hour_index != index:
            raise ValueError("Annual weather hour_index values must run from 0 through 8,759 in order.")
        stamp = str(row.get("timestamp", "") or "").strip()
        if not stamp:
            raise ValueError(f"Annual weather hour {index} requires a local timestamp.")
        try:
            parsed_stamp = datetime.fromisoformat(stamp)
        except ValueError as error:
            raise ValueError(f"Annual weather hour {index} timestamp is invalid.") from error
        wall_time = parsed_stamp.replace(tzinfo=None)
        utc_offset = parsed_stamp.utcoffset()
        if index == 0:
            source_utc_offset = utc_offset
        elif utc_offset != source_utc_offset:
            raise ValueError(
                "Annual weather timestamps must use one fixed time offset; "
                "normalize daylight-saving transitions before import."
            )
        if previous_wall_time is not None and wall_time - previous_wall_time != timedelta(hours=1):
            raise ValueError(
                f"Annual weather timestamp at hour {index} must be exactly one local hour "
                "after the prior record; missing or repeated hours require source normalization."
            )
        previous_wall_time = wall_time
        db = _number(row.get("outdoor_dry_bulb_c"), f"Annual weather hour {index} dry-bulb")
        wb = row.get("outdoor_wet_bulb_c")
        dew = row.get("outdoor_dew_point_c")
        if wb is None and dew is None:
            raise ValueError(f"Annual weather hour {index} needs wet-bulb or dew-point data.")
        if wb is not None:
            wb = _number(wb, f"Annual weather hour {index} wet-bulb")
            if wb > db:
                raise ValueError(f"Annual weather hour {index} wet-bulb cannot exceed dry-bulb.")
        basis = row.get("outdoor_wet_bulb_basis")
        from ai.heat_loads import WET_BULB_BASES
        if basis is not None and basis not in WET_BULB_BASES:
            raise ValueError(f"Annual weather hour {index} has an invalid wet-bulb basis.")
        if dew is not None:
            dew = _number(dew, f"Annual weather hour {index} dew-point")
            if dew > db:
                raise ValueError(f"Annual weather hour {index} dew-point cannot exceed dry-bulb.")
        pressure = _number(row.get("atmospheric_pressure_kpa"), f"Annual weather hour {index} pressure", 50, 120)
        checked.append({
            "hour_index": index,
            "timestamp": stamp,
            "outdoor_dry_bulb_c": db,
            "outdoor_wet_bulb_c": wb,
            "outdoor_wet_bulb_basis": basis,
            "outdoor_dew_point_c": dew,
            "atmospheric_pressure_kpa": pressure,
            "wind_speed_m_s": _optional_number(row.get("wind_speed_m_s"), f"Annual weather hour {index} wind speed"),
            "wind_direction_deg": _optional_angle(row.get("wind_direction_deg"), f"Annual weather hour {index} wind direction"),
            "global_horizontal_w_m2": _optional_nonnegative(row.get("global_horizontal_w_m2"), f"Annual weather hour {index} global radiation"),
            "direct_normal_w_m2": _optional_nonnegative(row.get("direct_normal_w_m2"), f"Annual weather hour {index} direct radiation"),
            "diffuse_horizontal_w_m2": _optional_nonnegative(row.get("diffuse_horizontal_w_m2"), f"Annual weather hour {index} diffuse radiation"),
        })
    result["records"] = checked
    result["fingerprint"] = fingerprint({key: result[key] for key in result if key not in {"updated_at", "fingerprint"}})
    return result


def _optional_number(value, label):
    return None if value in (None, "") else _number(value, label)


def _optional_nonnegative(value, label):
    return None if value in (None, "") else _number(value, label, 0)


def _optional_angle(value, label):
    return None if value in (None, "") else _number(value, label, 0, 360)


def import_epw(text, *, weather_id, source, citations, location, timezone_name):
    """Normalize a standard EPW file into the annual weather contract."""
    if not isinstance(text, str):
        raise ValueError("EPW input must be text.")
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    data = rows[8:] if len(rows) >= 8 else []
    if len(data) == 8784:
        raise ValueError("Leap-year EPW files with 8,784 records must be normalized externally before import.")
    if len(data) != HOURS_PER_YEAR:
        raise ValueError("EPW input must contain exactly 8,760 hourly records.")
    records = []
    for index, line in enumerate(data):
        fields = [item.strip() for item in line.split(",")]
        if len(fields) < 22:
            raise ValueError(f"EPW record {index} is incomplete.")
        year, month, day, hour = (int(fields[0]), int(fields[1]), int(fields[2]), int(fields[3]) - 1)
        stamp = datetime(year, month, day, hour).isoformat()
        records.append({
            "hour_index": index, "timestamp": stamp,
            "outdoor_dry_bulb_c": float(fields[6]), "outdoor_dew_point_c": float(fields[7]),
            "atmospheric_pressure_kpa": float(fields[9]) / 1000.0,
            "global_horizontal_w_m2": float(fields[13]), "direct_normal_w_m2": float(fields[14]),
            "diffuse_horizontal_w_m2": float(fields[15]), "wind_direction_deg": float(fields[20]),
            "wind_speed_m_s": float(fields[21]),
        })
    return validate_annual_weather({
        "schema_version": 1, "weather_id": weather_id, "location": location,
        "timezone": timezone_name, "source": source, "citations": citations, "records": records,
        "conversion": "EPW columns normalized; dew point retained as humidity basis.",
    })


def validate_annual_calendar(raw):
    if not isinstance(raw, dict):
        raise ValueError("Annual calendar must be an object.")
    result = {**empty_annual_calendar(), **deepcopy(raw)}
    result["calendar_id"] = str(result.get("calendar_id", "") or "").strip()
    result["timezone"] = str(result.get("timezone", "") or "").strip()
    result["source"] = str(result.get("source", "") or "").strip()
    result["citations"] = _citation_list(result.get("citations"), "Annual calendar")
    year = result.get("year")
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError("Annual calendar year must be an integer.")
    start = date(year, 1, 1)
    dates = result.get("dates")
    if not isinstance(dates, list) or len(dates) != 365:
        raise ValueError("Annual calendar requires exactly 365 dates.")
    holidays = result.get("holidays", [])
    if not isinstance(holidays, list):
        raise ValueError("Annual calendar holidays must be a list.")
    holiday_map = {}
    for holiday in holidays:
        if not isinstance(holiday, dict):
            raise ValueError("Annual holiday entries must be objects.")
        day = str(holiday.get("date", ""))
        if day in holiday_map or not day:
            raise ValueError("Annual holiday dates must be unique and non-empty.")
        datetime.strptime(day, "%Y-%m-%d")
        holiday_map[day] = str(holiday.get("name", day))
    checked = []
    for index, row in enumerate(dates):
        expected = start + timedelta(days=index)
        if not isinstance(row, dict) or row.get("date") != expected.isoformat():
            raise ValueError(f"Annual calendar date {index} must be {expected.isoformat()}.")
        day = expected.isoformat()
        day_type = "sunday_holiday" if expected.weekday() == 6 or day in holiday_map else "saturday" if expected.weekday() == 5 else "weekday"
        supplied = row.get("day_type", day_type)
        if supplied not in DAY_TYPES or supplied != day_type:
            raise ValueError(f"Annual calendar day type for {day} does not match the explicit calendar/holiday mapping.")
        checked.append({"date": day, "day_of_year": index + 1, "day_type": day_type, "holiday_name": holiday_map.get(day, "")})
    if not result["calendar_id"] or not result["timezone"] or not result["source"] or not result["citations"]:
        raise ValueError("Annual calendar requires ID, timezone, source, and citations.")
    result["dates"] = checked
    result["fingerprint"] = fingerprint({key: result[key] for key in result if key != "fingerprint"})
    return result


def validate_annual_radiation(raw):
    if not isinstance(raw, dict):
        raise ValueError("Annual radiation must be an object.")
    result = {**empty_annual_radiation(), **deepcopy(raw)}
    for key in ("radiation_id", "location", "timezone", "method", "source"):
        result[key] = str(result.get(key, "") or "").strip()
    result["citations"] = _citation_list(result.get("citations"), "Annual radiation")
    surfaces = result.get("surfaces")
    if not isinstance(surfaces, list):
        raise ValueError("Annual radiation surfaces must be a list.")
    seen = set()
    checked = []
    for index, item in enumerate(surfaces):
        if not isinstance(item, dict):
            raise ValueError(f"Annual radiation surface {index} must be an object.")
        surface_id = str(item.get("surface_id", "") or "").strip()
        if not surface_id or surface_id in seen:
            raise ValueError("Annual radiation surface IDs must be unique and non-empty.")
        owner_room_id = str(item.get("owner_room_id", "") or "").strip()
        orientation = str(item.get("orientation", "") or "").strip()
        if not owner_room_id or not orientation:
            raise ValueError(f"Annual radiation surface {surface_id} requires an owning room and reviewed orientation.")
        values = item.get("irradiance_w_m2")
        if not isinstance(values, list) or len(values) != HOURS_PER_YEAR:
            raise ValueError(f"Annual radiation surface {surface_id} requires 8,760 values.")
        source = str(item.get("source", result["source"]) or "").strip()
        citations = _citation_list(item.get("citations", result["citations"]), f"Annual radiation {surface_id}")
        if not source or not citations:
            raise ValueError(f"Annual radiation surface {surface_id} requires source and citations.")
        checked.append({"surface_id": surface_id, "owner_room_id": owner_room_id, "orientation": orientation, "irradiance_w_m2": [_number(value, f"Annual radiation {surface_id}", 0) for value in values], "source": source, "citations": citations})
        seen.add(surface_id)
    if surfaces and (not result["radiation_id"] or not result["location"] or not result["timezone"] or not result["source"] or not result["citations"]):
        raise ValueError("Annual radiation requires ID, location, timezone, source, and citations.")
    result["surfaces"] = checked
    result["fingerprint"] = fingerprint({key: result[key] for key in result if key not in {"updated_at", "fingerprint"}})
    return result


def _annual_weather_row(row):
    wb, basis = _resolved_annual_wet_bulb(
        row["outdoor_dry_bulb_c"], row.get("outdoor_wet_bulb_c"), row.get("outdoor_wet_bulb_basis"),
        row.get("outdoor_dew_point_c"), row["atmospheric_pressure_kpa"],
    )
    return {
        "outdoor_dry_bulb_c": {"value": row["outdoor_dry_bulb_c"]},
        "outdoor_wet_bulb_c": {"value": wb},
        "outdoor_wet_bulb_basis": basis,
    }


def _resolved_annual_wet_bulb(dry_bulb_c, wet_bulb_c, wet_bulb_basis, dew_point_c, pressure_kpa):
    if wet_bulb_c is not None:
        return wet_bulb_c, wet_bulb_basis
    wet_bulb_c = _wet_bulb_from_dew_point(dry_bulb_c, dew_point_c, pressure_kpa)
    basis = "thermodynamic" if dew_point_c >= 0 else "legacy_unverified"
    return wet_bulb_c, basis


def _wet_bulb_from_dew_point(dry_bulb_c, dew_point_c, pressure_kpa):
    """Convert a cited dew point to wet bulb without treating dew point as wet bulb."""
    if dew_point_c is None:
        raise ValueError("Annual weather requires wet-bulb or dew-point data.")
    if dew_point_c >= 0:
        target_vapour = ashrae_saturation_pressure_kpa(dew_point_c)
        target_ratio = humidity_ratio_from_vapour_pressure(target_vapour, pressure_kpa)
        low = 0.0
        high = float(dry_bulb_c)
        if high < low:
            raise ValueError("A nonnegative dew point cannot exceed a sub-zero dry-bulb state.")
        for _ in range(80):
            mid = (low + high) / 2.0
            ratio = humidity_ratio_from_db_wb(dry_bulb_c, mid, pressure_kpa, "thermodynamic")
            if ratio > target_ratio:
                high = mid
            else:
                low = mid
        return round((low + high) / 2.0, 6)

    # A sub-zero weather dew point does not say whether the source reports a
    # frost point or a supercooled-water dew point. Preserve the old explicitly
    # unverified conversion rather than silently choosing an ice reference.
    target_vapour = saturation_pressure_kpa(dew_point_c)
    target_ratio = humidity_ratio_from_vapour_pressure(target_vapour, pressure_kpa)
    low, high = -80.0, float(dry_bulb_c)
    for _ in range(80):
        mid = (low + high) / 2.0
        try:
            ratio = humidity_ratio_from_db_wb(dry_bulb_c, mid, pressure_kpa)
        except ValueError:
            low = mid
            continue
        if ratio > target_ratio:
            high = mid
        else:
            low = mid
    return round((low + high) / 2.0, 6)


def _profile_for_hour(schedule_library, day_type, room, hour):
    profiles, missing, provisional = resolved_profiles(schedule_library, day_type, room)
    return profiles, missing, provisional, hour % 24


def calculate_annual_report(requirements, schedule_library, model, annual_weather, annual_calendar, *, annual_radiation=None, annual_gate=None, heating_gate=None, infiltration_gate=None, glazing_gate=None, shading_gate=None, dynamic_mass_gate=None, radiation_gate=None, project_context=None, calculator_input_snapshot_fingerprint="", selected_sections=None, ahu_systems=None, plant_systems=None):
    """Calculate annual room loads and safe AHU/plant summaries."""
    weather = validate_annual_weather(annual_weather)
    calendar = validate_annual_calendar(annual_calendar)
    schedules = validate_schedule_library(schedule_library)
    model = validate_hourly_load_model(model)
    gate = validate_annual_method_gate(annual_gate or empty_annual_method_gate())
    radiation = validate_annual_radiation(annual_radiation or empty_annual_radiation())
    heating_gate = validate_heating_method_gate(heating_gate or empty_heating_method_gate())
    infiltration_gate = validate_infiltration_method_gate(infiltration_gate or empty_infiltration_method_gate())
    glazing_gate = validate_glazing_method_gate(glazing_gate or empty_glazing_method_gate())
    shading_gate = validate_shading_method_gate(shading_gate or empty_shading_method_gate())
    allowed_sections = {"cooling", "heating", "ahu", "plant"}
    selected_sections = set(allowed_sections if selected_sections is None else selected_sections)
    report = {
        "schema_version": 1, "report_type": "annual_energy_report", "status": "blocked", "validated": False,
        "input_snapshot_fingerprint": calculator_input_snapshot_fingerprint,
        "input_fingerprints": {"annual_weather": weather.get("fingerprint", ""), "annual_calendar": calendar.get("fingerprint", ""), "annual_radiation": radiation.get("fingerprint", ""), "annual_method_gate": gate.get("fingerprint", "")},
        "selected_sections": sorted(selected_sections), "rooms": [], "cooling": _empty_section(), "heating": _empty_section(), "ahu": _empty_section(), "plant": _empty_section(),
        "annual_inputs": {
            "weather_id": weather.get("weather_id", ""), "weather_location": weather.get("location", ""),
            "weather_timezone": weather.get("timezone", ""), "calendar_id": calendar.get("calendar_id", ""),
            "calendar_year": calendar.get("year"), "radiation_id": radiation.get("radiation_id", ""),
            "weather_citations": deepcopy(weather.get("citations", [])),
            "calendar_citations": deepcopy(calendar.get("citations", [])),
            "radiation_citations": deepcopy(radiation.get("citations", [])),
        },
        "blocked_reasons": [], "excluded_components": [], "readiness": {"status": "blocked", "issues": []}, "scope_summary": {"active_room_ids": [], "included_room_ids": [], "blocked_rooms": [], "complete_scope": False},
    }
    if not calculator_input_snapshot_fingerprint:
        report["blocked_reasons"].append("A current calculator-input snapshot is required for annual analysis.")
    unknown_sections = selected_sections - allowed_sections
    if not selected_sections:
        report["blocked_reasons"].append("At least one annual report section must be selected.")
    if unknown_sections:
        report["blocked_reasons"].append("Unsupported annual report section(s): " + ", ".join(sorted(unknown_sections)) + ".")
    if len(weather["records"]) != HOURS_PER_YEAR or len(calendar["dates"]) != 365:
        report["blocked_reasons"].append("Annual weather and calendar must cover exactly 8,760 local hours.")
    if weather["timezone"] != calendar["timezone"]:
        report["blocked_reasons"].append("Annual weather and calendar timezones must match before hourly schedules can be aligned.")
    if radiation.get("surfaces") and radiation["timezone"] != calendar["timezone"]:
        report["blocked_reasons"].append("Annual radiation and calendar timezones must match before hourly solar values can be aligned.")
    if report["blocked_reasons"]:
        return report
    zones = {item["zone_id"]: item for item in model["zones"]}
    floors = {item["floor_id"]: item for item in model["floors"]}
    annual_rooms = []
    for room in model["rooms"]:
        zone = zones.get(room.get("zone_id"), {})
        room_result = _annual_room(room, zone, floors, schedules, weather, calendar, radiation, infiltration_gate, glazing_gate, shading_gate, dynamic_mass_gate, radiation_gate, heating_gate)
        annual_rooms.append(room_result)
    report["rooms"] = annual_rooms
    month_by_hour = [int(day["date"][5:7]) for day in calendar["dates"] for _ in range(24)]
    calculated = [item for item in annual_rooms if item.get("status") != "blocked"]
    report["scope_summary"] = {"active_room_ids": [item["room_id"] for item in annual_rooms], "included_room_ids": [item["room_id"] for item in calculated], "blocked_rooms": [{"room_id": item["room_id"], "reasons": item.get("blocked_reasons", [])} for item in annual_rooms if item.get("status") == "blocked"], "complete_scope": bool(annual_rooms) and len(calculated) == len(annual_rooms) and all(item.get("status") == "review_ready" for item in annual_rooms)}
    if "cooling" in selected_sections:
        report["cooling"] = _aggregate_annual_section(calculated, "cooling", zones=zones, floors=floors, month_by_hour=month_by_hour)
    if "heating" in selected_sections:
        report["heating"] = _aggregate_annual_section(calculated, "heating", zones=zones, floors=floors, month_by_hour=month_by_hour)
    if "ahu" in selected_sections:
        report["ahu"] = _aggregate_ahu(calculated, ahu_systems or {})
    if "plant" in selected_sections:
        report["plant"] = _aggregate_plant(report["ahu"], plant_systems or {})
    report["excluded_components"] = sorted(set(item for row in annual_rooms for item in row.get("excluded_components", [])))
    report["readiness"] = _annual_readiness(report, gate, heating_gate)
    report["status"] = report["readiness"]["status"]
    return report


def _annual_room(room, zone, floors, schedules, weather, calendar, radiation, infiltration_gate, glazing_gate, shading_gate, dynamic_mass_gate, radiation_gate, heating_gate):
    cooling_load = room.get("cooling_load", {})
    cooling_factor = cooling_load.get("safety_factor")
    heating_factor = room.get("heating_safety_factor")
    result = {"room_id": room["room_id"], "name": room.get("name", room["room_id"]), "zone_id": room.get("zone_id", ""), "status": "blocked", "cooling_hours": [], "heating_hours": [], "cooling": _empty_summary(), "heating": _empty_summary(), "blocked_reasons": [], "excluded_components": [], "safety_factor_application": {
        "cooling": {"factor": cooling_factor, "basis": "legacy_room_factor", "source": cooling_load.get("source", ""), "citations": [], "citation_status": "not_recorded_on_cooling_factor", "status": "pending" if cooling_factor is not None else "missing", "applied_at": ""},
        "heating": {"factor": heating_factor, "basis": "legacy_room_factor", "source": room.get("heating_safety_factor_source", ""), "citations": deepcopy(room.get("heating_safety_factor_citations", [])), "status": "pending" if heating_factor is not None else "missing", "applied_at": ""},
    }}
    missing = room_static_missing(room, zone, infiltration_gate, glazing_gate)
    if missing:
        result["blocked_reasons"] = sorted(set(missing))
        for mode in ("cooling", "heating"):
            if result["safety_factor_application"][mode]["status"] == "pending":
                result["safety_factor_application"][mode]["status"] = "not_applied_blocked"
        return result
    dynamic_states = {surface["surface_id"]: surface.get("dynamic_thermal_mass", {}).get("initial_state_temperature_c", room["indoor_cooling_setpoint_c"]) for surface in room.get("cooling_load", {}).get("envelope_surfaces", []) if surface.get("dynamic_thermal_mass", {}).get("enabled")}
    cooling_blocked = []
    heating_blocked = []
    for index, weather_row in enumerate(weather["records"]):
        calendar_row = calendar["dates"][index // 24]
        hour = index % 24
        profiles, profile_missing, provisional = _profile_for_hour(schedules, calendar_row["day_type"], room, hour)
        if profile_missing:
            cooling_blocked.extend(profile_missing)
            continue
        source_for_hour = _radiation_source_for_hour(radiation, index)
        weather_payload = _annual_weather_row(weather_row)
        try:
            cooling_parts = room_contributions(room, zone, infiltration_gate, profiles, hour, weather_payload, weather_row["atmospheric_pressure_kpa"], glazing_gate=glazing_gate, shading_gate=shading_gate, dynamic_mass_gate=dynamic_mass_gate, radiation_gate=radiation_gate, radiation_source=source_for_hour, dynamic_states=dynamic_states)
            cooling_hour = hour_total(hour, cooling_parts, room["cooling_load"]["safety_factor"])
            cooling_hour["hour_index"] = index
            cooling_hour["timestamp"] = weather_row.get("timestamp", "")
            cooling_hour["month"] = int(weather_row.get("timestamp", "")[5:7]) if len(weather_row.get("timestamp", "")) >= 7 else min(12, (index // 730) + 1)
            result["cooling_hours"].append(cooling_hour)
            heating_hour, heating_errors = _annual_heating_hour(room, zone, profiles, hour, weather_row, heating_gate, infiltration_gate, glazing_gate)
            heating_hour["hour_index"] = index
            heating_hour["timestamp"] = weather_row.get("timestamp", "")
            heating_hour["month"] = cooling_hour["month"]
            if "cited heating safety factor is missing" not in heating_errors:
                result["heating_hours"].append(heating_hour)
            heating_blocked.extend(heating_errors)
            if provisional:
                result["excluded_components"].append("provisional annual schedule evidence")
        except (KeyError, TypeError, ValueError) as error:
            cooling_blocked.append(str(error))
    if not result["cooling_hours"]:
        for mode in ("cooling", "heating"):
            if result["safety_factor_application"][mode]["status"] == "pending":
                result["safety_factor_application"][mode]["status"] = "not_applied_blocked"
        result["blocked_reasons"] = sorted(set(cooling_blocked or ["No annual cooling hours were calculable."]))
        return result
    if result["cooling_hours"] and result["safety_factor_application"]["cooling"]["status"] == "pending":
        result["safety_factor_application"]["cooling"].update({"status": "applied", "applied_at": "room-hour"})
    if result["heating_hours"] and result["safety_factor_application"]["heating"]["status"] == "pending":
        result["safety_factor_application"]["heating"].update({"status": "applied", "applied_at": "room-hour"})
    result["cooling"] = _annual_summary(result["cooling_hours"], "design_total_kw")
    result["heating"] = _annual_summary(result["heating_hours"], "design_total_kw") if result["heating_hours"] else _empty_summary()
    result["blocked_reasons"] = sorted(set(cooling_blocked + heating_blocked))
    result["status"] = "review_ready" if not result["blocked_reasons"] and heating_gate_is_approved(heating_gate) else "draft"
    return result


def _annual_heating_hour(room, zone, profiles, hour, weather, heating_gate, infiltration_gate, glazing_gate):
    setpoint = room.get("indoor_heating_setpoint_c")
    blockers = []
    if setpoint in (None, ""):
        blockers.append("heating setpoint is missing")
    if room.get("heating_safety_factor") is None:
        blockers.append("cited heating safety factor is missing")
    if blockers:
        return {"hour": hour, "components": {}, "design_total_kw": 0.0, "subtotal_kw": 0.0, "safety_allowance_kw": 0.0}, blockers
    outdoor_db = weather["outdoor_dry_bulb_c"]
    outdoor_wb, resolved_outdoor_wb_basis = _resolved_annual_wet_bulb(
        outdoor_db, weather.get("outdoor_wet_bulb_c"), weather.get("outdoor_wet_bulb_basis"),
        weather.get("outdoor_dew_point_c"), weather["atmospheric_pressure_kpa"],
    )
    pressure = weather["atmospheric_pressure_kpa"]
    envelope, blocked = heating_conduction(room["cooling_load"].get("envelope_surfaces", []), outdoor_db, setpoint)
    glazing, glazing_blocked = heating_glazing_conduction(room, outdoor_db, setpoint, glazing_gate)
    contributions = [envelope, glazing]
    flow = room["cooling_load"].get("outside_air_lps")
    if flow:
        conditions = room["cooling_load_conditions"]
        contributions.append(heating_air_load(flow * profiles.get("outside_air", [0.0] * 24)[hour], setpoint, conditions.get("indoor_cooling_wet_bulb_c", setpoint), outdoor_db, outdoor_wb, pressure, indoor_wet_bulb_basis=conditions.get("indoor_wet_bulb_basis", "legacy_unverified"), outdoor_wet_bulb_basis=resolved_outdoor_wb_basis or conditions.get("outdoor_wet_bulb_basis", "legacy_unverified"), airflow_reference_basis=room["cooling_load"].get("outside_air_flow_reference_basis", "legacy_unverified")))
    infiltration = next((item for item in room.get("unapproved_components", []) if item.get("component_type") == "infiltration" and item.get("calculation_status") == "calculated"), None)
    if infiltration:
        conditions = room["cooling_load_conditions"]
        contributions.append(heating_infiltration_load(infiltration["value"], infiltration["unit"], setpoint, conditions.get("indoor_cooling_wet_bulb_c", setpoint), outdoor_db, outdoor_wb, pressure, room_volume_m3=room.get("area_m2", 0) * (room.get("ceiling_height_mm", 0) or zone.get("ceiling_height_mm", 0)) / 1000.0, schedule_factor=profiles.get("infiltration", [0.0] * 24)[hour], method_id=infiltration.get("method_id", ""), gate_version=infiltration_gate.get("updated_at", ""), indoor_wet_bulb_basis=conditions.get("indoor_wet_bulb_basis", "legacy_unverified"), outdoor_wet_bulb_basis=resolved_outdoor_wb_basis or conditions.get("outdoor_wet_bulb_basis", "legacy_unverified")))
    gross = round(sum(max(item["sensible_kw"], 0.0) for item in contributions), 6)
    credit, _ = heating_internal_gain_credit(room, profiles, hour, gross)
    net = max(0.0, gross + credit["sensible_kw"])
    factor = float(room["heating_safety_factor"])
    allowance = round(net * (factor - 1.0), 6)
    rows = {item["name"]: {"sensible_kw": item["sensible_kw"], "latent_kw": 0.0, "total_kw": item["total_kw"], "inputs": item.get("inputs", {}), "formula": item.get("formula", "")} for item in [*contributions, credit]}
    return {"hour": hour, "components": rows, "gross_heating_sensible_kw": gross, "net_heating_sensible_kw": net, "subtotal_kw": net, "safety_factor": factor, "safety_allowance_kw": allowance, "design_total_kw": round(net + allowance, 6), "blocked_surfaces": blocked + glazing_blocked}, sorted({row.get("reason", "") for row in blocked + glazing_blocked if row.get("reason")})


def _radiation_source_for_hour(radiation, index):
    if not radiation.get("surfaces"):
        return {}
    return {
        "source_id": radiation.get("radiation_id", "annual"),
        "fingerprint": radiation.get("fingerprint", ""),
        "annual_irradiance_by_surface": {
            item["surface_id"]: item["irradiance_w_m2"][index]
            for item in radiation.get("surfaces", [])
        },
        "hours": [{"hour": hour, "irradiance_w_m2": 0.0} for hour in range(24)],
    }


def _empty_summary():
    return {"annual_energy_kwh": 0.0, "peak_kw": 0.0, "peak_hours": [], "monthly_kwh": {str(month): 0.0 for month in MONTHS}, "hours": []}


def _empty_section():
    return {"status": "not_selected", "annual_energy_kwh": 0.0, "peak_kw": 0.0, "peak_hours": [], "monthly_kwh": {str(month): 0.0 for month in MONTHS}, "hours": [], "blocked_reasons": []}


def _annual_summary(hours, key):
    summary = _empty_summary()
    summary["hours"] = deepcopy(hours)
    for row in hours:
        value = float(row.get(key, row.get("subtotal_kw", 0.0)) or 0.0)
        summary["annual_energy_kwh"] += value
        stamp = row.get("timestamp", "")
        month = int(row.get("month") or (int(stamp[5:7]) if len(stamp) >= 7 and stamp[4] == "-" else 1))
        summary["monthly_kwh"][str(month)] += value
    summary["annual_energy_kwh"] = round(summary["annual_energy_kwh"], 6)
    summary["monthly_kwh"] = {key: round(value, 6) for key, value in summary["monthly_kwh"].items()}
    peak = max((float(row.get(key, row.get("subtotal_kw", 0.0)) or 0.0) for row in hours), default=0.0)
    summary["peak_kw"] = round(peak, 6)
    summary["peak_hours"] = [row.get("hour_index", row.get("hour")) for row in hours if float(row.get(key, row.get("subtotal_kw", 0.0)) or 0.0) == peak]
    return summary


def _aggregate_annual_section(rooms, section, *, zones=None, floors=None, month_by_hour=None):
    hours = []
    missing_hours_by_room = {}
    rows_by_room = {}
    for room in rooms:
        indexed = _index_annual_hours(room.get(f"{section}_hours", []))
        rows_by_room[room["room_id"]] = indexed
        missing = [index for index in range(HOURS_PER_YEAR) if index not in indexed]
        if missing:
            missing_hours_by_room[room["room_id"]] = {
                "count": len(missing), "ranges": _hour_index_ranges(missing),
            }
    for index in range(HOURS_PER_YEAR):
        total = sum(float(rows_by_room[room["room_id"]][index].get("design_total_kw", 0.0)) for room in rooms if index in rows_by_room[room["room_id"]])
        month = (month_by_hour[index] if month_by_hour and index < len(month_by_hour)
                 else next((rows_by_room[room["room_id"]][index].get("month", 1) for room in rooms if index in rows_by_room[room["room_id"]]), 1))
        missing_room_ids = [room["room_id"] for room in rooms if index not in rows_by_room[room["room_id"]]]
        hours.append({"hour_index": index, "month": month, "demand_kw": round(total, 6), "energy_kwh": round(total, 6),
                      "status": "incomplete" if missing_room_ids else "calculated" if rooms else "not_calculated",
                      "missing_room_ids": missing_room_ids})
    summary = _annual_summary(hours, "demand_kw")
    summary["incomplete_room_hours"] = missing_hours_by_room
    summary["blocked_reasons"] = [
        f"Room {room_id} has {details['count']} missing {section} hour(s); annual energy excludes those hours."
        for room_id, details in sorted(missing_hours_by_room.items())
    ]
    summary["monthly_incomplete_hours"] = {
        str(month): sum(1 for row in hours if row["month"] == month and row["status"] == "incomplete")
        for month in MONTHS
    }
    summary["status"] = "review_ready" if rooms and not missing_hours_by_room and all(room.get("status") == "review_ready" for room in rooms) else "draft" if rooms else "blocked"
    zone_rows = {}
    for room in rooms:
        zone_id = room.get("zone_id", "")
        if zone_id:
            zone_rows.setdefault(zone_id, []).append(room)
    summary["zone_summaries"] = {
        zone_id: _annual_group_summary(group, section, month_by_hour=month_by_hour)
        for zone_id, group in zone_rows.items()
    }
    floor_rows = {}
    for zone_id, group in zone_rows.items():
        floor_id = (zones or {}).get(zone_id, {}).get("floor_id", "")
        if floor_id:
            floor_rows.setdefault(floor_id, []).extend(group)
    summary["floor_summaries"] = {
        floor_id: _annual_group_summary(group, section, month_by_hour=month_by_hour)
        for floor_id, group in floor_rows.items()
    }
    return summary


def _annual_group_summary(rooms, section, *, month_by_hour=None):
    rows_by_room = {room["room_id"]: _index_annual_hours(room.get(f"{section}_hours", [])) for room in rooms}
    missing_hours_by_room = {}
    for room in rooms:
        missing = [index for index in range(HOURS_PER_YEAR) if index not in rows_by_room[room["room_id"]]]
        if missing:
            missing_hours_by_room[room["room_id"]] = {
                "count": len(missing), "ranges": _hour_index_ranges(missing),
            }
    hours = []
    for index in range(HOURS_PER_YEAR):
        total = sum(
            float(rows_by_room[room["room_id"]][index].get("design_total_kw", 0.0))
            for room in rooms if index in rows_by_room[room["room_id"]]
        )
        month = (month_by_hour[index] if month_by_hour and index < len(month_by_hour)
                 else next((rows_by_room[room["room_id"]][index].get("month", 1) for room in rooms if index in rows_by_room[room["room_id"]]), 1))
        missing_room_ids = [room["room_id"] for room in rooms if index not in rows_by_room[room["room_id"]]]
        hours.append({"hour_index": index, "month": month, "demand_kw": round(total, 6), "energy_kwh": round(total, 6),
                      "status": "incomplete" if missing_room_ids else "calculated", "missing_room_ids": missing_room_ids})
    summary = _annual_summary(hours, "demand_kw")
    summary["incomplete_room_hours"] = missing_hours_by_room
    summary["monthly_incomplete_hours"] = {
        str(month): sum(1 for row in hours if row["month"] == month and row["status"] == "incomplete")
        for month in MONTHS
    }
    summary["status"] = "draft" if missing_hours_by_room else "calculated"
    return summary


def _index_annual_hours(rows):
    """Index sparse annual results by their source hour; never compact gaps."""
    indexed = {}
    for row in rows:
        index = row.get("hour_index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < HOURS_PER_YEAR:
            indexed[index] = row
    return indexed


def _hour_index_ranges(indices):
    ranges = []
    for index in indices:
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])
    return ranges


def _aggregate_ahu(rooms, systems):
    if not systems or not systems.get("systems"):
        return {**_empty_section(), "status": "blocked", "blocked_reasons": ["No explicit AHU systems are configured."]}
    rows = []
    for system in systems.get("systems", []):
        served_zones = set(system.get("served_zone_ids", []))
        hours = [{"hour_index": index, "demand_kw": round(sum(room["cooling_hours"][index].get("design_total_kw", 0.0) for room in rooms if room.get("zone_id") in served_zones and len(room.get("cooling_hours", [])) > index), 6)} for index in range(HOURS_PER_YEAR)]
        rows.extend(hours)
    summary = _annual_summary(rows, "demand_kw")
    summary["status"] = "draft"
    summary["energy_basis"] = "coincident_room_load_passthrough"
    summary["blocked_reasons"] = ["Annual AHU coil psychrometrics are not yet available; this is a coincident room-load subtotal."]
    return summary


def _aggregate_plant(ahu, systems):
    if not systems or not systems.get("systems"):
        return {**_empty_section(), "status": "blocked", "blocked_reasons": ["No explicit plant systems are configured."]}
    summary = deepcopy(ahu)
    summary["status"] = "draft"
    summary["energy_basis"] = "annual_ahu_load_passthrough"
    summary["blocked_reasons"] = ["Annual plant hydraulic and coil-energy models require annual AHU/plant state inputs."]
    return summary


def _annual_readiness(report, gate, heating_gate):
    issues = []
    issues.extend({"status": "blocked", "scope": "project", "reason": reason, "remediation": "Provide the missing annual input before calculating."} for reason in report.get("blocked_reasons", []))
    if not annual_gate_is_approved(gate):
        issues.append({"status": "draft", "scope": "project", "reason": "Annual method gate is not approved.", "remediation": "Add a qualified engineer release before review-ready use."})
    if report["scope_summary"].get("blocked_rooms"):
        issues.extend({"status": "blocked", "scope": "room", "affected_id": item["room_id"], "reason": "; ".join(item["reasons"]), "remediation": "Resolve the cited room inputs or exclude the room explicitly."} for item in report["scope_summary"]["blocked_rooms"])
    if "heating" in report["selected_sections"] and not heating_gate_is_approved(heating_gate):
        issues.append({"status": "draft", "scope": "heating", "reason": "Heating method gate is not approved.", "remediation": "Approve the heating method gate or retain annual heating as draft."})
    if report["ahu"].get("status") == "blocked" and "ahu" in report["selected_sections"]:
        issues.extend({"status": "blocked", "scope": "ahu", "reason": reason, "remediation": "Configure explicit annual AHU inputs."} for reason in report["ahu"].get("blocked_reasons", []))
    if report["plant"].get("status") == "blocked" and "plant" in report["selected_sections"]:
        issues.extend({"status": "blocked", "scope": "plant", "reason": reason, "remediation": "Configure explicit annual plant inputs."} for reason in report["plant"].get("blocked_reasons", []))
    for section_name in ("ahu", "plant"):
        if section_name in report["selected_sections"] and report[section_name].get("status") not in {"review_ready"}:
            issues.append({"status": "draft", "scope": section_name, "reason": f"Annual {section_name.upper()} section is not review-ready.", "remediation": f"Complete the annual {section_name.upper()} state adapter or exclude this section explicitly."})
    has_blocking = any(issue["status"] == "blocked" for issue in issues)
    has_draft = any(issue["status"] == "draft" for issue in issues)
    status = "review_ready" if annual_gate_is_approved(gate) and report["scope_summary"].get("complete_scope") and not has_blocking and not has_draft else "draft" if report["cooling"].get("hours") else "blocked"
    return {"status": status, "issues": issues, "complete_scope": report["scope_summary"].get("complete_scope", False)}


def annual_hourly_csv(report):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["hour_index", "cooling_demand_kw", "heating_demand_kw", "cooling_energy_kwh", "heating_energy_kwh", "cooling_status", "heating_status", "cooling_missing_rooms", "heating_missing_rooms"])
    cooling = {row.get("hour_index"): row for row in report.get("cooling", {}).get("hours", [])}
    heating = {row.get("hour_index"): row for row in report.get("heating", {}).get("hours", [])}
    for index in range(HOURS_PER_YEAR):
        c = cooling.get(index, {})
        h = heating.get(index, {})
        c_status, h_status = c.get("status", "not_calculated"), h.get("status", "not_calculated")
        writer.writerow([
            index,
            c.get("demand_kw", "") if c_status == "calculated" else "",
            h.get("demand_kw", "") if h_status == "calculated" else "",
            c.get("energy_kwh", "") if c_status == "calculated" else "",
            h.get("energy_kwh", "") if h_status == "calculated" else "",
            c_status, h_status,
            ";".join(c.get("missing_room_ids", [])), ";".join(h.get("missing_room_ids", [])),
        ])
    return output.getvalue()


def annual_monthly_csv(report):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["month", "cooling_kwh", "cooling_incomplete_hours", "heating_kwh", "heating_incomplete_hours"])
    for month in MONTHS:
        cooling, heating = report.get("cooling", {}), report.get("heating", {})
        writer.writerow([month, cooling.get("monthly_kwh", {}).get(str(month), 0.0), cooling.get("monthly_incomplete_hours", {}).get(str(month), 0),
                         heating.get("monthly_kwh", {}).get(str(month), 0.0), heating.get("monthly_incomplete_hours", {}).get(str(month), 0)])
    return output.getvalue()
