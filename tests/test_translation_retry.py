import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


ROWS = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Will you be making a statement?'},
        {'id': 1, 'start': 2, 'end': 3, 'source': 'Stay tuned.'}]


def translation(text):
    return [{**ROWS[0], 'sv': 'Kommer du att göra ett uttalande?'}, {**ROWS[1], 'sv': text}]


class RefusedTranslationsTests(unittest.TestCase):
    def test_flagged_wordings_come_from_the_saved_report(self):
        report = {'original_rows': translation('Stanna kvar.'), 'rows': translation('Håll med tittning.'),
                  'reviews': [{'flags': [{'id': 1}]}], 'repair_reviews': [{'flags': [{'id': 1}]}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)):
            (Path(folder) / 'job-5.json').write_text(json.dumps(report), encoding='utf-8')
            self.assertEqual(worker.refused_translations({'id': 5}),
                             {1: ['Stanna kvar.', 'Håll med tittning.']})

    def test_no_report_or_no_flags_means_nothing_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)):
            self.assertEqual(worker.refused_translations({'id': 6}), {})
            (Path(folder) / 'job-7.json').write_text(json.dumps(
                {'rows': translation('x'), 'reviews': [{'flags': []}]}), encoding='utf-8')
            self.assertEqual(worker.refused_translations({'id': 7}), {})


class TranslateAndReviewTests(unittest.TestCase):
    def run_flow(self, reviews, refused):
        stages = []
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'translate', side_effect=[translation('Stanna kvar.'),
                                                               translation('Häng kvar.')]) as translate, \
                patch.object(worker, 'review_and_save_translation', side_effect=reviews) as review, \
                patch.object(worker, 'refused_translations', return_value=refused):
            (Path(folder) / 'job-9.json').write_text('{"first": true}', encoding='utf-8')
            try:
                result = worker.translate_and_review({'id': 9}, ROWS, announce=stages.append)
                error = None
            except worker.SourceQualityError as caught:
                result, error = None, caught
            kept = (Path(folder) / 'job-9-attempt1.json').exists()
        return result, error, stages, translate, review, kept

    def test_a_clean_first_review_translates_once(self):
        result, error, stages, translate, review, kept = self.run_flow([None], {})
        self.assertIsNone(error)
        self.assertEqual(translate.call_count, 1)
        self.assertEqual(stages, ['review'])
        self.assertEqual(result[1]['sv'], 'Stanna kvar.')

    def test_a_failed_review_retranslates_once_with_the_refused_wordings_and_keeps_the_source(self):
        failure = worker.SourceQualityError('2 av 4 repliker är tydligt trasiga')
        result, error, stages, translate, review, kept = self.run_flow([failure, None], {1: ['Stanna kvar.']})
        self.assertIsNone(error)
        self.assertEqual(translate.call_count, 2)
        self.assertEqual(translate.call_args_list[1].kwargs['refused'], {1: ['Stanna kvar.']})
        self.assertEqual(stages, ['review', 'retry', 'review'])
        self.assertEqual(result[1]['sv'], 'Häng kvar.')
        self.assertTrue(kept)       # the first attempt's report is not lost

    def test_two_failures_end_as_a_translation_error_that_is_still_a_quality_error(self):
        failure = worker.SourceQualityError('trasiga')
        result, error, stages, translate, review, kept = self.run_flow([failure, failure], {1: ['Stanna kvar.']})
        self.assertIsInstance(error, worker.TranslationQualityError)
        self.assertIsInstance(error, worker.SourceQualityError)   # old handlers still see it
        self.assertIn('två översättningsförsök', str(error))
        self.assertEqual(translate.call_count, 2)

    def test_without_anything_to_refuse_there_is_no_pointless_second_try(self):
        failure = worker.SourceQualityError('trasiga')
        result, error, stages, translate, review, kept = self.run_flow([failure], {})
        self.assertIsInstance(error, worker.TranslationQualityError)
        self.assertEqual(translate.call_count, 1)

    def test_the_fallback_reason_says_which_check_rejected_it(self):
        self.assertIn('översättningens slutkontroll', worker.fallback_reason_text(worker.TranslationQualityError('x')))
        self.assertIn('källkontrollen', worker.fallback_reason_text(worker.SourceQualityError('x')))


