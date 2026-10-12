import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from i18n import LANGUAGES, STRINGS, t


class TranslationCatalogTests(unittest.TestCase):
    def test_every_string_has_both_languages(self):
        for key, entry in STRINGS.items():
            for lang in LANGUAGES:
                self.assertIn(lang, entry, f"{key} is missing a {lang} translation")
                self.assertTrue(entry[lang].strip(), f"{key}'s {lang} translation is empty")

    def test_t_returns_requested_language(self):
        self.assertEqual(t("nav_overview", "sv"), "Översikt")
        self.assertEqual(t("nav_overview", "en"), "Overview")

    def test_t_falls_back_to_swedish_for_unknown_language(self):
        self.assertEqual(t("nav_overview", "fr"), "Översikt")

    def test_t_returns_the_key_itself_for_an_unknown_key(self):
        self.assertEqual(t("no_such_key", "en"), "no_such_key")


if __name__ == "__main__":
    unittest.main()
