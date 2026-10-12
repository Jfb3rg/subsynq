import sys
import unittest

sys.path.insert(0, '/ai')
from asr_windows import prefer_fuller_reading, prefer_fuller_window_readings


def cue(start, end, text, confidence=.9):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


class FullerReadingTests(unittest.TestCase):
    """Real job 18 (Ray Gunn): the 90 s context pass squeezed sentences into run-on cues and
    dropped whole ones; the 30 s pass heard them. Only an independent reading that holds the
    same words PLUS more may win, and nothing is invented."""

    CONTEXT = [cue(56.48, 59.14, "Nova is a big star she's emotional", .61),
               cue(59.74, 65.38, 'kind of things do you mean how about murder I', .66),
               cue(66.76, 69.745, "didn't do it keep her out of trouble and you'll", .6),
               cue(69.745, 75.44, 'be a very rich man out we should', .49),
               cue(75.44, 78.23, 'We should ask for more money.', .53)]
    PRECISION = [cue(55.99, 58.01, "Venus Nova's a big star.", .75),
                 cue(58.19, 60.05, "She's emotional, volatile.", .6),
                 cue(60.47, 61.71, 'She does things.', .97),
                 cue(61.73, 62.95, 'What kind of things do you mean?', 1.0),
                 cue(63.01, 64.25, 'How about murder?', 1.0),
                 cue(66.28, 67.86, "I didn't do it!", 1.0),
                 cue(68.04, 71.26, "Keep her out of trouble, and you'll be a very rich man.", 1.0),
                 cue(71.69, 72.67, 'Get out!', .84),
                 cue(73.49, 75.35, "You're dead to me, understand?", .61),
                 cue(75.81, 78.21, 'We should ask for more money.', .96)]

    def texts(self, rows):
        return [row['text'] for row in rows]

    def test_dropped_sentences_come_back_from_the_independent_reading(self):
        result = self.texts(prefer_fuller_reading(self.CONTEXT, self.PRECISION))
        for sentence in ("Venus Nova's a big star.", 'She does things.', "You're dead to me, understand?",
                         "I didn't do it!", 'Get out!'):
            self.assertIn(sentence, result)
        self.assertNotIn("Nova is a big star she's emotional", result)

    def test_text_that_is_already_complete_is_left_alone(self):
        same = [cue(1, 3, 'We should ask for more money.'), cue(4, 6, 'I keep thinking there are none.')]
        self.assertEqual(prefer_fuller_reading(same, [dict(row) for row in same]), same)

    def test_a_single_extra_word_is_not_enough(self):
        base = [cue(0, 3, 'I keep thinking there are no nice surprises')]
        other = [cue(0, 3, 'I keep thinking there are no nice surprises anymore')]
        self.assertEqual(self.texts(prefer_fuller_reading(base, other)), [base[0]['text']])

    def test_a_reading_that_changes_most_words_does_not_win(self):
        base = [cue(0, 4, 'the only honest players in a crooked game')]
        other = [cue(0, 4, 'a totally different invented sentence appears here today')]
        self.assertEqual(prefer_fuller_reading(base, other), base)

    def test_an_unsure_cue_in_the_other_reading_blocks_it(self):
        weak = [dict(row, confidence=.3) if row['text'] == 'She does things.' else row
                for row in self.PRECISION]
        self.assertEqual(prefer_fuller_reading(self.CONTEXT, weak)[0]['text'],
                         "Nova is a big star she's emotional")

    def test_it_must_sit_on_the_same_passage(self):
        far = [dict(row, start=row['start'] + 40, end=row['end'] + 40) for row in self.PRECISION]
        self.assertEqual(prefer_fuller_reading(self.CONTEXT, far), self.CONTEXT)

    def test_a_cue_that_stays_is_never_overlapped(self):
        # the other reading's last cue reaches into a neighbouring cue that is kept
        context = [cue(0, 3, 'alpha beta gamma delta'), cue(3.1, 6, 'epsilon zeta eta theta')]
        other = [cue(0, 2, 'alpha beta gamma'), cue(2.05, 4.5, 'delta one two epsilon')]
        self.assertEqual(self.texts(prefer_fuller_reading(context, other)), self.texts(context))

    def test_result_is_ordered_and_without_overlap(self):
        result = prefer_fuller_reading(self.CONTEXT, self.PRECISION)
        for a, b in zip(result, result[1:]):
            self.assertLessEqual(a['end'], b['start'] + 1e-9)
            self.assertLess(a['start'], a['end'])

    def test_very_short_text_is_never_replaced(self):
        base = [cue(0, 1, 'Merci')]
        other = [cue(0, 1.2, 'Merci beaucoup mon ami')]
        self.assertEqual(prefer_fuller_reading(base, other), base)


class FullerWindowReadingTests(unittest.TestCase):
    """The centre window lost "Will" and "I like"; the neighbouring window heard them."""

    def test_the_other_window_wins_when_it_holds_more(self):
        merged = [cue(121.99, 124.75, 'you be making a statement stay tuned like', .83),
                  cue(128.08, 130.70, "you gun you're a real throwback", .96)]
        windows = [{'start': 60, 'end': 150, 'segments': [dict(row) for row in merged]},
                   {'start': 120, 'end': 157.4, 'segments': [
                       cue(121.82, 123.12, 'Will you be making a statement?', .63),
                       cue(123.08, 123.9, 'Stay tuned.', .84),
                       cue(124.28, 125.0, 'I', .9),
                       cue(127.79, 128.89, 'like you, Gun.', .6),
                       cue(129.19, 130.71, "You're real throwback!", .99)]}]
        texts = [row['text'] for row in prefer_fuller_window_readings(merged, windows)]
        self.assertEqual(texts[:2], ['Will you be making a statement?', 'Stay tuned.'])
        self.assertIn('like you, Gun.', texts)

    def test_windows_without_text_change_nothing(self):
        merged = [cue(0, 2, 'one two three four')]
        self.assertEqual(prefer_fuller_window_readings(merged, [{'start': 0, 'end': 30, 'segments': []}]), merged)


if __name__ == '__main__':
    unittest.main()
