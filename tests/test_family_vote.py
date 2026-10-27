import sys
import unittest
from pathlib import Path

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
from asr_windows import restore_family_voted_speech, restore_multi_pass_agreed_speech
import worker


def cue(start, end, text, confidence=.5):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


def readings(raw):
    return {name: [cue(*item) for item in items] for name, items in raw.items()}


# Real HOPE readings (dev job 69, 0.8.276). The burned-in English subtitles say what was spoken:
# 21 s "My God…", 22.5 "Dispatch. Dispatch.", 48 "That's when all hell broke loose.", 51 "Chief." and 52.5 "Chief?",
# 57 "The monster is at my house.", 66 "It's not a species we're familiar with.", 75 "Monster or not, that's just not right!"
HOPE = readings({
    'context': [(24.45, 25.17, '상왕 씨.', .30), (25.25, 29.31, '죄다 삿불 끈다고 빠져나가서 그 사람들이 모자랄 테니까.', .58), (58.10, 58.80, '그러니까요.', .53)],
    'precision': [(22.00, 25.23, '상황실 상황실', .67), (57.54, 58.78, '저희 집에 있다니까요', .16), (67.76, 69.36, '우리가 아는 척은 아닙니다.', .53)],
    'rescue': [(19.84, 21.36, '세상에', .84), (23.08, 25.18, '상황실 상황실', .61), (56.42, 58.90, '국회문 절집에 있다니 깨워.', .57),
               (66.96, 68.92, '우리가 아는 척은', .83), (32.12, 34.54, '1대', .29), (37.72, 39.19, '2대', .43)],
    'whispered_gap': [(47.69, 50.65, '어마어마한 무시한 싸움을 입으러 뛸거에요', .47), (50.65, 51.57, '소장님', .67), (52.40, 53.09, '소장님', .59),
                      (56.91, 58.77, '국회문이 저희 집에 있다니까요', .56)],
    'v2': [(22.64, 23.56, '세상에...', .49), (23.56, 24.70, '상황실!', .85), (56.42, 59.78, '그 괴물 저희 집에 있다니깨요', .29),
           (65.00, 69.88, '이거는 우리가 아는 척은 아니겠지.', .33), (73.88, 77.00, '아무리 괴물이라도 그럴 순 없는 거예요.', .30)],
    'qwen': [(56.12, 60.08, '그 괴물이 절 집에 있대니까요.', .5), (64.70, 70.18, '이건 우리가 아는 종이 아니야.', .5), (21.70, 25.53, '상황실, 상황실.', .5)],
    'forced': [(16.82, 21.36, '이 세상에', .60), (23.72, 24.58, '상황실!', .78), (47.76, 50.48, '어마어마한 무시한 싸움이 벌어질 거야', .45), (50.48, 51.56, '사장님', .51),
               (52.44, 53.10, '사장님', .55), (56.22, 58.84, '국회는 저희 있겠니때로', .53), (66.56, 69.32, '우리가 아는 척은 아닙니다.', .57),
               (73.90, 76.92, '아무리 괴물이라도 그럴 순 없는 거예요.', .73)],
})
FINAL = [cue(.8, 5.5, '며칠 전에 친구들이랑 사냥을 해나갔었어'), cue(6.7, 13.2, '미꾸라지 마냥 미끄러운 거래 한 20분쯤 걸었는데'),
         cue(14.2, 16.4, '갑자기 기침이...'), cue(25.4, 29.3, '죄다 삿불 끊다고 빠져나가서 사람들이 모자랄텐데'), cue(30.9, 32.1, '예비 봤자 8명인데요?')]


def added(result):
    return [row for row in result if row not in FINAL]


def near(rows, start):
    return [row for row in rows if abs(row['start'] - start) < 1.0]


class HopeTests(unittest.TestCase):
    def test_the_older_rule_leaves_all_of_these_lines_out(self):
        self.assertEqual(restore_multi_pass_agreed_speech(FINAL, HOPE), FINAL)

    def test_two_families_reading_a_line_alike_bring_it_back_without_a_voice_filtered_pass(self):
        result = restore_family_voted_speech(FINAL, HOPE)
        self.assertEqual(result[:2], FINAL[:2], 'the existing cues are untouched')
        new = added(result)
        self.assertEqual(len(new), 8, [row['text'] for row in new])
        for start in (19.84, 23.56, 47.7, 50.6, 52.4, 56.4, 66.6, 73.9):
            self.assertTrue(near(new, start), f'a restored cue near {start}')

    def test_the_outvoted_family_does_not_decide_the_words(self):
        """Whisper v3 read "국회문" twice; v2 and Qwen read "괴물": the monster line is what the subtitles say."""
        line = near(added(restore_family_voted_speech(FINAL, HOPE)), 56.4)[0]
        self.assertIn('괴물', line['text'])
        self.assertNotIn('국회', line['text'])
        self.assertLess(abs(line['start'] - 56.4), .6, 'timing comes from the passes, not from the clip window')

    def test_demanding_an_independent_family_keeps_only_what_v2_or_qwen_confirms(self):
        new = added(restore_family_voted_speech(FINAL, HOPE, independent_families=True))
        self.assertEqual(sorted(round(row['start']) for row in new), [24, 56, 67, 74])
        self.assertTrue(all('괴물' in row['text'] or '상황실' in row['text'] or '우리가' in row['text'] for row in new))

    def test_three_families_are_a_stricter_vote(self):
        new = added(restore_family_voted_speech(FINAL, HOPE, minimum_families=3))
        self.assertEqual(sorted(round(row['start']) for row in new), [24, 56, 67])

    def test_a_stricter_agreement_leaves_the_loosest_matches_out(self):
        loose = len(added(restore_family_voted_speech(FINAL, HOPE, minimum_agreement=.6)))
        strict = len(added(restore_family_voted_speech(FINAL, HOPE, minimum_agreement=.85)))
        self.assertGreater(loose, strict)
        self.assertEqual(sorted(round(row['start']) for row in added(restore_family_voted_speech(FINAL, HOPE, minimum_agreement=.85))), [22, 74])


