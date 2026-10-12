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
import scan_state
import server
from i18n import STRINGS
from jobs import initialize_jobs


def item(path):
    return {"path": path, "name": Path(path).name, "kind": "movie", "size_bytes": 1,
            "ai_sv_exists": False, "embedded_sv_exists": False, "probe": {}}


class RootTests(unittest.TestCase):
    def test_one_library_maps_to_one_folder_and_all_leaves_out_an_unconnected_extra(self):
        self.assertEqual(scan_state.roots_for("movie", lambda r: False), ["/movies"])
        self.assertEqual(scan_state.roots_for("episode", lambda r: False), ["/tv"])
        self.assertEqual(scan_state.roots_for("media", lambda r: False), ["/extra"])
        self.assertEqual(scan_state.roots_for("all", lambda r: False), ["/movies", "/tv"])
        self.assertEqual(scan_state.roots_for("all", lambda r: True), ["/movies", "/tv", "/extra"])
        with self.assertRaises(ValueError):
            scan_state.roots_for("../etc", lambda r: True)


class MergeTests(unittest.TestCase):
    PREVIOUS = {"scanned_at": "old", "items": [item("/movies/a.mkv"), item("/tv/s/e1.mkv"), item("/extra/x.mkv")]}

    def test_scanning_one_library_keeps_what_the_others_found(self):
        merged = scan_state.merge_scan(self.PREVIOUS, ["/movies"], [item("/movies/b.mkv")], "new", False)
        self.assertEqual([i["path"] for i in merged["items"]], ["/movies/b.mkv", "/tv/s/e1.mkv", "/extra/x.mkv"])
        self.assertEqual(merged["scanned"], {"/movies": "new", "/tv": "old", "/extra": "old"})
        self.assertEqual(merged["scanned_at"], "new")

    def test_a_scan_of_everything_replaces_the_result(self):
        merged = scan_state.merge_scan(self.PREVIOUS, ["/movies", "/tv"], [item("/tv/z.mkv")], "new", True)
        self.assertEqual([i["path"] for i in merged["items"]], ["/tv/z.mkv"])
        self.assertEqual(merged["scanned"], {"/movies": "new", "/tv": "new"})

    def test_folders_are_matched_on_whole_names_and_the_order_follows_the_libraries(self):
        previous = {"items": [item("/movies-old/a.mkv"), item("/extra/x.mkv")]}
        merged = scan_state.merge_scan(previous, ["/movies"], [item("/movies/n.mkv")], "t", False)
        self.assertEqual([i["path"] for i in merged["items"]], ["/movies/n.mkv", "/extra/x.mkv", "/movies-old/a.mkv"])

    def test_first_scan_without_a_stored_result(self):
        merged = scan_state.merge_scan(None, ["/tv"], [item("/tv/a.mkv")], "t", False)
        self.assertEqual(merged, {"scanned_at": "t", "scanned": {"/tv": "t"}, "items": [item("/tv/a.mkv")]})
        self.assertEqual(scan_state.scanned_at_for(merged, "/tv"), "t")
        self.assertIsNone(scan_state.scanned_at_for(merged, "/movies"))
        self.assertEqual(scan_state.scanned_at_for({"scanned_at": "old"}, "/movies"), "old")


