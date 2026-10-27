import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from translation_qa import non_latin_flags, non_latin_runs

# Guardians (2017) (dev job 122) and THE PILOT (job 93): the translator was told to copy names unchanged, so Cyrillic names stayed in the Swedish text,
# and the reviewer listed them as "names_kept" instead of flagging them.
GUARDIANS = [{'id': 0, 'start': 8.47, 'end': 12.01, 'source': 'За две недели я должна объехать весь бывший Советский Союз,',
              'sv': 'Jag måste resa genom hela det forna Советский Союз på två veckor.'},
             {'id': 1, 'start': 12.01, 'end': 14.69, 'source': 'собрать команду сверхлюдей и остановить сверхзлодеев?', 'sv': 'samla ett team av övermänniskor och stoppa superskurkar?'},
             {'id': 6, 'start': 34.0, 'end': 36.0, 'source': 'Мы такие же, как ты, Лер.', 'sv': 'Vi är precis som du, Лер.'},
             {'id': 7, 'start': 40.0, 'end': 42.0, 'source': 'Меня зовут Николай, кстати.', 'sv': 'Jag heter Николай, förresten.'}]


class NonLatinTests(unittest.TestCase):
    def test_the_real_leftovers_are_found_as_runs(self):
        self.assertEqual(non_latin_runs(GUARDIANS[0]['sv']), ['Советский Союз'])
        self.assertEqual(non_latin_runs(GUARDIANS[2]['sv']), ['Лер'])
        self.assertEqual(non_latin_runs(GUARDIANS[3]['sv']), ['Николай'])
        self.assertEqual(non_latin_runs('Hon sa 你好 och 안녕 och مرحبا.'), ['你好', '안녕', 'مرحبا'])

    def test_swedish_and_other_latin_letters_and_symbols_are_fine(self):
        for text in ('Åsa köpte smörgåsar, ärtor och ölet.', 'Café Müller i São Paulo, señor Ñandú, Straße.', '♪ La la la ♪ 100 % — ok!', 'Vi åkte till Sovjetunionen.', ''):
            self.assertEqual(non_latin_runs(text), [], text)

    def test_the_flag_is_a_hard_untranslated_remark_the_repair_can_anchor_on(self):
        flags = non_latin_flags(GUARDIANS)
        self.assertEqual([(flag['id'], flag['category'], flag['target_quote']) for flag in flags], [(0, 'untranslated', 'Советский Союз'), (6, 'untranslated', 'Лер'), (7, 'untranslated', 'Николай')])
        self.assertIn('latinska bokstäver', flags[0]['reason'])
        self.assertEqual(non_latin_flags([{'id': 1, 'sv': 'Hej då.'}]), [])
        self.assertEqual(non_latin_flags([{'id': 1, 'en': 'Привет'}], 'en')[0]['id'], 1)

    def test_the_translator_is_told_to_write_latin_letters_and_is_not_told_to_copy_a_cyrillic_name(self):
        rules = worker.output_rules('sv')
        self.assertIn('ONLY with Latin letters', rules)
        self.assertIn('Лер -> Ler', rules)
        self.assertNotIn('Swedish', worker.output_rules('en'), 'the English pass gets no Swedish rule')
        self.assertEqual(worker.name_instruction([{'source': 'Привет, Лер.'}]), '', 'a name in another script is never copied')
        self.assertIn('Gunn', worker.name_instruction([{'source': 'Hello there.'}, {'source': 'Mister Gunn, I presume?'}]))
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count('output_rules(target_key)'), 7, 'the definition, repairs, phrase render, translation, draft review and independent review')


def clean_reviewer(request):
    sample = json.loads(request['prompt'])['rows']
    return {'response': json.dumps({'comparisons': [{'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'], 'meaning_preserved': True}
                                                    for row in sample], 'flags': []})}


class GateTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.object(worker, 'FOCUSED_REVIEW', True), patch.object(worker, 'release_language_model_before_asr'),
                        patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama', side_effect=clean_reviewer)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_clean_reviewer_still_gets_the_leftover_flagged_in_the_audit(self):
        audit = []
        worker.assess_translation_quality([dict(row) for row in GUARDIANS], audit=audit, whole_file=True)
        flagged = [entry for entry in audit if entry.get('deterministic') == 'non_latin_and_units']
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]['bad_ids'], [0, 6, 7])
        self.assertTrue(all(item['meaning_preserved'] is False for item in flagged[0]['comparisons']))
        audit = []
        latin = {0: 'Jag måste resa genom hela det forna Sovjetunionen på två veckor.', 6: 'Vi är precis som du, Ler.', 7: 'Jag heter Nikolaj, förresten.'}
        worker.assess_translation_quality([{**row, 'sv': latin.get(row['id'], row['sv'])} for row in GUARDIANS], audit=audit, whole_file=True)
        self.assertEqual([entry for entry in audit if entry.get('deterministic')], [])

    def run_gate(self, repaired_text, final):
        rows = [dict(row) for row in GUARDIANS]

        def repair(job, candidate, flags, **kwargs):
            return [{**row, 'sv': repaired_text(row)} for row in candidate]
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'repair_flagged_translation', side_effect=repair), patch.object(worker, 'review_fragments', create=True), \
                patch.object(worker, 'translate', side_effect=lambda job, part, **kwargs: [{**row, 'sv': next(item['sv'] for item in GUARDIANS if item['id'] == row['id'])} for row in part]):      # three of four rows flagged = a cluster: the block is translated afresh
            worker.review_and_save_translation({'id': 9, 'spoken_swedish_ids': []}, rows, final=final)
            return rows

    def test_a_repair_that_writes_latin_letters_passes(self):
        fixes = {0: 'Jag måste resa genom hela det forna Sovjetunionen på två veckor.', 6: 'Vi är precis som du, Ler.', 7: 'Jag heter Nikolaj, förresten.'}
        rows = self.run_gate(lambda row: fixes.get(row['id'], row['sv']), final=False)
        self.assertEqual([row['sv'] for row in rows if row['id'] in fixes], list(fixes.values()))

    def test_a_repair_that_leaves_cyrillic_refuses_the_file_even_on_the_last_attempt(self):
        for final in (False, True):
            with self.assertRaisesRegex(worker.SourceQualityError, 'ett annat skrivsystem'):
                self.run_gate(lambda row: row['sv'], final=final)

    def test_the_gate_is_in_settle_and_the_audit_entry_is_added_before_the_parallel_split(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index("'deterministic': 'non_latin_and_units'"), source.index('workers = parallel_workers(len(rows)) if whole_file'))
        self.assertIn('Letters of another script in a Swedish text are never let through.', source)


if __name__ == '__main__':
    unittest.main()
