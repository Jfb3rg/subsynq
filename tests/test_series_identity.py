import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server


def episode(path, series="Animation", season_number=1, number=1):
    return {"path": path, "name": Path(path).name, "kind": "episode", "size_bytes": 1, "ai_sv_exists": False,
            "embedded_sv_exists": False, "probe": {}, "series": series, "season": f"Säsong {season_number}",
            "season_number": season_number, "episode_number": number, "episode_code": f"S{season_number:02d}E{number:02d}"}


class SeriesIdentityTests(unittest.TestCase):
    def test_a_category_folder_above_the_series_is_not_the_series(self):
        self.assertEqual(server.series_identity(episode("/tv/Animation/Gummi Bears/Season 1/a.mkv")), ("Gummi Bears", "Animation"))
        self.assertEqual(server.series_identity(episode("/tv/Tv Shows/Grimm/Season 2/b.mkv", series="Tv Shows")), ("Grimm", "Tv Shows"))
        self.assertEqual(server.series_identity(episode("/tv/A/B/Show/Säsong 3/c.mkv")), ("Show", "A / B"))

    def test_the_plain_layout_is_unchanged(self):
        self.assertEqual(server.series_identity(episode("/tv/My Show/Season 2/a.mkv", series="My Show")), ("My Show", ""))

    def test_layouts_without_a_season_folder_or_outside_the_libraries_keep_what_the_scan_decided(self):
        self.assertEqual(server.series_identity(episode("/tv/Some Show/a.mkv", series="Some Show")), ("Some Show", ""))
        self.assertEqual(server.series_identity(episode("/tv/Animation/Gummi Bears/a.mkv", series="Gummi Bears")), ("Gummi Bears", ""))
        self.assertEqual(server.series_identity(episode("/data/Animation/Show/Season 1/a.mkv", series="Fallback")), ("Fallback", ""))
        self.assertEqual(server.series_identity({"path": "/tv/x.mkv"}), ("", ""))
        self.assertEqual(server.series_identity({}), ("", ""))

    def test_an_episode_in_its_own_folder_inside_the_season_and_files_directly_in_the_series_folder(self):
        self.assertEqual(server.series_identity(episode("/tv/Tv Shows/Brotherhood/Season 1/Brotherhood S01E02/e.mkv",
                                                        series="Tv Shows")), ("Brotherhood", "Tv Shows"))
        self.assertEqual(server.series_identity(episode("/tv/Tv Shows/The Big Bang Theory/Bonus S01E0 pilot.mkv",
                                                        series="Tv Shows")), ("The Big Bang Theory", "Tv Shows"))
        self.assertEqual(server.series_identity(episode("/tv/Tv Shows/Some Show/Extras/x.mkv", series="Tv Shows")),
                         ("Some Show", "Tv Shows"))
        self.assertEqual(server.series_identity(episode("/tv/Some Show/Extras/x.mkv", series="Some Show")), ("Some Show", ""))
        self.assertEqual(server.series_identity(episode("/tv/Season 1/x.mkv", series="Fallback")), ("Fallback", ""))


class LibraryGroupingTests(unittest.TestCase):
    def render(self, items):
        return server.render_library(items, "episode", token="", jobs={}, ignore_existing=False, lang="sv")

    def test_every_series_gets_its_own_heading_with_its_own_seasons_even_in_a_category_folder(self):
        items = [episode("/tv/Animation/Gummi Bears/Season 1/Gummi Bears - S01E01.mkv"),
                 episode("/tv/Animation/Gummi Bears/Season 2/Gummi Bears - S02E01.mkv", season_number=2),
                 episode("/tv/Animation/Dororo (2019)/Season 1/Dororo - S01E01.mkv")]
        html = self.render(items)
        self.assertIn("<h3>Gummi Bears</h3>", html)
        self.assertIn("<h3>Dororo (2019)</h3>", html)
        self.assertNotIn("<h3>Animation</h3>", html)
        self.assertIn("Animation · 2 säsonger", html)
        self.assertIn("Animation · 1 säsonger", html)
        self.assertLess(html.index("<h3>Dororo (2019)</h3>"), html.index("<h3>Gummi Bears</h3>"))

    def test_the_same_series_name_in_two_categories_stays_two_groups(self):
        items = [episode("/tv/Animation/Heroes/Season 1/a.mkv"), episode("/tv/Tv Shows/Heroes/Season 1/b.mkv", series="Tv Shows")]
        html = self.render(items)
        self.assertEqual(html.count("<h3>Heroes</h3>"), 2)
        self.assertIn("data-series='Animation / Heroes'", html)
        self.assertIn("data-series='Tv Shows / Heroes'", html)

    def test_a_plain_layout_renders_as_before(self):
        html = self.render([episode("/tv/My Show/Season 1/a.mkv", series="My Show")])
        self.assertIn("<h3>My Show</h3>", html)
        self.assertIn("data-series='My Show'", html)
        self.assertIn("<small>1 säsonger", html)


