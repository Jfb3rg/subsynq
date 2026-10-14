import gzip
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


class PageSizeTests(unittest.TestCase):
    """The pages are 40-70 KB of text and the phone reaches them over a relay: compress them, and let the phone
    keep the icons instead of fetching nine files on every page and every reload."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = Path(folder.name)
        initialize_jobs(config / "subsynq.db")
        with sqlite3.connect(config / "subsynq.db") as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [
                ("instance_id", "test-instance"), ("created_at", "2026-01-01T00:00:00+00:00")])
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

    def get(self, route, **headers):
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{route}", headers=headers)
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, dict(reply.headers), reply.read()

    def test_a_big_page_is_compressed_when_the_browser_accepts_gzip(self):
        _, plain_headers, plain = self.get("/settings")
        status, headers, packed = self.get("/settings", **{"Accept-Encoding": "gzip, deflate, br"})
        self.assertEqual(status, 200)
        self.assertGreater(len(plain), 5000)
        self.assertNotIn("Content-Encoding", plain_headers)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        self.assertEqual(int(headers["Content-Length"]), len(packed))
        self.assertLess(len(packed), len(plain) / 3)
        self.assertEqual(gzip.decompress(packed), plain)

    def test_a_browser_without_gzip_gets_the_plain_page(self):
        _, headers, body = self.get("/settings", **{"Accept-Encoding": "identity"})
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_a_small_answer_is_not_compressed(self):
        _, headers, body = self.get("/api/scan-status", **{"Accept-Encoding": "gzip"})
        self.assertLess(len(body), server.GZIP_MIN_BYTES)
        self.assertNotIn("Content-Encoding", headers)

    def test_icons_can_be_kept_by_the_browser_for_a_day(self):
        for route in ("/icons/subsynq.svg", "/icons/whisper.png"):
            _, headers, body = self.get(route)
            self.assertEqual(headers["Cache-Control"], "public, max-age=86400", route)
            self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_pages_themselves_are_never_cached(self):
        _, headers, _ = self.get("/settings")
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
