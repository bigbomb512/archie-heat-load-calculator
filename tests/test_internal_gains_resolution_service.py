#!/usr/bin/env python3
"""Project-local API checks for internal-gains resolution."""

import json
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import internal_gains_resolution_service


class Web:
    def safe_link(self, path):
        return f"/artifact/{Path(path).name}"

    def update_project(self, project):
        return None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "internal-gains-service", "review_dir": str(root)}
        (root / "building_evidence.json").write_text(json.dumps({"spaces": [{"name": "Office", "level_name": "Level 1", "area_m2": 40, "evidence": [{"page": 1}]}]}))
        (root / "ai_preliminary_run.json").write_text(json.dumps({"manual_placeholder_proposal": {"rooms": [{"kind": "room", "label": "Office", "level_name": "Level 1", "area_m2": 40, "preliminary_profile_id": "office", "page": 1}]}}))
        response = internal_gains_resolution_service.post(Web(), project, {"action": "resolve"})
        check("resolve writes internal-gains artifact", (root / "internal_gains_resolution.json").exists() and response["internal_gains_resolution"]["records"])
        room_id = response["internal_gains_resolution"]["records"][0]["room_id"]
        response = internal_gains_resolution_service.post(Web(), project, {"action": "apply_override", "room_id": room_id, "field": "occupancy_count", "value": 8, "reviewer": "Engineer"})
        check("API override persists provenance", response["internal_gains_resolution"]["records"][0]["occupancy_count"] == 8)
        response = internal_gains_resolution_service.post(Web(), project, {"action": "clear_override", "room_id": room_id, "field": "occupancy_count"})
        check("API clear override is available", response["internal_gains_resolution"]["records"][0]["override"] is None)


if __name__ == "__main__":
    main()
