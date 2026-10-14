import re
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
from jobs import initialize_jobs


class RequestTimeTextTests(unittest.TestCase):
    def test_a_fast_request_ends_with_its_time(self):
        self.assertEqual(server.format_request_time(0.0123), " 0.012s")
        self.assertEqual(server.format_request_time(0), " 0.000s")

    def test_a_slow_request_is_marked(self):
        self.assertEqual(server.format_request_time(server.SLOW_REQUEST_SECONDS - .001),
                         f" {server.SLOW_REQUEST_SECONDS - .001:.3f}s")
        self.assertEqual(server.format_request_time(2.5), " 2.500s LÅNGSAM")
        self.assertTrue(server.format_request_time(server.SLOW_REQUEST_SECONDS).endswith("LÅNGSAM"))


class AccessLogTimeTests(unittest.TestCase):
    """The real Handler: every access-log line carries the time the server worked on the request."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = Path(folder.name)
        initialize_jobs(config / "subsynq.db")
        with sqlite3.connect(config / "subsynq.db") as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for name, value in (("CONFIG", config), ("DATABASE", config / "subsynq.db")):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lines = []
        patcher = mock.patch.object(server.Handler, "log_message",
                                    lambda handler, fmt, *args: self.lines.append(fmt % args))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, route):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.httpd.server_port}{route}", timeout=10) as reply:
            return reply.status, reply.read()

    def test_the_line_keeps_the_standard_parts_and_ends_with_seconds(self):
        self.assertEqual(self.get("/api/scan-status")[0], 200)
        self.assertEqual(len(self.lines), 1)
        self.assertRegex(self.lines[0], r'^"GET /api/scan-status HTTP/1\.\d" 200 (?:\d+|-) \d+\.\d{3}s$')

    def test_a_slow_answer_is_marked_in_the_line(self):
        calls = []

        def monotonic():
            calls.append(1)
            return 100.0 if len(calls) == 1 else 103.2     # the request starts at 100.0, the answer starts at 103.2

        with mock.patch.object(server.time, "monotonic", side_effect=monotonic):
            self.get("/api/scan-status")
        self.assertRegex(self.lines[0], r'" 200 (?:\d+|-) 3\.200s LÅNGSAM$')

    def test_a_missing_page_is_timed_too(self):
        with self.assertRaises(Exception):
            self.get("/finns-inte")
        self.assertRegex(self.lines[0], r'^"GET /finns-inte HTTP/1\.\d" 404 (?:\d+|-) \d+\.\d{3}s$')


if __name__ == "__main__":
    unittest.main()
