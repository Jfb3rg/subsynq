import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
import worker


def track(index, language='und', **flags):
    return {'index': index, 'language': language, 'title': '', 'codec': 'aac', 'channels': 2, **flags}


def speech_model(languages):
    """A reading model that answers with one language per call and some real-looking text."""
    answers = iter(languages)
    model = Mock()
    model.transcribe.side_effect = lambda *a, **k: (
        [SimpleNamespace(text='This is clearly some dialogue')], SimpleNamespace(language=next(answers)))
    return model


class AudioMapTests(unittest.TestCase):
    def test_default_is_the_first_audio_track_and_a_choice_maps_that_exact_stream(self):
        self.assertEqual(worker.audio_map({'id': 1}), '0:a:0')
        self.assertEqual(worker.audio_map({'id': 1, 'audio_stream': 3}), '0:3')
        self.assertEqual(worker.audio_map({'id': 1, 'audio_stream': 0}), '0:0')

    def test_no_ffmpeg_call_in_the_worker_still_hardcodes_the_first_audio_track(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count("'0:a:0'"), 1, 'only audio_map may name the first track')


class ReadTrackLanguageTests(unittest.TestCase):
    def read(self, model, *, windows=3, speech=3.0, run=None):
        spans = [{'start': 0, 'end': int(speech * 16000)}] if speech else []
        with patch.object(worker, 'probe_windows',
                          return_value=[{'start': 10.0 * i, 'end': 10.0 * i + 5} for i in range(windows)]), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker.subprocess, 'run', run or Mock()) as ffmpeg, \
                patch.object(worker, 'decode_audio'), \
                patch.object(worker, 'get_speech_timestamps', return_value=spans):
            result = worker.read_track_language({'id': 4}, Path('film.mkv'), model, 60.0, '/tmp', 1)
        return result, ffmpeg

    def test_reads_the_requested_stream_and_stops_when_two_windows_agree(self):
        model = speech_model(['en', 'en', 'de'])
        (language, readings), ffmpeg = self.read(model)
        self.assertEqual(language, 'en')
        self.assertEqual(len(readings), 2)
        self.assertEqual(model.transcribe.call_count, 2)
        command = ffmpeg.call_args_list[0].args[0]
        self.assertEqual(command[command.index('-map') + 1], '0:1')

    def test_disagreeing_windows_use_all_three_and_a_majority_wins(self):
        (language, readings), _ = self.read(speech_model(['de', 'en', 'de']))
        self.assertEqual((language, len(readings)), ('de', 3))

    def test_silence_is_not_listened_to(self):
        model = speech_model([])
        (language, readings), _ = self.read(model, speech=0)
        self.assertIsNone(language)
        self.assertEqual(model.transcribe.call_count, 0)

    def test_a_track_ffmpeg_cannot_decode_reads_as_no_language(self):
        broken = Mock(side_effect=subprocess.CalledProcessError(1, 'ffmpeg'))
        (language, readings), _ = self.read(speech_model([]), run=broken)
        self.assertEqual((language, readings), (None, []))


class SelectAudioTrackTests(unittest.TestCase):
    def select(self, tracks, job, readings=None):
        listened = []

        def read(job_, media, model, total, folder, stream):
            listened.append(stream)
            return (readings or {}).get(stream), [{'start': 0.0}]
        with patch.object(worker, 'probe_media', return_value={'status': 'ok', 'audio': tracks}), \
                patch.object(worker, 'read_track_language', side_effect=read), \
                patch.object(worker, 'update_job'):
            choice = worker.select_audio_track(job, Path('film.mkv'), Mock(), 60.0, '/tmp')
        return choice, listened

    def test_man_of_war_picks_the_english_original_over_the_german_dub(self):
        job = {'id': 48}
        choice, listened = self.select([track(0), track(1)], job, {0: 'de', 1: 'en'})
        self.assertEqual((choice['index'], choice['method']), (1, 'audio'))
        self.assertEqual(job['audio_stream'], 1)
        self.assertEqual(worker.audio_map(job), '0:1')
        self.assertEqual(listened, [0, 1])
        self.assertIn('readings', job['audio_choice'])

    def test_a_manual_choice_wins_without_listening(self):
        job = {'id': 48, 'audio_track': 0}
        choice, listened = self.select([track(0), track(1, 'eng')], job, {1: 'en'})
        self.assertEqual((choice['index'], choice['method'], listened), (0, 'manual', []))
        self.assertEqual(worker.audio_map(job), '0:0')

    def test_a_single_track_file_is_not_listened_to_and_keeps_its_track(self):
        job = {'id': 5}
        choice, listened = self.select([track(1)], job)
        self.assertEqual((choice['index'], choice['method'], listened), (1, 'only', []))

    def test_without_a_usable_probe_the_first_track_is_kept(self):
        job = {'id': 5}
        with patch.object(worker, 'probe_media', return_value={'status': 'error', 'message': 'x'}):
            self.assertIsNone(worker.select_audio_track(job, Path('film.mkv'), Mock(), 60.0, '/tmp'))
        self.assertEqual(worker.audio_map(job), '0:a:0')

    def test_a_job_chooses_only_once(self):
        job = {'id': 48}
        first, _ = self.select([track(0), track(1)], job, {0: 'de', 1: 'en'})
        second, listened = self.select([track(0), track(1)], job, {0: 'de', 1: 'en'})
        self.assertIs(first, second)
        self.assertEqual(listened, [])


class AudioTrackLabelTests(unittest.TestCase):
    def test_labels_say_which_track_and_why(self):
        label = worker.audio_track_label({'index': 1, 'language': 'en', 'method': 'audio',
                                           'stale_manual': False, 'considered': []})
        self.assertEqual(label, 'ljudspår 1 (engelska, hörs som engelska i ljudet)')
        label = worker.audio_track_label({'index': 0, 'language': 'de', 'method': 'first',
                                           'stale_manual': False, 'considered': []})
        self.assertEqual(label, 'ljudspår 0 (tyska, ingen engelska hittades, första spåret)')
        label = worker.audio_track_label({'index': 2, 'language': None, 'method': 'manual',
                                           'stale_manual': True, 'considered': []})
        self.assertIn('det valda spåret finns inte längre', label)
        self.assertIn('ljudspår 2 (valt av dig)', label)


if __name__ == '__main__':
    unittest.main()
