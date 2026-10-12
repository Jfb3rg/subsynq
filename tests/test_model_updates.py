import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import model_manager as mm
from model_manager import Catalogue, ModelManager, ModelSpec, OllamaBackend


class VersionedBackend:
    """A fake backend where each model has an installed and a latest version."""

    def __init__(self, installed, latest, fail_update=()):
        self.installed, self.latest, self.updated, self.fail_update = dict(installed), dict(latest), [], set(fail_update)

    def is_present(self, spec):
        return spec.name in self.installed

    def download(self, spec, report):
        self.installed[spec.name] = self.latest[spec.name]

    def remove(self, spec):
        self.installed.pop(spec.name, None)

    def installed_version(self, spec):
        return self.installed.get(spec.name)

    def latest_version(self, spec):
        value = self.latest[spec.name]
        if isinstance(value, Exception):
            raise value
        return value

    def update(self, spec, report):
        self.updated.append(spec.name)
        report(50, "halvvägs")
        if spec.name in self.fail_update:
            raise RuntimeError("nätverket föll")
        self.installed[spec.name] = self.latest[spec.name]


def specs(*names):
    return [ModelSpec(n, "fake", n, f"Modell {n.upper()}", 1.0, "worker") for n in names]


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        self.clock = [1_000_000.0]
        self.busy = False

    def manager(self, backend, names=("a", "b", "c")):
        return ModelManager(lambda: Catalogue(specs(*names), self.dir / "models.json", []), {"fake": backend},
                            self.dir, self.dir, free_bytes=lambda: 10 ** 12, sleep=lambda s: None,
                            log=lambda m: None, is_busy=lambda: self.busy, now=lambda: self.clock[0])

    def status(self):
        return json.loads((self.dir / "models-status.json").read_text(encoding="utf-8"))

    def versions(self):
        return {m["id"]: m["update"] for m in self.status()["models"]}

    def request(self, **body):
        (self.dir / "models-update-request.json").write_text(json.dumps(body), encoding="utf-8")

    def test_it_only_looks_it_never_downloads_by_itself(self):
        backend = VersionedBackend({"a": "v1", "b": "v1", "c": "v1"}, {"a": "v1", "b": "v2", "c": RuntimeError("offline")})
        manager = self.manager(backend)
        self.assertTrue(manager.run_once())
        self.assertEqual(self.versions(), {"a": "current", "b": "available", "c": "unknown"})
        model_b = [m for m in self.status()["models"] if m["id"] == "b"][0]
        self.assertEqual((model_b["installed"], model_b["latest"]), ("v1", "v2"))
        self.assertEqual(backend.updated, [])
        self.assertEqual(backend.installed["b"], "v1")
        self.assertTrue(self.status()["update_checked_at"])

    def test_ready_is_reported_before_the_slow_check_and_the_check_repeats_only_every_12_hours(self):
        calls = []

        class Counting(VersionedBackend):
            def latest_version(self, spec):
                calls.append(spec.name)
                return super().latest_version(spec)

        manager = self.manager(Counting({"a": "v1"}, {"a": "v1"}), names=("a",))
        manager.run_once()
        self.assertEqual(calls, ["a"])
        manager.run_once()
        self.assertEqual(calls, ["a"])                      # too soon
        self.clock[0] += mm.UPDATE_CHECK_SECONDS + 1
        manager.run_once()
        self.assertEqual(calls, ["a", "a"])

    def test_a_requested_update_downloads_the_new_version_and_only_for_models_that_have_one(self):
        backend = VersionedBackend({"a": "v1", "b": "v1", "c": "v1"}, {"a": "v1", "b": "v2", "c": "v1"})
        manager = self.manager(backend)
        manager.run_once()
        self.request(keys=["fake|b", "fake|a", "fake|finns-inte"])
        manager.run_once()
        self.assertEqual(backend.updated, ["b"])
        self.assertEqual(backend.installed["b"], "v2")
        self.assertEqual(self.versions()["b"], "current")
        notice = self.status()["notice"]
        self.assertIn("Modell B: uppdaterad", notice)
        self.assertIn("Modell A: är redan den senaste versionen", notice)
        self.assertIn("finns-inte: okänd eller saknas", notice)
        self.assertFalse((self.dir / "models-update-request.json").exists())
        self.assertTrue(manager.ready)

    def test_an_update_waits_while_a_subtitle_job_is_running_and_keeps_the_request(self):
        backend = VersionedBackend({"a": "v1"}, {"a": "v2"})
        manager = self.manager(backend, names=("a",))
        manager.run_once()
        self.request(keys=["fake|a"])
        self.busy = True
        manager.run_once()
        self.assertEqual(backend.updated, [])
        self.assertTrue((self.dir / "models-update-request.json").exists())
        self.assertIn("väntar tills pågående undertextjobb är klara", self.status()["notice"])
        self.busy = False
        manager.run_once()
        self.assertEqual(backend.updated, ["a"])
        self.assertFalse((self.dir / "models-update-request.json").exists())

    def test_a_failed_update_leaves_the_old_version_in_place_and_says_so(self):
        backend = VersionedBackend({"a": "v1"}, {"a": "v2"}, fail_update={"a"})
        manager = self.manager(backend, names=("a",))
        manager.run_once()
        self.request(keys=["fake|a"])
        manager.run_once()
        self.assertEqual(backend.installed["a"], "v1")
        self.assertIn("uppdateringen misslyckades", self.status()["notice"])
        self.assertEqual(self.versions()["a"], "available")
        self.assertTrue(manager.ready)

    def test_check_now_rechecks_immediately(self):
        backend = VersionedBackend({"a": "v1"}, {"a": "v1"})
        manager = self.manager(backend, names=("a",))
        manager.run_once()
        self.assertEqual(self.versions()["a"], "current")
        backend.latest["a"] = "v2"
        manager.run_once()
        self.assertEqual(self.versions()["a"], "current")  # not due yet
        self.request(check=True)
        manager.run_once()
        self.assertEqual(self.versions()["a"], "available")
        self.assertIn("Kontrollerade efter nya versioner", self.status()["notice"])

    def test_too_little_disk_space_blocks_the_update(self):
        backend = VersionedBackend({"a": "v1"}, {"a": "v2"})
        manager = ModelManager(lambda: Catalogue(specs("a"), None, []), {"fake": backend}, self.dir, self.dir,
                               free_bytes=lambda: int(0.1 * mm.GIB), sleep=lambda s: None, log=lambda m: None,
                               is_busy=lambda: False, now=lambda: self.clock[0])
        manager.run_once()
        self.request(keys=["fake|a"])
        manager.run_once()
        self.assertEqual(backend.updated, [])
        self.assertIn("för lite diskutrymme", self.status()["notice"])

    def test_waiting_wakes_up_for_an_update_request_too(self):
        steps = []
        manager = self.manager(VersionedBackend({}, {}))

        def sleep(seconds):
            steps.append(seconds)
            if len(steps) == 2:
                self.request(check=True)

        manager.sleep = sleep
        manager._wait(600)
        self.assertEqual(len(steps), 2)


