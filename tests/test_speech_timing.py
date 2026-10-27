import sys
import unittest
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/app')
from speech_timing import pad_reading_time, reject_long_bridge, speech_span


class SpeechTimingTests(unittest.TestCase):
    def test_real_showtime_row_gets_display_time_in_the_following_pause(self):
        rows = [{'id': 32, 'start': 103.84, 'end': 104.34, 'source': "It's showtime.", 'sv': 'Nu börjar föreställningen.'},
                {'id': 33, 'start': 106.06, 'end': 106.46, 'source': 'Just blow something up!', 'sv': 'Bara spräng något!'}]
        before = [dict(row) for row in rows]
        spans = [{'start': 103.9, 'end': 104.5}, {'start': 106.18, 'end': 106.6}]
        fixed, changes = pad_reading_time(rows, spans, 175.334)
        self.assertEqual(rows, before)
        self.assertEqual([r['id'] for r in fixed], [32, 33])
        self.assertEqual(fixed[0]['end'], 105.14)
        self.assertEqual(fixed[1]['end'], 106.96)
        self.assertEqual([r['start'] for r in fixed], [103.84, 106.06])
        self.assertTrue(all(r['source'] == o['source'] and r['sv'] == o['sv'] for r, o in zip(fixed, before)))
        self.assertEqual([r['id'] for r in changes], [32, 33])

    def test_new_unsubtitled_voice_blocks_the_padding(self):
        rows = [{'id': 1, 'start': 1., 'end': 1.5, 'sv': 'Det här är en lång svensk undertextrad.'}]
        fixed, _ = pad_reading_time(rows, [{'start': 1., 'end': 1.4}, {'start': 1.8, 'end': 2.2}], 10.)
        self.assertEqual(fixed[0]['end'], 1.72)

    def test_neighbour_media_end_and_maximum_hold_are_hard_bounds(self):
        row = {'id': 1, 'start': 1., 'end': 1.5, 'sv': 'x' * 100}
        spans = [{'start': 1., 'end': 1.5}]
        fixed, _ = pad_reading_time([row, {'id': 2, 'start': 1.7, 'end': 2., 'sv': 'Ja.'}], spans, 10.)
        self.assertEqual(fixed[0]['end'], 1.62)
        self.assertEqual(pad_reading_time([row], spans, 1.8)[0][0]['end'], 1.8)
        fixed, _ = pad_reading_time([row], spans, 10.)
        self.assertEqual(fixed[0]['end'], 2.5)
        again, _ = pad_reading_time(fixed, spans, 10.)
        self.assertEqual(again, fixed, 'repeating a review must not add another second')

    def test_saved_quality_source_and_actual_srt_use_the_same_display_end(self):
        import worker
        row = {'id': 32, 'start': 103.84, 'end': 104.34, 'source': "It's showtime.", 'sv': 'Nu börjar föreställningen.'}
        job = {'id': 5, 'reading_context': {'duration': 175.334, 'spans': [{'start': 103.9, 'end': 104.5}]}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / 'test.db'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
            with patch.object(worker, 'QUALITY_REPORTS', root / 'quality'), patch.object(worker, 'SOURCE_TRANSCRIPTS', root / 'source'), \
                    patch.object(worker, 'DATABASE', database), patch.object(worker, 'update_job'), \
                    patch.object(worker, 'assess_translation_quality'), patch.object(worker, 'check_cancelled'):
                rows = [row]
                worker.review_and_save_translation(job, rows)
                quality = json.loads((root / 'quality' / 'job-5.json').read_text())
                source = worker.save_source_transcript(job, root / 'video.mp4', rows, 'CPU', 'Whisper')
                saved_source = json.loads(source.read_text())
                output = worker.write_srt(job, root / 'video.mp4', rows)
            self.assertEqual(quality['rows'][0]['end'], 105.14)
            self.assertEqual(saved_source['rows'][0]['end'], 105.14)
            self.assertEqual(saved_source['rows'][0]['reading_time_source_end'], 104.34)
            self.assertIn('00:01:43,840 --> 00:01:45,140', output.read_text())
            self.assertEqual(quality['reading_time'], job['reading_time']['sv'])

    def test_padding_does_not_hide_a_failed_quality_verdict(self):
        import worker
        rows = [{'id': 32, 'start': 103.84, 'end': 104.34, 'source': "It's showtime.", 'sv': 'En felaktig mening.'}]
        job = {'id': 5, 'reading_context': {'duration': 175.334, 'spans': [{'start': 103.9, 'end': 104.5}]}}
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'assess_translation_quality', side_effect=worker.SourceQualityError('fel')):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation(job, rows)
            self.assertEqual(json.loads((Path(folder) / 'job-5.json').read_text())['status'], 'unusable')
        self.assertEqual(rows[0]['end'], 104.34)

    def test_no_padding_without_detector_evidence_or_for_an_already_readable_row(self):
        row = {'id': 1, 'start': 1., 'end': 1.5, 'sv': 'x' * 100}
        self.assertEqual(pad_reading_time([row], [], 10.), ([row], []))
        self.assertEqual(pad_reading_time([row], [{'start': 5., 'end': 6.}], 10.)[0][0]['end'], 2.5,
                         'an accepted short cue remains valid when VAD misses its own voice')
        brief = {**row, 'sv': 'Ja.'}
        self.assertEqual(pad_reading_time([brief], [{'start': 1., 'end': 1.4}], 10.), ([brief], []))

    def test_overlapping_detector_windows_are_merged_and_seven_second_cap_is_kept(self):
        row = {'id': 1, 'start': 1., 'end': 7.8, 'sv': 'x' * 200}
        fixed, _ = pad_reading_time([row], [{'start': 1., 'end': 7.9}, {'start': 7., 'end': 8.1}], 10.)
        self.assertEqual(fixed[0]['end'], 8.)

    def test_invalid_timing_evidence_is_not_used(self):
        row = {'id': 1, 'start': 1., 'end': 1.5, 'sv': 'x' * 100}
        for spans, duration in [([{'start': float('nan'), 'end': 2.}], 10.),
                                ([{'start': 1., 'end': 20.}], 10.),
                                ([{'start': 1., 'end': 2.}], float('inf'))]:
            with self.assertRaises(ValueError):
                pad_reading_time([row], spans, duration)

    def test_english_padding_uses_english_text_and_does_not_shorten_rows(self):
        row = {'id': 1, 'start': 1., 'end': 1.5, 'sv': 'Ja.', 'en': 'x' * 40}
        fixed, _ = pad_reading_time([row], [{'start': 1., 'end': 1.5}], 10., target_key='en')
        self.assertEqual(fixed[0]['end'], 2.5)

    def test_tight_span_uses_confident_words(self):
        words = [
            {'start': 159.5, 'end': 159.9, 'probability': .9},
            {'start': 160.0, 'end': 160.4, 'probability': .8},
        ]
        self.assertEqual(speech_span(words), (159.42, 160.48))

    def test_untrusted_words_do_not_create_a_cue(self):
        self.assertIsNone(speech_span([
            {'start': 150, 'end': 151, 'probability': .1},
        ]))

    def test_long_asr_bridge_is_rejected(self):
        self.assertTrue(reject_long_bridge({'start': 134.1, 'end': 175.0, 'text': 'hallucination'}))
        self.assertFalse(reject_long_bridge({'start': 134.1, 'end': 136.0, 'text': 'speech'}))


if __name__ == '__main__':
    unittest.main()
