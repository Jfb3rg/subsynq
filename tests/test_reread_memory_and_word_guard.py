import copy
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
import source_judge as judge

CURRENT = "name is stanislav kanyev he's got quite a reputation i need him waiting to kiev now"
OPTIONS = [CURRENT,
 "His name is Stanislav Konev. He's got quite a reputation. I need a way in the key now.",
 "His name is Stanislav Konev. He's got quite a reputation. I need a way in the Kiev, now.",
 "His name is Stanislav Kanyev. He's got quite a reputation. I need a way to Kiev now.",
 "His name is Stanislav Koniev. He's got quite a reputation.",
 "His name is Stanislav Kanyev. He's got quite a reputation. I need a way to Kiev now. His name is Stanislav Kanyev. He's. He's got quite a reputation."]


def fixture():
    return [{'id': 9, 'start': 31.91, 'end': 37.95, 'source': CURRENT,
             'alternatives': [{'text': text, 'support': 2, 'current_support': 2} for text in OPTIONS[1:]]}]


def answer(letter):
    return lambda request: {'response': json.dumps({'rows': [{'id': 9, 'a_has_error': True, 'choice': letter}]})}


class SourceWordProtectionTests(unittest.TestCase):
    def test_real_jobs_160_164_cannot_change_kiev_to_key(self):
        before = fixture()
        original = copy.deepcopy(before)
        result, decisions = judge.choose_readings(before, answer('B'), 'fake')
        self.assertEqual(result[0]['source'], CURRENT)
        self.assertFalse(decisions[0]['applied'])
        self.assertEqual(decisions[0]['reason'], 'supported_words_removed')
        self.assertIn('kiev', decisions[0]['preserved_words'])
        self.assertEqual(before, original)

    def test_preserving_reading_still_allowed(self):
        result, decisions = judge.choose_readings(fixture(), answer('D'), 'fake')
        self.assertTrue(decisions[0]['applied'])
        self.assertEqual(result[0]['source'], OPTIONS[3])

    def test_prompt_exposes_words_but_not_a_fabricated_new_reading(self):
        items = judge.judge_items(fixture())
        body = json.loads(judge.build_request('fake', items)['prompt'])
        self.assertIn('kiev', body['rows'][0]['preserve_words'])
        self.assertEqual(list(body['rows'][0]['candidates'].values()), OPTIONS)

    def test_punctuation_and_casing_do_not_make_distinct_votes(self):
        self.assertEqual(judge.supported_words('Go to Kiev now.',
            ['Go to Kiev now.', 'Come to Kiev now.', 'COME TO KIEV NOW!']), [])

    def test_short_other_utterances_do_not_protect_a_word(self):
        self.assertEqual(judge.supported_words(CURRENT, [CURRENT, 'Kiev now.', 'To Kiev.']), [])

    def test_standard_all_right_alright_spelling_is_not_a_removed_word(self):
        current = 'way you make it your life all right lights on me this'
        candidates = [current, 'The way you make it your life. All right, lights on me.',
                      'The way you make it your life. Alright, lights on me.',
                      'And why you make it your life? All right lights on me']
        for chosen in candidates[1:3]:
            self.assertEqual(judge.removed_supported_words(current, chosen, candidates), [])
        self.assertNotEqual(judge._choice_words('all. Right'), judge._choice_words('alright'))

    def test_non_english_supported_word_is_protected_without_a_movie_rule(self):
        current = 'Til hvert ser jeg at folk kan begynne å abonnere på Spriten, altså.'
        alternatives = [current, current.replace('Til hvert', 'Etter hvert'), current.replace('ser jeg', 'ser vi')]
        self.assertIn('altså', judge.removed_supported_words(current, current.replace(', altså', ''), alternatives))

    def test_current_reading_always_kept_when_chosen(self):
        result, decisions = judge.choose_readings(fixture(), answer('A'), 'fake')
        self.assertEqual(result[0]['source'], CURRENT)
        self.assertFalse(decisions[0]['applied'])

    def test_existing_minimal_german_fix_not_blocked(self):
        current = 'Meine nicht. Ich und ihr Vater, wir sind zusammen in den Teams.'
        chosen = current.replace('nicht.', 'Nichte.')
        rows = [{'id': 9, 'source': current, 'alternatives': [{'text': chosen, 'support': 1, 'current_support': 2}]}]
        result, decisions = judge.choose_readings(rows, answer('B'), 'fake')
        self.assertTrue(decisions[0]['applied'])
        self.assertEqual(result[0]['source'], chosen)

    def test_existing_neighbour_and_repetition_guards_still_apply(self):
        rows = fixture()
        rows[0]['alternatives'].append({'text': OPTIONS[3] + ' His name is Stanislav Kanyev.', 'support': 3})
        result, decisions = judge.choose_readings(rows, answer('F'), 'fake')
        self.assertFalse(decisions[0]['applied'])
        self.assertEqual(result[0]['source'], CURRENT)


