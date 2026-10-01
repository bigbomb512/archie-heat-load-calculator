#!/usr/bin/env python3
"""Project lifecycle checks for room-use classification."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import room_use_resolution_service


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


class LocalWeb:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def main():
    with TemporaryDirectory() as directory:
        root, web = Path(directory), LocalWeb()
        project = {"id": "room-use-test", "review_dir": str(root)}
        (root / "building_evidence.json").write_text(json.dumps({"spaces": [{"name": "Kitchen", "level_name": "Level 1", "area": 12, "evidence": [{"page": 1, "excerpt": "Kitchen"}]}]}), encoding="utf-8")
        (root / "vision_response.json").write_text(json.dumps({"result": {"auto_extraction": {"entities": []}}}), encoding="utf-8")
        resolved = room_use_resolution_service.post(web, project, {"action": "resolve"})
        body = resolved["room_use_resolution"]
        room_id = body["records"][0]["room_id"]
        check("resolve writes project-local artifact", (root / "room_use_resolution.json").exists() and body["records"][0]["taxonomy_id"] == "kitchen")
        overridden = room_use_resolution_service.post(web, project, {"action": "apply_override", "room_id": room_id, "taxonomy_id": "retail", "reviewer": "Engineer", "note": "Showroom fitout"})
        check("override uses controlled taxonomy and is returned through opaque API", overridden["room_use_resolution"]["records"][0]["preliminary_profile_id"] == "retail" and overridden["artifact_url"] == "/artifact/room_use_resolution.json")
        restored = room_use_resolution_service.post(web, project, {"action": "clear_override", "room_id": room_id})
        check("clearing override restores current PDF/AI classification", restored["room_use_resolution"]["records"][0]["taxonomy_id"] == "kitchen")
        building = json.loads((root / "building_evidence.json").read_text(encoding="utf-8"))
        building["spaces"][0]["name"] = "Office"
        (root / "building_evidence.json").write_text(json.dumps(building), encoding="utf-8")
        stale = room_use_resolution_service.get(web, project)
        check("changed source evidence returns stale state without overwriting history", stale["status"] == "stale" and stale["room_use_resolution"]["stale_reasons"])


if __name__ == "__main__":
    main()
