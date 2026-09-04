#!/usr/bin/env python3

from copy import deepcopy
from datetime import datetime, timezone
import re


NUMERIC_FIELDS = {
    "occupancy": "occupancy",
    "indoor_cooling_setpoint_c": "indoor cooling setpoint",
    "indoor_heating_setpoint_c": "indoor heating setpoint",
    "outdoor_summer_db_c": "outdoor summer condition",
    "outdoor_winter_db_c": "outdoor winter condition",
    "ceiling_height_mm": "ceiling height",
    "ceiling_void_height_mm": "ceiling void height",
}

REQUIRED_INPUTS = {
    "space_usage": "space usage",
    "occupancy": "peak expected occupancy",
    "operating_hours": "operating hours",
    "design_conditions": "indoor and outdoor design conditions",
    "outside_air": "outside-air basis",
    "exhaust": "exhaust basis",
    "heat_sources": "major appliance/equipment heat loads",
    "ceiling": "ceiling height and void availability",
    "existing_services": "existing mechanical/electrical/service constraints",
    "code_basis": "applicable Australian code basis",
}

VERIFICATION_CATEGORIES = (
    "occupancy",
    "design_conditions",
    "outside_air",
    "exhaust",
    "heat_sources",
    "ceiling",
    "existing_services",
)
VERIFICATION_STATUSES = {"confirmed", "provisional", "missing", "not_applicable"}
EXHAUST_OUTCOMES = {"unknown", "not_required", "general_exhaust", "process_kitchen_exhaust"}
ACTIVITY_OPTIONS = {"unknown", "none", "baking_or_cooking"}
REQUIREMENT_OPTIONS = {"unknown", "required", "not_required"}
HEAT_SOURCE_KINDS = {"", "appliance", "refrigeration", "other"}
SURFACE_KINDS = {"", "opaque_wall", "roof", "glazing", "other"}
ORIENTATIONS = {"", "N", "NE", "E", "SE", "S", "SW", "W", "NW", "horizontal", "internal"}
PROCESS_TYPES = {"none", "retail", "office", "toilet", "kitchen", "baking", "other"}
OUTSIDE_AIR_METHODS = {"occupancy", "area", "fixed", "combined"}
PROCESS_EXHAUST_REQUIREMENTS = {"not_required", "required", "unknown"}
RECIRCULABLE_OPTIONS = {"yes", "no", "unknown"}
UNRESOLVED_TEXT = re.compile(r"\b(tbc|to be confirmed|to confirm|verify(?: on site)?|to verify|unknown|not known)\b", re.I)
ZONE_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


def empty_design_requirements():
    return {
        "version": 5,
        "scope": "project",
        "space_usage": "",
        "occupancy": None,
        "operating_hours": "",
        "indoor_cooling_setpoint_c": None,
        "indoor_heating_setpoint_c": None,
        "outdoor_summer_db_c": None,
        "outdoor_winter_db_c": None,
        "fresh_air_basis": "",
        "exhaust_basis": "",
        "cooking_activity": "unknown",
        "hood_requirement": "unknown",
        "exhaust_outcome": "unknown",
        "make_up_air_requirement": "unknown",
        "ceiling_height_mm": None,
        "ceiling_void_height_mm": None,
        "heat_sources": [],
        "zones": [],
        "cooling_load_conditions": empty_cooling_load_conditions(),
        "existing_services": "",
        "service_constraints": {
            "electrical_capacity": "",
            "condensate_route": "",
            "outdoor_unit_location": "",
            "riser_or_base_building_services": "",
            "maintenance_access": "",
        },
        "code_basis": "",
        "designer_notes": "",
        "verification": {
            category: {"status": "missing", "source": ""}
            for category in VERIFICATION_CATEGORIES
        },
        "updated_at": "",
    }


