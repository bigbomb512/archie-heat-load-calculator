#!/usr/bin/env python3

"""Build a reviewable thermal-model draft from reviewed drawing evidence."""

from copy import deepcopy
import re

from ai.design_requirements import empty_design_requirements, empty_zone_ventilation_requirements, validate_design_requirements
from ai.building_evidence import build_building_evidence, slug


EQUIPMENT = (
    ("open_display_fridge", "Open display fridge", "refrigeration"),
    ("cake_display_fridge", "Cake display fridge", "refrigeration"),
    ("upright_fridge", "Upright fridge", "refrigeration"),
    ("upright_freezer", "Upright freezer", "refrigeration"),
    ("ice_maker", "Ice maker", "appliance"),
    ("oven", "Oven", "appliance"),
    ("boiler", "Boiler", "appliance"),
)


def build_thermal_evidence(ai_input, spatial_ocr=None, vision_response=None, drawing_coverage=None, building_evidence=None):
    """Return only facts that can be tied to a reviewed source page."""
    building_evidence = building_evidence or build_building_evidence(ai_input, drawing_coverage or {}, spatial_ocr or {}, vision_response or {})
    pages = page_text(ai_input, spatial_ocr or {})
    facts = []
    for page, text in pages.items():
        add_area_facts(facts, page, text)
        add_ceiling_facts(facts, page, text)
        add_adjacency_facts(facts, page, text)
        add_equipment_facts(facts, page, text)
        add_lighting_facts(facts, page, text)
    add_building_facts(facts, building_evidence)
    classifications = {
        page.get("page"): page.get("sheet_classification", page.get("detected_type", "other"))
        for page in ai_input.get("drawing_set", {}).get("pages", [])
    }
    for fact in facts:
        for source in fact.get("evidence", []):
            source["sheet_classification"] = classifications.get(source.get("page"), "other")
    return {
        "version": 1,
        "source_pdf": ai_input.get("source_pdf", ""),
        "facts": unique_facts(facts),
        "sources": {
            "ai_input": "reviewed PDF packet",
            "spatial_ocr": bool(spatial_ocr),
            "vision_response": bool(vision_response),
            "drawing_coverage": bool(drawing_coverage),
        },
        "drawing_coverage": drawing_coverage or {},
        "building_evidence": building_evidence,
    }


def build_thermal_model(evidence):
    facts = evidence.get("facts", [])
    area = first_fact(facts, "zone_area_m2")
    ceiling = first_fact(facts, "ceiling_height_mm")
    address = first_fact(facts, "project_address")
    adjacencies = [fact for fact in facts if fact["field"] == "adjacency"]
    lighting = [fact for fact in facts if fact["field"] == "lighting_connected_w"]
    equipment = [fact for fact in facts if fact["field"] == "equipment_candidate"]
    building = evidence.get("building_evidence", {})
    spaces = building.get("spaces", [])
    zones = proposed_zones(spaces, adjacencies, lighting, equipment)
    for zone in zones:
        if zone.get("ceiling_height_mm") is None and ceiling:
            zone["ceiling_height_mm"] = ceiling.get("value")
            zone["evidence_fact_ids"].append(ceiling["fact_id"])
    review_items = [
        review("shopfront_condition", "Confirm whether the pedestrian-street shopfront is conditioned mall, semi-outdoor, or outdoors."),
        review("refrigeration_heat_rejection", "Confirm each refrigeration model, condenser location, heat rejection, and operating duty."),
        review("oven_exhaust", "Confirm oven duty and whether process exhaust is required."),
        review("occupancy_schedule", "Confirm staff/customer occupancy and operating schedule."),
        review("mechanical_capacity", "Confirm existing supply, outside-air, exhaust, and make-up-air capacity."),
        review("adjacent_conditions", "Confirm adjoining-space temperatures and roof/ceiling exposure."),
        review("design_weather", "Select and cite the engineer-approved design-weather basis for this address."),
    ]
    return {
        "version": 1,
        "status": "review_required",
        "source_pdf": evidence.get("source_pdf", ""),
        "site_context": {
            "project_address": address.get("value") if address else "",
            "status": "direct" if address else "missing",
            "source_fact_id": address.get("fact_id", "") if address else "",
            "design_weather_status": "engineer_selection_required",
        },
        "zones": zones or [fallback_zone(area, ceiling, adjacencies, lighting, equipment)],
        "review_items": review_items + building.get("exceptions", []),
        "drawing_coverage": evidence.get("drawing_coverage", {}),
        "building_evidence_summary": {key: len(building.get(key, [])) for key in ("spaces", "surfaces", "openings", "constructions", "lighting", "equipment", "cross_sheet_links")},
        "evidence_summary": {
            "direct_fact_count": len(facts),
            "exception_count": len(review_items),
        },
    }


