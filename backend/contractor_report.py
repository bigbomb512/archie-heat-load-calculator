"""The contractor's report of a job's final load: what to buy, why, and every value it rests on.

`details(...)` gathers it from the job's saved files (the full hourly report, the input set and the job plan);
`render_html(details)` lays it out as one printable page set: the load, unit sizes to consider (a split per room and
one ducted system for the whole building), the design day by the hour, the peak by month, where the load comes from,
the rooms, the values used room by room with their sources, what was assumed, what isn't included, and the sources.
Charts are inline SVG, so the HTML and its PDF need no scripts or network. Every page carries the label
"Preliminary estimate from the drawings — not an engineer's design".
"""
import html
import json
from math import ceil
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZES_PATH = ROOT / "config" / "unit_sizes.json"
PACK_PATH = ROOT / "config" / "ai_preliminary_assumption_pack.json"
HOUSE_PATH = ROOT / "config" / "house_profiles.json"
STORED_HEAT_PATH = ROOT / "config" / "stored_heat.json"
TEMPLATE_VERSION = "job-report-v6"
LABEL = "Preliminary estimate from the drawings — not an engineer's design"

# Load groups in the fixed categorical order of the reference palette (dataviz skill, light mode), stacked in that order.
GROUPS = [
    ("air", "Outside air and leakage", ("outside_air", "infiltration", "transfer_air"), "#2a78d6"),
    ("sun", "Sun through glass, walls and roof", ("glazing_solar", "solar", "solar_radiation"), "#eb6834"),
    ("people", "People", ("people",), "#1baf7a"),
    ("lights", "Lights", ("lighting",), "#eda100"),
    ("equipment", "Equipment and appliances", ("equipment_refrigeration", "internal_moisture"), "#e87ba4"),
    ("conduction", "Heat through walls, roof and windows", ("envelope", "glazing_conduction", "dynamic_thermal_mass",
                                                             "dynamic_partition"), "#008300"),
    ("fan", "Fan heat", ("fan_heat",), "#6250d6"),
]
GROUP_OF = {name: key for key, _label, names, _color in GROUPS for name in names}


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def sizes():
    return _json(SIZES_PATH)


def pick(load_kw, steps):
    """(units, size kW): the smallest nominal size at or above the load; above the largest, the fewest equal units."""
    load_kw = max(float(load_kw or 0), 0.0)
    steps = sorted(float(step) for step in steps)
    units = max(1, ceil(load_kw / steps[-1] - 1e-9)) if load_kw > steps[-1] else 1
    share = load_kw / units
    return units, next(step for step in steps if step >= share - 1e-9)


def _label(units, size):
    return f"{units} × {size:g} kW" if units > 1 else f"{size:g} kW"


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ----- gathering -----------------------------------------------------------------------------------------------------

def room_sizing(report, plan_rooms):
    """Each cooled room at its own worst hour of any month, fan heat added at the report's rate, with a wall split."""
    config = sizes()
    fan_percent = _number((report.get("fan_heat") or {}).get("percent_of_room_sensible")) or 0.0
    included = {row["name"] for row in plan_rooms if row.get("final_kw") is not None}
    levels = {row["name"]: row.get("level") or "" for row in plan_rooms}
    areas = {row["name"]: row.get("area_m2") for row in plan_rooms}
    worst = {}
    for scenario in report.get("scenario_results") or []:
        for room in scenario.get("rooms") or []:
            peak = room.get("peak") or {}
            total, sensible = _number(peak.get("design_total_kw")), _number(peak.get("sensible_kw"))
            name = room.get("name", "")
            if total is None or room.get("status") == "blocked" or (included and name not in included):
                continue
            load = total + (sensible or 0.0) * fan_percent / 100
            if name not in worst or load > worst[name]["load_kw"]:
                worst[name] = {"name": name, "level": levels.get(name, ""), "area_m2": areas.get(name),
                               "month": scenario.get("representative_month", ""),
                               "hour": peak.get("display_hour", peak.get("hour")),
                               "load_kw": round(load, 2), "sensible_kw": round((sensible or 0.0) * (1 + fan_percent / 100), 2),
                               "room_kw": round(total, 2)}
    rows = []
    for row in sorted(worst.values(), key=lambda item: (-item["load_kw"], item["name"])):
        units, size = pick(row["load_kw"], config["wall_split_kw"])
        note = config["small_room_note"] if row["load_kw"] < config["small_room_kw"] else ""
        row.update({"units": units, "size_kw": size, "suggested": _label(units, size), "ducted_alternative": ""})
        if units > 1:           # too big for one wall split: a ducted (or cassette) unit is the usual choice
            duct_units, duct_size = pick(row["load_kw"], config["ducted_kw"])
            row["ducted_alternative"] = _label(duct_units, duct_size)
            note = (f"Larger than one wall split: {_label(duct_units, duct_size)} ducted is the usual choice "
                    f"(or {_label(units, size)} wall splits).")
        row["note"] = note
        row["name"] = shown(plan_rooms).get(row["name"], row["name"])
        rows.append(row)
    return rows


def shown(plan_rooms):
    """{drawing label: the name shown} from the plan's rows (a name given on the quick-fix screen, else the label)."""
    return {row["name"]: row.get("display_name") or row["name"] for row in plan_rooms if row.get("name")}


def ducted(total_kw, dwelling=False):
    config = sizes()
    units, size = pick(total_kw, config["ducted_kw"])
    note = ""
    if units > 1:
        note = (f"Larger than the biggest ducted unit in the range ({max(config['ducted_kw']):g} kW): "
                + ("several ducted systems, zoned by area (for example one per floor)." if dwelling
                   else "several ducted units zoned by area, or a commercial packaged unit."))
    return {"load_kw": round(float(total_kw or 0), 2), "units": units, "size_kw": size, "suggested": _label(units, size),
            "note": note}


def design_day(report):
    """The sizing day hour by hour, in load groups; fan heat is the hour's total less its parts."""
    peak = report.get("included_scope_peak") or {}
    scenario = next((row for row in report.get("scenario_results") or [] if row.get("scenario_id") == peak.get("scenario_id")), None)
    if not scenario or len(scenario.get("included_scope_hours") or []) != 24:
        return {}
    month = scenario.get("representative_month", "")
    seasonal = next((row for row in (report.get("seasonal") or {}).get("months") or []
                     if row.get("scenario_id") == scenario.get("scenario_id")), {})
    totals = seasonal.get("hourly_kw") if len(seasonal.get("hourly_kw") or []) == 24 else None
    hours = []
    actual_hours = []
    for index, row in enumerate(scenario["included_scope_hours"]):
        groups = {key: 0.0 for key, *_rest in GROUPS}
        for name, item in (row.get("components") or {}).items():
            groups[GROUP_OF.get(name, "equipment")] += _number((item or {}).get("total_kw")) or 0.0
        parts = sum(groups.values())
        total = _number(totals[index]) if totals else _number(row.get("design_total_kw")) or parts
        groups["fan"] += max(0.0, total - parts)
        hours.append({key: round(max(value, 0.0), 3) for key, value in groups.items()})
        signed = {key: 0.0 for key, *_rest in GROUPS}
        for name, item in (row.get("components") or {}).items():
            signed[GROUP_OF.get(name, "equipment")] += _number((item or {}).get("total_kw")) or 0.0
        actual_total = _number(totals[index]) if totals else _number(row.get("design_total_kw"))
        actual_total = actual_total if actual_total is not None else sum(signed.values())
        fan_residual = actual_total - sum(signed.values())
        signed["fan"] += fan_residual
        actual_hours.append({"total_kw": actual_total,
                             "sensible_kw": (_number(row.get("subtotal_sensible_kw")) or 0.0) + fan_residual,
                             "latent_kw": _number(row.get("subtotal_latent_kw")), "groups": signed})
    return {"month": month, "hours": hours, "totals": [round(sum(hour.values()), 2) for hour in hours],
            "actual_hours": actual_hours, "peak_hour": peak.get("display_hour", peak.get("hour"))}


