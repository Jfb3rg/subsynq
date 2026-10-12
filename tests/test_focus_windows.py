import sys
import unittest

sys.path.insert(0, '/app')
from focus_windows import plan_short_focus_windows


class FocusWindowTests(unittest.TestCase):
    def test_targets_known_gap_and_french_scene_entrance(self):
        rows = [
            {'start': 241.98, 'end': 243.34, 'text': '19 months at sea.'},
            {'start': 244.76, 'end': 246.22, 'text': 'Hoping to stay ahead.'},
            {'start': 250.255, 'end': 251.115, 'text': 'Nowhere safe.'},
            {'start': 828.13, 'end': 828.75, 'text': "What's going on?"},
        ]
        windows = plan_short_focus_windows(rows, 900)
        self.assertTrue(any(row['start'] <= 248 <= row['end']
                            and row['reason'] == 'lucka_fore_kort_replik'
                            for row in windows))
        self.assertTrue(any(row['start'] <= 827 <= row['end']
                            and row['reason'] == 'borjan_efter_lang_paus'
                            for row in windows))

    def test_budget_and_no_overshoot(self):
        rows = [{'start': i * 15.0, 'end': i * 15.0 + .5, 'text': 'You'}
                for i in range(100)]
        windows = plan_short_focus_windows(rows, 1500, maximum_per_bucket=3,
                                           maximum_total=4)
        self.assertLessEqual(len(windows), 4)
        self.assertTrue(all(0 <= row['start'] < row['end'] <= 1500 for row in windows))
