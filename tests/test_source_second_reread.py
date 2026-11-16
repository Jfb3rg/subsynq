import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import reread
import worker


def rows(count):
    return [{'id': index, 'start': index * 3.0, 'end': index * 3.0 + 2.0, 'source': f'This is line number {index}.'} for index in range(count)]


def record(row, texts):
    return {'id': row['id'], 'current': row['source'],
            'readings': [{'name': name, 'text': text, 'confidence': .8} for name, text in zip(('precision', 'v2', 'qwen'), texts)]}


class ConfirmedByRereadTests(unittest.TestCase):
    def test_only_exactly_the_same_words_from_every_family_confirm_a_row(self):
        sample = rows(6)
        records = [record(sample[0], [sample[0]['source']] * 3),                                   # every family: the same words
                   record(sample[1], ['This is line number 1', 'this is line number 1.']),          # punctuation and case are not words
                   record(sample[2], [sample[2]['source'], sample[2]['source'], 'This is fine number 2.']),   # one family hears another word
                   record(sample[3], [sample[3]['source']]),                                        # one family is no proof
                   record(sample[4], ['Something else entirely was said here.'] * 3),
                   record({**sample[5], 'source': 'An older text of the row.'}, ['An older text of the row.'] * 3)]   # stale record
        self.assertEqual(reread.confirmed_by_reread(sample, records, {0, 1, 2, 3, 4, 5}), [0, 1])
        self.assertEqual(reread.confirmed_by_reread(sample, records, {1, 2}), [1])
        self.assertEqual(reread.confirmed_by_reread(sample, [], {0}), [])


