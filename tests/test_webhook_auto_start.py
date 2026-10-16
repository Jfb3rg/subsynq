import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, '/app')
import server


class WebhookAutoStartTests(unittest.TestCase):
    """A webhook queues a job only when "Starta undertextjobb automatiskt" is on (real case 2026-10-16: Sonarr
    imported an episode and queued a billable cloud job although the setting was off)."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = Path(self.folder.name)
        self.media = config / 'episode.mkv'
        self.media.write_bytes(b'x')
        for patcher in (mock.patch.object(server, 'CONFIG', config),
                        mock.patch.object(server, 'DATABASE', config / 'subsynq.db')):
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        with sqlite3.connect(server.DATABASE) as db:
            self.token = db.execute("SELECT value FROM metadata WHERE key='webhook_token'").fetchone()[0]
        for name in ('resolve_webhook_media', 'resolve_webhook_media_per_root'):
            patcher = mock.patch.object(server, name, return_value=self.media)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.queue = mock.patch.object(server, 'queue_webhook_media', return_value=None)
        self.queued = self.queue.start()
        self.addCleanup(self.queue.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def set_auto_start(self, value):
        with sqlite3.connect(server.DATABASE) as db:
            db.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('auto_start_jobs', ?)", (value,))

    def call(self, path, payload):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=15)
        connection.request('POST', f'{path}?token={urllib.parse.quote(self.token, safe="")}', json.dumps(payload),
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = json.loads(response.read().decode('utf-8'))
        connection.close()
        return response.status, body

    PAYLOADS = (
        ('/webhooks/sonarr', {'eventType': 'Download', 'episodeFile': {'path': '/tv/a/b.mkv'}}),
        ('/webhooks/radarr', {'eventType': 'Download', 'movieFile': {'path': '/movies/a.mkv'}}),
        ('/webhooks/emby', {'Event': 'library.new', 'Item': {'Path': '/movies/a.mkv'}}),
        ('/webhooks/jellyfin', {'NotificationType': 'ItemAdded', 'Path': '/movies/a.mkv'}),
    )

    def test_with_automatic_start_off_no_webhook_queues_anything(self):
        self.set_auto_start('0')
        for path, payload in self.PAYLOADS:
            status, body = self.call(path, payload)
            self.assertEqual((status, body['status']), (200, 'skipped'), path)
            self.assertIn('Automatisk jobbstart är av', body['reason'])
        self.queued.assert_not_called()

    def test_a_missing_setting_counts_as_off(self):
        with sqlite3.connect(server.DATABASE) as db:
            db.execute("DELETE FROM metadata WHERE key='auto_start_jobs'")
        self.assertEqual(self.call(*self.PAYLOADS[0])[1]['status'], 'skipped')
        self.queued.assert_not_called()

    def test_with_automatic_start_on_a_download_queues_the_file(self):
        self.set_auto_start('1')
        for path, payload in self.PAYLOADS:
            status, body = self.call(path, payload)
            self.assertEqual((status, body['status']), (200, 'queued'), path)
        self.assertEqual(self.queued.call_count, len(self.PAYLOADS))

    def test_other_events_are_still_ignored_whatever_the_setting(self):
        self.set_auto_start('1')
        self.assertEqual(self.call('/webhooks/sonarr', {'eventType': 'Test'})[1]['status'], 'ignored')
        self.queued.assert_not_called()

    def test_a_wrong_token_is_still_refused(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=15)
        connection.request('POST', '/webhooks/sonarr?token=wrong', json.dumps({'eventType': 'Download'}),
                           {'Content-Type': 'application/json'})
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

    def test_the_settings_pages_say_so(self):
        source = Path(server.__file__).read_text(encoding='utf-8')
        self.assertIn('Gäller också webhooks från Radarr, Sonarr, Jellyfin och Emby', source)
        self.assertIn('och bara när \\"Starta undertextjobb automatiskt\\" är på', source)


if __name__ == '__main__':
    unittest.main()
