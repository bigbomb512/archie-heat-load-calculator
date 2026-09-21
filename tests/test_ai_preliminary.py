"""Focused checks for the isolated, draft-only AI preliminary assembler."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.ai_preliminary import assemble, calculate, provider_eligibility, validate_manual_placeholder_entities, validate_placeholder_proposal


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def building():
    return {"spaces": [
        {"id": "room-a", "name": "Retail tenancy", "level_name": "Level 1", "area": "42 m2", "confidence": "high", "evidence": [{"page": 3, "excerpt": "Retail tenancy"}]},
        {"id": "room-b", "name": "Unlabelled conditioned space", "level_name": "Level 2", "area": "", "confidence": "low", "evidence": [{"page": 5, "excerpt": "Conditioned space"}]},
    ]}


def main():
    direct = assemble(building())
    rooms = direct["material"]["hourly_load_model"]["rooms"]
    areas = {room["name"]: room["area_m2"] for room in rooms}
    check("direct PDF area outranks controlled fallback", areas["Retail tenancy"] == 42)
    check("generic profile fallback remains available", any(row["profile_id"] == "generic_conditioned_room" for row in direct["materialized_fields"]))
    check("missing geometry falls back only through a visible controlled assumption", any(row["origin"] == "ai_assumption" for row in direct["materialized_fields"]))
    check("low-confidence estimates remain in the preliminary input set", any(row["confidence_band"] == "low" for row in direct["review_queue"]))
    report = calculate(direct)
    check("preliminary report uses the hourly engine and stays draft", report["status"] == "draft" and report["included_scope_peak"] and not report["project_peak"])
    check("unsupported components remain explicit exclusions", {row["component"] for row in direct["exclusions"]} >= {"opaque envelope", "glazing and façade solar", "infiltration"})
    surface_vision = {"result": {"auto_extraction": {"entities": [{"kind": "surface", "page": 3, "label": "Retail tenancy external wall", "area_m2": 30,
        "surface_kind": "wall", "orientation": "N", "boundary_reference": "external", "confidence": "high"}]}}}
    surface_model = assemble(building(), surface_vision)
    surface_room = next(room for room in surface_model["material"]["hourly_load_model"]["rooms"] if room["name"] == "Retail tenancy")
    check("explicitly sized AI external surface reaches the existing hourly envelope path", surface_room["cooling_load"]["envelope_surfaces"] and not surface_room["cooling_load"]["envelope_not_applicable"])
    duplicate_vision = {"result": {"auto_extraction": {"entities": [{"kind": "room", "page": 3, "label": "Retail tenancy", "level_name": "02", "area_m2": 42, "confidence": "high"}]}}}
    duplicate_building = {"spaces": [{"id": "same-room", "name": "Retail tenancy", "level_name": "Level 02", "area": "42 m2", "evidence": [{"page": 3, "excerpt": "Retail tenancy"}]}]}
    check("PDF and AI sightings of one room do not double count", len(assemble(duplicate_building, duplicate_vision)["material"]["hourly_load_model"]["rooms"]) == 1)
    reordered = deepcopy(building())
    reordered["spaces"].reverse()
    check("evidence reordering preserves preliminary fingerprint", direct["input_fingerprint"] == assemble(reordered)["input_fingerprint"])
    changed = deepcopy(building())
    changed["spaces"][0]["area"] = "43 m2"
    check("source changes create a new preliminary fingerprint", direct["input_fingerprint"] != assemble(changed)["input_fingerprint"])
    settings = {"saved_project_consent": True, "automatic_analysis_enabled": True, "maximum_provider_budget_aud": 5, "preliminary_pack_version": "au-preliminary-v2"}
    check("missing provider creates awaiting-provider state", provider_eligibility(settings, False) == "awaiting_provider")
    check("provider requires budget approval", provider_eligibility({**settings, "maximum_provider_budget_aud": None}, True) == "awaiting_budget")
    placeholder = [{"kind": "room", "label": "Dining area", "level_name": "Level 1", "area_m2": 100,
                    "preliminary_profile_id": "hospitality", "confidence": 0.6, "page": 8,
                    "excerpt": "Dining tables", "rationale": "Placeholder AI interpreted the plan."}]
    placeholder_model = assemble({"spaces": []}, manual_placeholder_entities=placeholder)
    check("source-linked manual placeholder rooms enter only the preliminary model", placeholder_model["material"]["hourly_load_model"]["rooms"][0]["name"] == "Dining area")
    completed = assemble({"spaces": [{"name": "Dining area", "level_name": "Level 1", "area": "", "evidence": [{"page": 8}]}]}, manual_placeholder_entities=placeholder)
    check("manual placeholder geometry completes a matching PDF label without duplication", len(completed["material"]["hourly_load_model"]["rooms"]) == 1 and completed["material"]["hourly_load_model"]["rooms"][0]["area_m2"] == 100)
    check("manual placeholder evidence requires a room source page", raises_value_error(lambda: validate_manual_placeholder_entities([{**placeholder[0], "page": ""}])) )
    proposal = {
        "rooms": [placeholder[0]],
        "surfaces": [{"surface_key": "dining-west-wall", "label": "Dining west external wall", "owner_room_label": "Dining area", "owner_level_name": "Level 1",
                      "physical_type": "wall", "thermal_role": "external", "external_exposure": "external", "orientation": "W", "gross_area_m2": 30,
                      "opening_coverage": "complete", "page": 8, "confidence": 0.8}],
        "openings": [{"opening_key": "dining-west-shopfront", "label": "Dining west shopfront", "owner_room_label": "Dining area", "owner_level_name": "Level 1",
                      "host_surface_key": "dining-west-wall", "external_exposure": "external", "orientation": "W", "opening_area_m2": 8,
                      "shading_category": "partial", "page": 8, "confidence": 0.7}],
    }
    preliminary_envelope = assemble({"spaces": []}, preliminary_proposal=proposal)
    prelim_room = preliminary_envelope["material"]["hourly_load_model"]["rooms"][0]
    check("proposal external wall and glazing materialize only in the preliminary model", len(prelim_room["cooling_load"]["envelope_surfaces"]) == 1 and len(prelim_room["cooling_load"]["glazing_surfaces"]) == 1)
    check("complete opening coverage subtracts opening area once", prelim_room["cooling_load"]["envelope_surfaces"][0]["area_m2"] == 22)
    envelope_report = calculate(preliminary_envelope)
    peak_components = envelope_report["included_scope_peak"]["components"]
    check("preliminary report keeps opaque and glazing solar components separate", "envelope" in peak_components and "glazing_conduction" in peak_components and "glazing_solar" in peak_components)
    refrigeration = assemble({"spaces": [{"name": "Cool Room", "level_name": "Level 1", "area": 10, "evidence": [{"page": 2}]}]})
    check("cool rooms are refrigeration exceptions rather than comfort-HVAC rooms", not refrigeration["material"]["hourly_load_model"]["rooms"] and refrigeration["excluded_spaces"][0]["scope"] == "refrigeration_process")
    no_orientation = deepcopy(proposal)
    no_orientation["openings"][0]["orientation"] = ""
    no_orientation["surfaces"][0]["orientation"] = ""
    no_orientation_model = assemble({"spaces": []}, preliminary_proposal=no_orientation)
    no_orientation_report = calculate(no_orientation_model)
    no_orientation_components = no_orientation_report["included_scope_peak"]["components"]
    check("unknown façade orientation keeps glazing conduction but excludes solar", "glazing_conduction" in no_orientation_components and "glazing_solar" not in no_orientation_components)
    invalid = validate_placeholder_proposal({"rooms": [placeholder[0]], "surfaces": [{"label": "Unowned wall", "gross_area_m2": 10}]})
    check("malformed surface remains a proposed exclusion instead of crashing assembly", invalid["surfaces"][0]["validation_errors"])


def raises_value_error(action):
    try:
        action()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    main()
