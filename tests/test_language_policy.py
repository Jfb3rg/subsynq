import sys
import unittest

sys.path.insert(0, '/app')
from language_policy import probe_windows, resolve_filename_language


class LanguagePolicyTests(unittest.TestCase):
    def reading(self, start=0, whisper='de', qwen='German', speech=2):
        return {'start': start, 'end': start + 15, 'speech_seconds': speech,
                'whisper_language': whisper, 'qwen_language': qwen,
                'whisper_text': 'Substantial source speech',
                'qwen_text': 'Substantial source speech'}

    def test_two_distinct_agreements_confirm_hint(self):
        result = resolve_filename_language('de', [self.reading(), self.reading(30)])
        self.assertEqual(result['effective_language'], 'de')

    def test_conflicting_audio_never_forces_filename_or_replacement(self):
        result = resolve_filename_language('de', [self.reading(whisper='ko', qwen='Korean')])
        self.assertEqual(result['status'], 'conflict')
        self.assertIsNone(result['effective_language'])

    def test_code_switch_blocks_global_lock_even_with_two_agreements(self):
        result = resolve_filename_language('de', [self.reading(), self.reading(30),
            self.reading(60, whisper='en', qwen='English')])
        self.assertIsNone(result['effective_language'])

    def test_duplicate_or_overlapping_windows_do_not_count_twice(self):
        for start in (0, 5):
            self.assertIsNone(resolve_filename_language('de', [
                self.reading(), self.reading(start)])['effective_language'])

    def test_silence_empty_text_and_single_engine_cannot_confirm(self):
        for reading in (self.reading(speech=0), self.reading(qwen=None),
                        {**self.reading(), 'qwen_text': ''}):
            self.assertIsNone(resolve_filename_language('de', [
                reading, {**reading, 'start': 30, 'end': 45}])['effective_language'])

    def test_planning_is_bounded_and_non_overlapping(self):
        for duration in (4, 20, 40, 90, 3600):
            windows = probe_windows(duration)
            self.assertLessEqual(len(windows), 3)
            for w in windows:
                self.assertGreaterEqual(w['start'], 0)
                self.assertLessEqual(w['end'], duration)
                self.assertLessEqual(w['end'] - w['start'], 15.001)
            for left, right in zip(windows, windows[1:]):
                self.assertLessEqual(left['end'], right['start'])
