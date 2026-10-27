import sys
import unittest

sys.path.insert(0, '/ai')
from source_audio_probe import source_audio_map, source_window


class SourceAudioProbeTests(unittest.TestCase):
    def test_saved_ffprobe_stream_wins_over_first_audio_stream(self):
        self.assertEqual(source_audio_map({'audio_track': {'index': 1}}), '0:1')
        self.assertEqual(source_audio_map({'audio_track': {'index': 0}}), '0:0')
        self.assertEqual(source_audio_map({'audio_track': {'index': 1}}, 2), '0:2')

    def test_unknown_or_invalid_stream_is_not_silently_guessed(self):
        for report in ({}, {'audio_track': None}, {'audio_track': {'index': -1}},
                       {'audio_track': {'index': True}}, {'audio_track': {'index': '1'}}):
            with self.subTest(report=report), self.assertRaises(ValueError):
                source_audio_map(report)

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
