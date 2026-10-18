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
from i18n import LANGUAGES, t
from jobs import initialize_jobs

MISSING = {"path": "/movies/saknas.mkv", "name": "saknas.mkv", "kind": "movie", "size_bytes": 1,
           "ai_sv_exists": False, "embedded_sv_exists": False, "probe": {}}
HAS_AI = {"path": "/movies/har-ai.mkv", "name": "har-ai.mkv", "kind": "movie", "size_bytes": 1,
          "ai_sv_exists": True, "ai_sv_path": "/movies/har-ai.ai.sv.srt", "embedded_sv_exists": False, "probe": {}}
EMBEDDED = {"path": "/movies/inbaddad.mkv", "name": "inbaddad.mkv", "kind": "movie", "size_bytes": 1,
            "ai_sv_exists": False, "embedded_sv_exists": True, "probe": {}}


def episode(series, season_number, number, **changes):
    return {"path": f"/tv/{series}/Season {season_number}/{series} S{season_number:02d}E{number:02d}.mkv",
            "name": f"{series} S{season_number:02d}E{number:02d}.mkv", "kind": "episode", "series": series,
            "season": f"Season {season_number}", "season_number": season_number, "episode_number": number,
            "episode_code": f"S{season_number:02d}E{number:02d}", "size_bytes": 1,
            "ai_sv_exists": False, "embedded_sv_exists": False, "probe": {}, **changes}


class FilterBarTests(unittest.TestCase):
    def test_two_links_with_counts_and_the_current_one_marked(self):
        everything = server.render_library_filter("movie", "all", 3, 2, "sv")
        self.assertIn("href='/library?kind=movie'", everything)
        self.assertIn("href='/library?kind=movie&amp;show=missing'", everything)
        self.assertIn("Alla (3)", everything)
        self.assertIn("Bara utan undertext (2)", everything)
        self.assertEqual(everything.count("aria-current='true'"), 1)
        self.assertRegex(everything, r"aria-current='true'>Alla")
        missing = server.render_library_filter("episode", "missing", 3, 2, "en")
        self.assertRegex(missing, r"aria-current='true'>Only without a subtitle")
        self.assertIn("href='/library?kind=episode'", missing)

    def test_every_text_exists_in_every_language(self):
        for key in ("filter_label", "filter_all", "filter_missing", "filter_count", "filter_none_missing"):
            for lang in LANGUAGES:
                self.assertTrue(t(key, lang), (key, lang))
        self.assertEqual(t("filter_count", "sv").format(shown=2, total=9), "2 av 9 filer")

    def test_anything_but_missing_means_every_file(self):
        self.assertEqual(server.library_show("missing"), "missing")
        for value in ("", "all", "MISSING", "1", None):
            self.assertEqual(server.library_show(value), "all")


class FilterCarriedThroughFormsTests(unittest.TestCase):
    def test_the_filter_is_carried_by_every_form_of_a_row_and_the_bars(self):
        has_ai_row = server.render_media_row(HAS_AI, token="safe", show="missing")
        self.assertEqual(has_ai_row.count("name='return_show' value='missing'"), 2, 'delete and recreate')
        self.assertIn("name='return_show' value='missing'", server.render_media_row(MISSING, token="safe", show="missing"))
        job = {"id": 3, "status": "transcribing", "progress": 5, "message": "", "hardware": None, "model": None}
        self.assertIn("name='return_show' value='missing'", server.render_media_row(MISSING, token="safe", job=job, show="missing"))
        self.assertIn("name='return_show' value='missing'", server.render_bulk_bar("movie", "tok", "sv", "missing"))
        self.assertIn("name='return_show' value='missing'",
                      server.render_scan_buttons("movie", {"items": []}, False, "tok", "sv", "missing"))

    def test_the_plain_list_has_no_extra_field(self):
        for html in (server.render_media_row(HAS_AI, token="safe"), server.render_media_row(MISSING, token="safe"),
                     server.render_bulk_bar("movie", "tok"), server.render_scan_buttons("movie", {"items": []}, False, "tok")):
            self.assertNotIn("return_show", html)

    def test_the_filter_reaches_the_rows_of_the_seasons(self):
        items = [episode("Show", 1, 1)]
        self.assertIn("name='return_show' value='missing'", server.render_library(items, "episode", "tok", {}, False, "sv", "missing"))
        self.assertNotIn("return_show", server.render_library(items, "episode", "tok", {}, False, "sv"))
        body = server.render_series_body(items, "Show", "tok", {}, False, "sv", "missing")
        self.assertIn("name='return_show' value='missing'", body)

    def test_the_return_path_keeps_the_filter(self):
        self.assertEqual(server.library_return_path("movie", "queued"), "/library?kind=movie&job=queued")
        self.assertEqual(server.library_return_path("movie", "queued", "missing"), "/library?kind=movie&job=queued&show=missing")
        self.assertEqual(server.library_return_path("nonsense", "bulk", "missing"), "/library?kind=episode&job=bulk&show=missing")


