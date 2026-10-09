"""A room's listed equipment never takes it below its typical allowance; the difference is a labelled top-up."""

import unittest

from ai import ai_preliminary, internal_gains_resolution, room_use_resolution

AREA = 100
# Hospitality: 25 W/m² × 0.8 heat to the room × 0.8 diversity.
ALLOWANCE_W = AREA * 25 * 0.8 * 0.8


def assembled(equipment):
    building = {"spaces": [{"name": "Cafe", "level_name": "Ground", "area_m2": AREA, "evidence": [{"page": 2, "excerpt": "CAFE"}]}]}
    room = {"kind": "room", "label": "Cafe", "level_name": "Ground", "area_m2": AREA, "preliminary_profile_id": "hospitality", "page": 2}
    proposal = {"rooms": [{**room, "equipment": equipment} if equipment else room]}
    use = room_use_resolution.resolve(building, proposal=proposal, source_fingerprints={})
    gains = internal_gains_resolution.resolve(building, proposal=proposal, room_use=use, pack=ai_preliminary.load_pack(),
                                              source_fingerprints={"proposal": "a"})
    out = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=use, internal_gains_resolution=gains)
    sources = out["material"]["requirements"]["zones"][0]["heat_sources"]
    heat = sum(row["quantity"] * row["watts"] * row["diversity_factor"] * row["space_gain_factor"] for row in sources)
    return sources, heat, out["materialized_fields"]


class EquipmentAllowanceTests(unittest.TestCase):
    def test_a_room_with_no_listed_equipment_uses_its_allowance(self):
        sources, heat, _ = assembled(None)
        self.assertEqual([row["name"] for row in sources], ["Preliminary profile equipment"])
        self.assertAlmostEqual(heat, ALLOWANCE_W, places=3)

    def test_a_few_small_listed_items_are_topped_up_to_the_allowance_and_it_is_recorded(self):
        sources, heat, ledger = assembled([{"name": "POS", "quantity": 1, "rated_input_w": 100, "heat_to_space_factor": 1.0,
                                            "evidence": [{"page": 5, "excerpt": "POS"}]}])
        names = [row["name"] for row in sources]
        self.assertIn("POS", names)                                           # the listed item stays visible
        self.assertTrue(any(name.startswith("Typical equipment allowance top-up") for name in names))
        self.assertAlmostEqual(heat, ALLOWANCE_W, places=1)
        top_up = next(row for row in ledger if row.get("field") == "equipment_allowance_top_up_w")
        self.assertGreater(top_up["value"], 0)
        self.assertIn("below the typical allowance", top_up["rationale"])

    def test_listed_items_above_the_allowance_are_used_as_they_are(self):
        sources, heat, ledger = assembled([{"name": "Oven", "quantity": 2, "rated_input_w": 3000, "heat_to_space_factor": 0.4,
                                            "evidence": [{"page": 5, "excerpt": "Oven schedule"}]}])
        self.assertEqual([row["name"] for row in sources], ["Oven"])
        self.assertGreater(heat, ALLOWANCE_W)
        self.assertFalse(any(row.get("field") == "equipment_allowance_top_up_w" for row in ledger))


if __name__ == "__main__":
    unittest.main()
