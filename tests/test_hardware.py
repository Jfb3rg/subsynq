import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, '/app')
from hardware import allowed_preferences, load_hardware, render_hardware


class HardwareTests(unittest.TestCase):
    def test_missing_status_is_explicit_cpu_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            hardware = load_hardware(Path(directory))
        self.assertEqual(hardware["recommended"], "cpu")
        self.assertIn("Ingen AI-motor", hardware["fallback_reason"])
        self.assertEqual(allowed_preferences(hardware), {"auto", "cpu"})

    def test_validated_gpu_status_and_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware-status.json"
            path.write_text(json.dumps({
                "worker_status": "ready", "active": None, "recommended": "amd-rx-9070-xt",
                "asr_model": "Qwen3-ASR-1.7B", "available": [
                    {"id": "amd-rx-9070-xt", "name": "<RX 9070 XT>", "backend": "ROCm 7.2.1", "verified": True, "available": True}
                ]
            }))
            hardware = load_hardware(Path(directory))
        self.assertEqual(hardware["recommended"], "amd-rx-9070-xt")
        self.assertIn("amd-rx-9070-xt", allowed_preferences(hardware))
        page = render_hardware(hardware, "auto", "token", saved=True)
        self.assertIn("&lt;RX 9070 XT&gt;", page)
        self.assertNotIn("<RX 9070 XT>", page)
        self.assertIn("Automatiskt → &lt;RX 9070 XT&gt;", page)
        self.assertIn("Hårdvaruvalet är sparat", page)

    def test_manual_cpu_choice_is_distinct_from_job_status(self):
        hardware = dict(load_hardware(Path('/definitely-missing')))
        page = render_hardware(hardware, 'cpu', 'token')
        self.assertIn('Sparat val för nästa jobb:</strong> CPU', page)
        self.assertIn('Ingen AI-bearbetning pågår', page)
        self.assertNotIn('Hårdvaruvalet är sparat', page)
        self.assertIn('this.form.requestSubmit()', page)

    def test_translates_to_english(self):
        hardware = dict(load_hardware(Path('/definitely-missing')))
        page = render_hardware(hardware, 'cpu', 'token', saved=True, lang='en')
        self.assertIn('Saved choice for the next job:</strong> CPU', page)
        self.assertIn('No AI processing active', page)
        self.assertIn('Hardware choice saved', page)
        self.assertIn('AI hardware', page)
        self.assertIn('Save choice', page)

    def test_malformed_status_falls_back_without_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "hardware-status.json").write_text("{")
            hardware = load_hardware(Path(directory))
        self.assertEqual(hardware["worker_status"], "not_connected")
        self.assertIn("kunde inte läsas", hardware["fallback_reason"])

    def test_job_error_is_not_mislabelled_as_hardware_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'hardware-status.json').write_text(json.dumps({
                'worker_status': 'error', 'recommended': 'cpu',
                'fallback_reason': None, 'worker_error': 'Översättningsblocket misslyckades',
                'available': [{'id': 'cpu', 'name': 'CPU', 'backend': 'CPU',
                               'verified': True, 'available': True}],
            }))
            page = render_hardware(load_hardware(Path(directory)), 'auto', 'token')
        self.assertIn('Senaste jobbfel:', page)
        self.assertNotIn('Tydlig reservorsak:', page)

    def test_verified_but_disconnected_gpu_is_visible_not_selectable(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'hardware-status.json').write_text(json.dumps({
                'worker_status': 'verifierad_idle', 'recommended': 'amd-rx-9070-xt',
                'available': [
                    {'id': 'amd-rx-9070-xt', 'name': 'AMD Radeon RX 9070 XT',
                     'backend': 'ROCm WSL-Docker', 'verified': True, 'available': False},
                    {'id': 'cpu', 'name': 'CPU', 'backend': 'CPU',
                     'verified': True, 'available': True},
                ],
            }))
            hardware = load_hardware(Path(directory))
        self.assertEqual(hardware['recommended'], 'cpu')
        self.assertNotIn('amd-rx-9070-xt', allowed_preferences(hardware))
        page = render_hardware(hardware, 'auto', 'token')
        self.assertIn('Automatiskt → CPU', page)
        self.assertIn('Verifierad men ännu inte ansluten:</strong> AMD Radeon RX 9070 XT', page)

    def test_valid_resources_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'hardware-status.json').write_text(json.dumps({
                'worker_status': 'running', 'recommended': 'cpu',
                'available': [{'id': 'cpu', 'name': 'CPU', 'backend': 'CPU',
                              'verified': True, 'available': True}],
                'resources': {'cpu_percent': 42.3, 'ram_used_gb': 10.1, 'ram_total_gb': 32.0},
            }))
            hardware = load_hardware(Path(directory))
        self.assertEqual(hardware['resources'],
                         {'cpu_percent': 42.3, 'ram_used_gb': 10.1, 'ram_total_gb': 32.0})

    def test_implausible_resources_are_dropped_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'hardware-status.json').write_text(json.dumps({
                'worker_status': 'running', 'recommended': 'cpu',
                'available': [{'id': 'cpu', 'name': 'CPU', 'backend': 'CPU',
                              'verified': True, 'available': True}],
                'resources': {'cpu_percent': 142.0, 'ram_used_gb': 99.0, 'ram_total_gb': 32.0},
            }))
            hardware = load_hardware(Path(directory))
        self.assertIsNone(hardware['resources'])

    def test_missing_resources_default_to_none(self):
        hardware = load_hardware(Path('/definitely-missing'))
        self.assertIsNone(hardware['resources'])
