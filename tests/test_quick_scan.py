import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, '/app')
import scan_state
import server
from library import scan_library


class QuickScanTests(unittest.TestCase):
    """The user (2026-11-04): the automatic scan every five minutes opened every video file again, so the disks could never spin down.
    A quick scan reads only folders that changed and inspects only new or changed files."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tv = Path(folder.name) / 'tv'
        for season in ('Season 1', 'Season 2'):
            (self.tv / 'Z Nation' / season).mkdir(parents=True)
            for episode in (1, 2):
                (self.tv / 'Z Nation' / season / f'Z.Nation.S0{season[-1]}E0{episode}.mkv').write_bytes(b'video')
        for directory in (self.tv / 'Z Nation' / 'Season 1', self.tv / 'Z Nation' / 'Season 2', self.tv / 'Z Nation', self.tv):
            os.utime(directory, (1_700_000_000, 1_700_000_000))   # created long ago, as a real library is
        self.probed = []

    def probe(self, media):
        self.probed.append(media.name)
        return {'status': 'ok', 'subtitle': []}

    def scan(self, previous=None, directories=None):
        state = {}
        items = scan_library([self.tv], probe=self.probe, previous=previous, directories=directories, state=state)
        return items, state

    def test_nothing_changed_reads_no_folder_and_opens_no_file(self):
        first, state = self.scan()
        self.probed.clear()
        again, quick = self.scan(previous=first, directories=state['directories'])
        self.assertEqual(again, first)
        self.assertEqual((self.probed, quick['listed'], quick['probed']), ([], 0, 0))
        self.assertEqual(quick['skipped'], len(state['directories']))

    def test_a_new_episode_reads_only_its_folder_and_inspects_only_that_file(self):
        first, state = self.scan()
        self.probed.clear()
        (self.tv / 'Z Nation' / 'Season 2' / 'Z.Nation.S02E03.mkv').write_bytes(b'new')
        again, quick = self.scan(previous=first, directories=state['directories'])
        self.assertEqual(self.probed, ['Z.Nation.S02E03.mkv'])
        self.assertEqual(quick['listed'], 1)
        self.assertEqual([item['name'] for item in again][-1], 'Z.Nation.S02E03.mkv')
        self.assertEqual(len(again), 5)

    def test_a_new_season_folder_is_found_through_the_series_folder(self):
        first, state = self.scan()
        (self.tv / 'Z Nation' / 'Season 3').mkdir()
        (self.tv / 'Z Nation' / 'Season 3' / 'Z.Nation.S03E01.mkv').write_bytes(b'new')
        self.probed.clear()
        again, quick = self.scan(previous=first, directories=state['directories'])
        self.assertEqual(self.probed, ['Z.Nation.S03E01.mkv'])
        self.assertEqual(quick['listed'], 2)                     # the series folder and the new season

    def test_a_written_subtitle_is_seen_without_opening_the_video(self):
        first, state = self.scan()
        (self.tv / 'Z Nation' / 'Season 1' / 'Z.Nation.S01E01.ai.sv.srt').write_text('1\n', encoding='utf-8')
        self.probed.clear()
        again, _ = self.scan(previous=first, directories=state['directories'])
        self.assertEqual(self.probed, [])
        self.assertTrue(next(i for i in again if i['name'] == 'Z.Nation.S01E01.mkv')['ai_sv_exists'])

    def test_a_replaced_or_removed_file_is_noticed(self):
        first, state = self.scan()
        target = self.tv / 'Z Nation' / 'Season 1' / 'Z.Nation.S01E02.mkv'
        target.unlink()
        target.write_bytes(b'a better copy')                    # removed and written again, as Sonarr does on an upgrade
        (self.tv / 'Z Nation' / 'Season 2' / 'Z.Nation.S02E01.mkv').unlink()
        self.probed.clear()
        again, _ = self.scan(previous=first, directories=state['directories'])
        self.assertEqual(self.probed, ['Z.Nation.S01E02.mkv'])
        self.assertNotIn('Z.Nation.S02E01.mkv', [item['name'] for item in again])

    def test_the_scan_buttons_list_every_folder_but_open_no_unchanged_file(self):
        first, _ = self.scan()
        self.probed.clear()
        again, full = self.scan(previous=first)                  # no folder record: the buttons
        self.assertEqual(again, first)
        self.assertEqual((self.probed, full['skipped']), ([], 0))
        self.assertEqual(full['reused'], 4)

    def test_a_folder_changed_just_now_is_read_again_next_time(self):
        (self.tv / 'Z Nation' / 'Season 1' / 'Z.Nation.S01E03.mkv').write_bytes(b'new')    # changed this very moment
        first, state = self.scan()
        self.assertEqual(state['directories'][str(self.tv / 'Z Nation' / 'Season 1')]['mtime_ns'], -1)
        _, quick = self.scan(previous=first, directories=state['directories'])
        self.assertEqual(quick['listed'], 1)

    def test_a_failed_inspection_is_tried_again(self):
        first, state = self.scan()
        first[0]['probe'] = {'status': 'error'}
        self.probed.clear()
        os.utime(self.tv / 'Z Nation' / 'Season 1', ns=(1, 1))   # make the folder count as changed
        self.scan(previous=first, directories=state['directories'])
        self.assertIn(first[0]['name'], self.probed)


class WebhookAddsToLibraryTests(unittest.TestCase):
    """The user (2026-11-04): a file Sonarr/Radarr/Emby reports must show in SubSynq at once, also when no job may start by itself."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        roots = {'movie': str(base / 'movies'), 'episode': str(base / 'tv'), 'media': str(base / 'extra')}
        for root in roots.values():
            Path(root).mkdir()
        self.episode = base / 'tv' / 'Z Nation' / 'Season 4' / 'Z.Nation.S04E05.mkv'
        self.episode.parent.mkdir(parents=True)
        self.episode.write_bytes(b'video')
        for patcher in (mock.patch.object(server, 'CONFIG', base), mock.patch.object(server, 'DATABASE', base / 'subsynq.db'),
                        mock.patch.dict(scan_state.LIBRARY_ROOTS, roots), mock.patch.object(scan_state, '_ROOT_ORDER', list(roots.values())),
                        mock.patch.object(server, 'probe_media', return_value={'status': 'ok', 'subtitle': []})):
            patcher.start()
            self.addCleanup(patcher.stop)
        server.initialize()
        movie = {'path': str(base / 'movies' / 'A.mkv'), 'name': 'A.mkv', 'kind': 'movie'}
        with sqlite3.connect(server.DATABASE) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_scan', ?)", (json.dumps({'items': [movie]}),))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_directories', ?)",
                       (json.dumps({str(self.episode.parent): {'mtime_ns': 1, 'files': [], 'subdirs': []}}),))

    def stored(self, key):
        with sqlite3.connect(server.DATABASE) as db:
            return json.loads(db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])

    def test_the_reported_episode_is_added_after_the_movies_with_its_episode_code(self):
        item = server.add_media_to_library(self.episode)
        self.assertEqual((item['kind'], item['episode_code']), ('episode', 'S04E05'))
        self.assertEqual([i['name'] for i in self.stored('library_scan')['items']], ['A.mkv', 'Z.Nation.S04E05.mkv'])
        self.assertNotIn(str(self.episode.parent), self.stored('library_directories'))   # the next quick scan reads the folder again
        server.add_media_to_library(self.episode)
        self.assertEqual(len(self.stored('library_scan')['items']), 2)                    # reported twice, stored once

    def test_a_file_outside_the_libraries_is_not_added(self):
        outside = Path(tempfile.mkdtemp()) / 'x.mkv'
        outside.write_bytes(b'x')
        self.assertIsNone(server.add_media_to_library(outside))


if __name__ == '__main__':
    unittest.main()
