import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from unittest.mock import patch
from server import (
    ACCENTS, THEMES, accent_css_block, active_job_count, auto_start_missing_jobs,
    library_counts, library_subtitle_breakdown, render_language_content, render_log_content,
    render_lokal_motor, render_media_row, render_overview_job_card, render_page,
    render_pipeline_steps, render_queue_content, render_settings_content, render_sidebar,
    render_stat_cards, render_subtitle_coverage, render_theme_content, subtitle_is_stale,
)
from hardware import DEFAULT as DEFAULT_HARDWARE


class SubtitleStaleTests(unittest.TestCase):
    def test_matching_fingerprint_is_not_stale(self):
        item = {'size_bytes': 1000, 'mtime_ns': 500}
        job = {'status': 'completed', 'media_size_bytes': 1000, 'media_mtime_ns': 500}
        self.assertFalse(subtitle_is_stale(item, job))

    def test_changed_size_is_stale(self):
        item = {'size_bytes': 2000, 'mtime_ns': 500}
        job = {'status': 'completed', 'media_size_bytes': 1000, 'media_mtime_ns': 500}
        self.assertTrue(subtitle_is_stale(item, job))

    def test_missing_fingerprint_on_old_jobs_is_never_stale(self):
        item = {'size_bytes': 2000, 'mtime_ns': 999}
        job = {'status': 'completed'}
        self.assertFalse(subtitle_is_stale(item, job))

    def test_non_completed_job_is_never_stale(self):
        item = {'size_bytes': 2000, 'mtime_ns': 999}
        job = {'status': 'failed', 'media_size_bytes': 1000, 'media_mtime_ns': 500}
        self.assertFalse(subtitle_is_stale(item, job))

    def test_restart_button_shown_only_when_stale(self):
        item = {'path': '/movies/f.mkv', 'name': 'f.mkv', 'kind': 'movie',
                'size_bytes': 2000, 'mtime_ns': 999, 'ai_sv_exists': True,
                'embedded_sv_exists': False, 'probe': {}}
        stale_job = {'status': 'completed', 'media_size_bytes': 1000, 'media_mtime_ns': 500}
        fresh_job = {'status': 'completed', 'media_size_bytes': 2000, 'media_mtime_ns': 999}
        stale_page = render_media_row(item, token='safe', job=stale_job)
        fresh_page = render_media_row(item, token='safe', job=fresh_job)
        self.assertIn('Kör igen', stale_page)
        self.assertIn("action='/jobs/restart'", stale_page)
        self.assertNotIn('Kör igen', fresh_page)


class LibraryCountsTests(unittest.TestCase):
    def test_counts_per_kind_and_subtitle_coverage(self):
        items = [
            {'kind': 'movie', 'ai_sv_exists': False, 'embedded_sv_exists': False},
            {'kind': 'episode', 'ai_sv_exists': True, 'embedded_sv_exists': False},
            {'kind': 'episode', 'ai_sv_exists': False, 'embedded_sv_exists': False},
            {'kind': 'media', 'ai_sv_exists': False, 'embedded_sv_exists': True},
        ]
        counts = library_counts(items)
        self.assertEqual(counts['movie'], (1, 0))
        self.assertEqual(counts['episode'], (2, 1))
        self.assertEqual(counts['media'], (1, 1))

    def test_empty_library_is_all_zero(self):
        counts = library_counts([])
        self.assertEqual(counts['movie'], (0, 0))
        self.assertEqual(counts['episode'], (0, 0))
        self.assertEqual(counts['media'], (0, 0))


