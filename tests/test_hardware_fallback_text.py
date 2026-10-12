import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker


class HardwareFallbackTextTests(unittest.TestCase):
    """A CPU-only install must not show a yellow warning about a GPU that was never configured."""

    def run_job_status(self, **settings):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'hardware-status.json'
            with patch.object(worker, 'HARDWARE', target), \
                    patch.object(worker, 'hardware_preference', return_value='auto'), \
                    patch.object(worker, 'resource_usage', return_value=None), \
                    patch.multiple(worker, **settings):
                worker.set_hardware(True, device='cpu', health=None)
            return json.loads(target.read_text(encoding='utf-8'))

    def test_no_gpu_service_configured_means_no_fallback_warning(self):
        status = self.run_job_status(GPU_ASR_URL='', GPU_ASR_ENABLED=True, NATIVE_CUDA_ENABLED=False)
        self.assertIsNone(status['fallback_reason'])
        self.assertEqual(status['active'], 'cpu')

    def test_a_configured_but_silent_amd_service_still_explains_the_cpu_fallback(self):
        status = self.run_job_status(GPU_ASR_URL='http://gpu-asr:8090', GPU_ASR_ENABLED=True, NATIVE_CUDA_ENABLED=False)
        self.assertIn('AMD Radeon RX 9070 XT svarade inte', status['fallback_reason'])

    def test_a_cuda_install_that_cannot_use_cuda_still_explains_the_cpu_fallback(self):
        status = self.run_job_status(GPU_ASR_URL='', NATIVE_CUDA_ENABLED=True)
        self.assertIn('NVIDIA CUDA är inte tillgängligt', status['fallback_reason'])


if __name__ == '__main__':
    unittest.main()
