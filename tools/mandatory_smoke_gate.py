#!/usr/bin/env python3
"""Mandatory, developer-only smoke gate for meaningful repository changes.

The gate deliberately uses the project's existing tests and quality checks.  It
does not approve engineering methods, contact an external provider, or modify
project artifacts.  The socket-based video test is opt-in because some
restricted CI/sandbox environments do not permit local socket binding.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tests"


def command_label(command: list[str]) -> str:
    return " ".join(command)


def run_check(label: str, command: list[str]) -> None:
    print(f"\n== {label} ==")
    print(f"$ {command_label(command)}")
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode:
        raise SystemExit(f"\nSMOKE GATE FAILED: {label} (exit {result.returncode})")


def python_regression_commands(include_video: bool) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    for path in sorted(TEST_DIR.glob("test_*.py")):
        if path.name == "test_frontend_contract.py":
            continue
        if path.name == "test_video_serving.py" and not include_video:
            continue
        commands.append((f"Python regression: {path.name}", [sys.executable, str(path)]))
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-video",
        action="store_true",
        help="also run the local-socket video-serving test",
    )
    args = parser.parse_args()

    if shutil.which("node") is None:
        raise SystemExit("SMOKE GATE FAILED: node is required for frontend syntax checks")

    checks: list[tuple[str, list[str]]] = [
        (
            "Temporary calculation sanity vectors",
            [sys.executable, str(TEST_DIR / "test_temporary_calculation_sanity.py")],
        ),
        (
            "Python quality",
            [sys.executable, str(ROOT / "tools" / "check_python_quality.py")],
        ),
        (
            "Repository hygiene",
            [sys.executable, str(TEST_DIR / "test_repository_hygiene.py")],
        ),
        ("Git whitespace check", ["git", "diff", "--check"]),
        ("Staged Git whitespace check", ["git", "diff", "--cached", "--check"]),
        (
            "Frontend contract",
            [sys.executable, str(TEST_DIR / "test_frontend_contract.py")],
        ),
        (
            "Frontend app syntax",
            ["node", "--check", str(ROOT / "frontend" / "js" / "app.js")],
        ),
        (
            "Frontend landing syntax",
            ["node", "--check", str(ROOT / "frontend" / "js" / "landing.js")],
        ),
    ]
    checks.extend(python_regression_commands(args.include_video))

    for label, command in checks:
        run_check(label, command)

    omitted = "" if args.include_video else " (video-serving test omitted; use --include-video when sockets are permitted)"
    print(f"\nMANDATORY SMOKE GATE PASSED: {len(checks)} checks{omitted}.")
    print("Development confidence only; this is not engineering or CAMEL+ validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
