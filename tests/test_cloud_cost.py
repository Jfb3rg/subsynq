import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from cloud_cost import DEFAULT_PROFILE, load_profile, parse_profile, render_cloud_cost
from hardware import DEFAULT as DEFAULT_HARDWARE, allowed_preferences, render_hardware


class CloudCostTests(unittest.TestCase):
    def test_manual_profile_and_estimate(self):
        profile = parse_profile({
            'provider': ['vast'], 'gpu': ['RTX 5060 Ti'], 'mode': ['test'],
            'compute_hourly': ['0.8'], 'storage_hourly': ['0.2'],
            'transfer_per_gb': ['0.1'], 'max_minutes': ['90'],
            'currency': ['USD'],
        })
        self.assertEqual(profile['compute_hourly'], 0.8)
        self.assertEqual(profile['max_minutes'], 90)
        self.assertIn('1.50 USD', render_cloud_cost(profile, 'token'))

    def test_rejects_nonfinite_price_and_bad_mode(self):
        form = {key: [value if value is not None else ''] for key, value in
                DEFAULT_PROFILE.items()}
        form['compute_hourly'] = ['NaN']
        with self.assertRaises(ValueError):
            parse_profile(form)
        form['compute_hourly'] = ['1']
        form['mode'] = ['free-rental']
        with self.assertRaises(ValueError):
            parse_profile(form)

    def test_missing_or_invalid_saved_profile_uses_defaults(self):
        self.assertEqual(load_profile(None), DEFAULT_PROFILE)
        self.assertEqual(load_profile('{broken'), DEFAULT_PROFILE)

    def test_gpu_name_is_escaped_and_cloud_is_not_selectable(self):
        page = render_cloud_cost({**DEFAULT_PROFILE, 'gpu': '<script>alert(1)</script>'}, 'token')
        self.assertNotIn('<script>', page)
        hardware_page = render_hardware(DEFAULT_HARDWARE, 'cpu', 'token')
        self.assertIn('value="cloud-vast" disabled', hardware_page)
        self.assertIn('value="cloud-runpod" disabled', hardware_page)
        self.assertNotIn('cloud-vast', allowed_preferences(DEFAULT_HARDWARE))

    def test_new_cost_plan_is_usd_only(self):
        form = {key: [value if value is not None else ''] for key, value in
                DEFAULT_PROFILE.items()}
        form['currency'] = ['SEK']
        with self.assertRaises(ValueError):
            parse_profile(form)
        page = render_cloud_cost(DEFAULT_PROFILE, 'token')
        self.assertIn('name="currency" value="USD"', page)
        self.assertNotIn('value="SEK"', page)
        self.assertNotIn('value="EUR"', page)

    def test_legacy_sek_amount_is_never_relabelled_as_usd(self):
        legacy = {**DEFAULT_PROFILE, 'provider': 'runpod', 'gpu': 'RTX 3090',
                  'compute_hourly': 0.22, 'currency': 'SEK'}
        self.assertEqual(load_profile(json.dumps(legacy))['currency'], 'SEK')
        page = render_cloud_cost(legacy, 'token')
        self.assertIn('äldre planen är i SEK', page)
        self.assertNotIn('0.22 USD', page)
        self.assertIn('name="compute_hourly" type="number"', page)
        self.assertIn('name="currency" value="USD"', page)


if __name__ == '__main__':
    unittest.main()
