"""Race and lifecycle checks for the persisted room-inference service."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import draft_service, room_inference_service


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
        project = {"id": "room-race", "review_dir": str(root)}
        for name, value in {
            "ai_input": {"drawing_set": {"pages": [{"page": 1, "detected_type": "floor_plan", "plan_role": "main_floor_plan", "level_name": "Ground", "structured_content": {"markdown": "OFFICE"}}]}},
            "drawing_coverage": {}, "spatial_ocr": {}, "building_evidence": {},
        }.items():
            (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

        original = room_inference_service.room_inference.infer
        original_draft_post = draft_service.post
        draft_refreshes = []
        (root / "calculator_draft.json").write_text("{}", encoding="utf-8")
        draft_service.post = lambda _web, _project, payload: draft_refreshes.append(payload["action"]) or {}

        def slow_infer(*_args):
            time.sleep(0.05)
            return {"rooms": [], "selected_pages": [1]}

        room_inference_service.room_inference.infer = slow_infer
        try:
            results = []
            threads = [threading.Thread(target=lambda: results.append(room_inference_service.post(Web(), project, {"action": "start"}))) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            check("concurrent starts share one job", len({item["job_id"] for item in results}) == 1)
            check("duplicate start is reported", any(item.get("deduplicated") for item in results))
            deadline = time.monotonic() + 5.0
            while "room-race" in room_inference_service._RUNNING and time.monotonic() < deadline:
                time.sleep(0.01)
            check("worker releases its running slot", "room-race" not in room_inference_service._RUNNING)
            job = json.loads((root / "room_inference_job.json").read_text(encoding="utf-8"))
            check("worker reaches a terminal state", job["status"] == "completed")
            check("completed room inference refreshes an existing calculator draft",
                  job.get("calculator_draft_refresh") == "current" and draft_refreshes == ["build"])
            reused = room_inference_service.post(Web(), project, {"action": "start"})
            check("completed same-fingerprint job is reused", reused["job_id"] == job["job_id"] and not reused.get("deduplicated"))
            # A legacy completion without source-linked pages is surfaced as
            # stale so the normal non-blocking frontend poll retries it.
            legacy = dict(job, status="completed")
            legacy["source_fingerprint"] = room_inference_service._source_fingerprint(room_inference_service._paths(project))
            (root / "ai_preliminary_run.json").write_text(json.dumps({"manual_placeholder_proposal": {"rooms": [{"label": "Office", "page": None}]}}), encoding="utf-8")
            (root / "room_inference_job.json").write_text(json.dumps(legacy), encoding="utf-8")
            check("legacy incomplete completion is stale", room_inference_service.get(Web(), project)["status"] == "stale")
        finally:
            room_inference_service.room_inference.infer = original
            draft_service.post = original_draft_post


if __name__ == "__main__":
    main()
