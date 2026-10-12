from pathlib import Path
from fractions import Fraction
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
from separation_probe import main, segment_seconds, validate_checkpoint, validate_sources


class SeparationProbeTests(unittest.TestCase):
    def test_fraction_segment_is_json_compatible_and_finite(self):
        self.assertEqual(segment_seconds(Fraction(39, 5)), 7.8)
        for value in [0, -1, float('nan'), float('inf')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                segment_seconds(value)

    def test_vocals_index(self):
        self.assertEqual(validate_sources(['drums', 'bass', 'other', 'vocals']), 3)
        for sources in [[], ['other'], ['vocals', 'vocals']]:
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                validate_sources(sources)

    def test_wrong_checkpoint_hash_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / '955717e8-8726e21a.th').write_bytes(b'not a model')
            with self.assertRaises(ValueError):
                validate_checkpoint(repo)

    def test_existing_output_rejected_before_loading(self):
        with tempfile.NamedTemporaryFile() as existing:
            argv = ['probe', '--media', '/unused', '--repo', '/unused', '--samples', '/unused',
                    '--output', existing.name]
            with patch.object(sys, 'argv', argv), self.assertRaises(FileExistsError):
                main()

    def test_bad_window_rejected_before_loading(self):
        argv = ['probe', '--media', '/unused', '--repo', '/unused', '--samples', '/unused',
                '--output', '/unused/report', '--end', '61']
        with patch.object(sys, 'argv', argv), self.assertRaises(ValueError):
            main()
