#!/usr/bin/env python3
"""Project-local API lifecycle checks for draft airflow resolution."""

import json
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import airflow_resolution_service


class Web:
    def safe_link(self, path):
        return f"/artifact/{Path(path).name}"

    def update_project(self, _project):
        return None


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project, web = {"id": "airflow-service", "review_dir": str(root)}, Web()
        write(root / "building_evidence.json", {"spaces": [{"name": "Office", "level_name": "Level 1", "area_m2": 40, "evidence": [{"page": 1}]}]})
        write(root / "ai_preliminary_run.json", {"manual_placeholder_proposal": {"rooms": [{"kind": "room", "label": "Office", "level_name": "Level 1", "area_m2": 40, "preliminary_profile_id": "office", "page": 1}]}})
        response = airflow_resolution_service.post(web, project, {"action": "resolve"})
        check("resolve writes airflow artifact", (root / "airflow_resolution.json").exists() and response["airflow_resolution"]["records"])
        room_id = response["airflow_resolution"]["records"][0]["owner_room_id"]
        response = airflow_resolution_service.post(web, project, {"action": "apply_override", "room_id": room_id, "air_path_type": "outside_air", "value": 25, "reviewer": "Engineer"})
        check("API airflow override is recorded", any(row.get("origin") == "contractor_override" and row.get("value") == 25 for row in response["airflow_resolution"]["records"]))
        response = airflow_resolution_service.post(web, project, {"action": "apply_override", "room_id": room_id, "air_path_type": "infiltration", "value": 7, "reviewer": "Engineer"})
        check("a second path override for the same room is recorded independently",
              {(row["air_path_type"], row["value"]) for row in response["airflow_resolution"]["records"]
               if row.get("owner_room_id") == room_id and row.get("origin") == "contractor_override"} == {("outside_air", 25), ("infiltration", 7)})
        rebuilt = airflow_resolution_service.post(web, project, {"action": "rebuild"})
        for attempt in range(2):
            overrides = {(row["air_path_type"], row["value"]) for row in rebuilt["airflow_resolution"]["records"]
                         if row.get("owner_room_id") == room_id and row.get("origin") == "contractor_override"}
            check(f"both path-specific overrides survive resolver rebuild {attempt + 1}",
                  overrides == {("outside_air", 25), ("infiltration", 7)})
            rebuilt = airflow_resolution_service.post(web, project, {"action": "rebuild"})
        research = airflow_resolution_service.post(web, project, {"action": "queue_source_research", "target": "airflow.infiltration", "reason": "Confirm leakage basis"})
        check("source research remains queued and does not fetch", research["airflow_resolution"]["research_jobs"][0]["status"] == "queued")
        rebuilt = airflow_resolution_service.post(web, project, {"action": "resolve"})
        check("queued airflow research survives resolver re-runs", rebuilt["airflow_resolution"]["research_jobs"] == research["airflow_resolution"]["research_jobs"])
        check("response does not expose local project path", str(root) not in json.dumps(research))

        building = json.loads((root / "building_evidence.json").read_text(encoding="utf-8"))
        building["review_note"] = "Changed after resolver run"
        (root / "building_evidence.json").write_text(json.dumps(building), encoding="utf-8")
        stale = airflow_resolution_service.get(web, project)
        check("changed airflow evidence is reported stale", stale["status"] == "stale" and stale["stale_reasons"])
        rejected = 0
        for action in ("apply_override", "clear_override"):
            try:
                airflow_resolution_service.post(web, project, {
                    "action": action, "room_id": room_id, "air_path_type": "outside_air",
                    "value": 30, "reviewer": "Engineer",
                })
            except ValueError as error:
                rejected += "stale" in str(error).casefold()
        check("airflow override edits are rejected while source inputs are stale", rejected == 2)
        refreshed = airflow_resolution_service.post(web, project, {"action": "resolve"})
        check("resolving current airflow sources re-enables override editing", refreshed["status"] != "stale")
        artifact = refreshed["airflow_resolution"]
        stale_row = next(row for row in artifact["records"]
                         if row.get("owner_room_id") == room_id and row.get("air_path_type") == "outside_air")
        stale_row["status"] = "stale"
        write(root / "airflow_resolution.json", artifact)
        try:
            airflow_resolution_service.post(web, project, {
                "action": "apply_override", "room_id": room_id, "air_path_type": "outside_air",
                "value": 45, "reviewer": "Engineer",
            })
            stale_edit_rejected = False
        except ValueError as error:
            stale_edit_rejected = "stale" in str(error).casefold()
        check("retained stale room/path rejects override edits even when source fingerprints are current",
              stale_edit_rejected)
        cleared = airflow_resolution_service.post(web, project, {
            "action": "clear_override", "room_id": room_id, "air_path_type": "outside_air",
        })["airflow_resolution"]
        cleared_row = next(row for row in cleared["records"]
                           if row.get("owner_room_id") == room_id and row.get("air_path_type") == "outside_air")
        check("clearing a retained stale override keeps the row stale and removes its value",
              cleared_row["status"] == "stale" and cleared_row.get("value") is None
              and not cleared_row.get("overrides")
              and not any(item.get("room_id") == room_id and item.get("path_type") == "outside_air"
                          for item in cleared.get("overrides", [])))
        airflow_resolution_service.post(web, project, {"action": "resolve"})
        artifact = json.loads((root / "airflow_resolution.json").read_text(encoding="utf-8"))
        artifact["source_fingerprints"]["unexpected_extra"] = "legacy-self-reference"
        write(root / "airflow_resolution.json", artifact)
        stale = airflow_resolution_service.get(web, project)
        check("airflow staleness detects extra self-reference source keys",
              stale["status"] == "stale" and "unexpected_extra" in stale["stale_reasons"])


if __name__ == "__main__":
    main()
