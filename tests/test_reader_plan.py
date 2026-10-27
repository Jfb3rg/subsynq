import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import reader_plan
import worker

HINDI = ['मैं वापस आऊंगा।', 'यह मेरी कहानी है।', 'तुम कहाँ जा रहे हो?', 'हम कल सुबह मिलेंगे।']
KOREAN = ['나는 돌아올 거야.', '이건 내 이야기야.', '어디 가는 거야?', '내일 아침에 만나자.']
CHINESE = ['我会回来的，别担心。', '这是我的故事，你听我说。', '你要去哪里呢，告诉我。']
JAPANESE = ['私は必ず戻ってきます。', 'これは私の物語です。', 'どこへ行くのですか。', '明日の朝に会いましょう。']

# Real KING trailer (dev job 64): what Whisper wrote in three neighbouring cues; the border between the first two cuts through "समंदर | है".
KING = ['मेरे दुश्मन समझ रहे थे मैं, अब कभी लोटके ना होगा, एक गुम्रामी का समुदर', 'है, उसमें ही जाके दूखा, बागी मेरी कहानी है,', 'सारी दुनिया को जो सुनाओ,']
KING_RUN = 'मेरे दुश्मन समझ रहे थे मैं अब कभी लौट के ना आऊँगा एक गुमनामी का समुद्र है उसमें ही जाके डूब जाएँगे अब बागी मेरी कहानी है सारी दुनिया को जो सुनाओ'


def cues(texts, gap=.5):
    start, result = 0.0, []
    for number, text in enumerate(texts):
        result.append({'start': start, 'end': start + 2.5, 'text': text, 'confidence': .8 - number / 20})
        start += 2.5 + gap
    return result


ON = {'SUBSYNQ_READER_PLAN': 'on'}


class PlanSelectionTests(unittest.TestCase):
    def test_the_plan_is_switched_off_by_default_because_it_lost_to_whisper_on_film_audio(self):
        self.assertEqual(reader_plan.mode({}), 'off')
        for text in (HINDI, KOREAN, CHINESE):
            self.assertIsNone(reader_plan.plan_for(text, {}))
        for value in ('0', 'off', 'false', 'nej', ''):
            self.assertIsNone(reader_plan.plan_for(HINDI, {'SUBSYNQ_READER_PLAN': value}), value)

    def test_hindi_and_korean_get_a_plan_by_the_script_whisper_wrote_when_it_is_switched_on(self):
        self.assertEqual(reader_plan.plan_for(HINDI, ON)['language'], 'Hindi')
        self.assertEqual(reader_plan.plan_for(KOREAN, ON)['language'], 'Korean')
        self.assertEqual(reader_plan.plan_for(KOREAN, ON)['label'], 'koreanska')

    def test_other_languages_have_no_plan_even_when_it_is_on(self):
        self.assertIsNone(reader_plan.plan_for(['Det här är en svensk replik som är ganska lång.', 'Och här kommer en till replik.'], ON))
        self.assertIsNone(reader_plan.plan_for(JAPANESE, ON), 'Whisper reads Japanese better than Qwen in the FLEURS test')
        self.assertIsNone(reader_plan.plan_for(['Hej.'], ON))

    def test_chinese_needs_the_setting_all(self):
        self.assertIsNone(reader_plan.plan_for(CHINESE, ON))
        self.assertEqual(reader_plan.plan_for(CHINESE, {'SUBSYNQ_READER_PLAN': 'all'})['language'], 'Chinese')

    def test_kana_means_japanese_even_when_the_plan_is_open_for_all(self):
        mixed = ['私は東京へ行きます。', '日本語の映画を見ました。', '友達と一緒に食べました。', '駅の前で会いましょう。']
        self.assertIsNone(reader_plan.plan_for(mixed, {'SUBSYNQ_READER_PLAN': 'all'}))


