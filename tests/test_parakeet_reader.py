import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path[:0] = ['/app', '/ai']
import asr_windows
import parakeet_reader
import worker
from asr_windows import replace_one_word_outvoted


def r(start, end, text, confidence=.8):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


def install_model(models_dir, files=parakeet_reader.REQUIRED_FILES, marker=True):
    folder = parakeet_reader.model_folder(models_dir)
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b'x')
    if marker:
        (folder / '.subsynq-revision').write_text('rev\n')
    return folder


class FakeReader:
    def __init__(self, texts=None, error=None):
        self.texts, self.error, self.released, self.clips, self.kept = list(texts or []), error, 0, 0, []

    def read(self, clip, keep=None):
        self.clips += 1
        self.kept.append(keep)
        if self.error:
            raise self.error
        return self.texts.pop(0)

    def release(self):
        self.released += 1


class ModelFilesTests(unittest.TestCase):
    def test_the_model_counts_as_present_only_with_every_file_and_the_revision_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(parakeet_reader.model_present(folder))
        with tempfile.TemporaryDirectory() as folder:
            install_model(folder, marker=False)
            self.assertFalse(parakeet_reader.model_present(folder))
        with tempfile.TemporaryDirectory() as folder:
            install_model(folder, files=parakeet_reader.REQUIRED_FILES[:-1])
            self.assertFalse(parakeet_reader.model_present(folder))
        with tempfile.TemporaryDirectory() as folder:
            install_model(folder)
            self.assertTrue(parakeet_reader.model_present(folder))

    def test_only_a_measured_language_is_read_and_it_can_be_switched_off(self):
        for language in ('en', 'de', 'DE'):
            self.assertTrue(parakeet_reader.usable_language({'language': language})[0])
        for choice in ({'language': 'sv'}, {'language': 'da'}, {'language': 'no'}, {'language': None}, {}, None):
            self.assertFalse(parakeet_reader.usable_language(choice)[0])
        self.assertEqual((parakeet_reader.padding({'language': 'en'}), parakeet_reader.padding({'language': 'de'}),
                          parakeet_reader.padding({'language': 'sv'}), parakeet_reader.padding(None)), (2.0, 2.0, 0.0, 0.0))
        self.assertTrue(parakeet_reader.enabled({}))
        for value in ('0', 'off', 'No'):
            self.assertFalse(parakeet_reader.enabled({'SUBSYNQ_PARAKEET': value}))

    def test_the_reader_loads_lazily_on_the_cpu_and_reports_failures_as_runtime_errors(self):
        loaded = MagicMock()
        loaded.recognize.return_value = MagicMock(text=' Hello there. ')
        fake_asr = MagicMock(load_model=MagicMock(return_value=loaded))
        fake_runtime = MagicMock()
        with patch.dict(sys.modules, {'onnx_asr': fake_asr, 'onnxruntime': fake_runtime}):
            reader = parakeet_reader.Reader('/models', threads=3)
            fake_asr.load_model.assert_not_called()
            self.assertEqual(reader.read('clip.wav'), 'Hello there.')
            reader.read('clip.wav')
            self.assertEqual(fake_asr.load_model.call_count, 1)
            self.assertEqual(fake_asr.load_model.call_args.kwargs['providers'], ['CPUExecutionProvider'])
            self.assertEqual(fake_runtime.SessionOptions.return_value.intra_op_num_threads, 3)
            reader.release()
            reader.read('clip.wav')
            self.assertEqual(fake_asr.load_model.call_count, 2)
        with patch.dict(sys.modules, {'onnx_asr': None}):
            with self.assertRaisesRegex(RuntimeError, 'laddas'):
                parakeet_reader.Reader('/models').read('clip.wav')
        loaded.recognize.side_effect = ValueError('trasigt klipp')
        with patch.dict(sys.modules, {'onnx_asr': fake_asr, 'onnxruntime': fake_runtime}):
            with self.assertRaisesRegex(RuntimeError, 'läsa klippet'):
                parakeet_reader.Reader('/models').read('clip.wav')


