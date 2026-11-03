"""Pages, levels and the site for Phase 1 on any drawing set (house set 15, 2026-11-03).

- A house's upper floors are named ("FIRST FLOOR PLAN"), and the page reading's level wins on its main plans.
- One unknown level: "" and "Unassigned level" are the same, and a level-less copy of a named room is merged into it.
- When a set has no floor plan and no services plan, the drawing with the most linework is traced (labelled).
- House numbers with a letter ("8A") and addresses after an address label are site clues.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai import drawing_coverage, room_inference, site_location_resolution
from ai.room_use_resolution import room_identity
from backend import autonomous_tasks_service as tasks


class LevelTests(unittest.TestCase):
    def levels(self, title):
        page = {"page": 8, "title": title, "structured_content": {"markdown": ""}}
        return [row["normalized"] for row in drawing_coverage._level_candidates(page, {}) if row["kind"] == "building_level"]

    def test_upper_floors_are_named(self):
        self.assertEqual(self.levels("FIRST FLOOR PLAN"), ["First Floor"])
        self.assertEqual(self.levels("Upper Ground Floor Plan"), ["Upper Ground Floor"])
        self.assertEqual(self.levels("MEZZANINE PLAN"), ["Mezzanine"])
        self.assertEqual(self.levels("Level 2 Floor Plan"), ["Level 2"])

    def test_one_unknown_level(self):
        self.assertEqual(room_identity("Kitchen", ""), room_identity("Kitchen", "Unassigned level"))
        self.assertEqual(room_identity("Kitchen", None), "room-use:unassigned-level:kitchen")
        self.assertNotEqual(room_identity("Kitchen", "Ground Floor"), room_identity("Kitchen", ""))

    def test_the_page_readings_level_wins_on_its_main_plans(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 7, "level_name": "Ground Floor"}, {"page": 8, "level_name": "Ground Floor"},
                {"page": 16, "level_name": "Ground Floor"}]}), encoding="utf-8")
            plan = {"page_type": "floor_plan", "whole_floor": True, "floor_plan_kind": "proposed_layout"}
            (root / "page_inventory.json").write_text(json.dumps({"pages": {
                "7": {**plan, "level": "Ground"}, "8": {**plan, "level": "First"},
                "16": {"page_type": "floor_plan", "whole_floor": False, "floor_plan_kind": "other", "level": "Ground Floor"}}}),
                encoding="utf-8")
            self.assertEqual(tasks._page_level(root, 8), "First")       # legend said "Natural ground level"
            self.assertEqual(tasks._page_level(root, 7), "Ground")
            self.assertEqual(tasks._page_level(root, 16), "Ground Floor")  # not a main plan: coverage as before


class DuplicateRoomTests(unittest.TestCase):
    def infer(self, pages, spatial):
        return room_inference.infer({"drawing_set": {"pages": pages}}, {}, {"pages": spatial})["rooms"]

    @staticmethod
    def plan(page, level=""):
        return {"page": page, "title": "Floor plan", "detected_type": "floor_plan", "plan_role": "main_floor_plan",
                "scale": "1:100", **({"level_name": level} if level else {})}

    @staticmethod
    def labels(page, *names):
        return {"page": page, "standalone_text_items": [{"text": name, "bbox": [100 + 200 * i, 100, 160 + 200 * i, 110]}
                                                        for i, name in enumerate(names)]}

    def test_a_level_less_copy_joins_the_named_room(self):
        rooms = self.infer([self.plan(16, "Ground Floor"), self.plan(22)],
                           [self.labels(16, "DINING", "KITCHEN"), self.labels(22, "DINING", "KITCHEN")])
        self.assertEqual([(row["label"], row["level_name"], row["source_pages"]) for row in rooms],
                         [("Dining", "Ground Floor", [16, 22]), ("Kitchen", "Ground Floor", [16, 22])])

    def test_a_name_on_two_named_levels_stays_apart(self):
        rooms = self.infer([self.plan(7, "Ground Floor"), self.plan(8, "First Floor"), self.plan(22)],
                           [self.labels(7, "BATHROOM"), self.labels(8, "BATHROOM"), self.labels(22, "BATHROOM")])
        self.assertEqual(sorted(row["level_name"] for row in rooms), ["First Floor", "Ground Floor", "Unassigned level"])

    def test_a_one_level_shop_is_unchanged(self):
        rooms = self.infer([self.plan(20)], [self.labels(20, "KITCHEN", "BAR")])
        self.assertEqual(sorted((row["label"], row["level_name"]) for row in rooms),
                         [("Bar", "Unassigned level"), ("Kitchen", "Unassigned level")])


class AnyPlanFallbackTests(unittest.TestCase):
    def test_with_no_plan_the_drawing_with_most_linework_is_traced(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pages = [{"page": 1, "title": "Cover", "plan_role": "reference"},
                     {"page": 2, "title": "Sketch", "plan_role": "reference"},
                     {"page": 3, "title": "Notes", "plan_role": "reference"}]
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [
                {"page": 2, "raw_counts": {"lines": 900, "curves": 40}}, {"page": 3, "raw_counts": {"lines": 20}}]}}),
                encoding="utf-8")
            selection = tasks._p0_page_selection(root)
        self.assertEqual([row["page"] for row in selection["selected"]], [2])
        self.assertTrue(any(note["status"] == "any_plan_fallback" for note in selection["notes"]))


class SiteTests(unittest.TestCase):
    def addresses(self, text):
        ai_input = {"drawing_set": {"pages": [{"page": 1, "title": "Title", "structured_content": {"markdown": text}}]}}
        return [row["address"] for row in site_location_resolution.infer_pdf_context(ai_input)["address_candidates"]]

    def test_house_numbers_with_a_letter(self):
        self.assertEqual(self.addresses("8A New South Head Road, Vaucluse NSW 2030"), ["8A New South Head Road, Vaucluse NSW 2030"])
        self.assertEqual(self.addresses("Unit 3/12B King St, Newtown NSW 2042"), ["3/12B King St, Newtown NSW 2042"])

    def test_an_address_after_a_label_needs_no_state(self):
        self.assertEqual(self.addresses("PROJECT ADDRESS: 139a Burns Rd Turramurra"), ["139a Burns Rd Turramurra"])

    def test_plain_text_without_a_street_is_not_an_address(self):
        self.assertEqual(self.addresses("ADDRESS: refer to cover sheet"), [])


if __name__ == "__main__":
    unittest.main()
