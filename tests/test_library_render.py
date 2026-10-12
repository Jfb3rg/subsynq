import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from server import needs_swedish_subtitle, render_library, render_media_row, runnable_library_item


class LibraryRenderEnglishTests(unittest.TestCase):
    def test_media_row_translates_status_and_buttons_to_english(self):
        item = {'path': '/movies/f.mkv', 'name': 'f.mkv', 'kind': 'movie', 'size_bytes': 1024,
                'ai_sv_exists': False, 'embedded_sv_exists': False, 'probe': {}}
        swedish = render_media_row(item, token='safe', lang='sv')
        english = render_media_row(item, token='safe', lang='en')
        self.assertIn('Svensk undertext saknas', swedish)
        self.assertIn('Skapa svensk AI-undertext', swedish)
        self.assertIn('Swedish subtitle missing', english)
        self.assertIn('Create Swedish AI subtitle', english)
        self.assertNotIn('saknas', english)

    def test_render_library_table_headers_translate(self):
        items = [{'path': '/movies/f.mkv', 'name': 'f.mkv', 'kind': 'movie', 'size_bytes': 1024,
                  'ai_sv_exists': False, 'embedded_sv_exists': False, 'probe': {}}]
        english = render_library(items, 'movie', 'safe', lang='en')
        self.assertIn('Movie / file', english)
        self.assertIn('Swedish AI subtitle', english)


