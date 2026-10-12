import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from jobs import enqueue_delete, enqueue_job, initialize_jobs


def manager(ready):
    return SimpleNamespace(ready=ready)


class DatabaseWaitTests(unittest.TestCase):
    """A fresh install may start the worker before subsynq has created the database."""

    def test_worker_waits_for_the_database_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'subsynq.db'
            with patch.object(worker, 'DATABASE', database):
                self.assertFalse(worker.database_is_ready())
                database.touch()
                self.assertFalse(worker.database_is_ready(), 'tom fil är inte en klar databas')
                with sqlite3.connect(database) as db:
                    db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.assertFalse(worker.database_is_ready(), 'metadata utan jobb och hårdvaruval är inte klart')
                naps = []

                def nap(seconds):
                    naps.append(seconds)
                    if len(naps) == 2:
                        initialize_jobs(database)
                        with sqlite3.connect(database) as db:
                            db.execute("INSERT INTO metadata VALUES ('hardware_preference', 'auto')")

                worker.wait_for_database(poll_seconds=7, sleep=nap)
                self.assertEqual(naps, [7, 7])
                self.assertTrue(worker.database_is_ready())
                self.assertEqual(worker.hardware_preference(), 'auto')
                naps.clear()
                worker.wait_for_database(poll_seconds=7, sleep=nap)
                self.assertEqual(naps, [], 'klar databas ger ingen väntan')

    def test_missing_database_is_not_created_by_the_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'subsynq.db'
            with patch.object(worker, 'DATABASE', database):
                self.assertFalse(worker.database_is_ready())
            self.assertFalse(database.exists())


class ModelWaitTests(unittest.TestCase):
    def test_local_jobs_wait_only_while_models_are_missing_and_the_job_would_run_here(self):
        with patch.object(worker, 'hardware_preference', return_value='auto'):
            self.assertFalse(worker.local_jobs_must_wait(None))
            self.assertFalse(worker.local_jobs_must_wait(manager(True)))
            self.assertTrue(worker.local_jobs_must_wait(manager(False)))
        for cloud in ('cloud-vast', 'cloud-cpu-runpod'):
            with patch.object(worker, 'hardware_preference', return_value=cloud):
                self.assertFalse(worker.local_jobs_must_wait(manager(False)), cloud)
        with patch.object(worker, 'hardware_preference', side_effect=RuntimeError('x')):
            self.assertTrue(worker.local_jobs_must_wait(manager(False)))

    def test_claim_job_leaves_ai_jobs_queued_but_still_takes_deletions(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'subsynq.db'
            initialize_jobs(database)
            enqueue_job(database, '/movies/a.mkv', 'a.mkv')
            enqueue_delete(database, '/movies/b.mkv', 'b.mkv', '/movies/b.ai.sv.srt')
            with patch.object(worker, 'DATABASE', database):
                deletion = worker.claim_job(only_deletes=True)
                self.assertEqual(deletion['media_path'], '/movies/b.mkv')
                self.assertIsNone(worker.claim_job(only_deletes=True))
                with sqlite3.connect(database) as db:
                    status = db.execute("SELECT status FROM subtitle_jobs WHERE media_path='/movies/a.mkv'").fetchone()[0]
                self.assertEqual(status, 'queued')
                transcription = worker.claim_job()
                self.assertEqual(transcription['media_path'], '/movies/a.mkv')

    def test_waiting_note_is_written_once_to_queued_jobs_only(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'subsynq.db'
            initialize_jobs(database)
            enqueue_job(database, '/movies/a.mkv', 'a.mkv')
            with patch.object(worker, 'DATABASE', database):
                worker.note_jobs_waiting_for_models()
                worker.note_jobs_waiting_for_models()
                with sqlite3.connect(database) as db:
                    message = db.execute("SELECT message FROM subtitle_jobs").fetchone()[0]
            self.assertEqual(message, worker.WAITING_FOR_MODELS_MESSAGE)


if __name__ == '__main__':
    unittest.main()
