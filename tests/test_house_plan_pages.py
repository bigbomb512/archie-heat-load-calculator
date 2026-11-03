"""A house set's floor plans: the proposed plan of each level is traced (not the existing, demolition or roof plan),
each level keeps its own rooms, and a house is never offered shop names (house set 18, 2026-11-01)."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai import room_outline, room_use_resolution
from backend import autonomous_tasks_service as tasks, room_proposal


def write(root, name, value):
    (root / name).write_text(json.dumps(value), encoding="utf-8")


class PlanChoiceTests(unittest.TestCase):
    def test_the_page_reading_picks_the_proposed_plan_of_each_level(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            # Sheet metadata as set 18 had it: the existing plan tagged as a plan of the wrong level, the proposed
            # lower ground floor as a reference sheet, the roof plan as a main floor plan.
            sheets = [(4, "uncertain_top_down_context", "Ground Floor"), (5, "uncertain_top_down_context", ""),
                      (8, "reference_context", "Lower Ground Floor"), (9, "main_floor_plan", "Ground Floor"),
                      (10, "main_floor_plan", "First Floor"), (11, "main_floor_plan", "Roof"),
                      (17, "uncertain_top_down_context", "Lower Ground Floor")]
            write(root, "ai_input.json", {"drawing_set": {"pages": [
                {"page": page, "plan_role": role, "level_name": level, "title": "Plan", "scale": "1:100"} for page, role, level in sheets]}})
            write(root, "drawing_coverage.json", {"page_roles": [
                {"page": page, "proposed_role": "supporting_geometry_plan" if role != "main_floor_plan" else role, "level_name": ""}
                for page, role, _level in sheets]})
            readings = {"4": ("existing_or_demolition", "Lower Ground"), "5": ("existing_or_demolition", "Ground"),
                        "8": ("proposed_layout", "Lower Ground"), "9": ("proposed_layout", "Ground"),
                        "10": ("proposed_layout", "First"), "17": ("dimension_setout", "Lower Ground")}
            write(root, "page_inventory.json", {"pages": {
                **{page: {"page_type": "floor_plan", "floor_plan_kind": kind, "whole_floor": True, "level": level, "title": "Plan"}
                   for page, (kind, level) in readings.items()},
                "11": {"page_type": "other", "floor_plan_kind": "", "whole_floor": False, "level": "Roof", "title": "Roof Plan"}}})
            write(root, "vector_geometry.json", {"geometry_key_points": {"pages": [
                {"page": 8, "dimension_candidates": [{"id": 1}] * 30}, {"page": 17, "dimension_candidates": [{"id": 1}] * 10}]}})
            selection = tasks._p0_page_selection(root)
            self.assertEqual([row["page"] for row in selection["selected"]], [8, 9, 10])
            reasons = {row["page"]: row["reason"] for row in selection["skipped"]}
            self.assertIn("existing or demolition", reasons[4])
            self.assertIn("existing or demolition", reasons[5])
            self.assertIn("other", reasons[11])
            self.assertIn("second view of lower ground", reasons[17])

    def test_without_a_page_reading_the_sheet_metadata_still_decides(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root, "ai_input.json", {"drawing_set": {"pages": [
                {"page": 19, "plan_role": "main_floor_plan", "level_name": "", "title": "Layout", "scale": "1:100"},
                {"page": 20, "plan_role": "main_floor_plan", "level_name": "", "title": "Dimensions", "scale": "1:100"}]}})
            write(root, "vector_geometry.json", {"geometry_key_points": {"pages": [
                {"page": 20, "dimension_candidates": [{"id": 1}] * 5}]}})
            self.assertEqual([row["page"] for row in tasks._p0_page_selection(root)["selected"]], [20])

    def test_a_page_without_a_coverage_level_takes_the_page_readings(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root, "drawing_coverage.json", {"page_roles": [{"page": 9, "level_name": "Ground Floor"}, {"page": 10, "level_name": ""}]})
            write(root, "page_inventory.json", {"pages": {"10": {"level": "First"}}})
            self.assertEqual((tasks._page_level(root, 9), tasks._page_level(root, 10), tasks._page_level(root, 11)),
                             ("Ground Floor", "First", ""))


class RoomPerLevelTests(unittest.TestCase):
    def rows(self, rooms, page_level):
        from shapely.geometry import Polygon
        square = Polygon([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
        record = {"packet": {"page": 10, "calibration": {"source": "ai_read_printed_dimension", "status": "agreed", "mm_per_px": 10}}}
        with patch.object(tasks.reviewer_room_geometry_service, "_rooms", return_value=rooms), \
             patch.object(tasks, "_inferred_room_data", return_value=[]), \
             patch.object(tasks, "_page_level", return_value=page_level):
            return tasks._p0_trace_rows(Path("/tmp"), record, [{"label": "Ensuite", "polygon": square, "method": "enclosed_walls"},
                                                              {"label": "Kitchen", "polygon": square, "method": "enclosed_walls"}])

    def test_a_name_on_two_levels_is_the_pages_levels_room(self):
        rooms = [{"room_id": "ens-ground", "label": "Ensuite", "level_name": "Ground Floor"},
                 {"room_id": "ens-first", "label": "Ensuite", "level_name": "First Floor"},
                 {"room_id": "kitchen", "label": "Kitchen", "level_name": "Ground Floor"}]
        self.assertEqual([row["room_id"] for row in self.rows(rooms, "First")], ["ens-first", "kitchen"])
        self.assertEqual([row["room_id"] for row in self.rows(rooms, "Ground")], ["ens-ground", "kitchen"])

    def test_one_level_keeps_the_first_room_of_the_name(self):
        rooms = [{"room_id": "a", "label": "Shop", "level_name": ""}, {"room_id": "b", "label": "Shop", "level_name": ""},
                 {"room_id": "e", "label": "Ensuite", "level_name": ""}]
        self.assertEqual([row["room_id"] for row in self.rows(rooms, "")], ["e"])


class HouseNamesTests(unittest.TestCase):
    def test_a_house_is_not_offered_shop_names(self):
        house = room_outline._room_names_for_context(dwelling=True)
        self.assertNotIn("Shop", house)
        self.assertNotIn("Bar", house)
        self.assertIn("Bedroom", house)
        self.assertIn("Kitchen", house)
        self.assertEqual(room_outline._room_names_for_context(), room_outline.USE_ROOM_NAMES)


class ReadRoomNamesTests(unittest.TestCase):
    def test_the_page_readings_room_list_gives_the_names_drawn_on_the_plan(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root, "page_inventory.json", {"pages": {"10": {"information": [
                {"kind": "room_geometry", "what": "Bedroom 2 3500x3940"}, {"kind": "room_geometry", "what": "Master bedroom 4580x4080"},
                {"kind": "room_geometry", "what": "WIR"}, {"kind": "room_geometry", "what": "Pool dimension 9160 mm"},
                {"kind": "room_geometry", "what": "Total site area 912.8 m²"},
                {"kind": "room_geometry", "what": "Dimensions: 400 mm, 2270 mm"},
                {"kind": "equipment_appliances", "what": "BBQ"}]}}})
            self.assertEqual(tasks._reading_room_names(root, 10), ["Bedroom 2", "Master bedroom", "WIR"])
            self.assertEqual(tasks._reading_room_names(root, 9), [])

    def test_a_room_list_written_as_one_sentence_gives_each_name(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root, "page_inventory.json", {"pages": {
                "5": {"information": [{"kind": "room_geometry", "what": "Basement floor plan includes Garage, Lift, Bar and Bath."},
                                      {"kind": "room_geometry", "what": "Basement area 184.3 m²."}]},
                "6": {"information": [{"kind": "room_geometry", "what": "Ground floor layout includes Living, WC, L'DRY, and Pool."}]},
                "8": {"information": [{"kind": "room_geometry",
                                       "what": "Ground floor rooms: Alfresco, Bed 2, Ensuite (plus separate detached structure to the east)"}]}}})
            self.assertEqual(tasks._reading_room_names(root, 5), ["Bar", "Bath", "Garage", "Lift"])
            self.assertEqual(tasks._reading_room_names(root, 6), ["L'DRY", "Living", "Pool", "WC"])
            self.assertEqual(tasks._reading_room_names(root, 8), ["Alfresco", "Bed 2", "Ensuite"])

    def test_a_name_from_the_plan_becomes_a_room_with_its_own_evidence(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            room_proposal.save_use_named_rooms(root, "P0_room_names_read", 10, "First", ["Bedroom 2"], "run", named_on_plan=True)
            room_proposal.save_use_named_rooms(root, "P0_room_names", 10, "First", ["Corridor"], "run")
            with patch.object(room_proposal, "base_proposal", return_value={"rooms": []}):
                rooms = {row["label"]: row for row in room_proposal.room_proposal({}, root)["rooms"]}
            self.assertEqual((rooms["Bedroom 2"]["source"], rooms["Corridor"]["source"]), ("named_on_plan", "named_by_use"))
            self.assertIn("drawn on the plan", rooms["Bedroom 2"]["evidence"][0]["reference"])
            self.assertEqual(rooms["Bedroom 2"]["level_name"], "First")


class HouseRoomUseTests(unittest.TestCase):
    def test_corridors_are_circulation_an_office_is_a_living_area_and_a_porch_is_outside(self):
        names = ["Corridor", "Hallway", "Office", "Porch", "Front verandah"]
        records = room_use_resolution.resolve({"spaces": [{"name": name, "level_name": "Ground"} for name in names]},
                                              dwelling_context={"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]})["records"]
        self.assertEqual({row["original_label"]: (row["taxonomy_id"], row["space_scope"]) for row in records},
                         {"Corridor": ("circulation", "comfort_hvac"), "Hallway": ("circulation", "comfort_hvac"),
                          "Office": ("house_living", "comfort_hvac"), "Porch": ("outdoor", "not_cooled"),
                          "Front verandah": ("outdoor", "not_cooled")})
        records = room_use_resolution.resolve({"spaces": [{"name": name, "level_name": "Basement"} for name in ("Entertainment", "WIP")]},
                                              dwelling_context={"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]})["records"]
        self.assertEqual({row["taxonomy_id"] for row in records}, {"house_living"})   # walk-in pantry: a pantry, not a robe
        shop = room_use_resolution.resolve({"spaces": [{"name": "Office", "level_name": "Ground"}]})["records"][0]
        self.assertEqual(shop["taxonomy_id"], "office")


if __name__ == "__main__":
    unittest.main()
