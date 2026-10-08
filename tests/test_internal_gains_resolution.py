#!/usr/bin/env python3
"""Focused checks for evidence-backed draft internal gains and schedules."""

from copy import deepcopy
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, internal_gains_resolution, room_use_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    pack = ai_preliminary.load_pack()
    building = {"spaces": [
        {"name": "Cafe", "level_name": "Ground", "area_m2": 100, "evidence": [{"page": 2, "excerpt": "CAFE"}]},
        {"name": "Cool Room", "level_name": "Ground", "area_m2": 20, "evidence": [{"page": 2, "excerpt": "COOL ROOM"}]},
        {"name": "Unresolved", "level_name": "Ground", "area_m2": 50, "evidence": [{"page": 2, "excerpt": "UNRESOLVED"}]},
    ]}
    proposal = {"rooms": [
        {"kind": "room", "label": "Cafe", "level_name": "Ground", "area_m2": 100, "preliminary_profile_id": "hospitality", "page": 2,
         "seat_count": 20, "lighting_fixtures": [{"quantity": 10, "wattage_w": 20, "evidence": [{"page": 4, "excerpt": "L-01 20W"}]}],
         "equipment": [{"name": "Oven", "quantity": 2, "rated_input_w": 3000, "heat_to_space_factor": .4, "evidence": [{"page": 5, "excerpt": "Oven schedule"}]}],
         "schedules": {"weekday": [1.0] * 24, "saturday": [0.5] * 24, "sunday": [0.0] * 24, "holiday": [0.0] * 24}},
        {"kind": "room", "label": "Cool Room", "level_name": "Ground", "area_m2": 20, "preliminary_profile_id": "storage", "space_scope": "refrigeration_process", "page": 2},
        {"kind": "room", "label": "Unresolved", "level_name": "Ground", "area_m2": 50, "preliminary_profile_id": "generic_conditioned_room", "page": 2},
    ]}
    use = room_use_resolution.resolve(building, proposal=proposal, source_fingerprints={})
    artifact = internal_gains_resolution.resolve(building, proposal=proposal, room_use=use, pack=pack,
                                                 source_fingerprints={"proposal": "a"})
    cafe = next(row for row in artifact["records"] if row["original_label"] == "Cafe")
    check("counted seats outrank density fallback", cafe["occupancy_count"] == 20 and cafe["fields"]["occupancy_count"]["origin"] == "direct_project_evidence")
    check("cited fixture quantity and wattage resolve lighting", cafe["lighting_load_w"] == 200)
    check("cited equipment retains heat-to-space and diversity operands", cafe["equipment"][0]["rated_input_w"] == 3000 and cafe["equipment"][0]["heat_to_space_factor"] == .4)
    check("all schedule day types contain bounded 24-hour profiles", all(len(cafe["schedule_profiles"][day]) == 24 for day in internal_gains_resolution.DAY_TYPES))
    check("explicit operating schedules outrank the room-use default", cafe["schedule_profiles"]["saturday"] == [.5] * 24 and cafe["schedule_profiles"]["sunday"] == [0.0] * 24)
    cool = next(row for row in artifact["records"] if row["original_label"] == "Cool Room")
    check("refrigeration rooms remain outside comfort internal gains", cool["status"] == "excluded")
    unresolved = next(row for row in artifact["records"] if row["original_label"] == "Unresolved")
    check("profile fallback remains visible as provisional", unresolved["status"] == "provisional" and unresolved["fields"]["lighting_load_w"]["origin"] == "controlled_preliminary_profile")
    overridden = internal_gains_resolution.apply_override(artifact, cafe["room_id"], "occupancy_count", 24, "Engineer", "Counted plan seats")
    cafe_override = next(row for row in overridden["records"] if row["room_id"] == cafe["room_id"])
    check("contractor occupancy override takes precedence", cafe_override["fields"]["occupancy_count"]["value"] == 24 and cafe_override["fields"]["occupancy_count"]["origin"] == "contractor_override")
    # Overrides survive every rebuild (Calculate rebuilds internal gains), not only the moment they are entered.
    overridden = internal_gains_resolution.apply_override(overridden, cafe["room_id"], "lighting_load_w", 900, "Engineer", "Client lighting schedule")
    overridden = internal_gains_resolution.apply_override(overridden, cafe["room_id"], "people_diversity", .7, "Engineer")
    for _ in range(2):
        overridden = internal_gains_resolution.resolve(building, proposal=proposal, room_use=use, pack=pack,
                                                       source_fingerprints={"proposal": "a"}, existing=overridden)
    rebuilt = next(row for row in overridden["records"] if row["room_id"] == cafe["room_id"])
    check("overrides are re-applied on rebuild", rebuilt["occupancy_count"] == 24 and rebuilt["fields"]["occupancy_count"]["origin"] == "contractor_override"
          and rebuilt["lighting_load_w"] == 900 and rebuilt["fields"]["lighting_load_w"]["rationale"] == "Client lighting schedule")
    check("a people-diversity override is read from its saved record", rebuilt["fields"]["people_diversity"]["value"] == .7
          and rebuilt["fields"]["people_diversity"]["origin"] == "contractor_override")
    rebuilt_model = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=use, internal_gains_resolution=overridden,
                                            source_fingerprints={"internal": overridden["fingerprint"]})
    check("the calculation model uses the overridden people count", next(row for row in rebuilt_model["material"]["requirements"]["zones"]
                                                                          if row["name"] == "Cafe")["occupancy"] == 24)
    try:
        internal_gains_resolution.validate({**artifact, "schedules": [{"schedule_id": "bad", "day_profiles": {"weekday": [2]}}]})
    except ValueError:
        invalid_rejected = True
    else:
        invalid_rejected = False
    check("invalid schedule values are rejected", invalid_rejected)
    assembled = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=use,
                                         internal_gains_resolution=artifact, source_fingerprints={"internal": artifact["fingerprint"]})
    check("preliminary assembler fingerprints and consumes internal gains", assembled["dependency_fingerprints"]["internal_gains_resolution"] == artifact["fingerprint"] and assembled["internal_gains_resolution"]["records"])
    cafe_model = next(row for row in assembled["material"]["hourly_load_model"]["rooms"] if row["name"] == "Cafe")
    cafe_req = next(row for row in assembled["material"]["requirements"]["zones"] if row["name"] == "Cafe")
    check("room-to-zone assembly carries resolved occupancy and equipment", cafe_model["room_id"] and cafe_req["occupancy"] == 20 and cafe_req["heat_sources"][0]["watts"] == 3000)


if __name__ == "__main__":
    main()
