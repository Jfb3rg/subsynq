import sys
import unittest

sys.path.insert(0, '/app')
from asr_windows import drop_misplaced_duplicate_cues, prefer_precision_for_unpunctuated_runs


def cue(start, end, text, confidence=1.0):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


def texts(rows):
    return [row['text'] for row in rows]


# Real PLANET 9 readings (job 30): the long pass wrote the stretch in lower case, cut in the middle of sentences.
LONG_FIRST = [
    cue(10.0, 11.8, 'No signal.'),
    cue(29.6, 33.4, "we've intercepted a transmission from earth negative the signal"),
    cue(33.4, 34.4, 'originates from the planet'),
    cue(34.4, 41.0, 'ahead how old is it estimated transmission date 12 million years ago'),
    cue(41.0, 42.1, "that's impossible"),
    cue(42.7, 47.1, "humanity didn't exist 12 million years ago correct and yet the"),
    cue(47.1, 48.4, 'message is addressed to us'),
    cue(124.9, 125.6, 'Wait.'),
]
SHORT_FIRST = [
    cue(28.7, 30.9, "Captain, we've intercepted a transmission.", .75),
    cue(31.0, 31.8, 'From Earth?', .87),
    cue(31.9, 32.6, 'Negative.', .99),
    cue(32.6, 34.7, 'The signal originates from the planet ahead.', .99),
    cue(36.1, 37.4, 'How old is it?', .99),
    cue(37.8, 41.0, 'Estimated transmission date, 12 million years ago.', .96),
    cue(41.1, 41.9, "That's impossible.", 1.0),
    cue(42.7, 44.8, "Humanity didn't exist 12 million years ago.", 1.0),
    cue(44.9, 45.4, 'Correct.', 1.0),
    cue(45.6, 48.3, 'And yet, the message is addressed to us.', 1.0),
]
SECOND_OPINION = [cue(28.3, 31.1, "Captain, we've intercepted a transmission.", .9),
                  cue(31.3, 31.9, 'From Earth?', .9)]
EXPECTED_FIRST = ["No signal.", "Captain, we've intercepted a transmission.", 'From Earth?', 'Negative.',
                  'The signal originates from the planet ahead.', 'How old is it?',
                  'Estimated transmission date, 12 million years ago.', "That's impossible.",
                  "Humanity didn't exist 12 million years ago.", 'Correct.',
                  'And yet, the message is addressed to us.', 'Wait.']


