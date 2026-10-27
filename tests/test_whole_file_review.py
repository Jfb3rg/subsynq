import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker


def rows(count):
    return [{'id': n, 'start': n * 2, 'end': n * 2 + 1.5, 'source': f'Line number {n} here.', 'sv': f'Rad nummer {n} här.'}
            for n in range(count)]


def flag_for(row):
    return {'id': row['id'], 'category': 'meaning', 'source_quote': row['source'][:14], 'target_quote': row['sv'][:12],
            'reason': f'Betydelsen ändras i rad {row["id"]}: en konkret förklaring av vad som gick förlorat.'}


class WholeFileReviewTests(unittest.TestCase):
    """Yukon Men, a 44-minute episode (899 cues), was stopped twice at a block with 3 flagged cues of 12 after 12 of 75 blocks: the rest was never judged.
    A Swedish file is now reviewed in full and its flags are counted against the whole file."""

    def setUp(self):
        for patcher in (patch.object(worker, 'FOCUSED_REVIEW', True), patch.object(worker, 'release_language_model_before_asr')):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def reviewer(flagged):
        calls = []

        def answer(request):
            sample = json.loads(request['prompt'])['rows']
            calls.append([row['id'] for row in sample])
            bad = [row for row in sample if row['id'] in flagged]
            return {'response': json.dumps({
                'comparisons': [{'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'],
                                 'meaning_preserved': row['id'] not in flagged} for row in sample],
                'flags': [flag_for(row) for row in bad]})}
        return answer, calls

    def test_a_block_with_three_flagged_cues_no_longer_stops_the_file_and_every_block_is_reviewed(self):
        flagged = {85, 87, 89}                      # 3 of the 12 cues 84-95, as in the episode
        answer, calls = self.reviewer(flagged)
        audit = []
        with patch.object(worker, 'call_ollama', side_effect=answer):
            self.assertTrue(worker.assess_translation_quality(rows(300), audit=audit, whole_file=True))
        self.assertEqual({row_id for entry in audit for flag in entry.get('flags', []) for row_id in [flag['id']]}, flagged)
        self.assertEqual({row_id for call in calls for row_id in call}, set(range(300)), 'every cue was reviewed')

    def test_the_old_rule_is_unchanged_without_whole_file_for_example_for_the_english_pass(self):
        answer, calls = self.reviewer({85, 87, 89})
        with patch.object(worker, 'call_ollama', side_effect=answer), self.assertRaisesRegex(worker.SourceQualityError, '3 av 12'):
            worker.assess_translation_quality(rows(300), audit=[])
        self.assertLess(max(row_id for call in calls for row_id in call), 100, 'it stopped at the failing block')

    def test_a_pervasively_broken_file_still_stops_early(self):
        answer, calls = self.reviewer(set(range(0, 300, 2)))     # every other cue
        with patch.object(worker, 'call_ollama', side_effect=answer), self.assertRaisesRegex(worker.SourceQualityError, 'granskade repliker är tydligt trasiga'):
            worker.assess_translation_quality(rows(300), audit=[], whole_file=True)
        self.assertLess(max(row_id for call in calls for row_id in call), 60)

    def test_a_few_scattered_flags_in_a_long_file_are_not_pervasive(self):
        answer, _ = self.reviewer({3, 90, 200})
        with patch.object(worker, 'call_ollama', side_effect=answer):
            self.assertTrue(worker.assess_translation_quality(rows(300), audit=[], whole_file=True))


class ReviewOnlyFlaggedWindowsTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.object(worker, 'FOCUSED_REVIEW', True), patch.object(worker, 'release_language_model_before_asr')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_windows(self, only_ids, count=300):
        answer, calls = WholeFileReviewTests.reviewer(set())
        with patch.object(worker, 'call_ollama', side_effect=answer):
            result = worker.assess_translation_quality(rows(count), audit=[], whole_file=True, only_ids=only_ids)
        return result, calls

    def test_a_repaired_cue_is_reviewed_with_its_neighbours_only(self):
        _, calls = self.run_windows({150})
        self.assertEqual(calls, [[149, 150, 151]])

    def test_separate_windows_are_separate_contiguous_blocks_and_neighbouring_ones_are_joined(self):
        _, calls = self.run_windows({20, 150, 152})
        self.assertEqual(calls, [[19, 20, 21], [149, 150, 151, 152, 153]])

    def test_the_edges_of_the_file_are_clipped(self):
        _, calls = self.run_windows({0, 299})
        self.assertEqual(calls, [[0, 1], [298, 299]])

    def test_a_long_run_is_split_into_blocks_of_twelve_with_two_cues_of_overlap(self):
        _, calls = self.run_windows(set(range(40, 60)))
        self.assertEqual(calls, [list(range(39, 51)), list(range(49, 61))])      # cues 39-60: two blocks of twelve, two cues shared

    def test_nothing_to_review_is_a_clean_pass_without_any_model_call(self):
        result, calls = self.run_windows(set())
        self.assertTrue(result)
        self.assertEqual(calls, [])


