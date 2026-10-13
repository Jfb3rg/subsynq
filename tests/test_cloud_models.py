import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import cloud_cpu_service as gateway
import cloud_gpu
import worker
from test_cloud_cpu import bootstrap_of

TOKEN = 'T' * 24
PEM_CERT = '-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----'
PEM_KEY = '-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----'


def build(models, **extra):
    return cloud_gpu.build_chain_onstart_script(
        TOKEN, models, device='cuda', tls_cert=PEM_CERT, tls_key=PEM_KEY,
        gateway_source='# gateway', qwen_source='# qwen',
        qwen_constraints='torch==2.9.1+cpu\nnumpy==2', whisper_constraints='av==18', **extra)


class GatewayAliasTests(unittest.TestCase):
    def test_aliases_are_parsed_leniently(self):
        self.assertEqual(gateway.parse_model_aliases('a:1=b:2, c=d ,broken,=x,y='), {'a:1': 'b:2', 'c': 'd'})
        self.assertEqual(gateway.parse_model_aliases(''), {})
        self.assertEqual(gateway.parse_model_aliases(None), {})

    def test_only_the_model_name_of_a_known_alias_is_swapped(self):
        aliases = {'gemma3:12b': 'gemma3:27b'}
        body = json.dumps({'model': 'gemma3:12b', 'prompt': 'hej', 'options': {'seed': 1}}).encode()
        swapped = json.loads(gateway.apply_model_alias(body, aliases))
        self.assertEqual(swapped, {'model': 'gemma3:27b', 'prompt': 'hej', 'options': {'seed': 1}})
        other = json.dumps({'model': 'gemma4:12b'}).encode()
        self.assertEqual(gateway.apply_model_alias(other, aliases), other)
        for untouched in (b'not json', b'[1,2]', b'{"prompt": "x"}'):
            self.assertEqual(gateway.apply_model_alias(untouched, aliases), untouched)
        self.assertEqual(gateway.apply_model_alias(body, {}), body)

    def test_the_phase_name_shows_the_model_that_really_runs(self):
        aliases = {'gemma3:12b': 'gemma3:27b'}
        body = gateway.apply_model_alias(json.dumps({'model': 'gemma3:12b'}).encode(), aliases)
        self.assertEqual(gateway.ollama_phase(body), 'ollama-gemma3:27b')
        unload = gateway.apply_model_alias(json.dumps({'model': 'gemma3:12b', 'keep_alive': 0}).encode(), aliases)
        self.assertIsNone(gateway.ollama_phase(unload))


class OnstartAliasTests(unittest.TestCase):
    def test_the_rented_server_pulls_the_large_models_and_answers_to_the_usual_names(self):
        script = bootstrap_of(build(['gemma3:27b', 'gemma4:26b'], model_aliases={'gemma3:12b': 'gemma3:27b', 'gemma4:12b': 'gemma4:26b'},
                       min_vram_gib=22))
        for needle in ('ollama pull gemma3:27b', 'ollama pull gemma4:26b', 'SUBSYNQ_CLOUD_OLLAMA_MODELS=gemma3:27b,gemma4:26b',
                       'SUBSYNQ_CLOUD_MODEL_ALIASES=gemma3:12b=gemma3:27b,gemma4:12b=gemma4:26b',
                       'SUBSYNQ_CLOUD_MIN_VRAM_GIB=22'):
            self.assertIn(needle, script)
        self.assertNotIn('ollama pull gemma3:12b', script)

    def test_without_aliases_the_script_is_exactly_as_before(self):
        script = bootstrap_of(build(['gemma3:12b', 'gemma4:12b']))
        self.assertNotIn('MODEL_ALIASES', script)
        self.assertNotIn('MIN_VRAM_GIB', script)
        self.assertIn('ollama pull gemma3:12b', script)

    def test_hostile_names_and_numbers_are_refused(self):
        for aliases in ({'a b': 'gemma3:27b'}, {'gemma3:12b': 'x;rm -rf /'}, {'gemma3:12b': ''}):
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                build(['gemma3:27b'], model_aliases=aliases)
        for bad in (0, 500, '22', True, float('nan')):
            with self.subTest(vram=bad), self.assertRaises(ValueError):
                build(['gemma3:27b'], min_vram_gib=bad)