class UnpunctuatedRunTests(unittest.TestCase):
    def test_a_run_without_a_sentence_end_becomes_the_short_passes_sentences(self):
        result = prefer_precision_for_unpunctuated_runs(LONG_FIRST, SHORT_FIRST, SECOND_OPINION)
        self.assertEqual(texts(result), EXPECTED_FIRST)
        self.assertEqual([row['start'] for row in result][:3], [10.0, 28.7, 31.0], 'times come from the short pass')

    def test_the_input_rows_are_not_modified(self):
        before = [dict(row) for row in LONG_FIRST]
        prefer_precision_for_unpunctuated_runs(LONG_FIRST, SHORT_FIRST, SECOND_OPINION)
        self.assertEqual(LONG_FIRST, before)

    def test_an_extra_edge_word_needs_confidence_or_a_second_reading(self):
        # "Captain," is only in the short pass, at confidence 0.75, and v2 did not hear it: the first cues stay.
        unchanged = prefer_precision_for_unpunctuated_runs(LONG_FIRST, SHORT_FIRST, [])
        self.assertEqual(texts(unchanged)[:4], texts(LONG_FIRST)[:4])
        self.assertNotIn('the signal The signal originates from the planet ahead.', ' '.join(texts(unchanged)))
        # The same word at confidence 0.9 is trusted by itself.
        confident = [dict(row, confidence=.9) if row['text'].startswith('Captain') else row for row in SHORT_FIRST]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(LONG_FIRST, confident, [])), EXPECTED_FIRST)

    def test_another_model_family_can_confirm_an_extra_edge_word_but_never_supplies_text(self):
        qwen = [cue(28.0, 34.0, "Captain, we have intercepted a transmission from Earth. Negative. The signal", .5)]
        result = prefer_precision_for_unpunctuated_runs(LONG_FIRST, SHORT_FIRST, [], corroboration=qwen)
        self.assertEqual(texts(result), EXPECTED_FIRST)
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(LONG_FIRST, [], [], corroboration=qwen)),
                         texts(LONG_FIRST), "a corroborating reading is never used as the text")

    def test_a_word_that_differs_in_the_middle_keeps_the_long_pass(self):
        # Houston is only in the long pass.
        long = [cue(49.7, 56.4, "transmission active houston i hope you're receiving this"),
                cue(57.6, 59.0, "we're preparing to descend"), cue(59.0, 59.9, "to the planet's surface.")]
        short = [cue(49.9, 50.9, 'Transmission active.'),
                 cue(54.6, 59.9, "i hope you're receiving this we're preparing to descend to the planet's surface")]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(long, short)), texts(long))

    def test_the_second_short_reading_is_used_when_the_first_lacks_a_word(self):
        long = [cue(49.7, 56.4, "transmission active houston i hope you're receiving this"),
                cue(57.6, 59.0, "we're preparing to descend"), cue(59.0, 59.9, "to the planet's surface.")]
        short = [cue(49.9, 50.9, 'Transmission active.'),
                 cue(54.6, 59.9, "i hope you're receiving this we're preparing to descend to the planet's surface")]
        second = [cue(49.9, 51.2, 'Transmission active.'), cue(54.0, 56.6, "Houston, I hope you're receiving this."),
                  cue(57.6, 59.0, "We're preparing to descend."), cue(59.0, 60.1, "to the planet's surface.")]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(long, short, second)),
                         ['Transmission active.', "Houston, I hope you're receiving this.",
                          "We're preparing to descend.", "to the planet's surface."])

    def test_a_lost_first_word_is_restored_from_a_confident_short_pass(self):
        long = [cue(91.7, 93.0, 'everything recording'), cue(94.0, 100.5, "oh my god i can't believe there was "
                                                                         'a civilization here scan for'),
                cue(100.5, 101.7, 'life scanning'), cue(110.0, 111.1, 'Understood, Captain.')]
        short = [cue(91.2, 92.3, 'Record everything.', .89), cue(92.6, 93.2, 'Recording.', .97),
                 cue(94.4, 95.4, 'Oh my god.', .61), cue(96.5, 98.9, "I can't believe there was a civilization here.", .98),
                 cue(99.8, 100.9, 'Scan for life.', .98), cue(101.2, 101.8, 'Scanning.', 1.0)]
        result = prefer_precision_for_unpunctuated_runs(long, short)
        self.assertEqual(texts(result), ['Record everything.', 'Recording.', 'Oh my god.',
                                         "I can't believe there was a civilization here.", 'Scan for life.',
                                         'Scanning.', 'Understood, Captain.'])

    def test_cues_that_already_end_a_sentence_are_never_touched(self):
        long = [cue(1, 2, 'Hello there.'), cue(3, 4, 'How are you?')]
        short = [cue(1, 2, 'Hello, there!'), cue(3, 4, 'How are you doing?')]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(long, short)), texts(long))

    def test_a_short_pass_at_another_time_is_not_used(self):
        shifted = [dict(row, start=row['start'] + 6, end=row['end'] + 6) for row in SHORT_FIRST]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(LONG_FIRST, shifted, SECOND_OPINION)),
                         texts(LONG_FIRST))

    def test_a_replacement_that_overlaps_the_next_cue_a_little_is_cut_there(self):
        long = LONG_FIRST[:-1] + [cue(48.2, 49.0, 'Wait.')]
        result = prefer_precision_for_unpunctuated_runs(long, SHORT_FIRST, SECOND_OPINION)
        # The sentence that would end at 48.3 s runs 0.1 s into the cue that starts at 48.2 s: it is used and ends there (0.8.310; APOCALYPSE Z
        # job 185 kept "Move away and keep" / "walking." because of such an overlap).
        self.assertIn('And yet, the message is addressed to us.', texts(result))
        used = next(row for row in result if row['text'] == 'And yet, the message is addressed to us.')
        self.assertEqual(used['end'], 48.2)

    def test_a_replacement_that_overlaps_the_next_cue_a_lot_is_not_used(self):
        long = LONG_FIRST[:-1] + [cue(47.6, 49.0, 'Wait.')]
        result = prefer_precision_for_unpunctuated_runs(long, SHORT_FIRST, SECOND_OPINION)
        self.assertNotIn('And yet, the message is addressed to us.', texts(result))

    def test_a_short_pass_that_is_just_as_unpunctuated_is_no_improvement(self):
        long = [cue(1, 2, 'we have been waiting'), cue(2, 3, 'for you')]
        short = [cue(1, 2, 'we have been waiting'), cue(2, 3, 'for you')]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(long, short)), texts(long))

    def test_a_long_silence_ends_a_run(self):
        long = [cue(1, 3, 'we have been waiting for you'), cue(9, 11, 'it is time to leave')]
        short = [cue(1, 2, 'We have been waiting.'), cue(2.1, 3, 'For you.'), cue(9, 10, 'It is time.'),
                 cue(10.1, 11, 'To leave.')]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(long, short)),
                         ['We have been waiting.', 'For you.', 'It is time.', 'To leave.'])

    def test_one_cue_that_only_gains_a_full_stop_is_left_alone(self):
        # A lone "Jarbon" must not move in time (real job 12) just because the short pass added a full stop.
        long = [cue(50.5, 51.6, 'Jarbon')]
        short = [cue(51.2, 51.6, 'Jarbon.')]
        self.assertEqual(prefer_precision_for_unpunctuated_runs(long, short), long)

    def test_nothing_to_compare_with_changes_nothing(self):
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(LONG_FIRST, [], [])), texts(LONG_FIRST))
        self.assertEqual(prefer_precision_for_unpunctuated_runs([], SHORT_FIRST, SECOND_OPINION), [])


    def test_a_sentence_split_over_two_cues_is_joined_when_the_short_pass_heard_it_as_one(self):
        long = [cue(129.4, 130.0, 'What are'), cue(130.0, 130.5, 'they showing?')]
        short = [cue(129.4, 130.5, 'What are they showing?')]
        self.assertEqual(texts(prefer_precision_for_unpunctuated_runs(long, short)), ['What are they showing?'])


