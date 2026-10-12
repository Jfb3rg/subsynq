import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class TranslationPatchTests(unittest.TestCase):
    def setUp(self):
        # Phrase scope tests isolate rendering; its model contract has separate tests.
        rendering = patch.object(worker, 'render_whole_cue_meanings',
            side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)
        self.rows = [{'id': 0, 'start': 5.86, 'end': 12.44,
                      'source': 'You know, photos are like this business, it is all about light',
                      'sv': 'Du vet, foton är som det här, det handlar om ljuset'},
                     {'id': 1, 'start': 12.44, 'end': 17.92,
                      'source': 'and dark.', 'sv': 'och mörkret.'}]
        self.flags = [{'id': 0, 'category': 'meaning', 'source_quote': 'this business',
                       'target_quote': 'foton är som det här',
                       'reason': 'Ordet business saknas; den här branschen.'}]

    @staticmethod
    def answer(before='det här', after='den här branschen'):
        return [{'id': 0, 'intended_meaning': 'Photos are compared to this business.',
                 'edits': [{'before': before, 'after': after}]}]

    def test_exact_phrase_replacement_keeps_photos_timing_source_and_neighbors(self):
        result = worker.apply_translation_patches(self.rows, self.flags, self.answer())
        self.assertEqual(result[0], {**self.rows[0], 'sv': self.rows[0]['sv'].replace(
            'det här', 'den här branschen')})
        self.assertEqual(result[1], self.rows[1])
        self.assertIn('foton är', self.rows[0]['sv'])
        self.assertIn('det här', self.rows[0]['sv'])

    def test_change_outside_flagged_quote_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'utanför'):
            worker.apply_translation_patches(self.rows, self.flags, self.answer('ljuset', 'ljuset och mörkret'))

    def test_nonexistent_ambiguous_and_empty_edits_are_rejected(self):
        for before, after in [('saknas', 'nytt'), ('är', 'var'), ('', 'nytt'), ('det här', '')]:
            with self.subTest(before=before, after=after), self.assertRaises(ValueError):
                worker.apply_translation_patches(self.rows, self.flags, self.answer(before, after))

    def test_overlapping_edits_are_rejected(self):
        answer = self.answer()
        answer[0]['edits'].append({'before': 'som det här', 'after': 'som affärer'})
        with self.assertRaisesRegex(ValueError, 'överlappar'):
            worker.apply_translation_patches(self.rows, self.flags, answer)

    def test_edit_cannot_split_a_word(self):
        with self.assertRaisesRegex(ValueError, 'delar ett ord'):
            worker.apply_translation_patches(self.rows, self.flags, self.answer('foto', 'foton'))

    def test_missing_extra_duplicate_and_boolean_ids_are_rejected(self):
        for answer in [[], [{'id': 1, 'edits': []}], [{'id': False, 'edits': []}],
                       self.answer() + self.answer(), self.answer() + [{'id': 1, 'edits': []}]]:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                worker.apply_translation_patches(self.rows, self.flags, answer)

    def test_multiple_nonoverlapping_edits_use_original_positions(self):
        answer = self.answer()
        answer[0]['edits'].append({'before': 'foton', 'after': 'fotografier'})
        result = worker.apply_translation_patches(self.rows, self.flags, answer)
        self.assertIn('fotografier är som den här branschen', result[0]['sv'])

    def test_real_partial_flag_uses_patch_without_grammar_or_copy_retry(self):
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                return_value={'response': json.dumps({'rows': self.answer()})}) as model, \
                patch.object(worker, 'repair_target_grammar') as grammar:
            result = worker.repair_flagged_translation({'id': -1}, self.rows, self.flags)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.args[0]['model'], worker.REVIEW_MODEL_NAME)
        properties = model.call_args.args[0]['format']['properties']['rows']['items']['properties']
        self.assertLess(list(properties).index('intended_meaning'), list(properties).index('edits'))
        grammar.assert_not_called()
        self.assertIn('foton är som den här branschen', result[0]['sv'])

    def test_invalid_patch_retries_once_then_stops_without_changing_input(self):
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                return_value={'response': json.dumps({'rows': self.answer('ljuset', 'mörkret')})}) as model:
            with self.assertRaises(RuntimeError):
                worker.repair_flagged_translation({'id': -1}, self.rows, self.flags)
        self.assertEqual(model.call_count, 2)
        self.assertIn('rejected_patch', json.loads(model.call_args.args[0]['prompt']))
        self.assertIn('ljuset', self.rows[0]['sv'])

    def test_patch_is_not_approval_and_new_full_review_can_refuse_it(self):
        def audit(rows, **options):
            options['audit'].append({'flags': self.flags})
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'assess_translation_quality', side_effect=audit), \
                patch.object(worker, 'call_ollama', return_value={'response': json.dumps({'rows': self.answer()})}):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation({'id': -1}, self.rows)
            report = json.loads((Path(folder) / 'job--1.json').read_text())
        self.assertEqual(report['status'], 'unusable')
        self.assertEqual(report['repair_steps'][0]['stage'], 'phrase')
        self.assertIn('det här', self.rows[0]['sv'])

    def test_missing_meaning_cannot_silently_skip_the_semantic_step(self):
        answer = self.answer()
        del answer[0]['intended_meaning']
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                return_value={'response': json.dumps({'rows': answer})}) as model:
            with self.assertRaises(RuntimeError):
                worker.repair_flagged_translation({'id': -1}, self.rows, self.flags)
        self.assertEqual(model.call_count, 2)
        self.assertIn('det här', self.rows[0]['sv'])

    def test_whole_idiom_uses_the_same_semantic_phrase_repair(self):
        rows = [{'id': 0, 'source': 'Stay tuned.', 'sv': 'Stanna kvar.'}]
        flags = [{'id': 0, 'category': 'meaning', 'source_quote': 'Stay tuned.',
                  'target_quote': 'Stanna kvar.', 'reason': 'Fortsätt följa, inte stanna på en plats.'}]
        answer = [{'id': 0, 'intended_meaning': 'Continue following what happens next.',
                   'edits': [{'before': 'Stanna kvar.', 'after': 'Vänta och se.'}]}]
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                return_value={'response': json.dumps({'rows': answer})}) as model, \
                patch.object(worker, 'repair_target_grammar') as grammar:
            result = worker.repair_flagged_translation({'id': -1}, rows, flags)
        self.assertEqual(result[0]['sv'], 'Vänta och se.')
        self.assertEqual(model.call_args.args[0]['model'], worker.REVIEW_MODEL_NAME)
        grammar.assert_not_called()

    def test_false_flag_keeps_correct_idiom_verbatim_without_another_repair_call(self):
        rows = [{'id': 16, 'start': 63.12, 'end': 64.38,
                 'source': 'How about murder?', 'sv': 'Vad sägs om mord?'}]
        flags = [{'id': 16, 'category': 'meaning', 'source_quote': 'How about murder?',
                  'target_quote': 'Vad sägs om mord?', 'reason': 'Skriv Hur med mord?'}]
        answer = {'rows': [{'id': 16, 'intended_meaning': 'Suggests murder as an example.',
                            'edits': []}]}
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                return_value={'response': json.dumps(answer)}) as model:
            result = worker.repair_flagged_translation({'id': -1}, rows, flags)
        self.assertEqual(result, rows)
        self.assertEqual(model.call_count, 1)

    def test_missing_source_meaning_stops_after_invalid_responses(self):
        for value in ('', None, 42):
            answer = self.answer()
            answer[0]['intended_meaning'] = value
            with self.subTest(value=value), patch.object(worker, 'check_cancelled'), \
                    patch.object(worker, 'call_ollama',
                        return_value={'response': json.dumps({'rows': answer})}) as model:
                with self.assertRaises(RuntimeError):
                    worker.repair_flagged_translation({'id': -1}, self.rows, self.flags)
                self.assertEqual(model.call_count, 2)
                self.assertIn('det här', self.rows[0]['sv'])


