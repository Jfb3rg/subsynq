import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class SourceAuditTests(unittest.TestCase):
    def test_saved_kana_alternative_reaches_audio_check_even_when_text_auditor_says_clear(self):
        rows=[{'id':5,'start':31.07,'end':32.07,'source':'振切れません'}]
        job={'id':-1,'source_choices':[{'id':5,'from':rows[0]['source'],'applied':False,
                                      'choice':'A','a_has_error':False,'candidates':['振切れません','振り切れません!']}]}
        def reread(job, rows, ids):
            self.assertEqual(ids,[5])
            rows[0]['source']='振り切れません'
            job['reread_rows']=[]
            return [5]
        with patch.object(worker,'REREAD_ENABLED',True), \
                patch.object(worker,'_audit_source_chunk',return_value=([],False,'')), \
                patch.object(worker,'reread_flagged_rows',side_effect=reread) as check, \
                patch.object(worker,'update_job'):
            self.assertEqual(worker.assess_source_with_reread(job,rows),[])
        check.assert_called_once()
        self.assertEqual(job['source_reread']['kana_review_candidates'],[5])

    def test_kana_planner_never_approves_words_and_rejects_stale_protected_or_non_kana_candidates(self):
        rows=[{'id':5,'source':'振切れません'}]
        decision={'id':5,'from':'振切れません','applied':False,'candidates':['振り切れません!']}
        with patch.object(worker,'REREAD_ENABLED',True):
            self.assertEqual(worker.kana_review_ids({'source_choices':[decision]},rows),[5])
            for changes in ({'from':'changed'},{'applied':True},{'candidates':['振切れます']}, {'candidates':None}):
                self.assertEqual(worker.kana_review_ids({'source_choices':[{**decision,**changes}]},rows),[])
            self.assertEqual(worker.kana_review_ids({'source_choices':[decision]},[{**rows[0],'confirmed_source_words':['protected']}]),[])
        with patch.object(worker,'REREAD_ENABLED',False):
            self.assertEqual(worker.kana_review_ids({'source_choices':[decision]},rows),[])

    def test_kana_plan_has_four_row_budget_without_changing_source(self):
        rows=[{'id':i,'source':'振切れません'} for i in range(8)]
        choices=[{'id':r['id'],'from':r['source'],'applied':False,'candidates':['振り切れません']} for r in rows]
        with patch.object(worker,'REREAD_ENABLED',True):
            self.assertEqual(worker.kana_review_ids({'source_choices':choices},rows),[0,1,2,3])
        self.assertTrue(all(r['source']=='振切れません' for r in rows))

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

    def test_mixed_language_findings_are_never_filtered_or_approved_by_language(self):
        rows = [{'id': i, 'source': text} for i, text in enumerate(
            ['Wir drehen einen Film.', 'I the the the the the.', 'Ich der die das Film.'])]
        with patch.object(worker, 'call_ollama', return_value=self.answer([1, 2])):
            broken = []
            with self.assertRaises(worker.BrokenSourceRows):
                worker.assess_source_quality(rows, broken=broken)
            self.assertEqual(broken, [1, 2])

    def test_small_mixed_language_finding_still_reaches_acoustic_rereading(self):
        rows = [{'id': i, 'source': text} for i, text in enumerate(
            ['Wir drehen einen Film.', 'I want to join you.', 'Ich will auch in den Film.', 'Film?'])]
        with patch.object(worker, 'call_ollama', return_value=self.answer([2])), \
                patch.object(worker, 'reread_flagged_rows', return_value=[]) as reread, \
                patch.object(worker, 'update_job'):
            self.assertEqual(worker.assess_source_with_reread({'id': -1}, rows), [2])
        reread.assert_called_once_with(unittest.mock.ANY, rows, [2])

    def test_verified_language_context_never_filters_word_findings_or_changes_source(self):
        context = {'base_language':'de', 'verified_local_languages':{'1':'en','99':'fr'},
                   'source_words_approved':False}
        with patch.object(worker,'call_ollama',return_value=self.answer([1])) as model:
            bad, mismatch, reason = worker._audit_source_chunk(self.rows,'',language_context=context)
        self.assertEqual(bad,[1])
        prompt=json.loads(model.call_args.args[0]['prompt'])
        self.assertEqual(prompt['rows'],self.rows)
        self.assertEqual(prompt['acoustic_language_context']['verified_local_languages'],{'1':'en'})
        self.assertFalse(prompt['acoustic_language_context']['source_words_approved'])
        self.assertEqual(context['verified_local_languages'],{'1':'en','99':'fr'})

    def test_verified_context_is_forwarded_to_both_source_reviewers_without_threshold_changes(self):
        context={'base_language':'de','verified_local_languages':{'1':'en'},'source_words_approved':False}
        rows=[{'id':i,'source':'A source sentence.'} for i in range(6)]
        broken=[]
        with patch.object(worker,'_audit_source_chunk',side_effect=lambda chunk,*a,**kw: ([1] if any(r['id']==1 for r in chunk) else [],False,'bad')), \
                patch.object(worker,'audit_short_source_quality') as short:
            self.assertTrue(worker.assess_source_quality(rows,'German.mp4',broken=broken,language_context=context))
        self.assertEqual(broken,[1])
        self.assertEqual(short.call_args.kwargs['language_context'],context)
        with patch.object(worker,'_audit_source_chunk',side_effect=lambda chunk,*a,**kw: ([r['id'] for r in chunk if r['id'] in (1,2,3)],False,'bad')):
            with self.assertRaises(worker.BrokenSourceRows):
                worker.assess_source_quality(rows,language_context=context)

    def test_only_verified_language_edges_split_and_all_rows_keep_their_order(self):
        rows=[{'id':i,'source':str(i)} for i in range(8)]
        context={'base_language':'de','verified_local_languages':{'2':'en','3':'en'}}
        chunks=worker.source_audit_chunks(rows,context,size=3)
        self.assertEqual([[r['id'] for r in chunk] for chunk in chunks],[[0,1],[2,3],[4,5,6],[7]])
        self.assertEqual([r for chunk in chunks for r in chunk],rows)
        self.assertEqual(worker.source_audit_chunks(rows),[rows])

    def test_local_switch_chunk_drops_only_global_hint_and_keeps_all_findings(self):
        rows=[{'id':i,'source':'A source sentence.'} for i in range(8)]
        context={'base_language':'de','verified_local_languages':{'2':'en','3':'en'}}
        def audit(chunk,*args,**options):
            return ([2] if chunk[0]['id']==2 else []),False,'bad'
        with patch.object(worker,'_audit_source_chunk',side_effect=audit) as model, \
                patch.object(worker,'audit_short_source_quality'):
            broken=[]
            self.assertTrue(worker.assess_source_quality(rows,'German.mp4',broken=broken,language_context=context))
        self.assertEqual(broken,[2])
        self.assertEqual([c.kwargs['use_filename_hint'] for c in model.call_args_list],[True,False,True])

    def test_short_audit_counts_findings_over_the_whole_input_not_each_language_group(self):
        rows=[{'id':i,'source':'A source sentence.'} for i in range(12)]
        context={'base_language':'de','verified_local_languages':{'2':'en','3':'en'}}
        def audit(chunk,*args):
            return [r['id'] for r in chunk if r['id'] in (2,3)],'Two broken cues.'
        with patch.object(worker,'_audit_short_source_chunk',side_effect=audit) as model:
            broken=[]
            self.assertTrue(worker.audit_short_source_quality(rows,'de',broken=broken,language_context=context))
        self.assertEqual(broken,[2,3])
        self.assertEqual([c.args[1] for c in model.call_args_list],['de','en','de'])


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
