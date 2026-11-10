import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from asr_windows import reading_alternatives


def cue(start, end, text, confidence):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


# Regression case: the long pass kept "I'm three"; the rescue pass heard "On three" more confidently. The source judge reads only the
# first doubtful rows of a file, so nothing examined this row and it was written without a word about the other reading.
FINAL = [cue(1800.0, 1802.18, "Yes, sir, I'm three.", .7177), cue(1802.44, 1805.92, 'All right, here we go one two', .6801)]
READINGS = {'context': [cue(1800.0, 1802.18, "Yes, sir, I'm three.", .7177)],
            'rescue': [cue(1799.88, 1800.81, 'Yes, sir!', .791), cue(1801.22, 1802.2, 'On three?', .7666)]}
OTHER = 'Yes, sir! On three?'
AGREE = [{'name': name, 'text': "Yes, sir, I'm three.", 'confidence': .8} for name in ('precision', 'v2', 'qwen')]


def rows(alternatives=True):
    row = {'id': 411, 'start': 1800.2, 'end': 1802.2, 'source': "Yes, sir, I'm three."}
    if alternatives:
        row['alternatives'] = [{'text': OTHER, 'support': 1, 'current_support': 1, 'one_word': True}]
    return [{'id': 410, 'start': 1788.5, 'end': 1790.5, 'source': 'Lily, you got a clean shot on him?'}, row,
            {'id': 412, 'start': 1802.4, 'end': 1805.9, 'source': 'All right, here we go one two'}]


def unsettled():
    return [{'id': 411, 'source': "Yes, sir, I'm three.", 'other': OTHER}]


class MarkerTests(unittest.TestCase):
    def test_the_rescue_wording_one_word_apart_is_marked(self):
        self.assertEqual([item['text'] for item in reading_alternatives(FINAL, READINGS)[0] if item.get('one_word')], [OTHER])

    def test_an_ordinary_doubtful_cue_has_no_marker(self):
        final = [cue(10.0, 12.0, 'I saw the mall.', .4)]
        found = reading_alternatives(final, {'context': final, 'v2': [cue(10.0, 12.0, 'I saw them all today.', .8)]})
        self.assertTrue(found[0])
        self.assertFalse(any(item.get('one_word') for item in found[0]))


class UnjudgedRowTests(unittest.TestCase):
    def choose(self, decisions):
        job = {'id': 64}
        plain = [{key: value for key, value in row.items() if key != 'alternatives'} for row in rows()]
        with patch.object(worker, 'update_job'), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'choose_readings', return_value=(plain, decisions)):
            worker.choose_between_readings(job, rows())
        return job

    def test_a_row_the_judge_never_read_is_recorded(self):
        self.assertEqual(self.choose([])['unsettled_readings'], unsettled())

    def test_a_row_the_judge_read_is_not(self):
        decision = {'id': 411, 'choice': 'A', 'applied': False, 'from': "Yes, sir, I'm three.", 'to': "Yes, sir, I'm three."}
        self.assertEqual(self.choose([decision])['unsettled_readings'], [])

    def test_the_row_is_recorded_when_the_judge_is_unavailable(self):
        job = {'id': 64}
        with patch.object(worker, 'update_job'), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'choose_readings', side_effect=RuntimeError('ingen modell')):
            worker.choose_between_readings(job, rows())
        self.assertEqual(job['unsettled_readings'], unsettled())


class RereadTests(unittest.TestCase):
    """The row goes to the existing three-model reread; nothing is chosen for it without that evidence."""

    def audit(self, readings, changed=(), source=None):
        job, data = {'id': 64, 'unsettled_readings': unsettled()}, rows(False)
        asked = []

        def reread(job, rows, ids):
            asked.append(list(ids))
            if source:
                rows[1]['source'] = source
            job['reread_rows'] = [{'id': 411, 'current': "Yes, sir, I'm three.", 'readings': readings,
                                   'decision': 'ersatt' if source else 'behållen'}]
            return list(changed)

        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'update_job'), patch.object(worker, 'assess_source_quality'), \
                patch.object(worker, 'source_audit_language_context', return_value=None), \
                patch.object(worker, 'reread_flagged_rows', side_effect=reread):
            still = worker.assess_source_with_reread(job, data)
        return job, data, asked, still

    def test_the_unjudged_row_is_read_again(self):
        job, _, asked, _ = self.audit(AGREE)
        self.assertEqual(asked, [[411]])
        self.assertEqual(job['source_reread']['one_word_review_candidates'], [411])

    def test_readers_that_hear_the_kept_words_settle_it(self):
        job, data, _, still = self.audit(AGREE)
        self.assertEqual((still, job['unsettled_readings'], job['source_reread']['settled_one_word_readings']), ([], [], [411]))
        self.assertEqual(worker.unsettled_reading_rows(job, data, set()), [])

    def test_a_replaced_row_is_no_longer_listed(self):
        heard = [{'name': name, 'text': 'Yes, sir. On three.', 'confidence': .8} for name in ('precision', 'v2', 'qwen')]
        job, data, _, still = self.audit(heard, changed=[411], source='Yes, sir. On three.')
        self.assertEqual(still, [])
        self.assertEqual(worker.unsettled_reading_rows(job, data, set()), [])

    def test_readers_that_disagree_leave_the_row_doubtful_and_listed(self):
        heard = [{'name': 'precision', 'text': 'Yes, sir', 'confidence': .9}, {'name': 'v2', 'text': "Yes, sir, I'm three", 'confidence': .63},
                 {'name': 'qwen', 'text': 'Yes, sir. On three.', 'confidence': .5}]
        job, data, _, still = self.audit(heard)
        self.assertEqual((still, job['source_reread']['settled_one_word_readings']), ([411], []))      # kept as source_unclear and listed as such
        self.assertEqual(worker.unsettled_reading_rows(job, data, set(still)), [])

    def test_no_reread_available_lists_the_row_with_the_other_wording(self):
        job, data = {'id': 64, 'unsettled_readings': unsettled()}, rows(False)
        with patch.object(worker, 'REREAD_ENABLED', False):
            self.assertEqual(worker.one_word_review_ids(job, data), [])
        listed = worker.unsettled_reading_rows(job, data, set())
        self.assertEqual([item['id'] for item in listed], [411])
        self.assertIn(OTHER, listed[0]['reason'])

    def test_a_row_already_listed_or_since_changed_is_not_listed_twice(self):
        job, data = {'id': 64, 'unsettled_readings': unsettled()}, rows(False)
        self.assertEqual(worker.unsettled_reading_rows(job, data, {411}), [])
        data[1]['source'] = 'Yes, sir. On three.'
        self.assertEqual(worker.unsettled_reading_rows(job, data, set()), [])

    def test_at_most_four_rows_a_file_and_never_a_confirmed_one(self):
        data = [{'id': n, 'start': n * 3.0, 'end': n * 3.0 + 2, 'source': f'Line number {n} here.'} for n in range(1, 8)]
        data[0]['confirmed_source_words'] = ['number']
        job = {'id': 64, 'unsettled_readings': [{'id': row['id'], 'source': row['source'], 'other': 'x'} for row in data]}
        with patch.object(worker, 'REREAD_ENABLED', True):
            self.assertEqual(worker.one_word_review_ids(job, data), [2, 3, 4, 5])


if __name__ == '__main__':
    unittest.main()
