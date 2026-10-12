import copy
import unittest
from asr_windows import split_corroborated_long_cues


class CorroboratedSplitTests(unittest.TestCase):
    def example(self):
        base = [{'start': 12., 'end': 19., 'text': 'When my story ends the whole town listens'}]
        def cue(text, start, duration):
            words = text.split()
            step = duration / len(words)
            return {'start': start - .08, 'end': start + duration + .08, 'text': text,
                'words': [{'text': w, 'start': start + i * step, 'end': start + (i + 1) * step,
                           'probability': .9} for i, w in enumerate(words)]}
        probe = {'start': 10., 'independent_text': base[0]['text'],
                 'speech': [{'start': 12., 'end': 19.}],
                 'segments': [cue('When my story ends', 12.2, 2.8),
                              cue('the whole town listens', 16., 2.)]}
        second = copy.deepcopy(probe)
        second['start'] = 10.5
        return base, [probe, second]

    def test_two_offset_reads_split_without_changing_words(self):
        base, probes = self.example()
        rows = split_corroborated_long_cues(base, probes)
        self.assertEqual(len(rows), 2)
        self.assertEqual(' '.join(r['text'] for r in rows), base[0]['text'])
        self.assertLess(rows[0]['end'], rows[1]['start'])

    def test_single_or_repeated_same_window_is_not_independent(self):
        base, probes = self.example()
        for evidence in ([probes[0]], [probes[0], copy.deepcopy(probes[0])]):
            self.assertEqual(split_corroborated_long_cues(base, evidence), base)

    def test_missing_independent_prefix_or_suffix_blocks_split(self):
        base, probes = self.example()
        for text in ['the whole town listens', 'When my story ends']:
            changed = copy.deepcopy(probes)
            for p in changed:
                p['independent_text'] = text
            self.assertEqual(split_corroborated_long_cues(base, changed), base)

    def test_disagreeing_times_or_missing_words_block_split(self):
        base, probes = self.example()
        probes[1]['segments'][0]['words'][-1]['end'] += .5
        self.assertEqual(split_corroborated_long_cues(base, probes), base)
        base, probes = self.example()
        probes[1]['segments'][1]['words'] = []
        self.assertEqual(split_corroborated_long_cues(base, probes), base)

    def test_short_or_low_confidence_cue_is_unchanged(self):
        base, probes = self.example()
        for probe in probes:
            probe['segments'][1]['words'][0]['probability'] = .2
        self.assertEqual(split_corroborated_long_cues(base, probes), base)

    def test_missing_audio_support_blocks_split(self):
        base, probes = self.example()
        for probe in probes:
            probe['speech'] = []
        self.assertEqual(split_corroborated_long_cues(base, probes), base)
