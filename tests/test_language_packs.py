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
import model_manager as mm
import models_card
import server
from model_manager import Catalogue, ModelManager, ModelSpec, enabled_packs, parse_catalogue, set_pack

SHIPPED = Path(mm.__file__).with_name("models.json")


def spec(name, optional=False, language=""):
    return ModelSpec(name, "fake", name, f"Modell {name.upper()}", 1.0, "worker", optional=optional, language=language)


class FakeBackend:
    def __init__(self, present=()):
        self.present, self.downloads, self.removed = set(present), [], []

    def is_present(self, spec_):
        return spec_.name in self.present

    def download(self, spec_, report):
        self.downloads.append(spec_.name)
        self.present.add(spec_.name)

    def remove(self, spec_):
        self.removed.append(spec_.name)
        self.present.discard(spec_.name)


class CatalogueTests(unittest.TestCase):
    def test_a_model_is_optional_only_when_the_file_says_true(self):
        data = {"models": [{"id": "a", "kind": "hf", "name": "o/a", "label": "A", "optional": True, "language": "sv"},
                           {"id": "b", "kind": "hf", "name": "o/b", "label": "B"},
                           {"id": "c", "kind": "hf", "name": "o/c", "label": "C", "optional": "ja"}]}
        a, b, c = parse_catalogue(data)
        self.assertEqual((a.optional, a.language, b.optional, b.language, c.optional), (True, "sv", False, "", False))

    def test_the_swedish_and_norwegian_readers_ship_as_optional_packs_and_everything_else_stays_required(self):
        specs = parse_catalogue(json.loads(SHIPPED.read_text(encoding="utf-8")))
        self.assertEqual({s.name: (s.optional, s.language) for s in specs if s.optional},
                         {"KBLab/kb-whisper-large": (True, "sv"), "NbAiLab/nb-whisper-large": (True, "no")})
        self.assertEqual(len([s for s in specs if not s.optional]), 6)


