import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = ['/ai', '/app']
import worker


class ASRTraceTests(unittest.TestCase):
    def test_real_job_saves_profiles_even_without_diagnostic_environment(self):
        with tempfile.TemporaryDirectory() as d, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(d)), \
                patch.dict(os.environ, {'SUBSYNQ_ASR_TRACE_OUTPUT': '/invalid/ignored'}):
            payload = {'context': [{'text': "You're here."}], 'final': []}
            path = worker.save_asr_trace({'id': 29}, payload)
            self.assertEqual(path.name, 'job-29.asr-trace.json')
            self.assertEqual(json.loads(path.read_text())['context'], payload['context'])
            fallback = worker.save_asr_trace({'id': 29, 'fallback_reason': 'bad source'}, payload)
            self.assertNotEqual(path, fallback)
            self.assertTrue(path.exists())

    def test_negative_probe_is_opt_in_and_respects_its_output(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(worker.save_asr_trace({'id': -1}, {}))
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SUBSYNQ_ASR_TRACE_OUTPUT': d+'/trace.json'}):
            path = worker.save_asr_trace({'id': -1}, {'final': []})
            self.assertEqual(json.loads(path.read_text())['job_id'], -1)
