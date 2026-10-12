import sys
import unittest

sys.path.insert(0, '/app')
from language_gaps import (corroborated_gap_cues, gap_candidates,
                           select_weak_speech_gaps, whisper_language)


class LanguageGapTests(unittest.TestCase):
    def test_language_routes_do_not_assume_english(self):
        self.assertEqual(whisper_language('French'), 'fr')
        self.assertEqual(whisper_language('Japanese'), 'ja')
        self.assertEqual(whisper_language('Chinese'), 'zh')
        self.assertIsNone(whisper_language('unknown'))

    def test_weak_audio_ranks_long_gap_without_episode_specific_time(self):
        probes = [
            {'gap_start': 3133.66, 'gap_end': 3157.10,
             'speech': [{'start': 3139.67, 'end': 3140.19}]},
            {'gap_start': 2712.03, 'gap_end': 2736.83, 'speech': []},
        ]
        self.assertEqual(select_weak_speech_gaps(probes, maximum_total=1), probes[:1])

    def test_french_gap_needs_independent_text_and_whisper_times(self):
        base = [{'start': 3130.68, 'end': 3133.66, 'text': 'Reasons are everywhere.'},
                {'start': 3157.10, 'end': 3157.80, 'text': 'Peut-être.'}]
        probe = {'gap_start': 3133.66, 'gap_end': 3157.10,
                 'speech': [{'start': 3139.67, 'end': 3140.19}],
                 'language': 'French',
                 'qwen_text': "Tu le remède d'un monde malade.",
                 'whisper_cues': [{'start': 3142.54, 'end': 3145.86,
                                  'text': "Tu es le remède d'un monde malade.",
                                  'confidence': .82}]}
        rows, added = corroborated_gap_cues(base, [probe])
        self.assertEqual(len(added), 1)
        self.assertEqual(len(rows), 3)
        probe['qwen_text'] = 'A different imagined sentence.'
        self.assertEqual(corroborated_gap_cues(base, [probe])[1], [])
        probe['qwen_text'] = "Tu le remède d'un monde malade."
        probe['whisper_cues'][0]['confidence'] = .4
        self.assertEqual(corroborated_gap_cues(base, [probe])[1], [])

    def test_candidate_gaps_are_general(self):
        rows = [{'start': 0, 'end': 2}, {'start': 12, 'end': 14},
                {'start': 45, 'end': 47}]
        self.assertEqual(gap_candidates(rows), [
            {'gap_start': 2.0, 'gap_end': 12.0},
            {'gap_start': 14.0, 'gap_end': 45.0},
        ])

    def test_japanese_cue_does_not_need_spaces_between_words(self):
        probe = {'gap_start': 10, 'gap_end': 20,
                 'speech': [{'start': 12, 'end': 13}],
                 'language': 'Japanese', 'qwen_text': 'ここから出て行こう。',
                 'whisper_cues': [{'start': 12, 'end': 14,
                                  'text': 'ここから出て行こう。', 'confidence': .9}]}
        self.assertEqual(len(corroborated_gap_cues([], [probe])[1]), 1)

    def test_one_uncertain_word_does_not_discard_a_strong_sentence(self):
        probe = {'gap_start': 10, 'gap_end': 20,
                 'speech': [{'start': 12, 'end': 13}],
                 'language': 'French',
                 'qwen_text': "Tu le remèdes, d'un monde malade.",
                 'whisper_cues': [{'start': 12.2, 'end': 15,
                                  'text': "Tu es le remède d'un monde malade.",
                                  'confidence': .41, 'mean_confidence': .82}]}
        self.assertEqual(len(corroborated_gap_cues([], [probe])[1]), 1)
        probe['whisper_cues'][0]['mean_confidence'] = .69
        self.assertEqual(corroborated_gap_cues([], [probe])[1], [])
        probe['whisper_cues'][0]['mean_confidence'] = .82
        probe['speech'] = []
        self.assertEqual(corroborated_gap_cues([], [probe])[1], [])

    def test_existing_subtitle_is_not_overwritten(self):
        base = [{'start': 12, 'end': 14, 'text': 'Existing line'}]
        probe = {'gap_start': 10, 'gap_end': 20,
                 'speech': [{'start': 12, 'end': 13}],
                 'language': 'Chinese', 'qwen_text': '我们现在应该离开这里',
                 'whisper_cues': [{'start': 12.2, 'end': 14,
                                  'text': '我们现在应该离开这里', 'confidence': .9}]}
        self.assertEqual(corroborated_gap_cues(base, [probe])[1], [])


if __name__ == '__main__':
    unittest.main()
