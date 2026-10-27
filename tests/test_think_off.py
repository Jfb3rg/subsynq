import io
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker


class ThinkingIsOffTests(unittest.TestCase):
    def send(self, request):
        sent = []

        def fake_urlopen(http_request, timeout=0):
            sent.append(json.loads(http_request.data))
            return io.BytesIO(json.dumps({'response': '{}', 'done_reason': 'unload' if request.get('keep_alive') == 0 else 'stop'}).encode())
        with patch.object(worker.urllib.request, 'urlopen', side_effect=fake_urlopen), patch.object(worker, 'log_model_timing'):
            worker.call_ollama(request)
        return sent[0]

    def test_a_request_without_the_switch_gets_think_false(self):
        self.assertIs(self.send({'model': 'gemma3:12b', 'prompt': 'x'})['think'], False)

    def test_a_request_that_sets_it_keeps_its_own_value_and_an_unload_gets_none(self):
        self.assertIs(self.send({'model': 'm', 'prompt': 'x', 'think': True})['think'], True)
        self.assertNotIn('think', self.send({'model': 'm', 'keep_alive': 0}))


if __name__ == '__main__':
    unittest.main()
