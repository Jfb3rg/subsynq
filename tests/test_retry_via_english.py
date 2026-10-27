import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker

ROWS = [{'id': 3, 'start': 0, 'end': 2, 'source': 'ஆடியன்ஸ்கு என்ன தேவையா'},
        {'id': 9, 'start': 3, 'end': 5, 'source': 'ஒருத்தன் இருக்கிறான்'}]


class Recorder:
    """Stands in for worker.translate: English for target_key 'en', Swedish otherwise, and remembers every call."""

    def __init__(self, fail_english=None):
        self.calls = []
        self.fail_english = fail_english

    def __call__(self, job, rows, *, target_key='sv', target_language='Swedish', refused=None, context_rows=None):
        self.calls.append({'target_key': target_key, 'sources': [row['source'] for row in rows], 'refused': refused,
                           'context_rows': context_rows})
        if target_key == 'en':
            if self.fail_english:
                raise self.fail_english
            return [{**row, 'en': f'English {row["id"]}'} for row in rows]
        return [{**row, 'sv': f'Svenska av ({row["source"]})'} for row in rows]


class ViaEnglishTests(unittest.TestCase):
    def test_the_rows_go_to_english_first_and_the_english_is_translated_to_swedish(self):
        recorder = Recorder()
        with patch.object(worker, 'translate', recorder):
            result = worker.translate_via_english({'id': 1}, ROWS, refused={3: ['advokaterna']})
        self.assertEqual([call['target_key'] for call in recorder.calls], ['en', 'sv'])
        self.assertEqual(recorder.calls[0]['sources'], [row['source'] for row in ROWS])
        self.assertEqual(recorder.calls[1]['sources'], ['English 3', 'English 9'])
        self.assertEqual(recorder.calls[1]['refused'], {3: ['advokaterna']}, 'a refused wording stays refused')
        self.assertEqual([row['sv'] for row in result], ['Svenska av (English 3)', 'Svenska av (English 9)'])

    def test_the_result_keeps_the_original_source_so_the_review_judges_what_was_said(self):
        with patch.object(worker, 'translate', Recorder()):
            result = worker.translate_via_english({'id': 1}, ROWS)
        self.assertEqual([row['source'] for row in result], [row['source'] for row in ROWS])
        self.assertEqual([(row['id'], row['start'], row['end']) for row in result], [(3, 0, 2), (9, 3, 5)])
        self.assertNotIn('en', result[0])


class RetryChoiceTests(unittest.TestCase):
    def test_a_language_with_its_own_script_retries_through_english(self):
        recorder, job = Recorder(), {'id': 1, 'script_language': 'Tamil'}
        with patch.object(worker, 'translate', recorder):
            result = worker.retry_translation(job, ROWS, refused={3: ['x']}, context_rows=[])
        self.assertEqual([call['target_key'] for call in recorder.calls], ['en', 'sv'])
        self.assertEqual(job['retried_via_english'], [3, 9])
        self.assertTrue(result[0]['sv'].startswith('Svenska av (English'))

    def test_other_languages_retry_straight_with_the_context_as_before(self):
        recorder, job = Recorder(), {'id': 1}
        with patch.object(worker, 'translate', recorder):
            worker.retry_translation(job, ROWS, refused={3: ['x']}, context_rows=['context'])
        self.assertEqual([call['target_key'] for call in recorder.calls], ['sv'])
        self.assertEqual(recorder.calls[0]['context_rows'], ['context'])
        self.assertEqual(recorder.calls[0]['refused'], {3: ['x']})
        self.assertNotIn('retried_via_english', job)

    def test_a_failing_english_step_falls_back_to_the_straight_retry(self):
        recorder, job = Recorder(fail_english=RuntimeError('Översättaren kunde inte översätta')), {'id': 1, 'script_language': 'Tamil'}
        with patch.object(worker, 'translate', recorder):
            result = worker.retry_translation(job, ROWS, refused=None, context_rows=['c'])
        self.assertEqual([call['target_key'] for call in recorder.calls], ['en', 'sv'])
        self.assertEqual(result[0]['sv'], 'Svenska av (ஆடியன்ஸ்கு என்ன தேவையா)', 'the straight translation of the original source')
        self.assertNotIn('retried_via_english', job)

    def test_a_cancelled_job_is_not_swallowed(self):
        recorder = Recorder(fail_english=worker.JobCancelled('stoppad'))
        with patch.object(worker, 'translate', recorder), self.assertRaises(worker.JobCancelled):
            worker.retry_translation({'id': 1, 'script_language': 'Tamil'}, ROWS)


class WholeFlowTests(unittest.TestCase):
    def run_flow(self, job):
        recorder, stages = Recorder(), []
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'translate', recorder), \
                patch.object(worker, 'review_and_save_translation',
                             side_effect=[worker.SourceQualityError('3 av 12 repliker är tydligt trasiga'), None]), \
                patch.object(worker, 'refused_translations', return_value={3: ['advokaterna']}):
            (Path(folder) / f"job-{job['id']}.json").write_text(json.dumps({'first': True}), encoding='utf-8')
            result = worker.translate_and_review(job, ROWS, announce=stages.append)
        return result, recorder, stages

    def test_a_refused_tamil_translation_is_redone_through_english_and_reviewed_again(self):
        result, recorder, stages = self.run_flow({'id': 31, 'script_language': 'Tamil'})
        self.assertEqual([call['target_key'] for call in recorder.calls], ['sv', 'en', 'sv'])
        self.assertEqual(stages, ['review', 'retry', 'review'])
        self.assertEqual([row['sv'] for row in result], ['Svenska av (English 3)', 'Svenska av (English 9)'])
        self.assertEqual([row['source'] for row in result], [row['source'] for row in ROWS])

    def test_without_a_script_language_the_old_retry_is_unchanged(self):
        result, recorder, stages = self.run_flow({'id': 32})
        self.assertEqual([call['target_key'] for call in recorder.calls], ['sv', 'sv'])
        self.assertEqual(recorder.calls[1]['refused'], {3: ['advokaterna']})


if __name__ == '__main__':
    unittest.main()