def engineer_room_details(report, input_set, plan_rooms):
    """Room peaks and hourly component records from the saved calculation, without new load estimates."""
    model = (input_set.get("material") or {}).get("hourly_load_model") or {}
    by_id = {row.get("room_id"): row for row in model.get("rooms") or []}
    names = shown(plan_rooms)
    levels = {row.get("name"): row.get("level") or "" for row in plan_rooms}
    # The room's own floor from the model (room -> zone -> floor); the plan's display list is only a fallback.
    floor_names = {row.get("floor_id"): row.get("name") or "" for row in model.get("floors") or []}
    zone_floor = {row.get("zone_id"): floor_names.get(row.get("floor_id"), "") for row in model.get("zones") or []}
    best = {}
    fan_percent = _number((report.get("fan_heat") or {}).get("percent_of_room_sensible")) or 0.0
    for scenario in report.get("scenario_results") or []:
        for result in scenario.get("rooms") or []:
            peak = result.get("peak") or {}
            if result.get("status") == "blocked" or _number(peak.get("design_total_kw")) is None:
                continue
            room_id = result.get("room_id")
            if room_id in best and best[room_id]["peak"]["design_total_kw"] >= peak["design_total_kw"]:
                continue
            source = by_id.get(room_id) or {}
            name = source.get("name") or result.get("name") or room_id or "not recorded"
            load = source.get("cooling_load") or {}
            glass = {}
            for item in load.get("glazing_surfaces") or []:
                facing = item.get("orientation") or "not recorded"
                area = _number(item.get("explicit_opening_area_m2"))
                if area is not None:
                    glass[facing] = glass.get(facing, 0) + area
            external_wall = sum(_number(item.get("area_m2")) or 0 for item in load.get("envelope_surfaces") or []
                                if item.get("kind") == "opaque_wall" and item.get("boundary_method") == "external")
            roof = any(item.get("kind") == "roof" and item.get("boundary_method") == "external"
                       for item in load.get("envelope_surfaces") or [])
            area, height = _number(source.get("area_m2")), _number(source.get("ceiling_height_mm"))
            hours = []
            for hour in result.get("hours") or []:
                components = {key: {"sensible_kw": _number(value.get("sensible_kw")),
                                    "latent_kw": _number(value.get("latent_kw")),
                                    "total_kw": _number(value.get("total_kw"))}
                              for key, value in (hour.get("components") or {}).items() if isinstance(value, dict)}
                hours.append({"hour": hour.get("hour"), "total_kw": hour.get("design_total_kw"),
                              "sensible_kw": hour.get("subtotal_sensible_kw"),
                              "latent_kw": hour.get("subtotal_latent_kw"), "components": components})
            level = zone_floor.get(source.get("zone_id")) or levels.get(name, "")
            best[room_id] = {"room_id": room_id, "name": names.get(name, name), "level": level, "area_m2": area,
                             "height_mm": height, "volume_m3": area * height / 1000 if area is not None and height is not None else None,
                             "people": source.get("occupancy"), "lighting_w_m2": load.get("lighting_w_m2"),
                             "equipment_w": sum((_number(item.get("watts")) or 0) * (_number(item.get("quantity")) or 0)
                                                for item in source.get("heat_sources") or []),
                             "glass_by_facing_m2": glass, "external_wall_m2": external_wall if external_wall else None,
                             "roof_exposed": roof, "peak": peak, "month": scenario.get("representative_month") or "not recorded",
                             "fan_heat_sizing_kw": (_number(peak.get("sensible_kw")) or 0.0) * fan_percent / 100,
                             "fan_heat_percent": fan_percent, "hours": hours}
    return sorted(best.values(), key=lambda row: (row["level"], row["name"]))


def _value(row):
    return _number(row.get("value")) if isinstance(row, dict) else _number(row)


def design_conditions(report, input_set):
    """The temperatures the load was calculated at, for the top of the report (a contractor checks these first):
    the summer design point and where it comes from, the outdoor air at the sizing hour, indoor, and the difference."""
    from ai import heat_loads
    basis = (report.get("design_conditions_basis") or {}).get("design_day") or {}
    days = {row.get("day"): row.get("design_point") or {} for row in basis.get("days") or [] if isinstance(row, dict)}
    hot, humid = days.get("hot") or {}, days.get("humid") or {}
    peak = report.get("included_scope_peak") or {}
    material = (input_set or {}).get("material") or {}
    scenarios = (material.get("design_day_scenarios") or {}).get("scenarios") or []
    scenario = next((row for row in scenarios if row.get("scenario_id") == peak.get("scenario_id")), None) \
        or next((row for row in scenarios if row.get("representative_month") == peak.get("month")), None) or {}
    hours = scenario.get("hours") or []
    hour = peak.get("display_hour", peak.get("hour"))
    at_peak = next((row for row in hours if row.get("hour") == hour), {}) if isinstance(hour, int) else {}
    day_max = max((_value(row.get("outdoor_dry_bulb_c")) for row in hours if _value(row.get("outdoor_dry_bulb_c")) is not None), default=None)
    rooms = (material.get("hourly_load_model") or {}).get("rooms") or []
    # Only the rooms in the result, and no guess: rooms held at different setpoints show as a range (Codex, 2026-11-15).
    included = {row.get("name") for result in report.get("scenario_results") or []
                if result.get("scenario_id") == peak.get("scenario_id")
                for row in result.get("rooms") or [] if row.get("status") != "blocked"}
    if included:
        rooms = [row for row in rooms if row.get("name") in included]
    db_values = sorted({value for row in rooms if (value := _value(row.get("indoor_cooling_setpoint_c"))) is not None})
    wb_values = sorted({value for row in rooms
                        if (value := _value((row.get("cooling_load_conditions") or {}).get("indoor_cooling_wet_bulb_c"))) is not None})
    indoor = {"db": db_values[0] if len(db_values) == 1 else None, "wb": wb_values[0] if len(wb_values) == 1 else None,
              "db_values": db_values, "wb_values": wb_values}
    pressure = _value(scenario.get("atmospheric_pressure_kpa")) or 101.325
    if indoor["db"] is not None and indoor["wb"] is not None and indoor["wb"] <= indoor["db"]:
        ratio = heat_loads.humidity_ratio_from_db_wb(indoor["db"], indoor["wb"], pressure, "thermodynamic")
        vapour = pressure * ratio / (0.621945 + ratio)
        indoor["rh_percent"] = round(100 * vapour / heat_loads.ashrae_saturation_pressure_kpa(indoor["db"]))
    sizing = {"month": scenario.get("representative_month") or peak.get("month", ""), "hour": hour,
              "db": _value(at_peak.get("outdoor_dry_bulb_c")), "wb": _value(at_peak.get("outdoor_wet_bulb_c")), "day_max_db": day_max}
    design_db = _number(hot.get("db"))
    return {"location": ", ".join(str(part) for part in (basis.get("location"), basis.get("state")) if part),
            "source": basis.get("source", ""), "far_from_site": bool(basis.get("far_from_site")),
            "distance_km": basis.get("distance_km"),
            "summer": {"db": design_db, "wb": _number(hot.get("wb"))},
            "humid": {"db": _number(humid.get("db")), "wb": _number(humid.get("wb"))},
            "sizing": sizing, "indoor": indoor,
            "difference_k": round(design_db - indoor["db"], 1) if design_db is not None and indoor["db"] is not None else None,
            "sizing_difference_k": round(sizing["db"] - indoor["db"], 1) if sizing["db"] is not None and indoor["db"] is not None else None}


def _range(values):
    values = list(values or [])
    if not values:
        return "not recorded"
    return f"{values[0]:.1f} °C" if len(values) == 1 else f"{values[0]:.1f}–{values[-1]:.1f} °C"


def _temps(db, wb):
    if db is None:
        return "not recorded"
    return f"{db:.1f} °C DB" + (f" / {wb:.1f} °C WB" if wb is not None else "")


def design_conditions_text(c):
    """One line with the numbers, for places that show the basis as text."""
    if not c or (c.get("summer") or {}).get("db") is None:
        return ""
    sizing, indoor = c["sizing"], c["indoor"]
    parts = [f"Outdoor summer design {_temps(c['summer']['db'], c['summer']['wb'])}"
             f"{' for ' + c['location'] if c.get('location') else ''}{' (' + c['source'] + ')' if c.get('source') else ''}"]
    if sizing.get("db") is not None:
        parts.append(f"at the sizing hour ({sizing['month']} {sizing['hour']}:00) {_temps(sizing['db'], sizing['wb'])}")
    if indoor.get("db") is not None:
        parts.append(f"indoor {_temps(indoor['db'], indoor.get('wb'))}"
                     + (f" (about {indoor['rh_percent']}% RH)" if indoor.get("rh_percent") is not None else ""))
    elif indoor.get("db_values"):
        parts.append(f"indoor {_range(indoor['db_values'])} DB across rooms")
    if c.get("difference_k") is not None:
        parts.append(f"design temperature difference {c['difference_k']:.1f} K")
    return "; ".join(parts) + "."