class JudgeTests(unittest.TestCase):
    def test_a_sound_reading_is_accepted(self):
        self.assertEqual(reader_plan.judge('मैं वापस आऊंगा', 'मैं वापस आऊँगा।', 'Devanagari'), (True, ''))

    def test_nothing_the_wrong_script_and_loops_are_refused(self):
        self.assertFalse(reader_plan.judge('मैं वापस आऊंगा', '  ', 'Devanagari')[0])
        accepted, reason = reader_plan.judge('मैं वापस आऊंगा', 'I will come back', 'Devanagari')
        self.assertFalse(accepted)
        self.assertIn('skrift', reason)
        loop = 'सब कुछ नहीं है ' * 6
        accepted, reason = reader_plan.judge('सब कुछ नहीं है नहीं है नहीं', loop, 'Devanagari')
        self.assertFalse(accepted)
        self.assertIn('upprepning', reason)

    def test_a_length_far_from_whispers_is_refused(self):
        long_text = 'मैं वापस आऊंगा और फिर कभी नहीं जाऊंगा क्योंकि यह मेरा घर है और मैं यहीं रहूंगा'
        self.assertFalse(reader_plan.judge('मैं वापस आऊंगा', long_text, 'Devanagari')[0])
        self.assertFalse(reader_plan.judge(long_text, 'मैं', 'Devanagari')[0])

    def test_a_run_whisper_barely_heard_may_still_be_read_but_not_a_whole_paragraph(self):
        self.assertTrue(reader_plan.judge('हां', 'हां ठीक है', 'Devanagari')[0])
        self.assertFalse(reader_plan.judge('हां', 'मैं वापस आऊंगा ' * 12, 'Devanagari')[0])

    def test_loop_detection_needs_real_repetition(self):
        self.assertTrue(reader_plan.has_loop('a b a b a b a b'.split()))
        self.assertFalse(reader_plan.has_loop('a b a b a c a b'.split()))
        self.assertFalse(reader_plan.has_loop('a a a'.split()))


class RunTests(unittest.TestCase):
    def test_neighbouring_cues_are_read_as_one_run_and_a_pause_starts_a_new_one(self):
        items = cues(HINDI[:2]) + [{'start': 20.0, 'end': 22.0, 'text': HINDI[2]}, {'start': 22.4, 'end': 24.0, 'text': HINDI[3]}]
        self.assertEqual(reader_plan.runs(items), [[0, 1], [2, 3]])

    def test_a_run_never_gets_longer_than_the_limit(self):
        long_cues = [{'start': n * 10.0, 'end': n * 10.0 + 9.0, 'text': 'क'} for n in range(5)]
        groups = reader_plan.runs(long_cues)
        self.assertEqual([index for group in groups for index in group], [0, 1, 2, 3, 4])
        self.assertTrue(all(long_cues[group[-1]]['end'] - long_cues[group[0]]['start'] <= 25.0 for group in groups))
        self.assertEqual(reader_plan.runs([]), [])

    def test_the_clip_of_a_run_is_padded_and_kept_inside_the_media(self):
        items = [{'start': 5.0, 'end': 7.0}, {'start': 7.5, 'end': 9.0}]
        self.assertEqual(reader_plan.run_window(items, [0, 1], 100.0), (4.8, 9.2))
        self.assertEqual(reader_plan.run_window([{'start': .1, 'end': 99.9}], [0], 100.0), (0.0, 100.0))


class DistributeTests(unittest.TestCase):
    def test_the_real_king_run_is_cut_into_the_same_three_cues_without_losing_a_word(self):
        pieces = reader_plan.distribute(KING, KING_RUN, 'Devanagari')
        self.assertTrue(pieces[0].endswith('समुद्र'), pieces[0])
        self.assertTrue(pieces[1].startswith('है उसमें'), pieces[1])
        self.assertEqual(pieces[2], 'सारी दुनिया को जो सुनाओ')
        self.assertEqual(' '.join(pieces).split(), KING_RUN.split(), 'every word of the run ends up in a cue, in order')

    def test_a_word_qwen_adds_follows_the_word_before_it_and_a_leading_one_goes_to_the_first_cue(self):
        pieces = reader_plan.distribute(['एक दो तीन', 'चार पांच'], 'शून्य एक दो तीन नया चार पांच', 'Devanagari')
        self.assertEqual(pieces, ['शून्य एक दो तीन नया', 'चार पांच'])

    def test_a_cue_whose_words_qwen_did_not_hear_gets_nothing(self):
        pieces = reader_plan.distribute(['एक दो तीन', 'चार पांच', 'छह सात'], 'एक दो तीन छह सात', 'Devanagari')
        self.assertEqual(pieces[0], 'एक दो तीन')
        self.assertIsNone(pieces[1])
        self.assertEqual(pieces[2], 'छह सात')

    def test_chinese_is_aligned_character_by_character(self):
        pieces = reader_plan.distribute(['我会回来的', '别担心'], '我会回来的别担心吧', 'CJK')
        self.assertEqual(pieces, ['我会回来的', '别担心吧'])

    def test_no_words_on_either_side_gives_nothing(self):
        self.assertEqual(reader_plan.distribute(['', ''], 'एक', 'Devanagari'), [None, None])
        self.assertEqual(reader_plan.distribute(['एक'], '', 'Devanagari'), [None])

    def test_a_similar_word_is_a_substitution_not_a_drop_and_an_add(self):
        owners = reader_plan.align(['लोटके', 'ना', 'होगा'], ['लौट', 'के', 'ना', 'आऊँगा'])
        self.assertEqual(owners[2], 1, 'ना matches ना')
        self.assertIsNotNone(owners[3], 'आऊँगा takes the place of होगा')


