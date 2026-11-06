import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = ['/app', '/ai']
import worker
from jobs import delete_subtitle_now, initialize_jobs, latest_jobs


class DeleteNowTests(unittest.TestCase):
    """The user (2026-11-02): removing a subtitle must not wait in the queue for the GPU; it happens at once, with the worker's own checks."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / 'tv'
        self.root.mkdir()
        self.database = Path(folder.name) / 'subsynq.db'
        initialize_jobs(self.database)
        self.media = self.root / 'Example.Series.S04E02.mkv'
        self.media.write_bytes(b'video')
        self.subtitle = self.media.with_suffix('.ai.sv.srt')
        self.subtitle.write_text('1\n00:00:01,000 --> 00:00:02,000\nHej\n', encoding='utf-8')
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_scan', ?)", (json.dumps({'items': [
                {'path': str(self.media), 'name': self.media.name, 'kind': 'episode', 'ai_sv_exists': True,
                 'ai_sv_path': str(self.subtitle)}]}),))

    def delete(self, subtitle=None, media=None):
        return delete_subtitle_now(self.database, str(media or self.media), self.media.name, str(subtitle or self.subtitle), roots=(self.root,))

    def test_the_ai_subtitle_is_removed_and_recorded_at_once(self):
        self.delete()
        self.assertFalse(self.subtitle.exists())
        self.assertTrue(self.media.exists())
        self.assertEqual(latest_jobs(self.database)[str(self.media)]['status'], 'deleted')
        with sqlite3.connect(self.database) as db:
            item = json.loads(db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()[0])['items'][0]
        self.assertEqual((item['ai_sv_exists'], item['ai_sv_path']), (False, None))

    def test_no_other_subtitle_can_be_removed(self):
        other = self.media.with_suffix('.sv.srt')
        other.write_text('x', encoding='utf-8')
        for target in (other, self.media.with_suffix('.opensub.sv.srt'), self.media):
            with self.subTest(target=target.name), self.assertRaises(ValueError):
                self.delete(subtitle=target)
        self.assertTrue(other.exists() and self.media.exists())

    def test_a_link_or_a_video_outside_the_libraries_is_refused(self):
        self.subtitle.unlink()
        os.symlink(self.media, self.subtitle)
        with self.assertRaises(ValueError):
            self.delete()
        self.assertTrue(self.media.exists())
        outside = Path(self.root.parent) / 'other.mkv'
        outside.write_bytes(b'x')
        with self.assertRaises(ValueError):
            self.delete(media=outside, subtitle=outside.with_suffix('.ai.sv.srt'))

    def test_a_subtitle_already_gone_is_still_recorded(self):
        self.subtitle.unlink()
        self.delete()
        self.assertEqual(latest_jobs(self.database)[str(self.media)]['status'], 'deleted')


class RereadOnTheRentedServerTests(unittest.TestCase):
    """regression case(Example Series): rereads loaded Whisper on the home CPU although the job ran on a rented GPU."""

    def test_the_rented_server_reads_again_when_the_job_runs_there(self):
        saved = worker.TRANSLATE_URL
        try:
            worker.TRANSLATE_URL = 'https://1.2.3.4:41000/secret/ollama/api/generate'
            model = worker.reread_whisper('large-v2')
            self.assertIsInstance(model, worker.RemoteCloudWhisperModel)
            self.assertEqual((model.base_url, model.whisper_model), ('https://1.2.3.4:41000/secret', 'large-v2'))
        finally:
            worker.TRANSLATE_URL = saved

    def test_a_local_job_still_reads_on_this_machine(self):
        from unittest.mock import patch
        with patch.object(worker, 'cloud_base_url', return_value=None), patch.object(worker, 'WhisperModel') as local:
            worker.reread_whisper('large-v3')
        local.assert_called_once()
        self.assertEqual(local.call_args.kwargs['device'], 'cpu')



class VisibleDeleteButtonTests(unittest.TestCase):
    """The user (2026-11-02): a small button on every file with an AI subtitle, not hidden behind the job details."""

    def item(self, **extra):
        return {'path': '/tv/Example Series/Example.Series.S04E02.mkv', 'name': 'Example.Series.S04E02.mkv', 'kind': 'episode', 'ai_sv_exists': True,
                'ai_sv_path': '/tv/Example Series/Example.Series.S04E02.ai.sv.srt', 'size_bytes': 1000, 'embedded_sv_exists': False, 'probe': {}, **extra}

    def test_the_row_shows_one_delete_button_next_to_the_actions(self):
        import server
        row = server.render_media_row(self.item(), token='tok')
        self.assertEqual(row.count("action='/jobs/delete'"), 1)
        self.assertIn('small-button danger', row)

    def test_no_button_without_a_subtitle_or_while_a_job_runs(self):
        import server
        self.assertNotIn('/jobs/delete', server.render_media_row(self.item(ai_sv_exists=False, ai_sv_path=None), token='tok'))
        self.assertNotIn('/jobs/delete', server.render_media_row(self.item(), token='tok', job={'id': 9, 'status': 'transcribing', 'progress': 10}))


if __name__ == '__main__':
    unittest.main()
