import io
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class DeviceSelectionTests(unittest.TestCase):
    def test_cpu_does_not_probe_or_wait_for_gpu(self):
        with patch.object(worker, 'gpu_health') as probe, patch.object(worker, 'wait_for_gpu') as wait:
            self.assertEqual(worker.select_job_hardware({'id': 1}, 'cpu'), (None, False))
        probe.assert_not_called()
        wait.assert_not_called()

    def test_cloud_does_not_select_local_gpu(self):
        with patch.object(worker, 'gpu_health') as probe:
            for choice in ('cloud-vast', 'cloud-runpod'):
                self.assertEqual(worker.select_job_hardware({'id': 1}, choice), (None, False))
        probe.assert_not_called()

    def test_auto_and_explicit_gpu_use_available_gpu(self):
        with patch.object(worker, 'gpu_health', return_value={'ready': True}):
            for choice in ('auto', 'amd-rx-9070-xt'):
                self.assertEqual(worker.select_job_hardware({'id': 1}, choice), ({'ready': True}, True))

    def test_missing_explicit_gpu_does_not_silently_use_cpu(self):
        with patch.object(worker, 'gpu_health', return_value=None), \
                patch.object(worker, 'wait_for_gpu', return_value=None):
            with self.assertRaises(RuntimeError):
                worker.select_job_hardware({'id': 1}, 'amd-rx-9070-xt')

    def test_unreadable_preference_does_not_default_to_auto(self):
        with patch.object(worker.sqlite3, 'connect', side_effect=sqlite3.OperationalError('busy')):
            with self.assertRaises(RuntimeError):
                worker.hardware_preference()

    def test_invalid_choice_does_not_select_gpu(self):
        with patch.object(worker, 'gpu_health') as probe:
            with self.assertRaises(RuntimeError):
                worker.select_job_hardware({'id': 1}, 'unknown')
        probe.assert_not_called()

    def test_cpu_job_forces_ollama_cpu_without_mutating_input(self):
        request = {'model': 'reviewer', 'options': {'num_ctx': 8192, 'num_gpu': 20}}
        with patch.object(worker.MODEL_JOB, 'device', 'cpu', create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', None, create=True), \
                patch.object(worker.urllib.request, 'urlopen', return_value=io.BytesIO(
                    b'{"done_reason":"stop","response":"{}"}')) as opening, patch('builtins.print'):
            worker.call_ollama(request)
        sent = json.loads(opening.call_args.args[0].data)
        self.assertEqual(sent['options'], {'num_ctx': 8192, 'num_gpu': 0, 'use_mmap': True})
        self.assertEqual(request['options'], {'num_ctx': 8192, 'num_gpu': 20})

    def send(self, device, request):
        with patch.object(worker.MODEL_JOB, 'device', device, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', None, create=True), \
                patch.object(worker.urllib.request, 'urlopen', return_value=io.BytesIO(
                    b'{"done_reason":"stop","response":"{}"}')) as opening, patch('builtins.print'):
            worker.call_ollama(request)
        return json.loads(opening.call_args.args[0].data)

    def test_memory_mapped_weights_are_used_only_for_local_cpu_runs(self):
        base = {'model': 'reviewer', 'options': {'num_ctx': 8192}}
        for device in ('cloud-vast', 'cloud-cpu-vast', 'cloud-runpod', 'cloud-cpu-runpod',
                       'amd-rx-9070-xt', 'nvidia-cuda', None):
            sent = self.send(device, dict(base))
            self.assertEqual(sent['options'], {'num_ctx': 8192}, device)
        self.assertEqual(self.send('cpu', dict(base))['options']['use_mmap'], True)

    def test_unload_requests_carry_no_extra_options(self):
        sent = self.send('cpu', {'model': 'reviewer', 'keep_alive': 0})
        self.assertNotIn('options', sent)
