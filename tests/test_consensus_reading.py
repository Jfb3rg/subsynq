import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import (plan_consensus_windows, prefer_consensus_reading, restore_multi_pass_agreed_speech,
                         prefer_forced_language_reading, span_readings)


def cue(start, end, text, confidence=.5):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


def texts(rows):
    return [row['text'] for row in rows]


# Real HOPE readings (job 33): the final cue came from one forced re-read, the other passes read it alike.
HOPE = {
    'precision': [cue(30.1, 32.1, '대비군이라고 하자 8명인데요?', .44), cue(57.5, 58.8, '저희 집에 있다니까요', .18),
                  cue(67.8, 69.4, '우리가 아는 적은 아닙니다.', .55)],
    'rescue': [cue(30.3, 32.2, '대비문의를 받자 8명인데요?', .6), cue(56.4, 58.8, '국회문이 저희 집에 있다니까요.', .5),
               cue(66.8, 69.3, '우리가 아는 척은 아니니까.', .33)],
    'whispered_gap': [cue(30.8, 32.2, '대비문의를 받자 8명인데요', .63),
                      cue(56.9, 58.8, '국회의원이 저희 집에 있다니까요.', .46)],
    'v2': [cue(30.3, 32.4, '대비군이라고 봤자 여덟 명인데요?', .36), cue(56.3, 59.1, '그 괴물이 저희 집에 있다니까요', .39),
           cue(67.3, 69.4, '우리가 아는 척은 아닌데', .58)],
    'context': [cue(30.9, 32.2, '그대밖에 8명인데요.', .36), cue(58.1, 58.8, '내니까요.', .42)],
}
FINAL = [cue(24.4, 25.2, '상황실.', .32), cue(30.93, 32.13, '승리를 받자 8명인데요', .43)]


class RestoreAgreedSpeechTests(unittest.TestCase):
    def test_speech_the_passes_read_alike_is_added_where_no_cue_covers_it(self):
        readings = {name: [cue(48.6, 49.2, 'Tristan', 1.0)] for name in ('precision', 'rescue', 'v2')}
        result = restore_multi_pass_agreed_speech(FINAL, readings)
        self.assertEqual(texts(result), ['상황실.', '승리를 받자 8명인데요', 'Tristan'])
        self.assertEqual(result[:2], FINAL, 'the existing cues are untouched and keep their order')

    def test_lines_the_passes_read_with_different_words_are_left_out_rather_than_guessed(self):
        # "국회문이" (rescue) or "그 괴물이" (v2): the passes disagree about the words, so nothing is added.
        result = restore_multi_pass_agreed_speech(FINAL, HOPE)
        self.assertEqual(result, FINAL)

    def test_a_second_family_reading_breaks_the_tie_towards_the_right_words(self):
        readings = dict(HOPE, qwen=[cue(56.0, 59.4, '그 괴물이 저희 집에 있다니까요', .5)])
        added = [row for row in restore_multi_pass_agreed_speech(FINAL, readings) if 55 < row['start'] < 60]
        self.assertEqual(texts(added), ['그 괴물이 저희 집에 있다니까요'])
        self.assertLess(abs(added[0]['start'] - 56.4), .6, 'timing comes from the passes, not from the clip window')

    def test_two_passes_are_not_enough(self):
        readings = {'precision': [cue(5, 6, 'Tristan', 1.0)], 'rescue': [cue(5, 6, 'Tristan', 1.0)]}
        self.assertEqual(restore_multi_pass_agreed_speech([], readings), [])

    def test_without_a_voice_activity_filtered_pass_nothing_is_added(self):
        word = lambda: [cue(5, 6, 'Tristan', 1.0)]
        self.assertEqual(restore_multi_pass_agreed_speech([], {'rescue': word(), 'whispered_gap': word(), 'v2': word()}), [])
        self.assertEqual(len(restore_multi_pass_agreed_speech(
            [], {'precision': word(), 'rescue': word(), 'v2': word()})), 1)

    def test_stock_phrases_and_unsure_single_words_are_never_added(self):
        stock = {name: [cue(5, 6, 'Thank you.', .9)] for name in ('precision', 'rescue', 'v2')}
        self.assertEqual(restore_multi_pass_agreed_speech([], stock), [])
        unsure = {name: [cue(5, 6, 'Heeej', .3)] for name in ('precision', 'rescue', 'v2')}
        self.assertEqual(restore_multi_pass_agreed_speech([], unsure), [])

    def test_a_cue_that_would_overlap_a_final_cue_is_not_added(self):
        readings = {name: [cue(5, 7, 'Tristan et Jarbon', .9)] for name in ('precision', 'rescue', 'v2')}
        self.assertEqual(restore_multi_pass_agreed_speech([cue(6, 8, 'Hey')], readings), [cue(6, 8, 'Hey')])


