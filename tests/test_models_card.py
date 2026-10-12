import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import models_card
import server
from i18n import STRINGS


def status(state="downloading", updated=None, **extra):
    data = {"updated_at": (updated or datetime.now(timezone.utc)).isoformat(), "state": state, "ready": state == "ready",
            "message": "", "done": 1, "total": 3,
            "models": [
                {"id": "a", "label": "Whisper large-v3", "used_by": "worker", "size_gb": 2.9, "state": "present",
                 "percent": 100, "detail": ""},
                {"id": "b", "label": "Qwen3-ASR 1.7B", "used_by": "qwen-asr", "size_gb": 4.4, "state": "downloading",
                 "percent": 42, "detail": "1.8 GB"},
                {"id": "c", "label": "Gemma (gemma3:12b)", "used_by": "translate-engine", "size_gb": 8.1,
                 "state": "pending", "percent": None, "detail": ""}]}
    data.update(extra)
    return data


class BannerTests(unittest.TestCase):
    def test_no_banner_without_status_or_when_ready_or_only_checking(self):
        self.assertEqual(models_card.render_models_banner(None), "")
        self.assertEqual(models_card.render_models_banner(status("ready")), "")
        self.assertEqual(models_card.render_models_banner(status("checking")), "")

    def test_downloading_banner_shows_each_model_with_progress(self):
        html = models_card.render_models_banner(status())
        self.assertIn("AI-modellerna laddas ner", html)
        self.assertIn("1 av 3 klara", html)
        self.assertIn("Whisper large-v3", html)
        self.assertIn("42 % · 1.8 GB", html)
        self.assertIn("width:42%", html)
        self.assertIn("width:100%", html)
        self.assertIn("väntar", html)
        self.assertIn("ungefär 12 GB kvar", html)
        self.assertIn("data-models-card", html)

    def test_error_banner_explains_and_names_the_problem(self):
        data = status("error", message="Kunde inte hämta Gemma: ConnectionError")
        data["models"][2].update(state="error", detail="ConnectionError")
        html = models_card.render_models_banner(data)
        self.assertIn("AI-modeller saknas", html)
        self.assertIn("Kunde inte hämta Gemma: ConnectionError", html)
        self.assertIn("Försöker igen automatiskt", html)
        self.assertIn("fel: ConnectionError", html)

    def test_stale_status_warns_that_the_worker_may_be_down(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        self.assertIn("Kontrollera att worker-containern kör", models_card.render_models_banner(status(updated=old)))
        self.assertNotIn("Kontrollera att worker-containern kör", models_card.render_models_banner(status()))

    def test_english_and_html_escaping(self):
        data = status()
        data["models"][0]["label"] = "<script>alert(1)</script>"
        html = models_card.render_models_banner(data, "en")
        self.assertIn("Downloading the AI models", html)
        self.assertIn("1 of 3 ready", html)
        self.assertNotIn("<script>", html)
        for key in (k for k in STRINGS if k.startswith("models_")):
            self.assertTrue(STRINGS[key]["sv"] and STRINGS[key]["en"], key)

    def test_load_status_ignores_missing_or_broken_files(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(models_card.load_status(folder))
            (Path(folder) / "models-status.json").write_text("{trasig", encoding="utf-8")
            self.assertIsNone(models_card.load_status(folder))
            (Path(folder) / "models-status.json").write_text(json.dumps(status()), encoding="utf-8")
            self.assertEqual(models_card.load_status(folder)["state"], "downloading")


class RouteAndPageTests(unittest.TestCase):
    def test_api_models_serves_the_status_file_and_render_page_shows_the_banner(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "models-status.json").write_text(json.dumps(status()), encoding="utf-8")
            with mock.patch.object(server, "CONFIG", Path(folder)):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                try:
                    body = urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/models", timeout=10).read()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                self.assertEqual(json.loads(body)["state"], "downloading")
                page = server.render_page("Test", "overview", "<p>INNEHÅLL</p>", "mork", "", {}, {"available": []},
                                          "tok", "sv")
                self.assertLess(page.index("class='card models-card'"), page.index("INNEHÅLL"))
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(server, "CONFIG", Path(folder)):
                page = server.render_page("Test", "overview", "<p>INNEHÅLL</p>", "mork", "", {}, {"available": []},
                                          "tok", "sv")
                self.assertNotIn("class='card models-card'", page)



class ModelsPageTests(unittest.TestCase):
    def setUp(self):
        self.status = status("ready")
        self.status.update(source="/config/models.json", catalogue_errors=[], notice="",
                           obsolete=[{"key": "ollama|gemma3:12b", "label": "Gemma 3 12B", "name": "gemma3:12b",
                                      "kind": "ollama", "size_gb": 8.1}])
        for model in self.status["models"]:
            model.setdefault("name", model["label"])

    def test_page_lists_models_the_source_file_and_old_models_with_a_remove_form(self):
        html = models_card.render_models_page(self.status, "tok<>", "sv")
        self.assertIn("/config/models.json", html)
        self.assertIn("Whisper large-v3", html)
        self.assertIn("Gamla modeller som inte längre står i listan", html)
        self.assertIn("value='ollama|gemma3:12b'", html)
        self.assertIn("action='/models/remove'", html)
        self.assertIn("value='tok&lt;&gt;'", html)
        self.assertIn("SubSynq tar aldrig bort något av sig själv", html)

    def test_no_remove_form_when_nothing_is_old(self):
        self.status["obsolete"] = []
        html = models_card.render_models_page(self.status, "tok", "en")
        self.assertIn("No old models to remove.", html)
        self.assertNotIn("/models/remove", html)

    def test_catalogue_errors_and_results_are_shown(self):
        self.status["catalogue_errors"] = ["/config/models.json: trasig"]
        self.status["notice"] = "Gemma 3 12B: borttagen"
        html = models_card.render_models_page(self.status, "tok", "sv", requested=True)
        self.assertIn("Modellfilen har ett fel: /config/models.json: trasig", html)
        self.assertIn("Gemma 3 12B: borttagen", html)
        self.assertIn("Begäran skickad", html)

    def test_missing_status_explains_instead_of_crashing(self):
        self.assertIn("inte rapporterat", models_card.render_models_page(None, "tok", "sv"))

    def test_post_models_remove_writes_a_request_only_with_a_valid_token(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(server, "CONFIG", Path(folder)):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()

                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs):
                        return None

                def post(fields):
                    import urllib.error
                    import urllib.parse
                    body = urllib.parse.urlencode(fields, doseq=True).encode()
                    request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/models/remove", body)
                    try:
                        urllib.request.build_opener(NoRedirect).open(request, timeout=10)
                    except urllib.error.HTTPError as error:
                        return error.code, error.headers.get("Location")
                try:
                    request_file = Path(folder) / "models-remove-request.json"
                    self.assertEqual(post({"token": "fel", "key": ["ollama|gemma3:12b"]})[0], 403)
                    self.assertFalse(request_file.exists())
                    self.assertEqual(post({"token": server.SCAN_TOKEN})[0], 400)
                    self.assertEqual(post({"token": server.SCAN_TOKEN, "key": ["ollama|gemma3:12b"]}),
                                     (303, "/settings/models?requested=1"))
                    self.assertEqual(json.loads(request_file.read_text())["keys"], ["ollama|gemma3:12b"])
                finally:
                    httpd.shutdown()
                    httpd.server_close()

    def test_sidebar_has_a_models_link_under_settings(self):
        html = server.render_sidebar("settings:models", 0, {"available": [], "active": None}, "sv")
        self.assertIn("href='/settings/models'", html)


if __name__ == "__main__":
    unittest.main()
