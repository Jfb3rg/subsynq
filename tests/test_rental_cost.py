import sys
import unittest
from pathlib import Path

sys.path.insert(0, '/app')
import cloud_market


def offer(identifier, price, traffic, location):
    return {'id': identifier, 'gpu_name': 'RTX 3090', 'dph_total': price, 'inet_down_cost': traffic, 'geolocation': location,
            'gpu_ram': 24576, 'num_gpus': 1, 'rentable': True, 'rented': False, 'machine_id': identifier}


class JobCostTests(unittest.TestCase):
    """Vast bills the hour and every downloaded gigabyte. Two Tamil jobs cost 0.87 USD each (balance 3.30 -> 1.55) where the hourly price
    alone said 0.06-0.12, because the whole chain downloads about 55 GB and a host's traffic price runs from 0.0007 to 0.027 USD/GB."""

    def test_the_estimate_is_hours_plus_traffic(self):
        self.assertEqual(cloud_market.estimated_job_cost(0.2, 0.013), round(0.2 * 0.5 + 0.013 * 55, 2))
        self.assertAlmostEqual(cloud_market.estimated_job_cost(0.371, 0.0007), 0.22, places=2)

    def test_an_unknown_traffic_price_is_assumed_to_be_in_the_middle(self):
        self.assertEqual(cloud_market.estimated_job_cost(0.3, None), round(0.3 * 0.5 + 0.01 * 55, 2))

    def test_normalized_offers_keep_the_traffic_price(self):
        items = cloud_market.normalize_vast({'offers': [offer(1, 0.3, 0.0026, 'Slovakia, SK'), offer(2, 0.3, None, 'Slovakia, SK')]})
        self.assertEqual([item['download_cost_per_gb'] for item in items], [0.0026, None])

    def test_within_a_region_the_cheaper_whole_job_comes_first_not_the_cheaper_hour(self):
        offers = cloud_market.normalize_vast({'offers': [offer(1, 0.21, 0.0156, 'Sweden, SE'), offer(2, 0.371, 0.0007, 'The Netherlands, NL')]})
        for item in offers:
            item['estimated_job_cost'] = cloud_market.estimated_job_cost(item['price'], item['download_cost_per_gb'])
        ranked = cloud_market.order_offers_for_rental(offers, None)
        self.assertEqual([item['id'] for item in ranked], ['2', '1'], 'the Dutch host costs 0.22 USD a job, the Swedish one 0.96')

    def test_a_preferred_country_gives_way_when_it_costs_far_more(self):
        offers = cloud_market.normalize_vast({'offers': [offer(1, 0.21, 0.0156, 'Sweden, SE'), offer(2, 0.371, 0.0007, 'The Netherlands, NL')]})
        for item in offers:
            item['estimated_job_cost'] = cloud_market.estimated_job_cost(item['price'], item['download_cost_per_gb'])
        self.assertEqual(cloud_market.order_offers_for_rental(offers, None, 'SE')[0]['id'], '2')
        cheap_sweden = cloud_market.normalize_vast({'offers': [offer(3, 0.25, 0.001, 'Sweden, SE'), offer(2, 0.371, 0.0007, 'The Netherlands, NL')]})
        for item in cheap_sweden:
            item['estimated_job_cost'] = cloud_market.estimated_job_cost(item['price'], item['download_cost_per_gb'])
        self.assertEqual(cloud_market.order_offers_for_rental(cheap_sweden, None, 'SE')[0]['id'], '3', 'a similar cost keeps the country wish')

    def test_offers_without_an_estimate_keep_the_hour_price_order(self):
        items = [{'id': 'a', 'price': 0.5, 'region': 'europe', 'country': 'SE'}, {'id': 'b', 'price': 0.3, 'region': 'europe', 'country': 'NO'}]
        self.assertEqual([item['id'] for item in cloud_market.order_offers_for_rental(items, None)], ['b', 'a'])

    def test_the_chain_offers_say_what_a_job_will_cost(self):
        payload = {'offers': [dict(offer(5, 0.2, 0.0013, 'Slovakia, SK'), inet_down=8000, reliability=0.99, disk_space=200)]}
        items = cloud_market.fetch_vast_chain_gpu_offers('key', 'RTX 3090', reader=lambda request: payload)
        self.assertIn('USD per jobb', items[0]['detail'])
        self.assertAlmostEqual(items[0]['estimated_job_cost'], 0.17, places=2)


if __name__ == '__main__':
    unittest.main()
