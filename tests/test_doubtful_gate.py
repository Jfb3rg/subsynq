import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import doubtful_report
import server
import worker
from translation_qa import doubtful_limit, doubtful_rows


def rows_of(count, text='Det här är en rad.'):
    return [{'id': number, 'start': number * 3.0, 'end': number * 3.0 + 2.5, 'source': f'This is the source sentence {number}.', 'sv': f'{text} {number}'} for number in range(count)]


def flag(row_id, category='meaning', reason='Betydelsen ändras.'):
    return {'id': row_id, 'category': category, 'reason': reason, 'source_quote': 'x', 'target_quote': 'y'}


class LimitTests(unittest.TestCase):
    def test_at_least_two_or_five_percent_of_the_rows(self):
        self.assertEqual([doubtful_limit(n) for n in (0, 5, 22, 40, 53, 100, 240)], [2, 2, 2, 2, 3, 5, 12])

    def test_the_rows_of_the_report_are_one_per_flagged_row_in_order(self):
        rows = rows_of(5)
        result = doubtful_rows([flag(3, reason=' Fel   ord. '), flag(1), flag(3, reason='igen'), flag(99)], rows)
        self.assertEqual([item['id'] for item in result], [1, 3])
        self.assertEqual(result[1], {'id': 3, 'start': 9.0, 'end': 11.5, 'source': 'This is the source sentence 3.', 'sv': 'Det här är en rad. 3', 'category': 'meaning', 'reason': 'Fel ord.', 'source_cut_off': False})


def run_review(count, remaining_ids, *, final, spoken=(), job=None, category='fragment'):
    rows = rows_of(count)
    job = job if job is not None else {'id': 9, 'spoken_swedish_ids': list(spoken)}

    def assess(candidate, *, audit=None, **kwargs):
        # the first call is the first review (flags the same rows), the second the review of the repaired candidate
        assess.calls += 1
        audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': list(remaining_ids), 'flags': [flag(i, category) for i in remaining_ids]})
    assess.calls = 0
    with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), patch.object(worker, 'assess_translation_quality', side_effect=assess), \
            patch.object(worker, 'repair_flagged_translation', side_effect=lambda job_, rows_, flags, **kwargs: [dict(row) for row in rows_]), patch.object(worker, 'check_cancelled'):
        try:
            worker.review_and_save_translation(job, rows, final=final)
            error = None
        except worker.SourceQualityError as raised:
            error = raised
        saved = json.loads((Path(folder) / 'job-9.json').read_text(encoding='utf-8'))
    return error, saved, job