class FlowTests(unittest.TestCase):
    """review_and_save_translation with a scripted review: what the second review covers and what counts against the file's limit."""

    def run_flow(self, count, first, second, *, final=False, categories=None, spoken=(), soft=False):
        source = rows(count)
        job = {'id': 9, 'spoken_swedish_ids': list(spoken)}
        seen = {'reviews': []}
        categories = categories or {}
        default = 'fragment' if soft else 'meaning'      # only a cut-off source (soft) may be kept in the last attempt; a real defect is repaired again or refused

        def flags(ids):
            return [{**flag_for(source[i]), 'category': categories.get(i, default)} for i in ids]

        def assess(candidate, *, audit=None, **kwargs):
            seen['reviews'].append(kwargs)
            call = len(seen['reviews'])
            ids = first if call == 1 else (second[min(call - 2, len(second) - 1)] if second and isinstance(second[0], list) else second)
            audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': list(ids), 'flags': flags(ids)})

        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'assess_translation_quality', side_effect=assess), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'repair_flagged_translation', side_effect=lambda job_, rows_, fl, **kw: [dict(row) for row in rows_]) as repair, \
                patch('fragment_context_review.review_fragments', return_value=[]):
            try:
                worker.review_and_save_translation(job, source, final=final)
                error = None
            except worker.SourceQualityError as raised:
                error = raised
            saved = json.loads((Path(folder) / 'job-9.json').read_text(encoding='utf-8'))
        return error, saved, job, seen, repair

    def test_a_long_file_is_reviewed_in_full_then_re_reviewed_at_the_flagged_cues_and_then_confirmed_in_full(self):
        _, _, _, seen, repair = self.run_flow(300, [10, 90, 200], [], final=True)
        self.assertEqual([(call['whole_file'], call.get('only_ids')) for call in seen['reviews']], [(True, None), (True, {10, 90, 200}), (True, None)])
        self.assertEqual({flag['id'] for flag in repair.call_args.args[2]}, {10, 90, 200})

    def test_a_short_file_is_re_reviewed_in_full_straight_away_as_before(self):
        _, _, _, seen, _ = self.run_flow(40, [3], [], final=True)
        self.assertEqual([(call['whole_file'], call.get('only_ids')) for call in seen['reviews']], [(True, None), (True, None)])

    def test_what_the_quick_check_misses_the_full_confirmation_still_finds(self):
        error, saved, job, seen, _ = self.run_flow(300, [10], [[], [77]], final=False)
        self.assertIsNotNone(error, 'a cue the full review flags after the repair fails the attempt, the quick check was clean')
        self.assertEqual(len(seen['reviews']), 3)
        error, saved, job, _, _ = self.run_flow(300, [10], [[], [77]], final=True, soft=True)
        self.assertIsNone(error)
        self.assertEqual([item['id'] for item in saved['doubtful']], [77], 'in the last attempt a single one is kept and reported')
        self.assertIn('final_review', saved)

    def test_a_quick_check_that_already_reaches_the_limit_fails_without_the_full_review(self):
        error, _, _, seen, _ = self.run_flow(300, [10], [[100 + 12 * i for i in range(15)]], final=True)
        self.assertIsNotNone(error)
        self.assertEqual(len(seen['reviews']), 2)

    def test_the_english_pass_keeps_the_old_complete_review(self):
        source = [{**row, 'en': row['sv']} for row in rows(30)]
        calls = []

        def assess(candidate, *, audit=None, **kwargs):
            calls.append(kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), patch.object(worker, 'assess_translation_quality', side_effect=assess):
            worker.review_and_save_translation({'id': 9}, source, target_key='en', target_language='English')
        self.assertEqual(calls, [{'target_key': 'en', 'target_language': 'English', 'whole_file': False}])

    def test_remaining_findings_count_against_the_whole_file_not_a_block(self):
        limit = worker.doubtful_limit(300)
        self.assertEqual(limit, 15)
        error, saved, job, _, _ = self.run_flow(300, list(range(10, 100, 7)), [10 + 15 * i for i in range(14)], final=True, soft=True)
        self.assertIsNone(error, '14 remaining of 300 cues is below the limit of 15, in the last attempt')
        self.assertEqual(len(saved['doubtful']), 14)
        error, _, _, _, _ = self.run_flow(300, list(range(10, 100, 7)), [10 + 15 * i for i in range(15)], final=True, soft=True)
        self.assertIsNotNone(error, '15 remaining reaches the limit')
        error, _, _, _, _ = self.run_flow(300, list(range(10, 100, 7)), [10], final=False, soft=True)
        self.assertIsNotNone(error, 'the first attempt still fails so that the refused wordings get their one new translation')

    def test_a_cue_with_a_damaged_source_is_not_repaired_but_counts_against_the_limit(self):
        error, saved, _, seen, repair = self.run_flow(300, [10, 50], [], final=True, categories={50: 'source_uncertain'})
        self.assertIsNone(error)
        self.assertEqual({flag['id'] for flag in repair.call_args.args[2]}, {10}, 'the uncertain source is never sent to the repair')
        self.assertEqual([item['id'] for item in saved['doubtful']], [50])
        self.assertEqual(seen['reviews'][1].get('only_ids'), {10})
        self.assertIsNone(seen['reviews'][2].get('only_ids'), 'and the file is confirmed in full')

    def test_only_damaged_sources_flagged_means_no_repair_and_the_same_limit(self):
        error, saved, _, _, repair = self.run_flow(300, [50], [], final=True, categories={50: 'source_uncertain'})
        self.assertIsNone(error)
        repair.assert_not_called()
        self.assertEqual([item['id'] for item in saved['doubtful']], [50])
        error, _, _, _, _ = self.run_flow(300, [50], [], final=False, categories={50: 'source_uncertain'})
        self.assertIsNotNone(error, 'not the last attempt: the translation gets its one new try first')

    def test_a_pervasive_failure_in_the_first_review_stops_before_any_repair(self):
        source = rows(60)

        def assess(candidate, **kwargs):
            raise worker.SourceQualityError('pervasivt trasig')
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'assess_translation_quality', side_effect=assess), patch.object(worker, 'repair_flagged_translation') as repair:
            with self.assertRaisesRegex(worker.SourceQualityError, 'pervasivt'):
                worker.review_and_save_translation({'id': 9}, source)
        repair.assert_not_called()


