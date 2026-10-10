"""The supply fan's heat is added to the result: a percentage of the room sensible heat (outside air excluded)."""

import unittest

from ai import ai_preliminary, internal_gains_resolution, room_use_resolution


class FanHeatTests(unittest.TestCase):
    def test_fan_heat_is_a_share_of_the_room_sensible_heat_and_the_design_total_includes_it(self):
        building = {"spaces": [{"name": "Shop", "level_name": "Ground", "area_m2": 150, "evidence": [{"page": 2, "excerpt": "SHOP"}]}]}
        proposal = {"rooms": [{"kind": "room", "label": "Shop", "level_name": "Ground", "area_m2": 150, "preliminary_profile_id": "retail", "page": 2}]}
        use = room_use_resolution.resolve(building, proposal=proposal, source_fingerprints={})
        gains = internal_gains_resolution.resolve(building, proposal=proposal, room_use=use, pack=ai_preliminary.load_pack(),
                                                  source_fingerprints={"proposal": "a"})
        report = ai_preliminary.calculate(ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=use,
                                                                  internal_gains_resolution=gains))
        peak = report["included_scope_peak"]
        fan = peak["components"]["fan_heat"]["sensible_kw"]
        room_sensible = peak["sensible_kw"] - fan - peak["components"]["outside_air"]["sensible_kw"]
        self.assertAlmostEqual(fan, room_sensible * 0.032, places=3)
        self.assertAlmostEqual(peak["total_kw"], sum(row["total_kw"] for row in peak["components"].values()), places=3)
        self.assertIn("AIRAH Technical Handbook", report["fan_heat"]["source"])
        # With a safety factor, the allowance and the design total are recalculated with the fan heat in.
        with_factor, _heat = ai_preliminary._with_fan_heat(
            {"sensible_kw": 10.0, "total_kw": 12.0, "raw_coincident_total_kw": 12.0, "final_design_total_kw": 13.2, "safety_factor": 1.1,
             "components": {"outside_air": {"sensible_kw": 2.0}}}, {"percent_of_room_sensible": 5.0}, 1.1)
        self.assertEqual((with_factor["components"]["fan_heat"]["total_kw"], with_factor["raw_coincident_total_kw"],
                          with_factor["final_design_total_kw"], with_factor["safety_allowance_kw"]), (0.4, 12.4, 13.64, 1.24))


if __name__ == "__main__":
    unittest.main()
