"""Restore a voted line using complete, measured words, never by clipping it."""
import copy
import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import restore_family_voted_speech, restore_multi_pass_agreed_speech


class RestoreTimingTests(unittest.TestCase):
    functions = (restore_multi_pass_agreed_speech, restore_family_voted_speech)

    def setUp(self):
        self.text = 'They stormed the orphanage.'
        self.neighbor = {'start': 31.91, 'end': 37.95,
                         'text': "name is stanislav kanyev he's got quite a reputation i need him waiting to kiev now",
                         'confidence': .42578125}
        self.readings = {
            'precision': [dict(start=30.37, end=32.07, text=self.text, confidence=.6435546875)],
            'rescue': [dict(start=30.29, end=31.69, text=self.text, confidence=.59228515625,
                            words=[dict(start=s, end=e, text=t, probability=p) for s, e, t, p in
                                   [(30.37, 30.85, 'They', .59228515625),
                                    (30.85, 31.33, 'stormed', .980224609375),
                                    (31.33, 31.37, 'the', .99609375),
                                    (31.37, 31.61, 'orphanage.', .998291015625)]])],
            'v2': [dict(start=30.6, end=32.14, text=self.text, confidence=.5224609375)],
            'qwen': [dict(start=29.99, end=32.44, text=self.text, confidence=.5)]}

    def check_blocked(self):
        for fn in self.functions:
            with self.subTest(fn=fn.__name__):
                self.assertEqual(fn([self.neighbor], self.readings), [self.neighbor])

    def test_real_line_uses_a_complete_nonoverlapping_reading(self):
        before = copy.deepcopy(self.readings)
        for fn in self.functions:
            with self.subTest(fn=fn.__name__):
                rows = fn([self.neighbor], self.readings)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]['text'], self.text)
                self.assertEqual((rows[0]['start'], rows[0]['end']), (30.29, 31.69))
                self.assertEqual(rows[1], self.neighbor)
                self.assertLessEqual(rows[0]['end'], rows[1]['start'])
                self.assertEqual(fn(rows, self.readings), rows, 'restoration is idempotent')
        self.assertEqual(self.readings, before)

    def test_without_measured_words_the_overlap_is_not_waived(self):
        self.readings['rescue'][0].pop('words')
        self.check_blocked()

    def test_a_different_wording_cannot_supply_the_timing(self):
        self.readings['rescue'][0]['text'] = 'They burned the orphanage.'
        self.check_blocked()

    def test_incomplete_or_unreliable_words_do_not_supply_timing(self):
        for damage in ('missing', 'low_confidence', 'outside', 'reversed'):
            with self.subTest(damage=damage):
                row = self.readings['rescue'][0]
                saved = copy.deepcopy(row['words'])
                if damage == 'missing': row['words'].pop()
                if damage == 'low_confidence': row['words'][0]['probability'] = .2
                if damage == 'outside': row['words'][-1]['end'] = 32.1
                if damage == 'reversed': row['words'][1]['end'] = 30.7
                self.check_blocked()
                row['words'] = saved

    def test_a_qwen_clip_bound_is_not_a_word_timing(self):
        self.readings['qwen'][0] = self.readings.pop('rescue')[0]
        self.check_blocked()

    def test_genuine_overlap_is_still_rejected(self):
        self.neighbor['start'] = 31.5
        self.check_blocked()

    def test_an_existing_copy_cannot_be_added_again(self):
        self.neighbor['text'] = self.text + ' His name is Stanislav.'
        self.check_blocked()

    def test_a_following_line_can_use_its_already_measured_later_start(self):
        self.neighbor = dict(start=25, end=30.5, text='John, I need your help.', confidence=.8)
        for row in self.readings.values():
            row[0]['start'] = 30.3
        rescue = self.readings['rescue'][0]
        rescue['start'], rescue['end'] = 30.6, 32.1
        for word in rescue['words']:
            word['start'] += .3
            word['end'] += .3
        for fn in self.functions:
            rows = fn([self.neighbor], self.readings)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], self.neighbor)
            self.assertEqual(rows[1]['start'], 30.6)

    def test_no_conflict_keeps_the_selected_reading_unchanged(self):
        for fn in self.functions:
            rows = fn([], self.readings)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0], self.readings['precision'][0])


if __name__ == '__main__':
    unittest.main()