class PreferConsensusReadingTests(unittest.TestCase):
    def test_a_doubtful_cue_read_differently_by_most_passes_takes_their_wording(self):
        result = prefer_consensus_reading(FINAL, HOPE, margin=.05)
        self.assertEqual(result[1]['text'], '대비군이라고 하자 8명인데요?')
        self.assertEqual((result[1]['start'], result[1]['end']), (30.93, 32.13), 'the timing is never changed')

    def test_a_confident_cue_is_never_changed(self):
        sure = [dict(FINAL[1], confidence=.7)]
        self.assertEqual(prefer_consensus_reading(sure, HOPE, margin=.05), sure)

    def test_a_cue_the_passes_do_not_clearly_disagree_with_is_left_alone(self):
        self.assertEqual(prefer_consensus_reading(FINAL, HOPE, margin=.5), FINAL)

    def test_a_reading_of_something_else_is_not_used(self):
        other = [cue(30.93, 32.13, 'Hello how are you today', .4)]
        self.assertEqual(prefer_consensus_reading(other, HOPE, margin=.0), other)

    def test_the_input_is_not_modified(self):
        before = [dict(row) for row in FINAL]
        prefer_consensus_reading(FINAL, HOPE, margin=.05)
        self.assertEqual(FINAL, before)


class PlanConsensusWindowsTests(unittest.TestCase):
    def test_speech_nobody_wrote_down_and_doubtful_cues_get_a_window_the_first_ones_first(self):
        windows = plan_consensus_windows(FINAL, HOPE)
        self.assertEqual(len(windows), 3)
        self.assertTrue(all(window['end'] - window['start'] <= 12.6 for window in windows))
        self.assertGreater(windows[0]['start'], 55, 'uncovered speech is asked about before a doubtful cue')
        self.assertTrue(any(29 < window['start'] < 31 for window in windows), 'the doubtful cue at 30.9 s')

    def test_a_confident_cue_that_the_passes_agree_with_needs_no_window(self):
        agreed = {name: [cue(5, 6, 'Hello there', .9)] for name in ('precision', 'rescue', 'v2')}
        self.assertEqual(plan_consensus_windows([cue(5, 6, 'Hello there', .9)], agreed), [])

    def test_the_number_of_windows_is_capped(self):
        many = {name: [cue(index * 20, index * 20 + 2, f'line number {index}', .8) for index in range(12)]
                for name in ('precision', 'rescue', 'v2')}
        self.assertEqual(len(plan_consensus_windows([], many, maximum_total=4)), 4)



# Real HOPE readings (job 36) of rows 1 and 2: the final cues came from the multilingual pass; the passes cut the
# same speech into pieces, and a forced-Korean reading without voice-activity filter read the right words.
ROW_1 = cue(6.2, 13.1, '삐꾸라지 마냥 삐끄러운 거를 20분쯤 걸었는데', .55)
ROW_2 = cue(13.1, 16.3, '갑자기 기증이.', .66)
HOPE_PIECES = {
    'precision': [cue(6.2, 13.1, '삐꾸라지 마냥 삐끄러운 거를 20분쯤 걸었는데', .55), cue(13.1, 16.3, '갑자기 기증이.', .66)],
    'context': [cue(6.4, 7.9, '삐꾸라지 마냥.', .8), cue(8.6, 9.8, '삐꾸라지 마냥.', .66),
                cue(11.5, 16.3, '20분쯤 걸었는데 갑자기 기증이.', .6)],
    'rescue': [cue(6.5, 7.9, '삐꾸라지 마냥', .71), cue(8.5, 10.0, '미끄러운 거리', .71), cue(10.5, 13.2, '한 20분쯤 걸었는데', .61),
               cue(14.3, 16.7, '갑자기 기적이 들리는 거야', .42)],
    'v2': [cue(7.0, 7.9, '삐꾸라지마냥', .77), cue(7.9, 10.2, '미끄덩거려.', .78), cue(11.1, 13.4, '한 20분쯤 걸었는데', .85),
           cue(13.9, 16.9, '갑자기 기적이 들린거야.', .49)],
    'forced': [cue(7.0, 7.8, '미꾸라지 마냥', .6), cue(9.0, 10.0, '미끄러운 거래', .6), cue(11.0, 13.1, '한 20분쯤 걸었는데', .8),
               cue(14.0, 16.8, '갑자기 기적이 들리는 거래', .6)],
}


