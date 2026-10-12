import copy
import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import (restore_corroborated_address_titles,
                         restore_corroborated_intervening_cues,
                         plan_address_title_confirmations)


def cue(text, start, duration=.2):
    words = [{'text': token, 'start': start + i * duration,
              'end': start + (i + 1) * duration, 'probability': .8}
             for i, token in enumerate(text.split())]
    return {'text': text, 'start': start, 'end': words[-1]['end'],
            'words': words, 'confidence': .8}


class JoinedTitleTests(unittest.TestCase):
    def setUp(self):
        self.v2 = [cue('Dr. Mora, are you coming?', 30.2)]
        self.v3 = [cue('Dr. Mora, are you coming?', 30.1)]
        self.confirmations = [cue('Dr. Mora, are you coming?', 30.12)]
        self.base = [{'text': 'Wait here.', 'start': 27, 'end': 29},
                     {'text': 'Mora, are you coming?', 'start': 30.5, 'end': 31.42},
                     {'text': 'Yes.', 'start': 32, 'end': 33}]

    def test_joined_title_keeps_question_end_and_neighbours_without_mutating_inputs(self):
        original = copy.deepcopy((self.base, self.v3, self.v2, self.confirmations))
        result = restore_corroborated_address_titles(*original[:3], original[3])
        self.assertEqual(result[1]['text'], 'Dr. Mora, are you coming?')
        self.assertEqual((result[1]['start'], result[1]['end']), (30.2, 31.42))
        self.assertEqual(result[::2], self.base[::2])
        self.assertEqual(original, (self.base, self.v3, self.v2, self.confirmations))

    def test_unconfirmed_conflicting_incomplete_or_invalid_readings_cannot_add_a_title(self):
        for failure in ('missing', 'different_title', 'different_body', 'untimed', 'low',
                        'nan', 'overlap', 'neighbor', 'late_end', 'already_present', 'long_pause'):
            base, rescue, v2, confirmations = copy.deepcopy(
                (self.base, self.v3, self.v2, self.confirmations))
            if failure == 'missing':
                confirmations = []
            elif failure == 'different_title':
                confirmations = [cue('Mrs. Mora, are you coming?', 30.12)]
            elif failure == 'different_body':
                confirmations = [cue('Dr. Mora, are you leaving?', 30.12)]
            elif failure == 'untimed':
                v2[0]['words'].pop()
            elif failure == 'low':
                v2[0]['words'][0]['probability'] = .3
            elif failure == 'nan':
                v2[0]['words'][0]['probability'] = float('nan')
            elif failure == 'overlap':
                v2[0]['words'][0]['end'] += .3
            elif failure == 'neighbor':
                base[0]['end'] = 30.3
            elif failure == 'late_end':
                base[1]['end'] = 31.1
            elif failure == 'already_present':
                base[1]['text'] = 'Dr. Mora, are you coming?'
            else:
                v2[0]['words'][0]['end'] = 30.21
                v2[0]['words'][1]['start'] = 30.59
            with self.subTest(failure=failure):
                self.assertEqual(restore_corroborated_address_titles(base, rescue, v2, confirmations), base)


class InterveningCueTests(unittest.TestCase):
    def setUp(self):
        self.v2 = [cue('You owe us an explanation.', 13.96),
                   cue('We should discuss this later.', 16.02)]
        self.rescue = [cue('You owe us an explanation.', 13.86),
                       cue('We should discuss this later.', 15.94)]
        self.confirmations = [cue('You owe us an explanation.', 13.84),
                              cue('We should discuss this later.', 15.92)]
        self.base = [{'text': 'Enough!', 'start': 12.5, 'end': 13.32},
                     {'text': 'We should discuss this later.', 'start': 14.28, 'end': 17.08},
                     {'text': 'Goodbye.', 'start': 18, 'end': 19}]

    def test_missing_sentence_and_next_onset_need_two_complete_independent_views(self):
        original = copy.deepcopy((self.base, self.rescue, self.confirmations, self.v2))
        result = restore_corroborated_intervening_cues(*original)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[1], self.v2[0])
        self.assertEqual(result[2], {**self.base[1], 'start': 16.02})
        self.assertEqual(result[0], self.base[0])
        self.assertEqual(result[3], self.base[2])
        self.assertEqual(original, (self.base, self.rescue, self.confirmations, self.v2))

    def test_no_invention_or_retiming_with_conflicting_missing_low_or_invalid_evidence(self):
        for failure in ('no_v2', 'no_confirmation', 'missing_word', 'different_anchor',
                        'low_missing', 'low_anchor', 'long_word', 'nan', 'word_overlap',
                        'neighbor_overlap', 'wrong_end', 'short_end', 'already_present', 'not_overlapping'):
            base, rescue, confirmations, v2 = copy.deepcopy(
                (self.base, self.rescue, self.confirmations, self.v2))
            if failure == 'no_v2':
                v2 = []
            elif failure == 'no_confirmation':
                confirmations = []
            elif failure == 'missing_word':
                confirmations[0] = cue('You owe an explanation.', 13.84)
            elif failure == 'different_anchor':
                confirmations[1] = cue('We should forget this later.', 15.92)
            elif failure == 'low_missing':
                v2[0]['words'][0]['probability'] = .3
            elif failure == 'low_anchor':
                v2[1]['words'][0]['probability'] = .3
            elif failure == 'long_word':
                v2[0]['words'][0]['start'] -= 2
                v2[0]['start'] -= 2
            elif failure == 'nan':
                v2[0]['words'][0]['end'] = float('nan')
            elif failure == 'word_overlap':
                v2[0]['words'][0]['end'] += .2
            elif failure == 'neighbor_overlap':
                base[0]['end'] = 14
            elif failure == 'wrong_end':
                base[1]['end'] += 1
            elif failure == 'short_end':
                base[1]['end'] = 16.9
            elif failure == 'already_present':
                base.insert(1, copy.deepcopy(v2[0]))
            else:
                base[1]['start'] = 15.5
            with self.subTest(failure=failure):
                self.assertEqual(restore_corroborated_intervening_cues(base, rescue, confirmations, v2), base)


