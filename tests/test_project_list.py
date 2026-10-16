import unittest
from unittest.mock import patch

from backend import job_service, web_app


class ProjectListTests(unittest.TestCase):
    def test_isolated_test_runs_are_not_returned_as_user_projects(self):
        with patch.object(web_app, "load_projects", return_value={
            "real-project": {
                "id": "real-project",
                "name": "Building drawings.pdf",
                "pages": 4,
                "analysed": True,
                "relevant": 3,
                "updated_at": "2026-09-28T10:00:00+0000",
            },
            # Test-run metadata is deliberately smaller than a real project.
            # It must not crash project_list or leak into the real-project list.
            "isolated-run": {
                "id": "isolated-run",
                "test_run": True,
                "updated_at": "2026-09-28T11:00:00+0000",
            },
            # Old local test metadata may not be tagged as a test run and can
            # lack the fields required by the sidebar response.
            "test": {
                "id": "test",
                "review_dir": "/tmp/test-review",
                "updated_at": "2026-09-28T12:00:00+0000",
            },
        }), patch.object(job_service, "status", side_effect=AssertionError("job list must not run full status checks")):
            projects = web_app.project_list()

        self.assertEqual([project["id"] for project in projects], ["real-project"])
        self.assertEqual(projects[0]["name"], "Building drawings.pdf")
        self.assertEqual(projects[0]["stage"], "drawings")
        self.assertIn("phase1", projects[0]["needs_you"])


if __name__ == "__main__":
    unittest.main()