class RefusedWordingInThePromptTests(unittest.TestCase):
    def test_selective_retry_gets_readonly_neighbors_in_both_requests(self):
        requests = []
        def fake(request):
            requests.append(json.loads(request['prompt']))
            return {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Fortsätt följa oss.'}]})}
        context = translation('Stanna kvar.')
        context[1].pop('sv')
        with patch.object(worker, 'call_ollama', side_effect=fake), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            result = worker.translate({'id': 3}, [ROWS[1]], context_rows=context)
        self.assertEqual([row['id'] for row in result], [1])
        for request in requests:
            self.assertEqual([row['id'] for row in request['rows']], [1])
            self.assertEqual(request['surrounding_context'],
                             [{'id': 0, 'source': ROWS[0]['source'], 'sv': context[0]['sv']}])
        self.assertNotIn('sv', context[1])

    def test_the_translator_is_told_which_wordings_were_rejected(self):
        requests = []
        def fake(request):
            requests.append(request)
            ids = [row['id'] for row in json.loads(request['prompt'])['rows']]
            return {'response': json.dumps({'rows': [{'id': i, 'sv': f'Rad {i}'} for i in ids]})}
        with patch.object(worker, 'call_ollama', side_effect=fake), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            worker.translate({'id': 3}, ROWS, refused={1: ['Stanna kvar.']})
        first = requests[0]
        self.assertEqual(json.loads(first['prompt'])['refused_targets'], {'1': ['Stanna kvar.']})
        self.assertIn('REJECTED', first['system'])

    def test_without_refused_wordings_the_prompt_is_unchanged(self):
        requests = []
        def fake(request):
            requests.append(request)
            ids = [row['id'] for row in json.loads(request['prompt'])['rows']]
            return {'response': json.dumps({'rows': [{'id': i, 'sv': f'Rad {i}'} for i in ids]})}
        with patch.object(worker, 'call_ollama', side_effect=fake), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            worker.translate({'id': 3}, ROWS)
        self.assertNotIn('refused_targets', json.loads(requests[0]['prompt']))
        self.assertNotIn('REJECTED', requests[0]['system'])


class ApprovedRetryRowsTests(unittest.TestCase):
    def report(self):
        return {'rows': translation('Stanna kvar.'), 'focused_review': True,
                'reviews': [{'ids': [0, 1], 'bad_ids': [1], 'flags': [{'id': 1}],
                             'comparisons': [{'id': 0, 'meaning_preserved': True},
                                             {'id': 1, 'meaning_preserved': False}]}]}

    def approved(self, report):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)):
            (Path(folder) / 'job-4.json').write_text(json.dumps(report), encoding='utf-8')
            return worker.approved_retry_rows({'id': 4}, ROWS, translation('Stanna kvar.'))

    def test_keeps_only_explicitly_approved_rows_even_after_a_failed_phrase_edit(self):
        report = self.report()
        report['rows'][1]['sv'] = 'Håll utkik.'
        self.assertEqual(self.approved(report), {0: translation('x')[0]})

    def test_later_flags_and_unreviewed_rows_are_not_retained(self):
        report = self.report()
        report['repair_reviews'] = [{'ids': [0], 'bad_ids': [0], 'flags': [{'id': 0}],
                                     'comparisons': [{'id': 0, 'meaning_preserved': False}]}]
        self.assertEqual(self.approved(report), {})
        report = self.report()
        report['reviews'] = []
        self.assertEqual(self.approved(report), {})

    def test_changed_source_time_target_and_malformed_or_duplicate_audits_are_not_trusted(self):
        for field, value in [('source', 'Different'), ('start', 9), ('sv', 'Ändrat')]:
            report = self.report()
            report['rows'][0][field] = value
            self.assertEqual(self.approved(report), {})
        for field, value in [('ids', [0, 0]), ('comparisons', []), ('flags', [{'id': 99}]),
                             ('bad_ids', [99])]:
            report = self.report()
            report['reviews'][0][field] = value
            self.assertEqual(self.approved(report), {})

    def test_orchestrator_retries_failed_row_and_reviews_the_whole_merged_file(self):
        first = translation('Stanna kvar.')
        calls = []
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'check_cancelled'):
            def review(job, rows):
                calls.append([dict(row) for row in rows])
                if len(calls) == 1:
                    (Path(folder) / 'job-4.json').write_text(json.dumps(self.report()), encoding='utf-8')
                    raise worker.SourceQualityError('bad idiom')
            with patch.object(worker, 'translate', side_effect=[first, [{**ROWS[1], 'sv': 'Fortsätt följa oss.'}]]) as translate, \
                    patch.object(worker, 'review_and_save_translation', side_effect=review):
                result = worker.translate_and_review({'id': 4}, ROWS)
                self.assertEqual(translate.call_args_list[1].args[1], [ROWS[1]])
                self.assertEqual(translate.call_count, 2)
                self.assertEqual(result[0], first[0])
                self.assertEqual(calls[1], result)
                self.assertEqual(len(calls[1]), 2)
                self.assertTrue((Path(folder) / 'job-4-attempt1.json').exists())


if __name__ == '__main__':
    unittest.main()