class SubtitleCoverageTests(unittest.TestCase):
    def _items(self):
        ok = lambda *tracks: {'status': 'ok', 'subtitle': list(tracks)}
        pgs = lambda language: {'codec': 'hdmv_pgs_subtitle', 'language': language, 'title': ''}
        text = lambda language: {'codec': 'subrip', 'language': language, 'title': ''}
        return [
            {'kind': 'movie', 'probe': ok(pgs('swe'), text('eng'))},
            {'kind': 'movie', 'probe': ok()},
            {'kind': 'episode', 'probe': ok(text('swe')), 'ai_en_exists': True},
            {'kind': 'episode', 'probe': {'status': 'pending'}},
            {'kind': 'media', 'probe': ok(), 'ai_sv_exists': True},
        ]

    def test_breakdown_counts_each_language_and_picture_subtitles_per_kind(self):
        breakdown = library_subtitle_breakdown(self._items())
        self.assertEqual(breakdown['movie'], {'total': 2, 'sv': 1, 'en': 1, 'image': 1,
                                              'no_sv': 1, 'unprobed': 0})
        self.assertEqual(breakdown['episode'], {'total': 2, 'sv': 1, 'en': 1, 'image': 0,
                                                'no_sv': 1, 'unprobed': 1})
        self.assertEqual(breakdown['media'], {'total': 1, 'sv': 1, 'en': 0, 'image': 0,
                                              'no_sv': 0, 'unprobed': 0})

    def test_card_shows_the_numbers_and_warns_about_unanalysed_files(self):
        page = render_subtitle_coverage(library_subtitle_breakdown(self._items()))
        self.assertIn('Undertexttäckning', page)
        self.assertIn('<td>Filmer</td><td>2</td><td>1</td><td>1</td><td>1</td><td>1</td>', page)
        self.assertIn('1 filer är inte analyserade', page)
        english = render_subtitle_coverage(library_subtitle_breakdown(self._items()), lang='en')
        self.assertIn('Subtitle coverage', english)

    def test_empty_library_shows_no_card(self):
        self.assertEqual(render_subtitle_coverage(library_subtitle_breakdown([])), '')


class ActiveJobCountTests(unittest.TestCase):
    def test_only_active_statuses_are_counted(self):
        jobs = {
            '/a': {'status': 'transcribing'},
            '/b': {'status': 'completed'},
            '/c': {'status': 'queued'},
            '/d': {'status': 'quality_failed'},
        }
        self.assertEqual(active_job_count(jobs), 2)

    def test_no_jobs_is_zero(self):
        self.assertEqual(active_job_count({}), 0)


class AccentCssBlockTests(unittest.TestCase):
    def test_theme_own_accent_produces_no_override(self):
        self.assertEqual(accent_css_block(''), '')

    def test_unknown_accent_falls_back_to_no_override(self):
        self.assertEqual(accent_css_block('does-not-exist'), '')

    def test_known_accent_overrides_with_matching_specificity(self):
        css = accent_css_block('turkos')
        self.assertIn(':root[data-theme]', css)
        self.assertIn('#2dd4bf', css)


class StatCardsTests(unittest.TestCase):
    def test_zero_queue_shows_honest_empty_state(self):
        page = render_stat_cards({'movie': (1, 0), 'episode': (15, 0), 'media': (14, 3)}, 0)
        self.assertIn('Inga jobb köade just nu', page)
        self.assertIn('0 av 1 har svensk AI-undertext', page)
        self.assertIn('3 av 14 har svensk AI-undertext', page)

    def test_nonzero_queue_is_stated(self):
        page = render_stat_cards({'movie': (0, 0), 'episode': (0, 0), 'media': (0, 0)}, 2)
        self.assertIn('2 jobb köade just nu', page)

    def test_translates_to_english(self):
        page = render_stat_cards({'movie': (1, 0), 'episode': (0, 0), 'media': (0, 0)}, 2, lang='en')
        self.assertIn('2 jobs queued right now', page)
        self.assertIn('0 of 1 have a Swedish AI subtitle', page)
        self.assertIn('MOVIES', page)


class OverviewJobCardTests(unittest.TestCase):
    def test_no_active_job_is_honest_about_idle_state(self):
        page = render_overview_job_card({}, {})
        self.assertIn('Inga aktiva jobb', page)
        self.assertNotIn('%', page)

    def test_active_job_shows_real_progress_and_name(self):
        jobs = {'/tv/show.mkv': {'media_path': '/tv/show.mkv', 'status': 'transcribing',
                                  'progress': 42, 'message': 'Lyssnar och skriver av'}}
        library_by_path = {'/tv/show.mkv': {'name': 'show.mkv'}}
        page = render_overview_job_card(jobs, library_by_path)
        self.assertIn('show.mkv', page)
        self.assertIn('42', page)
        self.assertIn('Lyssnar och skriver av', page)
        self.assertIn("step-dot active", page)

    def test_completed_job_is_not_treated_as_active(self):
        jobs = {'/tv/show.mkv': {'media_path': '/tv/show.mkv', 'status': 'completed',
                                  'progress': 100, 'message': 'Klar'}}
        page = render_overview_job_card(jobs, {})
        self.assertIn('Inga aktiva jobb', page)

    def test_translates_to_english(self):
        jobs = {'/tv/show.mkv': {'media_path': '/tv/show.mkv', 'status': 'transcribing',
                                  'progress': 42, 'message': 'Listening'}}
        library_by_path = {'/tv/show.mkv': {'name': 'show.mkv'}}
        page = render_overview_job_card(jobs, library_by_path, lang='en')
        self.assertIn('Ongoing job', page)
        self.assertIn('Listening and transcribing', page)
        idle_page = render_overview_job_card({}, {}, lang='en')
        self.assertIn('No active jobs', idle_page)


