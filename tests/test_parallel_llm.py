import json
import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import cloud_gpu
import worker


def rows(count):
    return [{'id': n, 'start': n * 2, 'end': n * 2 + 1.5, 'source': f'Line number {n} here.', 'sv': f'Rad nummer {n} här.'} for n in range(count)]


class PartsTests(unittest.TestCase):
    def test_a_long_file_is_cut_into_parts_of_whole_batches_in_order(self):
        parts = worker.contiguous_parts(rows(899), 3, unit=36)
        self.assertEqual([len(part) for part in parts], [324, 324, 251])
        self.assertEqual([row['id'] for part in parts for row in part], list(range(899)))

    def test_parts_for_the_review_share_two_cues_with_the_next_part(self):
        parts = worker.contiguous_parts(rows(300), 3, unit=10, overlap=2)
        self.assertEqual([(part[0]['id'], part[-1]['id']) for part in parts], [(0, 101), (100, 201), (200, 299)])

    def test_a_file_that_fits_in_fewer_parts_gets_fewer(self):
        self.assertEqual(len(worker.contiguous_parts(rows(40), 3, unit=36)), 2)
        self.assertEqual(worker.contiguous_parts([], 3, unit=36), [])


class RunParallelTests(unittest.TestCase):
    def test_tasks_run_at_the_same_time_carry_the_job_state_and_return_in_order(self):
        worker.MODEL_JOB.device, worker.MODEL_JOB.job_id = 'nvidia', 77
        barrier = threading.Barrier(3, timeout=5)

        def task(n):
            barrier.wait()                                  # all three must be running at once
            return (n, worker.MODEL_JOB.device, worker.MODEL_JOB.job_id)
        self.addCleanup(lambda: (setattr(worker.MODEL_JOB, 'device', None), setattr(worker.MODEL_JOB, 'job_id', None)))
        self.assertEqual(worker.run_parallel([lambda n=n: task(n) for n in range(3)], 3), [(n, 'nvidia', 77) for n in range(3)])

    def test_every_task_finishes_and_the_first_error_is_raised(self):
        finished = []

        def good():
            finished.append('good')

        def bad():
            raise RuntimeError('boom')
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            worker.run_parallel([bad, good, good], 3)
        self.assertEqual(finished, ['good', 'good'])


class WorkersTests(unittest.TestCase):
    def test_only_a_long_job_on_a_server_that_is_not_the_cpu_with_parallelism_switched_on_is_split(self):
        with patch.object(worker, 'LLM_PARALLEL', 3), patch.object(worker, 'MODEL_JOB') as state:
            state.device = 'cloud-vast'
            self.assertEqual(worker.parallel_workers(300), 3)
            self.assertEqual(worker.parallel_workers(119), 1)
            self.assertEqual(worker.parallel_workers(100, 144), 1)
            state.device = 'cpu'
            self.assertEqual(worker.parallel_workers(900), 1)
        with patch.object(worker, 'LLM_PARALLEL', 1), patch.object(worker, 'MODEL_JOB') as state:
            state.device = 'cloud-vast'
            self.assertEqual(worker.parallel_workers(900), 1)

    def test_the_setting_is_read_from_the_environment_and_clamped(self):
        for value, expected in (('3', 3), ('9', 4), ('0', 1), ('x', 1), ('', 1)):
            with patch.dict(os.environ, {'SUBSYNQ_LLM_PARALLEL': value}):
                self.assertEqual(worker._llm_parallel(), expected, value)
                self.assertEqual(cloud_gpu.llm_parallel(), expected, value)


def comparison_verdict(sample, flagged):
    return {'comparisons': [{'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'], 'meaning_preserved': row['id'] not in flagged}
                            for row in sample],
            'flags': [{'id': row['id'], 'category': 'meaning', 'source_quote': row['source'][:14], 'target_quote': row['sv'][:12],
                       'reason': f'Betydelsen ändras i rad {row["id"]}: en konkret förklaring av vad som gick förlorat.'}
                      for row in sample if row['id'] in flagged]}


class ParallelReviewTests(unittest.TestCase):
    FLAGGED = {7, 55, 101, 150, 151, 290}

    def review(self, parallel, count=300):
        threads, lock = set(), threading.Lock()
        barrier = threading.Barrier(3, timeout=10)

        def answer(request):
            with lock:
                first = threading.get_ident() not in threads
                threads.add(threading.get_ident())
            if first and parallel > 1 and count >= 300:
                barrier.wait()                              # the three parts must really be reviewing at the same time
            sample = json.loads(request['prompt'])['rows']
            return {'response': json.dumps(comparison_verdict(sample, self.FLAGGED))}
        audit = []
        with patch.object(worker, 'FOCUSED_REVIEW', True), patch.object(worker, 'LLM_PARALLEL', parallel), patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'call_ollama', side_effect=answer), patch.object(worker, 'check_cancelled'):
            state.device = 'cloud-vast'
            state.job_id = None
            self.assertTrue(worker.assess_translation_quality(rows(count), audit=audit, whole_file=True))
        return audit, threads

    def test_three_parts_find_exactly_the_flags_the_one_after_the_other_review_finds(self):
        serial, serial_threads = self.review(1)
        parallel, parallel_threads = self.review(3)
        self.assertEqual(len(serial_threads), 1)
        self.assertGreater(len(parallel_threads), 1, 'the parts really ran in several threads')
        found = lambda audit: {flag['id'] for entry in audit for flag in entry.get('flags', [])}
        self.assertEqual(found(parallel), found(serial))
        self.assertEqual(found(parallel), self.FLAGGED)
        self.assertEqual({row_id for entry in parallel for row_id in entry['ids']}, set(range(300)), 'every cue was reviewed')

    def test_a_short_file_or_the_quick_re_review_is_not_split(self):
        _, threads = self.review(3, count=100)
        self.assertEqual(len(threads), 1)

    def test_the_checks_that_need_the_whole_file_run_once_not_per_part(self):
        answer_calls = []
        with patch.object(worker, 'FOCUSED_REVIEW', True), patch.object(worker, 'LLM_PARALLEL', 3), patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'call_ollama', side_effect=lambda r: {'response': json.dumps(comparison_verdict(json.loads(r['prompt'])['rows'], set()))}):
            state.device, state.job_id = 'cloud-vast', None
            original = worker.assess_translation_quality
            with patch.object(worker, 'assess_translation_quality', side_effect=lambda *a, **k: answer_calls.append(k) or original(*a, **k)):
                original(rows(300), audit=[], whole_file=True)
        self.assertEqual([call.get('shape_checks', True) for call in answer_calls], [True, False, False, False])
        self.assertEqual(answer_calls[0].get('only_ids'), set(), 'the whole-file checks are a call without any block')