def proposed_zones(spaces, adjacencies, lighting, equipment):
    zones = []
    for index, space in enumerate(spaces):
        name = space.get("name") or "Proposed thermal zone"
        area = area_number(space.get("area"))
        zone_id = "shop_31b" if name.lower() == "shop 31b" else "zone_" + slug(name) + f"_{index + 1}"
        zones.append({
            "zone_id": zone_id, "name": name, "usage": "", "area_m2": area, "ceiling_height_mm": None,
            "evidence_fact_ids": [], "building_evidence_ids": [space.get("id")],
            "surfaces": surface_candidates(zone_id, adjacencies),
            "internal_load_candidates": load_candidates(equipment),
            "lighting": lighting_candidate(lighting),
            "status": "direct" if space.get("status") == "direct" else "inferred",
        })
    return zones


def fallback_zone(area, ceiling, adjacencies, lighting, equipment):
    return {
        "zone_id": "thermal_zone_001", "name": "Proposed thermal zone", "usage": "food retail" if equipment else "",
        "area_m2": area.get("value") if area else None, "ceiling_height_mm": ceiling.get("value") if ceiling else None,
        "evidence_fact_ids": [fact["fact_id"] for fact in (area, ceiling) if fact], "building_evidence_ids": [],
        "surfaces": surface_candidates("thermal_zone_001", adjacencies), "internal_load_candidates": load_candidates(equipment),
        "lighting": lighting_candidate(lighting), "status": "inferred",
    }


def surface_candidates(zone_id, adjacencies):
    return [{"surface_id": f"{zone_id}-boundary-{index + 1}", "kind": "adjacent_space_boundary", "adjacency": fact["value"], "status": "direct", "source_fact_id": fact["fact_id"]} for index, fact in enumerate(adjacencies)]


def load_candidates(equipment):
    return [{"name": fact["value"]["name"], "kind": fact["value"]["kind"], "quantity": fact["value"]["quantity"], "watts": None, "status": "missing", "source_fact_id": fact["fact_id"], "question": "Confirm model, heat-to-space wattage, and operating duty."} for fact in equipment]


def lighting_candidate(lighting):
    return {"connected_w": lighting[0]["value"] if lighting else None, "status": "direct" if lighting else "missing", "source_fact_id": lighting[0]["fact_id"] if lighting else "", "question": "Confirm lighting diversity/control basis before calculation."}


def area_number(value):
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def add_building_facts(facts, building):
    for item in building.get("spaces", []):
        area = area_number(item.get("area"))
        if area is not None:
            add_fact(facts, "zone_area_m2", area, "m²", item["evidence"][0]["page"], item["evidence"][0].get("excerpt", item.get("name", "")))
    for item in building.get("lighting", []):
        if item.get("connected_w") is not None:
            add_fact(facts, "lighting_connected_w", item["connected_w"], "W", item["evidence"][0]["page"], item["evidence"][0].get("excerpt", "Lighting schedule"))
    for item in building.get("equipment", []):
        add_fact(facts, "equipment_candidate", {"id": slug(item.get("name", "equipment")), "name": item.get("name", "Equipment"), "kind": item.get("kind", "appliance"), "quantity": item.get("quantity", 1)}, "", item["evidence"][0]["page"], item["evidence"][0].get("excerpt", "Equipment"))
    for item in building.get("surfaces", []):
        if item.get("adjacency"):
            add_fact(facts, "adjacency", item["adjacency"], "", item["evidence"][0]["page"], item["evidence"][0].get("excerpt", "Surface evidence"))


def apply_thermal_model(model, decisions, requirements=None):
    """Map accepted direct facts into the existing designer-owned schema."""
    result = deepcopy(requirements or empty_design_requirements())
    decisions = decisions or {}
    zone_models = model.get("zones", [])
    if not zone_models:
        return validate_design_requirements(result)
    zones = [mapped_zone(zone_model, decisions) for zone_model in zone_models]
    result["zones"] = zones
    result["space_usage"] = zones[0]["usage"]
    if zones[0]["ceiling_height_mm"] is not None:
        result["ceiling_height_mm"] = zones[0]["ceiling_height_mm"]
        result["verification"]["ceiling"] = {
            "status": "provisional",
            "source": "Thermal model: reviewed reflected-ceiling evidence.",
        }
    return validate_design_requirements(result)


def mapped_zone(zone_model, decisions):
    zone = {
        "zone_id": zone_model["zone_id"], "name": zone_model["name"], "usage": zone_model.get("usage", ""),
        "source_room_labels": [zone_model["name"]],
        "area_m2": accepted_value(decisions, "zone_area_m2", zone_model.get("area_m2")),
        "ceiling_height_mm": accepted_value(decisions, "ceiling_height_mm", zone_model.get("ceiling_height_mm")),
        "heat_sources": [], "cooling_load": {"envelope_not_applicable": False, "verification_status": "missing", "source": "", "envelope_surfaces": []},
        "ventilation_requirements": empty_zone_ventilation_requirements(),
    }
    lighting = zone_model.get("lighting", {})
    connected_w = accepted_value(decisions, "lighting_connected_w", lighting.get("connected_w"))
    if connected_w is not None and zone["area_m2"]:
        zone["cooling_load"].update({"lighting_w_m2": round(float(connected_w) / float(zone["area_m2"]), 3), "verification_status": "provisional", "source": "Thermal model: drawing lighting schedule; confirm diversity/control basis."})
    return zone


