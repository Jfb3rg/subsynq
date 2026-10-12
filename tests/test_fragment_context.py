import json
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
from fragment_context_review import review_fragments
import worker


class FragmentContextTests(unittest.TestCase):
    def test_empty_regular_after_review_cannot_override_unresolved_context(self):
        rows = [{'id': 0, 'source': 'source', 'sv': 'Jag väntar och', 'start': 1, 'end': 2}]
        flag = {'id': 0, 'category': 'fragment'}
        def audit(rows, audit=None, **kwargs):
            if not audit:
                audit.append({'flags': [flag] if rows[0]['sv'] == 'Jag väntar och' else []})
        candidate = [{**rows[0], 'sv': 'Jag går och'}]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'assess_translation_quality', side_effect=audit), \
                patch.object(worker, 'repair_flagged_translation', return_value=candidate), \
                patch('fragment_context_review.review_fragments', return_value=[{
                    'id': 0, 'status': 'unresolved', 'reason': 'Konjunktionen har ingen faktisk fortsättning.'}]):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation({'id': 9}, rows)
            saved = json.loads((Path(folder) / 'job-9.json').read_text())
            self.assertEqual(saved['status'], 'unusable')
            self.assertEqual(rows[0]['sv'], 'Jag väntar och')

    def cases(self):
        return [{'id': 0, 'start': 1, 'end': 2, 'sv': 'Jag stannar och'},
                {'id': 1, 'start': 2, 'end': 3, 'sv': 'lyssnar på dig.'}]

    def call(self, status='resolved', alter=False):
        def run(request):
            case = json.loads(request['prompt'])['cases'][0]
            return {'response': json.dumps({'checks': [{'id': case['id'],
                'joined_target': case['joined_target'] + (' hitta på ord' if alter else ''),
                'status': status, 'reason': 'De faktiska orden granskas tillsammans utan gissade ord.'}]})}
        return run

    def test_real_neighbor_continuation_can_pass(self):
        checks = review_fragments(self.cases(), [{'id': 0, 'category': 'fragment'}], self.call(), 'test')
        self.assertEqual(checks[0]['status'], 'resolved')

    def test_unresolved_and_uncertain_remain_explicit(self):
        for status in ['unresolved', 'uncertain']:
            self.assertEqual(review_fragments(self.cases(), [{'id': 0, 'category': 'fragment'}],
                self.call(status), 'test')[0]['status'], status)

    def test_invented_context_cannot_pass(self):
        with self.assertRaises(ValueError):
            review_fragments(self.cases(), [{'id': 0, 'category': 'fragment'}], self.call(alter=True), 'test')

    def test_no_prior_fragment_needs_no_additional_model(self):
        def fail(request):
            raise AssertionError('Unexpected model call')
        self.assertEqual(review_fragments(self.cases(), [{'id': 0, 'category': 'meaning'}], fail, 'test'), [])

    def test_large_media_reviews_are_bounded_and_cover_every_flag(self):
        rows = [{'id': i, 'sv': 'En fullständig kort mening.'} for i in range(9)]
        sizes = []
        def call(request):
            cases = json.loads(request['prompt'])['cases']
            sizes.append(len(cases))
            return {'response': json.dumps({'checks': [{
                'id': c['id'], 'joined_target': c['joined_target'], 'status': 'resolved',
                'reason': 'Den markerade raden är en fullständig svensk mening.'} for c in cases]})}
        checks = review_fragments(rows, [{'id': i, 'category': 'fragment'} for i in range(9)], call, 'test')
        self.assertEqual(sizes, [4, 4, 1])
        self.assertEqual(len(checks), 9)