class ParallelTranslationTests(unittest.TestCase):
    def translate(self, parallel, count=500):
        progress, concurrent, peak, lock = [], [0], [0], threading.Lock()
        barrier, started = threading.Barrier(3, timeout=10), set()

        def answer(request):
            with lock:
                first = threading.get_ident() not in started
                started.add(threading.get_ident())
                concurrent[0] += 1
                peak[0] = max(peak[0], concurrent[0])
            try:
                if first and parallel > 1:
                    barrier.wait()
                payload = json.loads(request['prompt'])
                if 'draft_sv' in payload['rows'][0]:
                    return {'response': json.dumps({'rows': [{'id': row['id'], 'sv': row['draft_sv']} for row in payload['rows']]})}
                return {'response': json.dumps({'rows': [{'id': row['id'], 'sv': f'Rad {row["id"]}.'} for row in payload['rows']]})}
            finally:
                with lock:
                    concurrent[0] -= 1
        with patch.object(worker, 'LLM_PARALLEL', parallel), patch.object(worker, 'MODEL_JOB') as state, patch.object(worker, 'call_ollama', side_effect=answer), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'identify_swedish_rows', return_value=set()), \
                patch.object(worker, 'update_job', side_effect=lambda *args, **kwargs: progress.append(args)):
            state.device, state.job_id = 'cloud-vast', None
            result = worker.translate({'id': -1}, rows(count))
        return result, progress, peak[0]

    def test_a_long_file_translated_in_parts_gives_the_same_rows_in_the_same_order(self):
        serial, _, serial_peak = self.translate(1)
        parallel, progress, parallel_peak = self.translate(3)
        self.assertEqual([row['sv'] for row in parallel], [row['sv'] for row in serial])
        self.assertEqual([row['id'] for row in parallel], list(range(500)))
        self.assertEqual(serial_peak, 1)
        self.assertGreaterEqual(parallel_peak, 3, 'the three parts were translated at the same time')

    def test_progress_counts_every_batch_once(self):
        _, progress, _ = self.translate(3)
        parts = [args[3] for args in progress]
        self.assertEqual(len(parts), 14)                    # 500 cues = 14 batches of 36
        self.assertTrue(parts[-1].endswith('del 14 av 14'), parts[-1])
        self.assertEqual([args[2] for args in progress], sorted(args[2] for args in progress), 'the progress only goes forward')

    def test_the_spoken_swedish_rows_of_every_part_are_all_kept(self):
        def identify(part_rows, call):
            return {row['id'] for row in part_rows if row['id'] % 100 == 0}
        job = {'id': -1}
        with patch.object(worker, 'LLM_PARALLEL', 3), patch.object(worker, 'MODEL_JOB') as state, patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'), patch.object(worker, 'identify_swedish_rows', side_effect=identify), \
                patch.object(worker, 'call_ollama', side_effect=lambda r: {'response': json.dumps({'rows': [
                    {'id': row['id'], 'sv': row.get('draft_sv', 'x')} for row in json.loads(r['prompt'])['rows']]})}):
            state.device, state.job_id = 'cloud-vast', None
            worker.translate(job, rows(500))
        self.assertEqual(job['spoken_swedish_ids'], [0, 100, 200, 300, 400])


class BootstrapParallelTests(unittest.TestCase):
    def script(self, prepared=False):
        import test_cloud_cpu
        return None

    def test_the_rented_server_is_started_with_the_same_parallelism(self):
        with patch.dict(os.environ, {'SUBSYNQ_LLM_PARALLEL': '3'}):
            self.assertIn('OLLAMA_NUM_PARALLEL=3', cloud_gpu.BOOTSTRAP_TEMPLATE.replace('@PARALLEL@', str(cloud_gpu.llm_parallel())))
        self.assertIn('OLLAMA_NUM_PARALLEL=@PARALLEL@', cloud_gpu.BOOTSTRAP_TEMPLATE)

    def test_the_default_is_one_request_at_a_time_as_always(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUBSYNQ_LLM_PARALLEL', None)
            self.assertEqual(cloud_gpu.llm_parallel(), 1)
            self.assertEqual(worker._llm_parallel(), 1)


if __name__ == '__main__':
    unittest.main()
