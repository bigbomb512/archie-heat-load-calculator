"""The AI answers the drawing tasks in rounds: each round builds what is now possible and answers what is waiting; a
refused reply is tried once more with its reason; a task that keeps failing stops; every call is logged."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import ai_provider, autonomous_tasks_service as tasks_service, drawing_tasks_service as drawing


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


class DrawingTaskTests(unittest.TestCase):
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

    def test_grid_coordinates_become_image_pixels_by_the_image_or_the_chosen_crop(self):
        # Gemini read a 1,536 x 1,469 plan on a 0-1000 grid: the Kitchen's corner (480, 217) is pixel (737.3, 318.8).
        outline = {"rooms": [{"room": "Kitchen", "points_px": [[480, 217], [798, 585]], "open_sides": ""}]}
        self.assertEqual(drawing.to_pixels(outline, (1536, 1469))["rooms"][0]["points_px"], [[737.3, 318.8], [1225.7, 859.4]])
        north = {"found": True, "crop_index": 1, "tail_px": [471, 868], "tip_px": [471, 815], "labelled_north": False}
        sizes = [(800, 600), (1024, 724)]
        self.assertEqual(drawing._grid_size("P2_north", north, sizes), (1024, 724))
        self.assertEqual(drawing.to_pixels(north, (1024, 724))["tail_px"], [482.3, 628.4])
        self.assertTrue(drawing.uses_grid("P2_north", sizes) and drawing.uses_grid("P0_room_outlines", [(10, 10)]))
        self.assertFalse(drawing.uses_grid("P4_openings", sizes))                 # several images, no index: left as is
        self.assertIsNone(drawing._grid_size("P4_openings", north, sizes))
        self.assertEqual(drawing.to_pixels({"count": 3, "label": "x"}, (100, 100)), {"count": 3, "label": "x"})
        self.assertIn("0-1000 grid", drawing._prompt({"prompt": "Draw.", "reply_attempts": []}, grid=True))
        self.assertNotIn("grid", drawing._prompt({"prompt": "Draw.", "reply_attempts": []}, grid=False))

    def test_drawing_tasks_use_the_vision_model_and_need_a_provider(self):
        clear = {"ARCHIE_AI_PROVIDER": "", "ARCHIE_AI_MODEL": "", "ARCHIE_AI_MODEL_DRAWINGS": "", "ARCHIE_AI_SMALL_MODEL": ""}
        with patch.dict("os.environ", clear):
            self.assertEqual(ai_provider.step_of("drawing:P0_room_outlines"), "DRAWINGS")
            self.assertEqual(ai_provider.model_for("drawing:P0_room_outlines"), "google/gemini-3.8-flash")
        with patch.dict("os.environ", {**clear, "OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "No AI provider is set up"):
                drawing.start(None, {"id": "j", "review_dir": "/nonexistent"})


if __name__ == "__main__":
    unittest.main()
