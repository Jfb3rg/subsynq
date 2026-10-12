import io
import json
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class OllamaConnectionTests(unittest.TestCase):
    def test_http_model_error_is_not_retried_45_times(self):
        error = urllib.error.HTTPError('http://model', 500, 'failed', {},
                                       io.BytesIO(b'{"error":"runner timed out"}'))
        with patch.object(worker.urllib.request, 'urlopen', side_effect=error) as opening, \
                patch.object(worker.time, 'sleep') as sleeping:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 500.*runner timed out'):
                worker.call_ollama({'model': 'test'})
        opening.assert_called_once()
        sleeping.assert_not_called()

    def test_transport_refusal_has_only_three_attempts(self):
        with patch.object(worker.urllib.request, 'urlopen', side_effect=
                          urllib.error.URLError('refused')) as opening, \
                patch.object(worker.time, 'sleep') as sleeping:
            with self.assertRaises(RuntimeError):
                worker.call_ollama({'model': 'test'})
        self.assertEqual(opening.call_count, 3)
        self.assertEqual(sleeping.call_count, 2)

    def test_timeout_does_not_repeat_expensive_inference(self):
        for error in (TimeoutError(), urllib.error.URLError(TimeoutError())):
            with self.subTest(error=error), \
                    patch.object(worker.urllib.request, 'urlopen', side_effect=error) as opening, \
                    patch.object(worker.time, 'sleep') as sleeping:
                with self.assertRaises(RuntimeError):
                    worker.call_ollama({'model': 'test'})
                opening.assert_called_once()
                sleeping.assert_not_called()

    def test_normal_unload_is_success_not_spurious_warning(self):
        result = {'done': True, 'done_reason': 'unload'}
        with patch.object(worker.urllib.request, 'urlopen', return_value=Response(json.dumps(result).encode())):
            self.assertEqual(worker.call_ollama({'model': 'test', 'keep_alive': 0}), result)

    def test_unload_response_is_not_accepted_as_generated_translation(self):
        with patch.object(worker.urllib.request, 'urlopen', return_value=Response(
                b'{"done":true,"done_reason":"unload"}')):
            with self.assertRaises(RuntimeError):
                worker.call_ollama({'model': 'test', 'prompt': 'translate'})

    def test_cancelled_job_does_not_start_model_request(self):
        with patch.object(worker.MODEL_JOB, 'job_id', 99, create=True), \
                patch.object(worker, 'check_cancelled', side_effect=worker.JobCancelled('stop')), \
                patch.object(worker.urllib.request, 'urlopen') as opening:
            with self.assertRaises(worker.JobCancelled):
                worker.call_ollama({'model': 'test'})
        opening.assert_not_called()

    def test_cancellation_is_checked_after_response_before_accepting_it(self):
        with patch.object(worker.MODEL_JOB, 'job_id', 99, create=True), \
                patch.object(worker, 'check_cancelled', side_effect=[None, worker.JobCancelled('stop')]), \
                patch.object(worker.urllib.request, 'urlopen', return_value=Response(
                    b'{"done_reason":"stop","response":"text"}')):
            with self.assertRaises(worker.JobCancelled):
                worker.call_ollama({'model': 'test'})

    def test_cancelled_job_with_http_failure_is_cancelled_not_quality_failed(self):
        error = urllib.error.HTTPError('http://model', 500, 'failed', {}, io.BytesIO(b'{}'))
        with patch.object(worker.MODEL_JOB, 'job_id', 99, create=True), \
                patch.object(worker, 'check_cancelled', side_effect=[None, worker.JobCancelled('stop')]), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(worker.JobCancelled):
                worker.call_ollama({'model': 'test'})


if __name__ == '__main__':
    unittest.main()
