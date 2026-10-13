import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import cloud_market
from cloud_cost import render_cloud_market
from cloud_market import (CHAIN_GPU_DOWNLOAD_TIERS, country_from_geolocation, fetch_offers, flag_emoji,
                          order_offers_for_rental,
                          fetch_vast_chain_gpu_offers, region_group,
                          load_keys, normalize_gpu_query, normalize_runpod, normalize_vram_bands,
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



class VramFilterTests(unittest.TestCase):
    def offers(self):
        return {'offers': [
            {'id': 1, 'gpu_name': 'RTX 5060 Ti', 'dph_total': .20, 'gpu_ram': 16311},
            {'id': 2, 'gpu_name': 'RTX 4000 Ada', 'dph_total': .25, 'gpu_ram': 20475},
            {'id': 3, 'gpu_name': 'RTX 3090', 'dph_total': .30, 'gpu_ram': 24576},
            {'id': 4, 'gpu_name': 'RTX 4090', 'dph_total': .40, 'gpu_ram': 24564},
            {'id': 5, 'gpu_name': 'RTX 5090', 'dph_total': .60, 'gpu_ram': 32607},
            {'id': 6, 'gpu_name': 'Tesla V100', 'dph_total': .65, 'gpu_ram': 32768},
            {'id': 7, 'gpu_name': 'A100', 'dph_total': 1.1, 'gpu_ram': 40960},
            {'id': 8, 'gpu_name': 'RTX A6000', 'dph_total': 1.2, 'gpu_ram': 49140},
            {'id': 9, 'gpu_name': 'RTX 3060', 'dph_total': .10, 'gpu_ram': 12288},
            {'id': 10, 'gpu_name': 'Mystery', 'dph_total': .15},
        ]}

    def ids(self, bands):
        return [offer['id'] for offer in fetch_offers('vast', reader=lambda request: self.offers(), vram_bands=bands)]

    def test_unknown_choices_are_ignored_and_order_is_fixed(self):
        self.assertEqual(normalize_vram_bands(['over32', '16', 'nonsense', '16', ' 24 ']), ('16', '24', 'over32'))
        self.assertEqual(normalize_vram_bands(None), ())
        self.assertEqual(normalize_vram_bands(['<script>']), ())

    def test_each_class_picks_the_cards_sold_in_that_size(self):
        self.assertEqual(self.ids(('16',)), ['1', '2'])
        self.assertEqual(self.ids(('24',)), ['3', '4'])
        self.assertEqual(self.ids(('32',)), ['5', '6'])
        self.assertEqual(self.ids(('over32',)), ['7', '8'])

    def test_several_classes_are_added_together_and_none_means_everything(self):
        self.assertEqual(self.ids(('16', '32')), ['1', '2', '5', '6'])
        self.assertEqual(len(self.ids(())), 10, 'no box ticked shows every offer, also those without a known size')

    def test_offers_with_unknown_memory_are_hidden_when_a_size_is_chosen(self):
        self.assertNotIn('10', self.ids(('16', '24', '32', 'over32')))

    def test_vast_is_asked_for_the_chosen_sizes_so_the_cheapest_1000_are_not_all_small_cards(self):
        asked = []

        def reader(request):
            asked.append(json.loads(request.data).get('gpu_ram'))
            return {'offers': []}
        for bands in ((), ('24',), ('16', '32'), ('over32',), ('16', '24', '32', 'over32')):
            fetch_offers('vast', reader=reader, vram_bands=bands)
        self.assertEqual(asked, [None, {'gte': 23552, 'lt': 31744}, {'gte': 14848, 'lt': 33280},
                                 {'gte': 33280}, {'gte': 14848}])

    def test_the_classes_are_enforced_locally_even_if_the_server_ignores_the_range(self):
        # The reader returns every card whatever was asked.
        self.assertEqual(self.ids(('24',)), ['3', '4'])

    def test_runpod_is_filtered_on_its_reported_memory(self):
        payload = {'data': {'gpuTypes': [
            {'id': 'A', 'displayName': 'RTX 4090', 'memoryInGb': 24,
             'lowestPrice': {'stockStatus': 'High', 'uninterruptablePrice': .7, 'availableGpuCounts': [1]}},
            {'id': 'B', 'displayName': 'RTX A4000', 'memoryInGb': 16,
             'lowestPrice': {'stockStatus': 'High', 'uninterruptablePrice': .3, 'availableGpuCounts': [1]}},
            {'id': 'C', 'displayName': 'H100', 'memoryInGb': 80,
             'lowestPrice': {'stockStatus': 'Low', 'uninterruptablePrice': 2.5, 'availableGpuCounts': [1]}},
        ]}}
        got = lambda bands: [offer['id'] for offer in fetch_offers('runpod', reader=lambda request: payload, vram_bands=bands)]
        self.assertEqual(got(('24',)), ['A'])
        self.assertEqual(got(('16', 'over32')), ['B', 'C'])
        self.assertEqual(got(()), ['B', 'A', 'C'])

    def test_the_catalog_caches_each_choice_separately(self):
        calls = []

        def fake_fetch(provider, key='', reader=None, gpu_query='', vram_bands=()):
            calls.append(vram_bands)
            return []
        cloud_market._cache.clear()
        with patch.object(cloud_market, 'fetch_offers', fake_fetch):
            cloud_market.catalog('config', 'vast', vram_bands=('24',))
            cloud_market.catalog('config', 'vast', vram_bands=('24',))
            cloud_market.catalog('config', 'vast', vram_bands=('24', '32'))
            cloud_market.catalog('config', 'vast', vram_bands=['bogus'])
            cloud_market.catalog('config', 'vast')
        self.assertEqual(calls, [('24',), ('24', '32'), ()], 'repeated and empty/unknown choices reuse the cache')
        cloud_market._cache.clear()

    def render(self, provider='vast', bands=(), offers=None):
        result = {'offers': offers if offers is not None else [], 'fetched_at': 'now', 'error': None}
        return render_cloud_market(provider, False, result, 'token', '', '', bands)

    def test_the_page_shows_four_boxes_and_marks_the_chosen_ones(self):
        page = self.render(bands=('24', 'over32'))
        for label in ('16 GB', '24 GB', '32 GB', 'över 32 GB'):
            self.assertIn(label, page)
        self.assertEqual(page.count('type="checkbox" name="vram"'), 4)
        self.assertIn('value="24" checked', page)
        self.assertIn('value="over32" checked', page)
        self.assertNotIn('value="16" checked', page)
        self.assertIn('Inga lediga en-GPU-erbjudanden', page)
        self.assertIn('24 GB, över 32 GB', page, 'the empty message names the chosen sizes')

    def test_the_boxes_are_also_offered_for_runpod_without_a_name_search(self):
        page = self.render('runpod')
        self.assertIn('name="vram"', page)
        self.assertNotIn('id="gpu-query"', page)

    def test_the_choice_follows_the_refresh_and_use_price_forms(self):
        offers = [{'id': '1', 'gpu': 'RTX 4090', 'price': .4, 'detail': '24 GB VRAM', 'vram_mb': 24576}]
        page = self.render(bands=('24',), offers=offers)
        self.assertEqual(page.count('<input type="hidden" name="vram" value="24">'), 2)
        self.assertIn('Visar bara 24 GB (1 st)', page)
        self.assertEqual(self.render(offers=offers).count('type="hidden" name="vram"'), 0)



class RegionTests(unittest.TestCase):
    def test_the_country_is_read_from_vasts_location_text(self):
        for text, code in (('Texas, US', 'US'), ('Norway, NO', 'NO'), ('Kosovo, XK', 'XK'), ('Jiangsu, CN', 'CN'),
                           ('SE', 'SE'), ('Somewhere', ''), ('lower, us', ''), ('', ''), (None, ''), (12, '')):
            self.assertEqual(country_from_geolocation(text), code, text)

    def test_flags_are_built_from_the_country_code_only(self):
        self.assertEqual(flag_emoji('SE'), '\U0001F1F8\U0001F1EA')
        self.assertEqual(flag_emoji('US'), '\U0001F1FA\U0001F1F8')
        for bad in ('', 'se', 'SWE', 'S', None, '<s'):
            self.assertEqual(flag_emoji(bad), '')

    def test_europe_comes_first_then_north_america_then_the_rest(self):
        for code in ('SE', 'NO', 'DK', 'FI', 'DE', 'NL', 'GB', 'FR', 'ES', 'PL', 'RO', 'CZ', 'BG', 'XK', 'UA'):
            self.assertEqual(region_group(code), 'europe', code)
        for code in ('US', 'CA'):
            self.assertEqual(region_group(code), 'north-america', code)
        for code in ('CN', 'JP', 'TW', 'KR', 'TH', 'AU', 'CL', 'MX', 'ZA', 'RU', 'TR', 'IN'):
            self.assertEqual(region_group(code), 'world', code)

    def test_vast_offers_carry_where_the_server_stands(self):
        offers = normalize_vast({'offers': [
            {'id': 1, 'gpu_name': 'RTX 3090', 'dph_total': .2, 'gpu_ram': 24576, 'geolocation': 'Norway, NO'},
            {'id': 2, 'gpu_name': 'RTX 3090', 'dph_total': .3, 'gpu_ram': 24576, 'geolocation': 'Texas, US'},
            {'id': 3, 'gpu_name': 'RTX 3090', 'dph_total': .1, 'gpu_ram': 24576, 'geolocation': 'Jiangsu, CN'},
            {'id': 4, 'gpu_name': 'RTX 3090', 'dph_total': .4, 'gpu_ram': 24576},
        ]})
        by_id = {offer['id']: offer for offer in offers}
        self.assertEqual((by_id['1']['country'], by_id['1']['region']), ('NO', 'europe'))
        self.assertEqual((by_id['2']['country'], by_id['2']['region']), ('US', 'north-america'))
        self.assertEqual((by_id['3']['country'], by_id['3']['region']), ('CN', 'world'))
        self.assertEqual((by_id['4']['country'], by_id['4']['region'], by_id['4']['location']), ('', '', ''))

    def render(self, provider, offers):
        return render_cloud_market(provider, False, {'offers': offers, 'fetched_at': 'now', 'error': None}, 'token')

    def vast_offer(self, offer_id, gpu, price, location, country, region):
        return {'id': offer_id, 'gpu': gpu, 'price': price, 'detail': '24 GB VRAM', 'vram_mb': 24576,
                'location': location, 'country': country, 'region': region}

    def test_the_list_is_grouped_europe_first_each_group_cheapest_first_with_flags(self):
        page = self.render('vast', [
            self.vast_offer('1', 'RTX 3090 cn', .10, 'Jiangsu, CN', 'CN', 'world'),
            self.vast_offer('2', 'RTX 3090 us', .20, 'Texas, US', 'US', 'north-america'),
            self.vast_offer('3', 'RTX 3090 de', .30, 'Germany, DE', 'DE', 'europe'),
            self.vast_offer('4', 'RTX 3090 no', .25, 'Norway, NO', 'NO', 'europe'),
            self.vast_offer('5', 'RTX 3090 ??', .05, '', '', ''),
        ])
        labels = [label for label in ('Europa (närmast Sverige)', 'USA och Kanada', 'Övriga världen', 'Okänd plats')
                  if f'<optgroup label="{label}">' in page]
        self.assertEqual(labels, ['Europa (närmast Sverige)', 'USA och Kanada', 'Övriga världen', 'Okänd plats'])
        order = [page.index(name) for name in ('RTX 3090 no', 'RTX 3090 de', 'RTX 3090 us', 'RTX 3090 cn', 'RTX 3090 ??')]
        self.assertEqual(order, sorted(order), 'Norway (0.25) before Germany (0.30), then the USA, China, unknown')
        self.assertIn('\U0001F1F3\U0001F1F4 Norway, NO · RTX 3090 no', page)
        self.assertIn('Flaggan visar landet', page)

    def test_runpod_has_no_location_so_its_list_stays_flat(self):
        page = self.render('runpod', [{'id': 'A', 'gpu': 'RTX 4090', 'price': .7, 'detail': '24 GB VRAM', 'vram_mb': 24576}])
        self.assertNotIn('<optgroup', page)
        self.assertNotIn('Flaggan visar landet', page)

    def test_a_hostile_location_text_is_escaped(self):
        page = self.render('vast', [self.vast_offer('1', 'RTX 3090', .1, '<script>x</script>, US', 'US', 'north-america')])
        self.assertNotIn('<script>x', page)
        self.assertIn('&lt;script&gt;', page)



class RentalOrderTests(unittest.TestCase):
    def offer(self, offer_id, price, region):
        return {'id': offer_id, 'gpu': 'RTX 4090', 'price': price, 'region': region}

    def test_nearest_region_first_then_cheapest_and_nothing_above_the_limit(self):
        offers = [self.offer('cn', .20, 'world'), self.offer('us', .30, 'north-america'),
                  self.offer('no', .60, 'europe'), self.offer('pl', .40, 'europe'),
                  self.offer('de-dear', 1.50, 'europe'), self.offer('unknown', .10, '')]
        ordered = [item['id'] for item in order_offers_for_rental(offers, 1.0)]
        self.assertEqual(ordered, ['pl', 'no', 'us', 'cn', 'unknown'])

    def test_the_limit_is_inclusive_and_can_leave_nothing(self):
        offers = [self.offer('a', .5, 'europe'), self.offer('b', .5000001, 'europe')]
        self.assertEqual([item['id'] for item in order_offers_for_rental(offers, .5)], ['a'])
        self.assertEqual(order_offers_for_rental(offers, .1), [])

    def test_offers_without_a_region_keep_their_price_order(self):
        offers = [{'id': 'x', 'price': .9}, {'id': 'y', 'price': .1}, {'id': 'z', 'price': .5}]
        self.assertEqual([item['id'] for item in order_offers_for_rental(offers, 2.0)], ['y', 'z', 'x'])

    def test_no_limit_means_no_filter_and_a_missing_price_is_not_dropped(self):
        offers = [self.offer('a', 9.0, 'europe'), {'id': 'b', 'region': 'europe'}]
        self.assertEqual(len(order_offers_for_rental(offers, None)), 2)
        self.assertEqual([item['id'] for item in order_offers_for_rental(offers, 1.0)], ['b'])


if __name__ == "__main__":
    unittest.main()
