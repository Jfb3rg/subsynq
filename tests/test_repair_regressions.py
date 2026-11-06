import json
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from translation_qa import SWEDISH_MEANING_RULE
from translation_review_prompt import audit_system


def model_rows(**by_id):
    return {'response': json.dumps({'rows': [{'id': int(row_id), 'sv': text} for row_id, text in by_id.items()]})}


class IndependentWordingIsNotACopyTests(unittest.TestCase):
    """regression case(Example Series 4x02), row 256: the right fix was found twice and refused by two guards."""

    ROWS = [{'id': 255, 'start': 975.4, 'end': 977.0, 'source': "Otherwise, they'll never let us leave willingly.",
             'sv': 'Annars kommer de aldrig att låta oss lämna frivilligt.'},
            {'id': 256, 'start': 977.14, 'end': 977.69, 'source': "Who won't?", 'sv': 'Vem kommer inte det?'},
            {'id': 257, 'start': 977.69, 'end': 978.3, 'source': 'Everyone.', 'sv': 'Alla.'}]
    # The real remark from the job's review (it suggests the same words the grammar task found on its own).
    FLAG = {'id': 256, 'category': 'grammar', 'source_quote': "Who won't?", 'target_quote': 'Vem kommer inte det?',
            'reason': "I svenskan låter 'Vem kommer inte det?' som en felaktig översättning av 'Who won't?'. Det korrekta sättet "
                      "att fråga vem som inte kommer att låta dem gå är 'Vem kommer inte det?' (vilket är grammatiskt tveksamt) "
                      "eller mer naturligt 'Vem kommer inte göra det?' eller 'Vem kommer inte låta oss?'"}

    def repair(self, *answers):
        steps = []
        with patch.object(worker, 'call_ollama', side_effect=list(answers)) as model, patch.object(worker, 'check_cancelled'):
            result = worker.repair_flagged_translation({'id': 57}, [dict(row) for row in self.ROWS], [self.FLAG], record=steps.append)
        return model, result, steps

    def test_the_grammar_task_s_own_wording_is_kept_by_the_meaning_repair(self):
        # Real answers: grammar task (refused there for the added word), then the meaning repair with the same words.
        model, result, steps = self.repair(model_rows(**{'256': 'Vem kommer inte göra det?'}),
                                           model_rows(**{'256': 'Vem kommer inte göra det?'}))
        self.assertEqual(result[1]['sv'], 'Vem kommer inte göra det?')
        self.assertEqual(model.call_count, 2)
        self.assertNotIn('copied_reason', [step['stage'] for step in steps])
        self.assertEqual([result[0], result[2]], [self.ROWS[0], self.ROWS[2]])

    def test_wording_only_the_remark_had_is_still_refused_once(self):
        # The grammar task changed nothing; the meaning repair copies the remark's other suggestion: asked again, as before.
        model, result, steps = self.repair(model_rows(**{'256': 'Vem kommer inte det?'}),
                                           model_rows(**{'256': 'Vem kommer inte låta oss?'}),
                                           model_rows(**{'256': 'Vilka då?'}))
        self.assertIn('copied_reason', [step['stage'] for step in steps])
        self.assertEqual(result[1]['sv'], 'Vilka då?')


