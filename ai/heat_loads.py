#!/usr/bin/env python3

"""Deterministic, designer-input cooling-load calculations.

This module deliberately contains no occupancy, lighting, ventilation, solar,
or safety defaults. Every load value is supplied by the designer and retained
with the output so a preliminary result can be reviewed later.
"""

from math import exp, isfinite, sqrt

WET_BULB_BASES = {"thermodynamic", "psychrometer", "legacy_unverified"}
AIRFLOW_REFERENCE_BASES = {"legacy_unverified", "outdoor_design_condition", "standard_air_1_2kg_da_m3"}
STANDARD_DRY_AIR_DENSITY_KG_M3 = 1.2


def contribution(name, sensible_kw=0.0, latent_kw=0.0, inputs=None, formula=""):
    sensible_kw = round(sensible_kw, 4)
    latent_kw = round(latent_kw, 4)
    return {
        "name": name,
        "sensible_kw": sensible_kw,
        "latent_kw": latent_kw,
        "total_kw": round(sensible_kw + latent_kw, 4),
        "inputs": inputs or {},
        "formula": formula,
    }


def people_load(occupancy, sensible_w_per_person, latent_w_per_person, diversity_factor):
    return contribution(
        "people",
        occupancy * sensible_w_per_person * diversity_factor / 1000,
        occupancy * latent_w_per_person * diversity_factor / 1000,
        {
            "occupancy": occupancy,
            "sensible_w_per_person": sensible_w_per_person,
            "latent_w_per_person": latent_w_per_person,
            "diversity_factor": diversity_factor,
        },
        "occupancy × W/person × diversity ÷ 1000",
    )


def lighting_load(area_m2, lighting_w_m2, diversity_factor):
    return contribution(
        "lighting",
        area_m2 * lighting_w_m2 * diversity_factor / 1000,
        inputs={"area_m2": area_m2, "lighting_w_m2": lighting_w_m2, "diversity_factor": diversity_factor},
        formula="area × lighting W/m² × diversity ÷ 1000",
    )


def equipment_load(sources):
    total_kw = 0.0
    rows = []
    latent_kw = 0.0
    for source in sources:
        gain_kw = source["quantity"] * source["watts"] * source["diversity_factor"] * source["space_gain_factor"] / 1000
        # Moisture into the room, W each (e.g. from an appliance's data sheet); none unless given.
        moisture_kw = source["quantity"] * float(source.get("latent_w") or 0) * source["diversity_factor"] / 1000
        total_kw += gain_kw
        latent_kw += moisture_kw
        rows.append({
            "name": source["name"],
            "kind": source["kind"],
            "gain_kw": round(gain_kw, 4),
            "quantity": source["quantity"],
            "heat_to_space_w_each": source["watts"],
            "diversity_factor": source["diversity_factor"],
            "space_gain_factor": source["space_gain_factor"],
            **({"latent_w_each": source["latent_w"], "latent_kw": round(moisture_kw, 4)} if source.get("latent_w") else {}),
        })
    return contribution(
        "equipment_refrigeration",
        total_kw,
        latent_kw,
        inputs={"sources": rows},
        formula="quantity × heat-to-space W each × diversity × space-gain factor ÷ 1000"
                + (" (+ quantity × latent W each × diversity ÷ 1000)" if latent_kw else ""),
    )


def envelope_load(surfaces, outdoor_db_c, indoor_db_c):
    total_kw = 0.0
    rows = []
    for surface in surfaces:
        boundary_db_c = surface.get("boundary_temperature_c")
        if boundary_db_c is None:
            boundary_db_c = outdoor_db_c
        gain_kw = surface["area_m2"] * surface["u_value_w_m2k"] * (boundary_db_c - indoor_db_c) / 1000
        total_kw += gain_kw
        rows.append({
            "surface_id": surface["surface_id"],
            "orientation": surface["orientation"],
            "boundary_method": surface.get("boundary_method", "external"),
            "boundary_temperature_c": boundary_db_c,
            "construction_id": surface.get("construction_id", ""),
            "construction_revision": surface.get("construction_revision"),
            "gain_kw": round(gain_kw, 4),
        })
    return contribution(
        "envelope",
        total_kw,
        inputs={"surfaces": rows, "outdoor_db_c": outdoor_db_c, "indoor_db_c": indoor_db_c},
        formula="surface area × U-value × (surface boundary temperature − indoor DB) ÷ 1000",
    )


