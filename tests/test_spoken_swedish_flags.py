import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


def spoken(number, text, start=None):
    start = number * 3.0 if start is None else start
    return {'id': number, 'start': start, 'end': start + 2, 'source': text, 'sv': text}


def flag(row, reason, **overrides):
    return {'id': row['id'], 'category': 'grammar', 'reason': reason,
            'source_quote': row['source'], 'target_quote': row['sv'], **overrides}


class SpokenSwedishFlagTests(unittest.TestCase):
    """Real case: Nelly Rapp, dev jobs 57, 60, 61 and 62. The reviewer called "Men vems stjärt?" a word error in a Swedish line that is kept exactly as
    spoken. The repair cannot change such a line and the review of the same text said the same again, so the job failed four times on a valid line."""

    def setUp(self):
        rendering = patch.object(worker, 'render_whole_cue_meanings',
                                 side_effect=lambda job, rows, flags, answer, *args, **kwargs: answer)
        rendering.start()
        self.addCleanup(rendering.stop)
        self.rows = [spoken(20, 'Hon är här.'), spoken(21, 'Mamma, det här har blivit så stort.'), spoken(22, 'Men vems stjärt?'),
                     spoken(23, 'Vi delar upp oss.'), spoken(24, 'Du behöver inte oroa dig.'), spoken(25, 'Nyckeln är borta.'),
                     spoken(26, 'Vi renoverar lite bara.'), spoken(27, 'Nu går vi.')]
        self.false_flag = flag(self.rows[2], 'Ordfel i stjärt: det korrekta ordet är stjärt, men stavat fel i målspråket.')

    def run_review(self, responses, job):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch.object(worker, 'QUALITY_REPORTS', Path(folder.name)), \
                patch.object(worker, 'call_ollama', side_effect=responses) as model, patch.object(worker, 'check_cancelled'):
            try:
                worker.review_and_save_translation(job, self.rows)
                error = None
            except worker.SourceQualityError as raised:
                error = raised
            saved = json.loads((Path(folder.name) / f"job-{job['id']}.json").read_text())
        return model, saved, error

    def test_a_remark_on_a_line_kept_as_spoken_does_not_fail_the_job_and_is_not_repaired(self):
        job = {'id': 9, 'spoken_swedish_ids': [row['id'] for row in self.rows]}
        before = [dict(row) for row in self.rows]
        model, saved, error = self.run_review([{'response': json.dumps({'flags': [self.false_flag]})}], job)
        self.assertIsNone(error)
        self.assertEqual(model.call_count, 1, 'one review, no repair, no second review of the same text')
        self.assertEqual(saved['status'], 'usable')
        self.assertEqual(self.rows, before, 'the spoken line is untouched')
        self.assertEqual([(item['id'], item['category']) for item in saved['spoken_swedish_flags']], [(22, 'grammar')])
        self.assertIn('behålls som det sades', saved['spoken_swedish_flags'][0]['note'])

    def test_the_same_remark_on_a_line_that_was_not_kept_as_spoken_still_goes_to_repair_and_still_fails_when_it_persists(self):
        """A line that is only equal to its source because the translator left it alone (untranslated) is not spoken Swedish."""
        job = {'id': 9, 'spoken_swedish_ids': [20, 21, 23, 24, 25, 26, 27]}
        responses = [{'response': json.dumps({'flags': [self.false_flag]})},
                     {'response': json.dumps({'rows': [{'id': 22, 'sv': 'Men vems stjärt?'}]})},
                     # The grammar task left the line unchanged, so the repair that reads the remark gets it too (0.8.323).
                     {'response': json.dumps({'rows': [{'id': 22, 'sv': 'Men vems stjärt?'}]})},
                     {'response': json.dumps({'flags': [self.false_flag]})}]
        model, saved, error = self.run_review(responses, job)
        self.assertIsNotNone(error)
        self.assertEqual(model.call_count, 4)
        self.assertEqual(saved['status'], 'unusable')
        self.assertNotIn('spoken_swedish_flags', saved)

    def test_without_any_record_of_spoken_lines_nothing_changes(self):
        responses = [{'response': json.dumps({'flags': [self.false_flag]})},
                     {'response': json.dumps({'rows': [{'id': 22, 'sv': 'Men vems stjärt?'}]})},
                     # The grammar task left the line unchanged, so the repair that reads the remark gets it too (0.8.323).
                     {'response': json.dumps({'rows': [{'id': 22, 'sv': 'Men vems stjärt?'}]})},
                     {'response': json.dumps({'flags': [self.false_flag]})}]
        _, saved, error = self.run_review(responses, {'id': 9})
        self.assertIsNotNone(error)
        self.assertEqual(saved['status'], 'unusable')

    def test_a_translated_line_next_to_a_spoken_one_is_still_repaired_and_the_spoken_one_is_not_sent_to_the_repair(self):
        translated = {'id': 1, 'start': 1.0, 'end': 2.5, 'source': 'This is my story.', 'sv': 'Det här är mitt berättelse.'}
        self.rows.insert(0, translated)
        job = {'id': 9, 'spoken_swedish_ids': [row['id'] for row in self.rows if row['id'] != 1]}
        own_flag = flag(translated, 'Fel genus: berättelse är ett ord av n-genus.')
        repaired = {'id': 1, 'sv': 'Det här är min berättelse.'}
        responses = [{'response': json.dumps({'flags': [own_flag, self.false_flag]})},
                     {'response': json.dumps({'rows': [repaired]})},
                     {'response': json.dumps({'flags': []})}]      # the re-review covers the repaired line and its neighbours only
        model, saved, error = self.run_review(responses, job)
        self.assertIsNone(error, 'the translated line was fixed; only the unrepairable remark on the spoken line is left')
        self.assertEqual(saved['status'], 'usable')
        self.assertEqual(self.rows[0]['sv'], 'Det här är min berättelse.')
        self.assertEqual(self.rows[3]['sv'], 'Men vems stjärt?')
        repair_prompt = json.loads(model.call_args_list[1].args[0]['prompt'])
        self.assertEqual(repair_prompt['requested_ids'] if 'requested_ids' in repair_prompt else
                         [row['id'] for row in repair_prompt['rows']], [1])

    def test_a_translated_line_that_still_fails_after_the_repair_fails_the_job_even_with_spoken_lines_around_it(self):
        translated = {'id': 1, 'start': 1.0, 'end': 2.5, 'source': 'This is my story.', 'sv': 'Det här är mitt berättelse.'}
        self.rows.insert(0, translated)
        job = {'id': 9, 'spoken_swedish_ids': [row['id'] for row in self.rows if row['id'] != 1]}
        own_flag = flag(translated, 'Fel genus: berättelse är ett ord av n-genus.')
        still = flag({**translated, 'sv': 'Det här är min berättelse.'}, 'Fortfarande fel genus enligt granskaren.')
        responses = [{'response': json.dumps({'flags': [own_flag]})},
                     {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Det här är min berättelse.'}]})},
                     {'response': json.dumps({'flags': [still]})}]
        _, saved, error = self.run_review(responses, job)
        self.assertIsNotNone(error)
        self.assertEqual(saved['status'], 'unusable')

    def test_as_many_bad_spoken_lines_as_the_files_limit_still_fail_the_file(self):
        """The protection against a garbled Swedish transcript stays: remarks on spoken lines count against the file's limit (max(2, 5 %))."""
        job = {'id': 9, 'spoken_swedish_ids': [row['id'] for row in self.rows]}
        flags = [self.false_flag, flag(self.rows[3], 'Meningen är avbruten och obegriplig.', category='fragment'),
                 flag(self.rows[5], 'Orden hänger inte ihop alls.')]
        model, saved, error = self.run_review([{'response': json.dumps({'flags': flags})}], job)
        self.assertIsNotNone(error)
        self.assertEqual(model.call_count, 1, 'refused by the file limit, not sent to a repair of spoken lines')
        self.assertEqual(saved['status'], 'unusable')
        self.assertIn('tydligt trasiga', saved['reason'])

    def test_an_english_target_never_exempts_anything(self):
        job = {'id': 9, 'spoken_swedish_ids': [row['id'] for row in self.rows]}
        english = [{'id': row['id'], 'start': row['start'], 'end': row['end'], 'source': row['source'], 'en': row['source']}
                   for row in self.rows]
        responses = [{'response': json.dumps({'flags': [flag({**english[2], 'sv': english[2]['en']}, 'Wrong word.',
                                                              target_quote='Men vems stjärt?')]})},
                     {'response': json.dumps({'rows': [{'id': 22, 'en': 'Men vems stjärt?'}]})},
                     # The grammar task left the line unchanged, so the repair that reads the remark gets it too (0.8.323).
                     {'response': json.dumps({'rows': [{'id': 22, 'en': 'Men vems stjärt?'}]})},
                     {'response': json.dumps({'flags': [flag({**english[2], 'sv': english[2]['en']}, 'Wrong word.',
                                                             target_quote='Men vems stjärt?')]})}]
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'call_ollama', side_effect=responses) as model, patch.object(worker, 'check_cancelled'):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation(job, english, target_key='en', target_language='English')
        self.assertEqual(model.call_count, 4)


