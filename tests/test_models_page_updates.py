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
import models_card
import server


def status(**extra):
    data = {"state": "ready", "ready": True, "done": 2, "total": 2, "source": "/app/models.json",
            "catalogue_errors": [], "obsolete": [], "notice": "", "update_checked_at": "2026-10-11T05:00:00+00:00",
            "models": [
                {"id": "g3", "label": "Gemma 3 12B", "name": "gemma3:12b", "kind": "ollama", "used_by": "translate-engine",
                 "size_gb": 8.1, "state": "present", "percent": 100, "detail": "", "update": "available",
                 "installed": "478f8be57615", "latest": "b92b218582c5"},
                {"id": "w3", "label": "Whisper large-v3", "name": "large-v3", "kind": "faster-whisper", "used_by": "worker",
                 "size_gb": 2.9, "state": "present", "percent": 100, "detail": "", "update": "current",
                 "installed": "edaa852ec7", "latest": "edaa852ec7"},
                {"id": "q", "label": "Qwen3-ASR", "name": "Qwen/Qwen3-ASR-1.7B", "kind": "hf", "used_by": "qwen-asr",
                 "size_gb": 4.4, "state": "present", "percent": 100, "detail": "", "update": "unknown"}]}
    data.update(extra)
    return data


class PageTests(unittest.TestCase):
    def test_version_column_and_update_form_only_offer_models_with_a_new_version(self):
        html = models_card.render_models_page(status(), "tok", "sv")
        self.assertIn("ny version finns", html)
        self.assertIn("senaste", html)
        self.assertIn("okänt", html)
        self.assertIn("action='/models/update'", html)
        self.assertIn("value='ollama|gemma3:12b'", html)
        self.assertNotIn("value='faster-whisper|large-v3'", html)         # current: not offered
        self.assertIn("478f8be57615 → b92b218582c5", html)
        self.assertIn("Uppdatera markerade", html)
        self.assertIn("Kontrollera nu", html)
        self.assertIn("2026-10-11 05:00", html)
        self.assertIn("rensar Ollama de gamla filerna", html)

    def test_nothing_to_update_says_so_and_still_offers_a_manual_check(self):
        data = status()
        for model in data["models"]:
            model["update"] = "current"
        html = models_card.render_models_page(data, "tok", "sv")
        self.assertIn("Alla modeller i listan är på senaste version.", html)
        self.assertNotIn("Uppdatera markerade", html)
        self.assertIn("Kontrollera nu", html)

    def test_not_yet_checked_has_no_all_clear_message(self):
        data = status(update_checked_at="")
        for model in data["models"]:
            model["update"] = ""
        html = models_card.render_models_page(data, "tok", "en")
        self.assertIn("not yet", html)
        self.assertNotIn("All listed models are on the latest version.", html)

    def test_request_messages_and_english(self):
        for requested, text in (("update", "Update requested"), ("check", "Check requested"), ("1", "Request sent")):
            self.assertIn(text, models_card.render_models_page(status(), "tok", "en", requested=requested))
        self.assertIn("new version available", models_card.render_models_page(status(), "tok", "en"))


class RouteTests(unittest.TestCase):
    def post(self, folder, fields):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with mock.patch.object(server, "CONFIG", Path(folder)):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                body = urllib.parse.urlencode(fields, doseq=True).encode()
                request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/models/update", body)
                try:
                    urllib.request.build_opener(NoRedirect).open(request, timeout=10)
                except urllib.error.HTTPError as error:
                    return error.code, error.headers.get("Location")
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_update_and_check_requests_are_written_only_with_a_valid_token(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "models-update-request.json"
            self.assertEqual(self.post(folder, {"token": "fel", "check": "1"})[0], 403)
            self.assertEqual(self.post(folder, {"token": server.SCAN_TOKEN})[0], 400)
            self.assertFalse(target.exists())
            self.assertEqual(self.post(folder, {"token": server.SCAN_TOKEN, "check": "1"}),
                             (303, "/settings/models?requested=check"))
            self.assertEqual(json.loads(target.read_text())["check"], True)
            self.assertEqual(self.post(folder, {"token": server.SCAN_TOKEN, "key": ["ollama|gemma3:12b"]}),
                             (303, "/settings/models?requested=update"))
            written = json.loads(target.read_text())
            self.assertEqual((written["keys"], written["check"]), (["ollama|gemma3:12b"], False))


if __name__ == "__main__":
    unittest.main()
