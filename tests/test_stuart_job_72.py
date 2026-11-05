import copy
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from reread_boundaries import confirmed_continuation
from translation_qa import asks_well_not_to_be_ja, unfounded_flags


def words(*items):
    return [{'word': f' {word}', 'start': start, 'end': end, 'probability': p} for word, start, end, p in items]


# The real tight reread of row 441 in Unraid job 72 (Stuart Fails to Save the Universe S01E01, Main 0.8.364).
PRECISION = {'name': 'precision', 'language': 'en', 'confidence': .85,
             'text': 'hard on yourself. Most people underestimate the mighty force that is geology.',
             'words': words(('hard', 1049.78, 1049.98, .62), ('on', 1049.98, 1050.08, 1.0), ('yourself.', 1050.08, 1050.32, 1.0),
                            ('Most', 1050.8, 1051.4, .99), ('people', 1051.4, 1051.7, 1.0), ('underestimate', 1051.7, 1052.14, 1.0),
                            ('the', 1052.14, 1052.66, 1.0), ('mighty', 1052.66, 1052.88, .99), ('force', 1052.88, 1053.26, 1.0),
                            ('that', 1053.26, 1053.46, .99), ('is', 1053.46, 1053.72, 1.0), ('geology.', 1053.72, 1054.48, .85))}
V2 = {'name': 'v2', 'language': 'en', 'confidence': .58, 'text': 'Most people underestimate the mighty force that is geology.',
      'words': words(('Most', 1050.62, 1051.4, .43), ('people', 1051.4, 1051.82, .93), ('underestimate', 1051.82, 1052.4, .84),
                     ('the', 1052.4, 1052.76, .82), ('mighty', 1052.76, 1053.1, .66), ('force', 1053.1, 1053.38, .93),
                     ('that', 1053.38, 1053.6, .88), ('is', 1053.6, 1053.9, .9), ('geology.', 1053.9, 1054.9, .56))}
QWEN = {'name': 'qwen', 'language': 'English', 'confidence': .5, 'text': 'Hard on yourself. Most people underestimate the mighty force that is geology.'}
READINGS = [PRECISION, V2, QWEN]


def rows():
    return [{'id': 439, 'start': 1045.58, 'end': 1049.15, 'source': 'How come I never thought of that?'},
            {'id': 440, 'start': 1049.15, 'end': 1049.98, 'source': "Don't be hard.", 'source_unclear': True},
            {'id': 441, 'start': 1050.08, 'end': 1056.08, 'source': 'yourself most people underestimate the mighty force that is geology sorry',
             'source_unclear': True},
            {'id': 442, 'start': 1062.61, 'end': 1063.11, 'source': 'Sorry, fellas.'}]


