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


if __name__ == '__main__':
    unittest.main()
