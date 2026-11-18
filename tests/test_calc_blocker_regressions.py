import tempfile
import unittest
import json
from pathlib import Path

from ai.ai_preliminary import assemble
from ai.autonomous_tasks import validate_site_reply
from ai.hourly_loads import validate_hourly_load_model
from backend.model_input_resolution_service import _hydrate_preliminary_artifacts


class CalculationBlockerRegressions(unittest.TestCase):
    def test_glazing_only_host_has_no_orphan_wall_schedule_and_validates(self):
        proposal = {
            "rooms": [{"kind": "room", "label": "Sitting", "level_name": "Ground", "area_m2": 20,
                       "preliminary_profile_id": "residential", "confidence": 0.8, "page": 1}],
            "surfaces": [{"surface_key": "wall-window", "label": "Window wall", "owner_room_label": "Sitting",
                          "owner_level_name": "Ground", "physical_type": "wall", "thermal_role": "external",
                          "external_exposure": "external", "gross_area_m2": 8, "glazing_only_host": True,
                          "orientation": "N", "opening_coverage": "complete", "page": 1, "confidence": 0.9}],
            "openings": [{"opening_key": "window", "label": "W01", "owner_room_label": "Sitting",
                          "owner_level_name": "Ground", "host_surface_key": "wall-window", "external_exposure": "external",
                          "orientation": "N", "opening_area_m2": 8, "shading_category": "unshaded", "page": 1,
                          "confidence": 0.9}],
        }
        model = assemble({"spaces": []}, preliminary_proposal=proposal)["material"]["hourly_load_model"]
        room = model["rooms"][0]
        self.assertEqual(room["cooling_load"]["envelope_surfaces"], [])
        self.assertEqual(len(room["cooling_load"]["glazing_surfaces"]), 1)
        self.assertNotIn("wall-window", room["schedule_assignments"]["solar"])
        self.assertIn(room["cooling_load"]["glazing_surfaces"][0]["surface_id"], room["schedule_assignments"]["solar"])
        validate_hourly_load_model(model)

    def test_hydration_retains_validator_reason_for_calculation_error(self):
        valid = assemble({"spaces": []})["material"]
        valid["hourly_load_model"]["rooms"] = [{
            "room_id": "room-sitting", "name": "Sitting", "zone_id": "zone-main", "mapping_status": "inferred",
            "verification_status": "provisional", "source": "Plan", "citations": [], "area_m2": 20,
            "occupancy": 0, "indoor_cooling_setpoint_c": 24, "heat_sources": [],
            "cooling_load": {"people_sensible_w_per_person": 0, "people_latent_w_per_person": 0,
                             "people_diversity_factor": 1, "lighting_w_m2": 0, "lighting_diversity_factor": 1,
                             "outside_air_lps": 0, "safety_factor": 1, "envelope_not_applicable": True,
                             "envelope_surfaces": [], "verification_status": "provisional", "source": "Plan"},
            "cooling_load_conditions": {}, "schedule_assignments": {"people": "", "lighting": "", "outside_air": "",
                "infiltration": "", "equipment": {}, "solar": {"ghost-wall": "solar"}},
            "unapproved_components": [],
        }]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {"requirements": root / "requirements.json", "schedules": root / "schedules.json",
                     "scenarios": root / "scenarios.json", "model": root / "model.json"}
            _, missing = _hydrate_preliminary_artifacts(None, {}, paths, {"material": valid, "input_fingerprint": "test"})
        item = next(row for row in missing if row["key"] == "hourly_load_model")
        self.assertIn("Room Sitting assigns schedules to unknown surfaces", item["validation_error"])
        self.assertIn("Room Sitting", item["message"])

    def test_site_reply_preserves_lot_number_and_removes_bare_lot(self):
        packet = {"excerpts": [
            {"page": 1, "text": "139a Burns Road, Turramurra Lot"},
            {"page": 20, "text": "Site is 139a Burns Road, Turramurra, Lot 2 DP581858", "clue_type": "site_location_fact"},
        ]}
        result = validate_site_reply(packet, json.dumps({"site": {"text": "139a Burns Road, Turramurra Lot", "page": 1,
                                                         "kind": "street_address"}}))
        self.assertEqual(result["site"]["text"], "139a Burns Road, Turramurra Lot 2 DP581858")
        bare = validate_site_reply({"excerpts": [{"page": 1, "text": "139a Burns Road, Turramurra Lot"}]},
                                   json.dumps({"site": {"text": "139a Burns Road, Turramurra Lot", "page": 1,
                                               "kind": "street_address"}}))
        self.assertEqual(bare["site"]["text"], "139a Burns Road, Turramurra")


if __name__ == "__main__":
    unittest.main()