def validate_design_requirements(data):
    if not isinstance(data, dict):
        raise ValueError("Design requirements must be a JSON object.")
    result = empty_design_requirements()
    for key in result:
        if key in data and key not in {"version", "scope", "updated_at"}:
            result[key] = data[key]

    for key, label in NUMERIC_FIELDS.items():
        result[key] = numeric_value(result[key], label)
    validate_measurements(result)
    validate_text_fields(result)
    validate_choices(result)
    result["heat_sources"] = validate_heat_sources(result["heat_sources"])
    result["zones"] = validate_zones(result["zones"])
    result["cooling_load_conditions"] = validate_cooling_load_conditions(result["cooling_load_conditions"])
    result["service_constraints"] = validate_service_constraints(result["service_constraints"])
    result["verification"] = validate_verification(data.get("verification", {}), result)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def numeric_value(value, label):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label.capitalize()} must be a number or blank.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label.capitalize()} must be a number or blank.") from error
    if result < 0:
        raise ValueError(f"{label.capitalize()} cannot be negative.")
    return result


def validate_measurements(result):
    occupancy = result["occupancy"]
    if occupancy is not None and (not occupancy.is_integer() or occupancy < 1):
        raise ValueError("Peak expected occupancy must be a positive whole number.")
    for key, minimum in (("ceiling_height_mm", 1000), ("ceiling_void_height_mm", 0)):
        value = result[key]
        if value is not None and (not value.is_integer() or value < minimum):
            label = NUMERIC_FIELDS[key].capitalize()
            raise ValueError(f"{label} must be a realistic whole-millimetre value.")


def validate_text_fields(result):
    for key in (
        "space_usage", "operating_hours", "fresh_air_basis", "exhaust_basis",
        "existing_services", "code_basis", "designer_notes",
    ):
        if not isinstance(result[key], str):
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be text.")
        result[key] = result[key].strip()


def validate_choices(result):
    choices = {
        "cooking_activity": ACTIVITY_OPTIONS,
        "hood_requirement": REQUIREMENT_OPTIONS,
        "exhaust_outcome": EXHAUST_OUTCOMES,
        "make_up_air_requirement": REQUIREMENT_OPTIONS,
    }
    for key, allowed in choices.items():
        if result[key] not in allowed:
            raise ValueError(f"Invalid {key.replace('_', ' ')}.")


def validate_heat_sources(sources):
    if not isinstance(sources, list):
        raise ValueError("Heat sources must be a list.")
    result = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise ValueError(f"Heat source {index} must be an object.")
        name = str(source.get("name", "")).strip()
        quantity = source.get("quantity", "")
        watts = source.get("watts", "")
        status = source.get("verification_status", "provisional")
        source_note = str(source.get("source", "")).strip()
        if not name and quantity in (None, "") and watts in (None, ""):
            continue
        if not name:
            raise ValueError(f"Heat source {index} needs a name.")
        if status not in VERIFICATION_STATUSES - {"missing", "not_applicable"}:
            raise ValueError(f"Heat source {index} needs a confirmed or provisional verification status.")
        try:
            quantity = float(quantity)
            watts = float(watts)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Heat source {index} quantity and wattage must be numbers.") from error
        if quantity <= 0 or watts < 0:
            raise ValueError(f"Heat source {index} quantity must be positive and wattage cannot be negative.")
        if status == "confirmed" and (not source_note or UNRESOLVED_TEXT.search(source_note)):
            status = "provisional"
        result.append({
            "name": name,
            "quantity": quantity,
            "watts": watts,
            "kind": validate_choice(source.get("kind", ""), HEAT_SOURCE_KINDS, f"Heat source {index} type"),
            "diversity_factor": optional_factor(source.get("diversity_factor"), f"Heat source {index} diversity factor"),
            "space_gain_factor": optional_factor(source.get("space_gain_factor"), f"Heat source {index} space-gain factor"),
            "verification_status": status,
            "source": source_note,
        })
    return result


def empty_zone():
    return {
        "zone_id": "",
        "name": "",
        "usage": "",
        "source_room_labels": [],
        "area_m2": None,
        "occupancy": None,
        "operating_hours": "",
        "indoor_cooling_setpoint_c": None,
        "indoor_heating_setpoint_c": None,
        "ceiling_height_mm": None,
        "heat_sources": [],
        "cooling_load": empty_zone_cooling_load(),
        "ventilation_requirements": empty_zone_ventilation_requirements(),
    }


