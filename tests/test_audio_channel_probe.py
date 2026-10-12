import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
import wave
import struct
from unittest.mock import patch

sys.path.insert(0, '/ai')
from audio_channel_probe import channel_stats, checked_samples, main, prepare_silence, wav_stats, window, whisper_options


class AudioChannelProbeTests(unittest.TestCase):
    def test_default_whisper_mode_preserves_existing_settings(self):
        options = whisper_options('hi')
        self.assertFalse(options['without_timestamps'])
        self.assertTrue(options['word_timestamps'])
        self.assertEqual(options['hallucination_silence_threshold'], 2)
        self.assertEqual(options['beam_size'], 5)
        self.assertFalse(options['vad_filter'])
        self.assertFalse(options['condition_on_previous_text'])

    def test_text_only_changes_decoding_not_language_or_beam(self):
        options = whisper_options('hi', True)
        self.assertTrue(options['without_timestamps'])
        self.assertFalse(options['word_timestamps'])
        self.assertIsNone(options['hallucination_silence_threshold'])
        self.assertEqual(options['language'], 'hi')
        self.assertEqual(options['beam_size'], 5)
        self.assertEqual(options['temperature'], 0)
        self.assertFalse(options['vad_filter'])

    def test_generated_silence_is_bounded_valid_and_not_media(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(start=0, end=4, samples=Path(directory) / 'control')
            manifest = prepare_silence(args)
            self.assertTrue(manifest['silence_control'])
            self.assertEqual(checked_samples(args.samples), manifest)
            self.assertEqual(wav_stats(args.samples / 'mono.wav')['rms'], 0)
            self.assertEqual(wav_stats(args.samples / 'mono.wav')['frames'], 64000)

    def test_silence_rejects_existing_directory_and_invalid_window(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                prepare_silence(SimpleNamespace(start=0, end=4, samples=Path(directory)))
            for start, end in [(1, 4), (0, 61), (0, float('inf'))]:
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    prepare_silence(SimpleNamespace(start=start, end=end, samples=Path(directory) / 'control'))

    def test_window_boundaries(self):
        self.assertEqual(window(12.8, 18.8), 6)
        self.assertEqual(window(1, 61), 60)
        for start, end in [(-1, 2), (2, 2), (0, 61), (float('nan'), 3), (0, float('inf'))]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                window(start, end)

    def test_identical_channels(self):
        stats = channel_stats([[0.25, 0.25], [-0.25, -0.25]])
        self.assertTrue(stats['channels_identical'])
        self.assertEqual(stats['side_rms'], 0)
        self.assertAlmostEqual(stats['correlation'], 1)
        self.assertEqual(stats['left_rms'], 0.25)

    def test_antiphase_channels(self):
        stats = channel_stats([[0.25, -0.25], [-0.25, 0.25]])
        self.assertEqual(stats['mid_rms'], 0)
        self.assertAlmostEqual(stats['correlation'], -1)
        self.assertFalse(stats['channels_identical'])

    def test_silence_is_json_safe(self):
        stats = channel_stats([[0, 0], [0, 0]])
        self.assertIsNone(stats['correlation'])
        json.dumps(stats, allow_nan=False)

    def test_invalid_samples(self):
        for samples in [[], [[1]], [[float('nan'), 0]], [[float('inf'), 0]]]:
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                channel_stats(samples)

    def test_hash_guard_and_unknown_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / 'mono.wav'
            with wave.open(str(wav), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b'\x00\x00' * 96000)
            manifest = {'start': 0, 'end': 6,
                        'variants': [{'name': 'mono', 'sha256': hashlib.sha256(wav.read_bytes()).hexdigest()}]}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(checked_samples(root), manifest)
            wav.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                checked_samples(root)
            manifest['variants'][0]['name'] = '../escape'
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                checked_samples(root)

    def test_shorter_audio_window_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / 'mono.wav'
            with wave.open(str(wav), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b'\x00\x00' * 16000)
            (root / 'manifest.json').write_text(json.dumps({'start': 0, 'end': 6,
                'variants': [{'name': 'mono', 'sha256': hashlib.sha256(wav.read_bytes()).hexdigest()}]}))
            with self.assertRaises(ValueError):
                checked_samples(root)

    def test_existing_output_rejected_before_model_or_audio(self):
        with tempfile.NamedTemporaryFile() as existing:
            with patch.object(sys, 'argv', ['probe', 'prepare', '--samples', '/unused',
                                         '--output', existing.name]), self.assertRaises(FileExistsError):
                main()

    def test_pcm_limit_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mono.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(struct.pack('<4h', -32768, 32767, 0, 0))
            stats = wav_stats(path)
            self.assertEqual(stats['frames'], 4)
            self.assertEqual(stats['peak'], 1)
            self.assertEqual(stats['pcm_limit_fraction'], 0.5)

    def test_manifest_nonfinite_window_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'start': 0, 'end': float('inf'), 'variants': []}))
            with self.assertRaises(ValueError):
                checked_samples(root)

    def test_empty_or_duplicate_variants_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for variants in [[], [{'name': 'mono'}, {'name': 'mono'}]]:
                (root / 'manifest.json').write_text(json.dumps({'start': 0, 'end': 6, 'variants': variants}))
                with self.subTest(variants=variants), self.assertRaises(ValueError):
                    checked_samples(root)
