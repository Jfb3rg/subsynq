import copy
import json
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from code_switch import plan_checks, apply_checks


class CodeSwitchTests(unittest.TestCase):
    def setUp(self):
        f = json.loads((Path(__file__).parent / 'fixtures' / 'glemselens-code-switch.json').read_text())
        self.rows, self.qwen, self.records = f['rows'], f['qwen'], f['records']
        self.job = {'norwegian_reader': {'chosen': True}, 'language_policy': {}}

    def plan(self, **kwargs):
        return plan_checks(self.job, self.rows, self.qwen, [], 145., **kwargs)

    def test_real_three_family_code_switch_changes_only_the_proven_source(self):
        before = copy.deepcopy(self.rows)
        plans = self.plan()
        result, changes = apply_checks(self.rows, plans, self.records)
        self.assertEqual(len(changes), 1)
        self.assertEqual(result[1]['text'], 'Forget about me.')
        self.assertEqual(result[1]['confirmed_source_words'], ['forget', 'about', 'me'])
        self.assertEqual(result[1]['confirmed_language'], 'en')
        self.assertEqual((result[1]['start'], result[1]['end']), (124.96, 126.16))
        self.assertEqual(result[0], before[0])
        self.assertEqual(result[2], before[2])
        self.assertEqual(self.rows, before)

    def test_explicit_language_clear_sources_and_other_reader_support_veto_probe(self):
        self.job['language_policy'] = {'effective_language': 'no'}
        self.assertEqual(self.plan(), [])
        self.job['language_policy'] = {}
        self.assertEqual(plan_checks(self.job, self.rows, self.qwen, [self.rows[1]], 145.), [])
        for confidence in (.8, 0, float('nan'), None):
            self.rows[1]['confidence'] = confidence
            self.assertEqual(self.plan(), [])

    def test_no_pack_no_qwen_or_competing_qwen_words_do_not_plan(self):
        self.assertEqual(self.plan(maximum=0), [])
        self.assertEqual(plan_checks({}, self.rows, self.qwen, [], 145.), [])
        self.assertEqual(plan_checks(self.job, self.rows, [], [], 145.), [])
        self.qwen.append({**self.qwen[0], 'text': 'Go away from me.'})
        self.assertEqual(self.plan(), [])

    def test_short_identifications_and_words_shared_with_current_source_are_not_rewritten(self):
        self.rows[1]['text'] = 'Jeg er Joe.'
        self.qwen[0]['text'] = 'Yeah, Joe.'
        self.assertEqual(self.plan(), [])
        self.qwen[0]['text'] = 'I am Joe.'
        self.assertEqual(self.plan(), [])
        self.rows[1]['text'] = 'øl'
        self.qwen[0]['text'] = 'Forget about me.'
        self.assertEqual(apply_checks(self.rows, self.plan(), self.records), (self.rows, []),
                         'the existing length guard also applies to a confirmed code switch')

    def test_protected_words_prefixes_and_neighbour_overlap_are_kept(self):
        for key in ('confirmed_source_words', 'confirmed_prefix', 'confirmed_lead_onset'):
            self.rows[1][key] = ['proof']
            self.assertEqual(self.plan(), [])
            del self.rows[1][key]
        self.rows[2]['start'] = 126.2
        self.assertEqual(self.plan(), [])

    def test_all_four_independent_window_readings_are_required(self):
        plans = self.plan()
        for records in (self.records[:-1], self.records + [self.records[0]]):
            self.assertEqual(apply_checks(self.rows, plans, records), (self.rows, []))

    def test_wrong_language_weak_words_disagreement_or_nonfinite_times_veto(self):
        plans = self.plan()
        for key, value in (('language', 'no'), ('language_probability', .79), ('language_probability', float('nan')),
                           ('text', 'Do not forget me'), ('window', True)):
            records = copy.deepcopy(self.records)
            records[0][key] = value
            self.assertEqual(apply_checks(self.rows, plans, records), (self.rows, []))
        for key, value in (('probability', .59), ('start', float('inf')), ('end', None)):
            records = copy.deepcopy(self.records)
            records[0]['words'][0][key] = value
            self.assertEqual(apply_checks(self.rows, plans, records), (self.rows, []))

    def test_missing_stale_or_duplicate_word_lists_are_not_evidence(self):
        plans = self.plan()
        for mode in ('missing', 'stale', 'overlap'):
            records = copy.deepcopy(self.records)
            if mode == 'missing': records[0]['words'] = []
            elif mode == 'stale': records[0]['words'][0]['text'] = 'Different'
            else: records[0]['words'][0]['end'] = records[0]['words'][1]['end']
            self.assertEqual(apply_checks(self.rows, plans, records), (self.rows, []))
        self.rows[1]['text'] = 'A changed row.'
        self.assertEqual(apply_checks(self.rows, plans, self.records), (self.rows, []))

    def test_no_candidate_does_not_load_any_model(self):
        with patch.object(worker, 'WhisperModel') as model:
            rows = worker.read_code_switch_checks({}, Path('/media/unused'), self.rows, {}, None, 145.)
        model.assert_not_called()
        self.assertIs(rows, self.rows)

    def test_source_chooser_cannot_restore_the_old_misheard_words(self):
        from source_judge import choose_readings
        result, _ = apply_checks(self.rows, self.plan(), self.records)
        row = {**result[1], 'id': 1, 'source': result[1]['text'], 'alternatives': [
            {'text': 'grønn og vann', 'support': 4, 'current_support': 1}]}
        call = lambda _: {'response': json.dumps({'rows': [{'id': 1, 'a_has_error': True, 'choice': 'B'}]})}
        final, choices = choose_readings([row], call, 'test')
        self.assertEqual(final[0]['source'], 'Forget about me.')
        self.assertFalse(choices[0]['applied'])
        self.assertEqual(choices[0]['reason'], 'confirmed_audio_word')

    def test_confirmed_english_row_does_not_force_its_norwegian_neighbours_to_english(self):
        result, _ = apply_checks(self.rows, self.plan(), self.records)
        rows = [{**r, 'source': r['text']} for r in result]
        self.assertEqual(worker.reread_row_language(self.job, rows, rows[1]), 'en')
        self.assertEqual(worker.reread_row_language(self.job, rows, rows[0]), 'no')
        self.job['language_policy'] = {'effective_language': 'no'}
        self.assertEqual(worker.reread_row_language(self.job, rows, rows[1]), 'no')
        self.job['language_policy'] = {}
        rows[1]['source'] = 'A stale different source.'
        self.assertEqual(worker.reread_row_language(self.job, rows, rows[1]), 'no')
        self.job['language_policy'] = {'effective_language': 'no'}
        with patch.object(worker, 'window_language', return_value='fr'):
            self.assertEqual(worker.reread_row_language(self.job, rows, rows[1]), 'fr',
                             'preserve the existing locally confirmed language precedence')

    def test_saved_source_roundtrip_preserves_the_confirmed_local_language(self):
        from source_audit_probe import saved_reread_input
        result, _ = apply_checks(self.rows, self.plan(), self.records)
        rows = [{**r, 'id': i, 'source': r['text']} for i, r in enumerate(result)]
        job = dict(self.job, id=9, audio_choice={'index': 1})
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            path = worker.save_source_transcript(job, '/extra/clip.mp4', rows, 'CPU', 'Whisper')
            saved = json.loads(path.read_text())
        restored, sources = saved_reread_input(saved)
        self.assertEqual(sources[1]['confirmed_source_words'], ['forget', 'about', 'me'])
        self.assertEqual(worker.reread_row_language(restored, sources, sources[1]), 'en')
        self.assertEqual(worker.reread_row_language(restored, sources, sources[0]), 'no')
