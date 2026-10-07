"""Regression tests for the opt-in drawing profiler."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import page_analysis_cache
from tools import profile_drawing_backend


class ProfileDrawingBackendTests(unittest.TestCase):
    @staticmethod
    def _mock_project(root):
        (root / "review").mkdir(parents=True, exist_ok=True)
        return {"id": "tiny-project", "review_dir": str(root / "review")}

    def test_uncached_run_all_replacement_accepts_cache_keywords(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._mock_project(root)
            built = []

            def run_all(_web, cloned):
                return page_analysis_cache.get_context(
                    cloned["review_dir"], 1,
                    lambda: built.append(True) or {"objects": []},
                    code_fingerprint="test-code",
                )

            with patch.object(profile_drawing_backend.web_app, "project_by_id", return_value=project), \
                    patch.object(profile_drawing_backend.autonomous_tasks_service, "run_all", side_effect=run_all), \
                    patch.object(profile_drawing_backend.autonomous_tasks_service,
                                 "_page_analysis_code_fingerprint", return_value="test-code"):
                self.assertEqual(profile_drawing_backend.main([
                    "run-all", "--project-id", "tiny-project", "--uncached-page-analysis",
                ]), 0)

            self.assertEqual(built, [True])

    def test_copy_path_requires_marker_and_leaves_unmarked_directory_untouched(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            project = self._mock_project(project_root)
            unmarked = root / "unmarked"
            unmarked.mkdir()
            protected = unmarked / "keep.txt"
            protected.write_text("user data", encoding="utf-8")
            with patch.object(profile_drawing_backend.web_app, "project_by_id", return_value=project):
                with self.assertRaises(SystemExit):
                    profile_drawing_backend.main([
                        "run-all", "--project-id", "tiny-project", "--copy-path", str(unmarked),
                    ])
            self.assertEqual(protected.read_text(encoding="utf-8"), "user data")

    def test_marked_copy_path_is_reused(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            project = self._mock_project(project_root)
            copy_path = root / "kept-copy"
            copy_path.mkdir()
            profile_drawing_backend._write_copy_marker(copy_path, "tiny-project")
            (copy_path / "review").mkdir()
            seen = []

            def run_all(_web, cloned):
                seen.append(Path(cloned["review_dir"]))
                return {"ok": True}

            with patch.object(profile_drawing_backend.web_app, "project_by_id", return_value=project), \
                    patch.object(profile_drawing_backend.autonomous_tasks_service, "run_all", side_effect=run_all), \
                    patch.object(profile_drawing_backend.autonomous_tasks_service,
                                 "_page_analysis_code_fingerprint", return_value="test-code"):
                self.assertEqual(profile_drawing_backend.main([
                    "run-all", "--project-id", "tiny-project", "--copy-path", str(copy_path),
                ]), 0)
            self.assertEqual(seen, [(copy_path / "review").resolve()])
            self.assertTrue((copy_path / profile_drawing_backend.COPY_MARKER).is_file())


if __name__ == "__main__":
    unittest.main()
