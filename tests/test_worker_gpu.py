import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ResourceUsageTests(unittest.TestCase):
    def test_resource_usage_reports_bounded_real_values(self):
        usage = worker.resource_usage()
        self.assertIsNotNone(usage)
        self.assertTrue(0 <= usage['cpu_percent'] <= 100)
        self.assertTrue(0 <= usage['ram_used_gb'] <= usage['ram_total_gb'])

    def test_refresh_merges_resources_without_touching_other_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hardware-status.json'
            path.write_text(json.dumps({'worker_status': 'running', 'active': 'cpu'}))
            with patch.object(worker, 'HARDWARE', path):
                worker.refresh_resource_usage()
            data = json.loads(path.read_text())
        self.assertEqual(data['worker_status'], 'running')
        self.assertEqual(data['active'], 'cpu')
        self.assertIn('cpu_percent', data['resources'])
        self.assertIn('ram_used_gb', data['resources'])
        self.assertIn('ram_total_gb', data['resources'])

    def test_refresh_is_a_silent_noop_without_an_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.json'
            with patch.object(worker, 'HARDWARE', path):
                worker.refresh_resource_usage()
            self.assertFalse(path.exists())


class WorkerGpuTests(unittest.TestCase):
    def hardware_status(self, *, gpu_enabled, native_enabled=False, health=None,
                        running=False, device='cpu'):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hardware-status.json'
            path.write_text(json.dumps({
                'available': [{'id': 'cpu', 'name': 'CPU', 'backend': 'CPU',
                               'verified': True, 'available': True}],
                'recommended': 'cpu',
            }))
            with patch.object(worker, 'HARDWARE', path), \
                    patch.object(worker, 'GPU_ASR_ENABLED', gpu_enabled), \
                    patch.object(worker, 'NATIVE_CUDA_ENABLED', native_enabled), \
                    patch.object(worker, 'hardware_preference', return_value='cpu'), \
                    patch.object(worker, 'resource_usage', return_value=None):
                worker.set_hardware(running, device=device, health=health)
            return json.loads(path.read_text())

    def test_idle_asr_status_uses_generic_paused_gpu_text(self):
        data = self.hardware_status(gpu_enabled=False)
        self.assertIn('GPU pausad', data['asr_model'])
        self.assertNotIn('AMD', data['asr_model'])

    def test_idle_asr_status_reports_missing_gpu_generically(self):
        data = self.hardware_status(gpu_enabled=True)
        self.assertIn('Ingen GPU hittad', data['asr_model'])
        self.assertNotIn('AMD', data['asr_model'])
        self.assertNotIn('NVIDIA', data['asr_model'])

    def test_idle_asr_status_uses_detected_gpu_name(self):
        data = self.hardware_status(
            gpu_enabled=True, health={'status': 'ready', 'device': 'AMD Radeon RX 9070 XT'})
        self.assertIn('GPU: AMD Radeon RX 9070 XT', data['asr_model'])

    def test_running_asr_status_uses_detected_gpu_name(self):
        data = self.hardware_status(
            gpu_enabled=True, health={'status': 'ready', 'device': 'Intel Arc Test'},
            running=True, device='local-gpu')
        self.assertIn('Intel Arc Test', data['asr_model'])

    def test_disabled_gpu_is_never_probed(self):
        with patch.object(worker, 'GPU_ASR_ENABLED', False), \
                patch.object(worker.urllib.request, 'urlopen') as urlopen:
            self.assertIsNone(worker.gpu_health())
            self.assertFalse(worker.verified_amd_configured())
            urlopen.assert_not_called()

    def test_gpu_profiles_never_send_more_than_thirty_seconds(self):
        gpu_profiles = worker.asr_profiles('amd-rx-9070-xt')
        self.assertTrue(all(window <= 30 for window, _overlap in gpu_profiles))
        self.assertEqual(worker.asr_profiles('cpu')[0], (90, 30))

    def test_cancel_request_cannot_be_overwritten_by_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'jobs.db'
            with sqlite3.connect(database) as db:
                db.execute('''CREATE TABLE subtitle_jobs (
                    id INTEGER PRIMARY KEY, status TEXT, progress INTEGER,
                    message TEXT, updated_at TEXT, output_path TEXT,
                    hardware TEXT, model TEXT)''')
                db.execute("INSERT INTO subtitle_jobs VALUES (1,'cancel_requested',29,'stop','',NULL,NULL,NULL)")
            with patch.object(worker, 'DATABASE', database):
                with self.assertRaises(worker.JobCancelled):
                    worker.update_job(1, 'transcribing', 30, 'next window')
            with sqlite3.connect(database) as db:
                status, progress = db.execute(
                    'SELECT status,progress FROM subtitle_jobs WHERE id=1').fetchone()
            self.assertEqual((status, progress), ('cancel_requested', 29))

    def test_ready_verified_amd_service_is_detected(self):
        payload = json.dumps({
            'status': 'ready', 'device': 'AMD Radeon RX 9070 XT',
        }).encode()
        with patch.object(worker, 'GPU_ASR_ENABLED', True), \
                patch.object(worker, 'GPU_ASR_URL', 'http://gpu'), \
                patch.object(worker.urllib.request, 'urlopen', return_value=Response(payload)):
            self.assertEqual(worker.gpu_health()['status'], 'ready')

    def test_remote_model_matches_worker_segment_interface(self):
        payload = json.dumps({
            'status': 'ok', 'language': 'English',
            'segments': [{'start': 1.0, 'end': 2.0, 'text': 'Six months.', 'words': [
                {'start': 1.0, 'end': 1.4, 'word': ' Six', 'probability': 1.0},
                {'start': 1.4, 'end': 2.0, 'word': ' months.', 'probability': 1.0},
            ]}],
        }).encode()
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'clip.wav'
            audio.write_bytes(b'RIFF-test')
            with patch.object(worker, 'GPU_ASR_URL', 'http://gpu'), \
                    patch.object(worker.urllib.request, 'urlopen', return_value=Response(payload)):
                segments, info = worker.RemoteWhisperModel().transcribe(audio)
        self.assertEqual(info.language, 'English')
        self.assertEqual(segments[0].text, 'Six months.')
        self.assertEqual(segments[0].words[1].word, ' months.')
        self.assertEqual(segments[0].no_speech_prob, 0.0)

    def test_remote_model_uses_given_base_url_not_the_local_gpu(self):
        payload = json.dumps({
            'status': 'ok', 'language': 'English',
            'segments': [{'start': 0.0, 'end': 1.0, 'text': 'Hi.', 'words': []}],
        }).encode()
        captured = {}

        def fake_urlopen(request, timeout=240):
            captured['url'] = request.full_url
            return Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'clip.wav'
            audio.write_bytes(b'RIFF-test')
            with patch.object(worker, 'GPU_ASR_URL', 'http://local-amd-gpu'), \
                    patch.object(worker.urllib.request, 'urlopen', side_effect=fake_urlopen):
                worker.RemoteWhisperModel(base_url='http://cloud-instance:41000').transcribe(audio)
        self.assertEqual(captured['url'], 'http://cloud-instance:41000/transcribe')

    def test_qwen_unusable_alignment_skips_optional_window(self):
        payload = json.dumps({
            'status': 'ok', 'language': 'French', 'items': [
                {'text': 'Bonjour', 'start': 1.2, 'end': 1.2},
            ],
        }).encode()
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'clip.wav'
            audio.write_bytes(b'RIFF-test')
            with patch.object(worker, 'QWEN_ASR_URL', 'http://qwen'), \
                    patch.object(worker.urllib.request, 'urlopen', return_value=Response(payload)):
                result = worker.qwen_transcribe(audio)
        self.assertEqual(result['items'], [])
        self.assertIn('längd', result['skipped_alignment'])


if __name__ == '__main__':
    unittest.main()
