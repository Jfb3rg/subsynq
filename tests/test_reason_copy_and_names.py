import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


REASON = ("Uttrycket 'Stay tuned' (håll med tittning/lyssning) har översatts till 'Stanna kvar' "
          "(stanna kvar på platsen), vilket ändrar innebörden.")


class ReasonCopyTests(unittest.TestCase):
    """Real jobs 17 and 21: the repair copied "(håll med tittning/lyssning)" from the reviewer's remark."""

    def test_wording_copied_from_the_remark_is_found(self):
        self.assertEqual(worker.reason_copy('Håll med tittning.', [REASON], ['Stay tuned.', 'Stanna kvar.']),
                         'håll med tittning')

    def test_a_run_already_in_the_source_or_refused_target_is_not_a_copy(self):
        self.assertIsNone(worker.reason_copy('Stanna kvar på platsen.', [REASON], ['Stay tuned.', 'Stanna kvar på platsen.']))

    def test_short_overlaps_and_unrelated_text_are_not_copies(self):
        self.assertIsNone(worker.reason_copy('Håll utkik.', [REASON], ['Stay tuned.']))
        self.assertIsNone(worker.reason_copy('Fortsätt titta, vi är strax tillbaka.', [REASON], []))
        self.assertIsNone(worker.reason_copy('Håll med tittning.', [], []))


class RepairCopiesTheRemarkTests(unittest.TestCase):
    def setUp(self):
        # Phrase scope tests isolate rendering; its model contract has separate tests.
        rendering = patch.object(worker, 'render_whole_cue_meanings',
            side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)

    ROWS = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Will you be making a statement?',
             'sv': 'Kommer du att göra ett uttalande?'},
            {'id': 1, 'start': 2, 'end': 3, 'source': 'Stay tuned.', 'sv': 'Stanna kvar.'}]
    FLAG = {'id': 1, 'category': 'meaning', 'source_quote': 'Stay tuned.', 'target_quote': 'Stanna kvar.',
            'reason': REASON}

    @staticmethod
    def answer(text, before='Stanna kvar.', intended_meaning='Continue paying attention.'):
        return {'response': json.dumps({'rows': [{'id': 1, 'intended_meaning': intended_meaning,
                'edits': [{'before': before, 'after': text}]}]})}

    def repair(self, *answers):
        with patch.object(worker, 'call_ollama', side_effect=list(answers)) as model, \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'repair_target_grammar', side_effect=lambda job, rows, *a, **k: rows):
            return model, worker.repair_flagged_translation({'id': 9}, self.ROWS, [self.FLAG])

    def test_idiom_repair_explains_intent_instead_of_using_remark_as_a_translation(self):
        model, result = self.repair(self.answer('Vänta och se.'))
        self.assertEqual(model.call_count, 1)
        request = model.call_args.args[0]
        self.assertIn('never authoritative translations', request['system'])
        self.assertIn('intended_meaning', request['system'])
        self.assertEqual(request['model'], worker.REVIEW_MODEL_NAME)
        self.assertNotIn('reason', json.loads(request['prompt'])['flags'][0])
        self.assertEqual(result[1]['sv'], 'Vänta och se.')
        self.assertEqual(result[0], self.ROWS[0])

    def test_a_clean_first_answer_costs_no_extra_call(self):
        model, result = self.repair(self.answer('Häng kvar.'))
        self.assertEqual(model.call_count, 1)
        self.assertNotIn('forbidden_phrases', json.loads(model.call_args.args[0]['prompt']))

    def test_an_unfounded_flag_may_leave_the_correct_target_unchanged(self):
        answer = {'response': json.dumps({'rows': [{'id': 1, 'intended_meaning': 'Continue following.',
                                                   'edits': []}]})}
        model, result = self.repair(answer)
        self.assertEqual(result, self.ROWS)
        self.assertEqual(model.call_count, 1)

    def test_a_correct_fix_the_reviewer_suggested_is_not_lost(self):
        flag = {'id': 1, 'category': 'meaning', 'source_quote': 'photos are like this business',
                'target_quote': 'foton är som det här',
                'reason': "Översättningen 'det här' missar ordet 'business'. Det borde vara 'det här företaget' "
                          "eller 'den här branschen'."}
        rows = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Hi.', 'sv': 'Hej.'},
                {'id': 1, 'start': 2, 'end': 3, 'source': 'photos are like this business',
                 'sv': 'foton är som det här'}]
        answer = self.answer('den här branschen', before='det här',
                             intended_meaning='Photos are compared to this business.')
        with patch.object(worker, 'call_ollama', return_value=answer), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'repair_target_grammar', side_effect=lambda job, r, *a, **k: r):
            result = worker.repair_flagged_translation({'id': 9}, rows, [flag])
        self.assertEqual(result[1]['sv'], 'foton är som den här branschen')

    def test_the_grammar_repair_keeps_the_original_when_it_copies_the_remark(self):
        rows = [{'id': 1, 'source': 'Stay tuned.', 'sv': 'Stanna kvar.'}]
        flag = {'id': 1, 'category': 'grammar', 'source_quote': 'Stay tuned.', 'target_quote': 'Stanna kvar.',
                'reason': REASON}
        with patch.object(worker, 'call_ollama', return_value={
                'response': json.dumps({'rows': [{'id': 1, 'sv': 'Håll med tittning'}]})}), \
                patch.object(worker, 'check_cancelled'):
            result = worker.repair_target_grammar({'id': 9}, rows, {1}, flags=[flag])
        self.assertEqual(result[0]['sv'], 'Stanna kvar.')


