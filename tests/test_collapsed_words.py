import copy
import json
import sys
import unittest
from pathlib import Path

sys.path[:0] = ['/app', '/ai']
from collapsed_words import plan_collapsed_word_checks, restore_collapsed_words
from source_judge import acceptable_choice, complete_cut_off_rows, repeats_neighbour


def evidence():
    # Actual raw job 186: both main passes heard up? but assigned it zero duration.
    words = [dict(text="What's", start=53.29, end=53.77, probability=.715),
             dict(text='up?', start=53.77, end=53.77, probability=.999),
             dict(text='First', start=53.91, end=54.17, probability=.995)]
    return [dict(profile=name, words=copy.deepcopy(words), no_speech_prob=.56, avg_logprob=-.22)
            for name in ('context', 'precision')]


def confirmation():
    # New CPU large-v2, selected English stream, 49–66 s, language en.
    return [dict(words=[dict(text="What's", start=53.66, end=53.82, probability=.644),
                        dict(text='up?', start=53.82, end=54., probability=.914),
                        dict(text='First', start=54.08, end=54.3, probability=.870)])]


class CollapsedWordTests(unittest.TestCase):
    row = dict(start=53.672, end=55.03, text="What's First time in Kyiv?", confidence=.715)

    def test_real_raw_failure_plans_the_measured_bounded_window(self):
        self.assertEqual(plan_collapsed_word_checks(evidence(), 120), [dict(start=49., end=66.)])
        self.assertEqual(plan_collapsed_word_checks(evidence(), 120, merged=[self.row]), [dict(start=49., end=66.)])
        self.assertEqual(plan_collapsed_word_checks(evidence(), 120, merged=[dict(self.row, text="What's up? First time in Kyiv?")]), [])
        self.assertEqual(plan_collapsed_word_checks(evidence(), 120, maximum_total=0), [])

    def test_timed_independent_reading_restores_only_the_dropped_word(self):
        original = copy.deepcopy((self.row, evidence(), confirmation()))
        rows, records = restore_collapsed_words([original[0]], original[1], original[2])
        self.assertEqual(rows[0]['text'], "What's up? First time in Kyiv?")
        self.assertEqual((rows[0]['start'], rows[0]['end']), (self.row['start'], self.row['end']))
        self.assertEqual(rows[0]['confirmed_source_words'], ['up'])
        self.assertEqual(records[0]['v2_words'][1]['end'], 54.)
        self.assertEqual(original, (self.row, evidence(), confirmation()))

    def test_one_raw_pass_or_one_repeated_profile_is_not_independent_evidence(self):
        for profiles in (evidence()[:1], [evidence()[0]] * 2):
            self.assertEqual(plan_collapsed_word_checks(profiles, 120), [])
            self.assertEqual(restore_collapsed_words([self.row], profiles, confirmation()), ([self.row], []))

    def test_raw_positive_v2_word_survives_an_overlapping_window_merge(self):
        raw = dict(confirmation()[0], profile='Whisper v2 kontrollerar ordfragment', no_speech_prob=.2, avg_logprob=-.3)
        profiles = evidence() + [raw]
        # The real full CPU run included the new v2 window, but another window
        # won the v2 cue merge and the positive up? interval disappeared again.
        self.assertEqual(restore_collapsed_words([self.row], profiles, [])[0][0]['text'], "What's up? First time in Kyiv?")
        for invalid in (dict(raw, profile='Whisper läser sammanhang'), dict(raw, no_speech_prob=.9), dict(raw, avg_logprob=-2)):
            self.assertEqual(restore_collapsed_words([self.row], evidence() + [invalid], []), ([self.row], []))

    def test_recorded_complete_cpu_run_keeps_the_positive_raw_v2_evidence(self):
        path = Path(__file__).with_name('fixtures') / 'collapsed-word-full-cpu.json'
        recorded = json.loads(path.read_text())
        fixed, changes = restore_collapsed_words(recorded['rows'], recorded['profiles'], [])
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['after'], "What's up? First time in Kyiv?")
        for old, new in zip(recorded['rows'], fixed):
            self.assertEqual((old['start'], old['end']), (new['start'], new['end']))
            if old['text'] != "What's First time in Kyiv?":
                self.assertEqual(old, new)

    def test_new_checks_keep_the_eight_window_budget_and_media_bounds(self):
        profiles, rows = [], []
        for offset in range(0, 400, 30):
            for profile in evidence():
                for word in profile['words']:
                    word['start'] += offset; word['end'] += offset
                profiles.append(profile)
            rows.append(dict(self.row, start=self.row['start'] + offset, end=self.row['end'] + offset))
        windows = plan_collapsed_word_checks(profiles, 470, merged=rows)
        self.assertEqual(len(windows), 8)
        self.assertTrue(all(0 <= window['start'] < window['end'] <= 470
                            and window['end'] - window['start'] <= 17 for window in windows))
        self.assertEqual(plan_collapsed_word_checks(evidence(), 54), [])

    def test_missing_weak_zero_length_shifted_or_wrong_anchor_confirmation_does_nothing(self):
        for failure in ('missing', 'weak', 'zero', 'shift', 'anchor', 'nan', 'outside'):
            heard = confirmation()
            if failure == 'missing': heard = []
            elif failure == 'weak': heard[0]['words'][1]['probability'] = .799
            elif failure == 'zero': heard[0]['words'][1]['end'] = 53.82
            elif failure == 'shift':
                for word in heard[0]['words']:
                    word['start'] += 1; word['end'] += 1
            elif failure == 'anchor': heard[0]['words'][0]['text'] = 'Something'
            elif failure == 'nan': heard[0]['words'][1]['end'] = float('nan')
            else: heard[0]['words'][1]['start'] = 53.6
            with self.subTest(failure=failure):
                self.assertEqual(restore_collapsed_words([self.row], evidence(), heard), ([self.row], []))

    def test_no_reinsertion_no_name_replacement_and_no_reader_plan_override(self):
        fixed = dict(self.row, text="What's up? First time in Kyiv?")
        self.assertEqual(restore_collapsed_words([fixed], evidence(), confirmation()), ([fixed], []))
        self.assertEqual(restore_collapsed_words([self.row], evidence(), confirmation(), skip_indices={0}), ([self.row], []))

    def test_invalid_raw_times_low_confidence_and_disagreement_do_not_plan(self):
        for failure in ('word', 'nan', 'probability', 'anchor', 'gap'):
            profiles = evidence()
            if failure == 'word': profiles[1]['words'][1]['text'] = 'down?'
            elif failure == 'nan': profiles[0]['words'][1]['start'] = float('nan')
            elif failure == 'probability': profiles[0]['words'][1]['probability'] = .79
            elif failure == 'anchor': profiles[0]['words'][0]['probability'] = .54
            else: profiles[0]['words'][2]['start'] += 1
            with self.subTest(failure=failure):
                self.assertEqual(plan_collapsed_word_checks(profiles, 120), [])