class RereadMemorySwitchTests(unittest.TestCase):
    def read(self, device, acknowledgement='unload'):
        events = []
        class Whisper:
            def __init__(self, *args, **kwargs):
                events.append('whisper')
                if events[0] != 'unload':
                    raise AssertionError('ASR loaded before language model release')
            def transcribe(self, *args, **kwargs):
                return iter([SimpleNamespace(text='Hello there.', avg_logprob=-.1)]), SimpleNamespace(language='en', language_probability=.99)
        def unload(request):
            events.append('unload')
            self.assertEqual(request, {'model': 'reviewer', 'keep_alive': 0})
            return {'done_reason': acknowledgement}
        def qwen(*args, **kwargs):
            events.append('qwen')
            return {'text': 'Hello there.'}
        job = {'id': 164, 'language_policy': {'effective_language': 'en'}}
        with patch.object(worker.MODEL_JOB, 'device', device, create=True), \
                patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'reviewer', create=True), \
                patch.object(worker.MODEL_JOB, 'cleanup_pending', None, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', 164, create=True), \
                patch.object(worker, 'call_ollama', side_effect=unload), \
                patch.object(worker, 'WhisperModel', Whisper), patch.object(worker.subprocess, 'run'), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'trim_cpu_allocator'), \
                patch.object(worker, 'qwen_health', return_value=True), patch.object(worker, 'qwen_identify', side_effect=qwen), \
                patch.object(worker, 'release_cloud_gpu_memory', return_value={}), patch.object(worker, 'qwen_unload'):
            if acknowledgement != 'unload':
                with self.assertRaisesRegex(RuntimeError, 'frigjorde inte minnet'):
                    worker.reread_readings(job, Path('/media/fake.mp4'), [{'id': 6, 'start': 26.37, 'end': 27.51, 'source': 'Hello there.'}], [6])
                return events, None
            result = worker.reread_readings(job, Path('/media/fake.mp4'), [{'id': 6, 'start': 26.37, 'end': 27.51, 'source': 'Hello there.'}], [6])
            self.assertFalse(worker.MODEL_JOB.used)
            self.assertIsNone(worker.MODEL_JOB.active_model)
            self.assertEqual(worker.MODEL_JOB.job_id, 164)
        return events, result

    def test_release_precedes_both_asr_readers_on_cloud_gpu(self):
        for device in ['cloud-vast', 'cloud-runpod', 'cloud-cpu-vast', 'cpu']:
            with self.subTest(device=device):
                events, result = self.read(device)
                self.assertEqual(events, ['unload', 'whisper', 'whisper', 'qwen'])
                self.assertEqual([r['name'] for r in result[6]], ['precision', 'v2', 'qwen'])

    def test_unacknowledged_release_prevents_loading_asr(self):
        for acknowledgement in ['stop', None]:
            with self.subTest(acknowledgement=acknowledgement):
                events, _ = self.read('cloud-vast', acknowledgement)
                self.assertEqual(events, ['unload'])

    def test_cloud_release_uses_active_endpoint_and_only_unloads(self):
        url = 'https://cloud.invalid/ollama/api/generate'
        with patch.object(worker.MODEL_JOB, 'used', True, create=True), \
                patch.object(worker.MODEL_JOB, 'active_model', 'reviewer', create=True), \
                patch.object(worker.MODEL_JOB, 'cleanup_pending', None, create=True), \
                patch.object(worker.MODEL_JOB, 'job_id', 164, create=True), \
                patch.object(worker.MODEL_JOB, 'device', 'cloud-vast', create=True), \
                patch.object(worker, 'TRANSLATE_URL', url), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'log_model_timing'), \
                patch.object(worker.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"done_reason":"unload"}')) as transport:
            worker.release_language_model_before_asr()
            request = transport.call_args.args[0]
            self.assertEqual(request.full_url, url)
            self.assertEqual(json.loads(request.data), {'model': 'reviewer', 'keep_alive': 0})
            self.assertFalse(worker.MODEL_JOB.used)


if __name__ == '__main__':
    unittest.main()

