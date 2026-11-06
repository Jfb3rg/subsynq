import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from translation_qa import SWEDISH_MEANING_RULE, deterministic_flags, file_consistency_flags


def row(row_id, source, sv, start=None):
    start = float(row_id * 3) if start is None else start
    return {'id': row_id, 'start': start, 'end': start + 2, 'source': source, 'sv': sv}


class FileConsistencyTests(unittest.TestCase):
    """regression case(Example Series 4x03), approved with "Söndag maj.", "BUD/S" for "bud" and the Norwegian "likegyldigt"."""

    def ids(self, rows):
        return [(flag['id'], flag['category']) for flag in file_consistency_flags(rows)]

    def test_a_name_the_file_keeps_is_not_translated_on_a_row_of_its_own(self):
        rows = [row(0, 'Including Red and Sun May.', 'Inklusive Red och Sun May.'), row(1, 'Sun May.', 'Söndag maj.'),
                row(2, 'Red?', 'Röd?'), row(3, 'Red?', 'Red?')]
        self.assertEqual(self.ids(rows), [(1, 'meaning'), (2, 'meaning')])

    def test_names_inside_sentences_and_near_spellings_are_left_alone(self):
        # Replay over 242 saved files: these are correct Swedish and must never be flagged.
        rows = [row(0, 'We crossed the Ray Mountains with Joe.', 'Vi korsade Ray Mountains med Joe.'),
                row(1, 'It is crazy to cross the Ray Mountains.', 'Det är galet att korsa Raybergen.'),
                row(2, 'Come here, Lucy.', 'Kom hit, Lucy.'), row(3, "Lucy's?", 'Lucys?'),
                row(4, 'Tell me, Canada.', 'Säg, Canada.'), row(5, 'Canada.', 'Kanada.')]
        self.assertEqual(self.ids(rows), [])

    def test_bud_said_to_someone_is_not_the_seal_training(self):
        rows = [row(0, 'Did you forget about that not personal bud?', 'Har du glömt att det inte är personligt, BUD/S?'),
                row(1, "We've been in the teams together since Bud's.", 'Vi har varit i teamen tillsammans sedan BUD/S.')]
        self.assertEqual(self.ids(rows), [(0, 'meaning')])

    def test_a_norwegian_word_in_the_swedish_is_flagged(self):
        rows = [row(0, "It's whatever.", 'Det är likegyldigt.'), row(1, "It's whatever.", 'Det spelar ingen roll.'),
                row(2, 'Jeg vet ikke.', 'Jeg vet ikke.')]          # a Norwegian source left as spoken is not this check's business
        self.assertEqual(self.ids(rows), [(0, 'untranslated')])

    def test_the_check_runs_with_the_other_fixed_checks(self):
        self.assertIn(1, [flag['id'] for flag in deterministic_flags([row(0, 'And Sun May.', 'Och Sun May.'), row(1, 'Sun May.', 'Söndag maj.')])])


class CompoundUntranslatedTests(unittest.TestCase):
    """regression case(SampleSeries) failed: the reviewer rightly said "entanglement" was left in "kvantentanglementgrej"."""

    SAMPLE = [row(80, 'Anyway, they invented some kind of quantum entanglement thing,', 'Hur som helst, de uppfann någon slags kvantentanglementgrej,')]

    def verdict(self, source_quote, target_quote):
        return {'flags': [{'id': 80, 'category': 'untranslated', 'source_quote': source_quote, 'target_quote': target_quote,
                           'reason': "Det engelska ordet 'entanglement' har lämnats kvar istället för att översättas till 'sammanflätning'."}]}

    def test_a_source_word_inside_a_compound_is_accepted_as_evidence(self):
        flags = worker.validate_translation_flags(self.verdict('entanglement', 'kvantentanglementgrej'), self.SAMPLE, 'sv')
        self.assertEqual([flag['id'] for flag in flags], [80])

    def test_short_or_absent_words_are_still_refused(self):
        sample = [row(1, 'Get in the tank.', 'Kliv in i tanken.')]
        with self.assertRaises(ValueError):           # "tank" is four letters: inflected Swedish words must not count
            worker.validate_translation_flags({'flags': [{'id': 1, 'category': 'untranslated', 'source_quote': 'tank',
                                                          'target_quote': 'tanken', 'reason': 'Ordet tank är oöversatt här.'}]}, sample, 'sv')
        with self.assertRaises(ValueError):
            worker.validate_translation_flags(self.verdict('quantum', 'kvantentanglementgrej'), self.SAMPLE, 'sv')


