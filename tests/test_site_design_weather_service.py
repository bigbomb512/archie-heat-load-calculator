#!/usr/bin/env python3
"""Project-local API lifecycle checks for licensed design-weather resolution."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import site_design_weather_service
from tests.test_site_design_weather_resolution import LOCATION, pack, profile, record


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


class LocalWeb:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def main():
    with TemporaryDirectory() as directory, TemporaryDirectory() as protected_directory:
        root = Path(directory)
        project, web = {"id": "weather-test", "review_dir": str(root)}, LocalWeb()
        (root / "site_location_resolution.json").write_text(json.dumps(LOCATION), encoding="utf-8")
        pack_path = Path(protected_directory) / "licensed-airah.json"
        pack_path.write_text(json.dumps(pack([record("cool", "cooling", profile(35, 23)), record("heat", "heating", profile(5, 4))])), encoding="utf-8")
        previous = os.environ.get(site_design_weather_service.PACK_PATH_ENV)
        os.environ[site_design_weather_service.PACK_PATH_ENV] = str(pack_path)
        try:
            basis = site_design_weather_service.post(web, project, {"action": "set_design_basis", "design_basis": "comfort"})
            check("basis creates a project-local artifact", (root / "site_design_weather_resolution.json").exists() and basis["status"] == "awaiting_design_basis")
            resolved = site_design_weather_service.post(web, project, {"action": "resolve_candidates"})
            body = resolved["site_design_weather_resolution"]
            check("service auto-selects cooling and heating", body["cooling"]["selected"]["record_id"] == "cool" and body["heating"]["selected"]["record_id"] == "heat")
            check("response has an opaque artifact link and no pack path", resolved["artifact_url"] == "/artifact/site_design_weather_resolution.json" and str(pack_path) not in json.dumps(resolved))
            changed_location = {**LOCATION, "fingerprint": "changed-location"}
            (root / "site_location_resolution.json").write_text(json.dumps(changed_location), encoding="utf-8")
            stale = site_design_weather_service.get(web, project)
            check("location changes make resolved weather visibly stale", stale["status"] == "needs_review" and stale["site_design_weather_resolution"]["stale_reasons"])
        finally:
            if previous is None:
                os.environ.pop(site_design_weather_service.PACK_PATH_ENV, None)
            else:
                os.environ[site_design_weather_service.PACK_PATH_ENV] = previous


if __name__ == "__main__":
    main()
