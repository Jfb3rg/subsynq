import http.client
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, '/ai')
import worker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class QwenConnectionTests(unittest.TestCase):
    def response(self):
        return Response(json.dumps({'status': 'ok', 'text': 'Hello'}).encode())

    def test_disconnect_then_success_retries_same_request(self):
        request = worker.urllib.request.Request('http://qwen/identify', data=b'audio')
        with patch.object(worker.urllib.request, 'urlopen', side_effect=[
                http.client.RemoteDisconnected('closed'), self.response()]) as opening, \
                patch.object(worker.time, 'sleep') as sleeping:
            self.assertEqual(worker.qwen_request_json(request)['text'], 'Hello')
        self.assertEqual(opening.call_count, 2)
        self.assertIs(opening.call_args_list[0].args[0], request)
        self.assertIs(opening.call_args_list[1].args[0], request)
        sleeping.assert_called_once_with(10)

    def test_partial_http_response_is_transport_failure(self):
        with patch.object(worker.urllib.request, 'urlopen', side_effect=[
                http.client.IncompleteRead(b'', 20), self.response()]), \
                patch.object(worker.time, 'sleep'):
            self.assertEqual(worker.qwen_request_json('http://qwen')['status'], 'ok')

    def test_unreachable_stops_after_bounded_retries(self):
        with patch.object(worker.urllib.request, 'urlopen', side_effect=
                urllib.error.URLError('refused')) as opening, \
                patch.object(worker.time, 'sleep') as sleeping:
            with self.assertRaisesRegex(RuntimeError, 'Tekniskt anslutningsfel'):
                worker.qwen_request_json('http://qwen')
        self.assertEqual(opening.call_count, 3)
        self.assertEqual(sleeping.call_count, 2)

    def test_temporary_http_error_retries(self):
        error = urllib.error.HTTPError('http://qwen', 503, 'starting', {},
                                       io.BytesIO(b'{}'))
        with patch.object(worker.urllib.request, 'urlopen', side_effect=[
                error, self.response()]), patch.object(worker.time, 'sleep'):
            self.assertEqual(worker.qwen_request_json('http://qwen')['status'], 'ok')

    def test_server_model_failure_and_bad_request_are_not_retried(self):
        for code in (400, 500):
            with self.subTest(code=code):
                error = urllib.error.HTTPError('http://qwen', code, 'bad', {},
                        io.BytesIO(b'{"error":"model failure"}'))
                with patch.object(worker.urllib.request, 'urlopen', side_effect=error) as opening, \
                        patch.object(worker.time, 'sleep') as sleeping:
                    with self.assertRaisesRegex(RuntimeError, 'model failure'):
                        worker.qwen_request_json('http://qwen')
                opening.assert_called_once()
                sleeping.assert_not_called()

    def test_invalid_json_fails_closed(self):
        for payload in (b'not-json', b'[]'):
            with patch.object(worker.urllib.request, 'urlopen', return_value=Response(payload)), \
                    patch.object(worker.time, 'sleep') as sleeping:
                with self.assertRaisesRegex(RuntimeError, 'ogiltigt JSON'):
                    worker.qwen_request_json('http://qwen')
                sleeping.assert_not_called()

    def test_readiness_waits_for_startup(self):
        with patch.object(worker, 'qwen_health', side_effect=[None, {'status': 'ready'}]) as health, \
                patch.object(worker.time, 'sleep') as sleeping:
            worker.wait_for_qwen()
        self.assertEqual(health.call_count, 2)
        sleeping.assert_called_once_with(5)

    def test_readiness_never_silently_skips_verification(self):
        with patch.object(worker, 'qwen_health', return_value=None) as health, \
                patch.object(worker.time, 'sleep') as sleeping:
            with self.assertRaisesRegex(RuntimeError, 'ingen kvalitetsbedömning'):
                worker.wait_for_qwen(attempts=3)
        self.assertEqual(health.call_count, 3)
        self.assertEqual(sleeping.call_count, 2)

    def test_user_can_cancel_readiness_wait(self):
        with patch.object(worker, 'check_cancelled', side_effect=worker.JobCancelled('stop')), \
                patch.object(worker, 'qwen_health') as health:
            with self.assertRaises(worker.JobCancelled):
                worker.wait_for_qwen({'id': 1})
        health.assert_not_called()

    def test_health_handles_disconnect(self):
        with patch.object(worker, 'QWEN_ASR_ENABLED', True), \
                patch.object(worker, 'QWEN_ASR_URL', 'http://qwen'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=
                             http.client.RemoteDisconnected('closed')):
            self.assertIsNone(worker.qwen_health())

    def test_identification_and_alignment_both_use_transport_retries(self):
        for operation in (worker.qwen_identify, worker.qwen_transcribe):
            with self.subTest(operation=operation.__name__), tempfile.TemporaryDirectory() as folder:
                audio = Path(folder) / 'clip.wav'
                audio.write_bytes(b'RIFF-test')
                with patch.object(worker, 'QWEN_ASR_URL', 'http://qwen'), \
                        patch.object(worker.urllib.request, 'urlopen', side_effect=[
                            http.client.RemoteDisconnected('closed'), self.response()]), \
                        patch.object(worker.time, 'sleep'):
                    self.assertEqual(operation(audio)['text'], 'Hello')

    def test_unload_also_recovers_from_disconnect(self):
        ready = Response(b'{"status":"ready","model_loaded":false}')
        with patch.object(worker, 'QWEN_ASR_URL', 'http://qwen'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=[
                http.client.RemoteDisconnected('closed'), ready]), \
                patch.object(worker.time, 'sleep'):
            worker.qwen_unload()


if __name__ == '__main__':
    unittest.main()
