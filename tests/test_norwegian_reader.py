import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import norwegian_reader
import worker
from asr_windows import _INDEPENDENT_FAMILIES, _family, reading_alternatives

# The language probes of the real jobs (dev 202 Agnes mot verden, 203 Glemselens øy, 201 MAN OF WAR, 135 a Danish trailer)
AGNES = [{'language': 'no', 'probability': .935, 'speech_seconds': 9.1}, {'language': 'no', 'probability': .953, 'speech_seconds': 11.0},
         {'language': 'no', 'probability': .937, 'speech_seconds': 8.2}]
GLEMSEL = [{'language': 'no', 'probability': .936, 'speech_seconds': 6.0}, {'language': 'nn', 'probability': .613, 'speech_seconds': 5.1},
           {'language': 'no', 'probability': .942, 'speech_seconds': 7.4}]


class DecisionTests(unittest.TestCase):
    def test_norwegian_files_are_read(self):
        self.assertTrue(norwegian_reader.decide(AGNES)[0])
        self.assertTrue(norwegian_reader.decide(GLEMSEL)[0])

    def test_other_languages_and_too_little_speech_are_not(self):
        english = [{'language': 'en', 'probability': .99, 'speech_seconds': 9}] * 3
        danish = [{'language': 'da', 'probability': .9, 'speech_seconds': 9}] * 3
        mixed = [AGNES[0], {'language': 'en', 'probability': .99, 'speech_seconds': 9}, {'language': 'sv', 'probability': .9, 'speech_seconds': 9}]
        for readings in (english, danish, mixed, AGNES[:1], [dict(item, speech_seconds=.5) for item in AGNES], []):
            self.assertFalse(norwegian_reader.decide(readings)[0], readings)

    def test_the_repository_can_be_switched_off(self):
        self.assertEqual(norwegian_reader.repository({}), 'NbAiLab/nb-whisper-large')
        self.assertEqual(norwegian_reader.repository({'SUBSYNQ_NORWEGIAN_WHISPER': '0'}), '')


class PackTests(unittest.TestCase):
    def test_the_pack_is_optional_and_off_until_switched_on(self):
        models = json.loads(Path('/app/models.json').read_text(encoding='utf-8'))['models']
        pack = next(model for model in models if model['id'] == norwegian_reader.PACK_ID)
        self.assertEqual((pack['kind'], pack['name'], pack['optional'], pack['language']), ('faster-whisper', norwegian_reader.REPOSITORY, True, 'no'))

    def test_it_is_its_own_independent_family(self):
        self.assertEqual(_family(norwegian_reader.READING_NAME), 'nb-whisper')
        self.assertIn('nb-whisper', _INDEPENDENT_FAMILIES)

    def test_its_reading_becomes_a_supported_candidate(self):
        # Agnes (job 202) row 33: the file has the short pass at 0.18; rescue, large-v2 and NB-Whisper read "Finn det du elsker."
        row = {'start': 112.88, 'end': 114.4, 'text': 'Finn deg du elsker.', 'confidence': .18}
        readings = {'precision': [row], 'rescue': [{'start': 112.61, 'end': 114.23, 'text': 'Finn det du elsker.'}],
                    'v2': [{'start': 113.02, 'end': 114.62, 'text': 'Finn det du elsker.'}],
                    'nb': [{'start': 112.7, 'end': 114.5, 'text': 'Finn det du elsker.'}]}
        candidates = reading_alternatives([row], readings)[0]
        self.assertEqual((candidates[0]['text'], candidates[0]['support']), ('Finn det du elsker.', 3))

    def test_a_swap_is_found_when_the_readings_hold_extra_words_at_the_start(self):
        # Agnes mot verden (dev job 209): the long pass "hjemme", large-v2 and NB-Whisper "gjemt" with "har seriøst" in front
        from asr_windows import settle_v2_words_with_qwen, _single_word_swap
        row = {'start': 94.28, 'end': 98.8, 'text': 'en kilo heroin hjemme der, og så ber du meg om å bare være her.'}
        v2 = [{'start': 93.98, 'end': 98.96, 'text': 'har seriøst en kilo heroin gjemt der, og så ber du meg om å bare være her.'}]
        nb = [{'start': 95.5, 'end': 98.8, 'text': 'har seriøst en kilo heroin gjemt der, og så ber du meg om å bare være her?'}]
        self.assertEqual(settle_v2_words_with_qwen([row], [], v2, nb)[0][0]['text'], 'en kilo heroin gjemt der, og så ber du meg om å bare være her.')
        self.assertIsNone(_single_word_swap('3 kilometers up the ridge'.split(), 'three kilometers up the ridge'.split()))
        self.assertIsNone(_single_word_swap('we go there now'.split(), 'we went over there later'.split()), 'two changes')

    def test_a_word_large_v2_and_nb_whisper_both_hear_replaces_the_short_passs(self):
        from asr_windows import settle_v2_words_with_qwen
        row = {'start': 112.88, 'end': 114.4, 'text': 'Finn deg du elsker.'}
        v2 = [{'start': 113.02, 'end': 114.62, 'text': 'Finn det du elsker.'}]
        nb = [{'start': 112.7, 'end': 114.5, 'text': 'Finn det du elsker.'}]
        rows, changes = settle_v2_words_with_qwen([row], [], v2, nb)
        self.assertEqual(rows[0]['text'], 'Finn det du elsker.')
        self.assertEqual(settle_v2_words_with_qwen([row], [], v2)[1], [], 'large-v2 alone is not enough')


