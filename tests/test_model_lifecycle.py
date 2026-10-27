import io
import json
import sys
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
import worker


class ModelLifecycleTests(unittest.TestCase):
    def reading_model(self, name, events):
        model = Mock()
        model.model.load_model.side_effect = lambda: events.append(f'load {name}')
        model.model.unload_model.side_effect = lambda: events.append(f'unload {name}')
        model.transcribe.side_effect = lambda *args, **kwargs: events.append(f'read {name}') or ([], None)
        return model

    def test_whisper_qwen_v2_main_switches_never_overlap(self):
        events = []
        main = self.reading_model('v3', events)
        v2 = self.reading_model('v2', events)
        session = worker.SequentialASRModels(main)
        reading = session.wrap(main)
        with patch.object(worker, 'trim_cpu_allocator'), \
                patch.object(worker, 'qwen_unload', side_effect=lambda: events.append('unload qwen')), \
                patch.object(worker, 'WhisperModel', side_effect=lambda *a, **k: events.append('construct v2') or v2):
            reading.transcribe('clip')
            session.qwen(lambda: events.append('read qwen'))
            session.create_whisper('large-v2').transcribe('clip')
            reading.transcribe('clip')
            session.close()
        self.assertEqual(events, ['read v3', 'unload v3', 'read qwen', 'unload qwen',
                                  'construct v2', 'read v2', 'unload v2', 'load v3',
                                  'read v3', 'unload v3'])

    def test_filename_probe_switches_to_qwen_and_reloads_whisper_for_main_pass(self):
        events = []
        model = self.reading_model('v3', events)
        model.transcribe.side_effect = lambda *a, **k: events.append('read v3') or (
            [SimpleNamespace(text='Hello')], SimpleNamespace(language='en'))
        session = worker.SequentialASRModels(model)
        reading = session.wrap(model)
        with patch.object(worker, 'filename_language_hint', return_value='en'), \
                patch.object(worker, 'probe_windows', return_value=[{'start': 0, 'end': 1}]), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'), \
                patch.object(worker.subprocess, 'run'), patch.object(worker, 'decode_audio'), \
                patch.object(worker, 'get_speech_timestamps', return_value=[{'start': 0, 'end': 16000}]), \
                patch.object(worker, 'QWEN_ASR_ENABLED', True), \
                patch.object(worker, 'qwen_identify', side_effect=lambda clip: events.append('read qwen') or
                             {'language': 'en', 'text': 'Hello'}), \
                patch.object(worker, 'qwen_unload', side_effect=lambda: events.append('unload qwen')), \
                patch.object(worker, 'trim_cpu_allocator'):
            worker.probe_filename_language({'id': 9}, Path('clip.mp4'), reading, 1, '/tmp', models=session)
            reading.transcribe('main clip')
            session.close()
        self.assertEqual(events, ['read v3', 'unload v3', 'read qwen', 'unload qwen',
                                  'load v3', 'read v3', 'unload v3'])

    def test_failed_qwen_request_still_unloads_before_whisper_can_resume(self):
        events = []
        main = self.reading_model('v3', events)
        session = worker.SequentialASRModels(main)
        with patch.object(worker, 'qwen_unload', side_effect=lambda: events.append('unload qwen')), \
                patch.object(worker, 'trim_cpu_allocator'):
            with self.assertRaises(RuntimeError):
                session.qwen(Mock(side_effect=RuntimeError('ASR failed')))
            session.wrap(main).transcribe('clip')
            session.close()
        self.assertEqual(events, ['unload v3', 'unload qwen', 'load v3', 'read v3', 'unload v3'])

    def test_unconfirmed_qwen_unload_prevents_whisper_load_and_constructor(self):
        for create in (False, True):
            with self.subTest(create=create):
                events = []
                main = self.reading_model('v3', events)
                session = worker.SequentialASRModels(main)
                with patch.object(worker, 'trim_cpu_allocator'), \
                        patch.object(worker, 'qwen_unload', side_effect=RuntimeError('not released')), \
                        patch.object(worker, 'WhisperModel') as constructor:
                    session.qwen(lambda: None)
                    with self.assertRaisesRegex(RuntimeError, 'not released'):
                        if create:
                            session.create_whisper('large-v2')
                        else:
                            session.wrap(main).transcribe('clip')
                constructor.assert_not_called()
                self.assertEqual(events, ['unload v3'])
                self.assertTrue(session.qwen_pending)

    def test_failed_whisper_unload_prevents_qwen_request(self):
        model = Mock()
        model.model.unload_model.side_effect = RuntimeError('still loaded')
        session = worker.SequentialASRModels(model)
        request = Mock()
        with self.assertRaisesRegex(RuntimeError, 'still loaded'):
            session.qwen(request)
        request.assert_not_called()
        self.assertIs(session.active, model)

    def test_cancellation_releases_model_inside_transcription_session(self):
        model = Mock()
        with patch.object(worker, '_transcribe', side_effect=worker.JobCancelled('stop')):
            with self.assertRaises(worker.JobCancelled):
                worker.transcribe({}, 'clip', model)
        model.model.unload_model.assert_called_once_with()

    def test_failed_reload_is_cleaned_without_reading_audio(self):
        model = Mock()
        session = worker.SequentialASRModels(model)
        session.release_whisper()
        model.model.load_model.side_effect = RuntimeError('load failed')
        with self.assertRaisesRegex(RuntimeError, 'load failed'):
            session.wrap(model).transcribe('clip')
        session.close()
        model.transcribe.assert_not_called()
        self.assertEqual(model.model.unload_model.call_count, 2)

    def test_main_releases_gemma_before_constructing_and_running_reserve(self):
        self.check_reserve_transition(unload_fails=False)

    def test_main_does_not_start_reserve_when_gemma_unload_fails(self):
        self.check_reserve_transition(unload_fails=True)

    def test_only_source_rejection_uses_reserve_and_cloud_rental_is_always_released(self):
        cases = [(preference, error_type)
                 for preference in ('cpu', 'cloud-vast', 'cloud-cpu-vast')
                 for error_type in (worker.SourceQualityError, worker.TranslationQualityError)]
        for preference, error_type in cases:
            with self.subTest(preference=preference, error_type=error_type):
                job = {'id': 26, 'status': 'queued'}
                model = Mock()
                error = error_type('quality rejected')
                translation_failed = error_type is worker.TranslationQualityError
                replacements = {
                    'MODEL_JOB': SimpleNamespace(),
                    'claim_job': Mock(side_effect=[job, KeyboardInterrupt()]),
                    'hardware_preference': Mock(return_value=preference),
                    'select_job_hardware': Mock(return_value=(None, False)),
                    'WhisperModel': Mock(return_value=model),
                    'rent_cloud_chain': Mock(return_value=(model, 123, 'url', None)),
                    'cloud_chain_endpoints': Mock(return_value=nullcontext()),
                    'vram_recorder': Mock(return_value=nullcontext()),
                    'work': Mock(side_effect=[error, None]),
                    'cloud_jobs_are_waiting': Mock(return_value=False),
                }
                replacements.update({name: Mock() for name in (
                    'wait_for_database', 'gpu_health', 'set_hardware', 'recover_interrupted_jobs',
                    'cleanup_orphaned_cloud_instances', 'reset_cloud_session_after_restart', 'prepare_asr_job',
                    'destroy_cloud_gpu', 'release_language_model_before_asr', 'finish_model_job', 'update_job')})
                with ExitStack() as stack:
                    for name, replacement in replacements.items():
                        stack.enter_context(patch.object(worker, name, replacement))
                    stack.enter_context(patch.object(worker.threading, 'Thread'))
                    database = stack.enter_context(patch.object(worker.sqlite3, 'connect'))
                    with self.assertRaises(KeyboardInterrupt):
                        worker.main()
                work, release, finish, update, whisper, destroy = (
                    replacements[name] for name in ('work', 'release_language_model_before_asr',
                        'finish_model_job', 'update_job', 'WhisperModel', 'destroy_cloud_gpu'))
                self.assertEqual(work.call_count, 1 if translation_failed else 2)
                if translation_failed:
                    model.with_model.assert_not_called()
                    release.assert_not_called()
                    self.assertNotIn('fallback_reason', job)
                    update.assert_any_call(26, 'quality_failed', 0, str(error))
                else:
                    release.assert_called_once_with()
                    self.assertIn('källkontrollen', job['fallback_reason'])
                finish.assert_called_once_with(26)
                if preference.startswith('cloud-'):
                    whisper.assert_not_called()
                    if not translation_failed:
                        model.with_model.assert_called_once_with('large-v2')
                    destroy.assert_called_once_with('vast', 123)
                    database.return_value.__enter__.return_value.execute.assert_called_once_with(
                        'UPDATE subtitle_jobs SET cloud_instance_id=NULL WHERE id=?', (26,))
                else:
                    self.assertEqual(whisper.call_count, 1 if translation_failed else 2)
                    self.assertEqual(whisper.call_args_list[0].args[0], 'large-v3')
                    if not translation_failed:
                        self.assertEqual(whisper.call_args_list[1].args[0], 'large-v2')
                    destroy.assert_not_called()

    def check_reserve_transition(self, *, unload_fails):
        events = []
        job = {'id': 9, 'status': 'queued'}

        def construct(name, **kwargs):
            events.append(name)
            return Mock()

        def work(*args, **kwargs):
            events.append('work')
            if events.count('work') == 1:
                worker.MODEL_JOB.used = True
                worker.MODEL_JOB.active_model = 'reviewer'
                raise worker.SourceQualityError('bad source')

        def unload(request):
            events.append('unload gemma')
            self.assertEqual(request, {'model': 'reviewer', 'keep_alive': 0})
            if unload_fails:
                raise RuntimeError('unload failed')
            return {'done_reason': 'unload'}

        with patch.object(worker.MODEL_JOB, 'job_id', None, create=True), \
                patch.object(worker.MODEL_JOB, 'used', False, create=True), \
                patch.object(worker.MODEL_JOB, 'phase', None, create=True), \
                patch.object(worker.MODEL_JOB, 'device', None, create=True), \
                patch.object(worker, 'claim_job', side_effect=[job, KeyboardInterrupt()]), \
                patch.object(worker, 'wait_for_database'), patch.object(worker, 'gpu_health'), \
                patch.object(worker, 'set_hardware'), \
                patch.object(worker, 'recover_interrupted_jobs'), \
                patch.object(worker, 'cleanup_orphaned_cloud_instances'), \
                patch.object(worker, 'reset_cloud_session_after_restart'), \
                patch.object(worker.threading, 'Thread'), \
                patch.object(worker, 'hardware_preference', return_value='cpu'), \
                patch.object(worker, 'select_job_hardware', return_value=(None, False)), \
                patch.object(worker, 'WhisperModel', side_effect=construct), \
                patch.object(worker, 'work', side_effect=work), \
                patch.object(worker, 'call_ollama', side_effect=unload), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'finish_model_job'), \
                patch.object(worker, 'update_job') as update:
            with self.assertRaises(KeyboardInterrupt):
                worker.main()
        if unload_fails:
            self.assertEqual(events, ['large-v3', 'work', 'unload gemma'])
            self.assertEqual(update.call_args.args[1], 'failed')
        else:
            self.assertEqual(events, ['large-v3', 'work', 'unload gemma', 'large-v2', 'work'])

    def test_gemma_unload_requires_explicit_acknowledgment(self):
        with patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'reviewer', create=True), \
                patch.object(worker, 'call_ollama', return_value={'done_reason': 'stop'}):
            with self.assertRaisesRegex(RuntimeError, 'Gemma frigjorde inte'):
                worker.release_language_model_before_asr()
            self.assertTrue(worker.MODEL_JOB.used)
            self.assertEqual(worker.MODEL_JOB.job_id, 9)

    def test_pending_cancellation_does_not_block_gemma_unload_but_stops_retry(self):
        with patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'reviewer', create=True), \
                patch.object(worker, 'call_ollama', return_value={'done_reason': 'unload'}) as unload, \
                patch.object(worker, 'check_cancelled', side_effect=worker.JobCancelled('stop')):
            with self.assertRaises(worker.JobCancelled):
                worker.release_language_model_before_asr()
            unload.assert_called_once()
            self.assertFalse(worker.MODEL_JOB.used)
            self.assertEqual(worker.MODEL_JOB.job_id, 9)

    def test_missing_native_trim_is_safe(self):
        with patch('ctypes.CDLL', side_effect=OSError('not supported')):
            self.assertFalse(worker.trim_cpu_allocator())

    def test_asr_weights_released_before_language_models(self):
        from unittest.mock import Mock
        model = Mock()
        events = []
        model.model.load_model.side_effect = lambda: events.append('load')
        model.model.unload_model.side_effect = lambda: events.append('unload')
        with patch.object(worker, '_transcribe', side_effect=lambda *args, **kwargs: events.append('asr') or ['row']):
            self.assertEqual(worker.transcribe_and_release({}, 'media', model, 'cpu'), ['row'])
        self.assertEqual(events, ['load', 'asr', 'unload'])

    def test_asr_exception_still_releases_weights(self):
        from unittest.mock import Mock
        model = Mock()
        with patch.object(worker, '_transcribe', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                worker.transcribe_and_release({}, 'media', model, 'cpu')
        model.model.unload_model.assert_called_once_with()

    def test_remote_asr_has_no_local_model_to_unload(self):
        model = worker.RemoteWhisperModel()
        with patch.object(worker, '_transcribe', return_value=['row']):
            self.assertEqual(worker.transcribe_and_release({}, 'media', model, 'cloud-vast'), ['row'])

    def setUp(self):
        # The language-identification step has its own tests (tests/test_swedish_passthrough.py).
        stub = patch.object(worker, 'identify_swedish_rows', return_value=set())
        stub.start()
        self.addCleanup(stub.stop)
        for state, name, value in ((worker.MODEL_JOB, 'active_model', None),
                                   (worker.MODEL_JOB, 'used', False),
                                   (worker.MODEL_JOB, 'cleanup_pending', None),
                                   (worker.ASR_JOB, 'pending', None)):
            active = patch.object(state, name, value, create=True)
            active.start()
            self.addCleanup(active.stop)

    def test_failed_asr_cleanup_blocks_next_job_until_unload_succeeds(self):
        session = worker.SequentialASRModels(Mock())
        session.release_whisper()
        session.qwen_pending = True
        worker.ASR_JOB.pending = session
        with patch.object(worker, 'qwen_unload', side_effect=RuntimeError('still loaded')):
            with self.assertRaisesRegex(RuntimeError, 'still loaded'):
                worker.prepare_asr_job()
        self.assertIs(worker.ASR_JOB.pending, session)
        with patch.object(worker, 'qwen_unload') as unload:
            worker.prepare_asr_job()
        unload.assert_called_once_with()
        self.assertIsNone(worker.ASR_JOB.pending)

    def test_session_is_retained_when_transcription_cleanup_fails(self):
        model = Mock()
        model.model.unload_model.side_effect = RuntimeError('still loaded')
        with patch.object(worker, '_transcribe', return_value=['row']):
            with self.assertRaisesRegex(RuntimeError, 'still loaded'):
                worker.transcribe({}, 'clip', model)
        pending = worker.ASR_JOB.pending
        self.assertIs(pending.active, model)
        model.model.unload_model.side_effect = None
        worker.prepare_asr_job()
        self.assertIsNone(worker.ASR_JOB.pending)

    def test_failed_gemma_cleanup_is_retained_and_blocks_next_job(self):
        with patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'used', True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'reviewer'), \
                patch.object(worker, 'call_ollama', side_effect=RuntimeError('still loaded')), \
                patch('builtins.print'):
            worker.finish_model_job(9)
            self.assertEqual(worker.MODEL_JOB.cleanup_pending, 'reviewer')
            with self.assertRaisesRegex(RuntimeError, 'still loaded'):
                worker.prepare_asr_job()
        with patch.object(worker, 'call_ollama', return_value={'done_reason': 'unload'}) as unload:
            worker.prepare_asr_job()
        unload.assert_called_once_with({'model': 'reviewer', 'keep_alive': 0})
        self.assertIsNone(worker.MODEL_JOB.cleanup_pending)

    def test_unconfirmed_cleanup_is_retained_without_changing_job_result(self):
        with patch.object(worker.MODEL_JOB, 'used', True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'reviewer'), \
                patch.object(worker, 'call_ollama', return_value={'done_reason': 'stop'}), \
                patch('builtins.print'):
            worker.finish_model_job(9)
        self.assertEqual(worker.MODEL_JOB.cleanup_pending, 'reviewer')

    def test_switch_unloads_previous_model_before_loading_reviewer_and_cleans_reviewer(self):
        responses = [io.BytesIO(b'{"done_reason":"unload"}'),
                     io.BytesIO(b'{"done_reason":"stop","response":"{}"}'),
                     io.BytesIO(b'{"done_reason":"unload"}')]
        with patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'translator'), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=responses) as opening, \
                patch('builtins.print'):
            worker.call_ollama({'model': 'reviewer', 'prompt': 'review'})
            worker.finish_model_job(9)
            self.assertIsNone(worker.MODEL_JOB.active_model)
        requests = [json.loads(c.args[0].data) for c in opening.call_args_list]
        self.assertEqual([r['model'] for r in requests], ['translator', 'reviewer', 'reviewer'])
        self.assertEqual([r.get('keep_alive') for r in requests], [0, None, 0])

    def test_failed_reviewer_load_still_cleans_reviewer_after_old_model_is_unloaded(self):
        responses = [io.BytesIO(b'{"done_reason":"unload"}'), TimeoutError(),
                     io.BytesIO(b'{"done_reason":"unload"}')]
        with patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'translator'), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=responses) as opening, \
                patch('builtins.print'):
            with self.assertRaises(RuntimeError):
                worker.call_ollama({'model': 'reviewer', 'prompt': 'review'})
            worker.finish_model_job(9)
        self.assertEqual([json.loads(c.args[0].data)['model'] for c in opening.call_args_list],
                         ['translator', 'reviewer', 'reviewer'])
    def test_translation_does_not_unload_before_final_audit(self):
        answer = {'response': json.dumps({'rows': [{'id': 0, 'sv': 'Det här är min bok.'}]})}
        with patch.object(worker, 'call_ollama', return_value=answer) as model, \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            worker.translate({'id': 9}, [{'id': 0, 'source': 'This is my book.'}])
        self.assertEqual(model.call_count, 2)
        self.assertTrue(all(call.args[0].get('keep_alive') != 0 for call in model.call_args_list))

    def test_cleanup_unloads_once_and_ignores_pending_cancellation(self):
        response = io.BytesIO(b'{"done_reason":"unload"}')
        with patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'phase', 'final_review', create=True), \
                patch.object(worker.urllib.request, 'urlopen', return_value=response) as opening, \
                patch.object(worker, 'check_cancelled', side_effect=worker.JobCancelled('stop')) as cancel, \
                patch('builtins.print'):
            worker.finish_model_job(9)
            self.assertIsNone(worker.MODEL_JOB.job_id)
            self.assertFalse(worker.MODEL_JOB.used)
            self.assertIsNone(worker.MODEL_JOB.phase)
        opening.assert_called_once()
        cancel.assert_not_called()
        self.assertEqual(json.loads(opening.call_args.args[0].data)['keep_alive'], 0)

    def test_cleanup_error_does_not_replace_original_job_result(self):
        with patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'phase', 'final_review', create=True), \
                patch.object(worker, 'call_ollama', side_effect=RuntimeError('unavailable')), \
                patch('builtins.print') as printing:
            worker.finish_model_job(9)
            self.assertFalse(worker.MODEL_JOB.used)
        self.assertEqual(json.loads(printing.call_args.args[0])['status'], 'error')

    def test_job_without_model_use_does_not_send_cleanup_request(self):
        with patch.object(worker.MODEL_JOB, 'used', False, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'phase', None, create=True), \
                patch.object(worker, 'call_ollama') as model:
            worker.finish_model_job(9)
        model.assert_not_called()

    def test_timing_log_has_metrics_and_no_prompt_or_response_text(self):
        result = {'done_reason': 'stop', 'response': 'PRIVATE_RESPONSE',
                  'load_duration': 1000000000, 'prompt_eval_duration': 2000000000,
                  'eval_duration': 4000000000, 'eval_count': 20}
        with patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'used', False, create=True), \
                patch.object(worker.MODEL_JOB, 'phase', 'final_review', create=True), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(result).encode())), \
                patch('builtins.print') as printing:
            worker.call_ollama({'model': 'test', 'prompt': 'PRIVATE_PROMPT',
                                'options': {'num_ctx': 8192, 'num_predict': 768}})
            self.assertTrue(worker.MODEL_JOB.used)
        logs = [json.loads(call.args[0]) for call in printing.call_args_list]
        self.assertEqual([record['event'] for record in logs], ['model_start', 'model_complete'])
        self.assertEqual(logs[1]['tokens_per_second'], 5)
        self.assertEqual(logs[1]['load_seconds'], 1)
        self.assertEqual(logs[1]['phase'], 'final_review')
        self.assertNotIn('PRIVATE', json.dumps(logs))

    def test_failed_request_logs_error_and_elapsed_time(self):
        with patch.object(worker.MODEL_JOB, 'job_id', 9, create=True), \
                patch.object(worker.MODEL_JOB, 'used', False, create=True), \
                patch.object(worker.MODEL_JOB, 'phase', 'final_review', create=True), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=TimeoutError()), \
                patch('builtins.print') as printing:
            with self.assertRaises(RuntimeError):
                worker.call_ollama({'model': 'test'})
        log = json.loads(printing.call_args.args[0])
        self.assertEqual(log['event'], 'model_error')
        self.assertEqual(log['job_id'], 9)
        self.assertGreaterEqual(log['elapsed_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
