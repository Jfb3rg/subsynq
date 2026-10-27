import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import doubtful_report
import worker
from translation_qa import cut_off_ids, doubtful_rows, hard_flags, source_is_cut_off

# Yukon Men S01E01 (dev job 136): the nine findings that refused the file after three repairs. Six were on source rows that Whisper cut off in the middle of a
# sentence (or that the segmentation split), two on complete rows. (The ends of rows 45 and 429 are the words of the delivered Swedish and of the next row.)
ROWS = [
    {'id': 37, 'source': 'Holy .'}, {'id': 38, 'source': "Let's get the out of"}, {'id': 39, 'source': 'This way of life can kill.'},
    {'id': 44, 'source': "My family's about a week away from being out of meat."},
    {'id': 45, 'source': 'Stan Zuray only has five kilos of meat in his freezer, enough to last'}, {'id': 46, 'source': 'family a week.'},
    {'id': 246, 'source': 'You know, I tried to jump the dogs across the lead one time,'}, {'id': 247, 'source': 'and the whole thing collapsed.'},
    {'id': 294, 'source': 'This is one time I want them to just charge ahead and go nuts.'}, {'id': 295, 'source': 'Get'}, {'id': 296, 'source': 'Get up'},
    {'id': 301, 'source': 'Whoa!'}, {'id': 302, 'source': 'You the dogs!'}, {'id': 303, 'source': 'Woo -hoo!'},
    {'id': 427, 'source': 'temperature in Tanana has dropped to 53 degrees below zero, and the pipes at'}, {'id': 428, 'source': 'the water plant are ready to explode.'},
    {'id': 429, 'source': "It's too cold outside."},
    {'id': 609, 'source': 'Look at this, all the blizzards and the cold weather,'}, {'id': 610, 'source': 'I think, is just made to change this year on us.'},
    {'id': 611, 'source': 'Damn, we came way too far to get nothing.'},
    {'id': 657, 'source': "and I'm gonna up my supply and take care of the demand. Trees freeze in the winter,"}, {'id': 658, 'source': 'cut and difficult'},
    {'id': 659, 'source': "to burn here's a tree that I'm a process James"},
]


class CutOffTests(unittest.TestCase):
    def test_unsegmented_sentences_are_not_single_word_stumps(self):
        for source in ('死ぬためだけに作られた兵器です', '我们明天去河边', 'これは私の家です',
                       '明天回家', 'ฉันจะกลับบ้าน'):
            with self.subTest(source=source):
                self.assertFalse(source_is_cut_off([{'id': 11, 'source': source}], 0))
        self.assertTrue(source_is_cut_off([{'source': '我们去'}], 0))
        self.assertTrue(source_is_cut_off([{'source': 'これは私の家で、'}], 0))
        self.assertTrue(source_is_cut_off([{'source': 'これは私の家で,'}], 0))

    def test_the_real_cut_off_rows_are_found_and_the_complete_ones_are_not(self):
        found = cut_off_ids(ROWS)
        self.assertEqual(sorted(found & {38, 45, 46, 246, 247, 295, 428, 658}), [38, 45, 46, 246, 247, 295, 428, 658],
                         'stump, split sentence and its continuation, comma, one word, lower-case start, fragment')
        for complete in (37, 39, 44, 294, 301, 302, 303, 429, 610, 611):
            self.assertNotIn(complete, found, complete)
        self.assertNotIn(302, found, '"You the dogs!" is complete (garbled, but not cut off)')
        self.assertNotIn(610, found, 'a complete sentence with a wrong word is a translation matter')

    def test_how_a_row_is_cut_off(self):
        def cut(text, before='Before it all happened.', after='After that came the rest.'):
            rows = [{'id': 1, 'source': before}, {'id': 2, 'source': text}, {'id': 3, 'source': after}]
            return source_is_cut_off(rows, 1)
        self.assertTrue(cut('Let us go to the'))
        self.assertTrue(cut('Hello there, my friend,'))
        self.assertTrue(cut('Okay'))
        self.assertTrue(cut('We went down to the river and then we', after='crossed the ice.'))
        self.assertTrue(cut('crossed the ice.', before='We went down to the river and then we'))
        self.assertFalse(cut('We went down to the river.'))
        self.assertFalse(cut('Okay.'))
        self.assertFalse(cut('"We went down to the river."'))
        self.assertFalse(cut('Crossed the ice, I think', before='We went.', after='Then rested.'), 'a longer row that merely lacks a full stop is not a stump')
        self.assertFalse(cut('我们去河边。'))
        self.assertTrue(cut('我们去'))

    def test_only_findings_on_complete_rows_are_hard(self):
        flags = [{'id': 38, 'category': 'meaning'}, {'id': 295, 'category': 'meaning'}, {'id': 302, 'category': 'meaning'}, {'id': 610, 'category': 'meaning'},
                 {'id': 658, 'category': 'grammar'}, {'id': 44, 'category': 'fragment'}]
        self.assertEqual([flag['id'] for flag in hard_flags(flags, ROWS)], [302, 610])
        self.assertEqual([flag['id'] for flag in hard_flags(flags)], [38, 295, 302, 610, 658], 'without rows only the category decides')

    def test_the_report_marks_a_cut_off_source_and_the_page_says_so(self):
        flags = [{'id': 38, 'category': 'meaning', 'reason': 'ofullständig'}, {'id': 610, 'category': 'meaning', 'reason': 'made saknas'}]
        rows = [{**row, 'sv': 'x', 'start': 1.0, 'end': 2.0} for row in ROWS]
        entries = doubtful_rows(flags, rows)
        self.assertEqual([(entry['id'], entry['source_cut_off']) for entry in entries], [(38, True), (610, False)])
        html = doubtful_report.render_report_content({'job_id': 1, 'media_name': 'x.mkv', 'rows': entries}, 'sv')
        self.assertEqual(html.count('Källraden är avbruten mitt i en mening'), 1)


