import sys
import unittest
import contextlib
import io
from unittest.mock import patch

sys.path.insert(0, '/ai')
from source_audio_probe import (source_audio_map, source_window, listening_windows,
                                contrast_candidates, calibration_decision)
import source_audio_probe


class SourceAudioProbeTests(unittest.TestCase):
    def test_frame_export_rejects_changed_media_invalid_times_and_out_of_range_without_writes(self):
        from pathlib import Path
        saved = {'job_id': 336, 'media_path': '/extra/original.mp4'}
        for media, times in [('/extra/other.mp4', [1.]), ('/extra/original.mp4', []),
                             ('/extra/original.mp4', [1.]*2), ('/extra/original.mp4', [-1.]),
                             ('/extra/original.mp4', [float('nan')]),
                             ('/extra/original.mp4', list(range(13)))]:
            with self.subTest(media=media, times=times), patch.object(source_audio_probe.subprocess, 'check_output') as probe, \
                    self.assertRaises(ValueError):
                source_audio_probe.export_witness_frames(saved, Path(media), times)
            probe.assert_not_called()
        info = b'{"streams":[{"index":0,"codec_type":"video"}],"format":{"duration":"5"}}'
        with patch.object(source_audio_probe.subprocess, 'check_output', return_value=info), \
                patch.object(Path, 'mkdir') as mkdir, self.assertRaises(ValueError):
            source_audio_probe.export_witness_frames(saved, Path(saved['media_path']), [5.])
        mkdir.assert_not_called()

    def test_frame_cli_returns_before_worker_import_and_rejects_analysis_modes(self):
        from pathlib import Path
        import builtins
        original_import = builtins.__import__
        def no_worker(name, *args, **kwargs):
            self.assertNotEqual(name, 'worker')
            return original_import(name, *args, **kwargs)
        base = ['probe', 'report.json', '--media', '/extra/original.mp4', '--frame-times', '1', '2']
        with patch.object(sys, 'argv', base), patch.object(Path, 'read_text', return_value='{"job_id":336}'), \
                patch.object(source_audio_probe, 'export_witness_frames') as export, \
                patch.object(builtins, '__import__', side_effect=no_worker):
            source_audio_probe.main()
        export.assert_called_once_with({'job_id': 336}, Path('/extra/original.mp4'), [1., 2.])
        for extra in (['--pipeline-source'], ['--export-only'], ['--language', 'de'], ['--only-rows', '2']):
            with self.subTest(extra=extra), patch.object(sys, 'argv', base + extra), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                source_audio_probe.main()

    def test_nbest_cannot_enter_write_modes_or_silently_guess_language_or_replace_the_saved_track(self):
        base = ['probe', 'missing-report.json', '--media', 'unused.mp4', '--nbest', '10',
                '--models', 'large-v3']
        for extra in ([], ['--language', 'ja', '--pipeline-source'],
                      ['--language', 'ja', '--export-only'],
                      ['--language', 'ja', '--score-candidates-row', '4'],
                      ['--language', 'ja', '--audio-track', '1']):
            with self.subTest(extra=extra), patch.object(sys, 'argv', base + extra), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                source_audio_probe.main()
            self.assertEqual(stopped.exception.code, 2)

    def test_reference_controls_change_text_in_space_and_non_space_languages(self):
        for reference, donor in [
                ('The driver cannot predict traffic flow today.', 'Another study follows commuters during winter.'),
                ('東京の交通量を調べます。', '大阪の気温を測ります。'),
                ('வணக்கம்தமிழ்மொழி', 'காப்பிதமிழ்மொழி')]:
            with self.subTest(reference=reference):
                candidates = contrast_candidates(reference, donor)
                self.assertEqual(candidates[0], ('reference', reference))
                self.assertEqual(candidates[-1], ('unrelated_reference', donor))
                self.assertEqual(len(set(text for _, text in candidates)), 5)
                for label, text in candidates[1:]:
                    self.assertNotEqual(text, reference)
                    self.assertTrue(text.strip())

    def test_calibration_requires_two_distinct_models_and_rejects_nonfinite_or_close_scores(self):
        def item(name, gold=4., wrong=2.):
            return {'model': name, 'candidates': [
                {'synthetic_control': 'reference', 'mean_audio_gain': gold},
                {'synthetic_control': 'deletion', 'mean_audio_gain': wrong}]}
        self.assertEqual(calibration_decision([item('large-v3'), item('large-v2')]), 'reference')
        for models in ([item('large-v3')], [item('large-v3'), item('large-v3')],
                       [item('large-v3'), item('large-v2', 3., 2.9)],
                       [item('large-v3'), item('large-v2', float('nan'))],
                       [item('large-v3'), item('large-v2', 4., 5.)]):
            with self.subTest(models=models):
                self.assertIsNone(calibration_decision(models))

    def test_reference_missing_audit_does_not_leak_the_reference_label_into_the_decision(self):
        models = [{'model': name, 'candidates': [
            {'synthetic_control': 'reference', 'mean_audio_gain': 5.},
            {'synthetic_control': 'deletion', 'mean_audio_gain': 4.},
            {'synthetic_control': 'substitution', 'mean_audio_gain': 2.}]
            } for name in ('large-v3', 'large-v2')]
        self.assertEqual(calibration_decision(models), 'reference')
        # This must be counted as a FALSE acceptance in calibration, not hidden
        # by pretending that a high-scoring candidate must be the reference.
        self.assertEqual(calibration_decision(models, without_reference=True), 'deletion')

    def test_listening_context_covers_selected_words_without_display_padding_or_negative_start(self):
        saved = {'rows': [
            {'id': 0, 'start': .5, 'end': 2., 'source_unclear': True},
            {'id': 1, 'start': 5., 'end': 8., 'reading_time_source_end': 6.},
            {'id': 2, 'start': 18., 'end': 20., 'source_unclear': True}]}
        result = listening_windows(saved, [1], duration=22.)
        self.assertEqual([(r['start'], r['end']) for r in result], [(0., 9.), (15., 22.)])
        self.assertEqual([c['id'] for c in result[0]['cues']], [0, 1])
        self.assertEqual(result[0]['cues'][1]['end'], 6.)

    def test_listening_export_rejects_unknown_rows_invalid_context_and_out_of_media_speech(self):
        saved = {'rows': [{'id': 3, 'start': 8., 'end': 10., 'source_unclear': True}]}
        for arguments in ({'extra_ids': [99]}, {'context': float('nan')}, {'context': 20},
                          {'duration': 9.}, {'duration': float('inf')}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                listening_windows(saved, **arguments)

    def test_saved_ffprobe_stream_wins_over_first_audio_stream(self):
        self.assertEqual(source_audio_map({'audio_track': {'index': 1}}), '0:1')
        self.assertEqual(source_audio_map({'audio_track': {'index': 0}}), '0:0')
        self.assertEqual(source_audio_map({'audio_track': {'index': 1}}, 2), '0:2')

    def test_unknown_or_invalid_stream_is_not_silently_guessed(self):
        for report in ({}, {'audio_track': None}, {'audio_track': {'index': -1}},
                       {'audio_track': {'index': True}}, {'audio_track': {'index': '1'}}):
            with self.subTest(report=report), self.assertRaises(ValueError):
                source_audio_map(report)

    def test_saved_rows_get_context_without_cutting_start_below_zero(self):
        self.assertEqual(source_window([{'start': .5, 'end': 4}]), (0, 6))
        self.assertEqual(source_window([{'start': 8, 'end': 20}]), (6, 22))

    def test_explicit_window_does_not_depend_on_media_name(self):
        self.assertEqual(source_window([{'start': 8, 'end': 20}], 0, 25), (0, 25))

    def test_unbounded_invalid_and_nonfinite_windows_are_rejected(self):
        for start, end in [(-1, 3), (1, 1), (4, 3), (0, 61), (float('nan'), 3), (0, float('inf'))]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                source_window([{'start': 8, 'end': 20}], start, end)

    def test_empty_source_report_is_rejected(self):
        with self.assertRaises(ValueError):
            source_window([])


if __name__ == '__main__':
    unittest.main()
