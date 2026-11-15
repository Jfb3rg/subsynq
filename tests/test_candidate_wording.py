import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/ai', '/app']
import worker

OLD = 'Du får se den lilla hamnen.'
NEW = 'Du ska få se den lilla hamnen.'
ROWS = [{'id': n, 'start': n * 3.0, 'end': n * 3.0 + 2.5, 'source': f'This is sentence number {n}.', 'sv': f'Det här är mening nummer {n}.'}
        for n in range(20)]
ROWS[5] = dict(ROWS[5], source='You will get to see the little harbour.', sv=OLD)


def reply(text):
    return {'response': text, 'done_reason': 'stop'}


class CandidateWordingTests(unittest.TestCase):
    def run_rows(self, answer, job=None, **patches):
        records, requests = [], []

        def call(request):
            requests.append(request)
            if isinstance(answer, Exception):
                raise answer
            return answer
        job = job if job is not None else {'id': 1, 'audio_choice': {'language': 'en'}}
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama', side_effect=call):
            result = worker.candidate_wordings(job, [dict(row) for row in ROWS], {5}, record=records.append)
        return result, requests, records, job

    def test_the_candidate_model_translates_the_line_on_its_own(self):
        result, requests, records, _ = self.run_rows(reply(NEW))
        self.assertEqual(result[5]['sv'], NEW)
        self.assertEqual([row['sv'] for row in result if row['id'] != 5], [row['sv'] for row in ROWS if row['id'] != 5])
        self.assertEqual(requests[0]['model'], worker.CANDIDATE_MODEL_NAME)
        self.assertIn('English (en) to Swedish (sv) translator', requests[0]['prompt'])
        self.assertTrue(requests[0]['prompt'].endswith(ROWS[5]['source']))
        self.assertEqual(records[0]['changes'], [{'id': 5, 'before': OLD, 'after': NEW}])

    def test_a_wording_already_refused_for_the_row_is_never_taken_back(self):
        job = {'id': 1, 'audio_choice': {'language': 'en'}, '_refused_wordings': {'sv:5': ['Du ska få se den lilla hamnen']}}
        result, _, records, _ = self.run_rows(reply(NEW), job)
        self.assertEqual(result[5]['sv'], OLD)
        self.assertEqual(records[0]['changes'], [])
        result, _, _, _ = self.run_rows(reply(OLD.upper()))
        self.assertEqual(result[5]['sv'], OLD)

    def test_a_missing_model_or_a_failing_request_leaves_the_rows_and_stops_asking(self):
        result, requests, records, job = self.run_rows(RuntimeError('Översättningsmotorn svarade HTTP 404: model not found'))
        self.assertEqual(result, ROWS)
        self.assertEqual(records[0]['stage'], 'candidate_failed')
        self.assertTrue(job['_candidate_unavailable'])
        with patch.object(worker, 'call_ollama') as model:
            again = worker.candidate_wordings(job, [dict(row) for row in ROWS], {5})
        model.assert_not_called()
        self.assertEqual(again, ROWS)

    def test_a_language_the_model_does_not_list_and_the_switch_leave_the_row_alone(self):
        result, requests, records, _ = self.run_rows(reply(NEW), {'id': 1, 'audio_choice': {'language': 'xx'}})
        self.assertEqual((result, requests, records[0]['stage']), (ROWS, [], 'candidate_skipped'))
        with patch.object(worker, 'CANDIDATE_ENABLED', False):
            result, requests, _, _ = self.run_rows(reply(NEW))
        self.assertEqual((result, requests), (ROWS, []))

    def test_a_wording_with_a_verified_defect_keeps_the_old_text(self):
        result, _, records, _ = self.run_rows(reply('Du får se den Привет hamnen.'))
        self.assertEqual(result[5]['sv'], OLD)
        self.assertEqual(records[0]['reverted'], [5])

    def test_a_copy_of_the_source_is_not_a_translation(self):
        result, _, records, _ = self.run_rows(reply(ROWS[5]['source']))
        self.assertEqual(result[5]['sv'], OLD)
        self.assertEqual(records[0]['changes'], [])


def review(answer):
    """The review names row 5 while it holds OLD; the repair leaves it as it was. Returns (error, saved report, requests)."""
    job, requests = {'id': 77, 'spoken_swedish_ids': [], 'audio_choice': {'language': 'en'}}, []

    def assess(candidate, *, audit=None, **kwargs):
        flagged = [row['id'] for row in candidate if row['id'] == 5 and row['sv'] == OLD]
        audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': flagged,
                      'flags': [{'id': i, 'category': 'meaning', 'reason': 'Ett ord saknas.', 'source_quote': 'harbour', 'target_quote': 'hamnen'}
                                for i in flagged]})

    def repair(job_, rows_, flags, **kwargs):
        return [dict(row) for row in rows_]

    def call(request):
        requests.append(request)
        return answer

    with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
            patch.object(worker, 'assess_translation_quality', side_effect=assess), patch.object(worker, 'repair_flagged_translation', side_effect=repair), \
            patch.object(worker, 'call_ollama', side_effect=call), patch.object(worker, 'check_cancelled'), \
            patch('fragment_context_review.review_fragments', return_value=[]):
        try:
            worker.review_and_save_translation(job, [dict(row) for row in ROWS], final=True)
            error = None
        except worker.SourceQualityError as raised:
            error = raised
        saved = json.loads((Path(folder) / 'job-77.json').read_text(encoding='utf-8'))
    return error, saved, requests


class ExtraRoundTests(unittest.TestCase):
    def test_a_line_the_repair_leaves_alone_while_the_review_names_it_again_gets_a_candidate_wording(self):
        error, saved, requests = review(reply(NEW))
        self.assertIsNone(error)
        self.assertEqual(len(requests), 1)
        self.assertEqual(saved['rows'][5]['sv'], NEW)
        self.assertIn('candidate_wording', [step['stage'] for step in saved['repair_steps']])

    def test_when_the_candidate_is_also_refused_the_file_is_still_refused(self):
        error, saved, _ = review(reply(OLD))
        self.assertIsNotNone(error)
        self.assertEqual(saved['status'], 'unusable')
        self.assertEqual(saved['remaining_findings']['hard'], [5])


class CatalogueTests(unittest.TestCase):
    def test_the_candidate_model_is_listed_with_its_role(self):
        models = json.loads(Path('/app/models.json').read_text(encoding='utf-8'))['models']
        self.assertEqual([m['name'] for m in models if m.get('role') == 'candidate'], ['translategemma:12b'])


if __name__ == '__main__':
    unittest.main()
