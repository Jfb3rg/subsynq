import json
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
from asr_windows import plan_lead_word_probes, replace_outvoted_single_readings, restore_family_voted_speech, restore_lead_words
import worker


def cue(start, end, text, confidence=.5):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


class FamilyVoteWordingTests(unittest.TestCase):
    """A: the family vote takes the wording more families heard, not the most confident clipped reading."""

    # Godzilla Minus Zero (dev job 219), 40-42 s: one v3 pass lost the first character.
    GODZILLA = {'context': [cue(39.9, 40.96, 'あんた何よりか', .29), cue(40.96, 41.84, '退避', .36)],
                'precision': [cue(39.9, 41.12, 'あんたバレるのか', .37), cue(41.24, 41.94, '避', 1.0)],
                'v2': [cue(39.99, 41.25, 'あんたバリバリか!', .26), cue(41.25, 42.0, '退避!', .43)],
                'qwen': [cue(40.66, 42.3, '还有一个，开始。', .5)]}
    FINAL = [cue(38.47, 39.9, '私があの案を提案したんです'), cue(39.9, 40.96, 'あんた何よりか', .29),
             cue(42.9, 44.42, 'やっぱりこうなるんだ', .81)]

    def test_godzilla_evacuate_is_restored_whole(self):
        result = restore_family_voted_speech(self.FINAL, self.GODZILLA)
        added = [row['text'] for row in result if row not in self.FINAL]
        self.assertEqual(added, ['退避!'])

    def test_a_tie_in_families_keeps_the_medoid(self):
        readings = {'precision': [cue(22.0, 25.23, '상황실 상황실', .67)], 'rescue': [cue(23.08, 25.18, '상황실 상황실', .61)],
                    'v2': [cue(23.56, 24.70, '상황실!', .85)], 'forced': [cue(23.72, 24.58, '상황실!', .78)],
                    'qwen': [cue(21.7, 25.53, '상황실, 상황실.', .5)]}
        with patch('asr_windows._most_heard_reading', return_value=None):
            before = restore_family_voted_speech([], readings)
        self.assertEqual(restore_family_voted_speech([], readings), before)


class OutvotedSingleReadingTests(unittest.TestCase):
    """C: a doubtful row only one reading heard is replaced when three families heard the same other words."""

    # Glemselens øy (dev jobs 212 and 216), 118-126 s.
    READINGS = {'context': [cue(121.64, 123.32, 'Gjent mors', .43), cue(124.96, 126.16, 'grønn og vann', .49)],
                'rescue': [cue(120.36, 122.38, 'Vi skal hjem.', .96)],
                'v2': [cue(120.34, 122.52, 'Vi skal hjem!', .87)],
                'nb': [cue(120.64, 122.44, 'Vi skal hjem.', .71)],
                'qwen': [cue(121.34, 123.62, 'Skull Yen Do.', .5)]}
    FINAL = [cue(118.3, 120.2, 'Hold fast i rommene, Gunnars!', .7), cue(121.96, 123.32, 'Gjent mors', .43),
             cue(124.96, 126.16, 'grønn og vann', .49)]

    def test_glemselens_oy_gjent_mors_becomes_vi_skal_hjem(self):
        rows, changes = replace_outvoted_single_readings(self.FINAL, self.READINGS)
        self.assertEqual([row['text'] for row in rows], ['Hold fast i rommene, Gunnars!', 'Vi skal hjem.', 'grønn og vann'])
        self.assertEqual((rows[1]['start'], rows[1]['end']), (120.36, 122.38))
        self.assertEqual(changes[0]['families'], ['nb-whisper', 'whisper-v2', 'whisper-v3'])

    def test_two_families_are_not_enough(self):
        readings = {key: value for key, value in self.READINGS.items() if key != 'nb'}
        rows, changes = replace_outvoted_single_readings(self.FINAL, readings)
        self.assertEqual(changes, [])
        self.assertEqual(rows[1]['text'], 'Gjent mors')

    def test_a_row_a_second_reading_heard_is_kept(self):
        readings = dict(self.READINGS, precision=[cue(121.7, 123.3, 'Gjent mors!', .5)])
        self.assertEqual(replace_outvoted_single_readings(self.FINAL, readings)[1], [])

    def test_a_confident_row_is_kept(self):
        final = [dict(row, confidence=.8) for row in self.FINAL]
        self.assertEqual(replace_outvoted_single_readings(final, self.READINGS)[1], [])

    def test_a_replacement_never_reaches_into_the_previous_row(self):
        final = [cue(118.3, 121.0, 'Hold fast i rommene, Gunnars!', .7)] + self.FINAL[1:]
        rows, changes = replace_outvoted_single_readings(final, self.READINGS)
        self.assertEqual(rows[1]['text'], 'Vi skal hjem.')
        self.assertEqual((rows[1]['start'], rows[1]['end']), (121.96, 123.32))

    def test_three_v3_passes_alone_cannot_replace(self):
        readings = {'context': [cue(121.64, 123.32, 'Gjent mors', .43)], 'rescue': [cue(120.36, 122.38, 'Vi skal hjem.', .96)],
                    'whispered_gap': [cue(120.4, 122.4, 'Vi skal hjem.', .9)], 'v2': [cue(120.34, 122.52, 'Vi skal hjem!', .87)]}
        self.assertEqual(replace_outvoted_single_readings(self.FINAL, readings)[1], [])


