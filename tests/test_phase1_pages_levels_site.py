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
from unittest.mock import patch

from ai import drawing_coverage, page_inventory, room_inference, site_location_resolution
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
            self.assertEqual(tasks._page_level(root, 7), "Ground Floor")  # the same level: coverage's spelling, so one floor
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


class MainPlanTests(unittest.TestCase):
    @staticmethod
    def plan(title, level="Ground Floor", kind="proposed_layout"):
        return {"page_type": "floor_plan", "whole_floor": True, "floor_plan_kind": kind, "title": title, "level": level}

    def test_a_diagram_is_not_a_main_plan(self):
        for title in ("GFA Diagram", "Gross Floor Area Plan", "Area Calculations", "Shadow Diagram 9am", "Landscape Plan",
                      "Backyard Plan", "Roof Plan", "Site Plan", "Location Plan", "Fill Area", "Open Space Plan"):
            self.assertEqual(page_inventory.main_plan_pages({5: self.plan(title), 7: self.plan("Ground Floor Plan")}),
                             {"ground": [7]}, title)

    def test_a_sheet_covering_several_levels_is_not_a_main_plan(self):
        for level in ("Ground, First", "Ground & First", "Ground and First"):
            self.assertEqual(page_inventory.main_plan_pages({5: self.plan("Plans", level), 7: self.plan("Ground Floor Plan")}),
                             {"ground": [7]}, level)

    def test_a_diagrams_labels_are_not_rooms(self):
        readings = {2: self.plan("Area Calculation", "First Floor, Ground Floor"), 9: self.plan("Ground Floor Plan")}
        self.assertEqual(page_inventory.diagram_pages(readings), {2})
        plan = {"title": "Plan", "detected_type": "floor_plan", "plan_role": "main_floor_plan", "scale": "1:100"}
        rooms = room_inference.infer(
            {"drawing_set": {"pages": [{**plan, "page": 2}, {**plan, "page": 9}]}}, {},
            {"pages": [{"page": 2, "standalone_text_items": [{"text": "OFFICE", "bbox": [100, 100, 160, 110]}]},
                       {"page": 9, "standalone_text_items": [{"text": "KITCHEN", "bbox": [100, 100, 160, 110]}]}]},
            skip_pages={2})["rooms"]
        self.assertEqual([row["label"] for row in rooms], ["Kitchen"])

    def test_a_set_of_only_diagrams_keeps_them(self):
        self.assertEqual(page_inventory.diagram_pages({2: self.plan("Area Calculation")}), set())

    def test_ordinary_floor_plans_stay(self):
        readings = {7: self.plan("Proposed Ground Floor Plan"), 8: self.plan("First Floor Plan", "First Floor"),
                    9: self.plan("Dimension Plan", "Mezzanine")}
        self.assertEqual(page_inventory.main_plan_pages(readings), {"ground": [7], "first": [8], "mezzanine": [9]})

    def selected(self, readings):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pages = [{"page": page, "title": row["title"], "detected_type": "floor_plan", "plan_role": "main_floor_plan"}
                     for page, row in readings.items()]
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": {str(page): row for page, row in readings.items()}}),
                                                      encoding="utf-8")
            return [row["page"] for row in tasks._p0_page_selection(root)["selected"]]

    def test_a_main_plan_with_no_level_is_the_only_named_levels_floor(self):
        # Happy Cap: "Tenancy MZ01" read as a Mezzanine, and the floor plan with no level: one floor, traced once.
        self.assertEqual(len(self.selected({6: self.plan("Floor Plan", ""), 7: self.plan("Dimension Plan", "Mezzanine")})), 1)

    def test_two_named_levels_are_two_floors(self):
        self.assertEqual(self.selected({7: self.plan("Ground Floor Plan"), 8: self.plan("First Floor Plan", "First Floor")}), [7, 8])


class MainPlanLineworkTests(unittest.TestCase):
    """Set 15 2026-11-06: the first floor plan had no linework, so every outline drawn on it was refused."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        plan = {"page_type": "floor_plan", "whole_floor": True, "floor_plan_kind": "proposed_layout"}
        (self.root / "page_inventory.json").write_text(json.dumps({"pages": {
            "7": {**plan, "title": "Ground Floor Plan", "level": "Ground"}, "8": {**plan, "title": "First Floor Plan", "level": "First"},
            "16": {**plan, "title": "GFA Diagram", "level": "Ground"}}}), encoding="utf-8")
        (self.root / "ai_input.json").write_text("{}", encoding="utf-8")

    def vector(self, *pages):
        (self.root / "vector_geometry.json").write_text(json.dumps({"note": "kept", "geometry_key_points": {
            "pages": [{"page": page} for page in pages]}}), encoding="utf-8")

    def stored(self):
        return json.loads((self.root / "vector_geometry.json").read_text(encoding="utf-8"))

    def test_a_main_plan_without_linework_gets_it_and_a_diagram_does_not(self):
        self.vector(7, 16)
        with patch("ai.vector_geometry.extract_pages", side_effect=lambda _a, _s, refs: [{"page": ref["page"]} for ref in refs]) as extract:
            self.assertEqual(tasks._add_main_plan_linework(self.root), [8])
        self.assertEqual([ref["page"] for ref in extract.call_args.args[2]], [8])
        self.assertEqual(extract.call_args.args[2][0]["title"], "First Floor Plan")
        stored = self.stored()
        self.assertEqual([row["page"] for row in stored["geometry_key_points"]["pages"]], [7, 16, 8])
        self.assertEqual(stored["note"], "kept")

    def test_nothing_is_read_when_every_main_plan_has_linework(self):
        self.vector(7, 8)
        with patch("ai.vector_geometry.extract_pages") as extract:
            self.assertEqual(tasks._add_main_plan_linework(self.root), [])
        extract.assert_not_called()

    def test_a_pdf_failure_leaves_the_job_as_it_was(self):
        self.vector(7)
        before = self.stored()
        with patch("ai.vector_geometry.extract_pages", side_effect=ValueError("bad pdf")):
            self.assertEqual(tasks._add_main_plan_linework(self.root), [])
        self.assertEqual(self.stored(), before)

    def test_a_job_with_no_extraction_is_left_alone(self):
        with patch("ai.vector_geometry.extract_pages") as extract:
            self.assertEqual(tasks._add_main_plan_linework(self.root), [])
        extract.assert_not_called()


class OutlinedNamesTests(unittest.TestCase):
    rooms = [{"label": "Kitchen"}]
    drawn = [{"room": "Bedrooms"}, {"room": "Bedrooms"}, {"room": "Kitchen"}, {"room": "Lounge"}, {"room": "Laundry"}]

    def test_a_house_keeps_the_names_it_was_asked_to_draw(self):
        packet = {"dwelling": True, "room_names": ["Bedrooms", "Kitchen", "Lounge"]}
        self.assertEqual(tasks._outlined_names_to_add(packet, self.drawn, self.rooms), ["Bedrooms", "Lounge"])

    def test_a_shop_room_list_is_unchanged(self):
        self.assertEqual(tasks._outlined_names_to_add({"room_names": ["Bedrooms"]}, self.drawn, self.rooms), [])


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