def page_text(ai_input, spatial_ocr):
    pages = {}
    drawing_pages = ai_input.get("drawing_set", {}).get("pages", [])
    selected_pages = (
        drawing_pages
        or ai_input.get("confirmed_pages", {}).get("floor_plans", [])
        + ai_input.get("confirmed_pages", {}).get("reference_pages", [])
        + ai_input.get("source_files", {}).get("page_images", [])
    )
    for page in selected_pages:
        page_no = page.get("page")
        if not page_no:
            continue
        text = page.get("structured_content", {}).get("markdown", "")
        pages[page_no] = pages.get(page_no, "") + "\n" + text
    for page in spatial_ocr.get("pages", []):
        page_no = page.get("page")
        if not page_no:
            continue
        excerpts = [item.get("text_excerpt", "") for item in page.get("title_blocks", [])]
        pages[page_no] = pages.get(page_no, "") + "\n" + "\n".join(excerpts)
    return pages


def add_area_facts(facts, page, text):
    match = re.search(r"(?:SHOP\s*\d+[A-Z]?\s+)?AREA\s*:\s*(\d+(?:\.\d+)?)\s*(?:m²|m2)", text, re.I)
    if match:
        add_fact(facts, "zone_area_m2", match.group(1), "m²", page, match.group(0))


def add_ceiling_facts(facts, page, text):
    match = re.search(r"CH\s*:\s*(\d{4})\s*MM", text, re.I)
    if match:
        add_fact(facts, "ceiling_height_mm", match.group(1), "mm", page, match.group(0))


def add_adjacency_facts(facts, page, text):
    upper = text.upper()
    for match in re.finditer(r"(?:ADJOINING|ADJACENT)\s+([A-Z0-9][A-Z0-9 .-]{1,45})", upper):
        add_fact(facts, "adjacency", match.group(1).strip().title(), "", page, match.group(0))
    if "PEDESTR" in upper:
        add_fact(facts, "adjacency", "Pedestrian street", "", page, "Pedestrian street")


def add_equipment_facts(facts, page, text):
    upper = text.upper()
    for key, label, kind in EQUIPMENT:
        matches = re.findall(label.upper(), upper)
        if matches:
            add_fact(facts, "equipment_candidate", {"id": key, "name": label, "kind": kind, "quantity": 1}, "", page, label)


def add_lighting_facts(facts, page, text):
    values = {(int(qty), int(watts)) for watts, qty in re.findall(r"(\d+)W[^\n]{0,180}?QTY\s*:\s*(\d+)", text, re.I)}
    if values:
        add_fact(facts, "lighting_connected_w", sum(qty * watts for qty, watts in values), "W", page, "Lighting schedule quantities and nominal wattages")


def add_address_facts(facts, page, text):
    match = re.search(r"(?:SHOP\s*\d+[A-Z]?,?\s*)?CHATSWOOD INTERCHANGE[,.\s]+436\s+VICTORIA\s+AVE[,.\s]+CHATSWOOD\s+NSW\s+2067", text, re.I)
    if match:
        add_fact(facts, "project_address", re.sub(r"\s+", " ", match.group(0)).strip(" ,."), "", page, match.group(0))


def add_fact(facts, field, value, unit, page, excerpt):
    facts.append({
        "fact_id": f"{field}-{page}-{len(facts) + 1}", "field": field, "value": normalise_value(value),
        "unit": unit, "status": "direct", "confidence": "high",
        "evidence": [{"page": page, "kind": "reviewed_pdf_text", "excerpt": excerpt}],
    })


def unique_facts(facts):
    result, seen = [], set()
    for fact in facts:
        key = (fact["field"], str(fact["value"]).lower())
        if key not in seen:
            seen.add(key)
            result.append(fact)
    return result


def first_fact(facts, field):
    return next((fact for fact in facts if fact["field"] == field), None)


def normalise_value(value):
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    return value


def review(item_id, question):
    return {"item_id": item_id, "status": "missing", "question": question}


def accepted_value(decisions, key, value):
    decision = decisions.get(key, {}) if isinstance(decisions, dict) else {}
    if decision.get("decision") in {"reject", "not_applicable"}:
        return None
    result = decision.get("value", value) if decision.get("decision") in {"accept", "edit"} else value
    if isinstance(value, (int, float)) and isinstance(result, str):
        try:
            return float(result)
        except ValueError:
            return value
    return result
