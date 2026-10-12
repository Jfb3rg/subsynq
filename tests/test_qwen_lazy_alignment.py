import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def load_service(asr, aligner, sequential):
    """Import ai/qwen_asr_service.py with fake torch/qwen_asr and the chosen memory mode."""
    fake_torch = SimpleNamespace(float32='float32', float16='float16', bfloat16='bfloat16',
                                 cuda=SimpleNamespace(is_available=lambda: False))
    spec = importlib.util.spec_from_file_location('qwen_service_test',
                                                 Path('/ai/qwen_asr_service.py'))
    service = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'torch': fake_torch,
            'qwen_asr': SimpleNamespace(Qwen3ASRModel=asr, Qwen3ForcedAligner=aligner)}), \
            patch.dict(os.environ, {'SUBSYNQ_QWEN_SEQUENTIAL': '1' if sequential else '0'}):
        spec.loader.exec_module(service)
    return service


class QwenLazyAlignmentTests(unittest.TestCase):
    """SUBSYNQ_QWEN_SEQUENTIAL=0: the original behaviour, ASR and aligner both resident."""

    def setUp(self):
        self.asr = Mock()
        self.aligner = Mock()
        self.asr.from_pretrained.return_value = SimpleNamespace(forced_aligner=None)
        self.service = load_service(self.asr, self.aligner, sequential=False)

    def test_identification_does_not_load_alignment_weights(self):
        model = self.service.load_model(with_alignment=False)
        self.assertIsNone(model.forced_aligner)
        self.assertNotIn('forced_aligner', self.asr.from_pretrained.call_args.kwargs)
        self.aligner.from_pretrained.assert_not_called()

    def test_later_timestamp_request_loads_aligner_once_without_reloading_asr(self):
        first = self.service.load_model(with_alignment=False)
        second = self.service.load_model(with_alignment=True)
        self.service.load_model(with_alignment=True)
        self.assertIs(first, second)
        self.assertIs(second.forced_aligner, self.aligner.from_pretrained.return_value)
        self.asr.from_pretrained.assert_called_once()
        self.aligner.from_pretrained.assert_called_once()


class QwenSequentialAlignmentTests(unittest.TestCase):
    """Default mode: the ASR weights are freed before the aligner weights are loaded."""

    def setUp(self):
        self.events = []
        self.asr = Mock()
        self.aligner = Mock()
        self.owner = SimpleNamespace(forced_aligner=None, model='asr-weights')
        self.asr.from_pretrained.return_value = self.owner
        self.real_aligner = Mock()
        self.real_aligner.align.return_value = ['aligned']

        def load_aligner(*args, **kwargs):
            # The whole point: at this moment the ASR weights must already be gone.
            self.events.append(('load aligner', self.owner.model))
            return self.real_aligner

        self.aligner.from_pretrained.side_effect = load_aligner
        self.service = load_service(self.asr, self.aligner, sequential=True)

    def test_sequential_is_the_default_when_the_variable_is_unset(self):
        spec = importlib.util.spec_from_file_location('qwen_service_default',
                                                     Path('/ai/qwen_asr_service.py'))
        module = importlib.util.module_from_spec(spec)
        env = {k: v for k, v in os.environ.items() if k != 'SUBSYNQ_QWEN_SEQUENTIAL'}
        fake_torch = SimpleNamespace(float32=1, float16=2, bfloat16=3)
        with patch.dict(sys.modules, {'torch': fake_torch, 'qwen_asr': SimpleNamespace(
                Qwen3ASRModel=Mock(), Qwen3ForcedAligner=Mock())}), \
                patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(module)
        self.assertTrue(module.SEQUENTIAL)

    def test_loading_for_timestamps_does_not_load_the_aligner_yet(self):
        model = self.service.load_model(with_alignment=True)
        self.assertIsInstance(model.forced_aligner, self.service.LazyAligner)
        self.aligner.from_pretrained.assert_not_called()

    def test_asr_weights_are_freed_before_the_aligner_is_loaded(self):
        model = self.service.load_model(with_alignment=True)
        self.assertEqual(model.forced_aligner.align(audio='a', text='t', language='English'),
                         ['aligned'])
        self.assertEqual(self.events, [('load aligner', None)])
        self.assertIsNone(model.model)
        self.real_aligner.align.assert_called_once_with(audio='a', text='t', language='English')

    def test_several_chunks_load_the_aligner_only_once(self):
        model = self.service.load_model(with_alignment=True)
        for _ in range(3):
            model.forced_aligner.align(audio='a', text='t', language='English')
        self.aligner.from_pretrained.assert_called_once()
        self.assertEqual(self.real_aligner.align.call_count, 3)

    def test_identification_keeps_the_asr_model_and_never_touches_the_aligner(self):
        model = self.service.load_model(with_alignment=False)
        self.assertIsNone(model.forced_aligner)
        self.assertEqual(model.model, 'asr-weights')
        self.aligner.from_pretrained.assert_not_called()

    def test_a_loaded_identify_model_can_be_upgraded_without_reloading_asr(self):
        first = self.service.load_model(with_alignment=False)
        second = self.service.load_model(with_alignment=True)
        self.assertIs(first, second)
        self.asr.from_pretrained.assert_called_once()


