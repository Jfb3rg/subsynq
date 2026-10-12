import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import model_manager as mm
from model_manager import (Catalogue, CatalogueError, FasterWhisperBackend, HubBackend, ModelManager, ModelSpec,
                           OllamaBackend, configured_name, load_catalogue, parse_catalogue)

SHIPPED = Path(mm.__file__).with_name("models.json")


def make_snapshot(cache_dir, repo_id, files, incomplete=False):
    repo = Path(cache_dir) / ("models--" + repo_id.replace("/", "--"))
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (repo / "blobs").mkdir()
    for name in files:
        blob = repo / "blobs" / f"blob-{name}"
        blob.write_bytes(b"x" * 10)
        (snapshot / name).symlink_to(blob)
    if incomplete:
        (repo / "blobs" / "partial.incomplete").write_bytes(b"x")
    return repo


def entry(**fields):
    base = {"id": "a", "kind": "hf", "name": "org/model", "label": "Modell", "size_gb": 1, "used_by": "worker"}
    base.update(fields)
    return base


class CatalogueTests(unittest.TestCase):
    def test_the_shipped_models_file_lists_the_six_models_the_pipeline_loads(self):
        specs = parse_catalogue(json.loads(SHIPPED.read_text(encoding="utf-8")))
        self.assertEqual([s.name for s in specs],
                         ["large-v3", "large-v2", "Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B",
                          "gemma3:12b", "gemma4:12b"])
        self.assertEqual({s.role: s.name for s in specs if s.role},
                         {"translation": "gemma3:12b", "review": "gemma4:12b"})
        self.assertEqual({s.kind for s in specs}, {"faster-whisper", "hf", "ollama"})
        self.assertEqual(len({s.id for s in specs}), 6)

    def test_invalid_files_are_rejected_with_a_reason(self):
        for data, reason in (
                ([], "models"), ({"models": []}, "models"), ({"models": ["x"]}, "ingen post"),
                ({"models": [entry(kind="onnx")]}, "kind"), ({"models": [entry(name="")]}, "name"),
                ({"models": [entry(), entry()]}, "två gånger"), ({"models": [entry(size_gb="stor")]}, "size_gb")):
            with self.assertRaisesRegex(CatalogueError, reason):
                parse_catalogue(data)

    def test_your_own_file_wins_and_a_broken_one_falls_back_with_a_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            own = Path(folder) / "models.json"
            own.write_text(json.dumps({"models": [entry(id="own", name="org/own")]}), encoding="utf-8")
            result = load_catalogue([own, SHIPPED], environ={})
            self.assertEqual((result.source, [s.id for s in result.specs]), (own, ["own"]))
            own.write_text("{trasig", encoding="utf-8")
            result = load_catalogue([own, SHIPPED], environ={})
            self.assertEqual(result.source, SHIPPED)
            self.assertEqual(len(result.specs), 6)
            self.assertIn("models.json", result.errors[0])
            self.assertEqual(load_catalogue([Path(folder) / "saknas.json"], environ={}).specs, [])

    def test_an_environment_variable_still_overrides_the_two_gemma_names(self):
        result = load_catalogue([SHIPPED], environ={"SUBSYNQ_TRANSLATION_MODEL": "gemma3:27b"})
        names = {s.role: s.name for s in result.specs if s.role}
        self.assertEqual(names, {"translation": "gemma3:27b", "review": "gemma4:12b"})
        self.assertEqual(configured_name("translation", "x", config_dir="/finns-inte", environ={}), "gemma3:12b")
        self.assertEqual(configured_name("review", "x", config_dir="/finns-inte", environ={}), "gemma4:12b")
        self.assertEqual(configured_name("saknas", "standard", config_dir="/finns-inte", environ={}), "standard")


