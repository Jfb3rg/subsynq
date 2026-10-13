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


class BulkConfirmationIsOnThePageTests(unittest.TestCase):
    """A browser can block confirm() dialogs for a site without any sign, which made the bulk button look dead."""

    def test_the_bar_asks_inline_instead_of_with_a_browser_dialog(self):
        bar = server.render_bulk_bar("movie", "tok", "sv")
        self.assertIn("data-bulk-ask hidden", bar)
        self.assertIn("data-bulk-yes", bar)
        self.assertIn("data-bulk-no", bar)
        self.assertIn("Ja, fortsätt", bar)
        self.assertIn("Yes, continue", server.render_bulk_bar("movie", "tok", "en"))
        script = INDEX[INDEX.index("const bulkBar="):]
        self.assertNotIn("confirm(submitter", script)
        self.assertIn("bulkBar.requestSubmit(submitter)", script)
        self.assertIn("bulkBar.dataset.confirmed", script)


class RowDetailsTests(unittest.TestCase):
    """The right column of the library is quiet: the status pill opens the details, the main action stays visible."""

    AI = {"path": "/movies/a.mkv", "name": "a.mkv", "kind": "movie", "size_bytes": 1, "ai_sv_exists": True,
          "ai_sv_path": "/movies/a.ai.sv.srt", "embedded_sv_exists": False, "probe": {}}
    MISSING = {"path": "/movies/b.mkv", "name": "b.mkv", "kind": "movie", "size_bytes": 1, "ai_sv_exists": False,
               "embedded_sv_exists": False, "probe": {}}

    def test_a_row_with_an_ai_subtitle_hides_its_actions_behind_the_green_pill(self):
        html = server.render_media_row(self.AI, token="tok")
        self.assertIn("<details class='row-more'>", html)
        summary, body = html.split("</summary>", 1)
        self.assertIn("subtitle-status available", summary)
        for action in ("/jobs/delete", "/jobs/restart", "Visa undertexten"):
            self.assertIn(action, body)
            self.assertNotIn(action, summary)
        self.assertNotIn("/jobs/start", html)

    def test_a_missing_subtitle_keeps_its_main_action_visible_without_any_details(self):
        html = server.render_media_row(self.MISSING, token="tok")
        self.assertNotIn("<details", html)
        self.assertIn("subtitle-status missing", html)
        self.assertIn("/jobs/start", html)

    def test_why_a_run_failed_sits_behind_the_pill_and_the_start_button_stays_outside(self):
        job = {"id": 5, "status": "failed", "progress": 0, "message": "Översättningsmotorn svarade HTTP 500",
               "hardware": "Moln-GPU (vast)", "model": "Granskning med gemma4:31b"}
        html = server.render_media_row(self.MISSING, token="tok", job=job)
        summary, body = html.split("</summary>", 1)
        self.assertIn("subtitle-status failed", summary)
        details, after = body.split("</details>", 1)
        self.assertIn("HTTP 500", details)
        self.assertIn("gemma4:31b", details)
        self.assertIn("/jobs/start", after)
        self.assertNotIn("/jobs/start", details)

    def test_a_running_job_keeps_its_progress_and_cancel_button_in_view(self):
        job = {"id": 6, "status": "translating", "progress": 70, "message": "Översätter 40 repliker",
               "hardware": "Moln-GPU (vast)", "model": "Översättning med gemma3:27b"}
        html = server.render_media_row(self.MISSING, token="tok", job=job)
        self.assertNotIn("<details", html)
        for needle in ("Översätter 40 repliker", "/jobs/cancel", "gemma3:27b"):
            self.assertIn(needle, html)

    def test_the_pill_says_it_can_be_clicked_and_the_style_exists(self):
        self.assertIn("Klicka för att visa åtgärder och detaljer", server.render_media_row(self.AI, token="tok"))
        self.assertIn("details.row-more > summary", INDEX)
        self.assertIn("Click to show actions and details", server.render_media_row(self.AI, token="tok", lang="en"))


if __name__ == "__main__":
    unittest.main()
