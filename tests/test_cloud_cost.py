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


class MaxHourlyPriceTests(unittest.TestCase):
    def form(self, **changes):
        form = {key: [value if value is not None else ''] for key, value in DEFAULT_PROFILE.items()}
        form.update({key: [value] for key, value in changes.items()})
        return form

    def test_the_default_limit_is_one_dollar_an_hour(self):
        self.assertEqual(DEFAULT_PROFILE['max_hourly'], 1.0)
        self.assertEqual(load_profile(None)['max_hourly'], 1.0)

    def test_an_older_saved_plan_without_the_field_gets_the_default(self):
        older = {key: value for key, value in DEFAULT_PROFILE.items() if key != 'max_hourly'}
        self.assertEqual(load_profile(json.dumps(older))['max_hourly'], 1.0)

    def test_a_chosen_limit_is_saved_and_an_empty_one_means_the_fixed_ceiling(self):
        self.assertEqual(parse_profile(self.form(max_hourly='0.75'))['max_hourly'], 0.75)
        self.assertIsNone(parse_profile(self.form(max_hourly=''))['max_hourly'])
        saved = json.dumps(parse_profile(self.form(max_hourly='')))
        self.assertIsNone(load_profile(saved)['max_hourly'], 'a cleared limit stays cleared')

    def test_zero_negative_and_nonfinite_limits_are_refused(self):
        for bad in ('0', '-1', 'NaN', 'inf', 'abc'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_profile(self.form(max_hourly=bad))

    def test_the_settings_page_has_the_field_with_the_saved_value(self):
        page = render_cloud_cost({**DEFAULT_PROFILE, 'max_hourly': 0.75}, 'token')
        self.assertIn('name="max_hourly"', page)
        self.assertIn('value="0.75"', page)
        self.assertIn('Högsta pris vid hyrning', page)
        self.assertIn('value=""', render_cloud_cost({**DEFAULT_PROFILE, 'max_hourly': None}, 'token'))


class PreferredPlaceProfileTests(unittest.TestCase):
    """The offer picked in the list leaves its country (and machine) in the plan."""

    def form(self, **changes):
        form = {key: [value if value is not None else ''] for key, value in DEFAULT_PROFILE.items()}
        form.update({key: [value] for key, value in changes.items()})
        return form

    def test_a_plan_without_a_place_is_the_default(self):
        self.assertEqual((DEFAULT_PROFILE['country'], DEFAULT_PROFILE['machine_id']), ('', None))
        profile = parse_profile(self.form())
        self.assertEqual((profile['country'], profile['machine_id']), ('', None))

    def test_country_and_machine_are_saved_and_read_back(self):
        profile = parse_profile(self.form(country='se', machine_id='12345'))
        self.assertEqual((profile['country'], profile['machine_id']), ('SE', 12345))
        again = load_profile(json.dumps(profile))
        self.assertEqual((again['country'], again['machine_id']), ('SE', 12345))

    def test_a_machine_is_never_kept_without_its_country(self):
        profile = parse_profile(self.form(country='', machine_id='12345'))
        self.assertEqual((profile['country'], profile['machine_id']), ('', None))

    def test_an_older_plan_without_the_fields_still_loads(self):
        older = {key: value for key, value in DEFAULT_PROFILE.items() if key not in ('country', 'machine_id')}
        profile = load_profile(json.dumps({**older, 'gpu': 'RTX 3090'}))
        self.assertEqual((profile['gpu'], profile['country'], profile['machine_id']), ('RTX 3090', '', None))

    def test_nonsense_places_are_refused(self):
        for bad in ('SWE', 'S', '1', '<b>', 'S E'):
            with self.subTest(country=bad), self.assertRaises(ValueError):
                parse_profile(self.form(country=bad))
        for bad in ('abc', '-5', '0', '1e3', str(2 ** 60)):
            with self.subTest(machine=bad), self.assertRaises(ValueError):
                parse_profile(self.form(country='SE', machine_id=bad))

    def test_the_plan_form_shows_the_chosen_country_as_a_box_that_can_be_cleared(self):
        page = render_cloud_cost({**DEFAULT_PROFILE, 'country': 'SE', 'machine_id': 777}, 'token')
        self.assertIn('<input type="checkbox" name="country" value="SE" checked>', page)
        self.assertIn('\U0001F1F8\U0001F1EA SE', page)
        self.assertIn('<input type="hidden" name="machine_id" value="777">', page)
        self.assertIn('Avmarkera och spara', page)

    def test_without_a_place_the_plan_form_says_so_and_has_no_box(self):
        page = render_cloud_cost(DEFAULT_PROFILE, 'token')
        self.assertNotIn('name="country"', page)
        self.assertNotIn('name="machine_id"', page)
        self.assertIn('Ingen plats vald', page)

    def test_saving_the_form_with_the_box_cleared_forgets_the_place(self):
        saved = parse_profile(self.form(country='SE', machine_id='777'))
        form = {key: [value if value is not None else ''] for key, value in saved.items() if key not in ('country', 'machine_id')}
        form['machine_id'] = ['777']        # the hidden field is still posted, the unchecked box is not
        cleared = parse_profile(form)
        self.assertEqual((cleared['country'], cleared['machine_id']), ('', None))


class CardMemoryProfileTests(unittest.TestCase):
    def form(self, **changes):
        form = {key: [value if value is not None else ''] for key, value in DEFAULT_PROFILE.items()}
        form.update({key: [value] for key, value in changes.items()})
        return form

    def test_the_default_plan_has_no_memory_and_an_older_plan_still_loads(self):
        self.assertIsNone(DEFAULT_PROFILE['vram_gb'])
        older = {key: value for key, value in DEFAULT_PROFILE.items() if key != 'vram_gb'}
        self.assertIsNone(load_profile(json.dumps(older))['vram_gb'])

    def test_the_memory_is_saved_rounded_and_read_back(self):
        profile = parse_profile(self.form(vram_gb='39.99'))
        self.assertEqual(profile['vram_gb'], 40.0)
        self.assertEqual(load_profile(json.dumps(profile))['vram_gb'], 40.0)

    def test_nonsense_memory_is_refused(self):
        for bad in ('abc', '0', '-4', 'nan', 'inf', '99999'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_profile(self.form(vram_gb=bad))

    def test_the_plan_names_the_reviewer_the_card_gets_and_what_the_measurement_showed(self):
        big = render_cloud_cost({**DEFAULT_PROFILE, 'vram_gb': 40.0}, 'token')
        self.assertIn('granskaren gemma4:31b', big)
        self.assertIn('färre påhittade fel än gemma4:26b', big)
        self.assertIn('ingen granskare är felfri', big)
        self.assertIn('<input type="hidden" name="vram_gb" value="40">', big)
        medium = render_cloud_cost({**DEFAULT_PROFILE, 'vram_gb': 24.0}, 'token')
        self.assertIn('granskaren gemma4:26b', medium)
        self.assertIn('fler påhittade fel än gemma4:31b', medium)
        self.assertIn('24 GB-kort ska fungera', medium)
        small = render_cloud_cost({**DEFAULT_PROFILE, 'vram_gb': 16.0}, 'token')
        self.assertIn('12B-modellerna', small)
        self.assertNotIn('mätprovet', small)

    def test_a_plan_without_a_known_memory_says_nothing_about_models(self):
        page = render_cloud_cost(DEFAULT_PROFILE, 'token')
        self.assertNotIn('granskaren', page)
        self.assertNotIn('name="vram_gb"', page)


if __name__ == '__main__':
    unittest.main()
