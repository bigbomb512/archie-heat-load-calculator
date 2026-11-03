"""A space that isn't cooled in a house (garage, laundry, bathroom …) is cooled when the drawings show air-conditioning
in it (user decision 2026-10-30)."""

import unittest

from ai import room_use_resolution
from backend import room_use_resolution_service

DWELLING = {"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]}


def resolve(label, evidence=None, dwelling=DWELLING):
    key = room_use_resolution.room_identity(label, "Ground")
    use_evidence = {key: {"contents": [], **({"air_conditioning_evidence": evidence} if evidence else {})}}
    return room_use_resolution.resolve({"spaces": [{"name": label, "level_name": "Ground"}]}, use_evidence=use_evidence,
                                       dwelling_context=dwelling)["records"][0]


class AirConditioningTextTests(unittest.TestCase):
    def test_clear_air_conditioning_text_is_found_line_by_line(self):
        words = [(3, 10, 5, "DUCTED"), (4, 30, 1, "SUPPLY"), (4, 30, 20, "AIR"), (5, 1, 1, "RA"), (5, 1, 9, "GRILLE"),
                 (6, 1, 1, "SPLIT"), (6, 1, 9, "SYSTEM")]
        self.assertEqual([row["excerpt"] for row in room_use_resolution_service._air_conditioning_text(words)],
                         ["DUCTED", "SUPPLY AIR", "RA GRILLE", "SPLIT SYSTEM"])

    def test_smoke_alarms_and_power_points_are_not_air_conditioning(self):
        words = [(1, 1, 1, "SA"), (1, 9, 1, "RA"), (1, 20, 1, "GPO"), (2, 1, 1, "AC"), (2, 1, 9, "POWER")]
        self.assertEqual(room_use_resolution_service._air_conditioning_text(words), [])


class CooledByTheDrawingsTests(unittest.TestCase):
    def test_a_garage_shown_air_conditioned_is_cooled_with_the_citation(self):
        plain = resolve("GARAGE")
        self.assertEqual((plain["taxonomy_id"], plain["space_scope"]), ("garage", "not_cooled"))
        cooled = resolve("GARAGE", [{"page": 7, "excerpt": "DUCTED"}])
        self.assertEqual((cooled["taxonomy_id"], cooled["space_scope"]), ("garage", "comfort_hvac"))
        self.assertIn("p7: DUCTED", cooled["rationale"])
        self.assertIn({"page": 7, "excerpt": "DUCTED", "method": "air_conditioning_text"}, cooled["evidence"])
        self.assertEqual(resolve("LAUNDRY", [{"page": 7, "excerpt": "A/C"}])["space_scope"], "comfort_hvac")

    def test_an_outdoor_space_stays_outside_and_a_shop_ignores_the_evidence(self):
        self.assertEqual(resolve("BALCONY", [{"page": 7, "excerpt": "A/C"}])["space_scope"], "not_cooled")
        shop = resolve("STORE", [{"page": 7, "excerpt": "DUCTED"}], dwelling={"is_dwelling": False, "evidence": []})
        self.assertEqual(shop["taxonomy_id"], "storage")


if __name__ == "__main__":
    unittest.main()
