"""Read-only indoor cooling setpoint comparisons for a saved preliminary input set."""

from copy import deepcopy

from ai import ai_preliminary, heat_loads, safety_factor_resolution

DEFAULT_SETPOINTS_C = (22.5, 22.0)


def compare(input_set, baseline_report, setpoints=DEFAULT_SETPOINTS_C):
    """Calculate variants in memory, retaining baseline relative humidity. Only the indoor state changes, and with it
    the surfaces held at the room's own temperature (to other cooled rooms); everything else is the baseline's."""
    material = input_set.get("material") or {}
    requirements = material.get("requirements") or {}
    rooms = (material.get("hourly_load_model") or {}).get("rooms") or []
    scenarios = (material.get("design_day_scenarios") or {}).get("scenarios") or []
    db = requirements.get("indoor_cooling_setpoint_c")
    wb = (requirements.get("cooling_load_conditions") or {}).get("indoor_cooling_wet_bulb_c")
    pressures = {value.get("value") if isinstance(value := row.get("atmospheric_pressure_kpa"), dict) else value
                 for row in scenarios}
    if isinstance(db, dict):
        db = db.get("value")
    if isinstance(wb, dict):
        wb = wb.get("value")
    if len(pressures) != 1 or None in pressures or not isinstance(db, (int, float)) or not isinstance(wb, (int, float)):
        return {"status": "not_calculated", "reason": "A single recorded indoor state and scenario pressure are required."}
    if any(row.get("indoor_cooling_setpoint_c") != db or
           (row.get("cooling_load_conditions") or {}).get("indoor_cooling_wet_bulb_c") != wb for row in rooms):
        return {"status": "not_calculated", "reason": "Rooms use different indoor cooling conditions."}
    pressure = next(iter(pressures))
    ratio = heat_loads.humidity_ratio_from_db_wb(db, wb, pressure, "thermodynamic")
    vapour = pressure * ratio / (0.621945 + ratio)
    rh = vapour / heat_loads.ashrae_saturation_pressure_kpa(db)
    baseline_peak = baseline_report.get("included_scope_peak") or {}
    if not baseline_peak:
        return {"status": "not_calculated", "reason": "The baseline cooling report has no included-scope peak."}
    variants = [{"setpoint_c": db, "wet_bulb_c": wb, "relative_humidity": rh,
                 "peak": baseline_peak, "scenario_results": baseline_report.get("scenario_results") or [],
                 "status": "baseline"}]
    policy = safety_factor_resolution.policy_for(
        input_set.get("safety_factor_resolution") or safety_factor_resolution.empty_safety_factor_resolution(),
        "cooling", preliminary=True)
    for setpoint in setpoints:
        try:
            new_wb = heat_loads.wet_bulb_from_db_rh(setpoint, rh, pressure)
            variant = deepcopy(input_set)
            variant_material = variant["material"]
            variant_material["requirements"]["indoor_cooling_setpoint_c"] = setpoint
            variant_material["requirements"]["cooling_load_conditions"]["indoor_cooling_wet_bulb_c"] = new_wb
            for room in variant_material["hourly_load_model"]["rooms"]:
                room["indoor_cooling_setpoint_c"] = setpoint
                room["cooling_load_conditions"]["indoor_cooling_wet_bulb_c"] = new_wb
                # A wall, floor or ceiling to another cooled room (or a ground floor whose flow is neglected) was given the
                # room's own temperature, so no heat flows through it. It moves with the setpoint: left at 24 °C it would
                # add heat that isn't there (about 2.6 kW at 22 °C on set 16; review 2026-11-17). An unconditioned
                # neighbour keeps its own temperature.
                for surface in (room.get("cooling_load") or {}).get("envelope_surfaces") or []:
                    if (surface.get("boundary_method") == "fixed_adjacent_temperature"
                            and isinstance(surface.get("boundary_temperature_c"), (int, float))
                            and abs(surface["boundary_temperature_c"] - db) < 1e-9):
                        surface["boundary_temperature_c"] = setpoint
            result = ai_preliminary.calculate(variant, safety_factor_policy=policy)
            variants.append({"setpoint_c": setpoint, "wet_bulb_c": new_wb, "relative_humidity": rh,
                             "peak": result.get("included_scope_peak") or {},
                             "scenario_results": result.get("scenario_results") or [], "status": "calculated"})
        except (TypeError, ValueError, KeyError) as error:
            variants.append({"setpoint_c": setpoint, "status": "not_calculated", "reason": str(error)})
    return {"status": "calculated", "pressure_kpa": pressure, "relative_humidity": rh, "variants": variants}
