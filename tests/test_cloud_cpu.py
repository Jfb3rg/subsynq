import base64
import http.client
import io
import json
import re
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/ai')
import cloud_cpu_service as gateway
import cloud_gpu
import cloud_market
import hardware
import worker
from test_worker_cloud_gpu import make_database

TOKEN = 'T' * 24
PEM_CERT = '-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----'
PEM_KEY = '-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----'



def unpack_bundle(script):
    """What the server does with the bundle (base64 -d | tar xz): {file name: text}."""
    encoded = script.split("<<'SUBSYNQ_BUNDLE_EOF'\n", 1)[1].split('SUBSYNQ_BUNDLE_EOF', 1)[0]
    archive = tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz')
    return {member.name: archive.extractfile(member).read().decode('utf-8')
            for member in archive.getmembers()}


def bootstrap_of(script):
    return unpack_bundle(script)['bootstrap.sh']

def vast_offer(**changes):
    offer = {'id': 1, 'num_gpus': 0, 'dph_total': 0.011, 'cpu_cores_effective': 64.0,
             'disk_space': 500.0, 'inet_down': 900.0, 'reliability': 0.99,
             'rentable': True, 'rented': False, 'cpu_name': 'AMD EPYC 7V12 64-Core Processor'}
    offer.update(changes)
    return offer


class CpuOfferTests(unittest.TestCase):
    def test_keeps_only_cpu_offers_that_meet_the_minimum(self):
        offers = [vast_offer(id=1, dph_total=0.02), vast_offer(id=2, dph_total=0.01),
                  vast_offer(id=3, num_gpus=1), vast_offer(id=4, cpu_cores_effective=8),
                  vast_offer(id=5, disk_space=20), vast_offer(id=6, inet_down=50),
                  vast_offer(id=7, reliability=0.5), vast_offer(id=8, rented=True)]
        result = cloud_market.normalize_vast_cpu({'offers': offers})
        self.assertEqual([item['id'] for item in result], ['2', '1'])
        self.assertEqual(result[0]['gpu'], 'CPU')
        self.assertIn('64 kärnor', result[0]['detail'])

    def test_rejects_an_invalid_payload(self):
        with self.assertRaises(ValueError):
            cloud_market.normalize_vast_cpu({'nope': []})

    def test_query_asks_for_no_gpu_and_only_vast_is_supported(self):
        seen = []

        def reader(request):
            seen.append(json.loads(request.data))
            return {'offers': [vast_offer()]}

        result = cloud_market.fetch_cpu_offers('vast', 'secret', reader=reader)
        self.assertEqual(len(result), 1)
        self.assertEqual(seen[0]['num_gpus'], {'eq': 0})
        with self.assertRaises(ValueError):
            cloud_market.fetch_cpu_offers('runpod', reader=reader)


class OnstartScriptTests(unittest.TestCase):
    def build(self, token=TOKEN, models=('gemma3:12b', 'gemma4:12b'), device='cpu'):
        return cloud_gpu.build_chain_onstart_script(
            token, models, device=device, tls_cert=PEM_CERT, tls_key=PEM_KEY,
            gateway_source='# gateway', qwen_source='# qwen',
            qwen_constraints='torch==2.9.1+cpu\nnumpy==2', whisper_constraints='av==18')

    def test_script_runs_everything_long_in_the_background(self):
        script = self.build()
        boot = bootstrap_of(script)
        self.assertIn('nohup bash /root/bootstrap.sh', script)
        self.assertTrue(script.rstrip().endswith('disown'))
        for needle in (TOKEN, 'ollama pull gemma3:12b', 'ollama pull gemma4:12b',
                       'SUBSYNQ_CLOUD_OLLAMA_MODELS=gemma3:12b,gemma4:12b',
                       'for model in large-v3 large-v2', 'SUBSYNQ_QWEN_DEVICE=cpu'):
            self.assertIn(needle, boot)

    def test_duplicate_models_are_pulled_once(self):
        script = self.build(models=('gemma3:12b', 'gemma3:12b'))
        self.assertEqual(bootstrap_of(script).count('ollama pull gemma3:12b'), 1)

    def test_visible_whisper_download_preserves_faster_whisper_repositories_and_files(self):
        from faster_whisper import download_model
        boot = bootstrap_of(self.build())
        source = boot.split("<<'HF_EOF'\n", 1)[1].split('HF_EOF', 1)[0]
        self.assertIn('HF_HUB_DISABLE_XET=1 HF_HUB_DOWNLOAD_TIMEOUT=120', boot)
        call = next(line for line in boot.splitlines() if 'hf_get Systran/faster-whisper-$model' in line)
        patterns = shlex.split(call)[2:]
        for model in ('large-v3', 'large-v2'):
            with patch('huggingface_hub.snapshot_download') as download:
                download_model(model)
                expected = download.call_args
                download.reset_mock()
                with patch.object(sys, 'argv', ['-', f'Systran/faster-whisper-{model}', *patterns]), \
                        patch('builtins.print'):
                    exec(compile(source, '<bootstrap-download>', 'exec'), {})
                actual = download.call_args
            self.assertEqual(actual.args, expected.args)
            self.assertEqual(actual.kwargs['allow_patterns'], expected.kwargs['allow_patterns'])
            self.assertEqual(actual.kwargs['max_workers'], 1)
            self.assertNotIn('tqdm_class', actual.kwargs)

    def test_rejects_unsafe_token_and_model_names(self):
        for token in ('short', 'x' * 20 + ';rm -rf /', ''):
            with self.assertRaises(ValueError):
                self.build(token=token)
        for model in ('a b', 'x;y', '$(id)', ''):
            with self.assertRaises(ValueError):
                self.build(models=(model,))
        with self.assertRaises(ValueError):
            self.build(models=())

    def real_script(self, device='cpu', tls_cert=PEM_CERT, tls_key=PEM_KEY, **sources):
        files = {'gateway_source': '/ai/cloud_cpu_service.py', 'qwen_source': '/ai/qwen_asr_service.py',
                 'qwen_constraints': '/ai/constraints-qwen.txt',
                 'whisper_constraints': '/ai/constraints-worker.txt'}
        arguments = {name: Path(path).read_text(encoding='utf-8') for name, path in files.items()}
        arguments.update(sources)
        return cloud_gpu.build_chain_onstart_script(
            TOKEN, ['gemma3:12b', 'gemma4:12b'], device=device, tls_cert=tls_cert, tls_key=tls_key,
            **arguments)

    @staticmethod
    def unpack(script):
        """What the server does with the bundle: base64 -d | tar xz."""
        encoded = script.split("<<'SUBSYNQ_BUNDLE_EOF'\n", 1)[1].split('SUBSYNQ_BUNDLE_EOF', 1)[0]
        archive = tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded)), mode='r:gz')
        return {member.name: archive.extractfile(member).read().decode('utf-8')
                for member in archive.getmembers()}, [member.mode for member in archive.getmembers()]

    def test_real_service_sources_fit_under_vasts_script_limit_and_unpack_intact(self):
        for device in ('cpu', 'cuda'):
            script = self.real_script(device)
            # Vast answers "invalid_args" above 16 384 characters (seen live).
            self.assertLess(len(script), cloud_gpu.MAX_ONSTART_CHARS, device)
            self.assertLess(cloud_gpu.MAX_ONSTART_CHARS, 16384)
            files, modes = self.unpack(script)
            self.assertEqual(set(files), {'cloud_cpu_service.py', 'qwen_asr_service.py',
                                          'constraints-qwen.txt', 'constraints-worker.txt', 'bootstrap.sh',
                                          'server.crt', 'server.key'})
            for name in ('cloud_cpu_service.py', 'qwen_asr_service.py'):
                original = Path('/ai', name).read_text(encoding='utf-8')
                # The server gets the same program, compacted: same code, no comments/docstrings.
                self.assertEqual(files[name], cloud_gpu.compact_python(original))
                self.assertLess(len(files[name]), len(original))
                compile(files[name], name, 'exec')
            self.assertIn('class Handler', files['cloud_cpu_service.py'])
            self.assertIn('def child_main', files['qwen_asr_service.py'])
            self.assertEqual(files['server.key'], PEM_KEY)
            self.assertEqual(set(modes), {0o600})
            self.assertIn('tar -xzf /root/bundle.tgz -C /root', script)
            # No readable copy of the sources or the key is left in the script text.
            self.assertNotIn('class Handler', script)
            self.assertNotIn('BEGIN PRIVATE KEY', script)

    def test_gpu_bundle_carries_the_cuda_torch_pin(self):
        files, _ = self.unpack(self.real_script('cuda'))
        self.assertNotIn('torch==2.9.1+cpu', files['constraints-qwen.txt'])
        self.assertIn('torch==2.9.1', files['constraints-qwen.txt'])
        files, _ = self.unpack(self.real_script('cpu'))
        self.assertIn('torch==2.9.1+cpu', files['constraints-qwen.txt'])

    def test_a_script_over_the_limit_is_refused_before_anything_is_rented(self):
        import os
        noise = os.urandom(60000).hex()  # incompressible, so the encoded bundle is huge
        with self.assertRaisesRegex(ValueError, 'över gränsen'):
            self.real_script(whisper_constraints=noise)

    def test_compact_python_keeps_behaviour_and_drops_only_comments_and_docstrings(self):
        source = ('"""Module doc."""\n# a comment\nX = 1  # trailing\n\n\nclass A:\n    """Doc."""\n'
                  '    def f(self):\n        """Doc."""\n        return X + 1\n\n\ndef g():\n    """Only a doc."""\n')
        compact = cloud_gpu.compact_python(source)
        self.assertNotIn('Doc', compact)
        self.assertNotIn('comment', compact)
        namespace = {}
        exec(compile(compact, 'x', 'exec'), namespace)
        self.assertEqual(namespace['A']().f(), 2)
        self.assertIsNone(namespace['g']())
        with self.assertRaises(SyntaxError):
            cloud_gpu.compact_python('not python at all (')

    def test_the_rented_server_keeps_qwen_models_loaded_between_requests(self):
        for device in ('cpu', 'cuda'):
            self.assertIn('SUBSYNQ_QWEN_SEQUENTIAL=0', bootstrap_of(self.real_script(device)))


