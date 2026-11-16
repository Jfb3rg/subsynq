"""A reread clip starts before its row: one word it hears from the row before is not written twice."""
import sys
import copy
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
    def dual_edge_sample(self, current='were never even'):
        rows = [{'id': 1, 'start': 8., 'end': 10., 'source': 'They.'},
                {'id': 2, 'start': 10., 'end': 11., 'source': current, 'source_unclear': True},
                {'id': 3, 'start': 11., 'end': 12., 'source': 'There.'}]
        words = [dict(word=word, start=start, end=end, probability=.9) for word, start, end in
                 [(' They', 9.7, 9.9), (' were', 10.15, 10.3), (' never', 10.3, 10.55),
                  (' even', 10.55, 10.75), (' there.', 11.05, 11.25)]]
        readings = [dict(name=name, language='en', confidence=.9, text='They were never even there.',
                         words=copy.deepcopy(words)) for name in ('precision', 'v2')]
        readings.append(dict(name='qwen', language='English', confidence=.5, text='They were never even there.'))
        return rows, readings

    def run_saved_readings(self, rows, readings):
        job = {'id': 5, 'media_path': '/dev/null'}
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama', side_effect=AssertionError('No models')), \
                patch.object(worker, 'reread_whisper', side_effect=AssertionError('No models')):
            changed = worker.reread_flagged_rows(job, rows, [2], reader=lambda *args: {2: readings})
        return changed, job

    def test_words_from_both_neighbours_refuse_the_entire_replacement(self):
        rows, readings = self.dual_edge_sample()
        before = copy.deepcopy((rows, readings))
        changed, job = self.run_saved_readings(rows, readings)
        self.assertEqual(changed, [])
        self.assertEqual((rows, readings), before)
        record = job['reread_rows'][0]
        self.assertEqual(record['decision'], 'omläsningen innehåller redan skrivna grannord')
        self.assertEqual(record['candidate'], 'They were never even there.')
        self.assertEqual(record['neighbour_edge_conflicts'], {'previous': 'they', 'following': 'there'})
        self.assertNotIn('neighbour_word_removed', record)

    def test_the_veto_does_not_apply_an_interior_change_from_the_spanning_candidate(self):
        rows, readings = self.dual_edge_sample('were hardly seen')
        before = copy.deepcopy(rows)
        changed, job = self.run_saved_readings(rows, readings)
        self.assertEqual(changed, [])
        self.assertEqual(rows, before)
        self.assertIn('neighbour_edge_conflicts', job['reread_rows'][0])

    def test_a_word_inside_the_row_at_either_edge_can_still_be_a_real_repetition(self):
        for position, start, end, expected in ((0, 10.01, 10.1, 'They were never even'),
                                               (-1, 10.8, 10.95, 'Were never even there.')):
            with self.subTest(edge=position):
                rows, readings = self.dual_edge_sample()
                for reading in readings[:2]:
                    reading['words'][position].update(start=start, end=end)
                changed, job = self.run_saved_readings(rows, readings)
                self.assertEqual(changed, [2])
                self.assertEqual(rows[1]['source'], expected)
                self.assertNotIn('neighbour_edge_conflicts', job['reread_rows'][0])
                self.assertEqual([(r['start'], r['end']) for r in rows], [(8., 10.), (10., 11.), (11., 12.)])

    def test_one_timed_reader_inside_an_edge_prevents_a_unanimous_outside_claim(self):
        rows, readings = self.dual_edge_sample()
        readings[1]['words'][0].update(start=10.01, end=10.1)
        changed, job = self.run_saved_readings(rows, readings)
        self.assertEqual(changed, [2])
        self.assertEqual(rows[1]['source'], 'They were never even')
        self.assertEqual(job['reread_rows'][0]['neighbour_word_removed'], 'there')
        self.assertNotIn('neighbour_edge_conflicts', job['reread_rows'][0])

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
