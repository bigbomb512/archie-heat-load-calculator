import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import retrace_job_copy as retrace


class RetraceJobCopyTests(unittest.TestCase):
    def test_refuses_destination_inside_saved_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            review = Path(folder) / "output" / "web_review"
            job = review / "job"
            job.mkdir(parents=True)
            with patch.object(retrace, "REVIEW_ROOT", review):
                with self.assertRaisesRegex(ValueError, "outside output/web_review"):
                    retrace.run(job, review / "copy")

    def test_dry_run_never_requests_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            review = Path(folder) / "output" / "web_review"
            job = review / "job"
            job.mkdir(parents=True)
            (job / "marker.txt").write_text("source")
            out = Path(folder) / "copy"
            with patch.object(retrace, "REVIEW_ROOT", review), \
                 patch.object(retrace, "_clone", side_effect=lambda _job, target: target.mkdir()), \
                 patch.object(retrace, "_build"), patch.object(retrace, "_summary", return_value={}), \
                 patch.object(retrace, "_outcomes", return_value=[]), \
                 patch.object(retrace.eval_room_tracing, "evaluate", return_value={}), \
                 patch.object(retrace.eval_window_placement, "evaluate", return_value={}), \
                 patch.object(retrace.ai_provider, "get") as provider:
                result = retrace.run(job, out)
            self.assertEqual(result["calls_made"], 0)
            provider.assert_not_called()
            self.assertEqual((job / "marker.txt").read_text(), "source")

    def test_live_cap_prevents_provider_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = {"review_dir": str(root)}
            record = {"task": "P0_room_outlines", "target": "page-8", "run_id": "one"}
            report = {"calls_made": 0}
            with patch.object(retrace, "_build"), patch.object(retrace, "_waiting", return_value=[record]), \
                 patch.object(retrace.ai_provider, "get") as provider:
                retrace._answer(None, project, 0, report)
            provider.assert_not_called()
            self.assertEqual(report["calls_made"], 0)
            self.assertIn("cap", report["stopped_reason"])

    def test_live_cap_stops_after_one_request(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"review_dir": folder}
            records = [{"task": "P0_room_names", "target": f"page-{page}",
                        "run_id": f"run-{page}", "status": "waiting_for_reply", "prompt": "read"}
                       for page in (8, 9)]
            report = {"calls_made": 0}
            provider = unittest.mock.Mock()
            provider.propose.return_value = ({"areas": []}, {})
            with patch.object(retrace, "_build"), patch.object(retrace, "_waiting", return_value=records), \
                 patch.object(retrace.tasks, "_current_task", side_effect=lambda _r, _t, target:
                              next(row for row in records if row["target"] == target)), \
                 patch.object(retrace.drawing, "_images", return_value=[]), \
                 patch.object(retrace.drawing, "_sizes", return_value=[]), \
                 patch.object(retrace.drawing, "uses_grid", return_value=False), \
                 patch.object(retrace.ai_provider, "get", return_value=provider), \
                 patch.object(retrace.ai_provider, "recorded", return_value=provider), \
                 patch.object(retrace.tasks, "post"):
                retrace._answer(None, project, 1, report)
            self.assertEqual(provider.propose.call_count, 1)
            self.assertEqual(report["calls_made"], 1)
            self.assertIn("cap", report["stopped_reason"])


if __name__ == "__main__":
    unittest.main()


class MergeNamesTest(unittest.TestCase):
    def test_printed_spellings_join_the_room_list_once(self):
        from scripts.retrace_job_copy import _merge_names
        merged = _merge_names(["Garage", "Kitchen"], ["Ensuite", "WIR", "Garage", "Kitchen"],
                              {"KITCHEN", "EN S U ITE", "W IR", "GARAGE /", "BEDROOM 5", "R O B E"})
        self.assertEqual(merged, ["Bedroom 5", "Ensuite", "Garage", "Kitchen", "Robe", "WIR"])
