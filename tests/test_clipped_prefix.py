import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from reread import clipped_prefix_evidence, conflicting_reread_rows


def sample():
    root = Path('/diagnostic') if str(__file__) == '/test.py' else Path(__file__).parent
    return json.loads((root/'fixtures/tail-confirmation.json').read_text())['clipped_prefix_case']


class ClippedPrefixTests(unittest.TestCase):
    def test_real_weak_edge_words_cannot_disprove_the_old_prefix(self):
        data = sample()
        proof = clipped_prefix_evidence(data['row'], data['record']['candidate'], data['record']['readings'])
        self.assertEqual(proof['removed_prefix'], list('怖くて'))
        self.assertAlmostEqual(proof['clip_start'], 36.412)
        self.assertEqual([w['name'] for w in proof['witnesses']], ['precision', 'v2'])

    def test_worker_keeps_source_and_reports_unresolved_acoustic_risk(self):
        data = sample()
        rows = [data['row']]
        before = copy.deepcopy(rows)
        with tempfile.NamedTemporaryFile() as media, patch.object(worker, 'REREAD_ENABLED', True):
            job = {'id': -1, 'media_path': media.name}
            changed = worker.reread_flagged_rows(job, rows, [6], reader=lambda *a: {6: data['record']['readings']})
        self.assertEqual(changed, [])
        self.assertEqual(rows, before)
        self.assertEqual(job['reread_rows'][0]['decision'], 'omläsningen tappar prefix vid klippstart')
        self.assertEqual([r['id'] for r in conflicting_reread_rows(rows, job['reread_rows'], [6])], [6])

    def test_not_a_blanket_ban_on_shorter_or_different_readings(self):
        for change in ('strong', 'inside', 'different', 'missing', 'duplicate'):
            data = sample()
            readings = data['record']['readings']
            if change == 'strong': readings[0]['words'][0]['probability'] = .6
            elif change == 'inside': readings[0]['words'][0]['start'] += .1
            elif change == 'different': readings[0]['text'] = 'Different words.'
            elif change == 'missing': readings.pop(0)
            else: readings.append(copy.deepcopy(readings[0]))
            self.assertIsNone(clipped_prefix_evidence(data['row'], data['record']['candidate'], readings), change)

    def test_prefix_edge_guard_also_handles_space_separated_languages(self):
        row = {'source': 'Please keep all the words.', 'start': 10.3}
        candidate = 'keep all the words.'
        readings = [{'name': name, 'text': candidate, 'words': [
            {'word': 'keep', 'start': 10., 'end': 10.2, 'probability': .4}]} for name in ('precision', 'v2')]
        self.assertEqual(clipped_prefix_evidence(row, candidate, readings)['removed_prefix'], ['please'])
        self.assertIsNone(clipped_prefix_evidence(row, 'Please keep all the words!', readings))


if __name__ == '__main__':
    unittest.main()