def ahu_results(ahu_report, room_report, input_set, plan_rooms, ahu_resolution=None, dwelling=False):
    """Present saved draft AHU results only when they belong to this room-load snapshot. A house with no AHU report
    has nothing to show (its units are the splits and ducted system above), not a "not calculated" warning."""
    unavailable = lambda reason: {"status": "unavailable", "reason": reason,
                                   "next_action": "Resolve AHU and air-side inputs, then calculate preliminary coil duty."}
    if not ahu_report:
        if dwelling:
            return {"status": "not_applicable"}
        return unavailable("No preliminary AHU coil-duty report has been calculated for this job.")
    snapshot = input_set.get("input_fingerprint")
    if not snapshot or room_report.get("preliminary_input_set_fingerprint") != snapshot:
        return unavailable("The current room-load input snapshot cannot be verified.")
    if (ahu_report.get("input_snapshot_fingerprint") != snapshot or
            (ahu_report.get("input_fingerprints") or {}).get("calculator_input_snapshot") != snapshot):
        return unavailable("The AHU report belongs to an older room-load input snapshot.")
    resolution_fingerprint = (ahu_resolution or {}).get("fingerprint")
    if not resolution_fingerprint or (ahu_report.get("input_fingerprints") or {}).get("ahu_resolution") != resolution_fingerprint:
        return unavailable("AHU inputs changed after the coil-duty report was calculated.")
    if ahu_report.get("status") == "blocked" or ahu_report.get("calculation_mode") != "ai_preliminary":
        blockers = [reason for row in ahu_report.get("blocked_ahus") or [] for reason in row.get("reasons") or []]
        return unavailable(blockers[0] if blockers else "No draft AHU system has a calculated coil duty.")

    material_rooms = (input_set.get("material", {}).get("hourly_load_model") or {}).get("rooms") or []
    room_labels = {row.get("room_id"): row.get("name") for row in material_rooms if row.get("room_id")}
    display_names = shown(plan_rooms)
    scenario_labels = {row.get("scenario_id"): row.get("representative_month") or row.get("scenario_id")
                       for row in room_report.get("scenario_results") or []}
    results = {}
    for scenario in ahu_report.get("scenario_results") or []:
        for system in scenario.get("ahus") or []:
            peak = system.get("peak") or {}
            hour = peak.get("hour")
            hourly = next((row for row in system.get("hours") or [] if row.get("hour") == hour), {})
            if not hourly or _number(hourly.get("coil_total_kw")) is None:
                continue
            ident = system.get("ahu_id")
            if not ident or (ident in results and results[ident]["total_kw"] >= peak.get("design_total_kw", 0)):
                continue
            flows = hourly.get("flows_lps") or {}
            results[ident] = {
                "id": ident, "name": system.get("name") or ident,
                "type": {"constant_volume": "Constant volume", "vav": "Variable air volume"}.get(
                    system.get("system_type"), system.get("system_type") or ""),
                "number_off": system.get("number_off"), "scenario": scenario_labels.get(scenario.get("scenario_id")) or scenario.get("scenario_id") or "",
                "hour": hour, "rooms": sorted({display_names.get(room_labels.get(room_id), room_labels.get(room_id) or room_id)
                                                for room_id in system.get("room_ids") or [] if room_id}),
                "zones": sorted(str(zone) for zone in system.get("served_zone_ids") or []),
                "coil_total_kw": hourly.get("coil_total_kw"), "coil_sensible_kw": hourly.get("coil_sensible_kw"),
                "coil_latent_kw": hourly.get("coil_latent_kw"), "total_kw": peak.get("design_total_kw", 0),
                "supply_lps": flows.get("supply"), "outside_lps": flows.get("outside_air"),
                "mixed_air": hourly.get("mixed_air_state") or {}, "coil_leaving": hourly.get("coil_leaving_state") or {},
                "fan_kw": hourly.get("fan_heat_kw"), "duct_kw": hourly.get("duct_effect_kw"),
            }
    if not results:
        blockers = [reason for scenario in ahu_report.get("scenario_results") or []
                    for row in scenario.get("blocked_ahus") or [] for reason in row.get("reasons") or []]
        return unavailable(blockers[0] if blockers else "No AHU has a calculated coil duty in the saved report.")
    return {"status": "draft", "systems": sorted(results.values(), key=lambda row: (row["name"], row["id"])),
            "excluded": [row for scenario in ahu_report.get("scenario_results") or []
                         for row in scenario.get("blocked_ahus") or []]}


def breakdown(peak):
    groups = {key: 0.0 for key, *_rest in GROUPS}
    for name, item in (peak.get("components") or {}).items():
        groups[GROUP_OF.get(name, "equipment")] += _number((item or {}).get("total_kw")) or 0.0
    total = sum(groups.values()) or 1.0
    return [{"key": key, "label": label, "color": color, "kw": round(groups[key], 2), "share": groups[key] / total}
            for key, label, _names, color in GROUPS if groups[key] > 0.005]


def _source_head(text):
    """A source as a contractor reads it: the value's name without links or Archie's internal notes."""
    text = str(text or "").split(": http")[0].split(" (http")[0].split(" (")[0].strip()
    if "assumption pack" in text:
        return "Archie's preliminary default (no published source)"
    if text == "controlled_preliminary_profile":
        return "Typical W/m² allowance for the room's use (no published source)"
    if text.casefold().startswith("typical home value"):
        detail = text.split(":", 1)[1].strip() if ":" in text else ""
        return "Typical home value (no published source)" + (f": {detail}" if detail else "")
    return text


ITEM_NAMES = {"Preliminary profile equipment": "Typical equipment allowance"}


