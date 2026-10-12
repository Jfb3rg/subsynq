import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from jobs import enqueue_delete, enqueue_job, enqueue_restart, initialize_jobs, latest_jobs, request_cancel


class JobTests(unittest.TestCase):
    def test_cancel_is_terminal_before_claim_and_requested_during_work(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'test.db'
            initialize_jobs(database)
            queued = enqueue_job(database, '/tv/show/S01E01.mkv', 'S01E01.mkv')
            self.assertEqual(request_cancel(database, queued, '/tv/show/S01E01.mkv'), 'cancelled')
            running = enqueue_job(database, '/tv/show/S01E01.mkv', 'S01E01.mkv')
            with sqlite3.connect(database) as db:
                db.execute("UPDATE subtitle_jobs SET status='transcribing' WHERE id=?", (running,))
            self.assertEqual(request_cancel(database, running, '/tv/show/S01E01.mkv'),
                             'cancel_requested')
            self.assertEqual(latest_jobs(database)['/tv/show/S01E01.mkv']['status'],
                             'cancel_requested')

    def test_queue_is_persistent_and_rejects_duplicate_active_job(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'test.db'
            initialize_jobs(database)
            job_id = enqueue_job(database, '/tv/show/S01E01.mkv', 'S01E01.mkv')
            self.assertGreater(job_id, 0)
            job = latest_jobs(database)['/tv/show/S01E01.mkv']
            self.assertEqual(job['status'], 'queued')
            with self.assertRaises(ValueError):
                enqueue_job(database, '/tv/show/S01E01.mkv', 'S01E01.mkv')
            with sqlite3.connect(database) as db:
                db.execute("UPDATE subtitle_jobs SET status='failed' WHERE id=?", (job_id,))
            self.assertGreater(enqueue_job(database, '/tv/show/S01E01.mkv', 'S01E01.mkv'), job_id)

    def test_rejected_file_deletion_is_a_persistent_job(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'test.db'
            initialize_jobs(database)
            job_id = enqueue_delete(database, '/tv/show/S01E01.mkv', 'S01E01.mkv',
                                    '/tv/show/S01E01.sv.ai.srt')
            job = latest_jobs(database)['/tv/show/S01E01.mkv']
            self.assertEqual(job['status'], 'delete_queued')
            self.assertEqual(job['output_path'], '/tv/show/S01E01.sv.ai.srt')
            self.assertEqual(job['id'], job_id)

    def test_restart_is_a_persistent_job_and_blocks_a_second_active_job(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'test.db'
            initialize_jobs(database)
            job_id = enqueue_restart(database, '/movies/f.mkv', 'f.mkv', '/movies/f.ai.sv.srt')
            job = latest_jobs(database)['/movies/f.mkv']
            self.assertEqual(job['status'], 'restart_queued')
            self.assertEqual(job['output_path'], '/movies/f.ai.sv.srt')
            self.assertEqual(job['id'], job_id)
            with self.assertRaises(ValueError):
                enqueue_job(database, '/movies/f.mkv', 'f.mkv')
            with sqlite3.connect(database) as db:
                db.execute("UPDATE subtitle_jobs SET status='restarting' WHERE id=?", (job_id,))
            with self.assertRaises(ValueError):
                enqueue_restart(database, '/movies/f.mkv', 'f.mkv', '/movies/f.ai.sv.srt')


if __name__ == '__main__':
    unittest.main()