def validate_zones(zones):
    if not isinstance(zones, list):
        raise ValueError("HVAC zones must be a list.")
    result = []
    seen_ids = set()
    for index, raw_zone in enumerate(zones, start=1):
        if not isinstance(raw_zone, dict):
            raise ValueError(f"HVAC zone {index} must be an object.")
        zone = empty_zone()
        for key in zone:
            if key in raw_zone:
                zone[key] = raw_zone[key]
        zone_id = str(zone["zone_id"] or "").strip().lower()
        if not ZONE_ID.fullmatch(zone_id):
            raise ValueError(f"HVAC zone {index} needs a stable ID using lowercase letters, numbers, hyphens, or underscores.")
        if zone_id in seen_ids:
            raise ValueError(f"HVAC zone ID '{zone_id}' is duplicated.")
        seen_ids.add(zone_id)
        zone["zone_id"] = zone_id
        for key in ("name", "usage", "operating_hours"):
            if not isinstance(zone[key], str):
                raise ValueError(f"HVAC zone {index} {key.replace('_', ' ')} must be text.")
            zone[key] = zone[key].strip()
        if not isinstance(zone["source_room_labels"], list) or not all(isinstance(label, str) for label in zone["source_room_labels"]):
            raise ValueError(f"HVAC zone {index} source room labels must be a list of text labels.")
        zone["source_room_labels"] = unique_text(zone["source_room_labels"])
        for key, label in (
            ("area_m2", "area"),
            ("occupancy", "occupancy"),
            ("indoor_cooling_setpoint_c", "indoor cooling setpoint"),
            ("indoor_heating_setpoint_c", "indoor heating setpoint"),
            ("ceiling_height_mm", "ceiling height"),
        ):
            zone[key] = numeric_value(zone[key], f"HVAC zone {index} {label}")
        if zone["area_m2"] is not None and zone["area_m2"] <= 0:
            raise ValueError(f"HVAC zone {index} area must be positive.")
        if zone["occupancy"] is not None and (not zone["occupancy"].is_integer() or zone["occupancy"] < 1):
            raise ValueError(f"HVAC zone {index} occupancy must be a positive whole number.")
        if zone["ceiling_height_mm"] is not None and (not zone["ceiling_height_mm"].is_integer() or zone["ceiling_height_mm"] < 1000):
            raise ValueError(f"HVAC zone {index} ceiling height must be a realistic whole-millimetre value.")
        zone["heat_sources"] = validate_heat_sources(zone["heat_sources"])
        zone["cooling_load"] = validate_zone_cooling_load(zone["cooling_load"])
        zone["ventilation_requirements"] = validate_zone_ventilation_requirements(zone["ventilation_requirements"], zone)
        result.append(zone)
    return result


def unique_text(values):
    result = []
    for value in values:
        text = value.strip()
        if text and text not in result:
            result.append(text)
    return result


def empty_cooling_load_conditions():
    return {
        "indoor_cooling_wet_bulb_c": None,
        "outdoor_summer_wet_bulb_c": None,
        "atmospheric_pressure_kpa": None,
        "verification_status": "missing",
        "source": "",
    }


def empty_zone_cooling_load():
    return {
        "people_sensible_w_per_person": None,
        "people_latent_w_per_person": None,
        "people_diversity_factor": None,
        "lighting_w_m2": None,
        "lighting_diversity_factor": None,
        "outside_air_lps": None,
        "safety_factor": None,
        "envelope_not_applicable": False,
        "envelope_surfaces": [],
        "verification_status": "missing",
        "source": "",
    }


def empty_envelope_surface():
    return {
        "surface_id": "",
        "kind": "",
        "orientation": "",
        "area_m2": None,
        "u_value_w_m2k": None,
        "solar_design_w_m2": None,
        "solar_gain_factor": None,
        "shading_factor": None,
        "verification_status": "missing",
        "source": "",
    }


