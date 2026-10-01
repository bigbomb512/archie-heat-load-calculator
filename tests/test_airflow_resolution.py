#!/usr/bin/env python3
"""Focused checks for draft ventilation, infiltration, and process-air resolution."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import airflow_resolution, ai_preliminary, ceiling_volume_resolution, room_use_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    pack = ai_preliminary.load_pack()
    building = {"spaces": [
        {"name": "Dining", "level_name": "Ground", "area_m2": 100, "occupancy_count": 20,
         "evidence": [{"page": 2, "excerpt": "DINING 20 seats"}]},
        {"name": "Kitchen", "level_name": "Ground", "area_m2": 40, "occupancy_count": 8,
         "process_exhaust_lps": 500, "evidence": [{"page": 4, "excerpt": "KITCHEN HOOD EXHAUST 500 L/s"}]},
    ]}
    proposal = {"rooms": [
        {"kind": "room", "label": "Dining", "level_name": "Ground", "area_m2": 100,
         "preliminary_profile_id": "hospitality", "page": 2},
        {"kind": "room", "label": "Kitchen", "level_name": "Ground", "area_m2": 40,
         "preliminary_profile_id": "hospitality", "space_scope": "comfort_hvac_with_process_exception", "page": 4},
    ]}
    room_use = room_use_resolution.resolve(building, proposal=proposal, source_fingerprints={})
    ceiling = ceiling_volume_resolution.resolve(
        building, proposal=proposal, assumption_pack=pack, source_fingerprints={},
    )
    artifact = airflow_resolution.resolve(building, {}, proposal, room_use, ceiling, pack, {"proposal": "test"})
    dining = airflow_resolution.room_id("Dining", "Ground")
    kitchen = airflow_resolution.room_id("Kitchen", "Ground")
    outside = next(row for row in artifact["records"] if row["owner_room_id"] == dining and row["air_path_type"] == "outside_air")
    infiltration = next(row for row in artifact["records"] if row["owner_room_id"] == dining and row["air_path_type"] == "infiltration")
    check("people/area ventilation is materialized with formula operands", outside["value"] > 0 and outside["formula"].startswith("max(") and outside["operands"]["occupancy"] == 20)
    check("controlled ACH fallback is explicit and uses resolved room volume", infiltration["origin"] == "controlled_fallback" and infiltration["unit"] == "ACH" and infiltration["operands"]["room_volume_m3"] > 0 and infiltration["operands"]["resolved_flow_lps"] > 0)
    process = next(row for row in artifact["records"] if row["owner_room_id"] == kitchen and row["air_path_type"] == "process_exhaust")
    make_up = next(row for row in artifact["records"] if row["owner_room_id"] == kitchen and row["air_path_type"] == "make_up_air")
    check("kitchen exhaust remains separate from comfort ventilation", process["value"] == 500 and process["status"] == "provisional" and make_up["value"] == 500)

    # A numeric AI proposal without evidence is not accepted as a physical
    # airflow value; it remains an explicit unresolved candidate.
    uncited = {"rooms": [{"label": "Dining", "level_name": "Ground"}],
               "airflows": [{"room_id": dining, "air_path_type": "outside_air", "value": 999}]}
    rejected = airflow_resolution.resolve(building, {}, uncited, room_use, ceiling, pack, {})
    candidate = next(row for row in rejected["records"] if row["air_path_type"] == "outside_air" and row["source"] == "AI airflow evidence")
    check("uncited AI numeric airflow remains excluded", candidate["value"] is None and candidate["status"] == "excluded")

    overridden = airflow_resolution.apply_override(artifact, dining, "outside_air", 42, "Engineer", "Confirmed schedule")
    rebuilt = airflow_resolution.resolve(building, {}, proposal, room_use, ceiling, pack, {"proposal": "test"}, overridden)
    override_row = next(row for row in rebuilt["records"] if row["owner_room_id"] == dining and row["air_path_type"] == "outside_air")
    check("contractor airflow override survives rebuild", override_row["value"] == 42 and override_row["origin"] == "contractor_override")

    conflict_proposal = {"rooms": proposal["rooms"], "airflows": [
        {"room_id": dining, "air_path_type": "outside_air", "value": 40, "ahu_id": "ahu-a", "tag": "OA-1", "source": "mechanical schedule", "evidence": [{"page": 6, "excerpt": "OA-1"}]},
        {"room_id": dining, "air_path_type": "outside_air", "value": 45, "ahu_id": "ahu-b", "tag": "OA-1", "source": "mechanical schedule", "evidence": [{"page": 7, "excerpt": "OA-1"}]},
    ]}
    conflicts = airflow_resolution.resolve(building, {}, conflict_proposal, room_use, ceiling, pack, {})
    conflict_rows = [row for row in conflicts["records"] if row.get("source_id") == "OA-1"]
    check("competing AHU ownership remains visible", conflict_rows and all(row["status"] == "blocked" for row in conflict_rows))

    same_owner_proposal = {"rooms": proposal["rooms"], "airflows": [
        {"room_id": dining, "air_path_type": "outside_air", "value": 40, "ahu_id": "ahu-a", "tag": "OA-2", "source": "mechanical schedule", "evidence": [{"page": 6, "excerpt": "OA-2"}]},
        {"room_id": dining, "air_path_type": "outside_air", "value": 45, "ahu_id": "ahu-a", "tag": "OA-2", "source": "mechanical schedule", "evidence": [{"page": 6, "excerpt": "OA-2"}]},
    ]}
    same_owner_conflicts = airflow_resolution.resolve(building, {}, same_owner_proposal, room_use, ceiling, pack, {})
    same_owner_rows = [row for row in same_owner_conflicts["records"] if row.get("source_id") == "OA-2"]
    check("same physical path with conflicting flow values is blocked even when ownership matches",
          len(same_owner_rows) == 2 and all(row["status"] == "blocked" and any("disagree on value" in reason for reason in row["unresolved_fields"]) for row in same_owner_rows))
    conflict_assembly = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=room_use,
                                               ceiling_volume_resolution=ceiling, airflow_resolution=same_owner_conflicts,
                                               source_fingerprints={"airflow_resolution": same_owner_conflicts["fingerprint"]})
    conflict_room = next(row for row in conflict_assembly["material"]["hourly_load_model"]["rooms"] if row["name"] == "Dining")
    check("conflicting path values do not win over the labelled preliminary fallback",
          conflict_room["cooling_load"]["outside_air_lps"] == round(20 * pack["profiles"]["hospitality"]["outside_air_lps_person"] + 100 * pack["profiles"]["hospitality"]["outside_air_lps_m2"], 3)
          and conflict_room["verification_status"] == "provisional")

    assembled = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=room_use,
                                        ceiling_volume_resolution=ceiling, airflow_resolution=artifact,
                                        source_fingerprints={"airflow_resolution": artifact["fingerprint"]})
    dining_model = next(row for row in assembled["material"]["hourly_load_model"]["rooms"] if row["name"] == "Dining")
    check("preliminary model receives a dedicated infiltration schedule", dining_model["schedule_assignments"]["infiltration"] != dining_model["schedule_assignments"]["outside_air"])
    report = ai_preliminary.calculate(assembled)
    room_report = next(row for row in report["scenario_results"][0]["rooms"] if row["name"] == "Dining")
    check("existing hourly engine reports draft outside-air and infiltration loads", room_report["status"] == "draft" and room_report["hours"][14]["components"]["outside_air"]["total_kw"] > 0 and room_report["hours"][14]["components"]["infiltration"]["total_kw"] > 0)


if __name__ == "__main__":
    main()
