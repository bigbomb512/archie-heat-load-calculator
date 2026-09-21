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
from ai.heat_loads import humidity_ratio_from_db_wb, saturation_pressure_kpa
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
            datetime.fromisoformat(stamp)
        except ValueError as error:
            raise ValueError(f"Annual weather hour {index} timestamp is invalid.") from error
        db = _number(row.get("outdoor_dry_bulb_c"), f"Annual weather hour {index} dry-bulb")
        wb = row.get("outdoor_wet_bulb_c")
        dew = row.get("outdoor_dew_point_c")
        if wb is None and dew is None:
            raise ValueError(f"Annual weather hour {index} needs wet-bulb or dew-point data.")
        if wb is not None:
            wb = _number(wb, f"Annual weather hour {index} wet-bulb")
            if wb > db:
                raise ValueError(f"Annual weather hour {index} wet-bulb cannot exceed dry-bulb.")
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
    wb = row.get("outdoor_wet_bulb_c")
    if wb is None:
        wb = _wet_bulb_from_dew_point(
            row["outdoor_dry_bulb_c"], row.get("outdoor_dew_point_c"), row["atmospheric_pressure_kpa"]
        )
    return {
        "outdoor_dry_bulb_c": {"value": row["outdoor_dry_bulb_c"]},
        "outdoor_wet_bulb_c": {"value": wb},
    }


def _wet_bulb_from_dew_point(dry_bulb_c, dew_point_c, pressure_kpa):
    """Convert a cited dew point to wet bulb without treating dew point as wet bulb."""
    if dew_point_c is None:
        raise ValueError("Annual weather requires wet-bulb or dew-point data.")
    target_vapour = saturation_pressure_kpa(dew_point_c)
    target_ratio = 0.621945 * target_vapour / (pressure_kpa - target_vapour)
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
    selected_sections = set(selected_sections or {"cooling", "heating", "ahu", "plant"})
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
    if len(weather["records"]) != HOURS_PER_YEAR or len(calendar["dates"]) != 365:
        report["blocked_reasons"].append("Annual weather and calendar must cover exactly 8,760 local hours.")
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
    calculated = [item for item in annual_rooms if item.get("status") != "blocked"]
    report["scope_summary"] = {"active_room_ids": [item["room_id"] for item in annual_rooms], "included_room_ids": [item["room_id"] for item in calculated], "blocked_rooms": [{"room_id": item["room_id"], "reasons": item.get("blocked_reasons", [])} for item in annual_rooms if item.get("status") == "blocked"], "complete_scope": bool(annual_rooms) and len(calculated) == len(annual_rooms) and all(item.get("status") == "review_ready" for item in annual_rooms)}
    if "cooling" in selected_sections:
        report["cooling"] = _aggregate_annual_section(calculated, "cooling", zones=zones, floors=floors)
    if "heating" in selected_sections:
        report["heating"] = _aggregate_annual_section(calculated, "heating", zones=zones, floors=floors)
    if "ahu" in selected_sections:
        report["ahu"] = _aggregate_ahu(calculated, ahu_systems or {})
    if "plant" in selected_sections:
        report["plant"] = _aggregate_plant(report["ahu"], plant_systems or {})
    report["excluded_components"] = sorted(set(item for row in annual_rooms for item in row.get("excluded_components", [])))
    report["readiness"] = _annual_readiness(report, gate, heating_gate)
    report["status"] = report["readiness"]["status"]
    return report


def _annual_room(room, zone, floors, schedules, weather, calendar, radiation, infiltration_gate, glazing_gate, shading_gate, dynamic_mass_gate, radiation_gate, heating_gate):
    result = {"room_id": room["room_id"], "name": room.get("name", room["room_id"]), "zone_id": room.get("zone_id", ""), "status": "blocked", "cooling_hours": [], "heating_hours": [], "cooling": _empty_summary(), "heating": _empty_summary(), "blocked_reasons": [], "excluded_components": []}
    missing = room_static_missing(room, zone, infiltration_gate, glazing_gate)
    if missing:
        result["blocked_reasons"] = sorted(set(missing))
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
            result["heating_hours"].append(heating_hour)
            heating_blocked.extend(heating_errors)
            if provisional:
                result["excluded_components"].append("provisional annual schedule evidence")
        except (KeyError, TypeError, ValueError) as error:
            cooling_blocked.append(str(error))
    if not result["cooling_hours"]:
        result["blocked_reasons"] = sorted(set(cooling_blocked or ["No annual cooling hours were calculable."]))
        return result
    result["cooling"] = _annual_summary(result["cooling_hours"], "design_total_kw")
    result["heating"] = _annual_summary(result["heating_hours"], "design_total_kw") if result["heating_hours"] else _empty_summary()
    result["blocked_reasons"] = sorted(set(cooling_blocked + heating_blocked))
    result["status"] = "review_ready" if not result["blocked_reasons"] and heating_gate_is_approved(heating_gate) else "draft"
    return result


