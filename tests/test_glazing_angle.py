"""Glass sun by incidence angle and inside blinds (ai/glazing_angle.py, 12 November 2026).

The angle and blind values are ASHRAE Handbook—Fundamentals 2001's (reproduced in Arici, MEEM 4200 Ch. 7, Tables 7-3
and 7-4); these tests pin them to the table, check the sun's incidence angle against plain geometry, and follow a blind
from the drawings to the room's glass gain.
"""
from math import acos, cos, degrees, radians
import sys
from pathlib import Path
import unittest

from ai import glazing_angle, house_windows, sun

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_draft_reference_rooms as reference      # noqa: E402  (the reference rooms' harness)


class AngleTableTests(unittest.TestCase):
    def test_the_modifier_is_the_table_ratio_at_each_tabulated_angle(self):
        for kind, row in glazing_angle.GLASS.items():
            for angle, shgc in zip(glazing_angle.ANGLES, row["shgc"]):
                self.assertAlmostEqual(glazing_angle.angle_modifier(kind, angle), shgc / row["shgc"][0], places=9, msg=kind)
            self.assertEqual(glazing_angle.angle_modifier(kind, 90), 0.0)
            self.assertAlmostEqual(glazing_angle.diffuse_modifier(kind), row["diffuse"] / row["shgc"][0], places=9)
        # Single clear glass: 0.84 at 40°, 0.82 at 50°, so 0.83 at 45° (relative to 0.86).
        self.assertAlmostEqual(glazing_angle.angle_modifier("single_clear", 45), 0.83 / 0.86, places=9)
        self.assertAlmostEqual(glazing_angle.angle_modifier("single_clear", 20), (1 + 0.84 / 0.86) / 2, places=9)

    def test_the_glass_type_follows_u_value_and_shgc(self):
        self.assertEqual(glazing_angle.glass_type(5.8, 0.45), "single_clear")      # the pack's default glass
        self.assertEqual(glazing_angle.glass_type(2.9, 0.76), "double_clear")
        self.assertEqual(glazing_angle.glass_type(1.9, 0.45), "double_low_e")
        self.assertEqual(glazing_angle.glass_type(None, None), "single_clear")


class BlindTests(unittest.TestCase):
    def test_table_values_and_the_least_credit_when_not_shown(self):
        self.assertEqual(glazing_angle.iac({"kind": "venetian", "colour": "light"}, "double_clear")[0], 0.66)
        self.assertEqual(glazing_angle.iac({"kind": "roller", "colour": "white", "opacity": "opaque"}, "single_clear", 0.86)[0], 0.40)
        self.assertEqual(glazing_angle.iac({"kind": "roller", "opacity": "translucent"}, "double_clear")[0], 0.46)
        value, note = glazing_angle.iac({"kind": "venetian"}, "single_clear", 0.86)
        self.assertEqual(value, 0.75)                                   # colour not shown: medium, the higher IAC
        self.assertIn("least credit", note)
        self.assertEqual(glazing_angle.iac({"kind": "roller"}, "single_clear", 0.86)[0], 0.82)       # opaque dark
        self.assertEqual(glazing_angle.iac({"kind": "venetian", "colour": "light"}, "single_clear", 0.45)[0], 0.78)  # heat absorbing row
        self.assertEqual(glazing_angle.iac({"kind": "curtain"}, "double_clear")[0], 1.0)
        self.assertEqual(glazing_angle.iac(None, "double_clear"), (1.0, ""))

    def test_schedule_descriptions(self):
        self.assertEqual(house_windows.internal_shade("AWNING WINDOW, DG, WHITE BLOCKOUT ROLLER BLIND"),
                         {"kind": "roller", "colour": "white", "opacity": "opaque",
                          "evidence": ["AWNING WINDOW, DG, WHITE BLOCKOUT ROLLER BLIND"]})
        self.assertEqual(house_windows.internal_shade("Sliding door with integral blinds")["kind"], "venetian")
        self.assertEqual(house_windows.internal_shade("Fixed glazing, sheer curtains")["kind"], "curtain")
        self.assertIsNone(house_windows.internal_shade("AWNING WINDOW, ALUMINIUM FRAME, DG"))