def room_values(input_set, plan_rooms):
    """Each cooled room's people, lights, outside air and equipment as the calculation used them."""
    uses = {row["name"]: (row.get("use") or {}).get("taxonomy_label") or "" for row in plan_rooms}
    included = {row["name"] for row in plan_rooms if row.get("final_kw") is not None}
    names = shown(plan_rooms)
    rows, equipment = [], []
    for room in ((input_set.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []:
        label = room.get("name", "")
        if included and label not in included:
            continue
        name = names.get(label, label)
        load = room.get("cooling_load") or {}
        rows.append({"name": name, "use": uses.get(label, ""), "area_m2": room.get("area_m2"), "people": room.get("occupancy"),
                     "people_w": (load.get("people_sensible_w_per_person"), load.get("people_latent_w_per_person")),
                     "lighting_w_m2": load.get("lighting_w_m2"), "outside_air_lps": load.get("outside_air_lps")})
        for source in room.get("heat_sources") or []:
            watts, quantity = _number(source.get("watts")) or 0.0, _number(source.get("quantity")) or 0.0
            counted = watts * quantity * (_number(source.get("diversity_factor")) or 1.0) * (_number(source.get("space_gain_factor")) or 1.0)
            if counted <= 0:
                continue
            item = str(source.get("name", ""))
            equipment.append({"room": name, "name": ITEM_NAMES.get(item, item), "quantity": quantity, "watts": watts,
                              "counted_w": round(counted), "source": _source_head(source.get("source"))})
    return rows, equipment


def envelope_values(input_set):
    """The distinct wall, roof, floor and glass values used, with how many surfaces use each and their source."""
    found = {}
    for room in ((input_set.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []:
        load = room.get("cooling_load") or {}
        for surface in load.get("envelope_surfaces") or []:
            if surface.get("boundary_method") != "external":
                continue
            key = ("surface", surface.get("kind"), surface.get("u_value_w_m2k"), _source_head(surface.get("source")))
            found.setdefault(key, 0)
            found[key] += 1
        for glass in load.get("glazing_surfaces") or []:
            window = glass.get("window") or {}
            key = ("glass", "window", (window.get("u_value_w_m2k"), window.get("shgc") or window.get("solar_transmission_factor")),
                   _source_head(glass.get("glass_source") or window.get("source") or glass.get("source")))
            found.setdefault(key, 0)
            found[key] += 1
    names = {"opaque_wall": "Outside wall", "roof": "Roof", "ceiling": "Ceiling", "floor": "Floor", "window": "Window glass"}
    rows = []
    for (kind, surface, value, source), count in sorted(found.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        text = (f"U {value[0]:g} W/m²K, SHGC {value[1]:g}" if kind == "glass" and None not in value
                else f"U {value:g} W/m²K" if isinstance(value, (int, float)) else str(value))
        rows.append({"what": names.get(surface, str(surface)), "value": text, "count": count, "source": source})
    return rows


SURFACE_WORDS = {"floor": "Floors", "ceiling": "Ceilings", "roof": "Roofs", "wall": "Walls"}


def _surface_rule(reason):
    """A per-surface boundary note as a rule shared by many surfaces: what it is next to, then what follows."""
    text = re.sub(r"\s*\(Phase 1 rule[^)]*\)", "", reason.split(" Matched to the ")[0]).strip()
    if ": " not in text:
        return text
    context, rule = text.split(": ", 1)
    if "(not air-conditioned" in context:
        return f"next to a space that isn't air-conditioned: {rule}"
    if "air-conditioned to the same temperature" in context:
        return f"next to an air-conditioned room at the same temperature: {rule}"
    if context.startswith("Over no traced room"):
        return f"over an open or untraced space: {rule}"
    return rule


def _stored_heat_line(rooms):
    defaults, coverings, count = set(), {}, 0
    for room in rooms:
        record = room.get("stored_heat") or {}
        if record.get("status") != "calculated":
            continue
        count += 1
        defaults.update(row["label"] for row in record.get("constructions", []) if row.get("chosen") == "default")
        coverings[record.get("floor_covering", "")] = coverings.get(record.get("floor_covering", ""), 0) + 1
    if not count:
        return None
    finishes = ", ".join(f"{'carpet' if name == 'carpet' else 'tiles'} in {number} room{'s' if number > 1 else ''}"
                         for name, number in sorted(coverings.items()) if name)
    return {"component": "stored heat", "rooms": [],
            "reason": ("ASHRAE's radiant time series method, with each room's factors from Archie's own heat balance "
                       "(not ASHRAE's published tables)"
                       + (f"; constructions not shown on the drawings taken as: {'; '.join(sorted(defaults))}" if defaults else "")
                       + (f"; floor finish {finishes}" if finishes else "") + ".")}


def assumed(input_set):
    """This job's assumptions (the input set's list: the full report keeps it only for the envelope-only run), one line
    per distinct statement with the rooms it applies to. Notes about one room's floor, ceiling, roof or wall are grouped
    by surface and rule, and stored heat is one line for the job, so a large house stays readable."""
    rooms = ((input_set.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []
    names = {room.get("room_id"): room.get("name", "") for room in rooms}
    grouped = {}
    for item in input_set.get("phase1_assumptions") or []:
        if not isinstance(item, dict) or not item.get("reason") or item.get("component") == "stored heat":
            continue
        component, reason = str(item.get("component") or "").strip(), str(item["reason"]).strip()
        room = names.get(item.get("room_id"), "")
        own = room and component.casefold().startswith(room.casefold() + " ")
        rest = component[len(room):].strip().split(" ")[0].casefold() if own else ""
        key = (SURFACE_WORDS[rest], _surface_rule(reason)) if rest in SURFACE_WORDS else (component, reason)
        grouped.setdefault(key, set())
        if room:
            grouped[key].add(room)
    rows = [{"component": component, "reason": reason, "rooms": sorted(members)}
            for (component, reason), members in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))]
    line = _stored_heat_line(rooms)
    return ([line] if line else []) + rows


def sources(report, dwelling):
    """The published sources the job's values rest on, in the order the report uses them."""
    pack, house, stored = _json(PACK_PATH), _json(HOUSE_PATH), _json(STORED_HEAT_PATH)
    basis = (report.get("design_conditions_basis") or {}).get("design_day") or {}
    fan = report.get("fan_heat") or {}
    config = sizes()
    rows = []
    if basis.get("source"):
        rows.append(("Design weather", basis["source"]))
    values = pack.get("value_sources") or {}
    house_values = house.get("value_sources") or {}
    if dwelling and house_values.get("lighting_w_m2"):
        rows.append(("House lighting", house_values["lighting_w_m2"]))
    if values.get("lighting_w_m2"):
        rows.append(("Lighting", values["lighting_w_m2"]))
    if values.get("occupancy_density_people_m2"):
        rows.append(("People per m²", values["occupancy_density_people_m2"]))
    if values.get("people_activity"):
        rows.append(("Heat from people", values["people_activity"]))
    if values.get("outside_air"):
        rows.append(("Outside air", values["outside_air"]))
    if dwelling:
        for key, label in (("infiltration_ach", "House air leakage"), ("cooled_hours", "House cooled hours")):
            if house_values.get(key):
                rows.append((label, house_values[key]))
    if fan.get("source"):
        rows.append(("Fan heat", f"{fan['source']}: {fan.get('basis', '')}".strip(": ")))
    if stored.get("basis"):
        rows.append(("Stored heat", stored["basis"]))
    rows.append(("Split-system sizes", config.get("wall_split_source", "")))
    rows.append(("Ducted sizes", config.get("ducted_source", "")))
    rows.append(("Unit ratings", config.get("rating_note", "")))
    # The value notes are written for Archie's own record ("unchanged" since the last decision); not for the reader.
    tidy = lambda text: str(text).replace(", unchanged:", ":").replace(", unchanged.", ".").replace(", unchanged", "")
    return [{"topic": topic, "text": tidy(text)} for topic, text in rows if text]


# ----- charts ----------------------------------------------------------------------------------------------------------

def _e(value):
    return html.escape(str(value))


def _nice_top(value):
    """(axis top, gridline step): the smallest tidy step that covers the value in at most six gridlines."""
    for step in (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        ticks = max(1, ceil(value / step - 1e-9))
        if ticks <= 6:
            return ticks * step, step
    return value, value / 5


def design_day_svg(day):
    """Stacked hourly columns (load groups in the palette's order), y in kW from zero, with the sizing hour marked."""
    if not day:
        return ""
    width, height, left, right, top, bottom = 720, 300, 48, 12, 14, 34
    plot_w, plot_h = width - left - right, height - top - bottom
    peak_total = max(day["totals"]) or 1.0
    y_max, step = _nice_top(peak_total * 1.12)       # headroom for the sizing hour's label
    bar_w = plot_w / 24
    y = lambda kw: top + plot_h - kw / y_max * plot_h
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Cooling load by hour on the {_e(day["month"])} design day, stacked by where it comes from" class="chart">']
    tick = 0.0
    while tick <= y_max + 1e-9:
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" class="axis" text-anchor="end">{tick:g}</text>')
        tick += step
    parts.append(f'<text x="12" y="{top + plot_h / 2:.0f}" class="axis" transform="rotate(-90 12 {top + plot_h / 2:.0f})" text-anchor="middle">kW</text>')
    for hour, groups in enumerate(day["hours"]):
        x = left + hour * bar_w + 2
        base = 0.0
        for key, label, _names, color in GROUPS:
            value = groups.get(key, 0.0)
            if value <= 0:
                continue
            y0, y1 = y(base), y(base + value)
            h = max(y0 - y1 - 1, 0.5)          # a 1px surface gap between stacked segments
            parts.append(f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bar_w - 4:.1f}" height="{h:.1f}" fill="{color}">'
                         f'<title>{hour:02d}:00 {_e(label)}: {value:.1f} kW</title></rect>')
            base += value
        if hour % 3 == 0:
            parts.append(f'<text x="{x + (bar_w - 4) / 2:.1f}" y="{height - bottom + 16}" class="axis" text-anchor="middle">{hour:02d}</text>')
    if isinstance(day.get("peak_hour"), int):
        hour = day["peak_hour"]
        x = left + hour * bar_w + bar_w / 2
        total = day["totals"][hour]
        # Above every bar (the neighbouring hours are often nearly as high), with a tick down to the sizing hour.
        label_y = top + 10
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{label_y + 3:.1f}" y2="{y(total) - 2:.1f}" stroke="#17242b" stroke-width="1"/>'
                     f'<text x="{x:.1f}" y="{label_y:.1f}" class="label" text-anchor="middle">{total:.1f} kW at {hour:02d}:00</text>')
    parts.append(f'<text x="{left + plot_w / 2:.0f}" y="{height - 4}" class="axis" text-anchor="middle">Hour of the day (local clock time)</text></svg>')
    return "".join(parts)


def months_svg(months, design_month):
    """Each month's peak (21st, hour by hour) as a column; the sizing month darker and labelled."""
    if not months:
        return ""
    width, height, left, right, top, bottom = 720, 220, 48, 12, 18, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max, step = _nice_top(max(row["kw"] for row in months) or 1.0)
    y = lambda kw: top + plot_h - kw / y_max * plot_h
    bar_w = plot_w / len(months)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Peak cooling load by month" class="chart">']
    tick = 0.0
    while tick <= y_max + 1e-9:
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" class="grid"/>'
                     f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" class="axis" text-anchor="end">{tick:g}</text>')
        tick += step
    for index, row in enumerate(months):
        x = left + index * bar_w + bar_w * 0.2
        design = row["month"] == design_month
        color = "#2a78d6" if design else "#a9c9ec"
        parts.append(f'<rect x="{x:.1f}" y="{y(row["kw"]):.1f}" width="{bar_w * 0.6:.1f}" height="{max(y(0) - y(row["kw"]), 0.5):.1f}" '
                     f'rx="2" fill="{color}"><title>{_e(row["month"])}: {row["kw"]:.1f} kW at {row["hour"]:02d}:00</title></rect>'
                     f'<text x="{x + bar_w * 0.3:.1f}" y="{height - bottom + 16}" class="axis" text-anchor="middle">{_e(row["month"][:3])}</text>')
        if design:
            parts.append(f'<text x="{x + bar_w * 0.3:.1f}" y="{y(row["kw"]) - 5:.1f}" class="label" text-anchor="middle">{row["kw"]:.1f} kW</text>')
    parts.append("</svg>")
    return "".join(parts)


def breakdown_svg(rows):
    """Horizontal bars, one per load group, kW and share labelled."""
    if not rows:
        return ""
    width, row_h, left, right = 720, 26, 250, 110
    height = row_h * len(rows) + 8
    top_kw = max(row["kw"] for row in rows) or 1.0
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Where the load comes from at the sizing hour" class="chart">']
    for index, row in enumerate(sorted(rows, key=lambda item: -item["kw"])):
        y0 = 4 + index * row_h
        bar = (width - left - right) * row["kw"] / top_kw
        parts.append(f'<text x="{left - 10}" y="{y0 + 15}" class="axis dark" text-anchor="end">{_e(row["label"])}</text>'
                     f'<rect x="{left}" y="{y0 + 4}" width="{max(bar, 1):.1f}" height="14" rx="2" fill="{row["color"]}">'
                     f'<title>{_e(row["label"])}: {row["kw"]:.1f} kW</title></rect>'
                     f'<text x="{left + bar + 8:.1f}" y="{y0 + 15}" class="label">{row["kw"]:.1f} kW · {round(row["share"] * 100)}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def legend():
    return "".join(f'<span class="key"><i style="background:{color}"></i>{_e(label)}</span>' for _key, label, _n, color in GROUPS)


# ----- the page ------------------------------------------------------------------------------------------------------

def design_conditions_html(c):
    """The design temperatures as cards under the total; a plain warning when they weren't recorded."""
    e = _e
    summer = c.get("summer") or {}
    if summer.get("db") is None:
        return ("<h2>Design conditions</h2><p class=warn>The design temperatures weren't recorded with this result; "
                "update the load before using this report.</p>")
    sizing, indoor, humid = c.get("sizing") or {}, c.get("indoor") or {}, c.get("humid") or {}
    where = e(c.get("location") or "the site")
    far = (f"<p class=warn>The nearest listed design location is {e(c['distance_km'])} km from the site; check it suits the site.</p>"
           if c.get("far_from_site") else "")
    lowered = (f"{summer['db'] - sizing['day_max_db']:.1f} K below the summer design day"
               if sizing.get("day_max_db") is not None and summer["db"] - sizing["day_max_db"] >= 0.05 else "the summer design day")
    def deg(value):
        return "not recorded" if value is None else f"{value:.1f} °C"

    def wet(value):
        return "" if value is None else f"{value:.1f} °C wet bulb"
    cards = [
        ("Outdoor design (summer)", deg(summer["db"]), wet(summer.get("wb")), f"{where} · {c.get('source') or 'source not recorded'}"),
        ("Outdoor at the sizing hour", deg(sizing.get("db")), wet(sizing.get("wb")),
         f"{sizing.get('month', '')} design day, {sizing.get('hour', '')}:00 · {lowered}"),
        ("Indoor design", deg(indoor.get("db")) if indoor.get("db") is not None else _range(indoor.get("db_values")),
         wet(indoor.get("wb")) if indoor.get("wb") is not None else (_range(indoor.get("wb_values")) + " wet bulb" if indoor.get("wb_values") else ""),
         f"about {indoor['rh_percent']}% relative humidity" if indoor.get("rh_percent") is not None
         else "rooms are held at different setpoints" if len(indoor.get("db_values") or []) > 1 else ""),
        ("Design temperature difference", f"{c['difference_k']:.1f} K" if c.get("difference_k") is not None
         else "differs by room" if len(indoor.get("db_values") or []) > 1 else "not recorded", "",
         "outdoor summer design dry bulb less indoor"),
    ]
    body = "".join(f"<div class=card>{e(title)}<b>{e(value)}</b>{f'<span>{e(second)}</span><br>' if second else ''}"
                   f"<span class=muted>{e(note)}</span></div>" for title, value, second, note in cards)
    check = (f" Dehumidification check day: {e(_temps(humid['db'], humid.get('wb')))}." if humid.get("db") is not None else "")
    return (f"<h2>Design conditions</h2>{far}<div class=cards>{body}</div>"
            f"<p class=muted>Each month's design day runs hour by hour from these temperatures.{check} "
            "This report is for cooling only: no winter (heating) design temperature or heating load is calculated.</p>")


def _kw(value, digits=1):
    value = _number(value)
    return "" if value is None else f"{value:.{digits}f}"


def _temperature(value):
    return f"{value:g} °C" if _number(value) is not None else "Not recorded"


def ahu_lines(ahu):
    if not ahu or ahu.get("status") == "not_applicable":
        return []
    if ahu.get("status") != "draft":
        return ["AHU duty not calculated: " + str(ahu.get("reason") or "No current AHU result is available."),
                str(ahu.get("next_action") or "")]
    lines = ["Air systems and coil duty (draft; not engineering reviewed):"]
    for system in ahu.get("systems") or []:
        lines += [f"  {system['name']} ({system['type']}, {system['number_off']} unit(s)); "
                  f"{system['scenario']} at {system['hour']}:00; "
                  f"coil {_kw(system.get('coil_total_kw'))} kW total, {_kw(system.get('coil_sensible_kw'))} kW sensible, "
                  f"{_kw(system.get('coil_latent_kw'))} kW latent.",
                  f"    Supply {_kw(system.get('supply_lps')) or 'Not recorded'} L/s; "
                  f"outdoor {_kw(system.get('outside_lps')) or 'Not recorded'} L/s; "
                  f"fan {_kw(system.get('fan_kw')) or 'Not recorded'} kW; "
                  f"duct effect {_kw(system.get('duct_kw')) or 'Not recorded'} kW.",
                  f"    Mixed air: {_temperature((system.get('mixed_air') or {}).get('dry_bulb_c'))} DB / "
                  f"{_temperature((system.get('mixed_air') or {}).get('wet_bulb_c'))} WB; "
                  f"coil leaving: {_temperature((system.get('coil_leaving') or {}).get('dry_bulb_c'))} DB / "
                  f"{_temperature((system.get('coil_leaving') or {}).get('wet_bulb_c'))} WB.",
                  "    Rooms: " + (", ".join(system.get("rooms") or []) or "Not recorded")]
    for row in ahu.get("excluded") or []:
        lines.append(f"  Not included: {row.get('ahu_id') or 'AHU'}: {'; '.join(row.get('reasons') or [])}")
    return lines


def _ahu_html(ahu):
    e = _e
    if not ahu or ahu.get("status") == "not_applicable":
        return ""
    if ahu.get("status") != "draft":
        return ("<section class=ahu-section><h2>Air systems and coil duty</h2>"
                f"<p class=warn><b>AHU duty not calculated.</b> {e(ahu.get('reason') or 'No current AHU result is available.')}</p>"
                f"<p>{e(ahu.get('next_action') or '')}</p></section>")
    cards = []
    for system in ahu.get("systems") or []:
        flows = f"Supply {_kw(system.get('supply_lps')) or 'Not recorded'} L/s · Outdoor {_kw(system.get('outside_lps')) or 'Not recorded'} L/s"
        rooms = ", ".join(system.get("rooms") or []) or "Not recorded"
        zones = ", ".join(system.get("zones") or []) or "Not recorded"
        mixed, leaving = system.get("mixed_air") or {}, system.get("coil_leaving") or {}
        cards.append(f"""<article class=ahu-card>
<h3>{e(system['name'])} <span class=muted>· {e(system['type'])} · {e(system['number_off'])} unit(s)</span></h3>
<p class=muted>Draft coil duty at {e(system['hour'])}:00 in scenario {e(system['scenario'])}. Served rooms: {e(rooms)}. Zones: {e(zones)}.</p>
<div class=ahu-metrics><div><span>Coil total</span><strong>{_kw(system['coil_total_kw'])} kW</strong></div>
<div><span>Sensible</span><strong>{_kw(system['coil_sensible_kw'])} kW</strong></div>
<div><span>Latent</span><strong>{_kw(system['coil_latent_kw'])} kW</strong></div></div>
<p>{e(flows)}</p>
<p>Mixed air: {_temperature(mixed.get('dry_bulb_c'))} DB / {_temperature(mixed.get('wet_bulb_c'))} WB. Coil leaving: {_temperature(leaving.get('dry_bulb_c'))} DB / {_temperature(leaving.get('wet_bulb_c'))} WB.</p>
<p>Separate effects: fan {_kw(system.get('fan_kw')) or 'Not recorded'} kW; duct {_kw(system.get('duct_kw')) or 'Not recorded'} kW. These are not the coil duty.</p>
</article>""")
    excluded = "".join(f"<li>{e(row.get('ahu_id') or 'AHU')}: {e('; '.join(row.get('reasons') or []))}</li>"
                       for row in ahu.get("excluded") or [])
    return ("<section class=ahu-section><h2>Air systems and coil duty</h2>"
            "<p class=muted>Draft AHU results are separate from the coincident building load and each room’s own peak. "
            "They are not engineering reviewed.</p>" + "".join(cards) +
            (f"<h3>AHUs not included</h3><ul>{excluded}</ul>" if excluded else "") + "</section>")


COMPONENT_LABELS = {"glazing_solar": "Window sun", "glazing_conduction": "Window conduction", "solar": "Wall and roof sun",
                    "envelope": "Wall, roof and floor conduction", "people": "People", "lighting": "Lights",
                    "equipment_refrigeration": "Equipment", "outside_air": "Outside air", "infiltration": "Air leakage",
                    "internal_moisture": "Internal moisture", "transfer_air": "Transfer air", "fan_heat": "Fan heat",
                    "dynamic_thermal_mass": "Stored heat", "dynamic_partition": "Partitions"}


def engineer_tables_html(details):
    """Compact A4 tables supplementing the existing graphical contractor summary."""
    e = _e
    day = details.get("design_day") or {}
    groups = [(key, label) for key, label, _members, _color in GROUPS]
    hourly = ""
    if len(day.get("actual_hours") or []) == 24:
        rows = []
        for hour, values in enumerate(day["actual_hours"]):
            total = values["total_kw"]
            cells = "".join(f"<td class=n>{_kw(values['groups'].get(key)) or '—'}</td>" for key, _label in groups)
            rows.append(f"<tr{' class=peak-row' if hour == day.get('peak_hour') else ''}><td>{hour:02d}:00</td>"
                        f"<td class=n><b>{_kw(total)}</b></td>"
                        f"<td class=n>{_kw(values.get('sensible_kw')) or 'not recorded'}</td>"
                        f"<td class=n>{_kw(values.get('latent_kw')) or 'not recorded'}</td>{cells}</tr>")
        short = {"air": "Air", "sun": "Sun", "people": "People", "lights": "Lights",
                 "equipment": "Equip.", "conduction": "Cond.", "fan": "Fan"}
        headings = "".join(f"<th class=n title='{e(label)}'>{short[key]}</th>" for key, label in groups)
        hourly = (f"<h2>Whole-building hourly load · {e(day.get('month') or 'design day')}</h2>"
                  "<p class=muted>All 24 hours are shown. Signed load groups are in kW; negative values offset cooling demand. "
                  "The highlighted hour sizes the building. The chart above shows positive gains only.</p>"
                  f"<div class=table-scroll><table class=hourly-table><tr><th>Hour</th><th class=n>Total kW</th>"
                  f"<th class=n>Sensible</th><th class=n>Latent</th>{headings}</tr>"
                  + "".join(rows) + "</table></div>")
    room_rows = details.get("engineer_rooms") or []
    if not room_rows:
        return hourly
    summary = []
    sections = []
    for room in room_rows:
        peak = room["peak"]
        area = room.get("area_m2")
        density = peak.get("design_total_kw") * 1000 / area if area else None
        summary.append(f"<tr><td>{e(room['name'])}</td><td>{e(room['level'])}</td><td class=n>{_kw(area) or 'not recorded'}</td>"
                       f"<td class=n>{_kw(peak.get('design_total_kw'), 2)}</td><td class=n>{_kw(peak.get('sensible_kw'), 2)}</td>"
                       f"<td class=n>{_kw(peak.get('latent_kw'), 2)}</td><td class=n>{_kw(density, 0) or 'not recorded'}</td>"
                       f"<td>{e(room['month'])} {e(peak.get('hour'))}:00</td></tr>")
        facts = [("Level", room["level"] or "not recorded"), ("Area", f"{area:.1f} m²" if area is not None else "not recorded"),
                 ("Ceiling height", f"{room['height_mm']:.0f} mm" if room.get("height_mm") is not None else "not recorded"),
                 ("Volume", f"{room['volume_m3']:.1f} m³" if room.get("volume_m3") is not None else "not recorded"),
                 ("People", room.get("people") if room.get("people") is not None else "not recorded"),
                 ("Lighting", f"{room['lighting_w_m2']} W/m²" if room.get("lighting_w_m2") is not None else "not recorded"),
                 ("Equipment rated", f"{room['equipment_w']:.0f} W" if room.get("equipment_w") is not None else "not recorded"),
                 ("External wall area", f"{room['external_wall_m2']:.1f} m²" if room.get("external_wall_m2") is not None else "not recorded"),
                 ("Roof exposed", "Yes" if room.get("roof_exposed") else "No recorded exposed roof"),
                 ("Glazing by facing", ", ".join(f"{key}: {value:.1f} m²" for key, value in sorted(room.get("glass_by_facing_m2", {}).items())) or "not recorded")]
        fact_html = " · ".join(f"<b>{e(key)}:</b> {e(value)}" for key, value in facts)
        peak_hour = next((row for row in room["hours"] if row.get("hour") == peak.get("hour")), {})
        components = "".join(f"<tr><td>{e(COMPONENT_LABELS.get(name, name.replace('_', ' ').capitalize()))}</td><td class=n>{_kw(value.get('sensible_kw'), 3) or 'not recorded'}</td>"
                             f"<td class=n>{_kw(value.get('latent_kw'), 3) or 'not recorded'}</td>"
                             f"<td class=n>{_kw(value.get('total_kw'), 3) or 'not recorded'}</td></tr>"
                             for name, value in sorted((peak_hour.get("components") or {}).items()))
        hours = "".join(f"<tr{' class=peak-row' if row.get('hour') == peak.get('hour') else ''}>"
                        f"<td>{row.get('hour', 0):02d}:00</td><td class=n>{_kw(row.get('total_kw'), 2) or 'not recorded'}</td>"
                        f"<td class=n>{_kw(row.get('sensible_kw'), 2) or 'not recorded'}</td>"
                        f"<td class=n>{_kw(row.get('latent_kw'), 2) or 'not recorded'}</td></tr>" for row in room["hours"])
        fan_note = (f"<p>Fan heat for separate room unit sizing: {_kw(room['fan_heat_sizing_kw'], 3)} kW "
                    f"({room['fan_heat_percent']:g}% of room sensible). It is added in the unit-sizing table, "
                    "not to this room calculation subtotal.</p>") if room.get("fan_heat_percent") else ""
        sections.append(f"<section class=room-detail><h3>{e(room['name'])}: own peak {_kw(peak.get('design_total_kw'), 2)} kW "
                        f"at {e(room['month'])} {e(peak.get('hour'))}:00</h3><p>{fact_html}</p>"
                        f"<h4>At this room’s peak · kW</h4><table><tr><th>Component</th><th class=n>Sensible</th>"
                        f"<th class=n>Latent</th><th class=n>Total</th></tr>{components}</table>{fan_note}"
                        f"<h4>{e(room['month'])} hourly room profile · kW</h4><table class=room-hours>"
                        f"<tr><th>Hour</th><th class=n>Total</th><th class=n>Sensible</th><th class=n>Latent</th></tr>"
                        f"{hours}</table></section>")
    comparison = comparison_html(details.get("setpoint_comparison") or {}, room_rows)
    return (hourly + "<h2>Room peak summary</h2><p class=muted>These are each room’s own worst hour; "
            "adding them does not give the coincident whole-building peak. Fan heat is applied to the separate room "
            "sizing figures above, not silently added to these room calculation components.</p>"
            "<div class=table-scroll><table><tr><th>Room</th><th>Level</th><th class=n>m²</th><th class=n>Total kW</th>"
            "<th class=n>Sensible</th><th class=n>Latent</th><th class=n>W/m²</th><th>Peak</th></tr>"
            + "".join(summary) + "</table></div>" + comparison + "<h2>Room detail</h2>" + "".join(sections))


def comparison_html(comparison, rooms=()):
    e = _e
    if comparison.get("status") != "calculated":
        return f"<h2>Indoor setpoint comparison</h2><p>Not calculated: {e(comparison.get('reason') or 'No compatible indoor state is recorded.')}</p>"
    variants = comparison.get("variants") or []
    baseline = (variants[0].get("peak") or {}).get("final_design_total_kw") if variants else None
    rows = []
    for item in variants:
        peak = item.get("peak") or {}
        total = peak.get("final_design_total_kw", peak.get("design_total_kw"))
        if item.get("status") == "not_calculated" or total is None:
            rows.append(f"<tr><td>{e(item['setpoint_c'])} °C</td><td colspan=8>Not calculated: {e(item.get('reason') or 'No peak recorded.')}</td></tr>")
            continue
        delta = total - baseline if baseline is not None else None
        percent = delta / baseline * 100 if baseline else None
        rows.append(f"<tr><td>{e(item['setpoint_c'])} °C</td><td class=n>{_kw(item.get('wet_bulb_c'))} °C</td>"
                    f"<td class=n>{item['relative_humidity']*100:.1f}%</td><td class=n>{_kw(total)} kW</td>"
                    f"<td class=n>{_kw(peak.get('sensible_kw'))}</td><td class=n>{_kw(peak.get('latent_kw'))}</td>"
                    f"<td>{e(peak.get('month') or 'not recorded')} {e(peak.get('hour'))}:00</td>"
                    f"<td class=n>{_kw(delta) if delta is not None else 'not recorded'}</td>"
                    f"<td class=n>{_kw(percent) if percent is not None else 'not recorded'}%</td></tr>")
    room_comparison = []
    for room in rooms:
        cells = []
        for variant in variants:
            peaks = [result.get("peak") or {} for scenario in variant.get("scenario_results") or []
                     for result in scenario.get("rooms") or [] if result.get("room_id") == room.get("room_id")]
            value = max((_number(peak.get("design_total_kw")) for peak in peaks
                         if _number(peak.get("design_total_kw")) is not None), default=None)
            cells.append(f"<td class=n>{_kw(value) if value is not None else 'not recorded'}</td>")
        room_comparison.append(f"<tr><td>{e(room['name'])}</td>{''.join(cells)}</tr>")
    room_headings = "".join(f"<th class=n>{e(variant.get('setpoint_c'))} °C kW</th>" for variant in variants)
    room_table = (f"<h3>Each room’s own peak at each setpoint</h3><table><tr><th>Room</th>{room_headings}</tr>"
                  + "".join(room_comparison) + "</table>") if rooms else ""
    return ("<h2>Indoor setpoint comparison</h2><p class=muted>Only the indoor condition changed: dry bulb, and the wet bulb "
            "that keeps the baseline relative humidity. Walls, floors and ceilings to other cooled rooms stay at the room's "
            "temperature (no heat flows through them); everything else is the baseline's. Peaks can occur at different hours.</p>"
            "<div class=table-scroll><table><tr><th>Indoor DB</th><th class=n>Indoor WB</th><th class=n>RH</th>"
            "<th class=n>Peak</th><th class=n>Sensible</th><th class=n>Latent</th><th>When</th>"
            "<th class=n>Δ kW</th><th class=n>Δ %</th></tr>" + "".join(rows) + "</table></div>" + room_table)


def render_html(d):
    e = _e
    config = sizes()
    stale = "".join(f"<p class=warn>{e(reason)}</p>" for reason in d.get("stale_reasons", []))
    shr = (d["sensible_kw"] / d["total_kw"]) if d.get("total_kw") else None
    duct = d.get("ducted") or {}
    split_rows = "".join(
        f"<tr><td>{e(row['name'])}</td><td>{e(row['level'])}</td><td>{e(row['month'][:3])} {e(row['hour'])}:00</td>"
        f"<td class=n>{_kw(row['load_kw'])}</td><td class=n>{_kw(row['sensible_kw'])}</td><td class=n><b>{e(row['suggested'])}</b></td>"
        f"<td class=muted>{e(row['note'])}</td></tr>" for row in d.get("room_sizing", []))
    components = "".join(f"<tr><td><i class=swatch style='background:{row['color']}'></i>{e(row['label'])}</td><td class=n>{_kw(row['kw'])}</td>"
                         f"<td class=n>{round(row['share'] * 100)}%</td></tr>" for row in d.get("breakdown", []))
    rooms = "".join(f"<tr><td>{e(row['name'])}</td><td>{e(row['level'])}</td><td class=n>{_kw(row['area_m2'])}</td>"
                    f"<td class=n>{_kw(row['kw'])}</td><td class=n>{'' if not row.get('w_m2') else round(row['w_m2'])}</td></tr>"
                    for row in d["rooms"])
    months = "".join(f"<tr><td>{e(row['month'])}</td><td class=n>{_kw(row['kw'])}</td><td class=n>{row['hour']:02d}:00</td></tr>"
                     for row in d["months"])
    values = "".join(
        f"<tr><td>{e(row['name'])}</td><td>{e(row['use'])}</td><td class=n>{_kw(row['area_m2'])}</td><td class=n>{_kw(row['people'], 0)}</td>"
        f"<td class=n>{_kw(row['people_w'][0], 0)} / {_kw(row['people_w'][1], 0)}</td><td class=n>{_kw(row['lighting_w_m2'])}</td>"
        f"<td class=n>{_kw(row['outside_air_lps'])}</td></tr>" for row in d.get("room_values", []))
    equipment = "".join(f"<tr><td>{e(row['room'])}</td><td>{e(row['name'])}</td><td class=n>{row['quantity']:g}</td>"
                        f"<td class=n>{row['watts']:.0f}</td><td class=n>{row['counted_w']}</td><td class=muted>{e(row['source'])}</td></tr>"
                        for row in d.get("equipment", []))
    envelope = "".join(f"<tr><td>{e(row['what'])}</td><td>{e(row['value'])}</td><td class=n>{row['count']}</td>"
                       f"<td class=muted>{e(row['source'])}</td></tr>" for row in d.get("envelope", []))
    def where(rooms):
        if not rooms:
            return ""
        text = ", ".join(rooms) if len(rooms) <= 6 else f"{len(rooms)} rooms"
        return f" <span class=muted>({e(text)})</span>"

    assumed_rows = "".join(f"<li><b>{e(row['component'].capitalize())}:</b> {e(row['reason'])}{where(row['rooms'])}</li>"
                           for row in d.get("assumed", []))
    to_confirm = "".join(f"<li>{e(text)}{f' <span class=muted>({count} rooms)</span>' if count > 1 else ''}</li>"
                         for text, count in d.get("assumptions", []))
    excluded = "".join(f"<li>{e(text)}</li>" for text in d["exclusions"])
    source_rows = "".join(f"<li><b>{e(row['topic'])}:</b> {e(row['text'])}</li>" for row in d.get("sources", []))
    not_cooled = ", ".join(e(name) for name in d["not_cooled"]) or "None"
    day = d.get("design_day") or {}
    conditions = design_conditions_html(d.get("design_conditions") or {})
    engineer = engineer_tables_html(d)
    return f"""<!doctype html><html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Cooling load: {e(d['name'])}</title>
<style>
:root{{color-scheme:light;--ink:#17242b;--muted:#5b6b73;--line:#d6dde0;--wash:#f5f7f8;--blue:#2a78d6}}
body{{font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:900px;margin:24px auto 64px;padding:0 16px;color:var(--ink);background:#fff}}
h1{{font-size:22px;margin:0}}h2{{font-size:16px;margin:26px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px;break-after:avoid}}
h3{{font-size:14px;margin:16px 0 6px}}.muted{{color:var(--muted)}}.warn{{background:#fff4e0;padding:8px 10px;border-radius:6px}}
.banner{{border:1px solid var(--ink);border-radius:6px;padding:8px 12px;margin:10px 0;font-weight:600}}
.total{{font-size:40px;font-weight:700;margin:12px 0 2px;font-variant-numeric:tabular-nums}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:12px 0}}
.card{{border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.card b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}
.ahu-card{{border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0;break-inside:avoid}}
.ahu-card h3{{margin:0 0 6px}}.ahu-card p{{margin:7px 0}}.ahu-metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}}
.ahu-metrics span{{display:block;color:var(--muted)}}.ahu-metrics strong{{font-size:18px;font-variant-numeric:tabular-nums}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}td,th{{padding:4px 8px;border-bottom:1px solid #eef1f2;text-align:left;vertical-align:top}}
th{{font-weight:600;background:var(--wash)}}td.n,th.n{{text-align:right;white-space:nowrap}}ul{{padding-left:18px}}li{{margin:3px 0}}
.chart{{width:100%;height:auto;display:block;margin:6px 0}}.chart .grid{{stroke:#e6eaec;stroke-width:1}}
.chart .axis{{font-size:11px;fill:var(--muted)}}.chart .axis.dark{{fill:var(--ink)}}.chart .label{{font-size:11px;fill:var(--ink);font-weight:600}}
.keys{{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px}}.key i,.swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.footer{{position:fixed;bottom:0;left:0;right:0;font-size:10px;color:var(--muted);text-align:center;padding:4px;background:#fff}}
svg,tr,.card{{break-inside:avoid}}
.room-detail{{break-before:page}}.room-detail h3{{break-after:avoid}}.room-detail h4{{margin:12px 0 4px}}
.room-hours{{font-size:11px}}.peak-row{{background:#dceef5;font-weight:700}}.hourly-table{{font-size:9px;table-layout:fixed}}
.hourly-table td,.hourly-table th{{padding:3px 2px}}
.table-scroll{{overflow-x:auto}}
@page{{size:A4;margin:14mm 12mm 16mm}}@media print{{body{{margin:0 auto;max-width:none}}.pagebreak{{break-before:page}}}}
@media (max-width:600px){{.total{{font-size:32px}}.ahu-metrics{{grid-template-columns:1fr}}td,th{{padding:4px}}}}
</style></head><body>
<div class=footer>{e(LABEL)} · {e(d['name'])} · generated {e(d['generated_at'])}</div>
<h1>Cooling load: {e(d['name'])}</h1><div class=muted>{e(d['address'])}</div>
<div class=banner>{e(LABEL)}. Use it to plan and quote; have the design checked before equipment is ordered for a job that needs an engineer's sign-off.</div>
{stale}
<div class=total>{_kw(d['total_kw'])} kW</div>
<div>Design cooling load for the whole building at {e(d['peak_hour'])}:00 on the {e(d['peak_month'])} design day. {e(d['rooms_counted'])} rooms counted. {e(d['household_note'])}</div>
<div class=cards>
<div class=card>Sensible<b>{_kw(d['sensible_kw'])} kW</b><span class=muted>raises the air temperature</span></div>
<div class=card>Latent<b>{_kw(d['latent_kw'])} kW</b><span class=muted>moisture from people and outside air</span></div>
<div class=card>Sensible heat ratio<b>{'' if shr is None else f'{shr:.2f}'}</b><span class=muted>check the unit's sensible capacity</span></div>
</div>
{conditions}

<h2>Units to consider</h2>
<h3>One ducted system for the whole building</h3>
<p>The building's load at its busiest hour is <b>{_kw(duct.get('load_kw'))} kW</b>: nominal size <b>{e(duct.get('suggested', ''))}</b>.
Sized on the whole building at one hour, because rooms peak at different times. {e(duct.get('note', ''))}</p>
<h3>Or a split system in each room</h3>
<p class=muted>Each room at its own worst hour of the year, with fan heat. A room's load is larger than its share of the whole
building's, because rooms don't all peak together; split systems in every room add up to more than one ducted system.</p>
<table><tr><th>Room</th><th>Level</th><th>Worst hour</th><th class=n>Load kW</th><th class=n>Sensible kW</th><th class=n>Nominal size</th><th></th></tr>
{split_rows or '<tr><td colspan=7>No room loads.</td></tr>'}</table>
<p class=muted>{e(config.get('rule', ''))} {e(config.get('rating_note', ''))} Size steps: wall splits {', '.join(f'{v:g}' for v in config.get('wall_split_kw', []))} kW;
ducted {', '.join(f'{v:g}' for v in config.get('ducted_kw', []))} kW (one manufacturer's range; see Sources).</p>

<h2>The {e(day.get('month') or d['peak_month'])} design day, hour by hour</h2>
<div class=keys>{legend()}</div>
{design_day_svg(day)}
<p class=muted>Positive heat-gain components on the design day; negative offsets are omitted from this chart. The signed net hourly load is in the table below.
Stored heat is included: sun, lights and people warm the floor and walls and reach the air over the following hours.</p>

<h2>Peak by month</h2>
{months_svg(d['months'], d['peak_month'])}
<table><tr><th>Month</th><th class=n>Peak kW</th><th class=n>Hour</th></tr>{months}</table>
<p class=muted>The 21st of each month, hour by hour, with that day's sun and the month's design temperature.</p>

<h2>Where the load comes from at the sizing hour</h2>
{breakdown_svg(d.get('breakdown', []))}
<table><tr><th>Part</th><th class=n>kW</th><th class=n>Share</th></tr>{components}</table>

<h2>Rooms</h2><p class=muted>Each room's load on the design day that sizes the system.</p>
<table><tr><th>Room</th><th>Level</th><th class=n>m²</th><th class=n>kW</th><th class=n>W/m²</th></tr>{rooms}</table>
<p class=muted>Not cooled: {not_cooled}.</p>
{engineer}
{_ahu_html(d.get('ahu') or {})}

<h2>Values used, room by room</h2>
<table><tr><th>Room</th><th>Use</th><th class=n>m²</th><th class=n>People</th><th class=n>W/person sens. / lat.</th><th class=n>Lights W/m²</th><th class=n>Outside air L/s</th></tr>
{values or '<tr><td colspan=7>None.</td></tr>'}</table>
<h3>Equipment</h3>
<table><tr><th>Room</th><th>Item</th><th class=n>Qty</th><th class=n>W each</th><th class=n>W counted</th><th>Value from</th></tr>
{equipment or '<tr><td colspan=6>No equipment counted.</td></tr>'}</table>
<p class=muted>W counted = quantity × W × how much of the time it runs × the share of its heat that stays in the room.
"Typical value" is a typical rating for that kind and size of item from Archie's equipment value list; "generic value" is
for the kind of item only, and the weakest. A manufacturer's data sheet for the installed model replaces either.</p>
<h3>Walls, roof and glass to outside</h3>
<table><tr><th>What</th><th>Value</th><th class=n>Surfaces</th><th>Source</th></tr>{envelope or '<tr><td colspan=4>None.</td></tr>'}</table>
<p class=muted>Design weather basis: {e(d['design_basis'])} The temperatures are under Design conditions at the top.</p>

<h2>Still to confirm</h2><ul>{to_confirm or '<li>None reported.</li>'}</ul>
<h2>Assumed for this job</h2><ul>{assumed_rows or '<li>None reported.</li>'}</ul>
<h2>Not included</h2><ul>{excluded or '<li>None reported.</li>'}</ul>
<h2>Sources</h2><ul>{source_rows}</ul>
</body></html>"""
