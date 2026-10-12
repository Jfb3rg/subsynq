import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import filter_unconfirmed_stretched_words


class StretchedWordsTests(unittest.TestCase):
    def cue(self, duration=5.5):
        return {'start': 10., 'end': 16., 'text': 'Remember the ancient city.',
                'confidence': .95, 'words': [
                    {'start': 10., 'end': 10.4, 'text': 'Remember'},
                    {'start': 10.4, 'end': 10.5, 'text': 'the'},
                    {'start': 10.5, 'end': 10.5 + duration, 'text': 'ancient'}]}

    def test_confidence_cannot_certify_a_word_stretched_over_silence(self):
        candidate = self.cue()
        self.assertEqual(filter_unconfirmed_stretched_words([candidate]), [])
        self.assertEqual(candidate['words'][2]['end'], 16.)

    def test_ordinary_quiet_speech_is_preserved_without_another_reader(self):
        candidate = self.cue(.7)
        self.assertEqual(filter_unconfirmed_stretched_words([candidate]), [candidate])

    def test_independent_matching_phrase_can_confirm_a_long_word(self):
        candidate = self.cue()
        confirmation = self.cue(.8)
        self.assertEqual(filter_unconfirmed_stretched_words(
            [candidate], [confirmation]), [candidate])

    def test_same_stretch_wrong_time_wrong_text_and_low_confidence_do_not_confirm(self):
        candidate = self.cue()
        for confirmation in [self.cue(), {**self.cue(.8), 'start': 1.},
                             {**self.cue(.8), 'text': 'A completely different phrase.'},
                             {**self.cue(.8), 'confidence': .4}]:
            with self.subTest(confirmation=confirmation):
                self.assertEqual(filter_unconfirmed_stretched_words(
                    [candidate], [confirmation]), [])
