import sys
import unittest

sys.path.insert(0, '/app')
from speech_timing import reject_long_bridge, speech_span


class SpeechTimingTests(unittest.TestCase):
    def test_tight_span_uses_confident_words(self):
        words = [
            {'start': 159.5, 'end': 159.9, 'probability': .9},
            {'start': 160.0, 'end': 160.4, 'probability': .8},
        ]
        self.assertEqual(speech_span(words), (159.42, 160.48))

    def test_untrusted_words_do_not_create_a_cue(self):
        self.assertIsNone(speech_span([
            {'start': 150, 'end': 151, 'probability': .1},
        ]))

    def test_long_asr_bridge_is_rejected(self):
        self.assertTrue(reject_long_bridge({'start': 134.1, 'end': 175.0, 'text': 'hallucination'}))
        self.assertFalse(reject_long_bridge({'start': 134.1, 'end': 136.0, 'text': 'speech'}))


if __name__ == '__main__':
    unittest.main()