class ClusterTests(unittest.TestCase):
    """Yukon Men, the delivered subtitle: cues 702-718 were each translated as the neighbouring line ("We think we're within a couple of miles of them." became
    "Det här snöfallet hjälper oss se deras spår."). 36 flagged of 899 stayed under the file-wide limit and the shifted stretch was written. A cluster of flagged
    cues in one block (three of twelve, the old block rule) is repaired by translating the block afresh, or the file is refused."""

    def test_the_blocks_with_a_cluster_are_found_on_the_review_grid(self):
        flags = [{'id': i} for i in (60, 61, 85, 87, 89, 701, 702, 703, 712, 713, 714, 715, 716, 717, 718)]
        found = worker.cluster_windows(flags, rows(899))
        self.assertEqual([window['flagged'] for window in found], [[85, 87, 89], [701, 702, 703], [712, 713, 714, 715, 716, 717, 718]])
        self.assertEqual([(window['ids'][0], window['ids'][-1]) for window in found], [(80, 91), (700, 711), (710, 721)])

    def test_scattered_flags_make_no_cluster_however_many_there_are(self):
        self.assertEqual(worker.cluster_windows([{'id': i} for i in range(0, 300, 7)], rows(300)), [])

    def test_a_short_block_needs_two(self):
        self.assertEqual(len(worker.cluster_windows([{'id': 1}, {'id': 3}], rows(5))), 1)
        self.assertEqual(worker.cluster_windows([{'id': 1}], rows(5)), [])

    def flow(self, remaining_after_retranslation, *, count=300, first=None, full=None, categories=None):
        source = rows(count)
        cluster = [10, 11, 12]
        seen = {'translate': [], 'reviews': []}
        categories = categories or {}

        def flags(ids):
            return [{**flag_for(source[i]), 'category': categories.get(i, 'meaning')} for i in ids]

        def assess(candidate, *, audit=None, **kwargs):
            seen['reviews'].append(kwargs)
            call = len(seen['reviews'])
            if call == 1:
                ids = first if first is not None else cluster
            elif kwargs.get('only_ids') is not None and call >= 4:
                ids = remaining_after_retranslation           # the re-review of the retranslated block
            else:
                ids = full if full is not None else cluster
            audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': list(ids), 'flags': flags(ids)})

        def fake_translate(job, part, **kwargs):
            seen['translate'].append((sorted(row['id'] for row in part), kwargs.get('refused')))
            return [{**row, 'sv': f'Ny rad {row["id"]}.'} for row in part]

        job = {'id': 9, 'spoken_swedish_ids': []}
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'assess_translation_quality', side_effect=assess), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'repair_flagged_translation', side_effect=lambda job_, rows_, fl, **kw: [dict(row) for row in rows_]), \
                patch.object(worker, 'translate', side_effect=fake_translate), patch('fragment_context_review.review_fragments', return_value=[]):
            try:
                worker.review_and_save_translation(job, source, final=True)
                error = None
            except worker.SourceQualityError as raised:
                error = raised
            saved = json.loads((Path(folder) / 'job-9.json').read_text(encoding='utf-8'))
        return error, saved, seen, source

    def test_a_cluster_is_translated_afresh_as_a_block_and_the_file_passes_when_it_is_clean_after_that(self):
        error, saved, seen, source = self.flow([])
        self.assertIsNone(error)
        self.assertEqual(len(seen['translate']), 1)
        ids, refused = seen['translate'][0]
        self.assertTrue(set(range(10, 13)) <= set(ids) and len(ids) <= 14, ids)
        self.assertEqual(refused[10], ['Rad nummer 10 här.'], 'the refused wording is forbidden')
        self.assertEqual(source[11]['sv'], 'Ny rad 11.', 'the file now holds the new translation')
        self.assertEqual(saved['cluster_retranslation'][0]['before']['10'], 'Rad nummer 10 här.')
        self.assertIn('cluster_review', saved)
        self.assertNotIn('doubtful', saved)

    def test_a_cluster_that_is_still_there_refuses_the_file_even_though_the_count_is_far_below_the_limit(self):
        error, saved, seen, _ = self.flow([10, 11, 12])
        self.assertIsNotNone(error)
        self.assertIn('3 av 12', str(error))
        self.assertIn('också efter att blocket översatts om', str(error))
        self.assertEqual(saved['status'], 'unusable')

    def test_a_cluster_of_damaged_sources_cannot_be_helped_by_a_new_translation(self):
        error, saved, seen, _ = self.flow([], categories={10: 'source_uncertain', 11: 'source_uncertain', 12: 'source_uncertain'},
                                          first=[10, 11, 12], full=[10, 11, 12])
        self.assertIsNotNone(error)
        self.assertEqual(seen['translate'], [])
        self.assertIn('3 av 12', str(error))

    def test_flags_that_are_not_a_cluster_pass_as_before_and_nothing_is_translated_again(self):
        soft = {10: 'fragment', 80: 'fragment', 150: 'fragment'}
        error, saved, seen, _ = self.flow([], first=[10, 80, 150], full=[10, 80, 150], categories=soft)
        self.assertIsNone(error)
        self.assertEqual(seen['translate'], [])
        self.assertEqual(len(saved['doubtful']), 3)


