import unittest
from unittest.mock import patch

from backend import web_app


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
        }):
            projects = web_app.project_list()

        self.assertEqual([project["id"] for project in projects], ["real-project"])
        self.assertEqual(projects[0]["name"], "Building drawings.pdf")


if __name__ == "__main__":
    unittest.main()
