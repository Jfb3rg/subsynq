import sys
import unittest

sys.path.insert(0, '/app')
from asr_consensus import normalized
from translation_qa import (dominant_non_latin_script, filename_language_hint,
                            forced_whisper_language, known_phrase_fallback,
                            looks_untranslated, restore_id_order,
                            whisper_language_label)


class TranslationQaTests(unittest.TestCase):
    def test_reads_explicit_language_marker_from_filename(self):
        self.assertEqual(filename_language_hint('HOPE Official Trailer (2026) tyska.mp4'), 'de')
        self.assertEqual(filename_language_hint('Film Deutsch 1080p.mkv'), 'de')
        self.assertEqual(whisper_language_label('de'), 'tyska')

    def test_ambiguous_or_incidental_filename_stays_automatic(self):
        self.assertIsNone(filename_language_hint('Hope trailer.mp4'))
        self.assertIsNone(filename_language_hint('French German comparison.mp4'))
        self.assertIsNone(filename_language_hint('germanium documentary.mp4'))

    def test_recognizes_coherent_tamil_without_judging_its_meaning(self):
        rows = [{'source': text} for text in (
            'இந்தக்கு நீ உயிரோட இருக்கேன் என்று சந்தோஷிம்படு.',
            'இந்த சூதும் போதையும் ஒரு காயனுடைய இரண்டு பக்கம் மாதிரி',
            'கோடி கோடியா சம்பாரிக்கலாம்',
        )]
        self.assertEqual(dominant_non_latin_script(
            rows, minimum_share=.5, minimum_letters=20), 'Tamil')
        self.assertEqual(forced_whisper_language(rows), 'ta')

    def test_repeated_non_latin_hallucination_is_not_trusted(self):
        rows = [{'source': 'ஒரே மாயையான வரி'} for _ in range(6)]
        self.assertIsNone(dominant_non_latin_script(
            rows, minimum_share=.5, minimum_letters=20))
        self.assertIsNone(forced_whisper_language(rows))

    def test_short_trailer_snippet_passes_at_default_threshold(self):
        # A concept trailer with only a few seconds of confident speech;
        # real, coherent Devanagari that a Swedish/English reviewer cannot judge.
        rows = [{'source': text} for text in (
            'है, उसमें ही जाके दूँगा, बागी मेरी कहानी है,',
            'सारी दुनिया को जो सुना,',
        )]
        self.assertEqual(dominant_non_latin_script(rows), 'Devanagari')

    def test_tiny_non_latin_fragment_still_needs_the_floor(self):
        rows = [{'source': 'है उसमें'}]
        self.assertIsNone(dominant_non_latin_script(rows))

    def test_detects_copied_french_and_english_but_not_swedish(self):
        self.assertTrue(looks_untranslated(
            'Il y a un mec qui est venu.', 'Il y a un mec qui est venu.'))
        self.assertTrue(looks_untranslated(
            'I saw him fight the warriors.', 'I saw him fight the warriors.'))
        self.assertFalse(looks_untranslated('Det här är bra.', 'Det här är bra.'))
        self.assertFalse(looks_untranslated(
            'Et après ?', 'Och sedan?'))

    def test_replaces_known_untranslated_phrase(self):
        self.assertEqual(
            known_phrase_fallback('Un mois, peut-être.', 'Un mois, peut-être.', normalized),
            'En månad, kanske.',
        )
        self.assertEqual(
            known_phrase_fallback('Merci.', 'Merci.', normalized),
            'Tack.',
        )

    def test_does_not_overwrite_an_existing_translation(self):
        self.assertEqual(
            known_phrase_fallback('Allez, Zizou.', 'Kom igen, Zizou.', normalized),
            'Kom igen, Zizou.',
        )

    def test_repairs_reviewed_spanish_sailing_phrase_only(self):
        self.assertEqual(
            known_phrase_fallback('Circled Spain, nowhere safe.',
                                  'Vi kretsade runt Spanien, ingenstans är säkert.', normalized),
            'Vi rundade Spanien. Ingenstans var det säkert.',
        )
        self.assertEqual(
            known_phrase_fallback('Circled Spain, nowhere safe.',
                                  'Vi seglade runt Spanien. Ingenstans var säkert.', normalized),
            'Vi seglade runt Spanien. Ingenstans var säkert.',
        )

    def test_uses_verified_translation_for_crazy_fame(self):
        self.assertEqual(
            known_phrase_fallback('This crazy fame...', 'Den här galna kändis...', normalized),
            'Den här vansinniga berömmelsen...',
        )

    def test_restores_model_rows_by_validated_id(self):
        rows = restore_id_order([10, 11, 12], [
            {'id': 12, 'sv': 'Tre'}, {'id': 10, 'sv': 'Ett'}, {'id': 11, 'sv': 'Två'},
        ])
        self.assertEqual([row['sv'] for row in rows], ['Ett', 'Två', 'Tre'])

    def test_rejects_missing_duplicate_or_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, 'rad-ID'):
            restore_id_order([10, 11], [{'id': 10, 'sv': 'Ett'}, {'id': 10, 'sv': 'Igen'}])
        with self.assertRaisesRegex(ValueError, 'rad-ID'):
            restore_id_order([10, 11], [{'id': 10, 'sv': 'Ett'}, {'id': 99, 'sv': 'Fel'}])

    def test_rejects_empty_translation_for_targeted_retry(self):
        with self.assertRaisesRegex(ValueError, 'tom undertextrad'):
            restore_id_order([10], [{'id': 10, 'sv': '   '}])
