import sys
import unittest
from pathlib import Path

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from asr_windows import harmonize_names_by_readings, join_title_rows, resegment_unpunctuated_runs
from translation_qa import proper_name_forms


def row(start, end, text, confidence=.9):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


class TitleRowTests(unittest.TestCase):
    """Ray Gunn (dev jobs 30, 38, 153): every pass put a break after "Mr." (48.2-49.0 s, then 49.0-49.9 s with no pause) and the subtitle showed "Herr" alone."""

    def test_the_real_rows_are_joined_into_one_cue(self):
        rows = [row(46.0, 47.5, 'Hello there.'), row(48.2, 49.0, 'Mr.', .73), row(49.0, 49.9, 'Gunn, I presume?', .93), row(50.9, 52.9, 'So, uh, what can I do for you?')]
        done, joined = join_title_rows(rows)
        self.assertEqual([item['text'] for item in done], ['Hello there.', 'Mr. Gunn, I presume?', 'So, uh, what can I do for you?'])
        self.assertEqual((done[1]['start'], done[1]['end'], done[1]['confidence']), (48.2, 49.9, .73))
        self.assertEqual(len(joined), 1)
        self.assertEqual(rows[1]['text'], 'Mr.', 'the input is not changed')

    def test_nothing_else_is_joined(self):
        for rows in ([row(1.0, 2.0, 'Mr.'), row(3.0, 4.0, 'Gunn, I presume?')],                     # a pause of a second: two sentences
                     [row(1.0, 2.0, 'Mr.'), row(2.0, 3.0, 'gunn, I presume?')],                       # no capital letter
                     [row(1.0, 2.0, 'Mr. Smith'), row(2.0, 3.0, 'Gunn, I presume?')],                 # the row holds more than the title
                     [row(1.0, 2.0, 'Okay.'), row(2.0, 3.0, 'Gunn, I presume?')],                     # not a title
                     [row(1.0, 2.0, 'Mr.'), row(2.0, 9.5, 'Gunn, I presume what can I do for you?')],  # longer than seven seconds
                     [row(1.0, 2.0, 'Mr.')]):
            self.assertEqual(join_title_rows(rows)[1], [], rows)


NAMES = [row(0.0, 2.0, 'You know, photos are like this business.'), row(20.0, 22.0, 'What are you looking for, Ray?'), row(60.0, 62.0, 'I like you, Gunn.'),
         row(80.0, 83.0, 'Hello, Ray, how are you?'), {'start': 87.4, 'end': 88.3, 'text': 'Be careful, Rey.'}]
READINGS = {'context': [{'start': 84.4, 'end': 88.3, 'text': 'Be careful, Ray.', 'confidence': .74}],
            'precision': [{'start': 84.2, 'end': 87.8, 'text': 'Be careful', 'confidence': .93}, {'start': 88.0, 'end': 88.2, 'text': 'Ray.', 'confidence': .22}],
            'rescue': [{'start': 86.5, 'end': 88.3, 'text': 'Be careful, Rey.', 'confidence': .6}],
            'v2': [{'start': 86.7, 'end': 88.5, 'text': 'Be careful, Ray.', 'confidence': .46}]}


class NameTests(unittest.TestCase):
    """Ray Gunn: context (large-v3) and large-v2 read "Ray", only the rescue pass read "Rey"; the file says "Ray" elsewhere."""

    def test_a_name_that_one_pass_wrote_differently_from_two_families_and_the_file_is_written_as_they_read_it(self):
        done, changes = harmonize_names_by_readings(NAMES, READINGS)
        self.assertEqual(done[-1]['text'], 'Be careful, Ray.')
        self.assertEqual([(item['from'], item['to'], item['families']) for item in changes], [('Rey', 'Ray', ['whisper-v2', 'whisper-v3'])])
        self.assertEqual(NAMES[-1]['text'], 'Be careful, Rey.', 'the input is not changed')

    def test_one_family_is_not_enough_and_neither_is_a_name_the_file_never_uses(self):
        self.assertEqual(harmonize_names_by_readings(NAMES, {'context': READINGS['context']})[1], [])
        self.assertEqual(harmonize_names_by_readings(NAMES, {'context': READINGS['context'], 'precision': READINGS['context']})[1], [], 'two passes of one family')
        only_once = [NAMES[0], NAMES[1], NAMES[4]]
        self.assertEqual(harmonize_names_by_readings(only_once, READINGS)[1], [], '"Ray" must stand at least twice in the other rows')
        elsewhere = NAMES[:4] + [row(70.0, 72.0, 'Rey is my friend.')] + NAMES[4:]
        self.assertEqual(harmonize_names_by_readings(elsewhere, READINGS)[1], [], 'a name that stands elsewhere too is a second person')

    def test_other_words_must_be_identical_and_the_readings_must_agree(self):
        different = {'context': [{'start': 84.4, 'end': 88.3, 'text': 'Be very careful, Ray.'}], 'v2': READINGS['v2']}
        self.assertEqual(harmonize_names_by_readings(NAMES, different)[1], [])
        conflicting = {'context': READINGS['context'], 'v2': [{'start': 86.7, 'end': 88.5, 'text': 'Be careful, Roy.'}]}
        self.assertEqual(harmonize_names_by_readings(NAMES, conflicting)[1], [])
        two_letters = {'context': [{'start': 84.4, 'end': 88.3, 'text': 'Be careful, Rob.'}], 'v2': [{'start': 86.7, 'end': 88.5, 'text': 'Be careful, Rob.'}]}
        self.assertEqual(harmonize_names_by_readings(NAMES, two_letters)[1], [])


