import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
import source_judge
from asr_windows import confirm_short_word_substitutions, plan_v2_second_opinion_windows
from audio_tracks import window_language


def fixture():
    original = {'start': 20.19, 'end': 21.12, 'text': 'Ánimo de Canarias.', 'confidence': .76595}
    precision = {'start': 20.18, 'end': 21.14, 'text': 'Venimos de Canarias.', 'confidence': .78149}
    words = [(19.46,19.82,'Sí,',.356),(19.9,20.04,'aquí',.9151),(20.04,20.32,'Corex,',.2803),
             (20.34,20.54,'venimos',.904),(20.54,20.66,'desde',.4728),(20.66,21.18,'Canarias.',.9052)]
    v2 = {'start': 19.46, 'end': 21.18, 'text': 'Sí, aquí Corex, venimos desde Canarias.',
          'words': [dict(start=a,end=b,text=t,probability=p) for a,b,t,p in words]}
    return original, precision, v2


class ShortWordTests(unittest.TestCase):
    def test_real_apoc_disagreement_plans_existing_bounded_double_check(self):
        original, precision, _ = fixture()
        self.assertEqual(plan_v2_second_opinion_windows([original],[precision],[],120), [{'start':16.,'end':33.}])
        self.assertEqual(plan_v2_second_opinion_windows([original],[dict(precision,text=original['text'])],[],120), [])

    def test_real_timed_independent_word_corrects_only_the_source_word(self):
        original, precision, v2 = fixture()
        before = copy.deepcopy((original,precision,v2))
        result, records = confirm_short_word_substitutions([original],[precision],[v2])
        self.assertEqual(result[0]['text'], 'Venimos de Canarias.')
        self.assertEqual((result[0]['start'],result[0]['end']), (20.19,21.12))
        self.assertEqual(result[0]['confirmed_source_words'], ['venimos'])
        self.assertEqual(records[0]['v2_words'][0]['probability'], .904)
        self.assertEqual((original,precision,v2), before)

    def test_no_guess_when_either_reader_or_word_times_are_missing(self):
        a,b,v2=fixture()
        for precise, others in (([],[v2]),([b],[]),([b],[dict(v2,words=[])])):
            self.assertEqual(confirm_short_word_substitutions([a],precise,others), ([a],[]))

    def test_low_word_probability_high_confidence_contradiction_and_neighbor_shift_refuse(self):
        a,b,v2=fixture()
        variants=[]
        low=copy.deepcopy(v2);low['words'][3]['probability']=.79;variants.append(low)
        contradict=copy.deepcopy(v2);contradict['words'][4]['probability']=.9;variants.append(contradict)
        shifted=copy.deepcopy(v2)
        for w in shifted['words']:w['start']+=.7;w['end']+=.7
        variants.append(shifted)
        for other in variants:
            self.assertEqual(confirm_short_word_substitutions([a],[b],[other]), ([a],[]))

    def test_two_word_changes_or_weak_v3_do_not_qualify(self):
        a,b,v2=fixture()
        for candidate in (dict(b,text='Venimos desde Canarias.'),dict(b,confidence=.69),dict(b,start=19.8)):
            self.assertEqual(confirm_short_word_substitutions([a],[candidate],[v2]), ([a],[]))

    def test_man_of_war_disputed_four_word_reading_is_not_overwritten(self):
        a={'start':26.37,'end':27.51,'text':'Come on, you niggas!','confidence':.35}
        others=[dict(a,text='Come on, you liars!',confidence=.9),dict(a,text='Come on, you dickhead!',confidence=.9)]
        self.assertEqual(confirm_short_word_substitutions([a],others,others),([a],[]))

    def test_confirmed_word_survives_text_only_choice(self):
        a,b,v2=fixture();fixed,_=confirm_short_word_substitutions([a],[b],[v2])
        row={**fixed[0],'id':6,'source':fixed[0]['text'], 'alternatives':[{'text':a['text'],'support':2,'current_support':2}]}
        request=source_judge.build_request('fake',source_judge.judge_items([row]))
        self.assertIn('venimos',json.loads(request['prompt'])['rows'][0]['preserve_words'])
        result,decisions=source_judge.choose_readings([row],lambda r:{'response':json.dumps({'rows':[{'id':6,'a_has_error':True,'choice':'B'}]})},'fake')
        self.assertEqual(result[0]['source'],b['text'])
        self.assertEqual(decisions[0]['reason'],'confirmed_audio_word')


