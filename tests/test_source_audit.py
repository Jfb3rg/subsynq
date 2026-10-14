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


class LanguageMismatchEvidenceTests(unittest.TestCase):
    """Real job 40 (dev 0.8.246): a good German transcript was rejected because the reviewer set language_mismatch
    to true while its own reason said the text was German and matched the file name."""

    GERMAN = [{'id': index, 'source': text} for index, text in enumerate(
        ['Ich weiß, wer Sie sind.', 'Warum hat man Sie geschickt?', 'Wir haben alle eine dunkle Seite.'])]
    KOREAN = [{'id': index, 'source': '며칠 전에 친구들이랑 사냥을 하러 갔었어.'} for index in range(4)]
    TAMIL = [{'id': index, 'source': 'அப்பா பயமா இருப்பா சுலிடுப்பா'} for index in range(4)]

    def answer(self, mismatch, reason='The text is German, consistent with the filename hint.'):
        return {'response': json.dumps({'bad_ids': [], 'language_mismatch': mismatch, 'reason': reason})}

    def assess(self, rows, name, acoustic, mismatch=True):
        with patch.object(worker, 'call_ollama', return_value=self.answer(mismatch)):
            return worker.assess_source_quality(rows, name, acoustic_language=acoustic)

    def test_a_mismatch_claim_against_what_the_speech_recognition_heard_is_ignored(self):
        with patch('builtins.print') as log:
            self.assertTrue(self.assess(self.GERMAN, 'EIN TALENT Trailer German Deutsch.mp4', 'de'))
        self.assertIn('ignoreras', ' '.join(str(call.args[0]) for call in log.call_args_list))

    def test_without_acoustic_confirmation_the_claim_still_stops_the_job(self):
        with self.assertRaisesRegex(worker.SourceQualityError, 'Källtranskriberingen'):
            self.assess(self.GERMAN, 'EIN TALENT Trailer German Deutsch.mp4', None)
        with self.assertRaises(worker.SourceQualityError):
            self.assess(self.GERMAN, 'EIN TALENT Trailer German Deutsch.mp4', 'fr')

    def test_the_wrong_script_keeps_the_claim_alive_even_when_the_speech_was_confirmed(self):
        with self.assertRaises(worker.SourceQualityError):
            self.assess(self.KOREAN, 'HOPE tyska.mp4', 'de')

    def test_languages_with_their_own_script_must_match_it(self):
        self.assertTrue(self.assess(self.TAMIL, 'Sigma tamil.mp4', 'ta'))
        self.assertTrue(self.assess(self.KOREAN, 'HOPE koreansk.mp4', 'ko'))
        with self.assertRaises(worker.SourceQualityError):
            self.assess(self.GERMAN, 'Sigma tamil.mp4', 'ta')

    def test_no_claim_means_nothing_changes(self):
        self.assertTrue(self.assess(self.GERMAN, 'EIN TALENT German.mp4', 'de', mismatch=False))

    def test_credible_language_mismatch_directly(self):
        credible = worker.credible_language_mismatch
        self.assertTrue(credible(self.GERMAN, 'de', None))
        self.assertTrue(credible(self.GERMAN, 'de', 'en'))
        self.assertFalse(credible(self.GERMAN, 'de', 'de'))
        self.assertTrue(credible(self.KOREAN, 'de', 'de'))
        self.assertFalse(credible(self.KOREAN, 'ko', 'ko'))
        self.assertTrue(credible(self.GERMAN, 'ko', 'ko'))


if __name__ == '__main__':
    unittest.main()
