import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker


def rows(count):
    return [{'id': index, 'start': index * 3.0, 'end': index * 3.0 + 2.0, 'source': f'line {index}'} for index in range(count)]


class SourceRereadTests(unittest.TestCase):
    def test_actual_source_chooser_retains_its_rejected_error_for_the_audit(self):
        sample = [{'id': 0, 'start': 1.15, 'end': 4.31, 'source': 'stjel kjølet!',
                   'alternatives': [{'text': 'Menesthjelpe til kjølre!', 'support': 1, 'current_support': 1}]}]
        job = {'id': 246}
        answer = {'response': json.dumps({'rows': [{'id': 0, 'a_has_error': True, 'choice': 'B'}]})}
        with patch.object(worker, 'call_ollama', return_value=answer), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            chosen, decisions = worker.choose_between_readings(job, sample)
        self.assertEqual(chosen[0]['source'], 'stjel kjølet!')
        self.assertFalse(decisions[0]['applied'])
        self.assertEqual(worker.unresolved_source_choice_ids(chosen, job['source_choices']), [0])
        # Reusing a job with a fresh, clean source must not carry the old verdict.
        worker.choose_between_readings(job, [{'id': 0, 'source': 'A clean source.'}])
        self.assertEqual(job['source_choices'], [])

    def test_rejected_source_choice_is_not_forgotten_by_two_clean_audits(self):
        sample = rows(14)
        sample[0]['source'] = 'stjel kjølet!'
        decision = {'id': 0, 'from': sample[0]['source'], 'to': 'Menesthjelpe til kjølre!',
                    'a_has_error': True, 'applied': False, 'choice': 'B'}
        for record, expected in (
                (None, [0]),
                ({'id': 0, 'current': sample[0]['source'], 'readings': [
                    {'name': 'precision', 'text': sample[0]['source']},
                    {'name': 'v2', 'text': sample[0]['source']}]}, []),
                ({'id': 0, 'current': sample[0]['source'], 'readings': [
                    {'name': 'precision', 'text': sample[0]['source']}]}, [0])):
            job = {'id': 246, 'source_choices': [decision]}
            def reread(job, *args):
                job['reread_rows'] = [record] if record else []
                return []
            with patch.object(worker, '_audit_source_chunk', return_value=([], False, '')), \
                    patch.object(worker, 'reread_flagged_rows', side_effect=reread) as called, patch.object(worker, 'update_job'):
                self.assertEqual(worker.assess_source_with_reread(job, sample), expected)
                self.assertEqual(called.call_args.args[2], [0])
            self.assertEqual(job['source_reread']['source_choice_findings'], [0])

    def test_source_choice_findings_require_an_actual_unchanged_error(self):
        sample = rows(14)
        valid = {'id': 0, 'from': sample[0]['source'], 'to': 'other words',
                 'a_has_error': True, 'applied': False, 'choice': 'B'}
        self.assertEqual(worker.unresolved_source_choice_ids(sample, [valid]), [0])
        for changes in ({'applied': True}, {'a_has_error': False}, {'a_has_error': 'true'},
                        {'from': 'stale source'}, {'id': 99}, {'id': False},
                        {'choice': 'A'}, {'choice': None}, {'choice': 'G'}, {'to': valid['from']}):
            self.assertEqual(worker.unresolved_source_choice_ids(sample, [{**valid, **changes}]), [])

    def test_acoustically_replaced_choice_is_cleared_and_union_keeps_stop_limit(self):
        sample = rows(6)
        choices = [{'id': i, 'from': sample[i]['source'], 'to': 'other words',
                    'a_has_error': True, 'applied': False, 'choice': 'B'} for i in (0, 1, 2)]
        with patch.object(worker, '_audit_source_chunk', return_value=([], False, '')), \
                patch.object(worker, 'reread_flagged_rows', return_value=[0, 1, 2]), patch.object(worker, 'update_job'):
            self.assertEqual(worker.assess_source_with_reread({'id': 1, 'source_choices': choices}, sample), [])
        with patch.object(worker, '_audit_source_chunk', return_value=([], False, '')), \
                patch.object(worker, 'reread_flagged_rows', return_value=[]), patch.object(worker, 'update_job'):
            with self.assertRaises(worker.BrokenSourceRows):
                worker.assess_source_with_reread({'id': 1, 'source_choices': choices}, sample)

    """The user, 2026-10-27 (Godzilla: music under the speech): read unclear rows again, then report what is still unclear; refuse only
    when more than a third of the rows are unclear."""

    def run_audit(self, verdicts, reread_changes=(), count=14, reread_records=()):
        calls = iter(verdicts)
        job = {'id': 223, 'media_path': '/extra/Godzilla.mp4'}
        def read_again(job, *args):
            job['reread_rows'] = list(reread_records)
            return list(reread_changes)
        with patch.object(worker, '_audit_source_chunk', side_effect=lambda *args, **kwargs: next(calls)), \
                patch.object(worker, 'reread_flagged_rows', side_effect=read_again) as reread, \
                patch.object(worker, 'update_job'):
            result = worker.assess_source_with_reread(job, rows(count), 'Godzilla.mp4')
        return result, job, reread

    def test_a_clean_source_is_not_read_again(self):
        result, job, reread = self.run_audit([([], False, '')])
        self.assertEqual(result, [])
        reread.assert_not_called()

    def test_a_single_bad_row_below_the_stop_limit_is_read_and_reported(self):
        result, job, reread = self.run_audit([([5], False, 'Felhört ord.'), ([5], False, 'Felhört ord.')], count=51)
        self.assertEqual(result, [5])
        self.assertEqual(reread.call_args.args[2], [5])
        self.assertEqual(job['source_reread']['still_unclear'], [5])

    def test_a_single_bad_row_can_be_fixed_without_having_to_fail_the_file_first(self):
        result, job, reread = self.run_audit([([5], False, 'trasigt'), ([], False, '')], reread_changes=[5], count=51)
        self.assertEqual(result, [])
        self.assertEqual(job['source_reread']['changed'], [5])
        reread.assert_called_once()

    def test_a_wrong_language_is_not_converted_into_a_rereadable_row_error(self):
        with patch.object(worker, '_audit_source_chunk', return_value=([], True, 'Fel språk.')), \
                patch.object(worker, 'reread_flagged_rows') as reread:
            with self.assertRaises(worker.SourceQualityError):
                worker.assess_source_with_reread({'id': 223}, rows(14), 'German.mp4')
        reread.assert_not_called()

    def test_a_clean_fallback_does_not_inherit_the_previous_source_audit(self):
        job = {'id': 223, 'source_reread': {'flagged': [4], 'still_unclear': [4]}}
        with patch.object(worker, '_audit_source_chunk', return_value=([], False, '')):
            self.assertEqual(worker.assess_source_with_reread(job, rows(14)), [])
        self.assertNotIn('source_reread', job)

    def test_a_short_audit_finding_below_its_limit_is_not_discarded(self):
        job = {'id': 184}
        with patch.object(worker, '_audit_source_chunk', return_value=([], False, '')), \
                patch.object(worker, 'call_ollama', return_value={'response': json.dumps({'bad_ids': [2], 'reason': 'Trasigt ord.'})}), \
                patch.object(worker, 'reread_flagged_rows', return_value=[]) as reread, \
                patch.object(worker, 'update_job'):
            result = worker.assess_source_with_reread(job, rows(6), 'German.mp4')
        self.assertEqual(result, [2])
        self.assertEqual(reread.call_args.args[2], [2])

    def test_godzilla_four_unclear_rows_of_fourteen_are_kept_and_reported(self):
        verdicts = [([4, 5, 8, 12], False, 'Osammanhängande fraser.'), ([4, 5, 8, 12], False, 'Osammanhängande fraser.')]
        result, job, reread = self.run_audit(verdicts)
        self.assertEqual(result, [4, 5, 8, 12])
        self.assertEqual(reread.call_args.args[2], [4, 5, 8, 12])
        self.assertEqual(job['source_reread']['still_unclear'], [4, 5, 8, 12])

    def test_rows_the_reread_fixed_are_no_longer_reported(self):
        verdicts = [([4, 5, 8, 12], False, 'trasigt'), ([4], False, 'trasigt')]
        result, job, _ = self.run_audit(verdicts, reread_changes=[5, 8, 12])
        self.assertEqual(result, [4])
        self.assertEqual(job['source_reread']['changed'], [5, 8, 12])

    def test_more_than_a_third_still_unclear_refuses_the_file(self):
        verdicts = [([1, 2, 3, 4, 5, 6], False, 'trasigt'), ([1, 2, 3, 4, 5], False, 'trasigt')]
        with self.assertRaises(worker.SourceQualityError):
            self.run_audit(verdicts)

    def test_exactly_a_third_is_still_kept(self):
        verdicts = [([0, 1, 2, 3, 4], False, 'trasigt'), ([0, 1, 2, 3, 4], False, 'trasigt')]
        result, _, _ = self.run_audit(verdicts, count=15)
        self.assertEqual(result, [0, 1, 2, 3, 4])

    def test_the_first_audit_keeps_its_old_limit(self):
        with patch.object(worker, '_audit_source_chunk', return_value=([1, 2, 3], False, 'trasigt')):
            with self.assertRaises(worker.BrokenSourceRows) as raised:
                worker.assess_source_quality(rows(12), 'x.mp4')
        self.assertEqual(raised.exception.ids, [1, 2, 3])
        with patch.object(worker, '_audit_source_chunk', return_value=([1, 2], False, 'trasigt')):
            self.assertTrue(worker.assess_source_quality(rows(12), 'x.mp4', lenient=True))


