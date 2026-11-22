"""The AI answers the drawing tasks in rounds: each round builds what is now possible and answers what is waiting; a
refused reply is tried once more with its reason; a task that keeps failing stops; every call is logged."""

import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from backend import ai_provider, autonomous_tasks_service as tasks_service, drawing_tasks_service as drawing
from ai import autonomous_tasks as validators


class CornerRetryFeedbackTests(unittest.TestCase):
    def test_small_corner_outline_reports_coverage_and_missing_side_to_retry(self):
        packet = {"room": "Corridor", "corners": [[0, 0], [40, 0], [40, 20], [0, 20], [100, 0], [100, 20]],
                  "sketch": [[0, 0], [100, 0], [100, 20], [0, 20]], "sketch_area_px": 2000}
        with self.assertRaises(ValueError) as refused:
            validators.validate_p0_outline_corners(packet, json.dumps({"corners": [1, 2, 3, 4]}))
        reason = str(refused.exception)
        self.assertIn("40% of the sketched area", reason)
        self.assertIn("right of the sketch", reason)
        self.assertIn("near corners 2, 3", reason)
        record = {"prompt": "Pick corners", "reply_attempts": [{"outcome": "rejected", "reason": reason}]}
        self.assertIn(reason, drawing._prompt(record))

    def test_minimum_area_without_sketch_reports_percentage(self):
        from ai import room_outline
        with self.assertRaisesRegex(ValueError, "40% of the minimum area"):
            room_outline.validate_corner_outline_reply({"corners": [1, 2, 3, 4]},
                [[0, 0], [20, 0], [20, 20], [0, 20]], None, "Dining", min_area=1000)


class FakeTasks:
    """Stand-in for the task store: the scale task unlocks the room-names task; the outline task is always refused."""

    def __init__(self, root):
        self.root, self.records, self.unlocked = Path(root), {}, False
        self.add("P0_dimensions", "page-20-dimension-1")
        self.add("P0_room_outlines", "page-20")

    def add(self, task, target):
        self.records[(task, target)] = {"task": task, "target": target, "run_id": f"{task}-run", "status": "waiting_for_reply",
                                        "prompt": f"Answer {task} as JSON.", "reply_attempts": []}

    def post(self, web, project, data):
        if data["action"] == "run_all":
            if self.unlocked and ("P0_room_names", "page-20") not in self.records:
                self.add("P0_room_names", "page-20")
            return {}
        record = self.records[(data["task"], data["target"])]
        reply = json.loads(data["reply"])
        if data["task"] == "P0_room_outlines":
            record["reply_attempts"].append({"outcome": "rejected", "reason": "Outline 1 crosses itself."})
        else:
            record["reply_attempts"].append({"outcome": "accepted"})
            record["status"] = "applied"
            record["applied_value"] = reply
            self.unlocked = True
        return {}

    def all_current(self, root, web=None, project=None):
        return list(self.records.values())

    def current(self, root, task, target):
        return self.records.get((task, target))

    def task_dir(self, root, task, target):
        return self.root / "ai_tasks" / task / target


class FakeProvider:
    model = "fake/vision"

    def __init__(self, prompts):
        self.prompts = prompts

    def propose(self, prompt, image_paths=()):
        self.prompts.append(prompt)
        return {"answer": "ok"}, {"provider": "fake", "model": self.model, "usage": {"cost_usd": 0.001}, "seconds": 0.1}


class BatchTasks(FakeTasks):
    def __init__(self, root, count=4):
        self.root, self.records, self.unlocked, self.applied, self.builds = Path(root), {}, False, [], 0
        for index in range(count):
            self.add("P6_kitchen", f"kitchen-{index}")

    def add(self, task, target):
        record = {"task": task, "target": target, "run_id": f"{target}-run", "status": "waiting_for_reply",
                  "prompt": f"Answer {target}.", "reply_attempts": []}
        self.records[(task, target)] = record
        task_dir = self.task_dir(self.root, task, target) / "runs" / record["run_id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGB", (10, 10), "white").save(task_dir / "image-1.png")

    def post(self, web, project, data):
        if data["action"] == "run_all":
            self.builds += 1
            return {}
        record = self.records[(data["task"], data["target"])]
        record.update(status="applied", applied_value={"answer": json.loads(data["reply"])})
        self.applied.append(data["target"])
        return {}

    def all_current(self, root, web=None, project=None):
        return list(self.records.values())

    def current(self, root, task, target):
        return self.records.get((task, target))


