"""Safe error and create-only hydration checks for model-input resolution."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import model_input_resolution_service


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
        project = {"id": "resolver-errors", "review_dir": str(root)}
        (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": []}}), encoding="utf-8")
        (root / "room_inference_job.json").write_text(json.dumps({"status": "running", "job_id": "room-job"}), encoding="utf-8")
        try:
            model_input_resolution_service.post(Web(), project, {"action": "resolve"})
        except model_input_resolution_service.ModelInputResolutionError as error:
            payload = model_input_resolution_service.error_payload(error)
            check("pending room inference uses 202", error.status_code == 202)
            check("error has actionable fields", payload["code"] == "room_inference_pending" and payload["remediation"] and "artifact" in payload)
        else:
            raise AssertionError("pending room inference did not fail closed")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "resolver-area-coverage", "review_dir": str(root)}
        (root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "shop", "original_label": "Shop", "level_name": "Level 1", "space_scope": "comfort_hvac", "status": "resolved"},
            {"room_id": "kitchen", "original_label": "Kitchen", "level_name": "Level 1", "space_scope": "comfort_hvac_with_process_exception", "status": "resolved"},
        ]}), encoding="utf-8")
        (root / "geometry_resolution.json").write_text(json.dumps({"entities": [
            {"kind": "area", "label": "Shop", "level_candidate": "Level 1", "geometry_status": "ai_estimated", "value": {"area_m2": 120.5}},
        ]}), encoding="utf-8")
        (root / "building_evidence.json").write_text(json.dumps({"spaces": []}), encoding="utf-8")
        (root / "ai_preliminary_run.json").write_text(json.dumps({}), encoding="utf-8")
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("geometry coverage matches active areas by room and level", len(eligible) == 2 and [row["room_id"] for row in missing] == ["kitchen"])


if __name__ == "__main__":
    main()
