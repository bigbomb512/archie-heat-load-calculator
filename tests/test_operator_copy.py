"""Keep retired outside-user wording out of operator-facing copy."""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETIRED_TERMS = re.compile(r"\b(?:contractor|toki\s+team)\b", re.IGNORECASE)
LEGACY_STORED_LABELS = {"contractor override"}


def _python_display_strings(source: str) -> list[str]:
    """Return string literals while omitting comments and docstrings."""
    tree = ast.parse(source)
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", ())
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstring_nodes.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]


class OperatorCopyTests(unittest.TestCase):
    def test_job_screens_have_no_retired_user_wording(self):
        for path in sorted((ROOT / "frontend/js/job").glob("*.js")):
            self.assertIsNone(RETIRED_TERMS.search(path.read_text()), path.name)

    def test_backend_display_strings_have_no_retired_user_wording(self):
        matches = []
        for path in (ROOT / "backend").rglob("*.py"):
            relative = path.relative_to(ROOT)
            for value in _python_display_strings(path.read_text()):
                if value in LEGACY_STORED_LABELS:
                    continue  # Existing source metadata is retained and mapped in the UI.
                if RETIRED_TERMS.search(value):
                    matches.append(f"{relative}: {value!r}")
        self.assertEqual(matches, [])


if __name__ == "__main__":
    unittest.main()
