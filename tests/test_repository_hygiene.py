#!/usr/bin/env python3
"""Guard against committing private PDFs or generated project artifacts."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIRS = {
    "output",
    "artifacts",
    "uploads",
    "test-results",
    "playwright-report",
}
FORBIDDEN_SUFFIXES = {".pdf"}


def tracked_paths():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(item) for item in result.stdout.decode().split("\0") if item]


class RepositoryHygieneTests(unittest.TestCase):
    def test_private_pdfs_and_generated_artifacts_are_not_tracked(self):
        violations = []
        for path in tracked_paths():
            if path.suffix.lower() in FORBIDDEN_SUFFIXES:
                violations.append(str(path))
            if path.parts and path.parts[0] in GENERATED_DIRS:
                violations.append(str(path))
            if "frontend" in path.parts and any(part in GENERATED_DIRS for part in path.parts):
                violations.append(str(path))
        self.assertEqual([], sorted(set(violations)))

    def test_generated_workspaces_are_ignored(self):
        ignore_file = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in ("output/", "frontend/test-results/", "frontend/playwright-report/"):
            self.assertIn(entry, ignore_file)


if __name__ == "__main__":
    unittest.main()
