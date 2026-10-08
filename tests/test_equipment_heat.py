#!/usr/bin/env python3
"""From an equipment reading to heat in a room: type, rating, heat-to-room factor, and the hand-off to internal gains."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai import equipment_heat as heat
from ai.internal_gains_resolution import _equipment_sources
from backend import ai_preliminary_service, page_extraction_service


class TypeAndRatingTests(unittest.TestCase):
    def test_types_come_from_the_name_and_hoods_and_cold_rooms_are_not_equipment(self):
        cases = {"UB FRIDGE": "refrigeration", "2 door drink fridge": "refrigeration", "ICE MAKER": "refrigeration",
                 "Combi oven": "cooking", "6 BURNER": "cooking", "Pizza deck oven": "cooking", "DISHWASHER": "dishwashing",
                 "Coffee machine": "hot_drinks", "75 INCH TV": "electronics", "POS": "electronics",
                 "Illuminated sign": "signage", "RANGE HOOD": "exhaust_hood", "COOLROOM": "refrigerated_room",
                 "Freezer room": "refrigerated_room", "Pub table": "other"}
        self.assertEqual({name: heat.equipment_type(name) for name in cases}, cases)

    def test_printed_watts_only_from_power_units(self):
        self.assertEqual([heat.printed_watts(text) for text in ("6.3 kW", "2400 W", "2400w", "15 A", "240 V", "", "0 kW")],
                         [6300, 2400, 2400, None, None, None, None])

    def test_a_printed_rating_wins_typical_values_only_for_narrow_types_and_gaps_are_named(self):
        fryer = heat.proposal({"name": "Deep fryer", "rated_power": "12 kW", "under_hood": True, "quantity": 3})
        self.assertEqual((fryer["rated_input_w"], fryer["rated_source"], fryer["heat_to_space_factor"], fryer["heat_w"]),
                         (12000, "printed on the drawings", 0.2, 7200))
        tv = heat.proposal({"name": "75 inch TV", "quantity": 2})
        self.assertEqual((tv["rated_input_w"], tv["rated_source"], tv["heat_w"]), (250, heat.PLACEHOLDER, 500))
        oven = heat.proposal({"name": "Combi oven", "under_hood": None})
        self.assertIsNone(oven["heat_w"])
        self.assertEqual(oven["needed"], ["rated power (from the spec sheet)", "whether it is under a hood"])
        hood = heat.proposal({"name": "Range hood"})
        self.assertIn("ventilation", hood["not_equipment"])
        self.assertIsNone(hood["heat_w"])

    def test_heat_for_an_accepted_item_needs_a_rating_and_a_factor_in_range(self):
        self.assertEqual(heat.heat_w({"quantity": 2, "rated_input_w": 600, "heat_to_space_factor": 1}), 1200)
        for bad in ({"rated_input_w": None, "heat_to_space_factor": 1}, {"rated_input_w": 600, "heat_to_space_factor": 1.5},
                    {"rated_input_w": 0, "heat_to_space_factor": 1}, {"rated_input_w": "x", "heat_to_space_factor": 1}):
            self.assertIsNone(heat.heat_w(bad))


class RoomAndHandOffTests(unittest.TestCase):
    def test_the_printed_location_suggests_a_room(self):
        rooms = ["Bar", "Kitchen", "Shop", "Shop Storage"]
        self.assertEqual(page_extraction_service.suggested_room("KITCHEN AREA", rooms), "Kitchen")
        self.assertEqual(page_extraction_service.suggested_room("shop storage", rooms), "Shop Storage")
        self.assertEqual(page_extraction_service.suggested_room("front of house", rooms), "")

    def test_only_accepted_items_with_a_room_rating_and_factor_reach_internal_gains(self):
        accepted = [
            {"id": "a", "name": "UB FRIDGE", "code": "E21", "quantity": 10, "room": "Kitchen", "rated_input_w": 350.0,
             "heat_to_space_factor": 1.0, "pages": [5]},
            {"id": "b", "name": "Combi oven", "quantity": 1, "room": "Kitchen", "rated_input_w": None, "heat_to_space_factor": 0.2, "pages": [5]},
            {"id": "c", "name": "TV", "quantity": 1, "room": "", "rated_input_w": 150.0, "heat_to_space_factor": 1.0, "pages": [9]},
        ]
        proposal = {"rooms": [{"label": "Kitchen", "room_id": "k"}, {"label": "Shop", "room_id": "s"}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(page_extraction_service, "accepted", return_value=accepted):
            result = ai_preliminary_service._with_accepted_equipment({"root": Path(folder)}, proposal)
        kitchen = result["rooms"][0]
        self.assertEqual([row["name"] for row in kitchen["equipment"]], ["UB FRIDGE"])
        row = _equipment_sources([kitchen])[0]
        self.assertEqual((row["quantity"], row["rated_input_w"], row["heat_to_space_factor"], row["evidence"][0]["page"]),
                         (10, 350.0, 1.0, 5))
        self.assertNotIn("equipment", result["rooms"][1])


class ReviewValidationTests(unittest.TestCase):
    def test_rating_and_factor_are_checked_when_accepting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / page_extraction_service.RESULT_FILE).write_text(json.dumps({"equipment_appliances": {
                "run": "r1", "findings": [{"id": "equipment_appliances:x", "value": {"name": "TV", "quantity": 1, "location": ""},
                                           "conflicts": {}, "pages": [9], "citations": [], "evidence": "supported"}]}}))
            project = {"id": "j", "review_dir": str(root)}
            base = {"finding_id": "equipment_appliances:x", "decision": "accepted"}
            with patch.object(page_extraction_service, "rooms", return_value=["Shop"]):
                for value, message in (({"name": "TV", "heat_to_space_factor": 2}, "between 0 and 1"),
                                       ({"name": "TV", "rated_input_w": 0}, "above 0 W"),
                                       ({"name": "TV", "rated_input_w": "lots"}, "must be a number")):
                    with self.assertRaisesRegex(ValueError, message):
                        page_extraction_service.review(None, project, {**base, "value": value})
                state = page_extraction_service.review(None, project, {**base, "value": {"name": "TV", "quantity": 1, "room": "Shop",
                                                                                          "rated_input_w": "150", "heat_to_space_factor": 1}})
            [finding] = state["findings"]
            self.assertEqual((finding["in_calculation"], finding["accepted_heat_w"]), (True, 150))


if __name__ == "__main__":
    unittest.main()