def empty_zone_ventilation_requirements():
    return {
        "process_type": "none",
        "basis_name": "",
        "basis_source": "",
        "outside_air_method": "combined",
        "people_rate_lps_per_person": None,
        "area_rate_lps_per_m2": None,
        "fixed_minimum_lps": None,
        "process_exhaust_requirement": "unknown",
        "process_exhaust_lps": None,
        "hood_type_or_duty": "",
        "recirculable": "unknown",
        "allowable_transfer_air_lps": None,
        "allowable_outside_air_credit_lps": None,
        "design_supply_lps_including_outside_air": None,
        "return_or_relief_lps": None,
        "dedicated_make_up_air_lps": None,
        "verification_status": "missing",
        "source": "",
    }


def validate_zone_ventilation_requirements(raw, zone):
    if not isinstance(raw, dict):
        raise ValueError("Zone ventilation requirements must be an object.")
    if not ventilation_has_input(raw):
        return empty_zone_ventilation_requirements()
    result = empty_zone_ventilation_requirements()
    for key in result:
        if key in raw:
            result[key] = raw[key]
    result["process_type"] = validate_choice(result["process_type"], PROCESS_TYPES, "Zone process type")
    result["outside_air_method"] = validate_choice(result["outside_air_method"], OUTSIDE_AIR_METHODS, "Zone outside-air method")
    result["process_exhaust_requirement"] = validate_choice(result["process_exhaust_requirement"], PROCESS_EXHAUST_REQUIREMENTS, "Zone process exhaust requirement")
    result["recirculable"] = validate_choice(result["recirculable"], RECIRCULABLE_OPTIONS, "Zone recirculation status")
    result["verification_status"] = validate_choice(result["verification_status"], VERIFICATION_STATUSES, "Zone ventilation verification status")
    for key, label in (
        ("people_rate_lps_per_person", "people rate"),
        ("area_rate_lps_per_m2", "area rate"),
        ("fixed_minimum_lps", "fixed minimum"),
        ("process_exhaust_lps", "process exhaust"),
        ("allowable_transfer_air_lps", "allowable transfer-air credit"),
        ("allowable_outside_air_credit_lps", "allowable outside-air credit"),
        ("design_supply_lps_including_outside_air", "design supply"),
        ("return_or_relief_lps", "return or relief"),
        ("dedicated_make_up_air_lps", "dedicated make-up air"),
    ):
        result[key] = numeric_value(result[key], f"Zone {label}")
    for key in ("basis_name", "basis_source", "hood_type_or_duty", "source"):
        result[key] = text_value(result[key], f"Zone ventilation {key.replace('_', ' ')}")

    required_rates = {
        "occupancy": ("people_rate_lps_per_person",),
        "area": ("area_rate_lps_per_m2",),
        "fixed": ("fixed_minimum_lps",),
        "combined": ("people_rate_lps_per_person", "area_rate_lps_per_m2", "fixed_minimum_lps"),
    }[result["outside_air_method"]]
    supplied_rates = [key for key in required_rates if result[key] is not None]
    if result["outside_air_method"] == "combined" and len(supplied_rates) < 2:
        raise ValueError("Zone combined outside-air method needs at least two approved rate bases.")
    if result["outside_air_method"] != "combined" and not supplied_rates:
        raise ValueError(f"Zone {result['outside_air_method']} outside-air method needs its approved rate.")
    if result["process_type"] in {"kitchen", "baking"} and result["process_exhaust_requirement"] != "required":
        raise ValueError("Kitchen or baking zones require an explicit process-exhaust requirement.")
    if result["process_exhaust_requirement"] == "required" and result["process_exhaust_lps"] is None:
        raise ValueError("Required process exhaust needs an approved L/s flow.")
    if result["process_exhaust_requirement"] == "not_required" and result["process_type"] in {"kitchen", "baking"}:
        raise ValueError("Kitchen or baking zones cannot mark process exhaust as not required.")
    return result