def solar_load(surfaces):
    total_kw = 0.0
    rows = []
    for surface in surfaces:
        gain_kw = surface["area_m2"] * surface["solar_design_w_m2"] * surface["solar_gain_factor"] * surface["shading_factor"] / 1000
        total_kw += gain_kw
        rows.append({
            "surface_id": surface["surface_id"],
            "orientation": surface["orientation"],
            "gain_kw": round(gain_kw, 4),
        })
    return contribution(
        "solar",
        total_kw,
        inputs={"surfaces": rows},
        formula="surface area × design solar W/m² × solar-gain factor × shading factor ÷ 1000",
    )


def saturation_pressure_kpa(dry_bulb_c):
    return 0.61094 * exp(17.625 * dry_bulb_c / (dry_bulb_c + 243.04))


def ashrae_saturation_pressure_kpa(temperature_c):
    """Pure-water saturation pressure using ASHRAE F25 Eq. 5 (IAPWS-IF97).

    This equation covers liquid water from 0°C to its critical temperature.
    The existing approximation remains available for legacy and psychrometer
    paths so their established results are not silently reinterpreted.
    """
    if not isfinite(float(temperature_c)) or temperature_c < 0 or temperature_c >= 373.946:
        raise ValueError("ASHRAE/IAPWS liquid-water saturation pressure requires 0°C <= temperature < 373.946°C.")
    temperature_k = float(temperature_c) + 273.15
    theta = temperature_k - 0.23855557567849 / (temperature_k - 650.17534844798)
    a = theta * theta + 1167.0521452767 * theta - 724213.16703206
    b = -17.073846940092 * theta * theta + 12020.82470247 * theta - 3232555.0322333
    c = 14.91510861353 * theta * theta - 4823.2657361591 * theta + 405113.40542057
    discriminant = b * b - 4 * a * c
    pressure_mpa = (2 * c / (-b + sqrt(discriminant))) ** 4
    return pressure_mpa * 1000


def ashrae_ice_saturation_pressure_kpa(temperature_c):
    """Sublimation pressure over ice using ASHRAE F25 Eq. 6 (IAPWS 2008)."""
    if not isfinite(float(temperature_c)) or temperature_c < -223.15 or temperature_c >= 0:
        raise ValueError("ASHRAE/IAPWS ice saturation pressure requires -223.15°C <= temperature < 0°C.")
    theta = (float(temperature_c) + 273.15) / 273.16
    a = (-21.2144006, 27.3203819, -6.10598130)
    b = (0.00333333333, 1.20666667, 1.70333333)
    return 0.611657 * exp(sum(ai * theta ** bi for ai, bi in zip(a, b)) / theta)


def humidity_ratio_from_db_wb(dry_bulb_c, wet_bulb_c, pressure_kpa, wet_bulb_basis="legacy_unverified"):
    if wet_bulb_basis not in WET_BULB_BASES:
        raise ValueError("Wet-bulb basis must be thermodynamic, psychrometer, or legacy_unverified.")
    if not all(isfinite(float(value)) for value in (dry_bulb_c, wet_bulb_c, pressure_kpa)):
        raise ValueError("Dry-bulb, wet-bulb, and pressure must be finite numbers.")
    if wet_bulb_c > dry_bulb_c:
        raise ValueError("Wet-bulb temperature cannot exceed dry-bulb temperature.")
    if pressure_kpa <= 0:
        raise ValueError("Atmospheric pressure must be positive.")
    if wet_bulb_basis == "thermodynamic":
        if wet_bulb_c < 0:
            saturation_pressure = ashrae_ice_saturation_pressure_kpa(wet_bulb_c)
            saturated_ratio = humidity_ratio_from_vapour_pressure(saturation_pressure, pressure_kpa)
            numerator = (2830 - 0.24 * wet_bulb_c) * saturated_ratio - 1.006 * (dry_bulb_c - wet_bulb_c)
            denominator = 2830 + 1.86 * dry_bulb_c - 2.1 * wet_bulb_c
        else:
            saturated_ratio = humidity_ratio_from_vapour_pressure(ashrae_saturation_pressure_kpa(wet_bulb_c), pressure_kpa)
            numerator = (2501 - 2.326 * wet_bulb_c) * saturated_ratio - 1.006 * (dry_bulb_c - wet_bulb_c)
            denominator = 2501 + 1.86 * dry_bulb_c - 4.186 * wet_bulb_c
        ratio = numerator / denominator
        if ratio < 0:
            raise ValueError("Dry-bulb, thermodynamic wet-bulb, and pressure are not physically compatible.")
        return ratio
    psychrometric_constant = 0.00066 * (1 + 0.00115 * wet_bulb_c)
    vapour_pressure = saturation_pressure_kpa(wet_bulb_c) - psychrometric_constant * pressure_kpa * (dry_bulb_c - wet_bulb_c)
    if vapour_pressure <= 0 or vapour_pressure >= pressure_kpa:
        raise ValueError("Dry-bulb, wet-bulb, and atmospheric pressure are not physically compatible.")
    return humidity_ratio_from_vapour_pressure(vapour_pressure, pressure_kpa)


