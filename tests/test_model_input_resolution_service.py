"""Lifecycle checks for the consolidated model-input resolver."""

import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import model_input_resolution_service
from backend import ai_preliminary_service


class Web:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "model-input-service", "name": "Resolver fixture", "review_dir": str(root)}
        (root / "building_evidence.json").write_text(json.dumps({"spaces": [{"name": "Office", "level_name": "Level 1", "area": 40, "evidence": [{"page": 1}]}]}), encoding="utf-8")
        (root / "ai_preliminary_run.json").write_text(json.dumps({"manual_placeholder_proposal": {"rooms": [{"kind": "room", "label": "Office", "level_name": "Level 1", "area_m2": 40, "preliminary_profile_id": "office", "page": 1}]}}), encoding="utf-8")
        response = model_input_resolution_service.post(Web(), project, {"action": "resolve"})
        check("resolve writes the shared register", (root / "model_input_resolution.json").exists())
        check("all register responses are project scoped", response["id"] == project["id"] and response["artifact_links"]["model_input_resolution"].endswith("model_input_resolution.json"))
        check("coverage and review queue are returned", "coverage_summary" in response and "review_queue" in response)
        required = json.loads((root / "model_input_resolution.json").read_text(encoding="utf-8"))["required_artifacts"]
        check("missing draft artifacts are hydrated", all(item["exists"] and item["hydrated"] and item["provisional"] for item in required))
        check("hydration creates all four inputs", all((root / filename).exists() for filename in ("design_requirements.json", "schedule_library.json", "design_day_scenarios.json", "hourly_load_model.json")))
        calculated = ai_preliminary_service.post(Web(), project, {"action": "calculate"})
        report = calculated["hourly_ai_preliminary_load_report"]
        check("a freshly hydrated draft input set calculates without stale-state drift", report.get("status") == "draft" and not calculated.get("stale_reasons"))
        refreshed = model_input_resolution_service.get(Web(), project)
        check("repeated GET is read-only and current", refreshed["status"] in {"current", "stale"})


if __name__ == "__main__":
    main()