def ventilation_has_input(raw):
    meaningful = (
        "basis_name", "basis_source", "people_rate_lps_per_person", "area_rate_lps_per_m2",
        "fixed_minimum_lps", "process_exhaust_lps", "hood_type_or_duty",
        "allowable_transfer_air_lps", "allowable_outside_air_credit_lps",
        "design_supply_lps_including_outside_air", "return_or_relief_lps",
        "dedicated_make_up_air_lps", "source",
    )
    if any(raw.get(key) not in (None, "") for key in meaningful):
        return True
    return any(raw.get(key) != default for key, default in (
        ("process_type", "none"),
        ("outside_air_method", "combined"),
        ("process_exhaust_requirement", "unknown"),
        ("recirculable", "unknown"),
        ("verification_status", "missing"),
    ))


def validate_cooling_load_conditions(raw):
    if not isinstance(raw, dict):
        raise ValueError("Cooling-load conditions must be an object.")
    result = empty_cooling_load_conditions()
    for key in result:
        if key in raw:
            result[key] = raw[key]
    for key, label in (
        ("indoor_cooling_wet_bulb_c", "indoor cooling wet-bulb"),
        ("outdoor_summer_wet_bulb_c", "outdoor summer wet-bulb"),
        ("atmospheric_pressure_kpa", "atmospheric pressure"),
    ):
        result[key] = numeric_value(result[key], label)
    result["verification_status"] = validate_choice(result["verification_status"], VERIFICATION_STATUSES, "Cooling-load conditions verification status")
    result["source"] = text_value(result["source"], "Cooling-load conditions source")
    return result


def validate_zone_cooling_load(raw):
    if not isinstance(raw, dict):
        raise ValueError("Zone cooling-load inputs must be an object.")
    result = empty_zone_cooling_load()
    for key in result:
        if key in raw:
            result[key] = raw[key]
    for key, label in (
        ("people_sensible_w_per_person", "people sensible gain"),
        ("people_latent_w_per_person", "people latent gain"),
        ("lighting_w_m2", "lighting density"),
        ("outside_air_lps", "outside-air flow"),
    ):
        result[key] = numeric_value(result[key], f"Zone {label}")
    for key, label in (("people_diversity_factor", "people diversity factor"), ("lighting_diversity_factor", "lighting diversity factor")):
        result[key] = optional_factor(result[key], f"Zone {label}")
    result["safety_factor"] = optional_safety_factor(result["safety_factor"], "Zone safety factor")
    if not isinstance(result["envelope_not_applicable"], bool):
        raise ValueError("Zone internal-envelope declaration must be true or false.")
    result["envelope_surfaces"] = validate_envelope_surfaces(result["envelope_surfaces"])
    result["verification_status"] = validate_choice(result["verification_status"], VERIFICATION_STATUSES, "Zone cooling-load verification status")
    result["source"] = text_value(result["source"], "Zone cooling-load source")
    return result


def validate_envelope_surfaces(surfaces):
    if not isinstance(surfaces, list):
        raise ValueError("Envelope surfaces must be a list.")
    result = []
    for index, raw_surface in enumerate(surfaces, start=1):
        if not isinstance(raw_surface, dict):
            raise ValueError(f"Envelope surface {index} must be an object.")
        surface = empty_envelope_surface()
        for key in surface:
            if key in raw_surface:
                surface[key] = raw_surface[key]
        for key in ("surface_id", "source"):
            surface[key] = text_value(surface[key], f"Envelope surface {index} {key.replace('_', ' ')}")
        surface["kind"] = validate_choice(surface["kind"], SURFACE_KINDS, f"Envelope surface {index} type")
        surface["orientation"] = validate_choice(surface["orientation"], ORIENTATIONS, f"Envelope surface {index} orientation")
        for key, label in (("area_m2", "area"), ("u_value_w_m2k", "U-value"), ("solar_design_w_m2", "design solar")):
            surface[key] = numeric_value(surface[key], f"Envelope surface {index} {label}")
        surface["solar_gain_factor"] = optional_factor(surface["solar_gain_factor"], f"Envelope surface {index} solar-gain factor")
        surface["shading_factor"] = optional_factor(surface["shading_factor"], f"Envelope surface {index} shading factor")
        surface["verification_status"] = validate_choice(surface["verification_status"], VERIFICATION_STATUSES, f"Envelope surface {index} verification status")
        result.append(surface)
    return result


