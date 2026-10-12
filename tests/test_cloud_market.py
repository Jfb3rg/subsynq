import json
import tempfile
import unittest
from pathlib import Path

from cloud_cost import render_cloud_market
from cloud_market import (CHAIN_GPU_DOWNLOAD_TIERS, fetch_offers, fetch_vast_chain_gpu_offers,
                          load_keys, normalize_gpu_query, normalize_runpod,
                          normalize_vast, remove_key, save_key)
from server import jobs_signature, library_return_path, render_queue_content


class CloudMarketTests(unittest.TestCase):
    def test_vast_keeps_only_rentable_single_gpu_offers_with_real_price(self):
        offers = normalize_vast({'offers': [
            {'id': 2, 'gpu_name': 'RTX 5060 Ti', 'dph_total': 0.30, 'gpu_ram': 16384, 'num_gpus': 1},
            {'id': 3, 'gpu_name': 'RTX 4090', 'dph_total': 0.40, 'rentable': False},
            {'id': 4, 'gpu_name': 'RTX 5090', 'dph_total': 0.80, 'num_gpus': 2},
            {'id': 5, 'gpu_name': 'RTX 3090', 'dph_total': 'nan'},
        ]})
        self.assertEqual([offer['id'] for offer in offers], ['2'])
        self.assertEqual(offers[0]['price'], 0.30)

    def test_runpod_hides_unknown_or_unavailable_gpu(self):
        payload = {'data': {'gpuTypes': [
            {'id': 'RTX 5060 Ti', 'displayName': 'RTX 5060 Ti', 'lowestPrice': None},
            {'id': 'RTX 4090', 'displayName': 'RTX 4090', 'lowestPrice':
             {'stockStatus': 'High', 'uninterruptablePrice': 0.75, 'availableGpuCounts': [1]}},
            {'id': 'RTX 3090', 'lowestPrice':
             {'stockStatus': 'None', 'uninterruptablePrice': 0.4, 'availableGpuCounts': [1]}},
        ]}}
        self.assertEqual([offer['id'] for offer in normalize_runpod(payload)], ['RTX 4090'])

    def test_queries_are_read_only_and_provider_specific(self):
        def vast_reader(request):
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.full_url, 'https://console.vast.ai/api/v0/bundles/')
            self.assertIn('Bearer secret-key', request.get_header('Authorization'))
            self.assertEqual(json.loads(request.data)['num_gpus'], {'eq': 1})
            return {'offers': []}
        self.assertEqual(fetch_offers('vast', 'secret-key', vast_reader), [])

        def runpod_reader(request):
            self.assertEqual(request.get_method(), 'POST')
            self.assertIn('api_key=secret-key', request.full_url)
            self.assertIn('gpuTypes', json.loads(request.data)['query'])
            return {'data': {'gpuTypes': []}}
        self.assertEqual(fetch_offers('runpod', 'secret-key', runpod_reader), [])

    def test_public_prices_do_not_require_api_key(self):
        def vast_reader(request):
            self.assertIsNone(request.get_header('Authorization'))
            return {'offers': [{'id': 12, 'gpu_name': 'RTX 5060 Ti',
                                'dph_total': 0.25, 'num_gpus': 1}]}
        self.assertEqual(fetch_offers('vast', reader=vast_reader)[0]['gpu'], 'RTX 5060 Ti')

        def runpod_reader(request):
            self.assertEqual(request.full_url, 'https://api.runpod.io/graphql')
            return {'data': {'gpuTypes': []}}
        self.assertEqual(fetch_offers('runpod', reader=runpod_reader), [])

    def test_exact_vast_gpu_search_avoids_top_result_limit(self):
        self.assertEqual(normalize_gpu_query('rtx 5060 ti'), 'RTX 5060 Ti')
        def reader(request):
            self.assertEqual(json.loads(request.data)['gpu_name'], {'eq': 'RTX 5060 Ti'})
            return {'offers': []}
        self.assertEqual(fetch_offers('vast', 'secret-key', reader, 'RTX 5060 Ti'), [])

    def test_keys_are_local_and_never_rendered(self):
        with tempfile.TemporaryDirectory() as directory:
            save_key(directory, 'vast', 'secret-key')
            self.assertEqual(load_keys(directory), {'vast': 'secret-key'})
            self.assertNotIn('secret-key', render_cloud_market(
                'vast', True, {'offers': [], 'fetched_at': None, 'error': None}, 'token'))
            remove_key(directory, 'vast')
            self.assertEqual(load_keys(directory), {})
            self.assertFalse((Path(directory) / 'cloud-api-keys.json').exists())

    def test_live_dropdown_escapes_provider_values(self):
        page = render_cloud_market('vast', True,
                                   {'offers': [{'id': '1', 'gpu': '<script>', 'price': 0.1, 'detail': ''}],
                                    'fetched_at': 'now', 'error': None}, 'token')
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)


