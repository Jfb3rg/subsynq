import sys
import unittest

import numpy as np

sys.path.insert(0, '/ai')
from reference_audio_probe import match_window, parse_window


class ReferenceAudioProbeTests(unittest.TestCase):
    def test_window_validation(self):
        self.assertEqual(parse_window('4:7'), (4, 7))
        for value in ['4', '-1:2', '2:2', '1:12', 'nan:2', '0:inf', '0:0.1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_window(value)

    def test_finds_offset_and_gain_without_filename_rules(self):
        rng = np.random.default_rng(41)
        reference = rng.normal(0, 0.2, 10 * 16000)
        target = np.zeros(6 * 16000)
        target[2 * 16000:4 * 16000] = reference[5 * 16000:7 * 16000] * 0.35
        result = match_window(reference, target, 2, 4)
        self.assertEqual(result['reference_start'], 5)
        self.assertEqual(result['reference_minus_target_seconds'], 3)
        self.assertGreater(result['correlation'], 0.999)

    def test_detects_phase_inversion(self):
        rng = np.random.default_rng(7)
        reference = rng.normal(0, 0.2, 3 * 16000)
        target = -reference.copy()
        result = match_window(reference, target, 0, 3)
        self.assertEqual(result['reference_start'], 0)
        self.assertLess(result['correlation'], -0.999)

    def test_rejects_silence_nonfinite_and_out_of_bounds(self):
        reference = np.ones(3 * 16000)
        target = np.zeros(2 * 16000)
        for changed in [target, np.full_like(target, np.nan)]:
            with self.assertRaises(ValueError):
                match_window(reference, changed, 0, 1)
        with self.assertRaises(ValueError):
            match_window(reference, np.ones_like(target), 1, 3)
        with self.assertRaises(ValueError):
            match_window(reference, np.arange(2 * 16000, dtype=np.float64), 0, 1)