def humidity_ratio_from_vapour_pressure(vapour_pressure_kpa, pressure_kpa):
    if vapour_pressure_kpa <= 0 or vapour_pressure_kpa >= pressure_kpa:
        raise ValueError("Water-vapour pressure must be between zero and total pressure.")
    return 0.621945 * vapour_pressure_kpa / (pressure_kpa - vapour_pressure_kpa)


def wet_bulb_method_id(basis):
    return {
        "thermodynamic": "thermodynamic_ashrae_eq33_iapws_water_ice_v3",
        "psychrometer": "empirical_psychrometer_reading_approximation_v1",
        "legacy_unverified": "legacy_unverified_psychrometer_relation_v1",
    }[basis]


def moist_air_enthalpy_kj_kg(dry_bulb_c, humidity_ratio):
    return 1.006 * dry_bulb_c + humidity_ratio * (2501 + 1.86 * dry_bulb_c)


def specific_volume_m3_kg(dry_bulb_c, humidity_ratio, pressure_kpa):
    return 0.287055 * (dry_bulb_c + 273.15) * (1 + 1.607 * humidity_ratio) / pressure_kpa


def outside_air_load(flow_lps, indoor_db_c, indoor_wb_c, outdoor_db_c, outdoor_wb_c, pressure_kpa,
                     *, indoor_wet_bulb_basis="legacy_unverified", outdoor_wet_bulb_basis="legacy_unverified",
                     airflow_reference_basis="legacy_unverified", flow_source="", flow_verification_status=""):
    if airflow_reference_basis not in AIRFLOW_REFERENCE_BASES:
        raise ValueError("Outside-air airflow reference basis must be legacy_unverified, outdoor_design_condition, or standard_air_1_2kg_da_m3.")
    indoor_ratio = humidity_ratio_from_db_wb(indoor_db_c, indoor_wb_c, pressure_kpa, indoor_wet_bulb_basis)
    outdoor_ratio = humidity_ratio_from_db_wb(outdoor_db_c, outdoor_wb_c, pressure_kpa, outdoor_wet_bulb_basis)
    if airflow_reference_basis == "standard_air_1_2kg_da_m3":
        mass_flow_kg_s = flow_lps / 1000 * STANDARD_DRY_AIR_DENSITY_KG_M3
        flow_reference_state = {"dry_air_density_kg_m3": STANDARD_DRY_AIR_DENSITY_KG_M3}
    else:
        mass_flow_kg_s = flow_lps / 1000 / specific_volume_m3_kg(outdoor_db_c, outdoor_ratio, pressure_kpa)
        flow_reference_state = {"dry_bulb_c": outdoor_db_c, "humidity_ratio": round(outdoor_ratio, 9), "pressure_kpa": pressure_kpa}
    sensible_kw = mass_flow_kg_s * 1.006 * (outdoor_db_c - indoor_db_c)
    total_kw = mass_flow_kg_s * (
        moist_air_enthalpy_kj_kg(outdoor_db_c, outdoor_ratio)
        - moist_air_enthalpy_kj_kg(indoor_db_c, indoor_ratio)
    )
    return contribution(
        "outside_air",
        sensible_kw,
        total_kw - sensible_kw,
        {
            "flow_lps": flow_lps,
            "flow_reference_basis": airflow_reference_basis,
            "flow_reference_status": "calculation_assumption_unverified" if airflow_reference_basis == "legacy_unverified" else "declared_basis_unverified",
            "flow_reference_state": flow_reference_state,
            **({"flow_source": flow_source} if flow_source else {}),
            **({"flow_verification_status": flow_verification_status} if flow_verification_status else {}),
            "indoor_db_c": indoor_db_c,
            "indoor_wb_c": indoor_wb_c,
            "indoor_wet_bulb_basis": indoor_wet_bulb_basis,
            "indoor_wet_bulb_method": wet_bulb_method_id(indoor_wet_bulb_basis),
            "outdoor_db_c": outdoor_db_c,
            "outdoor_wb_c": outdoor_wb_c,
            "outdoor_wet_bulb_basis": outdoor_wet_bulb_basis,
            "outdoor_wet_bulb_method": wet_bulb_method_id(outdoor_wet_bulb_basis),
            "atmospheric_pressure_kpa": pressure_kpa,
            "mass_flow_kg_s": round(mass_flow_kg_s, 6),
        },
        "outside-air mass flow × moist-air enthalpy difference; latent = total − sensible",
    )


