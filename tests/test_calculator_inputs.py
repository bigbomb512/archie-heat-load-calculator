#!/usr/bin/env python3

import unittest

from ai.calculator_inputs import assemble_calculator_inputs
from ai.hourly_loads import build_hourly_load_model
from ai.design_requirements import validate_design_requirements


def requirements():
    return validate_design_requirements({
        "indoor_cooling_setpoint_c": 24, "outdoor_summer_db_c": 35,
        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18, "outdoor_summer_wet_bulb_c": 24, "atmospheric_pressure_kpa": 101.325, "verification_status": "confirmed", "source": "Reviewed conditions"},
        "zones": [{"zone_id": "zone_001", "name": "Retail", "usage": "Retail", "source_room_labels": ["Retail"], "area_m2": 20, "occupancy": 10,
                   "heat_sources": [{"name": "Fridge", "quantity": 1, "watts": 1000, "kind": "refrigeration", "diversity_factor": 1, "space_gain_factor": 1, "verification_status": "confirmed", "source": "Equipment schedule"}],
                   "cooling_load": {"people_sensible_w_per_person": 75, "people_latent_w_per_person": 55, "people_diversity_factor": 1, "lighting_w_m2": 10, "lighting_diversity_factor": 1, "outside_air_lps": 100, "safety_factor": 1.1, "envelope_not_applicable": True, "envelope_surfaces": [], "verification_status": "confirmed", "source": "Cooling basis"}}],
    })


def scenario():
    return {"scenarios": [{"scenario_id": "summer", "title": "Summer", "mode": "cooling", "representative_month": "January", "day_type": "weekday", "status": "confirmed", "source": "Weather basis", "citations": [], "atmospheric_pressure_kpa": {"value": 101.325, "status": "confirmed", "source": "Weather basis", "citations": []}, "hours": [{"hour": i, "outdoor_dry_bulb_c": {"value": 35, "status": "confirmed", "source": "Weather basis", "citations": []}, "outdoor_wet_bulb_c": {"value": 24, "status": "confirmed", "source": "Weather basis", "citations": []}} for i in range(24)]}]}


class CalculatorInputTests(unittest.TestCase):
    def test_missing_room_inputs_are_blocked(self):
        model = build_hourly_load_model(requirements())
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"])
        self.assertIn(result["status"], {"blocked", "draft"})
        self.assertNotEqual(result["status"], "ready")
        self.assertTrue(any("schedule" in row["reason"] for row in result["issues"]))

    def test_research_cache_is_recorded_but_not_used_without_target(self):
        model = build_hourly_load_model(requirements())
        cache = {"schema_version": 1, "revision": 1, "records": [{"record_id": "default-1", "url": "https://example.test", "publisher": "Test", "retrieved_at": "2026-01-01T00:00:00+00:00", "content_hash": "x", "category": "occupancy_default", "value": 10, "unit": "people", "scope": {"location": "Sydney"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Reviewer", "expiry": "2099-01-01T00:00:00+00:00"}]}
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], research_cache=cache)
        self.assertEqual(result["research_defaults_available"][0]["record_id"], "default-1")
        self.assertEqual(model["rooms"], build_hourly_load_model(requirements())["rooms"])


if __name__ == "__main__":
    unittest.main()
