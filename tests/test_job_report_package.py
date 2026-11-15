"""Results' "Create the report" packages the job's own final load (set 16, 2026-11-07: it looked for the engineer
workflow's cooling report, which a job from the drawings never has, and always failed)."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import job_service, web_app


class Request:
    path = "/api/report-package"

    def _identity(self):
        return None


class JobReportPackageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = {"id": "job-1", "review_dir": str(self.root)}
        report = {"status": "draft", "included_scope_peak": {"final_design_total_kw": 31.3, "month": "January", "hour": 15},
                  "scenario_results": [], "input_fingerprints": {}}
        (self.root / "hourly_ai_preliminary_load_report.json").write_text(json.dumps(report), encoding="utf-8")
        for target, value in ((web_app, "project_by_id"), (web_app.security, "require_project_role")):
            patcher = patch.object(target, value, return_value=self.project if value == "project_by_id" else "owner")
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, body, state):
        plan = {"status": {"found_site": "26 Todman Avenue, West Pymble NSW 2073"}, "final_report": {
            "status": "draft", "known_exclusions": [{"component": "Supply duct heat gain", "reason": "Not calculated."}],
            "design_conditions_basis": {"design_day": {"location": "Sydney", "state": "NSW", "source": "AIRAH DA9"}},
            "seasonal": {"months": [{"month": "January", "hourly_kw": [0.0] * 15 + [31.3] + [0.0] * 8}]},
            "included_scope_peak": {"final_design_total_kw": 31.3, "month": "January", "hour": 15, "sensible_kw": 31.1,
                                    "latent_kw": 0.2, "components": {"glazing_solar": {"total_kw": 10.3}, "people": {"total_kw": 0.4}},
                                    "household_counted_once": {"people_counted": 4}}},
            "rooms": [{"name": "Living room", "level": "Ground Floor", "area_m2": 46.3, "final_kw": 11.6, "final_w_m2": 250.5, "kind": "comfort"},
                      {"name": "Garage", "level": "Basement", "area_m2": 41.6, "final_kw": None, "kind": "not_cooled"},
                      {"name": "Dining", "level": "", "area_m2": 40.8, "final_kw": None, "kind": "not_a_room"}]}
        gaps = {"items": [{"text": "Uses typical lighting."}, {"text": "Uses typical lighting."}, {"text": "The site address isn't confirmed."}]}
        with patch.object(web_app, "read_json_body", return_value={"project_id": "job-1", **body}), \
                patch("backend.productization._chrome_binary", return_value=""), \
                patch.object(job_service, "status", return_value=state), \
                patch.object(job_service, "job_setup", return_value={"name": "Todman Ave"}), \
                patch("backend.job_plan_service.get", return_value=plan), \
                patch("backend.result_status_service.gather", return_value=gaps):
            return web_app.api_save_report_package(Request())

    def test_the_jobs_current_final_load_is_packaged(self):
        result = self.post({"report_type": "cooling", "source": "job"}, {"results_ready": True, "result_stale": False})
        self.assertEqual(result["status"], "current")
        self.assertTrue(Path(result["package"]["html_path"]).exists())
        self.assertTrue(Path(result["package"]["pdf_path"]).read_bytes().startswith(b"%PDF"))
        page = Path(result["package"]["html_path"]).read_text(encoding="utf-8")
        for text in ("Todman Ave", "31.3 kW", "Living room", "Sun through glass, walls and roof", "Sydney",
                     "not an engineer&#x27;s design", "Garage", "Units to consider",
                     "Uses typical lighting. <span class=muted>(2 rooms)</span>", "Supply duct heat gain", "household of 4"):
            self.assertIn(text, page)
        self.assertIn("Not cooled: Garage.", page)     # a room the operators retired isn't listed as a room
        self.assertLess(len(page), 50_000)      # a contractor's report, not the job's results
        manifest = result["package"]["manifest"]
        self.assertIn("hourly_ai_preliminary_load_report.json", [row["path"] for row in manifest["source_artifacts"]])
        self.assertFalse((Path(result["package"]["artifact_url"]) / "artifacts").exists())   # sources named by hash, not copied

    def test_current_ahu_result_is_in_report_and_changes_package_fingerprint(self):
        room_path = self.root / "hourly_ai_preliminary_load_report.json"
        room = json.loads(room_path.read_text(encoding="utf-8"))
        room["preliminary_input_set_fingerprint"] = "snapshot-1"
        room_path.write_text(json.dumps(room), encoding="utf-8")
        (self.root / "ai_preliminary_input_set.json").write_text(json.dumps({
            "input_fingerprint": "snapshot-1", "material": {"hourly_load_model": {"rooms": [
                {"room_id": "room-1", "name": "Living room"}]}}}), encoding="utf-8")
        (self.root / "ahu_resolution.json").write_text(json.dumps({"fingerprint": "ahu-1"}), encoding="utf-8")
        ahu_path = self.root / "hourly_ai_preliminary_ahu_load_report.json"
        ahu = {"status": "draft", "calculation_mode": "ai_preliminary",
               "input_snapshot_fingerprint": "snapshot-1",
               "input_fingerprints": {"calculator_input_snapshot": "snapshot-1", "ahu_resolution": "ahu-1"},
               "scenario_results": [{"scenario_id": "summer", "ahus": [{
                   "ahu_id": "AHU-1", "name": "AHU-1", "system_type": "constant_volume", "number_off": 1,
                   "served_zone_ids": ["zone-1"], "room_ids": ["room-1"],
                   "peak": {"hour": 15, "design_total_kw": 4.4},
                   "hours": [{"hour": 15, "coil_total_kw": 4.0, "coil_sensible_kw": 3.5, "coil_latent_kw": 0.5,
                              "flows_lps": {"supply": 250, "outside_air": 15}, "fan_heat_kw": 0.3,
                              "duct_effect_kw": 0.1}]}]}]}
        ahu_path.write_text(json.dumps(ahu), encoding="utf-8")
        first = self.post({"report_type": "cooling", "source": "job"}, {"results_ready": True, "result_stale": False})
        html = Path(first["package"]["html_path"]).read_text(encoding="utf-8")
        self.assertIn("Air systems and coil duty", html)
        self.assertIn("AHU-1", html)
        self.assertIn("4.0 kW", html)
        self.assertIn("hourly_ai_preliminary_ahu_load_report.json",
                      [row["path"] for row in first["package"]["manifest"]["source_artifacts"]])
        ahu["scenario_results"][0]["ahus"][0]["hours"][0]["coil_total_kw"] = 4.2
        ahu_path.write_text(json.dumps(ahu), encoding="utf-8")
        second = self.post({"report_type": "cooling", "source": "job"}, {"results_ready": True, "result_stale": False})
        self.assertNotEqual(first["package"]["package_fingerprint"], second["package"]["package_fingerprint"])
        self.assertIn("4.0 kW", Path(first["package"]["html_path"]).read_text(encoding="utf-8"))

    def test_an_out_of_date_final_load_asks_for_an_update_first(self):
        with self.assertRaisesRegex(ValueError, "out of date; update it first"):
            self.post({"report_type": "cooling", "source": "job"}, {"results_ready": True, "result_stale": True})

    def test_no_final_load_says_to_calculate_it(self):
        (self.root / "hourly_ai_preliminary_load_report.json").unlink()
        with self.assertRaisesRegex(ValueError, "must be calculated first"):
            self.post({"report_type": "cooling", "source": "job"}, {"results_ready": False, "result_stale": False})

    def test_the_engineer_workflow_still_packages_its_own_report(self):
        with self.assertRaisesRegex(ValueError, "No cooling report exists to package"):
            self.post({"report_type": "cooling"}, {"results_ready": True, "result_stale": False})


if __name__ == "__main__":
    unittest.main()
