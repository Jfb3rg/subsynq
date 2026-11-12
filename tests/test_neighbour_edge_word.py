"""A reread clip starts before its row: one word it hears from the row before is not written twice."""
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from source_judge import without_neighbour_edge_word


def timed(text, start, step=.3, name='precision'):
    return {'name': name, 'language': 'en', 'confidence': .8, 'text': text,
            'words': [{'word': f' {word}', 'start': round(start + index * step, 2), 'end': round(start + (index + 1) * step, 2), 'probability': .9}
                      for index, word in enumerate(text.split())]}


PREVIOUS = 'Yes, well, we have been walking quietly'
CURRENT = 'by enough sleeping guards to know it is a nasty mirror.'
HEARD = 'quietly by enough sleeping guards to know the way by heart.'
QWEN = {'name': 'qwen', 'language': 'English', 'confidence': .5, 'text': 'Quietly by enough sleeping guards to know the way by heart.'}


class NeighbourEdgeWordTests(unittest.TestCase):
    def test_a_word_timed_before_the_row_that_closes_the_previous_line_is_taken_away(self):
        readings = [timed(HEARD, 11.72), timed(HEARD, 11.7, name='v2'), QWEN]
        self.assertEqual(without_neighbour_edge_word(HEARD, CURRENT, PREVIOUS, '', readings, 12.0, 15.5),
                         ('by enough sleeping guards to know the way by heart.', 'quietly'))

    def test_what_is_left_opens_a_sentence_after_a_closed_line(self):
        heard = 'Lads, time to go.'
        self.assertEqual(without_neighbour_edge_word(heard, 'Time to grow.', 'Sorry, lads.', '', [timed(heard, 11.6)], 12.0, 13.0),
                         ('Time to go.', 'lads'))

    def test_a_word_timed_inside_the_row_was_said_again(self):
        readings = [timed(HEARD, 12.05), timed(HEARD, 11.7, name='v2')]
        self.assertEqual(without_neighbour_edge_word(HEARD, CURRENT, PREVIOUS, '', readings, 12.0, 15.5), (HEARD, None))

    def test_without_word_times_nothing_is_taken_away(self):
        self.assertEqual(without_neighbour_edge_word(HEARD, CURRENT, PREVIOUS, '', [QWEN], 12.0, 15.5), (HEARD, None))

    def test_a_word_the_row_already_held_or_the_neighbour_does_not_hold_stays(self):
        readings = [timed(HEARD, 11.72)]
        self.assertIsNone(without_neighbour_edge_word(HEARD, 'quietly ' + CURRENT, PREVIOUS, '', readings, 12.0, 15.5)[1])
        self.assertIsNone(without_neighbour_edge_word(HEARD, CURRENT, 'Yes, well, we have been walking.', '', readings, 12.0, 15.5)[1])
        self.assertIsNone(without_neighbour_edge_word('quietly now.', 'now.', PREVIOUS, '', [timed('quietly now.', 11.72)], 12.0, 13.0)[1])

    def test_the_closing_word_that_opens_the_next_line(self):
        heard = 'We keep the lamps burning until morning. Then'
        readings = [timed(heard, 20.0, step=.5)]
        self.assertEqual(without_neighbour_edge_word(heard, 'We keep the lamps burning until mourning.', '', 'Then we sleep.', readings, 20.0, 23.6),
                         ('We keep the lamps burning until morning.', 'then'))
        self.assertIsNone(without_neighbour_edge_word(heard, 'We keep the lamps burning until mourning.', '', 'Then we sleep.', readings, 20.0, 24.0)[1])

    def test_the_reread_replaces_the_row_without_the_neighbours_word(self):
        rows = [{'id': 1, 'start': 9.0, 'end': 12.0, 'source': PREVIOUS}, {'id': 2, 'start': 12.0, 'end': 15.5, 'source': CURRENT},
                {'id': 3, 'start': 16.0, 'end': 17.0, 'source': 'Good night.'}]
        job = {'id': 5, 'media_path': '/dev/null'}
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'):
            changed = worker.reread_flagged_rows(job, rows, [2], reader=lambda *a: {2: [timed(HEARD, 11.72), timed(HEARD, 11.7, name='v2'), QWEN]})
        self.assertEqual(changed, [2])
        self.assertEqual([row['source'] for row in rows], [PREVIOUS, 'by enough sleeping guards to know the way by heart.', 'Good night.'])
        self.assertEqual(job['reread_rows'][0]['neighbour_word_removed'], 'quietly')


if __name__ == '__main__':
    unittest.main()