def transfer_air_load(flow_value, flow_unit, source_db_c, source_wb_c, target_db_c, target_wb_c, pressure_kpa,
                     *, source_room_id="", method_id="", source_wet_bulb_basis="legacy_unverified", target_wet_bulb_basis="legacy_unverified"):
    """Room-to-room air transfer using source-state volume and moist-air enthalpy.

    Signed sensible and latent terms are retained because a cooler/drier source
    room can reduce the receiving room's cooling load. The flow volume is
    referenced to the sending-room state.
    """
    flow_lps = infiltration_flow_lps(flow_value, flow_unit)
    source_ratio = humidity_ratio_from_db_wb(source_db_c, source_wb_c, pressure_kpa, source_wet_bulb_basis)
    target_ratio = humidity_ratio_from_db_wb(target_db_c, target_wb_c, pressure_kpa, target_wet_bulb_basis)
    source_volume = specific_volume_m3_kg(source_db_c, source_ratio, pressure_kpa)
    mass_flow_kg_s = flow_lps / 1000.0 / source_volume
    sensible_kw = mass_flow_kg_s * 1.006 * (source_db_c - target_db_c)
    total_kw = mass_flow_kg_s * (
        moist_air_enthalpy_kj_kg(source_db_c, source_ratio)
        - moist_air_enthalpy_kj_kg(target_db_c, target_ratio)
    )
    return contribution(
        "transfer_air", sensible_kw, total_kw - sensible_kw,
        {
            "flow_value": flow_value, "flow_unit": flow_unit, "flow_lps": round(flow_lps, 6),
            "flow_reference": "sending_room_air_state", "source_room_id": source_room_id,
            "source_db_c": source_db_c, "source_wb_c": source_wb_c,
            "source_wet_bulb_basis": source_wet_bulb_basis,
            "source_wet_bulb_method": wet_bulb_method_id(source_wet_bulb_basis),
            "target_db_c": target_db_c, "target_wb_c": target_wb_c,
            "target_wet_bulb_basis": target_wet_bulb_basis,
            "target_wet_bulb_method": wet_bulb_method_id(target_wet_bulb_basis),
            "pressure_kpa": pressure_kpa, "mass_flow_kg_s": round(mass_flow_kg_s, 8),
            "method_id": method_id,
        },
        "transfer mass flow × sending-room to receiving-room moist-air enthalpy difference; sensible = mass flow × 1.006 × dry-bulb difference",
    )
def infiltration_flow_lps(value, unit, room_volume_m3=None):
    """Resolve an approved infiltration input to outdoor-condition L/s."""
    if value is None or value <= 0:
        raise ValueError("Infiltration flow must be positive.")
    if unit == "ACH":
        if room_volume_m3 is None or room_volume_m3 <= 0:
            raise ValueError("ACH infiltration requires a positive reviewed room volume.")
        return value * room_volume_m3 / 3.6
    if unit == "L/s":
        return value
    if unit == "m3/s":
        return value * 1000
    if unit == "m3/h":
        return value / 3.6
    raise ValueError("Unsupported infiltration unit.")