class OllamaVersionTests(unittest.TestCase):
    def test_registry_digest_is_the_sha256_of_the_manifest_and_the_path_is_built_correctly(self):
        self.assertEqual(mm._ollama_registry_path("gemma3:12b"), ("library/gemma3", "12b"))
        self.assertEqual(mm._ollama_registry_path("gemma3"), ("library/gemma3", "latest"))
        self.assertEqual(mm._ollama_registry_path("someone/model:q4"), ("someone/model", "q4"))
        seen = []

        class Response:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(request, timeout):
            seen.append((request.full_url, request.get_header("Accept")))
            return Response(b'{"schemaVersion":2}')

        digest = mm._registry_digest("gemma3:12b", opener)
        self.assertEqual(digest, hashlib.sha256(b'{"schemaVersion":2}').hexdigest())
        self.assertEqual(seen[0][0], "https://registry.ollama.ai/v2/library/gemma3/manifests/12b")
        self.assertIn("manifest.v2+json", seen[0][1])

    def test_installed_version_is_the_digest_ollama_reports_for_that_model(self):
        import io

        class Local:
            def __call__(self, request, timeout):
                body = json.dumps({"models": [{"name": "gemma3:12b", "digest": "aaa"},
                                              {"name": "gemma4:12b", "digest": "bbb"}]}).encode()
                stream = io.BytesIO(body)
                stream.__enter__ = lambda *a: stream
                stream.__exit__ = lambda *a: False
                return stream

        backend = OllamaBackend("http://x", opener=Local(), registry_opener=lambda r, timeout: None)
        self.assertEqual(backend.installed_version(ModelSpec("g", "ollama", "gemma4:12b", "G", 1, "t")), "bbb")
        self.assertIsNone(backend.installed_version(ModelSpec("g", "ollama", "okänd:1b", "G", 1, "t")))


class HubVersionTests(unittest.TestCase):
    def test_installed_revision_comes_from_refs_main(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "models--Qwen--Qwen3-ASR-1.7B"
            (repo / "refs").mkdir(parents=True)
            self.assertIsNone(mm._installed_revision(repo))
            (repo / "refs" / "main").write_text("7278e1e70f\n", encoding="utf-8")
            self.assertEqual(mm._installed_revision(repo), "7278e1e70f")
            backend = mm.HubBackend(folder)
            self.assertEqual(backend.installed_version(ModelSpec("q", "hf", "Qwen/Qwen3-ASR-1.7B", "Q", 1, "w")),
                             "7278e1e70f")


if __name__ == "__main__":
    unittest.main()
