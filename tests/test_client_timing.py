import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
from jobs import initialize_jobs


def form(**fields):
    return urllib.parse.parse_qs(urllib.parse.urlencode(fields))


class ClientTimingTextTests(unittest.TestCase):
    def test_the_browsers_numbers_become_one_readable_line(self):
        line = server.format_client_timing(form(
            page="/queue", type="navigate", protocol="http/1.0", ect="4g", total="6120", connect="3003", ttfb="28",
            download="10", dom="350", load="300", rtt="100", size="12928", slowest="/icons/gpu.svg", slowest_ms="480"))
        self.assertEqual(
            line, "Klienttid /queue (navigate, http/1.0, nät 4g): total 6120 ms, connect 3003 ms, ttfb 28 ms, "
                  "download 10 ms, dom 350 ms, load 300 ms, rtt 100 ms, 12928 B, långsammast /icons/gpu.svg 480 ms")

    def test_missing_nonsense_and_hostile_values_never_reach_the_log_as_such(self):
        line = server.format_client_timing(form(
            page="/q\n<script>alert(1)</script>", type="x\r\ny", protocol="h2; DROP", ect="4g\n", total="abc",
            connect="-5", ttfb="99999999999", dom="12.7", slowest="/a b\nc", slowest_ms="5", size="12;ls"))
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertNotIn("<", line)
        self.assertNotIn("total", line)
        self.assertNotIn("connect", line)
        self.assertNotIn("ttfb", line)
        self.assertIn("dom 12 ms", line)
        self.assertIn("12 B", line)

    def test_nothing_measured_is_said_plainly(self):
        self.assertEqual(server.format_client_timing({}), "Klienttid ? (?, ?, nät ?): inga tider")


class ClientTimingRouteTests(unittest.TestCase):
    """The real Handler: the beacon the page sends after it has loaded."""

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
        patcher = mock.patch.object(server.Handler, "log_message", lambda *args: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def beacon(self, **fields):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}/api/client-timing",
            urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return reply.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_a_beacon_is_logged_and_answered_without_content(self):
        with self.assertLogs(level="INFO") as logged:
            self.assertEqual(self.beacon(page="/queue", type="navigate", total="6100", connect="3000"), 204)
        self.assertTrue(any("Klienttid /queue (navigate" in line and "connect 3000 ms" in line
                            for line in logged.output), logged.output)

    def test_without_a_session_the_beacon_is_refused_when_a_password_is_set(self):
        with mock.patch.object(server, "auth_settings", return_value=("hash", "secret")):
            with self.assertNoLogs(level="INFO"):
                self.assertEqual(self.beacon(page="/queue", total="1"), 403)

    def test_the_page_sends_the_beacon_after_it_has_loaded(self):
        page = Path(server.__file__).with_name("index.html").read_text(encoding="utf-8")
        self.assertIn("navigator.sendBeacon('/api/client-timing'", page)
        self.assertIn("addEventListener('load'", page)


if __name__ == "__main__":
    unittest.main()