class ContinuationTests(unittest.TestCase):
    """Rows 440/441: "yourself" closes "Don't be hard on yourself."; the vote per row saw similar text with other words and kept the split."""

    def continuation(self, readings=READINGS, left=None, right=None):
        data = rows()
        return confirmed_continuation(left or data[1], right or data[2], readings)

    def test_the_first_word_goes_back_to_the_sentence_it_closes(self):
        result = self.continuation()
        self.assertEqual([(row['source'], row['start'], row['end']) for row in result['rows']],
                         [("Don't be hard on yourself.", 1049.15, 1050.32),
                          ('Most people underestimate the mighty force that is geology.', 1050.8, 1056.08)])
        self.assertEqual((result['added_words'], result['dropped_words'], result['moved_word']), (['on'], ['sorry'], 'yourself'))

    def test_a_row_that_starts_a_sentence_is_left_alone(self):
        right = dict(rows()[2], source='Yourself most people underestimate the mighty force that is geology sorry')
        self.assertIsNone(self.continuation(right=right))

    def test_both_architectures_must_hear_the_end_of_the_previous_sentence(self):
        qwen = dict(QWEN, text='Most people underestimate the mighty force that is geology.')
        self.assertIsNone(self.continuation([PRECISION, V2, qwen]))
        self.assertIsNone(self.continuation([PRECISION, V2]))

    def test_every_reading_must_hear_the_same_rest(self):
        v2 = dict(V2, text='Most people overestimate the mighty force that is geology.')
        self.assertIsNone(self.continuation([PRECISION, v2, QWEN]))

    def test_at_most_one_unheard_last_word_is_dropped(self):
        right = dict(rows()[2], source='yourself most people underestimate the mighty force that is geology sorry man')
        self.assertIsNone(self.continuation(right=right))

    def test_no_existing_word_is_replaced(self):
        right = dict(rows()[2], source='yourself many people underestimate the mighty force that is geology sorry')
        self.assertIsNone(self.continuation(right=right))

    def test_at_most_one_heard_word_is_added(self):
        precision = dict(PRECISION, text='hard on on yourself. Most people underestimate the mighty force that is geology.')
        qwen = dict(QWEN, text='Hard on on yourself. Most people underestimate the mighty force that is geology.')
        self.assertIsNone(self.continuation([precision, V2, qwen]))

    def test_a_weak_closing_word_or_a_gap_between_the_rows_stops_it(self):
        weak = copy.deepcopy(PRECISION)
        weak['words'][2]['probability'] = .5
        self.assertIsNone(self.continuation([weak, V2, QWEN]))
        right = dict(rows()[2], start=1050.6)
        self.assertIsNone(self.continuation(right=right))

    def test_the_reread_changes_both_rows(self):
        job, data = {'id': 72, 'media_path': '/dev/null'}, rows()
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'):
            changed = worker.reread_flagged_rows(job, data, [441], reader=lambda *a: {441: READINGS})
        self.assertEqual(changed, [440, 441])
        self.assertEqual([row['source'] for row in data[1:3]],
                         ["Don't be hard on yourself.", 'Most people underestimate the mighty force that is geology.'])
        self.assertNotIn('source_unclear', data[1])
        self.assertEqual(data[1]['confirmed_source_words'], ['on'])
        self.assertEqual(job['reread_rows'][0]['decision'], 'radgränsen flyttad efter bekräftad fortsättning')
        self.assertEqual((data[0]['source'], data[3]['source']), ('How come I never thought of that?', 'Sorry, fellas.'))


ROW = {'id': 460, 'source': 'Well, my dear, have you changed your mind?', 'sv': 'Ja, min kära, har du ändrat dig?'}


def flag(reason, target_quote=ROW['sv']):
    return {'id': 460, 'category': 'meaning', 'source_quote': ROW['source'], 'target_quote': target_quote, 'reason': reason}


class WellIsJaTests(unittest.TestCase):
    """Row 460: an opening "Ja," is natural Swedish for "Well,"; the four remarks of job 72 only quarrelled with that and refused the file."""

    REMARKS = ["Källan säger 'Well' (väl/jo) men översättningen säger 'Ja', vilket ändrar innebörden av inledningen.",
               "Källan börjar med 'Well' (väl/jo/alltså) men översättningen har lagt till ett 'Ja' som inte finns i källan.",
               "Översättningen ändrar 'Well' (väl/jo/alltså) till 'Ja', vilket förändrar meningen från en inledande utfyllnad till ett bekräftande svar.",
               "Källan börjar med 'Well' (väl/jo/tja) och inte 'Ja'. Översättningen lägger till ett bekräftande 'Ja' som inte finns i källan."]

    def test_the_four_real_remarks_are_set_aside(self):
        for reason in self.REMARKS:
            kept, removed = unfounded_flags([flag(reason)], [ROW])
            self.assertEqual((kept, [item['id'] for item in removed]), ([], [460]), reason)

    def test_a_remark_about_anything_else_in_the_line_counts(self):
        reason = "Källan säger 'Well' men översättningen säger 'Ja', och 'min kära' saknar 'my dear'."
        self.assertFalse(asks_well_not_to_be_ja(flag(reason), ROW['source'], ROW['sv']))
        self.assertFalse(asks_well_not_to_be_ja(flag("Ordet 'ändrat' är fel."), ROW['source'], ROW['sv']))

    def test_only_an_opening_well_and_an_opening_ja(self):
        reason = self.REMARKS[0]
        self.assertFalse(asks_well_not_to_be_ja(flag(reason), 'Oh well, have you changed your mind?', ROW['sv']))
        self.assertFalse(asks_well_not_to_be_ja(flag(reason), ROW['source'], 'Nej, min kära, har du ändrat dig?'))
        self.assertFalse(asks_well_not_to_be_ja(dict(flag(reason), category='grammar'), ROW['source'], ROW['sv']))


if __name__ == '__main__':
    unittest.main()
