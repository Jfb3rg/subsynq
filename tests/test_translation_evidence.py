import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


def flag(row, **overrides):
    return {'id': row['id'], 'category': 'grammar', 'reason': 'Felaktig svensk grammatik.',
            'source_quote': row['source'], 'target_quote': row['sv'], **overrides}


class TranslationEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Phrase scope tests isolate rendering; its model contract has separate tests.
        rendering = patch.object(worker, 'render_whole_cue_meanings',
            side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)
        self.rows = [
            {'id': 0, 'start': 0, 'end': 2, 'source': 'मैं वापस आऊंगा।', 'sv': 'Jag kommer tillbaka.'},
            {'id': 1, 'start': 2, 'end': 4, 'source': 'This is my story.', 'sv': 'Det här är mitt berättelse.'},
            {'id': 2, 'start': 4, 'end': 5, 'source': 'ありがとう。', 'sv': 'Tack.'},
            {'id': 3, 'start': 6, 'end': 7, 'source': 'நன்றி', 'sv': 'Tack.'},
        ]

    def test_source_script_cannot_be_quoted_as_target_defect(self):
        bad = flag(self.rows[0], category='transliteration', target_quote=self.rows[0]['source'])
        with self.assertRaisesRegex(ValueError, 'target_quote'):
            worker.validate_translation_flags({'flags': [bad]}, self.rows, 'sv')

    def test_quote_from_neighbor_is_invalid(self):
        bad = flag(self.rows[1], target_quote=self.rows[0]['sv'])
        with self.assertRaises(ValueError):
            worker.validate_translation_flags({'flags': [bad]}, self.rows, 'sv')

    def test_omitted_source_words_are_not_evidence_of_untranslated_target(self):
        bad = flag(self.rows[0], category='untranslated')
        with self.assertRaisesRegex(ValueError, 'oöversatta källord'):
            worker.validate_translation_flags({'flags': [bad]}, self.rows, 'sv')

    def test_literal_untranslated_phrase_in_target_is_valid_evidence(self):
        row = {**self.rows[0], 'source': 'Je ne reviendrai pas.',
               'sv': 'Jag säger: Je ne reviendrai pas.'}
        bad = flag(row, category='untranslated', target_quote='Je ne reviendrai pas.')
        self.assertEqual(worker.validate_translation_flags({'flags': [bad]}, [row], 'sv'), [bad])

    def test_generic_label_without_evidence_is_invalid(self):
        with self.assertRaises(ValueError):
            worker.validate_translation_flags({'flags': [
                {'id': 0, 'reason': 'Translitterering.'}]}, self.rows, 'sv')

    def test_same_generic_reason_for_different_rows_is_not_a_valid_verdict(self):
        with self.assertRaisesRegex(ValueError, 'generiska'):
            worker.validate_translation_flags({'flags': [
                flag(self.rows[0], reason='Ofullständig översättning.'),
                flag(self.rows[1], reason='Ofullständig översättning.')
            ]}, self.rows, 'sv')

    def test_category_label_alone_is_not_a_reason_even_with_matching_quotes(self):
        for category in ('grammar', 'meaning', 'transliteration', 'source_uncertain'):
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, 'felkategori'):
                worker.validate_translation_flags({'flags': [flag(
                    self.rows[1], category=category, reason=category)]}, self.rows, 'sv')

    def test_repeated_identical_defect_may_have_same_reason(self):
        same = {**self.rows[1], 'id': 4}
        flags = [flag(self.rows[1]), flag(same)]
        self.assertEqual(worker.validate_translation_flags(
            {'flags': flags}, self.rows + [same], 'sv'), flags)

    def test_explicit_denial_of_claimed_defect_invalidates_entire_verdict(self):
        for category, reason in (
                ('meaning', 'Ordet är ovanligt. Det är dock inte ett tydligt meningsfel.'),
                ('meaning', 'This is not a concrete meaning error, only awkward wording.'),
                ('meaning', 'Detta är inte något betydelsefel, bara ett stilval.'),
                ('grammar', 'Det är inte ett grammatikfel utan en smaksak.')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, 'förnekar'):
                worker.validate_translation_flags({'flags': [flag(self.rows[1]), flag(
                    self.rows[0], category=category, reason=reason)]}, self.rows, 'sv')

    def test_negative_fact_and_other_category_denial_are_not_filtered(self):
        for category, reason in (
                ('meaning', 'Källan säger att jag inte kommer, men målet säger att jag kommer.'),
                ('meaning', 'Det är inte ett grammatikfel utan ett betydelsefel: fel person kommer.'),
                ('source_uncertain', 'Källan är skadad; detta är inte ett tydligt meningsfel i målet.')):
            item = flag(self.rows[0], category=category, reason=reason)
            self.assertEqual(worker.validate_translation_flags(
                {'flags': [item]}, self.rows, 'sv'), [item])

    def test_self_contradictory_verdict_retries_full_sample_and_can_still_reject(self):
        invalid = flag(self.rows[0], category='meaning',
                       reason='Det är inte ett tydligt meningsfel, bara en ovanlig formulering.')
        valid = flag(self.rows[1])
        responses = [{'response': json.dumps({'flags': [invalid]})},
                     {'response': json.dumps({'flags': [valid]})}]
        audit = []
        with patch.object(worker, 'call_ollama', side_effect=responses) as model:
            worker.assess_translation_quality(self.rows, audit=audit)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(audit[0]['status'], 'reviewer_error')
        self.assertEqual(audit[1]['flags'], [valid])
        self.assertEqual(json.loads(model.call_args.args[0]['prompt'])['rows'], self.rows)

    def test_persisting_self_contradiction_never_approves_translation(self):
        invalid = flag(self.rows[0], category='meaning',
                       reason='Det är inte ett tydligt meningsfel, bara en ovanlig formulering.')
        with patch.object(worker, 'call_ollama', return_value={
                'response': json.dumps({'flags': [invalid]})}) as model:
            with self.assertRaises(RuntimeError):
                worker.assess_translation_quality(self.rows)
        self.assertEqual(model.call_count, 6)  # whole block, first half, first cue: two tries each, nothing approved

    def test_empty_reason_unknown_category_and_wrong_id_are_invalid(self):
        for overrides in ({'reason': ''}, {'category': 'bad'}, {'id': 99}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                worker.validate_translation_flags({'flags': [flag(self.rows[1], **overrides)]}, self.rows, 'sv')

    def test_invalid_reviewer_output_is_retried_not_used_as_quality_verdict(self):
        bad = flag(self.rows[0], target_quote=self.rows[0]['source'])
        audit = []
        responses = [{'response': json.dumps({'flags': [bad]})}, {'response': '{"flags":[]}'}]
        with patch.object(worker, 'call_ollama', side_effect=responses) as model:
            self.assertTrue(worker.assess_translation_quality(self.rows, audit=audit))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(audit[0]['status'], 'reviewer_error')
        self.assertEqual(audit[1]['bad_ids'], [])

    def test_repeated_invalid_evidence_stops_as_technical_error(self):
        bad = flag(self.rows[0], target_quote=self.rows[0]['source'])
        with patch.object(worker, 'call_ollama', return_value={
                'response': json.dumps({'flags': [bad]})}):
            with self.assertRaises(RuntimeError) as raised:
                worker.assess_translation_quality(self.rows)
        self.assertNotIsInstance(raised.exception, worker.SourceQualityError)

    def test_persisting_generic_verdict_is_saved_as_error_not_unusable(self):
        flags = [flag(self.rows[0], reason='Ofullständig översättning.'),
                 flag(self.rows[1], reason='Ofullständig översättning.')]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                    patch.object(worker, 'call_ollama', return_value={
                        'response': json.dumps({'flags': flags})}):
                with self.assertRaises(RuntimeError):
                    worker.review_and_save_translation({'id': 9}, self.rows)
            saved = json.loads((Path(folder) / 'job-9.json').read_text())
        self.assertEqual(saved['status'], 'error')
        self.assertEqual(len(saved['reviews']), 8)  # 2 invalid answers + a split note per level, down to one cue
        self.assertEqual([review.get('status') for review in saved['reviews']].count('split'), 2)
        self.assertEqual(saved['reviews'][0]['verdict']['flags'], flags)
        self.assertNotIn('repair_attempts', saved)

    def test_multilingual_sources_and_swedish_targets_are_distinguished_in_prompt(self):
        with patch.object(worker, 'call_ollama', return_value={'response': '{"flags":[]}'}) as model:
            worker.assess_translation_quality(self.rows)
        request = model.call_args.args[0]
        self.assertIn('Hindi SOURCE plus Swedish TARGET', request['system'])
        self.assertIn('NEVER evidence of untranslated target', request['system'])
        self.assertEqual(json.loads(request['prompt'])['rows'], self.rows)

    def test_configured_reviewer_and_focused_prompt_do_not_replace_translation_model(self):
        verdict = {'flags': [], 'comparisons': [
            {'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'],
             'meaning_preserved': True} for row in self.rows]}
        with patch.object(worker, 'REVIEW_MODEL_NAME', 'separate-reviewer'), \
                patch.object(worker, 'FOCUSED_REVIEW', True), \
                patch.object(worker, 'call_ollama', return_value={'response': json.dumps(verdict)}) as model:
            worker.assess_translation_quality(self.rows)
        request = model.call_args.args[0]
        self.assertEqual(request['model'], 'separate-reviewer')
        self.assertFalse(request['think'])
        self.assertIn('WHO does WHAT', request['system'])
        self.assertNotEqual(worker.MODEL_NAME, 'separate-reviewer')

    def test_repair_only_changes_requested_target_not_source_timing_or_neighbors(self):
        response = {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Det här är min berättelse.'}]})}
        with patch.object(worker, 'call_ollama', return_value=response) as model, \
                patch.object(worker, 'check_cancelled'):
            repaired = worker.repair_flagged_translation({'id': 9}, self.rows, [flag(self.rows[1])])
        self.assertEqual(repaired[0], self.rows[0])
        self.assertEqual(repaired[2], self.rows[2])
        self.assertEqual(repaired[1], {**self.rows[1], 'sv': 'Det här är min berättelse.'})
        request = json.loads(model.call_args.args[0]['prompt'])
        self.assertEqual(request['requested_ids'], [1])
        self.assertNotIn('context', request)
        self.assertEqual(request['rows'], [{'id': 1, 'sv': self.rows[1]['sv']}])
        self.assertNotIn('source', request['rows'][0])
        self.assertEqual(request['source_context'], [{'id': 1, 'source': self.rows[1]['source']}])
        self.assertEqual(request['grammar_findings'], [{'id': 1,
            'target_quote': self.rows[1]['sv']}])
        self.assertNotIn('source_quote', request['grammar_findings'][0])

    def test_grammar_evidence_is_current_target_only_and_requested(self):
        response = {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Det här är min berättelse.'}]})}
        flags = [flag(self.rows[1]), flag(self.rows[0]),
                 flag(self.rows[1], target_quote='not in the candidate'),
                 flag(self.rows[1], category='meaning')]
        with patch.object(worker, 'call_ollama', return_value=response) as model, \
                patch.object(worker, 'check_cancelled'):
            worker.repair_target_grammar({'id': 9}, self.rows, {1}, flags=flags)
        findings = json.loads(model.call_args.args[0]['prompt'])['grammar_findings']
        self.assertEqual(findings, [{'id': 1, 'target_quote': self.rows[1]['sv']}])

    def test_stale_grammar_quote_after_meaning_repair_is_not_reused(self):
        responses = [
            {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Det här är min berättelse.'}]})},
            {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Det här är min berättelse.'}]})},
        ]
        with patch.object(worker, 'call_ollama', side_effect=responses) as model, \
                patch.object(worker, 'check_cancelled'):
            worker.repair_flagged_translation({'id': 9}, self.rows,
                [flag(self.rows[1]), flag(self.rows[1], category='meaning')])
        self.assertNotIn('grammar_findings', json.loads(model.call_args.args[0]['prompt']))

    def test_grammar_cleanup_cannot_reorder_correct_words_for_style(self):
        rows = [{'id': 0, 'source': 'I come to the house.', 'sv': 'Till huset kommer jag.'}]
        response = {'response': json.dumps({'rows': [{'id': 0, 'sv': 'Jag kommer till huset.'}]})}
        with patch.object(worker, 'call_ollama', return_value=response), \
                patch.object(worker, 'check_cancelled'):
            result = worker.repair_target_grammar({'id': 9}, rows, {0})
        self.assertEqual(result, rows)

    def test_grammar_cleanup_cannot_invent_or_remove_words(self):
        row = {'id': 0, 'source': 'I will not come.', 'sv': 'Jag kommer inte.'}
        for target in ('Jag kommer.', 'Jag kommer inte i morgon.'):
            with self.subTest(target=target), \
                    patch.object(worker, 'call_ollama', return_value={
                        'response': json.dumps({'rows': [{'id': 0, 'sv': target}]})}), \
                    patch.object(worker, 'check_cancelled'):
                result = worker.repair_target_grammar({'id': 9}, [row], {0})
                self.assertEqual(result, [row])

    def test_meaning_repair_without_grammar_flags_has_no_extra_rewrite(self):
        # Real Ray Gunn replay: the unsolicited grammar pass changed photos to photons.
        rows = [{'id': 0, 'start': 0, 'end': 2, 'source': 'photos are like this business',
                 'sv': 'foton är som det här'}]
        responses = [
            {'response': json.dumps({'rows': [{'id': 0, 'intended_meaning': 'Photos resemble this business.',
                         'edits': [{'before': 'det här', 'after': 'affärer'}]}]})},
            {'response': json.dumps({'rows': [{'id': 0, 'sv': 'fotoner är som affärer'}]})},
        ]
        with patch.object(worker, 'call_ollama', side_effect=responses) as model, \
                patch.object(worker, 'check_cancelled'):
            result = worker.repair_flagged_translation({'id': 9}, rows,
                      [flag(rows[0], category='meaning')])
        self.assertEqual(result[0]['sv'], 'foton är som affärer')
        self.assertEqual(model.call_count, 1)
        first = json.loads(model.call_args_list[0].args[0]['prompt'])
        self.assertIn('source', first['context'][0])

    def test_uncertain_source_is_not_repaired_by_guessing(self):
        with patch.object(worker, 'call_ollama') as model:
            with self.assertRaises(worker.SourceQualityError):
                worker.repair_flagged_translation({'id': 9}, self.rows,
                    [flag(self.rows[1], category='source_uncertain')])
        model.assert_not_called()

    def test_repair_cannot_return_an_extra_neighbor_id(self):
        answer = {'response': json.dumps({'rows': [
            {'id': 1, 'sv': 'Min berättelse.'}, {'id': 2, 'sv': 'Något annat.'}]})}
        with patch.object(worker, 'call_ollama', return_value=answer), \
                patch.object(worker, 'check_cancelled'):
            with self.assertRaises(RuntimeError):
                worker.repair_flagged_translation_once({'id': 9}, self.rows, [flag(self.rows[1])])
            self.assertEqual(worker.repair_flagged_translation({'id': 9}, self.rows, [flag(self.rows[1])]), self.rows, 'the wrapper leaves the row flagged instead')

    def run_saved_review(self, initial_flags, final_flags):
        responses = [
            {'response': json.dumps({'flags': initial_flags})},
            {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Det här är min berättelse.'}]})},
            {'response': json.dumps({'flags': final_flags})},
        ]
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return folder.name, responses

    def test_single_bad_row_is_repaired_then_full_candidate_is_reviewed_again(self):
        folder, responses = self.run_saved_review([flag(self.rows[1])], [])
        before = [dict(row) for row in self.rows]
        with patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', side_effect=responses) as model, \
                patch.object(worker, 'check_cancelled'):
            worker.review_and_save_translation({'id': 9}, self.rows)
        saved = json.loads((Path(folder) / 'job-9.json').read_text())
        self.assertEqual(model.call_count, 3)
        self.assertEqual(saved['original_rows'], before)
        self.assertEqual(saved['status'], 'usable')
        self.assertEqual(self.rows[1]['sv'], 'Det här är min berättelse.')
        self.assertEqual(saved['repair_attempts'], 1)

    def test_persisting_flag_after_repair_does_not_pass_even_for_one_row(self):
        final_row = {**self.rows[1], 'sv': 'Det här är min berättelse.'}
        folder, responses = self.run_saved_review([flag(self.rows[1])], [flag(final_row)])
        with patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', side_effect=responses), \
                patch.object(worker, 'check_cancelled'):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation({'id': 9}, self.rows)
        saved = json.loads((Path(folder) / 'job-9.json').read_text())
        self.assertEqual(saved['status'], 'unusable')
        self.assertEqual(self.rows[1]['sv'], 'Det här är mitt berättelse.')

    def test_meaning_repair_still_requires_review_without_grammar_rewrite(self):
        responses = [
            {'response': json.dumps({'flags': [flag(self.rows[1], category='meaning')]})},
            {'response': json.dumps({'rows': [{'id': 1, 'intended_meaning': 'This is my story.',
                         'edits': [{'before': 'mitt', 'after': 'min'}]}]})},
            {'response': json.dumps({'flags': [flag(
                {**self.rows[1], 'sv': 'Det här är min berättelse.'}, category='source_uncertain')]})},
        ]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', side_effect=responses), \
                patch.object(worker, 'check_cancelled'):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation({'id': 9}, self.rows)
            saved = json.loads((Path(folder) / 'job-9.json').read_text())
        self.assertEqual(saved['status'], 'unusable')
        self.assertEqual(saved['repair_attempts'], 1)
        self.assertEqual([s['stage'] for s in saved['repair_steps']], ['phrase'])
        self.assertEqual(saved['rows'][1]['sv'], 'Det här är min berättelse.')
        self.assertEqual(self.rows[1]['sv'], 'Det här är mitt berättelse.')

    def test_malformed_grammar_response_is_saved_before_parsing(self):
        responses = [
            {'response': json.dumps({'flags': [flag(self.rows[1])]})},
            {'response': 'not JSON'},
            {'response': json.dumps({'flags': [flag(self.rows[1])]})},       # the re-review: the row is still flagged, the gate refuses
        ]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', side_effect=responses), \
                patch.object(worker, 'check_cancelled'):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation({'id': 9}, self.rows)
            saved = json.loads((Path(folder) / 'job-9.json').read_text())
        self.assertEqual(saved['repair_steps'][0]['response'], 'not JSON')
        self.assertEqual(saved['repair_steps'][-1]['stage'], 'repair_failed')
        self.assertEqual(saved['status'], 'unusable')

    def test_unsafe_edit_is_discarded_per_row_without_losing_valid_agreement_fix(self):
        rows = [self.rows[1], {'id': 4, 'source': 'I am not leaving.', 'sv': 'Jag går inte.'}]
        response = {'response': json.dumps({'rows': [
            {'id': 1, 'sv': 'Det här är min berättelse.'}, {'id': 4, 'sv': 'Jag går.'}]})}
        events = []
        with patch.object(worker, 'call_ollama', return_value=response), patch.object(worker, 'check_cancelled'):
            result = worker.repair_target_grammar({'id': 9}, rows, {1, 4}, record=events.append)
        self.assertEqual(result[0]['sv'], 'Det här är min berättelse.')
        self.assertEqual(result[1], rows[1])
        self.assertEqual(events[-1]['proposed'], 'Jag går.')
        self.assertEqual(events[-1]['status'], 'rejected')

    def test_discarded_proposal_only_passes_after_new_clean_full_review(self):
        responses = [
            {'response': json.dumps({'flags': [flag(self.rows[0])]})},
            {'response': json.dumps({'rows': [{'id': 0, 'sv': 'Jag kommer aldrig tillbaka.'}]})},
            # The grammar edit was discarded (word count), so the repair that reads the remark gets the line too (0.8.323).
            {'response': json.dumps({'rows': [{'id': 0, 'sv': 'Jag kommer tillbaka.'}]})},
            {'response': '{"flags":[]}'},
        ]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', side_effect=responses) as model, \
                patch.object(worker, 'check_cancelled'):
            worker.review_and_save_translation({'id': 9}, self.rows)
            saved = json.loads((Path(folder) / 'job-9.json').read_text())
        self.assertEqual(model.call_count, 4)
        self.assertEqual(saved['status'], 'usable')
        self.assertEqual(self.rows[0]['sv'], 'Jag kommer tillbaka.')
        reviewed = json.loads(model.call_args.args[0]['prompt'])['rows']
        self.assertEqual(reviewed, self.rows, 'a short file is re-reviewed in full after a repair')


class UntranslatedQuoteTests(unittest.TestCase):
    def setUp(self):
        # Phrase scope tests isolate rendering; its model contract has separate tests.
        rendering = patch.object(worker, 'render_whole_cue_meanings',
            side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)

    """Real job 17: the reviewer correctly saw that `gun` stayed untranslated but quoted the
    whole line as source_quote, which the validator rejects -- twice, because the retry never
    said which rule was broken. The validator stays exactly as strict; only the guidance changed."""

    ROWS = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Stay tuned.', 'sv': 'Håll utkik.'},
            {'id': 1, 'start': 2, 'end': 3, 'source': 'Like you, gun.', 'sv': 'Liksom du, gun.'}]
    WHOLE_LINE = {'id': 1, 'category': 'untranslated', 'source_quote': 'Like you, gun.',
                  'target_quote': 'Liksom du, gun.', 'reason': "Ordet 'gun' är inte översatt till svenska."}
    WORD_ONLY = {**WHOLE_LINE, 'source_quote': 'gun'}

    @staticmethod
    def answer(*flags):
        return {'response': json.dumps({'flags': list(flags)})}

    def test_the_validator_is_exactly_as_strict_as_before(self):
        with self.assertRaisesRegex(ValueError, 'oöversatta källord'):
            worker.validate_translation_flags({'flags': [self.WHOLE_LINE]}, self.ROWS, 'sv')
        self.assertEqual(worker.validate_translation_flags({'flags': [self.WORD_ONLY]}, self.ROWS, 'sv'),
                         [self.WORD_ONLY])
        for invented in ({'source_quote': 'gun', 'target_quote': 'Liksom du'},   # word not in target quote
                         {'source_quote': 'tuned'}):                              # word not in that row's source
            with self.assertRaises(ValueError):
                worker.validate_translation_flags({'flags': [{**self.WORD_ONLY, **invented}]}, self.ROWS, 'sv')

    def test_the_first_question_states_the_quoting_rules(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer()) as model:
            worker.assess_translation_quality(self.ROWS)
        properties = model.call_args_list[0].args[0]['format']['properties']['flags']['items']['properties']
        self.assertIn('ONLY the word(s) left untranslated', properties['source_quote']['description'])
        self.assertIn('same source word(s) unchanged', properties['target_quote']['description'])

    def test_the_retry_names_the_exact_rule_that_was_broken_and_then_accepts_a_correct_flag(self):
        audit = []
        with patch.object(worker, 'call_ollama',
                          side_effect=[self.answer(self.WHOLE_LINE), self.answer(self.WORD_ONLY)]) as model:
            self.assertTrue(worker.assess_translation_quality(self.ROWS, audit=audit))
        self.assertEqual(model.call_count, 2)
        first, second = (call.args[0]['system'] for call in model.call_args_list)
        self.assertNotIn('The validator rejected', first)
        self.assertIn('The validator rejected your previous answer with: "Påstådda oöversatta källord', second)
        self.assertIn('ONLY the word(s) left untranslated', second)
        self.assertIn('Keep every flag that is a genuine defect', second)
        self.assertEqual(audit[0]['status'], 'reviewer_error')
        self.assertEqual(audit[1]['bad_ids'], [1])  # the defect is still reported, not hidden

    def test_a_reviewer_that_keeps_quoting_wrongly_still_stops_the_job(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer(self.WHOLE_LINE)) as model:
            with self.assertRaises(RuntimeError) as raised:
                worker.assess_translation_quality(self.ROWS)
        self.assertNotIsInstance(raised.exception, worker.SourceQualityError)
        self.assertEqual(model.call_count, 4)  # two tries for the pair, then two for its first cue; nothing approved

    def test_the_rejection_message_does_not_leak_into_the_next_block(self):
        rows = [{'id': n, 'start': n, 'end': n + .5, 'source': f'Line {n}.', 'sv': f'Rad {n}.'}
                for n in range(40)]
        with patch.object(worker, 'call_ollama', side_effect=[
                self.answer({**self.WORD_ONLY, 'id': 0, 'source_quote': 'Line 0.'}),  # not untranslated: fine
                self.answer()] + [self.answer()] * 20) as model:
            try:
                worker.assess_translation_quality(rows)
            except (RuntimeError, worker.SourceQualityError):
                pass
        calls = model.call_args_list
        self.assertNotIn('The validator rejected', calls[0].args[0]['system'])   # first try of block 1
        self.assertIn('The validator rejected', calls[1].args[0]['system'])      # its retry
        for call in calls[2:]:                                                   # every later block starts clean
            self.assertNotIn('The validator rejected', call.args[0]['system'])


class RepairWordingTests(unittest.TestCase):
    def setUp(self):
        # Phrase scope tests isolate rendering; its model contract has separate tests.
        rendering = patch.object(worker, 'render_whole_cue_meanings',
            side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)

    """Real job 17: the reviewer's reason said '(håll med tittning/lyssning)' and the repair copied
    that calque, which the next review flagged again."""

    ROWS = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Will you be making a statement?',
             'sv': 'Kommer du att göra ett uttalande?'},
            {'id': 1, 'start': 2, 'end': 3, 'source': 'Stay tuned.', 'sv': 'Stanna kvar.'}]
    FLAG = {'id': 1, 'category': 'meaning', 'source_quote': 'Stay tuned.', 'target_quote': 'Stanna kvar.',
            'reason': "'Stay tuned' (håll med tittning/lyssning) har översatts till 'Stanna kvar'."}

    def repair(self):
        response = {'response': json.dumps({'rows': [{'id': 1, 'intended_meaning': 'Continue following.',
                    'edits': [{'before': 'Stanna kvar.', 'after': 'Vänta och se.'}]}]})}
        with patch.object(worker, 'call_ollama', return_value=response) as model, \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'repair_target_grammar', side_effect=lambda job, rows, *a, **k: rows):
            result = worker.repair_flagged_translation({'id': 9}, self.ROWS, [self.FLAG])
        return model.call_args.args[0], result

    def test_the_repair_is_told_the_reason_is_not_a_translation(self):
        request, _ = self.repair()
        self.assertIn('never authoritative translations', request['system'])
        self.assertIn('intended_meaning', request['system'])
        self.assertIn('intended action', request['system'])
        self.assertEqual(request['model'], worker.REVIEW_MODEL_NAME)

    def test_current_wording_and_fallible_flags_are_passed_as_data(self):
        request, result = self.repair()
        prompt = json.loads(request['prompt'])
        self.assertEqual(prompt['flags'], [{key: value for key, value in self.FLAG.items()
                                           if key != 'reason'}])
        self.assertEqual(prompt['context'][1]['sv'], 'Stanna kvar.')
        self.assertIn('Use edits [] if the target is correct', request['system'])
        self.assertEqual(result[1]['sv'], 'Vänta och se.')
        self.assertEqual(result[0], self.ROWS[0])  # neighbours untouched, as before


if __name__ == '__main__':
    unittest.main()