class UnresolvedGrammarTests(unittest.TestCase):
    """B: a grammar flag the remark-free grammar task cannot repair goes on to the repair that reads the remark."""

    ROW = {'id': 27, 'source': 'I wish we had time to build more.', 'sv': 'Jag önskar att vi hade haft tid att bygga mer.'}
    FLAG = {'id': 27, 'category': 'grammar', 'source_quote': ROW['source'], 'target_quote': ROW['sv'],
            'reason': "Tidsformen är pluskvamperfekt ('hade haft'), källan uttrycker en önskan om nutid."}

    def run_repair(self, answers):
        replies = iter(answers)
        with patch.object(worker, 'call_ollama', side_effect=lambda request: {'response': json.dumps(next(replies))}) as model, \
                patch.object(worker, 'check_cancelled'):
            result = worker.repair_flagged_translation_once({'id': 218}, [dict(self.ROW)], [dict(self.FLAG)])
        return result, model

    def test_fortitude_tense_is_repaired_with_the_remark(self):
        result, model = self.run_repair([{'rows': [{'id': 27, 'sv': self.ROW['sv']}]},
                                         {'rows': [{'id': 27, 'sv': 'Jag önskar att vi hade tid att bygga mer.'}]}])
        self.assertEqual(result[0]['sv'], 'Jag önskar att vi hade tid att bygga mer.')
        self.assertEqual(model.call_count, 2)        # grammar task, then the remark-aware repair; no second grammar call
        self.assertNotIn('Tidsformen', model.call_args_list[0].args[0]['prompt'])
        self.assertIn('Tidsformen', model.call_args_list[1].args[0]['prompt'])

    def test_a_grammar_fix_the_grammar_task_makes_ends_there(self):
        row = {'id': 3, 'source': "He's about to jam the door.", 'sv': 'Han ska precis kilas fast dörren.'}
        flag = {'id': 3, 'category': 'grammar', 'target_quote': row['sv'], 'reason': 'passiv'}
        with patch.object(worker, 'call_ollama', return_value={'response': json.dumps(
                {'rows': [{'id': 3, 'sv': 'Han ska precis kila fast dörren.'}]})}) as model, patch.object(worker, 'check_cancelled'):
            result = worker.repair_flagged_translation_once({'id': 152}, [row], [flag])
        self.assertEqual(result[0]['sv'], 'Han ska precis kila fast dörren.')
        self.assertEqual(model.call_count, 1)


class ShownModelNameTests(unittest.TestCase):
    """D: reports and logs name the model that ran on the rented server, not the home alias."""

    def test_rented_server_names(self):
        with patch.object(worker, 'MODEL_NAME', 'gemma3:12b'), patch.object(worker, 'REVIEW_MODEL_NAME', 'gemma4:12b'), \
                patch.object(worker.MODEL_JOB, 'cloud_models', ('gemma3:27b', 'gemma4:26b'), create=True):
            self.assertEqual(worker.shown_model_name('gemma3:12b'), 'gemma3:27b')
            self.assertEqual(worker.shown_model_name('gemma4:12b'), 'gemma4:26b')
            self.assertEqual(worker.shown_model_name('other'), 'other')

    def test_home_names_without_a_rented_server(self):
        with patch.object(worker, 'MODEL_NAME', 'gemma3:12b'), patch.object(worker, 'REVIEW_MODEL_NAME', 'gemma4:12b'), \
                patch.object(worker.MODEL_JOB, 'cloud_models', None, create=True):
            self.assertEqual(worker.shown_model_name('gemma3:12b'), 'gemma3:12b')