class StatusTests(unittest.TestCase):
    def test_status_follows_a_scan_from_start_to_finish_and_failure(self):
        clock = [100.0]
        status = scan_state.ScanStatus(now=lambda: clock[0])
        self.assertEqual(status.snapshot()["state"], "idle")
        status.start("all", ["/movies", "/tv"])
        status.progress("/movies", 3)
        status.progress("/tv", 4)
        status.progress("/tv", 6)
        clock[0] = 112.0
        snapshot = status.snapshot()
        self.assertEqual((snapshot["state"], snapshot["folder"], snapshot["files"], snapshot["seconds"]),
                         ("running", "/tv", 9, 12))
        self.assertTrue(status.running)
        status.finish(9)
        clock[0] = 500.0
        snapshot = status.snapshot()
        self.assertEqual((snapshot["state"], snapshot["seconds"]), ("done", 12))
        status.start("movie", ["/movies"])
        status.fail("Biblioteksmappen saknas: /movies")
        self.assertEqual(status.snapshot()["state"], "error")
        self.assertFalse(status.running)

    def test_banner_shows_while_running_and_briefly_after_but_not_when_idle(self):
        clock = [0.0]
        status = scan_state.ScanStatus(now=lambda: clock[0])
        self.assertEqual(scan_state.render_scan_banner(status.snapshot(), "sv", now=0), "")
        status.start("movie", ["/movies"])
        status.progress("/movies", 5)
        html = scan_state.render_scan_banner(status.snapshot(), "sv", now=1)
        self.assertIn("data-scan-card", html)
        self.assertIn("Skannar filmer", html)
        self.assertIn("/movies: 5 filer", html)
        status.finish(5)
        clock[0] = 10.0
        self.assertIn("Skanningen av filmer är klar", scan_state.render_scan_banner(status.snapshot(), "sv", now=10))
        self.assertEqual(scan_state.render_scan_banner(status.snapshot(), "sv", now=10 + 61), "")
        status.start("all", ["/movies"])
        status.fail("Biblioteksmappen saknas: <b>/movies</b>")
        html = scan_state.render_scan_banner(status.snapshot(), "sv", now=11)
        self.assertIn("Skanningen misslyckades", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertIn("Scanning the whole library", scan_state.render_scan_banner(
            {**status.snapshot(), "state": "running", "choice": "all"}, "en", now=11))

    def test_every_new_text_has_both_languages(self):
        for key, entry in STRINGS.items():
            if key.startswith("scan_"):
                self.assertTrue(entry.get("sv") and entry.get("en"), key)


class ServerScanTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "subsynq.db"
        initialize_jobs(self.database)
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO metadata VALUES ('library_scan', ?)", (json.dumps(
                {"scanned_at": "old", "items": [item("/movies/a.mkv"), item("/tv/s/e1.mkv")]}),))
        for patcher in (mock.patch.object(server, "DATABASE", self.database),
                        mock.patch.object(server, "CONFIG", Path(self.folder.name)),
                        mock.patch.object(server, "SCAN_STATUS", scan_state.ScanStatus())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def stored(self):
        with sqlite3.connect(self.database) as db:
            return json.loads(db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()[0])

    def test_scanning_the_series_folder_only_reads_that_folder_and_keeps_the_movies(self):
        asked = []

        def fake_scan(roots, probe=None, kind_overrides=None, progress=None):
            asked.append(list(roots))
            progress("/tv", 1)
            return [item("/tv/s/e2.mkv")]

        with mock.patch.object(server, "scan_library", fake_scan):
            result = server.perform_library_scan("episode")
        self.assertEqual(asked, [["/tv"]])
        self.assertEqual([i["path"] for i in self.stored()["items"]], ["/movies/a.mkv", "/tv/s/e2.mkv"])
        self.assertEqual(result["scanned"]["/tv"], self.stored()["scanned_at"])
        snapshot = server.SCAN_STATUS.snapshot()
        self.assertEqual((snapshot["state"], snapshot["files"], snapshot["choice"]), ("done", 1, "episode"))

    def test_a_failed_scan_is_reported_and_keeps_the_previous_list(self):
        def broken(roots, **kwargs):
            raise FileNotFoundError("Biblioteksmappen saknas: /movies")

        with mock.patch.object(server, "scan_library", broken):
            with self.assertRaises(FileNotFoundError):
                server.perform_library_scan("movie")
        self.assertEqual(server.SCAN_STATUS.snapshot()["state"], "error")
        self.assertIn("/movies", server.SCAN_STATUS.snapshot()["message"])
        self.assertEqual(len(self.stored()["items"]), 2)
        self.assertFalse(server.SCAN_LOCK.locked())

    def test_scan_everything_skips_an_extra_folder_that_is_not_connected(self):
        asked = []
        not_connected = lambda path: mock.Mock(is_dir=lambda: path != "/extra")  # noqa: E731
        with mock.patch.object(server, "scan_library",
                               lambda roots, **kw: asked.append(list(roots)) or []), \
                mock.patch.object(server, "Path", not_connected):
            server.perform_library_scan()
        self.assertEqual(asked, [["/movies", "/tv"]])

    def test_buttons_scan_one_library_or_everything_and_lock_while_scanning(self):
        html = server.render_scan_buttons("episode", {"scanned_at": "2026-01-01T00:00:00+00:00"}, False, "tok", "sv")
        self.assertIn("name='library' value='episode'", html)
        self.assertIn("Skanna serier", html)
        self.assertIn("name='library' value='all'", html)
        self.assertIn("Skanna allt", html)
        self.assertEqual(html.count(" disabled"), 0)
        busy = server.render_scan_buttons("movie", {}, True, "tok", "sv")
        self.assertEqual(busy.count(" disabled"), 2)
        with mock.patch.object(server, "Path", lambda p: mock.Mock(is_dir=lambda: False)):
            extra = server.render_scan_buttons("media", {}, False, "tok", "sv")
        self.assertEqual(extra.count(" disabled"), 1)
        self.assertIn("/extra är inte ansluten", extra)

    def test_scan_route_validates_the_choice_and_answers_with_a_redirect(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)

        def post(fields):
            request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/scan",
                                             urllib.parse.urlencode(fields).encode(), method="POST")
            try:
                return opener.open(request, timeout=10)
            except urllib.error.HTTPError as error:
                return error

        self.assertEqual(post({"token": server.SCAN_TOKEN, "library": "nonsense"}).code, 400)
        self.assertEqual(post({"token": "wrong", "library": "movie"}).code, 403)
        started = threading.Event()
        release = threading.Event()

        def slow(roots, probe=None, kind_overrides=None, progress=None):
            progress(roots[0], 2)
            started.set()
            release.wait(10)
            return []

        with mock.patch.object(server, "scan_library", slow):
            answer = post({"token": server.SCAN_TOKEN, "library": "movie", "kind": "movie"})
            self.assertEqual(answer.code, 303)
            self.assertEqual(answer.headers["Location"], "/library?kind=movie")
            self.assertTrue(started.wait(10))
            self.assertEqual(post({"token": server.SCAN_TOKEN, "library": "episode"}).code, 409)
            with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/scan-status", timeout=10) as response:
                data = json.loads(response.read())
            self.assertEqual((data["state"], data["files"]), ("running", 2))
            release.set()
            for _ in range(200):
                if not server.SCAN_LOCK.locked() and server.SCAN_STATUS.snapshot()["state"] == "done":
                    break
                threading.Event().wait(0.05)
        self.assertEqual(server.SCAN_STATUS.snapshot()["state"], "done")


if __name__ == "__main__":
    unittest.main()
