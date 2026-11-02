import copy
import json
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from source_audit_probe import saved_reread_input
import source_audit_probe


class SourceDiagnosticInputTests(unittest.TestCase):
    def test_cluster_replay_restores_pre_cluster_fragment_and_only_uses_pre_cluster_flags(self):
        saved = {'rows': [{'id': 30, 'source': 'a', 'sv': 'after a'},
                          {'id': 32, 'source': 'b', 'sv': 'Invented completion.'}],
                 'cluster_retranslation': [{'ids': [30, 32], 'before': {'30': 'old bad', '32': 'You, in life...'}}],
                 'repair_reviews': [{'flags': [{'id': 30}, {'id': 99}]}],
                 'cluster_review': [{'flags': [{'id': 32}]}]}
        before = copy.deepcopy(saved)
        rows, ids, flagged = source_audit_probe.cluster_replay_input(saved)
        self.assertEqual(ids, {30, 32})
        self.assertEqual(flagged, {30})
        self.assertEqual(rows[1]['sv'], 'You, in life...')
        self.assertEqual(saved, before)
        saved['cluster_retranslation'][0]['before'].pop('32')
        with self.assertRaises(ValueError):
            source_audit_probe.cluster_replay_input(saved)

    def test_context_bridge_adds_only_single_holes_in_source_order_and_preserves_uncertainty(self):
        rows = [{'id': i * 10, 'source': f'source {i}', 'sv': f'target {i}', 'source_unclear': i == 3}
                for i in range(8)]
        prompt = {'requested_ids': [10, 50], 'context': [dict(rows[i]) for i in (0, 1, 2, 4, 5, 7)]}
        before = copy.deepcopy((rows, prompt))
        bridged, added = source_audit_probe.bridge_repair_context(prompt, rows)
        self.assertEqual(added, [30, 60])
        self.assertEqual([row['id'] for row in bridged['context']], [row['id'] for row in rows])
        self.assertTrue(bridged['context'][3]['source_unclear'])
        self.assertEqual(bridged['requested_ids'], [10, 50])
        self.assertEqual((rows, prompt), before)
        sparse = {'context': [rows[0], rows[3], rows[7]]}
        self.assertEqual(source_audit_probe.bridge_repair_context(sparse, rows), (sparse, []))
        for context in ([rows[0], rows[0]], [{'id': 999}]):
            with self.assertRaises(ValueError):
                source_audit_probe.bridge_repair_context({'context': context}, rows)

    def test_repair_replay_uses_pre_repair_targets_and_rejects_stale_quotes(self):
        before = {'id': 1, 'source': 'Original source.', 'sv': 'Före.'}
        saved = {'original_rows': [before], 'rows': [{**before, 'sv': 'Efter.'}],
                 'repair_steps': [{'stage': 'phrase', 'input': {'flags': [
                     {'id': 1, 'category': 'meaning', 'source_quote': 'Original source.', 'target_quote': 'Före.'}]}}]}
        rows, flags = source_audit_probe.repair_replay_input(saved)
        self.assertEqual(rows, [before])
        rows[0]['sv'] = 'Ändrad provkopia.'
        self.assertEqual(saved['original_rows'][0]['sv'], 'Före.')
        saved['repair_steps'][0]['input']['flags'][0]['target_quote'] = 'Efter.'
        with self.assertRaises(ValueError):
            source_audit_probe.repair_replay_input(saved)

    def test_repair_diagnostic_does_not_save_to_config_or_let_context_change_other_rows(self):
        rows = [{'id': i, 'source': f'source{i}', 'sv': f'target{i}'} for i in range(3)]
        flag = {'id': 1, 'category': 'meaning', 'source_quote': 'source1', 'target_quote': 'target1'}
        saved = {'job_id': 336, 'original_rows': rows, 'repair_steps': [{'stage': 'phrase', 'input': {'flags': [flag]}}]}
        job_state = dict(vars(worker.MODEL_JOB))
        def bad_repair(job, given, flags, record):
            given[0]['sv'] = 'Changed unrequested neighbour'
            return given
        with patch.object(worker, 'repair_flagged_translation', side_effect=bad_repair), \
                patch.object(worker, 'call_ollama'), patch.object(source_audit_probe, 'Path') as path, \
                patch('builtins.print'), self.assertRaises(ValueError):
            source_audit_probe.repair_context_check(saved)
        self.assertEqual(path.call_args.args[0], '/results/repair-context-check.json')
        result = json.loads(path.return_value.write_text.call_args.args[0])
        self.assertEqual(result['arms'][0]['status'], 'technical_error')
        self.assertNotIn('unchanged_unrequested_rows', result['arms'][0])
        self.assertEqual(rows[0]['sv'], 'target0')
        self.assertEqual(vars(worker.MODEL_JOB), job_state)

    def source(self):
        return {'job_id': 313, 'media_path': '/extra/clip.mp4', 'audio_track': {'index': 1},
                'rows': [{'id': 6, 'start': 8.04, 'end': 9.14, 'reading_time_source_end': 9., 'source': 'er meg jeg svarer.'}]}

    def test_saved_language_and_actual_speech_end_are_used_without_altering_input(self):
        saved = self.source()
        before = copy.deepcopy(saved)
        job, rows = saved_reread_input(saved, {'job_id': 313, 'norwegian_reader': {'chosen': True}})
        self.assertEqual(rows[0]['end'], 9.)
        self.assertEqual(worker.reread_language(job, rows), 'no')
        self.assertEqual(job['audio_stream'], 1)
        self.assertEqual(saved, before)

    def test_saved_explicit_language_wins_and_wrong_trace_or_end_is_rejected(self):
        saved = self.source()
        saved.update(language_policy={'effective_language': 'en'}, norwegian_reader={'chosen': False})
        job, rows = saved_reread_input(saved, {'job_id': 313, 'norwegian_reader': {'chosen': True}})
        self.assertEqual(worker.reread_language(job, rows), 'en')
        self.assertFalse(job['norwegian_reader']['chosen'])
        with self.assertRaises(ValueError):
            saved_reread_input(saved, {'job_id': 312})
        for end in (float('nan'), True, 8., 10.):
            saved['rows'][0]['reading_time_source_end'] = end
            with self.assertRaises(ValueError):
                saved_reread_input(saved)

    def test_translation_diagnostic_uses_real_entrypoints_and_keeps_input_unchanged(self):
        rows = self.source()['rows']
        before = copy.deepcopy(rows)
        translated = [{**rows[0], 'sv': 'Det är jag som svarar.'}]
        def translate(job, given):
            worker.update_job(job['id'], 'translating')
            worker.check_cancelled(job['id'])
            given[0]['sv'] = translated[0]['sv']
            return given
        with patch.object(worker, 'translate', side_effect=translate), \
                patch.object(worker, 'assess_translation_quality') as review, \
                patch.object(source_audit_probe, 'Path') as path, patch('builtins.print'):
            source_audit_probe.translation_check(rows)
        review.assert_called_once_with(translated, audit=[])
        self.assertEqual(rows, before)
        path.assert_called_once_with('/results/translation-check.json')
        self.assertIn('"status": "reviewed"', path.return_value.write_text.call_args.args[0])

    def test_failed_translation_review_is_recorded_and_propagated(self):
        translated = [{**self.source()['rows'][0], 'sv': 'Fel.'}]
        with patch.object(worker, 'translate', return_value=translated), \
                patch.object(worker, 'assess_translation_quality', side_effect=worker.SourceQualityError('Meaning error')), \
                patch.object(source_audit_probe, 'Path') as path, patch('builtins.print'):
            with self.assertRaises(worker.SourceQualityError):
                source_audit_probe.translation_check(self.source()['rows'])
        self.assertIn('"status": "failed"', path.return_value.write_text.call_args.args[0])
        self.assertIn('Meaning error', path.return_value.write_text.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