class JoinedPiecesAndForcedReadingTests(unittest.TestCase):
    def test_the_pieces_of_a_pass_inside_a_cue_are_joined_into_one_reading(self):
        joined = span_readings([ROW_1], HOPE_PIECES)
        self.assertEqual(joined['forced+'][0]['text'], '미꾸라지 마냥 미끄러운 거래 한 20분쯤 걸었는데')
        self.assertEqual(joined['rescue+'][0]['text'], '삐꾸라지 마냥 미끄러운 거리 한 20분쯤 걸었는데')
        self.assertNotIn('precision+', joined, 'one piece is not a join')

    def test_pieces_belonging_to_other_cues_are_not_joined(self):
        joined = span_readings([ROW_2], HOPE_PIECES)
        self.assertNotIn('rescue+', joined)

    def test_a_cue_the_forced_reading_and_other_models_contradict_is_corrected_even_at_a_higher_confidence(self):
        result = prefer_consensus_reading([ROW_1, ROW_2], HOPE_PIECES, margin=.05)
        self.assertIn('기적이', result[1]['text'])
        self.assertNotIn('기증', result[1]['text'])
        self.assertEqual((result[1]['start'], result[1]['end']), (13.1, 16.3), 'the timing is never changed')

    def test_the_joined_pieces_decide_a_cue_the_passes_cut_differently(self):
        # Rescue, v2 and the forced reading all cut the speech in pieces and all read "미꾸라지"; the cue says "삐꾸라지".
        agreed = dict(HOPE_PIECES,
                      rescue=[cue(6.5, 7.9, '미꾸라지 마냥', .71), cue(8.5, 10.0, '미끄러운 거리', .71),
                              cue(10.5, 13.2, '한 20분쯤 걸었는데', .61)],
                      v2=[cue(7.0, 7.9, '미꾸라지마냥', .77), cue(7.9, 10.2, '미끄덩거려.', .78),
                          cue(11.1, 13.4, '한 20분쯤 걸었는데', .85)])
        result = prefer_consensus_reading([ROW_1], agreed, margin=.05)
        self.assertIn('미꾸라지', result[0]['text'])

    def test_a_cue_all_passes_but_one_read_alike_keeps_its_wording(self):
        # Only the forced reading says "미꾸라지": one voice against the other model families changes nothing.
        result = prefer_consensus_reading([ROW_1], HOPE_PIECES, margin=.05)
        self.assertEqual(result[0]['text'], ROW_1['text'])

    def test_one_other_family_alone_does_not_override_a_cue_above_the_ordinary_ceiling(self):
        only_v2 = {name: rows for name, rows in HOPE_PIECES.items() if name != 'forced'}
        only_v2['rescue'] = [row for row in only_v2['rescue'] if not row['text'].startswith('갑자기')]
        self.assertEqual(prefer_consensus_reading([ROW_2], only_v2, margin=.05)[0]['text'], ROW_2['text'])

    def test_closing_credit_boilerplate_is_ignored_in_every_step(self):
        boiler = {name: [cue(89.4, 112.7, '한글자막 제공 및 자막 제공 및 광고를 포함하고 있습니다.', .5)]
                  for name in ('precision', 'rescue', 'forced')}
        self.assertEqual(restore_multi_pass_agreed_speech([], boiler), [])
        self.assertEqual(plan_consensus_windows([], boiler), [])



FORCED = [cue(0.8, 5.5, '며칠 전에 친구들이랑 사냥을 해나갔었어.', .62), cue(6.2, 10.1, '삐꾸라지 마냥 삐끄러운 거래.', .53),
          cue(10.6, 13.2, '한 20분쯤 걸었는데', .90), cue(14.3, 16.9, '갑자기 기적이 들리는 거래.', .63),
          cue(29.2, 32.2, '예비군이래 봤자 8명인데요?', .48),
          cue(89.4, 112.7, '한글자막 제공 및 자막 제공 및 광고를 포함하고 있습니다.', .5)]
ORDINARY = [cue(0.8, 5.5, '며칠 전에 친구들이랑 사냥을 해나갔었어.', .62),
            cue(6.7, 13.2, '삐꾸라지 마냥 삐끄러운 거를 20분쯤 걸었는데', .53), cue(14.2, 16.4, '갑자기 기침이...', .52),
            cue(30.9, 32.1, '승리를 받자 8명인데요.', .43)]


