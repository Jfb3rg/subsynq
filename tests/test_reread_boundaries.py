"""Real free readings: language selection is not approval of source words."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0]=['/app','/ai']
import worker
from reread_boundaries import (language_group_plans,confirmed_group_language,local_language_override,
                               kana_slot,confirmed_kana_insertion)
from reread import conflicting_reread_rows


def fixture():
    root=Path('/diagnostic') if str(__file__)=='/test.py' else Path(__file__).parent
    return json.loads((root/'fixtures/reread-boundaries.json').read_text())


class BoundaryTests(unittest.TestCase):
    def test_real_two_window_two_architecture_language_evidence_only_overrides_its_own_rows(self):
        f=fixture()['german'];before=copy.deepcopy(f['rows'])
        plan=language_group_plans(f['job'],f['rows'],[11,12])[0]
        self.assertEqual([tuple(round(x,3) for x in w) for w in plan['windows']],[(40.028,45.4),(39.76,45.7)])
        self.assertEqual(confirmed_group_language(plan,f['readings']),'en')
        f['job']['reread_language_evidence']=[dict(plan=plan,readings=f['readings'],source_words_approved=False)]
        self.assertEqual([worker.reread_row_language(f['job'],f['rows'],r) for r in f['rows']],['de','en','en','de'])
        self.assertEqual(f['rows'],before)
        self.assertTrue(f['rows'][1]['source_unclear'])

    def test_no_filename_inference_singleton_long_or_protected_group_is_probed(self):
        f=fixture()['german']
        self.assertEqual(language_group_plans({'language_policy':{'effective_language':'de'}},f['rows'],[11,12]),[])
        self.assertEqual(language_group_plans(f['job'],f['rows'],[12]),[])
        for change in ({'start':30.},{'confirmed_source_words':['my']},{'confirmed_lead_onset':{'start':40.}}):
            rows=copy.deepcopy(f['rows']);rows[1].update(change)
            self.assertEqual(language_group_plans(f['job'],rows,[11,12]),[])
        rows=copy.deepcopy(f['rows']);rows[2]['start']=43.9
        self.assertEqual(language_group_plans(f['job'],rows,[11,12]),[])

    def test_every_reader_and_both_windows_must_agree_on_language_and_text(self):
        f=fixture()['german'];plan=language_group_plans(f['job'],f['rows'],[11,12])[0]
        for key,value in (('language','German'),('text','Different real words.'),('language_probability',.89),('confidence',.64)):
            readings=copy.deepcopy(f['readings']);readings[0][key]=value
            self.assertIsNone(confirmed_group_language(plan,readings))
        self.assertIsNone(confirmed_group_language(plan,f['readings'][:-1]))
        self.assertIsNone(confirmed_group_language(plan,f['readings']+[f['readings'][0]]))

    def test_changed_source_time_or_policy_invalidates_the_local_hint(self):
        f=fixture()['german'];plan=language_group_plans(f['job'],f['rows'],[11,12])[0]
        f['job']['reread_language_evidence']=[dict(plan=plan,readings=f['readings'])]
        for change in ({'source':'New source words.'},{'start':40.4},{'end':44.}):
            self.assertIsNone(local_language_override(f['job'],{**f['rows'][1],**change}))
        f['job']['language_policy']['status']='manual'
        self.assertIsNone(local_language_override(f['job'],f['rows'][1]))

    def test_worker_probes_only_once_and_does_not_change_source_or_times(self):
        f=fixture()['german'];before=copy.deepcopy(f['rows'])
        with patch.object(worker,'read_bounded_reread_windows',return_value=f['readings']) as read:
            worker.probe_local_reread_languages(f['job'],Path('/fake.mp4'),f['rows'],[11,12])
            worker.probe_local_reread_languages(f['job'],Path('/fake.mp4'),f['rows'],[11,12])
        self.assertEqual(read.call_count,1)
        self.assertEqual(f['job']['reread_language_evidence'][0]['language'],'en')
        self.assertFalse(f['job']['reread_language_evidence'][0]['source_words_approved'])
        self.assertEqual(f['rows'],before)

    def test_real_local_english_does_not_license_copying_words_from_the_previous_row(self):
        f=fixture()['german'];before=copy.deepcopy(f['rows'])
        with tempfile.NamedTemporaryFile() as media:
            job={**f['job'],'id':-1,'media_path':media.name}
            with patch.object(worker,'REREAD_ENABLED',True), \
                    patch.object(worker,'read_bounded_reread_windows',return_value=f['readings']), \
                    patch.object(worker,'redistribute_reread_groups',return_value=[]), \
                    patch.object(worker,'reread_readings',return_value={int(i):r for i,r in f['tight_readings'].items()}):
                changed=worker.reread_flagged_rows(job,f['rows'],[11,12])
        self.assertEqual(changed,[])
        self.assertEqual(f['rows'],before)
        self.assertEqual([worker.reread_row_language(job,f['rows'],r) for r in f['rows']],['de','en','en','de'])
        record=next(r for r in job['reread_rows'] if r['id']==12)
        self.assertEqual(record['decision'],'omläsningen innehåller redan skrivna grannord')
        self.assertIn(12,[r['id'] for r in conflicting_reread_rows(f['rows'],job['reread_rows'],[11,12])])

    def test_real_kana_insertion_preserves_every_old_character_and_the_cue_times(self):
        f=fixture()['japanese'];before=copy.deepcopy(f['row'])
        text,proof=confirmed_kana_insertion(f['row'],f['readings'])
        self.assertEqual(text,'振り切れません')
        self.assertEqual(proof['letter'],'り')
        self.assertEqual(len(proof['proofs']),4)
        self.assertTrue(any(w['outside_before'] for w in proof['proofs']))
        self.assertEqual(f['row'],before)
        self.assertTrue(all(w['probability']>=.8 for r in proof['proofs'] for w in r['words']))

    def test_no_content_character_substitution_multiple_or_edge_insertion(self):
        for current,candidate in (('振切れません','振取切れません'),('振切れません','振り切りれません'),
                                  ('振切れません','振り切れます'),('振切れません','あ振切れません'),
                                  ('振切れません','振切れませんか'),('hello there','hello their')):
            self.assertIsNone(kana_slot(current,candidate))

    def test_missing_reader_different_qwen_or_weak_inserted_character_refuses(self):
        f=fixture()['japanese']
        self.assertIsNone(confirmed_kana_insertion(f['row'],f['readings'][:-1])[0])
        for name,change in (('qwen',{'text':'振り切れます。'}),('precision',None),('v2',None)):
            readings=copy.deepcopy(f['readings']);r=next(r for r in readings if r['name']==name)
            if change:r.update(change)
            else:next(w for w in r['words'] if w['word'].strip()=='り')['probability']=.79
            self.assertIsNone(confirmed_kana_insertion(f['row'],readings)[0])

    def test_straddling_prefix_nonfinite_and_shifted_inserted_word_refuse(self):
        f=fixture()['japanese']
        for changes in ({'start':31.,'end':31.7},{'end':float('nan')},{'start':31.1,'end':31.2}):
            readings=copy.deepcopy(f['readings'])
            r=next(r for r in readings if r['name']=='precision')
            next(w for w in r['words'] if w['word'].strip()=='り').update(changes)
            self.assertIsNone(confirmed_kana_insertion(f['row'],readings)[0])
        self.assertIsNone(confirmed_kana_insertion(f['row'],f['readings'],[dict(start=32.,end=33.)])[0])
        self.assertIsNone(confirmed_kana_insertion(f['row'],f['readings'],[dict(start=32.08,end=33.)])[0])

    def test_worker_applies_kana_without_discarding_the_outside_utterance_from_report(self):
        f=fixture()['japanese'];row=f['row'];before=copy.deepcopy(row)
        record=dict(id=5,current=row['source'],decision='modellfamiljerna läste olika ord',
                    readings=[r for r in f['readings'] if r['window']==0])
        job={'id':-1}
        with patch.object(worker,'read_bounded_reread_windows',return_value=[r for r in f['readings'] if r['window']==1]):
            worker.reread_kana_rows(job,Path('/fake.mp4'),[row],[record])
        self.assertEqual(row['source'],'振り切れません')
        self.assertEqual((row['start'],row['end']),(before['start'],before['end']))
        self.assertTrue(record['kana_insertion']['applied'])
        self.assertEqual(record['readings'][0]['text'],f['readings'][0]['text'])


if __name__=='__main__':unittest.main()
