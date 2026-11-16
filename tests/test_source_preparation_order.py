import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker


ROWS = [dict(id=1, start=0., end=2., source='I brought the', source_unclear=True),
        dict(id=2, start=2., end=5., source='package. Can we leave?')]
WORDS = [[(1.6, 2., 'the'), (2., 2.6, 'package'), (2.8, 3., 'can'), (3., 3.2, 'we')]]


class SourcePreparationOrderTests(unittest.TestCase):
    def translate(self, job, rows):
        with patch.object(worker, 'translate', side_effect=lambda j, rs: [dict(r, sv='Test') for r in rs]), \
                patch.object(worker, 'review_and_save_translation'), patch.object(worker, 'check_cancelled'):
            return worker.translate_and_review(job, rows)

    def test_real_work_audits_and_saves_the_new_boundaries(self):
        class AuditReached(Exception):
            pass
        rows = copy.deepcopy(ROWS)
        job = dict(id=-1, media_path='/unused', _sentence_cut_words=copy.deepcopy(WORDS))
        saved = []
        def save(j, media, rs, *args, **kwargs):
            saved.append((kwargs.get('raw', False), copy.deepcopy(rs)))
        def audit(j, rs, *args, **kwargs):
            self.assertEqual([r['source'] for r in rs], ['I brought the package.', 'Can we leave?'])
            self.assertEqual((rs[0]['end'], rs[1]['start']), (2.6, 2.8))
            self.assertTrue(rs[0]['source_unclear'])
            raise AuditReached()
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            stack.enter_context(patch.object(worker.MODEL_JOB, 'phase', None, create=True))
            media = Path(temp) / 'sample.mkv'
            media.write_bytes(b'fixture, no media decoding')
            replacements = dict(safe_media=lambda p: media, set_hardware=lambda *a, **k: None,
                local_cpu_name=lambda: 'CPU', asr_model_description=lambda *a: 'Whisper',
                transcribe_and_release=lambda *a: rows, release_cloud_gpu_memory=lambda: None,
                update_job=lambda *a, **k: None, save_source_transcript=save,
                choose_between_readings=lambda *a: (rows, []), apply_verified_source_edits=lambda *a: (rows, []),
                active_gemma_models=lambda: ['translation', 'review'], validate_episode_segments=lambda rs: None,
                assess_source_with_reread=audit, restore_source_punctuation=lambda *a, **k: 0)
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(worker, name, side_effect=replacement))
            with self.assertRaises(AuditReached):
                worker.work(job, None, 'cpu', {})
        self.assertEqual(saved[0][1], ROWS, 'the raw ASR snapshot remains untouched')
        self.assertEqual(saved[1][1], rows, 'the pre-audit source snapshot uses prepared rows')
        self.assertNotIn('_sentence_cut_words', job)

    def test_translation_keeps_audited_boundaries_and_flags_without_second_punctuation_attempt(self):
        rows = copy.deepcopy(ROWS)
        job = dict(id=-1, _sentence_cut_words=copy.deepcopy(WORDS))
        with patch.object(worker, 'restore_source_punctuation') as punctuation:
            self.assertTrue(worker.prepare_source_for_review(job, rows))
            audited = copy.deepcopy(rows)
            # Even if later code provides more cut evidence, translation cannot move audited rows.
            job['_sentence_cut_words'] = copy.deepcopy(WORDS)
            with patch.object(worker, 'move_cuts_to_sentence_ends') as cut:
                translated = self.translate(job, rows)
            cut.assert_not_called()
            punctuation.assert_called_once()
        self.assertEqual(rows, audited)
        self.assertTrue(translated[0]['source_unclear'])
        self.assertNotIn('_source_punctuation_texts', job)

    def test_new_asr_wordings_get_punctuation_with_no_boundary_move(self):
        rows = copy.deepcopy(ROWS)
        job = dict(id=-1, _sentence_cut_words=copy.deepcopy(WORDS))
        with patch.object(worker, 'restore_source_punctuation'):
            worker.prepare_source_for_review(job, rows)
        times = [(r['id'], r['start'], r['end']) for r in rows]
        rows[0]['source'], rows[1]['source'] = 'i carried the package', 'can we leave now'
        reply = {'response': json.dumps({'text': 'I carried the package. Can we leave now?'})}
        with patch.object(worker, 'call_ollama', return_value=reply) as call, \
                patch.object(worker, 'release_language_model_before_asr'), \
                patch.object(worker, 'move_cuts_to_sentence_ends') as cut:
            result = self.translate(job, rows)
        call.assert_called_once()
        cut.assert_not_called()
        self.assertEqual([r['source'] for r in result], ['I carried the package.', 'Can we leave now?'])
        self.assertEqual([(r['id'], r['start'], r['end']) for r in result], times)
        self.assertTrue(result[0]['source_unclear'])

    def test_unsuccessful_optional_punctuation_is_not_retried_on_unchanged_source(self):
        rows = [dict(id=i, start=i*2., end=i*2.+1., source=s) for i, s in enumerate(['we can leave', 'bring the package'])]
        job = dict(id=-1)
        bad = {'response': json.dumps({'text': 'Different words.'})}
        with patch.object(worker, 'call_ollama', return_value=bad) as call, \
                patch.object(worker, 'release_language_model_before_asr'):
            worker.prepare_source_for_review(job, rows)
            before = copy.deepcopy(rows)
            self.translate(job, rows)
        call.assert_called_once()
        self.assertEqual(rows, before)

    def test_restricted_punctuation_keeps_full_context_and_only_changes_requested_rows(self):
        rows = [dict(id=i, source=s) for i, s in enumerate(['we can leave', 'bring the package', 'close the door'])]
        requests = []
        def call(request):
            requests.append(json.loads(request['prompt'])['text'])
            return {'response': json.dumps({'text': 'We can leave. Bring the package. Close the door.'})}
        self.assertEqual(worker.restore_source_punctuation(rows, call, only_ids={0}), 1)
        self.assertEqual(requests, ['we can leave bring the package close the door'])
        self.assertEqual([r['source'] for r in rows], ['We can leave.', 'bring the package', 'close the door'])