class ReviewMemoryTests(unittest.TestCase):
    def test_cpu_review_releases_previous_blocks_without_skipping_any_cue(self):
        rows = [{'id': i, 'start': i * 2, 'end': i * 2 + 1,
                 'source': 'Hello there.', 'sv': 'Hej där.'} for i in range(30)]
        for device, expected_releases in [('cpu', 3), ('cloud-vast', 0), ('nvidia', 0)]:
            with self.subTest(device=device), patch.object(worker, 'MODEL_JOB') as state, \
                    patch.object(worker, 'FOCUSED_REVIEW', False), \
                    patch.object(worker, 'release_language_model_before_asr') as release, \
                    patch.object(worker, 'call_ollama', return_value={'response': '{"flags":[]}'}) as model:
                state.device = device
                audit = []
                worker.assess_translation_quality(rows, audit=audit)
                self.assertEqual(release.call_count, expected_releases)
                self.assertEqual(model.call_count, 4)
                self.assertEqual({i for block in audit for i in block['ids']}, set(range(30)))

    def test_failed_release_stops_review_instead_of_loading_over_previous_weights(self):
        rows = [{'id': i, 'start': i * 2, 'end': i * 2 + 1,
                 'source': 'Hello there.', 'sv': 'Hej där.'} for i in range(13)]
        with patch.object(worker, 'MODEL_JOB') as state, patch.object(worker, 'FOCUSED_REVIEW', False), \
                patch.object(worker, 'release_language_model_before_asr', side_effect=RuntimeError('unload failed')), \
                patch.object(worker, 'call_ollama', return_value={'response': '{"flags":[]}'}) as model:
            state.device = 'cpu'
            with self.assertRaisesRegex(RuntimeError, 'unload failed'):
                worker.assess_translation_quality(rows)
        self.assertEqual(model.call_count, 1)


if __name__ == '__main__':
    unittest.main()
