import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asr_windows import plan_tail_confirmation, restore_confirmed_tail, split_confirmed_tail_candidate, plan_tail_timing_windows


class ConfirmedTailTests(unittest.TestCase):
    def test_timing_windows_have_fixed_budget_and_half_second_offset(self):
        self.assertEqual(plan_tail_timing_windows({'start': 8.25}, 60), [
            {'start': 10., 'end': 22.}, {'start': 10.5, 'end': 22.5}])
        self.assertEqual(plan_tail_timing_windows({'start': 8.25}, 10.25), [
            {'start': 10., 'end': 10.25}])

    def setUp(self):
        self.base = [{'start': 10., 'end': 20., 'text': 'I told everyone.'}]
        self.candidate = {'start': 21., 'end': 22.4, 'text': 'I am coming.',
                          'confidence': .9, 'words': [
                              {'text': word, 'probability': .9,
                               'start': 21. + index * .4, 'end': 21.4 + index * .4}
                              for index, word in enumerate(('I', 'am', 'coming.'))]}
        self.probe = {'segments': [self.candidate],
                      'independent_text': 'I told everyone. I am coming.',
                      'speech': [{'start': 21., 'end': 22.5}]}

    def test_plan_only_bounded_edge_not_whole_long_outro(self):
        self.assertEqual(plan_tail_confirmation(self.base, 180), [
            {'start': 14., 'end': 26., 'target_start': 20., 'target_end': 26.}])
        self.assertEqual(plan_tail_confirmation([], 180), [])
        self.assertEqual(plan_tail_confirmation(self.base, 30), [])

    def test_independent_words_times_and_speech_restore_tail(self):
        before = copy.deepcopy(self.base)
        self.assertEqual(restore_confirmed_tail(self.base, [self.probe]),
                         self.base + [self.candidate])
        self.assertEqual(self.base, before)

    def test_missing_confirmation_speech_or_confidence_keeps_original(self):
        for change in ('independent', 'speech', 'confidence', 'mean', 'remote', 'long'):
            p = copy.deepcopy(self.probe)
            if change == 'independent':
                p['independent_text'] = 'Music only.'
            elif change == 'speech':
                p['speech'] = []
            elif change == 'confidence':
                p['segments'][0]['confidence'] = .59
            elif change == 'mean':
                p['segments'][0]['words'][0]['probability'] = .1
            elif change == 'remote':
                p['segments'][0].update(start=30., end=32.)
            else:
                p['segments'][0]['end'] = 27.
            with self.subTest(change=change):
                self.assertEqual(restore_confirmed_tail(self.base, [p]), self.base)

    def test_stock_music_hallucination_still_rejected_even_when_models_agree(self):
        p = copy.deepcopy(self.probe)
        p['segments'][0]['text'] = 'Thank you for watching.'
        p['independent_text'] = 'Thank you for watching.'
        p['segments'][0]['words'] = [{'text': word, 'probability': .9,
            'start': 21. + i * .2, 'end': 21.2 + i * .2}
            for i, word in enumerate(('Thank', 'you', 'for', 'watching.'))]
        self.assertEqual(restore_confirmed_tail(self.base, [p]), self.base)

    def test_exact_incomplete_prefix_moves_without_rewriting_other_words(self):
        base = [{'start': 15., 'end': 21.5, 'text': 'I told everyone. I'}]
        result = restore_confirmed_tail(base, [self.probe])
        self.assertEqual(result[0], {'start': 15., 'end': 21., 'text': 'I told everyone.'})
        self.assertEqual(result[1], self.candidate)
        self.assertEqual(base[0]['text'], 'I told everyone. I')

    def test_overlap_without_exact_partial_prefix_does_not_replace_source(self):
        base = [{'start': 15., 'end': 21.5, 'text': 'I told everyone. You'}]
        self.assertEqual(restore_confirmed_tail(base, [self.probe]), base)

    def test_one_word_or_unknown_word_confidences_are_not_enough(self):
        for change in ('single', 'untimed'):
            p = copy.deepcopy(self.probe)
            if change == 'single':
                p['segments'][0]['text'] = 'Coming.'
            else:
                p['segments'][0]['words'] = []
            self.assertEqual(restore_confirmed_tail(self.base, [p]), self.base)

    def test_script_vowel_marks_must_not_be_discarded_as_exact_agreement(self):
        p = copy.deepcopy(self.probe)
        p['segments'][0]['text'] = 'वह गई।'
        p['independent_text'] = 'वह गया।'
        for word, text in zip(p['segments'][0]['words'], ('वह', 'गई।')):
            word['text'] = text
        p['segments'][0]['words'] = p['segments'][0]['words'][:2]
        self.assertEqual(restore_confirmed_tail(self.base, [p]), self.base)

    def test_unicode_equivalent_spelling_and_punctuation_can_agree(self):
        p = copy.deepcopy(self.probe)
        p['segments'][0]['text'] = 'Café ouvert.'
        p['independent_text'] = 'Cafe\u0301 ouvert!'
        for word, text in zip(p['segments'][0]['words'], ('Café', 'ouvert.')):
            word['text'] = text
        p['segments'][0]['words'] = p['segments'][0]['words'][:2]
        self.assertEqual(len(restore_confirmed_tail(self.base, [p])), 2)

    def test_words_must_really_have_times_and_match_candidate_text(self):
        for change in ('untimed', 'mismatched'):
            p = copy.deepcopy(self.probe)
            if change == 'untimed':
                p['segments'][0]['words'][0].pop('start')
            else:
                p['segments'][0]['words'][0]['text'] = 'They'
            self.assertEqual(restore_confirmed_tail(self.base, [p]), self.base)

    def test_confirmation_evidence_saved_with_both_source_reports(self):
        sys.path.insert(0, '/ai')
        import worker
        job = {'id': -1, 'tail_confirmation': {'probes': [self.probe], 'restored': True}}
        rows = [{'id': 0, 'start': 21., 'end': 22.4, 'source': 'I am coming.'}]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            for raw in (True, False):
                path = worker.save_source_transcript(job, Path('/example.mp4'), rows, 'CPU', 'ASR', raw=raw)
                saved = json.loads(path.read_text())
                self.assertEqual(saved['tail_confirmation'], job['tail_confirmation'])
                self.assertEqual(saved['rows'], rows)

    def merged_example(self):
        base = [{'start': 14., 'end': 21.5, 'text': 'The whole town heard my story I'}]
        texts = 'The whole town heard my story I am coming.'.split()
        spans = [(17., 17.5), (17.5, 18.), (18., 18.5), (18.5, 19.),
                 (19., 19.5), (19.5, 20.1), (20.1, 21.8), (21.8, 22.1), (22.1, 22.4)]
        cue = {'text': ' '.join(texts), 'start': 16.92, 'end': 22.48, 'confidence': .9,
               'words': [{'text': text, 'start': start, 'end': end, 'probability': .9}
                         for text, (start, end) in zip(texts, spans)]}
        probe = {'segments': [cue], 'independent_text': cue['text'],
                 'speech': [{'start': 20., 'end': 22.5}]}
        return base, cue, probe

    def test_exact_timed_anchor_extracts_new_phrase_without_losing_known_prefix(self):
        base, cue, probe = self.merged_example()
        split = split_confirmed_tail_candidate(base[-1], cue)
        self.assertEqual(split['text'], 'I am coming.')
        self.assertEqual(split['words'], cue['words'][-3:])
        self.assertEqual(cue['text'], 'The whole town heard my story I am coming.')
        # Its stretched onset is NOT used as a new subtitle timestamp.
        self.assertEqual(restore_confirmed_tail(base, [probe]), base)

    def test_shifted_timing_confirmation_can_restore_same_phrase(self):
        base, cue, probe = self.merged_example()
        confirmation = {**self.probe, 'timing_confirmation_for': {
            'text': 'I am coming.', 'end': 22.48,
            'first_word_start': 20.1, 'first_word_end': 21.8},
            'independent_window': {'start': 14., 'end': 26.}}
        result = restore_confirmed_tail(base, [probe, confirmation])
        self.assertEqual(result[0]['text'], 'The whole town heard my story')
        self.assertEqual(result[0]['end'], 21.)
        self.assertEqual(result[1]['text'], 'I am coming.')

    def test_wrong_confirmation_text_end_or_onset_never_supplies_guessed_times(self):
        base, cue, probe = self.merged_example()
        for change in ('text', 'end', 'onset', 'outside'):
            confirmation = copy.deepcopy(self.probe)
            confirmation['timing_confirmation_for'] = {
                'text': 'I am coming.', 'end': 22.48,
                'first_word_start': 20.1, 'first_word_end': 21.8}
            confirmation['independent_window'] = {'start': 14., 'end': 26.}
            if change == 'text':
                confirmation['timing_confirmation_for']['text'] = 'They are coming.'
            elif change == 'end':
                confirmation['timing_confirmation_for']['end'] = 24.
            elif change == 'onset':
                confirmation['timing_confirmation_for']['first_word_end'] = 20.5
            else:
                confirmation['independent_window']['end'] = 22.
            with self.subTest(change=change):
                self.assertEqual(restore_confirmed_tail(base, [probe, confirmation]), base)

    def test_different_prefix_or_unknown_word_times_do_not_split(self):
        base, cue, probe = self.merged_example()
        for change in ('prefix', 'times', 'text'):
            altered = copy.deepcopy(cue)
            if change == 'prefix':
                altered['text'] = altered['text'].replace('town', 'city')
                altered['words'][2]['text'] = 'city'
            elif change == 'times':
                altered['words'][0].pop('start')
            else:
                altered['words'][0]['text'] = 'A'
            self.assertEqual(split_confirmed_tail_candidate(base[-1], altered), altered)

    def test_complete_known_word_is_not_borrowed_into_new_phrase(self):
        base, cue, _ = self.merged_example()
        base[0].update(end=20.1, text='The whole town heard my story')
        split = split_confirmed_tail_candidate(base[-1], cue)
        self.assertEqual(split['text'], 'I am coming.')

    def test_non_monotonic_tail_word_times_do_not_split(self):
        base, cue, _ = self.merged_example()
        cue['words'][-1]['start'] = 21.9
        self.assertEqual(split_confirmed_tail_candidate(base[-1], cue), cue)
