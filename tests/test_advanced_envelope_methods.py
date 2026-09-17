import unittest

from ai.envelope_method_gates import (
    dynamic_thermal_mass_gate_is_approved,
    empty_dynamic_thermal_mass_method_gate,
    empty_solar_radiation_method_gate,
    solar_radiation_gate_is_approved,
    validate_dynamic_thermal_mass_method_gate,
    validate_solar_radiation_method_gate,
)
from ai.solar_radiation import absorbed_solar_gain_kw, validate_solar_radiation_source
from ai.thermal_mass import calculate_first_order_rc, validate_rc_surface


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Approved method"}]


class AdvancedEnvelopeTests(unittest.TestCase):
    def source(self):
        return validate_solar_radiation_source({
            "source_id": "surface-solar-1", "location": "Sydney", "timezone": "Australia/Sydney",
            "date": "2026-01-15", "surface_orientation": "N", "method": "Cited surface-plane schedule",
            "source": "Approved solar schedule", "citations": citation("SOL-1"),
            "hours": [{"hour": hour, "irradiance_w_m2": 100 if hour == 12 else 0} for hour in range(24)],
        })

    def test_gate_defaults_are_not_approved(self):
        self.assertFalse(dynamic_thermal_mass_gate_is_approved(empty_dynamic_thermal_mass_method_gate()))
        self.assertFalse(solar_radiation_gate_is_approved(empty_solar_radiation_method_gate()))

    def test_approved_gate_requires_engineer_and_citation(self):
        gate = empty_dynamic_thermal_mass_method_gate()
        gate.update({"approval_status": "approved", "engineer_name": "Engineer", "engineer_credential": "CPEng", "approved_at": "2026-09-16T00:00:00Z", "method_citation": "RC method", "citations": citation("RC-1")})
        self.assertTrue(dynamic_thermal_mass_gate_is_approved(validate_dynamic_thermal_mass_method_gate(gate)))

    def test_solar_source_requires_all_hours_and_is_fingerprinted(self):
        source = self.source()
        self.assertEqual(len(source["hours"]), 24)
        self.assertTrue(source["fingerprint"])
        self.assertAlmostEqual(absorbed_solar_gain_kw(source, 12, 10, 0.5), 0.5)

    def test_rc_calculation_is_signed_and_auditable(self):
        surface = validate_rc_surface({
            "surface_id": "wall-1", "area_m2": 10, "r_exterior_m2k_w": 1,
            "r_interior_m2k_w": 1, "capacitance_kj_m2k": 10,
            "solar_absorptance": 0.5, "source": "Construction schedule", "citations": citation("W-1"),
        })
        result = calculate_first_order_rc(surface, 35, 24, 24, irradiance_w_m2=100)
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["surface_id"], "wall-1")
        self.assertIn("formula", result)
        self.assertGreater(result["state_temperature_c"], 24)


if __name__ == "__main__":
    unittest.main()
