import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker


class DestroyRetryTests(unittest.TestCase):
    """A session's server was reported closed after its idle hour although the destroy call had failed, and kept billing. A failed destroy is
    now kept on a pending list and tried again until the provider confirms it."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Path(self.folder.name) / 'test.db'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.patches = [patch.object(worker, 'DATABASE', self.database), patch.object(worker, 'load_cloud_keys', return_value={'vast': 'key', 'runpod': 'key'}),
                        patch.object(worker.time, 'sleep')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.folder.cleanup()

    def pending(self):
        return [item['id'] for item in worker._pending_destroys()]

    def test_a_destroy_that_fails_three_times_stays_on_the_list_and_is_tried_again(self):
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', side_effect=RuntimeError('Kunde inte nå Vast.ai: timed out')) as destroy:
            self.assertFalse(worker.destroy_cloud_gpu('vast', 123))
        self.assertEqual(destroy.call_count, 3)
        self.assertEqual(self.pending(), [123])
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', return_value=True) as destroy:
            worker.retry_pending_cloud_destroys(force=True)
        destroy.assert_called_once_with('vast', 'key', 123)
        self.assertEqual(self.pending(), [])

    def test_a_later_attempt_that_works_leaves_nothing_on_the_list(self):
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', side_effect=[RuntimeError('Vast.ai svarade med fel 502: {}'), True]):
            self.assertTrue(worker.destroy_cloud_gpu('vast', 123))
        self.assertEqual(self.pending(), [])

    def test_a_server_the_provider_no_longer_has_counts_as_gone(self):
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', side_effect=RuntimeError("Vast.ai svarade med fel 404: {'error': 'no_such_instance'}")):
            self.assertTrue(worker.destroy_cloud_gpu('vast', 123))
        self.assertEqual(self.pending(), [])

    def test_a_runpod_server_is_kept_with_its_provider(self):
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', side_effect=RuntimeError('timeout')):
            worker.destroy_cloud_gpu('runpod', 'abc')
        self.assertEqual(self.pending(), ['runpod:abc'])
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', return_value=True) as destroy:
            worker.retry_pending_cloud_destroys(force=True)
        destroy.assert_called_once_with('runpod', 'key', 'abc')

    def test_the_idle_hour_says_so_when_the_server_could_not_be_closed(self):
        saved = []
        kept = {'provider': 'vast', 'instance_id': 123, 'session': True}
        with patch.object(worker, '_KEPT_CLOUD', kept), patch.object(worker.cloud_session, 'load', return_value={}), \
                patch.object(worker.cloud_session, 'idle_seconds', return_value=10 ** 6), \
                patch.object(worker.cloud_session, 'save', side_effect=lambda db, state: saved.append(state)), \
                patch.object(worker.cloud_gpu, 'destroy_chain_instance', side_effect=RuntimeError('timeout')):
            worker.expire_kept_cloud_server()
        self.assertEqual(saved[0]['state'], 'off')
        self.assertIn('kunde INTE stängas', saved[0]['message'])
        self.assertEqual(self.pending(), [123])

    def test_the_retry_waits_a_minute_between_rounds(self):
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', side_effect=RuntimeError('timeout')):
            worker.destroy_cloud_gpu('vast', 123)
        with patch.object(worker.cloud_gpu, 'destroy_chain_instance', return_value=True) as destroy:
            worker.retry_pending_cloud_destroys(force=True)
            worker._pending_destroys(add=7)
            worker.retry_pending_cloud_destroys()
        self.assertEqual(destroy.call_count, 1)


if __name__ == '__main__':
    unittest.main()
