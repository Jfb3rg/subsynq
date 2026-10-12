import json
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
from jobs import initialize_jobs, latest_jobs

MISSING = {"path": "/movies/saknas.mkv", "name": "saknas.mkv", "kind": "movie", "size_bytes": 1,
           "ai_sv_exists": False, "embedded_sv_exists": False, "probe": {}}
HAS_AI = {"path": "/movies/har-ai.mkv", "name": "har-ai.mkv", "kind": "movie", "size_bytes": 1,
          "ai_sv_exists": True, "ai_sv_path": "/movies/har-ai.ai.sv.srt", "embedded_sv_exists": False, "probe": {}}
EMBEDDED = {"path": "/movies/inbaddad.mkv", "name": "inbaddad.mkv", "kind": "movie", "size_bytes": 1,
            "ai_sv_exists": False, "embedded_sv_exists": True, "probe": {}}


class BulkRenderTests(unittest.TestCase):
    def test_checkbox_flags_follow_what_each_row_can_do(self):
        missing = server.render_media_row(MISSING, token="safe")
        self.assertIn("class='bulk-check'", missing)
        self.assertIn("form='bulk-form'", missing)
        self.assertIn("data-start='1' data-delete='0'", missing)
        has_ai = server.render_media_row(HAS_AI, token="safe")
        self.assertIn("data-start='0' data-delete='1'", has_ai)
        self.assertNotIn("bulk-check", server.render_media_row(EMBEDDED, token="safe"))
        self.assertNotIn("bulk-check", server.render_media_row(MISSING, token=""))

    def test_no_checkbox_while_a_job_is_active(self):
        job = {"id": 1, "status": "transcribing", "progress": 5, "message": "", "hardware": None, "model": None}
        self.assertNotIn("bulk-check", server.render_media_row(HAS_AI, token="safe", job=job))
        self.assertNotIn("bulk-check", server.render_media_row(MISSING, token="safe", job=job))

    def test_bulk_bar_translates_and_stays_quote_safe(self):
        english = server.render_bulk_bar("movie", "tok", "en")
        self.assertIn("Create subtitles for selected", english)
        self.assertIn("Queue {n} subtitle jobs?", english)
        self.assertNotIn("markerade", english)
        swedish = server.render_bulk_bar("movie", "tok", "sv")
        self.assertIn("Skapa undertext för markerade", swedish)
        for text in (english, swedish):
            for attribute in text.split("data-confirm=")[1:]:
                self.assertNotIn("'", attribute.split("'")[1])

    def test_bulk_bar_posts_to_one_form(self):
        bar = server.render_bulk_bar("movie", "tok<>")
        self.assertIn("id='bulk-form'", bar)
        self.assertIn("action='/jobs/bulk'", bar)
        self.assertIn("value='tok&lt;&gt;'", bar)
        self.assertIn("name='action' value='start'", bar)
        self.assertIn("name='action' value='delete'", bar)


class BulkRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        database = Path(self.folder.name) / "subsynq.db"
        initialize_jobs(database)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO metadata VALUES ('library_scan', ?)",
                       (json.dumps({"scanned_at": None, "items": [MISSING, HAS_AI, EMBEDDED]}),))
        patcher = mock.patch.object(server, "DATABASE", database)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.database = database
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, fields):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        body = urllib.parse.urlencode(fields, doseq=True).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}/jobs/bulk", body)
        try:
            urllib.request.build_opener(NoRedirect).open(request, timeout=10)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location") or error.read().decode()
        raise AssertionError("expected a redirect or error")

    def test_start_queues_only_eligible_files(self):
        status, location = self.post({"token": server.SCAN_TOKEN, "action": "start", "return_kind": "movie",
                                      "media_path": [MISSING["path"], EMBEDDED["path"], HAS_AI["path"],
                                                     "/movies/finns-inte.mkv", MISSING["path"]]})
        self.assertEqual(status, 303)
        self.assertIn("job=bulk", location)
        self.assertIn("done=1", location)
        self.assertIn("skipped=3", location)
        jobs = latest_jobs(self.database)
        self.assertEqual(set(jobs), {MISSING["path"]})
        self.assertEqual(jobs[MISSING["path"]]["status"], "queued")

    def test_delete_queues_only_files_with_an_ai_subtitle(self):
        status, location = self.post({"token": server.SCAN_TOKEN, "action": "delete", "return_kind": "movie",
                                      "media_path": [MISSING["path"], HAS_AI["path"]]})
        self.assertEqual(status, 303)
        self.assertIn("done=1", location)
        jobs = latest_jobs(self.database)
        self.assertEqual(set(jobs), {HAS_AI["path"]})
        self.assertEqual(jobs[HAS_AI["path"]]["status"], "delete_queued")
        # A second click must not stack another job on a file that already has one.
        _, location = self.post({"token": server.SCAN_TOKEN, "action": "delete", "return_kind": "movie",
                                 "media_path": [HAS_AI["path"]]})
        self.assertIn("done=0", location)

    def test_rejects_bad_token_empty_selection_and_unknown_action(self):
        self.assertEqual(self.post({"token": "fel", "action": "start", "media_path": [MISSING["path"]]})[0], 403)
        self.assertEqual(self.post({"token": server.SCAN_TOKEN, "action": "start"})[0], 400)
        self.assertEqual(self.post({"token": server.SCAN_TOKEN, "action": "rm", "media_path": [MISSING["path"]]})[0], 400)
        self.assertEqual(latest_jobs(self.database), {})


if __name__ == "__main__":
    unittest.main()