class PipelineStepsTests(unittest.TestCase):
    def test_early_step_is_active_and_later_ones_are_waiting(self):
        page = render_pipeline_steps('transcribing')
        self.assertIn("step-dot active", page)
        self.assertEqual(page.count("step-dot waiting"), 3)
        self.assertNotIn("step-dot done", page)

    def test_later_step_marks_earlier_ones_done(self):
        page = render_pipeline_steps('verifying')
        self.assertEqual(page.count("step-dot done"), 2)
        self.assertIn("step-dot active", page)
        self.assertEqual(page.count("step-dot waiting"), 1)

    def test_non_pipeline_status_returns_none(self):
        self.assertIsNone(render_pipeline_steps('queued'))
        self.assertIsNone(render_pipeline_steps('quality_failed'))
        self.assertIsNone(render_pipeline_steps('completed'))

    def test_translates_to_english(self):
        page = render_pipeline_steps('transcribing', lang='en')
        self.assertIn('Listening and transcribing', page)
        self.assertIn('Waiting', page)


class LokalMotorTests(unittest.TestCase):
    def test_falls_back_to_honest_placeholder_without_live_numbers(self):
        page = render_lokal_motor(DEFAULT_HARDWARE)
        self.assertIn('Kommer senare', page)

    def test_shows_real_cpu_and_ram_when_reported(self):
        hardware = dict(DEFAULT_HARDWARE)
        hardware['resources'] = {'cpu_percent': 37.0, 'ram_used_gb': 12.3, 'ram_total_gb': 32.0}
        page = render_lokal_motor(hardware)
        self.assertIn('37', page)
        self.assertIn('12.3', page)
        self.assertIn('32.0', page)
        self.assertNotIn('Kommer senare', page)

    def test_carries_live_update_hooks_for_the_polling_script(self):
        page = render_lokal_motor(DEFAULT_HARDWARE)
        self.assertIn('data-engine-card', page)
        self.assertIn('data-cpu-fill', page)
        self.assertIn('data-cpu-status', page)

    def test_translates_to_english(self):
        page = render_lokal_motor(DEFAULT_HARDWARE, lang='en')
        self.assertIn('Coming later', page)
        self.assertIn('Local engine', page)
        self.assertIn('No GPU block detected', page)
        self.assertIn('data-ram-fill', page)
        self.assertIn('data-ram-status', page)