class LibraryRenderTests(unittest.TestCase):
    def test_scanned_movies_and_extra_media_are_runnable(self):
        library = {'items': [
            {'path': '/movies/film.mkv', 'kind': 'movie'},
            {'path': '/extra/clip.mp4', 'kind': 'media'},
            {'path': '/tv/show.mkv', 'kind': 'episode'},
            {'path': '/outside/unknown.mkv', 'kind': 'unknown'},
        ]}
        self.assertEqual(runnable_library_item(library, '/movies/film.mkv')['kind'], 'movie')
        self.assertEqual(runnable_library_item(library, '/extra/clip.mp4')['kind'], 'media')
        self.assertEqual(runnable_library_item(library, '/tv/show.mkv')['kind'], 'episode')
        self.assertIsNone(runnable_library_item(library, '/outside/unknown.mkv'))
        self.assertIsNone(runnable_library_item(library, '/movies/missing.mkv'))

    def test_running_job_has_cancel_button(self):
        item = {
            'path': '/tv/Show/Season 1/Show S01E01.mkv', 'name': 'Show S01E01.mkv',
            'kind': 'episode', 'episode_code': 'S01E01', 'size_bytes': 1024,
            'ai_sv_exists': False, 'embedded_sv_exists': False, 'probe': {},
        }
        job = {'id': 7, 'status': 'transcribing', 'progress': 25,
               'message': 'Lyssnar', 'hardware': 'AMD Radeon RX 9070 XT'}
        page = render_media_row(item, nested=True, token='token', job=job)
        self.assertIn('Avbryt jobb', page)
        self.assertIn("action='/jobs/cancel'", page)
        self.assertIn("name='job_id' value='7'", page)

    def test_completed_job_shows_actual_hardware_and_model(self):
        item = {
            'path': '/tv/Show/Season 1/Show S01E01.mkv', 'name': 'Show S01E01.mkv',
            'kind': 'episode', 'episode_code': 'S01E01', 'size_bytes': 1024,
            'ai_sv_exists': True, 'embedded_sv_exists': False, 'probe': {},
        }
        job = {'status': 'completed', 'hardware': 'AMD Radeon RX 9070 XT',
               'model': 'Whisper large-v3 + Qwen3-ASR 1.7B'}
        page = render_media_row(item, nested=True, token='token', job=job)
        self.assertIn('Använd hårdvara/modell:', page)
        self.assertIn('AMD Radeon RX 9070 XT', page)
        self.assertIn('Qwen3-ASR 1.7B', page)

    def test_ignore_existing_subtitle_overrides_embedded_coverage(self):
        item = {
            'path': '/movies/film.mkv', 'name': 'film.mkv', 'kind': 'movie', 'size_bytes': 1024,
            'ai_sv_exists': False, 'embedded_sv_exists': True,
            'probe': {'status': 'ok', 'audio': [],
                      'subtitle': [{'index': 0, 'language': 'sv', 'codec': 'hdmv_pgs_subtitle'}]},
        }
        self.assertFalse(needs_swedish_subtitle(item))
        self.assertTrue(needs_swedish_subtitle(item, ignore_existing=True))
        default_page = render_media_row(item, token='safe')
        self.assertNotIn('Skapa svensk AI-undertext', default_page)
        override_page = render_media_row(item, token='safe', ignore_existing=True)
        self.assertIn('Skapa svensk AI-undertext', override_page)
        self.assertIn('Körs trots inbäddad svensk undertext', override_page)

    def test_ignore_existing_subtitle_never_overrides_own_ai_subtitle(self):
        item = {
            'path': '/movies/film.mkv', 'name': 'film.mkv', 'kind': 'movie', 'size_bytes': 1024,
            'ai_sv_exists': True, 'embedded_sv_exists': False, 'probe': {},
        }
        self.assertFalse(needs_swedish_subtitle(item, ignore_existing=True))

    def test_english_subtitle_link_shown_independently_of_swedish_status(self):
        item = {
            'path': '/movies/film.mkv', 'name': 'film.mkv', 'kind': 'movie', 'size_bytes': 1024,
            'ai_sv_exists': True, 'embedded_sv_exists': False, 'ai_en_exists': True, 'probe': {},
        }
        page = render_media_row(item, token='safe')
        self.assertIn('Visa engelsk undertext', page)
        self.assertIn('lang=en', page)

    def test_old_completed_job_does_not_claim_missing_srt_exists(self):
        item = {
            'path': '/extra/trailer.mp4', 'name': 'trailer.mp4', 'kind': 'media',
            'size_bytes': 1024, 'ai_sv_exists': False, 'embedded_sv_exists': False,
            'probe': {'status': 'ok', 'audio': [], 'subtitle': []},
        }
        page = render_media_row(item, token='safe', job={
            'id': 9, 'status': 'completed', 'message': 'Klar',
        })
        self.assertIn('Svensk undertext saknas', page)
        self.assertIn('undertextfilen saknas nu', page)
        self.assertIn('Skapa svensk AI-undertext', page)
        self.assertNotIn('AI-undertext skapad', page)

    def test_series_and_seasons_are_grouped_and_missing_is_marked(self):
        base = {'kind': 'episode', 'size_bytes': 1024, 'probe': {'status': 'pending'},
                'series': 'My Show'}
        items = [
            {**base, 'name': 'second.mkv', 'season': 'Säsong 1', 'season_number': 1,
             'episode_number': 2, 'episode_code': 'S01E02', 'ai_sv_exists': False,
             'path': '/tv/My Show/Season 1/second.mkv'},
            {**base, 'name': 'first.mkv', 'season': 'Säsong 1', 'season_number': 1,
             'episode_number': 1, 'episode_code': 'S01E01', 'ai_sv_exists': True,
             'path': '/tv/My Show/Season 1/first.mkv'},
            {**base, 'name': 'new.mkv', 'season': 'Säsong 2', 'season_number': 2,
             'episode_number': 1, 'episode_code': 'S02E01', 'ai_sv_exists': False,
             'path': '/tv/My Show/Season 2/new.mkv'},
        ]
        page = render_library(items, 'episode', 'safe-token')
        self.assertIn('My Show', page)
        self.assertIn('Säsong 1', page)
        self.assertIn('Säsong 2', page)
        self.assertNotIn("class='season-group' open", page)
        self.assertIn("data-season='My Show␟Säsong 1'", page)
        self.assertNotIn("class='series-group' open", page)
        self.assertIn("class='series-group' data-series='My Show'", page)
        self.assertIn('2 säsonger', page)
        self.assertLess(page.index("data-series='My Show'"), page.index('Säsong 1'))
        self.assertIn("class='subtitle-missing'", page)
        self.assertIn('Svensk undertext saknas', page)
        self.assertIn('Skapa svensk AI-undertext', page)
        self.assertIn("action='/jobs/start'", page)
        self.assertLess(page.index('S01E01'), page.index('S01E02'))
        self.assertIn('Visa undertexten', page)

    def test_rejected_job_is_visible_even_when_file_exists(self):
        item = {'kind': 'episode', 'size_bytes': 1024, 'probe': {'status': 'pending'},
                'series': 'My Show', 'season': 'Säsong 1', 'season_number': 1,
                'episode_number': 1, 'episode_code': 'S01E01', 'ai_sv_exists': True,
                'ai_sv_path': '/tv/My Show/e01.sv.ai.srt', 'name': 'e01.mkv',
                'path': '/tv/My Show/e01.mkv'}
        page = render_library([item], 'episode', 'safe-token', {
            item['path']: {'status': 'quality_failed', 'message': 'Orimliga tider'}
        })
        self.assertIn('Underkänd – använd inte', page)
        self.assertIn('Visa undertexten', page)
        self.assertIn('Ta bort AI-undertext', page)
        self.assertIn("action='/jobs/delete'", page)
        self.assertIn("class='delete-job-form preserve-seasons'", page)

    def test_quality_failed_job_without_srt_can_be_retried(self):
        item = {'kind': 'media', 'size_bytes': 1024,
                'probe': {'status': 'ok', 'audio': [], 'subtitle': []},
                'ai_sv_exists': False, 'embedded_sv_exists': False,
                'name': 'broken-trailer.mp4', 'path': '/extra/broken-trailer.mp4'}
        page = render_media_row(item, token='safe-token', job={
            'status': 'quality_failed', 'message': 'Källtranskriberingen underkändes',
        })
        self.assertIn('Underkänd – använd inte', page)
        self.assertIn('Skapa svensk AI-undertext', page)

    def test_completed_ai_subtitle_can_also_be_deleted(self):
        item = {
            'path': '/tv/My Show/e01.mkv', 'kind': 'episode', 'series': 'My Show',
            'season': 'Säsong 1', 'season_number': 1, 'episode_number': 1,
            'episode_code': 'S01E01', 'ai_sv_exists': True,
            'ai_sv_path': '/tv/My Show/e01.ai.sv.srt', 'name': 'e01.mkv',
            'size_bytes': 1024, 'embedded_sv_exists': False, 'probe': {},
        }
        page = render_library([item], 'episode', token='safe', jobs={item['path']: {
            'id': 8, 'status': 'completed', 'message': 'Klar',
        }})
        self.assertIn('Ta bort AI-undertext', page)
        self.assertIn("action='/jobs/delete'", page)

    def test_embedded_swedish_pgs_is_not_marked_missing_or_runnable(self):
        item = {'kind': 'episode', 'size_bytes': 1024,
                'probe': {'status': 'ok', 'audio': [], 'subtitle': [{
                    'index': 2, 'codec': 'hdmv_pgs_subtitle', 'language': 'swe',
                    'title': 'Swedish', 'default': False, 'forced': False,
                    'hearing_impaired': False}]},
                'series': 'My Show', 'season': 'Säsong 1', 'season_number': 1,
                'episode_number': 2, 'episode_code': 'S01E02', 'ai_sv_exists': False,
                'embedded_sv_exists': True, 'name': 'e02.mkv',
                'path': '/tv/My Show/e02.mkv'}
        page = render_library([item], 'episode', 'safe-token')
        self.assertIn('Svensk originalundertext finns', page)
        self.assertIn('PGS', page)
        self.assertIn('alla har svensk undertext', page)
        self.assertNotIn('Skapa svensk AI-undertext', page)
        self.assertNotIn("class='subtitle-missing'", page)


if __name__ == '__main__':
    unittest.main()