class WorkerPlanTests(unittest.TestCase):
    def plan(self, mode, translation='', review='', local=('gemma3:12b', 'gemma4:12b')):
        with patch.object(worker, 'MODEL_NAME', local[0]), patch.object(worker, 'REVIEW_MODEL_NAME', local[1]), \
                patch.object(worker, 'CLOUD_TRANSLATION_MODEL', translation), patch.object(worker, 'CLOUD_REVIEW_MODEL', review):
            return worker.cloud_model_plan(mode)

    def test_default_is_the_home_models_with_no_aliases(self):
        self.assertEqual(self.plan('cuda'), (['gemma3:12b', 'gemma4:12b'], {}, None))

    def test_large_models_apply_to_a_rented_gpu_only(self):
        models, aliases, vram = self.plan('cuda', 'gemma3:27b', 'gemma4:26b')
        self.assertEqual(models, ['gemma3:27b', 'gemma4:26b'])
        self.assertEqual(aliases, {'gemma3:12b': 'gemma3:27b', 'gemma4:12b': 'gemma4:26b'})
        self.assertEqual(vram, worker.CLOUD_LARGE_MODEL_MIN_VRAM_GIB)
        self.assertEqual(self.plan('cpu', 'gemma3:27b', 'gemma4:26b'), (['gemma3:12b', 'gemma4:12b'], {}, None))

    def test_only_one_of_the_two_can_be_larger(self):
        models, aliases, _ = self.plan('cuda', translation='gemma3:27b')
        self.assertEqual((models, aliases), (['gemma3:27b', 'gemma4:12b'], {'gemma3:12b': 'gemma3:27b'}))

    def test_models_of_30b_and_up_need_a_32_gb_card_and_smaller_ones_a_24_gb_card(self):
        self.assertEqual(self.plan('cuda', 'gemma3:27b', 'gemma4:26b')[2], worker.CLOUD_LARGE_MODEL_MIN_VRAM_GIB)
        self.assertEqual(worker.CLOUD_LARGE_MODEL_MIN_VRAM_GIB, 22)
        self.assertEqual(self.plan('cuda', 'gemma3:27b', 'gemma4:31b')[2], 30)
        self.assertEqual(self.plan('cuda', 'gemma4:31b', 'gemma4:31b')[2], 30)
        self.assertEqual(worker.cloud_min_vram_gib(['llama:70b']), 30)
        self.assertEqual(worker.cloud_min_vram_gib(['gemma4:e4b', 'gemma3:12b']), 22)
        self.assertEqual(worker.cloud_min_vram_gib(['gemma4:31b-it-q4_K_M']), 30)
        self.assertEqual(worker.cloud_min_vram_gib(['custom']), 22)

    def test_one_model_used_for_both_roles_gets_one_alias(self):
        models, aliases, _ = self.plan('cuda', 'gemma3:27b', 'gemma4:26b', local=('gemma3:12b', 'gemma3:12b'))
        self.assertEqual((models, aliases), (['gemma3:27b', 'gemma3:27b'], {'gemma3:12b': 'gemma3:27b'}))