class FinalAttemptRereadTests(unittest.TestCase):
    """regression case(Example Series 4x02): "Yes, sir, I'm three." was first doubted by the final review and never read again."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.reports = Path(folder.name)
        self.rows = [row(409, 'On three, everyone lays down fire.', ''), row(411, "Yes, sir, I'm three.", ''),
                     row(412, 'All right, here we go one two', '')]
        self.translated = [{**r, 'sv': sv} for r, sv in zip(self.rows, ['På tre ger alla eld.', 'Ja, befäl, jag är tre.', 'Okej, nu kör vi, ett, två.'])]

    def save_report(self, hard):
        (self.reports / 'job-64.json').write_text(json.dumps({'remaining_findings': {'hard': hard, 'soft': []}}))

    def run_final(self, job, changed_text, review_error=None):
        def reread(job, rows, ids):
            job['reread_rows'] = [{'id': row_id} for row_id in ids]
            if changed_text is None:
                return []
            next(r for r in rows if r['id'] == 411)['source'] = changed_text
            return [411]
        with patch.object(worker, 'QUALITY_REPORTS', self.reports), patch.object(worker, 'REREAD_ENABLED', True), \
                patch.object(worker, 'reread_flagged_rows', side_effect=reread) as reader, \
                patch.object(worker, 'retry_translation', side_effect=lambda job, rows, **k: [{**r, 'sv': 'Ja, befäl. På tre?'} for r in rows]) as retry, \
                patch.object(worker, 'review_and_save_translation', side_effect=review_error) as review, \
                patch.object(worker, 'check_cancelled'):
            result = worker.reread_after_final_attempt(job, self.rows, self.translated, worker.SourceQualityError('Undertexten har kvar konkreta fel.'))
        return result, reader, retry, review

    def test_a_row_refused_only_by_the_final_review_is_read_again_and_translated_again(self):
        self.save_report([411])
        job = {'id': 64, 'reread_rows': [{'id': 10}]}
        result, reader, retry, review = self.run_final(job, 'Yes, sir! On three?')
        self.assertEqual(reader.call_args.args[2], [411])
        self.assertEqual([r['id'] for r in retry.call_args.args[1]], [411])
        self.assertEqual(retry.call_args.args[1][0]['source'], 'Yes, sir! On three?')
        self.assertEqual(retry.call_args.kwargs['refused'], {})                  # the old wordings belong to the old source
        self.assertEqual(result[1]['sv'], 'Ja, befäl. På tre?')
        self.assertEqual([result[0], result[2]], [self.translated[0], self.translated[2]])
        self.assertTrue(review.call_args.kwargs['final'])                        # the same final review judges the whole file
        self.assertEqual([r['id'] for r in job['reread_rows']], [10, 411])      # the earlier records are kept for the report

    def test_nothing_new_to_read_or_no_change_refuses_the_file_as_before(self):
        self.save_report([10])
        with self.assertRaises(worker.TranslationQualityError):
            self.run_final({'id': 64, 'reread_rows': [{'id': 10}]}, 'unused')     # already read between the attempts
        self.save_report([411])
        with self.assertRaises(worker.TranslationQualityError) as refused:
            self.run_final({'id': 64, 'reread_rows': []}, None)                  # the readers do not agree on anything else
        self.assertIn('efter två översättningsförsök', str(refused.exception))

    def test_a_file_still_refused_after_the_reread_says_so(self):
        self.save_report([411])
        with self.assertRaises(worker.TranslationQualityError) as refused:
            self.run_final({'id': 64, 'reread_rows': []}, 'Yes, sir! On three?', review_error=worker.SourceQualityError('Fel kvar.'))
        self.assertIn('och omläsning', str(refused.exception))


class RuleTextTests(unittest.TestCase):
    def test_the_rules_that_caused_errors_in_jobs_61_and_63_are_narrowed(self):
        self.assertIn('never BUD/S', SWEDISH_MEANING_RULE)
        self.assertIn('"Och X då?"', SWEDISH_MEANING_RULE)
        self.assertIn('Iklädd en kvinnas kjol.', SWEDISH_MEANING_RULE)
        self.assertIn('"Hallå!"', SWEDISH_MEANING_RULE)
        self.assertIn('likegyldig', SWEDISH_MEANING_RULE)


if __name__ == '__main__':
    unittest.main()
