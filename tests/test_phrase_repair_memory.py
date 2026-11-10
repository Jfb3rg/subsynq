import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


SOURCE = "It's a very staid device."


def row(text):
    return [{'id': 4, 'source': 'Look at it.', 'sv': 'Titta på den.'}, {'id': 5, 'source': SOURCE, 'sv': text},
            {'id': 6, 'source': 'Could be both.', 'sv': 'Kan vara båda.'}]


def flag(word, row_id=5):
    return {'id': row_id, 'category': 'meaning', 'source_quote': 'staid', 'target_quote': word, 'reason': 'Fel ord.'}


def edit(before, after, row_id=5):
    return {'id': row_id, 'intended_meaning': 'A sedate device.', 'edits': [{'before': before, 'after': after}]}


class PhraseRepairMemoryTests(unittest.TestCase):
    """The phrase repair remembers every wording refused for a row in the job (regression case: one word went back and forth between two
    refused wordings for five rounds and the file was refused)."""

    def setUp(self):
        rendering = patch.object(worker, 'render_whole_cue_meanings', side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)
        cancelled = patch.object(worker, 'check_cancelled')
        cancelled.start()
        self.addCleanup(cancelled.stop)
        self.requests = []

    def model(self, *answers):
        replies = iter(answers)

        def call(request):
            self.requests.append(request)
            return {'response': json.dumps({'rows': [next(replies)]})}
        return patch.object(worker, 'call_ollama', side_effect=call)

    def round(self, job, text, word, *answers, **kwargs):
        with self.model(*answers):
            return worker.repair_translation_spans(job, row(text), [flag(word)], **kwargs)[1]['sv']

    def test_a_row_flagged_for_the_first_time_gets_the_prompt_it_always_got(self):
        self.round({'id': -1}, 'Det är en väldigt stabil apparat.', 'stabil', edit('stabil', 'samlad'))
        target = json.loads(self.requests[0]['prompt'])['editable_targets'][0]
        self.assertEqual(sorted(target), ['allowed_before', 'current_target', 'id'])
        self.assertNotIn('rejected_targets', self.requests[0]['system'])

    def test_an_edit_back_to_a_refused_wording_is_asked_again_with_the_whole_history(self):
        job = {'id': -1}
        self.assertEqual(self.round(job, 'Det är en väldigt stabil apparat.', 'stabil', edit('stabil', 'samlad')), 'Det är en väldigt samlad apparat.')
        self.assertEqual(self.round(job, 'Det är en väldigt samlad apparat.', 'samlad', edit('samlad', 'stram')), 'Det är en väldigt stram apparat.')
        self.requests.clear()
        steps = []
        result = self.round(job, 'Det är en väldigt stram apparat.', 'stram', edit('stram', 'samlad'), edit('stram', 'sober'), record=steps.append)
        self.assertEqual(result, 'Det är en väldigt sober apparat.')
        first, second = (json.loads(request['prompt']) for request in self.requests)
        self.assertEqual(first['editable_targets'][0]['rejected_targets'],
                         ['Det är en väldigt stabil apparat.', 'Det är en väldigt samlad apparat.', 'Det är en väldigt stram apparat.'])
        self.assertIn('rejected_targets', self.requests[0]['system'])
        self.assertIn('redan har nekats för rad 5', second['rejected_patch'])
        self.assertEqual([step['stage'] for step in steps], ['phrase', 'phrase_guard', 'phrase'])

    def test_a_second_refused_wording_keeps_the_current_text_and_reports_the_row(self):
        job = {'id': -1}
        self.round(job, 'Det är en väldigt samlad apparat.', 'samlad', edit('samlad', 'stram'))
        steps, unresolved = [], set()
        result = self.round(job, 'Det är en väldigt stram apparat.', 'stram', edit('stram', 'samlad'), edit('stram', 'Samlad'),
                            record=steps.append, unresolved=unresolved)
        self.assertEqual(result, 'Det är en väldigt stram apparat.')
        self.assertEqual(unresolved, {5})
        self.assertEqual(steps[-1]['stage'], 'refused_again')
        self.assertEqual(steps[-1]['ids'], [5])

    def test_keeping_the_line_unchanged_is_not_a_return_to_a_refused_wording(self):
        job = {'id': -1}
        self.round(job, 'Det är en väldigt samlad apparat.', 'samlad', edit('samlad', 'stram'))
        self.requests.clear()
        result = self.round(job, 'Det är en väldigt stram apparat.', 'stram', {'id': 5, 'intended_meaning': 'A sedate device.', 'edits': []})
        self.assertEqual(result, 'Det är en väldigt stram apparat.')
        self.assertEqual(len(self.requests), 1)

    def test_the_unresolved_row_is_rewritten_as_a_whole_row_then_and_in_later_rounds(self):
        job = {'id': -1}
        self.round(job, 'Det är en väldigt samlad apparat.', 'samlad', edit('samlad', 'stram'))
        rows, flags = row('Det är en väldigt stram apparat.'), [flag('stram')]
        calls, histories = [], []

        def whole_row(request):
            data = json.loads(request['prompt'])
            calls.append('phrase' if 'editable_targets' in data else 'row')
            if 'editable_targets' in data:
                return {'response': json.dumps({'rows': [edit('stram', 'samlad')]})}
            histories.append(data['rejected_targets']['5'])
            return {'response': json.dumps({'rows': [{'id': 5, 'sv': 'Det är en väldigt sober apparat.' if len(histories) == 1 else 'Det är en väldigt sansad apparat.'}]})}

        with patch.object(worker, 'call_ollama', side_effect=whole_row):
            result = worker.repair_flagged_translation_once(job, rows, flags)
            self.assertEqual(result[1]['sv'], 'Det är en väldigt sober apparat.')
            self.assertEqual(calls, ['phrase', 'phrase', 'row'])
            self.assertEqual(histories, [['Det är en väldigt samlad apparat.', 'Det är en väldigt stram apparat.']])
            self.assertEqual(job['_phrase_unresolved'], ['sv:5'])
            calls.clear()
            worker.repair_flagged_translation_once(job, row('Det är en väldigt sober apparat.'), [flag('sober')])
            self.assertEqual(calls, ['row'])


if __name__ == '__main__':
    unittest.main()
