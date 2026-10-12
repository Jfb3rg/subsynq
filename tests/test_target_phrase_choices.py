import copy
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class TargetPhraseChoiceTests(unittest.TestCase):
    def setUp(self):
        # Phrase scope tests isolate rendering; its model contract has separate tests.
        rendering = patch.object(worker, 'render_whole_cue_meanings',
            side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)
        self.rows = [{'id': 8, 'source': 'Stay tuned.', 'sv': 'Stanna kvar.'},
                     {'id': 9, 'source': 'This is business.', 'sv': 'Det här är affärer.'}]
        self.flags = [{'id': 8, 'category': 'meaning', 'source_quote': 'Stay tuned.',
                       'target_quote': 'Stanna kvar.', 'reason': 'Wrong action.'},
                      {'id': 9, 'category': 'meaning', 'source_quote': 'business',
                       'target_quote': 'affärer.', 'reason': 'Wrong word.'}]

    def test_choices_are_unique_whole_target_words_inside_the_quote(self):
        choices = worker.editable_target_phrases(self.rows[0], self.flags)
        self.assertIn('Stanna kvar.', choices)
        self.assertIn('kvar', choices)
        self.assertNotIn('Stay tuned.', choices)
        self.assertNotIn('anna', choices)
        for phrase in choices:
            worker.apply_translation_patches(self.rows, self.flags, [
                {'id': 8, 'edits': [{'before': phrase, 'after': 'nytt'}]},
                {'id': 9, 'edits': []}])

    def test_ambiguous_and_stale_quotes_are_rejected_before_model_call(self):
        for target in ['Stanna kvar. Stanna kvar.', 'Vänta och se.']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                worker.editable_target_phrases({**self.rows[0], 'sv': target}, self.flags)

    def test_response_schema_and_prompt_exclude_english_edit_anchors(self):
        def model(request):
            allowed = request['format']['properties']['rows']['items']['properties']['edits']['items']['properties']['before']['enum']
            self.assertNotIn('Stay tuned.', allowed)
            self.assertIn('Stanna kvar.', allowed)
            data = json.loads(request['prompt'])
            self.assertEqual(data['editable_targets'][0]['current_target'], 'Stanna kvar.')
            return {'response': json.dumps({'rows': [
                {'id': 8, 'intended_meaning': 'Keep following.',
                 'edits': [{'before': 'Stanna kvar.', 'after': 'Vänta och se.'}]},
                {'id': 9, 'intended_meaning': 'Business.', 'edits': []}]})}
        original = copy.deepcopy(self.rows)
        with patch.object(worker, 'call_ollama', side_effect=model), patch.object(worker, 'check_cancelled'):
            result = worker.repair_translation_spans({'id': -1}, self.rows, self.flags)
        self.assertEqual(result[0]['sv'], 'Vänta och se.')
        self.assertEqual(result[1], original[1])
        self.assertEqual(self.rows, original)

    def test_wrong_row_anchor_is_rejected_even_if_model_ignores_schema(self):
        answer = {'rows': [{'id': 8, 'intended_meaning': 'Keep following.',
                            'edits': [{'before': 'affärer.', 'after': 'nytt'}]},
                           {'id': 9, 'intended_meaning': 'Business.', 'edits': []}]}
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama',
                return_value={'response': json.dumps(answer)}) as model:
            with self.assertRaises(RuntimeError):
                worker.repair_translation_spans({'id': -1}, self.rows, self.flags)
        self.assertEqual(model.call_count, 2)
