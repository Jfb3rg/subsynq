import sys
import unittest

sys.path.insert(0, '/app')
from qwen_quality import alignment_quality, reliable_alignment_islands


class QwenQualityTests(unittest.TestCase):
    def test_keeps_short_reliable_island_not_bad_later_sentence(self):
        words = [
            {'text': 'Merci', 'start': 6.0, 'end': 6.4},
            {'text': 'Can', 'start': 11.04, 'end': 11.2},
            {'text': 'you', 'start': 11.2, 'end': 11.2},
            {'text': 'get', 'start': 11.2, 'end': 11.4},
        ]
        self.assertEqual(reliable_alignment_islands(words), words[:1])
        self.assertEqual(reliable_alignment_islands(words[1:]), [])

    def test_rejects_zero_length_word_and_out_of_clip_time(self):
        self.assertEqual(alignment_quality([
            {'text': 'Bonjour', 'start': .2, 'end': .4},
            {'text': 'vous', 'start': .5, 'end': .5},
        ])['status'], 'unreliable')
        self.assertEqual(alignment_quality([
            {'text': 'Bonjour', 'start': .2, 'end': 2.0},
        ], clip_seconds=1.0)['status'], 'unreliable')

    def test_accepts_tight_speech_island(self):
        self.assertEqual(alignment_quality([
            {'text': 'Nineteen', 'start': .0, 'end': .32},
            {'text': 'months', 'start': .4, 'end': .64},
        ], clip_seconds=1.0)['status'], 'usable')
