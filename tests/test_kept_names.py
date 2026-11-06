import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from translation_qa import deterministic_flags, file_consistency_flags, repair_kept_names


def row(row_id, source, sv):
    return {'id': row_id, 'start': float(row_id * 3), 'end': float(row_id * 3 + 2), 'source': source, 'sv': sv}


def example_series_4x02():
    """The real rows of regression case(Example Series 4x02, Main 0.8.364), refused only for 457 and 462."""
    return [row(214, 'Doc, this is Red.', 'Doc, det är Red.'), row(222, "Damn, Red, everybody's gone.", 'För fan, Red, alla är borta.'),
            row(456, 'Where the hell is everyone?', 'Var i helvete är alla?'), row(457, 'Red.', 'Röd.'),
            row(458, "They're gone.", 'De är borta.'), row(461, 'Makes no sense.', 'Det är ju inte logiskt.'),
            row(462, 'Red?', 'Röd?'), row(463, 'Hey, 10-K!', 'Hallå, 10-K!')]


def example_series_4x03():
    """The real rows of regression case(Example Series 4x03, Main 0.8.364), refused only for 441."""
    return [row(125, 'Including Red and Sun May.', 'Inklusive Red och Sun May.'), row(440, 'Was it?', 'Var det så?'),
            row(441, 'Sun May.', 'Sol i maj.'), row(442, 'This was her spot.', 'Det här var hennes plats.')]


class KeptNameRepairTests(unittest.TestCase):
    """regression case: the phrase repair took "Red" for the colour in four rounds; the rows that are only names get the file's own name."""

    def test_rows_that_are_only_a_kept_name_get_the_name_back(self):
        rows = example_series_4x02()
        repaired, changes = repair_kept_names(rows, file_consistency_flags(rows))
        by_id = {item['id']: item['sv'] for item in repaired}
        self.assertEqual((by_id[457], by_id[462]), ('Red.', 'Red?'))
        self.assertEqual([change['id'] for change in changes], [457, 462])
        self.assertEqual(deterministic_flags(repaired), [])
        self.assertEqual([item['sv'] for item in repaired if item['id'] not in (457, 462)],
                         [item['sv'] for item in rows if item['id'] not in (457, 462)])

    def test_a_two_word_name_is_written_as_the_file_writes_it(self):
        rows = example_series_4x03()
        repaired, _ = repair_kept_names(rows, file_consistency_flags(rows))
        self.assertEqual({item['id']: item['sv'] for item in repaired}[441], 'Sun May.')
        self.assertEqual(deterministic_flags(repaired), [])

    def test_a_row_with_any_other_word_is_left_to_the_model(self):
        rows = [row(0, 'Doc, this is Red.', 'Doc, det är Red.'), row(1, 'Hey, Red!', 'Hallå, Röd!')]
        flags = file_consistency_flags(rows)
        self.assertEqual([flag['id'] for flag in flags], [1])
        repaired, changes = repair_kept_names(rows, flags)
        self.assertEqual((repaired, changes), (rows, []))

    def test_only_a_requested_flag_is_repaired(self):
        rows = example_series_4x02()
        flags = [flag for flag in file_consistency_flags(rows) if flag['id'] == 457]
        repaired, changes = repair_kept_names(rows, flags)
        self.assertEqual([change['id'] for change in changes], [457])
        self.assertEqual({item['id']: item['sv'] for item in repaired}[462], 'Röd?')

    def test_a_name_the_file_never_keeps_is_not_touched(self):
        rows = [row(0, 'Tell me, Canada.', 'Säg, Kanada.'), row(1, 'Canada.', 'Kanada.')]
        self.assertEqual(repair_kept_names(rows, [{'id': 1, 'category': 'meaning', 'source_quote': 'Canada', 'target_quote': 'Kanada.'}]),
                         (rows, []))

    def test_only_swedish_is_repaired(self):
        rows = example_series_4x02()
        self.assertEqual(repair_kept_names(rows, file_consistency_flags(rows), target_key='en'), (rows, []))

    def test_the_repair_step_sends_only_the_other_flags_to_the_model(self):
        rows = example_series_4x02() + [row(500, 'Casual sex.', 'Fjäsksex.')]
        other = {'id': 500, 'category': 'meaning', 'source_quote': 'Casual sex.', 'target_quote': 'Fjäsksex.', 'reason': 'Fel ord.'}
        flags = file_consistency_flags(rows) + [other]
        sent, steps = [], []

        def model(job, candidate, given, **kwargs):
            sent.append([flag['id'] for flag in given])
            return [dict(item, sv='Tillfälligt sex.') if item['id'] == 500 else dict(item) for item in candidate]

        with patch.object(worker, 'repair_flagged_translation_split', side_effect=model):
            result = worker.repair_flagged_translation({'id': 73}, rows, flags, record=steps.append)
        by_id = {item['id']: item['sv'] for item in result}
        self.assertEqual(sent, [[500]])
        self.assertEqual((by_id[457], by_id[462], by_id[500]), ('Red.', 'Red?', 'Tillfälligt sex.'))
        self.assertEqual([step['stage'] for step in steps], ['kept_name'])

    def test_no_model_call_when_only_names_were_flagged(self):
        rows = example_series_4x03()
        with patch.object(worker, 'repair_flagged_translation_split', side_effect=AssertionError('model called')):
            result = worker.repair_flagged_translation({'id': 70}, rows, file_consistency_flags(rows))
        self.assertEqual({item['id']: item['sv'] for item in result}[441], 'Sun May.')


if __name__ == '__main__':
    unittest.main()
