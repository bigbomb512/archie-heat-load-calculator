#!/usr/bin/env python3

import unittest

from ai.calculator_inputs import (
    _scope_for_room,
    assemble_calculator_inputs,
    materialize_cooling_payload,
    validate_project_context,
)
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
    def context(self):
        return {
            "schema_version": 1,
            "site": {"country": "AU", "locality": "Sydney", "state": "NSW", "climate_zone": "5", "source": "Project brief", "citations": []},
            "building_use": "retail",
            "room_uses": {"zone_001-room-1": {"use": "retail", "source": "Architect room label", "citations": []}},
            "conditioned_scope": {"status": "confirmed", "mode": "all_rooms", "room_ids": [], "source": "Client cooling brief", "citations": []},
            "reviewer": "Project owner",
        }

    def default_cache(self):
        bindings = [
            ("room.indoor_cooling_setpoint_c", 24, "C"),
            ("room.people_sensible_w_per_person", 75, "W/person"),
            ("room.people_latent_w_per_person", 55, "W/person"),
            ("room.people_diversity_factor", 0.8, ""),
            ("room.lighting_w_m2", 10, "W/m2"),
            ("room.lighting_diversity_factor", 0.9, ""),
            ("room.safety_factor", 1.1, ""),
            ("room.indoor_cooling_wet_bulb_c", 18, "C"),
            ("room.occupancy_density_per_m2", 0.5, "people/m2"),
            ("room.outside_air_lps_per_person", 5, "L/s/person"),
            ("room.outside_air_lps_per_m2", 1, "L/s/m2"),
            ("schedule.people", [1.0] * 24, "profile"),
            ("schedule.lighting", [1.0] * 24, "profile"),
            ("schedule.outside_air", [1.0] * 24, "profile"),
        ]
        return {"schema_version": 1, "revision": 1, "source_pack_version": "au-cooling-v1", "records": [{
            "record_id": "au-retail-defaults", "url": "https://www.abcb.gov.au/approved-pack", "publisher": "Approved pack",
            "retrieved_at": "2026-01-01T00:00:00+00:00", "content_hash": "pack-v1", "category": "occupancy_default",
            "value": "retail defaults", "unit": "various", "scope": {"country": "AU", "room_use": "retail"},
            "citation": "Approved retail source pack v1", "review_status": "approved", "reviewed_by": "Engineering lead",
            "expiry": "2099-01-01T00:00:00+00:00", "source_pack_version": "au-cooling-v1", "released": True,
            "bindings": [{"target": target, "value": value, "unit": unit} for target, value, unit in bindings],
        }]}

    def default_backed_model(self):
        model = build_hourly_load_model(requirements())
        room = model["rooms"][0]
        model["floors"][0]["verification_status"] = "confirmed"
        model["zones"][0]["verification_status"] = "confirmed"
        room.update({"mapping_status": "confirmed", "verification_status": "confirmed", "source": "Architect plan", "area_m2": 20, "occupancy": None, "indoor_cooling_setpoint_c": None})
        room["cooling_load"].update({
            "people_sensible_w_per_person": None, "people_latent_w_per_person": None, "people_diversity_factor": None,
            "lighting_w_m2": None, "lighting_diversity_factor": None, "outside_air_lps": None, "safety_factor": None, "source": "",
        })
        room["cooling_load_conditions"].update({"indoor_cooling_wet_bulb_c": None, "source": ""})
        room["schedule_assignments"].update({"people": "", "lighting": "", "outside_air": ""})
        for component in room["unapproved_components"]:
            component.update({"calculation_status": "not_present_confirmed", "verification_status": "confirmed", "source": "Reviewed project declaration"})
        return model

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

    def test_proposed_research_records_remain_visible_but_ineligible(self):
        model = self.default_backed_model()
        cache = self.default_cache()
        cache["records"][0]["review_status"] = "proposed"
        cache["records"][0]["released"] = False
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], research_cache=cache, project_context=self.context())
        self.assertTrue(result["research_defaults_unavailable"])
        self.assertFalse(any(item["resolution_status"] == "approved_default" for item in result["resolved_inputs"]))

    def test_approved_defaults_create_a_stable_derived_snapshot(self):
        model = self.default_backed_model()
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], research_cache=self.default_cache(), project_context=self.context())
        repeated = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], research_cache=self.default_cache(), project_context=self.context())
        self.assertEqual(result["status"], "review_ready")
        self.assertEqual(result["input_fingerprint"], repeated["input_fingerprint"])
        self.assertIn("zone_001-room-1", result["included_room_ids"])
        self.assertTrue(any(item["resolution_status"] == "derived_evidence" and item["target"].endswith(".occupancy") for item in result["resolved_inputs"]))
        self.assertTrue(any(item["resolution_status"] == "approved_default" and "schedule_assignments.people" in item["target"] for item in result["resolved_inputs"]))
        payload, schedules, _scenarios = materialize_cooling_payload(result)
        self.assertEqual(payload["rooms"][0]["occupancy"], 10)
        self.assertEqual(payload["rooms"][0]["cooling_load"]["outside_air_lps"], 70)
        self.assertTrue(schedules["schedules"])
        self.assertIsNone(model["rooms"][0]["occupancy"])

    def test_cited_override_beats_an_approved_default(self):
        target = "rooms.zone_001-room-1.cooling_load.lighting_w_m2"
        overrides = {"schema_version": 1, "revision": 1, "records": [{
            "override_id": "retail-lighting", "target": target, "value": 12, "unit": "W/m2", "source": "Client lighting schedule",
            "reviewer": "Project owner", "citations": [{"reference": "LS-01", "page": 2, "excerpt": "12 W/m2"}],
        }]}
        result = assemble_calculator_inputs(self.default_backed_model(), {"schedules": []}, scenario(), ["summer"], research_cache=self.default_cache(), project_context=self.context(), overrides=overrides)
        row = next(item for item in result["resolved_inputs"] if item["target"] == target)
        self.assertEqual(row["resolution_status"], "project_override")
        self.assertEqual(row["value"], 12)

    def test_missing_conditioned_scope_blocks_public_assembly(self):
        result = assemble_calculator_inputs(self.default_backed_model(), {"schedules": []}, scenario(), ["summer"], research_cache=self.default_cache(), project_context={"schema_version": 1})
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("Conditioned scope is missing" in item["reason"] for item in result["issues"]))

    def test_context_preserves_explicit_conditioning_system(self):
        context = self.context()
        context["room_uses"]["zone_001-room-1"]["conditioning_system"] = "comfort_hvac"
        checked = validate_project_context(context)
        self.assertEqual(checked["room_uses"]["zone_001-room-1"]["conditioning_system"], "comfort_hvac")

    def test_context_rejects_unknown_conditioning_system(self):
        context = self.context()
        context["room_uses"]["zone_001-room-1"]["conditioning_system"] = "unknown_system"
        with self.assertRaises(ValueError):
            validate_project_context(context)

    def test_context_preserves_explicit_default_profile_mapping(self):
        context = self.context()
        context["room_uses"]["zone_001-room-1"].update({
            "default_profile": "class_6_shop",
            "default_profile_source": "Project owner profile mapping",
            "default_profile_citations": [{"reference": "Drawing 6 profile decision", "page": None, "excerpt": "Use the Class 6 shop daily profile for this room."}],
        })
        checked = validate_project_context(context)
        self.assertEqual(checked["room_uses"]["zone_001-room-1"]["default_profile"], "class_6_shop")
        self.assertEqual(_scope_for_room(checked, "zone_001-room-1")["room_use"], "class_6_shop")
        self.assertEqual(_scope_for_room(checked, "zone_001-room-1")["declared_room_use"], "retail")

    def test_default_profile_mapping_requires_its_own_source(self):
        context = self.context()
        context["room_uses"]["zone_001-room-1"]["default_profile"] = "class_6_shop"
        with self.assertRaises(ValueError):
            validate_project_context(context)

    def test_valid_but_unactivated_fusion_fact_does_not_override_a_default(self):
        fusion = {"facts": [{
            "fact_id": "unreviewed-lighting", "target_path": "rooms.zone_001-room-1.cooling_load.lighting_w_m2", "value": 99,
            "unit": "W/m2", "source": {"page": 3}, "citations": [], "extraction_confidence": "high",
            "validation_status": "valid", "activation_status": "proposed",
        }]}
        result = assemble_calculator_inputs(self.default_backed_model(), {"schedules": []}, scenario(), ["summer"], fusion=fusion, research_cache=self.default_cache(), project_context=self.context())
        row = next(item for item in result["resolved_inputs"] if item["target"].endswith("lighting_w_m2"))
        self.assertEqual(row["resolution_status"], "approved_default")
        self.assertEqual(row["value"], 10)

    def test_active_pdf_calculation_candidate_is_project_evidence(self):
        model = self.default_backed_model()
        target = "rooms.zone_001-room-1.area_m2"
        fusion = {"calculation_input_evidence": {"candidates": [{
            "candidate_id": "calc-area-1", "target_path": target, "status": "active", "value": 24,
            "unit": "m²", "source": {"page": 20, "drawing_number": "202", "excerpt": "Retail AREA: 24 m²"},
            "confidence": "high",
        }]}}
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], fusion=fusion, research_cache=self.default_cache(), project_context=self.context())
        row = next(item for item in result["resolved_inputs"] if item["target"] == target)
        self.assertEqual(row["resolution_status"], "project_evidence")
        self.assertEqual(row["value"], 24)

    def test_high_risk_geometry_is_never_defaulted(self):
        model = self.default_backed_model()
        cache = self.default_cache()
        cache["records"][0]["bindings"].append({"target": "room.area_m2", "value": 999, "unit": "m2"})
        model["rooms"][0]["area_m2"] = None
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], research_cache=cache, project_context=self.context())
        area = next(item for item in result["resolved_inputs"] if item["target"].endswith(".area_m2"))
        self.assertEqual(area["resolution_status"], "blocked")
        self.assertIsNone(area["value"])

    def test_direct_pdf_area_normalises_square_metre_unit(self):
        model = self.default_backed_model()
        target = "rooms.zone_001-room-1.area_m2"
        fusion = {"calculation_input_evidence": {"candidates": [{
            "candidate_id": "calc-area-unit", "target_path": target, "status": "active", "value": 24,
            "unit": "m²", "source": {"page": 20, "drawing_number": "202", "excerpt": "AREA: 24 m²"},
            "confidence": "high",
        }]}}
        result = assemble_calculator_inputs(model, {"schedules": []}, scenario(), ["summer"], fusion=fusion, research_cache=self.default_cache(), project_context=self.context())
        area = next(item for item in result["resolved_inputs"] if item["target"] == target)
        self.assertEqual(area["resolution_status"], "project_evidence")
        self.assertEqual(area["unit"], "m2")


if __name__ == "__main__":
    unittest.main()