class IncidenceTests(unittest.TestCase):
    SITE = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}

    def test_the_incidence_angle_is_the_angle_between_the_sun_and_the_glass(self):
        day = sun.design_days(self.SITE["latitude"], self.SITE["longitude"], self.SITE["timezone"])[1]
        hours = sun.surface_hours(day, 90.0, 270.0)
        for row, out in zip(day, hours):
            if row["altitude_deg"] <= 5:
                continue
            altitude, relative = radians(row["altitude_deg"]), radians(row["azimuth_deg"] - 270.0)
            expected = degrees(acos(max(-1.0, min(1.0, cos(altitude) * cos(relative)))))
            self.assertAlmostEqual(out["incidence_deg"], expected, delta=0.05)
        self.assertIn("incidence_deg", hours[12])

    def test_glass_totals_scale_each_part(self):
        day = sun.design_days(self.SITE["latitude"], self.SITE["longitude"], self.SITE["timezone"])[1]
        hours = sun.surface_hours(day, 90.0, 270.0)
        glass = sun.glass_totals(self.SITE, 1, 270.0, "double_clear")
        plain = sun.surface_totals(self.SITE, 1, 90.0, 270.0)
        for hour in range(24):
            row = hours[hour]
            expected = (row["direct_w_m2"] * row["sunlit_fraction"] * glazing_angle.angle_modifier("double_clear", row["incidence_deg"])
                        + row["diffuse_w_m2"] * glazing_angle.diffuse_modifier("double_clear"))
            self.assertAlmostEqual(glass[hour], expected, delta=0.02)
            self.assertLessEqual(glass[hour], plain[hour] + 0.01)


class BlindThroughTheDraftTests(unittest.TestCase):
    def test_a_blind_on_the_window_reduces_the_glass_gain_by_its_iac(self):
        case = next(row for row in reference.SUITE["cases"] if row["case_id"] == "A_west_shop")
        plain_model, plain = reference.run(case)
        shaded_proposal = reference.proposal(case)
        shaded_proposal["openings"][0]["internal_shade"] = {"kind": "venetian", "colour": "light", "evidence": ["test"]}
        model = reference.ai_preliminary.assemble({"spaces": []}, preliminary_proposal=shaded_proposal, site_location=reference.SITE,
                                                   design_weather_table=reference.DESIGN_TABLE, monthly_climate_table=reference.MONTHLY_TABLE)
        shaded = reference.ai_preliminary.calculate(model)
        before = reference.room_hour(plain, 1, 16, "Shop")["glazing_solar"]["sensible_kw"]
        after = reference.room_hour(shaded, 1, 16, "Shop")["glazing_solar"]["sensible_kw"]
        # The pack's glass (U 5.8, SHGC 0.45) is single glazing below SHGC 0.70: the heat-absorbing row, light venetian 0.78.
        self.assertAlmostEqual(after / before, 0.78, places=3)
        glazing = model["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["glazing_surfaces"][0]
        self.assertEqual(glazing["window"]["internal_shading_factor"], 0.78)
        self.assertEqual(glazing["angle_dependence"]["ashrae_id"], "1a")
        self.assertTrue(any(row["component"] == "inside blinds" for row in model["phase1_assumptions"]))
        self.assertEqual(plain_model["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["glazing_surfaces"][0]["window"]["internal_shading_factor"], 1.0)


class OpeningValidationTests(unittest.TestCase):
    def test_the_geometry_step_keeps_a_recognised_blind_and_drops_anything_else(self):
        from backend import reviewer_room_geometry_service as geometry
        self.assertEqual(geometry._opening_internal_shade({"kind": "Roller", "colour": "white", "opacity": "opaque", "evidence": ["x"]}),
                         {"kind": "roller", "colour": "white", "opacity": "opaque", "evidence": ["x"]})
        self.assertEqual(geometry._opening_internal_shade({"kind": "venetian", "colour": "purple"}), {"kind": "venetian", "evidence": []})
        self.assertIsNone(geometry._opening_internal_shade({"kind": "shutter"}))


if __name__ == "__main__":
    unittest.main()
