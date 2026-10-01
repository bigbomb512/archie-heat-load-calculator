#!/usr/bin/env python3
"""Shared room-proposal, source-fingerprint and override-retention checks."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, airflow_resolution, ceiling_volume_resolution, internal_gains_resolution
from backend import (
    ai_preliminary_service, airflow_resolution_service,
    ceiling_volume_resolution_service, internal_gains_resolution_service,
    model_input_resolution_service, room_use_resolution_service,
)
from ai.pipeline_evaluation import evaluate_project_pipeline


class Web:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def main():
    with TemporaryDirectory() as directory:
        root, web = Path(directory), Web()
        project = {"id": "shared-room-inputs", "review_dir": str(root)}
        local_rooms = [
            {"kind": "room", "room_id": "local:kitchen", "label": "Kitchen", "level_name": "Ground",
             "area_m2": 28, "source_pages": [1]},
            {"kind": "room", "room_id": "local:dining", "label": "Dining", "level_name": "Ground",
             "area_m2": 54, "source_pages": [1]},
        ]
        write(root / "building_evidence.json", {"spaces": []})
        write(root / "vision_response.json", {"result": {"auto_extraction": {"entities": []}}})
        write(root / "ai_preliminary_run.json", {
            "local_room_inference_proposal": {"rooms": local_rooms},
            "manual_placeholder_proposal": None,
        })
        paths = ai_preliminary_service._paths(project)

        def pipeline_resolve():
            ai_preliminary_service._resolve_room_uses(paths, persist=True)
            ai_preliminary_service._resolve_ceiling_volumes(paths, persist=True)
            ai_preliminary_service._resolve_internal_gains(paths, persist=True)
            ai_preliminary_service._resolve_airflow(paths, persist=True)

        pipeline_resolve()
        for filename in ("room_use_resolution.json", "ceiling_volume_resolution.json",
                         "internal_gains_resolution.json", "airflow_resolution.json"):
            rows = json.loads((root / filename).read_text(encoding="utf-8"))["records"]
            owners = ({row.get("owner_room_id") for row in rows} if filename == "airflow_resolution.json"
                      else {row.get("room_id") for row in rows})
            check(f"pipeline creates both local-inference rooms in {filename}", len(owners) == 2)

        current = evaluate_project_pipeline(root)["stages"]
        for stage in ("room_use_resolution", "ceiling_volume_resolution", "internal_gains_resolution", "airflow_resolution"):
            check(f"scorecard reports freshly built {stage} current", current[stage]["freshness"] == "current")
        for get, key in (
            (room_use_resolution_service.get, "room_use_resolution"),
            (ceiling_volume_resolution_service.get, "ceiling_volume_resolution"),
            (internal_gains_resolution_service.get, "internal_gains_resolution"),
            (airflow_resolution_service.get, "airflow_resolution"),
        ):
            response = get(web, project)
            check(f"editor GET reports current for {key}", response["status"] != "stale" and not response.get("stale_reasons"))

        ceiling_before = ceiling_volume_resolution_service.get(web, project)["ceiling_volume_resolution"]
        ceiling_ids = {row["room_id"] for row in ceiling_before["records"]}
        ceiling_after = ceiling_volume_resolution_service.post(web, project, {"action": "resolve"})["ceiling_volume_resolution"]
        check("ceiling editor re-resolve retains pipeline room IDs", {row["room_id"] for row in ceiling_after["records"]} == ceiling_ids)
        ceiling_room = next(row for row in ceiling_after["records"] if row["original_label"] == "Kitchen")
        ceiling_volume_resolution_service.post(web, project, {
            "action": "apply_override", "room_id": ceiling_room["room_id"], "ceiling_height_mm": 3100,
            "reviewer": "Engineer", "note": "Site measure",
        })
        check("stale retained ceiling override is excluded from calculation mapping",
              ceiling_room["room_id"] not in ceiling_volume_resolution.values_by_room(
                  {"records": [{**ceiling_room, "status": "stale"}]}
              ))
        internal_before = internal_gains_resolution_service.get(web, project)["internal_gains_resolution"]
        internal_ids = {row["room_id"] for row in internal_before["records"]}
        internal_after = internal_gains_resolution_service.post(web, project, {"action": "resolve"})["internal_gains_resolution"]
        check("internal-gains editor re-resolve retains pipeline room IDs", {row["room_id"] for row in internal_after["records"]} == internal_ids)
        internal_room = next(row for row in internal_after["records"] if row["original_label"] == "Dining")
        internal_gains_resolution_service.post(web, project, {
            "action": "apply_override", "room_id": internal_room["room_id"], "field": "occupancy_count",
            "value": 42, "reviewer": "Engineer",
        })
        airflow_room_id = airflow_resolution.room_id("Kitchen", "Ground")
        airflow_resolution_service.post(web, project, {"action": "resolve"})
        airflow_resolution_service.post(web, project, {
            "action": "apply_override", "room_id": airflow_room_id, "air_path_type": "outside_air",
            "value": 55, "reviewer": "Engineer",
        })
        airflow_resolution_service.post(web, project, {
            "action": "apply_override", "room_id": airflow_room_id, "air_path_type": "infiltration",
            "value": 7, "reviewer": "Engineer",
        })

        airflow_path = root / "airflow_resolution.json"
        airflow_artifact = json.loads(airflow_path.read_text(encoding="utf-8"))
        stale_path = next(row for row in airflow_artifact["records"]
                          if row.get("owner_room_id") == airflow_room_id and row.get("air_path_type") == "outside_air")
        stale_path["status"] = "stale"
        write(airflow_path, airflow_artifact)
        try:
            airflow_resolution_service.post(web, project, {
                "action": "apply_override", "room_id": airflow_room_id, "air_path_type": "outside_air",
                "value": 65, "reviewer": "Engineer",
            })
            stale_path_edit_rejected = False
        except ValueError as error:
            stale_path_edit_rejected = "stale" in str(error).casefold()
        check("retained stale airflow path cannot be edited when source fingerprints are otherwise current",
              stale_path_edit_rejected)
        current_room_use = json.loads((root / "room_use_resolution.json").read_text(encoding="utf-8"))
        current_ceiling = json.loads((root / "ceiling_volume_resolution.json").read_text(encoding="utf-8"))
        current_internal = json.loads((root / "internal_gains_resolution.json").read_text(encoding="utf-8"))
        current_geometry = ai_preliminary_service._preliminary_geometry(paths)
        current_proposal = ai_preliminary_service._prepare_preliminary_proposal(
            paths, json.loads((root / "ai_preliminary_run.json").read_text(encoding="utf-8"))["local_room_inference_proposal"],
            current_room_use, current_geometry,
        )
        stale_input_set = ai_preliminary.assemble(
            json.loads((root / "building_evidence.json").read_text(encoding="utf-8")),
            preliminary_proposal=current_proposal,
            room_use_resolution=current_room_use,
            ceiling_volume_resolution=current_ceiling,
            internal_gains_resolution=current_internal,
            airflow_resolution=airflow_artifact,
            allow_area_fallbacks=False,
        )
        stale_airflow_value = next(row for row in stale_input_set["value_resolution"]["records"]
                                   if str(row.get("target", "")).startswith("airflow.")
                                   and row.get("target_id") == airflow_room_id
                                   and row.get("field") == "outside_air_lps")
        check("stale outside-air override is excluded from the existing room's preliminary airflow mapping",
              stale_airflow_value["status"] == "excluded"
              and next(row for row in stale_input_set["airflow_resolution"]["records"]
                       if row.get("owner_room_id") == airflow_room_id
                       and row.get("air_path_type") == "outside_air")["status"] == "stale")
        airflow_resolution_service.post(web, project, {"action": "resolve"})

        building = json.loads((root / "building_evidence.json").read_text(encoding="utf-8"))
        building["review_note"] = "Evidence updated"
        write(root / "building_evidence.json", building)
        stale = internal_gains_resolution_service.get(web, project)
        check("internal-gains GET reports stale after evidence changes", stale["status"] == "stale" and stale["stale_reasons"])
        try:
            internal_gains_resolution_service.post(web, project, {
                "action": "apply_override", "room_id": internal_room["room_id"], "field": "occupancy_count",
                "value": 50, "reviewer": "Engineer",
            })
            rejected = False
        except ValueError as error:
            rejected = "stale" in str(error).casefold()
        check("internal-gains override is rejected while source evidence is stale", rejected)

        pipeline_resolve()
        check("re-resolving current sources allows internal-gains override", internal_gains_resolution_service.get(web, project)["status"] != "stale")
        proposal = json.loads((root / "ai_preliminary_run.json").read_text(encoding="utf-8"))
        proposal["local_room_inference_proposal"]["rooms"] = []
        proposal["manual_placeholder_proposal"] = None
        write(root / "ai_preliminary_run.json", proposal)
        write(root / "building_evidence.json", {"spaces": []})
        pipeline_resolve()
        retained_ceiling = ceiling_volume_resolution_service.get(web, project)["ceiling_volume_resolution"]["records"]
        retained_internal = internal_gains_resolution_service.get(web, project)["internal_gains_resolution"]["records"]
        retained_airflow = airflow_resolution_service.get(web, project)["airflow_resolution"]["records"]
        check("removed ceiling room with override is retained stale", any(row["room_id"] == ceiling_room["room_id"] and row["status"] == "stale" and row.get("override") for row in retained_ceiling))
        check("removed internal-gains room with override is retained stale", any(row["room_id"] == internal_room["room_id"] and row["status"] == "stale" and row.get("override") for row in retained_internal))
        check("removed airflow room retains both overrides as stale records",
              {(row["air_path_type"], row["value"]) for row in retained_airflow
               if row.get("owner_room_id") == airflow_room_id and row.get("status") == "stale" and row.get("overrides")}
              == {("outside_air", 55), ("infiltration", 7)})

        register = model_input_resolution_service.post(web, project, {"action": "resolve"})
        airflow_reviews = [row for row in register["review_queue"]
                           if str(row.get("target", "")).startswith("airflow.") and row.get("status") == "stale"]
        check("retained airflow overrides remain model-input review items",
              {row.get("field") for row in airflow_reviews if row.get("target_id")} >= {"outside_air_lps", "infiltration"})
        stale_room_reviews = {(row.get("target"), row.get("target_id")) for row in register["review_queue"]
                              if row.get("status") == "stale"}
        check("retained ceiling and internal-gains records remain review items",
              ("room.ceiling_height_mm", ceiling_room["room_id"]) in stale_room_reviews
              and any(target_id == internal_room["room_id"] and target.startswith("room.")
                      for target, target_id in stale_room_reviews))
        input_set = ai_preliminary_service._assemble(web, project)
        room_rows = input_set.get("material", {}).get("hourly_load_model", {}).get("rooms", [])
        check("removed room does not reappear as a preliminary calculation room",
              not any(str(row.get("name", row.get("original_label", ""))).casefold() == "kitchen"
                      for row in room_rows if isinstance(row, dict)))
        stale_airflow_values = [row for row in input_set.get("value_resolution", {}).get("records", [])
                                if str(row.get("target", "")).startswith("airflow.")
                                and row.get("target_id") == airflow_room_id]
        check("stale airflow values are excluded from preliminary calculation inputs",
              len(stale_airflow_values) >= 2 and all(row.get("status") == "excluded" for row in stale_airflow_values))
        check("stale airflow overrides remain visible in the assembled draft but are non-calculating",
              all(row.get("status") == "stale" for row in input_set["airflow_resolution"]["records"]
                  if row.get("owner_room_id") == airflow_room_id))


if __name__ == "__main__":
    main()
