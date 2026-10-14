import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server

PAGE = Path(server.__file__).with_name("index.html").read_text(encoding="utf-8")


class ReloadGuardTests(unittest.TestCase):
    """Real log, dev 2026-10-14: after a scan the page reloaded itself about every 3.7 s for 80 s. Every
    automatic reload now passes autoReload, which pauses a tab that reloads too often (checked in node)."""

    def test_only_the_guard_and_its_button_call_location_reload(self):
        self.assertEqual(len(re.findall(r"location\.reload\(\)", PAGE)), 2,
                         "a new polling script must call autoReload(why), never location.reload()")
        guard = PAGE[PAGE.index("// <reload-guard>"):PAGE.index("// </reload-guard>")]
        self.assertEqual(len(re.findall(r"location\.reload\(\)", guard)), 2)

    def test_every_automatic_reload_names_its_reason(self):
        reasons = re.findall(r"autoReload\('([a-z]+)'", PAGE)
        self.assertEqual(sorted(reasons), ["hardware", "models", "queue", "scan", "status"])
        for reason in set(reasons):
            self.assertIn(f"{reason}:'", PAGE[PAGE.index("const reloadReasons"):PAGE.index("function reloadState")],
                          f"{reason} needs a Swedish name in the pause notice")

    def test_the_limits_are_the_ones_the_arithmetic_in_the_comment_relies_on(self):
        self.assertIn("reloadWindowMs=25000,reloadLimit=6,reloadPauseMs=300000", PAGE)

    def test_the_pause_notice_has_a_button_that_clears_it(self):
        self.assertIn("Ladda om nu", PAGE)
        self.assertIn("sessionStorage.removeItem(reloadPauseKey)", PAGE)

    def test_a_tap_on_a_link_or_a_submit_blocks_automatic_reloads(self):
        """Real log, dev 2026-10-14: the user tapped "Kö" while a job ran; the page's own reload replaced the tap."""
        guard = PAGE[PAGE.index("// <reload-guard>"):PAGE.index("// </reload-guard>")]
        self.assertIn("document.addEventListener('pointerdown'", guard)
        self.assertIn("noteNavigation(3000)", guard)
        self.assertIn("noteNavigation(15000)", guard)
        self.assertIn("document.addEventListener('submit'", guard)
        body = guard[guard.index("function autoReload"):]
        self.assertLess(body.index("now<navigationUntil"), body.index("showReloadPaused(why);return false"),
                        "a navigation in progress is checked before anything else")

    def test_every_reason_has_its_own_minimum_gap_and_the_job_list_waits_15_seconds(self):
        gaps = PAGE[PAGE.index("const reloadMinGapMs="):].split(";")[0]
        self.assertIn("queue:15000", gaps)
        for reason in ("queue", "status", "hardware", "models", "scan"):
            self.assertRegex(gaps, rf"{reason}:\d+")
        self.assertIn("reloadMinGapMs[why]||5000", PAGE)


if __name__ == "__main__":
    unittest.main()