class LazySeriesTests(unittest.TestCase):
    """A big library does not put thousands of episode rows in one page: a series loads when it is opened."""

    @staticmethod
    def big(count_series=12, per_series=30):
        return [episode(f"/tv/Tv Shows/Show {s:02d}/Season 1/Show {s:02d} - S01E{e:02d}.mkv", series="Tv Shows", number=e)
                for s in range(count_series) for e in range(1, per_series + 1)]

    def test_a_big_library_lists_the_series_and_leaves_their_rows_to_be_loaded(self):
        items = self.big()
        self.assertGreater(len(items), server.LAZY_SERIES_MIN_EPISODES)
        html = server.render_library(items, "episode", token="tok", jobs={}, ignore_existing=False, lang="sv")
        self.assertEqual(html.count("data-lazy='1'"), 12)
        self.assertEqual(html.count("data-series-body"), 12)
        self.assertNotIn("<tr", html)
        self.assertNotIn("bulk-check", html)
        self.assertIn("<h3>Show 00</h3>", html)
        self.assertIn("1 säsonger · 30 avsnitt", html)
        self.assertLess(len(html), 20000, "the page stays small however many episodes there are")

    def test_a_small_library_is_rendered_in_full_as_before(self):
        items = self.big(count_series=2, per_series=5)
        html = server.render_library(items, "episode", token="tok", jobs={}, ignore_existing=False, lang="sv")
        self.assertNotIn("data-lazy", html)
        self.assertEqual(html.count("bulk-check"), 10)

    def test_one_series_can_be_rendered_on_its_own(self):
        items = self.big() + [episode("/tv/Animation/Heroes/Season 2/h.mkv", season_number=2)]
        body = server.render_series_body(items, "Tv Shows / Show 03", token="tok", jobs={}, ignore_existing=False, lang="sv")
        self.assertEqual(body.count("bulk-check"), 30)
        self.assertIn("Säsong 1", body)
        self.assertNotIn("Show 04", body)
        heroes = server.render_series_body(items, "Animation / Heroes", token="tok")
        self.assertIn("Säsong 2", heroes)
        self.assertIsNone(server.render_series_body(items, "Heroes", token="tok"))
        self.assertIsNone(server.render_series_body(items, "Nope", token="tok"))

    def test_the_page_script_loads_a_series_when_it_opens_and_keeps_the_other_scripts_working(self):
        index = server.PAGE
        for needle in ("details.series-group[data-lazy]", "/library/series?series=", "subsynq:rows",
                       "restoredSeasons.has(folder.dataset.season)", "event.target.matches('form.preserve-seasons')"):
            self.assertIn(needle, index)


class SeriesRouteTests(unittest.TestCase):
    def setUp(self):
        import http.client
        import json
        import sqlite3
        import tempfile
        import threading
        import urllib.parse
        from http.server import ThreadingHTTPServer
        from unittest import mock
        self.http, self.json, self.parse = http.client, json, urllib.parse
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = Path(self.folder.name)
        for patcher in (mock.patch.object(server, "CONFIG", config), mock.patch.object(server, "DATABASE", config / "subsynq.db")):
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        items = LazySeriesTests.big(count_series=3, per_series=3)
        with sqlite3.connect(server.DATABASE) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_scan', ?)",
                       (json.dumps({"scanned_at": "t", "items": items}),))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        connection = self.http.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")

    def test_the_route_returns_the_seasons_of_one_series_and_404_for_an_unknown_one(self):
        status, body = self.get("/library/series?series=" + self.parse.quote("Tv Shows / Show 01"))
        self.assertEqual(status, 200)
        self.assertEqual(body.count("bulk-check"), 3)
        self.assertIn("Säsong 1", body)
        self.assertEqual(self.get("/library/series?series=Nope")[0], 404)
        self.assertEqual(self.get("/library/series")[0], 404)


if __name__ == "__main__":
    unittest.main()