class SidebarTests(unittest.TestCase):
    def test_active_page_is_marked_and_others_are_not(self):
        page = render_sidebar('overview', 0, DEFAULT_HARDWARE)
        self.assertIn("href='/' class='active'", page)
        self.assertNotIn("href='/settings' class='active'", page)

    def test_queue_count_is_visible_in_nav(self):
        page = render_sidebar('overview', 3, DEFAULT_HARDWARE)
        self.assertIn("<span class='count'>3</span>", page)

    def test_nav_labels_translate_to_english_when_requested(self):
        swedish = render_sidebar('overview', 0, DEFAULT_HARDWARE, lang='sv')
        english = render_sidebar('overview', 0, DEFAULT_HARDWARE, lang='en')
        self.assertIn('>Översikt<', swedish)
        self.assertIn('>Kö<', swedish)
        self.assertIn('>Overview<', english)
        self.assertIn('>Queue<', english)
        self.assertNotIn('>Översikt<', english)

    def test_unknown_language_falls_back_to_swedish(self):
        page = render_sidebar('overview', 0, DEFAULT_HARDWARE, lang='fr')
        self.assertIn('>Översikt<', page)

    def test_lokal_motor_card_is_present_without_fabricated_ram(self):
        page = render_sidebar('overview', 0, DEFAULT_HARDWARE)
        self.assertIn('Lokal motor', page)
        self.assertIn('Kommer senare', page)

    def test_settings_subpages_are_grouped_under_a_disclosure(self):
        page = render_sidebar('settings:theme', 0, DEFAULT_HARDWARE)
        self.assertIn("href='/settings' class=''>Allmänt", page)
        self.assertIn("href='/settings/theme' class='active'>Tema", page)
        self.assertIn("href='/settings/cloud' class=''>Moln-GPU", page)
        self.assertIn("<summary>Inställningar</summary>", page)

    def test_settings_disclosure_is_open_when_on_a_settings_page(self):
        on_settings = render_sidebar('settings:general', 0, DEFAULT_HARDWARE)
        elsewhere = render_sidebar('overview', 0, DEFAULT_HARDWARE)
        self.assertIn("<details class='nav-group' open>", on_settings)
        self.assertIn("<details class='nav-group'>", elsewhere)


class LanguageContentTests(unittest.TestCase):
    def test_both_language_chips_are_offered_and_current_one_is_selected(self):
        page = render_language_content('en', 'token')
        self.assertIn('Svenska', page)
        self.assertIn('English', page)
        self.assertIn("value='en' checked", page)
        self.assertNotIn("value='sv' checked", page)

    def test_saved_notice_only_shown_when_saved(self):
        unsaved = render_language_content('sv', 'token')
        saved = render_language_content('sv', 'token', saved=True)
        self.assertNotIn('sparat', unsaved)
        self.assertIn('sparat', saved)

    def test_settings_general_page_embeds_the_language_switcher(self):
        page = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False,
                                        lang='en')
        self.assertIn('Interface language', page)


class SettingsContentTests(unittest.TestCase):
    def test_hardware_saved_notice_only_shown_when_saved(self):
        unsaved = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False)
        saved = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', True)
        self.assertNotIn('Hårdvaruvalet är sparat', unsaved)
        self.assertIn('Hårdvaruvalet är sparat', saved)

    def test_instance_id_is_shown_for_support_purposes(self):
        page = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False,
                                        instance_id='abc-123')
        self.assertIn('abc-123', page)

    def test_theme_cards_are_not_on_the_general_page_anymore(self):
        page = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False)
        self.assertNotIn('theme-card', page)

    def test_ignore_existing_subtitle_checkbox_reflects_saved_state(self):
        unchecked = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False)
        checked = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False,
                                           ignore_existing_subtitle=True)
        self.assertIn("name='ignore_existing_subtitle' value='1'", unchecked)
        self.assertNotIn("name='ignore_existing_subtitle' value='1' checked", unchecked)
        self.assertIn("name='ignore_existing_subtitle' value='1' checked", checked)

    def test_automation_saved_notice_only_shown_when_saved(self):
        unsaved = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False)
        saved = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False,
                                         automation_saved=True)
        self.assertNotIn('Automatiseringen är sparad', unsaved)
        self.assertIn('Automatiseringen är sparad', saved)

    def test_english_subtitle_checkbox_reflects_saved_state(self):
        off = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False)
        on = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False,
                                      include_english_subtitle=True)
        self.assertNotIn("name='include_english_subtitle' value='1' checked", off)
        self.assertIn("name='include_english_subtitle' value='1' checked", on)

    def test_auto_scan_and_auto_start_checkboxes_reflect_saved_state(self):
        off = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False)
        on = render_settings_content('mork', '', DEFAULT_HARDWARE, 'auto', 'token', False,
                                      auto_scan_enabled=True, auto_start_jobs=True)
        self.assertNotIn("name='auto_scan_enabled' value='1' checked", off)
        self.assertNotIn("name='auto_start_jobs' value='1' checked", off)
        self.assertIn("name='auto_scan_enabled' value='1' checked", on)
        self.assertIn("name='auto_start_jobs' value='1' checked", on)