class PreparedCloudTests(unittest.TestCase):
    def test_optional_prepared_image_validation_rejects_missing_image(self):
        for image in ('', 'ubuntu:24.04', 'registry.test/chain:latest'):
            with patch.dict(cloud_gpu.os.environ, {'SUBSYNQ_CLOUD_IMAGE': image}), \
                    patch.object(worker, 'load_cloud_keys') as keys, \
                    patch.object(cloud_gpu, 'create_chain_instance') as create:
                with self.assertRaisesRegex(RuntimeError, 'Ingen server hyrdes'):
                    cloud_gpu.require_prepared_image()
                keys.assert_not_called()
                create.assert_not_called()

    def test_prepared_image_must_use_an_immutable_digest(self):
        image = 'registry.test/subsynq/cloud@sha256:' + 'a' * 64
        with patch.dict(cloud_gpu.os.environ, {'SUBSYNQ_CLOUD_IMAGE': image}), \
                patch('cloud_image.verify_image', return_value=True):
            self.assertEqual(cloud_gpu.require_prepared_image(), image)

    def test_optional_prepared_image_validation_rejects_unavailable_registry(self):
        image = 'registry.test/subsynq/cloud@sha256:' + 'a' * 64
        with patch.dict(cloud_gpu.os.environ, {'SUBSYNQ_CLOUD_IMAGE': image}), \
                patch('cloud_image.verify_image', side_effect=ConnectionError), \
                patch.object(cloud_gpu, 'create_chain_instance') as create:
            with self.assertRaisesRegex(RuntimeError, 'Ingen server hyrdes'):
                cloud_gpu.require_prepared_image()
            create.assert_not_called()

    def test_normal_rental_does_not_require_prepared_image_or_registry(self):
        with patch.dict(cloud_gpu.os.environ, {'SUBSYNQ_CLOUD_IMAGE': ''}), \
                patch.object(cloud_gpu, 'require_prepared_image', side_effect=AssertionError('spärr anropad')):
            result, error, create, destroy, _ = RentCloudCpuTests().run_rent([{'status': 'ready'}])
        self.assertIsNone(error)
        create.assert_called_once()
        destroy.assert_not_called()
        self.assertEqual(result[1], 555)
        boot = bootstrap_of(create.call_args[0][2])
        self.assertNotIn('/opt/subsynq-prepared', boot)

    def test_prepared_bootstrap_has_no_installations_or_model_downloads(self):
        for device in ('cpu', 'cuda'):
            boot = bootstrap_of(OnstartScriptTests().real_script(device, prepared=True))
            for forbidden in ('apt-get', 'pip install', 'pip_install', 'curl ', 'ollama pull',
                              'snapshot_download', 'download_model'):
                self.assertNotIn(forbidden, boot)
            self.assertIn('HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1', boot)
            self.assertIn('python /ai/cloud_preflight.py', boot)
            self.assertIn('HF_HUB_CACHE=/models/huggingface/hub', boot)
            self.assertIn('HF_HUB_CACHE=/models ', boot)
            result = subprocess.run(['bash', '-n'], input=boot, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class BootstrapPipRetryTests(unittest.TestCase):
    def run_install(self, errors):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pip = root / 'pip'
            state = root / 'attempts'
            failed = root / 'failed'
            slept = root / 'slept'
            # A real Bash execution, with fake pip/sleep only: no downloads.
            pip.write_text('#!/usr/bin/env python3\n'
                           'import json, pathlib, sys\n'
                           f'state = pathlib.Path({str(state)!r})\n'
                           'calls = json.loads(state.read_text()) if state.exists() else []\n'
                           'attempt = len(calls)\n'
                           'calls.append(sys.argv[1:])\n'
                           'state.write_text(json.dumps(calls))\n'
                           f'errors = {errors!r}\n'
                           'error = errors[min(attempt, len(errors)-1)]\n'
                           'if error:\n'
                           '    print(error, file=sys.stderr)\n'
                           '    sys.exit(7)\n')
            pip.chmod(0o700)
            script = ('set -e -o pipefail\n'
                      f'trap \'echo failed > "{failed}"\' ERR\n'
                      f'sleep() {{ echo "$1" >> "{slept}"; }}\n'
                      + cloud_gpu.CHAIN_PIP_INSTALL_SCRIPT
                      + f'pip_install "{pip}" torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128\n'
                      + 'echo NEXT_PHASE\n')
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10)
            return (result, json.loads(state.read_text()), failed.exists(),
                    slept.read_text().splitlines() if slept.exists() else [])

    def test_mid_download_timeout_then_success_keeps_options_and_continues(self):
        result, calls, failed, slept = self.run_install([
            "ReadTimeoutError: HTTPSConnectionPool(host='pypi.nvidia.com'): Read timed out.", ''])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertIn('120', calls[0])
        self.assertIn('/root/.cache/pip', calls[0])
        self.assertNotIn('--no-cache-dir', calls[0])
        self.assertIn('torch==2.9.1', calls[0])
        self.assertEqual(slept, ['15'])
        self.assertIn('NEXT_PHASE', result.stdout)
        self.assertFalse(failed)

    def test_persistent_timeout_stops_after_three_attempts_and_records_failure(self):
        result, calls, failed, slept = self.run_install(['ReadTimeoutError: Read timed out.'])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(calls), 3)
        self.assertEqual(slept, ['15', '30'])
        self.assertTrue(failed)
        self.assertNotIn('NEXT_PHASE', result.stdout)

    def test_dependency_and_certificate_errors_are_not_retried(self):
        for error in ('ERROR: ResolutionImpossible', 'ERROR: No matching distribution found',
                      'SSLError: CERTIFICATE_VERIFY_FAILED'):
            with self.subTest(error=error):
                result, calls, failed, slept = self.run_install([error])
                self.assertEqual(result.returncode, 7)
                self.assertEqual(len(calls), 1)
                self.assertTrue(failed)
                self.assertEqual(slept, [])
                self.assertNotIn('NEXT_PHASE', result.stdout)

    def test_generated_cpu_and_cuda_scripts_use_retry_for_all_three_installations(self):
        for device in ('cpu', 'cuda'):
            boot = bootstrap_of(OnstartScriptTests().build(device=device))
            self.assertIn(cloud_gpu.CHAIN_PIP_INSTALL_SCRIPT, boot)
            # Qwen torch, qwen-asr, faster-whisper and the Hugging Face client.
            self.assertEqual(boot.count('pip_install /root/'), 4)
            self.assertNotIn('--no-cache-dir', boot)
            result = subprocess.run(['bash', '-n'], input=boot, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)



class FakeSegment:
    def __init__(self):
        self.start, self.end, self.text = 1.0, 2.5, ' Hello'
        self.no_speech_prob, self.avg_logprob = 0.1, -0.3
        self.words = [SimpleNamespace(start=1.0, end=1.4, word=' Hello', probability=0.9)]


class FakeWhisper:
    def __init__(self):
        self.calls = []

    def transcribe(self, path, **options):
        self.calls.append((Path(path).read_bytes(), options))
        return [FakeSegment()], SimpleNamespace(language='en')


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(self.server.get_reply.get(self.path)).encode()
        status = 200 if self.server.get_reply.get(self.path) is not None else 404
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.seen.append((self.path, data, self.headers.get('X-SubSynq-Language')))
        body = json.dumps({'status': 'ok'}).encode()
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


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.backend = serve(Backend)
        self.backend.seen = []
        self.backend.get_reply = {}
        self.gateway_port = None
        patches = [patch.object(gateway, 'TOKEN', TOKEN),
                   patch.object(gateway, 'QWEN_PORT', self.backend.server_port),
                   patch.object(gateway, 'OLLAMA_PORT', self.backend.server_port),
                   patch.object(gateway, 'REQUIRED_MODELS', ['gemma3:12b']),
                   patch.object(gateway, 'whisper', None),
                   patch.object(gateway, 'whisper_loaded', None),
                   patch.dict(gateway.whisper_state, {'status': 'ready', 'error': None})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.server = serve(gateway.Handler)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.backend.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data)

    def test_wrong_or_missing_token_gets_404_everywhere(self):
        for path in ('/health', '/wrong/health', f'/{TOKEN}x/health', '/'):
            self.assertEqual(self.request('GET', path)[0], 404, path)
        self.assertEqual(self.request('POST', '/transcribe', b'x')[0], 404)

    def test_health_is_ready_only_when_all_three_backends_answer(self):
        self.backend.get_reply = {'/health': {'status': 'ready'},
                                  '/api/tags': {'models': [{'name': 'gemma3:12b'}]}}
        with patch.object(gateway, 'available_memory_bytes', return_value=32 * 1024 ** 3):
            status, body = self.request('GET', f'/{TOKEN}/health')
            self.assertEqual((status, body['status']), (200, 'ready'))
            self.backend.get_reply = {'/health': {'status': 'ready'},
                                      '/api/tags': {'models': [{'name': 'other:1b'}]}}
            body = self.request('GET', f'/{TOKEN}/health')[1]
            self.assertEqual(body['status'], 'loading')
            self.assertEqual(body['missing_models'], ['gemma3:12b'])
            self.backend.get_reply = {'/api/tags': {'models': [{'name': 'gemma3:12b'}]}}
            body = self.request('GET', f'/{TOKEN}/health')[1]
            self.assertEqual((body['status'], body['qwen']), ('loading', False))

    def test_too_little_memory_is_reported_not_hidden(self):
        self.backend.get_reply = {'/health': {'status': 'ready'},
                                  '/api/tags': {'models': [{'name': 'gemma3:12b'}]}}
        with patch.object(gateway, 'available_memory_bytes', return_value=8 * 1024 ** 3):
            body = self.request('GET', f'/{TOKEN}/health')[1]
        self.assertEqual(body['status'], 'insufficient_memory')

    def test_failed_whisper_load_is_an_error_state(self):
        self.backend.get_reply = {'/health': {'status': 'ready'},
                                  '/api/tags': {'models': [{'name': 'gemma3:12b'}]}}
        with patch.object(gateway, 'available_memory_bytes', return_value=32 * 1024 ** 3), \
                patch.dict(gateway.whisper_state, {'status': 'error', 'error': 'boom'}):
            body = self.request('GET', f'/{TOKEN}/health')[1]
        self.assertEqual((body['status'], body['error']), ('error', 'boom'))

    def test_qwen_and_ollama_requests_are_forwarded(self):
        status, _ = self.request('POST', f'/{TOKEN}/qwen/identify', b'wav',
                                 {'X-SubSynq-Language': 'sv', 'Content-Type': 'audio/wav'})
        self.assertEqual(status, 200)
        self.request('POST', f'/{TOKEN}/ollama/api/generate', b'{"a":1}',
                     {'Content-Type': 'application/json'})
        self.assertEqual(self.backend.seen, [('/identify', b'wav', 'sv'),
                                             ('/api/generate', b'{"a":1}', None)])

    def test_transcribe_runs_whisper_with_validated_options(self):
        fake = FakeWhisper()
        options = {'language': 'sv', 'beam_size': 5, 'word_timestamps': True, 'temperature': 0}
        with patch.object(gateway, 'activate_whisper', return_value=fake) as activate:
            status, body = self.request(
                'POST', f'/{TOKEN}/transcribe', b'RIFFwav',
                {'X-SubSynq-Options': json.dumps(options), 'X-SubSynq-Whisper-Model': 'large-v2'})
        self.assertEqual(status, 200)
        activate.assert_called_once_with('large-v2')
        self.assertEqual(fake.calls, [(b'RIFFwav', options)])
        self.assertEqual(body['language'], 'en')
        self.assertEqual(body['segments'][0]['words'][0]['probability'], 0.9)
        self.assertEqual(body['segments'][0]['no_speech_prob'], 0.1)

    def test_transcribe_refuses_unknown_options_and_models(self):
        with patch.object(gateway, 'activate_whisper', return_value=FakeWhisper()) as activate:
            for headers in ({'X-SubSynq-Options': json.dumps({'task': 'translate'})},
                            {'X-SubSynq-Options': json.dumps({'beam_size': 'five'})},
                            {'X-SubSynq-Options': json.dumps({'beam_size': True})},
                            {'X-SubSynq-Options': '[1]'},
                            {'X-SubSynq-Whisper-Model': 'large-v1'}):
                status, _ = self.request('POST', f'/{TOKEN}/transcribe', b'wav', headers)
                self.assertEqual(status, 400, headers)
        activate.assert_not_called()

    def test_transcribe_says_so_while_whisper_is_not_ready(self):
        with patch.dict(gateway.whisper_state, {'status': 'loading', 'error': None}):
            status, _ = self.request('POST', f'/{TOKEN}/transcribe', b'wav')
        self.assertEqual(status, 503)

    def monitored(self, samples):
        """A VramMonitor fed from a list (one reading per observe), installed as the gateway's."""
        feed = iter(samples)
        monitor = gateway.VramMonitor(sampler=lambda: next(feed, samples[-1]), interval=60)
        patcher = patch.object(gateway, 'VRAM', monitor)
        patcher.start()
        self.addCleanup(patcher.stop)
        return monitor

    def test_vram_endpoint_reports_peaks_and_is_disabled_without_a_gpu_monitor(self):
        status, body = self.request('GET', f'/{TOKEN}/vram')
        self.assertEqual((status, body), (200, {'enabled': False}))
        monitor = self.monitored([(3100, 24564, [])])
        monitor.observe()
        status, body = self.request('GET', f'/{TOKEN}/vram')
        self.assertEqual((status, body['enabled'], body['total_mib']), (200, True, 24564))
        self.assertEqual(body['phases']['idle']['peak_mib'], 3100)

    def test_each_request_type_is_measured_as_its_own_phase(self):
        monitor = self.monitored([(3000, 24564, []), (9000, 24564, []), (5500, 24564, []),
                                  (6000, 24564, []), (9900, 24564, [])])
        with patch.object(gateway, 'activate_whisper', return_value=FakeWhisper()):
            self.request('POST', f'/{TOKEN}/transcribe', b'RIFFwav',
                         {'X-SubSynq-Whisper-Model': 'large-v2'})
        self.request('POST', f'/{TOKEN}/qwen/transcribe', b'wav')
        self.request('POST', f'/{TOKEN}/ollama/api/generate',
                     json.dumps({'model': 'gemma4:12b', 'prompt': 'x'}).encode())
        self.request('POST', f'/{TOKEN}/ollama/api/generate',
                     json.dumps({'model': 'gemma4:12b', 'keep_alive': 0}).encode())
        phases = monitor.report()['phases']
        self.assertEqual(set(phases), {'whisper-large-v2', 'qwen-alignment', 'ollama-gemma4:12b'})
        self.assertEqual({name: entry['requests'] for name, entry in phases.items()},
                         {'whisper-large-v2': 1, 'qwen-alignment': 1, 'ollama-gemma4:12b': 1})
        self.assertGreater(phases['ollama-gemma4:12b']['peak_mib'], 0)

    def test_only_one_whisper_model_stays_resident(self):
        loaded = []

        class Model:
            def __init__(self, name, **_kwargs):
                loaded.append(name)

        fake_module = SimpleNamespace(WhisperModel=Model)
        with patch.dict(sys.modules, {'faster_whisper': fake_module}):
            first = gateway.activate_whisper('large-v3')
            self.assertIs(gateway.activate_whisper('large-v3'), first)
            gateway.activate_whisper('large-v2')
            gateway.activate_whisper('large-v3')
        self.assertEqual(loaded, ['large-v3', 'large-v2', 'large-v3'])


