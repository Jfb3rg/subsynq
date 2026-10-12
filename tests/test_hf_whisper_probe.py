import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
from hf_whisper_probe import checked_token_ids, generation_options, legacy_generation_fields, main, weight_options


class HfWhisperProbeTests(unittest.TestCase):
    def test_raw_generation_token_ids_are_preserved(self):
        self.assertEqual(checked_token_ids([50258, 17622, 50257]), [50258, 17622, 50257])
        self.assertEqual(checked_token_ids([]), [])

    def test_invalid_token_ids_fail_before_reporting_text(self):
        for token_ids in [[float('nan')], [float('inf')], [-1], [True], [1.5], ['nan'], None]:
            with self.subTest(token_ids=token_ids), self.assertRaises(ValueError):
                checked_token_ids(token_ids)

    def test_weight_formats_are_explicit_without_unsafe_loading(self):
        self.assertEqual(weight_options('pytorch'), {'use_safetensors': False, 'weights_only': True})
        self.assertEqual(weight_options('safetensors'), {'use_safetensors': True, 'weights_only': True})
        with self.assertRaises(ValueError):
            weight_options('auto-remote')

    def test_legacy_fields_come_from_local_token_ids(self):
        fields = legacy_generation_fields({'<|hi|>': 1, '<|transcribe|>': 2,
                                          '<|translate|>': 3, '<|notimestamps|>': 4}, 'hi')
        self.assertEqual(fields['lang_to_id'], {'<|hi|>': 1})
        self.assertEqual(fields['task_to_id'], {'transcribe': 2, 'translate': 3})
        self.assertEqual(fields['no_timestamps_token_id'], 4)

    def test_legacy_fields_reject_missing_or_colliding_ids(self):
        for vocabulary in [{}, {'<|hi|>': 1, '<|transcribe|>': 1,
                               '<|translate|>': 3, '<|notimestamps|>': 4}]:
            with self.subTest(vocabulary=vocabulary), self.assertRaises(ValueError):
                legacy_generation_fields(vocabulary, 'hi')

    def test_explicit_text_only_reference_settings(self):
        options = generation_options('hi')
        self.assertEqual(options['language'], 'hi')
        self.assertEqual(options['task'], 'transcribe')
        self.assertFalse(options['return_timestamps'])
        self.assertFalse(options['do_sample'])
        self.assertEqual(options['num_beams'], 5)
        self.assertEqual(options['max_new_tokens'], 256)

    def test_output_guard_before_loading_model(self):
        with tempfile.NamedTemporaryFile() as existing:
            argv = ['probe', '--model', '/unused', '--samples', '/unused', '--language', 'hi',
                    '--output', existing.name]
            with patch.object(sys, 'argv', argv), self.assertRaises(FileExistsError):
                main()
