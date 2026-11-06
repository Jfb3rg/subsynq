import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker

# regression case(Example Series 4x03, Main 0.8.366): row 209 "Yeah, well, I've been led blindly" is continued in lower case by row 210, so it counts as cut
# off; the final review first named "Ja, men jag har blivit led blind" (not Swedish) and the file shipped it as a doubtful line without any repair.
SOURCES = ['This is a complete sentence.'] * 20 + ["Yeah, well, I've been led blindly", 'blindly by enough military intelligence.'] + \
          ['This is a complete sentence.'] * 38


def review(first, later, *, repaired=None):
    """first: flagged ids of the first review; later: flagged ids of every later review. Returns (error, saved report, job, repair calls)."""
    rows = [{'id': n, 'start': n * 3.0, 'end': n * 3.0 + 2.5, 'source': SOURCES[n], 'sv': f'Rad {n}.'} for n in range(len(SOURCES))]
    job, calls, reviews = {'id': 76, 'spoken_swedish_ids': []}, [], []

    def assess(candidate, *, audit=None, **kwargs):
        ids = first if not reviews else later
        reviews.append(ids)
        audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': list(ids),
                      'flags': [{'id': i, 'category': 'grammar' if i == 20 else 'meaning', 'reason': 'Fel.', 'source_quote': 'x', 'target_quote': 'y'}
                                for i in ids]})

    def repair(job_, rows_, flags, **kwargs):
        calls.append(sorted({flag['id'] for flag in flags}))
        return [dict(row, sv=(repaired or {}).get(row['id'], row['sv'])) for row in rows_]

    with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
            patch.object(worker, 'assess_translation_quality', side_effect=assess), patch.object(worker, 'repair_flagged_translation', side_effect=repair), \
            patch.object(worker, 'check_cancelled'), patch('fragment_context_review.review_fragments', return_value=[]):
        try:
            worker.review_and_save_translation(job, rows, final=True)
            error = None
        except worker.SourceQualityError as raised:
            error = raised
        saved = json.loads((Path(folder) / 'job-76.json').read_text(encoding='utf-8'))
    return error, saved, job, calls


class CutOffRepairTests(unittest.TestCase):
    def test_a_translation_defect_first_named_by_the_final_review_on_a_cut_off_row_is_repaired_once(self):
        error, saved, job, calls = review([5], [20])
        self.assertEqual(calls, [[5], [20]])
        self.assertIsNone(error)
        self.assertEqual([(item['id'], item['source_cut_off']) for item in job['doubtful_rows']], [(20, True)])
        self.assertEqual(len(saved['extra_repair_reviews']), 1)

    def test_a_cut_off_row_already_repaired_in_the_first_round_is_not_repaired_again(self):
        error, saved, job, calls = review([5, 20], [20])
        self.assertEqual(calls, [[5, 20]])
        self.assertIsNone(error)
        self.assertNotIn('extra_repair_reviews', saved)

    def test_a_repair_that_works_leaves_nothing_to_report(self):
        error, saved, job, calls = review([5], [20], repaired={20: 'Ja, jag har letts i blindo'})
        self.assertEqual(calls, [[5], [20]])
        self.assertIsNone(error)
        self.assertEqual({row['id']: row['sv'] for row in saved['rows']}[20], 'Ja, jag har letts i blindo')


if __name__ == '__main__':
    unittest.main()
