import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
import worker


def rows_with_alternatives():
    return [
        {'id': 0, 'start': 97.3, 'end': 98.7, 'source': 'War is a monster.'},
        {'id': 1, 'start': 100.2, 'end': 104.8, 'source': 'way you make it your life all right lights on me this',
         'alternatives': [{'text': 'The way you make it your life. All right, lights on me.', 'support': 3,
                           'current_support': 2}]},
        {'id': 2, 'start': 115.2, 'end': 116.6, 'source': 'This is about to get ugly.'}]


def llm(answer):
    return Mock(side_effect=lambda request: {'response': json.dumps(answer)})


class ChooseBetweenReadingsTests(unittest.TestCase):
    def run_step(self, call, rows=None):
        with patch.object(worker, 'call_ollama', call), patch.object(worker, 'update_job') as update, \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'active_gemma_models',
                                                                      return_value=('gemma3:27b', 'gemma4:31b')):
            result = worker.choose_between_readings({'id': 49}, rows or rows_with_alternatives())
        return result, update

    def test_the_model_picks_the_better_reading_and_the_decision_is_kept(self):
        call = llm({'rows': [{'id': 1, 'a_has_error': True, 'choice': 'B'}]})
        (rows, decisions), update = self.run_step(call)
        self.assertEqual(rows[1]['source'], 'The way you make it your life. All right, lights on me.')
        self.assertTrue(all('alternatives' not in row for row in rows))
        self.assertEqual([(d['id'], d['applied']) for d in decisions], [(1, True)])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0]['model'], worker.MODEL_NAME)
        self.assertIn('1 osäkra repliker', update.call_args.args[3])
        self.assertEqual(update.call_args.kwargs['model'], 'Källval med gemma3:27b')

    def test_without_doubtful_rows_the_model_is_never_called(self):
        call = Mock()
        plain = [{'id': 0, 'start': 0.0, 'end': 1.0, 'source': 'Hello there.'}]
        (rows, decisions), update = self.run_step(call, plain)
        self.assertEqual((rows, decisions), (plain, []))
        call.assert_not_called()
        update.assert_not_called()

    def test_a_model_that_is_unavailable_leaves_every_row_as_it_was(self):
        (rows, decisions), _ = self.run_step(Mock(side_effect=RuntimeError('gone')))
        self.assertEqual(rows[1]['source'], 'way you make it your life all right lights on me this')
        self.assertTrue(all('alternatives' not in row for row in rows))
        self.assertEqual(decisions, [])

    def test_a_cancelled_job_is_not_swallowed(self):
        with self.assertRaises(worker.JobCancelled):
            self.run_step(Mock(side_effect=worker.JobCancelled('stop')))


class SourceTranscriptTests(unittest.TestCase):
    def test_the_choices_are_saved_with_the_reviewed_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            rows = [{'id': 0, 'start': 0.0, 'end': 1.0, 'source': 'Hello.', 'alternatives': [{'text': 'x'}]}]
            choices = [{'id': 0, 'choice': 'B', 'applied': True}]
            target = worker.save_source_transcript({'id': 49}, Path('clip.mp4'), rows, 'cpu', 'm', source_choices=choices)
            payload = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(payload['source_choices'], choices)
            self.assertEqual(payload['rows'], [{'id': 0, 'start': 0.0, 'end': 1.0, 'source': 'Hello.'}])
            raw = worker.save_source_transcript({'id': 49}, Path('clip.mp4'), rows, 'cpu', 'm', raw=True)
            self.assertEqual(json.loads(raw.read_text(encoding='utf-8'))['source_choices'], [])


if __name__ == '__main__':
    unittest.main()
