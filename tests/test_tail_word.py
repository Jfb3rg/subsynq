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
                                                                    'v2': 'that is worth silver.', 'qwen': 'Well, that is worth silver.'})
        self.assertEqual(text, 'We saw the harbour and, well, that is worth silver.')
        self.assertEqual(detail['now'], 'silver')
        self.assertEqual(detail['families'], ['qwen', 'v2'])

    def test_one_reader_is_not_enough_and_two_that_hear_the_old_word_keep_it(self):
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth gold', 'v2': 'that is worth silver.'})[0])
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth gold', 'v2': 'that is worth gold.',
                                                                       'qwen': 'that is worth silver'})[0])

    def test_readers_that_hear_different_words_or_nothing_change_nothing(self):
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth tin', 'v2': 'that is worth silver.', 'qwen': ''})[0])
        self.assertIsNone(settle_tail_word(CURRENT, 'worth', 'gold', {'precision': 'that is worth', 'v2': 'that is worth.'})[0])

    def test_the_closing_mark_and_quotes_stay(self):
        text, _ = settle_tail_word('He said "it is worth gold."', 'worth', 'gold', {'v2': 'it is worth silver', 'qwen': 'it is worth silver.'})
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
        changed, rows, job, calls = self.run_rows({'precision': 'is worth gold', 'v2': 'that is worth silver', 'qwen': 'that is worth silver.'})
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
        changed, rows, job, _ = self.run_rows({'v2': 'that is worth silver', 'qwen': 'that is worth silver.'}, rows)
        self.assertEqual((changed, rows[1]['source']), ([], CURRENT))
        self.assertIn('skipped', job['reread_rows'][0]['tail_word'])

    def test_the_switch_and_a_failing_reader_leave_the_row_alone(self):
        with patch.object(worker, 'TAIL_WORD_ENABLED', False):
            changed, rows, _, calls = self.run_rows({'v2': 'that is worth silver', 'qwen': 'that is worth silver.'})
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


class ReadRowTailsTests(unittest.TestCase):
    """read_row_tails with fake readers: the clip's words are cut at the row's end plus KEEP_AFTER, for Whisper and for Qwen3-ASR alike."""

    class Word:
        def __init__(self, word, start, end):
            self.word, self.start, self.end = word, start, end

    class Segment:
        def __init__(self, words):
            self.words = words

    def fake_whisper(self, words):
        outer = self

        class Model:
            def transcribe(self, clip, **options):
                return [outer.Segment([outer.Word(*word) for word in words])], None
        return Model()

    def test_every_reader_is_cut_after_the_row_and_qwen_runs_where_the_job_runs(self):
        rows = [{'id': 2, 'start': 12.0, 'end': 15.5, 'source': CURRENT}]
        # The clip starts 2 s before the row's end (13.5 s): the row ends at 2.0 s into the clip, words up to 2.3 s belong to it.
        words = [(' is', 1.0, 1.3), (' worth', 1.4, 1.9), (' silver.', 2.0, 2.4), (' Then', 2.9, 3.2)]
        items = [{'text': 'is', 'start': 1.0, 'end': 1.3}, {'text': 'worth', 'start': 1.4, 'end': 1.9},
                 {'text': 'silver.', 'start': 2.0, 'end': 2.4}, {'text': 'Then', 'start': 2.9, 'end': 3.2}]
        job = {'id': 6, 'media_path': '/dev/null'}
        with patch.object(worker.subprocess, 'run'), patch.object(worker, 'audio_map', return_value='0:1'), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'reread_row_language', return_value='en'), patch.object(worker, 'reread_whisper', side_effect=lambda name: self.fake_whisper(words)), \
                patch.object(worker, 'qwen_health', return_value=True), patch.object(worker, 'qwen_transcribe', return_value={'items': items}) as qwen, \
                patch.object(worker, 'release_cloud_gpu_memory', return_value=None), patch.object(worker, 'qwen_unload') as unload:
            heard = worker.read_row_tails(job, '/dev/null', rows, [2])
        self.assertEqual(heard, {2: {'precision': 'is worth silver.', 'v2': 'is worth silver.', 'qwen': 'is worth silver.'}})
        self.assertEqual(qwen.call_args.args[1], 'English')
        unload.assert_called_once()

    def test_a_qwen_that_cannot_align_gives_no_reading_and_a_missing_qwen_is_skipped(self):
        rows = [{'id': 2, 'start': 12.0, 'end': 15.5, 'source': CURRENT}]
        words = [(' worth', 1.4, 1.9)]
        common = [patch.object(worker.subprocess, 'run'), patch.object(worker, 'audio_map', return_value='0:1'), patch.object(worker, 'check_cancelled'),
                  patch.object(worker, 'reread_row_language', return_value='en'),
                  patch.object(worker, 'reread_whisper', side_effect=lambda name: self.fake_whisper(words))]
        for patcher in common:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in common])
        with patch.object(worker, 'qwen_health', return_value=True), patch.object(worker, 'qwen_transcribe', return_value={'items': []}), \
                patch.object(worker, 'release_cloud_gpu_memory', return_value=None), patch.object(worker, 'qwen_unload'):
            heard = worker.read_row_tails({'id': 6}, '/dev/null', rows, [2])
        self.assertEqual(heard, {2: {'precision': 'worth', 'v2': 'worth'}})
        with patch.object(worker, 'qwen_health', return_value=False), patch.object(worker, 'qwen_transcribe') as qwen:
            heard = worker.read_row_tails({'id': 6}, '/dev/null', rows, [2])
        qwen.assert_not_called()
        self.assertEqual(sorted(heard[2]), ['precision', 'v2'])


if __name__ == '__main__':
    unittest.main()
