import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, '/app')
import server
import subtitle_cleanup

INDEX = Path(server.__file__).with_name('index.html').read_text(encoding='utf-8')


def phone_css():
    """The rules inside the `@media (max-width: 700px)` phone block."""
    start = INDEX.index('@media (max-width: 700px) {')
    return INDEX[start:INDEX.index('\n}\n', start)]


class WideLayoutTests(unittest.TestCase):
    """The tablet and desktop widths (above the 700 px phone block). A drop-down sized by its longest option once made the film
    table 2000 px wide and squeezed every name to a few letters per line (reproduced on a real library of 621 films)."""

    def test_a_file_list_never_lets_its_content_size_the_columns(self):
        self.assertIn('table.files { table-layout: fixed; }', INDEX)
        self.assertIn('table.files th:first-child, table.files td.filename { width: 60%; }', INDEX)
        self.assertIn('table.files td { overflow-wrap: anywhere; }', INDEX)

    def test_the_audio_drop_down_is_as_wide_as_its_cell_not_as_its_longest_track_name(self):
        self.assertIn('.audio-pick select { display: block; width: 100%; max-width: 100%; min-width: 0;', INDEX)
        self.assertIn('text-overflow: ellipsis;', INDEX[INDEX.index('.audio-pick select'):INDEX.index('\n', INDEX.index('.audio-pick select'))])

    def test_the_status_pill_may_wrap_up_to_a_desktop_width(self):
        self.assertIn('@media (max-width: 1100px) { .subtitle-status { white-space: normal; } }', INDEX)

    def test_a_long_track_name_is_cut_in_the_drop_down(self):
        long_title = 'Director commentary with the cast and crew about every scene of the whole film in surround ' * 3
        tracks = [{'index': 1, 'language': 'eng', 'codec': 'dts', 'channels': 6, 'title': long_title},
                  {'index': 2, 'language': 'swe', 'codec': 'ac3', 'channels': 2, 'title': 'Svenska'}]
        item = {'probe': {'status': 'ok', 'audio': tracks}}
        html = server.render_audio_track_choice(item)
        options = re.findall(r"<option value='(\d+)'>(.*?)</option>", html)
        self.assertEqual([value for value, _ in options], ['1', '2'])
        self.assertTrue(options[0][1].endswith('…'))
        self.assertLessEqual(len(options[0][1]), 90, 'the whole label stays bounded however long the track name is')
        self.assertIn('Svenska', options[1][1], 'a short title is kept whole')


class PhoneLayoutTests(unittest.TestCase):
    def test_file_lists_are_stackable_so_the_name_keeps_the_whole_line(self):
        item = {'path': '/extra/a.mp4', 'name': 'a.mp4', 'kind': 'media', 'size_bytes': 1024,
                'ai_sv_exists': False, 'embedded_sv_exists': False, 'probe': {'status': 'pending'}}
        html = server.render_library([item], 'media', 'tok')
        self.assertIn("<table class='stack files'>", html)
        self.assertIn("<td class='filename'>", html)
        css = phone_css()
        self.assertIn('table.stack tr { display: flex; flex-wrap: wrap;', css)
        self.assertIn('table.stack td.filename { flex: 1 1 100%;', css)
        self.assertIn('table.stack td.filename + td { flex: 1 1 100%; }', css)

    def test_episode_tables_in_a_season_are_stackable_too(self):
        source = Path(server.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count("<table class='stack files'>"), 2, 'movie/extra list and season table')

    def test_cleanup_rows_are_stackable_and_the_name_cell_grows(self):
        source = Path(subtitle_cleanup.__file__).read_text(encoding='utf-8')
        self.assertIn("<table class='stack'>", source)
        self.assertIn("<td class='grow'>", source)
        self.assertIn('table.stack td.grow { flex: 1 1 calc(100% - 40px);', phone_css())

    def test_job_titles_take_their_own_line_on_a_phone(self):
        self.assertIn('.queue-row .title { flex: 1 1 calc(100% - 40px);', phone_css())

    def test_wide_tables_scroll_inside_their_card_and_drop_downs_fit_the_screen(self):
        css = phone_css()
        self.assertIn('.content table { display: block; max-width: 100%; overflow-x: auto;', css)
        self.assertIn('select { min-width: 0; width: 100%; }', css)

    def test_the_settings_list_has_its_own_row_so_opening_it_cannot_move_the_library_links(self):
        html = server.render_sidebar('overview', 0, {'available': [], 'active': None}, 'sv')
        library = html[html.index("<div class='nav-library'>"):html.index("<details class='nav-group'")]
        for link in ('/library?kind=movie', '/library?kind=episode', '/library?kind=media'):
            self.assertIn(link, library)
        self.assertIn("class='nav-heading'", library)
        self.assertNotIn('nav-group', library)
        tablet = INDEX[INDEX.index('@media (max-width: 900px) {\n  .nav { align-items: center; }'):]
        tablet = tablet[:tablet.index('\n}\n')]
        self.assertIn('.nav-group { flex: 1 1 100%; }', tablet)
        self.assertIn('.nav-library { display: flex; flex: 1 1 100%;', tablet)
        self.assertIn('.nav-library { display: contents; }', INDEX.replace(tablet, ''), 'desktop keeps one column')

    def test_the_phone_rules_do_not_leak_to_desktop(self):
        outside = INDEX.replace(phone_css(), '')
        self.assertNotIn('table.stack', outside)
        self.assertRegex(INDEX, r'viewport" content="width=device-width, initial-scale=1"')
        self.assertIsNone(re.search(r'\.content table \{ display: block', outside))


if __name__ == '__main__':
    unittest.main()
