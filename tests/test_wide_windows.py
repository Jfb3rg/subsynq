import sys
import unittest

sys.path.insert(0, '/ai')
import worker


class WideWindowsTests(unittest.TestCase):
    def test_places_are_widened_by_twelve_seconds_and_clipped_to_the_file(self):
        self.assertEqual(worker.wide_windows([{'start': 5.0, 'end': 8.0}], 100.0), [{'start': 0.0, 'end': 20.0}])
        self.assertEqual(worker.wide_windows([{'start': 95.0, 'end': 99.0}], 100.0), [{'start': 83.0, 'end': 100.0}])

    def test_overlapping_and_nearly_touching_places_become_one_window(self):
        windows = [{'start': 56.0, 'end': 59.0}, {'start': 24.0, 'end': 29.0}, {'start': 29.0, 'end': 33.0},
                   {'start': 13.0, 'end': 17.0}]
        # 1-29, 12-41, 17-45 and 44-71 run into each other: one stretch of 70 s, cut at 60 s with a 4 s overlap.
        self.assertEqual(worker.wide_windows(windows, 119.5),
                         [{'start': 1.0, 'end': 61.0}, {'start': 57.0, 'end': 71.0}])

    def test_places_far_apart_stay_separate(self):
        self.assertEqual(worker.wide_windows([{'start': 20.0, 'end': 22.0}, {'start': 200.0, 'end': 203.0}], 300.0),
                         [{'start': 8.0, 'end': 34.0}, {'start': 188.0, 'end': 215.0}])

    def test_no_window_is_longer_than_the_limit_and_the_pieces_overlap(self):
        result = worker.wide_windows([{'start': 10.0, 'end': 20.0}, {'start': 60.0, 'end': 70.0},
                                      {'start': 110.0, 'end': 120.0}], 300.0, padding=30.0, maximum_seconds=60.0)
        self.assertTrue(all(window['end'] - window['start'] <= 60.0 for window in result))
        self.assertEqual(result[0]['start'], 0.0)
        self.assertGreaterEqual(result[-1]['end'], 150.0)
        for earlier, later in zip(result, result[1:]):
            self.assertLessEqual(later['start'], earlier['end'], 'no stretch is left unread between the pieces')

    def test_nothing_in_nothing_out(self):
        self.assertEqual(worker.wide_windows([], 100.0), [])


if __name__ == '__main__':
    unittest.main()
