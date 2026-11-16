"""Confirm kept words without changing ASR decisions or the source refusal gate."""
import copy
import sys
import unittest

sys.path.insert(0, '/app')
from reread import confirmed_by_reread, confirmation_comparison


def sample(source='We are going home.'):
    row = dict(id=7, start=10., end=12., source=source)
    record = dict(id=7, start=10., end=12., current=source, decision='liknande text med olika ord',
                  readings=[dict(name=n, text=source, confidence=.9, language=lang)
                            for n, lang in [('precision', 'en'), ('v2', 'en'), ('qwen', 'English')]])
    return row, record


def leading_word(record, *, start=9.7, end=9.8, word='Well,'):
    reading = record['readings'][0]
    reading['text'] = word + ' ' + reading['text']
    reading['words'] = [dict(word=word+' ', start=start, end=end)] + [
        dict(word=w+' ', start=10.+i*.4, end=10.3+i*.4)
        for i, w in enumerate(record['current'].split())]


class ReplacementConfirmationTests(unittest.TestCase):
    def sample(self):
        row, record = sample()
        record.update(current='We go.', candidate=row['source'], decision='ersatt')
        return row, record

    def confirm(self, row, record):
        return confirmed_by_reread([row], [record], {7})

    def test_applied_replacement_is_bound_to_candidate_and_preserves_raw_evidence(self):
        row, record = self.sample()
        before = copy.deepcopy((row, record))
        proofs = []
        self.assertEqual(confirmed_by_reread([row], [record], {7}, comparisons=proofs), [7])
        self.assertEqual(proofs[0]['source'], row['source'])
        self.assertEqual(proofs[0]['previous_source'], record['current'])
        self.assertTrue(proofs[0]['applied_replacement'])
        self.assertEqual((row, record), before)

    def test_each_required_reader_must_participate_and_support_every_word(self):
        for index in range(3):
            for missing in (False, True):
                with self.subTest(reader=index, missing=missing):
                    row, record = self.sample()
                    if missing:
                        record['readings'].pop(index)
                    else:
                        record['readings'][index]['text'] = 'We are not going home.'
                    self.assertEqual(self.confirm(row, record), [])

    def test_an_extra_dissenting_reader_and_duplicate_reader_identity_are_not_ignored(self):
        row, record = self.sample()
        record['readings'].append(dict(name='context', text='We are staying home.', confidence=.9))
        self.assertEqual(self.confirm(row, record), [])
        row, record = self.sample()
        record['readings'].append(dict(record['readings'][0]))
        self.assertEqual(self.confirm(row, record), [])

    def test_a_candidate_without_an_explicit_applied_replacement_is_not_confirmation(self):
        for decision in ('liknande text med olika ord', 'modellfamiljerna läste olika ord',
                         'omläsningen innehåller redan skrivna grannord', None):
            row, record = self.sample()
            record['decision'] = decision
            self.assertEqual(self.confirm(row, record), [])

    def test_missing_or_stale_candidate_or_changed_final_words_cannot_clear_a_flag(self):
        for change in ('missing', 'candidate', 'source', 'id'):
            row, record = self.sample()
            if change == 'missing':
                record.pop('candidate')
            elif change == 'candidate':
                record['candidate'] = 'We are not going home.'
            elif change == 'source':
                row['source'] = 'We are not going home.'
            else:
                record['id'] = 8
            self.assertEqual(self.confirm(row, record), [])

    def test_changed_missing_or_invalid_audio_bounds_cannot_clear_a_flag(self):
        for field in ('start', 'end'):
            for value in (None, float('nan'), float('inf'), -1., 0., 10.01, 12.01):
                row, record = self.sample()
                record[field] = value
                self.assertEqual(self.confirm(row, record), [])
        row, record = self.sample()
        row['start'] += .001
        self.assertEqual(self.confirm(row, record), [])

    def test_reading_time_extension_uses_preserved_audio_end(self):
        row, record = self.sample()
        row.update(reading_time_source_end=row['end'], end=14.)
        self.assertEqual(self.confirm(row, record), [7])
        row.pop('reading_time_source_end')
        self.assertEqual(self.confirm(row, record), [])

    def test_no_word_trimming_or_spelling_aliases_are_used_for_a_replacement(self):
        row, record = self.sample()
        leading_word(record)
        self.assertEqual(self.confirm(row, record), [])
        row, record = self.sample()
        record['readings'][2]['text'] = 'We are goin’ home.'
        self.assertEqual(self.confirm(row, record), [])

    def test_a_replacement_outside_the_requested_ids_does_not_confirm(self):
        row, record = self.sample()
        self.assertEqual(confirmed_by_reread([row], [record], {8}), [])


