import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker

SOURCES = ['Guten Tag, mein Späkel!', 'Vad fan?', 'är jävlar!', 'Where are you going?', 'Hahaha!', 'Titta inte ner!']
LANGUAGES = {0: 'de', 1: 'sv', 2: 'sv', 3: 'en', 4: 'none', 5: 'sv'}


def rows():
    return [{'id': i, 'start': i * 2, 'end': i * 2 + 1, 'source': text} for i, text in enumerate(SOURCES)]


def language_answer(request, languages=LANGUAGES):
    ids = [row['id'] for row in json.loads(request['prompt'])['rows']]
    return {'response': json.dumps({'rows': [{'id': i, 'language': languages[i]} for i in ids]})}


class RowSelectionTests(unittest.TestCase):
    def test_only_rows_the_model_calls_swedish_and_that_hold_no_foreign_word_are_kept(self):
        batch = rows() + [{'id': 6, 'source': 'You are är Swedish?'}, {'id': 7, 'source': 'Ich bin so müde'},
                          {'id': 8, 'source': '...'}, {'id': 9, 'source': 'Hej då'}]
        languages = {**LANGUAGES, 6: 'sv', 7: 'sv', 8: 'sv'}
        self.assertEqual(worker.swedish_rows_to_keep(batch, languages), {1, 2, 5})
        self.assertEqual(worker.swedish_rows_to_keep(batch, {}), set(), 'unclassified rows are translated')
        self.assertEqual(worker.swedish_rows_to_keep(batch, {1: ' SV '}), {1}, 'the code is read leniently')

    def test_danish_norwegian_and_unknown_codes_are_never_kept(self):
        batch = [{'id': 0, 'source': 'Jeg ved det ikke.'}, {'id': 1, 'source': 'Hvor er du fra?'}]
        self.assertEqual(worker.swedish_rows_to_keep(batch, {0: 'da', 1: 'no'}), set())


class IdentifyTests(unittest.TestCase):
    def test_the_language_model_is_asked_once_per_batch_with_every_id_constrained(self):
        requests = []

        def call(request):
            requests.append(request)
            return language_answer(request)

        kept = worker.identify_swedish_rows(rows(), call)
        self.assertEqual(kept, {1, 2, 5})
        self.assertEqual(len(requests), 1)
        schema = requests[0]['format']['properties']['rows']
        self.assertEqual(schema['items']['properties']['id']['enum'], [0, 1, 2, 3, 4, 5])
        self.assertEqual((schema['minItems'], schema['maxItems']), (6, 6))
        self.assertEqual(requests[0]['options']['temperature'], 0)

    def test_big_files_are_asked_in_batches(self):
        many = [{'id': i, 'source': 'Vad fan?'} for i in range(95)]
        calls = []
        kept = worker.identify_swedish_rows(many, lambda r: calls.append(1) or language_answer(r, {i: 'sv' for i in range(95)}))
        self.assertEqual((len(calls), len(kept)), (3, 95))

    def test_any_failure_means_every_row_is_translated_as_before(self):
        for response in ({'response': 'not json'}, {'response': '{"rows": [{"id": 0, "language": "sv"}]}'},
                         {'response': '{"nothing": 1}'}, {'response': json.dumps({'rows': [{'id': 99, 'language': 'sv'}] * 6})},
                         {'response': json.dumps({'rows': [{'id': i, 'language': 'sv'} for i in (0, 0, 1, 2, 3, 4)]})}):
            with self.subTest(response=str(response)[:30]):
                self.assertEqual(worker.identify_swedish_rows(rows(), lambda request, r=response: r), set())

        def broken(request):
            raise RuntimeError('Översättningsmotorn svarade HTTP 500')

        self.assertEqual(worker.identify_swedish_rows(rows(), broken), set())


class EndToEndTests(unittest.TestCase):
    """The translator and the draft reviewer both rewrite Swedish speech; the kept rows must still come out as spoken."""

    def run_translate(self, languages):
        translations = {0: 'God dag, min Späkel!', 1: 'Vad i helvete?', 2: 'Ni är ju galna!', 3: 'Vart ska du?',
                        4: 'Hahaha!', 5: 'Se dig inte omkring!'}

        def fake_call(request):
            if request['system'].startswith('You identify the language'):
                return language_answer(request, languages)
            payload = json.loads(request['prompt'])
            return {'response': json.dumps({'rows': [{'id': row['id'], 'sv': translations[row['id']]}
                                                    for row in payload['rows']]})}

        with patch.object(worker, 'MODEL_JOB') as state, patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            state.device = 'nvidia'
            return worker.translate({'id': -1}, rows())

    def test_swedish_rows_come_out_exactly_as_spoken_and_the_others_are_translated(self):
        result = self.run_translate(LANGUAGES)
        self.assertEqual([row['sv'] for row in result],
                         ['God dag, min Späkel!', 'Vad fan?', 'är jävlar!', 'Vart ska du?', 'Hahaha!', 'Titta inte ner!'])

    def test_when_the_language_step_gives_nothing_the_translation_is_unchanged(self):
        result = self.run_translate({i: 'none' for i in range(6)})
        self.assertEqual(result[1]['sv'], 'Vad i helvete?')

    def test_an_english_target_never_runs_the_language_step(self):
        with patch.object(worker, 'identify_swedish_rows') as identify, patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'call_ollama', side_effect=lambda request: {'response': json.dumps({'rows': [
                    {'id': row['id'], 'en': row['source']} for row in json.loads(request['prompt'])['rows']]})}), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            state.device = 'nvidia'
            worker.translate({'id': -1}, rows(), target_key='en', target_language='English')
        identify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