class TitleProbePlanningTests(unittest.TestCase):
    def test_previous_title_name_can_plan_but_cannot_add_an_unheard_title(self):
        base = [cue('Dr.', 10), cue('Mora, welcome.', 10.3),
                cue('Mora, are you coming?', 31.6)]
        windows = plan_address_title_confirmations(base, [], [], 40, include_seen_names=True)
        self.assertEqual(windows, [{'start': 28., 'end': 35.}])
        self.assertEqual(restore_corroborated_address_titles(base, [], [], []), base)

    def test_unknown_names_and_already_titled_addresses_are_not_rechecked(self):
        base = [cue('Dr. Mora, welcome.', 10), cue('Mira, are you coming?', 31.6),
                cue('Dr. Mora, are you coming?', 35)]
        self.assertEqual(plan_address_title_confirmations(base, [], [], 40, include_seen_names=True), [])
        self.assertEqual(plan_address_title_confirmations(base, [], [], 40,
                         include_seen_names=True, maximum_total=0), [])

    def test_title_confirmation_rejects_an_unconfirmed_body(self):
        prefix, body = cue('Dr.', 30.2), cue('Mora, are you coming?', 30.4)
        base = [{'text': body['text'], 'start': 30.4, 'end': 31.2}]
        first = [{**prefix, 'corroborated': True}, body]
        second = [prefix, body]
        self.assertEqual(restore_corroborated_address_titles(base, first, [prefix, body], second)[0]['text'],
                         'Dr. Mora, are you coming?')
        self.assertEqual(restore_corroborated_address_titles(base, first, [prefix, body], [prefix]), base)

    def test_sentence_padding_overlap_does_not_discard_nonoverlapping_title_words(self):
        prefix = cue('Dr.', 30.22, .48)
        prefix.update(start=30.14, end=30.78)
        body = cue('Mora, are you coming?', 30.8, .2)
        body.update(start=30.72, end=31.68)
        base = [{'text': body['text'], 'start': 30.62, 'end': 31.68}]
        rescue = [{**prefix, 'corroborated': True}]
        result = restore_corroborated_address_titles(base, rescue, [prefix, body])
        self.assertEqual(result[0]['text'], 'Dr. Mora, are you coming?')
        self.assertEqual((result[0]['start'], result[0]['end']), (30.14, 31.68))
        prefix['words'][0]['end'] = 30.9
        self.assertEqual(restore_corroborated_address_titles(base, rescue, [prefix, body]), base)

    def test_two_offset_titles_plan_bounded_views_but_never_change_source(self):
        base = [{'text': 'Mora, are you coming?', 'start': 31.6, 'end': 33.1}]
        rescue = [cue('Dr.', 29.8, .56)]
        confirmations = [cue('Dr.', 30.3, .52)]
        old = copy.deepcopy((base, rescue, confirmations))
        self.assertEqual(plan_address_title_confirmations(base, rescue, confirmations, 40),
                         [{'start': 28., 'end': 36.1}])
        self.assertEqual((base, rescue, confirmations), old)
        self.assertEqual(plan_address_title_confirmations(base, rescue, confirmations, 34)[0]['end'], 34)

    def test_unheard_conflicting_low_or_already_present_titles_plan_nothing(self):
        base = [{'text': 'Mora, are you coming?', 'start': 31.6, 'end': 33.1}]
        rescue = [cue('Dr.', 29.8, .56)]
        confirmation = [cue('Dr.', 30.3, .52)]
        for changes in [[], [cue('Mrs.', 30.3)], [{**confirmation[0], 'confidence': .3}]]:
            self.assertEqual(plan_address_title_confirmations(base, rescue, changes, 40), [])
        self.assertEqual(plan_address_title_confirmations(
            [{**base[0], 'text': 'Dr. Mora, are you coming?'}], rescue, confirmation, 40), [])
        self.assertEqual(plan_address_title_confirmations(
            [{**base[0], 'start': 35}], rescue, confirmation, 40), [])


if __name__ == '__main__':
    unittest.main()
