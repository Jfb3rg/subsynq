import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import model_manager
import swedish_reader
import worker

SWEDISH = [{'start': 0, 'speech_seconds': 6.0, 'language': 'sv', 'probability': .95},
           {'start': 30, 'speech_seconds': 5.0, 'language': 'sv', 'probability': .90},
           {'start': 60, 'speech_seconds': 4.0, 'language': 'en', 'probability': .55}]


class DecisionTests(unittest.TestCase):
    def test_two_of_three_windows_heard_as_swedish_is_swedish(self):
        use, reason = swedish_reader.decide(SWEDISH)
        self.assertTrue(use)
        self.assertEqual(reason, '2 av 3 ljudfönster hörs som svenska')

    def test_a_file_that_is_mostly_another_language_keeps_large_v3(self):
        english = [{**item, 'language': 'en'} for item in SWEDISH]
        self.assertEqual(swedish_reader.decide(english), (False, 'ljudet hörs inte som svenska'))
        german = [SWEDISH[0], {**SWEDISH[1], 'language': 'de'}, {**SWEDISH[2], 'language': 'de'}]
        self.assertFalse(swedish_reader.decide(german)[0])

    def test_the_real_language_readings_of_saved_jobs(self):
        # (language, probability, speech seconds) per window, from data/config/source-transcripts/job-N.json
        def windows(*items):
            return [{'language': language, 'probability': probability, 'speech_seconds': seconds} for language, probability, seconds in items]
        swedish = {'Nelly Rapp': windows(('sv', .92, 8), ('sv', .95, 15), ('sv', .96, 11)),
                   'Beck 32': windows(('sv', .78, 8), ('sv', .92, 15), ('no', .47, 15)),
                   'Jönssonligan': windows(('sv', .96, 13), ('sv', .98, 15), ('sv', .94, 10))}
        for name, readings in swedish.items():
            self.assertTrue(swedish_reader.decide(readings)[0], name)
        others = {'TRANSFERVINDUE (danska, job 127: KB-Whisper valdes av misstag)': windows(('da', .93, 12), ('sv', .73, 14), ('sv', .85, 5)),
                  'Dobbeltfejl (danska)': windows(('da', .99, 8), ('da', .94, 13), ('da', .98, 11)),
                  'Agnes mot verden (norska)': windows(('no', .94, 15), ('no', .95, 13), ('no', .94, 7)),
                  'Glemselens øy (norska)': windows(('no', .94, 15), ('nn', .61, 2), ('no', .94, 15))}
        for name, readings in others.items():
            self.assertFalse(swedish_reader.decide(readings)[0], name)

    def test_one_sure_danish_window_vetoes_swedish_but_an_unsure_one_does_not(self):
        base = [{'language': 'sv', 'probability': .95, 'speech_seconds': 6.0}, {'language': 'sv', 'probability': .93, 'speech_seconds': 5.0}]
        self.assertEqual(swedish_reader.decide(base + [{'language': 'da', 'probability': .9, 'speech_seconds': 5.0}]),
                         (False, 'ett ljudfönster hörs säkert som danska eller norska'))
        self.assertTrue(swedish_reader.decide(base + [{'language': 'no', 'probability': .6, 'speech_seconds': 5.0}])[0])

    def test_swedish_needs_three_quarters_certainty_in_a_window(self):
        self.assertFalse(swedish_reader.decide([{'language': 'sv', 'probability': .73, 'speech_seconds': 6.0},
                                                {'language': 'sv', 'probability': .70, 'speech_seconds': 5.0}])[0])

    def test_unsure_swedish_does_not_count(self):
        unsure = [{**item, 'probability': .4} for item in SWEDISH[:2]]
        self.assertFalse(swedish_reader.decide(unsure)[0])

    def test_too_little_speech_is_not_enough_to_decide(self):
        quiet = [{'start': 0, 'speech_seconds': .2}, {'start': 30, 'speech_seconds': 8.0, 'language': 'sv', 'probability': .99},
                 {'start': 60, 'speech_seconds': 0.0}]
        self.assertEqual(swedish_reader.decide(quiet), (False, 'för lite tal för att avgöra språket'))
        self.assertEqual(swedish_reader.decide([]), (False, 'för lite tal för att avgöra språket'))

    def test_two_windows_that_both_say_swedish_are_enough(self):
        self.assertTrue(swedish_reader.decide(SWEDISH[:2])[0])
        self.assertFalse(swedish_reader.decide([SWEDISH[0], {**SWEDISH[1], 'language': 'en'}])[0])


