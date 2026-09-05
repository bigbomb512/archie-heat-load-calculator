#!/usr/bin/env python3

"""Evidence-first, engineer-entered hourly cooling design-day calculations."""

from copy import deepcopy
from datetime import datetime, timezone
import re

from ai.design_requirements import (
    validate_cooling_load_conditions,
    validate_design_requirements,
    validate_zone_cooling_load,
)
from ai.heat_loads import envelope_load, equipment_load, lighting_load, outside_air_load, people_load, solar_load
from ai.site_design_conditions import validate_citations
from ai.cooling_readiness import assess_cooling_readiness, room_component_issues, topology_issues


DAY_TYPES = ("weekday", "saturday", "sunday_holiday")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
STATUSES = {"missing", "provisional", "confirmed", "not_applicable"}
ID = re.compile(r"^[a-z][a-z0-9_-]*$")
ROOM_COMPONENT_STATES = {"not_present_confirmed", "stored_not_calculated", "not_assessed"}
ROOM_COMPONENT_TYPES = {
    "infiltration": {"group": "airflow", "units": {"L/s", "m3/s", "m3/h", "ACH"}},
    "minimum_supply_air": {"group": "airflow", "units": {"L/s", "m3/s", "m3/h"}},
    "extract_air": {"group": "airflow", "units": {"L/s", "m3/s", "m3/h"}},
    "spill_air": {"group": "airflow", "units": {"L/s", "m3/s", "m3/h"}},
    "transfer_air": {"group": "airflow", "units": {"L/s", "m3/s", "m3/h"}},
    "make_up_air": {"group": "airflow", "units": {"L/s", "m3/s", "m3/h"}},
    "vapour_gain": {"group": "moisture", "units": {"kg/h", "g/h", "W"}},
    "steam_gain": {"group": "moisture", "units": {"kg/h", "g/h", "W"}},
    "process_latent_load": {"group": "moisture", "units": {"kg/h", "g/h", "W"}},
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_schedule_library():
    return {"schema_version": 1, "updated_at": "", "schedules": []}


def empty_design_day_scenarios():
    return {"schema_version": 1, "updated_at": "", "scenarios": []}


def empty_hourly_load_model():
    return {
        "schema_version": 3,
        "updated_at": "",
        "source_requirements_updated_at": "",
        "floors": [],
        "zones": [],
        "rooms": [],
    }


def empty_day_profile():
    return {"values": [], "status": "missing", "source": "", "citations": []}


def empty_schedule():
    return {
        "schedule_id": "", "title": "", "description": "", "status": "missing",
        "source": "", "citations": [], "day_profiles": {day_type: empty_day_profile() for day_type in DAY_TYPES},
    }


def validate_schedule_library(raw):
    if not isinstance(raw, dict):
        raise ValueError("Schedule library must be a JSON object.")
    schedules = raw.get("schedules", [])
    if not isinstance(schedules, list):
        raise ValueError("Schedule library schedules must be a list.")
    result, ids = [], set()
    for index, raw_schedule in enumerate(schedules, start=1):
        schedule = validate_schedule(raw_schedule, index)
        if schedule["schedule_id"] in ids:
            raise ValueError(f"Schedule ID '{schedule['schedule_id']}' is duplicated.")
        ids.add(schedule["schedule_id"])
        result.append(schedule)
    return {"schema_version": 1, "updated_at": timestamp(), "schedules": result}


def validate_schedule(raw, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Schedule {index} must be an object.")
    schedule_id = text(raw.get("schedule_id", ""), f"Schedule {index} ID")
    if not ID.fullmatch(schedule_id):
        raise ValueError(f"Schedule {index} ID must start with a letter and use lowercase letters, numbers, hyphens, or underscores.")
    status = status_value(raw.get("status", "missing"), f"Schedule {schedule_id}")
    result = {
        "schedule_id": schedule_id,
        "title": text(raw.get("title", ""), f"Schedule {schedule_id} title"),
        "description": text(raw.get("description", ""), f"Schedule {schedule_id} description"),
        "status": status,
        "source": text(raw.get("source", ""), f"Schedule {schedule_id} source"),
        "citations": validate_citations(raw.get("citations", []), f"Schedule {schedule_id}"),
        "day_profiles": {},
    }
    if status in {"confirmed", "provisional"} and (not result["title"] or not result["source"]):
        raise ValueError(f"Schedule {schedule_id} needs a title and source when {status}.")
    if not isinstance(raw.get("day_profiles", {}), dict):
        raise ValueError(f"Schedule {schedule_id} day profiles must be an object.")
    for day_type in DAY_TYPES:
        result["day_profiles"][day_type] = validate_day_profile(raw.get("day_profiles", {}).get(day_type, empty_day_profile()), schedule_id, day_type)
    return result


def validate_day_profile(raw, schedule_id, day_type):
    if not isinstance(raw, dict):
        raise ValueError(f"Schedule {schedule_id} {day_type} profile must be an object.")
    status = status_value(raw.get("status", "missing"), f"Schedule {schedule_id} {day_type} profile")
    source = text(raw.get("source", ""), f"Schedule {schedule_id} {day_type} source")
    values = raw.get("values", [])
    if not isinstance(values, list):
        raise ValueError(f"Schedule {schedule_id} {day_type} values must be a list.")
    if values and len(values) != 24:
        raise ValueError(f"Schedule {schedule_id} {day_type} needs exactly 24 hourly values.")
    checked = []
    for hour, value in enumerate(values):
        number = number_value(value, f"Schedule {schedule_id} {day_type} hour {hour}", 0, 1)
        checked.append(number)
    if status in {"confirmed", "provisional"}:
        if len(checked) != 24:
            raise ValueError(f"Schedule {schedule_id} {day_type} needs exactly 24 hourly values when {status}.")
        if not source:
            raise ValueError(f"Schedule {schedule_id} {day_type} needs a source when {status}.")
    if status == "not_applicable" and checked:
        raise ValueError(f"Schedule {schedule_id} {day_type} values must be blank when not applicable.")
    return {"values": checked, "status": status, "source": source, "citations": validate_citations(raw.get("citations", []), f"Schedule {schedule_id} {day_type}")}


def schedule_library_summary(library):
    library = validate_schedule_library(library)
    missing, provisional = [], []
    if not library["schedules"]:
        missing.append("schedule library")
    for schedule in library["schedules"]:
        for day_type, profile in schedule["day_profiles"].items():
            if profile["status"] == "missing":
                missing.append(f"{schedule['schedule_id']} {day_type}")
            elif profile["status"] == "provisional":
                provisional.append(f"{schedule['schedule_id']} {day_type}")
    return readiness(missing, provisional, {"schedule_count": len(library["schedules"])})


def empty_design_day():
    return {
        "scenario_id": "", "title": "", "mode": "cooling", "representative_month": "",
        "day_type": "weekday", "status": "missing", "source": "", "citations": [],
        "atmospheric_pressure_kpa": empty_number_field(), "hours": [],
    }


def empty_number_field():
    return {"value": None, "status": "missing", "source": "", "citations": []}


def validate_design_day_scenarios(raw):
    if not isinstance(raw, dict):
        raise ValueError("Design-day scenarios must be a JSON object.")
    scenarios = raw.get("scenarios", [])
    if not isinstance(scenarios, list):
        raise ValueError("Design-day scenarios must be a list.")
    result, ids = [], set()
    for index, raw_scenario in enumerate(scenarios, start=1):
        scenario = validate_design_day(raw_scenario, index)
        if scenario["scenario_id"] in ids:
            raise ValueError(f"Design-day scenario ID '{scenario['scenario_id']}' is duplicated.")
        ids.add(scenario["scenario_id"])
        result.append(scenario)
    return {"schema_version": 1, "updated_at": timestamp(), "scenarios": result}


def validate_design_day(raw, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Design-day scenario {index} must be an object.")
    scenario_id = text(raw.get("scenario_id", ""), f"Design-day scenario {index} ID")
    if not ID.fullmatch(scenario_id):
        raise ValueError(f"Design-day scenario {index} ID must start with a letter and use lowercase letters, numbers, hyphens, or underscores.")
    mode = raw.get("mode", "cooling")
    if mode not in {"cooling", "heating"}:
        raise ValueError(f"Design-day scenario {scenario_id} mode must be cooling or heating.")
    month = text(raw.get("representative_month", ""), f"Design-day scenario {scenario_id} representative month")
    if month and month not in MONTHS:
        raise ValueError(f"Design-day scenario {scenario_id} representative month is invalid.")
    day_type = raw.get("day_type", "weekday")
    if day_type not in DAY_TYPES:
        raise ValueError(f"Design-day scenario {scenario_id} day type is invalid.")
    status = status_value(raw.get("status", "missing"), f"Design-day scenario {scenario_id}")
    result = {
        "scenario_id": scenario_id, "title": text(raw.get("title", ""), f"Design-day scenario {scenario_id} title"),
        "mode": mode, "representative_month": month, "day_type": day_type, "status": status,
        "source": text(raw.get("source", ""), f"Design-day scenario {scenario_id} source"),
        "citations": validate_citations(raw.get("citations", []), f"Design-day scenario {scenario_id}"),
        "atmospheric_pressure_kpa": validate_number_field(raw.get("atmospheric_pressure_kpa", empty_number_field()), f"Design-day scenario {scenario_id} atmospheric pressure", 50, 120),
        "hours": validate_design_day_hours(raw.get("hours", []), scenario_id, mode),
    }
    if status in {"confirmed", "provisional"} and (not result["title"] or not month or not result["source"]):
        raise ValueError(f"Design-day scenario {scenario_id} needs title, representative month, and source when {status}.")
    return result


def validate_design_day_hours(raw, scenario_id, mode):
    if not isinstance(raw, list):
        raise ValueError(f"Design-day scenario {scenario_id} hours must be a list.")
    if raw and len(raw) != 24:
        raise ValueError(f"Design-day scenario {scenario_id} needs exactly 24 hours.")
    result, hours = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"Design-day scenario {scenario_id} hour must be an object.")
        hour = item.get("hour")
        if isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 or hour > 23 or hour in hours:
            raise ValueError(f"Design-day scenario {scenario_id} needs distinct hour values from 0 to 23.")
        hours.add(hour)
        row = {
            "hour": hour,
            "outdoor_dry_bulb_c": validate_number_field(item.get("outdoor_dry_bulb_c", empty_number_field()), f"Design-day scenario {scenario_id} hour {hour} outdoor dry-bulb", -100, 100),
            "outdoor_wet_bulb_c": validate_number_field(item.get("outdoor_wet_bulb_c", empty_number_field()), f"Design-day scenario {scenario_id} hour {hour} outdoor wet-bulb", -100, 100),
        }
        db = row["outdoor_dry_bulb_c"]["value"]
        wb = row["outdoor_wet_bulb_c"]["value"]
        if db is not None and wb is not None and wb > db:
            raise ValueError(f"Design-day scenario {scenario_id} hour {hour} outdoor wet-bulb cannot exceed dry-bulb.")
        if mode == "heating":
            row["outdoor_relative_humidity_percent"] = validate_number_field(item.get("outdoor_relative_humidity_percent", empty_number_field()), f"Design-day scenario {scenario_id} hour {hour} outdoor relative humidity", 0, 100)
        result.append(row)
    return sorted(result, key=lambda item: item["hour"])


def validate_number_field(raw, label, low, high):
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must include value, status, source, and citations.")
    status = status_value(raw.get("status", "missing"), label)
    value = raw.get("value")
    if value not in (None, ""):
        value = number_value(value, label, low, high)
    else:
        value = None
    source = text(raw.get("source", ""), f"{label} source")
    if status in {"confirmed", "provisional"} and (value is None or not source):
        raise ValueError(f"{label} needs a value and source when {status}.")
    if status == "not_applicable" and value is not None:
        raise ValueError(f"{label} must be blank when not applicable.")
    return {"value": value, "status": status, "source": source, "citations": validate_citations(raw.get("citations", []), label)}


def design_day_summary(scenarios):
    scenarios = validate_design_day_scenarios(scenarios)
    missing, provisional = [], []
    if not scenarios["scenarios"]:
        missing.append("design-day scenarios")
    for scenario in scenarios["scenarios"]:
        items = [scenario["atmospheric_pressure_kpa"]]
        for hour in scenario["hours"]:
            items.extend([hour["outdoor_dry_bulb_c"], hour["outdoor_wet_bulb_c"]])
        if scenario["status"] == "missing" or len(scenario["hours"]) != 24:
            missing.append(scenario["scenario_id"])
        elif scenario["status"] == "provisional" or any(item["status"] == "provisional" for item in items):
            provisional.append(scenario["scenario_id"])
    return readiness(missing, provisional, {"scenario_count": len(scenarios["scenarios"])})


def build_hourly_load_model(requirements):
    requirements = requirements_snapshot(requirements)
    floors = [{
        "floor_id": "unassigned", "name": "Unassigned legacy floor", "elevation_m": None,
        "verification_status": "provisional", "source": "Migrated from legacy design zones; assign a reviewed floor.", "citations": [],
    }]
    zones = []
    rooms = []
    for zone in requirements.get("zones", []):
        zones.append({
            "zone_id": zone["zone_id"], "name": zone.get("name", zone["zone_id"]), "floor_id": "unassigned",
            "verification_status": "provisional", "source": "Migrated from legacy design zone; assign a reviewed floor.", "citations": [],
        })
        room_id = f"{zone['zone_id']}-room-1"
        room = {
            "room_id": room_id,
            "name": zone.get("name", zone["zone_id"]),
            "zone_id": zone["zone_id"],
            "source_zone_id": zone["zone_id"],
            "mapping_status": "inferred",
            "verification_status": "provisional",
            "source": "Seeded from existing design zone; engineer must confirm or edit room mapping.",
            "source_room_labels": list(zone.get("source_room_labels", [])),
            "area_m2": zone.get("area_m2"),
            "occupancy": zone.get("occupancy"),
            "indoor_cooling_setpoint_c": effective(zone, requirements, "indoor_cooling_setpoint_c"),
            "heat_sources": [seed_heat_source(source, room_id, index) for index, source in enumerate(zone.get("heat_sources", []), start=1)],
            "cooling_load": deepcopy(zone.get("cooling_load", {})),
            "cooling_load_conditions": deepcopy(requirements.get("cooling_load_conditions", {})),
            "schedule_assignments": {"people": "", "lighting": "", "outside_air": "", "equipment": {}, "solar": {}},
            "unapproved_components": default_room_components(),
        }
        rooms.append(room)
    return {
        "schema_version": 3,
        "updated_at": timestamp(),
        "source_requirements_updated_at": requirements.get("updated_at", ""),
        "floors": floors,
        "zones": zones,
        "rooms": rooms,
    }


def seed_heat_source(source, room_id, index):
    result = deepcopy(source)
    result["source_id"] = source.get("source_id") or f"{room_id}-source-{index}"
    return result


def validate_hourly_load_model(raw):
    if not isinstance(raw, dict):
        raise ValueError("Hourly load model must be a JSON object.")
    raw = migrate_hourly_model(raw)
    rooms = raw.get("rooms", [])
    if not isinstance(rooms, list):
        raise ValueError("Hourly load model rooms must be a list.")
    floors = raw.get("floors", [])
    zones = raw.get("zones", [])
    if not isinstance(floors, list) or not isinstance(zones, list):
        raise ValueError("Hourly load model floors and zones must be lists.")
    checked_floors, floor_ids = [], set()
    for index, raw_floor in enumerate(floors, start=1):
        floor = validate_floor(raw_floor, index)
        if floor["floor_id"] in floor_ids:
            raise ValueError(f"Floor ID '{floor['floor_id']}' is duplicated.")
        floor_ids.add(floor["floor_id"])
        checked_floors.append(floor)
    checked_zones, zone_ids = [], set()
    for index, raw_zone in enumerate(zones, start=1):
        zone = validate_model_zone(raw_zone, index)
        if zone["zone_id"] in zone_ids:
            raise ValueError(f"Zone ID '{zone['zone_id']}' is duplicated.")
        if zone["floor_id"] not in floor_ids:
            raise ValueError(f"Zone '{zone['zone_id']}' references unknown floor '{zone['floor_id']}'.")
        zone_ids.add(zone["zone_id"])
        checked_zones.append(zone)
    result, room_ids = [], set()
    for index, raw_room in enumerate(rooms, start=1):
        room = validate_room(raw_room, index)
        if room["room_id"] in room_ids:
            raise ValueError(f"Room ID '{room['room_id']}' is duplicated.")
        if room["zone_id"] not in zone_ids:
            raise ValueError(f"Room '{room['room_id']}' references unknown zone '{room['zone_id']}'.")
        room_ids.add(room["room_id"])
        result.append(room)
    for room in result:
        for component in room["unapproved_components"]:
            source_room_id = component["source_room_id"]
            if source_room_id and source_room_id not in room_ids:
                raise ValueError(f"Room {room['room_id']} component '{component['component_id']}' references unknown source room '{source_room_id}'.")
            if source_room_id == room["room_id"]:
                raise ValueError(f"Room {room['room_id']} component '{component['component_id']}' cannot reference itself as a source room.")
    return {
        "schema_version": 3,
        "updated_at": timestamp(),
        "source_requirements_updated_at": text(raw.get("source_requirements_updated_at", ""), "Hourly load model source requirements timestamp"),
        "floors": checked_floors,
        "zones": checked_zones,
        "rooms": result,
    }


def migrate_hourly_model(raw):
    """Normalise saved v1/v2 overlays without changing their original artifacts."""
    if not isinstance(raw, dict):
        raise ValueError("Hourly load model must be a JSON object.")
    result = deepcopy(raw)
    if result.get("schema_version", 1) < 2 or "floors" not in result or "zones" not in result:
        result["floors"] = [{
            "floor_id": "unassigned", "name": "Unassigned legacy floor", "elevation_m": None,
            "verification_status": "provisional", "source": "Migrated from schema-v1 room overlay; assign a reviewed floor.", "citations": [],
        }]
        seen = set()
        result["zones"] = []
        for room in result.get("rooms", []):
            zone_id = str(room.get("zone_id", "")).strip()
            if zone_id and zone_id not in seen:
                seen.add(zone_id)
                result["zones"].append({
                    "zone_id": zone_id, "name": zone_id, "floor_id": "unassigned",
                    "verification_status": "provisional", "source": "Migrated from schema-v1 room overlay; assign a reviewed floor.", "citations": [],
                })
    result["schema_version"] = 3
    return result


def migrate_hourly_model_v1(raw):
    """Backward-compatible name retained for callers of the v1 migration helper."""
    return migrate_hourly_model(raw)


def validate_floor(raw, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Floor {index} must be an object.")
    floor_id = text(raw.get("floor_id", ""), f"Floor {index} ID")
    if not ID.fullmatch(floor_id):
        raise ValueError(f"Floor {index} ID must use lowercase stable IDs.")
    status = status_value(raw.get("verification_status", "missing"), f"Floor {floor_id}")
    source = text(raw.get("source", ""), f"Floor {floor_id} source")
    if status in {"confirmed", "provisional"} and not source:
        raise ValueError(f"Floor {floor_id} needs a source when {status}.")
    name = text(raw.get("name", ""), f"Floor {floor_id} name")
    if not name:
        raise ValueError(f"Floor {floor_id} needs a name.")
    return {
        "floor_id": floor_id, "name": name,
        "elevation_m": optional_number(raw.get("elevation_m"), f"Floor {floor_id} elevation", -10000, 10000),
        "verification_status": status, "source": source,
        "citations": validate_citations(raw.get("citations", []), f"Floor {floor_id}"),
    }


def validate_model_zone(raw, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Zone {index} must be an object.")
    zone_id = text(raw.get("zone_id", ""), f"Zone {index} ID")
    floor_id = text(raw.get("floor_id", ""), f"Zone {zone_id} floor ID")
    if not ID.fullmatch(zone_id) or not ID.fullmatch(floor_id):
        raise ValueError(f"Zone {index} and its floor ID must use lowercase stable IDs.")
    status = status_value(raw.get("verification_status", "missing"), f"Zone {zone_id}")
    source = text(raw.get("source", ""), f"Zone {zone_id} source")
    if status in {"confirmed", "provisional"} and not source:
        raise ValueError(f"Zone {zone_id} needs a source when {status}.")
    name = text(raw.get("name", ""), f"Zone {zone_id} name")
    if not name:
        raise ValueError(f"Zone {zone_id} needs a name.")
    return {
        "zone_id": zone_id, "name": name, "floor_id": floor_id,
        "verification_status": status, "source": source,
        "citations": validate_citations(raw.get("citations", []), f"Zone {zone_id}"),
    }


def validate_room(raw, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Room {index} must be an object.")
    room_id = text(raw.get("room_id", ""), f"Room {index} ID")
    zone_id = text(raw.get("zone_id", ""), f"Room {room_id} zone ID")
    if not ID.fullmatch(room_id) or not ID.fullmatch(zone_id):
        raise ValueError(f"Room {index} and its zone ID must use lowercase stable IDs.")
    mapping_status = raw.get("mapping_status", "inferred")
    verification_status = raw.get("verification_status", "missing")
    if mapping_status not in {"inferred", "confirmed"}:
        raise ValueError(f"Room {room_id} mapping status must be inferred or confirmed.")
    status_value(verification_status, f"Room {room_id}")
    name = text(raw.get("name", ""), f"Room {room_id} name")
    room_source = text(raw.get("source", ""), f"Room {room_id} source")
    if not name:
        raise ValueError(f"Room {room_id} needs a name.")
    if verification_status in {"confirmed", "provisional"} and not room_source:
        raise ValueError(f"Room {room_id} needs a source when {verification_status}.")
    cooling = validate_zone_cooling_load(raw.get("cooling_load", {}))
    conditions = validate_cooling_load_conditions(raw.get("cooling_load_conditions", {}))
    sources, source_ids = [], set()
    for source_index, raw_source in enumerate(raw.get("heat_sources", []), start=1):
        heat_source = validate_hourly_heat_source(raw_source, room_id, source_index)
        if heat_source["source_id"] in source_ids:
            raise ValueError(f"Room {room_id} heat-source ID '{heat_source['source_id']}' is duplicated.")
        source_ids.add(heat_source["source_id"])
        sources.append(heat_source)
    assignments = validate_assignments(raw.get("schedule_assignments", {}), room_id, source_ids, cooling)
    components = validate_room_components(raw.get("unapproved_components"), room_id)
    return {
        "room_id": room_id,
        "name": name,
        "zone_id": zone_id,
        "source_zone_id": text(raw.get("source_zone_id", ""), f"Room {room_id} source zone ID"),
        "mapping_status": mapping_status,
        "verification_status": verification_status,
        "source": room_source,
        "citations": validate_citations(raw.get("citations", []), f"Room {room_id}"),
        "source_room_labels": text_list(raw.get("source_room_labels", []), f"Room {room_id} source room labels"),
        "area_m2": optional_number(raw.get("area_m2"), f"Room {room_id} area", 0, 1000000),
        "occupancy": optional_number(raw.get("occupancy"), f"Room {room_id} occupancy", 0, 1000000),
        "indoor_cooling_setpoint_c": optional_number(raw.get("indoor_cooling_setpoint_c"), f"Room {room_id} cooling setpoint", -100, 100),
        "heat_sources": sources,
        "cooling_load": cooling,
        "cooling_load_conditions": conditions,
        "schedule_assignments": assignments,
        "unapproved_components": components,
    }


def default_room_components():
    return [{
        "component_id": component_type,
        "component_type": component_type,
        "value": None,
        "unit": "",
        "source_room_id": "",
        "source": "",
        "citations": [],
        "verification_status": "missing",
        "calculation_status": "not_assessed",
    } for component_type in ROOM_COMPONENT_TYPES]


def validate_room_components(raw_components, room_id):
    if raw_components is None:
        raw_components = default_room_components()
    if not isinstance(raw_components, list):
        raise ValueError(f"Room {room_id} unapproved components must be a list.")
    supplied_types = {item.get("component_type") for item in raw_components if isinstance(item, dict)}
    components = list(raw_components) + [item for item in default_room_components() if item["component_type"] not in supplied_types]
    result, component_ids = [], set()
    for index, raw in enumerate(components, start=1):
        component = validate_room_component(raw, room_id, index)
        if component["component_id"] in component_ids:
            raise ValueError(f"Room {room_id} component ID '{component['component_id']}' is duplicated.")
        component_ids.add(component["component_id"])
        result.append(component)
    return result


def validate_room_component(raw, room_id, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Room {room_id} component {index} must be an object.")
    component_type = text(raw.get("component_type", ""), f"Room {room_id} component {index} type")
    if component_type not in ROOM_COMPONENT_TYPES:
        raise ValueError(f"Room {room_id} component {index} has an unsupported type '{component_type}'.")
    component_id = text(raw.get("component_id", ""), f"Room {room_id} component {index} ID")
    if not ID.fullmatch(component_id):
        raise ValueError(f"Room {room_id} component {index} needs a stable component ID.")
    calculation_status = raw.get("calculation_status", "not_assessed")
    if calculation_status not in ROOM_COMPONENT_STATES:
        raise ValueError(f"Room {room_id} component '{component_id}' has an invalid calculation status.")
    value = optional_number(raw.get("value"), f"Room {room_id} component '{component_id}' value", 0, 1000000000)
    unit = text(raw.get("unit", ""), f"Room {room_id} component '{component_id}' unit")
    source_room_id = text(raw.get("source_room_id", ""), f"Room {room_id} component '{component_id}' source room ID")
    source = text(raw.get("source", ""), f"Room {room_id} component '{component_id}' source")
    verification_status = status_value(raw.get("verification_status", "missing"), f"Room {room_id} component '{component_id}'")
    citations = validate_citations(raw.get("citations", []), f"Room {room_id} component '{component_id}'")
    if source_room_id and component_type != "transfer_air":
        raise ValueError(f"Room {room_id} component '{component_id}' may reference a source room only for transfer air.")
    if source_room_id and not ID.fullmatch(source_room_id):
        raise ValueError(f"Room {room_id} component '{component_id}' source room ID must use a lowercase stable ID.")
    if calculation_status == "not_present_confirmed":
        if value is not None or unit:
            raise ValueError(f"Room {room_id} component '{component_id}' marked not present cannot have a value or unit.")
        if verification_status != "confirmed" or not source:
            raise ValueError(f"Room {room_id} component '{component_id}' marked not present needs confirmed review and a source.")
    elif calculation_status == "stored_not_calculated":
        if value is None or not unit:
            raise ValueError(f"Room {room_id} component '{component_id}' stored for later calculation needs a value and unit.")
        if value <= 0:
            raise ValueError(f"Room {room_id} component '{component_id}' stored for later calculation needs a positive value; confirm not present when it is zero.")
        if unit not in ROOM_COMPONENT_TYPES[component_type]["units"]:
            allowed = ", ".join(sorted(ROOM_COMPONENT_TYPES[component_type]["units"]))
            raise ValueError(f"Room {room_id} component '{component_id}' unit must be one of: {allowed}.")
        if verification_status not in {"confirmed", "provisional"} or not source:
            raise ValueError(f"Room {room_id} component '{component_id}' stored for later calculation needs a source and review status.")
    else:
        if value is not None or unit or source_room_id or source or citations or verification_status != "missing":
            raise ValueError(f"Room {room_id} component '{component_id}' not assessed cannot include a value, source, citation, or review status.")
    return {
        "component_id": component_id,
        "component_type": component_type,
        "value": value,
        "unit": unit,
        "source_room_id": source_room_id,
        "source": source,
        "citations": citations,
        "verification_status": verification_status,
        "calculation_status": calculation_status,
    }


def validate_hourly_heat_source(raw, room_id, index):
    if not isinstance(raw, dict):
        raise ValueError(f"Room {room_id} heat source {index} must be an object.")
    source_id = text(raw.get("source_id", ""), f"Room {room_id} heat source {index} ID")
    if not ID.fullmatch(source_id):
        raise ValueError(f"Room {room_id} heat source {index} needs a stable source ID.")
    result = deepcopy(raw)
    result["source_id"] = source_id
    return result


def validate_assignments(raw, room_id, source_ids, cooling):
    if not isinstance(raw, dict):
        raise ValueError(f"Room {room_id} schedule assignments must be an object.")
    for key in ("people", "lighting", "outside_air"):
        if not isinstance(raw.get(key, ""), str):
            raise ValueError(f"Room {room_id} {key} schedule assignment must be text.")
    equipment = raw.get("equipment", {})
    solar = raw.get("solar", {})
    if not isinstance(equipment, dict) or not isinstance(solar, dict):
        raise ValueError(f"Room {room_id} equipment and solar assignments must be objects.")
    unknown_equipment = set(equipment) - set(source_ids)
    if unknown_equipment:
        raise ValueError(f"Room {room_id} assigns schedules to unknown heat sources: {', '.join(sorted(unknown_equipment))}.")
    surface_ids = {str(item.get("surface_id", "")) for item in cooling.get("envelope_surfaces", [])}
    unknown_solar = set(solar) - surface_ids
    if unknown_solar:
        raise ValueError(f"Room {room_id} assigns schedules to unknown surfaces: {', '.join(sorted(unknown_solar))}.")
    return {
        "people": raw.get("people", "").strip(),
        "lighting": raw.get("lighting", "").strip(),
        "outside_air": raw.get("outside_air", "").strip(),
        "equipment": {str(key): text(value, f"Room {room_id} equipment schedule") for key, value in equipment.items()},
        "solar": {str(key): text(value, f"Room {room_id} solar schedule") for key, value in solar.items()},
    }


def hourly_model_summary(model, requirements=None):
    model = validate_hourly_load_model(model)
    issues = topology_issues(model, (requirements or {}).get("updated_at", "")) + room_component_issues(model)
    missing = [item["reason"] for item in issues if item["status"] == "blocked"]
    provisional = [item["affected_id"] for item in issues if item["status"] == "draft"]
    return readiness(missing, provisional, {
        "floor_count": len(model["floors"]), "zone_count": len(model["zones"]), "room_count": len(model["rooms"]),
        "issues": issues,
    })


def calculate_hourly_load_report(requirements, schedule_library, scenarios, model, selected_scenario_ids, coverage=None):
    requirements = requirements_snapshot(requirements)
    schedule_library = artifact_snapshot(schedule_library, validate_schedule_library)
    scenarios = artifact_snapshot(scenarios, validate_design_day_scenarios)
    model = artifact_snapshot(model, validate_hourly_load_model)
    coverage = coverage or {}
    stale = model.get("source_requirements_updated_at") != requirements.get("updated_at")
    selected = select_scenarios(scenarios, selected_scenario_ids)
    report = {
        "report_schema_version": 2,
        "report_type": "hourly_cooling_load",
        "status": "blocked",
        "input_fingerprints": {
            "requirements_updated_at": requirements.get("updated_at", ""),
            "schedule_library_updated_at": schedule_library.get("updated_at", ""),
            "design_day_scenarios_updated_at": scenarios.get("updated_at", ""),
            "hourly_load_model_updated_at": model.get("updated_at", ""),
        },
        "excluded_components": [
            "partitions", "infiltration", "minimum supply air", "extract/spill/transfer/make-up air",
            "vapour/steam/process latent loads", "dynamic thermal mass", "detailed glazing physics",
            "AHU coil effects", "fan/duct effects", "heat recovery", "plant loads",
        ],
        "scenario_results": [],
        "included_scope_peak": {},
        "project_peak": {},
        "warnings": [],
        "blocked_reasons": [],
        "readiness": {},
        "scope_summary": {},
        "known_exclusions": [],
        "unresolved_room_inputs": [],
    }
    if stale:
        report["blocked_reasons"].append("Hourly load model is stale because design requirements changed after the model was saved.")
        attach_readiness(report, model, requirements, coverage)
        return report
    if not selected:
        report["blocked_reasons"].append("Select at least one design-day scenario.")
        attach_readiness(report, model, requirements, coverage)
        return report
    for scenario in selected:
        report["scenario_results"].append(calculate_scenario(requirements, schedule_library, model, scenario))
    all_rooms = [room for scenario in report["scenario_results"] for room in scenario["rooms"]]
    if not any(room["status"] != "blocked" for room in all_rooms):
        report["blocked_reasons"].append("No selected scenario produced a complete room result.")
        report["blocked_reasons"].extend(reason for scenario in report["scenario_results"] for reason in scenario["blocked_reasons"])
        attach_readiness(report, model, requirements, coverage)
        return report
    readiness_result = attach_readiness(report, model, requirements, coverage)
    report["included_scope_peak"] = governing_peak(report["scenario_results"], "included_scope_peak")
    if readiness_result["scope_summary"]["complete_scope"] and readiness_result["status"] == "review_ready":
        report["project_peak"] = deepcopy(report["included_scope_peak"])
    report["warnings"] = [warning for scenario in report["scenario_results"] for warning in scenario["warnings"]]
    return report


def attach_readiness(report, model, requirements, coverage, envelope_input=None):
    readiness_result = assess_cooling_readiness(report, model, requirements.get("updated_at", ""), coverage, envelope_input)
    report["status"] = readiness_result["status"]
    report["readiness"] = {"status": readiness_result["status"], "issues": readiness_result["issues"]}
    report["scope_summary"] = readiness_result["scope_summary"]
    coverage_rows = readiness_result["scope_summary"].get("room_input_coverage", [])
    report["known_exclusions"] = [
        {"room_id": row["room_id"], **component}
        for row in coverage_rows for component in row["stored_not_calculated"]
    ]
    report["unresolved_room_inputs"] = [
        {"room_id": row["room_id"], **component}
        for row in coverage_rows for component in row["not_assessed"]
    ]
    return readiness_result


def select_scenarios(scenarios, selected_ids):
    selected_ids = selected_ids or []
    if not isinstance(selected_ids, list):
        raise ValueError("Selected scenario IDs must be a list.")
    lookup = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    unknown = [item for item in selected_ids if item not in lookup]
    if unknown:
        raise ValueError("Unknown design-day scenario IDs: " + ", ".join(unknown))
    return [lookup[item] for item in selected_ids]


def calculate_scenario(requirements, library, model, scenario):
    result = {
        "scenario_id": scenario["scenario_id"], "title": scenario["title"], "mode": scenario["mode"],
        "representative_month": scenario["representative_month"], "day_type": scenario["day_type"],
        "status": "blocked", "rooms": [], "zones": [], "floors": [], "included_scope_hours": [], "included_scope_peak": {},
        "scope_summary": {},
        "warnings": [], "blocked_reasons": [],
    }
    if scenario["mode"] != "cooling":
        result["blocked_reasons"].append("Heating design-day scenarios are stored but hourly heating calculation is not implemented.")
        return result
    scenario_missing, scenario_provisional = scenario_ready(scenario)
    if scenario_missing:
        result["blocked_reasons"].extend(scenario_missing)
        return result
    room_results = [calculate_room_hours(requirements, library, scenario, room) for room in model["rooms"]]
    result["rooms"] = room_results
    calculated_rooms = [room for room in room_results if room["status"] != "blocked"]
    if not calculated_rooms:
        result["blocked_reasons"].append("All rooms are blocked for this scenario.")
        return result
    zones = {zone["zone_id"]: zone for zone in model["zones"]}
    floors = {floor["floor_id"]: floor for floor in model["floors"]}
    result["zones"] = aggregate_zones(calculated_rooms, zones)
    result["floors"] = aggregate_floors(result["zones"], floors)
    result["included_scope_hours"] = aggregate_project(result["zones"])
    result["included_scope_peak"] = peak(result["included_scope_hours"])
    blocked_rooms = [{"room_id": room["room_id"], "reasons": room["blocked_reasons"]} for room in room_results if room["status"] == "blocked"]
    incomplete_component_rooms = [room["room_id"] for room in room_results if room.get("room_input_scope", {}).get("status") != "complete"]
    result["scope_summary"] = {
        "active_room_ids": [room["room_id"] for room in room_results],
        "included_room_ids": [room["room_id"] for room in calculated_rooms],
        "blocked_rooms": blocked_rooms,
        "incomplete_component_room_ids": incomplete_component_rooms,
        "complete_scope": not blocked_rooms and not incomplete_component_rooms,
    }
    room_provisional = any(room["status"] != "review_ready" for room in room_results)
    result["status"] = "draft" if scenario_provisional or room_provisional or blocked_rooms else "review_ready"
    if scenario_provisional:
        result["warnings"].append("Design-day scenario contains provisional evidence.")
    result["warnings"].extend(warning for room in room_results for warning in room["warnings"])
    return result


def scenario_ready(scenario):
    missing, provisional = [], scenario["status"] == "provisional"
    if scenario["status"] in {"missing", "not_applicable"}:
        missing.append("Scenario status must be confirmed or provisional.")
    elif scenario["status"] != "confirmed":
        provisional = True
    if not scenario["title"] or not scenario["representative_month"] or not scenario["source"]:
        missing.append("Scenario title, representative month, and source are required.")
    pressure = scenario["atmospheric_pressure_kpa"]
    if pressure["value"] is None or not pressure["source"] or pressure["status"] in {"missing", "not_applicable"}:
        missing.append("Scenario atmospheric pressure is required.")
    elif pressure["status"] == "provisional":
        provisional = True
    if len(scenario["hours"]) != 24:
        missing.append("Scenario needs 24 hourly weather records.")
    for row in scenario["hours"]:
        for key, label in (("outdoor_dry_bulb_c", "outdoor dry-bulb"), ("outdoor_wet_bulb_c", "outdoor wet-bulb")):
            item = row[key]
            if item["value"] is None or not item["source"] or item["status"] in {"missing", "not_applicable"}:
                missing.append(f"Hour {row['hour']} {label} is required.")
            elif item["status"] == "provisional":
                provisional = True
    return missing, provisional


def calculate_room_hours(requirements, library, scenario, room):
    component_scope = room_component_scope(room)
    result = {
        "room_id": room["room_id"], "name": room["name"], "zone_id": room["zone_id"], "status": "blocked",
        "hours": [], "peak": {}, "warnings": [], "blocked_reasons": [], "room_input_scope": component_scope,
    }
    static_missing = room_static_missing(room)
    if static_missing:
        result["blocked_reasons"].extend(static_missing)
        return result
    profiles, profile_missing, profile_provisional = resolved_profiles(library, scenario["day_type"], room)
    if profile_missing:
        result["blocked_reasons"].extend(profile_missing)
        return result
    provisional = profile_provisional or room_is_provisional(room)
    if provisional:
        result["warnings"].append("Room mapping, static inputs, or schedules remain provisional.")
    if component_scope["stored_not_calculated"]:
        result["warnings"].append("Known room airflow or moisture inputs are stored but excluded until an approved calculation method exists.")
    if component_scope["not_assessed"]:
        result["warnings"].append("Room airflow or moisture input categories have not been assessed.")
    for weather in scenario["hours"]:
        hour = weather["hour"]
        contributions = room_contributions(room, profiles, hour, weather, scenario["atmospheric_pressure_kpa"]["value"])
        result["hours"].append(hour_total(hour, contributions, room["cooling_load"]["safety_factor"]))
    result["peak"] = peak(result["hours"])
    result["status"] = "draft" if provisional else "review_ready"
    return result


def room_static_missing(room):
    load = room["cooling_load"]
    conditions = room["cooling_load_conditions"]
    required = {
        "room name": room["name"], "room source": room["source"], "area": room["area_m2"], "occupancy": room["occupancy"],
        "indoor cooling setpoint": room["indoor_cooling_setpoint_c"], "people sensible gain": load.get("people_sensible_w_per_person"),
        "people latent gain": load.get("people_latent_w_per_person"), "people diversity": load.get("people_diversity_factor"),
        "lighting density": load.get("lighting_w_m2"), "lighting diversity": load.get("lighting_diversity_factor"),
        "outside-air flow": load.get("outside_air_lps"), "safety factor": load.get("safety_factor"), "cooling-load source": load.get("source"),
        "indoor cooling wet-bulb": conditions.get("indoor_cooling_wet_bulb_c"), "cooling-load conditions source": conditions.get("source"),
    }
    missing = [label for label, value in required.items() if value in (None, "")]
    if not room["heat_sources"]:
        missing.append("heat sources")
    for source in room["heat_sources"]:
        for key, label in (("name", "name"), ("quantity", "quantity"), ("watts", "watts"), ("diversity_factor", "diversity"), ("space_gain_factor", "space gain"), ("source", "source")):
            if source.get(key) in (None, ""):
                missing.append(f"{source.get('source_id', 'heat source')} {label}")
    surfaces = load.get("envelope_surfaces", [])
    if not surfaces and not load.get("envelope_not_applicable"):
        missing.append("envelope surfaces or internal-room declaration")
    for surface in surfaces:
        for key, label in (("surface_id", "surface ID"), ("area_m2", "area"), ("u_value_w_m2k", "U-value"), ("solar_design_w_m2", "design solar"), ("solar_gain_factor", "solar gain"), ("shading_factor", "shading"), ("source", "source")):
            if surface.get(key) in (None, ""):
                missing.append(f"{surface.get('surface_id', 'surface')} {label}")
    return missing


def resolved_profiles(library, day_type, room):
    assignments = room["schedule_assignments"]
    required = {
        **{f"equipment:{source['source_id']}": assignments["equipment"].get(source["source_id"], "") for source in room["heat_sources"] if heat_source_is_timed(source)},
        **{f"solar:{surface['surface_id']}": assignments["solar"].get(surface["surface_id"], "") for surface in room["cooling_load"].get("envelope_surfaces", []) if solar_is_timed(surface)},
    }
    if people_is_timed(room):
        required["people"] = assignments["people"]
    if lighting_is_timed(room):
        required["lighting"] = assignments["lighting"]
    if outside_air_is_timed(room):
        required["outside_air"] = assignments["outside_air"]
    lookup = {item["schedule_id"]: item for item in library["schedules"]}
    profiles, missing, provisional = {}, [], False
    for target, schedule_id in required.items():
        if not schedule_id:
            missing.append(f"{target} schedule assignment")
            continue
        schedule = lookup.get(schedule_id)
        if not schedule:
            missing.append(f"{target} references unknown schedule '{schedule_id}'")
            continue
        day = schedule["day_profiles"][day_type]
        if schedule["status"] in {"missing", "not_applicable"} or day["status"] in {"missing", "not_applicable"} or len(day["values"]) != 24 or not day["source"]:
            missing.append(f"{target} schedule '{schedule_id}' has no usable {day_type} profile")
            continue
        profiles[target] = day["values"]
        provisional = provisional or schedule["status"] != "confirmed" or day["status"] != "confirmed"
    return profiles, missing, provisional


def heat_source_is_timed(source):
    return float(source.get("quantity", 0)) * float(source.get("watts", 0)) * float(source.get("diversity_factor", 0)) * float(source.get("space_gain_factor", 0)) != 0


def solar_is_timed(surface):
    return float(surface.get("area_m2", 0)) * float(surface.get("solar_design_w_m2", 0)) * float(surface.get("solar_gain_factor", 0)) * float(surface.get("shading_factor", 0)) != 0


def people_is_timed(room):
    load = room["cooling_load"]
    return room["occupancy"] * (load["people_sensible_w_per_person"] + load["people_latent_w_per_person"]) * load["people_diversity_factor"] != 0


def lighting_is_timed(room):
    load = room["cooling_load"]
    return room["area_m2"] * load["lighting_w_m2"] * load["lighting_diversity_factor"] != 0


def outside_air_is_timed(room):
    return room["cooling_load"]["outside_air_lps"] != 0


def room_is_provisional(room):
    if room["mapping_status"] != "confirmed" or room["verification_status"] != "confirmed":
        return True
    if room["cooling_load"].get("verification_status") != "confirmed":
        return True
    if room["cooling_load_conditions"].get("verification_status") != "confirmed":
        return True
    if any(source.get("verification_status") != "confirmed" for source in room["heat_sources"]):
        return True
    if any(surface.get("verification_status") != "confirmed" for surface in room["cooling_load"].get("envelope_surfaces", [])):
        return True
    return any(component["calculation_status"] != "not_present_confirmed" for component in room["unapproved_components"])


def room_component_scope(room):
    result = {"room_id": room["room_id"], "stored_not_calculated": [], "not_assessed": [], "not_present_confirmed": []}
    for component in room.get("unapproved_components", []):
        item = {
            "component_id": component["component_id"], "component_type": component["component_type"],
            "value": component["value"], "unit": component["unit"], "source": component["source"],
            "citations": deepcopy(component["citations"]), "source_room_id": component["source_room_id"],
        }
        result[component["calculation_status"]].append(item)
    result["status"] = "complete" if not result["stored_not_calculated"] and not result["not_assessed"] else "incomplete"
    return result


def room_contributions(room, profiles, hour, weather, pressure):
    load = room["cooling_load"]
    people = scale(people_load(room["occupancy"], load["people_sensible_w_per_person"], load["people_latent_w_per_person"], load["people_diversity_factor"]), schedule_factor(profiles, "people", hour), "people")
    lighting = scale(lighting_load(room["area_m2"], load["lighting_w_m2"], load["lighting_diversity_factor"]), schedule_factor(profiles, "lighting", hour), "lighting")
    equipment = [scale(equipment_load([source]), schedule_factor(profiles, f"equipment:{source['source_id']}", hour), "equipment_refrigeration") for source in room["heat_sources"]]
    envelope = envelope_load(room["cooling_load"].get("envelope_surfaces", []), weather["outdoor_dry_bulb_c"]["value"], room["indoor_cooling_setpoint_c"])
    solar = []
    for surface in room["cooling_load"].get("envelope_surfaces", []):
        timed_surface = deepcopy(surface)
        timed_surface["solar_design_w_m2"] *= schedule_factor(profiles, f"solar:{surface['surface_id']}", hour) if solar_is_timed(surface) else 0
        solar.append(solar_load([timed_surface]))
    outside_air = outside_air_load(
        load["outside_air_lps"] * schedule_factor(profiles, "outside_air", hour), room["indoor_cooling_setpoint_c"],
        requirements_wet_bulb(room, "indoor_cooling_wet_bulb_c"), weather["outdoor_dry_bulb_c"]["value"],
        weather["outdoor_wet_bulb_c"]["value"], pressure,
    )
    return [people, lighting, *equipment, envelope, *solar, outside_air]


def requirements_wet_bulb(room, key):
    return room["cooling_load_conditions"][key]


def schedule_factor(profiles, key, hour):
    return profiles.get(key, [0.0] * 24)[hour]


def scale(contribution, factor, name):
    result = deepcopy(contribution)
    result["name"] = name
    result["base_sensible_kw"] = result["sensible_kw"]
    result["base_latent_kw"] = result["latent_kw"]
    result["schedule_factor"] = factor
    result["sensible_kw"] = round(result["sensible_kw"] * factor, 4)
    result["latent_kw"] = round(result["latent_kw"] * factor, 4)
    result["total_kw"] = round(result["sensible_kw"] + result["latent_kw"], 4)
    return result


def hour_total(hour, contributions, safety_factor):
    components = combine_components(contributions)
    sensible = round(sum(item["sensible_kw"] for item in components.values()), 4)
    latent = round(sum(item["latent_kw"] for item in components.values()), 4)
    subtotal = round(sensible + latent, 4)
    safety = round(subtotal * (safety_factor - 1), 4)
    return {
        "hour": hour, "components": components, "subtotal_sensible_kw": sensible, "subtotal_latent_kw": latent,
        "subtotal_kw": subtotal, "safety_factor": safety_factor, "safety_allowance_kw": safety,
        "design_total_kw": round(subtotal + safety, 4),
    }


def combine_components(contributions):
    result = {}
    for item in contributions:
        current = result.setdefault(item["name"], {"sensible_kw": 0.0, "latent_kw": 0.0, "total_kw": 0.0, "base_sensible_kw": 0.0, "base_latent_kw": 0.0, "schedule_factors": []})
        for key in ("sensible_kw", "latent_kw", "base_sensible_kw", "base_latent_kw"):
            current[key] = round(current[key] + item.get(key, 0), 4)
        current["total_kw"] = round(current["sensible_kw"] + current["latent_kw"], 4)
        if "schedule_factor" in item:
            current["schedule_factors"].append(item["schedule_factor"])
    return result


def aggregate_zones(rooms, zone_lookup):
    grouped = {}
    for room in rooms:
        grouped.setdefault(room["zone_id"], []).append(room)
    result = []
    for zone_id, members in grouped.items():
        hours = [aggregate_hours([room["hours"][hour] for room in members], hour) for hour in range(24)]
        zone = zone_lookup.get(zone_id, {})
        result.append({
            "zone_id": zone_id, "name": zone.get("name", zone_id), "floor_id": zone.get("floor_id", ""),
            "room_ids": [room["room_id"] for room in members], "hours": hours, "peak": peak(hours),
        })
    return sorted(result, key=lambda item: item["zone_id"])


def aggregate_floors(zones, floor_lookup):
    grouped = {}
    for zone in zones:
        grouped.setdefault(zone.get("floor_id", ""), []).append(zone)
    result = []
    for floor_id, members in grouped.items():
        hours = [aggregate_hours([zone["hours"][hour] for zone in members], hour) for hour in range(24)]
        floor = floor_lookup.get(floor_id, {})
        result.append({
            "floor_id": floor_id, "name": floor.get("name", floor_id),
            "zone_ids": [zone["zone_id"] for zone in members],
            "room_ids": [room_id for zone in members for room_id in zone["room_ids"]],
            "hours": hours, "peak": peak(hours),
        })
    return sorted(result, key=lambda item: item["floor_id"])


def aggregate_project(zones):
    return [aggregate_hours([zone["hours"][hour] for zone in zones], hour) for hour in range(24)]


def aggregate_hours(rows, hour):
    components = {}
    for row in rows:
        for name, item in row["components"].items():
            current = components.setdefault(name, {"sensible_kw": 0.0, "latent_kw": 0.0, "total_kw": 0.0})
            current["sensible_kw"] = round(current["sensible_kw"] + item["sensible_kw"], 4)
            current["latent_kw"] = round(current["latent_kw"] + item["latent_kw"], 4)
            current["total_kw"] = round(current["sensible_kw"] + current["latent_kw"], 4)
    sensible = round(sum(item["sensible_kw"] for item in components.values()), 4)
    latent = round(sum(item["latent_kw"] for item in components.values()), 4)
    subtotal = round(sensible + latent, 4)
    safety = round(sum(row["safety_allowance_kw"] for row in rows), 4)
    return {"hour": hour, "components": components, "subtotal_sensible_kw": sensible, "subtotal_latent_kw": latent, "subtotal_kw": subtotal, "safety_allowance_kw": safety, "design_total_kw": round(subtotal + safety, 4)}


def peak(hours):
    if not hours:
        return {}
    maximum = max(item["design_total_kw"] for item in hours)
    tied = [item["hour"] for item in hours if item["design_total_kw"] == maximum]
    display = next(item for item in hours if item["hour"] == min(tied))
    return {
        "design_total_kw": maximum, "tied_hours": tied, "display_hour": min(tied), "hour": min(tied),
        "sensible_kw": display["subtotal_sensible_kw"], "latent_kw": display["subtotal_latent_kw"],
        "total_kw": display["subtotal_kw"], "components": deepcopy(display["components"]),
    }


def governing_peak(scenarios, peak_key="included_scope_peak"):
    choices = [scenario for scenario in scenarios if scenario.get(peak_key)]
    if not choices:
        return {}
    maximum = max(item[peak_key]["design_total_kw"] for item in choices)
    tied = [{"scenario_id": item["scenario_id"], "month": item["representative_month"], "hours": item[peak_key]["tied_hours"]} for item in choices if item[peak_key]["design_total_kw"] == maximum]
    first = tied[0]
    governing = next(item for item in choices if item["scenario_id"] == first["scenario_id"])
    result = deepcopy(governing[peak_key])
    result.update({"scenario_id": first["scenario_id"], "month": first["month"], "ties": tied})
    return result


def effective(zone, requirements, key):
    return zone.get(key) if zone.get(key) is not None else requirements.get(key)


def requirements_snapshot(raw):
    """Validate requirement fields without manufacturing a new persisted revision."""
    stored_timestamp = raw.get("updated_at", "") if isinstance(raw, dict) else ""
    result = validate_design_requirements(raw)
    result["updated_at"] = stored_timestamp
    return result


def artifact_snapshot(raw, validator):
    """Validate a saved artifact without manufacturing a new revision timestamp."""
    stored_timestamp = raw.get("updated_at", "") if isinstance(raw, dict) else ""
    result = validator(raw)
    result["updated_at"] = stored_timestamp
    return result


def text(value, label):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    return value.strip()


def text_list(values, label):
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"{label} must be a list of text values.")
    return [value.strip() for value in values if value.strip()]


def status_value(value, label):
    if value not in STATUSES:
        raise ValueError(f"{label} has an invalid status.")
    return value


def number_value(value, label, low, high):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number.") from error
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return value


def optional_number(value, label, low, high):
    return None if value in (None, "") else number_value(value, label, low, high)


def readiness(missing, provisional, extra=None):
    status = "confirmed" if not missing and not provisional else ("ready_for_engineer_confirmation" if not missing else "review_required")
    return {"status": status, "requires_engineer_review": bool(missing or provisional), "missing": missing, "provisional": provisional, **(extra or {})}