class UnclearRowsInReviewTests(unittest.TestCase):
    def test_a_second_text_verdict_does_not_hide_an_unresolved_acoustic_conflict(self):
        sample = rows(51)
        sample[15]['source'] = 'Bestevennet fra du!'
        # Actual readings in job 229: no source change, but the second source audit forgot this row.
        record = {'id': 15, 'current': sample[15]['source'], 'decision': 'modellfamiljerna läste olika ord',
                  'readings': [{'name': 'precision', 'text': 'Beste wennen voor al die...'},
                               {'name': 'v2', 'text': 'Best event ever, oh yeah!'},
                               {'name': 'qwen', 'text': "Best when it's for you."}]}
        job = {'id': 229}
        def reread(*args):
            job['reread_rows'] = [record]
            return []
        with patch.object(worker, '_audit_source_chunk', side_effect=[([15], False, 'trasigt'), ([], False, '')]), \
                patch.object(worker, 'reread_flagged_rows', side_effect=reread), patch.object(worker, 'update_job'):
            self.assertEqual(worker.assess_source_with_reread(job, sample), [15])
        self.assertEqual(job['source_reread']['still_unclear'], [15])

    def test_combined_findings_still_cannot_exceed_a_third(self):
        sample = rows(6)
        records = [{'id': i, 'current': sample[i]['source'], 'decision': 'modellfamiljerna läste olika ord',
                    'readings': [{'name': 'precision', 'text': 'other one'}, {'name': 'v2', 'text': 'new words'}]}
                   for i in (1, 2)]
        with self.assertRaises(worker.BrokenSourceRows):
            SourceRereadTests().run_audit([([1, 2], False, 'trasigt'), ([3, 4], False, 'trasigt')],
                                        count=6, reread_records=records)

    def test_the_saved_translation_source_keeps_the_actual_reread_and_uncertainty(self):
        sample = [{'id': 0, 'start': 3, 'end': 4, 'source': 'corrected source', 'source_unclear': True},
                  {'id': 1, 'start': 5, 'end': 6, 'source': 'clear source'}]
        job = {'id': 5, 'source_reread': {'flagged': [0], 'changed': [], 'still_unclear': [0]},
               'reread_rows': [{'id': 1, 'current': 'old source', 'candidate': 'clear source', 'decision': 'ersatt'}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            target = worker.save_source_transcript(job, '/extra/clip.mp4', sample, 'CPU', 'Whisper')
            saved = json.loads(target.read_text())
        self.assertEqual(saved['source_reread'], job['source_reread'])
        self.assertEqual(saved['reread_rows'], job['reread_rows'])
        self.assertEqual(saved['rows'], sample)

    def test_source_uncertain_on_a_known_unclear_row_is_not_counted_but_other_findings_are(self):
        sample = [{'id': 4, 'source': '大型災害対策事務局は大撃を行います', 'sv': 'x', 'source_unclear': True},
                  {'id': 5, 'source': '振切れません', 'sv': 'y'}]
        flags = [{'id': 4, 'category': 'source_uncertain'}, {'id': 4, 'category': 'meaning'}, {'id': 5, 'category': 'source_uncertain'}]
        remaining, kept = worker.split_known_unclear_flags(flags, sample)
        self.assertEqual(kept, [{'id': 4, 'category': 'source_uncertain'}])
        self.assertEqual(remaining, [{'id': 4, 'category': 'meaning'}, {'id': 5, 'category': 'source_uncertain'}])

if __name__ == '__main__':
    unittest.main()
