import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker


def rows(count):
    return [{'id': n, 'start': n * 2, 'end': n * 2 + 1.5, 'source': f'Line number {n} here.', 'sv': f'Rad nummer {n} här.'}
            for n in range(count)]


def comparisons(sample, preserved=True):
    return [{'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'],
             'meaning_preserved': preserved} for row in sample]


def flag_for(row):
    return {'id': row['id'], 'category': 'meaning', 'source_quote': row['source'][:14], 'target_quote': row['sv'][:12],
            'reason': 'Betydelsen ändras: en konkret förklaring av vad som gick förlorat.'}


def contradictory(sample):
    """A flag on one cue while the meaning comparison says every cue is preserved: the reviewer contradicts itself."""
    return {'comparisons': comparisons(sample), 'flags': [flag_for(sample[1])]}


def consistent(sample):
    return {'comparisons': comparisons(sample), 'flags': []}


class ReviewSplitTests(unittest.TestCase):
    """A reviewer that cannot answer about a whole block is asked about each half, with the same strict validation."""

    def setUp(self):
        for patcher in (patch.object(worker, 'FOCUSED_REVIEW', True),
                        patch.object(worker, 'release_language_model_before_asr')):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def responder(whole_block_size, whole, part):
        calls = []

        def answer(request):
            sample = json.loads(request['prompt'])['rows']
            calls.append([row['id'] for row in sample])
            return {'response': json.dumps((whole if len(sample) == whole_block_size else part)(sample))}

        return answer, calls

    def test_a_self_contradicting_answer_about_the_block_is_replaced_by_valid_answers_about_its_halves(self):
        answer, calls = self.responder(4, contradictory, consistent)
        audit = []
        with patch.object(worker, 'call_ollama', side_effect=answer):
            self.assertTrue(worker.assess_translation_quality(rows(4), audit=audit))
        self.assertEqual(calls, [[0, 1, 2, 3], [0, 1, 2, 3], [0, 1], [2, 3]])
        self.assertEqual([entry['status'] if 'status' in entry else 'review' for entry in audit],
                         ['reviewer_error', 'reviewer_error', 'split', 'review', 'review'])
        self.assertEqual([entry['ids'] for entry in audit[3:]], [[0, 1], [2, 3]])
        self.assertTrue(all(entry['bad_ids'] == [] for entry in audit[3:]))

    def test_the_split_goes_on_down_to_one_cue_and_a_real_defect_is_still_reported(self):
        def answer(request):
            sample = json.loads(request['prompt'])['rows']
            ids = [row['id'] for row in sample]
            if len(sample) > 1:
                return {'response': json.dumps(contradictory(sample))}      # invalid at every size above one cue
            single = {'comparisons': comparisons(sample, preserved=ids != [1]),
                      'flags': [flag_for(sample[0])] if ids == [1] else []}   # cue 1 genuinely changed meaning
            return {'response': json.dumps(single)}

        audit = []
        with patch.object(worker, 'call_ollama', side_effect=answer):
            self.assertTrue(worker.assess_translation_quality(rows(4), audit=audit))
        reviewed = [entry for entry in audit if 'bad_ids' in entry]
        self.assertEqual([entry['ids'] for entry in reviewed], [[0], [1], [2], [3]])
        self.assertEqual([entry['bad_ids'] for entry in reviewed], [[], [1], [], []])

    def test_a_genuinely_broken_block_still_stops_after_the_halves_are_judged(self):
        def answer(request):
            sample = json.loads(request['prompt'])['rows']
            if len(sample) > 1:
                return {'response': json.dumps(contradictory(sample))}
            return {'response': json.dumps({'comparisons': comparisons(sample, preserved=False), 'flags': [flag_for(sample[0])]})}

        with patch.object(worker, 'call_ollama', side_effect=answer):
            with self.assertRaises(worker.SourceQualityError):
                worker.assess_translation_quality(rows(4))

    def test_one_cue_that_never_gets_a_valid_answer_fails_as_a_technical_error(self):
        def answer(request):
            sample = json.loads(request['prompt'])['rows']
            return {'response': json.dumps(contradictory(sample) if len(sample) > 1 else {'comparisons': [], 'flags': []})}

        with patch.object(worker, 'call_ollama', side_effect=answer):
            with self.assertRaises(RuntimeError) as raised:
                worker.assess_translation_quality(rows(2))
        self.assertNotIsInstance(raised.exception, worker.SourceQualityError)
        self.assertIn('slutkontrollen gav inget giltigt svar', str(raised.exception))

    def test_an_engine_failure_is_not_split_into_more_requests(self):
        with patch.object(worker, 'call_ollama', side_effect=RuntimeError('Översättningsmotorn svarade HTTP 500')) as model:
            with self.assertRaises(RuntimeError):
                worker.assess_translation_quality(rows(4))
        self.assertEqual(model.call_count, 1)

    def test_a_valid_answer_about_the_whole_block_is_never_split(self):
        answer, calls = self.responder(4, consistent, consistent)
        with patch.object(worker, 'call_ollama', side_effect=answer):
            self.assertTrue(worker.assess_translation_quality(rows(4)))
        self.assertEqual(calls, [[0, 1, 2, 3]])


if __name__ == '__main__':
    unittest.main()
