import copy
import json
from pathlib import Path
import unittest

from asr_windows import correct_confirmed_word_onsets


class ConfirmedOnsetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(Path(__file__).with_name('fixtures').joinpath('fortitude-confirmed-onset.json').read_text())

    def run_onset(self):
        return correct_confirmed_word_onsets(self.fixture['final'], self.fixture['rescue'], self.fixture['v2'])

    def test_full_actual_audio_keeps_every_word_end_and_other_row(self):
        original = copy.deepcopy(self.fixture)
        result, changes = self.run_onset()
        expected = copy.deepcopy(self.fixture['final'])
        expected[1]['start'] = 10.62
        self.assertEqual(result, expected)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['rescue_start'], 10.62)
        self.assertEqual(changes[0]['v2_start'], 10.96)
        self.assertEqual(self.fixture, original)

    def test_one_reader_is_insufficient(self):
        for key in ('rescue', 'v2'):
            with self.subTest(key=key):
                saved = self.fixture[key]
                self.fixture[key] = []
                self.assertEqual(self.run_onset(), (self.fixture['final'], []))
                self.fixture[key] = saved

    def test_disagreeing_onsets_do_not_move_the_row(self):
        for word in self.fixture['v2'][0]['words']:
            word['start'] += .4
            word['end'] += .4
        self.assertEqual(self.run_onset(), (self.fixture['final'], []))

    def test_a_different_word_is_not_a_timing_confirmation(self):
        self.fixture['v2'][0]['words'][0]['text'] = 'Who'
        self.fixture['v2'][0]['text'] = self.fixture['v2'][0]['text'].replace('You', 'Who')
        self.assertEqual(self.run_onset(), (self.fixture['final'], []))

    def test_uncertain_stretched_invalid_and_overlapping_words_do_not_move_the_row(self):
        for modification in ({'probability': .59}, {'probability': float('nan')}, {'start': -1},
                             {'end': 14.0}, {'start': float('inf')}, {'end': 11.8}):
            with self.subTest(modification=modification):
                original = copy.deepcopy(self.fixture)
                self.fixture['v2'][0]['words'][0].update(modification)
                self.assertEqual(self.run_onset(), (self.fixture['final'], []))
                self.fixture = original

    def test_missing_or_stale_word_arrays_do_not_confirm_text(self):
        for key in ('rescue', 'v2'):
            with self.subTest(key=key):
                original = copy.deepcopy(self.fixture)
                self.fixture[key][0].pop('words')
                self.assertEqual(self.run_onset(), (self.fixture['final'], []))
                self.fixture = original

    def test_experimental_reader_plan_rows_are_not_changed(self):
        self.assertEqual(correct_confirmed_word_onsets(self.fixture['final'], self.fixture['rescue'], self.fixture['v2'],
                                                      skip_indices={1}), (self.fixture['final'], []))

    def test_a_conflicting_valid_reading_is_not_silently_outvoted(self):
        earlier = copy.deepcopy(self.fixture['rescue'][0])
        earlier['words'][0]['start'] = 9.5
        earlier['words'][0]['end'] = 10.08
        self.fixture['rescue'].append(earlier)
        self.assertEqual(self.run_onset(), (self.fixture['final'], []))


if __name__ == '__main__':
    unittest.main()
