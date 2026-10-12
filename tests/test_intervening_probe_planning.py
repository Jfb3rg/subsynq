import copy
import sys
import unittest
sys.path.insert(0, '/app')
from asr_windows import plan_intervening_cue_confirmations, restore_corroborated_intervening_cues
from test_corroborated_missing_cues import cue


class InterveningProbePlanningTests(unittest.TestCase):
    def setUp(self):
        self.readings = [cue('You owe us an explanation.', 13.96),
                         cue('We should discuss this later.', 16.02)]
        self.base = [{'text': 'Enough!', 'start': 12.5, 'end': 13.32},
                     {'text': self.readings[1]['text'], 'start': 14.28, 'end': 17.08},
                     {'text': 'Goodbye.', 'start': 18, 'end': 19}]

    def test_uncertain_word_can_plan_but_cannot_restore_text(self):
        self.readings[0]['words'][-1]['probability'] = .496
        before = copy.deepcopy((self.base, self.readings))
        self.assertEqual(plan_intervening_cue_confirmations(self.base, self.readings, 20),
                         [{'start': 0., 'end': 19.}])
        self.assertEqual(restore_corroborated_intervening_cues(
            self.base, self.readings, self.readings, self.readings), self.base)
        self.assertEqual((self.base, self.readings), before)

    def test_bad_times_missing_anchor_existing_sentence_and_neighbor_are_not_planned(self):
        for failure in ('nan', 'overlap', 'untimed', 'anchor', 'already', 'neighbor', 'onset'):
            base, reading = copy.deepcopy((self.base, self.readings))
            if failure == 'nan': reading[0]['words'][0]['end'] = float('nan')
            elif failure == 'overlap': reading[0]['words'][0]['end'] += .4
            elif failure == 'untimed': reading[0]['words'].pop()
            elif failure == 'anchor': reading[1]['text'] = 'A different next sentence.'
            elif failure == 'already': base.insert(1, copy.deepcopy(reading[0]))
            elif failure == 'neighbor': base[0]['end'] = 14
            else: base[1]['start'] = 15.5
            with self.subTest(failure=failure):
                self.assertEqual(plan_intervening_cue_confirmations(base, reading, 20), [])

    def test_budget_and_media_bounds(self):
        self.assertEqual(plan_intervening_cue_confirmations(self.base, self.readings, 20,
                                                          maximum_total=0), [])
        base, reading = [], []
        for offset in range(0, 200, 30):
            base.extend({**r, 'start': r['start'] + offset, 'end': r['end'] + offset} for r in self.base)
            for r in self.readings:
                reading.append({**r, 'start': r['start'] + offset, 'end': r['end'] + offset,
                                'words': [{**w, 'start': w['start'] + offset, 'end': w['end'] + offset}
                                          for w in r['words']]})
        windows = plan_intervening_cue_confirmations(base, reading, 220)
        self.assertEqual(len(windows), 4)
        self.assertTrue(all(0 <= w['start'] < w['end'] <= 220 and w['end'] - w['start'] <= 26
                            for w in windows))

    def test_fresh_complete_readings_can_correct_next_end_with_neighbors_protected(self):
        base = copy.deepcopy(self.base)
        base[1]['end'] = 16.9
        self.assertEqual(restore_corroborated_intervening_cues(
            base, self.readings, self.readings, self.readings), base)
        result = restore_corroborated_intervening_cues(
            base, self.readings, self.readings, self.readings, allow_confirmed_end=True)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[2], {**base[1], 'start': self.readings[1]['start'],
                                    'end': self.readings[1]['end']})
        self.assertEqual(result[0], base[0])
        self.assertEqual(result[-1], base[-1])
        base[-1]['start'] = 17.01
        self.assertEqual(restore_corroborated_intervening_cues(
            base, self.readings, self.readings, self.readings, allow_confirmed_end=True), base)

    def test_fresh_reading_with_stretched_word_cannot_confirm(self):
        stretched = copy.deepcopy(self.readings)
        stretched[0]['start'] -= 1.5
        stretched[0]['words'][0]['start'] -= 1.5
        self.assertEqual(restore_corroborated_intervening_cues(
            self.base, stretched, self.readings, self.readings, allow_confirmed_end=True), self.base)

    def test_new_end_permission_does_not_relax_word_or_agreement_requirements(self):
        for failure in ('low_v2', 'low_v3', 'missing_confirmation', 'different_anchor', 'nan'):
            first, second, v2 = copy.deepcopy((self.readings, self.readings, self.readings))
            if failure == 'low_v2': v2[0]['words'][-1]['probability'] = .54
            elif failure == 'low_v3': first[1]['words'][0]['probability'] = .54
            elif failure == 'missing_confirmation': second = []
            elif failure == 'different_anchor': second[1] = cue('We should forget this later.', 16.02)
            else: second[0]['words'][0]['end'] = float('nan')
            with self.subTest(failure=failure):
                self.assertEqual(restore_corroborated_intervening_cues(
                    self.base, first, second, v2, allow_confirmed_end=True), self.base)