class PackFileTests(unittest.TestCase):
    def test_nothing_is_switched_on_until_the_user_does_it(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(enabled_packs(folder), set())
            set_pack(folder, "a", True)
            set_pack(folder, "b", True)
            self.assertEqual(enabled_packs(folder), {"a", "b"})
            set_pack(folder, "a", False)
            self.assertEqual(enabled_packs(folder), {"b"})
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["language-packs.json"], "no temporary file left")

    def test_a_broken_file_means_nothing_is_on(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "language-packs.json").write_text("{trasig")
            self.assertEqual(enabled_packs(folder), set())
            (Path(folder) / "language-packs.json").write_text(json.dumps({"enabled": ["a", 5, None]}))
            self.assertEqual(enabled_packs(folder), {"a"})

    def test_many_saves_at_once_never_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            gate, errors = threading.Barrier(8), []

            def work(number):
                gate.wait()
                try:
                    for _ in range(20):
                        set_pack(folder, f"p{number}", True)
                except Exception as error:      # noqa: BLE001
                    errors.append(error)
            threads = [threading.Thread(target=work, args=(number,)) for number in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            self.assertEqual(errors, [])
            self.assertIsInstance(enabled_packs(folder), set)


class ManagerTests(unittest.TestCase):
    def manager(self, backend, folder, catalogue):
        self.catalogue = catalogue
        return ModelManager(lambda: Catalogue(list(self.catalogue), Path(folder) / "models.json", []), {"fake": backend}, folder, folder,
                            free_bytes=lambda: 10 ** 12, sleep=lambda seconds: None, log=lambda message: None)

    def status(self, folder):
        return json.loads((Path(folder) / "models-status.json").read_text(encoding="utf-8"))

    def test_a_pack_that_is_off_is_not_downloaded_and_does_not_hold_the_models_up(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a"})
            manager = self.manager(backend, folder, [spec("a"), spec("sv", optional=True, language="sv")])
            self.assertTrue(manager.run_once())
            self.assertEqual(backend.downloads, [])
            status = self.status(folder)
            self.assertEqual((status["ready"], status["done"], status["total"]), (True, 1, 1), "the pack is not counted")
            pack = next(m for m in status["models"] if m["id"] == "sv")
            self.assertEqual((pack["optional"], pack["enabled"], pack["language"], pack["state"]), (True, False, "sv", "pending"))

    def test_switching_a_pack_on_downloads_it_and_the_models_wait_until_it_is_there(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a"})
            manager = self.manager(backend, folder, [spec("a"), spec("sv", optional=True, language="sv")])
            manager.run_once()
            set_pack(folder, "sv", True)
            self.assertTrue(manager.run_once())
            self.assertEqual(backend.downloads, ["sv"])
            status = self.status(folder)
            self.assertEqual((status["done"], status["total"]), (2, 2))
            self.assertTrue(next(m for m in status["models"] if m["id"] == "sv")["enabled"])

    def test_a_pack_that_is_on_but_fails_to_download_keeps_the_manager_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a"})
            backend.download = mock.Mock(side_effect=RuntimeError("nätverket föll"))
            manager = self.manager(backend, folder, [spec("a"), spec("sv", optional=True)])
            set_pack(folder, "sv", True)
            self.assertFalse(manager.run_once())
            self.assertFalse(manager.ready)

    def test_a_pack_that_is_off_and_on_the_disk_can_be_removed_but_an_enabled_one_cannot(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a", "sv"})
            manager = self.manager(backend, folder, [spec("a"), spec("sv", optional=True)])
            manager.run_once()                                  # records both as installed
            (Path(folder) / "models-remove-request.json").write_text(json.dumps({"keys": ["fake|sv"]}))
            set_pack(folder, "sv", True)
            manager.run_once()
            self.assertEqual(backend.removed, [], "in use, so nothing was removed")
            self.assertIn("används fortfarande", self.status(folder)["notice"])
            set_pack(folder, "sv", False)
            (Path(folder) / "models-remove-request.json").write_text(json.dumps({"keys": ["fake|sv"]}))
            manager.run_once()
            self.assertEqual(backend.removed, ["sv"])

    def test_a_disabled_pack_is_not_offered_as_an_old_model(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.manager(FakeBackend(present={"a", "sv"}), folder, [spec("a"), spec("sv", optional=True)])
            manager.run_once()
            self.assertEqual(self.status(folder)["obsolete"], [])

    def test_the_wait_between_checks_ends_when_a_pack_is_switched_on(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.manager(FakeBackend(present={"a"}), folder, [spec("a")])
            slept = []

            def sleep(seconds):
                slept.append(seconds)
                if len(slept) == 2:
                    set_pack(folder, "sv", True)
            manager.sleep = sleep
            manager._wait(600)
            self.assertEqual(len(slept), 2, "woken after the second step instead of sleeping ten minutes")


def card_status():
    return {"models": [
        {"id": "a", "label": "Whisper large-v3", "name": "large-v3", "kind": "faster-whisper", "used_by": "worker", "size_gb": 2.9, "state": "present", "enabled": True},
        {"id": "whisper-kb-swedish", "label": "KB-Whisper large (svenska)", "name": "KBLab/kb-whisper-large", "kind": "faster-whisper", "used_by": "worker",
         "size_gb": 2.9, "state": "pending", "optional": True, "language": "sv", "enabled": False}]}


class CardTests(unittest.TestCase):
    def test_the_pack_has_its_own_card_and_is_not_in_the_main_table(self):
        page = models_card.render_models_page(card_status(), "tok")
        self.assertIn("Språkpaket (frivilliga)", page)
        main = page.split("Språkpaket (frivilliga)")[0]
        self.assertNotIn("KB-Whisper", main)
        self.assertIn("Whisper large-v3", main)

    def test_a_pack_that_is_off_offers_to_switch_on_and_says_how_much_it_downloads(self):
        html = models_card.render_language_packs(card_status(), "tok", "sv")
        self.assertIn("action='/models/pack'", html)
        self.assertIn("name='id' value='whisper-kb-swedish'", html)
        self.assertIn("name='enabled' value='1'", html)
        self.assertIn("Slå på och hämta", html)
        self.assertIn("Hämta ungefär 2.9 GB", html)
        self.assertIn("<span data-pack-state>av</span>", html)
        self.assertIn("Språk: svenska · 2.9 GB", html)
        self.assertNotIn("<table", html, "stacked blocks: a table with buttons is cut off on a phone")
        self.assertNotIn("Ta bort från disken", html, "nothing on the disk to remove")

    def test_a_pack_that_is_off_but_on_the_disk_can_be_removed(self):
        status = card_status()
        status["models"][1]["state"] = "present"
        html = models_card.render_language_packs(status, "tok", "sv")
        self.assertIn("av (ligger kvar på disken)", html)
        self.assertIn("name='key' value='faster-whisper|KBLab/kb-whisper-large'", html)
        self.assertIn("Ta bort från disken", html)

    def test_a_pack_that_is_on_offers_to_switch_off_and_shows_the_download(self):
        status = card_status()
        status["models"][1].update(enabled=True, state="downloading", percent=40, detail="1.2 GB")
        html = models_card.render_language_packs(status, "tok", "en")
        self.assertIn("name='enabled' value='0'", html)
        self.assertIn("Switch off", html)
        self.assertIn("40 %", html)
        self.assertNotIn("Remove from the disk", html)

    def test_no_card_without_packs_and_tokens_are_escaped(self):
        self.assertEqual(models_card.render_language_packs({"models": card_status()["models"][:1]}, "tok"), "")
        self.assertEqual(models_card.render_language_packs(None, "tok"), "")
        self.assertIn("value='t&lt;&gt;'", models_card.render_language_packs(card_status(), "t<>"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PackRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = Path(self.folder.name)
        for name, value in (("CONFIG", config), ("DATABASE", config / "subsynq.db")):
            patcher = mock.patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        self.config = config
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, **fields):
        body = urllib.parse.urlencode({"token": server.SCAN_TOKEN, **fields}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}/models/pack", body)
        try:
            urllib.request.build_opener(NoRedirect).open(request, timeout=10)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location") or error.read().decode()
        raise AssertionError("expected a redirect or an error")

    def test_switching_the_swedish_pack_on_and_off(self):
        self.assertEqual(self.post(id="whisper-kb-swedish", enabled="1"), (303, "/settings/models?requested=pack"))
        self.assertEqual(enabled_packs(self.config), {"whisper-kb-swedish"})
        self.assertEqual(self.post(id="whisper-kb-swedish", enabled="0")[0], 303)
        self.assertEqual(enabled_packs(self.config), set())

    def test_only_listed_optional_packs_can_be_switched(self):
        for pack in ("whisper-large-v3", "finns-inte", "", "../../etc"):
            self.assertEqual(self.post(id=pack, enabled="1"), (400, "Okänt språkpaket."), pack)
        self.assertEqual(enabled_packs(self.config), set())

    def test_a_request_without_the_token_is_refused(self):
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}/models/pack",
                                         urllib.parse.urlencode({"id": "whisper-kb-swedish", "enabled": "1"}).encode())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.build_opener(NoRedirect).open(request, timeout=10)
        self.assertIn(caught.exception.code, (400, 403))
        self.assertEqual(enabled_packs(self.config), set())


if __name__ == "__main__":
    unittest.main()