def optional_factor(value, label):
    result = numeric_value(value, label)
    if result is not None and not 0 <= result <= 1:
        raise ValueError(f"{label.capitalize()} must be between 0 and 1.")
    return result


def optional_safety_factor(value, label):
    result = numeric_value(value, label)
    if result is not None and result < 1:
        raise ValueError(f"{label.capitalize()} must be at least 1.")
    return result


def validate_choice(value, allowed, label):
    if value not in allowed:
        raise ValueError(f"Invalid {label.lower()}.")
    return value


def text_value(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    return value.strip()


def validate_service_constraints(constraints):
    template = empty_design_requirements()["service_constraints"]
    if not isinstance(constraints, dict):
        raise ValueError("Service constraints must be an object.")
    result = {}
    for key in template:
        value = constraints.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be text.")
        result[key] = value.strip()
    return result


def validate_verification(raw, requirements):
    if not isinstance(raw, dict):
        raise ValueError("Verification details must be an object.")
    result = {}
    for category in VERIFICATION_CATEGORIES:
        item = raw.get(category, {})
        if not isinstance(item, dict):
            raise ValueError(f"Verification for {category.replace('_', ' ')} must be an object.")
        status = item.get("status") or inferred_status(category, requirements)
        source = item.get("source", "")
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"Invalid verification status for {category.replace('_', ' ')}.")
        if not isinstance(source, str):
            raise ValueError(f"Verification source for {category.replace('_', ' ')} must be text.")
        source = source.strip()
        if status == "confirmed" and unresolved_category(category, requirements, source):
            status = "provisional"
        result[category] = {"status": status, "source": source}
    return result


def inferred_status(category, requirements):
    return "provisional" if category_has_value(category, requirements) else "missing"


def category_has_value(category, requirements):
    values = {
        "occupancy": [requirements.get("occupancy")],
        "design_conditions": [requirements.get(key) for key in (
            "indoor_cooling_setpoint_c", "indoor_heating_setpoint_c",
            "outdoor_summer_db_c", "outdoor_winter_db_c",
        )],
        "outside_air": [requirements.get("fresh_air_basis")],
        "exhaust": [requirements.get("exhaust_basis"), requirements.get("exhaust_outcome")],
        "heat_sources": [requirements.get("heat_sources")],
        "ceiling": [requirements.get("ceiling_height_mm"), requirements.get("ceiling_void_height_mm")],
        "existing_services": [requirements.get("existing_services")],
    }
    return all(value not in (None, "", [], "unknown") for value in values[category])


def unresolved_category(category, requirements, source):
    values = {
        "occupancy": [source],
        "design_conditions": [source],
        "outside_air": [requirements.get("fresh_air_basis", ""), source],
        "exhaust": [requirements.get("exhaust_basis", ""), source],
        "heat_sources": [source] + [item.get("source", "") for item in requirements.get("heat_sources", [])],
        "ceiling": [source],
        "existing_services": [requirements.get("existing_services", ""), source],
    }
    return any(UNRESOLVED_TEXT.search(value or "") for value in values[category])


def requirements_summary(data=None):
    try:
        data = validate_design_requirements(data or empty_design_requirements())
        input_errors = []
    except ValueError as error:
        data = compatibility_requirements(data)
        input_errors = [str(error)]
    missing = []
    provisional = []
    ready = []

    for key, label in REQUIRED_INPUTS.items():
        state = input_state(key, data)
        if state == "confirmed":
            ready.append(label)
        elif state == "provisional":
            provisional.append(label)
        else:
            missing.append(label)

    zones = data.get("zones", [])
    zone_readiness = [zone_summary(zone, data) for zone in zones] if isinstance(zones, list) else []
    incomplete_zones = [item for item in zone_readiness if item["missing_inputs"]]
    status = "final_design_inputs_complete" if not missing and not provisional and not incomplete_zones else (
        "brief_allowed" if ready or provisional else "final_design_blocked"
    )
    return {
        "status": status,
        "provided_count": len(ready) + len(provisional),
        "required_count": len(REQUIRED_INPUTS),
        "confirmed_inputs": ready,
        "provisional_inputs": provisional,
        "missing_inputs": missing,
        "input_errors": input_errors,
        "zone_readiness": zone_readiness,
        "incomplete_zone_count": len(incomplete_zones),
        "final_design_blocked": bool(missing or provisional or incomplete_zones),
    }