class DiskPresenceTests(unittest.TestCase):
    def test_faster_whisper_presence_matches_what_the_worker_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FasterWhisperBackend(folder)
            spec = ModelSpec("w", "faster-whisper", "large-v3", "Whisper", 2.9, "worker")
            self.assertFalse(backend.is_present(spec))
            make_snapshot(folder, "Systran/faster-whisper-large-v3", ["model.bin", "config.json", "tokenizer.json"])
            self.assertTrue(backend.is_present(spec))

    def test_interrupted_or_partial_downloads_do_not_count(self):
        with tempfile.TemporaryDirectory() as folder:
            make_snapshot(folder, "Systran/faster-whisper-large-v3", ["model.bin", "config.json"], incomplete=True)
            self.assertFalse(FasterWhisperBackend(folder).is_present(
                ModelSpec("w", "faster-whisper", "large-v3", "Whisper", 2.9, "worker")))
        with tempfile.TemporaryDirectory() as folder:
            make_snapshot(folder, "Systran/faster-whisper-large-v3", ["config.json"])
            self.assertFalse(FasterWhisperBackend(folder).is_present(
                ModelSpec("w", "faster-whisper", "large-v3", "Whisper", 2.9, "worker")))

    def test_hub_models_need_config_and_weights(self):
        spec = ModelSpec("q", "hf", "Qwen/Qwen3-ASR-1.7B", "Qwen", 4.4, "qwen-asr")
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(HubBackend(folder).is_present(spec))
            make_snapshot(folder, "Qwen/Qwen3-ASR-1.7B", ["config.json"])
            self.assertFalse(HubBackend(folder).is_present(spec))
        with tempfile.TemporaryDirectory() as folder:
            make_snapshot(folder, "Qwen/Qwen3-ASR-1.7B", ["config.json", "model.safetensors"])
            self.assertTrue(HubBackend(folder).is_present(spec))

    def test_removal_deletes_exactly_one_models_folder_and_nothing_else(self):
        spec = ModelSpec("q", "hf", "Qwen/Qwen3-ASR-1.7B", "Qwen", 4.4, "qwen-asr")
        with tempfile.TemporaryDirectory() as folder:
            make_snapshot(folder, "Qwen/Qwen3-ASR-1.7B", ["config.json", "model.safetensors"])
            other = make_snapshot(folder, "Qwen/Qwen3-ForcedAligner-0.6B", ["config.json", "m.safetensors"])
            HubBackend(folder).remove(spec)
            self.assertFalse(HubBackend(folder).is_present(spec))
            self.assertTrue(other.exists())
            HubBackend(folder).remove(spec)   # already gone: not an error

    def test_removal_refuses_symlinks_and_look_alikes(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "viktigt"
            target.mkdir()
            (target / "fil.txt").write_text("behåll")
            (Path(folder) / "models--Qwen--Qwen3-ASR-1.7B").symlink_to(target)
            with self.assertRaises(ValueError):
                HubBackend(folder).remove(ModelSpec("q", "hf", "Qwen/Qwen3-ASR-1.7B", "Qwen", 4.4, "qwen-asr"))
            self.assertTrue((target / "fil.txt").exists())
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "inte-en-modell").mkdir()
            with self.assertRaises(ValueError):
                mm._remove_cache_dir(Path(folder) / "inte-en-modell", folder)


class FakeOllama(BaseHTTPRequestHandler):
    models = []
    pull_lines = []
    deleted = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": n} for n in self.models]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.end_headers()
        for line in self.pull_lines:
            self.wfile.write((json.dumps(line) + "\n").encode())

    def do_DELETE(self):
        self.deleted.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class OllamaBackendTests(unittest.TestCase):
    def setUp(self):
        FakeOllama.deleted = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.backend = OllamaBackend(f"http://127.0.0.1:{self.httpd.server_port}")
        self.spec = ModelSpec("g", "ollama", "gemma3:12b", "Gemma", 8.1, "translate-engine")

    def test_presence_follows_the_models_ollama_reports(self):
        FakeOllama.models = ["gemma4:12b"]
        self.assertFalse(self.backend.is_present(self.spec))
        FakeOllama.models = ["gemma4:12b", "gemma3:12b"]
        self.assertTrue(self.backend.is_present(self.spec))

    def test_pull_reports_progress_across_layers_and_finishes_on_success(self):
        FakeOllama.pull_lines = [
            {"status": "pulling manifest"},
            {"status": "pulling a", "digest": "a", "total": 100, "completed": 25},
            {"status": "pulling a", "digest": "a", "total": 100, "completed": 100},
            {"status": "pulling b", "digest": "b", "total": 100, "completed": 50},
            {"status": "success"},
        ]
        seen = []
        self.backend.download(self.spec, lambda percent, detail: seen.append(percent))
        # Never 100 % until the manager has confirmed the model is really there.
        self.assertEqual(seen, [25, 99, 75, 75])

    def test_pull_error_and_missing_success_are_failures(self):
        FakeOllama.pull_lines = [{"error": "pull model manifest: file does not exist"}]
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            self.backend.download(self.spec, lambda *a: None)
        FakeOllama.pull_lines = [{"status": "pulling manifest"}]
        with self.assertRaisesRegex(RuntimeError, "bekräfta"):
            self.backend.download(self.spec, lambda *a: None)

    def test_remove_sends_a_delete_for_exactly_that_model(self):
        self.backend.remove(self.spec)
        self.assertEqual(FakeOllama.deleted, [{"model": "gemma3:12b"}])