class SettingsTests(unittest.TestCase):
    def test_the_repository_can_be_switched_off_or_changed(self):
        self.assertEqual(swedish_reader.repository({}), 'KBLab/kb-whisper-large')
        for off in ('', '0', 'off', '  '):
            self.assertEqual(swedish_reader.repository({'SUBSYNQ_SWEDISH_WHISPER': off}), '')
        self.assertEqual(swedish_reader.repository({'SUBSYNQ_SWEDISH_WHISPER': 'KBLab/kb-whisper-medium'}), 'KBLab/kb-whisper-medium')

    def test_the_model_counts_as_present_only_with_its_weights(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = 'KBLab/kb-whisper-large'
            self.assertFalse(swedish_reader.model_present(folder, repo))
            snapshot = Path(folder) / 'models--KBLab--kb-whisper-large' / 'snapshots' / 'abc'
            snapshot.mkdir(parents=True)
            (snapshot / 'config.json').write_text('{}')
            self.assertFalse(swedish_reader.model_present(folder, repo), 'config without weights')
            (snapshot / 'model.bin').write_bytes(b'x')
            self.assertTrue(swedish_reader.model_present(folder, repo))

    def test_the_model_manager_lists_and_downloads_it_like_the_other_whisper_models(self):
        catalogue = model_manager.load_catalogue([Path('/app/models.json')])
        kb = [spec for spec in catalogue.specs if spec.name == 'KBLab/kb-whisper-large']
        self.assertEqual([(spec.kind, spec.id, spec.label) for spec in kb], [('faster-whisper', 'whisper-kb-swedish', 'KB-Whisper large (svenska)')])
        self.assertEqual(model_manager.FasterWhisperBackend('/models')._repo_id(kb[0]), 'KBLab/kb-whisper-large')


class FakeRemote:
    """A rented server's reader: it can be asked for another Whisper model by name."""
    def __init__(self):
        self.asked = []

    def with_model(self, name):
        self.asked.append(name)
        return 'KB-REMOTE'


class FakeKbReader:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def transcribe(self, path, **options):
        self.calls.append((Path(path).name, options))
        if self.error:
            raise self.error
        return [], SimpleNamespace(language='sv', language_probability=.9)


class FakeModels:
    def __init__(self, remote=None):
        self.remote, self.created = remote, []

    def create_whisper(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return 'KB-READER'


def choose(job=None, *, device='cpu', models=None, probe=None, present=True, repository='KBLab/kb-whisper-large', enabled=True):
    job = {'id': 1, **(job or {})}
    models = models or FakeModels()
    packs = {swedish_reader.PACK_ID} if enabled else set()
    with patch.object(swedish_reader, 'model_present', return_value=present), patch.object(worker, 'update_job'), \
            patch.object(model_manager, 'enabled_packs', return_value=packs):
        chosen = worker.prefer_swedish_reader(job, Path('/media/x.mkv'), 'LARGE-V3', models, device, 90.0, '/tmp',
                                              probe=probe or (lambda *args: SWEDISH), repository=repository)
    return chosen, job, models


class PreferSwedishReaderTests(unittest.TestCase):
    def test_swedish_speech_is_read_with_kb_whisper_on_the_local_cpu(self):
        chosen, job, models = choose()
        self.assertEqual(chosen, 'KB-READER')
        self.assertEqual(models.created, [(('KBLab/kb-whisper-large',), {'device': 'cpu', 'compute_type': 'int8', 'cpu_threads': 6,
                                                                      'download_root': '/models', 'local_files_only': True})])
        self.assertTrue(job['swedish_reader']['chosen'])
        self.assertEqual(job['swedish_reader']['reason'], '2 av 3 ljudfönster hörs som svenska')
        self.assertEqual(job['swedish_reader']['readings'], SWEDISH)

    def test_a_rented_server_reads_swedish_with_kb_whisper_and_needs_no_local_copy(self):
        for device in ('cloud-vast', 'cloud-cpu-vast', 'cloud-runpod'):
            remote = FakeRemote()
            chosen, job, models = choose(device=device, models=FakeModels(remote=remote), present=False)
            self.assertEqual(chosen, 'KB-REMOTE', device)
            self.assertEqual(remote.asked, ['KBLab/kb-whisper-large'])
            self.assertEqual(models.created, [], 'nothing is loaded on this machine')
            self.assertTrue(job['swedish_reader']['chosen'])

    def test_a_rented_server_keeps_large_v3_for_other_languages_a_switched_off_pack_and_the_retry(self):
        remote = FakeRemote()
        other = [{**item, 'language': 'hi'} for item in SWEDISH]
        self.assertEqual(choose(device='cloud-vast', models=FakeModels(remote=remote), probe=lambda *args: other)[0], 'LARGE-V3')
        self.assertEqual(choose(device='cloud-vast', models=FakeModels(remote=remote), enabled=False)[0], 'LARGE-V3')
        self.assertEqual(choose({'fallback_reason': 'moln föll'}, device='cloud-vast', models=FakeModels(remote=remote))[0], 'LARGE-V3')
        self.assertEqual(choose({'swedish_reader_retry': {'first_attempt': {}}}, device='cloud-vast', models=FakeModels(remote=remote))[0], 'LARGE-V3')
        self.assertEqual(remote.asked, [])

    def test_the_rented_servers_model_is_tried_once_before_the_job_depends_on_it(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'swedish-probe.wav').write_bytes(b'wav')
            reader = FakeKbReader()
            remote = FakeRemote()
            remote.with_model = lambda name: reader
            job = {'id': 1}
            with patch.object(swedish_reader, 'model_present', return_value=False), patch.object(worker, 'update_job'), \
                    patch.object(model_manager, 'enabled_packs', return_value={swedish_reader.PACK_ID}):
                chosen = worker.prefer_swedish_reader(job, Path('/media/x.mkv'), 'LARGE-V3', FakeModels(remote=remote), 'cloud-vast', 90.0,
                                                      folder, probe=lambda *args: SWEDISH, repository='KBLab/kb-whisper-large')
        self.assertIs(chosen, reader)
        self.assertEqual([name for name, _ in reader.calls], ['swedish-probe.wav'])
        self.assertEqual(reader.calls[0][1]['language'], 'sv')

    def test_a_server_that_fails_to_download_or_load_the_model_keeps_large_v3(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'swedish-probe.wav').write_bytes(b'wav')
            remote = FakeRemote()
            remote.with_model = lambda name: FakeKbReader(RuntimeError('moln-GPU misslyckades: ingen plats på disken'))
            job = {'id': 1}
            with patch.object(swedish_reader, 'model_present', return_value=False), patch.object(worker, 'update_job'), \
                    patch.object(model_manager, 'enabled_packs', return_value={swedish_reader.PACK_ID}):
                chosen = worker.prefer_swedish_reader(job, Path('/media/x.mkv'), 'LARGE-V3', FakeModels(remote=remote), 'cloud-vast', 90.0,
                                                      folder, probe=lambda *args: SWEDISH, repository='KBLab/kb-whisper-large')
        self.assertEqual(chosen, 'LARGE-V3')
        self.assertFalse(job['swedish_reader']['chosen'])
        self.assertIn('ingen plats på disken', job['swedish_reader']['reason'])

    def test_a_server_that_cannot_give_the_model_never_fails_the_job(self):
        remote = FakeRemote()
        remote.with_model = MagicMock(side_effect=RuntimeError('ingen kontakt'))
        chosen, job, _ = choose(device='cloud-vast', models=FakeModels(remote=remote))
        self.assertEqual(chosen, 'LARGE-V3')
        self.assertIn('misslyckades', job['swedish_reader']['reason'])

    def test_other_speech_keeps_large_v3_and_says_why(self):
        chosen, job, models = choose(probe=lambda *args: [{**item, 'language': 'ta'} for item in SWEDISH])
        self.assertEqual(chosen, 'LARGE-V3')
        self.assertEqual(models.created, [])
        self.assertFalse(job['swedish_reader']['chosen'])
        self.assertEqual(job['swedish_reader']['reason'], 'ljudet hörs inte som svenska')

    def test_other_devices_and_the_retry_after_a_rejected_source_keep_their_own_model(self):
        for device in ('amd-rx-9070-xt', 'native-cuda', 'cloud-vast', 'cloud-cpu-vast'):
            self.assertEqual(choose(device=device)[0], 'LARGE-V3', device)
        chosen, job, _ = choose({'fallback_reason': 'large-v3 underkändes'})
        self.assertEqual(chosen, 'LARGE-V3', 'the large-v2 retry must not be replaced again')
        self.assertEqual(job['swedish_reader']['reason'], 'bara lokal CPU eller hyrd server och inte som reservläsning')

    def test_a_model_that_is_not_downloaded_yet_keeps_large_v3_without_listening(self):
        probe = MagicMock(return_value=SWEDISH)
        chosen, job, _ = choose(present=False, probe=probe)
        self.assertEqual(chosen, 'LARGE-V3')
        probe.assert_not_called()
        self.assertEqual(job['swedish_reader']['reason'], 'modellen är inte hämtad')

    def test_the_language_pack_has_to_be_switched_on(self):
        probe = MagicMock(return_value=SWEDISH)
        chosen, job, models = choose(enabled=False, probe=probe)
        self.assertEqual(chosen, 'LARGE-V3')
        probe.assert_not_called()
        self.assertEqual(models.created, [])
        self.assertIn('avstängt', job['swedish_reader']['reason'])

    def test_it_can_be_switched_off(self):
        probe = MagicMock(return_value=SWEDISH)
        chosen, job, _ = choose(repository='', probe=probe)
        self.assertEqual(chosen, 'LARGE-V3')
        probe.assert_not_called()
        self.assertEqual(job['swedish_reader']['reason'], 'avstängd')

    def test_a_failure_never_fails_the_job(self):
        def broken(*args):
            raise RuntimeError('ffmpeg dog')
        chosen, job, _ = choose(probe=broken)
        self.assertEqual(chosen, 'LARGE-V3')
        self.assertIn('misslyckades', job['swedish_reader']['reason'])
        models = FakeModels()
        models.create_whisper = MagicMock(side_effect=OSError('för lite minne'))
        chosen, job, _ = choose(models=models)
        self.assertEqual(chosen, 'LARGE-V3')
        self.assertFalse(job['swedish_reader']['chosen'])

    def test_a_cancelled_job_is_not_swallowed(self):
        def cancelled(*args):
            raise worker.JobCancelled('stoppad')
        with self.assertRaises(worker.JobCancelled):
            choose(probe=cancelled)


class LanguageReadingTests(unittest.TestCase):
    def test_each_window_gets_whispers_language_guess_and_a_window_without_speech_none(self):
        spans = iter([[{'start': 0, 'end': 16000 * 4}], [], [{'start': 0, 'end': 16000 * 7}]])
        model = MagicMock()
        model.transcribe.return_value = (iter([]), SimpleNamespace(language='sv', language_probability=.9123))
        with patch.object(worker, 'probe_windows', return_value=[{'start': 0, 'end': 15}, {'start': 30, 'end': 45}, {'start': 60, 'end': 75}]), \
                patch.object(worker, 'check_cancelled'), patch.object(worker.subprocess, 'run'), \
                patch.object(worker, 'decode_audio', return_value=[0.0]), patch.object(worker, 'get_speech_timestamps', side_effect=lambda *a, **k: next(spans)):
            readings = worker.swedish_language_readings({'id': 1}, Path('/media/x.mkv'), model, 90.0, '/tmp')
        self.assertEqual(readings, [{'start': 0, 'speech_seconds': 4.0, 'language': 'sv', 'probability': .912},
                                    {'start': 30, 'speech_seconds': 0.0},
                                    {'start': 60, 'speech_seconds': 7.0, 'language': 'sv', 'probability': .912}])
        self.assertEqual(model.transcribe.call_count, 2)
        self.assertIsNone(model.transcribe.call_args.kwargs['language'])

    def test_a_server_that_gives_no_certainty_never_counts_the_window_as_swedish(self):
        model = MagicMock()
        model.transcribe.return_value = (iter([]), SimpleNamespace(language='sv', language_probability=None))
        with patch.object(worker, 'probe_windows', return_value=[{'start': 0, 'end': 15}]), patch.object(worker, 'check_cancelled'), \
                patch.object(worker.subprocess, 'run'), patch.object(worker, 'decode_audio', return_value=[0.0]), \
                patch.object(worker, 'get_speech_timestamps', return_value=[{'start': 0, 'end': 16000 * 5}]):
            readings = worker.swedish_language_readings({'id': 1}, Path('/media/x.mkv'), model, 90.0, '/tmp')
        self.assertEqual(readings[0]['probability'], 0.0)
        self.assertFalse(swedish_reader.decide(readings * 3)[0])

    def test_a_clip_that_cannot_be_cut_ends_the_listening(self):
        with patch.object(worker, 'probe_windows', return_value=[{'start': 0, 'end': 15}]), patch.object(worker, 'check_cancelled'), \
                patch.object(worker.subprocess, 'run', side_effect=worker.subprocess.TimeoutExpired('ffmpeg', 90)):
            self.assertEqual(worker.swedish_language_readings({'id': 1}, Path('/media/x.mkv'), MagicMock(), 90.0, '/tmp'), [])


KB_JOB = {'swedish_reader': {'repository': 'KBLab/kb-whisper-large', 'chosen': True, 'reason': '3 av 3 ljudfönster hörs som svenska'},
          'forced_reading_choice': {'x': 1}}


class SafetyNetTests(unittest.TestCase):
    def run_net(self, job, outcomes):
        job = {'id': 1, **job}
        calls = []

        def fake_work(*args, **kwargs):
            calls.append(args)
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        with patch.object(worker, 'work', fake_work), patch.object(worker, 'release_language_model_before_asr') as release, \
                patch.object(worker, 'update_job') as update:
            try:
                result = worker.work_with_swedish_safety_net(job, 'MODEL', 'cpu', {'h': 1})
            except Exception as error:                                  # noqa: BLE001 - the tests look at what came out
                result = error
        return result, job, calls, release, update

    def test_a_source_that_kb_whisper_read_and_the_checks_refused_is_read_again_with_large_v3(self):
        refused = worker.TranslationQualityError('Undertexten har kvar konkreta fel')
        result, job, calls, release, update = self.run_net(KB_JOB, [refused, 'KLAR'])
        self.assertEqual(result, 'KLAR')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1], 'the same model, device and health the loop passed in')
        release.assert_called_once()
        self.assertIn('läser om med Whisper large-v3', update.call_args.args[3])
        self.assertEqual(job['swedish_reader_retry']['first_attempt']['chosen'], True)
        self.assertIn('konkreta fel', job['swedish_reader_retry']['error'])
        for stale in ('forced_reading_choice',):
            self.assertNotIn(stale, job)

    def test_a_refused_source_error_is_retried_the_same_way(self):
        result, job, calls, _, _ = self.run_net(KB_JOB, [worker.SourceQualityError('för många trasiga repliker'), 'KLAR'])
        self.assertEqual((result, len(calls)), ('KLAR', 2))

    def test_the_retry_is_made_once_and_its_own_failure_stands(self):
        second = worker.TranslationQualityError('samma fel igen')
        result, job, calls, _, _ = self.run_net(KB_JOB, [worker.TranslationQualityError('första'), second])
        self.assertIs(result, second)
        self.assertEqual(len(calls), 2)

    def test_a_job_that_large_v3_read_is_not_retried(self):
        refused = worker.TranslationQualityError('fel')
        for reader in ({}, {'chosen': False, 'reason': 'ljudet hörs inte som svenska'}):
            result, job, calls, release, _ = self.run_net({'swedish_reader': reader}, [refused])
            self.assertIs(result, refused)
            self.assertEqual(len(calls), 1)
            release.assert_not_called()
        self.assertIs(self.run_net({}, [refused])[0], refused)

    def test_a_cancelled_job_and_other_errors_are_not_retried(self):
        for error in (worker.JobCancelled('stoppad'), RuntimeError('ffmpeg dog'), OSError('disk full')):
            result, job, calls, _, _ = self.run_net(KB_JOB, [error])
            self.assertIs(result, error)
            self.assertEqual(len(calls), 1)

    def test_a_job_that_passes_the_first_time_is_run_once(self):
        result, job, calls, release, _ = self.run_net(KB_JOB, ['KLAR'])
        self.assertEqual((result, len(calls)), ('KLAR', 1))
        release.assert_not_called()
        self.assertNotIn('swedish_reader_retry', job)

    def test_the_second_attempt_does_not_choose_kb_whisper_again_and_remembers_the_first(self):
        job = {'id': 1, 'swedish_reader_retry': {'error': 'x', 'first_attempt': KB_JOB['swedish_reader']}}
        probe = MagicMock(return_value=SWEDISH)
        with patch.object(swedish_reader, 'model_present', return_value=True), patch.object(worker, 'update_job'), \
                patch.object(model_manager, 'enabled_packs', return_value={swedish_reader.PACK_ID}):
            chosen = worker.prefer_swedish_reader(job, Path('/media/x.mkv'), 'LARGE-V3', FakeModels(), 'cpu', 90.0, '/tmp',
                                                  probe=probe, repository='KBLab/kb-whisper-large')
        self.assertEqual(chosen, 'LARGE-V3')
        probe.assert_not_called()
        self.assertFalse(job['swedish_reader']['chosen'])
        self.assertEqual(job['swedish_reader']['first_attempt'], KB_JOB['swedish_reader'])
        self.assertIn('läser om med large-v3', job['swedish_reader']['reason'])

    def test_the_local_loop_runs_the_safety_net_not_a_bare_work(self):
        source = (Path(worker.__file__)).read_text(encoding='utf-8')
        self.assertIn('work_with_swedish_safety_net(job, model, device, health)', source)
        self.assertEqual(source.count('\n            work(job, model, device, health)\n'), 0)


class RecordTests(unittest.TestCase):
    def test_the_choice_is_saved_with_the_source_transcript(self):
        job = {'id': 7, 'swedish_reader': {'repository': 'KBLab/kb-whisper-large', 'chosen': True, 'reason': '3 av 3 ljudfönster hörs som svenska'}}
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            target = worker.save_source_transcript(job, Path('/media/x.mkv'), [{'id': 1, 'start': 0, 'end': 1, 'source': 'Hej'}], 'CPU', 'modell')
            saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(saved['swedish_reader'], job['swedish_reader'])


if __name__ == '__main__':
    unittest.main()
