"""Words two rows share at their edge go from one of them only where the readings heard them once."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from doubled_edges import drop_doubled_edge_words


def row(start, end, text, **extra):
    return dict({'start': start, 'end': end, 'text': text, 'confidence': .8}, **extra)


ROWS = [row(10.0, 12.0, 'I can sell the boat to you for the'), row(12.0, 14.5, 'for the small sum of ten crowns.')]
ONCE = [row(10.0, 14.5, 'I can sell the boat to you for the small sum of ten crowns.')]


class DoubledEdgeTests(unittest.TestCase):
    def test_words_two_readings_hold_once_leave_the_row_they_do_not_belong_to(self):
        rows, changes = drop_doubled_edge_words(ROWS, {'precision': ONCE, 'qwen': ONCE})
        self.assertEqual([item['text'] for item in rows], ['I can sell the boat to you', 'for the small sum of ten crowns.'])
        self.assertEqual((changes[0]['words'], changes[0]['removed_from'], changes[0]['readings']), ('for the', 'first', 2))
        self.assertEqual([(item['start'], item['end']) for item in rows], [(10.0, 12.0), (12.0, 14.5)])
        self.assertEqual(ROWS[0]['text'], 'I can sell the boat to you for the')

    def test_one_reading_is_not_enough(self):
        rows, changes = drop_doubled_edge_words(ROWS, {'precision': ONCE, 'qwen': [row(10.0, 14.5, 'I can sell it.')]})
        self.assertEqual((rows, changes), (ROWS, []))

    def test_a_reading_that_holds_the_words_twice_keeps_them(self):
        twice = [row(10.0, 14.5, 'I can sell the boat to you for the, for the small sum of ten crowns.')]
        rows, changes = drop_doubled_edge_words(ROWS, {'precision': ONCE, 'qwen': ONCE, 'v2': twice})
        self.assertEqual((rows, changes), (ROWS, []))

    def test_a_reading_that_writes_the_words_twice_over_its_own_rows_keeps_them(self):
        twice = [row(10.0, 12.0, 'I can sell the boat to you for the'), row(12.1, 14.5, 'For the small sum of ten crowns.')]
        self.assertEqual(drop_doubled_edge_words(ROWS, {'precision': ONCE, 'qwen': ONCE, 'v2': twice})[1], [])

    def test_two_windows_of_one_reading_that_overlap_are_not_one_sentence(self):
        # Both windows were read with a margin and heard the words at their shared edge: that is not a repetition.
        windows = [row(9.7, 12.3, 'can sell the boat to you for the'), row(11.7, 14.8, 'for the small sum of ten crowns.')]
        rows, changes = drop_doubled_edge_words(ROWS, {'precision': ONCE, 'v2': ONCE, 'qwen': windows})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['readings'], 2)

    def test_a_row_that_is_the_repeated_words_alone_stays(self):
        said_again = [row(10.0, 11.0, 'Where are they?'), row(11.1, 12.0, 'Where are they?')]
        once = [row(10.0, 12.0, 'Where are they?')]
        self.assertEqual(drop_doubled_edge_words(said_again, {'precision': once, 'qwen': once})[1], [])
        lone = [row(10.0, 10.4, 'Well'), row(10.4, 12.0, 'Well, the harvest comes late this year.')]
        once = [row(10.0, 12.0, 'Well, the harvest comes late this year.')]
        self.assertEqual(drop_doubled_edge_words(lone, {'precision': once, 'qwen': once})[1], [])

    def test_the_words_stay_where_the_readings_end_the_sentence(self):
        rows = [row(20.0, 22.0, 'Did you count the jars?'),
                row(22.0, 25.0, 'count the jars put the jars in the cellar'), row(25.0, 27.0, 'Lock the cellar door.')]
        once = [row(20.0, 22.0, 'Did you count the jars?'), row(22.1, 25.0, 'Put the jars in the cellar.'),
                row(25.1, 27.0, 'Lock the cellar door.')]
        result, changes = drop_doubled_edge_words(rows, {'precision': once, 'qwen': once})
        self.assertEqual([item['text'] for item in result],
                         ['Did you count the jars?', 'Put the jars in the cellar', 'Lock the cellar door.'])
        self.assertEqual(changes[0]['removed_from'], 'second')

    def test_the_first_row_takes_the_mark_the_readings_put_at_its_new_end(self):
        rows = [row(30.0, 33.0, 'We met down by the river Listen Anna.'), row(33.0, 35.0, 'Listen, Anna, you owe me a horse.')]
        once = [row(30.0, 35.0, 'We met down by the river. Listen, Anna, you owe me a horse.')]
        result, changes = drop_doubled_edge_words(rows, {'precision': once, 'qwen': once})
        self.assertEqual([item['text'] for item in result], ['We met down by the river.', 'Listen, Anna, you owe me a horse.'])
        closed = [row(40.0, 42.0, 'now I will take you'), row(42.0, 44.0, 'you the name is Berg')]
        once = [row(40.0, 44.0, 'Now I will take you. The name is Berg.')]
        result, changes = drop_doubled_edge_words(closed, {'precision': once, 'qwen': once})
        self.assertEqual([item['text'] for item in result], ['now I will take you.', 'The name is Berg'])

    def test_rows_apart_in_time_and_protected_rows_are_left(self):
        apart = [ROWS[0], dict(ROWS[1], start=12.6)]
        readings = {'precision': ONCE, 'qwen': ONCE}
        self.assertEqual(drop_doubled_edge_words(apart, readings)[1], [])
        self.assertEqual(drop_doubled_edge_words(ROWS, readings, skip_indices={1})[1], [])
        self.assertEqual(drop_doubled_edge_words([ROWS[0], dict(ROWS[1], confirmed_prefix='for the')], readings)[1], [])
        self.assertEqual(drop_doubled_edge_words([ROWS[0], dict(ROWS[1], confirmed_lead_onset=True)], readings)[1], [])

    def test_readings_far_from_the_boundary_do_not_count(self):
        far = [row(100.0, 104.5, ONCE[0]['text'])]
        self.assertEqual(drop_doubled_edge_words(ROWS, {'precision': far, 'qwen': far})[1], [])
        self.assertEqual(drop_doubled_edge_words(ROWS, {})[1], [])


if __name__ == '__main__':
    unittest.main()