class RemoteCpuWhisperTests(unittest.TestCase):
    def response(self, payload):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return json.dumps(payload).encode()

        return Response()

    def test_options_and_model_are_sent_and_all_fields_come_back(self):
        model = worker.RemoteCloudWhisperModel('http://cloud:1/tok', 'moln-CPU').with_model('large-v2')
        payload = {'status': 'ok', 'language': 'sv', 'segments': [{
            'start': 0, 'end': 1, 'text': 'Hej', 'no_speech_prob': .2, 'avg_logprob': -.4,
            'words': [{'start': 0.0, 'end': .5, 'word': 'Hej', 'probability': .8}]}]}
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / 'a.wav'
            clip.write_bytes(b'wav')
            with patch.object(worker.urllib.request, 'urlopen',
                              return_value=self.response(payload)) as opened:
                segments, info = model.transcribe(
                    str(clip), language='sv', beam_size=5, word_timestamps=True)
        request = opened.call_args[0][0]
        self.assertEqual(request.full_url, 'http://cloud:1/tok/transcribe')
        self.assertEqual(request.get_header('X-subsynq-whisper-model'), 'large-v2')
        self.assertEqual(json.loads(request.get_header('X-subsynq-options')),
                         {'language': 'sv', 'beam_size': 5, 'word_timestamps': True})
        self.assertEqual(info.language, 'sv')
        self.assertEqual((segments[0].no_speech_prob, segments[0].avg_logprob), (.2, -.4))
        self.assertEqual(segments[0].words[0].probability, .8)
        self.assertFalse(hasattr(model, 'model'))

    def test_error_reply_becomes_a_technical_error(self):
        model = worker.RemoteCloudWhisperModel('http://cloud:1/tok', 'moln-CPU')
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / 'a.wav'
            clip.write_bytes(b'wav')
            with patch.object(worker.urllib.request, 'urlopen',
                              return_value=self.response({'status': 'error'})):
                with self.assertRaisesRegex(RuntimeError, 'ogiltigt svar'):
                    model.transcribe(str(clip))

    def test_extra_whisper_readers_never_load_local_weights(self):
        remote = worker.RemoteCloudWhisperModel('http://cloud:1/tok', 'moln-CPU')
        models = worker.SequentialASRModels(remote)
        with patch.object(worker, 'WhisperModel') as local, \
                patch.object(worker, 'qwen_unload'):
            reader = models.create_whisper('large-v2', device='cpu', compute_type='int8')
        local.assert_not_called()
        self.assertEqual(reader.whisper_model, 'large-v2')
        self.assertEqual(reader.base_url, 'http://cloud:1/tok')

    def test_endpoints_point_qwen_and_gemma_at_the_server_only_during_the_job(self):
        before = worker.TRANSLATE_URL, worker.QWEN_ASR_URL
        with worker.cloud_chain_endpoints('https://cloud:1/tok', worker.ssl.create_default_context()):
            self.assertEqual(worker.TRANSLATE_URL, 'https://cloud:1/tok/ollama/api/generate')
            self.assertEqual(worker.QWEN_ASR_URL, 'https://cloud:1/tok/qwen')
        self.assertEqual((worker.TRANSLATE_URL, worker.QWEN_ASR_URL), before)
        with self.assertRaises(RuntimeError):
            with worker.cloud_chain_endpoints('https://cloud:1/tok', worker.ssl.create_default_context()):
                raise RuntimeError('x')
        self.assertEqual((worker.TRANSLATE_URL, worker.QWEN_ASR_URL), before)

    def test_cloud_cpu_never_selects_the_local_gpu(self):
        self.assertEqual(worker.select_job_hardware({'id': 1}, 'cloud-cpu-vast'), (None, False))
        self.assertTrue(worker.is_cloud_cpu('cloud-cpu-vast'))
        self.assertFalse(worker.is_cloud_cpu('cloud-vast'))


