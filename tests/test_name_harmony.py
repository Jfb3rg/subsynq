import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import name_harmony
import worker


def rows(*texts):
    return [{'id': number, 'start': number * 5.0, 'end': number * 5.0 + 3, 'source': text} for number, text in enumerate(texts)]


KB = rows('Häckla är tillbaka.',
          'Hekla är en vampyr.',
          'Nu tror man att Hekla vill hitta porten till Underjorden.',
          'Om vi ska ha en chans mot Ekla måste vi ha det där silverkorset.',
          'Birgitta lät smida ett silverkors.',
          'Det är en sak som jag måste förbereda er på.')
WITNESS = {0: 'Häckla är tillbaka.',
           1: 'Häckla är en vampyr.',
           2: 'Nu tror man att Häckla vill hitta porten till underjorden.',
           3: 'Om vi ska ha en chans mot Häcklan måste vi ha det där silverkorset.'}


class DetectionTests(unittest.TestCase):
    def test_a_name_spelled_three_ways_is_one_conflict(self):
        found = name_harmony.conflicts(KB)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['variants'], {'Ekla': [3], 'Hekla': [2, 1], 'Häckla': [0]},
                         'a sentence-initial word (Häckla, row 0) joins because it is near a name in the middle of a sentence')
        self.assertEqual(name_harmony.rows_to_read(found), [0, 1, 2, 3])

    def test_an_inflection_is_not_another_spelling(self):
        self.assertEqual(name_harmony.conflicts(rows('Då såg vi Häckla.', 'Mot Häcklan måste vi gå.', 'Då kom Häcklas hem.')), [])

    def test_ordinary_words_and_other_names_are_left_alone(self):
        texts = rows('Släpp ondskan fri.', 'Vi ska släppa den.', 'Då kom Eva och Ida hem.', 'Pappa sa det. Pappas hus.', 'Då kom Birgitta.', 'Birgitta log.')
        self.assertEqual(name_harmony.conflicts(texts), [], 'Släpp/släppa occur in lower case, Eva/Ida are two different short names')

    def test_words_that_only_start_a_sentence_are_never_names(self):
        """Real run on Nelly Rapp: 'Stjärten.' / 'Skärten.' and 'Med ...' / 'Men ...' only ever start a sentence."""
        texts = rows('Stjärten.', 'Skärten. Måste det vara.', 'Med krafter farligare än vi någonsin skådat.', 'Men vems stjärt?')
        self.assertEqual(name_harmony.conflicts(texts), [])

    def test_a_transcript_without_names_or_rows_is_fine(self):
        self.assertEqual(name_harmony.conflicts([]), [])
        self.assertEqual(name_harmony.conflicts(rows('det är bra', 'ja')), [])

    def test_distances_follow_the_length_of_the_name(self):
        self.assertTrue(name_harmony.near('Hekla', 'Häckla'))
        self.assertTrue(name_harmony.near('Ekla', 'Hekla'))
        self.assertFalse(name_harmony.near('Ekla', 'Häckla'), 'three edits')
        self.assertFalse(name_harmony.near('Eva', 'Ida'), 'two edits is too much for a short name')
        self.assertFalse(name_harmony.near('Häckla', 'Häcklan'), 'the same name inflected')


