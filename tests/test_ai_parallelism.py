"""Local HTTP rate-limit recovery, without external provider traffic."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch

from backend import ai_provider


@contextmanager
def rate_server(retry_after=None, reject=2, status=429):
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            hits.append(True)
            limited = len(hits) <= reject
            self.send_response(status if limited else 200)
            self.send_header("Content-Type", "application/json")
            if limited and retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "too many requests"} if limited else {"ok": True}).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits
    finally:
        server.shutdown(); server.server_close(); worker.join()


class AIParallelismTests(unittest.TestCase):
    def test_default_and_override_are_shared_at_call_time(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(ai_provider.parallel_calls(), 8)
        for raw, expected in [("3", 3), ("0", 1), ("wrong", 8)]:
            with patch.dict("os.environ", {"ARCHIE_AI_PARALLEL_CALLS": raw}):
                self.assertEqual(ai_provider.parallel_calls(), expected)

    def test_429_honours_retry_after_and_eventually_returns_one_answer(self):
        with rate_server(".01") as (url, hits), patch.object(ai_provider.random, "uniform") as jitter:
            provider = ai_provider.OpenAIProvider(api_key="local-fake", timeout=2)
            self.assertEqual(provider._post(url, {}, {}), {"ok": True})
        self.assertEqual(len(hits), 3)
        jitter.assert_not_called()

    def test_429_without_retry_after_uses_exponential_backoff_with_jitter(self):
        with rate_server() as (url, hits), patch.object(ai_provider.random, "uniform", return_value=0) as jitter:
            provider = ai_provider.OpenAIProvider(api_key="local-fake", timeout=5)
            self.assertEqual(provider._post(url, {}, {}), {"ok": True})
        self.assertEqual(len(hits), 3)
        self.assertEqual([call.args for call in jitter.call_args_list], [(0, .25), (0, .5)])

    def test_too_many_requests_message_is_retried_even_with_another_http_status(self):
        with rate_server(".01", status=503) as (url, hits):
            provider = ai_provider.OpenAIProvider(api_key="local-fake", timeout=2)
            self.assertEqual(provider._post(url, {}, {}), {"ok": True})
        self.assertEqual(len(hits), 3)

    def test_rate_limit_wait_does_not_exceed_whole_request_deadline(self):
        with rate_server("120", reject=10) as (url, hits):
            provider = ai_provider.OpenAIProvider(api_key="local-fake", timeout=1)
            with self.assertRaises(ai_provider.ProviderTimeout):
                provider._post(url, {}, {})
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
