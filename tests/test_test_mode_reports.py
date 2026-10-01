import json
import tempfile
import unittest
from pathlib import Path

from backend.test_mode_service import _stamp_test_report


class TestModeReportTests(unittest.TestCase):
    def test_test_workspace_stamps_downstream_reports_draft_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps({"status": "draft", "included_scope_peak": {"hour": 14}}), encoding="utf-8")

            report = _stamp_test_report(path)

            self.assertEqual(report["label"], "TEST RUN — AI preliminary estimate — not engineering reviewed or validated")
            self.assertTrue(report["test_run"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "draft")


if __name__ == "__main__":
    unittest.main()