class ApprovalsTests(unittest.TestCase):
    """A normal reviewer event (an invalid answer, then the block split in halves) used to cost the whole file its approvals: the retry then translated
    every cue again (Yukon Men: 25 batches instead of 2, about 10 minutes on a rented GPU)."""

    def test_an_invalid_answer_and_its_split_do_not_cancel_the_approvals_of_the_rest(self):
        source = rows(6)
        translated = [dict(row) for row in source]
        comparisons = lambda ids: [{'id': i, 'source_meaning': 'a', 'target_meaning': 'b', 'meaning_preserved': True} for i in ids]
        report = {'focused_review': True, 'rows': translated, 'repair_reviews': [],
                  'reviews': [{'ids': [0, 1, 2, 3], 'status': 'reviewer_error', 'attempt': 1, 'reason': 'ogiltigt', 'verdict': None},
                              {'ids': [0, 1, 2, 3], 'status': 'split', 'reason': 'delas'},
                              {'ids': [0, 1], 'bad_ids': [], 'flags': [], 'comparisons': comparisons([0, 1])},
                              {'ids': [2, 3], 'bad_ids': [3], 'flags': [{'id': 3}], 'comparisons': comparisons([2, 3])},
                              {'ids': [4, 5], 'bad_ids': [], 'flags': [], 'comparisons': comparisons([4, 5])}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)):
            (Path(folder) / 'job-9.json').write_text(json.dumps(report), encoding='utf-8')
            approved = worker.approved_retry_rows({'id': 9}, source, translated)
        self.assertEqual(sorted(approved), [0, 1, 2, 4, 5], 'everything judged and not flagged is kept; only cue 3 is translated again')


if __name__ == '__main__':
    unittest.main()
