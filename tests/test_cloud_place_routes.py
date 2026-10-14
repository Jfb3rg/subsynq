import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
from jobs import initialize_jobs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


SWEDEN = {'id': '901', 'gpu': 'RTX 3090', 'price': .27, 'detail': '24 GB VRAM', 'vram_mb': 24576, 'vram_gb': 24.0,
          'location': 'Sweden, SE', 'country': 'SE', 'region': 'europe', 'machine_id': 4242}
NORWAY = dict(SWEDEN, id='902', price=.2, location='Norway, NO', country='NO', machine_id=99)
RUNPOD = {'id': 'RTX 3090', 'gpu': 'RTX 3090', 'price': .4, 'detail': '24 GB VRAM', 'vram_mb': 24576}


class CloudPlaceRouteTests(unittest.TestCase):
    """The real Handler: choosing an offer stores its country and machine, the one form refreshes the list."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = Path(folder.name)
        initialize_jobs(self.config / "subsynq.db")
        with sqlite3.connect(self.config / "subsynq.db") as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [('instance_id', 'test-instance'),
                                                                              ('created_at', '2026-01-01T00:00:00+00:00')])
        self.offers = [SWEDEN, NORWAY]
        self.catalog_calls = []

        def catalog(config, provider, *, force=False, gpu_query='', vram_bands=()):
            self.catalog_calls.append((provider, force, gpu_query, tuple(vram_bands)))
            return {'offers': list(self.offers), 'fetched_at': 'now', 'error': None}

        for target, value in (("CONFIG", self.config), ("DATABASE", self.config / "subsynq.db")):
            patcher = mock.patch.object(server, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(server, 'catalog', side_effect=catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def url(self, route):
        return f"http://127.0.0.1:{self.httpd.server_port}{route}"

    def post(self, route, **fields):
        body = urllib.parse.urlencode({"token": server.SCAN_TOKEN, **fields}, doseq=True).encode()
        try:
            urllib.request.build_opener(NoRedirect).open(urllib.request.Request(self.url(route), body), timeout=10)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location") or error.read().decode()
        raise AssertionError("expected a redirect or error")

    def profile(self):
        with sqlite3.connect(self.config / "subsynq.db") as db:
            row = db.execute("SELECT value FROM metadata WHERE key='cloud_cost_profile'").fetchone()
        return json.loads(row[0])

    def test_choosing_an_offer_stores_its_country_and_machine(self):
        status, location = self.post('/cloud-offer', provider='vast', offer_id='901', gpu_query='', vram=['24'])
        self.assertEqual(status, 303)
        self.assertIn('saved=offer', location)
        profile = self.profile()
        self.assertEqual((profile['country'], profile['machine_id']), ('SE', 4242))
        self.assertEqual((profile['gpu'], profile['compute_hourly']), ('RTX 3090', .27))

    def test_choosing_a_norwegian_offer_replaces_the_swedish_place(self):
        self.post('/cloud-offer', provider='vast', offer_id='901')
        self.post('/cloud-offer', provider='vast', offer_id='902')
        profile = self.profile()
        self.assertEqual((profile['country'], profile['machine_id']), ('NO', 99))

    def test_an_offer_without_a_country_clears_the_place(self):
        self.post('/cloud-offer', provider='vast', offer_id='901')
        self.offers = [RUNPOD]
        status, _ = self.post('/cloud-offer', provider='runpod', offer_id='RTX 3090')
        self.assertEqual(status, 303)
        profile = self.profile()
        self.assertEqual((profile['provider'], profile['country'], profile['machine_id']), ('runpod', '', None))

    def test_a_vanished_offer_changes_nothing(self):
        self.post('/cloud-offer', provider='vast', offer_id='901')
        status, _ = self.post('/cloud-offer', provider='vast', offer_id='123456')
        self.assertEqual(status, 409)
        self.assertEqual(self.profile()['country'], 'SE')

    def test_the_plan_form_keeps_clears_and_never_invents_a_place(self):
        self.post('/cloud-offer', provider='vast', offer_id='901')
        plan = self.profile()
        fields = {key: ('' if value is None else value) for key, value in plan.items()
                  if key not in ('country', 'machine_id')}
        keep = self.post('/cloud-cost', **fields, country='SE', machine_id='4242')
        self.assertEqual(keep[0], 303)
        self.assertEqual((self.profile()['country'], self.profile()['machine_id']), ('SE', 4242))
        clear = self.post('/cloud-cost', **fields, machine_id='4242')       # the unchecked box is not posted
        self.assertEqual(clear[0], 303)
        self.assertEqual((self.profile()['country'], self.profile()['machine_id']), ('', None))
        self.assertEqual(self.post('/cloud-cost', **fields, country='SWE')[0], 400)

    def test_the_single_search_button_refreshes_with_the_typed_search_and_sizes(self):
        status, location = self.post('/cloud-refresh', provider='vast', gpu_query='RTX 3090', vram=['24', '32'])
        self.assertEqual(status, 303)
        self.assertEqual(self.catalog_calls, [('vast', True, 'RTX 3090', ('24', '32'))])
        self.assertIn('provider=vast', location)
        self.assertIn('gpu=RTX%203090', location)
        self.assertIn('&vram=24&vram=32', location)
        self.assertIn('saved=refreshed', location)

    def test_the_page_has_one_search_form_and_its_list_for_the_script_to_swap(self):
        with urllib.request.urlopen(self.url('/settings/cloud?provider=vast&vram=24'), timeout=10) as reply:
            page = reply.read().decode()
        self.assertEqual(page.count('id="cloud-search"'), 1)
        self.assertEqual(page.count('id="cloud-results"'), 1)
        self.assertIn('Sök och uppdatera', page)
        self.assertNotIn('Hämta aktuella erbjudanden', page)
        self.assertIn('Ingen plats vald', page)
        self.assertNotIn(True, [call[1] for call in self.catalog_calls], 'a plain page view never forces a fetch')

    def test_after_choosing_an_offer_the_plan_card_names_the_country(self):
        self.post('/cloud-offer', provider='vast', offer_id='901')
        with urllib.request.urlopen(self.url('/settings/cloud?provider=vast&saved=offer'), timeout=10) as reply:
            page = reply.read().decode()
        self.assertIn('name="country" value="SE" checked', page)
        self.assertIn('name="machine_id" value="4242"', page)
        self.assertIn('serverns land kopierades', page)


if __name__ == "__main__":
    unittest.main()