class FakeBackend:
    def __init__(self, present=(), fail=()):
        self.present, self.fail, self.downloads, self.removed = set(present), set(fail), [], []

    def is_present(self, spec):
        return spec.name in self.present

    def download(self, spec, report):
        self.downloads.append(spec.name)
        report(40, "1.0 GB")
        if spec.name in self.fail:
            raise RuntimeError("nätverket föll")
        self.present.add(spec.name)

    def remove(self, spec):
        self.removed.append(spec.name)
        self.present.discard(spec.name)


def specs(*names):
    return [ModelSpec(n, "fake", n, f"Modell {n.upper()}", 1.0, "worker") for n in names]


class ManagerTests(unittest.TestCase):
    def manager(self, backend, folder, catalogue=None, free=10 ** 12, sleep=lambda s: None):
        self.catalogue = catalogue if catalogue is not None else specs("a", "b")
        return ModelManager(lambda: Catalogue(list(self.catalogue), Path(folder) / "models.json", []),
                            {"fake": backend}, folder, folder, free_bytes=lambda: free, sleep=sleep,
                            log=lambda m: None)

    def status(self, folder):
        return json.loads((Path(folder) / "models-status.json").read_text(encoding="utf-8"))

    def test_everything_present_means_ready_without_downloading(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a", "b"})
            manager = self.manager(backend, folder)
            self.assertTrue(manager.run_once())
            self.assertTrue(manager.ready)
            self.assertEqual(backend.downloads, [])
            status = self.status(folder)
            self.assertEqual((status["state"], status["ready"], status["done"], status["total"]),
                             ("ready", True, 2, 2))
            self.assertEqual(status["source"], str(Path(folder) / "models.json"))

    def test_a_models_folder_that_cannot_be_written_to_gives_a_clear_message_and_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present=set())
            manager = self.manager(backend, folder)
            with patch("model_manager.os.access", return_value=False):
                self.assertFalse(manager.run_once())
            self.assertEqual(backend.downloads, [])
            status = self.status(folder)
            self.assertEqual(status["state"], "error")
            self.assertIn("chown", status["message"])
            self.assertIn("/models", status["message"])
            self.assertTrue(all(m["state"] == "error" and "chown" in m["detail"] for m in status["models"]))
            self.assertTrue(manager.run_once(), "när mappen går att skriva i hämtas allt av sig själv")

    def test_a_permission_error_during_download_is_explained_in_plain_language(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(fail={"a"})
            manager = self.manager(backend, folder)
            with patch.object(FakeBackend, "download", side_effect=PermissionError(13, "Permission denied", "/models/x")):
                self.assertFalse(manager.run_once())
            self.assertIn("chown", manager._models["a"]["detail"])
            self.assertIn("/models/x", manager._models["a"]["detail"])

    def test_missing_models_are_downloaded_and_then_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a"})
            manager = self.manager(backend, folder)
            self.assertFalse(manager.ready)
            self.assertTrue(manager.run_once())
            self.assertEqual(backend.downloads, ["b"])
            self.assertEqual({m["id"]: m["state"] for m in self.status(folder)["models"]},
                             {"a": "present", "b": "present"})

    def test_one_failure_keeps_it_not_ready_but_the_others_still_download(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(fail={"a"})
            manager = self.manager(backend, folder)
            self.assertFalse(manager.run_once())
            self.assertEqual(backend.downloads, ["a", "b"])
            status = self.status(folder)
            self.assertEqual(status["state"], "error")
            self.assertIn("nätverket föll", status["message"])
            self.assertEqual({m["id"]: m["state"] for m in status["models"]}, {"a": "error", "b": "present"})
            backend.fail.clear()
            self.assertTrue(manager.run_once())
            self.assertTrue(manager.ready)

    def test_too_little_disk_space_stops_before_downloading(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend()
            manager = self.manager(backend, folder, free=int(0.5 * mm.GIB))
            self.assertFalse(manager.run_once())
            self.assertEqual(backend.downloads, [])
            self.assertIn("diskutrymme", self.status(folder)["message"])

    def test_a_backend_that_cannot_be_checked_counts_as_missing_not_a_crash(self):
        class Broken(FakeBackend):
            def is_present(self, spec):
                raise OSError("Ollama är inte uppe än")
        with tempfile.TemporaryDirectory() as folder:
            backend = Broken()
            self.manager(backend, folder).run_once()
            self.assertEqual(backend.downloads, ["a", "b"])

    def test_changing_the_list_downloads_the_new_model_and_offers_the_old_one_for_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a", "b"})
            manager = self.manager(backend, folder, specs("a", "b"))
            self.assertTrue(manager.run_once())
            self.assertEqual(self.status(folder)["obsolete"], [])
            self.catalogue = specs("a", "c")            # b is replaced by a newer model c
            self.assertTrue(manager.run_once())
            self.assertEqual(backend.downloads, ["c"])
            status = self.status(folder)
            self.assertEqual([m["id"] for m in status["models"]], ["a", "c"])
            self.assertEqual([(o["key"], o["label"]) for o in status["obsolete"]], [("fake|b", "Modell B")])
            self.assertEqual(backend.removed, [])        # offered, never removed by itself
            self.assertIn("b", backend.present)

    def test_only_a_requested_removal_of_an_obsolete_model_is_carried_out(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a", "b", "c"})
            manager = self.manager(backend, folder, specs("a", "b"))
            manager.run_once()
            self.catalogue = specs("a", "c")
            manager.run_once()
            request = Path(folder) / "models-remove-request.json"
            request.write_text(json.dumps({"keys": ["fake|b", "fake|a", "fake|okänd"]}), encoding="utf-8")
            manager.run_once()
            self.assertEqual(backend.removed, ["b"])      # b was obsolete; a is still listed; the third is unknown
            self.assertFalse(request.exists())
            status = self.status(folder)
            self.assertEqual(status["obsolete"], [])
            self.assertIn("Modell B: borttagen", status["notice"])
            self.assertIn("används fortfarande", status["notice"])
            self.assertIn("okänd modell", status["notice"])

    def test_models_that_were_never_installed_by_subsynq_cannot_be_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(present={"a", "b", "x"})
            manager = self.manager(backend, folder, specs("a", "b"))
            manager.run_once()
            (Path(folder) / "models-remove-request.json").write_text(json.dumps({"keys": ["fake|x"]}),
                                                                    encoding="utf-8")
            manager.run_once()
            self.assertEqual(backend.removed, [])

    def test_a_missing_or_invalid_list_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ModelManager(lambda: Catalogue([], None, ["x/models.json: trasig"]), {"fake": FakeBackend()},
                                   folder, folder, sleep=lambda s: None, log=lambda m: None)
            self.assertFalse(manager.run_once())
            status = self.status(folder)
            self.assertEqual(status["state"], "error")
            self.assertIn("trasig", status["message"])

    def test_run_forever_retries_with_growing_delay_then_rechecks_when_ready(self):
        waited = []

        class Stop(Exception):
            pass

        def sleep(seconds):
            waited.append(seconds)
            if sum(waited) >= 60 + 120 + 2 * mm.RECHECK_SECONDS:
                raise Stop

        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend(fail={"a", "b"})
            manager = self.manager(backend, folder, sleep=sleep)
            original, calls = manager.run_once, []

            def run_once():
                calls.append(1)
                if len(calls) == 3:
                    backend.fail.clear()
                return original()
            manager.run_once = run_once
            with self.assertRaises(Stop):
                manager.run_forever()
            self.assertEqual(len(calls), 4)
            self.assertEqual(sum(waited), 60 + 120 + 2 * mm.RECHECK_SECONDS)
            self.assertTrue(all(step <= mm.WAIT_STEP for step in waited))

    def test_waiting_wakes_up_early_for_a_removal_request(self):
        with tempfile.TemporaryDirectory() as folder:
            steps = []

            def sleep(seconds):
                steps.append(seconds)
                if len(steps) == 3:
                    (Path(folder) / "models-remove-request.json").write_text("{}", encoding="utf-8")

            manager = self.manager(FakeBackend(), folder, sleep=sleep)
            manager._wait(600)
            self.assertEqual(len(steps), 3)


if __name__ == "__main__":
    unittest.main()