class DrawingTaskTests(unittest.TestCase):
    def test_tenancy_timeout_retry_uses_smaller_image(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "large.png"
            Image.new("RGB", (2200, 1600), "white").save(image)
            class Provider:
                def propose(self, prompt, image_paths):
                    with Image.open(image_paths[0]) as opened:
                        self.size = opened.size
                    # Corners of the smaller image: they must come back as corners of the full-size one.
                    return json.dumps({"points_px": [[0, 0], [self.size[0], 0], [self.size[0], self.size[1]]],
                                       "evidence": "outer walls"}), {"provider": "fake"}
            provider = Provider()
            reply, raw = drawing._retry_smaller_tenancy(provider, "outline", [str(image)])
            self.assertLessEqual(max(provider.size), 1024)
            points = json.loads(reply)["points_px"]
            self.assertAlmostEqual(points[1][0], 2200, delta=3)
            self.assertAlmostEqual(points[2][1], 1600, delta=3)
            self.assertEqual(json.loads(reply)["evidence"], "outer walls")
            self.assertIn("retried_smaller_image", raw)

    def test_codex_cli_time_out_is_a_provider_timeout(self):
        import subprocess
        from backend import ai_provider
        with tempfile.TemporaryDirectory() as folder:
            provider = ai_provider.CodexCliProvider(executable="codex", timeout=300,
                                                    config_path=Path(folder) / "missing.toml")
        with patch("backend.ai_provider.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 300)):
            with self.assertRaises(ai_provider.ProviderTimeout):
                provider.propose("outline")

    def test_round_fetches_in_parallel_applies_in_order_and_rebuilds_once(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = BatchTasks(folder)
            lock, active, maximum = threading.Lock(), 0, 0
            class ParallelProvider:
                model = "fake"
                def propose(self, prompt, image_paths=()):
                    nonlocal active, maximum
                    with lock:
                        active += 1
                        maximum = max(maximum, active)
                    time.sleep(.03)
                    with lock:
                        active -= 1
                    return {"ok": True}, {}
            with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                    patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir), \
                    patch.object(drawing.ai_provider, "parallel_calls", return_value=4), patch.object(drawing, "MAX_ROUNDS", 1):
                drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: ParallelProvider())
            self.assertGreater(maximum, 1)
            self.assertEqual(fake.applied, [f"kitchen-{index}" for index in range(4)])
            # One rebuild before the round and one after it (the round limit ran out), none per answer.
            self.assertEqual(fake.builds, 2)

    def test_north_pages_answered_in_one_round_are_answered_not_refused_and_not_asked_again(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = BatchTasks(folder, count=0)
            for page in (4, 5, 6):
                fake.add("P2_north", f"page-{page}")
            calls = []

            def post(web, project, data):
                if data["action"] == "run_all":
                    return fake.post(web, project, data)
                record = fake.records[(data["task"], data["target"])]
                record["reply_attempts"].append({"outcome": "waiting_for_reply"})
                if data["target"] == "page-6":          # the last page works out north and settles them all
                    for row in fake.records.values():
                        row["status"] = "applied"
                else:
                    record["block_reason"] = tasks_service.NORTH_WAITING_NOTE + "(s) 6."
                return {}

            class Provider:
                model = "fake"
                def propose(self, prompt, image_paths=()):
                    calls.append(prompt)
                    return {"found": False}, {}
            with patch.object(tasks_service, "post", post), patch.object(tasks_service, "_all_current", fake.all_current), \
                    patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir), \
                    patch.object(tasks_service, "_export_determinations"), \
                    patch.object(drawing.ai_provider, "parallel_calls", return_value=3):
                result = drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: Provider())
            self.assertEqual((result["answered"], result["refused"], len(calls)), (3, 0, 3))

    def test_a_north_page_waiting_on_others_is_not_sent_again(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = BatchTasks(folder, count=0)
            fake.add("P2_north", "page-4")
            fake.records[("P2_north", "page-4")]["block_reason"] = tasks_service.NORTH_WAITING_NOTE + "(s) 6."
            with patch.object(tasks_service, "_all_current", fake.all_current):
                self.assertEqual(drawing._waiting(Path(folder)), [])

    def test_parallel_usage_limit_stops_round_without_applying_partial_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = BatchTasks(folder)
            class Limited:
                model = "fake"
                def propose(self, prompt, image_paths=()):
                    raise ai_provider.UsageLimitReached("limit")
            with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                    patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir), \
                    patch.object(drawing.ai_provider, "parallel_calls", return_value=4), \
                    patch.object(drawing, "_progress"):
                with self.assertRaises(ai_provider.UsageLimitReached):
                    drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: Limited())
            self.assertEqual(fake.applied, [])
            self.assertEqual(fake.builds, 1)
    def test_provider_limit_keeps_the_drawing_job_reviewable_and_starts_independent_skills(self):
        class App:
            def __init__(self, project): self.project = project
            def project_by_id(self, _project_id): return self.project

        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "limit", "review_dir": folder}
            app = App(project)
            limited = ai_provider.UsageLimitReached("The AI usage limit is reached; retry after reset.")
            with patch.object(drawing.ai_provider, "configured", return_value=True), \
                    patch.object(drawing, "answer_all", side_effect=limited), \
                    patch("backend.skill_workflow_service.start_after_reading") as start_skills:
                drawing.start(app, project, then_skills=True)
                deadline = time.monotonic() + 5
                while drawing._JOB.key(project) in drawing._JOB.running and time.monotonic() < deadline:
                    time.sleep(0.01)
                state = drawing.status(app, project)
            self.assertEqual((state["status"], state["provider_limited"]), ("done", True))
            self.assertIn("retry after the limit resets", state["warning"])
            start_skills.assert_called_once_with(app, project)

    def test_rounds_unlock_the_next_tasks_a_refused_reply_is_retried_once_with_its_reason_and_calls_are_logged(self):
        with tempfile.TemporaryDirectory() as folder:
            fake, prompts = FakeTasks(folder), []
            with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                    patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir):
                result = drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: FakeProvider(prompts))
            self.assertEqual(fake.records[("P0_dimensions", "page-20-dimension-1")]["status"], "applied")
            self.assertEqual(fake.records[("P0_room_names", "page-20")]["status"], "applied")      # unlocked in round 2
            outline = fake.records[("P0_room_outlines", "page-20")]
            self.assertEqual((outline["status"], len(outline["reply_attempts"])), ("waiting_for_reply", 2))   # then a person
            self.assertEqual((result["answered"], result["refused"], result["calls"]), (2, 2, 4))
            retry = [prompt for prompt in prompts if "P0_room_outlines" in prompt][1]
            self.assertIn("Your previous reply was refused: Outline 1 crosses itself.", retry)
            log = [json.loads(line) for line in (Path(folder) / ai_provider.USAGE_FILE).read_text().splitlines()]
            self.assertEqual(len(log), 4)
            self.assertTrue(all(row["purpose"].startswith("drawing:") for row in log))

    def test_deliberate_retry_only_repeats_unfinished_tasks_and_discards_refused_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            fake, prompts = FakeTasks(folder), []
            with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                    patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir):
                drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: FakeProvider(prompts))
                resumed = drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: FakeProvider(prompts))
                state = tasks_service._task_progress(Path(folder))
                again = drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: FakeProvider(prompts),
                                           retry_not_answered=True)
            # Refused twice: settled as not answered, so a run or resume doesn't send it again and it doesn't hold the
            # job at the drawings stage (Happy Cap 2026-11-02); only a deliberate retry asks again.
            self.assertEqual(resumed["calls"], 0)
            self.assertEqual((state["waiting"], state["not_answered"]), (0, 1))
            self.assertEqual(again["calls"], 2)
            self.assertEqual(again["answered"], 0)
            self.assertEqual(again["refused"], 2)
            self.assertEqual(len(prompts), 6)
            self.assertTrue(all("P0_room_outlines" in prompt for prompt in prompts[-2:]))

    def test_malformed_reply_is_archived_and_retried_with_feedback(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "j", "review_dir": folder}
            record = tasks_service._create_run(Path(folder), "P5_roof", "shop", {}, "Read roof")
            prompts = []
            class Provider:
                model = "fake"
                def propose(self, prompt, image_paths):
                    prompts.append(prompt)
                    raise ai_provider.InvalidProviderReply("The reply was not a JSON object.",
                        {"reply_text": "[]", "usage": {"cost_usd": .001}})
            def current_records(root):
                return [tasks_service._current_task(root, "P5_roof", "shop")]
            with patch.object(tasks_service, "post") as publish, \
                    patch.object(tasks_service, "_all_current", side_effect=current_records):
                result = drawing.answer_all(None, project, provider_for=lambda task: Provider())
            self.assertEqual((result["calls"], result["refused"]), (2, 2))
            self.assertEqual(result["unresolved"][0]["kind"], "answer_rejected")
            self.assertIn("previous reply was refused: The reply was not a JSON object.", prompts[1])
            saved = tasks_service._current_task(Path(folder), "P5_roof", "shop")
            self.assertEqual([row["outcome"] for row in saved["reply_attempts"]], ["rejected", "rejected"])
            archives = list((tasks_service._record_file(Path(folder), record).parent / "reply_attempts").glob("*.json"))
            self.assertTrue(all(json.loads(path.read_text())["raw_reply"] == "[]" for path in archives))
            self.assertTrue(all(call.args[2]["action"] == "run_all" for call in publish.call_args_list))

    def test_a_scale_fallback_is_listed_in_plain_words_even_from_an_older_record(self):
        old = {"task": "P0_scale_read", "target": "page-9", "status": "not_needed", "applied_value": {"fallback_required": True},
               "block_reason": "Not usable (Return a readable scale notation or scale bar, or null so the standard-object "
                               "estimate can be used.); the next way of finding the scale is used."}
        withdrawn = {"task": "P0_tenancy_outline", "target": "page-20", "status": "not_needed", "block_reason": "Every room has an outline."}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(drawing._JOB, "status", return_value={"status": "done"}), \
                    patch.object(tasks_service, "_all_current", return_value=[old, withdrawn]):
                rows = drawing.status(None, {"id": "j", "review_dir": folder})["not_needed_tasks"]
        self.assertEqual(rows[0], {"task": "P0_scale_read", "target": "page-9", "status": "not_needed", "fallback": True,
                                   "label": "Reading the printed scale",
                                   "reason": "No readable scale on page 9; the scale is found another way."})
        self.assertEqual((rows[1]["fallback"], rows[1]["reason"]), (False, "Every room has an outline."))

    def test_status_reconciles_later_repairs_without_rewriting_history(self):
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as folder:
            original = {"status": "done", "unresolved": [
                {"task": "P5_roof", "target": "shop", "kind": "service_error"}]}
            project = {"id": "j", "review_dir": folder}
            with patch.object(drawing._JOB, "status", side_effect=lambda project: deepcopy(original)), \
                    patch.object(tasks_service, "_current_task", return_value={"status": "applied", "applied_value": {"roof": "not_exposed"}}):
                self.assertEqual(drawing.status(None, project)["unresolved"], [])
            with patch.object(drawing._JOB, "status", side_effect=lambda project: deepcopy(original)), \
                    patch.object(tasks_service, "_current_task", return_value={"status": "waiting_for_reply",
                        "reply_attempts": [{"outcome": "rejected", "reason": "Invalid JSON"}]}):
                self.assertEqual(drawing.status(None, project)["unresolved"][0]["kind"], "answer_rejected")
            self.assertEqual(original["unresolved"][0]["kind"], "service_error")

    def test_waiting_follow_up_is_neutral_while_supervisor_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "j", "review_dir": folder}
            (Path(folder) / "phase1_supervisor_job.json").write_text('{"status":"running"}')
            with patch.object(drawing._JOB, "status", return_value={"status": "done"}), \
                    patch.object(drawing, "_waiting", return_value=[{"task": "P8_glazing_certificate", "target": "page-3"}]):
                state = drawing.status(None, project)
            self.assertEqual((state["waiting_tasks"], state["follow_up_sender"]), (1, "model check"))
            self.assertNotIn("recovery", state)
            (Path(folder) / "phase1_supervisor_job.json").write_text('{"status":"running","provider_limited":true}')
            with patch.object(drawing._JOB, "status", return_value={"status": "done"}):
                limited = drawing.status(None, project)
            self.assertTrue(limited["provider_limited"])
            self.assertIn("retry after the limit resets", limited["warning"])
            self.assertNotIn("follow_up_sender", limited)

    def test_grid_coordinates_become_image_pixels_by_the_image_or_the_chosen_crop(self):
        # Gemini read a 1,536 x 1,469 plan on a 0-1000 grid: the Kitchen's corner (480, 217) is pixel (737.3, 318.8).
        outline = {"rooms": [{"room": "Kitchen", "points_px": [[480, 217], [798, 585]], "open_sides": ""}]}
        self.assertEqual(drawing.to_pixels(outline, (1536, 1469))["rooms"][0]["points_px"], [[737.3, 318.8], [1225.7, 859.4]])
        north = {"found": True, "crop_index": 1, "tail_px": [471, 868], "tip_px": [471, 815], "labelled_north": False}
        sizes = [(800, 600), (1024, 724)]
        self.assertEqual(drawing._grid_size("P2_north", north, sizes), (1024, 724))
        self.assertEqual(drawing.to_pixels(north, (1024, 724))["tail_px"], [482.3, 628.4])
        self.assertTrue(drawing.uses_grid("P2_north", sizes) and drawing.uses_grid("P0_room_outlines", [(10, 10)]))
        # The outline task draws on image 1; the sheets after it only show where the rooms are named.
        self.assertTrue(drawing.uses_grid("P0_room_outlines", sizes))
        self.assertEqual(drawing._grid_size("P0_room_outlines", {"rooms": []}, sizes), (800, 600))
        self.assertFalse(drawing.uses_grid("P4_openings", sizes))                 # several images, no index: left as is
        self.assertIsNone(drawing._grid_size("P4_openings", north, sizes))
        self.assertEqual(drawing.to_pixels({"count": 3, "label": "x"}, (100, 100)), {"count": 3, "label": "x"})
        self.assertIn("0-1000 grid", drawing._prompt({"prompt": "Draw.", "reply_attempts": []}, grid=True))
        self.assertNotIn("grid", drawing._prompt({"prompt": "Draw.", "reply_attempts": []}, grid=False))

    def test_drawing_tasks_use_the_vision_model_and_need_a_provider(self):
        clear = {"ARCHIE_AI_PROVIDER": "", "ARCHIE_AI_MODEL": "", "ARCHIE_AI_MODEL_DRAWINGS": "", "ARCHIE_AI_SMALL_MODEL": ""}
        with patch.dict("os.environ", clear):
            self.assertEqual(ai_provider.step_of("drawing:P2_north"), "DRAWINGS")
            self.assertEqual(ai_provider.model_for("drawing:P2_north"), "google/gemini-3.8-flash")
            # Outlines and corner picking go to the model that traced Casa Nova's tenancy (2026-10-24).
            for task in ("drawing:P0_room_outlines", "drawing:P0_outline_corners", "supervisor:P0_tenancy_outline", "drawing:P0_room_names"):
                self.assertEqual((ai_provider.step_of(task), ai_provider.model_for(task)), ("OUTLINES", "anthropic/claude-haiku-5.5"))
            self.assertFalse(drawing.uses_grid("P0_outline_corners", [(100, 100)]))
        with patch.dict("os.environ", {**clear, "OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "No AI provider is set up"):
                drawing.start(None, {"id": "j", "review_dir": "/nonexistent"})


if __name__ == "__main__":
    unittest.main()