class ConfirmationComparisonTests(unittest.TestCase):
    def confirm(self, row, record):
        return confirmed_by_reread([row], [record], {7})

    def test_a_word_before_the_row_does_not_make_its_readers_disagree(self):
        row, record = sample()
        leading_word(record)
        before = copy.deepcopy((row, record))
        proofs = []
        self.assertEqual(confirmed_by_reread([row], [record], {7}, comparisons=proofs), [7])
        self.assertEqual(proofs[0]['readings'][0]['excluded_words'][0]['word'], 'Well, ')
        self.assertEqual((row, record), before)

    def test_an_extra_word_inside_the_row_stays_a_disagreement(self):
        row, record = sample()
        leading_word(record, start=10., end=10.1)
        self.assertEqual(self.confirm(row, record), [])

    def test_a_word_ending_at_the_start_is_outside_even_within_the_midpoint_margin(self):
        row, record = sample()
        leading_word(record, start=9.9, end=10.)
        self.assertEqual(self.confirm(row, record), [7])

    def test_a_word_starting_at_the_end_is_outside_even_within_the_midpoint_margin(self):
        row, record = sample()
        leading_word(record)
        reading = record['readings'][0]
        reading['text'] += ' Thanks.'
        reading['words'].append(dict(word=' Thanks.', start=12., end=12.1))
        self.assertEqual(self.confirm(row, record), [7])

    def test_an_overlapping_word_or_negation_is_not_discarded_at_either_edge(self):
        for side in ('before', 'after'):
            row, record = sample()
            if side == 'before':
                leading_word(record, start=9.9, end=10.01, word='Not')
            else:
                leading_word(record)
                reading = record['readings'][0]
                reading['text'] += ' Not.'
                reading['words'].append(dict(word=' Not.', start=11.99, end=12.1))
            self.assertEqual(self.confirm(row, record), [], side)

    def test_float_roundoff_is_tolerated_but_positive_overlap_is_not(self):
        row, record = sample()
        leading_word(record, start=9.9, end=10.+5e-10)
        self.assertEqual(self.confirm(row, record), [7])
        record['readings'][0]['words'][0]['end'] = 10.00001
        self.assertEqual(self.confirm(row, record), [])

    def test_an_untimed_extra_word_cannot_be_ignored(self):
        row, record = sample()
        record['readings'][2]['text'] = 'Well, ' + row['source']
        self.assertEqual(self.confirm(row, record), [])

    def test_empty_zero_nan_reversed_or_incomplete_word_times_cannot_remove_words(self):
        for kind in ('missing', 'zero', 'nan', 'reverse', 'text_mismatch', 'overlap', 'too_wide'):
            with self.subTest(kind=kind):
                row, record = sample()
                leading_word(record)
                words = record['readings'][0]['words']
                if kind == 'missing':
                    words[0].pop('start')
                elif kind == 'zero':
                    words[0]['end'] = words[0]['start']
                elif kind == 'nan':
                    words[0]['start'] = float('nan')
                elif kind == 'reverse':
                    words[0]['start'] = 9.9
                elif kind == 'text_mismatch':
                    words.pop()
                elif kind == 'overlap':
                    words[1]['end'] = 11.
                else:
                    words[0]['start'] = 9.
                self.assertEqual(self.confirm(row, record), [])

    def test_a_word_straddling_the_row_cannot_be_discarded_as_a_neighbour(self):
        row, record = sample()
        leading_word(record, start=9.7, end=10.01)
        self.assertEqual(self.confirm(row, record), [])

    def test_display_time_does_not_admit_the_following_rows_word(self):
        row, record = sample()
        row.update(end=13., reading_time_source_end=12.)
        reading = record['readings'][0]
        reading['text'] += ' Thanks.'
        reading['words'] = [dict(word=w+' ', start=10.+i*.4, end=10.3+i*.4)
                            for i, w in enumerate(record['current'].split())]
        reading['words'].append(dict(word='Thanks.', start=12.2, end=12.3))
        self.assertEqual(self.confirm(row, record), [7])

    def test_explicit_english_spellings_confirm_without_changing_the_source(self):
        for source, heard in [('We are going home.', "We are goin' home."),
                              ('Because it works.', "'Cause it works."),
                              ('She is working here.', 'She is workin’ here.')]:
            with self.subTest(source=source):
                row, record = sample(source)
                record['readings'][1]['text'] = heard
                self.assertEqual(self.confirm(row, record), [7])
                self.assertEqual(row['source'], source)

    def test_spelling_rules_do_not_expand_other_words_names_or_ambiguous_contractions(self):
        for source, heard in [('What is the cause?', 'What is the because?'),
                              ('We are going home.', 'We are Goin home.'),
                              ('I want a ticket.', 'I wanna ticket.'),
                              ('We have got a car.', 'We have gotta car.'),
                              ("We're leaving.", 'Were leaving.')]:
            with self.subTest(source=source):
                row, record = sample(source)
                record['readings'][2]['text'] = heard
                self.assertEqual(self.confirm(row, record), [])

    def test_spelling_rules_need_english_evidence_and_no_language_disagreement(self):
        for languages in (['de']*3, [None]*3, ['en', 'de', 'English']):
            row, record = sample()
            record['readings'][1]['text'] = "We are goin' home."
            for reading, language in zip(record['readings'], languages):
                reading['language'] = language
            self.assertEqual(self.confirm(row, record), [])

    def test_a_third_reader_with_another_word_or_negation_is_not_outvoted(self):
        for other in ('We are not going home.', 'We are going there.'):
            row, record = sample()
            record['readings'][0]['text'] = "We are goin' home."
            record['readings'][2]['text'] = other
            self.assertEqual(self.confirm(row, record), [])

    def test_stale_source_times_or_replacement_records_do_not_gain_confirmation(self):
        for kind in ('source', 'times', 'replacement'):
            row, record = sample()
            leading_word(record)
            if kind == 'source':
                row['source'] = 'We are going there.'
            elif kind == 'times':
                row['end'] = 11.8
            else:
                record['decision'] = 'ersatt'
            self.assertEqual(self.confirm(row, record), [])

    def test_one_family_or_duplicate_readers_cannot_supply_proof(self):
        row, record = sample()
        leading_word(record)
        record['readings'] = [record['readings'][0]] * 2
        self.assertIsNone(confirmation_comparison(row, record))
        self.assertEqual(self.confirm(row, record), [])


if __name__ == '__main__':
    unittest.main()