class ApplyTests(unittest.TestCase):
    def test_qwens_words_replace_whispers_and_every_cue_keeps_whispers_timing(self):
        original = cues(KING)
        result, record, run_record = reader_plan.apply_readings(original, [[0, 1, 2]], [KING_RUN], 'Devanagari')
        self.assertEqual(len(run_record), 1)
        self.assertTrue(run_record[0]['accepted'])
        for before, after in zip(original, result):
            self.assertEqual((after['start'], after['end'], after['confidence']), (before['start'], before['end'], before['confidence']))
        self.assertEqual(result[2]['text'], 'सारी दुनिया को जो सुनाओ')
        self.assertEqual([row['used'] for row in record], [True, True, True])
        self.assertEqual(original[0]['text'], KING[0], 'the input is not changed')

    def test_a_refused_run_keeps_every_cue_and_says_why(self):
        result, record, run_record = reader_plan.apply_readings(cues(HINDI[:2]), [[0, 1]], ['I will come back and tell my story'], 'Devanagari')
        self.assertEqual([cue['text'] for cue in result], HINDI[:2])
        self.assertFalse(run_record[0]['accepted'])
        self.assertTrue(all(not row['used'] and 'skrift' in row['reason'] for row in record))

    def test_the_same_text_is_not_a_change(self):
        result, record, _ = reader_plan.apply_readings(cues(HINDI[:2]), [[0, 1]], [' '.join(HINDI[:2])], 'Devanagari')
        self.assertEqual([row['reason'] for row in record], ['samma text', 'samma text'])
        self.assertEqual([cue['text'] for cue in result], HINDI[:2])

    def test_a_cue_that_would_swallow_far_more_than_it_had_keeps_whispers_text(self):
        texts = ['एक दो तीन चार पांच', 'छह सात आठ नौ दस']
        qwen = 'एक दो तीन चार पांच ' + 'अलग ' * 3 + 'बहुत ज्यादा नया लंबा शब्द यहां जुड़ गया है आज छह सात आठ नौ दस'
        result, record, _ = reader_plan.apply_readings(cues(texts), [[0, 1]], [qwen], 'Devanagari')
        self.assertEqual(result[0]['text'], texts[0])
        self.assertIn('längden', record[0]['reason'])

    def test_each_run_is_judged_on_its_own(self):
        items = cues(HINDI[:2]) + [{'start': 30.0, 'end': 32.0, 'text': HINDI[2]}]
        result, record, run_record = reader_plan.apply_readings(items, [[0, 1], [2]], ['मैं वापस आऊँगा। यह मेरी कहानी है।', 'wrong script text'], 'Devanagari')
        self.assertEqual([run['accepted'] for run in run_record], [True, False])
        self.assertEqual(result[0]['text'], 'मैं वापस आऊँगा।')
        self.assertEqual(result[2]['text'], HINDI[2])


class FakeModels:
    def __init__(self, answers=None, error_at=None):
        self.answers, self.error_at, self.calls, self.released = answers or [], error_at, [], 0

    def qwen(self, request, *args):
        number = len(self.calls)
        self.calls.append(args[1:])
        if self.error_at == number:
            raise RuntimeError('Qwen3-ASR HTTP 503')
        return {'text': self.answers[number] if number < len(self.answers) else ''}

    def release_qwen(self):
        self.released += 1


def run_step(items, models, enabled=True, setting='on'):
    job = {'id': 5}
    with patch.object(worker, 'subprocess') as sub, patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'), \
            patch.object(worker, 'audio_map', return_value='0:a:0'), patch.object(worker, 'QWEN_ASR_ENABLED', enabled), \
            patch.dict(os.environ, {'SUBSYNQ_READER_PLAN': setting}):
        sub.SubprocessError = Exception
        result = worker.read_cues_with_plan(job, Path('/media/x.mp4'), items, models, 60.0)
    return result, job


