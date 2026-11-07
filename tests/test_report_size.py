"""The load reports keep every kW figure and the design day's workings, but not every month's repeated wall and window
workings (set 16, 2026-11-07: a house's report was 423 MB)."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend import ai_preliminary_service as service


def component(kw, **inputs):
    return {"sensible_kw": kw, "latent_kw": 0.0, "total_kw": kw, "inputs": inputs, "input_rows": [inputs], "formula": "q = ..."}


def scenario(scenario_id):
    hour = {"hour": 15, "subtotal_kw": 3.0, "components": {
        "glazing_solar": component(1.5, glass_area_m2=4.0), "envelope": component(0.5, surfaces=[{"gain_kw": 0.5}]),
        "outside_air": component(0.4, flow_lps=20.0), "equipment_refrigeration": component(0.6, note="Typical equipment allowance top-up")}}
    return {"scenario_id": scenario_id, "rooms": [{"room_id": "living", "hours": [hour], "peak": {"components": hour["components"]}}],
            "zones": [], "floors": []}


class ReportSizeTests(unittest.TestCase):
    def report(self):
        return {"included_scope_peak": {"scenario_id": "month_02"},
                "scenario_results": [scenario("design_day"), scenario("month_02"), scenario("month_03")]}

    def test_the_design_day_and_the_peak_month_keep_their_workings(self):
        report = service._slim_report(self.report())
        for index in (0, 1):
            components = report["scenario_results"][index]["rooms"][0]["hours"][0]["components"]
            self.assertEqual(components["glazing_solar"]["inputs"], {"glass_area_m2": 4.0})
        self.assertEqual(report["workings_kept_for"], ["design_day", "month_02"])

    def test_other_months_keep_every_figure_but_not_the_wall_and_window_workings(self):
        report = service._slim_report(self.report())
        components = report["scenario_results"][2]["rooms"][0]["hours"][0]["components"]
        self.assertNotIn("inputs", components["glazing_solar"])
        self.assertNotIn("formula", components["envelope"])
        self.assertEqual((components["glazing_solar"]["total_kw"], components["envelope"]["total_kw"]), (1.5, 0.5))
        self.assertEqual(components["outside_air"]["inputs"], {"flow_lps": 20.0})            # the air-handling step reads these
        self.assertIn("top-up", components["equipment_refrigeration"]["inputs"]["note"])       # and "still assumed" reads this
        self.assertIn("inputs", report["scenario_results"][2]["rooms"][0]["peak"]["components"]["glazing_solar"])

    def test_the_load_reports_are_written_without_indenting(self):
        with TemporaryDirectory() as folder:
            for name, compact in (("hourly_ai_preliminary_load_report.json", True), ("phase1_base_room_load_report.json", True),
                                  ("ai_preliminary_model.json", False)):
                path = Path(folder) / name
                service._write(path, {"a": [1, 2]})
                self.assertEqual("\n" not in path.read_text(encoding="utf-8").strip(), compact, name)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": [1, 2]})


if __name__ == "__main__":
    unittest.main()