class SecondRereadTests(unittest.TestCase):
    def test_applied_unanimous_replacement_clears_only_the_list_and_is_rechecked_at_the_end(self):
        for late in (False, True):
            for conflict in (False, True):
                with self.subTest(late=late, conflict=conflict):
                    sample = rows(20)
                    job = {'id': 5}
                    target = 7 if late else 2

                    def read(job, source, ids, **kwargs):
                        job['reread_rows'] = []
                        if target not in ids:
                            return []
                        row = source[target]
                        text = 'Another sentence was heard here.'
                        evidence = record(row, [text] * 3)
                        evidence.update(decision='ersatt', candidate=text, start=row['start'], end=row['end'])
                        job['reread_rows'].append(evidence)
                        row['source'] = text
                        return [target]

                    findings = [[2], [2, 7]] if late else [[2], [2]]
                    with self.audit(findings), patch.object(worker, 'reread_flagged_rows', side_effect=read), \
                            patch.object(worker, 'update_job'), patch.object(worker, 'REREAD_ENABLED', True), \
                            patch.object(worker, 'SECOND_REREAD_ENABLED', True), \
                            patch.object(worker, 'unresolved_source_choice_ids', return_value=[9] if conflict else []):
                        unclear = worker.assess_source_with_reread(job, sample)
                    self.assertEqual(unclear, sorted(({2, 7} if late else {2}) | ({9} if conflict else set())))
                    self.assertEqual(job['source_reread']['still_unclear'], unclear)
                    self.assertEqual(job['source_reread']['unresolved_source_choices'], [9] if conflict else [])
                    expected = [target]
                    self.assertEqual(job['source_reread']['confirmed_by_reread'], expected)
                    # The final report must recompute against the actual source, not trust saved ids.
                    ids = set(job['source_reread']['confirmed_by_reread'])
                    self.assertEqual(reread.confirmed_by_reread(sample, job['source_reread']['readings'], ids), expected)
                    sample[target]['source'] += ' Never.'
                    self.assertEqual(reread.confirmed_by_reread(sample, job['source_reread']['readings'], ids), [])

    def test_timed_edge_proof_can_clear_the_list_but_not_a_raw_conflict_or_source_choice(self):
        for choice in (False, True):
            sample = rows(20)
            sample[2]['source'] = 'Go now.'
            job = {'id': 5}

            def read(job, source, ids, **kwargs):
                row = source[2]
                evidence = record(row, ['Please, go now.', 'Go now.', 'Go now.'])
                evidence.update(start=row['start'], end=row['end'])
                evidence['readings'][0]['words'] = [dict(word='Please, ', start=5.9, end=6.),
                    dict(word='go ', start=6., end=6.2), dict(word='now.', start=6.4, end=6.7)]
                job['reread_rows'] = [evidence]
                return []

            with self.audit([[2], [2]]), patch.object(worker, 'reread_flagged_rows', side_effect=read), \
                    patch.object(worker, 'update_job'), patch.object(worker, 'REREAD_ENABLED', True), \
                    patch.object(worker, 'unresolved_source_choice_ids', return_value=[2] if choice else []):
                self.assertEqual(worker.assess_source_with_reread(job, sample), [2])
            self.assertEqual(job['source_reread']['still_unclear'], [2])
            self.assertEqual([r['id'] for r in job['source_reread']['unresolved_conflicts']], [2])
            self.assertEqual(job['source_reread']['confirmed_by_reread'], [] if choice else [2])
            self.assertEqual(job['source_reread']['unresolved_source_choices'], [2] if choice else [])

    def test_time_comparison_changes_the_list_but_not_the_audit_count_or_raw_readings(self):
        sample = rows(20)
        sample[2]['source'] = 'Because we need more time.'
        job = {'id': 5}

        def read(job, source, ids, **kwargs):
            row = source[2]
            evidence = record(row, [row['source'], row['source'], "'Cause we need more time."])
            evidence.update(start=row['start'], end=row['end'])
            for reading in evidence['readings']:
                reading['language'] = 'English'
            job['reread_rows'] = [evidence]
            return []

        with self.audit([[2], [2]]), patch.object(worker, 'reread_flagged_rows', side_effect=read), \
                patch.object(worker, 'update_job'), patch.object(worker, 'REREAD_ENABLED', True):
            self.assertEqual(worker.assess_source_with_reread(job, sample), [2])
        self.assertEqual(job['source_reread']['still_unclear'], [2])
        self.assertEqual(job['source_reread']['confirmed_by_reread'], [2])
        self.assertEqual(job['source_reread']['confirmation_comparisons'][0]['id'], 2)
        self.assertEqual(job['source_reread']['readings'][0]['readings'][2]['text'], "'Cause we need more time.")
        self.assertEqual(sample[2]['source'], 'Because we need more time.')

    def audit(self, verdicts):
        """Each call of the whole-file audit names the next list of ids."""
        calls = iter(verdicts)
        return patch.object(worker, '_audit_source_chunk', side_effect=lambda *a, **k: (next(calls), False, 'otydligt'))

    def run_audit(self, sample, verdicts, heard, **switches):
        job = {'id': 5}
        asked = []

        def read(job, rows, ids, **kwargs):
            asked.append(list(ids))
            job['reread_rows'] = [record(row, heard[row['id']]) for row in rows if row['id'] in ids and row['id'] in heard]
            return []
        with self.audit(verdicts), patch.object(worker, 'reread_flagged_rows', side_effect=read), patch.object(worker, 'update_job'), \
                patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'SECOND_REREAD_ENABLED', switches.get('second', True)), \
                patch.object(worker, 'SECOND_REREAD_MAXIMUM_ROWS', switches.get('maximum', 60)):
            unclear = worker.assess_source_with_reread(job, sample)
        return job, unclear, asked

    def test_rows_only_the_second_audit_names_are_read_again_and_all_records_are_kept(self):
        sample = rows(20)
        heard = {2: [sample[2]['source']] * 3, 7: [sample[7]['source']] * 3, 9: ['This is fine number 9.', 'This was line number 9.', 'It is line 9.']}
        job, unclear, asked = self.run_audit(sample, [[2], [2, 7, 9]], heard)
        self.assertEqual(asked, [[2], [7, 9]])
        self.assertEqual(unclear, [2, 7, 9])                       # the gate and the unclear mark see every row, as before
        self.assertEqual([item['id'] for item in job['source_reread']['readings']], [2, 7, 9])
        self.assertEqual(job['source_reread']['late_flagged'], [7, 9])
        self.assertEqual(job['source_reread']['not_read_again'], [])
        self.assertEqual(job['source_reread']['confirmed_by_reread'], [2, 7])
        self.assertEqual(job['reread_rows'], job['source_reread']['readings'])

    def test_the_second_round_can_be_switched_off_and_has_a_maximum(self):
        sample = rows(20)
        heard = {index: [sample[index]['source']] * 3 for index in range(20)}
        job, unclear, asked = self.run_audit(sample, [[2], [2, 7, 9]], heard, second=False)
        self.assertEqual((asked, unclear), ([[2]], [2, 7, 9]))
        self.assertEqual(job['source_reread']['not_read_again'], [7, 9])
        self.assertEqual(job['source_reread']['confirmed_by_reread'], [2])
        job, unclear, asked = self.run_audit(sample, [[2], [2, 7, 9]], heard, maximum=1)
        self.assertEqual((asked, unclear), ([[2], [7]], [2, 7, 9]))
        self.assertEqual(job['source_reread']['not_read_again'], [9])
        self.assertEqual(worker.source_unclear_reason(job, 9), worker.SOURCE_UNCLEAR_NOT_READ_REASON)
        self.assertEqual(worker.source_unclear_reason(job, 7), worker.SOURCE_UNCLEAR_REASON)

    def test_the_refusal_gate_counts_confirmed_rows_and_runs_before_the_second_round(self):
        sample = rows(6)
        heard = {index: [sample[index]['source']] * 3 for index in range(6)}
        with self.assertRaises(worker.BrokenSourceRows):
            self.run_audit(sample, [[0], [0, 1, 2]], heard)

    def test_a_row_replaced_in_the_second_round_stays_listed_with_what_was_done(self):
        sample = rows(20)
        job = {'id': 5}

        def read(job, rows, ids, **kwargs):
            job['reread_rows'] = [record(row, ['Another sentence was heard here.'] * 3) for row in rows if row['id'] in ids]
            if ids == [7]:
                rows[7]['source'] = 'Another sentence was heard here.'
                return [7]
            return []
        with self.audit([[2], [2, 7]]), patch.object(worker, 'reread_flagged_rows', side_effect=read), patch.object(worker, 'update_job'), \
                patch.object(worker, 'REREAD_ENABLED', True):
            self.assertEqual(worker.assess_source_with_reread(job, sample), [2, 7])
        self.assertEqual(job['source_reread']['late_changed'], [7])
        self.assertEqual(job['source_reread']['confirmed_by_reread'], [])
        self.assertEqual(worker.source_unclear_reason(job, 7), worker.SOURCE_UNCLEAR_REPLACED_REASON)


if __name__ == '__main__':
    unittest.main()
