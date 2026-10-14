"""Phase 1 automatically resolves its room scope and saves an independent base-load report."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai.ai_preliminary import assemble
from backend import ai_preliminary_service


class PhaseOneCalculationTests(unittest.TestCase):
    def test_workspace_report_exposes_surface_audit_and_automation_note(self):
        compact = ai_preliminary_service._workspace_report({
            "phase1_automation_note": "One evidence task could not be read.",
            "phase1_surface_audit": [{"room_id": "room-a", "surface_id": "wall-a", "total_kw": 1.2}],
        })
        self.assertEqual(compact["phase1_automation_note"], "One evidence task could not be read.")
        self.assertEqual(compact["phase1_surface_audit"][0]["surface_id"], "wall-a")

    def test_missing_area_and_geometry_diagnostics_merge_by_room(self):
        input_set = assemble(
            {"spaces": [{"name": "Kitchen", "level_name": "Level 1", "area": "",
                         "evidence": [{"page": 33, "excerpt": "Kitchen"}]}]},
            preliminary_proposal={"rooms": [], "issues": [{
                "component": "room area", "label": "Kitchen", "level": "Level 1",
                "scope": "comfort_hvac_with_process_exception",
                "reason": "No validated area or structured boundary is available.",
                "evidence": [{"page": 33, "excerpt": "Kitchen partition evidence"}],
            }]},
            allow_area_fallbacks=False,
        )

        self.assertEqual(len(input_set["excluded_spaces"]), 1)
        excluded = input_set["excluded_spaces"][0]
        self.assertEqual(excluded["room_name"], "Kitchen")
        self.assertEqual(excluded["scope"], "comfort_hvac_with_process_exception")
        self.assertEqual(len(excluded["reasons"]), 2)
        self.assertIn("Kitchen partition evidence", [row.get("excerpt") for row in excluded["evidence"]])
        self.assertIn("Kitchen", [row.get("excerpt") for row in excluded["evidence"]])

    def test_base_report_auto_includes_resolved_rooms_without_manual_confirmation(self):
        with TemporaryDirectory() as directory:
            project = {"id": "phase-one", "review_dir": directory}
            paths = ai_preliminary_service._paths(project)
            input_set = assemble({"spaces": [{"id": "shop", "name": "Retail tenancy", "level_name": "Ground",
                                                "area": "42 m2", "confidence": "high",
                                                "evidence": [{"page": 3, "excerpt": "Retail tenancy 42 m²"}]}]})
            paths["current_set"].write_text(json.dumps(input_set), encoding="utf-8")
            for name in ("design_requirements.json", "schedule_library.json", "design_day_scenarios.json", "hourly_load_model.json"):
                (Path(directory) / name).write_text("{}", encoding="utf-8")

            with patch.object(ai_preliminary_service, "_stale_reasons", return_value=[]):
                report = ai_preliminary_service._calculate(None, project, scope="phase1_base_room")

            confirmation = json.loads(paths["room_scope"].read_text(encoding="utf-8"))
            saved_report = json.loads(paths["base_report"].read_text(encoding="utf-8"))
            self.assertEqual(confirmation["reviewer"], "AI Phase 1 scope resolver")
            self.assertEqual([(row["label"], row["include"]) for row in confirmation["rows"]], [("Retail tenancy", True)])
            self.assertEqual(report["calculation_phase"], 1)
            self.assertEqual(saved_report["report_type"], "hourly_ai_preliminary_phase1_base_room_load")
            self.assertEqual(saved_report["confirmed_rooms"][0]["area_m2"], 42)
            self.assertIsNotNone(saved_report["included_scope_peak"].get("design_total_kw"))
            self.assertEqual(len(report["warnings"]), len(set(report["warnings"])))
            self.assertTrue(any("Phase 1 includes resolved envelope loads only" in warning
                                for warning in report["warnings"]))
            self.assertFalse(any("Envelope and listed air-side loads were not assessed" in warning
                                 for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
