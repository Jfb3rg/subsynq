import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import resegment_unpunctuated_runs


def cue(start, end, text, confidence=.9):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


# Real cues of Man of War (dev job 49, English track): the 90 s pass cut the first sentences in the middle, in lower
# case and without a full stop; the 30 s pass heard six proper sentences ("Fastest" more, "in" less).
LONG = [cue(13.9, 19.7, 'way between two points is a straight line let\'s go russia overnight', .915),
        cue(19.7, 20.9, 'launched its long', .986),
        cue(20.9, 25.1, "anticipated attack on ukraine he's about to jam the door mike i'm in", .533),
        cue(25.1, 26.2, 'trouble we need your help', .958)]
SHORT = [cue(13.5, 16.1, 'Fastest way between two points is a straight line.', .896),
         cue(16.6, 17.3, "Let's go.", .985),
         cue(18.6, 22.8, 'Russia overnight launched its long-anticipated attack on Ukraine.', .709),
         cue(22.8, 23.9, "He's about to jam the door.", .703),
         cue(24.0, 25.4, "Mike, I'm trouble.", .992),
         cue(25.4, 26.2, 'We need your help.', .884)]


class ResegmentTests(unittest.TestCase):
    def test_the_real_man_of_war_run_gets_the_short_passes_sentences_with_the_long_passes_words(self):
        result = resegment_unpunctuated_runs(LONG, SHORT)
        self.assertEqual([row['text'] for row in result], [
            'Fastest way between two points is a straight line.', "Let's go.",
            'Russia overnight launched its long-anticipated attack on Ukraine.',
            "He's about to jam the door.", "Mike, I'm in trouble.", 'We need your help.'])
        self.assertEqual(len(result), 6)
        self.assertTrue(all(row['text'].rstrip().endswith(('.', '?', '!')) for row in result))
        self.assertEqual([(row['start'], row['end']) for row in result],
                         [(row['start'], row['end']) for row in SHORT], 'the short pass decides the cue timing')

    def test_the_long_passes_word_the_short_pass_lost_is_kept(self):
        result = resegment_unpunctuated_runs(LONG, SHORT)
        self.assertIn("Mike, I'm in trouble.", [row['text'] for row in result], 'the short pass said "I\'m trouble"')

    def test_an_extra_edge_word_is_taken_only_when_confident_or_heard_twice(self):
        unsure = [dict(SHORT[0], confidence=.5)] + SHORT[1:]
        without = resegment_unpunctuated_runs(LONG, unsure)
        self.assertEqual(without[0]['text'], 'way between two points is a straight line.')
        heard = resegment_unpunctuated_runs(LONG, unsure, v2=[cue(13.4, 16.0, 'Fastest way between two points', .8)])
        self.assertEqual(heard[0]['text'], 'Fastest way between two points is a straight line.')

    def test_words_the_short_pass_added_inside_a_sentence_are_never_taken(self):
        short = [SHORT[0], SHORT[1], dict(SHORT[2], text='Russia overnight has launched its long-anticipated attack on Ukraine.')] \
            + SHORT[3:]
        texts = [row['text'] for row in resegment_unpunctuated_runs(LONG, short)]
        self.assertIn('Russia overnight launched its long-anticipated attack on Ukraine.', texts)

    def test_cues_that_already_end_a_sentence_are_untouched(self):
        done = [cue(1.0, 3.0, 'Hello there my friend.'), cue(3.2, 5.0, 'How are you today?')]
        short = [cue(1.0, 3.0, 'Hello there, my friend.'), cue(3.2, 5.0, 'How are you today?')]
        self.assertEqual(resegment_unpunctuated_runs(done, short), done)

    def test_readings_that_differ_too_much_do_nothing(self):
        other = [cue(row['start'], row['end'], 'completely different words about nothing at all.') for row in SHORT]
        self.assertEqual(resegment_unpunctuated_runs(LONG, other), LONG)

    def test_a_run_is_never_merged_into_fewer_cues(self):
        # Real cues of the Tamil trailer (dev job 42): one short-pass cue held two long-pass cues.
        long = [cue(26.3, 28.3, 'கோடி கோடியா சம்பாரிக்கலாம்'), cue(28.3, 28.7, 'ஏமிரம்')]
        short = [cue(26.4, 28.1, 'கோடி கோடியா சம்பாரிக்கலாம். ஏமிரம்.')]
        self.assertEqual(resegment_unpunctuated_runs(long, short), long)

    def test_a_replacement_that_overlaps_the_neighbouring_cue_is_not_used(self):
        before = [cue(10.0, 13.9, 'He said so.')]
        result = resegment_unpunctuated_runs(before + LONG, SHORT)
        self.assertEqual(result, before + LONG)

    def test_the_input_is_not_modified(self):
        before = [dict(row) for row in LONG]
        resegment_unpunctuated_runs(LONG, SHORT)
        self.assertEqual(LONG, before)


if __name__ == '__main__':
    unittest.main()