class LeadWordProbeTests(unittest.TestCase):
    """FORTITUDE (dev jobs 218 and 221): only the rescue pass heard "you" before the cut row "were arrested"; Qwen is asked once more."""

    FINAL = [cue(6.85, 8.01, 'Give me the code', .8), cue(11.4, 12.18, 'were arrested', .74),
             cue(12.76, 15.8, "Wearing a woman's skirt technically it's a dress.", .71)]
    READINGS = {'context': [cue(6.85, 8.01, 'Give me the code', .71), cue(11.16, 12.18, 'were arrested', .74)],
                'precision': [cue(11.14, 12.16, 'were arrested', .73)],
                'rescue': [cue(10.52, 17.22, "you were arrested wearing a woman's skirt technically it's a dress i'd like it back", .6)]}

    def test_one_reading_plans_a_qwen_window_and_restores_nothing_alone(self):
        windows = plan_lead_word_probes(self.FINAL, self.READINGS, 160.0)
        self.assertEqual([(w['lead'], w['row_start']) for w in windows], [('you', 11.4)])
        self.assertLess(windows[0]['clip_start'], windows[0]['start'])
        self.assertEqual(restore_lead_words(self.FINAL, self.READINGS)[1], [])

    def test_qwen_hearing_the_lead_restores_it(self):
        window = plan_lead_word_probes(self.FINAL, self.READINGS, 160.0)[0]
        probe = cue(window['start'], window['end'], "You were arrested wearing a woman's skirt.")
        rows, changes = restore_lead_words(self.FINAL, dict(self.READINGS, qwen=[probe]))
        self.assertEqual(rows[1]['text'], 'you were arrested')
        self.assertEqual(rows[1]['start'], 10.52)
        self.assertEqual(changes[0]['readings'], ['qwen', 'rescue'])

    def test_qwen_not_hearing_it_changes_nothing(self):
        window = plan_lead_word_probes(self.FINAL, self.READINGS, 160.0)[0]
        probe = cue(window['start'], window['end'], 'Were arrested wearing a skirt.')
        self.assertEqual(restore_lead_words(self.FINAL, dict(self.READINGS, qwen=[probe]))[1], [])

    def test_a_row_that_starts_a_sentence_is_not_probed(self):
        final = [dict(row) for row in self.FINAL]
        final[1]['text'] = 'Were arrested'
        readings = dict(self.READINGS, context=[cue(11.16, 12.18, 'Were arrested', .74)], precision=[cue(11.14, 12.16, 'Were arrested', .73)])
        self.assertEqual(plan_lead_word_probes(final, readings, 160.0), [])

    def test_the_worker_reads_each_window_with_qwen_and_keeps_the_leads_time(self):
        class Models:
            def __init__(self):
                self.released = 0
            def qwen(self, identify, clip, *language):
                return {'text': "You were arrested wearing a woman's skirt."}
            def release_qwen(self):
                self.released += 1
        models = Models()
        with patch.object(worker, 'QWEN_ASR_ENABLED', True), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'), patch.object(worker.subprocess, 'run'), \
                patch.object(worker, 'audio_map', return_value='0:a:0'):
            probes = worker.read_lead_word_probes({'id': 221}, '/extra/f.mp4', self.FINAL, self.READINGS, models, 160.0, set())
        self.assertEqual([(p['start'], p['text']) for p in probes], [(10.52, "You were arrested wearing a woman's skirt.")])
        self.assertEqual(models.released, 1)

    def test_the_worker_asks_nothing_when_qwen_is_off(self):
        with patch.object(worker, 'QWEN_ASR_ENABLED', False):
            self.assertEqual(worker.read_lead_word_probes({'id': 1}, 'x', self.FINAL, self.READINGS, None, 160.0, set()), [])


if __name__ == '__main__':
    unittest.main()
