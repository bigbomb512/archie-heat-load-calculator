"""Read-only Results summary of a saved heating report."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from backend import heating_summary_service as service


def report(status="review_ready", peak=True):
    room_peak = {"design_total_kw": 2.5, "hour": 6, "components": {
        "heating_envelope": {"sensible_kw": 1.0},
        "heating_glazing_conduction": {"sensible_kw": 0.4},
        "heating_outside_air": {"sensible_kw": 0.8},
        "heating_internal_gain_credit": {"sensible_kw": -0.2}}}
    governing = {"design_total_kw": 3.0, "final_design_total_kw": 3.3, "hour": 6, "display_hour": 6}
    return {"status": status, "included_scope_peak": governing if peak else {},
            "blocked_reasons": ["Winter weather is incomplete"] if status == "blocked" else [],
            "scenario_results": [{"scenario_id": "winter", "title": "Winter design",
                                  "included_scope_peak": governing if peak else {},
                                  "scope_summary": {"blocked_rooms": [{"room_id": "r2", "reasons": ["No setpoint"]}]},
                                  "rooms": [{"room_id": "r1", "name": "Living", "status": "review_ready",
                                             "peak": room_peak, "hours": [{"hour": 6, "inputs": {
                                                 "outdoor_db_c": -2, "setpoint_c": 20}}]},
                                            {"room_id": "r2", "name": "Study", "status": "blocked",
                                             "peak": {}, "hours": []}]}]}


class HeatingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"review_dir": str(self.root)}
        self.stale = []
        self.web = SimpleNamespace(
            hourly_paths=lambda project: {"heating_report": self.root / "hourly_heating_load_report.json",
                                          "heating_method_gate": self.root / "heating_method_gate.json"},
            existing_path=lambda value, fallback: Path(value or fallback) if Path(value or fallback).exists() else None,
            current_hourly_heating_load_report_path=lambda project: self.root / "hourly_heating_load_report.json" if not self.stale else None,
            heating_report_stale_reasons=lambda project: self.stale,
            empty_heating_method_gate=lambda: {"approval_status": "placeholder"},
            heating_gate_summary=lambda gate: {"status": gate["approval_status"], "message": "Gate status"},
            load_json=lambda path: json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, value):
        (self.root / "hourly_heating_load_report.json").write_text(json.dumps(value), encoding="utf-8")

    def test_not_calculated(self):
        result = service.get(self.web, self.project)
        self.assertEqual(result["status"], "not_calculated")
        self.assertIn("heating report has to be calculated", result["next_step"])
        self.assertIn("named HVAC engineer", result["next_step"])
        self.assertIsNone(result["total_kw"])
        self.assertEqual(result["rooms"], [])

    def test_stale_hides_loads(self):
        self.save(report())
        self.stale = ["heating room inputs changed"]
        result = service.get(self.web, self.project)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["stale_reasons"], self.stale)
        self.assertIsNone(result["total_kw"])
        self.assertEqual(result["rooms"], [])

    def test_blocked_hides_loads_and_explains_reasons(self):
        self.save(report("blocked", peak=False))
        result = service.get(self.web, self.project)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Winter weather", result["reason"])
        self.assertIsNone(result["total_kw"])
        self.assertEqual(result["rooms"], [])

    def test_current_placeholder_gate_is_draft(self):
        self.save(report(status="draft"))
        result = service.get(self.web, self.project)
        self.assertEqual(result["status"], "current")
        self.assertTrue(result["draft"])
        self.assertEqual(result["total_kw"], 3.3)
        self.assertEqual(result["peak"], {"scenario_id": "winter", "title": "Winter design", "hour": 6})
        self.assertEqual((result["outdoor_design_c"], result["indoor_design_c"]), (-2.0, 20.0))
        self.assertEqual(result["rooms"][0]["components"]["Walls, roof and floor conduction"], 1.0)
        self.assertEqual(result["rooms"][0]["components"]["Internal gain credit"], -0.2)
        self.assertEqual(result["blocked_rooms"][0]["name"], "Study")

    def test_current_approved_gate_is_review_ready(self):
        self.save(report())
        (self.root / "heating_method_gate.json").write_text('{"approval_status":"approved"}', encoding="utf-8")
        result = service.get(self.web, self.project)
        self.assertEqual(result["status"], "current")
        self.assertFalse(result["draft"])
        self.assertTrue(result["gate"]["approved"])


if __name__ == "__main__":
    unittest.main()