def _annual_heating_hour(room, zone, profiles, hour, weather, heating_gate, infiltration_gate, glazing_gate):
    setpoint = room.get("indoor_heating_setpoint_c")
    if setpoint in (None, ""):
        return {"hour": hour, "components": {}, "design_total_kw": 0.0, "subtotal_kw": 0.0, "safety_allowance_kw": 0.0}, ["heating setpoint is missing"]
    outdoor_db = weather["outdoor_dry_bulb_c"]
    outdoor_wb = weather.get("outdoor_wet_bulb_c")
    if outdoor_wb is None:
        outdoor_wb = _wet_bulb_from_dew_point(outdoor_db, weather.get("outdoor_dew_point_c"), weather["atmospheric_pressure_kpa"])
    pressure = weather["atmospheric_pressure_kpa"]
    envelope, blocked = heating_conduction(room["cooling_load"].get("envelope_surfaces", []), outdoor_db, setpoint)
    glazing, glazing_blocked = heating_glazing_conduction(room, outdoor_db, setpoint, glazing_gate)
    contributions = [envelope, glazing]
    flow = room["cooling_load"].get("outside_air_lps")
    if flow:
        contributions.append(heating_air_load(flow * profiles.get("outside_air", [0.0] * 24)[hour], setpoint, room["cooling_load_conditions"].get("indoor_cooling_wet_bulb_c", setpoint), outdoor_db, outdoor_wb, pressure))
    infiltration = next((item for item in room.get("unapproved_components", []) if item.get("component_type") == "infiltration" and item.get("calculation_status") == "calculated"), None)
    if infiltration:
        contributions.append(heating_infiltration_load(infiltration["value"], infiltration["unit"], setpoint, room["cooling_load_conditions"].get("indoor_cooling_wet_bulb_c", setpoint), outdoor_db, outdoor_wb, pressure, room_volume_m3=room.get("area_m2", 0) * (room.get("ceiling_height_mm", 0) or zone.get("ceiling_height_mm", 0)) / 1000.0, schedule_factor=profiles.get("infiltration", [0.0] * 24)[hour], method_id=infiltration.get("method_id", ""), gate_version=infiltration_gate.get("updated_at", "")))
    gross = round(sum(max(item["sensible_kw"], 0.0) for item in contributions), 6)
    credit, _ = heating_internal_gain_credit(room, profiles, hour, gross)
    net = max(0.0, gross + credit["sensible_kw"])
    factor = float(room.get("heating_safety_factor") or 1.0)
    allowance = round(net * (factor - 1.0), 6)
    rows = {item["name"]: {"sensible_kw": item["sensible_kw"], "latent_kw": 0.0, "total_kw": item["total_kw"], "inputs": item.get("inputs", {}), "formula": item.get("formula", "")} for item in [*contributions, credit]}
    return {"hour": hour, "components": rows, "gross_heating_sensible_kw": gross, "net_heating_sensible_kw": net, "subtotal_kw": net, "safety_allowance_kw": allowance, "design_total_kw": round(net + allowance, 6), "blocked_surfaces": blocked + glazing_blocked}, sorted({row.get("reason", "") for row in blocked + glazing_blocked if row.get("reason")})


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


def _aggregate_annual_section(rooms, section, *, zones=None, floors=None):
    hours = []
    for index in range(HOURS_PER_YEAR):
        total = sum(float(room.get(f"{section}_hours", [])[index].get("design_total_kw", 0.0)) for room in rooms if len(room.get(f"{section}_hours", [])) > index)
        month = next((room["cooling_hours"][index].get("month", 1) for room in rooms if len(room.get("cooling_hours", [])) > index), 1)
        hours.append({"hour_index": index, "month": month, "demand_kw": round(total, 6), "energy_kwh": round(total, 6), "status": "calculated"})
    summary = _annual_summary(hours, "demand_kw")
    summary["status"] = "review_ready" if rooms and all(room.get("status") == "review_ready" for room in rooms) else "draft" if rooms else "blocked"
    zone_rows = {}
    for room in rooms:
        zone_id = room.get("zone_id", "")
        if zone_id:
            zone_rows.setdefault(zone_id, []).append(room)
    summary["zone_summaries"] = {
        zone_id: _annual_group_summary(group, section)
        for zone_id, group in zone_rows.items()
    }
    floor_rows = {}
    for zone_id, group in zone_rows.items():
        floor_id = (zones or {}).get(zone_id, {}).get("floor_id", "")
        if floor_id:
            floor_rows.setdefault(floor_id, []).extend(group)
    summary["floor_summaries"] = {
        floor_id: _annual_group_summary(group, section)
        for floor_id, group in floor_rows.items()
    }
    return summary


def _annual_group_summary(rooms, section):
    hours = []
    for index in range(HOURS_PER_YEAR):
        total = sum(
            float(room.get(f"{section}_hours", [])[index].get("design_total_kw", 0.0))
            for room in rooms if len(room.get(f"{section}_hours", [])) > index
        )
        month = next((room[f"{section}_hours"][index].get("month", 1) for room in rooms if len(room.get(f"{section}_hours", [])) > index), 1)
        hours.append({"hour_index": index, "month": month, "demand_kw": round(total, 6), "energy_kwh": round(total, 6)})
    return _annual_summary(hours, "demand_kw")


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
    writer.writerow(["hour_index", "cooling_demand_kw", "heating_demand_kw", "cooling_energy_kwh", "heating_energy_kwh"])
    cooling = {row.get("hour_index"): row for row in report.get("cooling", {}).get("hours", [])}
    heating = {row.get("hour_index"): row for row in report.get("heating", {}).get("hours", [])}
    for index in range(HOURS_PER_YEAR):
        c = cooling.get(index, {})
        h = heating.get(index, {})
        writer.writerow([index, c.get("demand_kw", 0.0), h.get("demand_kw", 0.0), c.get("demand_kw", 0.0), h.get("demand_kw", 0.0)])
    return output.getvalue()


def annual_monthly_csv(report):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["month", "cooling_kwh", "heating_kwh"])
    for month in MONTHS:
        writer.writerow([month, report.get("cooling", {}).get("monthly_kwh", {}).get(str(month), 0.0), report.get("heating", {}).get("monthly_kwh", {}).get(str(month), 0.0)])
    return output.getvalue()