def infiltration_load(value, unit, indoor_db_c, indoor_wb_c, outdoor_db_c, outdoor_wb_c, pressure_kpa,
                      *, room_volume_m3=None, schedule_factor=1.0, method_id="", gate_version="",
                      indoor_wet_bulb_basis="legacy_unverified", outdoor_wet_bulb_basis="legacy_unverified"):
    """Cooling infiltration using the approved outdoor-condition psychrometric method.

    The raw signed terms stay visible for audit. Only positive sensible and
    latent cooling gains are passed to the hourly cooling total.
    """
    base_flow_lps = infiltration_flow_lps(value, unit, room_volume_m3)
    applied_flow_lps = base_flow_lps * schedule_factor
    raw = outside_air_load(applied_flow_lps, indoor_db_c, indoor_wb_c, outdoor_db_c, outdoor_wb_c, pressure_kpa,
                           indoor_wet_bulb_basis=indoor_wet_bulb_basis, outdoor_wet_bulb_basis=outdoor_wet_bulb_basis,
                           airflow_reference_basis="outdoor_design_condition")
    raw_sensible = raw["sensible_kw"]
    raw_latent = raw["latent_kw"]
    return contribution(
        "infiltration",
        max(raw_sensible, 0.0),
        max(raw_latent, 0.0),
        {
            **raw["inputs"],
            "input_value": value,
            "input_unit": unit,
            "resolved_flow_lps": round(base_flow_lps, 6),
            "applied_flow_lps": round(applied_flow_lps, 6),
            "room_volume_m3": round(room_volume_m3, 6) if room_volume_m3 is not None else None,
            "schedule_factor": schedule_factor,
            "raw_signed_sensible_kw": raw_sensible,
            "raw_signed_latent_kw": raw_latent,
            "method_id": method_id,
            "gate_version": gate_version,
            "flow_reference": "outdoor_design_condition",
            "flow_reference_basis": "outdoor_design_condition",
            "flow_reference_status": "calculation_assumption_unverified",
        },
        "approved infiltration flow × moist-air enthalpy difference; signed diagnostics retained, positive sensible and latent cooling gains applied",
    )
def calculate_zone_cooling(requirements, zone):
    load = zone.get("cooling_load", {})
    conditions = requirements.get("cooling_load_conditions", {})
    missing = zone_missing_inputs(requirements, zone)
    if missing:
        return blocked_zone(zone, missing)
    try:
        surfaces = load.get("envelope_surfaces", [])
        contributions = [
            people_load(zone["occupancy"], load["people_sensible_w_per_person"], load["people_latent_w_per_person"], load["people_diversity_factor"]),
            lighting_load(zone["area_m2"], load["lighting_w_m2"], load["lighting_diversity_factor"]),
            equipment_load(zone["heat_sources"]),
            envelope_load(surfaces, requirements["outdoor_summer_db_c"], effective_value(zone, requirements, "indoor_cooling_setpoint_c")),
            solar_load(surfaces),
            outside_air_load(
                load["outside_air_lps"],
                effective_value(zone, requirements, "indoor_cooling_setpoint_c"),
                conditions["indoor_cooling_wet_bulb_c"],
                requirements["outdoor_summer_db_c"],
                conditions["outdoor_summer_wet_bulb_c"],
                conditions["atmospheric_pressure_kpa"],
                indoor_wet_bulb_basis=conditions.get("indoor_wet_bulb_basis", "legacy_unverified"),
                outdoor_wet_bulb_basis=conditions.get("outdoor_wet_bulb_basis", "legacy_unverified"),
                flow_source=load.get("outside_air_source", ""),
                flow_verification_status=load.get("outside_air_verification_status", "missing"),
                airflow_reference_basis=load.get("outside_air_flow_reference_basis", "legacy_unverified"),
            ),
        ]
    except ValueError as error:
        return blocked_zone(zone, [str(error)])

    subtotal_sensible = sum(item["sensible_kw"] for item in contributions)
    subtotal_latent = sum(item["latent_kw"] for item in contributions)
    subtotal = subtotal_sensible + subtotal_latent
    safety_factor = load["safety_factor"]
    safety_allowance = subtotal * (safety_factor - 1)
    provisional = zone_is_provisional(requirements, zone)
    return {
        "zone_id": zone["zone_id"],
        "zone_name": zone.get("name", zone["zone_id"]),
        "status": "calculated_provisional" if provisional else "calculated",
        "warnings": ["One or more designer inputs are provisional."] if provisional else [],
        "contributions": contributions,
        "subtotal_kw": round(subtotal, 4),
        "subtotal_sensible_kw": round(subtotal_sensible, 4),
        "subtotal_latent_kw": round(subtotal_latent, 4),
        "safety_factor": safety_factor,
        "safety_allowance_kw": round(safety_allowance, 4),
        "design_total_kw": round(subtotal * safety_factor, 4),
    }


