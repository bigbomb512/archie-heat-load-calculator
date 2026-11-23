"""Kitchen-only labels follow the exhaust decision unless cooling is confirmed."""

import unittest

from ai import room_use_resolution


def resolve(label, dwelling=False, ac_evidence=None, reviewer=False):
    building = {"spaces": [{"name": label, "level_name": "Ground", "area_m2": 20,
                            "evidence": [{"page": 1, "excerpt": label}]}]}
    context = {"is_dwelling": dwelling, "evidence": [{"page": 1, "excerpt": "BASIX"}] if dwelling else []}
    room_id = room_use_resolution.room_identity(label, "Ground")
    evidence = {room_id: {"contents": [], "air_conditioning_evidence": ac_evidence or []}}
    existing = None
    if reviewer:
        original = room_use_resolution.resolve(building, dwelling_context=context)
        taxonomy_id = original["records"][0]["taxonomy_id"]
        existing = room_use_resolution.apply_override(original, room_id, taxonomy_id, "Engineer",
                                                      "Kitchen air-conditioning confirmed on review.")
    return room_use_resolution.resolve(building, existing=existing, use_evidence=evidence,
                                       dwelling_context=context)["records"][0]


class KitchenScopeTests(unittest.TestCase):
    def test_house_and_commercial_kitchens_are_excluded_for_exhaust(self):
        for dwelling in (True, False):
            with self.subTest(dwelling=dwelling):
                row = resolve("Kitchen", dwelling)
                self.assertEqual((row["space_scope"], row["status"]), ("not_cooled", "excluded"))
                self.assertIn("exhaust hood draws the cooled air straight out", row["rationale"])

    def test_kitchen_only_label_variants_are_excluded(self):
        for label in ("KITCH", "Kitchen (2)", "Commercial Kitchen"):
            with self.subTest(label=label):
                self.assertEqual(resolve(label)["space_scope"], "not_cooled")

    def test_shared_room_labels_remain_cooled(self):
        for dwelling in (True, False):
            for label in ("Kitchen/Dining", "Living & Kitchen"):
                with self.subTest(dwelling=dwelling, label=label):
                    self.assertIn(resolve(label, dwelling)["space_scope"],
                                  {"comfort_hvac", "comfort_hvac_with_process_exception"})

    def test_kitchenette_and_butlers_pantry_follow_their_existing_use_rules(self):
        for dwelling in (True, False):
            for label in ("Kitchenette", "Butler's pantry"):
                with self.subTest(dwelling=dwelling, label=label):
                    row = resolve(label, dwelling)
                    self.assertNotIn("exhaust hood", row["rationale"])
                    if label == "Kitchenette" and dwelling:
                        self.assertEqual(row["space_scope"], "not_cooled")  # unsettled house room
                    else:
                        self.assertIn(row["space_scope"], {"comfort_hvac", "comfort_hvac_with_process_exception"})

    def test_kitchen_air_conditioning_on_drawings_keeps_it_cooled(self):
        for dwelling in (True, False):
            with self.subTest(dwelling=dwelling):
                row = resolve("Kitchen", dwelling, ac_evidence=[{"page": 7, "excerpt": "Kitchen supply air"}])
                self.assertIn(row["space_scope"], {"comfort_hvac", "comfort_hvac_with_process_exception"})

    def test_reviewer_override_keeps_a_kitchen_cooled(self):
        for dwelling in (True, False):
            with self.subTest(dwelling=dwelling):
                row = resolve("Kitchen", dwelling, reviewer=True)
                self.assertIn(row["space_scope"], {"comfort_hvac", "comfort_hvac_with_process_exception"})
                self.assertEqual(row["classification_origin"], "contractor_override")

    def test_confirmed_scope_override_keeps_a_kitchen_cooled(self):
        building = {"spaces": [{"name": "Kitchen", "level_name": "Ground", "area_m2": 20}]}
        proposal = {"rooms": [{"kind": "room", "label": "Kitchen", "level_name": "Ground",
                              "calculation_scope_override": "comfort_hvac_with_process_exception"}]}
        row = room_use_resolution.resolve(building, proposal=proposal)["records"][0]
        self.assertEqual(row["space_scope"], "comfort_hvac_with_process_exception")


if __name__ == "__main__":
    unittest.main()
