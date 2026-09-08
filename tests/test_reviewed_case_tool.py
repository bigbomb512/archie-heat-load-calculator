#!/usr/bin/env python3

"""Smoke tests for the private reviewed-case preparation tool."""

import json
from pathlib import Path
import tempfile
import unittest

from tools.create_reviewed_cooling_case import prepare


class ReviewedCaseToolTests(unittest.TestCase):
    def test_prepare_requires_named_reviewer_and_preserves_source_link(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source"
            output = Path(folder) / "output"
            source.mkdir()
            (source / "ai_input.json").write_text(json.dumps({
                "source_pdf": "/private/drawing-set.pdf",
                "confirmed_pages": {"floor_plans": [{
                    "page": 1, "level_name": "Level 1", "detected_type": "floor_plan",
                    "title": "Level 1 plan", "rooms": [{"name": "Room A", "area": "25 m2"}],
                    "structured_content": {"markdown": "ROOM A AREA: 25 m2 external wall window"},
                }], "reference_pages": []},
            }), encoding="utf-8")
            manifest = Path(folder) / "review.json"
            manifest.write_text(json.dumps({
                "project_id": "private-case", "reviewer": "ENG-1", "reviewed_at": "2026-09-07",
                "approved_scope": "Supported cooling", "unresolved_inputs": [], "exclusions": ["infiltration"],
            }), encoding="utf-8")
            result = prepare(source, output, manifest)
            self.assertEqual(result["review_manifest"]["reviewer"], "ENG-1")
            self.assertEqual(result["source_pdf"], "/private/drawing-set.pdf")
            self.assertTrue((output / "building_evidence.json").exists())
            self.assertTrue((output / "thermal_model.json").exists())
            draft = json.loads((output / "calculator_draft.json").read_text(encoding="utf-8"))
            self.assertEqual(draft["schema_version"], 2)
            self.assertTrue(draft["review_items"] or any(draft["candidates"].values()))


if __name__ == "__main__":
    unittest.main()
