"""A browser that leaves before its answer is sent is not an error (2026-11-20: two Broken pipe tracebacks per
dropped /api/job-plan poll)."""

import io
import sys
import unittest
from unittest.mock import patch

from backend import web_app


class _ClosedSocket(io.RawIOBase):
    def __init__(self):
        self.writes = 0

    def writable(self):
        return True

    def write(self, data):
        self.writes += 1
        raise BrokenPipeError(32, "Broken pipe")


def handler():
    item = web_app.Handler.__new__(web_app.Handler)
    item.wfile = _ClosedSocket()
    item.request_version = "HTTP/1.1"
    item.requestline = "GET /api/job-plan HTTP/1.1"
    item.command, item.path = "GET", "/api/job-plan"
    item.client_address = ("127.0.0.1", 1)
    item.close_connection = False
    item.log_request = lambda *args, **kwargs: None
    return item


class ClientGoneTests(unittest.TestCase):
    def test_a_closed_connection_drops_the_answer_quietly_and_no_error_answer_follows(self):
        item = handler()
        item.send_json({"rooms": []})                # does not raise into the route's error handler
        self.assertTrue(item.close_connection)
        writes = item.wfile.writes
        item.send_json({"error": "x"}, 400)          # the route's error answer is not attempted on the closed socket
        self.assertEqual(item.wfile.writes, writes)

    def test_the_server_stays_quiet_about_a_dropped_connection_but_reports_real_errors(self):
        server = web_app.ArchieHTTPServer.__new__(web_app.ArchieHTTPServer)
        with patch("socketserver.BaseServer.handle_error") as report:
            try:
                raise BrokenPipeError(32, "Broken pipe")
            except BrokenPipeError:
                server.handle_error(None, ("127.0.0.1", 1))
            report.assert_not_called()
            try:
                raise ValueError("real")
            except ValueError:
                server.handle_error(None, ("127.0.0.1", 1))
            report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