class FilterPageTests(unittest.TestCase):
    ITEMS = [MISSING, HAS_AI, EMBEDDED,
             episode("Klar", 1, 1, ai_sv_exists=True, ai_sv_path="/tv/Klar/x.ai.sv.srt"),
             episode("Delvis", 1, 1), episode("Delvis", 1, 2, ai_sv_exists=True, ai_sv_path="/tv/Delvis/y.ai.sv.srt"),
             episode("Delvis", 2, 1, embedded_sv_exists=True)]

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = Path(self.folder.name)
        for patcher in (mock.patch.object(server, "CONFIG", config), mock.patch.object(server, "DATABASE", config / "subsynq.db")):
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        with sqlite3.connect(server.DATABASE) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_scan', ?)", (json.dumps({"scanned_at": None, "items": self.ITEMS}),))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, query):
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{self.httpd.server_port}{query}", timeout=15).read().decode("utf-8")
        except urllib.error.HTTPError as error:
            self.fail(f"{query}: HTTP {error.code} {error.read().decode()[:300]}")

    def test_the_movie_list_shows_everything_by_default_and_only_the_missing_with_the_filter(self):
        everything = self.get("/library?kind=movie")
        for name in ("saknas.mkv", "har-ai.mkv", "inbaddad.mkv"):
            self.assertIn(name, everything)
        self.assertIn("Alla (3)", everything)
        self.assertIn("Bara utan undertext (1)", everything)
        self.assertIn("3 filer", everything)
        only = self.get("/library?kind=movie&show=missing")
        self.assertIn("saknas.mkv", only)
        self.assertNotIn("har-ai.mkv", only)
        self.assertNotIn("inbaddad.mkv", only)
        self.assertIn("1 av 3 filer", only)
        self.assertIn("name='return_show' value='missing'", only)
        self.assertNotIn("name='return_show' value='missing'", everything)

    def test_the_series_list_drops_finished_series_and_finished_episodes(self):
        only = self.get("/library?kind=episode&show=missing")
        self.assertIn("<h3>Delvis</h3>", only)
        self.assertNotIn("<h3>Klar</h3>", only)
        self.assertIn("Delvis S01E01", only)
        self.assertNotIn("Delvis S01E02", only)
        self.assertNotIn("Delvis S02E01", only)
        self.assertIn("1 av 4 filer", only)

    def test_when_nothing_is_missing_the_page_says_so_and_keeps_the_filter_links(self):
        with mock.patch.object(server, "needs_swedish_subtitle", lambda item, ignore=False: False):
            page = self.get("/library?kind=movie&show=missing")
        self.assertIn("Alla filer här har redan en svensk undertext.", page)
        self.assertIn("Alla (3)", page)
        self.assertNotIn("<form id='bulk-form'", page)

    def test_an_empty_library_has_no_filter_bar(self):
        self.assertIn("<nav class='library-filter'", self.get("/library?kind=episode"), "the same check finds the bar when there are files")
        with sqlite3.connect(server.DATABASE) as db:
            db.execute("UPDATE metadata SET value=? WHERE key='library_scan'", (json.dumps({"scanned_at": None, "items": []}),))
        page = self.get("/library?kind=movie&show=missing")
        self.assertNotIn("<nav class='library-filter'", page)

    def test_the_lazy_series_body_honours_the_filter(self):
        everything = self.get("/library/series?series=Delvis")
        self.assertIn("Delvis S01E02", everything)
        self.assertIn("Delvis S02E01", everything)
        only = self.get("/library/series?series=Delvis&show=missing")
        self.assertIn("Delvis S01E01", only)
        self.assertNotIn("Delvis S01E02", only)
        self.assertNotIn("Delvis S02E01", only)
        self.assertIn("name='return_show' value='missing'", only)


class FilterRedirectTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        database = Path(self.folder.name) / "subsynq.db"
        initialize_jobs(database)
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO metadata VALUES ('library_scan', ?)", (json.dumps({"scanned_at": None, "items": [MISSING, HAS_AI]}),))
        patcher = mock.patch.object(server, "DATABASE", database)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, path, fields):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        body = urllib.parse.urlencode({"token": server.SCAN_TOKEN, **fields}, doseq=True).encode()
        try:
            urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}", body), timeout=10)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location") or error.read().decode()
        raise AssertionError("expected a redirect or error")

    def test_start_returns_to_the_filtered_list(self):
        status, location = self.post("/jobs/start", {"media_path": MISSING["path"], "return_show": "missing"})
        self.assertEqual((status, location), (303, "/library?kind=movie&job=queued&show=missing"))

    def test_start_without_the_filter_returns_to_the_plain_list(self):
        status, location = self.post("/jobs/start", {"media_path": MISSING["path"]})
        self.assertEqual((status, location), (303, "/library?kind=movie&job=queued"))

    def test_bulk_delete_and_restart_return_to_the_filtered_list(self):
        status, location = self.post("/jobs/bulk", {"action": "start", "return_kind": "movie", "return_show": "missing",
                                                    "media_path": [MISSING["path"]]})
        self.assertEqual(status, 303)
        self.assertTrue(location.startswith("/library?kind=movie&job=bulk&show=missing&bulk_action=start"), location)
        status, location = self.post("/jobs/delete", {"media_path": HAS_AI["path"], "return_show": "missing"})
        self.assertEqual((status, location), (303, "/library?kind=movie&job=deleting&show=missing"))

    def test_an_unknown_filter_value_is_ignored(self):
        _, location = self.post("/jobs/start", {"media_path": MISSING["path"], "return_show": "<script>"})
        self.assertEqual(location, "/library?kind=movie&job=queued")


if __name__ == "__main__":
    unittest.main()
