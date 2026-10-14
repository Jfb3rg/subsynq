import itertools
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
import worker


def make_database(directory, rows=()):
    database = Path(directory) / 'jobs.db'
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE subtitle_jobs (
            id INTEGER PRIMARY KEY, media_path TEXT, media_name TEXT,
            status TEXT, progress INTEGER, message TEXT, updated_at TEXT,
            output_path TEXT, hardware TEXT, model TEXT, cloud_instance_id INTEGER)''')
        for job_id, status, instance in rows:
            db.execute("INSERT INTO subtitle_jobs (id, media_path, media_name, status, progress, message, "
                       "updated_at, cloud_instance_id) VALUES (?,?,?,?,0,'','',?)",
                       (job_id, f'/tv/{job_id}.mkv', f'{job_id}.mkv', status, instance))
        db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
    return database


def kept_server(**overrides):
    server = {'provider': 'vast', 'mode': 'cuda', 'gpu': 'RTX 4090', 'instance_id': 555,
              'base_url': 'https://cloud:41000/token', 'tls_context': object(),
              'cloud_models': ('gemma3:27b', 'gemma4:26b'), 'since': 0.0}
    server.update(overrides)
    return server


class KeepServerBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = make_database(self.directory.name, [(1, 'transcribing', 555), (2, 'queued', None)])
        worker._KEPT_CLOUD = None
        self.addCleanup(setattr, worker, '_KEPT_CLOUD', None)
        self.patches = [patch.object(worker, 'DATABASE', self.database)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def statuses(self, sql, *args):
        with sqlite3.connect(self.database) as db:
            return db.execute(sql, args).fetchall()

    def set_status(self, job_id, status):
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE subtitle_jobs SET status=? WHERE id=?', (status, job_id))


class FinishCloudChainTests(KeepServerBase):
    def finish(self, *, reusable=True, preference='cloud-vast', keep=True, unloaded=True):
        with patch.object(worker, 'hardware_preference', return_value=preference), \
                patch.object(worker, 'CLOUD_KEEP_SERVER', keep), \
                patch.object(worker, 'destroy_cloud_gpu') as destroy, \
                patch.object(worker, 'unload_cloud_models', return_value=unloaded) as unload, \
                patch.object(worker, 'cloud_wanted_gpu', return_value='RTX 4090'):
            worker.finish_cloud_chain({'id': 1}, 'vast', 'cuda', 555, 'https://cloud:41000/token', 'ctx', reusable)
        self.unload = unload
        return destroy

    def test_a_ready_server_is_kept_when_another_cloud_job_waits(self):
        destroy = self.finish()
        destroy.assert_not_called()
        self.assertEqual(worker._KEPT_CLOUD['instance_id'], 555)
        self.assertEqual(worker._KEPT_CLOUD['gpu'], 'RTX 4090')
        self.assertEqual(self.statuses("SELECT value FROM metadata WHERE key='cloud_kept_server'"), [('555',)])
        self.assertEqual(self.statuses('SELECT cloud_instance_id FROM subtitle_jobs WHERE id=1'), [(None,)])

    def test_it_is_destroyed_when_nothing_waits(self):
        self.set_status(2, 'completed')
        destroy = self.finish()
        destroy.assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)
        self.assertEqual(self.statuses('SELECT * FROM metadata'), [])
        self.assertEqual(self.statuses('SELECT cloud_instance_id FROM subtitle_jobs WHERE id=1'), [(None,)])

    def test_it_is_destroyed_after_a_server_error_even_if_a_job_waits(self):
        self.finish(reusable=False).assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_it_is_destroyed_when_the_hardware_choice_is_no_longer_cloud(self):
        self.finish(preference='cpu').assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_the_reuse_can_be_switched_off(self):
        self.finish(keep=False).assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_restart_queued_jobs_count_as_waiting_and_deleting_ones_do_not(self):
        self.set_status(2, 'delete_queued')
        self.finish().assert_called_once_with('vast', 555)
        self.set_status(2, 'restart_queued')
        worker._KEPT_CLOUD = None
        with patch.object(worker, 'destroy_cloud_gpu') as destroy, \
                patch.object(worker, 'hardware_preference', return_value='cloud-vast'), \
                patch.object(worker, 'unload_cloud_models', return_value=True), \
                patch.object(worker, 'cloud_wanted_gpu', return_value='RTX 4090'):
            worker.finish_cloud_chain({'id': 1}, 'vast', 'cuda', 556, 'u', 'ctx', True)
        destroy.assert_not_called()


    def test_a_server_whose_gpu_memory_could_not_be_freed_is_destroyed_not_kept(self):
        destroy = self.finish(unloaded=False)
        destroy.assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_the_models_are_unloaded_before_the_server_is_kept(self):
        worker.MODEL_JOB.cloud_models = ('gemma3:27b', 'gemma4:26b')
        self.addCleanup(setattr, worker.MODEL_JOB, 'cloud_models', None)
        self.finish()
        self.unload.assert_called_once_with('https://cloud:41000/token', 'ctx', ('gemma3:27b', 'gemma4:26b'))


class UnloadCloudModelsTests(unittest.TestCase):
    class Reply:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return json.dumps(self.payload).encode()

    def run_unload(self, models, reply=None, error=None):
        sent = []

        def urlopen(request, timeout=0):
            sent.append((request.full_url, request.get_method(), json.loads(request.data)))
            if error:
                raise error
            return self.Reply(reply if reply is not None else {'done_reason': 'unload'})
        with patch.object(worker, 'install_tls_opener'), patch.object(worker, 'reset_opener') as reset, \
                patch.object(worker.urllib.request, 'urlopen', side_effect=urlopen):
            result = worker.unload_cloud_models('https://cloud:41000/token', 'ctx', models)
        reset.assert_called_once_with()
        return result, sent

    def test_each_model_gets_a_keep_alive_zero_request_through_the_gateway(self):
        result, sent = self.run_unload(('gemma3:27b', 'gemma4:26b', 'gemma3:27b'))
        self.assertTrue(result)
        self.assertEqual(sent, [
            ('https://cloud:41000/token/ollama/api/generate', 'POST', {'model': 'gemma3:27b', 'keep_alive': 0}),
            ('https://cloud:41000/token/ollama/api/generate', 'POST', {'model': 'gemma4:26b', 'keep_alive': 0})])

    def test_an_unacknowledged_unload_is_a_failure(self):
        self.assertFalse(self.run_unload(('gemma3:27b',), reply={'done_reason': 'stop'})[0])

    def test_a_network_error_is_a_failure_not_a_crash(self):
        self.assertFalse(self.run_unload(('gemma3:27b',), error=OSError('down'))[0])

    def test_without_known_models_nothing_is_sent_and_the_server_is_not_trusted(self):
        for models in (None, (), ('',)):
            with patch.object(worker.urllib.request, 'urlopen') as urlopen:
                self.assertFalse(worker.unload_cloud_models('https://x/t', 'ctx', models))
                urlopen.assert_not_called()


class AcquireCloudChainTests(KeepServerBase):
    def acquire(self, kept, *, ready=True, gpu='RTX 4090'):
        worker._KEPT_CLOUD = kept
        rented = ('model', 999, 'https://new/token', 'new-ctx')
        with patch.object(worker, 'cloud_server_is_ready', return_value=ready), \
                patch.object(worker, 'cloud_wanted_gpu', return_value=gpu), \
                patch.object(worker, 'rent_cloud_chain', return_value=rented) as rent, \
                patch.object(worker, 'destroy_cloud_gpu') as destroy:
            result = worker.acquire_cloud_chain({'id': 2}, 'vast', 'cuda')
        return result, rent, destroy

    def test_a_matching_ready_server_is_reused_without_renting(self):
        self.set_status(2, 'transcribing')
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO metadata VALUES ('cloud_kept_server', '555')")
        worker.MODEL_JOB.cloud_models = None
        result, rent, destroy = self.acquire(kept_server())
        rent.assert_not_called()
        destroy.assert_not_called()
        model, instance_id, base_url, context = result
        self.assertEqual((instance_id, base_url), (555, 'https://cloud:41000/token'))
        self.assertEqual(model.base_url, 'https://cloud:41000/token')
        self.assertEqual(worker.MODEL_JOB.cloud_models, ('gemma3:27b', 'gemma4:26b'))
        self.assertIsNone(worker._KEPT_CLOUD)
        self.assertEqual(self.statuses('SELECT * FROM metadata'), [], 'the marker is removed while a job uses it')
        self.assertEqual(self.statuses('SELECT cloud_instance_id FROM subtitle_jobs WHERE id=2'), [(555,)])
        self.assertIn('Återanvänder', self.statuses('SELECT message FROM subtitle_jobs WHERE id=2')[0][0])

    def test_a_server_of_another_card_is_destroyed_and_a_new_one_rented(self):
        result, rent, destroy = self.acquire(kept_server(), gpu='RTX 3090')
        destroy.assert_called_once_with('vast', 555)
        rent.assert_called_once()
        self.assertEqual(result[1], 999)

    def test_a_server_that_no_longer_answers_is_destroyed_and_a_new_one_rented(self):
        result, rent, destroy = self.acquire(kept_server(), ready=False)
        destroy.assert_called_once_with('vast', 555)
        self.assertEqual(result[1], 999)

    def test_without_a_kept_server_a_new_one_is_rented(self):
        result, rent, destroy = self.acquire(None)
        rent.assert_called_once()
        destroy.assert_not_called()


class ExpireKeptServerTests(KeepServerBase):
    def expire(self, *, preference='cloud-vast', now=10.0, since=0.0):
        worker._KEPT_CLOUD = kept_server(since=since)
        with patch.object(worker, 'hardware_preference', return_value=preference), \
                patch.object(worker.time, 'monotonic', return_value=now), \
                patch.object(worker, 'destroy_cloud_gpu') as destroy:
            worker.expire_kept_cloud_server()
        return destroy

    def test_a_fresh_server_with_a_waiting_job_stays(self):
        self.expire().assert_not_called()
        self.assertIsNotNone(worker._KEPT_CLOUD)

    def test_it_goes_at_once_when_no_cloud_job_waits_any_more(self):
        self.set_status(2, 'cancelled')
        self.expire().assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_it_goes_when_nobody_claims_it_in_time(self):
        self.expire(now=worker.CLOUD_KEEP_IDLE_SECONDS + 1).assert_called_once_with('vast', 555)

    def test_nothing_happens_without_a_kept_server(self):
        worker._KEPT_CLOUD = None
        with patch.object(worker, 'destroy_cloud_gpu') as destroy:
            worker.expire_kept_cloud_server()
        destroy.assert_not_called()


class KeptServerSafetyTests(KeepServerBase):
    def test_a_restarted_worker_destroys_the_server_that_was_waiting(self):
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO metadata VALUES ('cloud_kept_server', 'runpod:abc')")
        with patch.object(worker, 'destroy_cloud_gpu') as destroy:
            worker.cleanup_orphaned_cloud_instances()
        destroy.assert_any_call('runpod', 'abc')
        self.assertEqual(self.statuses('SELECT * FROM metadata'), [])

    def test_docker_stop_destroys_the_kept_server_and_exits(self):
        worker._KEPT_CLOUD = kept_server()
        with patch.object(worker, 'destroy_cloud_gpu') as destroy:
            with self.assertRaises(SystemExit):
                worker.stop_gracefully(15, None)
        destroy.assert_called_once_with('vast', 555)
        self.assertIsNone(worker._KEPT_CLOUD)

    def test_readiness_needs_a_ready_answer_over_the_pinned_connection(self):
        class Reply:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                return json.dumps(self.payload).encode()
        for payload, expected in (({'status': 'ready'}, True), ({'status': 'loading'}, False), ([], False)):
            with patch.object(worker, 'install_tls_opener'), patch.object(worker, 'reset_opener') as reset, \
                    patch.object(worker.urllib.request, 'urlopen', return_value=Reply(payload)):
                self.assertIs(worker.cloud_server_is_ready('https://cloud/x', 'ctx'), expected)
                reset.assert_called_once_with()
        with patch.object(worker, 'install_tls_opener'), patch.object(worker, 'reset_opener'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=OSError('down')):
            self.assertFalse(worker.cloud_server_is_ready('https://cloud/x', 'ctx'))


class SlowHostTests(KeepServerBase):
    """A host that cannot finish installing is replaced by the next one."""

    def run_rent(self, offers, *, slow=('501',), profile=None):
        self.created = []
        self.destroyed = []
        counter = itertools.count(501)
        current = {}

        def create(api_key, offer_id, onstart, **kwargs):
            instance = next(counter)
            self.created.append((instance, offer_id))
            current['id'] = str(instance)
            return instance

        class Reply:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                return json.dumps(self.payload).encode()

        def urlopen(request, timeout=0):
            return Reply({'status': 'loading', 'bootstrap_step': 'ollama: hämtar gemma4:26b'}
                         if current['id'] in slow else {'status': 'ready'})

        clock = itertools.count(0, 60)
        with patch.object(worker, 'load_cloud_keys', return_value={'vast': 'secret'}), \
                patch.object(worker, 'fetch_vast_chain_gpu_offers', return_value=offers), \
                patch.object(worker, 'load_cloud_profile', return_value=profile or {'gpu': 'RTX 4090'}), \
                patch.object(worker.cloud_gpu, 'create_instance', side_effect=create), \
                patch.object(worker.cloud_gpu, 'show_instance', return_value={}), \
                patch.object(worker.cloud_gpu, 'public_service_url', return_value='http://cloud:41000'), \
                patch.object(worker.cloud_gpu, 'destroy_instance',
                             side_effect=lambda key, instance: self.destroyed.append(instance)), \
                patch.object(worker, 'fetch_cloud_logs', return_value=None), \
                patch.object(worker, 'fetch_vast_container_log', return_value=None), \
                patch.object(worker.time, 'sleep'), \
                patch.object(worker.time, 'monotonic', side_effect=lambda: next(clock)), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=urlopen):
            try:
                result, error = worker.rent_cloud_chain({'id': 1}, 'vast', 'cuda'), None
            except Exception as exc:
                result, error = None, exc
        worker.reset_opener()
        return result, error

    def offers(self, *machines):
        return [{'id': str(100 + number), 'gpu': 'RTX 4090', 'price': .4 + number / 100, 'detail': 'x',
                 'vram_gb': 24.0, 'machine_id': machine} for number, machine in enumerate(machines)]

    def test_a_slow_host_is_destroyed_and_the_next_one_is_used(self):
        result, error = self.run_rent(self.offers('m1', 'm2', 'm3'))
        self.assertIsNone(error)
        self.assertEqual(result[1], 502)
        self.assertEqual(self.destroyed, [501])
        self.assertEqual([offer for _, offer in self.created], [100, 101])
        message = self.statuses('SELECT message FROM subtitle_jobs WHERE id=1')[0][0]
        self.assertIn('Ansluten', message)

    def test_the_slow_hosts_other_offers_are_skipped(self):
        result, error = self.run_rent(self.offers('m1', 'm1', 'm2'))
        self.assertIsNone(error)
        self.assertEqual([offer for _, offer in self.created], [100, 102])

    def test_at_most_two_other_hosts_are_tried_then_the_job_stops(self):
        result, error = self.run_rent(self.offers('m1', 'm2', 'm3', 'm4', 'm5'), slow=('501', '502', '503', '504'))
        self.assertIsInstance(error, RuntimeError)
        self.assertRegex(str(error), 'blev inte redo')
        self.assertEqual(len(self.created), 3, 'the first host plus two replacements')
        self.assertEqual(sorted(self.destroyed), [501, 502, 503], 'every rented server is destroyed')
        self.assertEqual(self.statuses('SELECT cloud_instance_id FROM subtitle_jobs WHERE id=1'), [(None,)])

    def test_a_single_slow_host_with_no_alternative_waits_until_the_normal_limit(self):
        result, error = self.run_rent(self.offers('m1'))
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.destroyed, [501])



class PriceLimitTests(unittest.TestCase):
    def limit(self, profile):
        with patch.object(worker, 'load_cloud_profile', return_value=profile):
            return worker.cloud_price_limit()

    def test_the_users_limit_is_used_but_never_above_the_fixed_ceiling(self):
        self.assertEqual(self.limit({'max_hourly': 0.5}), 0.5)
        self.assertEqual(self.limit({'max_hourly': 5}), worker.CLOUD_MAX_USD_PER_HOUR)

    def test_no_or_unusable_limit_means_the_fixed_ceiling(self):
        for profile in ({}, {'max_hourly': None}, {'max_hourly': 0}, {'max_hourly': 'abc'}, {'max_hourly': -1}):
            self.assertEqual(self.limit(profile), worker.CLOUD_MAX_USD_PER_HOUR, profile)

    def test_an_unreadable_plan_means_the_fixed_ceiling(self):
        with patch.object(worker, 'load_cloud_profile', side_effect=sqlite3.OperationalError('no table')):
            self.assertEqual(worker.cloud_price_limit(), worker.CLOUD_MAX_USD_PER_HOUR)


class RentalChoiceTests(KeepServerBase):
    """The rented server is the nearest affordable one, not the cheapest in the world and not the dearest."""
    run_rent = SlowHostTests.run_rent

    def offers(self):
        def make(offer_id, price, region):
            return {'id': offer_id, 'gpu': 'RTX 4090', 'price': price, 'detail': 'x', 'vram_gb': 24.0,
                    'region': region, 'machine_id': f'm{offer_id}'}
        return [make('201', .20, 'world'), make('202', .30, 'north-america'), make('203', .60, 'europe'),
                make('204', .40, 'europe'), make('205', 1.50, 'europe')]

    def test_europe_first_cheapest_there_and_within_the_limit(self):
        result, error = self.run_rent(self.offers(), slow=(), profile={'gpu': 'RTX 4090', 'max_hourly': 1.0})
        self.assertIsNone(error)
        self.assertEqual([offer for _, offer in self.created], [204], 'Europe at 0.40, not Asia at 0.20 or Europe at 1.50')

    def test_a_dearer_limit_still_picks_the_cheapest_in_europe(self):
        self.run_rent(self.offers(), slow=(), profile={'gpu': 'RTX 4090', 'max_hourly': 2.0})
        self.assertEqual([offer for _, offer in self.created], [204])

    def test_without_a_saved_limit_the_fixed_ceiling_applies(self):
        offers = [offer for offer in self.offers() if offer['id'] == '205'] + [
            dict(self.offers()[0], id='206', price=2.5, region='europe')]
        self.run_rent(offers, slow=())
        self.assertEqual([offer for _, offer in self.created], [205], 'the 2.50 offer is above the 2.00 ceiling')

    def test_when_everything_is_above_the_limit_nothing_is_rented(self):
        result, error = self.run_rent(self.offers(), slow=(), profile={'gpu': 'RTX 4090', 'max_hourly': 0.1})
        self.assertIsInstance(error, RuntimeError)
        self.assertRegex(str(error), 'kostar mer än din gräns på 0.10 USD/tim \\(billigaste 0.20 USD/tim\\)')
        self.assertEqual(self.created, [])

    def test_a_replacement_for_a_slow_host_also_stays_within_the_limit(self):
        result, error = self.run_rent(self.offers(), slow=('501',), profile={'gpu': 'RTX 4090', 'max_hourly': 1.0})
        self.assertIsNone(error)
        self.assertEqual([offer for _, offer in self.created], [204, 203])


if __name__ == '__main__':
    unittest.main()
