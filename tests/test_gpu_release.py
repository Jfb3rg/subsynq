import http.client
import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import ast
import cloud_cpu_service as gateway
import cloud_gpu
import worker

TOKEN = 'release-test-token-0123456789'


class FakeCt2Model:
    def __init__(self):
        self.unloaded = 0

    def unload_model(self):
        self.unloaded += 1


class FakeWhisper:
    def __init__(self):
        self.model = FakeCt2Model()


class QwenBackend(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.seen.append(self.path)
        body = json.dumps(self.server.reply).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def serve(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class GatewayReleaseTests(unittest.TestCase):
    """The rented RTX 3090 kept Whisper (3.4 GiB) and Qwen3-ASR (3.6 GiB) in the card through the translation, so gemma3:27b ran partly on the CPU."""

    def setUp(self):
        self.qwen = serve(QwenBackend)
        self.qwen.seen, self.qwen.reply = [], {'status': 'ready', 'model_loaded': False, 'restarting': True}
        self.fake = FakeWhisper()
        for item in (patch.object(gateway, 'QWEN_PORT', self.qwen.server_port), patch.object(gateway, 'TOKEN', TOKEN),
                     patch.object(gateway, 'whisper', self.fake), patch.object(gateway, 'whisper_loaded', 'large-v3')):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.qwen.server_close)
        self.addCleanup(self.qwen.shutdown)

    def test_whisper_is_unloaded_dropped_and_qwen_is_asked_for_a_hard_unload(self):
        report = gateway.release_gpu_memory()
        self.assertEqual(report, {'whisper': True, 'qwen': True})
        self.assertEqual(self.fake.model.unloaded, 1)
        self.assertIsNone(gateway.whisper)
        self.assertIsNone(gateway.whisper_loaded)
        self.assertEqual(self.qwen.seen, ['/unload?hard=1'])

    def test_a_second_release_finds_nothing_to_free_and_a_silent_qwen_is_reported(self):
        gateway.release_gpu_memory()
        with patch.object(gateway, 'local_json', return_value=None):
            self.assertEqual(gateway.release_gpu_memory(), {'whisper': False, 'qwen': None})

    def test_a_failing_unload_still_drops_the_model(self):
        self.fake.model.unload_model = Mock(side_effect=RuntimeError('cuda busy'))
        report = gateway.release_gpu_memory()
        self.assertTrue(report['whisper'])
        self.assertIsNone(gateway.whisper)

    def test_qwen_that_only_released_its_model_is_not_reported_as_restarting(self):
        self.qwen.reply = {'status': 'ready', 'model_loaded': False, 'restarting': False}
        self.assertIs(gateway.release_gpu_memory()['qwen'], False)

    def test_the_route_needs_the_token_and_answers_with_the_report(self):
        server = serve(gateway.Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for path, expected in (('/release', 404), ('/wrong/release', 404), (f'/{TOKEN}/release', 200)):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            connection.request('POST', path, body=b'')
            response = connection.getresponse()
            body = response.read()
            connection.close()
            self.assertEqual(response.status, expected, path)
        self.assertEqual(json.loads(body), {'whisper': True, 'qwen': True})


def load_qwen_service(device):
    """ai/qwen_asr_service.py with a fake torch, the chosen device and the in-process memory mode the rented GPU uses."""
    fake_torch = SimpleNamespace(float32='float32', float16='float16', bfloat16='bfloat16', cuda=SimpleNamespace(is_available=lambda: False))
    spec = importlib.util.spec_from_file_location('qwen_service_release_test', Path('/ai/qwen_asr_service.py'))
    service = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'torch': fake_torch, 'qwen_asr': SimpleNamespace(Qwen3ASRModel=Mock(), Qwen3ForcedAligner=Mock())}), \
            patch.dict(os.environ, {'SUBSYNQ_QWEN_SEQUENTIAL': '0', 'SUBSYNQ_QWEN_DEVICE': device}):
        spec.loader.exec_module(service)
    return service


