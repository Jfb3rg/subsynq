"""Wrong-script tight reads require bounded free context, not a forced language/text."""
import copy
import json
import sys
import tempfile
import unittest
import numpy as np
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from reread import foreign_context_plan, foreign_context_reading, foreign_context_replacement


def fixture():
    root = Path('/diagnostic') if str(__file__)=='/test.py' else Path(__file__).parent
    return json.loads((root/'fixtures/multilingual-context.json').read_text())


def evaluated(sample, plan):
    return [dict(name=r['name'], window=i, **foreign_context_reading(plan, r['segments'], w['offset'],
                r['language'], r['language_probability'])) for i,w in enumerate(sample['windows']) for r in w['readings']]


class ForeignContextTests(unittest.TestCase):
    def test_fixed_windows_real_words_and_weak_negative_control_are_all_recorded(self):
        sample=fixture()
        plan=foreign_context_plan(sample['rows'],sample['record'],'en')
        self.assertEqual(plan['windows'],[(92.432,98.96),(91.432,99.96),(92.,99.)])
        readings=evaluated(sample,plan)
        self.assertEqual(sum(r['accepted'] for r in readings),5)
        weak=next(r for r in readings if r['name']=='precision' and r['window']==2)
        self.assertEqual(weak['text'],"I don't like it.")
        self.assertFalse(weak['accepted'])
        self.assertLess(weak['words'][0]['probability'],.6)
        text,record,start,end=foreign_context_replacement(sample['record']['current'],plan,readings)
        self.assertEqual(text,'Highly unlikely.')
        self.assertGreaterEqual(start,plan['left'])
        self.assertLessEqual(end,plan['right']+.000001)
        self.assertEqual(record['agreement'],1.)

    def test_no_other_script_rescue_when_a_whisper_already_reads_the_current_script(self):
        for name in ('precision','v2'):
            sample=fixture()
            next(r for r in sample['record']['readings'] if r['name']==name)['text']='Conflicting English words.'
            self.assertIsNone(foreign_context_plan(sample['rows'],sample['record'],'en'))

    def test_no_missing_qwen_language_stale_current_protected_or_long_row(self):
        sample=fixture()
        self.assertIsNone(foreign_context_plan(sample['rows'],sample['record'],None))
        for change in ({'source':'Fresh source words.'},{'end':99.}, {'confirmed_source_words':['know']},
                       {'confirmed_lead_onset':{'start':95.432}},{'start':float('nan')}):
            current=copy.deepcopy(sample)
            current['rows'][1].update(change)
            self.assertIsNone(foreign_context_plan(current['rows'],current['record'],'en'))

    def test_no_missing_duplicate_or_different_script_qwen_reader(self):
        sample=fixture()
        for readings in (sample['record']['readings'][:2],sample['record']['readings']*2):
            self.assertIsNone(foreign_context_plan(sample['rows'],{**sample['record'],'readings':readings},'en'))
        next(r for r in sample['record']['readings'] if r['name']=='qwen')['text']='Αλλά ανάγκη.'
        self.assertIsNone(foreign_context_plan(sample['rows'],sample['record'],'el'))

    def test_every_qualified_dissent_vetoes_even_with_five_other_supports(self):
        sample=fixture();plan=foreign_context_plan(sample['rows'],sample['record'],'en');readings=evaluated(sample,plan)
        source=copy.deepcopy(next(r for r in sample['windows'][2]['readings'] if r['name']=='precision'))
        next(s for s in source['segments'] if "I don't like" in s['text'])['words'][0]['probability']=.9
        strong=foreign_context_reading(plan,source['segments'],sample['windows'][2]['offset'],'en',.99)
        self.assertTrue(strong['accepted'])
        readings=[dict(name='precision',window=2,**strong) if r['name']=='precision' and r['window']==2 else r for r in readings]
        self.assertIsNone(foreign_context_replacement(sample['record']['current'],plan,readings))

    def test_no_replacement_without_two_qualified_windows_from_each_model(self):
        sample=fixture();plan=foreign_context_plan(sample['rows'],sample['record'],'en');readings=evaluated(sample,plan)
        self.assertIsNone(foreign_context_replacement(sample['record']['current'],plan,readings[:-1]))
        next(r for r in readings if r['name']=='precision' and r['window']==0)['accepted']=False
        self.assertIsNone(foreign_context_replacement(sample['record']['current'],plan,readings))

    def test_context_in_another_language_does_not_force_qwens_language(self):
        sample=fixture();plan=foreign_context_plan(sample['rows'],sample['record'],'de')
        self.assertFalse(any(r['accepted'] for r in evaluated(sample,plan)))

    def test_the_same_rule_accepts_french_without_an_english_phrase_or_language_rule(self):
        sample=fixture()
        sample['rows'][1]['source']=sample['record']['current']='Il va venir.'
        next(r for r in sample['record']['readings'] if r['name']=='qwen')['text']='Très improbable.'
        for window in sample['windows']:
            for reading in window['readings']:
                reading['language']='fr'
                for segment in reading['segments']:
                    if segment['text'].strip()=='Highly unlikely.':
                        segment['text']='Très improbable.'
                        for word,text in zip(segment['words'],('Très','improbable.')):
                            word['word']=text
        plan=foreign_context_plan(sample['rows'],sample['record'],'fr')
        self.assertEqual(foreign_context_replacement(sample['record']['current'],plan,evaluated(sample,plan))[0],'Très improbable.')

    def test_no_spliced_segment_zero_weak_nonfinite_or_neighbour_words(self):
        sample=fixture();plan=foreign_context_plan(sample['rows'],sample['record'],'en')
        source=sample['windows'][0]['readings'][0]
        offset=sample['windows'][0]['offset']
        for change in ({'probability':.59},{'start':0.}, {'end':float('inf')}, {'end':0.}):
            segments=copy.deepcopy(source['segments'])
            target=next(s for s in segments if 'Highly' in s['text'])
            target['words'][0].update(change)
            self.assertFalse(foreign_context_reading(plan,segments,offset,'en',.99)['accepted'])
        segments=copy.deepcopy(source['segments'])
        target=next(s for s in segments if 'Highly' in s['text'])
        target['words']+=segments[0]['words']
        self.assertFalse(foreign_context_reading(plan,segments,offset,'en',.99)['accepted'])

    def test_confirmed_local_language_survives_but_explicit_policy_still_wins(self):
        row=dict(id=1,start=1.,end=2.,source='Sehr unwahrscheinlich.',confirmed_language='de',
                 confirmed_source_words=['sehr','unwahrscheinlich'])
        self.assertEqual(worker.reread_row_language({},[row],row),'de')
        self.assertEqual(worker.reread_row_language({'language_policy':{'effective_language':'fr'}},[row],row),'fr')
        row['source']='Changed source.'
        self.assertIsNone(worker.reread_row_language({},[row],row))

    def test_actual_context_reader_is_unhinted_bounded_and_never_repeats_a_row(self):
        sample=fixture();record=sample['record']
        next(r for r in record['readings'] if r['name']=='qwen')['language']='English'
        calls=[]
        class Whisper:
            def __init__(self,model_name,**kw):
                self.name='precision' if model_name=='large-v3' else 'v2'
            def transcribe(self,path,**kw):
                calls.append(kw)
                i=int(Path(path).stem.split('-')[-1])
                r=next(r for r in sample['windows'][i]['readings'] if r['name']==self.name)
                segments=[SimpleNamespace(**{**s,'words':[SimpleNamespace(**{
                    **w,'start':np.float64(w['start']),'end':np.float64(w['end']),
                    'probability':np.float64(w['probability'])}) for w in s['words']]}) for s in r['segments']]
                return iter(segments),SimpleNamespace(language=r['language'],language_probability=r['language_probability'])
        job={'id':-1}
        with patch.object(worker,'WhisperModel',Whisper),patch.object(worker.subprocess,'run'), \
                patch.object(worker,'release_language_model_before_asr'),patch.object(worker,'trim_cpu_allocator'), \
                patch.object(worker,'check_cancelled'):
            result=worker.reread_foreign_context(job,Path('/fake.mp4'),sample['rows'],[record])
            self.assertEqual(worker.reread_foreign_context(job,Path('/fake.mp4'),sample['rows'],[record]),{})
        self.assertEqual(len(calls),6)
        self.assertTrue(all(c['language'] is None and c['multilingual'] for c in calls))
        self.assertTrue(all(not set(c)&{'initial_prompt','prefix','hotwords'} for c in calls))
        self.assertEqual(result[29][0][0],'Highly unlikely.')
        self.assertEqual(job['foreign_context_attempted'],[29])
        self.assertEqual(len(job['foreign_context_readings'][0]['readings']),6)

    def test_explicit_language_or_exhausted_budget_never_loads_extra_models(self):
        sample=fixture()
        next(r for r in sample['record']['readings'] if r['name']=='qwen')['language']='English'
        for job in ({'id':-1,'language_policy':{'effective_language':'en'}},
                    {'id':-1,'foreign_context_attempted':[1,2,3,4]}):
            with patch.object(worker,'WhisperModel') as model:
                self.assertEqual(worker.reread_foreign_context(job,Path('/fake.mp4'),sample['rows'],[sample['record']]),{})
            model.assert_not_called()

    def test_worker_applies_measured_times_only_to_the_target_and_preserves_all_evidence(self):
        sample=fixture();before=copy.deepcopy(sample['rows']);plan=foreign_context_plan(sample['rows'],sample['record'],'en')
        readings=evaluated(sample,plan);result=foreign_context_replacement(sample['record']['current'],plan,readings)
        with tempfile.NamedTemporaryFile() as media:
            job={'id':-1,'media_path':media.name,'foreign_context_readings':[dict(plan=plan,readings=readings,applied=True)]}
            with patch.object(worker,'REREAD_ENABLED',True), \
                    patch.object(worker,'reread_readings',return_value={29:sample['record']['readings']}), \
                    patch.object(worker,'reread_foreign_context',return_value={29:(result,plan)}):
                changed=worker.reread_flagged_rows(job,sample['rows'],[29])
        self.assertEqual(changed,[29])
        self.assertEqual(sample['rows'][0],before[0]);self.assertEqual(sample['rows'][2],before[2])
        self.assertEqual(sample['rows'][1]['source'],'Highly unlikely.')
        self.assertEqual(sample['rows'][1]['confirmed_source_words'],['highly','unlikely'])
        self.assertEqual(len(job['reread_rows'][0]['foreign_context']['readings']),6)
        self.assertEqual(job['reread_rows'][0]['readings'],sample['record']['readings'])


if __name__=='__main__':
    unittest.main()