class FakeModels:
    def __init__(self, remote=None):
        self.remote, self.created, self.released = remote, [], 0

    def create_whisper(self, *args, **kwargs):
        self.created.append(args[0])
        return object()

    def release_whisper(self):
        self.released += 1


class WorkerTests(unittest.TestCase):
    def run_it(self, *, enabled=True, present=True, readings=AGNES, read=None, remote=None):
        job = {'id': 5, 'swedish_reader': {'readings': readings}}
        models = FakeModels(remote)
        with patch.object(worker.model_manager, 'enabled_packs', return_value={norwegian_reader.PACK_ID} if enabled else set()), \
                patch.object(worker.swedish_reader, 'model_present', return_value=present), patch.object(worker, 'update_job'):
            rows = worker.read_norwegian_places(job, '/m', None, models, 'cpu', 100.0, '/tmp', read or (lambda reader: [{'text': 'Finn det du elsker.'}]))
        return job, models, rows

    def test_a_norwegian_file_with_the_pack_on_is_read(self):
        job, models, rows = self.run_it()
        self.assertEqual(rows, [{'text': 'Finn det du elsker.'}])
        self.assertEqual(models.created, [norwegian_reader.REPOSITORY])
        self.assertEqual(models.released, 1)
        self.assertTrue(job['norwegian_reader']['chosen'])

    def test_off_missing_or_not_norwegian_reads_nothing(self):
        for kwargs, reason in (({'enabled': False}, 'avstängt'), ({'present': False}, 'inte hämtad'),
                               ({'readings': [{'language': 'en', 'probability': .99, 'speech_seconds': 9}] * 3}, 'inte som norska')):
            job, models, rows = self.run_it(**kwargs)
            self.assertEqual((rows, models.created), ([], []), kwargs)
            self.assertIn(reason, job['norwegian_reader']['reason'])
        job, models, rows = self.run_it(present=False, remote=object())
        self.assertEqual(models.created, [norwegian_reader.REPOSITORY], 'a rented server downloads the model itself')

    def test_a_failure_never_fails_the_job_and_frees_the_model(self):
        def broken(reader):
            raise OSError('läsfel')
        job, models, rows = self.run_it(read=broken)
        self.assertEqual(rows, [])
        self.assertEqual(models.released, 1)
        self.assertIn('läsfel', job['norwegian_reader']['reason'])

    def test_wiring(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('v2_profile = run_profile('), source.index('nb_profile = read_norwegian_places('))
        self.assertIn("consensus_readings[norwegian_reader.READING_NAME] = nb_profile", source)
        self.assertIn('settle_v2_words_with_qwen(merged, qwen_reading, v2_profile, nb_profile)', source)
        self.assertIn("reading_model=reader, collect_words=True, language_code='no'))", source)
        self.assertIn("whole_file = models.remote is not None or device != 'cpu'", source)
        self.assertNotIn('qwen_unreadable_language', source, 'Qwen keeps reading Norwegian (the user, 2026-10-25)')


class BootScriptRoomTests(unittest.TestCase):
    def test_the_real_gpu_boot_script_keeps_room_under_the_limit(self):
        # 0.8.318 (NB-Whisper in the gateway's allowed models) left 75 characters; a run of the suite then hit 16 003 (the throw-away
        # certificate varies the packed length). The constraint copies now lose their comments; keep at least 150 characters spare.
        import secrets
        import cloud_gpu
        sources = {name: Path('/ai', name).read_text(encoding='utf-8') for name in (
            'cloud_cpu_service.py', 'qwen_asr_service.py', 'constraints-qwen.txt', 'constraints-worker.txt')}
        for vram in (16, 24, 48):
            models, aliases, minimum = worker.cloud_model_plan('cuda', vram)
            longest = 0
            for _ in range(4):
                cert, key = worker.make_chain_certificate()
                script = cloud_gpu.build_chain_onstart_script(
                    secrets.token_urlsafe(24), models, device='cuda', tls_cert=cert, tls_key=key,
                    gateway_source=sources['cloud_cpu_service.py'], qwen_source=sources['qwen_asr_service.py'],
                    qwen_constraints=sources['constraints-qwen.txt'], whisper_constraints=sources['constraints-worker.txt'],
                    model_aliases=aliases or None, min_vram_gib=minimum)
                longest = max(longest, len(script))
            self.assertLessEqual(longest, cloud_gpu.MAX_ONSTART_CHARS - 150, vram)


if __name__ == '__main__':
    unittest.main()
