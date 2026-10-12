import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from server import (
    queue_webhook_media, remap_root, render_media_settings_content, resolve_webhook_media,
    resolve_webhook_media_per_root,
)


class RemapRootTests(unittest.TestCase):
    def test_empty_prefix_leaves_path_untouched(self):
        self.assertEqual(remap_root("/movies/Film/Film.mkv", "", "/movies"),
                         "/movies/Film/Film.mkv")

    def test_matching_prefix_is_replaced_with_subsynqs_own_root(self):
        self.assertEqual(remap_root("/data/movies/Film/Film.mkv", "/data/movies", "/movies"),
                         "/movies/Film/Film.mkv")

    def test_non_matching_prefix_is_left_alone(self):
        # resolve_webhook_media() is what rejects a path that still doesn't
        # land inside the right root after this -- remap_root() itself just
        # does the substring swap and never guesses.
        self.assertEqual(remap_root("/other/Film.mkv", "/data/movies", "/movies"),
                         "/other/Film.mkv")


class ResolveWebhookMediaTests(unittest.TestCase):
    def test_path_outside_the_configured_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ligger utanför"):
            resolve_webhook_media("/etc/passwd", "/movies", "")

    def test_unsupported_extension_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "movies"
            root.mkdir()
            (root / "notes.txt").write_text("x")
            with self.assertRaisesRegex(ValueError, "stöds inte"):
                resolve_webhook_media(str(root / "notes.txt"), str(root), "")

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "movies"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "hittades inte"):
                resolve_webhook_media(str(root / "missing.mkv"), str(root), "")

    def test_valid_path_with_root_remapping_resolves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "movies"
            root.mkdir()
            (root / "Film.mkv").touch()
            radarr_path = "/data/movies/Film.mkv"
            resolved = resolve_webhook_media(radarr_path, str(root), "/data/movies")
            self.assertEqual(resolved, (root / "Film.mkv").resolve())


class ResolveWebhookMediaPerRootTests(unittest.TestCase):
    """Emby on Linux commonly mounts movies and TV as two unrelated
    top-level directories rather than subfolders of one shared parent --
    each SubSynq root needs its own independent prefix."""

    def test_each_root_uses_its_own_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "movies").mkdir()
            (base / "tv").mkdir()
            (base / "movies" / "Film.mkv").touch()
            (base / "tv" / "Show.mkv").touch()
            root_prefixes = (
                (str(base / "movies"), "/data/movies"),
                (str(base / "tv"), "/data/shows"),
            )
            self.assertEqual(
                resolve_webhook_media_per_root("/data/movies/Film.mkv", root_prefixes),
                (base / "movies" / "Film.mkv").resolve())
            self.assertEqual(
                resolve_webhook_media_per_root("/data/shows/Show.mkv", root_prefixes),
                (base / "tv" / "Show.mkv").resolve())

    def test_path_outside_every_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "movies").mkdir()
            root_prefixes = ((str(base / "movies"), "/data/movies"),)
            with self.assertRaisesRegex(ValueError, "ligger utanför"):
                resolve_webhook_media_per_root("/somewhere/else/Film.mkv", root_prefixes)


class QueueWebhookMediaTests(unittest.TestCase):
    def test_skips_when_ai_subtitle_already_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "Film.mkv"
            media.touch()
            media.with_suffix(".ai.sv.srt").touch()
            reason = queue_webhook_media(media, ignore_existing=False)
        self.assertIn("finns redan", reason)

    def test_skips_when_embedded_swedish_subtitle_present(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "Film.mkv"
            media.touch()
            probe_result = {"status": "ok", "subtitle": [
                {"language": "sv", "title": "", "codec": "srt"}]}
            with patch("server.probe_media", return_value=probe_result):
                reason = queue_webhook_media(media, ignore_existing=False)
        self.assertIn("inbäddad", reason)

    def test_ignore_existing_skips_the_embedded_check(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "Film.mkv"
            media.touch()
            with patch("server.probe_media") as probe, \
                    patch("server.enqueue_job") as enqueue:
                reason = queue_webhook_media(media, ignore_existing=True)
            probe.assert_not_called()
            enqueue.assert_called_once_with(unittest.mock.ANY, str(media), media.name)
        self.assertIsNone(reason)

    def test_queues_a_fresh_file(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "Film.mkv"
            media.touch()
            with patch("server.probe_media", return_value={"status": "ok", "subtitle": []}), \
                    patch("server.enqueue_job") as enqueue:
                reason = queue_webhook_media(media, ignore_existing=False)
            enqueue.assert_called_once_with(unittest.mock.ANY, str(media), media.name)
        self.assertIsNone(reason)


class MediaSettingsRenderTests(unittest.TestCase):
    def test_webhook_urls_and_token_are_shown(self):
        page = render_media_settings_content("csrf-token", "my-webhook-secret", "", "")
        self.assertIn("/webhooks/radarr?token=my-webhook-secret", page)
        self.assertIn("/webhooks/sonarr?token=my-webhook-secret", page)
        self.assertIn("/webhooks/jellyfin?token=my-webhook-secret", page)
        self.assertIn("/webhooks/emby?token=my-webhook-secret", page)

    def test_saved_and_regenerated_notices_are_conditional(self):
        plain = render_media_settings_content("t", "secret", "", "")
        saved = render_media_settings_content("t", "secret", "", "", saved=True)
        regenerated = render_media_settings_content("t", "secret", "", "", token_regenerated=True)
        self.assertNotIn("rotmapparna är sparade", plain.lower())
        self.assertIn("rotmapparna är sparade", saved.lower())
        self.assertIn("ny webhook-token", regenerated.lower())


if __name__ == "__main__":
    unittest.main()
