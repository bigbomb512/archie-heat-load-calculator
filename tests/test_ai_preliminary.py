"""Focused checks for the isolated, draft-only AI preliminary assembler."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.ai_preliminary import (
    assemble, build_local_codex_handoff, calculate, empty_local_codex_response,
    provider_eligibility, validate_manual_placeholder_entities, validate_placeholder_proposal,
)
from ai import safety_factor_resolution


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
    geometry_resolution = {"entities": [{
        "kind": "area", "entity_id": "geometry-proof-retail", "label": "Retail tenancy", "level_candidate": "Level 1",
        "geometry_status": "ai_estimated", "source": {"page": 3, "drawing_number": "A-101"},
        "value": {"area_m2": 50.0, "geometry_proof_id": "geometry-proof-retail", "derivation": {
            "formula": "shoelace_area_px2 × (mm_per_px²) ÷ 1,000,000", "operands": {"polygon_area_px2": 5000, "mm_per_px": 100},
        }},
    }]}
    geometry_model = assemble(building(), geometry_resolution=geometry_resolution)
    geometry_room = next(row for row in geometry_model["material"]["hourly_load_model"]["rooms"] if row["name"] == "Retail tenancy")
    geometry_field = next(row for row in geometry_model["materialized_fields"] if row["room_id"] == geometry_room["room_id"] and row["field"] == "area_m2")
    check("normalized AI geometry proof supplies a draft-only room area", geometry_room["area_m2"] == 50.0 and geometry_field["origin"] == "ai_geometry" and geometry_field["geometry_proof_id"] == "geometry-proof-retail")
    check("generic profile fallback remains available", any(row["profile_id"] == "generic_conditioned_room" for row in direct["materialized_fields"]))
    check("missing geometry falls back only through a visible controlled assumption", any(row["origin"] == "ai_assumption" for row in direct["materialized_fields"]))
    measured_only = assemble({"spaces": [
        {"name": "Retail tenancy", "level_name": "Level 1", "area": "42 m2", "evidence": [{"page": 3}]},
        {"name": "Office", "level_name": "Level 2", "area": "", "evidence": [{"page": 5}]},
    ]}, allow_area_fallbacks=False)
    measured_rooms = measured_only["material"]["hourly_load_model"]["rooms"]
    check("consolidated policy can refuse generic room-area assumptions", [room["name"] for room in measured_rooms] == ["Retail tenancy"])
    check("rooms without area remain explicit exclusions when fallback is disabled", any(row.get("room_name") == "Office" and "area" in row.get("reason", "") for row in measured_only["excluded_spaces"]))
    check("low-confidence estimates remain in the preliminary input set", any(row["confidence_band"] == "low" for row in direct["review_queue"]))
    report = calculate(direct)
    check("preliminary report uses the hourly engine and stays draft", report["status"] == "draft" and report["included_scope_peak"] and not report["project_peak"])
    fallback_policy = safety_factor_resolution.policy_for(safety_factor_resolution.resolve({}), "cooling", preliminary=True)
    fallback_report = calculate(direct, safety_factor_policy=fallback_policy)
    fallback_peak = fallback_report["included_scope_peak"]
    fallback_hours = [hour for scenario in fallback_report["scenario_results"] for room in scenario["rooms"] for hour in room["hours"]]
    check("preliminary fallback applies once, neutralizes room factors, and remains draft", fallback_report["status"] == "draft" and fallback_report["safety_policy_applied"] and fallback_report["safety_factor"] == 1.1 and fallback_peak["final_design_total_kw"] == round(fallback_peak["raw_coincident_total_kw"] * 1.1, 4) and all(hour["safety_factor"] == 1.0 and hour["safety_allowance_kw"] == 0 for hour in fallback_hours))
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
    settings = {"saved_project_consent": True, "automatic_analysis_enabled": True, "preliminary_pack_version": "au-preliminary-v2"}
    check("missing provider creates awaiting-provider state", provider_eligibility(settings, False) == "awaiting_provider")
    check("consented provider is eligible without a budget gate", provider_eligibility(settings, True) == "eligible")
    handoff = build_local_codex_handoff(
        {"id": "project-1", "name": "Local fixture"},
        {**building(), "source_pdf": "/private/fixture.pdf", "api_key": "must-not-leak"},
        {"result": {"auto_extraction": {"entities": []}, "token": "must-not-leak"}},
        {"source_pdf": "/private/fusion.pdf"},
        {"building_evidence": "fixture-fingerprint"},
    )
    check("local Codex handoff keeps source-linked building evidence", handoff["mode"] == "local_codex_placeholder" and handoff["evidence"]["building"]["spaces"])
    check("local Codex handoff excludes paths and credentials", "source_pdf" not in handoff["evidence"]["building"] and "api_key" not in handoff["evidence"]["building"] and "token" not in handoff["evidence"]["vision"])
    response_template = empty_local_codex_response(handoff)
    check("local Codex response template is bound to its handoff", response_template["handoff_fingerprint"] == handoff["handoff_fingerprint"] and response_template["proposal"] == {"rooms": [], "surfaces": [], "openings": [], "issues": []})
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
    geometry_only = {
        "rooms": [{"kind": "room", "label": "Geometry shop", "level_name": "Ground", "preliminary_profile_id": "retail",
                   "confidence": "high", "page": 4, "source_pages": [4], "evidence": [{"page": 4, "excerpt": "Geometry shop"}],
                   "geometry": {"boundary_points_px": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]], "scale_mm_per_px": 100}}]
    }
    geometry_only_resolution = {"entities": [{"kind": "area", "entity_id": "geometry-proof-shop", "label": "Geometry shop",
        "level_candidate": "Ground", "geometry_status": "ai_estimated", "source": {"page": 4},
        "value": {"area_m2": 50.0, "geometry_proof_id": "geometry-proof-shop", "derivation": {"formula": "shoelace", "operands": {"polygon_area_px2": 5000, "mm_per_px": 100}}}}]}
    geometry_only_model = assemble({"spaces": []}, preliminary_proposal=geometry_only, geometry_resolution=geometry_only_resolution)
    geometry_only_room = geometry_only_model["material"]["hourly_load_model"]["rooms"][0]
    geometry_only_field = next(row for row in geometry_only_model["materialized_fields"] if row["field"] == "area_m2")
    check("structured room geometry may supply the draft area through the normalized proof", geometry_only_room["area_m2"] == 50.0 and geometry_only_field["origin"] == "ai_geometry")


def raises_value_error(action):
    try:
        action()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    main()
