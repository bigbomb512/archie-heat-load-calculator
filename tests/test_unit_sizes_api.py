"""The Results page's unit sizes (/api/unit-sizes) are the contractor's report's own, from the saved final load, and are
never shown for a final load that is out of date or not calculated."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import contractor_report, web_app
from tests.test_contractor_report import PLAN_ROOMS, REPORT


class Request:
    path = "/api/unit-sizes?project_id=job-1"

    def _identity(self):
        return None


class UnitSizesApiTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = {"id": "job-1", "review_dir": str(self.root)}
        (self.root / "hourly_ai_preliminary_load_report.json").write_text(json.dumps(REPORT), encoding="utf-8")
        (self.root / "ai_preliminary_input_set.json").write_text(json.dumps(
            {"room_use_resolution": {"dwelling_context": {"is_dwelling": True}}}), encoding="utf-8")
        for target, value in ((web_app, "project_by_id"), (web_app.security, "require_project_role")):
            patcher = patch.object(target, value, return_value=self.project if value == "project_by_id" else "viewer")
            patcher.start()
            self.addCleanup(patcher.stop)

    def get(self, status, total=9.0):
        plan = {"status": status, "rooms": PLAN_ROOMS,
                "final_report": {"included_scope_peak": {"final_design_total_kw": total} if total is not None else {}}}
        with patch("backend.job_plan_service.get", return_value=plan):
            return web_app.api_unit_sizes(Request())

    def test_current_result_gives_the_reports_ducted_and_per_room_sizes(self):
        result = self.get({"results_ready": True, "result_stale": False})
        self.assertEqual(result["status"], "calculated")
        self.assertTrue(result["dwelling"])
        self.assertEqual(result["ducted"], contractor_report.ducted(9.0, True))
        self.assertEqual(result["rooms"], contractor_report.room_sizing(REPORT, PLAN_ROOMS))
        self.assertEqual([row["name"] for row in result["rooms"]], ["Living", "Bed 1", "Hall"])
        config = contractor_report.sizes()
        self.assertEqual(result["rule"], config["rule"])
        self.assertEqual(result["wall_split_kw"], config["wall_split_kw"])
        self.assertEqual(result["ducted_kw"], config["ducted_kw"])
        self.assertEqual(len(result["sources"]), 2)

    def test_out_of_date_result_shows_no_sizes(self):
        result = self.get({"results_ready": True, "result_stale": True})
        self.assertEqual(result["status"], "stale")
        self.assertNotIn("rooms", result)
        self.assertNotIn("ducted", result)

    def test_no_final_load_shows_no_sizes(self):
        for status, total in (({"results_ready": False}, 9.0), ({"results_ready": True}, None)):
            result = self.get(status, total)
            self.assertEqual(result["status"], "not_calculated")
            self.assertNotIn("rooms", result)


if __name__ == "__main__":
    unittest.main()
