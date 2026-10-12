import copy
import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import reconcile_qwen_segments, restore_corroborated_address_titles


class AddressTitleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.base = [{'start': 25.0, 'end': 28.0, 'text': 'We can discuss that later.'},
                     {'start': 30.62, 'end': 32.15, 'text': 'Mora, are you coming?'}]
        self.rescue = [{'start': 28.68, 'end': 29.4, 'text': 'Dr.',
                        'confidence': .78, 'corroborated': True}]
        self.v2 = [{'start': 30.14, 'end': 30.75, 'text': 'Dr.', 'confidence': .59,
                    'words': [{'start': 30.22, 'end': 30.7, 'text': 'Dr.', 'probability': .59}]},
                   {'start': 30.75, 'end': 32.16, 'text': 'Mora, are you coming?',
                    'words': [{'start': 30.8, 'end': 30.94, 'text': 'Mora,', 'probability': .84},
                              {'start': 31.02, 'end': 31.18, 'text': 'are', 'probability': .81},
                              {'start': 31.18, 'end': 31.28, 'text': 'you', 'probability': .89},
                              {'start': 31.28, 'end': 32.08, 'text': 'coming?', 'probability': .9}]}]

    def test_confirmed_title_uses_v2_word_timing_and_preserves_the_name_question(self):
        original = copy.deepcopy((self.base, self.rescue, self.v2))
        result = restore_corroborated_address_titles(self.base, self.rescue, self.v2)
        self.assertEqual(result[0], self.base[0])
        self.assertEqual(result[1]['text'], 'Dr. Mora, are you coming?')
        self.assertEqual((result[1]['start'], result[1]['end']), (30.14, 32.15))
        self.assertEqual(result[1]['words'], self.v2[0]['words'] + self.v2[1]['words'])
        self.assertEqual((self.base, self.rescue, self.v2), original)
        self.assertNotIn('28.68', str(result[1]))

    def test_a_single_reader_unconfirmed_rescue_or_low_word_probability_cannot_restore(self):
        for failure in ('no_v2', 'no_rescue', 'unconfirmed', 'low_probability', 'different_title'):
            rescue, v2 = copy.deepcopy((self.rescue, self.v2))
            if failure == 'no_v2':
                v2 = []
            elif failure == 'no_rescue':
                rescue = []
            elif failure == 'unconfirmed':
                rescue[0]['corroborated'] = False
            elif failure == 'low_probability':
                v2[0]['words'][0]['probability'] = .3
            else:
                rescue[0]['text'] = 'Mr.'
            with self.subTest(failure=failure):
                self.assertEqual(restore_corroborated_address_titles(self.base, rescue, v2), self.base)

    def test_address_restoration_follows_existing_boundary_reconciliation(self):
        base = copy.deepcopy(self.base)
        base[1]['end'] = 32.16
        base.append({'start': 32.14, 'end': 33.0, 'text': 'Please wait.'})
        self.assertEqual(restore_corroborated_address_titles(base, self.rescue, self.v2), base)
        reconciled = reconcile_qwen_segments(base, [], [])
        result = restore_corroborated_address_titles(reconciled, self.rescue, self.v2)
        self.assertEqual(result[1]['text'], 'Dr. Mora, are you coming?')
        self.assertEqual((result[1]['start'], result[1]['end']), (30.14, 32.15))
        self.assertEqual(result[0], reconciled[0])
        self.assertEqual(result[2], reconciled[2])

    def test_conflicting_words_invalid_times_separate_answer_and_neighbor_overlap_are_kept(self):
        for failure in ('mismatch', 'word_mismatch', 'long_pause', 'word_overlap', 'no_answer',
                        'neighbor_overlap', 'duplicate_title', 'outside_word_time', 'nonfinite_time'):
            base, rescue, v2 = copy.deepcopy((self.base, self.rescue, self.v2))
            if failure == 'mismatch':
                base[1]['text'] = 'Mora, are you leaving?'
            elif failure == 'word_mismatch':
                v2[0]['words'][0]['text'] = 'Mrs.'
            elif failure == 'long_pause':
                v2[1]['start'] += 1
            elif failure == 'word_overlap':
                v2[0]['words'][0]['end'] = 31.0
            elif failure == 'no_answer':
                v2[0]['text'] = v2[0]['words'][0]['text'] = rescue[0]['text'] = 'No.'
            elif failure == 'neighbor_overlap':
                base[0]['end'] = 30.3
            elif failure == 'duplicate_title':
                base[1]['text'] = 'Dr. Mora, are you coming?'
            elif failure == 'outside_word_time':
                v2[0]['words'][0]['start'] = 26.0
            else:
                v2[0]['words'][0]['start'] = float('nan')
            with self.subTest(failure=failure):
                self.assertEqual(restore_corroborated_address_titles(base, rescue, v2), base)


if __name__ == '__main__':
    unittest.main()
