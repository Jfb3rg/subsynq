import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import numpy as np
import worker
from asr_windows import refine_starts, restore_lead_words

# Yukon Men S01E01 (dev job 89): the cue starts and ends as written, and the voiced stretches (voice detection, no padding). The onsets are measured; the
# ends of the stretches are close to the cue ends (the first stretch after 18.8 s carries several cues in one breath).
CUES = [(3.14, 6.00), (6.00, 9.13), (9.13, 11.84), (12.24, 15.18), (18.42, 19.60), (19.76, 20.66), (20.66, 21.32), (21.32, 23.26), (23.58, 25.52),
        (25.80, 27.86), (28.22, 30.56), (30.82, 32.02)]
SPANS = [(3.62, 5.90), (9.66, 11.70), (12.86, 15.10), (18.82, 23.20), (24.19, 25.40), (26.43, 27.80), (28.70, 30.40), (31.33, 31.95)]


def cues():
    return [{'start': start, 'end': end, 'text': f'cue {n}', 'confidence': .9} for n, (start, end) in enumerate(CUES)]


class RefineStartsTests(unittest.TestCase):
    def restored(self):
        # Real job 230: rescue/Qwen restored "you" at 10.52; VAD subsequently moved it back to 11.40.
        original = [{'start': 7.208, 'end': 8.01, 'text': 'Give me the code'},
                    {'start': 11.16, 'end': 12.18, 'text': 'were arrested', 'confidence': .744140625}]
        readings = {'rescue': [{'start': 10.52, 'end': 17.22,
                               'text': "you were arrested wearing a woman's skirt technically it's a dress i'd like it back"}],
                    'qwen': [{'start': 10.52, 'end': 12.18, 'text': 'You were arrested.'}]}
        return restore_lead_words(original, readings)[0]

    def test_vad_does_not_erase_the_measured_start_of_a_restored_lead(self):
        original = self.restored()
        refined, changed, shifts = refine_starts(original, [(7.228, 8.196), (11.52, 12.548)])
        self.assertEqual(refined, original)
        self.assertEqual(refined[1]['start'], 10.52)
        self.assertEqual((changed, shifts), (0, []))

    def test_rows_without_matching_proof_still_use_the_original_vad_rule(self):
        for edit in ('no_proof', 'changed_words', 'stale_time', 'one_reader', 'nonfinite'):
            with self.subTest(edit=edit):
                cue = self.restored()[1]
                if edit == 'no_proof':
                    cue.pop('confirmed_lead_onset')
                elif edit == 'changed_words':
                    cue['text'] = 'They were arrested'
                elif edit == 'stale_time':
                    cue['confirmed_lead_onset']['start'] -= .2
                elif edit == 'one_reader':
                    cue['confirmed_lead_onset']['readings'] = ['rescue', 'rescue']
                else:
                    cue['confirmed_lead_onset']['start'] = float('nan')
                self.assertEqual(refine_starts([cue], [(11.52, 12.548)])[0][0]['start'], 11.40)

    def test_a_cue_that_starts_before_the_voice_begins_with_it_minus_a_moment(self):
        refined, changed, shifts = refine_starts(cues(), SPANS)
        self.assertEqual([row['start'] for row in refined],
                         [3.50, 6.00, 9.54, 12.74, 18.70, 19.76, 20.66, 21.32, 24.07, 26.31, 28.58, 31.21])
        self.assertEqual(changed, 8)
        self.assertEqual(sorted(shifts), [.28, .36, .36, .39, .41, .49, .50, .51])
        self.assertTrue(all(.1 <= shift <= 1.2 for shift in shifts))

    def test_the_median_shift_matches_what_was_measured_on_the_whole_episode(self):
        _, _, shifts = refine_starts(cues(), SPANS)
        self.assertAlmostEqual(sorted(shifts)[len(shifts) // 2], .41, places=2)         # 0.45 s median over 760 cues on the episode

    def test_cues_in_one_breath_and_cues_far_from_any_voice_are_left_alone(self):
        refined, _, _ = refine_starts(cues(), SPANS)
        for index in (1, 5, 6, 7):
            self.assertEqual(refined[index]['start'], CUES[index][0], f'cue {index}')

    def test_only_the_start_changes_and_nothing_else(self):
        before = cues()
        refined, _, _ = refine_starts(before, SPANS)
        self.assertEqual([{k: v for k, v in row.items() if k != 'start'} for row in refined], [{k: v for k, v in row.items() if k != 'start'} for row in before])
        self.assertEqual([row['start'] for row in before], [start for start, _ in CUES], 'the input is not changed')

    def test_a_cue_is_never_shortened_below_the_minimum_and_a_tiny_change_is_not_made(self):
        refined, changed, _ = refine_starts([{'start': 10.0, 'end': 10.5, 'text': 'x'}], [(10.4, 12.0)])
        self.assertEqual((refined[0]['start'], changed), (10.0, 0))
        refined, changed, _ = refine_starts([{'start': 10.0, 'end': 12.0, 'text': 'x'}], [(10.15, 12.0)])
        self.assertEqual((refined[0]['start'], changed), (10.0, 0), 'a shift under 0.1 s is jitter')

    def test_no_voice_found_changes_nothing(self):
        refined, changed, shifts = refine_starts(cues(), [])
        self.assertEqual((changed, shifts), (0, []))
        self.assertEqual([row['start'] for row in refined], [start for start, _ in CUES])

    def test_unsorted_spans_are_fine_and_a_later_onset_than_the_limit_is_ignored(self):
        refined, _, _ = refine_starts([{'start': 5.0, 'end': 9.0, 'text': 'x'}], [(8.0, 9.0), (6.0, 7.0)])
        self.assertEqual(refined[0]['start'], 5.88)
        refined, _, _ = refine_starts([{'start': 5.0, 'end': 9.0, 'text': 'x'}], [(6.4, 7.0)])
        self.assertEqual(refined[0]['start'], 5.0, '1.4 s is more than a cue start can be wrong by: another line')


class SpeechRateTests(unittest.TestCase):
    """A start is never moved so late that the words cannot be spoken. The voice detection lags behind speech over music or noise and behind a fast line."""

    def test_wife_and_dog_the_daddy_line_keeps_the_time_to_say_its_eight_words(self):
        # job 154: spoken from about 45.1 s (a clean reading puts "did" at 45.22 s), the detector found a voice from 45.6 s (music underneath); the start went to 45.48 s
        cue = {'start': 44.42, 'end': 46.44, 'text': 'And what did he find in Hell, Daddy?', 'confidence': .67}
        refined, changed, shifts = refine_starts([cue], [(45.6, 46.4)])
        self.assertEqual((refined[0]['start'], changed, shifts), (45.107, 1, [.687]))
        self.assertEqual(refine_starts([cue], [(45.6, 46.4)], maximum_rate=None)[0][0]['start'], 45.48, 'without the limit: the old result')

    def test_yukon_men_the_fast_cues_the_old_shift_cut_into(self):
        # job 89 cue starts, voice onsets and, in brackets, where a clean reading of the audio puts the first word
        for start, end, onset, text, spoken_from in (
                (2469.09, 2470.09, 2469.58, 'Did you guys hear that?', 2469.14), (1282.54, 1283.88, 1283.36, "And we're running out of wood.", 1282.66),
                (2233.43, 2235.43, 2234.46, "Hey, let's get as close as we can.", 2233.86), (265.94, 267.28, 266.4, "A whiteout, it's when you can barely see", 265.96)):
            refined, _, _ = refine_starts([{'start': start, 'end': end, 'text': text}], [(onset, end - .1)])
            self.assertLessEqual(refined[0]['start'], spoken_from + .15, text)

    def test_a_slow_cue_is_moved_all_the_way_and_short_or_unspaced_text_is_not_limited(self):
        self.assertEqual(refine_starts([{'start': 10.0, 'end': 16.0, 'text': 'one two three four five six seven eight'}], [(11.0, 15.5)])[0][0]['start'], 10.88)
        self.assertEqual(refine_starts([{'start': 10.0, 'end': 11.0, 'text': 'Truth?'}], [(10.5, 10.9)])[0][0]['start'], 10.38, 'one word: no rate to speak of')
        self.assertEqual(refine_starts([{'start': 10.0, 'end': 11.2, 'text': '我们必须马上离开这里不然就来不及了'}], [(10.8, 11.1)])[0][0]['start'], 10.68, 'no spaces between words')

    def test_a_shift_that_the_limit_cuts_below_the_jitter_is_not_made(self):
        cue = {'start': 100.0, 'end': 101.5, 'text': 'one two three four five six seven eight nine'}        # 9 words need 1.5 s at 6 words per second
        refined, changed, _ = refine_starts([cue], [(100.5, 101.4)])
        self.assertEqual((refined[0]['start'], changed), (100.0, 0))


class WorkerRefinementTests(unittest.TestCase):
    def test_the_refinement_is_recorded_on_the_job(self):
        job = {'id': 4}
        refined = worker.refine_cue_starts(job, '/media/x.mkv', cues(), 40.0, find_spans=lambda *args: SPANS)
        self.assertEqual(refined[0]['start'], 3.5)
        self.assertEqual(job['timing_refinement']['changed'], 8)
        self.assertEqual(job['timing_refinement']['of'], 12)

    def test_a_failure_never_fails_the_job_and_keeps_the_times(self):
        job = {'id': 4}

        def broken(*args):
            raise OSError('ffmpeg')
        self.assertEqual(worker.refine_cue_starts(job, '/media/x.mkv', cues(), 40.0, find_spans=broken), cues())
        self.assertIn('ffmpeg', job['timing_refinement']['error'])

    def test_a_cancelled_job_stays_cancelled(self):
        def cancelled(*args):
            raise worker.JobCancelled('stopp')
        with self.assertRaises(worker.JobCancelled):
            worker.refine_cue_starts({'id': 4}, '/media/x.mkv', cues(), 40.0, find_spans=cancelled)

    def test_the_spans_are_found_in_ten_minute_pieces_with_their_own_time_offset(self):
        pieces = []

        class Done:
            stdout = (np.zeros(16000, dtype=np.int16)).tobytes()

        def fake_run(command, **kwargs):
            pieces.append(float(command[command.index('-ss') + 1]))
            return Done()
        with patch.object(worker.subprocess, 'run', side_effect=fake_run), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'get_speech_timestamps', side_effect=lambda audio, options: [{'start': 16000 * 3, 'end': 16000 * 5}]):
            spans = worker.tight_speech_spans({'id': 4}, '/media/x.mkv', 1500)
        self.assertEqual(pieces, [0.0, 600.0, 1200.0])
        self.assertEqual(spans, [(3.0, 5.0), (603.0, 605.0), (1203.0, 1205.0)])

    def test_the_voice_detection_has_no_padding_and_the_step_runs_before_the_trace_is_saved(self):
        source = open(worker.__file__, encoding='utf-8').read()
        self.assertIn('speech_pad_ms=0)):\n            spans.append((start + item', source)
        self.assertLess(source.index('merged = refine_cue_starts(job, media, merged, total)'), source.index('trace_payload.update(timing_refinement='))
        self.assertIn('starttider flyttades fram till talets början', source)


if __name__ == '__main__':
    unittest.main()
