import json
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
import subtitle_cleanup
from jobs import initialize_jobs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class CleanupRouteTests(unittest.TestCase):
    """The real Handler against temporary folders: move to trash, restore, empty."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.root = base / "movies"
        self.config = base / "config"
        (self.root / "Film").mkdir(parents=True)
        self.config.mkdir()
        self.video = self.root / "Film" / "Film.mkv"
        self.video.write_bytes(b"video")
        self.srt = self.root / "Film" / "Film.sv.srt"
        self.srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHej\n", encoding="utf-8")
        initialize_jobs(self.config / "subsynq.db")
        for name, value in (("CONFIG", self.config), ("DATABASE", self.config / "subsynq.db"),
                            ("CLEANUP_ROOTS", (self.root,))):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, route, **fields):
        body = urllib.parse.urlencode({"token": server.SCAN_TOKEN, **fields}, doseq=True).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{route}", body)
        try:
            urllib.request.build_opener(NoRedirect).open(request, timeout=10)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location") or error.read().decode()
        raise AssertionError("expected a redirect or error")

    def cached_names(self):
        return sorted(item["name"] for item in subtitle_cleanup.load_scan(self.config)["items"])

    def test_trash_restore_and_empty_keep_the_list_in_step(self):
        subtitle_cleanup.save_scan(self.config, subtitle_cleanup.scan_srt([self.root]))
        self.assertEqual(self.cached_names(), ["Film.sv.srt"])

        status, location = self.post("/cleanup/trash", mode="selected", path=[str(self.srt)])
        self.assertEqual((status, location), (303, "/cleanup?moved=1&failed=0"))
        self.assertFalse(self.srt.exists())
        self.assertTrue(self.video.exists())
        self.assertEqual(self.cached_names(), [])
        batch = subtitle_cleanup.list_trash(self.config / "trash")[0]["id"]

        status, location = self.post("/cleanup/restore", batch=batch)
        self.assertEqual((status, location), (303, "/cleanup?restored=1&skipped=0"))
        self.assertTrue(self.srt.exists())
        # No manual rescan needed: the restored file is back in the saved list.
        self.assertEqual(self.cached_names(), ["Film.sv.srt"])
        self.assertEqual(subtitle_cleanup.list_trash(self.config / "trash"), [])

        self.post("/cleanup/trash", mode="selected", path=[str(self.srt)])
        self.assertEqual(self.post("/cleanup/empty")[1], "/cleanup?emptied=1")
        self.assertEqual(subtitle_cleanup.list_trash(self.config / "trash"), [])
        self.assertFalse(self.srt.exists())

    def test_filtered_mode_moves_every_match_and_nothing_else(self):
        other = self.root / "Film" / "Film.ai.sv.srt"
        other.write_text("1\n00:00:01,000 --> 00:00:02,000\nHej\n", encoding="utf-8")
        subtitle_cleanup.save_scan(self.config, subtitle_cleanup.scan_srt([self.root]))
        status, location = self.post("/cleanup/trash", mode="filtered", kind="other")
        self.assertEqual((status, location), (303, "/cleanup?moved=1&failed=0"))
        self.assertFalse(self.srt.exists())
        self.assertTrue(other.exists())
        self.assertEqual(self.cached_names(), ["Film.ai.sv.srt"])

    def test_paths_outside_the_media_folders_are_refused(self):
        outside = Path(self.folder.name) / "hemlig.srt"
        outside.write_text("x")
        status, location = self.post("/cleanup/trash", mode="selected", path=[str(outside)])
        self.assertEqual((status, location), (303, "/cleanup?moved=0&failed=1"))
        self.assertTrue(outside.exists())
        self.assertEqual(self.post("/cleanup/trash", mode="selected")[0], 400)


if __name__ == "__main__":
    unittest.main()
