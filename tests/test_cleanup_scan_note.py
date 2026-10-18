import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import subtitle_cleanup
from i18n import LANGUAGES, t

EMPTY_SCAN = {"scanned_at": None, "items": []}
HIDDEN_NOTE = re.compile(r"<p class='scan-note' role='status' data-scan-note hidden>")
SHOWN_NOTE = re.compile(r"<p class='scan-note' role='status' data-scan-note>")


def render(**changes):
    filters = subtitle_cleanup.parse_filters({})
    return subtitle_cleanup.render_cleanup_content(EMPTY_SCAN, filters, [], "tok", **changes)


class ScanNoteRenderTests(unittest.TestCase):
    def test_the_note_is_on_the_page_but_hidden_until_a_scan_is_running(self):
        self.assertRegex(render(), HIDDEN_NOTE)
        self.assertNotRegex(render(), SHOWN_NOTE)
        self.assertRegex(render(scanning=True), SHOWN_NOTE)
        self.assertNotRegex(render(scanning=True), HIDDEN_NOTE)

    def test_the_note_is_translated_and_sits_above_the_filter_form(self):
        swedish, english = render(lang="sv", scanning=True), render(lang="en", scanning=True)
        self.assertIn("Skannar undertextmapparna", swedish)
        self.assertIn("Scanning the subtitle folders", english)
        self.assertNotIn("Skannar undertextmapparna", english)
        self.assertLess(swedish.index("data-scan-note"), swedish.index("class='cleanup-filters'"))
        for lang in LANGUAGES:
            self.assertTrue(t("cleanup_scanning", lang))

    def test_the_rescan_button_is_a_submit_button_named_scan(self):
        self.assertIn("<button type='submit' name='scan' value='1'", render())


class ScanNotePageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.root, self.config = base / "movies", base / "config"
        (self.root / "Film").mkdir(parents=True)
        self.config.mkdir()
        (self.root / "Film" / "Film.sv.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHej\n", encoding="utf-8")
        for name, value in (("CONFIG", self.config), ("DATABASE", self.config / "subsynq.db"), ("CLEANUP_ROOTS", (self.root,))):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, query="/cleanup"):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.httpd.server_port}{query}", timeout=30).read().decode("utf-8")

    def test_a_page_opened_during_a_scan_shows_the_note_and_one_opened_after_it_does_not(self):
        real_scan, started, release = subtitle_cleanup.scan_srt, threading.Event(), threading.Event()

        def held_scan(roots):
            started.set()
            release.wait(20)
            return real_scan(roots)
        with mock.patch.object(subtitle_cleanup, "scan_srt", held_scan):
            clicker = threading.Thread(target=lambda: self.get("/cleanup?kind=ai&scan=1"))
            clicker.start()
            self.assertTrue(started.wait(10), "the scan started")
            during = self.get()
            release.set()
            clicker.join(20)
        self.assertRegex(during, SHOWN_NOTE)
        self.assertRegex(self.get(), HIDDEN_NOTE)
        self.assertFalse(subtitle_cleanup.scan_running())

    def test_the_lock_is_free_again_after_a_failing_scan(self):
        with mock.patch.object(subtitle_cleanup, "scan_srt", side_effect=OSError("share unavailable")):
            with self.assertRaises(OSError):
                subtitle_cleanup.scan_if_needed(self.config, [self.root], {}, time.time())
        self.assertFalse(subtitle_cleanup.scan_running())


class ScanNoteScriptTests(unittest.TestCase):
    """The click handling lives in index.html; these check that it is there and what it promises."""

    SHELL = Path(server.__file__).with_name("index.html").read_text(encoding="utf-8")

    def test_only_the_rescan_button_starts_the_note_and_a_second_click_is_ignored(self):
        self.assertIn("document.querySelectorAll('form.cleanup-filters')", self.SHELL)
        self.assertIn("event.submitter.name!=='scan'", self.SHELL)
        self.assertIn("if(form.dataset.scanning){event.preventDefault();return;}", self.SHELL)
        self.assertIn("note.hidden=false", self.SHELL)

    def test_a_page_that_the_server_says_is_scanning_checks_again_through_the_reload_guard(self):
        self.assertIn("autoReload('cleanup')", self.SHELL)
        self.assertIn("cleanup:'skanningen i Städa undertexter'", self.SHELL)

    def test_back_and_forward_do_not_leave_the_page_stuck_in_the_scanning_state(self):
        self.assertIn("window.addEventListener('pageshow'", self.SHELL)
        self.assertIn("delete form.dataset.scanning", self.SHELL)


if __name__ == "__main__":
    unittest.main()
