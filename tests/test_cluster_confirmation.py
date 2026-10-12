import copy
import sys
import unittest
sys.path[:0] = ['/app', '/ai']
import asr_windows as a


def cue(text, start):
    words = text.split()
    return {'text': text, 'start': start, 'end': start + len(words) * .2,
            'confidence': .9, 'words': [
                {'text': w, 'start': start + i * .2, 'end': start + (i + 1) * .2,
                 'probability': .9} for i, w in enumerate(words)]}


class ClusterConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.base = [{'text': 'one two three four five', 'start': 2., 'end': 3.},
                     {'text': 'six seven eight nine ten', 'start': 3.2, 'end': 4.2},
                     {'text': 'Leave now.', 'start': 9., 'end': 9.4}]
        self.reading = [cue('One two three four five.', 2), cue('Six seven eight nine ten.', 3.2)]

    def test_three_readings_restore_sentences_and_preserve_neighbors(self):
        old = copy.deepcopy(self.base)
        fixed = a.restore_corroborated_clusters(self.base, self.reading, self.reading, self.reading)
        self.assertEqual([r['text'] for r in fixed], [r['text'] for r in self.reading] + ['Leave now.'])
        self.assertEqual(fixed[-1], old[-1])
        self.assertEqual(self.base, old)

    def test_no_change_for_missing_word_disagreement_bad_confidence_or_overlap(self):
        variants = [[], [cue('One two three four wrong.', 2)] + self.reading[1:]]
        low = copy.deepcopy(self.reading)
        low[0]['words'][0]['probability'] = .54
        variants.append(low)
        overlap = copy.deepcopy(self.reading)
        overlap[0]['words'][1]['start'] = 2.05
        variants.append(overlap)
        for reading in variants:
            with self.subTest(reading=reading):
                self.assertEqual(a.restore_corroborated_clusters(self.base, self.reading, reading, self.reading), self.base)

    def test_contractions_share_one_physical_word_without_false_overlap(self):
        row = cue("You're dead to me, understand?", 2)
        self.assertTrue(a._valid_cue_words(row))
        tokens = a._normalized(row['text']).split()
        self.assertTrue(a._matching_timed_phrase([row], tokens, 2, row['end']))
        self.assertFalse(a._matching_timed_phrase([row], ['you'], 2, 2.2))
        self.assertFalse(a._matching_timed_phrase([row], ['re'], 2, 2.2))
        row['words'][1]['start'] = 2.05
        self.assertFalse(a._valid_cue_words(row))

    def test_no_plan_for_complete_rows_and_work_is_bounded(self):
        self.assertEqual(a.plan_unpunctuated_clusters(self.reading, 20), [])
        rows = [{**r, 'start': r['start'] + i * 30, 'end': r['end'] + i * 30}
                for i in range(8) for r in self.base[:2]]
        windows = a.plan_unpunctuated_clusters(rows, 250)
        self.assertEqual(len(windows), 4)
        self.assertTrue(all(w['end'] - w['start'] <= 27 for w in windows))

    def test_unanimous_rereads_cannot_drop_an_existing_word(self):
        altered = [cue('One two three four wrong.', 2)] + self.reading[1:]
        self.assertEqual(a.restore_corroborated_clusters(self.base, altered, altered, altered), self.base)

    def test_neighbor_overlap_and_nonfinite_times_are_rejected(self):
        crowded = self.base + [{'text': 'Other.', 'start': 1.9, 'end': 2.1}]
        self.assertEqual(a.restore_corroborated_clusters(crowded, self.reading, self.reading, self.reading),
                         sorted(crowded, key=lambda r: r['start']))
        broken = copy.deepcopy(self.reading)
        broken[0]['words'][0]['end'] = float('nan')
        self.assertEqual(a.restore_corroborated_clusters(self.base, broken, broken, broken), self.base)