class AutoStartMissingJobsTests(unittest.TestCase):
    def test_only_never_attempted_files_needing_a_subtitle_are_queued(self):
        items = [
            {'path': '/movies/new.mkv', 'name': 'new.mkv', 'kind': 'movie',
             'ai_sv_exists': False, 'embedded_sv_exists': False},
            {'path': '/movies/covered.mkv', 'name': 'covered.mkv', 'kind': 'movie',
             'ai_sv_exists': True, 'embedded_sv_exists': False},
            {'path': '/movies/already-tried.mkv', 'name': 'already-tried.mkv', 'kind': 'movie',
             'ai_sv_exists': False, 'embedded_sv_exists': False},
            {'path': '/movies/embedded.mkv', 'name': 'embedded.mkv', 'kind': 'movie',
             'ai_sv_exists': False, 'embedded_sv_exists': True},
        ]
        with patch('server.latest_jobs', return_value={
                '/movies/already-tried.mkv': {'status': 'failed'}}), \
                patch('server.enqueue_job') as enqueue:
            auto_start_missing_jobs(items)
        enqueue.assert_called_once_with(unittest.mock.ANY, '/movies/new.mkv', 'new.mkv')

    def test_ignore_existing_lets_embedded_coverage_through(self):
        items = [{'path': '/movies/embedded.mkv', 'name': 'embedded.mkv', 'kind': 'movie',
                  'ai_sv_exists': False, 'embedded_sv_exists': True}]
        with patch('server.latest_jobs', return_value={}), \
                patch('server.enqueue_job') as enqueue:
            auto_start_missing_jobs(items, ignore_existing=True)
        enqueue.assert_called_once_with(unittest.mock.ANY, '/movies/embedded.mkv', 'embedded.mkv')


class ThemeContentTests(unittest.TestCase):
    def test_every_theme_is_offered_and_current_one_is_selected(self):
        page = render_theme_content('midnatt', '', 'token', False)
        for key in THEMES:
            self.assertIn(f"value='{key}'", page)
        self.assertIn("theme-card selected", page)

    def test_y2k_cyber_themes_are_present_with_labels(self):
        page = render_theme_content('mork', '', 'token', False)
        self.assertIn('Y2K Cyber', page)
        self.assertIn('Vaporwave', page)
        self.assertIn('Synthwave', page)
        self.assertIn('Terminal', page)

    def test_saved_notice_only_shown_when_saved(self):
        unsaved = render_theme_content('mork', '', 'token', False)
        saved = render_theme_content('mork', '', 'token', True)
        self.assertNotIn('Utseendet är sparat', unsaved)
        self.assertIn('Utseendet är sparat', saved)

    def test_immersive_themes_get_gradient_swatch_and_effects_badge(self):
        page = render_theme_content('y2k', '', 'token', False)
        self.assertIn("theme-card immersive selected", page)
        self.assertIn("theme-swatch grad", page)
        self.assertIn("effects-badge", page)
        self.assertIn("⚡ Effekter", page)

    def test_flat_themes_have_no_effects_badge(self):
        page = render_theme_content('mork', '', 'token', False)
        card_start = page.index("<label class='theme-card selected'>")
        card_end = page.index("</label>", card_start)
        self.assertNotIn("effects-badge", page[card_start:card_end])

    def test_immersive_themes_are_on_their_own_row(self):
        page = render_theme_content('mork', '', 'token', False)
        flat_grid_end = page.index("</div>", page.index("class='theme-grid'>"))
        immersive_heading = page.index("immersive-heading")
        immersive_grid = page.index("theme-grid immersive-grid")
        self.assertLess(flat_grid_end, immersive_heading)
        self.assertLess(immersive_heading, immersive_grid)
        self.assertNotIn('Y2K Cyber', page[:flat_grid_end])


class RenderPageTests(unittest.TestCase):
    def test_shell_placeholders_are_all_replaced(self):
        page = render_page('Översikt', 'overview', '<p>innehåll</p>', 'mork', '', {},
                            DEFAULT_HARDWARE, 'token')
        self.assertNotIn('{{', page)
        self.assertIn('data-theme="mork"', page)
        self.assertIn('<p>innehåll</p>', page)

    def test_unknown_accent_key_is_rejected_by_caller_contract(self):
        self.assertIn('', ACCENTS)