class RentCloudCpuTests(unittest.TestCase):
    runpod_cost = 0.16

    def run_rent(self, health, *, offers=None, keys=None, mode='cpu', provider='vast', create_effect=None):
        with tempfile.TemporaryDirectory() as directory:
            database = make_database(directory, [(1, '/tv/a.mkv', 'a.mkv', 'transcribing', None)])
            replies = iter(health)
            self.stored_ids = []
            real_update = worker.update_job

            def spy(job_id, *args, **kwargs):
                if 'cloud_instance_id' in kwargs:
                    self.stored_ids.append(kwargs['cloud_instance_id'])
                return real_update(job_id, *args, **kwargs)


            def urlopen(request, timeout=0):
                reply = next(replies)
                if isinstance(reply, Exception):
                    raise reply
                return RentCloudCpuTests.Response(reply)

            offers = [{'id': '77', 'gpu': 'CPU', 'price': .011, 'detail': '64 kärnor'}] \
                if offers is None else offers
            with patch.object(worker, 'DATABASE', database), \
                    patch.object(worker, 'load_cloud_keys',
                                 return_value={'vast': 'secret', 'runpod': 'secret'} if keys is None else keys), \
                    patch.object(worker, 'fetch_runpod_chain_offers', return_value=offers), \
                    patch.object(worker.cloud_gpu, 'runpod_create_pod',
                                 **({'side_effect': create_effect} if create_effect
                                    else {'return_value': ('abc123', self.runpod_cost)})) as runpod_create, \
                    patch.object(worker.cloud_gpu, 'runpod_show_pod', return_value={}), \
                    patch.object(worker.cloud_gpu, 'runpod_service_url', return_value='http://cloud:41000'), \
                    patch.object(worker.cloud_gpu, 'runpod_destroy_pod') as runpod_destroy, \
                    patch.object(worker, 'update_job', side_effect=spy), \
                    patch.object(worker, 'fetch_cpu_offers', return_value=offers), \
                    patch.object(worker, 'fetch_vast_chain_gpu_offers', return_value=offers) as gpu_offers, \
                    patch.object(worker, 'load_cloud_profile', return_value={'gpu': 'RTX 5060 Ti'}), \
                    patch.object(worker.cloud_gpu, 'create_instance',
                                 **({'side_effect': create_effect} if create_effect
                                    else {'return_value': 555})) as create, \
                    patch.object(worker.cloud_gpu, 'show_instance', return_value={}), \
                    patch.object(worker.cloud_gpu, 'public_service_url', return_value='http://cloud:41000'), \
                    patch.object(worker.cloud_gpu, 'destroy_instance') as destroy, \
                    patch.object(worker.time, 'sleep'), \
                    patch.object(worker.urllib.request, 'urlopen', side_effect=urlopen):
                try:
                    result = worker.rent_cloud_chain({'id': 1}, provider, mode)
                    error = None
                except Exception as exc:
                    result, error = None, exc
                with sqlite3.connect(database) as db:
                    remaining = db.execute('SELECT cloud_instance_id FROM subtitle_jobs').fetchone()[0]
            self.gpu_offers = gpu_offers
            worker.reset_opener()
            if provider == 'runpod':
                create, destroy = runpod_create, runpod_destroy
            return result, error, create, destroy, remaining

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return json.dumps(self.payload).encode()

    def test_only_vast_and_a_saved_key_are_accepted(self):
        with self.assertRaisesRegex(RuntimeError, 'bara hos Vast.ai och Runpod.io'):
            worker.rent_cloud_chain({'id': 1}, 'someone-else', 'cpu')
        _, error, create, _, _ = self.run_rent([], keys={})
        self.assertRegex(str(error), 'API-nyckel')
        create.assert_not_called()

    def test_no_offer_means_nothing_is_rented(self):
        _, error, create, _, _ = self.run_rent([], offers=[])
        self.assertRegex(str(error), 'Inga lediga')
        create.assert_not_called()

    def test_ready_server_returns_a_token_protected_remote_model(self):
        result, error, create, destroy, _ = self.run_rent([
            worker.urllib.error.URLError('connection refused'), {'status': 'loading', 'whisper': 'ready', 'qwen': False,
                                            'ollama': False, 'missing_models': ['gemma3:12b']},
            {'status': 'ready'}])
        self.assertIsNone(error)
        model, instance_id, base_url, context = result
        self.assertEqual(instance_id, 555)
        self.assertTrue(base_url.startswith('https://cloud:41000/'))
        self.assertEqual(context.verify_mode, worker.ssl.CERT_REQUIRED)
        self.assertFalse(context.check_hostname)
        self.assertEqual(model.base_url, base_url)
        self.assertEqual(create.call_args.kwargs['image'], cloud_gpu.CPU_IMAGE)
        self.assertEqual(create.call_args.kwargs['disk_gb'], cloud_gpu.CPU_DISK_GB)
        self.assertIn(base_url.rsplit('/', 1)[1], bootstrap_of(create.call_args[0][2]))
        destroy.assert_not_called()

    def test_too_little_memory_destroys_the_instance_and_stops(self):
        result, error, _, destroy, remaining = self.run_rent([
            {'status': 'insufficient_memory', 'ram_bytes': 8 * 1024 ** 3,
             'min_ram_bytes': 16 * 1024 ** 3}])
        self.assertIsNone(result)
        self.assertRegex(str(error), 'för lite minne \\(8 GiB, kräver 16 GiB\\)')
        destroy.assert_called_once_with('secret', 555)
        self.assertIsNone(remaining)

    def test_a_broken_whisper_load_destroys_the_instance_and_stops(self):
        _, error, _, destroy, remaining = self.run_rent([{'status': 'error', 'error': 'boom'}])
        self.assertRegex(str(error), 'boom')
        destroy.assert_called_once()
        self.assertIsNone(remaining)

    def test_never_ready_destroys_the_instance_without_a_local_fallback(self):
        with patch.object(worker, 'CLOUD_CHAIN_SPINUP_SECONDS', 0), \
                patch.object(worker, 'fetch_vast_container_log', return_value=None):
            _, error, _, destroy, _ = self.run_rent([])
        self.assertRegex(str(error), 'aldrig nåbar')
        destroy.assert_called_once()

    def test_missing_address_records_last_status_and_reads_logs_before_destroying(self):
        import itertools
        events = []
        ticks = itertools.count(0, 5)
        state = {'actual_status': 'running', 'public_ipaddr': 'private-address', 'ports': {}}
        with patch.object(worker.time, 'monotonic', side_effect=lambda: next(ticks)), \
                patch.object(worker, 'CLOUD_CHAIN_ADDRESS_SECONDS', 20), \
                patch.object(worker.cloud_gpu, 'chain_service_url', return_value=None), \
                patch.object(worker.cloud_gpu, 'show_chain_instance', return_value=state), \
                patch.object(worker, 'fetch_vast_container_log',
                             side_effect=lambda *args: events.append('log') or 'container failed to start'), \
                patch.object(worker, 'destroy_cloud_gpu', side_effect=lambda *args: events.append('destroy')):
            _, error, _, _, remaining = self.run_rent([], mode='cuda')
        self.assertIn('status=running', str(error))
        self.assertIn('publik IP=finns', str(error))
        self.assertIn('tjänsteport=saknas', str(error))
        self.assertIn('container failed to start', str(error))
        self.assertNotIn('private-address', str(error))
        self.assertEqual(events, ['log', 'destroy'])
        self.assertIsNone(remaining)

    def test_address_diagnostics_exclude_free_text_and_support_both_providers(self):
        self.assertEqual(worker.cloud_address_status('vast', None),
                         'status=okänd, publik IP=saknas, tjänsteport=saknas')
        state = {'actual_status': 'secret-api-value', 'status_msg': 'private-text',
                 'public_ipaddr': 'private-address', 'ports': {f'{cloud_gpu.SERVICE_PORT}/tcp': []}}
        summary = worker.cloud_address_status('vast', state)
        self.assertEqual(summary, 'status=okänd, publik IP=finns, tjänsteport=saknas')
        state['ports'][f'{cloud_gpu.SERVICE_PORT}/tcp'] = [{'HostPort': '444'}]
        self.assertIn('tjänsteport=finns', worker.cloud_address_status('vast', state))
        self.assertEqual(worker.cloud_address_status('runpod', {'desiredStatus': 'RUNNING',
                         'publicIp': 'private-address', 'portMappings': {str(cloud_gpu.SERVICE_PORT): 444}}),
                         'status=running, publik IP=finns, tjänsteport=finns')

    def test_health_message_names_what_is_still_missing(self):
        message = worker.cloud_health_message({'whisper': 'loading', 'qwen': True, 'ollama': False,
                                               'missing_models': ['gemma4:12b']})
        self.assertIn('Whisper', message)
        self.assertIn('gemma4:12b', message)
        self.assertNotIn('Qwen', message)