class TrimTests(unittest.TestCase):
    TOKENS = [' Und', ' dann', ' kam', ' er', '.', ' Wo', 'hin', '?']
    TIMES = [.2, .6, 1.0, 1.4, 1.5, 2.6, 3.0, 3.2]

    def test_only_the_words_that_begin_inside_the_window_are_kept(self):
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 2.0, 4.0), 'Wohin?')
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 0.0, 2.0), 'Und dann kam er.')
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 5.0, 6.0), '')

    def test_a_word_is_never_cut_in_two(self):
        # "Wohin" begins at 2.6 s, its second token sits at 3.0 s: a window that ends at 2.8 s keeps the whole word, one that starts at 2.8 s none of it.
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 2.0, 2.8), 'Wohin?')
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 2.8, 4.0), '')

    def test_a_word_a_tenth_after_the_end_still_counts_but_not_one_before_the_start(self):
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 1.0, 2.5), 'kam er. Wohin?')
        self.assertEqual(parakeet_reader.trim_to_window(self.TOKENS, self.TIMES, 1.05, 2.0), 'er.')

    def test_the_reader_asks_for_timestamps_only_when_it_cuts(self):
        plain, timed = MagicMock(), MagicMock()
        plain.recognize.return_value = MagicMock(text='Hallo Welt. Wie geht es?')
        timed.recognize.return_value = MagicMock(tokens=self.TOKENS, timestamps=self.TIMES)
        loaded = MagicMock(with_timestamps=MagicMock(return_value=timed), recognize=plain.recognize)
        fake_asr = MagicMock(load_model=MagicMock(return_value=loaded))
        with patch.dict(sys.modules, {'onnx_asr': fake_asr, 'onnxruntime': MagicMock()}):
            reader = parakeet_reader.Reader('/models')
            self.assertEqual(reader.read('clip.wav'), 'Hallo Welt. Wie geht es?')
            loaded.with_timestamps.assert_not_called()
            self.assertEqual(reader.read('clip.wav', (2.0, 4.0)), 'Wohin?')
            loaded.with_timestamps.assert_called_once()


class WorkerReadingTests(unittest.TestCase):
    WINDOWS = [{'start': 10.0, 'end': 20.0, 'kind': 'doubtful'}, {'start': 40.0, 'end': 48.0}]

    def read(self, job, windows=None, reader=None, present=True):
        with patch.object(worker, 'update_job'), patch.object(worker, 'check_cancelled'), patch.object(worker, 'audio_map', return_value='0:a:0'), \
                patch.object(worker.subprocess, 'run') as run, patch.object(parakeet_reader, 'model_present', return_value=present):
            result = worker.read_with_parakeet(job, 'film.mkv', self.WINDOWS if windows is None else windows, reader)
        return result, run

    def test_each_window_is_cut_and_read_and_the_model_is_released(self):
        reader, job = FakeReader(['Hello there.', '']), {'id': 1, 'audio_choice': {'language': 'en'}}
        readings, run = self.read(job, reader=reader)
        self.assertEqual(readings, [{'start': 10.0, 'end': 20.0, 'text': 'Hello there.', 'confidence': .5}])
        self.assertEqual((reader.clips, reader.released, run.call_count), (2, 1, 2))
        self.assertEqual(job['parakeet_reader'], {'state': 'used', 'reason': '', 'windows': 1, 'seconds': 18.0})

    def test_nothing_is_read_for_no_windows_another_language_a_missing_model_or_when_switched_off(self):
        english = {'id': 1, 'audio_choice': {'language': 'en'}}
        for job, kwargs, reason in ((dict(english), {'windows': []}, 'inga oense'),
                                    ({'id': 1, 'audio_choice': {'language': 'sv'}}, {}, 'inte godkänt'),
                                    (dict(english), {'present': False}, 'inte hämtad')):
            reader = FakeReader(['x'])
            readings, run = self.read(job, reader=None if 'present' in kwargs else reader, **kwargs)
            self.assertEqual((readings, run.call_count), ([], 0))
            self.assertIn(reason, job['parakeet_reader']['reason'])
            self.assertEqual(job['parakeet_reader']['state'], 'skipped')
        with patch.dict('os.environ', {'SUBSYNQ_PARAKEET': '0'}):
            job = dict(english)
            self.assertEqual(self.read(job, reader=FakeReader(['x']))[0], [])
            self.assertIn('avstängd', job['parakeet_reader']['reason'])

    def test_german_is_read_with_two_seconds_of_sound_around_each_window_and_cut_back(self):
        reader, job = FakeReader(['Wohin?', 'Zum Bahnhof.']), {'id': 1, 'audio_choice': {'language': 'de'}}
        readings, run = self.read(job, reader=reader)
        self.assertEqual([item['text'] for item in readings], ['Wohin?', 'Zum Bahnhof.'])
        self.assertEqual([item['start'] for item in readings], [10.0, 40.0])                      # the reading keeps the window's own times
        first = run.call_args_list[0].args[0]
        self.assertEqual((first[first.index('-ss') + 1], first[first.index('-t') + 1]), ('8.0', '14.0'))   # 8-22 s: the window 10-20 s and 2 s each side
        self.assertEqual(reader.kept, [(2.0, 12.0), (2.0, 10.0)])

    def test_english_gets_the_same_two_seconds(self):
        reader, job = FakeReader(['Hello.', 'Bye.']), {'id': 1, 'audio_choice': {'language': 'en'}}
        _, run = self.read(job, reader=reader)
        first = run.call_args_list[0].args[0]
        self.assertEqual((first[first.index('-ss') + 1], first[first.index('-t') + 1]), ('8.0', '14.0'))
        self.assertEqual(reader.kept, [(2.0, 12.0), (2.0, 10.0)])

    def test_a_language_without_padding_is_read_as_cut(self):
        reader, job = FakeReader(['Hello.']), {'id': 1, 'audio_choice': {'language': 'en'}}
        with patch.dict(parakeet_reader.PADDING, {'en': 0.0}):
            _, run = self.read(job, windows=[{'start': 10.0, 'end': 20.0}], reader=reader)
        first = run.call_args_list[0].args[0]
        self.assertEqual((first[first.index('-ss') + 1], first[first.index('-t') + 1], reader.kept), ('10.0', '10.0', [None]))

    def test_the_padding_never_starts_before_the_beginning_of_the_file(self):
        reader, job = FakeReader(['Hallo.']), {'id': 1, 'audio_choice': {'language': 'de'}}
        _, run = self.read(job, windows=[{'start': 0.5, 'end': 2.0}], reader=reader)
        first = run.call_args_list[0].args[0]
        self.assertEqual((first[first.index('-ss') + 1], reader.kept), ('0.0', [(0.5, 2.0)]))

    def test_a_failure_gives_no_vote_and_never_fails_the_job(self):
        reader, job = FakeReader(error=RuntimeError('Parakeet kunde inte laddas')), {'id': 1, 'audio_choice': {'language': 'en'}}
        readings, _ = self.read(job, reader=reader)
        self.assertEqual(readings, [])
        self.assertEqual(job['parakeet_reader']['state'], 'failed')
        self.assertEqual(reader.released, 1)

    def test_a_cancelled_job_stays_cancelled(self):
        reader, job = FakeReader(error=worker.JobCancelled('stoppad')), {'id': 1, 'audio_choice': {'language': 'en'}}
        with self.assertRaises(worker.JobCancelled):
            self.read(job, reader=reader)
        self.assertEqual(reader.released, 1)

    def test_the_audio_it_reads_is_capped(self):
        windows = [{'start': 100.0 * n, 'end': 100.0 * n + 100.0} for n in range(11)]
        reader, job = FakeReader(list('abcdefghijk')), {'id': 1, 'audio_choice': {'language': 'en'}}
        readings, _ = self.read(job, windows=windows, reader=reader)
        self.assertEqual((len(readings), job['parakeet_reader']['seconds']), (9, 900.0))
        self.assertIn('tak', job['parakeet_reader']['reason'])


