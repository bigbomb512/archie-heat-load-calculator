"""Connection failures with the AI service: a real local HTTP server (no outside network) checks the request path,
how connection failures are classified and retried, and that the drawing tasks treat them as unanswered, not as
anything learned about the drawing."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from backend import ai_provider, autonomous_tasks_service as tasks_service, drawing_tasks_service as drawing
from backend.job_runner import write_json
from tests.test_drawing_tasks import FakeTasks

REPLY = {"choices": [{"message": {"content": '{"north_bearing_deg": 12}'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


class _Server:
    """A local HTTP server that answers normally, or closes the connection without answering."""

    def __init__(self, behaviour="answer"):
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                outer.requests += 1
                self.rfile.read(int(self.headers["Content-Length"]))
                if behaviour == "reset":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                body = json.dumps(REPLY).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _provider(url, deadline_s=300, opener=None):
    class Local(ai_provider.OpenRouterProvider):
        def url(self):
            return url
    provider = Local(api_key="or-test", opener=opener)
    provider.deadline_s = deadline_s
    return provider


def _closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ProviderTransportTests(unittest.TestCase):
    def test_a_real_http_reply_is_read_with_the_drawing_time_limit_on(self):
        # Regression: the time limit used to set a timeout on urllib's already-closed socket object, and every
        # drawing call failed with "[Errno 9] Bad file descriptor" after its reply had arrived (Butcher Buffet).
        server = _Server()
        self.addCleanup(server.close)
        self.assertEqual(_provider(server.url).propose("x")[0], {"north_bearing_deg": 12})
        self.assertEqual(_provider(server.url, deadline_s=None).propose("x")[0], {"north_bearing_deg": 12})

    def test_a_connection_closed_after_sending_is_a_transport_error_and_is_not_sent_again(self):
        server = _Server("reset")
        self.addCleanup(server.close)
        with self.assertRaises(ai_provider.ProviderTransportError) as caught:
            _provider(server.url).propose("x")
        self.assertTrue(caught.exception.may_have_run)        # the service may have run (and charged) it
        self.assertEqual(server.requests, 1)                   # so it is not sent again automatically
        self.assertIn("The connection to the openrouter AI service failed", str(caught.exception))

    def test_a_refused_connection_is_tried_again_with_fresh_requests_and_bounded_waits(self):
        url = f"http://127.0.0.1:{_closed_port()}/"
        attempts, made = [], []

        def counting(request, timeout=None):
            attempts.append(time.monotonic())
            made.append(id(request))
            return urllib.request.urlopen(request, timeout=timeout)

        with patch.object(ai_provider, "CONNECT_RETRY_DELAYS_S", (0.05, 0.1)):
            started = time.monotonic()
            with self.assertRaises(ai_provider.ProviderTransportError) as caught:
                _provider(url, opener=counting).propose("x")
        self.assertFalse(caught.exception.may_have_run)       # never reached the service: nothing charged
        self.assertEqual(len(attempts), 3)                     # first try + two retries, then it stops
        self.assertEqual(len(set(made)), 3)                    # a fresh request each time
        self.assertGreaterEqual(attempts[1] - attempts[0], 0.05)
        self.assertGreaterEqual(attempts[2] - attempts[1], 0.1)
        self.assertLess(time.monotonic() - started, 2)

    def test_retry_waits_never_go_past_the_whole_request_limit(self):
        url = f"http://127.0.0.1:{_closed_port()}/"
        attempts = []

        def counting(request, timeout=None):
            attempts.append(1)
            return urllib.request.urlopen(request, timeout=timeout)

        with patch.object(ai_provider, "CONNECT_RETRY_DELAYS_S", (5, 5)):
            started = time.monotonic()
            with self.assertRaises(ai_provider.ProviderTransportError):
                _provider(url, deadline_s=1, opener=counting).propose("x")
        self.assertEqual(len(attempts), 1)                     # a 5 s wait would pass the 1 s limit, so no retry
        self.assertLess(time.monotonic() - started, 1)

    def test_the_usage_log_says_the_connection_failed(self):
        server = _Server("reset")
        self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ai_provider.ProviderTransportError):
                ai_provider.recorded(_provider(server.url), folder, "drawing:P2_north").propose("x")
            row = json.loads((Path(folder) / ai_provider.USAGE_FILE).read_text())
        self.assertEqual((row["error"], row["ok"]), ("connection", False))


class DrawingConnectionFailureTests(unittest.TestCase):
    def run_tasks(self, folder, provider, fake=None):
        fake = fake or FakeTasks(folder)
        write_json(Path(folder) / drawing.JOB_FILE, {"job_id": "j1", "status": "running", "step": "answer"})
        with patch.object(tasks_service, "post", fake.post), patch.object(tasks_service, "_all_current", fake.all_current), \
                patch.object(tasks_service, "_current_task", fake.current), patch.object(tasks_service, "_task_dir", fake.task_dir):
            result = drawing.answer_all(None, {"id": "j", "review_dir": folder}, provider_for=lambda task: provider)
        return fake, result, json.loads((Path(folder) / drawing.JOB_FILE).read_text())

    def test_one_failed_connection_leaves_that_task_unanswered_and_the_others_carry_on(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            class OnceBroken:
                model = "fake/vision"

                def propose(self, prompt, image_paths=()):
                    calls.append(prompt)
                    if "P0_dimensions" in prompt and len(calls) == 1:
                        raise ai_provider.ProviderTransportError("openrouter", OSError(9, "Bad file descriptor"))
                    return {"answer": "ok"}, {"usage": {}}

            fake, result, job = self.run_tasks(folder, OnceBroken())
        # The outline task was still answered in round 1, and the scale task got its one retry and was applied.
        self.assertEqual(fake.records[("P0_dimensions", "page-20-dimension-1")]["status"], "applied")
        self.assertEqual(len(fake.records[("P0_room_outlines", "page-20")]["reply_attempts"]), 2)
        self.assertFalse(result["connection_stopped"])
        # The connection recovered; the separately refused outline stays an explicit exception.
        self.assertEqual([row["kind"] for row in result["unresolved"]], ["answer_rejected"])
        self.assertEqual(result["unresolved"][0]["task"], "P0_room_outlines")

    def test_repeated_connection_failures_stop_the_calls_and_say_nothing_about_the_drawings(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTasks(folder)
            fake.add("P2_north", "page-25")
            fake.add("P1_site", "project")
            calls = []

            class Broken:
                model = "fake/vision"

                def propose(self, prompt, image_paths=()):
                    calls.append(prompt)
                    raise ai_provider.ProviderTransportError("openrouter", OSError(9, "Bad file descriptor"))

            fake, result, job = self.run_tasks(folder, Broken(), fake)
        self.assertEqual(len(calls), drawing.MAX_CONNECTION_FAILURES_IN_A_ROW)   # stopped, not one call per task
        self.assertTrue(result["connection_stopped"])
        self.assertEqual(result["not_tried"], 1)
        self.assertEqual({row["kind"] for row in result["unresolved"]}, {"connection"})
        # Nothing was recorded as a reply or a finding: every task is still waiting, with no attempts.
        for record in fake.records.values():
            self.assertEqual((record["status"], record["reply_attempts"]), ("waiting_for_reply", []))
        self.assertIn("Nothing was concluded about the drawings", job["last_problem"])
        north = [row for row in result["unresolved"] if row["task"] == "P2_north"]
        if north:
            self.assertIn("connection to the AI service failed", north[0]["message"])

    def test_each_failure_kind_has_its_own_plain_message(self):
        record = {"task": "P2_north", "target": "page-25"}
        kinds = {
            "connection": ai_provider.ProviderTransportError("openrouter", OSError(9, "Bad file descriptor")),
            "timeout": ai_provider.ProviderTimeout(300),
            "unreadable_reply": ValueError("not JSON"),
            "service_error": RuntimeError("The openrouter API returned HTTP 500: oops"),
        }
        for kind, error in kinds.items():
            self.assertEqual(drawing.failure_kind(error), kind)
        self.assertEqual(
            drawing.failure_message(record, "connection", kinds["connection"], again=False),
            "Finding north (page 25) couldn't get an answer: the connection to the AI service failed. "
            "Nothing was concluded about the drawing. Try again to send it again.")
        self.assertIn("It will be tried once more.",
                      drawing.failure_message(record, "timeout", kinds["timeout"], again=True))


if __name__ == "__main__":
    unittest.main()