class NavigationTests(unittest.TestCase):
    def test_delete_redirect_matches_library_kind(self):
        self.assertEqual(library_return_path('media', 'deleting'), '/library?kind=media&job=deleting')
        self.assertEqual(library_return_path('movie', 'deleting'), '/library?kind=movie&job=deleting')

    def test_queue_signature_changes_with_job_progress(self):
        jobs = {'/extra/a': {'id': 1, 'status': 'queued', 'progress': 0, 'media_path': '/extra/a'}}
        before = jobs_signature(jobs)
        page = render_queue_content([], jobs, 'token')
        self.assertIn(f"data-jobs-signature='{before}'", page)
        jobs['/extra/a']['progress'] = 30
        self.assertNotEqual(before, jobs_signature(jobs))


class VastChainGpuOfferTests(unittest.TestCase):
    """Whole-chain GPU rentals choose the HOST by network speed; the GPU model stays the user's."""

    @staticmethod
    def offer(offer_id, price, down, reliability=0.99, disk=200, gpu='RTX 3090'):
        return {'id': offer_id, 'gpu_name': gpu, 'dph_total': price, 'num_gpus': 1, 'gpu_ram': 24576,
                'inet_down': down, 'reliability': reliability, 'disk_space': disk,
                'rentable': True, 'rented': False}

    def reader_for(self, offers, seen):
        def reader(request):
            body = json.loads(request.data)
            seen.append(body)
            return {'offers': [item for item in offers
                               if item['inet_down'] >= body['inet_down']['gte']]}
        return reader

    def test_the_cheap_slow_host_is_skipped_for_a_faster_one(self):
        seen = []
        offers = [self.offer(1, 0.156, 15), self.offer(2, 0.160, 86), self.offer(3, 0.177, 763),
                  self.offer(4, 0.192, 859)]
        result = fetch_vast_chain_gpu_offers('key', 'RTX 3090', self.reader_for(offers, seen))
        self.assertEqual([item['id'] for item in result], ['3', '4'])
        self.assertIn('ner 763 Mbit/s', result[0]['detail'])
        self.assertIn('tillförl. 0.99', result[0]['detail'])
        self.assertEqual(len(seen), 1)  # the first tier (500) was enough

    def test_the_query_keeps_the_chosen_gpu_and_asks_for_speed_reliability_and_disk(self):
        seen = []
        for gpu in ('RTX 5060 Ti', 'RTX 3090', 'RTX 4090'):
            fetch_vast_chain_gpu_offers('key', gpu, self.reader_for([self.offer(1, .2, 900, gpu=gpu)], seen))
        self.assertEqual([body['gpu_name'] for body in seen],
                         [{'eq': 'RTX 5060 Ti'}, {'eq': 'RTX 3090'}, {'eq': 'RTX 4090'}])
        for body in seen:
            self.assertEqual(body['inet_down'], {'gte': CHAIN_GPU_DOWNLOAD_TIERS[0]})
            self.assertEqual(body['reliability'], {'gte': 0.95})
            self.assertEqual(body['disk_space'], {'gte': 80})
            self.assertEqual((body['num_gpus'], body['order']), ({'eq': 1}, [['dph_total', 'asc']]))

    def test_it_falls_back_to_the_lower_tier_only_when_no_host_reaches_the_first(self):
        seen = []
        result = fetch_vast_chain_gpu_offers(
            'key', 'RTX 4090', self.reader_for([self.offer(7, .4, 350), self.offer(8, .5, 120)], seen))
        self.assertEqual([item['id'] for item in result], ['7'])
        self.assertEqual([body['inet_down']['gte'] for body in seen], list(CHAIN_GPU_DOWNLOAD_TIERS))

    def test_nothing_is_offered_when_no_host_is_fast_enough(self):
        self.assertEqual(fetch_vast_chain_gpu_offers(
            'key', 'RTX 5060 Ti', self.reader_for([self.offer(1, .1, 40), self.offer(2, .1, 250)], [])), [])

    def test_the_filters_are_enforced_locally_even_if_the_server_ignores_them(self):
        lax = lambda request: {'offers': [self.offer(1, .1, 900, reliability=0.5),
                                          self.offer(2, .1, 900, disk=20),
                                          self.offer(3, .1, 100),
                                          self.offer(4, .3, 900)]}
        result = fetch_vast_chain_gpu_offers('key', 'RTX 3090', lax)
        self.assertEqual([item['id'] for item in result], ['4'])

    def test_an_empty_or_garbled_model_name_and_reply_are_refused(self):
        with self.assertRaises(ValueError):
            fetch_vast_chain_gpu_offers('key', '  ', lambda request: {'offers': []})
        with self.assertRaises(ValueError):
            fetch_vast_chain_gpu_offers('key', 'RTX 3090', lambda request: {'nope': 1})
        with self.assertRaises(ValueError):
            fetch_vast_chain_gpu_offers('key', 'RTX; DROP', lambda request: {'offers': []})


if __name__ == '__main__':
    unittest.main()
