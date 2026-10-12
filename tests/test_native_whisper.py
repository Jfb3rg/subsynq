import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import native_whisper
import worker


class NativeWhisperTests(unittest.TestCase):
    def test_cpu_never_probes_cuda(self):
        with patch.object(native_whisper, 'cuda_health') as health, patch.object(native_whisper, 'WhisperModel') as model:
            native_whisper.load_model('large-v3', 'cpu')
            health.assert_not_called()
            model.assert_called_once_with('large-v3', device='cpu', compute_type='int8',
                                          cpu_threads=6, download_root='/models', local_files_only=True)

    def test_cuda_uses_same_cache(self):
        with patch.object(native_whisper, 'cuda_health', return_value={'status': 'ready'}), patch.object(native_whisper, 'WhisperModel') as model:
            native_whisper.load_model('large-v3', 'cuda')
            model.assert_called_once_with('large-v3', device='cuda', compute_type='float16',
                                          cpu_threads=6, download_root='/models', local_files_only=True)

    def test_missing_cuda_never_silently_loads_cpu(self):
        with patch.object(native_whisper, 'cuda_health', return_value=None), patch.object(native_whisper, 'WhisperModel') as model:
            with self.assertRaises(RuntimeError):
                native_whisper.load_model('large-v3', 'cuda')
            model.assert_not_called()

    def test_no_devices(self):
        with patch.object(native_whisper.ctranslate2, 'get_cuda_device_count', return_value=0), patch.object(native_whisper.ctranslate2, 'get_supported_compute_types') as types:
            self.assertIsNone(native_whisper.cuda_health())
            types.assert_not_called()

    def test_incompatible_compute_types(self):
        with patch.object(native_whisper.ctranslate2, 'get_cuda_device_count', return_value=1), patch.object(native_whisper.ctranslate2, 'get_supported_compute_types', return_value={'float32'}):
            self.assertIsNone(native_whisper.cuda_health())

    def test_runtime_failure(self):
        with patch.object(native_whisper.ctranslate2, 'get_cuda_device_count', side_effect=RuntimeError('driver unavailable')):
            self.assertIsNone(native_whisper.cuda_health())

    def test_cuda_identity(self):
        with patch.object(native_whisper.ctranslate2, 'get_cuda_device_count', return_value=1), patch.object(native_whisper.ctranslate2, 'get_supported_compute_types', return_value={'float16'}):
            self.assertEqual(native_whisper.cuda_health()['device_id'], 'nvidia-cuda')

    def test_explicit_cpu_bypasses_gpu(self):
        with patch.object(worker, 'NATIVE_CUDA_ENABLED', True), patch.object(worker, 'gpu_health') as health:
            self.assertEqual(worker.select_job_hardware({}, 'cpu'), (None, False))
            health.assert_not_called()

    def test_explicit_cuda_unavailable_fails(self):
        with patch.object(worker, 'NATIVE_CUDA_ENABLED', True), patch.object(worker, 'gpu_health', return_value=None):
            with self.assertRaises(RuntimeError):
                worker.select_job_hardware({}, 'nvidia-cuda')

    def test_auto_can_use_cpu(self):
        with patch.object(worker, 'NATIVE_CUDA_ENABLED', True), patch.object(worker, 'gpu_health', return_value=None):
            self.assertEqual(worker.select_job_hardware({}, 'auto'), (None, False))

    def test_native_cuda_does_not_accept_amd(self):
        with patch.object(worker, 'NATIVE_CUDA_ENABLED', True):
            with self.assertRaises(RuntimeError):
                worker.select_job_hardware({}, 'amd-rx-9070-xt')

    def test_native_health_never_uses_remote_service(self):
        with patch.object(worker, 'NATIVE_CUDA_ENABLED', True), patch.object(native_whisper, 'cuda_health', return_value=None), patch.object(worker.urllib.request, 'urlopen') as get:
            self.assertIsNone(worker.gpu_health())
            get.assert_not_called()

    def test_invalid_device_rejected(self):
        with self.assertRaises(ValueError):
            native_whisper.load_model('large-v3', 'automatic')
