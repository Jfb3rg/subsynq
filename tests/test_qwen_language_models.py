import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
import worker


def load_service(asr, aligner, environment=None):
    """Import ai/qwen_asr_service.py with fake torch/qwen_asr (sequential mode off: models stay resident in the process)."""
    fake_torch = SimpleNamespace(float32='float32', float16='float16', bfloat16='bfloat16',
                                 cuda=SimpleNamespace(is_available=lambda: False))
    spec = importlib.util.spec_from_file_location('qwen_service_language_test', Path('/ai/qwen_asr_service.py'))
    service = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'torch': fake_torch, 'qwen_asr': SimpleNamespace(Qwen3ASRModel=asr, Qwen3ForcedAligner=aligner)}), \
            patch.dict(os.environ, {'SUBSYNQ_QWEN_SEQUENTIAL': '0', **(environment or {})}):
        spec.loader.exec_module(service)
    return service


class ServiceLanguageModelTests(unittest.TestCase):
    """Tamil is not a language of the official Qwen3-ASR (its own FLEURS-Tamil result is 153 % WER); a community fine-tune of the same
    architecture answers it (1,475 h of Tamil, 803 h of them conversational). Everything else keeps the official model."""

    def setUp(self):
        self.asr = Mock()
        self.asr.from_pretrained.side_effect = lambda name, **kwargs: SimpleNamespace(name=name, forced_aligner=None)
        self.aligner = Mock()
        self.service = load_service(self.asr, self.aligner)

    def test_tamil_clues_choose_the_fine_tune_and_pass_no_language(self):
        for clue in ('tamil', 'Tamil', 'ta', 'TAM'):
            with self.subTest(clue=clue):
                self.assertEqual(self.service.language_model(clue), ('lemuralabs/tamil-asr-qwen3', None))

    def test_other_languages_keep_the_official_model_and_their_clue(self):
        self.assertEqual(self.service.language_model('French'), (self.service.ASR_MODEL, 'French'))
        self.assertEqual(self.service.language_model(None), (self.service.ASR_MODEL, None))
        self.assertEqual(self.service.language_model(''), (self.service.ASR_MODEL, None))

    def test_the_tamil_model_can_be_switched_off(self):
        service = load_service(self.asr, self.aligner, {'SUBSYNQ_QWEN_TAMIL_MODEL': ''})
        self.assertEqual(service.language_model('tamil'), (service.ASR_MODEL, 'tamil'))

    def test_the_fine_tune_is_loaded_without_the_aligner_and_replaces_the_official_model(self):
        official = self.service.load_model(with_alignment=False)
        self.assertEqual(official.name, self.service.ASR_MODEL)
        tamil = self.service.load_model(with_alignment=True, language='tamil')
        self.assertEqual(tamil.name, 'lemuralabs/tamil-asr-qwen3')
        self.assertIsNone(tamil.forced_aligner, 'the aligner does not know Tamil')
        self.aligner.from_pretrained.assert_not_called()
        again = self.service.load_model(with_alignment=False, language='ta')
        self.assertIs(again, tamil, 'the same fine-tune is not loaded twice')
        back = self.service.load_model(with_alignment=False)
        self.assertEqual(back.name, self.service.ASR_MODEL, 'asking for another language swaps the model back')
        self.assertEqual(self.asr.from_pretrained.call_count, 3)

    def test_health_names_the_language_models(self):
        self.assertEqual(self.service.LANGUAGE_MODELS, {'tamil': 'lemuralabs/tamil-asr-qwen3'})


class WorkerLanguageClueTests(unittest.TestCase):
    def response(self):
        return io.BytesIO(json.dumps({'status': 'ok', 'text': 'x', 'language': 'Tamil'}).encode())

    def sent_headers(self, **arguments):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'clip.wav'
            audio.write_bytes(b'RIFF-test')
            with patch.object(worker, 'QWEN_ASR_URL', 'http://qwen'), \
                    patch.object(worker, 'qwen_request_json', return_value={'status': 'ok', 'text': 'x'}) as sending:
                worker.qwen_identify(audio, **arguments)
        return sending.call_args.args[0].headers

    def test_a_language_clue_travels_with_the_window(self):
        self.assertEqual(self.sent_headers(language='tamil').get('X-subsynq-language'), 'tamil')

    def test_no_clue_sends_no_header(self):
        self.assertNotIn('X-subsynq-language', self.sent_headers())

    def test_tamil_is_the_language_with_a_fine_tune_and_reads_every_doubtful_cue(self):
        self.assertEqual(worker.QWEN_LANGUAGE_HINTS, {'ta': 'tamil'})
        self.assertGreaterEqual(worker.QWEN_HINTED_DOUBTFUL_WINDOWS, 30)


if __name__ == '__main__':
    unittest.main()
