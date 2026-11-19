"""Count shared geometry work across the complete model-input resolve call."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import ai_preliminary_service, model_input_resolution_service


class ModelInputMemoScopeTests(unittest.TestCase):
    def test_resolve_reuses_geometry_until_its_saved_inputs_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = {"id": "memo-scope", "review_dir": str(root)}
            paths = ai_preliminary_service._paths(project)
            geometry = {"entities": [{"kind": "area", "value": {"area_m2": 10}}]}

            def exercise(_web, _project):
                first = ai_preliminary_service._preliminary_geometry(paths)
                second = ai_preliminary_service._preliminary_geometry(paths)
                self.assertEqual(first, second)
                (root / "ai_input.json").write_text('{"changed":true}', encoding="utf-8")
                third = ai_preliminary_service._preliminary_geometry(paths)
                self.assertEqual(third, geometry)

            with patch.object(ai_preliminary_service, "_build_preliminary_geometry", return_value=geometry) as build:
                with patch.object(model_input_resolution_service, "_build_inputs", side_effect=exercise):
                    model_input_resolution_service._build(None, project)
                self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()
