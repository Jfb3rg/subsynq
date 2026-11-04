import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = ['/app', '/ai']
import opensubtitles as os_
import subtitle_sync as sync


def srt(cues):
    return ''.join(f'{n}\n{sync._clock(s)} --> {sync._clock(e)}\nRad {n}\n\n' for n, (s, e) in enumerate(cues, 1))


class TimingTests(unittest.TestCase):
    """The user (2026-11-04): OpenSubtitles never found anything for a Radarr-named library; a subtitle is now accepted when its timing fits."""

    def setUp(self):
        # Speech in ten 90 s windows: a line every 4 s lasting 1.5-2.5 s, irregular like real dialogue.
        self.windows = sync.windows_for(6000, count=10)
        self.speech, self.cues = [], []
        for start, length in self.windows:
            t = start + 1
            n = 0
            while t < start + length - 4:
                duration = 1.5 + (n * 7 % 5) / 5
                self.speech.append((t, t + duration))
                self.cues.append((t - .05, t + duration + .1))
                t += duration + 1.2 + (n * 3 % 4)
                n += 1

    def test_the_right_subtitle_fits_and_a_constant_offset_is_measured(self):
        moved = [(s - 4.0, e - 4.0) for s, e in self.cues]
        result = sync.verdict(srt(moved), self.speech, self.windows)
        self.assertTrue(result['fits'])
        self.assertEqual((result['offset'], result['scale']), (4.0, 1.0))
        shifted = sync.shift(srt(moved), result['offset'], result['scale'])
        self.assertEqual(sync.cue_times(shifted)[0], (round(self.cues[0][0], 3), round(self.cues[0][1], 3)))

    def test_a_subtitle_of_another_film_does_not_fit(self):
        other = [(s * 1.37 + 211.3, e * 1.37 + 211.3) for s, e in self.cues]          # same kind of rhythm, other places
        self.assertFalse(sync.verdict(srt(other), self.speech, self.windows)['fits'])

    def test_too_few_lines_never_fit(self):
        self.assertFalse(sync.verdict(srt(self.cues[:6]), self.speech, self.windows)['fits'])

    def test_shift_keeps_the_text_and_numbering(self):
        text = '1\n00:00:10,000 --> 00:00:12,500\nHej\n'
        self.assertEqual(sync.shift(text, -1.5, 1.0), '1\n00:00:08,500 --> 00:00:11,000\nHej\n')
        self.assertEqual(sync.shift(text, 0.0, 1.0), text)


class NfoIdentityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)

    def test_a_film_is_identified_by_movie_nfo(self):
        film = self.base / 'Godzilla vs. Kong (2021)' / 'Godzilla vs. Kong (2021) Remux-2160p.mkv'
        film.parent.mkdir()
        film.write_bytes(b'x')
        (film.parent / 'movie.nfo').write_text('<movie><uniqueid type="tmdb" default="true">399566</uniqueid>'
                                               '<uniqueid type="imdb">tt5034838</uniqueid></movie>', encoding='utf-8')
        self.assertEqual(os_.nfo_identity(film), {'kind': 'movie', 'imdb': 5034838, 'tmdb': 399566})
        self.assertEqual(os_.identity_params(os_.nfo_identity(film)), {'imdb_id': 5034838})

    def test_an_episode_takes_season_and_episode_from_its_own_nfo_and_the_id_from_tvshow_nfo(self):
        episode = self.base / 'Z Nation' / 'Season 4' / 'Z.Nation.4x02.mkv'
        episode.parent.mkdir(parents=True)
        episode.write_bytes(b'x')
        (self.base / 'Z Nation' / 'tvshow.nfo').write_text('<tvshow><uniqueid type="imdb">tt3843168</uniqueid></tvshow>', encoding='utf-8')
        episode.with_suffix('.nfo').write_text('<episodedetails><season>4</season><episode>2</episode>'
                                               '<uniqueid type="tvdb">6304575</uniqueid></episodedetails>', encoding='utf-8')
        identity = os_.nfo_identity(episode)
        self.assertEqual(identity, {'kind': 'episode', 'imdb': 3843168, 'tmdb': None, 'season': 4, 'episode': 2})
        self.assertEqual(os_.identity_params(identity), {'parent_imdb_id': 3843168, 'season_number': 4, 'episode_number': 2})

    def test_no_nfo_or_an_episode_without_its_number_is_no_identity(self):
        film = self.base / 'Beck' / 'Beck.mkv'
        film.parent.mkdir()
        film.write_bytes(b'x')
        self.assertIsNone(os_.nfo_identity(film))
        (self.base / 'tvshow.nfo').write_text('<tvshow><uniqueid type="imdb">tt1</uniqueid><uniqueid type="imdb">tt3843168</uniqueid></tvshow>')
        self.assertIsNone(os_.nfo_identity(film))       # inside a series, but its own .nfo gives no season/episode


class CandidateTests(unittest.TestCase):
    IDENTITY = {'kind': 'episode', 'imdb': 3843168, 'tmdb': None, 'season': 4, 'episode': 2}

    @staticmethod
    def result(release, season=4, downloads=10, ai=False, parent=3843168):
        return {'attributes': {'language': 'sv', 'release': release, 'download_count': downloads, 'ai_translated': ai,
                               'files': [{'file_id': hash(release) % 10**6, 'file_name': release}],
                               'feature_details': {'parent_imdb_id': parent, 'season_number': season, 'episode_number': 2}}}

    def test_only_this_episode_and_the_closest_version_first(self):
        results = [self.result('Z.Nation.S04E02.720p.HDTV', downloads=500), self.result('Z.Nation.S04E02.1080p.BluRay', downloads=3),
                   self.result('Z.Nation.S03E02.1080p.BluRay', season=3), self.result('Other.Show.S04E02', parent=1),
                   self.result('Z.Nation.S04E02.AI', ai=True)]
        chosen = os_.timing_candidates(results, self.IDENTITY, 'Z.Nation.4x02.ITA.ENG.1080p.BDMux.DD5.1.x264.mkv')
        self.assertEqual([c['release'] for c in chosen], ['Z.Nation.S04E02.1080p.BluRay', 'Z.Nation.S04E02.720p.HDTV'])


if __name__ == '__main__':
    unittest.main()