class QwenHardUnloadTests(unittest.TestCase):
    def post(self, service, path):
        handler = service.Handler.__new__(service.Handler)
        handler.path = path
        handler.send_json = Mock()
        with patch.object(service, 'restart_process') as restart:
            service.Handler.do_POST(handler)
        return handler.send_json.call_args.args, restart

    def test_a_hard_unload_on_a_card_after_loading_restarts_the_process(self):
        service = load_qwen_service('cuda')
        service.load_model(with_alignment=False)
        (status, payload), restart = self.post(service, '/unload?hard=1')
        self.assertEqual((status, payload['restarting']), (200, True))
        restart.assert_called_once()
        self.assertIsNone(service.model)

    def test_nothing_is_restarted_when_no_model_was_ever_loaded(self):
        service = load_qwen_service('cuda')
        (_, payload), restart = self.post(service, '/unload?hard=1')
        self.assertFalse(payload['restarting'])
        restart.assert_not_called()

    def test_a_plain_unload_and_the_cpu_never_restart(self):
        service = load_qwen_service('cuda')
        service.load_model(with_alignment=False)
        restart = self.post(service, '/unload')[1]
        restart.assert_not_called()
        cpu = load_qwen_service('cpu')
        cpu.load_model(with_alignment=False)
        restart = self.post(cpu, '/unload?hard=1')[1]
        restart.assert_not_called()

    def test_the_restart_replaces_this_process_with_the_same_command_when_idle(self):
        service = load_qwen_service('cuda')
        done = threading.Event()
        calls = []

        def fake_exec(program, arguments):
            calls.append((program, arguments))
            done.set()
        with patch.object(service.os, 'execv', side_effect=fake_exec), patch.object(service.time, 'sleep'), \
                patch.object(service.sys, 'orig_argv', ['python3', '-u', '/root/qwen_asr_service.py']):
            service.restart_process()
            self.assertTrue(done.wait(5))
        self.assertEqual(calls, [(sys.executable, [sys.executable, '-u', '/root/qwen_asr_service.py'])])


class CompactPythonTests(unittest.TestCase):
    """The boot script of a rental may hold 16 000 characters; the gateway and the Qwen service are sent in it, so their source is indented with one
    space per level (the same program, checked by comparing the syntax trees) and carries no comments or docstrings."""

    def test_the_narrow_copy_is_the_same_program_with_one_space_per_level(self):
        source = ('def outer(x):\n    """doc"""\n    if x:\n        for item in x:\n            print(item, "a\\n    b")\n'
                  '    return {"k": [1, 2,\n        3]}\n')
        narrow = cloud_gpu.compact_python(source)
        plain = ast.unparse(ast.parse(source.replace('    """doc"""\n', ''))) + '\n'
        self.assertEqual(ast.dump(ast.parse(narrow)), ast.dump(ast.parse(plain)))
        self.assertIn('\n if x:\n  for item in x:\n   print(', narrow)
        self.assertNotIn('doc', narrow)

    def test_the_two_services_keep_their_behaviour_and_the_chain_script_fits(self):
        for path in ('/ai/cloud_cpu_service.py', '/ai/qwen_asr_service.py'):
            source = Path(path).read_text(encoding='utf-8')
            plain = ast.unparse(ast.parse(source))
            narrow = cloud_gpu.compact_python(source)
            self.assertLess(len(narrow), len(plain) + 1)
            tree = ast.parse(narrow)
            self.assertTrue(any(isinstance(node, ast.FunctionDef) and node.name == ('release_gpu_memory' if 'cloud' in path else 'restart_process')
                                for node in ast.walk(tree)))


class WorkerReleaseTests(unittest.TestCase):
    def test_the_address_is_found_only_while_a_job_runs_on_a_rented_server(self):
        with patch.object(worker, 'TRANSLATE_URL', 'https://203.0.113.5:8443/tok/ollama/api/generate'):
            self.assertEqual(worker.cloud_base_url(), 'https://203.0.113.5:8443/tok')
        with patch.object(worker, 'TRANSLATE_URL', 'http://translate-engine:11434/api/generate'):
            self.assertIsNone(worker.cloud_base_url())
            self.assertIsNone(worker.release_cloud_gpu_memory())

    def test_the_release_is_posted_to_the_server_and_its_answer_returned(self):
        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return b'{"whisper": "released", "qwen": "restarting"}'
        seen = []
        with patch.object(worker, 'TRANSLATE_URL', 'https://203.0.113.5:8443/tok/ollama/api/generate'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=lambda request, timeout: seen.append((request.full_url, request.get_method())) or Reply()):
            self.assertEqual(worker.release_cloud_gpu_memory(), {'whisper': 'released', 'qwen': 'restarting'})
        self.assertEqual(seen, [('https://203.0.113.5:8443/tok/release', 'POST')])

    def test_a_server_that_cannot_release_never_fails_the_job(self):
        with patch.object(worker, 'TRANSLATE_URL', 'https://203.0.113.5:8443/tok/ollama/api/generate'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=OSError('refused')):
            self.assertIsNone(worker.release_cloud_gpu_memory())

    def test_it_runs_after_the_transcription_and_before_the_translation_and_after_the_rereading(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        asr = source.index('raw_rows = transcribe_and_release(job, media, model, device)\n    release_cloud_gpu_memory()')
        translation = source.index("    release_cloud_gpu_memory()\n    update_job(job['id'], 'translating', 61")
        self.assertLess(asr, translation)
        self.assertIn('if release_cloud_gpu_memory() is None:', source)


if __name__ == '__main__':
    unittest.main()
