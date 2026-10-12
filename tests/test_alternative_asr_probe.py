import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/ai')
from alternative_asr_probe import main, nemo_text, window


class AlternativeAsrProbeTests(unittest.TestCase):
    def test_bounded_window(self):
        self.assertEqual(window(0, 25), 25)
        self.assertEqual(window(12.8, 18.8), 6)
        self.assertEqual(window(1, 61), 60)

    def test_invalid_window(self):
        for start, end in [(-1, 5), (0, 61), (5, 5), (6, 3)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                window(start, end)

    def test_nonfinite_window(self):
        for start, end in [(float('nan'), 5), (0, float('inf')), (float('-inf'), 5)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                window(start, end)

    def test_existing_output_is_rejected_before_loading_models(self):
        with tempfile.NamedTemporaryFile(suffix='.json') as existing:
            argv = ['probe', 'unused.mp4', '--engine', 'mms', '--model', 'unused',
                    '--output', existing.name]
            with patch.object(sys, 'argv', argv), self.assertRaises(FileExistsError):
                main()

    def test_nemo_string_hypothesis_and_tuple_outputs(self):
        self.assertEqual(nemo_text(['speech']), 'speech')
        self.assertEqual(nemo_text([SimpleNamespace(text='speech')]), 'speech')
        self.assertEqual(nemo_text((['speech'], None)), 'speech')

    def test_nemo_silence_remains_empty(self):
        self.assertEqual(nemo_text(['']), '')
        self.assertEqual(nemo_text([SimpleNamespace(text='')]), '')

    def test_nemo_missing_multiple_or_invalid_text_is_rejected(self):
        for result in [[], (), ['a', 'b'], 'not a list', [None], [SimpleNamespace(text=None)]]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                nemo_text(result)
