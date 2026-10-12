"""Generic matched-window ASR sample preparation."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave


ai_path = Path(__file__).resolve().parents[1] / 'ai'
sys.path.insert(0, str(ai_path if (ai_path / 'aligned_asr_samples.py').is_file() else Path('/ai')))
from aligned_asr_samples import prepare


def write_wave(path, frames):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(frames)


class AlignedAsrSamplesTests(unittest.TestCase):
    def test_slices_three_equal_windows_and_verifies_hashes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source'
            source.mkdir()
            content = bytes(range(256)) * 250
            for name in ('mono', 'vocals'):
                write_wave(source / (name + '.wav'), content)
            reference = root / 'reference.wav'
            write_wave(reference, bytes(reversed(range(256))) * 250)
            variants = [{'name': name, 'sha256': hashlib.sha256((source / (name + '.wav')).read_bytes()).hexdigest()}
                        for name in ('mono', 'vocals')]
            (source / 'manifest.json').write_text(json.dumps({'start': 0, 'end': 2, 'variants': variants}))
            output = root / 'aligned'
            result = prepare(source, reference, 0.25, 1.25, 0.5, output)
            self.assertEqual([row['name'] for row in result['variants']], ['mono', 'vocals', 'reference'])
            self.assertEqual([row['frames'] for row in result['variants']], [16000] * 3)
            self.assertEqual((output / 'mono.wav').read_bytes(), (output / 'vocals.wav').read_bytes())
            with self.assertRaises(FileExistsError):
                prepare(source, reference, 0.25, 1.25, 0.5, output)

    def test_rejects_out_of_bounds_and_modified_source(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source'
            source.mkdir()
            write_wave(source / 'mono.wav', b'\0\0' * 16000)
            reference = root / 'reference.wav'
            write_wave(reference, b'\0\0' * 16000)
            (source / 'manifest.json').write_text(json.dumps({'start': 0, 'end': 1,
                'variants': [{'name': 'mono', 'sha256': hashlib.sha256((source / 'mono.wav').read_bytes()).hexdigest()}]}))
            with self.assertRaises(ValueError):
                prepare(source, reference, 0, 1, 0.1, root / 'too-long')
            with self.assertRaises(ValueError):
                prepare(source, reference, 0, 1.1, 0, root / 'outside')
            write_wave(source / 'mono.wav', b'\1\0' * 16000)
            with self.assertRaises(ValueError):
                prepare(source, reference, 0, 0.5, 0, root / 'changed')


if __name__ == '__main__':
    unittest.main()
