"""HTTP byte ranges used by the landing page's scroll-scrubbed video."""
import http.client
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.web_app import Handler


class VideoServingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.content = bytes(range(256)) * 8
        Path(cls.temp.name, "tour.mp4").write_bytes(cls.content)
        Path(cls.temp.name, "page.html").write_text("hello")
        cls.root_patch = patch("backend.web_app.ROOT", Path(cls.temp.name))
        cls.root_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.root_patch.stop()
        cls.temp.cleanup()

    def request(self, range_value=None, method="GET", path="/tour.mp4"):
        connection = http.client.HTTPConnection(*self.server.server_address)
        connection.request(method, path, headers={"Range": range_value} if range_value else {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_full_video(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(body, self.content)

    def test_ranges(self):
        for value, start, end in [("bytes=0-1", 0, 1), ("bytes=200-399", 200, 399),
                                  ("bytes=2000-", 2000, 2047), ("bytes=-12", 2036, 2047),
                                  ("bytes=2000-9999", 2000, 2047)]:
            with self.subTest(value=value):
                status, headers, body = self.request(value)
                self.assertEqual(status, 206)
                self.assertEqual(headers["Content-Range"], f"bytes {start}-{end}/2048")
                self.assertEqual(int(headers["Content-Length"]), end - start + 1)
                self.assertEqual(body, self.content[start:end + 1])

    def test_unsatisfiable_ranges(self):
        for value in ["bytes=2048-", "bytes=4-2", "bytes=-0"]:
            status, headers, body = self.request(value)
            self.assertEqual(status, 416)
            self.assertEqual(headers["Content-Range"], "bytes */2048")
            self.assertEqual(body, b"")

    def test_head_and_other_files(self):
        status, headers, body = self.request("bytes=0-1", method="HEAD")
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Length"], "2")
        self.assertEqual(body, b"")
        self.assertEqual(self.request(path="/page.html")[2], b"hello")
        self.assertEqual(self.request(path="/missing.mp4")[0], 404)

    def test_unsupported_range_falls_back_to_full_video(self):
        self.assertEqual(self.request("bytes=0-1,4-5")[::2], (200, self.content))


if __name__ == "__main__":
    unittest.main()