class ForcedLanguageReadingTests(unittest.TestCase):
    def test_a_more_confident_forced_reading_of_the_same_speech_replaces_the_wording_only(self):
        result = prefer_forced_language_reading(ORDINARY, FORCED)
        self.assertEqual(texts(result), ['며칠 전에 친구들이랑 사냥을 해나갔었어.', '삐꾸라지 마냥 삐끄러운 거래. 한 20분쯤 걸었는데',
                                         '갑자기 기적이 들리는 거래.', '예비군이래 봤자 8명인데요?'])
        self.assertEqual([(row['start'], row['end']) for row in result], [(row['start'], row['end']) for row in ORDINARY])

    def test_a_cue_that_is_at_least_as_confident_is_kept(self):
        sure = [dict(row, confidence=.7) for row in ORDINARY]
        self.assertEqual(texts(prefer_forced_language_reading(sure, FORCED)), texts(sure))

    def test_a_different_sentence_at_the_same_time_is_never_used(self):
        other = [cue(14.2, 16.4, '완전히 다른 문장입니다', .5)]
        self.assertEqual(prefer_forced_language_reading(other, FORCED), other)

    def test_boilerplate_in_the_forced_reading_is_never_used(self):
        cue_in_credits = [cue(90.0, 100.0, '한글자막 제공 및 광고', .3)]
        self.assertEqual(prefer_forced_language_reading(cue_in_credits, FORCED), cue_in_credits)

    def test_without_a_forced_reading_nothing_changes(self):
        self.assertEqual(prefer_forced_language_reading(ORDINARY, []), ORDINARY)
        self.assertEqual(prefer_forced_language_reading(ORDINARY, None), ORDINARY)



# Real HOPE readings (job 38): the forced reading was the most confident one for the first cue but had dropped words.
OTHERS_38 = {
    'precision': [cue(0.8, 5.5, '며칠 전에 친구들이랑 사냥을 해나갔었어.', .61),
                  cue(6.2, 13.1, '삐꾸라지 마냥 삐끄러운 거를 20분쯤 걸었는데', .55), cue(13.1, 16.3, '갑자기 기증이.', .66)],
    'rescue': [cue(1.6, 5.5, '이전에 친구들이랑 사냥을 해나갔었어', .65), cue(6.6, 7.9, '삐꾸라지 마냥', .71),
               cue(8.6, 10.0, '미끄러운 거리', .70), cue(10.5, 13.2, '한 20분쯤 걸었는데', .59),
               cue(14.3, 16.7, '갑자기 기적이 들리는 거야', .38)],
    'v2': [cue(3.3, 6.3, '친구들이랑 사냥을 나갈 수 있었어.', .48), cue(7.0, 7.9, '삐꾸라지마냥', .77),
           cue(7.9, 10.2, '미끄덩거려.', .78), cue(11.1, 13.4, '한 20분쯤 걸었는데', .85),
           cue(13.9, 16.9, '갑자기 기적이 들린거야.', .49)],
}
FORCED_38 = [cue(0.7, 1.8, '며칠 전에', .81), cue(2.9, 5.6, '친구들이랑 사냥을 있었어', .89),
             cue(6.7, 7.9, '삐꾸라지 마냥', .77), cue(8.6, 10.0, '미끄러운 거리', .72),
             cue(10.4, 13.1, '한 20분쯤 걸었는데', .74), cue(13.1, 16.6, '갑자기 기적이 들리는', .85)]
FINAL_38 = [cue(0.8, 5.5, '며칠 전에 친구들이랑 사냥을 해나갔었어.', .61),
            cue(6.2, 13.1, '삐꾸라지 마냥 삐끄러운 거를 20분쯤 걸었는데', .55), cue(13.1, 16.3, '갑자기 기증이.', .66)]


class ForcedReadingMustNotDropWordsTests(unittest.TestCase):
    def test_a_confident_forced_reading_that_dropped_words_does_not_replace_a_cue_the_other_passes_agree_with(self):
        result = prefer_forced_language_reading(FINAL_38, FORCED_38, OTHERS_38)
        self.assertEqual(result[0]['text'], '며칠 전에 친구들이랑 사냥을 해나갔었어.')

    def test_the_same_reading_still_corrects_the_cues_the_other_passes_disagree_with(self):
        result = prefer_forced_language_reading(FINAL_38, FORCED_38, OTHERS_38)
        self.assertEqual(result[2]['text'], '갑자기 기적이 들리는')
        self.assertEqual(result[1]['text'], '삐꾸라지 마냥 미끄러운 거리 한 20분쯤 걸었는데')

    def test_without_the_other_passes_the_confidence_alone_decides(self):
        result = prefer_forced_language_reading(FINAL_38, FORCED_38)
        self.assertEqual(result[0]['text'], '며칠 전에 친구들이랑 사냥을 있었어')


if __name__ == '__main__':
    unittest.main()
