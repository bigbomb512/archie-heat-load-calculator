#!/usr/bin/env python3
"""Project lifecycle checks for ceiling-height resolution."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import ceiling_volume_resolution_service


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
        project = {"id": "ceiling-test", "review_dir": str(root)}
        (root / "building_evidence.json").write_text(json.dumps({"spaces": [{"name": "Office", "level_name": "Level 1", "area": 30, "evidence": [{"page": 1, "excerpt": "OFFICE"}]}]}), encoding="utf-8")
        (root / "vision_response.json").write_text(json.dumps({"result": {"auto_extraction": {"entities": []}}}), encoding="utf-8")
        resolved = ceiling_volume_resolution_service.post(web, project, {"action": "resolve"})
        row = resolved["ceiling_volume_resolution"]["records"][0]
        check("resolve writes a project-local artifact", (root / "ceiling_volume_resolution.json").exists() and row["origin"] == "preliminary_fallback")
        overridden = ceiling_volume_resolution_service.post(web, project, {"action": "apply_override", "room_id": row["room_id"], "ceiling_height_mm": 3100, "reviewer": "Engineer", "note": "Site measure"})
        check("override is returned through the opaque API", overridden["ceiling_volume_resolution"]["records"][0]["ceiling_height_mm"] == 3100 and overridden["artifact_url"] == "/artifact/ceiling_volume_resolution.json")
        restored = ceiling_volume_resolution_service.post(web, project, {"action": "clear_override", "room_id": row["room_id"]})
        check("clearing override restores the controlled draft fallback", restored["ceiling_volume_resolution"]["records"][0]["origin"] == "preliminary_fallback")


if __name__ == "__main__":
    main()
