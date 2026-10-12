import sys
import unittest

sys.path.insert(0, '/ai')
from whisper_tokenizer_conversion import missing_timestamp_tokens


class WhisperTokenizerConversionTests(unittest.TestCase):
    def test_standard_timestamp_suffix_only(self):
        stamps = [f'<|{i / 50:.2f}|>' for i in range(1501)]
        self.assertEqual(missing_timestamp_tokens({'a': 0, 'b': 1}, ['a', 'b'] + stamps), stamps)

    def test_complete_vocabulary_unchanged(self):
        self.assertEqual(missing_timestamp_tokens({'a': 0, 'b': 1}, ['a', 'b']), [])

    def test_existing_ids_and_unknown_missing_tokens_rejected(self):
        for source, converted in [({'a': 1}, ['a']), ({'other': 0}, ['a']),
                                  ({'a': 0}, ['a', 'new_word']), ({'a': 0}, ['a', 'a'])]:
            with self.subTest(source=source, converted=converted), self.assertRaises(ValueError):
                missing_timestamp_tokens(source, converted)

    def test_partial_or_reordered_timestamps_rejected(self):
        stamps = [f'<|{i / 50:.2f}|>' for i in range(1501)]
        for suffix in [stamps[:-1], list(reversed(stamps))]:
            with self.subTest(suffix_start=suffix[:1]), self.assertRaises(ValueError):
                missing_timestamp_tokens({'a': 0}, ['a'] + suffix)
