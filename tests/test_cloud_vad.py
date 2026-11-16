import io
import json
import sys
import threading
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from faster_whisper.vad import VadOptions
import faster_whisper.vad as vad
import cloud_cpu_service as gateway
import model_manager
import worker
import gpu_policy
from vad_router import cloud_speech_timestamps


class CloudVadTests(unittest.TestCase):
    def test_language_whispers_remain_remote_even_with_cpu_constructor_arguments(self):
        session = worker.SequentialASRModels(worker.RemoteCloudWhisperModel('https://example/token', 'cloud'))
        with patch.object(worker, 'WhisperModel') as local:
            for name in ('large-v2', 'KBLab/kb-whisper-large', 'NbAiLab/nb-whisper-large'):
                reader = session.create_whisper(name, device='cpu', compute_type='int8')
                self.assertIsInstance(reader, worker.RemoteCloudWhisperModel)
                self.assertEqual(reader.whisper_model, name)
            local.assert_not_called()

    def test_tamil_service_passes_cuda_to_the_fine_tuned_model(self):
        from test_qwen_language_models import load_service
        asr, aligner = MagicMock(), MagicMock()
        service = load_service(asr, aligner, {'SUBSYNQ_QWEN_DEVICE': 'cuda', 'SUBSYNQ_QWEN_DTYPE': 'float16'})
        service.load_model(language='Tamil')
        self.assertEqual(asr.from_pretrained.call_args.args[0], 'lemuralabs/tamil-asr-qwen3')
        self.assertEqual(asr.from_pretrained.call_args.kwargs['device_map'], 'cuda')

    def test_worker_requires_gpu_residency_before_sending_a_generation(self):
        state = SimpleNamespace(device='cloud-vast', job_id=None, cloud_models=('gemma3:27b', 'gemma4:31b'))
        request = {'model': worker.MODEL_NAME, 'prompt': 'words'}
        with patch.object(worker, 'MODEL_JOB', state), \
                patch.object(worker, 'TRANSLATE_URL', 'https://example/token/ollama/api/generate'), \
                patch.object(gpu_policy, 'require_cloud_gpu', side_effect=RuntimeError('no GPU')) as gate, \
                patch('urllib.request.urlopen') as network:
            with self.assertRaisesRegex(RuntimeError, 'no GPU'):
                worker.call_ollama(request)
            self.assertEqual(gate.call_args.args[2], 'gemma3:27b')
            network.assert_not_called()

    def test_worker_dispatches_to_cloud_and_never_constructs_local_vad(self):
        audio, options = np.zeros(512), VadOptions(threshold=.25)
        with patch.object(worker, 'TRANSLATE_URL', 'https://example/token/ollama/api/generate'), \
                patch.object(worker.MODEL_JOB, 'device', 'cloud-vast', create=True), \
                patch('vad_router.cloud_speech_timestamps', return_value=[]) as remote, \
                patch.object(worker, 'local_speech_timestamps') as local:
            self.assertEqual(worker.get_speech_timestamps(audio, options), [])
            self.assertIs(remote.call_args.args[1], audio)
            self.assertIs(remote.call_args.args[2], options)
            self.assertEqual(remote.call_args.kwargs['expected_device'], 'cuda')
            local.assert_not_called()

    def test_worker_missing_cloud_endpoint_stops_instead_of_using_cpu(self):
        with patch.object(worker, 'TRANSLATE_URL', 'http://local/api/generate'), \
                patch.object(worker.MODEL_JOB, 'device', 'cloud-vast', create=True), \
                patch.object(worker, 'local_speech_timestamps') as local:
            with self.assertRaisesRegex(RuntimeError, 'CPU-reserv'):
                worker.get_speech_timestamps(np.zeros(512))
            local.assert_not_called()

    def test_local_cpu_jobs_keep_the_original_vad(self):
        with patch.object(worker, 'TRANSLATE_URL', 'http://local/api/generate'), \
                patch.object(worker.MODEL_JOB, 'device', 'cpu', create=True), \
                patch.object(worker, 'local_speech_timestamps', return_value=[]) as local:
            worker.get_speech_timestamps(np.zeros(512), threshold=.25)
            self.assertEqual(local.call_args.kwargs['threshold'], .25)

    def test_cloud_request_preserves_pcm_options_and_checks_device(self):
        samples = np.linspace(-.2, .2, 1600, dtype=np.float32)
        options = VadOptions(threshold=.25, min_speech_duration_ms=120, speech_pad_ms=0)
        answer = {'status': 'ok', 'device': 'cuda', 'segments': [{'start': 128, 'end': 1024}]}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(answer).encode())) as send:
            self.assertEqual(cloud_speech_timestamps('https://example/token', samples, options,
                                                     expected_device='cuda'), answer['segments'])
        request = send.call_args.args[0]
        self.assertEqual(request.data, samples.tobytes())
        payload = json.loads(request.get_header('X-subsynq-vad-options'))
        self.assertEqual(payload['threshold'], .25)
        self.assertNotIn('max_speech_duration_s', payload)

    def test_backend_failure_never_falls_back_to_local_vad(self):
        with patch('urllib.request.urlopen', side_effect=OSError('offline')), \
                patch.object(vad, 'get_speech_timestamps') as local:
            with self.assertRaisesRegex(RuntimeError, 'ingen lokal CPU'):
                cloud_speech_timestamps('https://example/token', np.zeros(512), VadOptions())
            local.assert_not_called()

    def test_cpu_reply_and_invalid_or_overlapping_timestamps_are_rejected(self):
        for changes in ({'device': 'cpu'}, {'segments': [{'start': 0, 'end': 513}]},
                        {'segments': [{'start': 0, 'end': 256}, {'start': 128, 'end': 512}]},
                        {'segments': [{'start': 0, 'end': 0}]}, {'segments': None}):
            answer = {'status': 'ok', 'device': 'cuda', 'segments': []} | changes
            with self.subTest(changes=changes), \
                    patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(answer).encode())):
                with self.assertRaises(RuntimeError):
                    cloud_speech_timestamps('https://example/token', np.zeros(512), VadOptions(),
                                            expected_device='cuda')

    def test_gpu_vad_requires_cuda_and_disables_cpu_fallback_before_warmup(self):
        ort = MagicMock()
        ort.get_available_providers.return_value = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        ort.InferenceSession.return_value.get_providers.return_value = ['CUDAExecutionProvider']
        with patch.dict(sys.modules, {'onnxruntime': ort}), patch.object(gateway, 'DEVICE', 'cuda'), \
                patch.object(vad, 'get_vad_model'), patch.object(vad, 'get_speech_timestamps') as warmup:
            gateway.configure_vad()
            ort.SessionOptions.return_value.add_session_config_entry.assert_called_once_with(
                'session.disable_cpu_ep_fallback', '1')
            ort.InferenceSession.return_value.disable_fallback.assert_called_once()
            self.assertEqual(ort.InferenceSession.call_args.kwargs['providers'], ['CUDAExecutionProvider'])
            warmup.assert_called_once()
        ort.get_available_providers.return_value = ['CPUExecutionProvider']
        ort.InferenceSession.return_value.get_providers.return_value = ['CPUExecutionProvider']
        with patch.dict(sys.modules, {'onnxruntime': ort}), patch.object(gateway, 'DEVICE', 'cuda'):
            with self.assertRaisesRegex(RuntimeError, 'CPU-reserv'):
                gateway.configure_vad()

    def test_gpu_vad_error_prevents_readiness(self):
        with patch.object(gateway, 'configure_vad', side_effect=RuntimeError('CUDA VAD unavailable')), \
                patch.object(gateway, 'activate_whisper') as load, \
                patch.object(gateway, 'whisper_state', {'status': 'loading', 'error': None}):
            gateway.load_whisper()
            self.assertEqual(gateway.whisper_state['status'], 'error')
            load.assert_not_called()

    def test_authenticated_endpoint_preserves_existing_vad_rules(self):
        samples = np.zeros(16000, dtype='<f4')
        options = VadOptions(threshold=.25, speech_pad_ms=0)
        expected = vad.get_speech_timestamps(samples, options)
        with patch.object(gateway, 'TOKEN', 'test-token'), patch.object(gateway, 'DEVICE', 'cpu'), \
                patch.object(gateway, 'whisper_state', {'status': 'ready', 'error': None}):
            server = ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertEqual(cloud_speech_timestamps(base + '/test-token', samples, options,
                                                         expected_device='cpu'), expected)
                with self.assertRaises(RuntimeError):
                    cloud_speech_timestamps(base + '/wrong-token', samples, options)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_bundled_vad_is_present_and_cannot_be_removed_separately(self):
        spec = SimpleNamespace(name='silero_vad_v6.onnx')
        backend = model_manager.BundledBackend()
        self.assertTrue(backend.is_present(spec))
        self.assertEqual(backend.installed_version(spec), backend.latest_version(spec))
        with self.assertRaises(RuntimeError):
            backend.remove(spec)

    def test_ollama_preloads_without_prompt_and_rejects_partial_gpu_residency(self):
        request = {'model': 'gemma3:12b', 'prompt': 'private words', 'options': {'seed': 1}}
        for vram, allowed in ((1000, True), (999, False), (0, False)):
            replies = [{'done': True}, {'models': [{'name': 'gemma3:27b', 'size': 1000, 'size_vram': vram}]}, {}]
            with self.subTest(vram=vram), patch('urllib.request.urlopen', side_effect=[
                    io.BytesIO(json.dumps(reply).encode()) for reply in replies]) as backend:
                if allowed:
                    result = gpu_policy.require_cloud_gpu('https://example/token/ollama/api/generate', request, 'gemma3:27b')
                    self.assertEqual(result['options'], {'seed': 1, 'num_gpu': 999})
                    self.assertEqual(result['prompt'], 'private words')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'CPU-reserv'):
                        gpu_policy.require_cloud_gpu('https://example/token/ollama/api/generate', request, 'gemma3:27b')
                preload = json.loads(backend.call_args_list[0].args[0].data)
                self.assertNotIn('prompt', preload)
                self.assertTrue(backend.call_args_list[1].args[0].endswith('/ollama/api/ps'))

    def test_unload_requests_do_not_trigger_gpu_preloading(self):
        request = {'model': 'gemma3:27b', 'keep_alive': 0}
        with patch('urllib.request.urlopen') as backend:
            self.assertEqual(gpu_policy.require_cloud_gpu('https://example/api/generate', request, 'gemma3:27b'), request)
            backend.assert_not_called()



if __name__ == '__main__':
    unittest.main()
