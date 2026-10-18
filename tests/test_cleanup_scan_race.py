import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import subtitle_cleanup

CUE = "1\n00:00:01,000 --> 00:00:02,000\nHej\n"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def run_together(count, work):
    """Start `count` threads at the same moment; returns what each returned (an exception is returned, not raised)."""
    gate, results = threading.Barrier(count), [None] * count

    def runner(index):
        gate.wait()
        try:
            results[index] = work(index)
        except Exception as error:      # noqa: BLE001 - the tests look at what went wrong
            results[index] = error
    threads = [threading.Thread(target=runner, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


class ConcurrentSaveTests(unittest.TestCase):
    """The Unraid log showed 'FileNotFoundError: cleanup-scan.json.tmp -> cleanup-scan.json' when several scans finished together:
    every save used the same temporary name, so the first rename took the file the others were about to rename."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = Path(self.folder.name)

    def test_many_saves_at_once_never_fail_and_leave_valid_files(self):
        items = [{"name": f"{number}.srt", "path": f"/movies/{number}.srt"} for number in range(300)]
        outcomes = run_together(12, lambda index: [subtitle_cleanup.save_scan(self.config, items) for _ in range(15)])
        self.assertEqual([outcome for outcome in outcomes if isinstance(outcome, Exception)], [])
        self.assertEqual(len(subtitle_cleanup.load_scan(self.config)["items"]), 300)

    def test_filters_and_retention_survive_concurrent_saves_too(self):
        filters = subtitle_cleanup.parse_filters({"kind": ["ai"], "older": ["0"], "q": [""]})
        outcomes = run_together(12, lambda index: [(subtitle_cleanup.save_filters(self.config, filters),
                                                   subtitle_cleanup.save_retention(self.config, 7)) for _ in range(15)])
        self.assertEqual([outcome for outcome in outcomes if isinstance(outcome, Exception)], [])
        self.assertEqual(subtitle_cleanup.load_filters(self.config), filters)
        self.assertEqual(subtitle_cleanup.load_retention(self.config), 7)

    def test_no_temporary_file_is_left_behind(self):
        subtitle_cleanup.save_scan(self.config, [])
        subtitle_cleanup.save_filters(self.config, subtitle_cleanup.parse_filters({}))
        subtitle_cleanup.save_retention(self.config, 3)
        self.assertEqual(sorted(path.name for path in self.config.iterdir()),
                         ["cleanup-filters.json", "cleanup-scan.json", "cleanup-settings.json"])

    def test_a_failed_write_leaves_no_temporary_file_and_keeps_the_old_content(self):
        subtitle_cleanup.save_scan(self.config, [{"name": "old.srt"}])
        with mock.patch.object(subtitle_cleanup.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            subtitle_cleanup.save_scan(self.config, [{"name": "new.srt"}])
        self.assertEqual([path.name for path in self.config.iterdir()], ["cleanup-scan.json"])
        self.assertEqual(subtitle_cleanup.load_scan(self.config)["items"], [{"name": "old.srt"}])


class OneScanAtATimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = Path(self.folder.name) / "config"
        self.root = Path(self.folder.name) / "movies"
        self.config.mkdir()
        (self.root / "Film").mkdir(parents=True)
        (self.root / "Film" / "Film.sv.srt").write_text(CUE, encoding="utf-8")
        self.scans = []
        real_scan = subtitle_cleanup.scan_srt

        def slow_scan(roots):
            self.scans.append(time.monotonic())
            time.sleep(0.3)          # a scan of a big library takes tens of seconds
            return real_scan(roots)
        patcher = mock.patch.object(subtitle_cleanup, "scan_srt", slow_scan)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clicks_that_arrive_during_a_scan_share_its_result(self):
        requested = time.time()
        outcomes = run_together(8, lambda index: subtitle_cleanup.scan_if_needed(self.config, [self.root], {}, requested))
        self.assertEqual([outcome for outcome in outcomes if isinstance(outcome, Exception)], [])
        self.assertEqual(len(self.scans), 1, 'one scan for eight clicks')
        self.assertEqual(sorted(outcomes, key=bool), [False] * 7 + [True], 'exactly one of them did the scanning')
        self.assertEqual([item["name"] for item in subtitle_cleanup.load_scan(self.config)["items"]], ["Film.sv.srt"])

    def test_a_later_request_scans_again(self):
        self.assertTrue(subtitle_cleanup.scan_if_needed(self.config, [self.root], {}, time.time()))
        time.sleep(0.02)
        (self.root / "Film" / "Film.en.srt").write_text(CUE, encoding="utf-8")
        self.assertTrue(subtitle_cleanup.scan_if_needed(self.config, [self.root], {}, time.time()))
        self.assertEqual(len(self.scans), 2)
        self.assertEqual(len(subtitle_cleanup.load_scan(self.config)["items"]), 2)

    def test_a_failing_scan_does_not_block_the_next_one(self):
        with mock.patch.object(subtitle_cleanup, "scan_srt", side_effect=OSError("share unavailable")), self.assertRaises(OSError):
            subtitle_cleanup.scan_if_needed(self.config, [self.root], {}, time.time())
        self.assertTrue(subtitle_cleanup.scan_if_needed(self.config, [self.root], {}, time.time()))


class CleanupScanRouteTests(unittest.TestCase):
    """The real Handler: several scan clicks at the same moment, as in the Unraid log."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.root = base / "movies"
        self.config = base / "config"
        (self.root / "Film").mkdir(parents=True)
        self.config.mkdir()
        (self.root / "Film" / "Film.sv.srt").write_text(CUE, encoding="utf-8")
        for name, value in (("CONFIG", self.config), ("DATABASE", self.config / "subsynq.db"), ("CLEANUP_ROOTS", (self.root,))):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        self.scans = []
        real_scan = subtitle_cleanup.scan_srt

        def slow_scan(roots):
            self.scans.append(1)
            time.sleep(0.4)
            return real_scan(roots)
        patcher = mock.patch.object(subtitle_cleanup, "scan_srt", slow_scan)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def click(self, query):
        try:
            urllib.request.build_opener(NoRedirect).open(f"http://127.0.0.1:{self.httpd.server_port}/cleanup?{query}", timeout=30)
        except urllib.error.HTTPError as error:
            return error.code
        return 200

    def test_simultaneous_scan_clicks_all_succeed_and_scan_once(self):
        queries = ["kind=ai&older=0&q=&scan=1", "kind=ai&dupes=1&older=0&q=&scan=1"]
        codes = run_together(8, lambda index: self.click(queries[index % 2]))
        self.assertEqual(codes, [303] * 8)
        self.assertEqual(len(self.scans), 1)
        self.assertEqual(json.loads((self.config / "cleanup-scan.json").read_text(encoding="utf-8"))["items"][0]["name"], "Film.sv.srt")

    def test_a_scan_that_cannot_read_the_share_still_answers_with_the_log_hint(self):
        with mock.patch.object(subtitle_cleanup, "scan_srt", side_effect=OSError("share unavailable")):
            self.assertEqual(self.click("kind=ai&scan=1"), 503)


if __name__ == "__main__":
    unittest.main()
