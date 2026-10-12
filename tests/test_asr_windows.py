import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0, '/app')
from asr_windows import (corroborate_rescue, correct_rescue_onset_drift,
                         correct_rescue_word_onsets, extend_rescue_prefix,
                         extract_qwen_target_phrase,
                         fill_bounded_rescue_gaps,
                         fill_profile_gaps,
                         filter_isolated_short_hallucinations,
                         filter_isolated_without_speech,
                         filter_unconfirmed_short_leads,
                         filter_unsupported_hallucinations, insert_confident_whispered_speech,
                         snap_whispered_onset_to_strict_speech,
                         merge_window_segments,
                         plan_rescue_confirmations, plan_rescue_windows, plan_windows,
                         plan_qwen_short_disagreements,
                         plan_code_switch_disagreements,
                         plan_v2_fragment_windows, plan_v2_second_opinion_windows,
                         plan_v2_timing_windows, plan_v2_word_check_windows,
                         reconcile_corroborated_rephrasings,
                         reconcile_code_switch_readings,
                         reconcile_rescue_resegmentation,
                         reconcile_v2_fragments, reconcile_v2_second_opinions,
                         reconcile_v2_shifted_words, reconcile_v2_word_substitutions,
                         restore_precision_sentence_split,
                         restore_precision_subjects,
                         prefer_precision_pauses, prefer_precision_sentence_boundaries,
                         select_native_focus_windows,
                         reattach_dangling_leads,
                         reconcile_qwen_segments, split_qwen_alignment,
                         split_word_cues, trim_duplicated_cue_boundary,
                         validate_episode_segments)


