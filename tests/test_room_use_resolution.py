#!/usr/bin/env python3
"""Focused checks for draft-only controlled room-use resolution."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, room_use_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def building():
    return {"spaces": [
        {"name": "Dining area", "level_name": "Level 1", "area": 80, "evidence": [{"page": 2, "excerpt": "DINING AREA"}]},
        {"name": "Kitchen", "level_name": "Level 1", "area": 20, "evidence": [{"page": 2, "excerpt": "KITCHEN"}]},
        {"name": "Cool Room", "level_name": "Level 1", "area": 10, "evidence": [{"page": 2, "excerpt": "COOL ROOM"}]},
        {"name": "Plant Room", "level_name": "Level 1", "area": 8, "evidence": [{"page": 2, "excerpt": "PLANT ROOM"}]},
        {"name": "Service Counter", "level_name": "Level 1", "area": 13, "evidence": [{"page": 2, "excerpt": "SERVICE COUNTER"}]},
        {"name": "Room 14", "level_name": "Level 1", "area": 12, "evidence": [{"page": 2, "excerpt": "ROOM 14"}]},
    ]}


def record(artifact, label):
    return next(row for row in artifact["records"] if row["original_label"] == label)


def main():
    source = {"building_evidence": "building-a", "vision_response": "vision-a", "ai_preliminary_proposal": "proposal-a"}
    artifact = room_use_resolution.resolve(building(), source_fingerprints=source)
    dining, kitchen = record(artifact, "Dining area"), record(artifact, "Kitchen")
    check("explicit dining label selects controlled hospitality profile", dining["taxonomy_id"] == "dining" and dining["preliminary_profile_id"] == "hospitality")
    check("kitchens retain HVAC profile and process exception scope", kitchen["preliminary_profile_id"] == "hospitality" and kitchen["space_scope"] == "comfort_hvac_with_process_exception")
    check("cool rooms are classified as excluded refrigeration scope", record(artifact, "Cool Room")["status"] == "excluded" and record(artifact, "Cool Room")["space_scope"] == "refrigeration_process")
    check("plant rooms remain excluded from comfort-HVAC scope", record(artifact, "Plant Room")["space_scope"] == "unresolved_scope")
    service_counter = record(artifact, "Service Counter")
    check("service counters map to retail comfort HVAC", service_counter["taxonomy_id"] == "retail" and service_counter["space_scope"] == "comfort_hvac")
    kitchen_counter = room_use_resolution.resolve({"spaces": [{"name": "Kitchen Counter", "level_name": "Level 1", "area": 4,
        "evidence": [{"page": 2, "excerpt": "KITCHEN COUNTER"}]}]}, source_fingerprints=source)
    check("explicit kitchen label outranks the counter shortcut", record(kitchen_counter, "Kitchen Counter")["taxonomy_id"] == "kitchen")
    generic = record(artifact, "Room 14")
    check("unresolved conditioned rooms use visible low-confidence generic fallback", generic["taxonomy_id"] == "generic_conditioned" and generic["status"] == "needs_review")
    check("room use records preserve cited evidence and stable room identity", dining["evidence"][0]["page"] == 2 and dining["room_id"] == room_use_resolution.room_identity("Dining area", "Level 1"))

    vision = {"result": {"auto_extraction": {"entities": [{"kind": "room", "label": "Studio", "level_name": "Level 2", "page": 5,
        "room_use_category": "office", "room_use_rationale": "Desks and meeting table shown.", "room_use_alternatives": ["retail"], "confidence": "high"}]}}}
    interpreted = room_use_resolution.resolve({"spaces": [{"name": "Studio", "level_name": "Level 2", "area": 40, "evidence": [{"page": 5, "excerpt": "Studio"}]}]}, vision, source_fingerprints=source)
    studio = record(interpreted, "Studio")
    check("AI visual classification selects only a controlled profile", studio["classification_origin"] == "ai_interpretation" and studio["preliminary_profile_id"] == "office")

    # A legacy label is not direct evidence when the runtime skill explicitly
    # reports that it is absent from the plan. The cited AI category should
    # win, while a functional subarea without its own boundary is excluded
    # from the room subtotal to prevent overlapping loads.
    skill_context = {"kind": "room", "label": "Shop", "level_name": "Unassigned level", "page": 2,
        "room_use_category": "dining", "confidence": .9, "direct_label_verified": False,
        "calculation_scope_override": "unresolved_scope", "non_counting_functional_subarea": True,
        "evidence": [{"page": 2, "excerpt": "Tables and buffet identify customer hospitality area."}]}
    skill_use = room_use_resolution.resolve({}, proposal={"rooms": [skill_context]}, source_fingerprints=source)
    shop = record(skill_use, "Shop")
    check("runtime skill can override an unverified legacy label", shop["taxonomy_id"] == "dining" and shop["classification_origin"] == "ai_interpretation")
    check("functional subarea without a boundary is not double-counted", shop["space_scope"] == "unresolved_scope" and shop["status"] == "excluded" and shop.get("non_counting_functional_subarea"))

    changed = deepcopy(interpreted)
    changed["records"][0]["override"] = {"taxonomy_id": "retail", "reviewer": "Engineer", "note": "Confirmed showroom", "updated_at": "2026-09-23T00:00:00+00:00"}
    overridden = room_use_resolution.resolve({"spaces": [{"name": "Studio", "level_name": "Level 2", "area": 40, "evidence": [{"page": 5, "excerpt": "Studio"}]}]}, vision, source_fingerprints=source, existing=changed)
    check("contractor override takes precedence over AI classification", overridden["records"][0]["classification_origin"] == "contractor_override" and overridden["records"][0]["preliminary_profile_id"] == "retail")
    check("source changes make a prior classification stale", not room_use_resolution.is_current(overridden, {**source, "vision_response": "vision-b"}))

    assembled = ai_preliminary.assemble(building(), room_use_resolution=artifact, source_fingerprints={"room_use_resolution": artifact["fingerprint"]})
    names = {room["name"] for room in assembled["material"]["hourly_load_model"]["rooms"]}
    check("preliminary assembly includes comfort rooms but excludes refrigeration and plant scope", {"Dining area", "Kitchen", "Room 14"} <= names and "Cool Room" not in names and "Plant Room" not in names)
    check("snapshot fingerprints the separate room-use resolution", assembled["dependency_fingerprints"]["room_use_resolution"] == artifact["fingerprint"])


if __name__ == "__main__":
    main()