class LogContentTests(unittest.TestCase):
    def test_translates_to_english(self):
        page = render_log_content([], 1, 1, lang='en')
        self.assertIn('No jobs have run yet.', page)
        self.assertIn('Waiting for events', page)
        self.assertIn('>Log<', page)


class QueueContentTests(unittest.TestCase):
    def test_processing_job_shows_progress_bar_and_cancel_button(self):
        jobs = {'/extra/king.mp4': {'id': 78, 'media_path': '/extra/king.mp4',
                                     'media_name': 'king.mp4', 'status': 'translating',
                                     'progress': 61, 'message': 'Översätter till svenska',
                                     'updated_at': '2026-09-23T14:00:00Z'}}
        page = render_queue_content([], jobs, 'token')
        self.assertIn('king.mp4', page)
        self.assertIn('progress-fill', page)
        self.assertIn("action='/jobs/cancel'", page)
        self.assertIn("name='job_id' value='78'", page)
        self.assertIn('Aktiva jobb · 1', page)

    def test_queued_job_appears_under_ko_not_aktiva_jobb(self):
        jobs = {'/extra/lou.mp4': {'id': 90, 'media_path': '/extra/lou.mp4',
                                    'media_name': 'lou.mp4', 'status': 'queued',
                                    'progress': 0, 'message': 'Väntar på AI-worker',
                                    'updated_at': '2026-09-24T08:00:00Z'}}
        page = render_queue_content([], jobs, 'token')
        self.assertIn('Aktiva jobb · 0', page)
        self.assertIn('Kö · 1', page)
        self.assertIn('lou.mp4', page)

    def test_finished_job_has_no_progress_bar_or_cancel_button(self):
        jobs = {'/extra/hope.mp4': {'id': 80, 'media_path': '/extra/hope.mp4',
                                     'media_name': 'hope.mp4', 'status': 'quality_failed',
                                     'progress': 0, 'message': 'Flera rader slutar mitt i en mening.',
                                     'updated_at': '2026-09-23T14:00:00Z'}}
        page = render_queue_content([], jobs, 'token')
        self.assertIn('hope.mp4', page)
        self.assertNotIn('progress-fill', page)
        self.assertNotIn("action='/jobs/cancel'", page)
        self.assertIn("class='pill dg'", page)

    def test_empty_states_are_honest(self):
        page = render_queue_content([], {}, 'token')
        self.assertIn('Inga aktiva jobb just nu.', page)
        self.assertIn('Inga köade jobb just nu.', page)
        self.assertIn('Ingen historik ännu.', page)

    def test_translates_to_english(self):
        page = render_queue_content([], {}, 'token', lang='en')
        self.assertIn('No active jobs right now.', page)
        self.assertIn('No queued jobs right now.', page)
        self.assertIn('No history yet.', page)
        self.assertIn('Active jobs', page)
        self.assertIn('Recent events', page)

    def test_history_is_paginated_at_seven_per_page(self):
        jobs = {f'/extra/clip-{i}.mp4': {'id': i, 'media_path': f'/extra/clip-{i}.mp4',
                                          'media_name': f'clip-{i}.mp4', 'status': 'completed',
                                          'progress': 100, 'message': 'Klar',
                                          'updated_at': f'2026-09-24T{i:02d}:00:00Z'}
                for i in range(1, 13)}
        page_one = render_queue_content([], jobs, 'token', history_page=1)
        self.assertIn('Sida 1 av 2', page_one)
        self.assertEqual(page_one.count("class='queue-row"), 7)
        page_two = render_queue_content([], jobs, 'token', history_page=2)
        self.assertIn('Sida 2 av 2', page_two)
        self.assertEqual(page_two.count("class='queue-row"), 5)

    def test_queue_is_paginated_separately_from_history(self):
        jobs = {f'/extra/q{i}.mp4': {'id': 100 + i, 'media_path': f'/extra/q{i}.mp4',
                                      'media_name': f'q{i}.mp4', 'status': 'queued',
                                      'progress': 0, 'message': '',
                                      'updated_at': f'2026-09-24T{i:02d}:00:00Z'}
                for i in range(1, 13)}
        page = render_queue_content([], jobs, 'token', queue_page=1)
        self.assertIn('Kö · 12', page)
        self.assertIn('Sida 1 av 2', page)


if __name__ == '__main__':
    unittest.main()