def flagged_review(count, rows_source, hard_ids, *, final=True):
    rows = [{'id': n, 'start': n * 3.0, 'end': n * 3.0 + 2.5, 'source': rows_source[n], 'sv': f'Rad {n}.'} for n in range(count)]
    job = {'id': 9, 'spoken_swedish_ids': []}

    def assess(candidate, *, audit=None, **kwargs):
        audit.append({'ids': [row['id'] for row in candidate], 'bad_ids': list(hard_ids),
                      'flags': [{'id': i, 'category': 'meaning', 'reason': 'Betydelsen ändras.', 'source_quote': 'x', 'target_quote': 'y'} for i in hard_ids]})
    with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), patch.object(worker, 'assess_translation_quality', side_effect=assess), \
            patch.object(worker, 'repair_flagged_translation', side_effect=lambda job_, rows_, flags, **kwargs: [dict(row) for row in rows_]), patch.object(worker, 'check_cancelled'), \
            patch('fragment_context_review.review_fragments', return_value=[]):
        try:
            worker.review_and_save_translation(job, rows, final=final)
            error = None
        except worker.SourceQualityError as raised:
            error = raised
        saved = json.loads((Path(folder) / 'job-9.json').read_text(encoding='utf-8'))
    return error, saved, job


class GateTests(unittest.TestCase):
    def test_real_unpunctuated_japanese_row_cannot_soften_a_translation_error(self):
        sources = ['This is a complete sentence.'] * 14
        sources[11] = '死ぬためだけに作られた兵器です'
        error, saved, _ = flagged_review(14, sources, [11])
        self.assertIsNotNone(error)
        self.assertEqual(saved['remaining_findings'], {'hard': [11], 'soft': []})

    # rows 20 and 45 are cut off; they are far apart, so they make no cluster (three flagged rows in one block of twelve are translated afresh or refuse the file)
    SOURCES = ['This is a complete sentence.'] * 20 + ['Let us get the out of'] + ['This is a complete sentence.'] * 24 + ['Get'] + ['This is a complete sentence.'] * 14

    def test_a_finding_that_remains_on_a_cut_off_source_is_reported_and_the_file_is_written(self):
        error, saved, job = flagged_review(60, self.SOURCES, [20, 45])
        self.assertIsNone(error)
        self.assertEqual([(item['id'], item['source_cut_off']) for item in job['doubtful_rows']], [(20, True), (45, True)])
        self.assertEqual(saved['status'], 'usable')

    def test_a_finding_on_a_complete_row_still_refuses_the_file_whatever_else_is_cut_off(self):
        error, saved, _ = flagged_review(60, self.SOURCES, [20, 45, 5])
        self.assertIsNotNone(error)
        self.assertEqual(saved['remaining_findings'], {'hard': [5], 'soft': [20, 45]})

    def test_the_extra_rounds_only_repair_complete_rows_and_there_are_three(self):
        _, saved, _ = flagged_review(60, self.SOURCES, [20, 5])
        self.assertEqual(len(saved['extra_repair_reviews']), 3)
        self.assertEqual(worker.EXTRA_REPAIR_ROUNDS, 3)
        _, saved, _ = flagged_review(60, self.SOURCES, [20, 45])
        self.assertNotIn('extra_repair_reviews', saved, 'nothing hard remains: no extra round')

    def test_the_first_attempt_is_unchanged(self):
        error, _, _ = flagged_review(60, self.SOURCES, [20], final=False)
        self.assertIsNotNone(error)


if __name__ == '__main__':
    unittest.main()
