import copy
import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import plan_v2_prefix_confirmations, restore_confirmed_v2_prefixes


class ConfirmedPrefixTests(unittest.TestCase):
    def fixtures(self):
        row = {'start': 10., 'end': 15.,
               'text': "know stars are like this trade it's about light and"}
        candidate = {'start': 9.32, 'end': 15., 'confidence': .8,
                     'text': 'You ' + row['text'], 'words': [
                         {'start': 9.4, 'end': 9.9, 'text': 'You', 'probability': .8}]}
        plan = plan_v2_prefix_confirmations([row], [candidate], 60.)[0]
        return row, candidate, {**plan,
            'text': "You know, stars are like this trade. It's about light and darkness."}

    def test_confirmed_prefix_and_punctuation_preserve_body_end_and_neighbours(self):
        row, candidate, probe = self.fixtures()
        neighbour = {'start': 16., 'end': 17., 'text': 'Nothing changes here.'}
        inputs = copy.deepcopy([row, neighbour, candidate, probe])
        result = restore_confirmed_v2_prefixes([row, neighbour], [probe])
        self.assertEqual(result[0], {**row, 'start': 9.32,
            'text': "You know, stars are like this trade. It's about light and"})
        self.assertEqual(result[1], neighbour)
        self.assertEqual(inputs, [row, neighbour, candidate, probe])

    def test_a_pronoun_guess_cannot_restore_or_complete_a_fragment(self):
        row, _, probe = self.fixtures()
        for text in ['', "He know stars are like this trade it's about light and",
                     "You know stars are like this job it's about light and",
                     "You know stars are like this trade it's about light"]:
            with self.subTest(text=text):
                self.assertEqual(restore_confirmed_v2_prefixes(
                    [row], [{**probe, 'text': text}]), [row])

    def test_invalid_v2_evidence_does_not_request_another_reader(self):
        row, candidate, _ = self.fixtures()
        variants = [{**candidate, 'confidence': .3},
                    {**candidate, 'text': 'You know a completely different thing.'},
                    {**candidate, 'text': 'Oh ' + row['text']},
                    {**candidate, 'start': 1.},
                    {**candidate, 'end': float('nan')},
                    {**candidate, 'words': []}]
        for change in [{'probability': .3}, {'probability': float('nan')},
                       {'end': 10.4}, {'end': 13.}, {'start': float('nan')}]:
            variants.append({**candidate, 'words': [{**candidate['words'][0], **change}]})
        for changed in variants:
            with self.subTest(candidate=changed):
                self.assertEqual(plan_v2_prefix_confirmations([row], [changed], 60.), [])
        self.assertEqual(plan_v2_prefix_confirmations([row], [candidate, candidate], 60.), [])

    def test_new_overlap_long_cue_changed_base_or_word_overlap_cannot_restore(self):
        row, candidate, probe = self.fixtures()
        neighbour = {'start': 8., 'end': 9.8, 'text': 'Previous line.'}
        self.assertEqual(restore_confirmed_v2_prefixes([neighbour, row], [probe]), [neighbour, row])
        for changed in [{**row, 'end': 16.5}, {**row, 'text': row['text'] + 'extra'},
                        {**row, 'words': [{'start': 9.7, 'end': 10.2, 'text': 'know'}]}]:
            self.assertEqual(restore_confirmed_v2_prefixes([changed], [probe]), [changed])
        long_row = {**row, 'end': 16.5}
        long_candidate = {**candidate, 'end': 16.5}
        long_probe = {**plan_v2_prefix_confirmations([long_row], [long_candidate], 60.)[0],
                      'text': probe['text']}
        self.assertEqual(restore_confirmed_v2_prefixes([long_row], [long_probe]), [long_row])

    def test_short_or_already_complete_cues_do_not_request_confirmation(self):
        row, candidate, _ = self.fixtures()
        for changed in [{**row, 'text': 'know it'},
                        {**row, 'text': 'You ' + row['text']},
                        {**row, 'text': row['text'].capitalize()}]:
            self.assertEqual(plan_v2_prefix_confirmations([changed], [candidate], 60.), [])

    def test_one_missing_trailing_token_is_retained_only_if_qwen_hears_it(self):
        row, candidate, _ = self.fixtures()
        candidate['text'] = ' '.join(candidate['text'].split()[:-1])
        plans = plan_v2_prefix_confirmations([row], [candidate], 60.)
        self.assertEqual(len(plans), 1)
        self.assertEqual(restore_confirmed_v2_prefixes([row], [
            {**plans[0], 'text': candidate['text']}]), [row])