class GuardTests(unittest.TestCase):
    def test_one_family_reading_the_same_line_twice_is_one_vote(self):
        word = lambda: [cue(5, 6.5, '저희 집에 있다니까요', .9)]
        self.assertEqual(restore_family_voted_speech([], {'rescue': word(), 'whispered_gap': word(), 'precision': word()}), [])

    def test_families_that_read_different_words_add_nothing(self):
        raw = {'v2': [cue(5, 7, '그 괴물이 저희 집에 있다니까요', .8)], 'qwen': [cue(5, 7, '내일 아침에 만나서 같이 가요', .5)]}
        self.assertEqual(restore_family_voted_speech([], raw), [])

    def test_nothing_is_added_where_a_cue_already_is(self):
        raw = {'v2': [cue(5, 7, '저희 집에 있다니까요', .8)], 'qwen': [cue(5, 7, '저희 집에 있다니까요.', .5)]}
        existing = [cue(5.2, 6.8, '이미 있는 대사')]
        self.assertEqual(restore_family_voted_speech(existing, raw), existing)

    def test_stock_phrases_and_an_unsure_lone_word_are_never_added(self):
        stock = {'v2': [cue(5, 6, 'Thank you', .9)], 'qwen': [cue(5, 6, 'Thank you.', .5)]}
        self.assertEqual(restore_family_voted_speech([], stock), [])
        unsure = {'v2': [cue(5, 6, 'Tristan', .3)], 'forced': [cue(5, 6, 'Tristan', .3)]}
        self.assertEqual(restore_family_voted_speech([], unsure), [])
        sure = {'v2': [cue(5, 6, 'Tristan', .9)], 'forced': [cue(5, 6, 'Tristan', .3)]}
        self.assertEqual(len(restore_family_voted_speech([], sure)), 1)

    def test_a_reading_longer_than_twelve_seconds_is_not_added(self):
        raw = {'v2': [cue(5, 20, '한참 이어지는 긴 이야기를 계속 하고 있어요', .8)], 'qwen': [cue(5, 20, '한참 이어지는 긴 이야기를 계속 하고 있어요.', .5)]}
        self.assertEqual(restore_family_voted_speech([], raw), [])

    def test_boilerplate_closing_credits_are_ignored(self):
        raw = {'v2': [cue(5, 7, '한글자막 제공 및 광고를 포함하고 있습니다', .8)], 'qwen': [cue(5, 7, '한글자막 제공 및 광고를 포함하고 있습니다.', .5)]}
        self.assertEqual(restore_family_voted_speech([], raw), [])

    def test_the_input_is_not_changed_and_an_empty_input_is_fine(self):
        before = [dict(row) for row in FINAL]
        restore_family_voted_speech(FINAL, HOPE)
        self.assertEqual(FINAL, before)
        self.assertEqual(restore_family_voted_speech([], {}), [])
        self.assertEqual(restore_family_voted_speech(FINAL, {}), FINAL)


class WorkerHookTests(unittest.TestCase):
    def test_the_vote_runs_right_after_the_older_rule_and_before_the_consensus_wording(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        older = source.index('merged = restore_multi_pass_agreed_speech(merged, consensus_readings)')
        vote = source.index('merged = restore_family_voted_speech(merged, consensus_readings)')
        wording = source.index('merged = prefer_consensus_reading(merged, consensus_readings, margin=.05)')
        self.assertLess(older, vote)
        self.assertLess(vote, wording)
        self.assertIn('family_vote_added=family_vote_added,', source)
        self.assertIn('if FAMILY_VOTE_ENABLED and focus_intervals is None:', source)

    def test_it_can_be_switched_off_by_the_environment(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn("os.environ.get('SUBSYNQ_FAMILY_VOTE', '1')", source)


if __name__ == '__main__':
    unittest.main()