class WorkerStepTests(unittest.TestCase):
    def test_each_run_is_read_once_with_the_language_clue_and_the_words_are_cut_into_the_cues(self):
        models = FakeModels([KING_RUN])
        (merged, replaced), job = run_step(cues(KING), models)
        self.assertEqual(models.calls, [('Hindi',)], 'three neighbouring cues are one clip, one request')
        self.assertEqual(replaced, {0, 1, 2})
        self.assertEqual(merged[2]['text'], 'सारी दुनिया को जो सुनाओ')
        self.assertEqual(models.released, 1)
        plan = job['reader_plan']
        self.assertTrue(plan['chosen'])
        self.assertEqual((plan['replaced'], plan['kept'], plan['language']), (3, 0, 'Hindi'))
        self.assertEqual(plan['rows'][0]['whisper'], KING[0])
        self.assertEqual(plan['runs'][0]['qwen'], KING_RUN)

    def test_a_pause_makes_a_second_request(self):
        items = cues(HINDI[:2]) + [{'start': 30.0, 'end': 32.5, 'text': HINDI[2], 'confidence': .5}]
        models = FakeModels(['मैं वापस आऊँगा। यह मेरी कहानी है।', 'तुम कहाँ जा रहे हो'])
        (merged, replaced), job = run_step(items, models)
        self.assertEqual(len(models.calls), 2)
        self.assertEqual(replaced, {0, 2})
        self.assertEqual(len(job['reader_plan']['runs']), 2)

    def test_switched_off_the_step_never_asks_qwen_and_the_job_keeps_whispers_text(self):
        models = FakeModels([KING_RUN])
        (merged, replaced), job = run_step(cues(KING), models, setting='')
        self.assertEqual((replaced, models.calls), (set(), []))
        self.assertEqual([cue['text'] for cue in merged], KING)
        self.assertFalse(job['reader_plan']['chosen'])
        self.assertIn('avstängd', job['reader_plan']['reason'])

    def test_a_language_without_a_plan_never_asks_qwen(self):
        models = FakeModels()
        (merged, replaced), job = run_step(cues(['Det här är en svensk replik som är ganska lång.', 'Och här kommer en till replik.']), models)
        self.assertEqual((replaced, models.calls), (set(), []))
        self.assertFalse(job['reader_plan']['chosen'])
        self.assertIn('ingen läsarplan', job['reader_plan']['reason'])

    def test_a_switched_off_qwen_keeps_whisper(self):
        models = FakeModels()
        (merged, replaced), job = run_step(cues(HINDI), models, enabled=False)
        self.assertEqual((replaced, models.calls), (set(), []))
        self.assertEqual(job['reader_plan']['reason'], 'Qwen3-ASR är avstängt')

    def test_a_failure_keeps_all_of_whispers_text_and_never_fails_the_job(self):
        items = cues(HINDI[:2]) + [{'start': 30.0, 'end': 32.5, 'text': HINDI[2]}]
        models = FakeModels(['मैं वापस आऊँगा। यह मेरी कहानी है।'], error_at=1)
        (merged, replaced), job = run_step(items, models)
        self.assertEqual(replaced, set())
        self.assertEqual([cue['text'] for cue in merged], HINDI[:3])
        self.assertIn('misslyckades', job['reader_plan']['reason'])
        self.assertEqual(models.released, 1, 'Qwen is freed also after a failure')

    def test_a_cancelled_job_is_not_swallowed(self):
        job = {'id': 5}
        with patch.object(worker, 'subprocess'), patch.object(worker, 'update_job'), patch.object(worker, 'audio_map', return_value='0:a:0'), \
                patch.object(worker, 'QWEN_ASR_ENABLED', True), patch.dict(os.environ, ON), \
                patch.object(worker, 'check_cancelled', side_effect=worker.JobCancelled('stoppad')):
            models = FakeModels()
            with self.assertRaises(worker.JobCancelled):
                worker.read_cues_with_plan(job, Path('/media/x.mp4'), cues(HINDI), models, 60.0)
        self.assertEqual(models.released, 1)

    def test_the_step_sits_after_the_passes_and_before_the_trace_and_replaced_rows_lose_their_alternatives(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('merged = trim_small_overlaps(merged)'), source.index('merged, plan_replaced = read_cues_with_plan('))
        self.assertLess(source.index('merged, plan_replaced = read_cues_with_plan('), source.index('save_asr_trace(job, trace_payload)'))
        self.assertIn("if index in alternatives and index not in plan_replaced", source)
        self.assertIn("'reader_plan': job.get('reader_plan'),", source)
        self.assertIn("Qwen3-ASR läser texten på", source)


if __name__ == '__main__':
    unittest.main()
