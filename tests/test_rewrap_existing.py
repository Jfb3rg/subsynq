import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = ['/app', '/ai']
import subtitle_cleanup
from subtitle_layout import MAX_LINE, format_srt, parse_srt, relayout_srt

LONG = ('1\n00:06:47,176 --> 00:06:51,460\nHan kommer att hitta vad än det var som slet sönder hennes ansikte och slukade hennes själ.\n\n'
        '2\n00:06:51,540 --> 00:06:53,040\nJag har hållit på med det här tillräckligt länge för att veta\n\n'
        '3\n00:06:53,040 --> 00:06:55,900\nnär en situation inte längre är förenlig med överlevnad.\n')
SHORT = '1\n00:00:01,000 --> 00:00:02,000\nÄr de båda vid liv?\n'


def words(text):
    return ' '.join(cue[2] for cue in parse_srt(text)).split()


class RelayoutTests(unittest.TestCase):
    def test_a_long_file_gets_two_lines_of_42_and_keeps_every_word(self):
        laid_out = relayout_srt(LONG)
        self.assertTrue(all(len(line) <= MAX_LINE for line in laid_out.split('\n')))
        self.assertEqual(words(laid_out), words(LONG))
        cues = parse_srt(laid_out)
        self.assertEqual((cues[0][0], cues[-1][1]), (407.176, 415.9))

    def test_a_file_that_fits_or_is_not_srt_is_left_alone(self):
        self.assertIsNone(relayout_srt(SHORT))
        self.assertIsNone(relayout_srt('inte en undertext\nalls'))

    def test_parse_and_format_keep_the_times(self):
        self.assertEqual(format_srt(parse_srt(SHORT)), SHORT)


class RewrapFilesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        self.root, self.trash = base / 'tv', base / 'trash'
        (self.root / 'Show').mkdir(parents=True)
        (self.root / 'Show' / 'Episode.mkv').write_bytes(b'video')
        self.ai = self.root / 'Show' / 'Episode.ai.sv.srt'
        self.ai.write_text(LONG, encoding='utf-8')
        self.other = self.root / 'Show' / 'Episode.sv.srt'
        self.other.write_text(LONG, encoding='utf-8')

    def tearDown(self):
        self.folder.cleanup()

    def test_an_ai_subtitle_is_laid_out_and_its_original_can_be_restored(self):
        batch, changed, unchanged, failed = subtitle_cleanup.rewrap_files([str(self.ai)], [str(self.root)], self.trash)
        self.assertEqual((changed, unchanged, failed), (1, 0, []))
        self.assertTrue(all(len(line) <= MAX_LINE for line in self.ai.read_text(encoding='utf-8').split('\n')))
        self.assertEqual(subtitle_cleanup.list_trash(self.trash)[0]['id'], batch)
        restored, skipped = subtitle_cleanup.restore_batch(batch, [str(self.root)], self.trash)
        self.assertEqual((restored, skipped), (1, []))
        self.assertEqual(self.ai.read_text(encoding='utf-8'), LONG)
        self.assertEqual(subtitle_cleanup.list_trash(self.trash), [])

    def test_a_file_changed_after_the_layout_is_not_overwritten_by_restore(self):
        batch, _, _, _ = subtitle_cleanup.rewrap_files([str(self.ai)], [str(self.root)], self.trash)
        self.ai.write_text(SHORT, encoding='utf-8')
        restored, skipped = subtitle_cleanup.restore_batch(batch, [str(self.root)], self.trash)
        self.assertEqual(restored, 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(self.ai.read_text(encoding='utf-8'), SHORT)

    def test_only_subsynq_files_and_only_files_that_need_it(self):
        (self.root / 'Show' / 'Other.mkv').write_bytes(b'video')
        short = self.root / 'Show' / 'Other.ai.sv.srt'
        short.write_text(SHORT, encoding='utf-8')
        batch, changed, unchanged, failed = subtitle_cleanup.rewrap_files([str(self.other), str(short)], [str(self.root)], self.trash)
        self.assertEqual((changed, unchanged, len(failed)), (0, 1, 1))
        self.assertEqual(self.other.read_text(encoding='utf-8'), LONG)
        self.assertEqual(subtitle_cleanup.list_trash(self.trash), [])

    def test_other_tools_ai_subtitles_are_never_touched(self):
        # Real names from a library: the first rewrap changed 244 such files.
        others = ['Episode.subgen.large-v3.ai.eng.srt', 'Episode.sv.ai.gpt-5.4-mini.srt', 'Episode.subgen.large-v3.ai.sv.srt', 'Other.ai.sv.srt']
        for name in others:
            (self.root / 'Show' / name).write_text(LONG, encoding='utf-8')
        _, changed, unchanged, failed = subtitle_cleanup.rewrap_files([str(self.root / 'Show' / name) for name in others],
                                                                      [str(self.root)], self.trash)
        self.assertEqual((changed, unchanged, len(failed)), (0, 0, 4))
        for name in others:
            self.assertEqual((self.root / 'Show' / name).read_text(encoding='utf-8'), LONG, name)

    def test_an_unreadable_file_is_a_failure_not_already_right(self):
        self.ai.write_text('1\n00:00:01,000 --> 00:00:02,000\n\nKom igen.\n', encoding='utf-8')
        _, changed, unchanged, failed = subtitle_cleanup.rewrap_files([str(self.ai)], [str(self.root)], self.trash)
        self.assertEqual((changed, unchanged, len(failed)), (0, 0, 1))

    def test_subsynq_names_old_and_new(self):
        self.assertTrue(subtitle_cleanup.is_subsynq_subtitle('Film (2020).ai.sv.srt'))
        self.assertTrue(subtitle_cleanup.is_subsynq_subtitle('Film.sv.ai.srt'))
        self.assertTrue(subtitle_cleanup.is_subsynq_subtitle('Film.ai.en.srt'))
        for name in ('Film.subgen.large-v3.ai.eng.srt', 'Film.sv.ai.gpt-5.4-mini.srt', 'Film.sv.srt', 'Film.ai.de.srt'):
            self.assertFalse(subtitle_cleanup.is_subsynq_subtitle(name), name)

    def test_the_button_counts_only_files_it_would_change(self):
        short = self.root / 'Show' / 'Short.ai.sv.srt'
        (self.root / 'Show' / 'Short.mkv').write_bytes(b'video')
        short.write_text(SHORT, encoding='utf-8')
        items = subtitle_cleanup.scan_srt([self.root])
        self.assertEqual(subtitle_cleanup.rewrap_count(items), 1)          # Episode.ai.sv.srt; not Short, not the other tool's Episode.sv.srt
        subtitle_cleanup.rewrap_files([str(self.ai)], [str(self.root)], self.trash)
        self.assertEqual(subtitle_cleanup.rewrap_count(subtitle_cleanup.scan_srt([self.root])), 0)

    def test_a_cue_too_short_to_split_is_not_counted_for_ever(self):
        tight = '1\n00:00:10,000 --> 00:00:11,500\nHan kommer att hitta vad än det var som slet sönder hennes ansikte och slukade hennes själ.\n'
        self.ai.write_text(tight, encoding='utf-8')
        subtitle_cleanup.rewrap_files([str(self.ai)], [str(self.root)], self.trash)
        self.assertFalse(subtitle_cleanup.needs_rewrap(self.ai))

    def test_a_laid_out_file_laid_out_again_stays_the_same(self):
        once = relayout_srt(LONG)
        self.assertIsNone(relayout_srt(once))

    def test_an_older_scan_without_the_count_is_checked_now(self):
        self.assertEqual(subtitle_cleanup.rewrap_count([{'path': str(self.ai), 'name': self.ai.name}]), 1)

    def test_a_file_outside_the_media_folders_is_refused(self):
        outside = Path(self.folder.name) / 'Outside.ai.sv.srt'
        outside.write_text(LONG, encoding='utf-8')
        _, changed, _, failed = subtitle_cleanup.rewrap_files([str(outside)], [str(self.root)], self.trash)
        self.assertEqual((changed, len(failed)), (0, 1))
        self.assertEqual(outside.read_text(encoding='utf-8'), LONG)


if __name__ == '__main__':
    unittest.main()
