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
