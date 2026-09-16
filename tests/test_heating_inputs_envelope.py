#!/usr/bin/env python3
"""Heating room inputs, setpoints, and envelope conduction.

Synthetic fixtures only. Covers checklist items "heating room inputs and
setpoints" and "heating envelope conduction"; the remaining heating items
(outside air and infiltration, internal-gain credits, safety factors,
reports) are deliberately still blocked.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.design_requirements import (
    empty_design_requirements, validate_design_requirements,
    validate_heating_load_conditions,
)
from ai.heat_loads import heating_envelope_load
from ai.hourly_loads import (
    build_hourly_load_model, calculate_hourly_load_report,
    calculate_heating_room_hours, room_heating_static_missing,
    validate_hourly_load_model,
)

SOURCE = "Synthetic unit-test fixture; not project evidence"


def surface(**overrides):
    row = dict(surface_id="north", kind="opaque_wall", orientation="N", area_m2=10,
               u_value_w_m2k=0.5, solar_design_w_m2=0, solar_gain_factor=0,
               shading_factor=0, verification_status="confirmed", source=SOURCE)
    row.update(overrides)
    return row


def requirements_data(**overrides):
    data = {
        "indoor_cooling_setpoint_c": 24, "outdoor_summer_db_c": 35,
        "indoor_heating_setpoint_c": 21, "outdoor_winter_db_c": 2,
        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18, "outdoor_summer_wet_bulb_c": 24,
                                    "atmospheric_pressure_kpa": 101.325,
                                    "verification_status": "confirmed", "source": SOURCE},
        "heating_load_conditions": {"outdoor_winter_db_c": 2, "verification_status": "confirmed",
                                    "source": SOURCE},
        "zones": [{
            "zone_id": "zone_001", "name": "Retail", "usage": "Retail", "area_m2": 20, "occupancy": 10,
            "ceiling_height_mm": 3000, "heat_sources": [],
            "cooling_load": {"people_sensible_w_per_person": 75, "people_latent_w_per_person": 55,
                             "people_diversity_factor": 1, "lighting_w_m2": 10,
                             "lighting_diversity_factor": 1, "outside_air_lps": 100,
                             "safety_factor": 1.1, "envelope_not_applicable": False,
                             "verification_status": "confirmed", "source": SOURCE,
                             "envelope_surfaces": [surface()]},
        }],
    }
    data.update(overrides)
    return validate_design_requirements(data)


def heating_room(requirements=None):
    requirements = requirements or requirements_data()
    model = build_hourly_load_model(requirements)
    room = model["rooms"][0]
    room.update(name="Synthetic room", source=SOURCE)
    return room


def winter_scenario(outdoor_db=2):
    def number(value):
        return dict(value=value, status="confirmed", source=SOURCE, citations=[])
    return dict(scenario_id="jul-weekday", title="July weekday", mode="heating",
                representative_month="July", day_type="weekday", status="confirmed",
                source=SOURCE, citations=[], atmospheric_pressure_kpa=number(101.325),
                hours=[dict(hour=h, outdoor_dry_bulb_c=number(outdoor_db),
                            outdoor_wet_bulb_c=number(outdoor_db - 1)) for h in range(24)])


class HeatingRoomInputs(unittest.TestCase):
    def test_requirements_carry_heating_load_conditions(self):
        self.assertIn("heating_load_conditions", empty_design_requirements())
        conditions = requirements_data()["heating_load_conditions"]
        self.assertEqual(conditions["outdoor_winter_db_c"], 2)
        self.assertEqual(conditions["verification_status"], "confirmed")

    def test_heating_conditions_validation_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            validate_heating_load_conditions({"outdoor_winter_db_c": "cold"})
        with self.assertRaises(ValueError):
            validate_heating_load_conditions({"verification_status": "approved"})
        with self.assertRaises(ValueError):
            validate_heating_load_conditions([])

    def test_model_seeds_room_heating_setpoint_from_zone_then_project(self):
        room = heating_room()
        self.assertEqual(room["indoor_heating_setpoint_c"], 21)
        zoned = requirements_data()
        zoned["zones"][0]["indoor_heating_setpoint_c"] = 18
        self.assertEqual(heating_room(zoned)["indoor_heating_setpoint_c"], 18)

    def test_model_seeds_winter_condition_from_project_when_conditions_blank(self):
        requirements = requirements_data(heating_load_conditions={
            "outdoor_winter_db_c": None, "verification_status": "provisional", "source": SOURCE})
        self.assertEqual(heating_room(requirements)["heating_load_conditions"]["outdoor_winter_db_c"], 2)

    def test_heating_inputs_survive_model_validation(self):
        requirements = requirements_data()
        model = validate_hourly_load_model(build_hourly_load_model(requirements))
        room = model["rooms"][0]
        self.assertEqual(room["indoor_heating_setpoint_c"], 21)
        self.assertEqual(room["heating_load_conditions"]["outdoor_winter_db_c"], 2)
        self.assertEqual(model["schema_version"], 5)

    def test_missing_setpoint_or_winter_condition_blocks(self):
        room = heating_room()
        room["indoor_heating_setpoint_c"] = None
        self.assertIn("indoor heating setpoint", room_heating_static_missing(room))
        room = heating_room()
        room["heating_load_conditions"]["outdoor_winter_db_c"] = None
        self.assertIn("outdoor winter dry-bulb", room_heating_static_missing(room))
        room = heating_room()
        room["heating_load_conditions"]["source"] = ""
        self.assertIn("heating-load conditions source", room_heating_static_missing(room))

    def test_winter_condition_at_or_above_setpoint_blocks(self):
        room = heating_room()
        room["heating_load_conditions"]["outdoor_winter_db_c"] = 21
        self.assertTrue(any("below the indoor heating setpoint" in reason
                            for reason in room_heating_static_missing(room)))

    def test_complete_heating_inputs_clear_every_blocker(self):
        self.assertEqual(room_heating_static_missing(heating_room()), [])

    def test_fabric_gaps_block_heating_like_cooling(self):
        room = heating_room()
        room["cooling_load"]["envelope_surfaces"] = []
        room["cooling_load"]["envelope_not_applicable"] = False
        self.assertIn("envelope surfaces or internal-room declaration",
                      room_heating_static_missing(room))
        room["cooling_load"]["envelope_not_applicable"] = True
        self.assertEqual(room_heating_static_missing(room), [])

    def test_missing_u_value_or_adjacent_boundary_blocks(self):
        room = heating_room()
        room["cooling_load"]["envelope_surfaces"] = [surface(u_value_w_m2k=None)]
        self.assertIn("north U-value", room_heating_static_missing(room))
        room = heating_room()
        room["cooling_load"]["envelope_surfaces"] = [
            surface(boundary_method="fixed_adjacent_temperature", boundary_temperature_c=None)]
        self.assertIn("north adjacent boundary temperature", room_heating_static_missing(room))


class HeatingEnvelopeConduction(unittest.TestCase):
    def test_conduction_is_a_positive_loss_in_winter(self):
        row = heating_envelope_load([surface()], 2, 21)
        self.assertEqual(row["name"], "heating_envelope")
        self.assertEqual(row["total_kw"], round(10 * 0.5 * (21 - 2) / 1000, 4))
        self.assertGreater(row["total_kw"], 0)
        self.assertEqual(row["latent_kw"], 0)

    def test_conduction_is_signed_when_outdoor_exceeds_the_setpoint(self):
        self.assertLess(heating_envelope_load([surface()], 30, 21)["total_kw"], 0)

    def test_fixed_adjacent_boundary_overrides_outdoor_temperature(self):
        row = heating_envelope_load(
            [surface(boundary_method="fixed_adjacent_temperature", boundary_temperature_c=15)], -5, 21)
        self.assertEqual(row["inputs"]["surfaces"][0]["boundary_temperature_c"], 15)
        self.assertEqual(row["total_kw"], round(10 * 0.5 * (21 - 15) / 1000, 4))

    def test_surfaces_sum_and_are_reported_individually(self):
        row = heating_envelope_load([surface(), surface(surface_id="roof", area_m2=20)], 2, 21)
        self.assertEqual(len(row["inputs"]["surfaces"]), 2)
        self.assertEqual(row["total_kw"], round(sum(item["loss_kw"] for item in row["inputs"]["surfaces"]), 4))

    def test_no_solar_or_latent_is_applied(self):
        row = heating_envelope_load([surface(solar_design_w_m2=800, solar_gain_factor=1, shading_factor=1)], 2, 21)
        self.assertEqual(row["total_kw"], round(10 * 0.5 * (21 - 2) / 1000, 4))

    def test_empty_fabric_is_zero_not_an_error(self):
        self.assertEqual(heating_envelope_load([], 2, 21)["total_kw"], 0)


class HeatingScenarioIntegration(unittest.TestCase):
    def test_every_hour_is_calculated_and_peaks_resolve_ties(self):
        room = heating_room()
        result = calculate_heating_room_hours(winter_scenario(), room, {})
        self.assertEqual(len(result["hours"]), 24)
        self.assertEqual(result["peak"]["tied_hours"], list(range(24)))
        self.assertEqual(result["peak"]["display_hour"], 0)
        self.assertEqual(result["peak"]["scope"], "envelope_conduction_only")

    def test_result_is_labelled_partial_and_never_a_complete_heating_load(self):
        result = calculate_heating_room_hours(winter_scenario(), heating_room(), {})
        self.assertEqual(result["hours"][0]["scope"], "envelope_conduction_only")
        self.assertTrue(any("not a complete room heating load" in warning
                            for warning in result["warnings"]))

    def test_calculation_does_not_mutate_the_room(self):
        room = heating_room()
        before = deepcopy(room)
        calculate_heating_room_hours(winter_scenario(), room, {})
        self.assertEqual(room, before)

    def test_heating_report_stays_unpublishable(self):
        requirements = requirements_data()
        model = build_hourly_load_model(requirements)
        model["rooms"][0].update(name="Synthetic room", source=SOURCE)
        report = calculate_hourly_load_report(
            requirements, {"schedules": []}, {"scenarios": [winter_scenario()]},
            model, ["jul-weekday"])
        scenario = report["scenario_results"][0]
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(scenario["scope_summary"]["heating_scope"], "envelope_conduction_only")
        for reason in ("heating outside-air and infiltration losses are not implemented",
                       "heating internal-gain credit rules are not implemented",
                       "heating safety factors are not implemented"):
            self.assertIn(reason, scenario["blocked_reasons"])
        self.assertEqual(len(scenario["rooms"][0]["hours"]), 24)
        self.assertFalse(scenario["zones"])
        self.assertFalse(scenario["included_scope_peak"])

    def test_heating_scenario_with_unreviewed_weather_is_blocked(self):
        requirements = requirements_data()
        model = build_hourly_load_model(requirements)
        model["rooms"][0].update(name="Synthetic room", source=SOURCE)
        scenario = winter_scenario()
        scenario["hours"][5]["outdoor_dry_bulb_c"] = {"value": None, "status": "missing", "source": "", "citations": []}
        report = calculate_hourly_load_report(
            requirements, {"schedules": []}, {"scenarios": [scenario]}, model, ["jul-weekday"])
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["scenario_results"][0]["rooms"])

    def test_cooling_scenarios_are_unaffected_by_the_heating_path(self):
        requirements = requirements_data()
        model = build_hourly_load_model(requirements)
        model["rooms"][0].update(name="Synthetic room", source=SOURCE)
        cooling = winter_scenario(35)
        cooling["mode"] = "cooling"
        report = calculate_hourly_load_report(
            requirements, {"schedules": []}, {"scenarios": [cooling]}, model, ["jul-weekday"])
        self.assertNotIn("heating_scope", report["scenario_results"][0].get("scope_summary", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