class FinalGateTests(unittest.TestCase):
    """The last attempt keeps and reports only findings that a translation cannot fix (a source cut off mid-sentence: category "fragment"). A finding that says the
    translation itself is wrong is never kept (user 2026-10-22), whatever the count."""

    def setUp(self):
        patcher = patch('fragment_context_review.review_fragments', return_value=[])      # the model-based check of cut-off sentences is not what is tested here
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_real_translation_defect_is_never_kept_not_even_one_in_a_long_file(self):
        for category in ('meaning', 'grammar', 'untranslated', 'transliteration'):
            error, saved, _ = run_review(900, [11], final=True, category=category)
            self.assertIsNotNone(error, category)
            self.assertEqual(saved['status'], 'unusable')
            self.assertEqual(saved['remaining_findings'], {'hard': [11], 'soft': []})
            self.assertNotIn('doubtful', saved)

    def test_a_mix_is_refused_by_its_real_defect_and_the_soft_ones_are_listed_as_such(self):
        rows = rows_of(100)
        job = {'id': 9, 'spoken_swedish_ids': []}

        def assess(candidate, *, audit=None, **kwargs):
            audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': [3, 8], 'flags': [flag(3, 'fragment'), flag(8, 'meaning')]})
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), patch.object(worker, 'assess_translation_quality', side_effect=assess), \
                patch.object(worker, 'repair_flagged_translation', side_effect=lambda job_, rows_, flags, **kwargs: [dict(row) for row in rows_]), patch.object(worker, 'check_cancelled'):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation(job, rows, final=True)
            saved = json.loads((Path(folder) / 'job-9.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['remaining_findings'], {'hard': [8], 'soft': [3]})

    def test_the_last_attempt_repairs_a_defect_again_up_to_two_more_times_before_it_refuses(self):
        rows = rows_of(100)
        job = {'id': 9, 'spoken_swedish_ids': []}
        repaired = []

        def assess(candidate, *, audit=None, **kwargs):
            # the first review and the first re-review flag row 8; after the second repair it is clean
            assess.calls += 1
            bad = [8] if assess.calls <= 2 + 1 else []
            audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': bad, 'flags': [flag(i, 'meaning') for i in bad]})
        assess.calls = 0

        def repair(job_, rows_, flags, **kwargs):
            repaired.append(sorted({item['id'] for item in flags}))
            return [dict(row) for row in rows_]
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), patch.object(worker, 'assess_translation_quality', side_effect=assess), \
                patch.object(worker, 'repair_flagged_translation', side_effect=repair), patch.object(worker, 'check_cancelled'):
            worker.review_and_save_translation(job, rows, final=True)
            saved = json.loads((Path(folder) / 'job-9.json').read_text(encoding='utf-8'))
        self.assertEqual(repaired, [[8], [8]], 'the normal repair and one extra round')
        self.assertEqual(saved['status'], 'usable')
        self.assertEqual(len(saved['extra_repair_reviews']), 1)
        self.assertNotIn('doubtful', saved)

    def test_the_extra_rounds_stop_at_two_and_the_first_attempt_has_none(self):
        error, saved, _ = run_review(100, [8], final=True, category='meaning')
        self.assertIsNotNone(error)
        self.assertEqual(len(saved['extra_repair_reviews']), worker.EXTRA_REPAIR_ROUNDS)
        error, saved, _ = run_review(100, [8], final=False, category='meaning')
        self.assertIsNotNone(error)
        self.assertNotIn('extra_repair_reviews', saved)

    def test_the_first_attempt_still_fails_on_one_remaining_finding_so_the_retry_happens(self):
        error, saved, job = run_review(53, [11], final=False)
        self.assertIsNotNone(error)
        self.assertEqual(saved['status'], 'unusable')
        self.assertNotIn('doubtful', saved)

    def test_the_last_attempt_keeps_a_stray_finding_and_reports_it(self):
        error, saved, job = run_review(53, [11], final=True)
        self.assertIsNone(error)
        self.assertEqual(saved['status'], 'usable')
        self.assertEqual([item['id'] for item in saved['doubtful']], [11])
        self.assertEqual([item['id'] for item in job['doubtful_rows']], [11])
        self.assertEqual(saved['doubtful'][0]['reason'], 'Betydelsen ändras.')

    def test_the_last_attempt_still_fails_at_the_limit(self):
        error, saved, _ = run_review(53, [5, 11, 20], final=True)
        self.assertIsNotNone(error, '3 of 53 rows is the limit')
        self.assertEqual(saved['status'], 'unusable')
        error, _, _ = run_review(22, [4, 9], final=True)
        self.assertIsNotNone(error, 'two of 22 rows is the limit for a short file')

    def test_two_findings_in_a_long_file_are_tolerated_and_three_are_not(self):
        self.assertIsNone(run_review(53, [5, 11], final=True)[0])
        self.assertIsNotNone(run_review(53, [5, 11, 20], final=True)[0])
        self.assertIsNone(run_review(100, [1, 30, 60, 90], final=True)[0])
        self.assertIsNotNone(run_review(100, [1, 30, 60, 90, 95], final=True)[0])

    def test_rows_kept_as_spoken_swedish_never_count_towards_the_limit(self):
        error, saved, job = run_review(22, [4, 9], final=True, spoken=[4])
        self.assertIsNone(error)
        self.assertEqual([item['id'] for item in job['doubtful_rows']], [9])

    def test_a_clean_review_reports_nothing(self):
        error, saved, job = run_review(30, [], final=True)
        self.assertIsNone(error)
        self.assertNotIn('doubtful', saved)

    def test_the_second_translation_attempt_is_the_final_one_and_the_english_pass_is_strict(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn('review_and_save_translation(job, translated, final=True)', source)
        self.assertIn("review_and_save_translation(job, english_rows, target_key='en', target_language='English')", source)
        self.assertIn("job['doubtful_rows'] = []", source)

    def test_the_finished_job_says_how_many_lines_may_be_wrong_and_saves_the_report(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn("kan vara fel (se rapporten)", source)
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'DOUBTFUL_REPORTS', Path(folder) / 'doubtful-lines'):
            worker.save_doubtful_report({'id': 12}, Path('/media/Film.mp4'), [{'id': 3, 'start': 9.0, 'sv': 'x'}])
            saved = json.loads((Path(folder) / 'doubtful-lines' / 'job-12.json').read_text(encoding='utf-8'))
            self.assertEqual((saved['job_id'], saved['media_name'], saved['rows'][0]['id']), (12, 'Film.mp4', 3))
            self.assertEqual([path.name for path in (Path(folder) / 'doubtful-lines').iterdir()], ['job-12.json'], 'no temporary file is left')


class ReportPageTests(unittest.TestCase):
    def report(self):
        return {'job_id': 4, 'media_name': 'Film <1>.mp4', 'rows': [
            {'id': 49, 'start': 98.6, 'end': 99.9, 'source': 'Cette célébrité s\'y démonte.', 'sv': 'Den här kändisen <b>visar</b> vad hen går för.', 'category': 'meaning',
             'reason': 'Källtexten betyder att kändisen tappar fattningen.'}]}

    def test_the_page_lists_each_row_with_time_source_swedish_and_the_remark_and_escapes_everything(self):
        html = doubtful_report.render_report_content(self.report(), 'sv')
        self.assertIn('Repliker som kan vara fel', html)
        self.assertIn('01:38', html)
        self.assertIn('Cette célébrité', html)
        self.assertIn('&lt;b&gt;visar&lt;/b&gt;', html)
        self.assertIn('Film &lt;1&gt;.mp4', html)
        self.assertIn('Källtexten betyder', html)
        self.assertNotIn('<table', html, 'stacked blocks read the same on a phone')
        self.assertIn('overflow-wrap:anywhere', html, 'a long unbroken word must not push the page sideways')
        self.assertIn('tvivlade på 1 repliker', html)

    def test_english_and_a_missing_report(self):
        self.assertIn('Lines that may be wrong', doubtful_report.render_report_content(self.report(), 'en'))
        self.assertIn('Ingen rapport finns', doubtful_report.render_report_content(None, 'sv'))

    def test_a_report_is_found_only_by_a_number_and_a_broken_file_is_none(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'doubtful-lines'
            target.mkdir()
            (target / 'job-4.json').write_text(json.dumps(self.report()), encoding='utf-8')
            (target / 'job-5.json').write_text('{kaputt', encoding='utf-8')
            self.assertEqual(doubtful_report.load_report(folder, 4)['job_id'], 4)
            self.assertIsNone(doubtful_report.load_report(folder, 5))
            self.assertIsNone(doubtful_report.load_report(folder, 6))
            self.assertTrue(doubtful_report.has_report(folder, 4))
            self.assertFalse(doubtful_report.has_report(folder, 6))
            with self.assertRaises(ValueError):
                doubtful_report.report_path(folder, '../x')

    def test_the_queue_row_of_a_finished_job_links_to_its_report(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'CONFIG', Path(folder)):
            (Path(folder) / 'doubtful-lines').mkdir()
            (Path(folder) / 'doubtful-lines' / 'job-4.json').write_text(json.dumps(self.report()), encoding='utf-8')
            job = {'id': 4, 'status': 'completed', 'message': 'Klar', 'media_name': 'Film.mp4', 'media_path': '/m/Film.mp4'}
            html = server.render_queue_job_row(job, {}, 'tok', cancellable=False)
            self.assertIn("href='/report?job=4'", html)
            self.assertIn('Visa osäkra repliker', html)
            other = server.render_queue_job_row({**job, 'id': 7}, {}, 'tok', cancellable=False)
            self.assertNotIn('/report?job', other)
            failed = server.render_queue_job_row({**job, 'status': 'quality_failed'}, {}, 'tok', cancellable=False)
            self.assertNotIn('/report?job', failed)

    def test_the_route_is_registered_in_both_page_lists(self):
        source = Path(server.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count('"/cleanup", "/report"'), 2)
        self.assertIn('elif path == "/report":', source)


if __name__ == '__main__':
    unittest.main()
