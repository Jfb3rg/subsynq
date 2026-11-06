import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
from subtitle_layout import MAX_LINE, join_fragments, layout_cue, layout_cues, wrap_lines


def lines(cues):
    return [line for _, _, text in cues for line in text.split('\n')]


class WrapTests(unittest.TestCase):
    """Example Series 4x03 (regression case): one line per cue, up to 107 characters; at most two lines of 42 now."""

    def test_a_short_line_is_left_alone(self):
        self.assertEqual(wrap_lines('Är de båda vid liv?'), 'Är de båda vid liv?')

    def test_two_lines_break_after_a_comma_or_before_a_conjunction(self):
        self.assertEqual(wrap_lines('Vapen och förnödenheter, det är ju helt jävla ologiskt.'),
                         'Vapen och förnödenheter,\ndet är ju helt jävla ologiskt.')
        self.assertEqual(wrap_lines('Förnödenheter finns fortfarande här, till och med ammunition.'),
                         'Förnödenheter finns fortfarande här,\ntill och med ammunition.')

    def test_a_line_never_ends_on_an_article_or_a_preposition(self):
        text = 'Vi måste ta oss till en plats där ingen kan hitta oss i natt.'
        first = wrap_lines(text).split('\n')[0].split(' ')[-1]
        self.assertNotIn(first, ('till', 'en', 'där', 'i'))

    def test_every_word_is_kept_in_order(self):
        text = 'Jag har hållit på med det här tillräckligt länge för att veta'
        self.assertEqual(wrap_lines(text).replace('\n', ' '), text)

    def test_a_number_with_a_space_is_not_broken(self):
        wrapped = wrap_lines('Vad vi än gör måste det bara se övertygande ut från 3 048 meters höjd.')
        self.assertNotIn('3\n048', wrapped)


class CueTests(unittest.TestCase):
    def test_a_cue_that_does_not_fit_two_lines_is_split_in_time(self):
        text = 'Han kommer att hitta vad än det var som slet sönder hennes ansikte och slukade hennes själ.'
        cues = layout_cue(407.176, 411.46, text)
        self.assertEqual(len(cues), 2)
        self.assertEqual((cues[0][0], cues[-1][1]), (407.176, 411.46))
        self.assertEqual(cues[0][1], cues[1][0])
        self.assertTrue(all(len(line) <= MAX_LINE for line in lines(cues)))
        self.assertEqual(' '.join(part.replace('\n', ' ') for _, _, part in cues), text)

    def test_a_cue_too_short_to_split_keeps_two_long_lines(self):
        text = 'Han kommer att hitta vad än det var som slet sönder hennes ansikte och slukade hennes själ.'
        cues = layout_cue(10.0, 11.5, text)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0][2].replace('\n', ' '), text)
        self.assertEqual(cues[0][2].count('\n'), 1)

    def test_the_written_file_has_no_line_longer_than_42(self):
        import worker
        rows = [{'id': 1, 'start': 10.18, 'end': 13.24, 'sv': 'En helikopter lyfter om 15 minuter, och om vi inte är ombord på den helikoptern,'},
                {'id': 2, 'start': 407.176, 'end': 411.46,
                 'sv': 'Han kommer att hitta vad än det var som slet sönder hennes ansikte och slukade hennes själ.'}]
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'test.db'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
            media = Path(folder) / 'video.mkv'
            media.write_bytes(b'')
            with patch.object(worker, 'update_job'), patch.object(worker, 'DATABASE', database):
                output = worker.write_srt({'id': 76}, media, rows)
            written = output.read_text(encoding='utf-8')
        blocks = written.strip().split('\n\n')
        self.assertEqual([block.split('\n')[0] for block in blocks], ['1', '2', '3'])
        self.assertTrue(all(len(line) <= MAX_LINE for block in blocks for line in block.split('\n')[2:]))


class JoinTests(unittest.TestCase):
    """Example Series 4x02 (regression case): one sentence came as several cues right after each other."""

    def test_a_sentence_in_two_cues_right_after_each_other_becomes_one_cue_of_two_lines(self):
        cues = [(215.72, 218.1, 'Har du någon aning om hur många jag sviker genom'), (218.12, 219.93, 'att missa den här bouleturneringen?')]
        self.assertEqual(join_fragments(cues), [(215.72, 219.93, 'Har du någon aning om hur många jag sviker genom att missa den här bouleturneringen?')])
        self.assertEqual(len(layout_cues(cues)), 1)

    def test_a_pause_a_finished_sentence_or_a_capital_keeps_the_cues_apart(self):
        pause = [(1532.36, 1533.2, 'Den'), (1537.8, 1540.1, 'större bilden som gör dig')]
        finished = [(10.0, 11.0, 'Vi går.'), (11.1, 12.0, 'nu direkt')]
        capital = [(10.0, 11.0, 'Vi går'), (11.1, 12.0, 'Nu direkt')]
        for cues in (pause, finished, capital):
            self.assertEqual(join_fragments(cues), cues)

    def test_cues_that_would_not_fit_two_lines_or_seven_seconds_stay_apart(self):
        long = [(0.0, 3.0, 'Jag vill vara där, oavsett om jag njuter av en härlig måltid eller'), (3.1, 5.0, 'behöver kissa, och jag kommer att vara där hela tiden')]
        slow = [(0.0, 5.0, 'Jag vill'), (5.2, 8.0, 'vara där')]
        self.assertEqual(join_fragments(long), long)
        self.assertEqual(join_fragments(slow), slow)


if __name__ == '__main__':
    unittest.main()