class AutoModelTests(unittest.TestCase):
    """"auto" picks the models from the VRAM of the card that is actually rented (only combinations that have run)."""

    def plan(self, vram, translation='auto', review='auto', mode='cuda'):
        with patch.object(worker, 'MODEL_NAME', 'gemma3:12b'), patch.object(worker, 'REVIEW_MODEL_NAME', 'gemma4:12b'), \
                patch.object(worker, 'CLOUD_TRANSLATION_MODEL', translation), patch.object(worker, 'CLOUD_REVIEW_MODEL', review):
            return worker.cloud_model_plan(mode, vram)

    def test_a_24_gb_card_gets_the_large_models_and_a_16_gb_card_the_usual_ones(self):
        self.assertEqual(self.plan(23.99)[0], ['gemma3:27b', 'gemma4:26b'])
        self.assertEqual(self.plan(24.0)[1], {'gemma3:12b': 'gemma3:27b', 'gemma4:12b': 'gemma4:26b'})
        self.assertEqual(self.plan(31.8)[0], ['gemma3:27b', 'gemma4:26b'], 'bigger cards use the tested pair until a larger one has been tried')
        self.assertEqual(self.plan(15.9), (['gemma3:12b', 'gemma4:12b'], {}, None))
        self.assertEqual(self.plan(22.9), (['gemma3:12b', 'gemma4:12b'], {}, None))

    def test_unknown_vram_and_cpu_rentals_keep_the_usual_models(self):
        for vram in (None, 0, -1, 'x', True):
            self.assertEqual(self.plan(vram)[0], ['gemma3:12b', 'gemma4:12b'], vram)
        self.assertEqual(self.plan(48.0, mode='cpu')[0], ['gemma3:12b', 'gemma4:12b'])

    def test_auto_can_be_mixed_with_a_named_model_and_is_case_insensitive(self):
        self.assertEqual(self.plan(24.0, 'AUTO', 'gemma4:31b')[0], ['gemma3:27b', 'gemma4:31b'])
        self.assertEqual(self.plan(16.0, 'Auto', 'gemma4:31b')[0], ['gemma3:12b', 'gemma4:31b'])
        self.assertEqual(self.plan(24.0, 'gemma3:27b', 'auto')[0], ['gemma3:27b', 'gemma4:26b'])
        self.assertEqual(self.plan(24.0, '', 'auto')[0], ['gemma3:12b', 'gemma4:26b'])

    def test_the_vram_guard_follows_the_chosen_models(self):
        self.assertEqual(self.plan(24.0)[2], worker.CLOUD_LARGE_MODEL_MIN_VRAM_GIB)
        self.assertEqual(self.plan(24.0, 'auto', 'gemma4:31b')[2], worker.CLOUD_HUGE_MODEL_MIN_VRAM_GIB)


class OfferVramTests(unittest.TestCase):
    def test_offers_carry_their_vram_so_the_models_can_follow_it(self):
        import cloud_market
        vast = cloud_market.normalize_vast({'offers': [
            {'id': 1, 'gpu_name': 'RTX 4090', 'dph_total': 0.4, 'gpu_ram': 24564},
            {'id': 2, 'gpu_name': 'RTX 5090', 'dph_total': 0.7, 'gpu_ram': 32607},
            {'id': 3, 'gpu_name': 'Mystery', 'dph_total': 0.1}]})
        self.assertEqual({offer['gpu']: offer['vram_gb'] for offer in vast},
                         {'Mystery': None, 'RTX 4090': 24.0, 'RTX 5090': 31.8})
        runpod = cloud_market.normalize_runpod_chain_gpus({'data': {'gpuTypes': [{
            'id': 'NVIDIA GeForce RTX 4090', 'displayName': 'RTX 4090', 'memoryInGb': 24, 'secureCloud': True,
            'lowestPrice': {'uninterruptablePrice': 0.69, 'stockStatus': 'High', 'availableGpuCounts': [1]}}]}})
        self.assertEqual(runpod[0]['vram_gb'], 24.0)


class ActiveModelLabelTests(unittest.TestCase):
    """The queue shows which model a job is using right now, never a stale or hard-coded name."""

    def test_home_models_are_shown_when_nothing_runs_in_the_cloud(self):
        worker.MODEL_JOB.cloud_models = None
        with patch.object(worker, 'MODEL_NAME', 'gemma3:12b'), patch.object(worker, 'REVIEW_MODEL_NAME', 'gemma4:12b'):
            self.assertEqual(worker.active_gemma_models(), ('gemma3:12b', 'gemma4:12b'))

    def test_the_rented_servers_models_are_shown_when_they_differ(self):
        worker.MODEL_JOB.cloud_models = ('gemma3:27b', 'gemma4:31b')
        self.addCleanup(setattr, worker.MODEL_JOB, 'cloud_models', None)
        self.assertEqual(worker.active_gemma_models(), ('gemma3:27b', 'gemma4:31b'))

    def test_no_stage_label_in_the_worker_is_a_hard_coded_gemma_name(self):
        source = open(worker.__file__, encoding='utf-8').read()
        self.assertNotIn('Gemma 3 12B', source)
        for label in ("f'Översättning med {active_gemma_models()[0]}'", "f'Granskning med {active_gemma_models()[1]}'",
                      "f'Källkontroll med {active_gemma_models()[0]}'"):
            self.assertIn(label, source)


if __name__ == "__main__":
    unittest.main()
