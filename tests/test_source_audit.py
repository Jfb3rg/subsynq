import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'id': index, 'source': text} for index, text in enumerate(
            ['मैं घर जा रहा हूँ।', 'वह कल आएगा।', 'यह मेरी कहानी है।'])]

    def answer(self, bad_ids=(), mismatch=False):
        return {'response': json.dumps({'bad_ids': list(bad_ids),
            'language_mismatch': mismatch, 'reason': 'Konkreta källfynd.'})}

    def test_non_latin_script_does_not_bypass_source_audit(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer()) as model:
            self.assertTrue(worker.assess_source_quality(self.rows, 'Example trailer.mp4'))
        model.assert_called_once()

    def test_pervasive_findings_in_non_latin_source_still_stop_writing(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer([0, 2])):
            with self.assertRaises(worker.SourceQualityError):
                worker.assess_source_quality(self.rows)

    def test_unlabelled_filename_cannot_generate_a_language_requirement(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer()) as model:
            worker._audit_source_chunk(self.rows, 'Example trailer.mp4')
        request = model.call_args.args[0]
        self.assertEqual(request['format']['properties']['language_mismatch']['enum'], [False])
        self.assertIsNone(json.loads(request['prompt'])['filename_language_hint'])

    def test_explicit_language_marker_keeps_real_mismatch_detection(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer(mismatch=True)) as model:
            self.assertTrue(worker._audit_source_chunk(self.rows, 'Example tyska.mp4')[1])
        request = model.call_args.args[0]
        self.assertNotIn('enum', request['format']['properties']['language_mismatch'])
        self.assertEqual(json.loads(request['prompt'])['filename_language_hint'], 'de')

    def test_impossible_mismatch_is_a_reviewer_error_not_a_quality_verdict(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer(mismatch=True)):
            with self.assertRaises(RuntimeError) as raised:
                worker.assess_source_quality(self.rows)
        self.assertNotIsInstance(raised.exception, worker.SourceQualityError)

    def test_string_boolean_is_not_coerced_to_true(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer(mismatch='false')):
            with self.assertRaises(RuntimeError):
                worker._audit_source_chunk(self.rows, 'Example tyska.mp4')

    def test_unconfirmed_filename_does_not_reenter_source_review(self):
        with patch.object(worker, 'call_ollama', return_value=self.answer()) as model:
            worker.assess_source_quality(self.rows, 'Example tyska.mp4',
                                         use_filename_hint=False)
        model.assert_called_once()
        request = model.call_args.args[0]
        self.assertIsNone(json.loads(request['prompt'])['filename_language_hint'])
        self.assertEqual(request['format']['properties']['language_mismatch']['enum'], [False])


if __name__ == '__main__':
    unittest.main()