class QwenRequestMemoryTests(unittest.TestCase):
    """Sequential mode: every request runs in its own process, so the long-lived service
    never holds model weights and memory is always returned to the machine."""

    def start(self, sequential):
        self.asr, self.aligner = Mock(), Mock()
        self.service = load_service(self.asr, self.aligner, sequential=sequential)
        self.server = self.service.ThreadingHTTPServer(('127.0.0.1', 0), self.service.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request('POST', path, body=b'RIFF', headers={'Content-Type': 'audio/wav'})
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def fake_model(self, fail=False):
        result = SimpleNamespace(language='English', text=' hi ', time_stamps=None)

        def transcribe(**kwargs):
            self.transcribe_kwargs = kwargs
            if fail:
                raise RuntimeError('model died')
            return [result]

        return SimpleNamespace(transcribe=transcribe)

    def test_sequential_requests_are_served_by_a_child_and_the_parent_loads_nothing(self):
        self.start(sequential=True)
        calls = []
        payload = {'status': 'ok', 'language': 'English', 'text': 'hi', 'items': []}
        self.service.run_child = lambda path, wav, language: calls.append((path, language)) or payload
        with patch.object(self.service, 'load_model', side_effect=AssertionError('parent loaded a model')):
            self.assertEqual(self.post('/transcribe'), (200, payload))
            self.assertEqual(self.post('/identify'), (200, payload))
        self.assertEqual(calls, [('/transcribe', None), ('/identify', None)])

    def test_a_child_failure_becomes_a_clear_500(self):
        self.start(sequential=True)

        def failing(path, wav, language):
            raise RuntimeError('Qwen-processen avslutades utan svar (kod -9, troligen minnesbrist)')

        self.service.run_child = failing
        status, body = self.post('/transcribe')
        self.assertEqual(status, 500)
        self.assertIn('minnesbrist', body['error'])

    def test_non_sequential_mode_still_serves_in_process(self):
        self.start(sequential=False)
        self.service.run_child = lambda *args: self.fail('no child process expected')
        with patch.object(self.service, 'load_model', return_value=self.fake_model()):
            status, body = self.post('/transcribe')
        self.assertEqual((status, body['text']), (200, 'hi'))

    def test_child_main_writes_the_payload_and_asks_for_timestamps_only_when_aligning(self):
        self.start(sequential=True)
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, 'out.json')
            with patch.object(self.service, 'load_model', return_value=self.fake_model()) as load:
                self.assertEqual(self.service.child_main(['/transcribe', '/x.wav', 'sv', out]), 0)
                self.assertTrue(self.transcribe_kwargs['return_time_stamps'])
                self.assertEqual(self.transcribe_kwargs['language'], 'sv')
                load.assert_called_with(with_alignment=True)
                self.assertEqual(self.service.child_main(['/identify', '/x.wav', '', out]), 0)
                self.assertFalse(self.transcribe_kwargs['return_time_stamps'])
                self.assertIsNone(self.transcribe_kwargs['language'])
                load.assert_called_with(with_alignment=False)
            self.assertEqual(json.load(open(out))['text'], 'hi')

    def test_child_main_reports_a_model_error_and_exits_nonzero(self):
        self.start(sequential=True)
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, 'out.json')
            with patch.object(self.service, 'load_model', return_value=self.fake_model(fail=True)):
                self.assertEqual(self.service.child_main(['/transcribe', '/x.wav', '', out]), 1)
            self.assertEqual(json.load(open(out)),
                             {'status': 'error', 'error': 'model died'})

    def test_run_child_maps_every_way_a_process_can_end(self):
        self.start(sequential=True)
        good = {'status': 'ok', 'language': 'English', 'text': 'hi', 'items': []}
        seen = {}

        def child(code, content):
            def run(command, timeout):
                seen['command'], seen['out'] = command, command[-1]
                seen['timeout'] = timeout
                if content is not None:
                    with open(command[-1], 'w') as handle:
                        json.dump(content, handle)
                return SimpleNamespace(returncode=code)
            return run

        with patch.object(self.service.subprocess, 'run', child(0, good)):
            self.assertEqual(self.service.run_child('/transcribe', '/w.wav', 'sv'), good)
        self.assertEqual(seen['command'][seen['command'].index('--child') + 1:-1],
                         ['/transcribe', '/w.wav', 'sv'])
        self.assertLess(seen['timeout'], 900)  # shorter than the worker's own request timeout
        self.assertFalse(os.path.exists(seen['out']))
        with patch.object(self.service.subprocess, 'run', child(-9, None)):
            with self.assertRaisesRegex(RuntimeError, 'minnesbrist'):
                self.service.run_child('/identify', '/w.wav', None)
        with patch.object(self.service.subprocess, 'run',
                          child(1, {'status': 'error', 'error': 'bad audio'})):
            with self.assertRaisesRegex(RuntimeError, 'bad audio'):
                self.service.run_child('/transcribe', '/w.wav', None)
        with patch.object(self.service.subprocess, 'run', child(2, None)):
            with self.assertRaisesRegex(RuntimeError, 'kod 2'):
                self.service.run_child('/transcribe', '/w.wav', None)
        with patch.object(self.service.subprocess, 'run',
                          side_effect=self.service.subprocess.TimeoutExpired('x', 1)):
            with self.assertRaisesRegex(RuntimeError, 'för lång tid'):
                self.service.run_child('/transcribe', '/w.wav', None)
