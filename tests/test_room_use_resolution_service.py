#!/usr/bin/env python3
"""Project lifecycle checks for room-use classification."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
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


class UseEvidenceServiceTests(unittest.TestCase):
    def test_physical_text_and_crop_are_assigned_to_outline_not_room_name(self):
        from backend import equipment_catalogue_service as equipment, autonomous_tasks_service as tasks
        with TemporaryDirectory() as directory:
            root = Path(directory)
            traces = [
                {"room_id": "back", "room_label": "Dining", "page": 1, "points_image_px": [[0,0],[100,0],[100,100],[0,100],[0,0]], "ai_measured_area_m2": 20},
                {"room_id": "front", "room_label": "Kitchen", "page": 1, "points_image_px": [[110,0],[210,0],[210,100],[110,100],[110,0]], "ai_measured_area_m2": 50}]
            (root / "reviewer_room_geometry.json").write_text(json.dumps({"records": traces}))
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [{"page": 1, "coordinate_systems": {"image_px": {"image_width": 220, "image_height": 100}}}]}}))
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 1, "width": 220, "height": 100, "word_samples": [{"text": "WC", "bbox": [10,10,20,20]}]}]}))
            task = {"task": "P6_kitchen", "status": "applied", "packet": {"plan_page": 1, "room_id": "front", "images": [{"page": 1, "bbox_px": [0,0,100,100]}]},
                    "applied_value": {"items": [{"type": "oven", "page": 1}, {"type": "dishwasher", "page": 2}]}}
            with patch.object(equipment, "resolve", return_value={"records": [], "zones": {}}), \
                    patch.object(tasks, "_all_current", return_value=[task]):
                context = room_use_resolution_service.gather_use_evidence(root)
            self.assertEqual({item["name"] for item in context["back"]["contents"]}, {"WC", "oven"})
            self.assertEqual(context["front"]["contents"], [])
            self.assertNotIn("dishwasher", str(context))

    def test_new_placement_invalidates_evidence_cache_without_using_heat_numbers(self):
        from backend import equipment_catalogue_service as equipment, autonomous_tasks_service as tasks
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reviewer_room_geometry.json").write_text(json.dumps({"records": [{"room_id": "room-use:unassigned-level:dining", "room_label": "Dining", "page": 1, "points_image_px": [[0,0],[10,0],[10,10],[0,10],[0,0]]}]}))
            with patch.object(equipment, "resolve", return_value={"records": [], "zones": {}}) as resolve, \
                    patch.object(tasks, "_all_current", return_value=[]):
                before = room_use_resolution_service.gather_use_evidence(root)
                self.assertEqual(before, room_use_resolution_service.gather_use_evidence(root))
                self.assertEqual(resolve.call_count, 1)
                (root / equipment.ARTIFACT).write_text(json.dumps({"zones": {"prep": {"room": "Dining"}}}))
                room_use_resolution_service.gather_use_evidence(root)
                self.assertEqual(resolve.call_count, 2)


if __name__ == "__main__":
    main()
    unittest.main(argv=[sys.argv[0]])
