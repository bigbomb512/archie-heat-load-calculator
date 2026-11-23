import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import result_label_service as service


class ResultLabelTests(unittest.TestCase):
    def setUp(self):
        self.project = {"review_dir": "/unused"}
        self.report = {"included_scope_peak": {"final_design_total_kw": 21.7}}
        self.open = [{"key": "windows", "label": "Window glass", "open": True},
                     {"key": "roof", "label": "Roof construction", "open": True}]
        self.range = {"status": "calculated", "unknowns": [
            {"key": "windows", "changes_unit": True, "swing_kw": 4.2},
            {"key": "roof", "changes_unit": False, "swing_kw": 0.4}]}

    def call(self, *, report=None, unknowns=None, rng=None, stale=False):
        with patch.object(service.assumption_range_service.ai_preliminary_service, "_paths",
                          return_value={"report": Path("/unused/report.json"), "root": Path("/unused")}), \
             patch.object(service.assumption_range_service, "_read", return_value=self.report if report is None else report), \
             patch.object(service.assumption_range_service, "open_unknowns", return_value=self.open if unknowns is None else unknowns), \
             patch.object(service.assumption_range_service, "get", return_value=self.range if rng is None else rng) as get:
            result = service.label(self.project, status={"results_ready": True, "result_stale": stale})
            if service.assumption_range_service._total(self.report if report is None else report) is not None:
                get.assert_called_once_with(self.project, start=False)
            return result

    def test_no_saved_report(self):
        self.assertEqual(self.call(report={})["status"], "not_calculated")

    def test_stale_never_design(self):
        result = self.call(unknowns=[], stale=True)
        self.assertEqual((result["status"], result["text"]), ("preliminary", "Preliminary (out of date)"))

    def test_range_not_calculated_counts_every_open_unknown(self):
        for state in ("running", "not_started", "inputs_changed"):
            with self.subTest(state=state):
                result = self.call(rng={"status": state})
                self.assertEqual((result["status"], result["count"]), ("preliminary", 2))
                self.assertIn("isn't worked out yet", result["reason"])
                self.assertTrue(all(row["changes_unit"] is None for row in result["open"]))

    def test_unit_changing_open(self):
        result = self.call()
        self.assertEqual((result["status"], result["count"]), ("preliminary", 1))
        self.assertEqual(result["open"][0]["swing_kw"], 4.2)

    def test_only_non_unit_changing_open(self):
        result = self.call(unknowns=self.open[1:])
        self.assertEqual((result["status"], result["count"]), ("design", 0))
        self.assertIn("still assumed", result["reason"])
        self.assertEqual(len(result["open"]), 1)

    def test_all_answered(self):
        result = self.call(unknowns=[], rng={"status": "nothing_open"})
        self.assertEqual((result["status"], result["count"], result["open"]), ("design", 0, []))


if __name__ == "__main__":
    unittest.main()