class HarmonizeTests(unittest.TestCase):
    def run_harmony(self, texts=KB, witness=WITNESS):
        clusters = name_harmony.conflicts(texts)
        return name_harmony.harmonize(texts, clusters, witness)

    def test_the_spelling_the_second_reading_is_closest_to_replaces_the_others(self):
        harmonized, changes = self.run_harmony()
        self.assertEqual([row['source'] for row in harmonized][:4], [
            'Häckla är tillbaka.', 'Häckla är en vampyr.', 'Nu tror man att Häckla vill hitta porten till Underjorden.',
            'Om vi ska ha en chans mot Häckla måste vi ha det där silverkorset.'])
        self.assertEqual(changes, [{'row': 3, 'from': 'Ekla', 'to': 'Häckla'}, {'row': 2, 'from': 'Hekla', 'to': 'Häckla'},
                                   {'row': 1, 'from': 'Hekla', 'to': 'Häckla'}])
        self.assertEqual(harmonized[4]['source'], KB[4]['source'])
        self.assertEqual(KB[1]['source'], 'Hekla är en vampyr.', 'the input rows are not changed')

    def test_the_real_nelly_rapp_readings_settle_it(self):
        """Dev job 57 (KB-Whisper) and the large-v3 reading of just those rows, as they came out: row 9's own reading has no name at all."""
        texts = rows('Häckla är tillbaka.', 'Hekla är en vampyr.', 'Med krafter farligare än vi någonsin skådat.',
                     'Nu tror man att Hekla vill hitta porten till underjorden.', 'Om vi ska ha en chans mot Ekla måste vi ha silverkorset.', 'Stjärten.', 'Skärten. Måste det vara.')
        witness = {0: 'Häcklar är tillbaka.', 1: 'Jag är glad att jag är tillbaka.', 2: 'Pyr är kraften farligare än vi någonsin skådat.',
                   3: 'Och synskordat. Men nu tror man att Häckla vill hitta porten till underjorden.',
                   4: 'Silverkors. Om vi ska ha en chans mot häcklar måste vi ha det där silverkorset.', 5: 'Skärten.', 6: 'Skärten, skärten måste du ha.'}
        harmonized, changes = self.run_harmony(texts, witness)
        self.assertEqual([row['source'] for row in harmonized], [
            'Häckla är tillbaka.', 'Häckla är en vampyr.', 'Med krafter farligare än vi någonsin skådat.',
            'Nu tror man att Häckla vill hitta porten till underjorden.', 'Om vi ska ha en chans mot Häckla måste vi ha silverkorset.',
            'Stjärten.', 'Skärten. Måste det vara.'])
        self.assertEqual(len(changes), 3, 'Stjärten is an ordinary word and must stay')

    def test_an_inflected_wrong_spelling_keeps_its_ending(self):
        texts = rows('Då kom Häckla.', 'Då kom Hekla.', 'Mot Heklan måste vi gå.')
        witness = {0: 'Då kom Häckla.', 1: 'Då kom Häckla.', 2: 'Mot Häcklan måste vi gå.'}
        harmonized, _ = self.run_harmony(texts, witness)
        self.assertEqual(harmonized[2]['source'], 'Mot Häcklan måste vi gå.')

    def test_nothing_changes_without_a_second_reading_that_repeats_a_close_spelling(self):
        for witness in ({}, {1: 'Häckla är en vampyr.'}, {0: 'Hjälp.', 1: 'Hjälp.', 2: 'Hjälp.', 3: 'Hjälp.'}):
            harmonized, changes = self.run_harmony(witness=witness)
            self.assertEqual(changes, [], witness)
            self.assertEqual(harmonized, KB)

    def test_a_row_whose_own_second_reading_holds_the_old_spelling_keeps_it(self):
        witness = {**WITNESS, 3: 'Om vi ska ha en chans mot Ekla måste vi ha silverkorset.'}
        harmonized, changes = self.run_harmony(witness=witness)
        self.assertEqual(harmonized[3]['source'], KB[3]['source'])
        self.assertNotIn(3, [change['row'] for change in changes])
        self.assertEqual(harmonized[1]['source'], 'Häckla är en vampyr.')

    def test_a_row_whose_second_reading_has_no_name_follows_the_agreed_spelling(self):
        witness = {**WITNESS, 1: 'Jag är glad att jag är tillbaka.'}
        harmonized, changes = self.run_harmony(witness=witness)
        self.assertEqual(harmonized[1]['source'], 'Häckla är en vampyr.')

    def test_whole_words_only(self):
        texts = rows('Då kom Häckla hem.', 'Hekla är här.', 'Heklaverket brann.', 'Nu kommer Hekla igen.')
        witness = {0: 'Då kom Häckla hem.', 1: 'Häckla är här.', 2: 'Häcklaverket brann.', 3: 'Nu kommer Häckla igen.'}
        harmonized, _ = self.run_harmony(texts, witness)
        self.assertEqual([row['source'] for row in harmonized], ['Då kom Häckla hem.', 'Häckla är här.', 'Heklaverket brann.', 'Nu kommer Häckla igen.'])


