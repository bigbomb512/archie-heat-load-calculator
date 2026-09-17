#!/usr/bin/env python3
"""Compatibility smoke checks for Max's heating-envelope contribution.

The authoritative heating workflow now lives in ``ai.heating_loads`` on main;
this test intentionally exercises that API instead of the superseded mixed
cooling/heating path that existed on Max.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.heating_loads import heating_air_load, heating_conduction, heating_glazing_conduction
from ai.glazing_gate import empty_glazing_method_gate


SOURCE = "Synthetic unit-test fixture; not project evidence"


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed heating basis"}]


class HeatingEnvelopeCompatibility(unittest.TestCase):
    def test_opaque_conduction_uses_indoor_minus_boundary(self):
        result, blocked = heating_conduction([{
            "surface_id": "wall-1",
            "boundary_method": "external",
            "area_m2": 10,
            "u_value_w_m2k": 0.5,
            "source": SOURCE,
            "citations": citation("E-01"),
        }], 2, 21)
        self.assertFalse(blocked)
        self.assertEqual(result["sensible_kw"], 0.095)

    def test_glazing_requires_approved_gate_and_uses_opening_area(self):
        room = {"cooling_load": {"glazing_surfaces": [{
            "surface_id": "window-1",
            "boundary_method": "external",
            "opening_width_m": 2,
            "opening_height_m": 1,
            "opening_quantity": 1,
            "window": {"u_value_w_m2k": 2.0},
            "source": SOURCE,
            "citations": citation("W-01"),
        }]}}
        result, blocked = heating_glazing_conduction(room, -5, 20, empty_glazing_method_gate())
        self.assertEqual(result["sensible_kw"], 0)
        self.assertTrue(blocked)

    def test_outside_air_heating_is_positive_when_indoor_is_warmer(self):
        result = heating_air_load(10, 20, 15, 0, 0, 101.325)
        self.assertGreater(result["sensible_kw"], 0)


if __name__ == "__main__":
    unittest.main()