class ContractedBoundaryTests(unittest.TestCase):
    def test_real_german_tank_completion_does_not_swallow_the_next_cue(self):
        old = 'Some people call me a living cardboard figure, but my job'
        candidate = "Well, some people call me a living cardboard figure, but my job's a lot."
        following = 'is a lot more complex than that.'
        self.assertTrue(repeats_neighbour(candidate, old, '', following))
        self.assertFalse(acceptable_choice(old, candidate, 3, 1, following=following))
        rows = [dict(id=11, source=old, alternatives=[dict(text=candidate, support=3)]),
                dict(id=12, source=following)]
        self.assertEqual(complete_cut_off_rows(rows), (rows, []))

    def test_spoken_contractions_and_existing_repetitions_are_preserved(self):
        self.assertFalse(repeats_neighbour("My job's a lot more complex.", 'My job is complex.', '', 'Come here.'))
        self.assertFalse(repeats_neighbour("My job's a lot.", "My job's a lot.", '', 'is a lot more complex.'))
        self.assertTrue(repeats_neighbour('They have arrived.', 'They arrived.', "Yes, they've", ''))

    def test_the_original_literal_veto_is_never_relaxed_by_an_expansion(self):
        self.assertTrue(repeats_neighbour('My job is a lot.', "My job's a lot.", '', 'is a lot more complex.'))


if __name__ == '__main__':
    unittest.main()