def choice():
    def probe(a,b,language):return dict(start=a,end=b,whisper_language=language,whisper_text='Actual spoken words in this window.',speech_seconds=10)
    return {'index':0,'readings':{'0':[probe(11.4,26.4,'es'),probe(51.3,66.3,'en')],
                                 '1':[probe(0,120,'de')]}}


class LocalLanguageTests(unittest.TestCase):
    def test_confident_unhinted_code_switch_is_not_voted_away(self):
        class Whisper:
            def __init__(self,*a,**kw):pass
            def transcribe(self,path,language=None,**kw):
                identity=int(Path(path).stem.split('-')[-1])
                heard=language or ('es' if identity==2 else 'fr')
                return iter([SimpleNamespace(text=heard,avg_logprob=-.1)]),SimpleNamespace(language=heard,language_probability=.99)
        rows=[dict(id=i,start=i*3.,end=i*3.+1.,source='Some Latin source text.') for i in (1,2,3)]
        with (patch.object(worker,'WhisperModel',Whisper),patch.object(worker.subprocess,'run'),
              patch.object(worker,'check_cancelled'),patch.object(worker,'release_language_model_before_asr'),
              patch.object(worker,'trim_cpu_allocator'),patch.object(worker,'qwen_health',return_value=False)):
            result=worker.reread_readings({'id':-1},Path('/fake.mp4'),rows,[1,2,3])
        self.assertEqual([r['text'] for r in result[2]],['es','es'])

    def test_apoc_mixed_track_hints_only_covered_region(self):
        self.assertEqual(window_language(choice(),19,22),'es')
        self.assertEqual(window_language(choice(),55,58),'en')
        self.assertIsNone(window_language(choice(),30,40))
        self.assertIsNone(window_language(choice(),20,60))

    def test_overlapping_probes_do_not_double_count_coverage(self):
        c=choice();c['readings']['0']*=3
        self.assertIsNone(window_language(c,0,40))
        c['readings']['0'].append(dict(start=19,end=23,whisper_language='en',whisper_text='Another audible English sentence',speech_seconds=3))
        self.assertIsNone(window_language(c,19,22))

    def test_rereaders_get_per_clip_language_and_qwen_full_names_and_continue_after_failure(self):
        heard=[];qwen=[]
        class Whisper:
            def __init__(self,*a,**kw):pass
            def transcribe(self,path,**kw):
                heard.append((Path(path).stem,kw['language']))
                return iter([SimpleNamespace(text='Some speech.',avg_logprob=-.1)]),SimpleNamespace(language='en',language_probability=.99)
        def independent(path,language=None):
            qwen.append((Path(path).stem,language))
            if len(qwen)==1:raise RuntimeError('one clip failed')
            return {'text':'Other speech.'}
        rows=[dict(id=6,start=20.19,end=21.12,source='Ánimo de Canarias.'),dict(id=16,start=55.,end=56.,source='An English sentence.')]
        with (patch.object(worker,'WhisperModel',Whisper),patch.object(worker.subprocess,'run'),
              patch.object(worker,'check_cancelled'),patch.object(worker,'release_language_model_before_asr'),
              patch.object(worker,'trim_cpu_allocator'),patch.object(worker,'qwen_health',return_value=True),
              patch.object(worker,'qwen_identify',side_effect=independent),patch.object(worker,'qwen_unload'),
              patch.object(worker,'release_cloud_gpu_memory',return_value={})):
            result=worker.reread_readings({'id':166,'audio_choice':choice()},Path('/fake.mp4'),rows,[6,16])
        self.assertEqual(heard,[('row-6','es'),('row-16','en')]*2)
        self.assertEqual(qwen,[('row-6','Spanish'),('row-16','English')])
        self.assertEqual([r['name'] for r in result[16]],['precision','v2','qwen'])
        self.assertEqual([r['name'] for r in result[6]],['precision','v2'])


if __name__ == '__main__':unittest.main()
