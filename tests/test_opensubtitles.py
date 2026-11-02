import io
import json
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import opensubtitles as os_
import server
import worker
from jobs import initialize_jobs

SRT = '1\n00:00:01,000 --> 00:00:02,000\nHej då.\n'


def hit(*, release, file_name='', moviehash_match=False, language='sv', machine=False, ai=False, file_id=7):
    return {'attributes': {'language': language, 'release': release, 'moviehash_match': moviehash_match, 'machine_translated': machine,
                           'ai_translated': ai, 'files': [{'file_id': file_id, 'file_name': file_name}]}}


class FakeApi:
    """Stands in for api.opensubtitles.com; records every request."""

    def __init__(self, by_hash=(), by_name=(), *, download_status=200, login_host='vip-api.opensubtitles.com', content=SRT.encode()):
        self.by_hash, self.by_name = list(by_hash), list(by_name)
        self.download_status, self.login_host, self.content = download_status, login_host, content
        self.requests = []

    def __call__(self, request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        self.requests.append((request.get_method(), url.netloc, url.path, dict(urllib.parse.parse_qsl(url.query)), dict(request.header_items())))
        if url.path.endswith('/login'):
            return io.BytesIO(json.dumps({'token': 'T', 'base_url': self.login_host}).encode())
        if url.path.endswith('/subtitles'):
            query = dict(urllib.parse.parse_qsl(url.query))
            return io.BytesIO(json.dumps({'data': self.by_hash if 'moviehash' in query else self.by_name}).encode())
        if url.path.endswith('/download'):
            if self.download_status != 200:
                raise urllib.error.HTTPError(request.full_url, self.download_status, 'x', {}, None)
            return io.BytesIO(json.dumps({'link': 'https://dl.opensubtitles.org/file.srt', 'remaining': 99}).encode())
        if url.netloc == 'dl.opensubtitles.org':
            return io.BytesIO(self.content)
        raise AssertionError(request.full_url)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.media = self.root / 'Film.2024.1080p.WEB-DL.DDP5.1.H.264-GRP.mkv'
        self.media.write_bytes(b'\x01' * (3 * os_.HASH_CHUNK))
        os_._tokens.clear()

    def enable(self, **extra):
        return os_.save_settings(self.config, enabled=True, automatic=extra.pop('automatic', False), api_key='KEY12345',
                                 username=extra.pop('username', 'johan'), password='pw')


class MatchingTests(Base):
    def test_hash_is_size_plus_words_of_both_ends(self):
        empty = self.root / 'zeros.bin'
        empty.write_bytes(bytes(2 * os_.HASH_CHUNK))
        self.assertEqual(os_.movie_hash(empty), f'{2 * os_.HASH_CHUNK:016x}')
        words = (3 * os_.HASH_CHUNK) + 2 * (os_.HASH_CHUNK // 8) * struct.unpack('<Q', b'\x01' * 8)[0]
        self.assertEqual(os_.movie_hash(self.media), f'{words & 0xFFFFFFFFFFFFFFFF:016x}')

    def test_name_key_ignores_case_separators_and_extension(self):
        self.assertEqual(os_.name_key('Film.2024.1080p.WEB-DL.DDP5.1.H.264-GRP.mkv'), os_.name_key('film 2024 1080p web dl ddp5 1 h 264 grp'))
        self.assertNotEqual(os_.name_key('Film.2024.1080p.WEB-DL-GRP'), os_.name_key('Film.2024.720p.WEB-DL-GRP'))

    def test_hash_match_first_then_exact_release_name_never_title(self):
        name = self.media.name
        chosen, _ = os_.choose([hit(release='other', moviehash_match=True, file_id=1)], [], name)
        self.assertEqual((chosen['file_id'], chosen['how']), (1, 'filkontrollsumma'))
        chosen, _ = os_.choose([hit(release='x', moviehash_match=False)], [hit(release='Film.2024.1080p.WEB-DL.DDP5.1.H.264-GRP', file_id=2)], name)
        self.assertEqual((chosen['file_id'], chosen['how']), (2, 'filnamn'))
        chosen, _ = os_.choose([], [hit(release='x', file_name='Film.2024.1080p.WEB-DL.DDP5.1.H.264-GRP.srt', file_id=3)], name)
        self.assertEqual(chosen['file_id'], 3)
        chosen, why = os_.choose([], [hit(release='Film 2024'), hit(release='Film.2024.720p.BluRay-OTHER')], name)
        self.assertIsNone(chosen)
        self.assertIn('Ingen titel-gissning', why)

    def test_machine_and_ai_translations_and_other_languages_are_skipped(self):
        for item in (hit(release='a', moviehash_match=True, machine=True), hit(release='a', moviehash_match=True, ai=True),
                     hit(release='a', moviehash_match=True, language='en')):
            self.assertIsNone(os_.choose([item], [], self.media.name)[0])


class ClientTests(Base):
    def test_fetch_logs_in_searches_by_hash_downloads_and_writes_beside_the_video(self):
        self.enable()
        api = FakeApi(by_hash=[hit(release='r', moviehash_match=True, file_id=11)])
        ai_subtitle = self.media.with_suffix('.ai.sv.srt')
        ai_subtitle.write_text('AI', encoding='utf-8')
        result = os_.fetch(self.config, self.media, 'SubSynq vtest', opener=api)
        target = Path(result['path'])
        self.assertEqual(target.name, 'Film.2024.1080p.WEB-DL.DDP5.1.H.264-GRP.opensub.sv.srt')
        self.assertEqual(target.read_text(encoding='utf-8'), SRT)
        self.assertEqual(ai_subtitle.read_text(encoding='utf-8'), 'AI')
        self.assertEqual((result['how'], result['remaining']), ('filkontrollsumma', 99))
        methods = [(method, host, path.rsplit('/', 1)[1]) for method, host, path, _, _ in api.requests]
        self.assertEqual(methods[0], ('POST', 'api.opensubtitles.com', 'login'))
        self.assertEqual(methods[1][1], 'vip-api.opensubtitles.com')                     # the member's host after login
        search = api.requests[1]
        self.assertEqual(search[3]['languages'], 'sv')
        self.assertIn('moviehash', search[3])
        self.assertEqual(search[4]['Api-key'], 'KEY12345')
        self.assertEqual(search[4]['User-agent'], 'SubSynq vtest')
        self.assertEqual(len([r for r in api.requests if r[2].endswith('/subtitles')]), 1)   # a hash match needs no name search

    def test_no_safe_match_downloads_nothing(self):
        self.enable()
        api = FakeApi(by_name=[hit(release='Film 2024')])
        with self.assertRaises(os_.OpenSubtitlesError):
            os_.fetch(self.config, self.media, 'SubSynq vtest', opener=api)
        self.assertFalse(any(r[2].endswith('/download') for r in api.requests))
        self.assertFalse(os_.target_path(self.media).exists())

    def test_daily_limit_is_reported(self):
        self.enable()
        api = FakeApi(by_hash=[hit(release='r', moviehash_match=True)], download_status=406)
        with self.assertRaises(os_.OpenSubtitlesError) as raised:
            os_.fetch(self.config, self.media, 'SubSynq vtest', opener=api)
        self.assertIn('gräns', str(raised.exception))

    def test_a_foreign_login_host_is_not_trusted(self):
        self.enable()
        api = FakeApi(by_hash=[hit(release='r', moviehash_match=True)], login_host='evil.example.com')
        os_.fetch(self.config, self.media, 'SubSynq vtest', opener=api)
        self.assertTrue(all(host in ('api.opensubtitles.com', 'dl.opensubtitles.org') for _, host, _, _, _ in api.requests))

    def test_old_encoding_is_converted_and_non_srt_refused(self):
        self.assertIn('Hej då', os_.subtitle_text(SRT.encode('cp1252')))
        with self.assertRaises(os_.OpenSubtitlesError):
            os_.subtitle_text(b'<html>not a subtitle</html>')

    def test_switched_off_does_nothing(self):
        with self.assertRaises(os_.OpenSubtitlesError):
            os_.fetch(self.config, self.media, 'SubSynq vtest', opener=FakeApi())


class SettingsTests(Base):
    def test_secrets_are_kept_when_left_empty_and_needed_to_switch_on(self):
        self.enable()
        os_.save_settings(self.config, enabled=True, automatic=True)
        settings = os_.load_settings(self.config)
        self.assertEqual((settings['api_key'], settings['username'], settings['password'], settings['automatic']), ('KEY12345', 'johan', 'pw', True))
        os_.forget_settings(self.config)
        self.assertFalse(os_.load_settings(self.config)['enabled'])
        with self.assertRaises(ValueError):
            os_.save_settings(self.config, enabled=True, automatic=False)

    def test_the_card_never_shows_a_secret(self):
        self.enable()
        card = os_.render_card(os_.load_settings(self.config), 'tok')
        self.assertNotIn('KEY12345', card)
        self.assertNotIn('johan', card)
        self.assertIn('/settings/opensubtitles', card)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        initialize_jobs(self.config / 'subsynq.db')
        with sqlite3.connect(self.config / 'subsynq.db') as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        for target, value in (('CONFIG', self.config), ('DATABASE', self.config / 'subsynq.db')):
            patcher = patch.object(server, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, route, **fields):
        body = urllib.parse.urlencode({'token': server.SCAN_TOKEN, **fields}).encode()
        try:
            urllib.request.build_opener(NoRedirect).open(urllib.request.Request(f'http://127.0.0.1:{self.httpd.server_port}{route}', body), timeout=10)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get('Location')
        raise AssertionError('expected a redirect')

    def test_settings_are_saved_through_the_form(self):
        status, location = self.post('/settings/opensubtitles', action='save', enabled='1', api_key='KEY12345')
        self.assertEqual((status, location), (303, '/settings?saved=opensubtitles#opensubtitles'))
        self.assertTrue(os_.load_settings(self.config)['enabled'])
        status, _ = self.post('/settings/opensubtitles', action='forget')
        self.assertFalse(os_.load_settings(self.config)['api_key'])

    def test_a_file_outside_the_library_is_refused(self):
        self.enable()
        status, location = self.post('/jobs/opensubtitles', media_path='/etc/passwd')
        self.assertEqual((status, location), (303, '/queue?saved=opensubtitles'))
        self.assertIn('biblioteksskanningen', server.opensubtitles_notice())

    def test_the_queue_button_only_for_refused_runs_when_switched_on(self):
        job = {'id': 1, 'status': 'quality_failed', 'media_path': '/extra/a.mkv', 'message': ''}
        library = {'/extra/a.mkv': {'path': '/extra/a.mkv', 'kind': 'media', 'ai_sv_exists': False}}
        self.assertNotIn('/jobs/opensubtitles', server.render_queue_job_row(job, library, 'tok', cancellable=False))
        self.enable()
        self.assertIn('/jobs/opensubtitles', server.render_queue_job_row(job, library, 'tok', cancellable=False))
        self.assertNotIn('/jobs/opensubtitles', server.render_queue_job_row({**job, 'status': 'completed'}, library, 'tok', cancellable=False))


class LibraryButtonTests(RouteTests):
    def item(self, **extra):
        return {'path': str(self.media), 'name': self.media.name, 'kind': 'movie', 'ai_sv_exists': False, 'size_bytes': 1000,
                'embedded_sv_exists': False, 'probe': {}, **extra}

    def test_the_library_row_offers_the_fallback_only_when_switched_on_and_without_an_ai_subtitle(self):
        self.assertNotIn('/jobs/opensubtitles', server.render_media_row(self.item(), token='tok'))
        self.enable()
        row = server.render_media_row(self.item(), token='tok')
        self.assertIn('/jobs/opensubtitles', row)
        self.assertIn("name='return_kind' value='movie'", row)
        self.assertNotIn('/jobs/opensubtitles', server.render_media_row(self.item(ai_sv_exists=True, ai_sv_path='x'), token='tok'))
        self.assertNotIn('/jobs/opensubtitles', server.render_media_row(self.item(), token='tok', job={'id': 9, 'status': 'transcribing', 'progress': 10}))

    def test_the_answer_returns_to_the_list_it_came_from(self):
        self.enable()
        status, location = self.post('/jobs/opensubtitles', media_path='/etc/passwd', return_kind='movie', return_show='missing')
        self.assertEqual((status, location), (303, '/library?kind=movie&job=opensubtitles&show=missing'))

    def test_enabled_follows_the_settings_file(self):
        self.assertFalse(os_.enabled(self.config))
        self.enable()
        self.assertTrue(os_.enabled(self.config))
        os_.forget_settings(self.config)
        self.assertFalse(os_.enabled(self.config))


class WorkerTests(Base):
    def setUp(self):
        super().setUp()
        self.database = self.config / 'jobs.db'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE subtitle_jobs (id INTEGER PRIMARY KEY, media_path TEXT, status TEXT, message TEXT)')
            db.execute("INSERT INTO subtitle_jobs VALUES (5, ?, 'quality_failed', 'Underkänd.')", (str(self.media),))
        for target, value in (('CONFIG', self.config), ('DATABASE', self.database)):
            patcher = patch.object(worker, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def message(self):
        with sqlite3.connect(self.database) as db:
            return db.execute('SELECT status, message FROM subtitle_jobs WHERE id=5').fetchone()

    def test_off_or_manual_only_does_nothing(self):
        worker.emergency_subtitle({'id': 5, 'media_path': str(self.media)})
        self.enable(automatic=False)
        with patch.object(worker.opensubtitles, 'fetch') as fetch:
            worker.emergency_subtitle({'id': 5, 'media_path': str(self.media)})
        fetch.assert_not_called()
        self.assertEqual(self.message(), ('quality_failed', 'Underkänd.'))

    def test_automatic_fetch_after_a_refused_job_is_noted_on_the_job(self):
        self.enable(automatic=True)
        with patch.object(worker.opensubtitles, 'fetch', return_value={'path': '/x/Film.opensub.sv.srt', 'how': 'filkontrollsumma'}):
            worker.emergency_subtitle({'id': 5, 'media_path': str(self.media)})
        status, message = self.message()
        self.assertEqual(status, 'quality_failed')
        self.assertIn('hämtad från OpenSubtitles (matchad på filkontrollsumma)', message)

    def test_no_match_is_noted_and_never_raises(self):
        self.enable(automatic=True)
        with patch.object(worker.opensubtitles, 'fetch', side_effect=os_.OpenSubtitlesError('Ingen svensk undertext.')):
            worker.emergency_subtitle({'id': 5, 'media_path': str(self.media)})
        self.assertIn('ingen undertext från OpenSubtitles', self.message()[1])
        with patch.object(worker.opensubtitles, 'fetch', side_effect=RuntimeError('boom')):
            worker.emergency_subtitle({'id': 5, 'media_path': str(self.media)})

    def test_a_completed_job_is_never_given_a_fallback(self):
        self.enable(automatic=True)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE subtitle_jobs SET status='completed' WHERE id=5")
        with patch.object(worker.opensubtitles, 'fetch') as fetch:
            worker.emergency_subtitle({'id': 5, 'media_path': str(self.media)})
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