class MisplacedDuplicateTests(unittest.TestCase):
    GHOST = cue(77.7, 78.5, 'Record everything.', .76)
    REAL = cue(91.2, 92.3, 'Record everything.', .89)
    SPEECH = [{'start': 69.0, 'end': 70.2}, {'start': 91.8, 'end': 93.4}]

    def test_a_cue_stamped_where_nothing_is_spoken_is_dropped_when_the_same_words_are_heard_elsewhere(self):
        result = drop_misplaced_duplicate_cues([self.GHOST, self.REAL], [self.REAL], self.SPEECH)
        self.assertEqual(result, [self.REAL])

    def test_a_line_that_is_really_repeated_is_kept(self):
        speech = self.SPEECH + [{'start': 77.5, 'end': 78.7}]
        self.assertEqual(drop_misplaced_duplicate_cues([self.GHOST, self.REAL], [self.REAL], speech),
                         [self.GHOST, self.REAL])

    def test_without_voice_activity_data_nothing_is_dropped(self):
        self.assertEqual(drop_misplaced_duplicate_cues([self.GHOST, self.REAL], [self.REAL], []),
                         [self.GHOST, self.REAL])

    def test_different_words_single_words_and_small_shifts_are_kept(self):
        other = cue(77.7, 78.5, 'Record the transmission.')
        self.assertEqual(drop_misplaced_duplicate_cues([other, self.REAL], [self.REAL], self.SPEECH), [other, self.REAL])
        single = cue(77.7, 78.5, 'Ray!')
        reference = cue(91.2, 92.3, 'Ray!')
        self.assertEqual(drop_misplaced_duplicate_cues([single, reference], [reference], self.SPEECH),
                         [single, reference])
        near = cue(88.0, 89.0, 'Record everything.')
        self.assertEqual(drop_misplaced_duplicate_cues([near, self.REAL], [self.REAL], self.SPEECH), [near, self.REAL])


if __name__ == '__main__':
    unittest.main()
