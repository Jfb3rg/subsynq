import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/ai', '/app']
import worker
from tail_word import settle_tail_word, unheard_last_word

CURRENT = 'We saw the harbour and, well, that is worth gold.'
TIGHT = ['we saw the harbour and well that is worth', 'We saw the harbour and, well, that is worth.', 'We saw the harbour and well that is worth.']


class UnheardLastWordTests(unittest.TestCase):
    def test_a_last_word_that_every_reading_stops_before_is_found(self):
        self.assertEqual(unheard_last_word(CURRENT, TIGHT), ('worth', 'gold'))

    def test_a_last_word_that_one_reading_heard_is_not(self):
        self.assertIsNone(unheard_last_word(CURRENT, TIGHT + ['We saw the harbour and well that is worth gold.']))

    def test_readings_that_end_elsewhere_or_too_few_readings_give_nothing(self):
        self.assertIsNone(unheard_last_word(CURRENT, ['We saw the harbour and well that is worth.', 'We saw a harbour and then it is']))
        self.assertIsNone(unheard_last_word(CURRENT, [TIGHT[0]]))
        self.assertIsNone(unheard_last_word('That is worth.', TIGHT))
        self.assertIsNone(unheard_last_word('It is it is it is', ['it is it is it is', 'it is it is it is']))


class SettleTailWordTests(unittest.TestCase):
    def test_two_readers_that_hear_another_word_change_it(self):
        text, detail = settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'we saw the harbour and well that is worth gold',
                                                                    'v2': 'that is worth silver.', 'parakeet': 'Well, that is worth silver.'})
        self.assertEqual(text, 'We saw the harbour and, well, that is worth silver.')
        self.assertEqual(detail['now'], 'silver')
        self.assertEqual(detail['families'], ['parakeet', 'v2'])

    def test_one_reader_is_not_enough_and_two_that_hear_the_old_word_keep_it(self):
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth gold', 'v2': 'that is worth silver.'})[0])
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth gold', 'v2': 'that is worth gold.',
                                                                       'parakeet': 'that is worth silver'})[0])

    def test_readers_that_hear_different_words_or_nothing_change_nothing(self):
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth tin', 'v2': 'that is worth silver.', 'parakeet': ''})[0])
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth', 'v2': 'that is worth.'})[0])

    def test_the_closing_mark_and_quotes_stay(self):
        text, _ = settle_tail_word('He said "it is worth gold."', 'worth', 'gold', {'v2': 'it is worth silver', 'parakeet': 'it is worth silver.'})
        self.assertEqual(text, 'He said "it is worth silver."')


def tight(name, text):
    return {'name': name, 'text': text, 'confidence': .6}


class WorkerTests(unittest.TestCase):
    def run_rows(self, tails, rows=None, **options):
        rows = rows or [{'id': 1, 'start': 9.0, 'end': 12.0, 'source': 'The tide came in early.'},
                        {'id': 2, 'start': 12.0, 'end': 15.5, 'source': CURRENT},
                        {'id': 3, 'start': 16.0, 'end': 17.0, 'source': 'Good night.'}]
        job = {'id': 6, 'media_path': '/dev/null'}
        calls = []

        def tail_reader(job_, media, rows_, ids):
            calls.append(list(ids))
            return {row_id: tails for row_id in ids}
        readings = {2: [tight('precision', TIGHT[0]), tight('v2', TIGHT[1]), tight('qwen', TIGHT[2])]}
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'):
            changed = worker.reread_flagged_rows(job, rows, [2], reader=lambda *a: readings, tail_reader=tail_reader, **options)
        return changed, rows, job, calls

    def test_the_last_word_is_changed_where_two_readers_hear_another_word(self):
        changed, rows, job, calls = self.run_rows({'precision': 'is worth gold', 'v2': 'that is worth silver', 'parakeet': 'that is worth silver.'})
        self.assertEqual((changed, calls), ([2], [[2]]))
        self.assertEqual(rows[1]['source'], 'We saw the harbour and, well, that is worth silver.')
        self.assertEqual(rows[1]['confirmed_source_words'], ['silver'])
        self.assertEqual(job['reread_rows'][0]['decision'], 'ersatt')
        self.assertEqual(job['reread_rows'][0]['tail_word']['now'], 'silver')

    def test_nothing_changes_without_two_readers_in_agreement(self):
        changed, rows, job, _ = self.run_rows({'precision': 'is worth gold', 'v2': 'that is worth silver'})
        self.assertEqual((changed, rows[1]['source']), ([], CURRENT))
        self.assertIn('tail_word', job['reread_rows'][0])

    def test_the_word_that_opens_the_next_row_is_not_taken(self):
        rows = [{'id': 1, 'start': 9.0, 'end': 12.0, 'source': 'The tide came in early.'}, {'id': 2, 'start': 12.0, 'end': 15.5, 'source': CURRENT},
                {'id': 3, 'start': 15.5, 'end': 17.0, 'source': 'Silver is heavy.'}]
        changed, rows, job, _ = self.run_rows({'v2': 'that is worth silver', 'parakeet': 'that is worth silver.'}, rows)
        self.assertEqual((changed, rows[1]['source']), ([], CURRENT))
        self.assertIn('skipped', job['reread_rows'][0]['tail_word'])

    def test_the_switch_and_a_failing_reader_leave_the_row_alone(self):
        with patch.object(worker, 'TAIL_WORD_ENABLED', False):
            changed, rows, _, calls = self.run_rows({'v2': 'that is worth silver', 'parakeet': 'that is worth silver.'})
        self.assertEqual((changed, calls, rows[1]['source']), ([], [], CURRENT))
        rows = [{'id': 1, 'start': 9.0, 'end': 12.0, 'source': 'The tide came in early.'}, {'id': 2, 'start': 12.0, 'end': 15.5, 'source': CURRENT}]
        job = {'id': 6, 'media_path': '/dev/null'}
        readings = {2: [tight('precision', TIGHT[0]), tight('v2', TIGHT[1])]}

        def broken(*args):
            raise RuntimeError('ingen modell')
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'):
            changed = worker.reread_flagged_rows(job, rows, [2], reader=lambda *a: readings, tail_reader=broken)
        self.assertEqual((changed, rows[1]['source']), ([], CURRENT))
        self.assertIn('ingen modell', job['tail_word_error'])


if __name__ == '__main__':
    unittest.main()
