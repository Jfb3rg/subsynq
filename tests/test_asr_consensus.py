import sys
import unittest

sys.path.insert(0, '/app')
from asr_consensus import compare_transcripts, is_more_complete_alternative, summarize


class AsrConsensusTests(unittest.TestCase):
    def test_exact_agreement_is_distinct_from_changed_word(self):
        self.assertEqual(compare_transcripts('Father Daryl.', 'father daryl')['status'], 'agreement')
        result = compare_transcripts(
            'But the rest of us, our friends never came.',
            'But the rest of us, our parents never came.',
        )
        self.assertEqual(result['status'], 'word_difference')
        self.assertIn('lyssna', result['label'])

    def test_missing_and_large_difference_are_never_approved(self):
        self.assertEqual(compare_transcripts('Find them through the years.', '')['status'], 'missing')
        self.assertEqual(
            compare_transcripts('Find them through the years.', 'Hunted for years.')['status'],
            'meaningful_difference',
        )

    def test_summary_counts_rows_that_need_listening(self):
        rows = [
            {'comparison': {'status': 'agreement'}},
            {'comparison': {'status': 'word_difference'}, 'source_before': 'old', 'reference_checked': True},
            {'comparison': {'status': 'missing'}},
        ]
        result = summarize(rows)
        self.assertEqual(result['rows'], 3)
        self.assertEqual(result['needs_listening'], 2)
        self.assertEqual(result['reference_checked'], 1)

    def test_detects_complete_alternative_without_accepting_unrelated_text(self):
        self.assertTrue(is_more_complete_alternative(
            'Number one.', "C'est mon meilleur pigeon, number one.",
        ))
        self.assertFalse(is_more_complete_alternative('Number one.', 'Maybe, yeah.'))
        self.assertFalse(is_more_complete_alternative('Number one.', 'Number one.'))
