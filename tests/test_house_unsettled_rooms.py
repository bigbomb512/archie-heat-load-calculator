"""A house room whose use the evidence can't settle is not cooled until someone says it is; other buildings keep it
cooled and wait for confirmation (user decision 2026-11-19)."""

import unittest

from ai import room_use_resolution

HOUSE = {"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]}
OTHER = {"is_dwelling": False, "evidence": []}


def resolve(label, dwelling, evidence=None, reviewed=None):
    key = room_use_resolution.room_identity(label, "Ground")
    space = {"name": label, "level_name": "Ground", **(reviewed or {})}
    use_evidence = {key: {"contents": [], **({"air_conditioning_evidence": evidence} if evidence else {})}}
    return room_use_resolution.resolve({"spaces": [space]}, use_evidence=use_evidence, dwelling_context=dwelling)["records"][0]


class UnsettledHouseRoomTests(unittest.TestCase):
    def test_an_unsettled_house_room_is_not_cooled_with_a_note(self):
        for label in ("Store", "Storage", "Room 7"):
            with self.subTest(label=label):
                row = resolve(label, HOUSE)
                self.assertEqual((row["space_scope"], row["status"]), ("not_cooled", "excluded"))
                self.assertIn("Fix screen", row["rationale"])

    def test_the_same_rooms_in_another_building_stay_cooled_for_confirmation(self):
        self.assertEqual(resolve("Room 7", OTHER)["space_scope"], "comfort_hvac")
        self.assertEqual(resolve("Room 7", OTHER)["status"], "needs_review")
        self.assertEqual(resolve("Store", OTHER)["space_scope"], "comfort_hvac")

    def test_settled_house_rooms_are_unchanged(self):
        for label in ("Bedroom", "Living", "Kitchen"):
            with self.subTest(label=label):
                self.assertEqual(resolve(label, HOUSE)["space_scope"]
                                 in {"comfort_hvac", "comfort_hvac_with_process_exception"}, True)

    def test_a_house_room_the_drawings_show_air_conditioned_stays_cooled(self):
        self.assertEqual(resolve("Store", HOUSE, [{"page": 7, "excerpt": "DUCTED"}])["space_scope"], "comfort_hvac")


if __name__ == "__main__":
    unittest.main()