class CloudGpuChainTests(unittest.TestCase):
    """cloud-<provider> runs the same whole chain, on the rented server's GPU."""

    def test_gpu_script_uses_cuda_wheels_float16_and_the_cuda_image(self):
        script = OnstartScriptTests().build(device='cuda')
        self.assertIn('--index-url https://download.pytorch.org/whl/cu128', bootstrap_of(script))
        boot = bootstrap_of(script)
        self.assertIn('SUBSYNQ_QWEN_DEVICE=cuda SUBSYNQ_QWEN_DTYPE=float16', boot)
        self.assertIn('SUBSYNQ_CLOUD_DEVICE=cuda', boot)
        self.assertEqual(cloud_gpu.chain_image('cuda'), cloud_gpu.GPU_CHAIN_IMAGE)
        self.assertEqual(cloud_gpu.chain_image('cpu'), cloud_gpu.CPU_IMAGE)
        cpu = OnstartScriptTests().build(device='cpu')
        self.assertIn('whl/cpu', bootstrap_of(cpu))
        self.assertIn('SUBSYNQ_CLOUD_DEVICE=cpu', bootstrap_of(cpu))

    def test_script_rejects_an_unknown_device_and_missing_torch_pin(self):
        with self.assertRaises(ValueError):
            OnstartScriptTests().build(device='tpu')
        with self.assertRaises(ValueError):
            cloud_gpu.build_chain_onstart_script(
                TOKEN, ['gemma3:12b'], device='cuda', gateway_source='g', qwen_source='q',
                tls_cert=PEM_CERT, tls_key=PEM_KEY,
                qwen_constraints='numpy==2', whisper_constraints='av==18')

    def test_gateway_stops_when_the_gpu_is_missing_or_too_small(self):
        with patch.object(gateway, 'DEVICE', 'cuda'), \
                patch.object(gateway, 'available_memory_bytes', return_value=32 * 1024 ** 3), \
                patch.object(gateway, 'local_json', return_value=None):
            with patch.object(gateway, 'gpu_info', return_value=None):
                self.assertEqual(gateway.health_report()['status'], 'no_gpu')
            with patch.object(gateway, 'gpu_info', return_value=('RTX 3060', 12 * 1024 ** 3)):
                report = gateway.health_report()
                self.assertEqual(report['status'], 'insufficient_vram')
                self.assertEqual(report['gpu_name'], 'RTX 3060')

    def test_gateway_whisper_uses_cuda_float16_only_on_gpu(self):
        with patch.object(gateway, 'DEVICE', 'cuda'):
            self.assertEqual(gateway.whisper_arguments(), {'device': 'cuda', 'compute_type': 'float16'})
        with patch.object(gateway, 'DEVICE', 'cpu'):
            self.assertEqual(gateway.whisper_arguments()['compute_type'], 'int8')

    def test_gateway_health_is_ready_on_a_big_enough_gpu(self):
        def local(port, path, timeout=5):
            return {'status': 'ready'} if path == '/health' else {'models': [{'name': 'gemma3:12b'}]}

        with patch.object(gateway, 'DEVICE', 'cuda'), \
                patch.object(gateway, 'REQUIRED_MODELS', ['gemma3:12b']), \
                patch.object(gateway, 'available_memory_bytes', return_value=16 * 1024 ** 3), \
                patch.object(gateway, 'local_json', side_effect=local), \
                patch.dict(gateway.whisper_state, {'status': 'ready', 'error': None}), \
                patch.object(gateway, 'gpu_info', return_value=('RTX 5060 Ti', 16 * 1024 ** 3)):
            self.assertEqual(gateway.health_report()['status'], 'ready')

    def test_nvidia_smi_output_is_parsed(self):
        result = SimpleNamespace(stdout='NVIDIA GeForce RTX 5060 Ti, 16311\n')
        with patch.object(gateway.subprocess, 'run', return_value=result):
            self.assertEqual(gateway.gpu_info(), ('NVIDIA GeForce RTX 5060 Ti', 16311 * 1024 ** 2))
        with patch.object(gateway.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertIsNone(gateway.gpu_info())

    def test_gpu_rental_searches_the_chosen_gpu_and_uses_the_cuda_image(self):
        suite = RentCloudCpuTests()
        result, error, create, destroy, _ = suite.run_rent(
            [{'status': 'ready'}], offers=[{'id': '88', 'gpu': 'RTX 5060 Ti', 'price': .12,
                                            'detail': '16 GB VRAM'}], mode='cuda')
        self.assertIsNone(error)
        self.assertEqual(create.call_args.kwargs['image'], cloud_gpu.GPU_CHAIN_IMAGE)
        self.assertEqual(suite.gpu_offers.call_args.args[1], 'RTX 5060 Ti')
        self.assertIn('Moln-GPU-tjänsten', result[0].label)
        self.assertIn('SUBSYNQ_CLOUD_DEVICE=cuda', bootstrap_of(create.call_args[0][2]))
        destroy.assert_not_called()

    def test_missing_gpu_and_too_little_vram_destroy_the_instance(self):
        for status, expected in (({'status': 'no_gpu'}, 'ingen NVIDIA-GPU'),
                                 ({'status': 'insufficient_vram', 'vram_bytes': 12 * 1024 ** 3,
                                   'min_vram_bytes': 15 * 1024 ** 3}, 'för lite GPU-minne \\(12 GiB')):
            _, error, _, destroy, remaining = RentCloudCpuTests().run_rent([status], mode='cuda')
            self.assertRegex(str(error), expected)
            destroy.assert_called_once()
            self.assertIsNone(remaining)

    def test_unsupported_provider_and_mode_are_refused(self):
        with self.assertRaises(ValueError):
            worker.rent_cloud_chain({'id': 1}, 'vast', 'tpu')
        with self.assertRaisesRegex(RuntimeError, 'Moln-GPU stöds bara hos Vast.ai och Runpod.io'):
            worker.rent_cloud_chain({'id': 1}, 'someone-else', 'cuda')


class TlsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(worker.reset_opener)
        self.cert, self.key = worker.make_chain_certificate()
        self.other_cert, _ = worker.make_chain_certificate()
        directory = Path(self.folder.name)
        (directory / 'c.pem').write_text(self.cert)
        (directory / 'k.pem').write_text(self.key)
        self.server = gateway.build_server(('127.0.0.1', 0), str(directory / 'c.pem'),
                                           str(directory / 'k.pem'))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        patcher = patch.object(gateway, 'TOKEN', TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.port = self.server.server_port

    def get(self, context, path='/wrong/health'):
        worker.install_tls_opener(context)
        try:
            worker.urllib.request.urlopen(f'https://127.0.0.1:{self.port}{path}', timeout=10)
        except worker.urllib.error.HTTPError as error:
            return error.code
        return 200

    def test_certificate_is_a_pem_pair_and_unique_per_rental(self):
        self.assertIn('BEGIN CERTIFICATE', self.cert)
        self.assertIn('BEGIN PRIVATE KEY', self.key)
        self.assertNotEqual(self.cert, self.other_cert)

    def test_pinned_certificate_connects_and_reaches_the_token_check(self):
        self.assertEqual(self.get(worker.pinned_tls_context(self.cert)), 404)

    def test_worker_qwen_health_reaches_backend_over_pinned_https(self):
        backend = serve(Backend)
        backend.get_reply = {'/health': {'status': 'ready', 'device': 'cuda'}}
        self.addCleanup(backend.server_close)
        self.addCleanup(backend.shutdown)
        with patch.object(gateway, 'QWEN_PORT', backend.server_port), \
                worker.cloud_chain_endpoints(f'https://127.0.0.1:{self.port}/{TOKEN}',
                                             worker.pinned_tls_context(self.cert)):
            self.assertEqual(worker.qwen_health()['device'], 'cuda')
            worker.wait_for_qwen(attempts=1)

    def test_qwen_health_failure_still_stops_worker(self):
        backend = serve(Backend)
        backend.get_reply = {'/health': {'status': 'error'}}
        self.addCleanup(backend.server_close)
        self.addCleanup(backend.shutdown)
        with patch.object(gateway, 'QWEN_PORT', backend.server_port), \
                worker.cloud_chain_endpoints(f'https://127.0.0.1:{self.port}/{TOKEN}',
                                             worker.pinned_tls_context(self.cert)):
            with self.assertRaisesRegex(RuntimeError, 'Qwen3-ASR blev inte redo'):
                worker.wait_for_qwen(attempts=1)

    def test_qwen_health_with_wrong_token_does_not_contact_backend(self):
        with patch.object(gateway.Handler, 'forward') as forward:
            self.assertEqual(self.get(worker.pinned_tls_context(self.cert), '/wrong/qwen/health'), 404)
            forward.assert_not_called()

    def test_model_description_reports_cloud_backend_for_both_extra_readers(self):
        with patch.object(worker, 'QWEN_ASR_ENABLED', True), \
                patch.object(worker, 'V2_FRAGMENT_ENABLED', True):
            for device, backend in (('cloud-vast', 'moln-GPU'), ('cloud-runpod', 'moln-GPU'),
                                    ('cloud-cpu-vast', 'moln-CPU'), ('cpu', 'CPU')):
                description = worker.asr_model_description(device)
                self.assertEqual(description.count(f'({backend})'), 2)
                if backend != 'CPU':
                    self.assertNotIn('(CPU)', description)

    def test_a_different_certificate_is_refused(self):
        with self.assertRaises(worker.urllib.error.URLError) as caught:
            self.get(worker.pinned_tls_context(self.other_cert))
        self.assertIsInstance(caught.exception.reason, worker.ssl.SSLCertVerificationError)

    def test_system_trusted_context_does_not_accept_the_self_signed_certificate(self):
        with self.assertRaises(worker.urllib.error.URLError):
            self.get(worker.ssl.create_default_context())

    def test_plain_http_to_the_gateway_gets_no_answer(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        with self.assertRaises((http.client.HTTPException, OSError)):
            connection.request('GET', f'/{TOKEN}/health')
            connection.getresponse().read()
        connection.close()

    def test_local_http_still_works_while_the_tls_opener_is_installed(self):
        backend = serve(Backend)
        backend.seen, backend.get_reply = [], {'/x': {'ok': 1}}
        self.addCleanup(backend.shutdown)
        worker.install_tls_opener(worker.pinned_tls_context(self.cert))
        with worker.urllib.request.urlopen(f'http://127.0.0.1:{backend.server_port}/x', timeout=5) as r:
            self.assertEqual(json.load(r), {'ok': 1})

    def test_endpoints_install_and_remove_the_pinned_opener(self):
        context = worker.pinned_tls_context(self.cert)
        with worker.cloud_chain_endpoints(f'https://127.0.0.1:{self.port}/{TOKEN}', context):
            self.assertEqual(worker.TRANSLATE_URL,
                             f'https://127.0.0.1:{self.port}/{TOKEN}/ollama/api/generate')
            with self.assertRaises(worker.urllib.error.HTTPError) as caught:
                worker.urllib.request.urlopen(f'https://127.0.0.1:{self.port}/nope', timeout=10)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(worker.urllib.error.URLError):
            worker.urllib.request.urlopen(f'https://127.0.0.1:{self.port}/nope', timeout=10)

    def test_a_certificate_mismatch_stops_the_rental_and_destroys_the_instance(self):
        error = worker.urllib.error.URLError(worker.ssl.SSLCertVerificationError('bad'))
        _, caught, _, destroy, remaining = RentCloudCpuTests().run_rent([error])
        self.assertRegex(str(caught), 'certifikat stämmer inte')
        destroy.assert_called_once()
        self.assertIsNone(remaining)

    def test_the_gateway_never_starts_without_a_certificate(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, '/ai/cloud_cpu_service.py'], capture_output=True, text=True, timeout=30,
            env={'SUBSYNQ_CLOUD_TOKEN': TOKEN, 'PATH': '/usr/local/bin:/usr/bin'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('utan kryptering', result.stderr)


def runpod_gpu(**changes):
    gpu = {'id': 'NVIDIA RTX A5000', 'displayName': 'RTX A5000', 'memoryInGb': 24,
           'secureCloud': True, 'lowestPrice': {'stockStatus': 'Low', 'uninterruptablePrice': 0.16,
                                                'availableGpuCounts': [1, 2]}}
    gpu.update(changes)
    return gpu


class RunpodOfferTests(unittest.TestCase):
    def test_cheapest_in_stock_secure_nvidia_card_with_enough_vram_comes_first(self):
        payload = {'data': {'gpuTypes': [
            runpod_gpu(id='NVIDIA GeForce RTX 3090', displayName='RTX 3090',
                       lowestPrice={'stockStatus': 'Low', 'uninterruptablePrice': 0.22}),
            runpod_gpu(),
            runpod_gpu(id='AMD Instinct MI300X OAM', memoryInGb=192),
            runpod_gpu(id='NVIDIA GeForce RTX 4080', memoryInGb=12),
            runpod_gpu(id='NVIDIA L4', secureCloud=False),
            runpod_gpu(id='NVIDIA A40', lowestPrice={'stockStatus': 'None', 'uninterruptablePrice': 0.3}),
            runpod_gpu(id='NVIDIA L40', lowestPrice={'stockStatus': 'Low', 'uninterruptablePrice': 0.4,
                                                     'availableGpuCounts': [2, 4]}),
        ]}}
        result = cloud_market.normalize_runpod_chain_gpus(payload)
        self.assertEqual([item['id'] for item in result],
                         ['NVIDIA RTX A5000', 'NVIDIA GeForce RTX 3090'])
        self.assertIn('24 GB VRAM', result[0]['detail'])

    def test_bad_payload_and_modes(self):
        with self.assertRaises(ValueError):
            cloud_market.normalize_runpod_chain_gpus({'errors': [{'message': 'x'}]})
        with self.assertRaises(ValueError):
            cloud_market.fetch_runpod_chain_offers('key', 'tpu')

    def test_cpu_offer_is_a_fixed_flavor_and_gpu_offer_queries_with_a_bearer_key(self):
        cpu = cloud_market.fetch_runpod_chain_offers('key', 'cpu')
        self.assertEqual((cpu[0]['id'], cpu[0]['price']), (cloud_market.RUNPOD_CPU_FLAVOR, None))
        seen = []

        def reader(request):
            seen.append((request.full_url, request.get_header('Authorization')))
            return {'data': {'gpuTypes': [runpod_gpu()]}}

        gpu = cloud_market.fetch_runpod_chain_offers('key', 'cuda', reader=reader)
        self.assertEqual(seen, [('https://api.runpod.io/graphql', 'Bearer key')])
        self.assertEqual(gpu[0]['id'], 'NVIDIA RTX A5000')


class RunpodApiTests(unittest.TestCase):
    def create(self, device, offer):
        with patch.object(cloud_gpu, '_runpod_request',
                          return_value={'id': 'pod1', 'costPerHr': 0.16}) as call:
            result = cloud_gpu.runpod_create_pod('key', offer, '#!/bin/bash\necho hi\n', device=device)
        return result, call.call_args

    def test_gpu_pod_request(self):
        result, call = self.create('cuda', 'NVIDIA RTX A5000')
        self.assertEqual(result, ('pod1', 0.16))
        method, path, key, body = call[0]
        self.assertEqual((method, path, key), ('POST', '/pods', 'key'))
        self.assertEqual(body['computeType'], 'GPU')
        self.assertEqual(body['gpuTypeIds'], ['NVIDIA RTX A5000'])
        self.assertEqual(body['imageName'], cloud_gpu.GPU_CHAIN_IMAGE)
        self.assertEqual(body['cloudType'], 'SECURE')
        self.assertTrue(body['supportPublicIp'])
        self.assertEqual(body['ports'], ['8766/tcp'])
        self.assertEqual(body['volumeInGb'], 0)
        self.assertEqual(body['allowedCudaVersions'], cloud_gpu.RUNPOD_CUDA_VERSIONS)
        # The pod's main process must outlive the boot script.
        self.assertEqual(body['dockerStartCmd'][:2], ['bash', '-c'])
        self.assertTrue(body['dockerStartCmd'][2].endswith(cloud_gpu.RUNPOD_KEEPALIVE))

    def test_cpu_pod_request(self):
        _, call = self.create('cpu', 'cpu3m')
        body = call[0][3]
        self.assertEqual((body['computeType'], body['cpuFlavorIds'], body['vcpuCount']),
                         ('CPU', ['cpu3m'], cloud_gpu.RUNPOD_CPU_VCPUS))
        self.assertEqual(body['imageName'], cloud_gpu.CPU_IMAGE)
        self.assertNotIn('gpuTypeIds', body)

    def test_a_reply_without_a_pod_id_is_an_error_and_unknown_devices_are_refused(self):
        with patch.object(cloud_gpu, '_runpod_request', return_value={'error': 'no'}):
            with self.assertRaisesRegex(RuntimeError, 'avvisade'):
                cloud_gpu.runpod_create_pod('key', 'x', 's', device='cpu')
        with self.assertRaises(ValueError):
            cloud_gpu.runpod_create_pod('key', 'x', 's', device='tpu')

    def test_service_url_needs_a_running_pod_a_public_ip_and_a_mapped_port(self):
        pod = {'desiredStatus': 'RUNNING', 'publicIp': '1.2.3.4', 'portMappings': {'8766': 40001}}
        self.assertEqual(cloud_gpu.runpod_service_url(pod), 'http://1.2.3.4:40001')
        for broken in ({**pod, 'desiredStatus': 'EXITED'}, {**pod, 'publicIp': ''},
                       {**pod, 'portMappings': {'22': 1}}, {**pod, 'portMappings': None}, None, {}):
            self.assertIsNone(cloud_gpu.runpod_service_url(broken))

    def test_facade_routes_by_provider_and_destroy_uses_the_delete_endpoint(self):
        with patch.object(cloud_gpu, '_runpod_request', return_value={}) as call:
            cloud_gpu.destroy_chain_instance('runpod', 'key', 'pod1')
            cloud_gpu.show_chain_instance('runpod', 'key', 'pod1')
        self.assertEqual([c[0][:2] for c in call.call_args_list],
                         [('DELETE', '/pods/pod1'), ('GET', '/pods/pod1')])
        with patch.object(cloud_gpu, 'destroy_instance') as vast:
            cloud_gpu.destroy_chain_instance('vast', 'key', 7)
        vast.assert_called_once_with('key', 7)
        for function in (cloud_gpu.show_chain_instance, cloud_gpu.destroy_chain_instance):
            with self.assertRaises(ValueError):
                function('nobody', 'key', 1)
        with self.assertRaises(ValueError):
            cloud_gpu.chain_service_url('nobody', {})

    def test_stored_ids_keep_vast_numbers_and_prefix_runpod(self):
        self.assertEqual(cloud_gpu.stored_instance_id('vast', 555), 555)
        self.assertEqual(cloud_gpu.stored_instance_id('runpod', 'pod1'), 'runpod:pod1')
        self.assertEqual(cloud_gpu.split_stored_instance_id('runpod:pod1'), ('runpod', 'pod1'))
        self.assertEqual(cloud_gpu.split_stored_instance_id(555), ('vast', 555))


class RefusedOrderTests(unittest.TestCase):
    """Thin stock: a refused order is retried with the next cheapest offer; nothing is billed."""

    OFFERS = [{'id': f'GPU-{n}', 'gpu': f'GPU {n}', 'price': .1 + n / 100, 'detail': ''} for n in range(6)]

    def test_the_next_cheapest_offer_is_tried_when_the_first_is_refused(self):
        suite = RentCloudCpuTests()
        result, error, create, destroy, _ = suite.run_rent(
            [{'status': 'ready'}], provider='runpod', mode='cuda', offers=self.OFFERS,
            create_effect=[RuntimeError('Runpod svarade med fel 500: no instances available'),
                           RuntimeError('capacity'), ('podX', 0.16)])
        self.assertIsNone(error)
        self.assertEqual([call.args[1] for call in create.call_args_list], ['GPU-0', 'GPU-1', 'GPU-2'])
        self.assertEqual(result[1], 'podX')
        self.assertEqual(suite.stored_ids, ['runpod:podX'])  # only the order that succeeded is stored
        destroy.assert_not_called()

    def test_when_every_attempt_is_refused_nothing_was_rented_and_nothing_is_destroyed(self):
        suite = RentCloudCpuTests()
        _, error, create, destroy, remaining = suite.run_rent(
            [{'status': 'ready'}], provider='runpod', mode='cuda', offers=self.OFFERS,
            create_effect=RuntimeError('no instances available'))
        self.assertEqual(create.call_count, worker.CLOUD_OFFER_ATTEMPTS)
        self.assertRegex(str(error), f'avvisade alla {worker.CLOUD_OFFER_ATTEMPTS} beställningar')
        self.assertIn('inget kostar pengar', str(error))
        self.assertIn('GPU 0', str(error))
        destroy.assert_not_called()
        self.assertEqual(suite.stored_ids, [])
        self.assertIsNone(remaining)

    def test_vast_orders_are_retried_the_same_way(self):
        suite = RentCloudCpuTests()
        result, error, create, _, _ = suite.run_rent(
            [{'status': 'ready'}], mode='cuda',
            offers=[{**offer, 'id': str(100 + n)} for n, offer in enumerate(self.OFFERS)],
            create_effect=[RuntimeError('offer taken'), 777])
        self.assertIsNone(error)
        self.assertEqual(result[1], 777)
        self.assertEqual(create.call_count, 2)


class RunpodRentTests(unittest.TestCase):
    def test_runpod_gpu_and_cpu_rentals_run_the_whole_chain_and_store_a_prefixed_id(self):
        for mode in ('cuda', 'cpu'):
            suite = RentCloudCpuTests()
            result, error, create, destroy, _ = suite.run_rent(
                [{'status': 'ready'}], provider='runpod', mode=mode,
                offers=[{'id': 'NVIDIA RTX A5000' if mode == 'cuda' else 'cpu3m', 'gpu': 'RTX A5000',
                         'price': .16, 'detail': '24 GB VRAM'}])
            self.assertIsNone(error, mode)
            model, instance_id, base_url, _ = result
            self.assertEqual(instance_id, 'abc123')
            self.assertTrue(base_url.startswith('https://cloud:41000/'))
            self.assertEqual(suite.stored_ids, ['runpod:abc123'])
            self.assertEqual(create.call_args.kwargs['device'], mode)
            destroy.assert_not_called()

    def test_a_failed_runpod_rental_destroys_the_pod(self):
        _, error, _, destroy, remaining = RentCloudCpuTests().run_rent(
            [{'status': 'insufficient_vram', 'vram_bytes': 8 * 1024 ** 3,
              'min_vram_bytes': 15 * 1024 ** 3}], provider='runpod', mode='cuda')
        self.assertRegex(str(error), 'för lite GPU-minne')
        destroy.assert_called_once_with('secret', 'abc123')
        self.assertIsNone(remaining)

    def test_a_price_above_the_hourly_limit_destroys_the_server_at_once(self):
        suite = RentCloudCpuTests()
        suite.runpod_cost = 5.0
        _, error, _, destroy, _ = suite.run_rent([{'status': 'ready'}], provider='runpod')
        self.assertRegex(str(error), 'över gränsen')
        destroy.assert_called_once()

    def test_runpod_gpu_rental_uses_the_cheapest_offer_not_the_saved_gpu_profile(self):
        suite = RentCloudCpuTests()
        _, error, create, _, _ = suite.run_rent(
            [{'status': 'ready'}], provider='runpod', mode='cuda',
            offers=[{'id': 'NVIDIA RTX A5000', 'gpu': 'RTX A5000', 'price': .16, 'detail': ''}])
        self.assertIsNone(error)
        self.assertEqual(create.call_args[0][1], 'NVIDIA RTX A5000')
        suite.gpu_offers.assert_not_called()

    def test_orphan_cleanup_asks_the_right_provider_to_destroy_each_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            database = make_database(directory, [
                (1, '/tv/a.mkv', 'a.mkv', 'failed', 'runpod:pod9'),
                (2, '/tv/b.mkv', 'b.mkv', 'failed', 777)])
            with patch.object(worker, 'DATABASE', database), \
                    patch.object(worker, 'destroy_cloud_gpu') as destroy:
                worker.cleanup_orphaned_cloud_instances()
        self.assertEqual(sorted(call.args for call in destroy.call_args_list),
                         sorted([('runpod', 'pod9'), ('vast', 777)], key=str))

    def test_runpod_options_are_labelled_with_the_provider_and_the_real_gpu_choice(self):
        html = hardware.render_hardware(CloudCpuHardwareTests.HARDWARE, 'cloud-runpod', 'tok',
                                        cloud_providers={'runpod'})
        self.assertIn('Runpod.io (kostar)', html)
        self.assertIn('billigaste lediga NVIDIA-kort', html)
        self.assertNotIn('RTX 5060 Ti · Runpod', html)
        self.assertIn('Moln-CPU · hela AI-kedjan · Runpod.io (kostar)', html)
        self.assertIn('Moln-CPU · Vast.ai (anslutning saknas)', html)


class CloudPromptPrivacyTests(unittest.TestCase):
    ROWS = [{'id': index, 'source': text} for index, text in enumerate(['Hello there.', 'Be safe.'])]
    ANSWER = {'response': json.dumps({'bad_ids': [], 'language_mismatch': False, 'reason': ''})}

    def prompt_for(self, device, name='Secret Family Show tyska S01E01.mkv'):
        with patch.object(worker, 'call_ollama', return_value=self.ANSWER) as model, \
                patch.object(worker.MODEL_JOB, 'device', device, create=True):
            worker._audit_source_chunk(self.ROWS, name)
        return model.call_args.args[0]

    def test_cloud_modes_send_an_empty_media_name_but_keep_the_language_hint(self):
        for device in ('cloud-vast', 'cloud-cpu-vast', 'cloud-runpod', 'cloud-cpu-runpod'):
            request = self.prompt_for(device)
            prompt = json.loads(request['prompt'])
            self.assertEqual(prompt['media_name'], '', device)
            self.assertEqual(prompt['filename_language_hint'], 'de', device)
            self.assertNotIn('Secret', request['prompt'])
            self.assertNotIn('Family', request['prompt'])
            self.assertNotIn('S01E01', request['prompt'])
            self.assertNotIn('Secret', request['system'])

    def test_local_modes_still_send_the_real_media_name(self):
        for device in ('cpu', 'amd-rx-9070-xt', None):
            prompt = json.loads(self.prompt_for(device)['prompt'])
            self.assertEqual(prompt['media_name'], 'Secret Family Show tyska S01E01.mkv', device)

    def test_a_cloud_run_without_a_hint_still_cannot_claim_a_language_mismatch(self):
        request = self.prompt_for('cloud-vast', name='Plain title.mkv')
        self.assertEqual(request['format']['properties']['language_mismatch']['enum'], [False])
        self.assertIsNone(json.loads(request['prompt'])['filename_language_hint'])


class VastPathTests(unittest.TestCase):
    """Vast.ai redirects (301/308, not followed for PUT/DELETE) paths without a trailing slash."""

    def urls(self, call):
        seen = []

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({'success': True, 'new_contract': 5, 'instances': {}}).encode()

            def __iter__(self):
                return iter(())

        def fake(request, timeout=0):
            seen.append((request.get_method(), request.full_url))
            reply = Reply()
            reply.read = lambda *a: json.dumps({'success': True, 'new_contract': 5, 'instances': {}}).encode()
            return reply

        with patch.object(cloud_gpu, 'urlopen', side_effect=fake):
            call()
        return seen

    def test_every_vast_call_uses_a_trailing_slash(self):
        base = cloud_gpu.VAST_API
        seen = self.urls(lambda: (
            cloud_gpu.create_instance('k', 42, '#!/bin/bash'),
            cloud_gpu.show_instance('k', 5),
            cloud_gpu.set_instance_state('k', 5, 'stopped'),
            cloud_gpu.destroy_instance('k', 5)))
        self.assertEqual(seen, [('PUT', f'{base}/asks/42/'), ('GET', f'{base}/instances/5/'),
                                ('PUT', f'{base}/instances/5/'), ('DELETE', f'{base}/instances/5/')])

    def test_a_path_that_already_ends_in_a_slash_is_not_doubled(self):
        seen = self.urls(lambda: cloud_gpu._request('GET', '/instances/', 'k'))
        self.assertEqual(seen, [('GET', f'{cloud_gpu.VAST_API}/instances/')])


class BootstrapDiagnosticsTests(unittest.TestCase):
    """The boot script reports progress and failure so a stuck server is never a blind wait."""

    def boot(self, device='cpu'):
        return bootstrap_of(OnstartScriptTests().build(device=device))

    def test_every_phase_writes_a_step_and_a_failure_is_recorded(self):
        boot = self.boot()
        self.assertIn("trap 'echo \"misslyckades vid: $(cat /root/step)\" > /root/failed' ERR", boot)
        for phase in ('apt: systempaket', 'pip: Hugging Face-klient', 'väntar på Python-miljöer och modeller',
                      'startar Qwen och molntjänsten', 'väntar på Ollama och Gemma-modellerna', 'klart'):
            self.assertIn(f"step '{phase}'", boot)
        # Each parallel track reports its own steps (tstep -> /root/step.<track>).
        for phase in ('pip: Qwen-miljö (torch)', 'pip: qwen-asr', 'pip: Whisper-miljö',
                      'installerar Ollama', 'startar Ollama', 'hämtar gemma3:12b', 'hämtar gemma4:12b'):
            self.assertIn(f"tstep '{phase}'", boot)
        self.assertIn('tstep "hämtar $1"', boot)
        self.assertIn('echo "$1" > "/root/step.$TRACK"', boot)
        self.assertIn('> "/root/failed.$TRACK"', boot)
        self.assertIn('set -eE -o pipefail', boot)  # errtrace: the trap also fires inside track functions

    def test_the_downloads_run_as_five_parallel_tracks_that_are_all_awaited(self):
        boot = self.boot()
        tracks = re.findall(r'^run_track (\w+) (\w+)$', boot, re.M)
        self.assertEqual([name for name, _ in tracks],
                         ['qwenpy', 'whisperpy', 'ollama', 'whispermodels', 'qwenmodels'])
        for name, function in tracks:
            self.assertIn(f'{function}() {{', boot)
        self.assertEqual(boot.count("\n  ) &\n"), 1)  # run_track backgrounds every track
        waits = re.findall(r'^wait "\$PID_(\w+)"$', boot, re.M)
        self.assertEqual(sorted(waits), sorted(name for name, _ in tracks))
        # The real gateway needs the Python/model tracks; Ollama keeps pulling after it started.
        self.assertLess(boot.index('wait "$PID_qwenmodels"'), boot.index('kill $DIAG_PID'))
        self.assertGreater(boot.index('wait "$PID_ollama"'), boot.index('kill $DIAG_PID'))

    def test_qwen_models_are_prefetched_so_they_no_longer_download_inside_the_first_request(self):
        boot = self.boot()
        self.assertIn('hf_get Qwen/Qwen3-ASR-1.7B', boot)
        self.assertIn('hf_get Qwen/Qwen3-ForcedAligner-0.6B', boot)
        self.assertEqual(boot.count('hf_get Systran/faster-whisper-$model'), 1)  # looped over v3 and v2
        self.assertIn('for model in large-v3 large-v2; do', boot)

    def test_shell_comments_are_dropped_but_heredoc_code_survives(self):
        raw = cloud_gpu.BOOTSTRAP_TEMPLATE
        boot = self.boot()
        self.assertIn('\n# ', raw)
        self.assertNotRegex(boot, r'(?m)^\s*#')
        self.assertIn("print(f'{repo}: hämtning klar', flush=True)", boot)
        self.assertIn('snapshot_download(repo, max_workers=1, allow_patterns=patterns)', boot)
        self.assertEqual(cloud_gpu.compact_shell('a\n# c\n\n  # d\nb # e\n'), 'a\nb # e\n')
        # No `#` line inside a heredoc body, which compaction would otherwise corrupt.
        body = re.search(r"<<'HF_EOF'\n(.*?)\nHF_EOF", raw, re.S).group(1)
        self.assertNotRegex(body, r'(?m)^\s*#')

    def test_a_diagnostics_gateway_starts_right_after_apt_and_is_replaced_later(self):
        boot = self.boot()
        diag, real = boot.index('SUBSYNQ_CLOUD_DIAG_ONLY=1'), boot.index('kill $DIAG_PID')
        self.assertGreater(diag, boot.index('python3-venv'))
        self.assertLess(diag, boot.index('run_track qwenpy track_qwenpy'))  # before any track starts
        self.assertLess(real, boot.index("step 'väntar på Ollama och Gemma-modellerna'"))
        self.assertNotIn('SUBSYNQ_CLOUD_DIAG_ONLY=1', boot[real:])
        self.assertIn('/root/whisper-venv/bin/python -u /root/cloud_cpu_service.py', boot[real:])

    def test_ollama_must_answer_before_the_models_are_pulled(self):
        boot = self.boot()
        self.assertLess(boot.index('/api/tags'), boot.index('ollama pull gemma3:12b'))
        self.assertLess(boot.index('ollama serve'), boot.index('/api/tags'))

    def test_the_gateway_joins_the_steps_and_failures_of_every_track(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, text in (('step', 'väntar\n'), ('step.ollama', 'hämtar gemma4:12b\n'),
                               ('step.qwenpy', 'pip: qwen-asr\n'), ('stepper', 'ignoreras\n')):
                (Path(folder) / name).write_text(text)
            with patch.object(gateway, 'STATE_DIR', folder):
                self.assertEqual(gateway.read_states('step'),
                                 'väntar | ollama: hämtar gemma4:12b | qwenpy: pip: qwen-asr')
                self.assertIsNone(gateway.read_states('failed'))
                (Path(folder) / 'failed.ollama').write_text('misslyckades vid: hämtar gemma4:12b\n')
                self.assertEqual(gateway.read_states('failed'),
                                 'ollama: misslyckades vid: hämtar gemma4:12b')
                with patch.object(gateway, 'available_memory_bytes', return_value=32 * 1024 ** 3), \
                        patch.object(gateway, 'local_json', return_value=None):
                    report = gateway.health_report()
        self.assertEqual(report['status'], 'bootstrap_failed')
        self.assertIn('ollama: misslyckades', report['bootstrap_error'])
        self.assertIn('qwenpy: pip: qwen-asr', report['bootstrap_step'])

    def test_a_missing_state_folder_is_not_an_error(self):
        with patch.object(gateway, 'STATE_DIR', '/nonexistent/subsynq'):
            self.assertIsNone(gateway.read_states('step'))

    def test_onstart_mirrors_the_log_to_the_container_log_and_returns_at_once(self):
        script = OnstartScriptTests().build()
        self.assertIn('tail -n +1 -F /root/bootstrap.log > /proc/1/fd/1', script)
        self.assertTrue(script.rstrip().endswith('disown'))

    def test_the_gateway_reports_step_failure_and_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, text in (('step', 'pip: qwen-asr\n'), ('bootstrap.log', 'Collecting x\nERROR: boom\n')):
                (Path(folder) / name).write_text(text)
            with patch.object(gateway, 'STATE_DIR', folder), \
                    patch.object(gateway, 'available_memory_bytes', return_value=32 * 1024 ** 3), \
                    patch.object(gateway, 'local_json', return_value=None):
                report = gateway.health_report()
                self.assertEqual((report['status'], report['bootstrap_step']), ('loading', 'pip: qwen-asr'))
                self.assertNotIn('bootstrap_error', report)
                (Path(folder) / 'failed').write_text('misslyckades vid: pip: qwen-asr\n')
                report = gateway.health_report()
                self.assertEqual(report['status'], 'bootstrap_failed')
                self.assertEqual(report['bootstrap_error'], 'misslyckades vid: pip: qwen-asr')
                tails = gateway.log_tails()
        self.assertIn('ERROR: boom', tails['bootstrap.log'])
        self.assertIsNone(tails['ollama.log'])

    def test_log_tails_are_limited(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'bootstrap.log').write_text('x' * 10000 + 'END')
            with patch.object(gateway, 'STATE_DIR', folder):
                tail = gateway.log_tails(limit=100)['bootstrap.log']
        self.assertEqual(len(tail), 100)
        self.assertTrue(tail.endswith('END'))

    def test_diagnostics_only_mode_never_loads_whisper(self):
        # Under the system python faster-whisper is not installed yet.
        source = Path('/ai/cloud_cpu_service.py').read_text(encoding='utf-8')
        self.assertIn('if not DIAG_ONLY:', source)
        self.assertEqual(gateway.DIAG_ONLY, False)


class FakeGatewayLogs:
    """Stand-ins for the worker's urlopen calls against a rented server."""

    def __init__(self, logs):
        self.logs = logs

    def __call__(self, request, timeout=0):
        url = request if isinstance(request, str) else request.full_url
        if not url.endswith('/log'):
            raise worker.urllib.error.URLError('connection refused')
        payload = json.dumps({'status': 'ok', 'logs': self.logs}).encode()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return payload

        return Response()


class WorkerDiagnosticsTests(unittest.TestCase):
    def test_log_summary_prefers_the_last_lines_and_never_raises(self):
        logs = {'bootstrap.log': 'a\nb\nc\nd\ne\nERROR: pip failed', 'ollama.log': None, 'gateway.log': ''}
        with patch.object(worker.urllib.request, 'urlopen', FakeGatewayLogs(logs)):
            summary = worker.fetch_cloud_logs('https://x/tok')
        self.assertIn('ERROR: pip failed', summary)
        self.assertNotIn('ollama.log', summary)
        with patch.object(worker.urllib.request, 'urlopen', side_effect=OSError('down')):
            self.assertIsNone(worker.fetch_cloud_logs('https://x/tok'))

    def test_health_message_shows_the_servers_current_step(self):
        message = worker.cloud_health_message({'whisper': 'loading', 'qwen': False, 'ollama': False,
                                               'bootstrap_step': 'hämtar Gemma-modeller (gemma3:12b)',
                                               'missing_models': ['gemma3:12b']})
        self.assertIn('steg: hämtar Gemma-modeller', message)

    def test_a_failed_install_stops_at_once_with_the_servers_own_log(self):
        health = {'status': 'bootstrap_failed', 'bootstrap_error': 'misslyckades vid: pip: qwen-asr'}
        suite = RentCloudCpuTests()
        with patch.object(worker, 'fetch_cloud_logs', return_value='bootstrap.log: ERROR: no matching distribution'):
            _, error, _, destroy, remaining = suite.run_rent([health], mode='cuda')
        self.assertRegex(str(error), 'kunde inte installera sina program: misslyckades vid: pip: qwen-asr')
        self.assertIn('no matching distribution', str(error))
        destroy.assert_called_once()
        self.assertIsNone(remaining)

    def test_a_timeout_names_the_last_step_and_includes_the_log(self):
        import itertools
        loading = {'status': 'loading', 'whisper': 'loading', 'qwen': False, 'ollama': False,
                   'missing_models': [], 'bootstrap_step': 'hämtar Whisper-modeller'}
        ticks = itertools.count(0, 5)  # a fake clock: every reading is 5 "seconds" later
        suite = RentCloudCpuTests()
        with patch.object(worker.time, 'monotonic', side_effect=lambda: next(ticks)), \
                patch.object(worker, 'CLOUD_CHAIN_SPINUP_SECONDS', 200), \
                patch.object(worker, 'CLOUD_CHAIN_ADDRESS_SECONDS', 150), \
                patch.object(worker, 'CLOUD_CHAIN_FIRST_REPLY_SECONDS', 10 ** 6), \
                patch.object(worker, 'fetch_cloud_logs', return_value='gateway.log: Whisper laddas'):
            _, error, _, destroy, _ = suite.run_rent([loading] * 200)
        self.assertRegex(str(error), 'sista steg: hämtar Whisper-modeller')
        self.assertIn('Serverns logg: gateway.log: Whisper laddas', str(error))
        destroy.assert_called_once()

    def test_a_server_that_never_answers_is_dropped_early_with_the_providers_log(self):
        silent = worker.urllib.error.URLError('connection refused')
        suite = RentCloudCpuTests()
        with patch.object(worker, 'CLOUD_CHAIN_FIRST_REPLY_SECONDS', -1), \
                patch.object(worker, 'fetch_cloud_logs', return_value=None), \
                patch.object(worker, 'fetch_vast_container_log', return_value='onstart: apt failed'):
            _, error, _, destroy, _ = suite.run_rent([silent, silent])
        self.assertRegex(str(error), 'svarade aldrig på hälsokontrollen')
        self.assertIn('onstart: apt failed', str(error))
        destroy.assert_called_once()

    def test_vast_container_log_lookup_uses_a_fresh_opener_and_never_raises(self):
        class Body:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'one\ntwo\nthree'

        opener = type('Opener', (), {'open': lambda self, url, timeout=0: Body()})()
        with patch.object(worker.cloud_gpu, 'request_logs', return_value='https://s3/x') as asked, \
                patch.object(worker.urllib.request, 'build_opener', return_value=opener), \
                patch.object(worker.time, 'sleep'):
            self.assertEqual(worker.fetch_vast_container_log('key', 5), 'one | two | three')
        asked.assert_called_once_with('key', 5, tail=60)
        with patch.object(worker.cloud_gpu, 'request_logs', side_effect=RuntimeError('x')):
            self.assertIsNone(worker.fetch_vast_container_log('key', 5))

class VramMonitorTests(unittest.TestCase):
    def monitor(self, samples):
        feed = iter(samples)
        return gateway.VramMonitor(sampler=lambda: next(feed), interval=60)

    def test_peaks_are_kept_per_phase_with_the_processes_at_the_peak(self):
        small = [{'pid': 1, 'name': 'python', 'mib': 3000}]
        big = [{'pid': 2, 'name': 'llama-server', 'mib': 9000}, {'pid': 1, 'name': 'python', 'mib': 3000}]
        monitor = self.monitor([(3100, 24564, small), (12600, 24564, big), (9000, 24564, big),
                                (8000, 24564, big),  # the reading the phase itself takes at its end
                                (3100, 24564, small)])
        monitor.observe()                       # nothing running: idle (what stays resident)
        with monitor.phase('ollama-gemma4:12b'):
            monitor.observe()                   # 12600 during the step
            monitor.observe()                   # 9000: lower, must not replace the peak
        monitor.observe()                       # idle again, same level
        report = monitor.report()
        self.assertEqual(report['phases']['ollama-gemma4:12b']['peak_mib'], 12600)
        self.assertEqual(report['phases']['ollama-gemma4:12b']['processes'], big)
        self.assertEqual(report['phases']['idle']['peak_mib'], 3100)
        self.assertEqual((report['overall_peak_mib'], report['overall_peak_phase']),
                         (12600, 'ollama-gemma4:12b'))
        self.assertEqual((report['total_mib'], report['samples']), (24564, 5))

    def test_overlapping_steps_are_labelled_with_both_names(self):
        monitor = self.monitor([(7000, 16000, []), (7000, 16000, []), (7000, 16000, [])])
        with monitor.phase('whisper-large-v3'):
            with monitor.phase('qwen-alignment'):
                monitor.observe()
        self.assertIn('qwen-alignment+whisper-large-v3', monitor.report()['phases'])

    def test_a_failing_step_still_leaves_the_phase(self):
        monitor = self.monitor([(1, 2, [])] * 5)
        with self.assertRaises(RuntimeError):
            with monitor.phase('qwen-identify'):
                raise RuntimeError('boom')
        self.assertEqual(monitor.active, [])
        self.assertEqual(monitor.report()['phases']['qwen-identify']['requests'], 1)

    def test_unreadable_gpu_is_counted_not_hidden(self):
        monitor = gateway.VramMonitor(sampler=lambda: None, interval=60)
        monitor.observe()
        report = monitor.report()
        self.assertEqual((report['sample_errors'], report['samples'], report['phases']), (1, 0, {}))

    def test_phase_names(self):
        self.assertEqual(gateway.qwen_phase('/transcribe'), 'qwen-alignment')
        self.assertEqual(gateway.qwen_phase('/identify'), 'qwen-identify')
        self.assertIsNone(gateway.qwen_phase('/unload'))
        self.assertEqual(gateway.ollama_phase(b'{"model": "gemma3:12b", "prompt": "x"}'), 'ollama-gemma3:12b')
        for body in (b'{"model": "gemma3:12b", "keep_alive": 0}', b'not json', b'[]', b'{}'):
            self.assertIsNone(gateway.ollama_phase(body), body)

    def test_nvidia_smi_readings_are_parsed_and_a_missing_tool_is_none(self):
        def run(command, **kwargs):
            if 'memory.used,memory.total' in command[1]:
                return SimpleNamespace(stdout='3100, 24564\n')
            return SimpleNamespace(stdout='1234, /usr/local/bin/ollama, 2800\n77, /usr/bin/python3, 300\nbad line\n')

        with patch.object(gateway.subprocess, 'run', side_effect=run):
            self.assertEqual(gateway.vram_sample(), (3100, 24564, [
                {'pid': 1234, 'name': 'ollama', 'mib': 2800}, {'pid': 77, 'name': 'python3', 'mib': 300}]))
        with patch.object(gateway.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertIsNone(gateway.vram_sample())

    def test_a_failing_process_listing_still_gives_the_total(self):
        def run(command, **kwargs):
            if 'memory.used,memory.total' in command[1]:
                return SimpleNamespace(stdout='3100, 24564\n')
            raise gateway.subprocess.SubprocessError('no apps')

        with patch.object(gateway.subprocess, 'run', side_effect=run):
            self.assertEqual(gateway.vram_sample(), (3100, 24564, []))

    def test_only_a_cuda_gateway_gets_a_monitor(self):
        self.assertIsNone(gateway.VRAM)  # the test environment is DEVICE=cpu


class WorkerVramTests(unittest.TestCase):
    DATA = {'enabled': True, 'total_mib': 24564, 'overall_peak_mib': 13500, 'overall_peak_phase': 'ollama-gemma4:12b',
            'phases': {'idle': {'peak_mib': 3100}, 'whisper-large-v3': {'peak_mib': 3400},
                       'qwen-alignment': {'peak_mib': 8700}, 'ollama-gemma4:12b': {'peak_mib': 13500}}}

    def served(self, payload):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return json.dumps(payload).encode()

        return patch.object(worker.urllib.request, 'urlopen', return_value=Response())

    def test_summary_lists_the_biggest_step_first_in_gib(self):
        summary = worker.vram_summary(self.DATA)
        self.assertTrue(summary.index('ollama-gemma4:12b 13.2') < summary.index('qwen-alignment 8.5'))
        self.assertIn('högst 13.2 av 24.0', summary)

    def test_fetch_ignores_disabled_unreachable_and_garbled_replies(self):
        with self.served({'enabled': False}):
            self.assertIsNone(worker.fetch_cloud_vram('https://x/tok'))
        with self.served([1, 2]):
            self.assertIsNone(worker.fetch_cloud_vram('https://x/tok'))
        with patch.object(worker.urllib.request, 'urlopen', side_effect=worker.urllib.error.URLError('down')):
            self.assertIsNone(worker.fetch_cloud_vram('https://x/tok'))

    def test_the_peaks_are_saved_with_the_job_and_printed(self):
        with tempfile.TemporaryDirectory() as folder, self.served(self.DATA), \
                patch.object(worker, 'CONFIG', Path(folder)), patch('builtins.print') as printed:
            summary = worker.record_cloud_vram({'id': 9}, 'https://x/tok')
            saved = json.loads((Path(folder) / 'cloud-vram' / 'job-9.json').read_text(encoding='utf-8'))
        self.assertIn('högst 13.2', summary)
        self.assertEqual((saved['job_id'], saved['vram']['overall_peak_mib']), (9, 13500))
        self.assertIn('VRAM-topp', printed.call_args.args[0])

    def test_recording_never_breaks_the_job(self):
        with patch.object(worker, 'fetch_cloud_vram', side_effect=RuntimeError('boom')), patch('builtins.print'):
            self.assertIsNone(worker.record_cloud_vram({'id': 1}, 'https://x/tok'))
        with tempfile.TemporaryDirectory() as folder, self.served(self.DATA), \
                patch.object(worker, 'CONFIG', Path(folder, 'missing', 'deeper')), patch('builtins.print'):
            Path(folder, 'missing').write_text('a file, so mkdir fails')
            self.assertIsNone(worker.record_cloud_vram({'id': 1}, 'https://x/tok'))

    def test_the_recorder_runs_when_the_job_fails_too_and_is_wired_into_cloud_jobs(self):
        with patch.object(worker, 'record_cloud_vram') as record:
            with self.assertRaises(RuntimeError):
                with worker.vram_recorder({'id': 3}, 'https://x/tok'):
                    raise RuntimeError('job failed')
        record.assert_called_once_with({'id': 3}, 'https://x/tok')
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn('cloud_chain_endpoints(base_url, tls_context), vram_recorder(job, base_url)', source)


class CloudCpuHardwareTests(unittest.TestCase):
    HARDWARE = {'available': [{'id': 'cpu', 'name': 'CPU', 'backend': 'CPU', 'verified': True,
                               'available': True}], 'recommended': 'cpu', 'worker_status': 'verifierad_idle',
                'active': None, 'fallback_reason': None, 'worker_error': None,
                'asr_model': 'x'}

    def test_preference_requires_a_saved_vast_key(self):
        self.assertIn('cloud-cpu-vast', hardware.allowed_preferences(self.HARDWARE, {'vast'}))
        self.assertNotIn('cloud-cpu-vast', hardware.allowed_preferences(self.HARDWARE, set()))
        self.assertIn('cloud-cpu-runpod', hardware.allowed_preferences(self.HARDWARE, {'runpod'}))
        self.assertNotIn('cloud-cpu-someone', hardware.allowed_preferences(self.HARDWARE, {'someone'}))

    def test_option_is_labelled_as_costing_real_money_and_confirmed(self):
        html = hardware.render_hardware(self.HARDWARE, 'cloud-cpu-vast', 'tok',
                                        cloud_providers={'vast'})
        self.assertIn('value="cloud-cpu-vast" selected', html)
        self.assertIn('Moln-CPU', html)
        self.assertIn('Vast.ai (kostar)<', html)
        self.assertNotIn('riktiga pengar<', html)
        self.assertNotIn('(vast', html)
        self.assertIn('cloud-cpu-', html.split('onsubmit=')[1])
        plain = hardware.render_hardware(self.HARDWARE, 'auto', 'tok', cloud_providers=set())
        self.assertIn('value="cloud-cpu-vast" disabled', plain)
        self.assertIn('Moln-CPU · Vast.ai (anslutning saknas)', plain)
        self.assertIn('Runpod.io (anslutning saknas)', plain)


if __name__ == '__main__':
    unittest.main()