class EveryRefusedWordingTests(unittest.TestCase):
    """regression case(Example Series 4x03), row 139: told only the latest refusal, the repair swapped "mitt"/"min" for four rounds."""

    ROWS = [{'id': 138, 'start': 632.8, 'end': 634.0, 'source': "That's a mistake.", 'sv': 'Det är ett misstag.'},
            {'id': 139, 'start': 635.4, 'end': 636.28, 'source': "Wouldn't be my first.", 'sv': 'Det vore inte mitt första.'},
            {'id': 140, 'start': 702.2, 'end': 703.0, 'source': "What's she staring at?", 'sv': 'Vad stirrar hon på?'}]
    # As in the real job: a grammar remark, so the grammar task answers first (unchanged) and the meaning repair follows.
    FLAG = {'id': 139, 'category': 'grammar', 'source_quote': "Wouldn't be my first.", 'target_quote': 'Det vore inte mitt första.',
            'reason': "Meningen är grammatiskt ofullständig på svenska; det saknas ett substantiv efter adjektivet."}
    UNCHANGED = staticmethod(lambda text: model_rows(**{'139': text}))

    def test_wording_keys_ignore_the_possessive_and_article_swaps(self):
        self.assertEqual(worker.wording_key('Det skulle inte vara mitt första.'), worker.wording_key('Det skulle inte vara min första'))
        self.assertNotEqual(worker.wording_key('Det vore inte mitt första.'), worker.wording_key('Det vore inte mitt första misstag.'))

    def test_the_repair_is_told_every_refused_wording_and_asked_again_on_a_repeat(self):
        job, steps = {'id': 58}, []
        rows = [dict(row) for row in self.ROWS]
        with patch.object(worker, 'call_ollama', side_effect=[
                self.UNCHANGED('Det vore inte mitt första.'),                         # round 1: grammar task
                model_rows(**{'139': 'Det skulle inte vara mitt första.'}),          # round 1: meaning repair (real answer)
                self.UNCHANGED('Det skulle inte vara min första.'),                   # round 2: grammar task
                model_rows(**{'139': 'Det skulle inte vara mitt första.'}),          # round 2: the real repeat
                model_rows(**{'139': 'Det vore inte mitt första misstag.'})]) as model, \
                patch.object(worker, 'check_cancelled'):
            rows = worker.repair_flagged_translation(job, rows, [self.FLAG], record=steps.append)
            rows[1]['sv'] = 'Det skulle inte vara min första.'                         # what the grammar task then made of it
            flag = {**self.FLAG, 'target_quote': 'Det skulle inte vara min första.'}
            rows = worker.repair_flagged_translation(job, rows, [flag], record=steps.append)
        second = json.loads(model.call_args_list[3].args[0]['prompt'])
        self.assertEqual(second['rejected_targets']['139'], ['Det vore inte mitt första.', 'Det skulle inte vara min första.'])
        self.assertEqual(model.call_count, 5)
        self.assertIn('refused_again', [step['stage'] for step in steps])
        self.assertEqual(rows[1]['sv'], 'Det vore inte mitt första misstag.')
        self.assertEqual([rows[0], rows[2]], [self.ROWS[0], self.ROWS[2]])

    def test_the_repair_may_write_out_a_noun_the_line_leaves_out(self):
        with patch.object(worker, 'call_ollama', side_effect=[self.UNCHANGED('Det vore inte mitt första.'),
                                                             model_rows(**{'139': 'Det vore inte mitt första misstag.'})]) as model, \
                patch.object(worker, 'check_cancelled'):
            worker.repair_flagged_translation({'id': 58}, [dict(row) for row in self.ROWS], [self.FLAG])
        system = model.call_args.args[0]['system']
        self.assertIn('never move their words into the target id', system)       # the strict rule stays
        self.assertIn('leaves out a noun that the immediately preceding source line names', system)

    def test_keeping_the_line_is_allowed_once_and_a_second_repeat_goes_to_the_final_review(self):
        job, steps = {'id': 58}, []
        with patch.object(worker, 'call_ollama', side_effect=[
                self.UNCHANGED('Det vore inte mitt första.'), model_rows(**{'139': 'Det vore inte min första.'}),     # round 1: kept, allowed
                self.UNCHANGED('Det vore inte min första.'), model_rows(**{'139': 'Det vore inte mitt första.'}),      # round 2: asked again
                model_rows(**{'139': 'Det vore inte mitt första.'})]) as model, \
                patch.object(worker, 'check_cancelled'):
            rows = worker.repair_flagged_translation(job, [dict(row) for row in self.ROWS], [self.FLAG], record=steps.append)
            self.assertEqual((model.call_count, steps.count({'stage': 'refused_again', 'ids': [139]})), (2, 0))
            flag = {**self.FLAG, 'target_quote': rows[1]['sv']}
            rows = worker.repair_flagged_translation(job, rows, [flag], record=steps.append)
        self.assertEqual(model.call_count, 5)                                         # one extra question, never a loop
        self.assertIn({'stage': 'refused_again', 'ids': [139]}, steps)
        self.assertEqual(rows[1]['sv'], 'Det vore inte mitt första.')                 # the final review judges it, as before


class CalqueRuleTests(unittest.TestCase):
    """regression case(Sample Trailer) was approved with "Hur vore mord?" and "Volatil.": the review called calques style."""

    def test_the_review_treats_a_calque_as_a_meaning_defect(self):
        system = audit_system('sv', 'Swedish')
        self.assertIn('calque or false friend is NOT style', system)
        self.assertIn('Vad sägs om mord?', system)

    def test_translator_reviewers_and_repairs_get_the_swedish_wording(self):
        self.assertIn('"Vad sägs om X?"', SWEDISH_MEANING_RULE)
        self.assertIn('oberäknelig', worker.output_rules('sv'))


if __name__ == '__main__':
    unittest.main()
