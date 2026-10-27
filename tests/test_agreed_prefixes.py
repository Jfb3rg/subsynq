import copy
import json
from pathlib import Path
import sys
import unittest

sys.path[:0] = ['/app', '/ai']
from asr_windows import restore_agreed_prefixes
from source_judge import choose_readings
import worker

# Exact saved readings from Dev job 90, independently rechecked on longer audio.
TIMED = json.loads('''{"start":74.94,"end":78.08,"text":"Yo me encargo de que Salomé siga el camino correcto.","confidence":0.78369140625,"words":[{"start":75.02,"end":75.1,"text":"Yo","probability":0.83056640625},{"start":75.1,"end":75.24,"text":"me","probability":0.92041015625},{"start":75.24,"end":75.6,"text":"encargo","probability":0.9581705729166666},{"start":75.6,"end":75.7,"text":"de","probability":0.89404296875},{"start":75.7,"end":75.8,"text":"que","probability":0.78369140625},{"start":75.8,"end":76.24,"text":"Salomé","probability":0.9178059895833334},{"start":76.24,"end":76.9,"text":"siga","probability":0.888671875},{"start":76.9,"end":77.04,"text":"el","probability":0.90087890625},{"start":77.04,"end":77.26,"text":"camino","probability":0.8798828125},{"start":77.26,"end":78,"text":"correcto.","probability":0.908447265625}]}''')
BASE = json.loads('''{"start":74.375,"end":77.875,"text":"me encargo de que Salomé siga el camino correcto."}''')


class AgreedPrefixTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(BASE)]
        self.readings = {'v2': [copy.deepcopy(TIMED)], 'qwen': [dict(start=74.075, end=78.175,
                         text=TIMED['text'], confidence=.5)]}

    def blocked(self):
        rows, changes = restore_agreed_prefixes(self.rows, self.readings)
        self.assertEqual(rows, self.rows)
        self.assertEqual(changes, [])

    def test_real_prefix_preserves_every_existing_word_and_reports_evidence(self):
        original = copy.deepcopy((self.rows, self.readings))
        rows, changes = restore_agreed_prefixes(self.rows, self.readings)
        self.assertEqual(rows[0]['text'], 'Yo ' + BASE['text'])
        self.assertEqual(rows[0]['start'], BASE['start'])
        self.assertEqual(rows[0]['end'], TIMED['end'])
        self.assertEqual(changes[0]['families'], ['qwen', 'whisper-v2'])
        self.assertEqual(changes[0]['timing_pass'], 'v2')
        self.assertEqual((self.rows, self.readings), original)
        self.assertEqual(restore_agreed_prefixes(rows, self.readings), (rows, []))

    def test_one_model_family_is_not_independent_confirmation(self):
        self.readings = {'v2': [TIMED], 'v2+': [TIMED]}
        self.blocked()

    def test_ordinary_v3_passes_do_not_count_as_two_families(self):
        self.readings = {'precision': [TIMED], 'rescue': [TIMED]}
        self.blocked()

    def test_qwen_window_bounds_are_not_word_times(self):
        self.readings['qwen'][0]['words'] = copy.deepcopy(TIMED['words'])
        self.readings['v2'][0].pop('words')
        self.blocked()

    def test_low_confidence_or_missing_word_times_are_not_enough(self):
        for damage in ('low', 'missing', 'overlap', 'outside', 'stretched'):
            with self.subTest(damage=damage):
                words = self.readings['v2'][0]['words']
                saved = copy.deepcopy(words)
                if damage == 'low': words[0]['probability'] = .2
                if damage == 'missing': words.pop()
                if damage == 'overlap': words[1]['start'] = words[0]['start']
                if damage == 'outside': words[-1]['end'] = 79
                if damage == 'stretched': words[0]['end'] = words[0]['start'] + 2
                self.blocked()
                self.readings['v2'][0]['words'] = saved

    def test_changed_existing_words_are_never_a_prefix_repair(self):
        for other in self.readings.values():
            other[0]['text'] = TIMED['text'].replace('correcto', 'equivocado')
        self.blocked()

    def test_words_in_a_neighbor_are_not_borrowed(self):
        self.rows.insert(0, dict(start=70, end=74, text='Eso lo hago yo.', confidence=.9))
        self.blocked()

    def test_a_real_timing_conflict_is_not_trimmed_or_allowed(self):
        self.rows.append(dict(start=78, end=79, text='Otra persona habla.', confidence=.9))
        self.blocked()

    def test_conflicting_supported_prefixes_are_left_for_review(self):
        alt = copy.deepcopy(TIMED)
        alt['text'] = alt['text'].replace('Yo ', 'Y ', 1)
        alt['words'][0]['text'] = 'Y'
        self.readings['v2'].append(alt)
        self.readings['precision'] = [alt]
        self.blocked()

    def test_new_utterances_are_not_attached_as_sentence_prefixes(self):
        self.readings['v2'][0]['words'][0]['text'] = 'Yo.'
        self.blocked()

    def test_experimental_reader_plan_rows_can_be_skipped(self):
        self.assertEqual(restore_agreed_prefixes(self.rows, self.readings, skip_indices={0}), (self.rows, []))

    def test_a_suffix_from_the_next_line_is_not_a_prefix(self):
        self.rows = [dict(start=10, end=12, text='I saw the tracks.', confidence=.5)]
        raw = dict(start=10, end=13, text='I saw the tracks. Big wolf.', confidence=.9)
        self.readings = {'precision': [raw], 'v2': [raw]}
        self.blocked()

    def test_worker_restores_after_reader_plan_before_refining_times_and_saves_trace(self):
        source = Path(worker.__file__).read_text()
        plan = source.index('merged, plan_replaced = read_cues_with_plan(')
        restore = source.index('merged, consensus_prefixes = restore_agreed_prefixes(')
        refine = source.index('merged = refine_cue_starts(', restore)
        self.assertLess(plan, restore)
        self.assertLess(restore, refine)
        self.assertIn('consensus_prefixes=consensus_prefixes,', source)
        self.assertIn("'confirmed_prefix': confirmed_prefixes[index]", source)

    def test_text_only_chooser_cannot_erase_a_confirmed_prefix(self):
        for prefix in ('Yo', 'No', "I'm", '2'):
            with self.subTest(prefix=prefix):
                current = prefix + ' ' + BASE['text']
                row = dict(id=1, source=current, confirmed_prefix=prefix,
                           alternatives=[dict(text=BASE['text'], support=3, current_support=2)])
                def call(request):
                    return {'response': json.dumps({'rows': [dict(id=1, choice='B', a_has_error=True)]})}
                result, decisions = choose_readings([row], call, 'fake')
                self.assertEqual(result[0]['source'], current)
                self.assertFalse(decisions[0]['applied'])
                self.assertEqual(decisions[0]['reason'], 'confirmed_audio_prefix')

    def test_text_choice_with_the_prefix_still_uses_existing_guards(self):
        current = 'Yo ' + BASE['text']
        chosen = current.replace('correcto', 'adecuado')
        row = dict(id=1, source=current, confirmed_prefix='Yo',
                   alternatives=[dict(text=chosen, support=3, current_support=2)])
        result, decisions = choose_readings([row], lambda request: {'response': json.dumps({
            'rows': [dict(id=1, choice='B', a_has_error=True)]})}, 'fake')
        self.assertEqual(result[0]['source'], chosen)
        self.assertTrue(decisions[0]['applied'])


if __name__ == '__main__':
    unittest.main()