class RememberingSpokenLinesTests(unittest.TestCase):
    def rows(self):
        return [{'id': number, 'start': number * 2.0, 'end': number * 2.0 + 1.5, 'source': text}
                for number, text in enumerate(['Vart ska du?', 'This is my story.', 'Hahaha!'])]

    def translate(self, job, kept, rows=None):
        translations = {0: 'Vart är du på väg?', 1: 'Det här är min berättelse.', 2: 'Ha ha ha då!'}

        def fake_call(request):
            payload = json.loads(request['prompt'])
            return {'response': json.dumps({'rows': [{'id': row['id'], 'sv': translations[row['id']]} for row in payload['rows']]})}
        with patch.object(worker, 'identify_swedish_rows', return_value=kept), patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'call_ollama', side_effect=fake_call), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            state.device = 'nvidia'
            return worker.translate(job, rows or self.rows())

    def test_translate_remembers_the_rows_it_kept_as_spoken_across_calls(self):
        job = {'id': -1}
        self.translate(job, {0, 2})
        self.assertEqual(job['spoken_swedish_ids'], [0, 2])
        self.translate(job, {1})
        self.assertEqual(job['spoken_swedish_ids'], [0, 1, 2], 'the retry adds to the list')

    def test_nothing_is_recorded_when_no_row_is_kept(self):
        job = {'id': -1}
        self.translate(job, set())
        self.assertNotIn('spoken_swedish_ids', job)

    def test_an_english_pass_records_nothing(self):
        job = {'id': -1}
        with patch.object(worker, 'identify_swedish_rows') as identify, patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'call_ollama', side_effect=lambda request: {'response': json.dumps({'rows': [
                    {'id': row['id'], 'en': 'Translated: ' + row['source']} for row in json.loads(request['prompt'])['rows']]})}), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            state.device = 'nvidia'
            worker.translate(job, self.rows(), target_key='en', target_language='English')
        identify.assert_not_called()
        self.assertNotIn('spoken_swedish_ids', job)

    def test_each_translation_run_starts_with_an_empty_list(self):
        """Two readings of the same file (KB-Whisper, then the large-v3 retry) number their rows differently."""
        job = {'id': 9, 'spoken_swedish_ids': [3, 7, 8]}
        seen = []
        rows = self.rows()
        with patch.object(worker, 'restore_source_punctuation'), patch.object(worker, 'translate', return_value=rows), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'review_and_save_translation', side_effect=lambda job_, rows_: seen.append(list(job_['spoken_swedish_ids']))):
            worker.translate_and_review(job, rows)
        self.assertEqual(seen, [[]])


if __name__ == '__main__':
    unittest.main()
