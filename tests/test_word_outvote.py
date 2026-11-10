import sys
import unittest

sys.path[:0] = ['/app', '/ai']
from asr_windows import replace_outvoted_word


def r(start, end, text, confidence=.8):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


# A real case from the saved jobs: only the long pass wrote "staid"; the precision and rescue passes, Whisper v2 and Qwen heard "state" between
# the same words. Whisper v2 opens the line with another word, so the whole-line vote (replace_one_word_outvoted) never counted it.
ROW = r(1267.34, 1270.16, "It's a very staid quantum interference device.", .594)
READINGS = {'context': [r(1267.34, 1270.16, "It's a very staid quantum interference device.", .594)],
            'precision': [r(1267.27, 1271.25, "It's a very state quantum interference device be both", .789)],
            'rescue': [r(1267.34, 1270.14, "It's a very state quantum interference device.", .427)],
            'v2': [r(1267.21, 1270.22, "That's a very state quantum interference device.", .291)],
            'qwen': [r(1267.04, 1270.46, "It's a very state quantum interference device.", .5)]}


class WordOutvoteTests(unittest.TestCase):
    def test_three_families_replace_the_one_word_and_keep_the_rest(self):
        rows, changes = replace_outvoted_word([dict(ROW, words=[{'word': 'staid'}])], READINGS)
        self.assertEqual(rows[0]['text'], "It's a very state quantum interference device.")
        self.assertEqual((rows[0]['start'], rows[0]['end']), (ROW['start'], ROW['end']))
        self.assertNotIn('words', rows[0])                    # the word timings no longer describe the text
        self.assertEqual((changes[0]['families'], changes[0]['kind']), (['qwen', 'whisper-v2', 'whisper-v3'], 'word'))
        self.assertEqual((changes[0]['from'], changes[0]['to']), (ROW['text'], rows[0]['text']))

    def test_two_families_are_not_enough(self):
        readings = {key: value for key, value in READINGS.items() if key != 'qwen'}
        self.assertEqual(replace_outvoted_word([ROW], readings)[1], [])

    def test_two_independent_families_are_required(self):
        readings = {key: value for key, value in READINGS.items() if key != 'v2'}
        readings['forced'] = [r(1267.3, 1270.2, "It's a very state quantum interference device.")]
        self.assertEqual(replace_outvoted_word([ROW], readings)[1], [])

    def test_a_second_reading_of_the_rows_own_word_keeps_it(self):
        readings = dict(READINGS, rescue=[r(1267.34, 1270.14, "It's a very staid quantum interference device.", .7)])
        self.assertEqual(replace_outvoted_word([ROW], readings)[1], [])

    def test_the_word_must_stand_between_the_same_words(self):
        readings = dict(READINGS, qwen=[r(1267.04, 1270.46, "It's a very state of the art interference device.", .5)])
        self.assertEqual(replace_outvoted_word([ROW], readings)[1], [])

    def test_the_first_and_last_words_of_a_row_are_never_judged(self):
        row = r(10.0, 13.0, "It's a very state quantum interference device.", .5)
        other = "That's a very state quantum interference machine."
        readings = {'context': [dict(row)], 'rescue': [r(10.0, 13.0, other)], 'v2': [r(10.0, 13.0, other)], 'qwen': [r(10.0, 13.0, other)]}
        self.assertEqual(replace_outvoted_word([row], readings), ([row], []))

    def test_two_outvoted_words_in_one_row_are_left_to_the_other_rules(self):
        row = r(10.0, 14.0, 'We saw a very staid quantum interference device in the old lab today.', .5)
        other = 'We saw a very state quantum interference device in the cold lab today.'
        readings = {'context': [dict(row)], 'rescue': [r(10.0, 14.0, other)], 'v2': [r(10.0, 14.0, other)], 'qwen': [r(10.0, 14.0, other)]}
        self.assertEqual(replace_outvoted_word([row], readings)[1], [])

    def test_readings_of_another_time_and_stock_phrases_do_not_vote(self):
        readings = {name: [dict(item, start=item['start'] + 30, end=item['end'] + 30) for item in items] if name != 'context' else items
                    for name, items in READINGS.items()}
        self.assertEqual(replace_outvoted_word([ROW], readings)[1], [])

    def test_a_short_row_is_left_alone(self):
        row = r(10.0, 11.0, 'A very staid device.', .5)
        readings = {'context': [dict(row)], **{name: [r(10.0, 11.0, 'A very state device.')] for name in ('rescue', 'v2', 'qwen')}}
        self.assertEqual(replace_outvoted_word([row], readings)[1], [])


if __name__ == '__main__':
    unittest.main()