class NotANameTests(unittest.TestCase):
    """WIFE AND DOG (dev job 154): "Daddy" and "Hell" were copied as names ("Och vad hittade han i helvetet, Daddy?"); the older text said "pappa"."""

    def test_daddy_and_hell_are_not_names(self):
        rows = [{'source': 'Eventually, found himself in hell.'}, {'source': 'And what did he find in Hell, Daddy?'}]
        self.assertEqual(proper_name_forms(row['source'] for row in rows), {})
        self.assertEqual(worker.name_instruction(rows), '')

    def test_real_names_are_still_names(self):
        forms = proper_name_forms(['Hello there.', 'Mister Gunn, I presume?', 'Be careful, Ray.', 'Where is Daddy, Ray?'])
        self.assertEqual(sorted(word for words in forms.values() for word in words), ['Gunn', 'Ray'])


# WIFE AND DOG (dev job 154): the long pass wrote "digging, and" before a pause; the short pass, Whisper large-v2 and Qwen3-ASR heard no "and" (and neither did a
# clean reading of the clip with large-v3 or large-v2).
LONG_PASS = [{'start': 25.69, 'end': 31.2, 'text': 'He kept digging and digging, and', 'confidence': .672}, {'start': 32.36, 'end': 36.9, 'text': 'eventually found himself in hell.', 'confidence': .737}]
SHORT_PASS = [{'start': 25.64, 'end': 30.37, 'text': 'He kept digging and digging.', 'confidence': .913}, {'start': 32.0, 'end': 36.91, 'text': 'Eventually, he found himself in hell.', 'confidence': .73}]
SECOND_READINGS = [{'start': 27.89, 'end': 30.55, 'text': 'He kept digging and digging.', 'confidence': .355}, {'start': 25.39, 'end': 31.5, 'text': 'He kept digging and digging.', 'confidence': .5}]


class JunctionWordTests(unittest.TestCase):
    def test_a_word_only_the_long_pass_heard_between_two_sentences_is_not_hung_on_the_first(self):
        done = resegment_unpunctuated_runs(LONG_PASS, SHORT_PASS, SECOND_READINGS[:1], SECOND_READINGS[1:])
        self.assertEqual([item['text'] for item in done], ['He kept digging and digging.', 'Eventually, found himself in hell.'])
        self.assertEqual([(item['start'], item['end']) for item in done], [(25.64, 30.37), (32.0, 36.91)])

    def test_a_word_another_reading_has_between_the_same_neighbours_leaves_the_run_as_the_long_pass_wrote_it(self):
        heard = SECOND_READINGS + [{'start': 28.0, 'end': 36.0, 'text': 'He kept digging and digging, and eventually found himself in hell.'}]
        done = resegment_unpunctuated_runs(LONG_PASS, SHORT_PASS, heard[:1], heard[1:])
        self.assertEqual([item['text'] for item in done], [item['text'] for item in LONG_PASS])

    def test_the_repeat_in_digging_and_digging_is_not_a_witness_for_an_and_after_it(self):
        done = resegment_unpunctuated_runs(LONG_PASS, SHORT_PASS, [{'start': 26.0, 'end': 31.0, 'text': 'He kept digging and digging and digging.'}], [])
        self.assertEqual(done[0]['text'], 'He kept digging and digging.')


class WiringTests(unittest.TestCase):
    def test_the_two_steps_run_after_the_credit_rows_and_are_reported(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('merged, credit_rows = drop_credit_rows(merged)'), source.index('merged, title_rows_joined = join_title_rows(merged)'))
        self.assertLess(source.index('merged, title_rows_joined = join_title_rows(merged)'), source.index('merged, name_harmonized = harmonize_names_by_readings(merged, consensus_readings)'))
        self.assertLess(source.index('harmonize_names_by_readings(merged, consensus_readings)'), source.index('merged, repeated_starts = drop_unconfirmed_repeated_starts'))
        self.assertIn('title_rows_joined=title_rows_joined, names_harmonized=name_harmonized', source)
        self.assertIn('titelrad slogs ihop med namnet efter', source)


if __name__ == '__main__':
    unittest.main()
