import sys
import unittest

sys.path.insert(0, '/ai')
from source_audio_probe import source_window


class SourceAudioProbeTests(unittest.TestCase):
    def test_saved_rows_get_context_without_cutting_start_below_zero(self):
        self.assertEqual(source_window([{'start': .5, 'end': 4}]), (0, 6))
        self.assertEqual(source_window([{'start': 8, 'end': 20}]), (6, 22))

    def test_explicit_window_does_not_depend_on_media_name(self):
        self.assertEqual(source_window([{'start': 8, 'end': 20}], 0, 25), (0, 25))

    def test_unbounded_invalid_and_nonfinite_windows_are_rejected(self):
        for start, end in [(-1, 3), (1, 1), (4, 3), (0, 61), (float('nan'), 3), (0, float('inf'))]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                source_window([{'start': 8, 'end': 20}], start, end)

    def test_empty_source_report_is_rejected(self):
        with self.assertRaises(ValueError):
            source_window([])


if __name__ == '__main__':
    unittest.main()
