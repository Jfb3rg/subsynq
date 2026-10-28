import copy
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, '/app')
from asr_windows import _normalized, remove_adjacent_phrase_overlap


class PhraseOverlapTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / 'fixtures' / 'man-of-war-duplicate-phrase.json').read_text())

    def run_case(self, fixture=None, **options):
        f = fixture or self.fixture
        return remove_adjacent_phrase_overlap(f['base'], f['v3'], f['v2'], **options)

    def test_real_duplicate_is_removed_once_without_losing_its_existing_lead(self):
        original = copy.deepcopy(self.fixture)
        rows, changes = self.run_case()
        self.assertEqual(len(changes), 1)
        self.assertEqual(rows[0]['text'], "This is what's left simple people")
        self.assertEqual(rows[1]['text'], "We're trying to live their lives, trying to survive!")   # no capital mid-sentence (dev job 262)
        self.assertEqual((rows[0]['end'], rows[1]['start']), (93.01, 93.01))
        self.assertEqual((rows[0]['start'], rows[1]['end']), (87.93, 96.27))
        self.assertEqual(rows[0]['confirmed_lead_onset'], self.fixture['base'][0]['confirmed_lead_onset'])
        old = Counter(_normalized(' '.join(r['text'] for r in original['base'])).split())
        new = Counter(_normalized(' '.join(r['text'] for r in rows)).split())
        self.assertEqual(old - new, Counter('trying to live their lives'.split()))
        self.assertFalse(new - old)
        self.assertEqual(self.fixture, original)

    def test_a_name_after_the_moved_lead_keeps_its_capital(self):
        fixture = copy.deepcopy(self.fixture)
        for key in ('base', 'v3', 'v2'):
            for row in fixture[key]:
                row['text'] = row['text'].replace('Trying', 'Riley').replace('trying', 'Riley')
                for word in row.get('words') or ():
                    word['text'] = word['text'].replace('Trying', 'Riley').replace('trying', 'Riley')
        fixture['base'].append({'start': 120.0, 'end': 121.0, 'text': 'We need Riley now.'})
        rows, changes = self.run_case(fixture)
        if changes:
            self.assertTrue(rows[1]['text'].split(' ', 1)[1].startswith('Riley'))
        from asr_windows import _is_name_like
        self.assertTrue(_is_name_like('Riley', [{'text': 'We need Riley now.'}]))
        self.assertFalse(_is_name_like('Trying', [{'text': 'Trying to live. We are trying.'}]))

    def test_a_second_timed_occurrence_in_either_family_preserves_real_repetition(self):
        for family in ('v3', 'v2'):
            f = copy.deepcopy(self.fixture)
            cue = copy.deepcopy(next(r for r in f[family] if 'trying to live' in r['text'].lower()))
            cue['start'] -= 2
            cue['end'] -= 2
            for word in cue['words']:
                word['start'] -= 2
                word['end'] -= 2
                word['probability'] = .1
            f[family].append(cue)
            self.assertEqual(self.run_case(f), (f['base'], []))

    def test_an_extra_reader_hearing_two_repetitions_vetoes_removal(self):
        extra = [{'start': 90., 'end': 96., 'text': 'Trying to live their lives. Trying to live their lives!'}]
        self.assertEqual(self.run_case(corroboration=extra), (self.fixture['base'], []))

    def test_both_families_and_four_strong_word_anchors_are_required(self):
        f = copy.deepcopy(self.fixture)
        f['v2'] = []
        self.assertEqual(self.run_case(f), (f['base'], []))
        f = copy.deepcopy(self.fixture)
        f['v2'][0]['words'][1]['probability'] = .6
        self.assertEqual(self.run_case(f), (f['base'], []))

    def test_disagreeing_times_or_invalid_word_times_are_not_used(self):
        for mode in ('shift', 'nan', 'zero'):
            f = copy.deepcopy(self.fixture)
            if mode == 'shift':
                for word in f['v2'][0]['words']:
                    word['start'] += .4
                    word['end'] += .4
            elif mode == 'nan':
                f['v2'][0]['words'][0]['start'] = float('nan')
            else:
                f['v2'][0]['words'][0]['end'] = f['v2'][0]['words'][0]['start']
            self.assertEqual(self.run_case(f), (f['base'], []))

    def test_a_stale_word_list_or_absent_complete_left_cue_vetoes_the_change(self):
        f = copy.deepcopy(self.fixture)
        f['v2'][0]['text'] = 'Different words.'
        self.assertEqual(self.run_case(f), (f['base'], []))
        f = copy.deepcopy(self.fixture)
        f['v3'] = [r for r in f['v3'] if _normalized(r['text']) != "this is what s left simple people"]
        self.assertEqual(self.run_case(f), (f['base'], []))

    def test_protected_rows_prefixes_and_words_are_kept(self):
        self.assertEqual(self.run_case(skip_indices={0}), (self.fixture['base'], []))
        self.assertEqual(self.run_case(skip_indices={1}), (self.fixture['base'], []))
        for where, key, value in ((1, 'confirmed_prefix', 'Trying to live'),
                                  (1, 'confirmed_lead_onset', {'start': 93.93}),
                                  (0, 'confirmed_source_words', ['trying'])):
            f = copy.deepcopy(self.fixture)
            f['base'][where][key] = value
            self.assertEqual(self.run_case(f), (f['base'], []))

    def test_short_matching_words_are_never_enough(self):
        f = copy.deepcopy(self.fixture)
        f['base'][0]['text'] = 'The plan will work now'
        f['base'][1]['text'] = 'work now and start again'
        self.assertEqual(self.run_case(f), (f['base'], []))
