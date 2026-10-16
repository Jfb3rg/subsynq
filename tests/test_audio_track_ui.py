import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, '/app')
import server
from jobs import enqueue_job, enqueue_restart, initialize_jobs, latest_jobs


def audio(index, language='und', title='', codec='aac', channels=2):
    return {'index': index, 'codec': codec, 'language': language, 'title': title, 'channels': channels,
            'default': False, 'forced': False, 'hearing_impaired': False}


def item_with(tracks, status='ok'):
    return {'path': '/extra/clip.mp4', 'name': 'clip.mp4', 'kind': 'media', 'size_bytes': 1024,
            'ai_sv_exists': False, 'embedded_sv_exists': False,
            'probe': {'status': status, 'audio': tracks, 'subtitle': [], 'video': []}}


class AudioTrackJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'jobs.db'
        initialize_jobs(self.db)

    def column(self, job_id):
        with sqlite3.connect(self.db) as db:
            return db.execute('SELECT audio_track FROM subtitle_jobs WHERE id=?', (job_id,)).fetchone()[0]

    def test_an_old_database_gains_the_column_and_keeps_its_rows(self):
        old = Path(self.tmp.name) / 'old.db'
        with sqlite3.connect(old) as db:
            db.execute("""CREATE TABLE subtitle_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_path TEXT NOT NULL, media_name TEXT NOT NULL, status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', output_path TEXT,
                hardware TEXT, model TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("INSERT INTO subtitle_jobs(media_path,media_name,status,created_at,updated_at) "
                       "VALUES ('/a.mkv','a.mkv','completed','x','x')")
        initialize_jobs(old)
        initialize_jobs(old)
        self.assertIsNone(latest_jobs(old)['/a.mkv']['audio_track'])

    def test_a_job_is_automatic_unless_a_track_is_named(self):
        self.assertIsNone(self.column(enqueue_job(self.db, '/a.mkv', 'a.mkv')))
        self.assertEqual(self.column(enqueue_job(self.db, '/b.mkv', 'b.mkv', 1)), 1)
        self.assertEqual(self.column(enqueue_job(self.db, '/c.mkv', 'c.mkv', 0)), 0)

    def test_a_rerun_keeps_the_track_picked_for_the_earlier_run(self):
        first = enqueue_job(self.db, '/a.mkv', 'a.mkv', 1)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE subtitle_jobs SET status='completed' WHERE id=?", (first,))
        restart = enqueue_restart(self.db, '/a.mkv', 'a.mkv', '/a.ai.sv.srt')
        self.assertEqual(self.column(restart), 1)

    def test_a_rerun_of_an_automatic_job_stays_automatic(self):
        first = enqueue_job(self.db, '/a.mkv', 'a.mkv')
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE subtitle_jobs SET status='completed' WHERE id=?", (first,))
        self.assertIsNone(self.column(enqueue_restart(self.db, '/a.mkv', 'a.mkv', '/a.ai.sv.srt')))

    def test_retrying_a_failed_job_keeps_its_track(self):
        item = item_with([audio(0), audio(1)])
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('INSERT INTO metadata VALUES (?,?)', ('library_scan', json.dumps({'items': [item]})))
        job_id = enqueue_job(self.db, item['path'], item['name'], 1)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE subtitle_jobs SET status='quality_failed' WHERE id=?", (job_id,))
        new_id = server.retry_failed_job(self.db, item['path'], job_id)
        self.assertEqual(self.column(new_id), 1)


class RequestedTrackTests(unittest.TestCase):
    def test_blank_means_automatic_and_a_known_stream_is_accepted(self):
        item = item_with([audio(1), audio(3)])
        self.assertIsNone(server.requested_audio_track('', item))
        self.assertIsNone(server.requested_audio_track(None, item))
        self.assertEqual(server.requested_audio_track('3', item), 3)
        self.assertEqual(server.requested_audio_track(' 1 ', item), 1)

    def test_a_stream_the_scan_did_not_see_is_refused(self):
        item = item_with([audio(1), audio(3)])
        for value in ('0', '2', '99', 'x', '-1', '1.5'):
            with self.assertRaises(ValueError, msg=value):
                server.requested_audio_track(value, item)

    def test_a_file_without_usable_scan_data_refuses_any_named_track(self):
        with self.assertRaises(ValueError):
            server.requested_audio_track('1', item_with([], status='pending'))


class AudioTrackChoiceRenderTests(unittest.TestCase):
    def test_no_drop_down_for_a_file_with_one_or_no_audio_track(self):
        self.assertEqual(server.render_audio_track_choice(item_with([audio(1)])), '')
        self.assertEqual(server.render_audio_track_choice(item_with([])), '')
        self.assertEqual(server.render_audio_track_choice(item_with([audio(0), audio(1)], status='pending')), '')

    def test_drop_down_lists_automatic_first_and_every_track(self):
        html = server.render_audio_track_choice(item_with([
            audio(0), audio(1, 'eng', 'Original 5.1', 'eac3', 6), audio(2, 'ger', '<b>x</b>')]))
        self.assertIn("name='audio_track'", html)
        self.assertLess(html.index("value=''"), html.index("value='0'"))
        self.assertIn('Automatiskt (engelska först)', html)
        self.assertIn('Spår 0 · Språk ej angivet · AAC · 2 kanaler', html)
        self.assertIn('Spår 1 · Engelska · EAC3 · 6 kanaler · Original 5.1', html)
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', html)
        self.assertNotIn('<b>x</b>', html)

    def test_english_page_uses_english_words(self):
        html = server.render_audio_track_choice(item_with([audio(0), audio(1)]), 'en')
        self.assertIn('Automatic (English first)', html)
        self.assertIn('Track 1', html)

    def test_start_form_carries_the_drop_down_only_for_multi_track_files(self):
        many = server.render_media_row(item_with([audio(0), audio(1)]), token='tok')
        self.assertIn("action='/jobs/start'", many)
        self.assertIn("name='audio_track'", many)
        self.assertLess(many.index("name='audio_track'"), many.index('Skapa svensk AI-undertext'))
        single = server.render_media_row(item_with([audio(0)]), token='tok')
        self.assertIn("action='/jobs/start'", single)
        self.assertNotIn("name='audio_track'", single)


if __name__ == '__main__':
    unittest.main()
