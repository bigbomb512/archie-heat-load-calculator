"""Drawing tasks: a whole-request time limit on each AI call, live per-task progress in the job file, and plain text
when a call times out. No network is used; providers and the task store are fakes."""

import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error

from backend import ai_provider, autonomous_tasks_service as tasks_service, drawing_tasks_service as drawing
from backend.job_runner import write_json
from tests.test_drawing_tasks import FakeTasks


class StalledResponse:
    """A host that answers the headers and then goes quiet (what held the Casa Nova run for 676 s)."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        time.sleep(3)
        return b"{}"


class WholeReplyResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ProviderTimeLimitTests(unittest.TestCase):
    def test_a_stalled_reply_is_stopped_at_the_whole_request_limit(self):
        provider = ai_provider.OpenRouterProvider(api_key="or-test", opener=lambda request, timeout=None: StalledResponse())
        provider.deadline_s = 0.3
        started = time.monotonic()
        with self.assertRaises(ai_provider.ProviderTimeout) as caught:
            provider.propose("x")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("No reply from the AI within 0.3 seconds", str(caught.exception))

    def test_a_silent_socket_or_connect_timeout_is_reported_as_a_timeout(self):
        def silent(request, timeout=None):
            raise TimeoutError("The read operation timed out")

        def unreachable(request, timeout=None):
            raise urllib.error.URLError(TimeoutError("timed out"))

        for opener in (silent, unreachable):
            provider = ai_provider.OpenRouterProvider(api_key="or-test", opener=opener)
            provider.deadline_s = 300
            with self.assertRaises(ai_provider.ProviderTimeout):
                provider.propose("x")

    def test_the_request_waits_no_longer_than_the_limit_and_a_whole_reply_is_read(self):
        seen = []
        reply = {"choices": [{"message": {"content": '{"answer": "ok"}'}}], "usage": {}}

        def open_(request, timeout=None):
            seen.append(timeout)
            return WholeReplyResponse(json.dumps(reply).encode())

        provider = ai_provider.OpenRouterProvider(api_key="or-test", opener=open_)
        provider.deadline_s = 300
        self.assertEqual(provider.propose("x")[0], {"answer": "ok"})
        self.assertEqual(len(seen), 1)
        self.assertTrue(299 < seen[0] <= 300)

    def test_only_drawing_calls_get_the_limit_and_it_can_be_set(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test", "ARCHIE_AI_PROVIDER": "openrouter"}, clear=False):
            os.environ.pop("ARCHIE_DRAWING_TIMEOUT_S", None)
            drawing_provider = ai_provider.get("drawing:P0_room_outlines")
            self.assertEqual((drawing_provider.deadline_s, drawing_provider.timeout), (300, 300))
            self.assertIsNone(ai_provider.get("pass1").deadline_s)
            self.assertEqual(ai_provider.get("skill:surface_area").timeout, ai_provider.TIMEOUT_S)
            with patch.dict(os.environ, {"ARCHIE_DRAWING_TIMEOUT_S": "5"}):
                self.assertEqual(ai_provider.get("drawing:P2_north").deadline_s, 30)    # never below 30 s
            with patch.dict(os.environ, {"ARCHIE_DRAWING_TIMEOUT_S": "240"}):
                self.assertEqual(ai_provider.get("drawing:P2_north").deadline_s, 240)

    def test_the_usage_log_says_a_call_timed_out(self):
        class Slow:
            name, model = "openrouter", "google/gemini-3.8-flash"

            def propose(self, prompt, image_paths=()):
                raise ai_provider.ProviderTimeout(300)

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ai_provider.ProviderTimeout):
                ai_provider.recorded(Slow(), folder, "drawing:P0_room_outlines").propose("x")
            row = json.loads((Path(folder) / ai_provider.USAGE_FILE).read_text())
        self.assertEqual((row["error"], row["ok"]), ("timeout", False))
        self.assertIn("300 seconds", row["detail"])


class DrawingProgressTests(unittest.TestCase):
    def run_tasks(self, folder, provider):
        fake = FakeTasks(folder)
        write_json(Path(folder) / drawing.JOB_FILE, {"job_id": "j1", "status": "running", "step": "answer"})
        with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir):
            result = drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: provider)
        return fake, result, json.loads((Path(folder) / drawing.JOB_FILE).read_text())

    def test_progress_is_saved_before_each_call_and_after_each_result(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshots = []

            class Watching:
                model = "fake/vision"

                def propose(self, prompt, image_paths=()):
                    snapshots.append(json.loads((Path(folder) / drawing.JOB_FILE).read_text()))
                    return {"answer": "ok"}, {"usage": {}}

            _fake, result, job = self.run_tasks(folder, Watching())
        first = snapshots[0]
        self.assertEqual((first["current_task"], first["current_task_label"], first["task_number"],
                          first["tasks_in_round"], first["tasks_done"], first["tasks_remaining"], first["rounds"]),
                         ("P0_dimensions", "Reading the scale from a printed dimension", 1, 2, 0, 2, 1))
        self.assertTrue(first["request_started_at"])
        second = snapshots[1]
        self.assertEqual((second["current_task"], second["tasks_done"], second["answered"], second["calls"]),
                         ("P0_room_outlines", 1, 1, 1))
        # Finished: counts kept, nothing shown as in progress.
        self.assertEqual((job["answered"], job["refused"], job["calls"]), (result["answered"], result["refused"], result["calls"]))
        self.assertIsNone(job["current_task"])
        self.assertIsNone(job["request_started_at"])
        self.assertTrue(job["last_progress_at"])

    def test_a_timed_out_call_is_tried_once_more_then_left_for_a_person_with_plain_text(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            class OutlinesTimeOut:
                model = "fake/vision"

                def propose(self, prompt, image_paths=()):
                    calls.append(prompt)
                    if "P0_room_outlines" in prompt:
                        raise ai_provider.ProviderTimeout(300)
                    return {"answer": "ok"}, {"usage": {}}

            fake, result, job = self.run_tasks(folder, OutlinesTimeOut())
            log = [json.loads(line) for line in (Path(folder) / ai_provider.USAGE_FILE).read_text().splitlines()]
        outline_calls = [prompt for prompt in calls if "P0_room_outlines" in prompt]
        self.assertEqual(len(outline_calls), 2)                     # bounded: one retry in the next round, no more
        self.assertEqual(fake.records[("P0_room_outlines", "page-20")]["status"], "waiting_for_reply")   # evidence kept
        self.assertEqual(job["last_problem"],
                         "Tracing the room outlines (page 20) got no reply within 300 seconds. Nothing was concluded "
                         "about the drawing. Try again to send it again.")
        self.assertEqual([(row["task"], row["kind"]) for row in job["unresolved"]], [("P0_room_outlines", "timeout")])
        self.assertEqual(sum(row.get("error") == "timeout" for row in log), 2)
        self.assertEqual(fake.records[("P0_room_names", "page-20")]["status"], "applied")   # other tasks carried on

    def test_status_gives_the_ages_of_the_current_request_and_last_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "p", "review_dir": folder}
            started = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 125))
            started = started[:-2] + ":" + started[-2:]
            write_json(Path(folder) / drawing.JOB_FILE, {
                "job_id": "j1", "status": "running", "pid": os.getpid(), "current_task": "P0_room_outlines",
                "request_started_at": started, "last_progress_at": started})
            drawing._JOB.running.add(drawing._JOB.key(project))
            try:
                state = drawing.status(None, project)
            finally:
                drawing._JOB.running.discard(drawing._JOB.key(project))
        self.assertAlmostEqual(state["request_seconds"], 125, delta=3)
        self.assertAlmostEqual(state["seconds_since_progress"], 125, delta=3)
        self.assertEqual(state["request_limit_s"], 300)

    def test_the_task_target_is_named_by_its_page(self):
        self.assertEqual(drawing._target_text({"target": "page-20-dimension-1"}), "page 20")
        self.assertEqual(drawing._target_text({"target": ""}), "the drawing set")


if __name__ == "__main__":
    unittest.main()
