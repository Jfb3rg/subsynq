import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server
from jobs import enqueue_job, initialize_jobs, latest_jobs


class QueueRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'retry.db'
        initialize_jobs(self.db)
        self.item = {'path': '/extra/clip.mp4', 'name': 'clip.mp4', 'kind': 'media',
                     'ai_sv_exists': False, 'embedded_sv_exists': False}
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
        self.save_scan()
        self.job_id = enqueue_job(self.db, self.item['path'], self.item['name'])
        self.set_status('quality_failed')

    def save_scan(self):
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                       ('library_scan', json.dumps({'items': [self.item]})))

    def set_status(self, status):
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE subtitle_jobs SET status=? WHERE id=?', (status, self.job_id))

    def retry(self, **kwargs):
        return server.retry_failed_job(self.db, self.item['path'], self.job_id, **kwargs)

    def test_every_finished_status_enqueues_same_media_not_other_files(self):
        for status in ('failed', 'quality_failed', 'completed', 'cancelled', 'deleted'):
            self.set_status(status)
            self.retry()
            new = latest_jobs(self.db)[self.item['path']]
            self.assertEqual(new['status'], 'queued')
            self.assertEqual(new['media_path'], self.item['path'])
            self.assertNotEqual(new['id'], self.job_id)
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE subtitle_jobs SET status='cancelled' WHERE id=?", (new['id'],))
            self.job_id = new['id']

    def test_active_and_stale_job_ids_cannot_be_rerun(self):
        for status in ('queued', 'transcribing', 'translating', 'restart_queued', 'cancel_requested'):
            self.set_status(status)
            with self.assertRaises(ValueError):
                self.retry()
        self.set_status('quality_failed')
        with self.assertRaises(ValueError):
            server.retry_failed_job(self.db, self.item['path'], self.job_id + 99)

    def test_double_click_cannot_enqueue_two_jobs(self):
        self.retry()
        with self.assertRaises(ValueError):
            self.retry()

    def test_missing_library_item_or_existing_embedded_subtitle_blocks_retry(self):
        self.item['kind'] = 'unknown'
        self.save_scan()
        with self.assertRaises(ValueError):
            self.retry()
        self.item.update(kind='media', embedded_sv_exists=True)
        self.save_scan()
        with self.assertRaises(ValueError):
            self.retry()

    def test_existing_ai_requires_explicit_replacement_confirmation(self):
        self.item.update(ai_sv_exists=True, ai_sv_path='/extra/clip.ai.sv.srt')
        self.save_scan()
        with self.assertRaises(ValueError):
            self.retry()
        self.retry(replace_existing=True)
        new = latest_jobs(self.db)[self.item['path']]
        self.assertEqual(new['status'], 'restart_queued')
        self.assertEqual(new['output_path'], self.item['ai_sv_path'])

    def test_queue_button_on_finished_runs_and_includes_exact_job_and_path(self):
        job = {'id': self.job_id, 'status': 'quality_failed', 'media_path': self.item['path']}
        render = lambda: server.render_queue_job_row(job, {self.item['path']: self.item}, 'safe', cancellable=False)
        page = render()
        self.assertIn("action='/jobs/retry'", page)
        self.assertIn('>Kör om<', page)
        self.assertIn("name='job_id' value='1'", page)
        self.assertIn("name='media_path' value='/extra/clip.mp4'", page)
        for status in ('completed', 'cancelled', 'deleted', 'failed'):
            job['status'] = status
            self.assertIn("action='/jobs/retry'", render())
        for status in ('queued', 'transcribing'):
            job['status'] = status
            self.assertNotIn("action='/jobs/retry'", render())

    def test_existing_ai_button_warns_before_removal(self):
        self.item['ai_sv_exists'] = True
        page = server.render_queue_job_row(
            {'id': 1, 'status': 'failed', 'media_path': self.item['path']},
            {self.item['path']: self.item}, 'safe', cancellable=False)
        self.assertIn('return confirm(', page)
        self.assertIn('Ta bort den gamla undertexten', page)

    def test_delete_button_only_when_an_ai_subtitle_exists_on_a_finished_run(self):
        job = {'id': self.job_id, 'status': 'completed', 'media_path': self.item['path']}
        render = lambda: server.render_queue_job_row(job, {self.item['path']: self.item}, 'safe', cancellable=False)
        self.assertNotIn("action='/jobs/delete'", render())
        self.item.update(ai_sv_exists=True, ai_sv_path='/extra/clip.ai.sv.srt')
        page = render()
        self.assertIn("action='/jobs/delete'", page)
        self.assertIn("name='return_kind' value='queue'", page)
        self.assertIn('return confirm(', page)
        job['status'] = 'transcribing'
        self.assertNotIn("action='/jobs/delete'", server.render_queue_job_row(job, {self.item['path']: self.item}, 'safe', cancellable=True))

    def test_delete_from_the_queue_returns_to_the_queue(self):
        self.item.update(ai_sv_exists=True, ai_sv_path='/extra/clip.ai.sv.srt')
        self.save_scan()
        self.set_status('completed')
        body = urlencode({'token': 'safe', 'media_path': self.item['path'], 'return_kind': 'queue'}).encode()
        handler = object.__new__(server.Handler)
        handler.path = '/jobs/delete'
        handler.headers = {'Content-Length': str(len(body))}
        handler.connection = Mock()
        handler.rfile = io.BytesIO(body)
        handler.respond = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        with patch.object(server, 'DATABASE', self.db), patch.object(server, 'SCAN_TOKEN', 'safe'):
            handler.do_POST()
        handler.send_response.assert_called_once_with(303)
        handler.send_header.assert_any_call('Location', '/queue?job=deleting')
        self.assertEqual(latest_jobs(self.db)[self.item['path']]['status'], 'delete_queued')

    def test_waiting_reruns_and_removals_are_counted_and_shown_like_the_menu_does(self):
        jobs = {f'/extra/{n}.mp4': {'id': n, 'status': status, 'media_path': f'/extra/{n}.mp4', 'media_name': f'{n}.mp4', 'updated_at': f'2026-10-25T18:0{n}'}
                for n, status in enumerate(('restart_queued', 'restart_queued', 'delete_queued', 'restarting', 'completed'))}
        page = server.render_queue_content([], jobs, 'safe')
        self.assertEqual(server.active_job_count(jobs), 4)
        self.assertIn('· 3</h2>', page.split('Senaste händelser')[0].split('Aktiva jobb')[1])
        self.assertIn('Aktiva jobb · 1', page)
        for n in range(4):
            self.assertIn(f'{n}.mp4', page.split('Senaste händelser')[0])

    def test_post_requires_token_and_returns_to_queue(self):
        for token, expected in (('wrong', 403), ('safe', 303)):
            handler = object.__new__(server.Handler)
            body = urlencode({'token': token, 'media_path': self.item['path'],
                              'job_id': self.job_id}).encode()
            handler.path = '/jobs/retry'
            handler.headers = {'Content-Length': str(len(body))}
            handler.connection = Mock()
            handler.rfile = io.BytesIO(body)
            handler.respond = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            with patch.object(server, 'DATABASE', self.db), patch.object(server, 'SCAN_TOKEN', 'safe'):
                handler.do_POST()
            if expected == 403:
                self.assertEqual(handler.respond.call_args.args[0], 403)
                handler.send_response.assert_not_called()
            else:
                handler.send_response.assert_called_once_with(303)
                handler.send_header.assert_any_call('Location', '/queue?job=queued')