class AsrWindowTests(unittest.TestCase):
    def test_trim_duplicated_cue_boundary_removes_a_repeated_word_run(self):
        # Real production cues from a rejected job: two adjacent Whisper
        # window reads each caught the same seven words right at their
        # shared boundary, with zero gap between the cues.
        cues = [
            {'start': 250.76, 'end': 254.74,
             'text': 'Dead, after staying like this for a long time, the only time he really left was to'},
            {'start': 254.74, 'end': 257.98,
             'text': 'only time he really left was to go back to The Ones Who Live, which he spent'},
        ]
        result = trim_duplicated_cue_boundary(cues)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['text'], 'go back to The Ones Who Live, which he spent')

    def test_trim_duplicated_cue_boundary_drops_a_fully_duplicated_cue(self):
        # Real production cues: the second cue is entirely a repeat of the
        # first cue's own tail.
        cues = [
            {'start': 372.9, 'end': 375.37,
             'text': "Going on right now about Rick's return to the Walking Dead"},
            {'start': 375.37, 'end': 376.39,
             'text': "about Rick's return to the Walking Dead."},
        ]
        result = trim_duplicated_cue_boundary(cues)
        self.assertEqual(len(result), 1)

    def test_trim_duplicated_cue_boundary_ignores_a_real_time_gap(self):
        cues = [
            {'start': 0.0, 'end': 2.0, 'text': 'I really mean it.'},
            {'start': 5.0, 'end': 7.0, 'text': 'I really mean it.'},
        ]
        result = trim_duplicated_cue_boundary(cues)
        self.assertEqual(result[1]['text'], 'I really mean it.')

    def test_trim_duplicated_cue_boundary_requires_a_minimum_overlap(self):
        cues = [
            {'start': 0.0, 'end': 2.0, 'text': 'And then we go.'},
            {'start': 2.0, 'end': 4.0, 'text': 'And then it happened.'},
        ]
        result = trim_duplicated_cue_boundary(cues)
        self.assertEqual(result[1]['text'], 'And then it happened.')

    def test_plans_overlap_until_audio_end(self):
        self.assertEqual(plan_windows(60), [
            {'start': 0.0, 'end': 30}, {'start': 22, 'end': 52}, {'start': 44, 'end': 60}
        ])
        self.assertEqual(plan_windows(12), [{'start': 0.0, 'end': 12}])
        with self.assertRaises(ValueError):
            plan_windows(60, overlap_seconds=30)

    def test_native_focus_preserves_original_window_grid(self):
        windows = plan_windows(500, window_seconds=90, overlap_seconds=30)
        chosen = select_native_focus_windows(windows, [(276, 304)], margin=30)
        self.assertEqual([row['start'] for row in chosen], [180, 240, 300])
        self.assertEqual(chosen[1], {'start': 240, 'end': 330})

    def test_overlap_keeps_centered_segments_and_language_switches(self):
        windows = [
            {'start': 0, 'end': 30, 'segments': [
                {'start': 25, 'end': 29, 'text': 'cut off sentence'},
            ]},
            {'start': 22, 'end': 52, 'segments': [
                {'start': 25, 'end': 29, 'text': 'Complete sentence.'},
                {'start': 30, 'end': 32, 'text': 'Je parle français.'},
                {'start': 33, 'end': 35, 'text': 'Back to English.'},
            ]},
            {'start': 44, 'end': 60, 'segments': []},
        ]
        result = merge_window_segments(windows)
        self.assertNotIn('cut off sentence', [row['text'] for row in result])
        self.assertEqual([row['text'] for row in result], [
            'Complete sentence.', 'Je parle français.', 'Back to English.'
        ])

    def test_small_cross_window_time_overlap_is_split(self):
        result = merge_window_segments([
            {'start': 0, 'end': 30, 'segments': [{'start': 24, 'end': 27, 'text': 'First'}]},
            {'start': 22, 'end': 52, 'segments': [{'start': 26, 'end': 29, 'text': 'Second'}]},
        ])
        self.assertEqual(result[0]['end'], result[1]['start'])
        self.assertEqual(result[0]['end'], 26.5)

    def test_recovers_uncovered_cue_from_neighbour_overlap(self):
        result = merge_window_segments([
            {'start': 0, 'end': 30, 'segments': [
                {'start': 26.5, 'end': 28.0, 'text': 'Recovered line'},
            ]},
            {'start': 22, 'end': 52, 'segments': []},
        ])
        self.assertEqual([row['text'] for row in result], ['Recovered line'])

    def test_does_not_layer_off_core_cue_over_centered_cue(self):
        result = merge_window_segments([
            {'start': 0, 'end': 30, 'segments': [
                {'start': 25, 'end': 29, 'text': 'cut off sentence'},
            ]},
            {'start': 22, 'end': 52, 'segments': [
                {'start': 25, 'end': 29, 'text': 'Complete sentence.'},
            ]},
        ])
        self.assertEqual([row['text'] for row in result], ['Complete sentence.'])

    def test_adjacent_window_duplicate_is_one_utterance(self):
        result = merge_window_segments([{
            'start': 0, 'end': 30, 'segments': [
                {'start': 13.92, 'end': 14.64, 'text': 'Merci.'},
                {'start': 14.72, 'end': 15.34, 'text': 'Merci.'},
            ],
        }])
        self.assertEqual([row['text'] for row in result], ['Merci.'])

    def test_episode_quality_gate_rejects_long_bridges(self):
        self.assertTrue(validate_episode_segments([
            {'start': 2.0, 'end': 4.2, 'text': 'Good'},
            {'start': 8.0, 'end': 11.0, 'text': 'Also good'},
        ]))
        with self.assertRaisesRegex(ValueError, 'orimligt lång'):
            validate_episode_segments([
                {'start': 2.83, 'end': 337.73, 'text': 'Jag heter Daryl Dixon.'},
            ])

    def test_long_segment_is_split_at_sentences_and_pauses(self):
        words = [
            SimpleNamespace(start=0.0, end=.4, probability=.9, word=' My'),
            SimpleNamespace(start=.5, end=1.0, probability=.9, word=' name'),
            SimpleNamespace(start=1.1, end=1.6, probability=.9, word=' is Daryl.'),
            SimpleNamespace(start=5.0, end=5.4, probability=.9, word=' I'),
            SimpleNamespace(start=5.5, end=6.0, probability=.9, word=' tried.'),
        ]
        cues = split_word_cues(words)
        self.assertEqual([cue['text'] for cue in cues], ['My name is Daryl.', 'I tried.'])
        self.assertTrue(all(cue['end'] - cue['start'] <= 7 for cue in cues))

    def test_word_group_near_the_duration_cap_is_kept_not_silently_dropped(self):
        # Real KING trailer bug: a run of high-confidence words (all >.5
        # probability, well over the .25 floor) has a raw span of 6.98s,
        # just under the 7s cap that stops the group from growing further.
        # Padding both ends by .08s used to push the *displayed* span to
        # 7.14s, which failed the old end-start<=maximum_duration check and
        # threw the whole group away -- seven seconds of correctly heard
        # dialogue vanished for a padding rounding reason, not low confidence.
        words = [
            SimpleNamespace(start=3.94, end=4.70, probability=.51, word=' कि'),
            SimpleNamespace(start=4.70, end=4.90, probability=.92, word=' मेरे'),
            SimpleNamespace(start=4.90, end=10.92, probability=.76, word=' दुश्मन'),
        ]
        cues = split_word_cues(words)
        self.assertEqual(len(cues), 1)
        self.assertIn('कि', cues[0]['text'])
        self.assertIn('दुश्मन', cues[0]['text'])
        self.assertLessEqual(cues[0]['end'] - cues[0]['start'], 7)

    def _split_both_word_backends(self, items, **options):
        return [
            split_word_cues([SimpleNamespace(word=' ' + item['text'], probability=.9,
                                            start=item['start'], end=item['end'])
                             for item in items], include_words=True, **options),
            split_qwen_alignment(items, **options),
        ]

    def test_duration_split_rebalances_a_short_sentence_tail_without_losing_words(self):
        items = [{'text': word, 'start': start, 'end': end}
                 for word, start, end in [
                     ('They', 0, .6), ('continued', 1.1, 1.8), ('walking', 2.3, 3.0),
                     ('along', 3.5, 4.2), ('the', 4.7, 5.4), ('road', 6.0, 6.85),
                     ('home.', 7.0, 7.4)]]
        for cues in self._split_both_word_backends(items):
            self.assertEqual([cue['text'] for cue in cues],
                             ['They continued walking along the', 'road home.'])
            self.assertTrue(all(cue['end'] - cue['start'] <= 7 for cue in cues))
            self.assertGreaterEqual(cues[-1]['end'] - cues[-1]['start'], 1)
            self.assertGreaterEqual(cues[-1]['start'], 5.9)
        whisper = self._split_both_word_backends(items)[0]
        self.assertEqual([word['text'] for cue in whisper for word in cue['words']],
                         [item['text'] for item in items])
        self.assertEqual([(word['start'], word['end'])
                          for cue in whisper for word in cue['words']],
                         [(item['start'], item['end']) for item in items])

    def test_character_split_rebalances_a_short_tail_within_both_limits(self):
        items = [{'text': text, 'start': i * .6, 'end': i * .6 + .4}
                 for i, text in enumerate(['Take', 'this', 'path', 'back', 'home.'])]
        for cues in self._split_both_word_backends(items, maximum_characters=20):
            self.assertEqual([cue['text'] for cue in cues],
                             ['Take this path', 'back home.'])
            self.assertTrue(all(len(cue['text']) <= 20 for cue in cues))

    def test_near_cap_flush_can_keep_a_short_tail_in_the_same_cue(self):
        items = [{'text': 'Keep', 'start': 0, 'end': .5},
                 {'text': 'going', 'start': 5.8, 'end': 6.8},
                 {'text': 'now.', 'start': 6.8, 'end': 6.95}]
        for cues in self._split_both_word_backends(items, maximum_pause=6):
            self.assertEqual([cue['text'] for cue in cues], ['Keep going now.'])
            self.assertLessEqual(cues[0]['end'] - cues[0]['start'], 7)

    def test_short_complete_reply_and_pause_are_not_rebalanced(self):
        for previous, last_start in [('stop.', 6.9), ('stop', 10.0)]:
            items = [{'text': 'Please', 'start': 0, 'end': .5},
                     {'text': previous, 'start': 5.8, 'end': 6.8},
                     {'text': 'No.', 'start': last_start, 'end': last_start + .3}]
            for cues in self._split_both_word_backends(items):
                self.assertEqual(cues[-1]['text'], 'No.')
                self.assertGreaterEqual(cues[-1]['start'], last_start - .081)

    def test_short_tail_is_preserved_when_rebalancing_would_break_limits(self):
        items = [{'text': 'longword', 'start': 0, 'end': .8},
                 {'text': 'end.', 'start': .8, 'end': 1.0}]
        for cues in self._split_both_word_backends(items, maximum_characters=8):
            self.assertEqual([cue['text'] for cue in cues], ['longword', 'end.'])

    def test_short_profile_fills_only_clear_primary_gaps(self):
        primary = [
            {'start': 10, 'end': 12, 'text': 'Primary line'},
            {'start': 20, 'end': 22, 'text': 'Next primary'},
            {'start': 30, 'end': 31, 'text': 'Monsieur!'},
        ]
        secondary = [
            {'start': 12.1, 'end': 12.8, 'text': 'Rapid next line'},
            {'start': 15, 'end': 16, 'text': 'Recovered missing line'},
            {'start': 23, 'end': 24, 'text': 'Sir!'},
        ]
        result = fill_profile_gaps(primary, secondary)
        self.assertEqual([row['text'] for row in result],
                         ['Primary line', 'Rapid next line', 'Recovered missing line',
                          'Next primary', 'Monsieur!'])

    def test_fuller_secondary_can_replace_overlapping_fragments(self):
        result = fill_profile_gaps(
            [{'start': 10, 'end': 11, 'text': 'Still'}],
            [{'start': 8, 'end': 12, 'text': "If I don't make it back, I tried. Still trying."}],
        )
        self.assertEqual(len(result), 1)
        self.assertIn("make it back", result[0]['text'])

    def test_stock_silence_hallucination_needs_independent_support(self):
        primary, secondary = filter_unsupported_hallucinations(
            [
                {'start': 111.0, 'end': 111.5, 'text': 'Thank you.'},
                {'start': 840.8, 'end': 841.4, 'text': 'Monsieur!'},
                {'start': 894.0, 'end': 894.6, 'text': 'Merci.'},
            ],
            [
                {'start': 840.9, 'end': 841.5, 'text': 'Sir!'},
                {'start': 894.2, 'end': 894.8, 'text': 'Merci!'},
                {'start': 1200.0, 'end': 1200.4, 'text': 'Thanks.'},
            ],
        )
        self.assertEqual([row['text'] for row in primary], ['Monsieur!', 'Merci.'])
        self.assertEqual([row['text'] for row in secondary], ['Sir!', 'Merci!'])

    def test_word_split_does_not_give_later_sentence_an_early_start(self):
        words = [
            SimpleNamespace(start=0.2, end=.6, probability=.9, word=' First.'),
            SimpleNamespace(start=4.0, end=4.3, probability=.9, word=' Later'),
            SimpleNamespace(start=4.4, end=4.9, probability=.9, word=' sentence.'),
        ]
        cues = split_word_cues(words)
        self.assertEqual(len(cues), 2)
        self.assertGreater(cues[1]['start'], 3.8)

    def test_dangling_lead_uses_later_phrase_timing(self):
        result = reattach_dangling_leads([
            {'start': 897.0, 'end': 897.62, 'text': 'Where'},
            {'start': 905.84, 'end': 906.42, 'text': 'you going to?'},
            {'start': 906.74, 'end': 907.98, 'text': 'Where I came from.'},
        ])
        self.assertEqual(result[0]['text'], 'Where you going to?')
        self.assertAlmostEqual(result[0]['start'], 905.72)

    def test_complete_short_line_is_never_reattached(self):
        result = reattach_dangling_leads([
            {'start': 10.0, 'end': 10.6, 'text': 'You!'},
            {'start': 15.0, 'end': 16.0, 'text': 'come with me.'},
        ])
        self.assertEqual(len(result), 2)

    def test_contracted_and_duplicate_leads_are_reattached_cleanly(self):
        result = reattach_dangling_leads([
            {'start': 10, 'end': 10.6, 'text': "There's"},
            {'start': 13.9, 'end': 15, 'text': 'got to be somewhere.'},
            {'start': 20, 'end': 20.6, 'text': 'She'},
            {'start': 22.1, 'end': 23.2, 'text': 'She wants things.'},
        ], minimum_gap=.6)
        self.assertEqual([row['text'] for row in result],
                         ["There's got to be somewhere.", 'She wants things.'])

    def test_high_confidence_no_vad_line_fills_bounded_dialogue_gap(self):
        base = [
            {'start': 1021.88, 'end': 1022.92, 'text': 'For how long?'},
            {'start': 1027.45, 'end': 1028.65, 'text': 'Yeah, we, um...'},
        ]
        rescue = [
            {'start': 1024.28, 'end': 1025.16, 'text': 'Six months.', 'confidence': .955},
        ]
        result = fill_bounded_rescue_gaps(base, rescue)
        self.assertEqual([row['text'] for row in result],
                         ['For how long?', 'Six months.', 'Yeah, we, um...'])

    def test_no_vad_rescue_rejects_silence_and_low_confidence(self):
        base = [
            {'start': 10, 'end': 11, 'text': 'Before.'},
            {'start': 40, 'end': 41, 'text': 'After.'},
        ]
        rescue = [
            {'start': 20, 'end': 21, 'text': 'Made up.', 'confidence': .99},
            {'start': 35, 'end': 36, 'text': 'Uncertain words.', 'confidence': .60},
        ]
        self.assertEqual(fill_bounded_rescue_gaps(base, rescue), base)

    def test_leading_candidate_is_ignored_without_duration_but_recovered_with_it(self):
        base = [{'start': 15.0, 'end': 17.0, 'text': 'Only known line.'}]
        rescue = [{'start': 5.0, 'end': 9.0,
                   'text': 'Recovered opening line.', 'confidence': .85}]
        # Same default behaviour as before this fix: no duration, no neighbour
        # before the candidate, so it stays refused.
        self.assertEqual(fill_bounded_rescue_gaps(base, rescue), base)
        # Passing the clip's real duration lets the candidate be bounded
        # against its own start instead of a nonexistent earlier line.
        result = fill_bounded_rescue_gaps(base, rescue, duration=30.0)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['text'], 'Recovered opening line.')

    def test_trailing_candidate_is_recovered_with_duration(self):
        base = [{'start': 5.0, 'end': 7.0, 'text': 'Only known line.'}]
        rescue = [{'start': 12.0, 'end': 16.0,
                   'text': 'Recovered closing line.', 'confidence': .85}]
        self.assertEqual(fill_bounded_rescue_gaps(base, rescue), base)
        result = fill_bounded_rescue_gaps(base, rescue, duration=18.0)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['text'], 'Recovered closing line.')

    def test_uncertain_bounded_phrase_gets_one_confirmation_window(self):
        base = [
            {'start': 72.39, 'end': 79.18, 'text': 'Holly keeps crying.'},
            {'start': 86.42, 'end': 87.32, 'text': 'to go home'},
        ]
        rescue = [{'start': 79.93, 'end': 83.17,
                   'text': 'she wants things the way they were', 'confidence': .77}]
        self.assertEqual(plan_rescue_confirmations(base, rescue, 90),
                         [{'start': 74.0, 'end': 90.0}])

    def test_confirmation_window_is_planned_for_a_candidate_before_the_first_line(self):
        # Real KING trailer numbers: a rescued phrase before the only known
        # line had no "before" neighbour, so the old code refused to even
        # plan a confirmation re-read for it.
        base = [{'start': 11.62, 'end': 17.51,
                 'text': 'है, उसमें ही जाके दूँगा, बागी मेरी कहानी है,'}]
        rescue = [{'start': 4.06, 'end': 10.92,
                   'text': 'मेरे दुश्मन समझ रहे थे मैं अब कभी लोटकर नहीं होगा एक गुमदामी',
                   'confidence': .73}]
        self.assertEqual(plan_rescue_confirmations(base, rescue, 64.32),
                         [{'start': 0.0, 'end': 22.0}])

    def test_confirmation_allows_bounded_unpunctuated_speech_but_not_ghost(self):
        base = [
            {'start': 72.39, 'end': 79.18, 'text': 'Holly keeps crying.'},
            {'start': 86.42, 'end': 87.32, 'text': 'to go home'},
        ]
        rescue = [{'start': 79.93, 'end': 83.17,
                   'text': 'she wants things the way they were', 'confidence': .77}]
        confirmation = [{'start': 80.30, 'end': 83.10,
                         'text': 'She wants things the way they were.',
                         'confidence': .68}]
        accepted = fill_bounded_rescue_gaps(base, corroborate_rescue(rescue, confirmation))
        self.assertEqual(len(accepted), 3)
        self.assertIn('she wants things', accepted[1]['text'])
        rejected = fill_bounded_rescue_gaps(base, corroborate_rescue(rescue, []))
        self.assertEqual(rejected, base)

    def test_confirmed_post_pause_line_is_recovered(self):
        base = [
            {'start': 267.3, 'end': 267.94, 'text': 'Been'},
            {'start': 279.0, 'end': 280.02, 'text': 'We had to let her go.'},
        ]
        rescue = [{'start': 276.315, 'end': 277.615,
                   'text': 'seal had a heart attack.', 'confidence': .86}]
        windows = plan_rescue_confirmations(base, rescue, 320)
        self.assertEqual(windows, [{'start': 271.0, 'end': 293.0}])
        confirmation = [{'start': 276.18, 'end': 277.52,
                         'text': 'Sue had a heart attack.', 'confidence': .31}]
        confirmed = corroborate_rescue(rescue, confirmation)
        self.assertTrue(confirmed[0]['corroborated'])
        self.assertEqual(confirmed[0]['text'], 'Sue had a heart attack.')
        self.assertEqual(fill_bounded_rescue_gaps(base, confirmed)[1]['text'],
                         'Sue had a heart attack.')
        self.assertEqual(fill_bounded_rescue_gaps(base, rescue), base)

    def test_confirmed_shifted_fragment_recovers_full_line(self):
        base = [
            {'start': 300.78, 'end': 301.96, 'text': 'wants things she'},
            {'start': 306.28, 'end': 307.34, 'text': 'wants to go home.'},
        ]
        rescue = [{'start': 300.215, 'end': 303.195,
                   'text': 'She wants things the way they were.', 'confidence': .646}]
        self.assertEqual(plan_rescue_confirmations(base, rescue, 340),
                         [{'start': 295.0, 'end': 317.0}])
        confirmation = [
            {'start': 299.99, 'end': 301.29, 'text': 'she wants things', 'confidence': .53},
            {'start': 302.11, 'end': 303.09, 'text': 'the way they were', 'confidence': .90},
        ]
        confirmed = corroborate_rescue(rescue, confirmation)
        self.assertTrue(confirmed[0]['corroborated'])
        self.assertEqual(fill_bounded_rescue_gaps(base, confirmed)[0]['text'],
                         'She wants things the way they were.')
        self.assertEqual(fill_bounded_rescue_gaps(base, rescue), base)

    def test_confirmation_merged_into_one_sentence_still_corroborates_each_split_fragment(self):
        # Real numbers from a production trace: the rescue pass split one
        # spoken sentence into two fragments, but the confirmation pass read
        # it as a single continuous sentence spanning both. Neither
        # fragment's own start/end lines up with the merged reading, so the
        # old whole-span match never corroborated either one and the first
        # fragment was silently dropped from the final subtitle.
        base = [
            {'start': 41.29, 'end': 43.25, 'text': "No idea what's waiting on the other side."},
            {'start': 47.95, 'end': 49.27, 'text': 'of us going back to America.'},
        ]
        rescue = [
            {'start': 45.685, 'end': 46.925, 'text': 'Isabel and I were talking',
             'confidence': .789},
            {'start': 46.925, 'end': 49.365, 'text': 'About the three of us going back to America',
             'confidence': .497},
        ]
        confirmation = [{'start': 45.7, 'end': 49.34,
                         'text': 'Isabel and I were talking about the three of us going back to America.',
                         'confidence': .50}]
        confirmed = corroborate_rescue(rescue, confirmation)
        self.assertTrue(confirmed[0]['corroborated'])
        result = fill_bounded_rescue_gaps(base, confirmed)
        self.assertEqual([row['text'] for row in result],
                         ["No idea what's waiting on the other side.",
                          'Isabel and I were talking',
                          'of us going back to America.'])

    def test_unconfirmed_one_word_ghost_requires_audio_support(self):
        rows = [{'start': 833.38, 'end': 834.12, 'text': "I'm"}]
        strict_speech = [{'start': 831.97, 'end': 833.52}]
        self.assertEqual(filter_unconfirmed_short_leads(rows, [[], [], []], strict_speech), [])
        self.assertEqual(filter_unconfirmed_short_leads(
            rows, [[], [], []], [{'start': 833.3, 'end': 833.8}]), rows)
        self.assertEqual(filter_unconfirmed_short_leads(
            rows, [[{'start': 833.4, 'end': 834.1, 'text': "I'm"}],
                   [], [{'start': 833.4, 'end': 834.1, 'text': "I'm"}]], []), rows)

    def test_two_vad_whisper_passes_do_not_confirm_daryl_ghost_word(self):
        rows = [{'start': 28.48, 'end': 29.0, 'text': "We're"}]
        profiles = [
            [{'start': 28.48, 'end': 29.0, 'text': "We're"}],
            [{'start': 28.34, 'end': 29.04, 'text': "We're"}],
            [{'start': 27.445, 'end': 28.265, 'text': 'help me'}],
        ]
        speech = [{'start': 27.468, 'end': 28.596}]
        self.assertEqual(filter_unconfirmed_short_leads(rows, profiles, speech), [])

    def test_one_pass_cannot_confirm_its_own_music_fragment(self):
        rows = [{'start': 1886.10, 'end': 1886.94, 'text': 'Do', 'confidence': .98}]
        precision = [dict(rows[0])]
        speech = [{'start': 1885.10, 'end': 1885.97}]
        self.assertEqual(filter_unconfirmed_short_leads(
            rows, [[], precision, []], speech), [])

    def test_v2_only_checks_disputed_short_fragments(self):
        context = [
            {'start': 855.78, 'end': 856.86, 'text': 'name, and', 'confidence': .35},
            {'start': 990.68, 'end': 991.72, 'text': 'Sit down, il sait.', 'confidence': .85},
        ]
        precision = [
            {'start': 855.78, 'end': 857.04,
             'text': 'name, grandfather.', 'confidence': .43},
            {'start': 990.58, 'end': 991.74,
             'text': 'Sit down, you say.', 'confidence': .63},
        ]
        self.assertEqual(plan_v2_fragment_windows(context, precision, 1200),
                         [{'start': 852.0, 'end': 877.0}])

    def test_v2_repairs_only_fragment_supported_by_v3_precision(self):
        base = [
            {'start': 855.78, 'end': 856.86, 'text': 'name, and'},
            {'start': 860.60, 'end': 861.06, 'text': 'father.'},
            {'start': 862.22, 'end': 863.74, 'text': 'He hurt the ankle.'},
        ]
        precision = [{'start': 858.35, 'end': 863.73,
                      'text': 'My, uh, grandfather, he hurt the ankle.',
                      'confidence': .59}]
        v2 = [
            {'start': 855.80, 'end': 856.12, 'text': 'Venez!', 'confidence': .87},
            {'start': 858.38, 'end': 861.14,
             'text': 'My, uh, grandfather.', 'confidence': .56},
        ]
        result = reconcile_v2_fragments(base, precision, v2)
        self.assertEqual([row['text'] for row in result],
                         ['name, and', 'My, uh, grandfather.', 'He hurt the ankle.'])
        self.assertEqual(reconcile_v2_fragments(base, [], v2), base)

    def test_v2_does_not_guess_disputed_code_switch(self):
        base = [{'start': 990.68, 'end': 991.72,
                 'text': 'Sit down, il sait.', 'confidence': .85}]
        precision = [{'start': 990.58, 'end': 991.74,
                      'text': 'Sit down, you say.', 'confidence': .63}]
        v2 = [{'start': 990.88, 'end': 991.76,
               'text': 'Sit down, he said.', 'confidence': .65}]
        self.assertEqual(reconcile_v2_fragments(base, precision, v2), base)

    def test_v2_fragment_does_not_overlap_adjacent_cue(self):
        base = [
            {'start': 0.0, 'end': 1.05, 'text': 'Previous.'},
            {'start': 1.10, 'end': 1.50, 'text': 'father.'},
            {'start': 2.0, 'end': 2.5, 'text': 'Next.'},
        ]
        precision = [{'start': 1.0, 'end': 1.5,
                      'text': 'My grandfather.', 'confidence': .6}]
        v2 = [{'start': 1.0, 'end': 1.5,
               'text': 'My grandfather.', 'confidence': .6}]
        self.assertEqual(reconcile_v2_fragments(base, precision, v2), base)

    def test_two_short_reads_replace_a_ghost_fragment(self):
        base = [
            {'start': 1545.95, 'end': 1548.47,
             'text': "That we've healed from our suffering to"},
            {'start': 1551.99, 'end': 1552.57, 'text': 'get somewhere.'},
            {'start': 1552.59, 'end': 1553.21, 'text': 'Of'},
            {'start': 1554.86, 'end': 1555.90, 'text': "I'll wait outside."},
        ]
        precision = [{'start': 1551.38, 'end': 1552.66,
                      'text': 'You ought to get some air.', 'confidence': .63}]
        rescue = [{'start': 1551.31, 'end': 1552.64,
                   'text': 'You ought to get some air.', 'confidence': .91}]
        speech = [{'start': 1552.35, 'end': 1552.87}]
        result = reconcile_corroborated_rephrasings(base, precision, rescue, speech)
        self.assertEqual([row['text'] for row in result],
                         [base[0]['text'], 'You ought to get some air.', base[-1]['text']])
        self.assertTrue(validate_episode_segments(result))
        self.assertEqual(reconcile_corroborated_rephrasings(
            base, precision, [], speech), base)
        self.assertEqual(reconcile_corroborated_rephrasings(
            base, precision, rescue, []), base)

    def test_precision_sentences_do_not_overlap_untouched_cue(self):
        base = [
            {'start': 0.0, 'end': 3.0,
             'text': 'This is the first sentence and this is the next one'},
            {'start': 3.0, 'end': 6.0,
             'text': 'Then there is a third sentence at the end.'},
            {'start': 6.0, 'end': 7.0, 'text': 'Afterwards.'},
        ]
        precision = [
            {'start': 0.0, 'end': 1.0, 'text': 'This is the first sentence.',
             'confidence': .8},
            {'start': 1.1, 'end': 2.2, 'text': 'And this is the next one.',
             'confidence': .8},
            {'start': 3.5, 'end': 6.05,
             'text': 'Then there is a third sentence at the end.',
             'confidence': .8},
        ]
        self.assertEqual(prefer_precision_sentence_boundaries(base, precision), base)

    def test_v2_confirms_later_single_word_only_with_v3_rescue(self):
        early = {'start': 1897.12, 'end': 1897.80,
                 'text': 'No,', 'confidence': .95}
        rescue = [{'start': 1900.03, 'end': 1900.67,
                   'text': 'No.', 'confidence': .87}]
        speech = [{'start': 1896.52, 'end': 1897.14}]
        self.assertEqual(plan_v2_timing_windows([early], rescue, speech, 2000),
                         [{'start': 1880.0, 'end': 1913.0}])
        base = [
            {'start': 1895.94, 'end': 1896.70, 'text': 'Parents?'},
            {'start': 1901.66, 'end': 1902.40, 'text': 'nothing like that.'},
        ]
        v2 = [{'start': 1900.14, 'end': 1900.74,
               'text': 'No.', 'confidence': .74}]
        result = reconcile_v2_shifted_words(base, [early], rescue, v2, speech)
        self.assertEqual([row['text'] for row in result],
                         ['Parents?', 'No.', 'nothing like that.'])
        self.assertEqual(reconcile_v2_shifted_words(base, [early], [], v2, speech), base)

    def test_no_vad_rescue_extends_matching_incomplete_replique(self):
        base = [
            {'start': 297.26, 'end': 298.42, 'text': 'She wants her mom back.'},
            {'start': 300.66, 'end': 301.96, 'text': 'She wants things she'},
            {'start': 306.28, 'end': 307.34, 'text': 'wants to go home.'},
        ]
        rescue = [{'start': 300.30, 'end': 303.10,
                   'text': 'She wants things the way they were.',
                   'confidence': .679}]
        result = fill_bounded_rescue_gaps(base, rescue)
        self.assertEqual(result[1]['text'], 'She wants things the way they were.')
        self.assertAlmostEqual(result[1]['start'], 300.30)
        self.assertEqual(len(result), 3)

    def test_no_vad_rescue_replaces_suffix_fragment_with_complete_line(self):
        base = [
            {'start': 294.20, 'end': 299.11, 'text': 'she wants her mom back she'},
            {'start': 302.19, 'end': 303.21, 'text': 'The way they were.'},
            {'start': 306.28, 'end': 308.14, 'text': 'wants to go home my'},
        ]
        rescue = [{'start': 300.17, 'end': 303.19,
                   'text': 'She wants things the way they were.',
                   'confidence': .77}]
        result = fill_bounded_rescue_gaps(base, rescue)
        self.assertEqual(result[1]['text'], 'She wants things the way they were.')
        self.assertAlmostEqual(result[1]['start'], 300.17)
        self.assertEqual(len(result), 3)

    def test_precision_pause_retains_same_words_with_later_timing(self):
        merged = [
            {'start': 336.68, 'end': 340.68,
             'text': 'name is daryl dixon i come from a place'},
            {'start': 340.68, 'end': 346.03,
             'text': "called the commonwealth it's in america i"},
        ]
        precision = [
            {'start': 336.71, 'end': 341.66,
             'text': 'name is daryl dixon i come from a place called the commonwealth'},
            {'start': 344.58, 'end': 346.02,
             'text': "it's in america i"},
        ]
        result = prefer_precision_pauses(merged, precision)
        self.assertEqual([row['text'] for row in result],
                         [row['text'] for row in precision])
        self.assertAlmostEqual(result[1]['start'], 344.58)

    def test_precision_pause_does_not_replace_different_words(self):
        merged = [
            {'start': 10, 'end': 12, 'text': 'Real first line.'},
            {'start': 12, 'end': 16, 'text': 'Real second line.'},
        ]
        precision = [
            {'start': 10, 'end': 12, 'text': 'Different words.'},
            {'start': 14, 'end': 16, 'text': 'More invented words.'},
        ]
        self.assertEqual(prefer_precision_pauses(merged, precision), merged)

    def test_precision_sentences_fix_duration_split_when_words_agree(self):
        merged = [
            {'start': 336.66, 'end': 340.98,
             'text': 'name is daryl dixon i come from a place called the', 'confidence': .72},
            {'start': 340.98, 'end': 346.04,
             'text': "commonwealth it's in america i", 'confidence': .55},
        ]
        precision = [
            {'start': 336.28, 'end': 337.74, 'text': "My name's Daryl Dixon.",
             'confidence': .75},
            {'start': 337.96, 'end': 341.41,
             'text': 'I come from a place called the Commonwealth.', 'confidence': .86},
            {'start': 344.44, 'end': 345.42, 'text': "It's in America.",
             'confidence': .99},
        ]
        self.assertEqual(prefer_precision_sentence_boundaries(merged, precision), precision)

    def test_precision_sentences_keep_later_clause_on_its_own_time(self):
        merged = [
            {'start': 352.69, 'end': 354.63,
             'text': 'went out looking for something and i', 'confidence': .75},
            {'start': 355.33, 'end': 360.61,
             'text': "i found this trouble if i don't make it back you", 'confidence': .47},
            {'start': 362.63, 'end': 364.29,
             'text': "know i tried i'm", 'confidence': .95},
        ]
        precision = [
            {'start': 352.62, 'end': 354.82,
             'text': 'I went out looking for something, and I...', 'confidence': .57},
            {'start': 355.10, 'end': 356.26,
             'text': 'Well, I found this trouble.', 'confidence': .26},
            {'start': 358.81, 'end': 363.51,
             'text': "If I don't make it back, you know I tried.", 'confidence': .29},
            {'start': 363.75, 'end': 364.27, 'text': "I'm", 'confidence': .93},
        ]
        self.assertEqual(prefer_precision_sentence_boundaries(merged, precision), precision)

    def test_precision_sentences_do_not_replace_different_words(self):
        merged = [
            {'start': 10, 'end': 14, 'text': 'The real family is right here now.'},
            {'start': 14, 'end': 18, 'text': 'They need help to get home today.'},
        ]
        precision = [
            {'start': 10, 'end': 12, 'text': 'Invented first line.', 'confidence': .99},
            {'start': 12.1, 'end': 15, 'text': 'Invented second line.', 'confidence': .99},
            {'start': 16.5, 'end': 18, 'text': 'Invented third line.', 'confidence': .99},
        ]
        self.assertEqual(prefer_precision_sentence_boundaries(merged, precision), merged)

    def test_no_vad_rescue_cannot_replace_unrelated_overlap(self):
        base = [
            {'start': 10, 'end': 11, 'text': 'She wants things she'},
            {'start': 15, 'end': 16, 'text': 'Next line.'},
        ]
        rescue = [{'start': 10, 'end': 12,
                   'text': 'He wants everyone to disappear forever.',
                   'confidence': .99}]
        self.assertEqual(fill_bounded_rescue_gaps(base, rescue), base)

    def test_rescue_windows_only_cover_suspicious_bounded_gaps(self):
        rows = [
            {'start': 0, 'end': 2, 'text': 'Opening.'},
            {'start': 40, 'end': 42, 'text': 'After real silence.'},
            {'start': 50, 'end': 51, 'text': 'Question?'},
            {'start': 55, 'end': 56, 'text': 'Following line.'},
        ]
        windows = plan_rescue_windows(rows, 60)
        self.assertEqual(windows, [{'start': 37.0, 'end': 60.0}])
        self.assertLess(sum(row['end'] - row['start'] for row in windows), 30)

    def test_rescue_windows_cover_a_bounded_gap_before_the_first_line(self):
        # A real short trailer (KING): the only two confirmed lines start at
        # 11.62s with nothing detected before it, even though independent
        # speech detection found real voice activity at 5.3s and 7.6s. The
        # old code only ever looked between two already-found lines, so a
        # gap before the very first one was never offered for rescue.
        rows = [
            {'start': 11.62, 'end': 17.51, 'text': 'है, उसमें ही जाके दूँगा,'},
            {'start': 17.51, 'end': 20.01, 'text': 'सारी दुनिया को जो सुना,'},
        ]
        self.assertEqual(plan_rescue_windows(rows, 64.32), [{'start': 0.0, 'end': 18.0}])

    def test_rescue_windows_cover_a_bounded_gap_after_the_last_line(self):
        rows = [{'start': 2.0, 'end': 4.0, 'text': 'Opening.'}]
        self.assertEqual(plan_rescue_windows(rows, 12.0), [{'start': 0.0, 'end': 12.0}])

    def test_rescue_windows_ignore_leading_and_trailing_silence_too_long_to_bound(self):
        rows = [{'start': 30.0, 'end': 32.0, 'text': 'Only line.'}]
        self.assertEqual(plan_rescue_windows(rows, 90.0), [])

    def test_rescue_windows_still_empty_without_any_confirmed_line(self):
        self.assertEqual(plan_rescue_windows([], 60), [])

    def test_isolated_you_and_tiny_fragment_are_removed(self):
        rows = [
            {'start': 2, 'end': 4, 'text': 'Real opening.'},
            {'start': 111.04, 'end': 111.54, 'text': 'You.'},
            {'start': 241.98, 'end': 243.34, 'text': 'Real dialogue.'},
            {'start': 250, 'end': 250.2, 'text': 'And'},
            {'start': 251, 'end': 252, 'text': 'Complete line.'},
        ]
        result = filter_isolated_short_hallucinations(rows)
        self.assertEqual([row['text'] for row in result],
                         ['Real opening.', 'Real dialogue.', 'Complete line.'])

    def test_isolated_incomplete_single_word_fragment_is_removed(self):
        rows = [
            {'start': 250, 'end': 252, 'text': 'Before.'},
            {'start': 267.34, 'end': 267.94, 'text': 'Been'},
            {'start': 278.99, 'end': 280.03, 'text': 'After.'},
        ]
        result = filter_isolated_short_hallucinations(rows)
        self.assertEqual([row['text'] for row in result], ['Before.', 'After.'])

    def test_short_sentence_internal_word_survives_corroborated_boundary(self):
        rows = [
            {'start': 30.0, 'end': 34.2, 'text': 'the signal comes from the'},
            {'start': 34.2, 'end': 34.44, 'text': 'vessel'},
            {'start': 34.44, 'end': 37.0, 'text': 'ahead on our screen'},
        ]
        precision = [{'start': 32.6, 'end': 34.8,
                      'text': 'The signal comes from the vessel ahead.'}]
        result = filter_isolated_short_hallucinations(
            rows, corroborating_segments=precision)
        self.assertEqual([row['text'] for row in result],
                         ['the signal comes from the', 'vessel', 'ahead on our screen'])
        # The following guard still requires actual speech, not just a
        # second Whisper window repeating a musical hallucination.
        self.assertEqual([row['text'] for row in filter_unconfirmed_short_leads(
            result, [rows, precision, []], [])],
            ['the signal comes from the', 'ahead on our screen'])
        self.assertEqual(len(filter_unconfirmed_short_leads(
            result, [rows, precision, []], [{'start': 34.0, 'end': 34.8}])), 3)

    def test_short_boundary_word_without_exact_context_is_still_removed(self):
        rows = [
            {'start': 30.0, 'end': 34.2, 'text': 'the signal comes from the'},
            {'start': 34.2, 'end': 34.44, 'text': 'vessel'},
            {'start': 34.44, 'end': 37.0, 'text': 'ahead on our screen'},
        ]
        precision = [{'start': 32.6, 'end': 34.8,
                      'text': 'The signal comes from the station ahead.'}]
        result = filter_isolated_short_hallucinations(
            rows, corroborating_segments=precision)
        self.assertEqual([row['text'] for row in result],
                         ['the signal comes from the', 'ahead on our screen'])

    def test_isolated_cue_without_strict_speech_support_is_removed(self):
        rows = [
            {'start': 2.75, 'end': 4.29, 'text': 'Invented opening sentence.'},
            {'start': 241.98, 'end': 243.34, 'text': 'Real dialogue.'},
        ]
        result = filter_isolated_without_speech(
            rows, [{'start': 241.8, 'end': 243.5}]
        )
        self.assertEqual([row['text'] for row in result], ['Real dialogue.'])

    def test_whisper_silence_hallucination_is_removed_by_existing_isolation_filter(self):
        # A four-second exact-zero diagnostic produced a confident two-word cue.
        # This tests the existing generic filter, not a phrase-specific blocklist.
        rows = [{'start': 0.0, 'end': 3.98, 'text': 'Invented repeated words.',
                 'confidence': .79}]
        self.assertEqual(filter_isolated_without_speech(rows, []), [])

    def test_lone_title_sequence_cue_is_removed_even_when_vad_is_fooled(self):
        rows = [
            {'start': 2.75, 'end': 4.29, 'text': "You just don't have the number two."},
            {'start': 241.98, 'end': 243.34, 'text': '19 months at sea.'},
        ]
        speech = [{'start': 2.7, 'end': 4.4}, {'start': 241.8, 'end': 243.5}]
        result = filter_isolated_without_speech(rows, speech)
        self.assertEqual([row['text'] for row in result], ['19 months at sea.'])

    def test_quiet_cue_inside_dialogue_is_not_removed(self):
        rows = [
            {'start': 10, 'end': 11, 'text': 'Before.'},
            {'start': 13, 'end': 14, 'text': 'Quiet but real.'},
            {'start': 16, 'end': 17, 'text': 'After.'},
        ]
        result = filter_isolated_without_speech(
            rows, [{'start': 9.8, 'end': 11.2}, {'start': 15.8, 'end': 17.2}]
        )
        self.assertEqual(result, rows)

    def test_qwen_alignment_splits_sentences_and_applies_window_offset(self):
        items = [
            {'text': 'Circled', 'start': 8.0, 'end': 8.4},
            {'text': 'Spain,', 'start': 8.45, 'end': 8.9},
            {'text': 'nowhere', 'start': 9.0, 'end': 9.5},
            {'text': 'safe.', 'start': 9.55, 'end': 10.0},
            {'text': 'Bonjour.', 'start': 12.0, 'end': 12.5},
        ]
        result = split_qwen_alignment(items, offset=240)
        self.assertEqual([row['text'] for row in result],
                         ['Circled Spain, nowhere safe.', 'Bonjour.'])
        self.assertAlmostEqual(result[0]['start'], 247.94)

    def test_qwen_short_gap_is_bounded_to_two_windows(self):
        base = [
            {'start': 1050.07, 'end': 1051.29, 'text': 'Save the powder.'},
            {'start': 1058.44, 'end': 1059.12, 'text': 'Can', 'confidence': .85},
            {'start': 1062.93, 'end': 1064.03, 'text': 'you get my medical bag?'},
        ]
        context = [dict(base[1])]
        precision = [{'start': 1058.52, 'end': 1059.06,
                      'text': 'Can', 'confidence': .93}]
        speech = [{'start': 1058.01, 'end': 1058.53}]
        windows = plan_qwen_short_disagreements(
            base, context, precision, speech, 1100)
        self.assertEqual([row['start'] for row in windows], [1051.0, 1053.0])
        self.assertTrue(all(row['target_start'] == 1058.44 for row in windows))
        self.assertEqual(plan_qwen_short_disagreements(
            base, context, precision, [], 1100), [])

    def test_two_targeted_qwen_readings_restore_one_word_only_with_speech(self):
        primary = [
            {'start': 1050.07, 'end': 1051.29, 'text': 'Save the powder.'},
            {'start': 1062.93, 'end': 1064.03, 'text': 'you get my medical bag?'},
        ]
        candidate = {'start': 1057.94, 'end': 1058.46,
                     'text': 'Merci.', 'confidence': 1.0}
        windows = [
            {'start': 1051, 'end': 1067, 'target_start': 1058.44,
             'target_end': 1059.12, 'segments': [dict(candidate)]},
            {'start': 1053, 'end': 1069, 'target_start': 1058.44,
             'target_end': 1059.12, 'segments': [dict(candidate)]},
        ]
        speech = [{'start': 1058.01, 'end': 1058.53}]
        result = reconcile_qwen_segments(primary, windows, speech)
        self.assertEqual([row['text'] for row in result],
                         ['Save the powder.', 'Merci.', 'you get my medical bag?'])
        self.assertEqual(reconcile_qwen_segments(primary, windows[:1], speech), primary)
        self.assertEqual(reconcile_qwen_segments(primary, windows, []), primary)

    def test_disputed_code_switch_needs_forced_read_and_v2_agreement(self):
        original = {'start': 990.68, 'end': 991.72,
                    'text': 'Sit down, il sait.', 'confidence': .85}
        precision = [{'start': 990.58, 'end': 991.74,
                      'text': 'Sit down, you say.', 'confidence': .63}]
        planned = plan_code_switch_disagreements([original], precision, 1100)
        self.assertEqual(len(planned), 1)
        self.assertEqual((planned[0]['start'], planned[0]['end']), (984.0, 997.0))
        self.assertEqual(planned[0]['language_hint'], 'English')
        qwen = [{**planned[0], 'segments': [
            {'start': 990.62, 'end': 991.74,
             'text': 'Sit down, he said.', 'confidence': 1.0}]}]
        v2 = [{'start': 990.60, 'end': 991.76,
               'text': 'Sit down, he said.', 'confidence': .62}]
        speech = [{'start': 990.88, 'end': 991.94}]
        result = reconcile_code_switch_readings([original], qwen, v2, speech)
        self.assertEqual(result[0]['text'], 'Sit down, he said.')
        self.assertEqual(reconcile_code_switch_readings(
            [original], qwen, [], speech), [original])
        self.assertEqual(reconcile_code_switch_readings(
            [original], qwen, v2, []), [original])

    def test_extracts_english_suffix_from_combined_french_qwen_cue(self):
        items = [
            {'text': 'français', 'start': 5.2, 'end': 5.76},
            {'text': 'Sit', 'start': 6.8, 'end': 7.04},
            {'text': 'down', 'start': 7.04, 'end': 7.36},
            {'text': 'he', 'start': 7.44, 'end': 7.52},
            {'text': 'said', 'start': 7.52, 'end': 7.76},
        ]
        result = extract_qwen_target_phrase(
            items, 984, 990.68, 991.72, 'Sit down, il sait.')
        self.assertEqual(result['text'], 'Sit down he said')
        self.assertAlmostEqual(result['start'], 990.74)
        self.assertIsNone(extract_qwen_target_phrase(
            items, 984, 990.68, 991.72, 'Different beginning.'))

    def test_two_qwen_windows_remove_ghost_and_restore_fuller_speech(self):
        primary = [
            {'start': 243.56, 'end': 244.16, 'text': 'Maybe'},
            {'start': 250.25, 'end': 251.12, 'text': 'Nowhere safe.'},
        ]
        recovered = {'start': 247.9, 'end': 251.1,
                     'text': 'Circled Spain, nowhere safe.', 'confidence': 1.0}
        windows = [
            {'start': 220, 'end': 250.9, 'segments': [dict(recovered)]},
            {'start': 242, 'end': 272, 'segments': [dict(recovered)]},
        ]
        speech = [{'start': 247.8, 'end': 251.2}]
        result = reconcile_qwen_segments(primary, windows, speech)
        self.assertEqual([row['text'] for row in result],
                         ['Circled Spain, nowhere safe.'])

    def test_one_qwen_reading_cannot_invent_a_new_subtitle(self):
        primary = [{'start': 10, 'end': 11, 'text': 'Real line.'}]
        windows = [
            {'start': 0, 'end': 30, 'segments': [
                {'start': 15, 'end': 16, 'text': 'Invented words.', 'confidence': 1.0}
            ]},
            {'start': 12, 'end': 42, 'segments': []},
        ]
        result = reconcile_qwen_segments(primary, windows, [{'start': 15, 'end': 16}])
        self.assertEqual(result, primary)

    def test_rescue_onset_corrects_a_vad_anchored_early_start(self):
        # Real numbers from a production trace: the VAD-driven main pass
        # anchored this cue a second before the first clear word; the no-VAD
        # rescue pass read the identical sentence from real word timestamps.
        base = [{'start': 6.65, 'end': 10.86,
                 'text': "I don't know if this is the place I'm supposed to be."}]
        rescue = [{'start': 8.54, 'end': 10.9,
                   'text': "I don't know if this is the place I'm supposed to be.",
                   'confidence': .944, 'corroborated': False}]
        result = correct_rescue_onset_drift(base, rescue)
        self.assertEqual(result[0]['start'], 8.54)
        self.assertEqual(result[0]['end'], 10.86)

    def test_rescue_onset_correction_tolerates_a_one_word_disagreement(self):
        base = [{'start': 21.28, 'end': 25.64, 'text': "I'm looking for a friend of mine."}]
        rescue = [{'start': 23.805, 'end': 25.665,
                   'text': "You're looking for a friend of mine",
                   'confidence': .331, 'corroborated': False}]
        result = correct_rescue_onset_drift(base, rescue)
        self.assertEqual(result[0]['start'], 23.805)
        self.assertEqual(result[0]['text'], "I'm looking for a friend of mine.")

    def test_rescue_onset_correction_ignores_unrelated_text(self):
        base = [{'start': 6.65, 'end': 10.86, 'text': 'Totally different sentence here.'}]
        rescue = [{'start': 8.54, 'end': 10.9,
                   'text': "I don't know if this is the place I'm supposed to be.",
                   'confidence': .944, 'corroborated': False}]
        self.assertEqual(correct_rescue_onset_drift(base, rescue), base)

    def test_rescue_onset_correction_never_moves_start_earlier(self):
        base = [{'start': 6.65, 'end': 10.86,
                 'text': "I don't know if this is the place I'm supposed to be."}]
        rescue = [{'start': 5.0, 'end': 10.9,
                   'text': "I don't know if this is the place I'm supposed to be.",
                   'confidence': .944, 'corroborated': False}]
        self.assertEqual(correct_rescue_onset_drift(base, rescue), base)

    def test_rescue_onset_correction_never_overlaps_the_previous_cue(self):
        base = [
            {'start': 4.0, 'end': 6.5, 'text': 'Earlier line.'},
            {'start': 6.65, 'end': 10.86,
             'text': "I don't know if this is the place I'm supposed to be."},
        ]
        rescue = [{'start': 6.0, 'end': 10.9,
                   'text': "I don't know if this is the place I'm supposed to be.",
                   'confidence': .944, 'corroborated': False}]
        result = correct_rescue_onset_drift(base, rescue)
        self.assertEqual(result[1]['start'], 6.65)

    def test_word_onset_fixes_phrase_inside_longer_rescue_reading(self):
        base = [
            {'start': 96.01, 'end': 97.83, 'text': 'You do whatever it took to find him.'},
            {'start': 97.89, 'end': 100.64, 'text': 'Keep going.'},
            {'start': 100.8, 'end': 101.76, 'text': "That's what we do."},
        ]
        tokens = [('you', 99.62, 100.06, .8), ('keep', 100.06, 100.28, .81),
                  ('going', 100.28, 100.56, .98), ("that's", 100.56, 101.28, .94),
                  ('what', 101.28, 101.38, .98), ('we', 101.38, 101.54, .99),
                  ('do', 101.54, 101.74, .96)]
        rescue = [{'start': 99.535, 'end': 101.815,
                   'text': "you keep going that's what we do",
                   'words': [{'text': text, 'start': start, 'end': end,
                              'probability': probability}
                             for text, start, end, probability in tokens]}]
        result = correct_rescue_word_onsets(base, rescue)
        self.assertEqual(result[1]['start'], 100.06)
        self.assertEqual(result[1]['text'], 'Keep going.')
        self.assertEqual(result[1]['end'], 100.64)
        self.assertEqual(result[2], base[2])

    def test_rescue_restores_confident_lead_before_agreed_suffix(self):
        base = [
            {'start': 105.89, 'end': 106.95, 'text': 'I should have known.'},
            {'start': 107.26, 'end': 111.41, 'text': 'All the people I left behind.'},
        ]
        tokens = [('I', 106.875, 107.44, .92), ('keep', 107.44, 109.76, .99),
                  ('thinking', 109.76, 110.02, .99), ('about', 110.02, 110.24, .99),
                  ('all', 110.24, 110.36, .99), ('the', 110.36, 110.48, .99),
                  ('people', 110.48, 110.72, .99), ('I', 110.72, 110.84, .99),
                  ('left', 110.84, 110.96, .99), ('behind', 110.96, 111.36, .99)]
        rescue = [{'start': 106.875, 'end': 111.435,
                   'text': 'i keep thinking about all the people i left behind',
                   'confidence': .876,
                   'words': [{'text': text, 'start': start, 'end': end,
                              'probability': probability}
                             for text, start, end, probability in tokens]}]
        result = extend_rescue_prefix(base, rescue)
        self.assertEqual(result[1]['text'],
                         'I keep thinking about all the people I left behind.')
        self.assertEqual(result[1]['start'], 106.96)
        self.assertEqual(result[1]['end'], 111.41)
        self.assertEqual(result[0], base[0])
        rescue[0]['words'][0]['probability'] = .3
        self.assertEqual(extend_rescue_prefix(base, rescue), base)

    def test_rescue_prefix_does_not_swallow_previous_subtitle(self):
        base = [
            {'start': 97.89, 'end': 100.64, 'text': 'Keep going.'},
            {'start': 100.8, 'end': 101.76, 'text': "That's what we do."},
        ]
        tokens = [('you', 99.62, 100.06), ('keep', 100.06, 100.28),
                  ('going', 100.28, 100.56), ("that's", 100.56, 101.28),
                  ('what', 101.28, 101.38), ('we', 101.38, 101.54),
                  ('do', 101.54, 101.74)]
        rescue = [{'start': 99.535, 'end': 101.815,
                   'text': "you keep going that's what we do", 'confidence': .9,
                   'words': [{'text': text, 'start': start, 'end': end,
                              'probability': .9} for text, start, end in tokens]}]
        self.assertEqual(extend_rescue_prefix(base, rescue), base)

    def test_restores_precision_subject_confirmed_by_independent_pass(self):
        # Real numbers from a production trace (Daryl Dixon Official Trailer,
        # ~46.5s): the main pass dropped the subject "I"; the precision pass
        # heard the same sentence, with the same words after it, and high
        # confidence.
        base = [{'start': 46.99, 'end': 47.53, 'text': 'am Laurent.', 'confidence': .453}]
        precision = [{'start': 46.52, 'end': 47.54, 'text': 'I am Laurent.', 'confidence': .727}]
        result = restore_precision_subjects(base, precision)
        self.assertEqual(result[0]['start'], 46.52)
        self.assertEqual(result[0]['text'], 'I am Laurent.')

    def test_precision_subject_restore_requires_the_same_rest_of_sentence(self):
        base = [{'start': 46.99, 'end': 47.53, 'text': 'am Laurent.', 'confidence': .453}]
        precision = [{'start': 46.52, 'end': 47.54, 'text': 'She saw Laurent.', 'confidence': .9}]
        self.assertEqual(restore_precision_subjects(base, precision), base)

    def test_precision_subject_restore_skips_an_already_confident_line(self):
        base = [{'start': 46.99, 'end': 47.53, 'text': 'am Laurent.', 'confidence': .8}]
        precision = [{'start': 46.52, 'end': 47.54, 'text': 'I am Laurent.', 'confidence': .9}]
        self.assertEqual(restore_precision_subjects(base, precision), base)

    def test_v2_second_opinion_window_targets_a_short_unsupported_cue(self):
        base = [{'start': 13.62, 'end': 14.26, 'text': 'paysans.', 'confidence': .757}]
        precision = [{'start': 11.54, 'end': 12.88, 'text': "There's a word in French.",
                      'confidence': .965}]
        windows = plan_v2_second_opinion_windows(base, precision, [], 90)
        self.assertEqual(len(windows), 1)
        self.assertLessEqual(windows[0]['start'], 13.62)
        self.assertGreaterEqual(windows[0]['end'], 14.26)

    def test_v2_second_opinion_window_skips_a_cue_precision_already_confirms(self):
        base = [{'start': 13.62, 'end': 14.26, 'text': 'paysans.', 'confidence': .757}]
        precision = [{'start': 13.6, 'end': 14.3, 'text': 'paysans.', 'confidence': .9}]
        self.assertEqual(plan_v2_second_opinion_windows(base, precision, [], 90), [])

    def test_v2_second_opinion_window_targets_a_rescue_confirmed_article(self):
        base = [{'start': 48.67, 'end': 50.85, 'text': "You're messenger to deliver Laurent.",
                 'confidence': .75}]
        rescue = [{'start': 49.055, 'end': 50.885,
                   'text': "You're the messenger to deliver Laurent.", 'confidence': .685}]
        windows = plan_v2_second_opinion_windows(base, [], rescue, 90)
        self.assertEqual(len(windows), 1)

    def test_v2_second_opinion_replaces_a_short_word_with_the_one_unique_alternative(self):
        base = [{'start': 13.62, 'end': 14.26, 'text': 'paysans.', 'confidence': .757}]
        precision = []
        v2_rows = [{'start': 13.6, 'end': 14.3, 'text': 'dépaysant.', 'confidence': .7}]
        result = reconcile_v2_second_opinions(base, precision, [], v2_rows)
        self.assertEqual(result[0]['text'], 'dépaysant.')

    def test_v2_second_opinion_refuses_two_competing_alternatives(self):
        base = [{'start': 13.62, 'end': 14.26, 'text': 'paysans.', 'confidence': .757}]
        v2_rows = [
            {'start': 13.6, 'end': 14.3, 'text': 'dépaysant.', 'confidence': .7},
            {'start': 13.65, 'end': 14.28, 'text': 'déplaisant.', 'confidence': .65},
        ]
        result = reconcile_v2_second_opinions(base, [], [], v2_rows)
        self.assertEqual(result[0]['text'], 'paysans.')

    def test_v2_second_opinion_inserts_an_article_confirmed_by_v2(self):
        # Real numbers from a production trace (Daryl Dixon Official
        # Trailer): the main pass dropped "the"; the no-VAD rescue pass
        # heard it, and an independent v2 reading confirms the same word
        # sequence around it before it is trusted.
        base = [{'start': 48.67, 'end': 50.85, 'text': "You're messenger to deliver Laurent.",
                 'confidence': .75}]
        rescue = [{'start': 49.055, 'end': 50.885,
                   'text': "You're the messenger to deliver Laurent.", 'confidence': .685}]
        v2_rows = [{'start': 48.9, 'end': 51.0,
                    'text': "you're the messenger to deliver Laurent",
                    'confidence': .6}]
        result = reconcile_v2_second_opinions(base, [], rescue, v2_rows)
        self.assertEqual(result[0]['text'], "You're the messenger to deliver Laurent.")

    def test_v2_second_opinion_does_not_insert_an_unconfirmed_article(self):
        base = [{'start': 48.67, 'end': 50.85, 'text': "You're messenger to deliver Laurent.",
                 'confidence': .75}]
        rescue = [{'start': 49.055, 'end': 50.885,
                   'text': "You're the messenger to deliver Laurent.", 'confidence': .685}]
        self.assertEqual(reconcile_v2_second_opinions(base, [], rescue, []), base)

    def _hp_rescue_words(self, tokens):
        return [{'text': text, 'start': start, 'end': end, 'probability': probability}
                for text, start, end, probability in tokens]

    def test_rescue_resegmentation_merges_a_misplaced_word_boundary(self):
        # Real numbers from a production trace (Harry Potter and the Deathly
        # Hallows Part 2 trailer, ~95s): the main pass split one sentence at
        # the wrong word, dropping its lead ("I never") and attaching its
        # last word ("you'll") to the next line instead. The no-VAD rescue
        # pass read the whole sentence correctly in one continuous reading.
        base = [
            {'start': 95.13, 'end': 96.47, 'text': 'any of you to die for', 'confidence': .998},
            {'start': 96.47, 'end': 97.77, 'text': "me you'll", 'confidence': .985},
        ]
        words = self._hp_rescue_words([
            ('I', 93.97, 94.41, .559), ('never', 94.41, 94.85, .953),
            ('wanted', 94.85, 95.19, .996), ('any', 95.19, 95.63, .997),
            ('of', 95.63, 95.79, .998), ('you', 95.79, 95.97, .999),
            ('to', 95.97, 96.11, .996), ('die', 96.11, 96.31, .999),
            ('for', 96.31, 96.45, .999), ('me', 96.45, 96.65, 1.0),
        ])
        rescue = [{'start': 93.89, 'end': 96.73,
                   'text': 'I never wanted any of you to die for me',
                   'confidence': .559, 'words': words}]
        result = reconcile_rescue_resegmentation(base, rescue)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['text'], 'I never wanted any of you to die for me')
        self.assertAlmostEqual(result[0]['start'], 93.89)
        self.assertAlmostEqual(result[0]['end'], 96.73)

    def test_rescue_resegmentation_never_loses_words_the_main_pass_already_had(self):
        base = [{'start': 95.13, 'end': 97.77, 'text': 'any of you to die for me your call'}]
        words = self._hp_rescue_words([
            ('any', 95.19, 95.63, .99), ('of', 95.63, 95.79, .99),
            ('you', 95.79, 95.97, .99), ('to', 95.97, 96.11, .99),
            ('die', 96.11, 96.31, .99), ('for', 96.31, 96.45, .99),
        ])
        rescue = [{'start': 95.19, 'end': 96.45, 'text': 'any of you to die for',
                   'confidence': .9, 'words': words}]
        self.assertEqual(reconcile_rescue_resegmentation(base, rescue), base)

    def test_rescue_resegmentation_does_not_bleed_into_the_next_cue(self):
        base = [
            {'start': 100.29, 'end': 101.36, 'text': "You'll stay with me."},
            {'start': 101.36, 'end': 101.87, 'text': 'Always.'},
        ]
        words = self._hp_rescue_words([
            ("you'll", 100.33, 100.77, .9), ('stay', 100.77, 100.93, .99),
            ('with', 100.93, 101.09, .99), ('me', 101.09, 101.27, .99),
            ('always', 101.27, 101.83, .76),
        ])
        rescue = [{'start': 100.25, 'end': 101.83,
                   'text': "you'll stay with me always",
                   'confidence': .9, 'words': words}]
        self.assertEqual(reconcile_rescue_resegmentation(base, rescue), base)

    def test_rescue_resegmentation_never_crosses_into_an_unrelated_rescue_reading(self):
        # Real numbers from a production trace: two separate rescue readings
        # ("Come on, Tom." and "Let's finish this the way we started.") sit
        # back-to-back with no pause between them at all. A first version of
        # this fix merged them into one run-on, unpunctuated line, silently
        # discarding a previously clean, correctly split cue -- a real
        # regression the pause-only check could not catch.
        base = [{'start': 108.26, 'end': 109.76, 'text': "Let's finish this the way we started."}]
        come_on_tom = self._hp_rescue_words([
            ('come', 106.83, 107.27, .67), ('on', 107.27, 107.71, .99),
            ('Tom', 107.71, 107.93, .77),
        ])
        lets_finish = self._hp_rescue_words([
            ("let's", 107.93, 108.71, .91), ('finish', 108.71, 108.89, .99),
            ('this', 108.89, 109.05, .98), ('the', 109.05, 109.15, .99),
            ('way', 109.15, 109.27, .99), ('we', 109.27, 109.39, .99),
            ('started', 109.39, 109.65, .99),
        ])
        together = self._hp_rescue_words([('together', 109.65, 110.47, .55)])
        rescue = [
            {'start': 106.75, 'end': 107.93, 'text': 'come on Tom',
             'confidence': .67, 'words': come_on_tom},
            {'start': 107.93, 'end': 109.65, 'text': "let's finish this the way we started",
             'confidence': .91, 'words': lets_finish},
            {'start': 109.65, 'end': 110.47, 'text': 'together',
             'confidence': .55, 'words': together},
        ]
        self.assertEqual(reconcile_rescue_resegmentation(base, rescue), base)

    def test_rescue_resegmentation_never_coarsens_two_already_clean_sentences(self):
        # Real numbers from a production trace: two existing cues were
        # already complete, correctly punctuated sentences on their own. A
        # single rescue reading happened to span both plus some genuinely
        # new leading words ("you've kept him alive") in one continuous,
        # unpunctuated read. A first version of this fix collapsed the two
        # clean sentences into one worse, run-on line to gain those words --
        # a real regression: recovering a few words is not worth destroying
        # sentence structure that was already correct.
        base = [
            {'start': 42.06, 'end': 44.56, 'text': 'so that he can die at the proper moment.',
             'confidence': .435},
            {'start': 44.76, 'end': 45.73, 'text': 'Bring him to me.', 'confidence': .992},
        ]
        words = self._hp_rescue_words([
            ("you've", 40.995, 41.595, .559), ('kept', 41.595, 41.795, .993),
            ('him', 41.795, 41.975, .985), ('alive', 41.975, 42.155, .998),
            ('so', 42.155, 42.535, .964), ('that', 42.535, 42.675, .461),
            ('he', 42.675, 42.775, .978), ('can', 42.775, 42.895, .989),
            ('die', 42.895, 43.135, .994), ('at', 43.135, 43.415, .992),
            ('the', 43.415, 43.655, .996), ('proper', 43.655, 44.035, .998),
            ('moment', 44.035, 44.475, .998), ('bring', 44.475, 44.995, .909),
            ('him', 44.995, 45.235, .997), ('to', 45.235, 45.435, .999),
            ('me', 45.435, 45.695, .998),
        ])
        rescue = [{'start': 40.915, 'end': 45.775,
                   'text': "you've kept him alive so that he can die at the proper moment bring him to me",
                   'confidence': .461, 'words': words}]
        self.assertEqual(reconcile_rescue_resegmentation(base, rescue), base)

    def test_rescue_resegmentation_protects_clean_sentences_even_with_a_messy_neighbour(self):
        # The exact real bug: an unrelated, unpunctuated third cue ("I'm
        # alive") sitting right before the two already-clean sentences let
        # the group slip past a first version of the "already clean" guard,
        # which only checked whether *every* cue in the group was
        # punctuated.
        base = [
            {'start': 37.34, 'end': 38.0, 'text': "I'm alive", 'confidence': .618},
            {'start': 42.06, 'end': 44.56, 'text': 'so that he can die at the proper moment.',
             'confidence': .435},
            {'start': 44.76, 'end': 45.73, 'text': 'Bring him to me.', 'confidence': .992},
        ]
        words = self._hp_rescue_words([
            ("you've", 40.995, 41.595, .559), ('kept', 41.595, 41.795, .993),
            ('him', 41.795, 41.975, .985), ('alive', 41.975, 42.155, .998),
            ('so', 42.155, 42.535, .964), ('that', 42.535, 42.675, .461),
            ('he', 42.675, 42.775, .978), ('can', 42.775, 42.895, .989),
            ('die', 42.895, 43.135, .994), ('at', 43.135, 43.415, .992),
            ('the', 43.415, 43.655, .996), ('proper', 43.655, 44.035, .998),
            ('moment', 44.035, 44.475, .998), ('bring', 44.475, 44.995, .909),
            ('him', 44.995, 45.235, .997), ('to', 45.235, 45.435, .999),
            ('me', 45.435, 45.695, .998),
        ])
        rescue = [{'start': 40.915, 'end': 45.775,
                   'text': "you've kept him alive so that he can die at the proper moment bring him to me",
                   'confidence': .461, 'words': words}]
        self.assertEqual(reconcile_rescue_resegmentation(base, rescue), base)

    def test_precision_split_restores_three_sentences_from_one_unpunctuated_blob(self):
        # Real numbers from the same trace: the main pass swallowed three
        # short spoken sentences into one long cue with no terminal
        # punctuation at all ("...let's" bled in from the next line too);
        # precision's own pass heard them as three separate, properly
        # punctuated sentences.
        base = [{'start': 100.68, 'end': 104.36,
                 'text': "stay with me always until the end let's"}]
        precision = [
            {'start': 100.29, 'end': 101.36, 'text': "You'll stay with me.",
             'confidence': .953},
            {'start': 101.36, 'end': 101.87, 'text': 'Always.', 'confidence': .942},
            {'start': 102.05, 'end': 103.07, 'text': 'Until the end.', 'confidence': .989},
        ]
        result = restore_precision_sentence_split(base, precision)
        self.assertEqual([row['text'] for row in result],
                         ["You'll stay with me.", 'Always.', 'Until the end.'])

    def test_precision_split_requires_terminal_punctuation_on_every_candidate(self):
        base = [{'start': 100.68, 'end': 104.36,
                 'text': "stay with me always until the end let's"}]
        precision = [
            {'start': 100.29, 'end': 101.36, 'text': "you'll stay with me", 'confidence': .95},
            {'start': 101.36, 'end': 101.87, 'text': 'always', 'confidence': .94},
        ]
        self.assertEqual(restore_precision_sentence_split(base, precision), base)

    def test_precision_split_leaves_an_already_punctuated_cue_alone(self):
        base = [{'start': 41.29, 'end': 43.25,
                 'text': "No idea what's waiting on the other side."}]
        precision = [
            {'start': 41.3, 'end': 42.0, 'text': "No idea", 'confidence': .9},
            {'start': 42.0, 'end': 43.25, 'text': "what's waiting on the other side.",
             'confidence': .9},
        ]
        self.assertEqual(restore_precision_sentence_split(base, precision), base)

    def test_whispered_speech_is_recovered_in_a_long_silent_gap(self):
        # Real numbers from a production trace (Harry Potter trailer): VAD
        # filtering skipped a whispered line entirely (no cue at all for
        # 0-27.6s). A no-VAD pass over the whole gap heard it clearly.
        base = [{'start': 34.12, 'end': 34.81, 'text': 'Join me.'}]
        candidates = [
            {'start': 5.96, 'end': 8.84, 'text': 'Harry, be safe.', 'confidence': .7},
            {'start': 10.00, 'end': 10.70, 'text': 'Be strong.', 'confidence': .65},
        ]
        result = insert_confident_whispered_speech(base, candidates, 148.69)
        self.assertEqual([row['text'] for row in result],
                         ['Harry, be safe.', 'Be strong.', 'Join me.'])

    def test_whispered_speech_rejects_a_silence_stock_phrase(self):
        # Real numbers from the same trace: a different long gap (near the
        # very end of the trailer) produced "We'll be right back." with a
        # high no_speech probability in the raw Whisper segment -- a classic
        # hallucination pattern near silence, not real dialogue.
        base = [{'start': 108.26, 'end': 109.76, 'text': "Let's finish this the way we started."}]
        candidates = [
            {'start': 140.88, 'end': 147.06, 'text': "We'll be right back.", 'confidence': .8},
        ]
        self.assertEqual(insert_confident_whispered_speech(base, candidates, 148.69), base)

    def test_whispered_speech_requires_minimum_confidence(self):
        base = []
        candidates = [{'start': 5.0, 'end': 8.0, 'text': 'Some guessed words here',
                       'confidence': .2}]
        self.assertEqual(insert_confident_whispered_speech(base, candidates, 60.0), base)

    def test_whispered_speech_rejects_a_single_word(self):
        base = []
        candidates = [{'start': 5.0, 'end': 6.0, 'text': 'Harry', 'confidence': .9}]
        self.assertEqual(insert_confident_whispered_speech(base, candidates, 60.0), base)

    def test_whispered_speech_never_overlaps_an_existing_cue(self):
        base = [{'start': 5.0, 'end': 9.0, 'text': 'Real existing line.'}]
        candidates = [{'start': 6.0, 'end': 7.0, 'text': 'Conflicting guess', 'confidence': .9}]
        self.assertEqual(insert_confident_whispered_speech(base, candidates, 60.0), base)

    def test_whispered_speech_respects_the_maximum_gap_bound(self):
        base = [{'start': 50.0, 'end': 51.0, 'text': 'Far away line.'}]
        candidates = [{'start': 5.0, 'end': 7.0, 'text': 'Too far from anything', 'confidence': .9}]
        self.assertEqual(
            insert_confident_whispered_speech(base, candidates, 60.0, maximum_gap=10.0), base)

    def _v2_words(self, tokens):
        return [{'text': text, 'start': start, 'end': end, 'probability': probability}
                for text, start, end, probability in tokens]

    def test_v2_word_check_targets_a_longer_low_confidence_sentence(self):
        base = [
            {'start': 40.36, 'end': 41.82, 'text': 'Dobbiamo proteggere la mia gente.',
             'confidence': .97},
            {'start': 41.82, 'end': 44.06, 'text': 'Il nostro villaggio era sempre sotto a se.',
             'confidence': .05},
        ]
        windows = plan_v2_word_check_windows(base, 120.0)
        self.assertEqual(len(windows), 1)
        self.assertLessEqual(windows[0]['start'], 41.82)
        self.assertGreaterEqual(windows[0]['end'], 44.06)

    def test_v2_word_check_ignores_an_already_confident_sentence(self):
        base = [{'start': 40.36, 'end': 41.82, 'text': 'Dobbiamo proteggere la mia gente.',
                 'confidence': .97}]
        self.assertEqual(plan_v2_word_check_windows(base, 120.0), [])

    def test_v2_substitution_swaps_the_one_mis_heard_word(self):
        # Real numbers from a production trace (Daryl Dixon S.3, Italian
        # trailer): large-v3's own reading was grammatically broken Italian
        # ("sotto a sé"); an independent large-v2 reading of the same audio
        # heard "sotto assedio" ("under siege") with reasonable confidence,
        # and every other word in the sentence matches exactly.
        base = [{'start': 41.82, 'end': 44.06,
                 'text': 'Il nostro villaggio era sempre sotto assieme.', 'confidence': .05}]
        words = self._v2_words([
            ('Il', 42.42, 42.48, .89), ('nostro', 42.48, 42.74, .87),
            ('villaggio', 42.74, 43.10, .89), ('era', 43.10, 43.28, .79),
            ('sempre', 43.28, 43.56, .82), ('sotto', 43.56, 43.88, .85),
            ('assedio.', 43.88, 44.36, .77),
        ])
        v2_rows = [{'start': 42.42, 'end': 44.36,
                   'text': 'Il nostro villaggio era sempre sotto assedio.',
                   'confidence': .77, 'words': words}]
        result = reconcile_v2_word_substitutions(base, v2_rows)
        self.assertEqual(result[0]['text'], 'Il nostro villaggio era sempre sotto assedio.')

    def test_v2_substitution_handles_a_second_real_case_in_the_same_trailer(self):
        base = [{'start': 48.16, 'end': 50.22,
                 'text': 'Quindi il villaggio offre una delle tue filtre.', 'confidence': .43}]
        words = self._v2_words([
            ('Quindi', 48.44, 48.74, .78), ('il', 48.74, 48.86, .84),
            ('villaggio', 48.86, 49.16, .91), ('offre', 49.16, 49.44, .91),
            ('una', 49.44, 49.58, .82), ('delle', 49.58, 49.76, .71),
            ('tue', 49.76, 49.90, .92), ('figlie.', 49.90, 50.26, .91),
        ])
        v2_rows = [{'start': 48.44, 'end': 50.26,
                   'text': 'Quindi il villaggio offre una delle tue figlie.',
                   'confidence': .91, 'words': words}]
        result = reconcile_v2_word_substitutions(base, v2_rows)
        self.assertEqual(result[0]['text'], 'Quindi il villaggio offre una delle tue figlie.')

    def test_v2_substitution_requires_every_other_word_to_match(self):
        base = [{'start': 41.82, 'end': 44.06,
                 'text': 'Il nostro villaggio era sempre sotto assieme.', 'confidence': .05}]
        words = self._v2_words([
            ('Il', 42.42, 42.48, .89), ('nostro', 42.48, 42.74, .87),
            ('paese', 42.74, 43.10, .89), ('era', 43.10, 43.28, .79),
            ('sempre', 43.28, 43.56, .82), ('sotto', 43.56, 43.88, .85),
            ('assedio.', 43.88, 44.36, .77),
        ])
        v2_rows = [{'start': 42.42, 'end': 44.36,
                   'text': 'Il nostro paese era sempre sotto assedio.',
                   'confidence': .77, 'words': words}]
        self.assertEqual(reconcile_v2_word_substitutions(base, v2_rows), base)

    def test_v2_substitution_requires_a_confident_replacement_word(self):
        base = [{'start': 41.82, 'end': 44.06,
                 'text': 'Il nostro villaggio era sempre sotto assieme.', 'confidence': .05}]
        words = self._v2_words([
            ('Il', 42.42, 42.48, .89), ('nostro', 42.48, 42.74, .87),
            ('villaggio', 42.74, 43.10, .89), ('era', 43.10, 43.28, .79),
            ('sempre', 43.28, 43.56, .82), ('sotto', 43.56, 43.88, .85),
            ('assedio.', 43.88, 44.36, .3),
        ])
        v2_rows = [{'start': 42.42, 'end': 44.36,
                   'text': 'Il nostro villaggio era sempre sotto assedio.',
                   'confidence': .3, 'words': words}]
        self.assertEqual(reconcile_v2_word_substitutions(base, v2_rows), base)

    def test_v2_substitution_ignores_a_different_length_sentence(self):
        base = [{'start': 58.57, 'end': 59.27, 'text': "It's his friend.", 'confidence': .31}]
        words = self._v2_words([
            ('Hogwarts', 57.84, 58.76, .17), ('is', 58.76, 59.02, .79),
            ('threatened!', 59.02, 59.48, .90),
        ])
        v2_rows = [{'start': 57.84, 'end': 59.48, 'text': 'Hogwarts is threatened!',
                   'confidence': .17, 'words': words}]
        self.assertEqual(reconcile_v2_word_substitutions(base, v2_rows), base)

    def test_whispered_onset_snaps_to_a_low_threshold_vad_span(self):
        # Real numbers from a production trace (Harry Potter trailer): the
        # no-VAD whispered-gap pass anchored "Harry, be safe, be strong."
        # at 5.96s, right where an unrelated title-card voice-over trails
        # off. A dedicated low-threshold VAD sweep (0.15, well below the
        # 0.72 used elsewhere) found nothing until ~8s -- matching what the
        # user actually heard on playback.
        candidates = [{'start': 5.96, 'end': 10.76, 'text': 'Harry, be safe, be strong.',
                       'confidence': .957}]
        speech = [{'start': 3.78, 'end': 5.45}, {'start': 8.29, 'end': 9.83}]
        result = snap_whispered_onset_to_strict_speech(candidates, speech)
        self.assertAlmostEqual(result[0]['start'], 7.99)
        self.assertEqual(result[0]['end'], 10.76)
        self.assertEqual(result[0]['text'], 'Harry, be safe, be strong.')

    def test_whispered_onset_ignores_a_tiny_shift(self):
        candidates = [{'start': 5.96, 'end': 10.76, 'text': 'Harry, be safe, be strong.'}]
        speech = [{'start': 6.05, 'end': 9.83}]
        self.assertEqual(snap_whispered_onset_to_strict_speech(candidates, speech), candidates)

    def test_whispered_onset_never_moves_past_the_maximum_shift(self):
        candidates = [{'start': 0.0, 'end': 10.76, 'text': 'Harry, be safe, be strong.'}]
        speech = [{'start': 9.5, 'end': 9.83}]
        self.assertEqual(snap_whispered_onset_to_strict_speech(candidates, speech), candidates)

    def test_whispered_onset_leaves_a_candidate_with_no_vad_support_alone(self):
        candidates = [{'start': 5.96, 'end': 10.76, 'text': 'Harry, be safe, be strong.'}]
        speech = [{'start': 20.0, 'end': 21.0}]
        self.assertEqual(snap_whispered_onset_to_strict_speech(candidates, speech), candidates)
