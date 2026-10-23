"""Saving a job's record merges into the stored one, so a background step holding an older copy can't wipe a field
another step added (2026-10-23: Casa Nova lost its prepared drawing set and Phase 1 never started)."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from backend import web_app


class ProjectRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "web_projects.json"
        patcher = patch.object(web_app, "PROJECTS_FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        web_app.update_project({"id": "job", "name": "Cafe.pdf"})

    def stored(self):
        return json.loads(self.file.read_text())["job"]

    def test_an_older_copy_saved_later_keeps_fields_it_never_had(self):
        older = web_app.load_projects()["job"]                 # a background step's copy, taken at its start
        fresh = web_app.load_projects()["job"]
        fresh["reasoning_packet"] = {"packet": "ready"}
        web_app.update_project(fresh)
        older["room_inference_job"] = "room_inference_job.json"
        web_app.update_project(older)
        self.assertEqual(self.stored(), {"id": "job", "name": "Cafe.pdf", "reasoning_packet": {"packet": "ready"},
                                         "room_inference_job": "room_inference_job.json"})

    def test_a_field_is_removed_only_when_named(self):
        web_app.update_project({"id": "job", "parity_report": "old.json", "heat_load_report": "old.json"})
        web_app.update_project({"id": "job"}, removed=["parity_report"])
        self.assertNotIn("parity_report", self.stored())
        self.assertEqual(self.stored()["heat_load_report"], "old.json")

    def test_saves_from_many_threads_all_land(self):
        def save(number):
            web_app.update_project({"id": "job", f"field_{number}": number})
        threads = [threading.Thread(target=save, args=(number,)) for number in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual({key for key in self.stored() if key.startswith("field_")}, {f"field_{n}" for n in range(20)})


class DrawingCoverageTests(unittest.TestCase):
    def test_rebuilding_the_same_coverage_leaves_the_file_and_the_review_alone(self):
        # Its generated_at is part of the drawing review's input fingerprint: a rebuild with nothing new must not
        # mark the review out of date.
        with TemporaryDirectory() as folder:
            path = Path(folder) / "drawing_coverage.json"
            first = web_app.write_drawing_coverage(path, {"pages": [1], "generated_at": "09:52"})
            stamp = path.stat().st_mtime_ns
            again = web_app.write_drawing_coverage(path, {"pages": [1], "generated_at": "10:43"})
            self.assertEqual((again, path.stat().st_mtime_ns), (first, stamp))
            changed = web_app.write_drawing_coverage(path, {"pages": [1, 2], "generated_at": "10:44"})
            self.assertEqual(json.loads(path.read_text()), changed)


if __name__ == "__main__":
    unittest.main()
