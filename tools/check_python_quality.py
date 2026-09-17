#!/usr/bin/env python3
"""Small dependency-free syntax and unused-import check for project Python."""

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (ROOT / "ai", ROOT / "backend", ROOT / "tools")


def python_files():
    return sorted(path for directory in SOURCE_DIRS for path in directory.glob("*.py"))


def unused_imports(tree):
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.asname or alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            imports.extend((alias.asname or alias.name, node.lineno) for alias in node.names if alias.name != "*")
    return [(name, line) for name, line in imports if name not in used]


def main():
    failures = []
    for path in python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            failures.append(f"{path}:{error.lineno}: syntax error: {error.msg}")
            continue
        for name, line in unused_imports(tree):
            failures.append(f"{path}:{line}: unused import '{name}'")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Python quality check passed for {len(python_files())} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
