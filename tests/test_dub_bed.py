import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import dub_bed
import worker

RATE = dub_bed.RATE
LAG = 1161          # MAN OF WAR: the German dub lies 72.6 ms behind the English track


def tracks(seconds=20.0, dialogue=((2.0, 5.0), (8.0, 9.5)), shout=(12.0, 13.5), seed=1):
    """A shared bed of music/effects with a shout in it, English dialogue only in the chosen track, German dialogue only in the other."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    bed = rng.normal(0, .05, n + LAG)
    bed[int(shout[0] * RATE):int(shout[1] * RATE)] += rng.normal(0, .2, int((shout[1] - shout[0]) * RATE))
    english, german = bed[:n].copy(), np.zeros(n)
    german[:] = bed[:n]
    for start, end in dialogue:
        english[int(start * RATE):int(end * RATE)] += rng.normal(0, .08, int((end - start) * RATE))
        german[int(start * RATE):int(end * RATE)] += rng.normal(0, .08, int((end - start) * RATE))
    other = np.concatenate([np.zeros(LAG), german])[:n]        # the dub lies LAG samples behind
    return english, other


ROWS = [{'start': 2.0, 'end': 5.0, 'text': 'Mike, I am in trouble.'}, {'start': 8.0, 'end': 9.5, 'text': 'We need your help.'},
        {'start': 12.0, 'end': 13.5, 'text': 'Come on, you niggas!'}]


class LagTests(unittest.TestCase):
    def test_the_lag_of_a_dub_with_the_same_bed_is_found(self):
        english, german = tracks()
        lag, correlation = dub_bed.find_lag(english, german)
        self.assertEqual(lag, LAG)
        self.assertGreater(correlation, .6)
        self.assertEqual(dub_bed.shared_mix_lag([(english, german)] * 3), LAG)

    def test_unrelated_tracks_have_no_shared_mix(self):
        rng = np.random.default_rng(5)
        probes = [(rng.normal(0, .1, 5 * RATE), rng.normal(0, .1, 5 * RATE)) for _ in range(3)]
        self.assertIsNone(dub_bed.shared_mix_lag(probes))

    def test_one_matching_probe_is_not_enough(self):
        english, german = tracks()
        rng = np.random.default_rng(6)
        noise = [(rng.normal(0, .1, 5 * RATE), rng.normal(0, .1, 5 * RATE)) for _ in range(2)]
        self.assertIsNone(dub_bed.shared_mix_lag([(english, german)] + noise))


class BedRowTests(unittest.TestCase):
    def test_a_shout_both_tracks_hold_unchanged_is_removed_and_dialogue_stays(self):
        english, german = tracks()
        levels = dub_bed.block_levels(english, german, LAG)
        kept, removed, share = dub_bed.bed_rows(ROWS, levels)
        self.assertEqual([row['text'] for row in kept], ['Mike, I am in trouble.', 'We need your help.'])
        self.assertEqual([row['text'] for row in removed], ['Come on, you niggas!'])
        self.assertLess(removed[0]['median_db'], -15)
        self.assertAlmostEqual(share, 2 / 3)

    def test_the_measured_man_of_war_rows(self):
        # medians measured on the real file (dev job 152): dialogue 0 to -8 dB, the shout -27.4 dB, "the f**k did I do that?" -16.6 dB with
        # one English-only block ("What?" at 27.5 s): only rows with NO English-only block go
        def blocks(start, values):
            return [(start + .25 * index, value) for index, value in enumerate(values)]
        levels = (blocks(23.97, [-1.9] * 5) + blocks(26.37, [-24.0, -30.0, -27.0, -28.0]) + blocks(27.61, [-5.0, -16.0, -17.0, -16.6, -18.0])
                  + blocks(31.91, [-.1] * 24))
        rows = [{'start': 23.97, 'end': 25.36, 'text': "Mike, I'm in trouble."}, {'start': 26.37, 'end': 27.51, 'text': 'Come on, you niggas!'},
                {'start': 27.61, 'end': 29.03, 'text': 'the f**k did I do that?'}, {'start': 31.91, 'end': 37.95, 'text': 'His name is Stanislav Kanyev.'}]
        kept, removed, _ = dub_bed.bed_rows(rows, levels)
        self.assertEqual([row['text'] for row in removed], ['Come on, you niggas!'])
        self.assertEqual(len(kept), 3)

    def test_two_copies_of_the_same_language_never_lose_a_row(self):
        # Stuart Fails to Save the Universe: two identical tracks (correlation 1.00, no lag); every row "is in both"
        english, _ = tracks()
        levels = dub_bed.block_levels(english, english.copy(), 0)
        kept, removed, share = dub_bed.bed_rows(ROWS, levels)
        self.assertEqual((len(kept), removed, share), (3, [], 0.0))

    def test_a_row_shorter_than_half_a_second_is_not_judged(self):
        kept, removed, _ = dub_bed.bed_rows([{'start': 1.0, 'end': 1.3, 'text': 'Hey!'}] + ROWS[:2],
                                            [(1.0, -30.0)] + [(2.0 + .25 * i, -1.0) for i in range(12)] + [(8.0 + .25 * i, -1.0) for i in range(6)])
        self.assertEqual((len(kept), removed), (3, []))


class OverfullRowTests(unittest.TestCase):
    """MAN OF WAR (dev job 170): "the f**k did I do that?" (27.61-29.03 s, short pass only, confidence 0.23) lies in the shared bed except one
    quarter second of English-only sound at 27.75 s; Whisper large-v2 heard "Run!" at 27.46-27.82 s and the user hears "Run"."""
    LEVELS = [(27.5, -16.5), (27.75, -4.8), (28.0, -18.0), (28.25, -17.0), (28.5, -16.6), (28.75, -16.0)]
    ROW = {'start': 27.61, 'end': 29.03, 'text': 'the f**k did I do that?', 'confidence': .23}
    READINGS = {'precision': [ROW], 'v2': [{'start': 26.88, 'end': 27.46, 'text': 'on, you niggas!', 'confidence': .54},
                                          {'start': 27.46, 'end': 27.82, 'text': 'Run!', 'confidence': .78}],
                'qwen': [{'start': 27.31, 'end': 29.33, 'text': 'One。', 'confidence': .5}]}

    def test_six_words_in_a_quarter_second_take_the_reading_that_fits(self):
        rows, changes = dub_bed.overfull_rows([self.ROW], self.LEVELS, self.READINGS)
        self.assertEqual([(row['start'], row['end'], row['text']) for row in rows], [(27.46, 27.82, 'Run!')])
        self.assertEqual((changes[0]['replaced_by'], changes[0]['own_seconds']), ('Run!', .25))

    def test_without_a_fitting_reading_the_row_stays(self):
        # APOCALYPSE Z (dev job 185): "Sí, aquí Core." was removed although every reader hears it
        rows, changes = dub_bed.overfull_rows([self.ROW], self.LEVELS, {'precision': [self.ROW]})
        self.assertEqual(rows, [self.ROW])
        self.assertTrue(changes[0]['kept'])

    def test_a_row_whose_words_fit_its_own_sound_or_that_is_dialogue_stays(self):
        short = {**self.ROW, 'text': 'What?'}
        self.assertEqual(dub_bed.overfull_rows([short], self.LEVELS, self.READINGS)[0], [short])
        dialogue = [(27.5 + .25 * index, -1.0) for index in range(6)]
        self.assertEqual(dub_bed.overfull_rows([self.ROW], dialogue, self.READINGS)[0], [self.ROW])


class WorkerTests(unittest.TestCase):
    def fake_decode(self, english, german):
        def decode(media, stream, start, seconds):
            source = english if stream == 1 else german
            return source[int(start * RATE):int((start + seconds) * RATE)].astype(np.float32)
        return decode

    def test_the_step_removes_the_bed_row_and_records_it(self):
        english, german = tracks(seconds=30.0)
        job = {'id': 7, 'audio_stream': 1}
        with patch.object(worker, 'check_cancelled'):
            kept = worker.drop_dub_bed_rows(job, '/m.mp4', ROWS, 30.0, decode=self.fake_decode(english, german), tracks=[{'index': 0}, {'index': 1}])
        self.assertEqual([row['text'] for row in kept], ['Mike, I am in trouble.', 'We need your help.'])
        self.assertEqual(job['dub_bed']['other_stream'], 0)
        self.assertAlmostEqual(job['dub_bed']['lag_seconds'], .0726, places=4)
        self.assertEqual([row['text'] for row in job['dub_bed']['removed']], ['Come on, you niggas!'])

    def test_one_track_switched_off_or_a_failure_changes_nothing(self):
        english, german = tracks(seconds=30.0)
        with patch.object(worker, 'check_cancelled'):
            self.assertEqual(worker.drop_dub_bed_rows({'id': 7, 'audio_stream': 1}, '/m', ROWS, 30.0, tracks=[{'index': 1}]), ROWS)
            self.assertEqual(worker.drop_dub_bed_rows({'id': 7}, '/m', ROWS, 30.0, tracks=[{'index': 0}, {'index': 1}]), ROWS)
            with patch.object(worker, 'DUB_BED_ENABLED', False):
                self.assertEqual(worker.drop_dub_bed_rows({'id': 7, 'audio_stream': 1}, '/m', ROWS, 30.0,
                                                          decode=self.fake_decode(english, german), tracks=[{'index': 0}, {'index': 1}]), ROWS)
            job = {'id': 7, 'audio_stream': 1}

            def broken(*args):
                raise OSError('ffmpeg')
            self.assertEqual(worker.drop_dub_bed_rows(job, '/m', ROWS, 30.0, decode=broken, tracks=[{'index': 0}, {'index': 1}]), ROWS)
            self.assertIn('ffmpeg', job['dub_bed']['error'])

    def test_a_cancelled_job_stays_cancelled(self):
        def cancelled(*args):
            raise worker.JobCancelled('stopp')
        with patch.object(worker, 'check_cancelled', side_effect=cancelled), self.assertRaises(worker.JobCancelled):
            worker.drop_dub_bed_rows({'id': 7, 'audio_stream': 1}, '/m', ROWS, 30.0, tracks=[{'index': 0}, {'index': 1}])

    def test_wiring(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('merged, repeated_starts = drop_unconfirmed_repeated_starts('), source.index('merged = drop_dub_bed_rows(job, media, merged, total, readings=consensus_readings)'))
        self.assertLess(source.index('merged = drop_dub_bed_rows(job, media, merged, total, readings=consensus_readings)'), source.index('merged, consensus_prefixes = restore_agreed_prefixes('))
        self.assertIn("dub_bed=job.get('dub_bed')", source)
        self.assertIn("os.environ.get('SUBSYNQ_DUB_BED', '1') != '0'", source)
        self.assertIn('finns oförändrad i det dubbade spåret', source)


if __name__ == '__main__':
    unittest.main()
