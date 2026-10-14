import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import plan_consensus_windows, prefer_consensus_reading, restore_multi_pass_agreed_speech


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


if __name__ == '__main__':
    unittest.main()
