import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import ai_preliminary_service, web_app


class FakeWeb:
    @staticmethod
    def safe_link(path):
        return f"/artifact/{Path(path).name}"

    @staticmethod
    def update_project(_project):
        return None


class PreliminaryWorkspaceViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = {"id": "workspace-test", "review_dir": str(self.root)}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def test_workspace_view_is_small_and_default_view_keeps_full_artifacts(self):
        room = {"room_id": "room-bar", "name": "Bar", "area_m2": 25, "large_detail": "x" * 100_000}
        self.write("ai_preliminary_input_set.json", {
            "material": {"hourly_load_model": {"floors": [], "zones": [], "rooms": [room]}},
            "materialized_fields": [], "large_input_detail": "y" * 100_000,
        })
        self.write("ai_preliminary_model.json", {"topology": {"rooms": [room]}, "calculation_records": ["z" * 100_000]})
        self.write("hourly_ai_preliminary_load_report.json", {
            "label": "AI preliminary estimate", "included_scope_peak": {"design_total_kw": 12.3},
            "scenario_results": [{"rooms": [{"room_id": "room-bar", "name": "Bar", "loads": ["detailed"]}]}],
            "unresolved_room_inputs": [{"room_id": "room-bar", "room_name": "Bar", "component_type": "envelope",
                                         "component_id": "roof_solar", "component": "Roof sun — not assessed",
                                         "reason": "No cited horizontal solar profile."}],
            "known_exclusions": [{"room_id": "room-bar", "room_name": "Bar", "component_type": "envelope",
                                  "component_id": "internal_boundary", "component": "Internal boundary",
                                  "reason": "No external conduction."}], "review_queue": [], "confirmed_rooms": [],
        })

        compact = ai_preliminary_service.get(FakeWeb, self.project, "workspace")
        self.assertIn("settings", compact)
        self.assertIn("run", compact)
        self.assertIn("status", compact)
        self.assertIn("room_scope", compact)
        self.assertIn("candidates", compact["room_scope"])
        self.assertEqual(compact["hourly_ai_preliminary_load_report"]["included_scope_peak"]["design_total_kw"], 12.3)
        self.assertEqual(compact["hourly_ai_preliminary_load_report"]["room_names"], [{"room_id": "room-bar", "name": "Bar"}])
        self.assertEqual(compact["hourly_ai_preliminary_load_report"]["unresolved_room_inputs"], [
            {"room_id": "room-bar", "room_name": "Bar", "component_type": "envelope",
             "component": "Roof sun — not assessed", "component_id": "roof_solar",
             "reason": "No cited horizontal solar profile."}
        ])
        self.assertEqual(compact["hourly_ai_preliminary_load_report"]["known_exclusions"], [
            {"room_id": "room-bar", "room_name": "Bar", "component_type": "envelope",
             "component": "Internal boundary", "component_id": "internal_boundary", "reason": "No external conduction."}
        ])
        self.assertNotIn("input_set", compact)
        self.assertNotIn("model", compact)
        self.assertNotIn("scenario_results", compact["hourly_ai_preliminary_load_report"])
        self.assertLess(len(json.dumps(compact, indent=2).encode("utf-8")), 1_000_000)

        full = ai_preliminary_service.get(FakeWeb, self.project)
        self.assertIn("input_set", full)
        self.assertIn("model", full)
        self.assertIn("scenario_results", full["hourly_ai_preliminary_load_report"])

    def test_workspace_post_option_returns_compact_response(self):
        response = ai_preliminary_service.post(FakeWeb, self.project, {
            "action": "save_settings", "response_view": "workspace",
            "settings": {"saved_project_consent": True},
        })
        self.assertNotIn("input_set", response)
        self.assertNotIn("model", response)
        self.assertIn("room_scope", response)
        self.assertTrue(response["settings"]["saved_project_consent"])

    def test_get_api_passes_workspace_view_and_keeps_freshness_check_opt_in(self):
        default_request = SimpleNamespace(path="/api/ai-preliminary-model?project_id=workspace-test")
        with patch.object(web_app, "project_by_id", return_value=self.project), \
             patch.object(web_app, "ensure_review_dir"):
            default = web_app.api_ai_preliminary_model(default_request)
        self.assertIn("input_set", default)
        self.assertIn("model", default)

        request = SimpleNamespace(path="/api/ai-preliminary-model?project_id=workspace-test&view=workspace")
        with patch.object(web_app, "project_by_id", return_value=self.project), \
             patch.object(web_app, "ensure_review_dir"):
            response = web_app.api_ai_preliminary_model(request)
        self.assertEqual(response["status"], "freshness_pending")
        self.assertTrue(response["freshness_pending"])
        self.assertNotIn("input_set", response)
        self.assertNotIn("model", response)

        request.path += "&check_freshness=1"
        with patch.object(web_app, "project_by_id", return_value=self.project), \
             patch.object(web_app, "ensure_review_dir"), \
             patch.object(ai_preliminary_service, "_stale_reasons", return_value=["room_use_resolution"]):
            checked = web_app.api_ai_preliminary_model(request)
        self.assertEqual(checked["status"], "stale")
        self.assertFalse(checked["freshness_pending"])
        self.assertEqual(checked["stale_reasons"], ["room_use_resolution"])


if __name__ == "__main__":
    unittest.main()