class WorkerStepTests(unittest.TestCase):
    def test_a_transcript_without_conflicts_costs_nothing(self):
        job = {'id': 1}
        witness = MagicMock()
        result = worker.harmonize_names(job, Path('/media/x.mkv'), rows('Det är bra.', 'Ja.'), witness=witness)
        self.assertEqual([row['source'] for row in result], ['Det är bra.', 'Ja.'])
        witness.assert_not_called()
        self.assertEqual(job['name_harmony'], {'conflicts': [], 'changes': []})

    def test_a_conflict_is_settled_with_the_second_reading_and_recorded(self):
        job = {'id': 1}
        asked = []

        def witness(job_, media, rows_, ids):
            asked.append(ids)
            return WITNESS
        with patch.object(worker, 'update_job'):
            result = worker.harmonize_names(job, Path('/media/x.mkv'), KB, witness=witness)
        self.assertEqual(asked, [[0, 1, 2, 3]])
        self.assertEqual(result[1]['source'], 'Häckla är en vampyr.')
        self.assertEqual(len(job['name_harmony']['changes']), 3)
        self.assertEqual(job['name_harmony']['readings']['3'], WITNESS[3])
        json.dumps(job['name_harmony'])

    def test_a_failing_second_reading_leaves_the_rows_alone_and_never_fails_the_job(self):
        job = {'id': 1}

        def broken(*args):
            raise RuntimeError('ffmpeg dog')
        with patch.object(worker, 'update_job'):
            result = worker.harmonize_names(job, Path('/media/x.mkv'), KB, witness=broken)
        self.assertEqual(result, KB)
        self.assertIn('ffmpeg dog', job['name_harmony']['error'])

    def test_a_cancelled_job_is_not_swallowed(self):
        def cancelled(*args):
            raise worker.JobCancelled('stoppad')
        with patch.object(worker, 'update_job'), self.assertRaises(worker.JobCancelled):
            worker.harmonize_names({'id': 1}, Path('/media/x.mkv'), KB, witness=cancelled)

    def test_only_jobs_that_kb_whisper_read_are_checked_and_the_result_is_saved(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn("if (job.get('swedish_reader') or {}).get('chosen'):\n        raw_rows = harmonize_names(job, media, raw_rows)", source)
        self.assertLess(source.index('raw_rows, source_choices = choose_between_readings(job, raw_rows)'),
                        source.index('raw_rows = harmonize_names(job, media, raw_rows)'), 'after the choice between readings, which can restore an old spelling')
        self.assertIn("'name_harmony': job.get('name_harmony'),", source)

    def test_the_second_reading_cuts_a_window_around_each_row(self):
        calls, model = [], MagicMock()
        model.transcribe.return_value = (iter([MagicMock(text=' Häckla är här. ')]), None)
        with patch.object(worker, 'WhisperModel', return_value=model) as loaded, patch.object(worker.subprocess, 'run', side_effect=lambda command, **kw: calls.append(command)), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'trim_cpu_allocator'):
            readings = worker.name_witness_readings({'id': 1}, Path('/media/x.mkv'), rows('Hekla är här.', 'Ja.'), [0])
        self.assertEqual(readings, {0: 'Häckla är här.'})
        self.assertEqual(loaded.call_args.args[0], 'large-v3')
        self.assertEqual(model.transcribe.call_args.kwargs['language'], 'sv')
        command = calls[0]
        self.assertEqual((command[command.index('-ss') + 1], command[command.index('-t') + 1]), ('0.0', '4.0'))


if __name__ == '__main__':
    unittest.main()