# The saved case of the one-word vote ("Take off that fucking hat you"): the long pass wrote the extra word.
ROW = r(2069.544, 2071.18, 'Take off that fucking hat you', .608)
OWN = {'precision': [r(2068.7, 2071.18, 'Take off that fucking hat you', .608)]}
HEARD = 'Take off that fucking hat.'


class ParakeetVoteTests(unittest.TestCase):
    def test_parakeet_is_its_own_independent_family(self):
        self.assertEqual(asr_windows._family('parakeet'), 'parakeet')
        self.assertIn('parakeet', asr_windows._INDEPENDENT_FAMILIES)

    def test_it_completes_the_three_families_the_vote_needs(self):
        others = dict(OWN, rescue=[r(2068.35, 2070.45, HEARD, .84)], qwen=[r(2068.4, 2071.48, HEARD, .5)])
        self.assertEqual(replace_one_word_outvoted([ROW], others)[1], [])        # Whisper v3 and Qwen: two families
        rows, changes = replace_one_word_outvoted([ROW], dict(others, parakeet=[r(2068.4, 2070.9, HEARD, .5)]))
        self.assertEqual(rows[0]['text'], HEARD)
        self.assertEqual(changes[0]['families'], ['parakeet', 'qwen', 'whisper-v3'])

    def test_it_is_not_enough_with_a_single_other_family(self):
        readings = dict(OWN, parakeet=[r(2068.4, 2070.9, HEARD, .5)], qwen=[r(2068.4, 2071.48, HEARD, .5)])
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])

    def test_hearing_the_rows_own_words_keeps_them(self):
        readings = dict(OWN, rescue=[r(2068.35, 2070.45, HEARD, .84)], v2=[r(2068.4, 2070.58, HEARD, .52)],
                        qwen=[r(2068.4, 2071.48, HEARD, .5)], parakeet=[r(2068.4, 2071.0, 'Take off that fucking hat you', .5)])
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])


if __name__ == '__main__':
    unittest.main()