def zone_summary(zone, project_defaults=None):
    if not isinstance(zone, dict):
        return {"zone_id": "", "name": "Invalid zone", "missing_inputs": ["valid zone data"], "inherited_inputs": [], "complete": False}
    project_defaults = project_defaults or {}
    inherited = []
    missing = []
    if not zone.get("name"):
        missing.append("zone name")
    if not zone.get("usage"):
        missing.append("zone usage")
    if zone.get("area_m2") is None:
        missing.append("area")
    if zone.get("occupancy") is None:
        missing.append("peak occupancy")
    if not zone.get("heat_sources"):
        missing.append("internal heat sources")
    for key, label in (
        ("operating_hours", "operating hours"),
        ("indoor_cooling_setpoint_c", "cooling setpoint"),
        ("indoor_heating_setpoint_c", "heating setpoint"),
        ("ceiling_height_mm", "ceiling height"),
    ):
        if zone.get(key) in (None, ""):
            if project_defaults.get(key) in (None, ""):
                missing.append(label)
            else:
                inherited.append(label)
    return {
        "zone_id": zone.get("zone_id", ""),
        "name": zone.get("name", "") or zone.get("zone_id", "Unnamed zone"),
        "missing_inputs": missing,
        "inherited_inputs": inherited,
        "complete": not missing,
    }


def compatibility_requirements(data):
    result = empty_design_requirements()
    if not isinstance(data, dict):
        return result
    for key in result:
        if key in data and key not in {"version", "scope", "updated_at"}:
            result[key] = deepcopy(data[key])
    try:
        result["verification"] = validate_verification(data.get("verification", {}), result)
    except ValueError:
        result["verification"] = empty_design_requirements()["verification"]
    return result


def input_state(key, data):
    if key in {"space_usage", "operating_hours", "code_basis"}:
        return "confirmed" if data.get(key) and not UNRESOLVED_TEXT.search(data[key]) else "missing"
    if key == "design_conditions":
        complete = all(data.get(field) is not None for field in (
            "indoor_cooling_setpoint_c", "indoor_heating_setpoint_c",
            "outdoor_summer_db_c", "outdoor_winter_db_c",
        ))
        return verification_state("design_conditions", data) if complete else "missing"
    if key == "outside_air":
        return verification_state("outside_air", data) if data.get("fresh_air_basis") else "missing"
    if key == "exhaust":
        status = verification_state("exhaust", data)
        if (
            data.get("exhaust_outcome") == "not_required"
            and status == "not_applicable"
            and data.get("exhaust_basis")
            and data.get("verification", {}).get("exhaust", {}).get("source")
        ):
            return "confirmed"
        return status if data.get("exhaust_outcome") != "unknown" and data.get("exhaust_basis") else "missing"
    if key == "heat_sources":
        status = verification_state("heat_sources", data)
        rows_confirmed = all(
            row.get("verification_status") == "confirmed" and row.get("source")
            for row in data.get("heat_sources", [])
        )
        if data.get("heat_sources") and status == "confirmed" and rows_confirmed:
            return "confirmed"
        return "provisional" if data.get("heat_sources") else "missing"
    if key == "ceiling":
        complete = data.get("ceiling_height_mm") is not None and data.get("ceiling_void_height_mm") is not None
        return verification_state("ceiling", data) if complete else "missing"
    if key == "occupancy":
        return verification_state("occupancy", data) if data.get("occupancy") is not None else "missing"
    return verification_state(key, data) if data.get(key) else "missing"


def verification_state(category, data):
    item = data.get("verification", {}).get(category, {})
    status = item.get("status", "missing")
    source = item.get("source", "")
    if status == "confirmed" and not source:
        return "provisional"
    return status
