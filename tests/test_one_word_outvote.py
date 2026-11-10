import sys
import unittest

sys.path[:0] = ['/app', '/ai']
from asr_windows import replace_one_word_outvoted


def r(start, end, text, confidence=.8):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


# A real case from the saved jobs: only the precision pass wrote the last "you"; the rescue pass, Whisper v2 and Qwen heard the line without it,
# their readings a second earlier than the refined row start.
ROW = r(2069.544, 2071.18, 'Take off that fucking hat you', .608)
READINGS = {'precision': [r(2068.7, 2071.18, 'Take off that fucking hat you', .608)],
            'rescue': [r(2068.35, 2070.45, 'Take off that fucking hat.', .843)],
            'v2': [r(2068.4, 2070.58, 'Take off that fucking hat.', .52)],
            'qwen': [r(2068.4, 2071.48, 'Take off that fucking hat.', .5)]}


class OneWordOutvoteTests(unittest.TestCase):
    def test_three_families_one_word_apart_replace_the_text_and_keep_the_timing(self):
        rows, changes = replace_one_word_outvoted([ROW], READINGS)
        self.assertEqual(rows[0]['text'], 'Take off that fucking hat.')
        self.assertEqual((rows[0]['start'], rows[0]['end']), (ROW['start'], ROW['end']))
        self.assertEqual(changes[0]['families'], ['qwen', 'whisper-v2', 'whisper-v3'])

    def test_two_families_are_not_enough(self):
        readings = {key: value for key, value in READINGS.items() if key != 'rescue'}
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])

    def test_two_independent_families_are_required(self):
        readings = {'precision': READINGS['precision'], 'rescue': READINGS['rescue'], 'context': [r(2068.4, 2070.6, 'Take off that fucking hat.')],
                    'v2': READINGS['v2']}
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])

    def test_a_second_reading_of_the_rows_own_words_keeps_them(self):
        readings = dict(READINGS, context=[r(2069.5, 2071.2, 'Take off that fucking hat you')])
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])

    def test_more_than_one_word_apart_is_left_to_the_other_rules(self):
        readings = {name: [dict(item, text='Take off that hat.') for item in items] for name, items in READINGS.items() if name != 'precision'}
        readings['precision'] = READINGS['precision']
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])

    def test_the_families_must_say_exactly_the_same_words(self):
        readings = dict(READINGS, qwen=[r(2068.4, 2071.48, 'Take off that freaking hat.')])
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[1], [])


    def test_many_distant_readings_and_an_unsorted_pass_give_the_same_result(self):
        # The search only looks at readings near the row; far-away readings (a long film) and a pass listed out of time order change nothing.
        far = [r(start, start + 2, 'Something else entirely.') for start in range(0, 2000, 4)]
        readings = {name: [r(2300 + 4 * place, 2302 + 4 * place, 'Unrelated line.') for place in range(50)] + items + far
                    for name, items in READINGS.items()}
        readings['precision'] = readings['precision'][::-1]
        rows, changes = replace_one_word_outvoted([ROW], readings)
        self.assertEqual(rows[0]['text'], 'Take off that fucking hat.')
        self.assertEqual(len(changes), 1)

    def test_a_reading_without_times_is_skipped(self):
        readings = dict(READINGS, context=[{'text': 'Take off that fucking hat you'}])
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[0][0]['text'], 'Take off that fucking hat.')


    # Real readings of a saved file: the cue ends at 866.04 s, the last word "Yes." sits at 866.1-866.4 s in two passes.
    TRAILING_ROW = r(865.256, 866.04, 'Honestly. Really? Yes.', .6)
    TRAILING = {'context': [r(864.9, 866.0, 'Honestly, really?', .27)], 'v2': [r(865.2, 865.8, 'Honestly.'), r(865.8, 866.2, 'Really?'), r(866.2, 866.5, 'Yeah.')],
                'precision': [r(865.0, 865.6, 'Honestly.', .92), r(865.7, 866.1, 'Really?', .94), r(866.1, 866.4, 'Yes.', .76)],
                'rescue': [r(865.7, 866.1, 'Really?', .84), r(866.1, 866.4, 'Yes.', .57)],
                'qwen': [r(864.6, 866.3, 'Honestly.', .5)], 'parakeet': [r(864.6, 866.3, 'Honestly. Really?', .5)]}

    def test_a_word_two_passes_heard_next_to_the_row_is_not_dropped_by_a_reading_that_stops_early(self):
        self.assertEqual(replace_one_word_outvoted([self.TRAILING_ROW], self.TRAILING)[1], [])

    def test_the_same_word_in_the_next_sentence_is_no_witness(self):
        readings = dict(READINGS, v2=READINGS['v2'] + [r(2071.2, 2072.4, 'You hungry?')], qwen=READINGS['qwen'] + [r(2071.2, 2072.6, 'You hungry?')])
        self.assertEqual(replace_one_word_outvoted([ROW], readings)[0][0]['text'], 'Take off that fucking hat.')


if __name__ == '__main__':
    unittest.main()
