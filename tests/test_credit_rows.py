import sys
import unittest
from pathlib import Path

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from asr_windows import drop_credit_rows


def cue(start, end, text):
    return {'start': start, 'end': end, 'text': text, 'confidence': .9}


class CreditRowTests(unittest.TestCase):
    def test_the_real_credit_lines_standing_alone_are_removed(self):
        # Last Samurai Standing (job 126, 108.9 s), a Norwegian film (job 25, 149.2 s), Lou (jobs 32, 33, 70, 78.6 s)
        for text, start in (('Teksting av', 108.9), ('Teksting av Nicolai Winther', 149.2), ("Sous-titrage ST'", 78.6)):
            rows = [cue(start - 20, start - 18, 'Stop it!'), cue(start, start + 1.1, text)]
            kept, removed = drop_credit_rows(rows)
            self.assertEqual([row['text'] for row in kept], ['Stop it!'], text)
            self.assertEqual([row['text'] for row in removed], [text])

    def test_a_credit_phrase_inside_a_conversation_is_kept(self):
        rows = [cue(10, 12, 'Who did this?'), cue(12.4, 14, 'Teksting av vem?'), cue(14.5, 16, 'I do not know.')]
        self.assertEqual(drop_credit_rows(rows), (rows, []))

    def test_a_long_line_and_ordinary_rows_are_kept(self):
        long_line = cue(50, 55, 'Teksting av den som skrev den här långa texten som ändå är sagd av någon i rummet idag')
        rows = [cue(10, 12, 'Hello there.'), long_line]
        self.assertEqual(drop_credit_rows(rows), (rows, []))
        self.assertEqual(drop_credit_rows([]), ([], []))

    def test_the_input_is_not_changed_and_a_credit_at_the_very_start_goes_too(self):
        rows = [cue(0.5, 2, 'Subtitles by the community'), cue(20, 22, 'Good evening.')]
        before = [dict(row) for row in rows]
        kept, removed = drop_credit_rows(rows)
        self.assertEqual(rows, before)
        self.assertEqual(([row['text'] for row in kept], len(removed)), (['Good evening.'], 1))

    def test_the_step_runs_before_the_repeated_starts_and_is_reported(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('merged, credit_rows = drop_credit_rows(merged)'), source.index('drop_unconfirmed_repeated_starts(merged, precision_profile)'))
        self.assertIn('credit_rows_dropped=credit_rows', source)
        self.assertIn('textningsrad i eftertexterna togs bort', source)


if __name__ == '__main__':
    unittest.main()
