"""The assumption pack's numbers sit in physically sensible ranges.

A cheap guard for the kind of slip that put a quarter of the sun through every wall (2026-10-25): each bound names its
source, or says it is only a sanity range. A value outside a range isn't necessarily wrong for a real project, but in
the preliminary pack it needs a reason before it ships.
"""

import json
from pathlib import Path
import unittest

PACK = json.loads((Path(__file__).resolve().parents[1] / "config" / "ai_preliminary_assumption_pack.json").read_text(encoding="utf-8"))
ENVELOPE = PACK["preliminary_envelope"]
PROFILES = PACK["profiles"]


class PackPlausibilityTests(unittest.TestCase):
    def within(self, label, value, low, high, source):
        self.assertTrue(isinstance(value, (int, float)) and low <= value <= high,
                        f"{label} = {value!r} is outside {low}–{high} ({source}).")

    def test_sun_on_walls_and_roofs_is_a_few_percent(self):
        ratio = ENVELOPE["opaque_solar_absorptance_over_ho_m2k_w"]
        self.within("absorptance / outside coefficient", ratio, 0.026, 0.052,
                    "ASHRAE Handbook—Fundamentals sol-air: 0.026 light to 0.052 m²K/W dark")
        for name, profile in PROFILES.items():
            for key in ("wall_u_w_m2k", "roof_u_w_m2k"):
                self.within(f"{name} {key}", profile[key], 0.1, 3.0, "sanity range for building constructions")
                self.within(f"{name} {key} × α/h_o (share of incident sun reaching the room)", profile[key] * ratio, 0.002, 0.06,
                            "follows from the two ranges above; a quarter of the sun (0.25) is glass-like, not a wall")

    def test_glass(self):
        for name, profile in PROFILES.items():
            self.within(f"{name} glazing U", profile["glazing_u_w_m2k"], 1.0, 6.5, "sanity range: low-e double to single clear")
            self.within(f"{name} SHGC", profile["shgc"], 0.2, 0.87, "sanity range: tinted/low-e to single clear glass")
        self.within("glazing frame fraction", ENVELOPE["glazing_frame_fraction"], 0.0, 0.4, "sanity range")

    def test_sun_profiles(self):
        self.within("flat-roof sun peak", max(ENVELOPE["horizontal_solar_profile_w_m2"]), 900, 1100,
                    "clear-sky summer global horizontal irradiance at Australian latitudes, sanity range")
        for direction, values in ENVELOPE["cardinal_solar_profiles_w_m2"].items():
            self.within(f"{direction} façade sun peak", max(values), 100, 900, "sanity range for clear-sky vertical irradiance")

    def test_people_air_lighting_and_equipment(self):
        for room_use, row in PACK["people_activity"]["by_room_use"].items():
            self.within(f"{room_use} people sensible", row["sensible_w"], 60, 140, "AIRAH Technical Handbook heat from people, sanity range")
            self.within(f"{room_use} people latent", row["latent_w"], 25, 250, "AIRAH Technical Handbook heat from people, sanity range")
        for name, profile in PROFILES.items():
            self.within(f"{name} outside air per person", profile["outside_air_lps_person"], 7.5, 12.5,
                        "AS 1668.2 typical rates, sanity range")
            self.within(f"{name} outside air per m²", profile["outside_air_lps_m2"], 0.0, 1.5, "sanity range")
            self.within(f"{name} people per m²", profile["occupancy_density_people_m2"], 0.01, 1.5, "sanity range")
            self.within(f"{name} lighting", profile["lighting_w_m2"], 3, 25, "sanity range for lighting power density")
            self.within(f"{name} equipment", profile["equipment_w_m2"], 1, 60, "sanity range")
            for key in ("people_diversity", "lighting_diversity", "equipment_diversity", "equipment_space_gain"):
                self.within(f"{name} {key}", profile[key], 0.3, 1.0, "sanity range for diversity factors")

    def test_fan_heat_and_no_safety_allowance(self):
        self.within("fan heat, % of room sensible", PACK["fan_heat"]["percent_of_room_sensible"], 1, 10,
                    "AIRAH Technical Handbook fan heat gain, sanity range")
        self.assertEqual(PACK["scenario"]["safety_factor"], 1.0, "No safety allowance by default (user decision 2026-10-25).")


if __name__ == "__main__":
    unittest.main()
