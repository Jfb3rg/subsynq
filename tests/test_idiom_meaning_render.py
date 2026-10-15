import copy
import json
import sys
import unittest
from unittest.mock import patch
sys.path[:0] = ['/ai', '/app']
import worker


class MeaningRenderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'id': 7, 'source': 'Stay tuned.', 'sv': 'Stanna kvar.'},
                     {'id': 8, 'source': 'Thank you.', 'sv': 'Tack.'}]
        self.flags = [{'id': 7, 'category': 'meaning', 'source_quote': 'Stay tuned.',
                       'target_quote': 'Stanna kvar.'}]
        self.answer = [{'id': 7, 'intended_meaning': 'Keep watching for what happens next.',
                        'edits': [{'before': 'Stanna kvar.', 'after': 'Stanna kvar.'}]}]

    def test_separate_render_uses_meaning_and_preserves_scope_and_model_roles(self):
        first = {'response': json.dumps({'rows': self.answer})}
        rendered = {'response': json.dumps({'rows': [{'id': 7, 'after': 'Fortsätt titta.'}]})}
        original = copy.deepcopy(self.rows)
        flags = [{**self.flags[0], 'reason': 'Changed action.'}]
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                side_effect=[first, rendered]) as model:
            result = worker.repair_translation_spans({'id': -1}, self.rows, flags)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0].args[0]['model'], worker.REVIEW_MODEL_NAME)
        request = model.call_args_list[1].args[0]
        self.assertEqual(request['model'], worker.MODEL_NAME)
        # The original line is given as source so that a fixed expression gets its fixed counterpart (probed on
        # real idioms: without it the renderer restated the description, e.g. two phrases joined by a comma).
        self.assertEqual(json.loads(request['prompt'])['rows'][0]['source'], 'Stay tuned.')
        self.assertIn('ONE natural fixed Swedish expression', request['system'])
        self.assertIn('never list two ways', request['system'])
        self.assertNotIn('Stanna kvar', request['prompt'])
        self.assertEqual(result[0]['sv'], 'Fortsätt titta.')
        self.assertEqual(result[1], original[1])
        self.assertEqual(self.rows, original)

    def test_renderer_cannot_add_omit_duplicate_or_change_requested_ids(self):
        invalid = [[], [{'id': 8, 'after': 'Fel.'}],
                   [{'id': 7, 'after': 'Text.'}, {'id': 7, 'after': 'Text.'}],
                   [{'id': True, 'after': 'Text.'}], [{'id': 7, 'after': ''}], [{}]]
        for rows in invalid:
            with self.subTest(rows=rows), patch.object(worker, 'call_ollama',
                    return_value={'response': json.dumps({'rows': rows})}), self.assertRaises(ValueError):
                worker.render_whole_cue_meanings({'id': -1}, self.rows, self.flags, self.answer, 'sv', 'Swedish')

    def test_empty_edits_and_partial_quotes_do_not_force_rephrasing(self):
        with patch.object(worker, 'call_ollama') as model:
            answer = [{**self.answer[0], 'edits': []}]
            self.assertEqual(worker.render_whole_cue_meanings({'id': -1}, self.rows, self.flags,
                             answer, 'sv', 'Swedish'), answer)
            flags = [{**self.flags[0], 'target_quote': 'kvar.'}]
            self.assertEqual(worker.render_whole_cue_meanings({'id': -1}, self.rows, flags,
                             self.answer, 'sv', 'Swedish'), self.answer)
        model.assert_not_called()

    def test_a_render_that_only_repeats_the_flagged_line_keeps_the_repairs_own_edit(self):
        """Real job 48 (dev, RTX 5090): "Meine nicht." -> the repair proposed "Inte min." but the renderer answered
        "Inte jag." again, so nothing changed and the same finding stopped the file twice."""
        rows = [{'id': 10, 'source': 'Meine nicht.', 'sv': 'Inte jag.'}]
        flags = [{'id': 10, 'category': 'meaning', 'source_quote': 'Meine nicht.', 'target_quote': 'Inte jag.'}]
        answer = [{'id': 10, 'intended_meaning': 'The speaker says theirs is not the one mentioned.',
                   'edits': [{'before': 'Inte jag.', 'after': 'Inte min.'}]}]
        repeated = {'response': json.dumps({'rows': [{'id': 10, 'after': 'Inte jag.'}]})}
        with patch.object(worker, 'call_ollama', return_value=repeated):
            result = worker.render_whole_cue_meanings({'id': -1}, rows, flags, answer, 'sv', 'Swedish')
        self.assertEqual(result[0]['edits'], [{'before': 'Inte jag.', 'after': 'Inte min.'}])

    def test_the_real_job_48_repair_now_changes_the_line(self):
        """The phase and render answers are the ones stored in dev job 48's quality report."""
        rows = [{'id': 9, 'source': 'Ich brauche jetzt einen Weg, ihn zu verteidigen.', 'sv': 'Jag behöver nu ett sätt att försvara honom.'},
                {'id': 10, 'source': 'Meine nicht.', 'sv': 'Inte jag.'},
                {'id': 11, 'source': 'Ich und ihr Vater, äh, wir sind seit Wartes zusammen in den Teams.',
                 'sv': 'Jag och hennes far, eh, vi har varit i team tillsammans sedan Wartes.'}]
        flags = [{'id': 10, 'category': 'meaning', 'source_quote': 'Meine nicht.', 'target_quote': 'Inte jag.',
                  'reason': "Källtexten 'Meine nicht' syftar på ägande (mina inte), medan måltexten 'Inte jag' syftar på personen själv."}]
        phase = {'response': json.dumps({'rows': [{'id': 10, 'intended_meaning': (
            'The speaker is referring to their own opinion or perspective, stating that theirs is not the same as the '
            'previously mentioned one (likely referring to the need for a way to defend someone).'),
            'edits': [{'before': 'Inte jag.', 'after': 'Inte min.'}]}]})}
        render = {'response': json.dumps({'rows': [{'id': 10, 'after': 'Inte jag.'}]})}
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama', side_effect=[phase, render]):
            result = worker.repair_translation_spans({'id': -1}, rows, flags)
        self.assertEqual([row['sv'] for row in result if row['id'] == 10], ['Inte min.'])
        self.assertEqual([row['sv'] for row in result if row['id'] != 10], [rows[0]['sv'], rows[2]['sv']])

    def test_a_render_that_changes_the_line_still_wins_over_the_proposal(self):
        rows = [{'id': 10, 'source': 'Meine nicht.', 'sv': 'Inte jag.'}]
        flags = [{'id': 10, 'category': 'meaning', 'source_quote': 'Meine nicht.', 'target_quote': 'Inte jag.'}]
        answer = [{'id': 10, 'intended_meaning': 'Not mine.', 'edits': [{'before': 'Inte jag.', 'after': 'Inte min.'}]}]
        changed = {'response': json.dumps({'rows': [{'id': 10, 'after': 'Inte mitt.'}]})}
        with patch.object(worker, 'call_ollama', return_value=changed):
            result = worker.render_whole_cue_meanings({'id': -1}, rows, flags, answer, 'sv', 'Swedish')
        self.assertEqual(result[0]['edits'], [{'before': 'Inte jag.', 'after': 'Inte mitt.'}])

    def test_a_placeholder_edit_that_changes_nothing_does_not_replace_an_unchanged_render(self):
        """The idiom path proposes edits whose after equals before on purpose (the renderer writes the line)."""
        repeated = {'response': json.dumps({'rows': [{'id': 7, 'after': 'Stanna kvar.'}]})}
        with patch.object(worker, 'call_ollama', return_value=repeated):
            result = worker.render_whole_cue_meanings({'id': -1}, self.rows, self.flags, self.answer, 'sv', 'Swedish')
        self.assertEqual(result[0]['edits'], [{'before': 'Stanna kvar.', 'after': 'Stanna kvar.'}])
