"""Accepted PDF review findings set calculation inputs: people, lighting watts and equipment items."""

import unittest
from unittest.mock import patch

from backend import finding_inputs_service as inputs, need_answers_service, page_extraction_service

ROOMS = {"rooms": [{"label": "Kitchen", "level": "Unassigned level"}, {"label": "Shop", "level": "Unassigned level"}]}


class FindingInputTests(unittest.TestCase):
    def setUp(self):
        self.options = patch.object(need_answers_service, "options", return_value=ROOMS)
        self.override = patch.object(need_answers_service, "_internal_gains_override")
        self.options.start()
        self.set_input = self.override.start()

    def tearDown(self):
        patch.stopall()

    def finding(self, subskill, field):
        return {"id": f"{subskill}:{field}:0", "subskill_id": subskill, "field": field}

    def test_seats_become_the_rooms_people_and_a_finding_without_a_room_asks_for_one(self):
        seats = self.finding("occupancy_seating", "occupancy")
        result = inputs.apply(None, {}, seats, {"room_id": "room-use:unassigned-level:shop", "count": 140}, None, "Sam")
        self.assertEqual((result["applied"], result["room"], result["summary"]), (True, "Shop", "Shop: 140 people."))
        room, field, value = self.set_input.call_args.args[2:5]
        self.assertEqual((room["label"], field, value), ("Shop", "occupancy_count", 140.0))
        with self.assertRaisesRegex(ValueError, "Choose the room"):
            inputs.apply(None, {}, seats, {"room_id": None, "count": 140}, None, "Sam")
        with self.assertRaisesRegex(ValueError, "Choose the room"):           # a zone the app doesn't know
            inputs.apply(None, {}, seats, {"room_id": "pdf-1:201:central-buffet-zone", "count": 140}, None, "Sam")
        picked = inputs.apply(None, {}, seats, {"room_id": None, "count": 140}, "shop", "Sam")
        self.assertEqual(picked["room"], "Shop")
        empty = inputs.apply(None, {}, seats, {"room_id": None, "count": None}, "Shop", "Sam")
        self.assertFalse(empty["applied"])

    def test_a_rooms_lighting_is_the_total_of_its_accepted_fittings_and_unrated_ones_are_named(self):
        fittings = self.finding("lighting_evidence", "lighting")
        others = [({"quantity": 12, "wattage_w": 30}, "Shop"), ({"quantity": 4, "wattage_w": None}, "Shop"),
                  ({"quantity": 50, "wattage_w": 10}, "Kitchen")]
        result = inputs.apply(None, {}, fittings, {"room_id": None, "quantity": 8, "wattage_w": 26}, "Shop", "Sam", accepted_lighting=others)
        self.assertTrue(result["applied"])
        self.assertEqual(self.set_input.call_args.args[3:5], ("lighting_load_w", 12 * 30 + 8 * 26))   # the kitchen's aren't added
        self.assertIn("1 accepted fitting type(s) here have no quantity or wattage", result["summary"])
        self.set_input.reset_mock()
        unrated = inputs.apply(None, {}, fittings, {"room_id": None, "quantity": 13, "wattage_w": None}, "Kitchen", "Sam")
        self.assertFalse(unrated["applied"])
        self.set_input.assert_not_called()

    def test_an_equipment_item_accepts_the_matching_listed_item_with_its_room_quantity_and_rating(self):
        listed = {"findings": [
            {"id": "f-e21", "value": {"code": "E21", "name": "UB FRIDGE", "quantity": 10, "rated_power": ""}},
            {"id": "f-e01", "value": {"code": "E01", "name": "POS", "quantity": 1, "rated_power": ""},
             "decided_value": {"code": "E01", "name": "x", "rated_input_w": 120, "heat_to_space_factor": 1.0}},
            {"id": "f-e06", "value": {"code": "E06", "name": "COMBI OVEN", "quantity": 1, "rated_power": "", "under_hood": True}},
            {"id": "f-tv", "value": {"code": "", "name": "Television", "quantity": None, "rated_power": ""}}]}
        equipment = self.finding("equipment_evidence", "equipment")
        with patch.object(page_extraction_service, "status", return_value=listed), \
                patch.object(page_extraction_service, "review") as review:
            fridges = inputs.apply(None, {}, equipment, {"room_id": "room-use:unassigned-level:kitchen", "equipment_id": "pdf-1:004:E21",
                                                         "name": "Underbench fridge", "quantity": 9}, None, "Sam")
            accepted = review.call_args.args[2]
            self.assertTrue(fridges["applied"])
            self.assertEqual((accepted["finding_id"], accepted["decision"]), ("f-e21", "accepted"))   # matched by code
            self.assertEqual((accepted["value"]["room"], accepted["value"]["quantity"]), ("Kitchen", 9))
            self.assertEqual((accepted["value"]["rated_input_w"], accepted["value"]["heat_to_space_factor"]), (350, 1.0))
            self.assertIn("typical rating", fridges["summary"])
            tv = inputs.apply(None, {}, equipment, {"room_id": None, "equipment_id": "pdf-1:206:tv", "name": "television",
                                                    "quantity": 3, "rated_input_w": 150}, "Shop", "Sam")
            self.assertEqual(review.call_args.args[2]["finding_id"], "f-tv")           # matched by name
            self.assertIn("rating from the drawings", tv["summary"])
            pos = inputs.apply(None, {}, equipment, {"room_id": None, "equipment_id": "pdf-1:004:E01", "name": "POS", "quantity": 1}, "Shop", "Sam")
            kept = review.call_args.args[2]["value"]
            self.assertEqual((kept["name"], kept["rated_input_w"]), ("POS", 120.0))     # the reading's name; the earlier rating
            self.assertIn("rating entered earlier", pos["summary"])
            review.reset_mock()
            # Cooking has no typical rating (it waits on AIRAH DA09): not counted until its rating is answered.
            oven = inputs.apply(None, {}, equipment, {"room_id": "room-use:unassigned-level:kitchen", "equipment_id": "pdf-1:004:E06",
                                                      "name": "Combi oven", "quantity": 1}, None, "Sam")
            self.assertFalse(oven["applied"])
            self.assertIn("Answer its rating in the needs list", oven["summary"])
            unknown = inputs.apply(None, {}, equipment, {"room_id": None, "equipment_id": "x:y:z", "name": "Speakers", "quantity": 8}, "Shop", "Sam")
            self.assertFalse(unknown["applied"])
            review.assert_not_called()

    def test_only_people_lighting_and_equipment_findings_set_inputs(self):
        self.assertTrue(inputs.applies({"subskill_id": "occupancy_seating", "field": "occupancy"}))
        self.assertFalse(inputs.applies({"subskill_id": "occupancy_seating", "field": "occupancy.room_id"}))   # a missing-field row
        self.assertFalse(inputs.applies({"subskill_id": "room_identity_use", "field": "rooms"}))


if __name__ == "__main__":
    unittest.main()
