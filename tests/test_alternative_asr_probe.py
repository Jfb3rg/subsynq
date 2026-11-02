import sys
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/ai')
from alternative_asr_probe import main, nemo_text, window, checked_local_cases, character_times, audio_content
from alternative_asr_probe import monitored_cpu, assign_checkpoint
from alternative_asr_probe import checked_candidate_cases, audio_choice_prompt, parse_audio_choice
from alternative_asr_probe import transcribe_omni_bytes, seamless_loading_info, seamless_reading
from alternative_asr_probe import owsm_local_config, owsm_reading


class AlternativeAsrProbeTests(unittest.TestCase):
    def test_owsm_preserves_model_configuration_and_requires_checkpoint_language_support(self):
        from pathlib import Path
        config = {'model': 'espnet_ctc', 'frontend_conf': {'fs': '16k'},
                  'token_list': ['<asr>', '<na>', '<eng>', '<nob>', '<tam>'],
                  'bpemodel': 'data/token_list/bpe_unigram50000/bpe.model',
                  'normalize_conf': {'stats_file': 'exp/s2t_stats_raw_bpe50000/train/feats_stats.npz'},
                  'encoder_conf': {'num_blocks': 27, 'use_flash_attn': True}}
        actual = owsm_local_config(config, Path('/results/pinned'), ['en', 'no', 'ta'])
        self.assertEqual(actual['encoder_conf'], config['encoder_conf'])
        self.assertTrue(actual['bpemodel'].startswith('/results/pinned/'))
        self.assertFalse(config['bpemodel'].startswith('/results/'))
        with self.assertRaises(ValueError):
            owsm_local_config(config, Path('/results/pinned'), ['de'])
        config['normalize_conf']['stats_file'] = '/config/unexpected.npz'
        with self.assertRaises(ValueError):
            owsm_local_config(config, Path('/results/pinned'), ['en'])

    def test_owsm_rejects_unsupported_language_or_overlong_input_before_install(self):
        import alternative_asr_probe
        for language, end in [('xx', 2), ('no', 32)]:
            case = {'job_id': 312, 'start': 1, 'end': end, 'label': 'invalid', 'language': language}
            with patch.object(sys, 'argv', ['probe', '--local-model-test', '--family', 'owsm',
                                          '--cases-json', json.dumps([case])]), \
                    patch('alternative_asr_probe.subprocess.run') as process, self.assertRaises(ValueError):
                alternative_asr_probe.local_model_test()
            process.assert_not_called()

    def test_owsm_native_input_is_fresh_language_guided_asr_without_previous_words(self):
        import numpy as np
        requests = []
        class Model:
            def batch_decode(self, audio, **kwargs):
                requests.append((audio, kwargs))
                return 'native transcription'
        audio = np.ones(32000, dtype=np.float32)
        text, info = owsm_reading(Model(), audio, 'no')
        audio[:] = 0
        owsm_reading(Model(), audio, 'ta')
        self.assertEqual(text, ['native transcription'])
        self.assertEqual(requests[0][1], {'batch_size': 1, 'lang_sym': '<nob>', 'task_sym': '<asr>'})
        self.assertEqual(requests[1][1]['lang_sym'], '<tam>')
        self.assertTrue(np.all(requests[0][0] == 1))
        self.assertEqual(info['input_samples'], 32000)
        with self.assertRaises(ValueError):
            owsm_reading(Model(), np.zeros(30*16000+1), 'en')

    def test_seamless_speech_only_loader_rejects_missing_asr_weights_but_records_unused_task_heads(self):
        allowed = {'unexpected_keys': ['text_encoder.layers.0.weight', 't2u_model.weight', 'vocoder.weight']}
        self.assertEqual(seamless_loading_info(allowed)['unexpected_keys'], allowed['unexpected_keys'])
        for invalid in ({'missing_keys': ['speech_encoder.layer.weight']},
                        {'unexpected_keys': ['speech_encoder.wrong.weight']},
                        {'mismatched_keys': ['shared.weight']}, {'error_msgs': ['bad checkpoint']}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                seamless_loading_info(invalid)

    def test_seamless_requires_source_language_before_any_runtime_download(self):
        import alternative_asr_probe
        for language in (None, 'xx'):
            case = {'job_id': 312, 'start': 1, 'end': 2, 'label': 'language'}
            if language:
                case['language'] = language
            with patch.object(sys, 'argv', ['probe', '--local-model-test', '--family', 'seamless',
                                          '--cases-json', json.dumps([case])]), \
                    patch('alternative_asr_probe.subprocess.run') as process, self.assertRaises(ValueError):
                alternative_asr_probe.local_model_test()
            process.assert_not_called()

    def test_seamless_uses_current_waveform_and_source_language_without_text_candidates(self):
        import numpy as np
        requests, waveforms = [], []
        class Inputs(dict):
            def to(self, *args, **kwargs):
                return self
        class Processor:
            def __call__(self, **kwargs):
                waveforms.append(kwargs)
                return Inputs(input_features=np.zeros((1, len(kwargs['audio'])//160, 160)))
            def batch_decode(self, ids, **kwargs):
                return ['Norwegian words']
        class Model:
            dtype = 'float32'
            generation_config = SimpleNamespace(eos_token_id=3)
            def generate(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(sequences=np.array([[3, 256064, 10, 3]]))
        audio = np.ones(32000, dtype=np.float32)
        _, first = seamless_reading(Model(), Processor(), audio, 'no')
        audio[:] = 0
        seamless_reading(Model(), Processor(), audio, 'ta')
        self.assertEqual([r['tgt_lang'] for r in requests], ['nob', 'tam'])
        self.assertEqual(set(requests[0]), {'input_features', 'tgt_lang', 'do_sample', 'max_new_tokens', 'return_dict_in_generate'})
        self.assertTrue(np.all(waveforms[0]['audio'] == 1))
        self.assertTrue(np.all(waveforms[1]['audio'] == 0))
        self.assertTrue(first['eos_reached'])
        self.assertFalse(first['reached_token_cap'])

    def test_omni_gets_fresh_immutable_audio_when_the_same_path_is_rewritten(self):
        from pathlib import Path
        snapshots = []
        class CachedPathReader:
            def transcribe(self, inputs, **kwargs):
                # A native reader may cache path mappings. The diagnostic must
                # supply bytes instead; retaining the old call must fail here.
                self_case.assertIsInstance(inputs[0], bytes)
                snapshots.append(inputs[0])
                self_case.assertEqual(kwargs, {'lang': ['tam_Taml'], 'batch_size': 1})
                return ['a reading']
        self_case = self
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.wav'
            path.write_bytes(b'first audio')
            reader = CachedPathReader()
            self.assertEqual(transcribe_omni_bytes(reader, path, 'tam_Taml'), ['a reading'])
            path.write_bytes(b'longer second audio')
            transcribe_omni_bytes(reader, path, 'tam_Taml')
            self.assertEqual(snapshots, [b'first audio', b'longer second audio'])

    def test_native_input_only_check_rejects_other_families_before_installation(self):
        import alternative_asr_probe
        cases = json.dumps([{'job_id': 336, 'start': 1, 'end': 2, 'label': 'input', 'language': 'ta'}])
        with patch.object(sys, 'argv', ['probe', '--local-model-test', '--family', 'tamil',
                                      '--input-check-only', '--cases-json', cases]), \
                patch('alternative_asr_probe.subprocess.run') as process, self.assertRaises(ValueError):
            alternative_asr_probe.local_model_test()
        process.assert_not_called()

    def test_bounded_families_reject_unsupported_language_before_runtime_or_model_download(self):
        import alternative_asr_probe
        cases = json.dumps([{'job_id': 336, 'start': 1, 'end': 2, 'label': 'unsupported', 'language': 'ta'}])
        for family in ('granite', 'parakeet'):
            with self.subTest(family=family), \
                    patch.object(sys, 'argv', ['probe', '--local-model-test', '--family', family, '--cases-json', cases]), \
                    patch('alternative_asr_probe.subprocess.run') as process, self.assertRaises(ValueError):
                alternative_asr_probe.local_model_test()
            process.assert_not_called()

    def test_candidate_input_is_bounded_and_references_cannot_be_hidden_outside_the_pool(self):
        case = {'job_id': 308, 'start': 2.5, 'end': 4.5, 'label': 'control', 'language': 'en',
                'candidates': ['spoken words', 'other words', 'third reading'],
                'provenance': 'saved diagnostic report', 'control_reference': 'spoken words'}
        self.assertEqual(checked_candidate_cases(json.dumps([case])), [case])
        for value in ([], [case]*9, [{**case, 'candidates': ['same']*3}],
                      [{**case, 'control_reference': 'a new invented transcript'}],
                      [{**case, 'provenance': ''}], [{**case, 'end': 50}],
                      [{**case, 'no_audio': True}], [{**case, 'expected': 'hidden answer'}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checked_candidate_cases(json.dumps(value))

    def test_audio_choice_tracks_candidate_identity_after_order_reversal_and_rejects_prose_or_truncation(self):
        candidates = ['first reading', 'second reading', 'third reading']
        answer = lambda text, reason='stop': {'choices': [{'finish_reason': reason, 'message': {'content': text}}]}
        self.assertEqual(parse_audio_choice(answer('A'), candidates)['chosen_text'], 'first reading')
        self.assertEqual(parse_audio_choice(answer('C'), list(reversed(candidates)))['chosen_text'], 'first reading')
        self.assertEqual(parse_audio_choice(answer('NONE'), candidates)['decision'], 'none')
        for response in (answer('A because it sounds right'), answer('D'), answer('A', 'length'), {}):
            with self.subTest(response=response):
                self.assertEqual(parse_audio_choice(response, candidates)['decision'], 'invalid')
        prompt = audio_choice_prompt(candidates)
        self.assertIn('NONE', prompt)
        self.assertIn('"A": "first reading"', prompt)
        self.assertNotIn('control_reference', prompt)
        self.assertNotIn('provenance', prompt)

    def test_checkpoint_assignment_preserves_storage_and_rejects_incomplete_weights(self):
        try:
            import torch
        except ImportError:
            self.skipTest('PyTorch storage contract is also checked in the isolated model runtime')
        model = torch.nn.Linear(2, 1)
        original_parameter = model.weight
        weight, bias = torch.tensor([[2., 3.]]), torch.tensor([4.])
        assign_checkpoint(model, [('weight', weight), ('bias', bias)])
        self.assertIs(model.weight, original_parameter)
        self.assertEqual(model.weight.data_ptr(), weight.data_ptr())
        self.assertEqual(model(torch.tensor([[1., 2.]])).item(), 12.)
        with self.assertRaises(RuntimeError):
            assign_checkpoint(model, [('weight', weight)])
        with self.assertRaises(ValueError):
            assign_checkpoint(model, [('weight', weight), ('weight', weight)])

    def test_low_available_memory_stops_our_test_process_group(self):
        from unittest.mock import Mock
        import signal
        child = Mock(pid=12345)
        child.poll.return_value = None
        with patch('alternative_asr_probe.subprocess.Popen', return_value=child), \
                patch('alternative_asr_probe.available_memory', return_value=1024**3), \
                patch('os.killpg') as kill:
            with self.assertRaises(MemoryError):
                monitored_cpu(['diagnostic-child'], {})
        kill.assert_called_once_with(12345, signal.SIGTERM)
        child.wait.assert_called_once_with(timeout=10)

    def test_audio_attachment_is_real_wav_not_a_silently_ignored_text_field(self):
        import base64
        import io
        import wave
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 160)
        data = stream.getvalue()
        content = audio_content('transcribe', data)
        self.assertEqual(content[1]['type'], 'input_audio')
        self.assertEqual(base64.b64decode(content[1]['input_audio']['data']), data)
        self.assertEqual(audio_content('transcribe', None), [{'type': 'text', 'text': 'transcribe'}])
        with self.assertRaises((ValueError, wave.Error, EOFError)):
            audio_content('transcribe', b'raw PCM without a RIFF header')

    def test_local_comparison_never_accepts_answer_hints_or_unbounded_input(self):
        case = {'job_id': 313, 'start': 97, 'end': 103, 'label': 'blind'}
        self.assertEqual(checked_local_cases(json.dumps([case])), [case])
        for cases in ([], [case] * 17, [{**case, 'expected': 'a suggested transcript'}],
                      [{**case, 'end': 120}], [{**case, 'job_id': True}]):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                checked_local_cases(json.dumps(cases))

    def test_ctc_numpy_offsets_are_saved_as_json_numbers_at_original_media_time(self):
        import numpy as np
        result = character_times([{'char': 'ø', 'start_offset': np.int64(3),
                                   'end_offset': np.int64(5)}], 97., np.int64(320))
        self.assertEqual(json.loads(json.dumps(result)), [{'char': 'ø', 'start_offset': 3,
            'end_offset': 5, 'start': 97.06, 'end': 97.1}])

    def test_bounded_window(self):
        self.assertEqual(window(0, 25), 25)
        self.assertEqual(window(12.8, 18.8), 6)
        self.assertEqual(window(1, 61), 60)

    def test_invalid_window(self):
        for start, end in [(-1, 5), (0, 61), (5, 5), (6, 3)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                window(start, end)

    def test_nonfinite_window(self):
        for start, end in [(float('nan'), 5), (0, float('inf')), (float('-inf'), 5)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                window(start, end)

    def test_existing_output_is_rejected_before_loading_models(self):
        with tempfile.NamedTemporaryFile(suffix='.json') as existing:
            argv = ['probe', 'unused.mp4', '--engine', 'mms', '--model', 'unused',
                    '--output', existing.name]
            with patch.object(sys, 'argv', argv), self.assertRaises(FileExistsError):
                main()

    def test_nemo_string_hypothesis_and_tuple_outputs(self):
        self.assertEqual(nemo_text(['speech']), 'speech')
        self.assertEqual(nemo_text([SimpleNamespace(text='speech')]), 'speech')
        self.assertEqual(nemo_text((['speech'], None)), 'speech')

    def test_nemo_silence_remains_empty(self):
        self.assertEqual(nemo_text(['']), '')
        self.assertEqual(nemo_text([SimpleNamespace(text='')]), '')

    def test_nemo_missing_multiple_or_invalid_text_is_rejected(self):
        for result in [[], (), ['a', 'b'], 'not a list', [None], [SimpleNamespace(text=None)]]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                nemo_text(result)
