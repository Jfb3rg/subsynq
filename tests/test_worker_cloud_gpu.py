import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


def make_database(directory, rows=()):
    database = Path(directory) / 'jobs.db'
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE subtitle_jobs (
            id INTEGER PRIMARY KEY, media_path TEXT, media_name TEXT,
            status TEXT, progress INTEGER, message TEXT, updated_at TEXT,
            output_path TEXT, hardware TEXT, model TEXT,
            cloud_instance_id INTEGER)''')
        for row in rows:
            db.execute(
                "INSERT INTO subtitle_jobs "
                "(id, media_path, media_name, status, progress, message, updated_at, cloud_instance_id) "
                "VALUES (?,?,?,?,0,'','',?)", row)
        db.execute('''CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)''')
    return database


class DestroyCloudGpuTests(unittest.TestCase):
    def test_swallows_errors_so_a_failed_teardown_never_crashes_the_worker(self):
        with patch.object(worker, 'load_cloud_keys', return_value={'vast': 'secret'}), \
                patch.object(worker.cloud_gpu, 'destroy_instance', side_effect=RuntimeError('boom')):
            worker.destroy_cloud_gpu('vast', 123)

    def test_does_nothing_without_an_instance_id(self):
        with patch.object(worker.cloud_gpu, 'destroy_instance') as destroy:
            worker.destroy_cloud_gpu('vast', None)
            destroy.assert_not_called()


class CleanupOrphanedCloudInstancesTests(unittest.TestCase):
    def test_destroys_and_clears_instances_left_by_a_crashed_job(self):
        with tempfile.TemporaryDirectory() as directory:
            database = make_database(directory, [
                (1, '/tv/a.mkv', 'a.mkv', 'queued', 777),
                (2, '/tv/b.mkv', 'b.mkv', 'transcribing', 888),
                (3, '/tv/c.mkv', 'c.mkv', 'completed', None),
            ])
            with patch.object(worker, 'DATABASE', database), \
                    patch.object(worker, 'destroy_cloud_gpu') as destroy:
                worker.cleanup_orphaned_cloud_instances()
            destroy.assert_called_once_with('vast', 777)
            with sqlite3.connect(database) as db:
                remaining = dict(db.execute('SELECT id, cloud_instance_id FROM subtitle_jobs'))
            self.assertIsNone(remaining[1])
            self.assertEqual(remaining[2], 888)


if __name__ == '__main__':
    unittest.main()
