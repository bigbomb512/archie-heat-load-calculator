"""Loopback and isolation checks for the permission-free local test workspace."""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import security, test_mode_service


class FakeWeb:
    def __init__(self, root, project):
        self.WEB_REVIEW = Path(root) / "web_review"
        self.projects = {project["id"]: project}

    def is_loopback_bind(self):
        return True

    def load_projects(self):
        return self.projects


class FakeThread:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.alive = True
        self.instances.append(self)

    def start(self):
        return None

    def is_alive(self):
        return self.alive


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    fixture_definition = json.loads((Path(__file__).resolve().parents[1] / "backend" / "fixtures" / "test_workspace_full_building.json").read_text(encoding="utf-8"))
    fixture_areas = [((row["rectangle_px"][2] - row["rectangle_px"][0]) *
                      (row["rectangle_px"][3] - row["rectangle_px"][1]) *
                      fixture_definition["scale_mm_per_px"] ** 2) / 1_000_000
                     for row in fixture_definition["rooms"]]
    check("saved full-building fixture has valid synthetic positive areas and an explicit warning",
          len(fixture_areas) >= 5 and all(area > 0 for area in fixture_areas) and "not extracted from the PDF" in fixture_definition["warning"])
    check("saved fixture includes isolated envelope, glazing, AHU, plant, and expected-result cases",
          len(fixture_definition["envelope"]["surfaces"]) == 1 and len(fixture_definition["envelope"]["openings"]) == 1
          and fixture_definition["ahu_candidate"]["system_type"] == "single_zone_constant_volume"
          and fixture_definition["plant_candidate"]["plant_type"] == "chiller"
          and fixture_definition["expected"]["review_ready"] is False)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "source"
        source.mkdir()
        pdf = root / "fixture.pdf"
        pdf.write_bytes(b"local fixture pdf")
        (source / "building_evidence.json").write_text(json.dumps({"rooms": ["Shop"]}), encoding="utf-8")
        project = {"id": "fixture-project", "name": "Butcher Buffet local fixture", "pdf": str(pdf),
                   "review_dir": str(source), "analysed": True, "pages": 3, "analysis_version": "fixture-v1"}
        # A test run must not inherit a contractor's old manual-AI proposal;
        # it can contain incomplete placeholder rooms and is unrelated to the
        # isolated deterministic fixture.
        (source / "ai_preliminary_run.json").write_text(json.dumps({"manual_placeholder_entities": [
            {"kind": "room", "label": "Old incomplete proposal"},
        ]}), encoding="utf-8")
        cloned_workspace = root / "clone-check"
        test_mode_service._clone_source(project, cloned_workspace, root / "clone-check.pdf")
        check("isolated run omits stale manual-AI proposals and old preliminary reports",
              not (cloned_workspace / "ai_preliminary_run.json").exists()
              and not (cloned_workspace / "hourly_ai_preliminary_load_report.json").exists())
        web = FakeWeb(root, project)
        config = security.SecurityConfig("test", "", "", "", "", "", "")
        FakeThread.instances.clear()
        with patch.dict("os.environ", {"ARCHIE_ENV": "test", "ARCHIE_TEST_MODE": "1"}, clear=False), \
             patch("backend.security.config", return_value=config), \
             patch("backend.test_mode_service.threading.Thread", FakeThread):
            current = test_mode_service.status(web)
            check("status reports an available fixture without local filesystem paths", current["available"] and str(root) not in json.dumps(current))
            first = test_mode_service.run(web)
            second = test_mode_service.run(web)
            check("identical active runs are deduplicated", first["run"]["run_id"] == second["run"]["run_id"] and second["deduplicated"])
            check("only one worker is created", len(FakeThread.instances) == 1)
            run_dir, run_data = test_mode_service._read_run(web.WEB_REVIEW / test_mode_service.RUN_NAMESPACE, first["run"]["run_id"])
            check("test run is stored under an isolated test namespace", run_dir.parent == (web.WEB_REVIEW / "test_runs").resolve())
            check("browser-safe status omits workspace paths", str(root) not in json.dumps(test_mode_service.get_run(web, run_data["run_id"])))
            try:
                test_mode_service.reset(web, run_data["run_id"])
            except RuntimeError:
                check("a running test cannot be deleted", True)
            else:
                check("a running test cannot be deleted", False)
            FakeThread.instances[0].alive = False
            test_mode_service._RUNNING.clear()
            removed = test_mode_service.reset(web, run_data["run_id"])
            check("reset removes only the selected test run", removed["reset"] and not run_dir.exists() and source.exists())
            try:
                test_mode_service.status(web, client_host="192.0.2.5")
            except PermissionError:
                check("non-loopback clients are rejected", True)
            else:
                check("non-loopback clients are rejected", False)
        with patch.dict("os.environ", {"ARCHIE_ENV": "test", "ARCHIE_TEST_MODE": "0"}, clear=False), \
             patch("backend.security.config", return_value=config):
            try:
                test_mode_service.status(web)
            except PermissionError:
                check("test API stays disabled without the explicit flag", True)
            else:
                check("test API stays disabled without the explicit flag", False)
        production = security.SecurityConfig("production", "https://archie.example", "ap-southeast-2", "pool", "client", "db", "bucket")
        with patch.dict("os.environ", {"ARCHIE_TEST_MODE": "1"}, clear=False), \
             patch("backend.security.config", return_value=production):
            try:
                test_mode_service.status(web)
            except PermissionError:
                check("production mode cannot enable the test workspace", True)
            else:
                check("production mode cannot enable the test workspace", False)


if __name__ == "__main__":
    main()
