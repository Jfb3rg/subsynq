import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import models_card
import server

INDEX = Path(server.__file__).with_name("index.html").read_text(encoding="utf-8")


class ModelsPageDoesNotReloadItselfTests(unittest.TestCase):
    def test_the_update_badge_does_not_use_the_class_that_makes_a_page_reload_every_ten_seconds(self):
        badge = models_card._version_text({"update": "available"}, "sv")
        self.assertIn("subtitle-status update", badge)
        self.assertNotIn("working", badge)
        self.assertIn(".subtitle-status.working, .subtitle-status.update", INDEX)

    def test_ticked_model_updates_hold_back_automatic_reloads(self):
        self.assertIn('form[action="/models/update"] input[type="checkbox"]:checked', INDEX)


class WebhookAddressTests(unittest.TestCase):
    def test_every_webhook_address_is_completed_with_the_address_the_page_was_opened_from(self):
        page = server.render_media_settings_content("tok", "secret value", "", "")
        for name in ("radarr", "sonarr", "jellyfin", "emby"):
            self.assertIn(f"data-webhook-path='/webhooks/{name}?token=secret%20value'", page)
        self.assertIn("Använd inte containernamnet", page)
        self.assertIn("location.origin+code.dataset.webhookPath", INDEX)

    def test_the_address_is_escaped(self):
        self.assertIn("&#x27;", server.webhook_code("/x'y"))




class BulkBarExplainsGreyButtonsTests(unittest.TestCase):
    def test_the_bar_has_a_hidden_hint_that_the_page_script_shows_when_nothing_ticked_can_be_acted_on(self):
        bar = server.render_bulk_bar("media", "tok", "sv")
        self.assertIn("data-bulk-hint hidden", bar)
        self.assertIn("ett jobb pågår", bar)
        self.assertIn("a job is in progress", server.render_bulk_bar("media", "tok", "en"))
        self.assertIn("hint.hidden=!(chosen.length>0&&!anyUsable)", INDEX)


if __name__ == "__main__":
    unittest.main()
