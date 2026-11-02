"""A cue boundary may move; the existing joined source must remain byte-identical."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0]=['/app','/ai']
import worker
from reread_boundaries import language_group_plans,confirmed_group_partition,local_language_override


def sample():
    root=Path('/diagnostic') if str(__file__)=='/test.py' else Path(__file__).parent
    f=json.loads((root/'fixtures/reread-boundaries.json').read_text())['german']
    return f,language_group_plans(f['job'],f['rows'],[11,12])[0]


class GroupPartitionTests(unittest.TestCase):
    def test_source_audit_context_is_bound_to_current_words_times_and_policy(self):
        f,plan=sample()
        job={**f['job'],'reread_language_evidence':[dict(plan=plan,readings=f['readings'])], 'reread_rows':[]}
        self.assertEqual(worker.source_audit_language_context(job,f['rows']),{
            'base_language':'de','verified_local_languages':{'11':'en','12':'en'},'source_words_approved':False})
        worker.redistribute_reread_groups(job,f['rows'])
        self.assertEqual(worker.source_audit_language_context(job,f['rows'])['verified_local_languages'],{'11':'en','12':'en'})
        for row in f['rows'][1:3]:
            row['source']+=' changed'
        self.assertIsNone(worker.source_audit_language_context(job,f['rows']))
        self.assertIsNone(worker.source_audit_language_context({},f['rows']))

    def test_real_crossed_contraction_moves_to_the_corroborated_comma_without_word_changes(self):
        f,plan=sample();before=copy.deepcopy(plan)
        result=confirmed_group_partition(plan,f['readings'])
        self.assertEqual(result['rows'][0]['source'],'Some people call me a living cardboard figure,')
        self.assertEqual(result['rows'][1]['source'],'but my job is a lot more complex than that.')
        self.assertEqual(result['boundary'],42.359)
        self.assertEqual(result['rows'][0]['end'],result['rows'][1]['start'])
        self.assertEqual(' '.join(r['source'] for r in result['rows']),plan['source'])
        self.assertEqual([r['id'] for r in result['rows']],[11,12])
        self.assertEqual(result['rows'][0]['start'],plan['rows'][0]['start'])
        self.assertEqual(result['rows'][1]['end'],plan['rows'][1]['end'])
        self.assertEqual(result['unassigned_leading_words'],['well'])
        self.assertFalse(result['source_words_approved'])
        self.assertEqual(plan,before)

    def test_source_approximation_cannot_replace_exact_existing_words(self):
        f,plan=sample()
        plan['rows'][0]['source']=plan['rows'][0]['source'].replace('cardboard','wooden')
        plan['source']=' '.join(r['source'] for r in plan['rows'])
        self.assertIsNone(confirmed_group_partition(plan,f['readings']))

    def test_no_new_punctuation_or_unconfirmed_model_comma(self):
        f,plan=sample()
        plan['rows'][0]['source']=plan['rows'][0]['source'].replace(',','')
        plan['source']=' '.join(r['source'] for r in plan['rows'])
        self.assertIsNone(confirmed_group_partition(plan,f['readings']))
        f,plan=sample()
        next(r for r in f['readings'] if r['name']=='qwen')['text']=next(r for r in f['readings'] if r['name']=='qwen')['text'].replace('figure,','figure')
        self.assertIsNone(confirmed_group_partition(plan,f['readings']))

    def test_no_move_without_a_word_physically_crossing_the_old_boundary(self):
        f,plan=sample()
        plan['rows'][0]['end']=plan['rows'][1]['start']=42.36
        self.assertIsNone(confirmed_group_partition(plan,f['readings']))

    def test_no_missing_duplicate_or_dissenting_reader(self):
        f,plan=sample()
        for readings in (f['readings'][:-1],f['readings']+[f['readings'][0]]):
            self.assertIsNone(confirmed_group_partition(plan,readings))
        f['readings'][0]['text']+=' Different words.'
        self.assertIsNone(confirmed_group_partition(plan,f['readings']))

    def test_weak_anchor_or_invalid_or_unstable_anchor_times_refuse(self):
        for change in ({'probability':.79},{'start':float('nan')},{'end':float('inf')},{'start':43.,'end':43.2}):
            f,plan=sample()
            next(w for w in f['readings'][0]['words'] if w['word'].strip()=='figure,').update(change)
            self.assertIsNone(confirmed_group_partition(plan,f['readings']))
        f,plan=sample()
        for token in ('figure,','but'):
            word=next(w for w in f['readings'][0]['words'] if w['word'].strip()==token)
            word['start']+=.5;word['end']+=.5
        self.assertIsNone(confirmed_group_partition(plan,f['readings']))

    def test_weak_or_non_crossing_contraction_refuses(self):
        for change in ({'probability':.59},{'end':43.94},{'start':43.95},{'end':float('nan')}):
            f,plan=sample()
            next(w for w in f['readings'][0]['words'] if w['word'].strip()=="job's").update(change)
            self.assertIsNone(confirmed_group_partition(plan,f['readings']))

    def test_worker_keeps_other_rows_metadata_and_validated_language_after_redistribution(self):
        f,plan=sample();before=copy.deepcopy(f['rows'])
        job={**f['job'],'reread_language_evidence':[dict(plan=plan,readings=f['readings'])],
             'reread_rows':[dict(id=i,current=next(r['source'] for r in f['rows'] if r['id']==i),decision='unchanged') for i in (11,12)]}
        self.assertEqual(worker.redistribute_reread_groups(job,f['rows']),[11,12])
        self.assertEqual(worker.redistribute_reread_groups(job,f['rows']),[])
        self.assertEqual(f['rows'][0],before[0]);self.assertEqual(f['rows'][3],before[3])
        self.assertEqual([worker.reread_row_language(job,f['rows'],r) for r in f['rows']],['de','en','en','de'])
        self.assertEqual(' '.join(r['source'] for r in f['rows'][1:3]),plan['source'])
        self.assertTrue(all(r['source_unclear'] for r in f['rows'][1:3]))
        f['rows'][1]['source']+=' changed'
        self.assertIsNone(local_language_override(job,f['rows'][1]))

    def test_stale_source_or_time_and_policy_changes_do_not_apply(self):
        for change in ({'source':'A changed source.'},{'start':40.4},{'end':44.}):
            f,plan=sample();f['rows'][1].update(change)
            job={**f['job'],'reread_language_evidence':[dict(plan=plan,readings=f['readings'])],'reread_rows':[]}
            self.assertEqual(worker.redistribute_reread_groups(job,f['rows']),[])
        for policy in ({'status':'manual','effective_language':'de','filename_hint':'de'},
                       {'status':'confirmed','effective_language':'fr','filename_hint':'fr'}):
            f,plan=sample()
            job={'language_policy':policy,'reread_language_evidence':[dict(plan=plan,readings=f['readings'])],'reread_rows':[]}
            self.assertEqual(worker.redistribute_reread_groups(job,f['rows']),[])

    def test_actual_worker_flow_refuses_neighbour_copy_then_preserves_joined_text(self):
        f,plan=sample();before=copy.deepcopy(f['rows'])
        with tempfile.NamedTemporaryFile() as media:
            job={**f['job'],'id':-1,'media_path':media.name}
            with patch.object(worker,'REREAD_ENABLED',True), \
                    patch.object(worker,'read_bounded_reread_windows',return_value=f['readings']), \
                    patch.object(worker,'reread_readings',return_value={int(i):r for i,r in f['tight_readings'].items()}):
                changed=worker.reread_flagged_rows(job,f['rows'],[11,12])
        self.assertEqual(changed,[11,12])
        self.assertEqual(' '.join(r['source'] for r in f['rows'][1:3]),' '.join(r['source'] for r in before[1:3]))
        self.assertEqual(job['reread_rows'][1]['initial_decision'],'omläsningen innehåller redan skrivna grannord')
        self.assertEqual(job['reread_rows'][0]['group_partition']['boundary'],42.359)


if __name__=='__main__':unittest.main()
