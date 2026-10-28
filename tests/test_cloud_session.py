import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, '/ai')
import cloud_session
import server
import worker
from jobs import initialize_jobs


def make_database(directory):
    database = Path(directory) / 'jobs.db'
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE subtitle_jobs (
            id INTEGER PRIMARY KEY, media_path TEXT, media_name TEXT,
            status TEXT, progress INTEGER, message TEXT, updated_at TEXT,
            output_path TEXT, hardware TEXT, model TEXT, cloud_instance_id INTEGER)''')
        db.execute("INSERT INTO subtitle_jobs (id, media_path, media_name, status, progress, message, updated_at, "
                   "cloud_instance_id) VALUES (1, '/tv/1.mkv', '1.mkv', 'transcribing', 0, '', '', 555)")
        db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
    return database


def kept_server(**overrides):
    server_state = {'provider': 'vast', 'mode': 'cuda', 'gpu': 'RTX 4090', 'instance_id': 555,
                    'base_url': 'https://cloud:41000/token', 'tls_context': object(),
                    'cloud_models': ('gemma3:27b',), 'since': 0.0, 'session': True}
    server_state.update(overrides)
    return server_state


class ModuleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = make_database(folder.name)

    def test_start_request_marks_the_session_starting_and_take_removes_it(self):
        cloud_session.request(self.database, 'start')
        self.assertEqual(cloud_session.load(self.database)['state'], 'starting')
        self.assertEqual(cloud_session.take(self.database), 'start')
        self.assertIsNone(cloud_session.take(self.database))

    def test_stop_replaces_a_start_that_was_not_taken(self):
        cloud_session.request(self.database, 'start')
        cloud_session.request(self.database, 'stop')
        self.assertEqual(cloud_session.pending(self.database), 'stop')

    def test_stop_while_running_says_the_server_closes_when_the_worker_is_free(self):
        cloud_session.save(self.database, {'state': 'ready', 'ready_at': time.time()})
        cloud_session.request(self.database, 'stop')
        self.assertIn('Stopp begärt', cloud_session.render(cloud_session.load(self.database), 'cloud-vast', 't'))

    def test_unknown_request_is_refused(self):
        with self.assertRaises(ValueError):
            cloud_session.request(self.database, 'reboot')

    def test_idle_time_counts_from_the_last_job(self):
        state = {'state': 'ready', 'ready_at': 1000.0, 'last_activity': 5000.0}
        self.assertEqual(cloud_session.idle_seconds(state, now=5600.0), 600.0)

    def test_card_shows_start_only_with_a_cloud_choice_and_stop_when_running(self):
        self.assertNotIn('/cloud/session/start', cloud_session.render({'state': 'off'}, 'cpu', 't'))
        self.assertIn('/cloud/session/start', cloud_session.render({'state': 'off'}, 'cloud-vast', 't'))
        now = time.time()
        card = cloud_session.render({'state': 'ready', 'since': now - 3600, 'ready_at': now - 3000, 'last_activity': now,
                                     'gpu': 'RTX 3090', 'price': 0.3}, 'cloud-vast', 't', now=now)
        self.assertIn('/cloud/session/stop', card)
        self.assertIn('0.30 USD', card)
        self.assertIn('Stängs automatiskt', card)

    def test_card_escapes_the_message(self):
        card = cloud_session.render({'state': 'failed', 'message': '<script>x</script>'}, 'cloud-vast', 't')
        self.assertNotIn('<script>', card)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RouteTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        config = Path(folder.name)
        initialize_jobs(config / 'subsynq.db')
        with sqlite3.connect(config / 'subsynq.db') as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.database = config / 'subsynq.db'
        for target, value in (('CONFIG', config), ('DATABASE', self.database)):
            patcher = patch.object(server, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, route, token):
        body = urllib.parse.urlencode({'token': token}).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.httpd.server_port}{route}', body)
        try:
            urllib.request.build_opener(NoRedirect).open(request, timeout=10)
        except urllib.error.HTTPError as error:
            return error.code
        raise AssertionError('expected a redirect or error')

    def test_start_and_stop_only_write_a_request(self):
        self.assertEqual(self.post('/cloud/session/start', server.SCAN_TOKEN), 303)
        self.assertEqual(cloud_session.pending(self.database), 'start')
        self.assertEqual(self.post('/cloud/session/stop', server.SCAN_TOKEN), 303)
        self.assertEqual(cloud_session.pending(self.database), 'stop')

    def test_the_answer_returns_to_the_page_the_button_was_on(self):
        for referer, expected in (('/', '/'), ('/queue', '/queue'), ('/settings/cloud', '/settings/cloud'), ('/settings', '/queue'), ('', '/queue')):
            with self.subTest(referer=referer):
                body = urllib.parse.urlencode({'token': server.SCAN_TOKEN}).encode()
                request = urllib.request.Request(f'http://127.0.0.1:{self.httpd.server_port}/cloud/session/stop', body,
                                                 headers={'Referer': f'http://127.0.0.1:{self.httpd.server_port}{referer}'} if referer else {})
                with self.assertRaises(urllib.error.HTTPError) as answer:
                    urllib.request.build_opener(NoRedirect).open(request, timeout=10)
                self.assertEqual((answer.exception.code, answer.exception.headers['Location']), (303, expected))

    def test_the_card_is_on_the_cloud_settings_page(self):
        with sqlite3.connect(self.database) as db:
            db.executemany('INSERT OR REPLACE INTO metadata VALUES (?, ?)', [('instance_id', 'test-instance'),
                                                                             ('created_at', '2026-01-01T00:00:00+00:00')])
        listing = {'offers': [], 'fetched_at': 'now', 'error': None}
        with patch.object(server, 'catalog', return_value=listing):
            page = urllib.request.urlopen(f'http://127.0.0.1:{self.httpd.server_port}/settings/cloud', timeout=10).read().decode()
        self.assertIn('Molnsession', page)
        self.assertLess(page.index('Molnsession'), page.index('/cloud-cost'))      # above the market and the cost plan

    def test_a_wrong_token_is_refused(self):
        self.assertNotEqual(self.post('/cloud/session/start', 'wrong'), 303)
        self.assertIsNone(cloud_session.pending(self.database))


class WorkerTests(unittest.TestCase):
    """Nothing is ever rented here: rent_cloud_chain, destroy_cloud_gpu and the readiness check are fakes."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = make_database(folder.name)
        worker._KEPT_CLOUD = None
        worker._SESSION_IN_USE = False
        self.addCleanup(setattr, worker, '_KEPT_CLOUD', None)
        self.addCleanup(setattr, worker, '_SESSION_IN_USE', False)
        for target, value in (('DATABASE', self.database), ('cloud_wanted_gpu', lambda provider, mode: 'RTX 4090'),
                              ('cloud_server_is_ready', lambda url, context: True)):
            patcher = patch.object(worker, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.destroy = patch.object(worker, 'destroy_cloud_gpu').start()
        self.addCleanup(patch.stopall)

    def kept_row(self):
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='cloud_kept_server'").fetchone()
        return row[0] if row else None

    def fake_rent(self, job, provider, mode):
        worker.update_job(job['id'], 'transcribing', 1, 'Servern är skapad', cloud_instance_id='777')
        self.assertEqual(self.kept_row(), '777')      # stored before the server is ready
        job['cloud_offer'] = {'gpu': 'RTX 3090', 'location': 'Sweden', 'price': 0.3}
        return object(), 777, 'https://cloud:1/t', 'ctx'

    def start(self, preference='cloud-vast', rent=None):
        cloud_session.request(self.database, 'start')
        with patch.object(worker, 'hardware_preference', return_value=preference), \
                patch.object(worker, 'rent_cloud_chain', side_effect=rent or self.fake_rent) as rented:
            worker.run_cloud_session()
        return rented

    def test_start_rents_once_and_keeps_the_server_for_the_session(self):
        rented = self.start()
        rented.assert_called_once()
        self.assertEqual(rented.call_args.args[1:], ('vast', 'cuda'))
        self.assertTrue(worker._KEPT_CLOUD['session'])
        self.assertEqual(worker._KEPT_CLOUD['instance_id'], 777)
        state = cloud_session.load(self.database)
        self.assertEqual((state['state'], state['gpu'], state['price']), ('ready', 'RTX 3090', 0.3))
        self.assertIsNotNone(self.kept_row())
        with sqlite3.connect(self.database) as db:      # the session never touches a job row
            self.assertEqual(db.execute('SELECT message, cloud_instance_id FROM subtitle_jobs').fetchall(), [('', 555)])

    def test_cloud_cpu_choice_rents_in_cpu_mode(self):
        rented = self.start('cloud-cpu-runpod')
        self.assertEqual(rented.call_args.args[1:], ('runpod', 'cpu'))

    def test_without_a_cloud_choice_nothing_is_rented(self):
        rented = self.start('cpu')
        rented.assert_not_called()
        self.assertEqual(cloud_session.load(self.database)['state'], 'failed')

    def test_a_failed_start_reports_and_keeps_nothing(self):
        self.start(rent=RuntimeError('Inga lediga servrar'))
        state = cloud_session.load(self.database)
        self.assertEqual(state['state'], 'failed')
        self.assertIn('Inga lediga servrar', state['message'])
        self.assertIsNone(worker._KEPT_CLOUD)
        self.assertIsNone(self.kept_row())

    def test_stop_during_start_cancels_the_rental(self):
        def rent(job, provider, mode):
            cloud_session.request(self.database, 'stop')
            worker.check_cancelled(job['id'])
        self.start(rent=rent)
        self.assertEqual(cloud_session.load(self.database)['state'], 'off')
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_stop_destroys_the_session_server(self):
        self.start()
        cloud_session.request(self.database, 'stop')
        worker.run_cloud_session()
        self.destroy.assert_called_once_with('vast', 777)
        self.assertIsNone(worker._KEPT_CLOUD)
        self.assertIsNone(self.kept_row())
        self.assertEqual(cloud_session.load(self.database)['state'], 'off')

    def test_session_server_stays_while_idle_under_an_hour_even_with_an_empty_queue(self):
        self.start()
        with patch.object(worker, 'hardware_preference', return_value='cloud-vast'):
            worker.expire_kept_cloud_server()
        self.destroy.assert_not_called()
        self.assertIsNotNone(worker._KEPT_CLOUD)

    def test_session_server_closes_after_an_hour_without_a_job(self):
        self.start()
        cloud_session.update(self.database, last_activity=time.time() - cloud_session.IDLE_SECONDS - 5)
        worker.expire_kept_cloud_server()
        self.destroy.assert_called_once_with('vast', 777)
        state = cloud_session.load(self.database)
        self.assertEqual(state['state'], 'off')
        self.assertIn('1 timme', state['message'])

    def test_a_job_reuses_the_session_server_and_keeps_it_afterwards(self):
        worker._KEPT_CLOUD = kept_server()
        cloud_session.save(self.database, {'state': 'ready', 'last_activity': 0})
        with patch.object(worker, 'rent_cloud_chain') as rented:
            _model, instance_id, url, context = worker.acquire_cloud_chain({'id': 1}, 'vast', 'cuda')
        rented.assert_not_called()
        self.assertEqual(instance_id, 555)
        with patch.object(worker, 'unload_cloud_models', return_value=True):
            worker.finish_cloud_chain({'id': 1}, 'vast', 'cuda', instance_id, url, context, True)
        self.destroy.assert_not_called()
        self.assertTrue(worker._KEPT_CLOUD['session'])
        state = cloud_session.load(self.database)
        self.assertEqual(state['state'], 'ready')
        self.assertGreater(state['last_activity'], time.time() - 60)

    def test_a_technical_fault_closes_the_session_server(self):
        worker._KEPT_CLOUD = kept_server()
        cloud_session.save(self.database, {'state': 'ready'})
        worker.acquire_cloud_chain({'id': 1}, 'vast', 'cuda')
        with patch.object(worker, 'unload_cloud_models', return_value=True):
            worker.finish_cloud_chain({'id': 1}, 'vast', 'cuda', 555, 'u', 'c', False)
        self.destroy.assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)
        self.assertEqual(cloud_session.load(self.database)['state'], 'off')

    def test_a_server_kept_without_a_session_is_still_closed_when_the_queue_is_empty(self):
        worker._KEPT_CLOUD = kept_server(session=False)
        with patch.object(worker, 'hardware_preference', return_value='cloud-vast'):
            worker.expire_kept_cloud_server()
        self.destroy.assert_called_once_with('vast', 555)


if __name__ == '__main__':
    unittest.main()