def zone_missing_inputs(requirements, zone):
    load = zone.get("cooling_load", {})
    conditions = requirements.get("cooling_load_conditions", {})
    required = {
        "zone area": zone.get("area_m2"),
        "zone occupancy": zone.get("occupancy"),
        "indoor cooling dry-bulb": effective_value(zone, requirements, "indoor_cooling_setpoint_c"),
        "outdoor summer dry-bulb": requirements.get("outdoor_summer_db_c"),
        "indoor cooling wet-bulb": conditions.get("indoor_cooling_wet_bulb_c"),
        "outdoor summer wet-bulb": conditions.get("outdoor_summer_wet_bulb_c"),
        "atmospheric pressure": conditions.get("atmospheric_pressure_kpa"),
        "people sensible gain": load.get("people_sensible_w_per_person"),
        "people latent gain": load.get("people_latent_w_per_person"),
        "people diversity": load.get("people_diversity_factor"),
        "lighting density": load.get("lighting_w_m2"),
        "lighting diversity": load.get("lighting_diversity_factor"),
        "outside-air flow": load.get("outside_air_lps"),
        "outside-air flow source": load.get("outside_air_source"),
        "safety factor": load.get("safety_factor"),
        "cooling-load source": load.get("source"),
    }
    missing = [name for name, value in required.items() if value in (None, "")]
    if load.get("outside_air_verification_status") == "missing":
        missing.append("outside-air flow review status")
    sources = zone.get("heat_sources", [])
    if not sources:
        missing.append("zone heat sources")
    for source in sources:
        if not source.get("source"):
            missing.append(f"{source.get('name', 'heat source')} source")
        for key, label in (("kind", "type"), ("diversity_factor", "diversity"), ("space_gain_factor", "space-gain factor")):
            if source.get(key) is None or source.get(key) == "":
                missing.append(f"{source.get('name', 'heat source')} {label}")
    surfaces = load.get("envelope_surfaces", [])
    if not surfaces and not load.get("envelope_not_applicable"):
        missing.append("envelope surfaces or an internal-zone declaration")
    for surface in surfaces:
        for key, label in (
            ("surface_id", "surface ID"), ("kind", "surface type"), ("orientation", "orientation"),
            ("area_m2", "area"), ("u_value_w_m2k", "U-value"), ("solar_design_w_m2", "design solar"),
            ("solar_gain_factor", "solar-gain factor"), ("shading_factor", "shading factor"), ("source", "source"),
        ):
            if surface.get(key) in (None, ""):
                missing.append(f"{surface.get('surface_id', 'surface')} {label}")
    return missing


def effective_value(zone, requirements, key):
    return zone.get(key) if zone.get(key) is not None else requirements.get(key)


def zone_is_provisional(requirements, zone):
    load = zone.get("cooling_load", {})
    conditions = requirements.get("cooling_load_conditions", {})
    if (load.get("verification_status") != "confirmed"
            or load.get("outside_air_verification_status") != "confirmed"
            or conditions.get("verification_status") != "confirmed"):
        return True
    if any(source.get("verification_status") != "confirmed" for source in zone.get("heat_sources", [])):
        return True
    return any(surface.get("verification_status") != "confirmed" for surface in load.get("envelope_surfaces", []))


def blocked_zone(zone, reasons):
    return {
        "zone_id": zone.get("zone_id", ""),
        "zone_name": zone.get("name", zone.get("zone_id", "Unnamed zone")),
        "status": "blocked",
        "warnings": [],
        "blocked_reasons": reasons,
        "contributions": [],
    }


def calculate_heat_load_report(requirements):
    results = [calculate_zone_cooling(requirements, zone) for zone in requirements.get("zones", [])]
    calculated = [item for item in results if item["status"] != "blocked"]
    blocked = [item for item in results if item["status"] == "blocked"]
    provisional = [item for item in calculated if item["status"] == "calculated_provisional"]
    return {
        "report_type": "preliminary_zone_cooling_load",
        "requirements_updated_at": requirements.get("updated_at", ""),
        "status": "blocked" if not calculated else ("calculated_provisional" if blocked or provisional else "calculated"),
        "calculation_basis": "Designer-entered metric inputs. No code defaults are applied.",
        "zone_results": results,
        "project_total_kw": round(sum(item.get("design_total_kw", 0) for item in calculated), 4),
        "calculated_zone_count": len(calculated),
        "blocked_zone_count": len(blocked),
    }
