#!/usr/bin/env python3

"""Deterministic, designer-input cooling-load calculations.

This module deliberately contains no occupancy, lighting, ventilation, solar,
or safety defaults. Every load value is supplied by the designer and retained
with the output so a preliminary result can be reviewed later.
"""

from math import exp


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
    for source in sources:
        gain_kw = source["quantity"] * source["watts"] * source["diversity_factor"] * source["space_gain_factor"] / 1000
        total_kw += gain_kw
        rows.append({
            "name": source["name"],
            "kind": source["kind"],
            "gain_kw": round(gain_kw, 4),
            "quantity": source["quantity"],
            "heat_to_space_w_each": source["watts"],
            "diversity_factor": source["diversity_factor"],
            "space_gain_factor": source["space_gain_factor"],
        })
    return contribution(
        "equipment_refrigeration",
        total_kw,
        inputs={"sources": rows},
        formula="quantity × heat-to-space W each × diversity × space-gain factor ÷ 1000",
    )


def envelope_load(surfaces, outdoor_db_c, indoor_db_c):
    total_kw = 0.0
    rows = []
    for surface in surfaces:
        gain_kw = surface["area_m2"] * surface["u_value_w_m2k"] * (outdoor_db_c - indoor_db_c) / 1000
        total_kw += gain_kw
        rows.append({
            "surface_id": surface["surface_id"],
            "orientation": surface["orientation"],
            "gain_kw": round(gain_kw, 4),
        })
    return contribution(
        "envelope",
        total_kw,
        inputs={"surfaces": rows, "outdoor_db_c": outdoor_db_c, "indoor_db_c": indoor_db_c},
        formula="surface area × U-value × (outdoor DB − indoor DB) ÷ 1000",
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


def humidity_ratio_from_db_wb(dry_bulb_c, wet_bulb_c, pressure_kpa):
    if wet_bulb_c > dry_bulb_c:
        raise ValueError("Wet-bulb temperature cannot exceed dry-bulb temperature.")
    if pressure_kpa <= 0:
        raise ValueError("Atmospheric pressure must be positive.")
    psychrometric_constant = 0.00066 * (1 + 0.00115 * wet_bulb_c)
    vapour_pressure = saturation_pressure_kpa(wet_bulb_c) - psychrometric_constant * pressure_kpa * (dry_bulb_c - wet_bulb_c)
    if vapour_pressure <= 0 or vapour_pressure >= pressure_kpa:
        raise ValueError("Dry-bulb, wet-bulb, and atmospheric pressure are not physically compatible.")
    return 0.621945 * vapour_pressure / (pressure_kpa - vapour_pressure)


def moist_air_enthalpy_kj_kg(dry_bulb_c, humidity_ratio):
    return 1.006 * dry_bulb_c + humidity_ratio * (2501 + 1.86 * dry_bulb_c)


def specific_volume_m3_kg(dry_bulb_c, humidity_ratio, pressure_kpa):
    return 0.287055 * (dry_bulb_c + 273.15) * (1 + 1.607 * humidity_ratio) / pressure_kpa


def outside_air_load(flow_lps, indoor_db_c, indoor_wb_c, outdoor_db_c, outdoor_wb_c, pressure_kpa):
    indoor_ratio = humidity_ratio_from_db_wb(indoor_db_c, indoor_wb_c, pressure_kpa)
    outdoor_ratio = humidity_ratio_from_db_wb(outdoor_db_c, outdoor_wb_c, pressure_kpa)
    mass_flow_kg_s = flow_lps / 1000 / specific_volume_m3_kg(outdoor_db_c, outdoor_ratio, pressure_kpa)
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
            "indoor_db_c": indoor_db_c,
            "indoor_wb_c": indoor_wb_c,
            "outdoor_db_c": outdoor_db_c,
            "outdoor_wb_c": outdoor_wb_c,
            "atmospheric_pressure_kpa": pressure_kpa,
            "mass_flow_kg_s": round(mass_flow_kg_s, 6),
        },
        "outside-air mass flow × moist-air enthalpy difference; latent = total − sensible",
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
        "safety factor": load.get("safety_factor"),
        "cooling-load source": load.get("source"),
    }
    missing = [name for name, value in required.items() if value in (None, "")]
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
    if load.get("verification_status") != "confirmed" or conditions.get("verification_status") != "confirmed":
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