class NameSpellingTests(unittest.TestCase):
    """Real job 21: the file writes "Gunn, antar jag?" but the line became "Liksom du, gun."."""

    @staticmethod
    def rows():
        return [{'id': 0, 'start': 0, 'end': 1, 'source': 'Mr.', 'sv': 'Herr.'},
                {'id': 1, 'start': 1, 'end': 2, 'source': 'Gunn, I presume?', 'sv': 'Gunn, antar jag?'},
                {'id': 2, 'start': 3, 'end': 4, 'source': 'Like you, gun.', 'sv': 'Liksom du, gun.'}]

    def test_the_file_s_own_spelling_replaces_the_doubled_letter_variant(self):
        rows = self.rows()
        changes = worker.apply_name_spellings(rows)
        self.assertEqual((rows[2]['source'], rows[2]['sv']), ('Like you, Gunn.', 'Liksom du, Gunn.'))
        self.assertEqual((changes[0]['from'], changes[0]['to']), ('gun', 'Gunn'))
        self.assertEqual(rows[1], self.rows()[1])      # other rows untouched

    def test_a_word_translated_away_on_another_row_is_an_ordinary_word(self):
        rows = self.rows()
        rows.append({'id': 3, 'start': 5, 'end': 6, 'source': 'He has a gun.', 'sv': 'Han har en pistol.'})
        before = [dict(row) for row in rows]
        self.assertEqual(worker.apply_name_spellings(rows), [])
        self.assertEqual(rows, before)

    def test_nothing_changes_when_the_translation_dropped_the_word(self):
        rows = self.rows()
        rows[2]['sv'] = 'Liksom du, pistol.'
        self.assertEqual(worker.apply_name_spellings(rows), [])
        self.assertEqual(rows[2]['source'], 'Like you, gun.')

    def test_an_ambiguous_name_is_left_alone(self):
        rows = self.rows()
        rows.append({'id': 3, 'start': 7, 'end': 8, 'source': 'Say hi to Gun.', 'sv': 'Säg hej till Gun.'})
        self.assertEqual(worker.apply_name_spellings(rows), [])

    def test_the_same_letters_are_not_rewritten(self):
        rows = self.rows()
        rows[2].update(source='Like you, gunn.', sv='Liksom du, gunn.')
        self.assertEqual(worker.apply_name_spellings(rows), [])

    def test_a_file_that_is_only_copied_is_never_touched(self):
        rows = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Matt kom hem.', 'sv': 'Matt kom hem.'},
                {'id': 1, 'start': 1, 'end': 2, 'source': 'Vi åt mat.', 'sv': 'Vi åt mat.'}]
        self.assertEqual(worker.apply_name_spellings(rows), [])
        self.assertEqual(rows[1]['source'], 'Vi åt mat.')

    def test_a_common_word_is_never_turned_into_a_name(self):
        rows = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Ann is here.', 'sv': 'Ann är här.'},
                {'id': 1, 'start': 1, 'end': 2, 'source': 'I saw an owl.', 'sv': 'Jag såg en uggla.'}]
        self.assertEqual(worker.apply_name_spellings(rows), [])
        self.assertEqual(rows[1]['source'], 'I saw an owl.')

    def test_the_review_writes_the_spelling_and_reports_it_before_anyone_reviews(self):
        rows = self.rows()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', return_value={'response': json.dumps({'flags': []})}) as model:
            worker.review_and_save_translation({'id': 77}, rows)
            report = json.loads((Path(folder) / 'job-77.json').read_text(encoding='utf-8'))
        self.assertEqual(rows[2]['sv'], 'Liksom du, Gunn.')
        self.assertEqual(report['status'], 'usable')
        self.assertEqual(report['name_spellings'][0]['to'], 'Gunn')
        reviewed = json.loads(model.call_args.args[0]['prompt'])['rows']
        self.assertEqual(reviewed[2]['sv'], 'Liksom du, Gunn.')     # the reviewer sees the corrected line


if __name__ == '__main__':
    unittest.main()
