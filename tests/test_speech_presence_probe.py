"""Tests for bounded, non-overlapping speech-coverage accounting."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ai'))
from speech_presence_probe import bounds, coverage


class SpeechPresenceProbeTests(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(bounds(5, 20), 15)
        for start, end in ((-1, 1), (0, 0), (0, 61), (float('nan'), 1)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                bounds(start, end)

    def test_coverage_merges_and_clips(self):
        intervals = [{'start': -1, 'end': 1}, {'start': .5, 'end': 2},
                     {'start': 3, 'end': 7}, {'start': 6, 'end': 8}]
        self.assertEqual(coverage(intervals, 5), 4.0)
        self.assertEqual(coverage([], 5), 0.0)


if __name__ == '__main__':
    unittest.main()
