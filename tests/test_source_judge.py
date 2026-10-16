import json
import sys
import unittest

sys.path.insert(0, '/app')
import source_judge as judge


def row(row_id, text, alternatives=None):
    item = {'id': row_id, 'start': float(row_id), 'end': row_id + .9, 'source': text}
    if alternatives is not None:
        item['alternatives'] = alternatives
    return item


def alt(text, support=1, current_support=1):
    return {'text': text, 'support': support, 'current_support': current_support}


def answering(*answers):
    """A fake language-model call that answers with the given (id, a_has_error, choice) triples."""
    requests = []

    def call(request):
        requests.append(request)
        return {'response': json.dumps({'rows': [
            {'id': row_id, 'a_has_error': flawed, 'choice': choice} for row_id, flawed, choice in answers]})}
    call.requests = requests
    return call


class JudgeItemsTests(unittest.TestCase):
    def test_only_rows_with_alternatives_become_items_and_carry_their_neighbours_and_support(self):
        rows = [row(0, 'War is a monster.'),
                row(1, 'way you make it your life all right lights on me this',
                    [alt('The way you make it your life. All right, lights on me.', 3, 2)]),
                row(2, 'This is about to get ugly.')]
        items = judge.judge_items(rows)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['id'], 1)
        self.assertEqual(item['candidates'][0], 'way you make it your life all right lights on me this')
        self.assertEqual(item['supports'], [2, 3], 'the current wording first, with its own support')
        self.assertEqual((item['previous'], item['next']), ('War is a monster.', 'This is about to get ugly.'))

    def test_duplicates_of_the_current_wording_and_each_other_do_not_make_candidates(self):
        rows = [row(0, 'Hello there.', [alt('Hello there.'), alt('Hello, there!'), alt('Hello, there!')])]
        self.assertEqual(judge.judge_items(rows)[0]['candidates'], ['Hello there.', 'Hello, there!'])
        self.assertEqual(judge.judge_items([row(0, 'Hello there.', [alt('Hello there.')])]), [])

    def test_the_request_pins_every_answer_to_the_asked_ids_and_to_letters(self):
        items = judge.judge_items([row(4, 'a b c d', [alt('a b c e')]), row(9, 'x y z w', [alt('x y z v')])])
        request = judge.build_request('gemma3:27b', items)
        schema = request['format']['properties']['rows']
        self.assertEqual(schema['items']['properties']['id']['enum'], [4, 9])
        self.assertEqual((schema['minItems'], schema['maxItems']), (2, 2))
        self.assertEqual(schema['items']['properties']['choice']['enum'], list('ABCDEF'))
        self.assertIn('a_has_error', schema['items']['required'])
        prompt = json.loads(request['prompt'])
        self.assertEqual(prompt['rows'][0]['candidates'], {'A': 'a b c d', 'B': 'a b c e'})
        self.assertEqual(request['options']['temperature'], 0)


class ChooseReadingsTests(unittest.TestCase):
    def test_a_flawed_current_wording_is_replaced_by_the_better_supported_reading(self):
        # Real cue, Man of War (dev job 49): three passes read "The way you make it your life ..."
        rows = [row(0, 'War is a monster.'),
                row(1, 'way you make it your life all right lights on me this',
                    [alt('The way you make it your life. All right, lights on me.', 3, 2)]),
                row(2, 'This is about to get ugly.')]
        result, decisions = judge.choose_readings(rows, answering((1, True, 'B')), 'm')
        self.assertEqual(result[1]['source'], 'The way you make it your life. All right, lights on me.')
        self.assertNotIn('alternatives', result[1])
        self.assertEqual([(d['id'], d['choice'], d['applied']) for d in decisions], [(1, 'B', True)])
        self.assertEqual(rows[1]['source'], 'way you make it your life all right lights on me this', 'input untouched')

    def test_the_error_flag_can_be_required_and_is_always_recorded(self):
        rows = [row(0, 'way you make it your life all right lights on me this',
                    [alt('The way you make it your life. All right, lights on me.', 3, 2)])]
        result, decisions = judge.choose_readings(rows, answering((0, False, 'B')), 'm', require_error_flag=True)
        self.assertEqual(result[0]['source'], 'way you make it your life all right lights on me this')
        self.assertFalse(decisions[0]['applied'])
        self.assertIs(decisions[0]['a_has_error'], False)
        result, decisions = judge.choose_readings(rows, answering((0, True, 'B')), 'm', require_error_flag=True)
        self.assertTrue(decisions[0]['applied'] and decisions[0]['a_has_error'])

    def test_choosing_a_leaves_the_row_alone(self):
        rows = [row(0, 'His name is Stanislav Koniev.', [alt('His name is Stanislav Kanyev.', 2, 1)])]
        result, decisions = judge.choose_readings(rows, answering((0, True, 'A')), 'm')
        self.assertEqual(result[0]['source'], 'His name is Stanislav Koniev.')

    def test_a_minimal_repair_is_allowed_even_when_the_passes_are_split(self):
        # German dub, dev job 48: Whisper v3 and Qwen read "nicht", only Whisper v2 read "Nichte".
        rows = [row(0, 'Meine nicht. Ich und ihr Vater, wir sind zusammen in den Teams.',
                    [alt('Meine Nichte. Ich und ihr Vater, wir sind zusammen in den Teams.', 1, 2)])]
        result, _ = judge.choose_readings(rows, answering((0, True, 'B')), 'm')
        self.assertTrue(result[0]['source'].startswith('Meine Nichte.'))

    def test_a_fluent_rewrite_only_one_engine_heard_is_refused(self):
        rows = [row(0, "anticipated attack on ukraine he's about to jam the door mike i'm in",
                    [alt("It's long-anticipated attack on Ukraine. Jam the door, Mike. I'm in trouble.", 1, 1)])]
        result, decisions = judge.choose_readings(rows, answering((0, True, 'B')), 'm')
        self.assertEqual(result[0]['source'], "anticipated attack on ukraine he's about to jam the door mike i'm in")
        self.assertFalse(decisions[0]['applied'])

    def test_a_reading_that_drops_most_of_the_words_is_refused(self):
        rows = [row(0, 'Run! on, you niggas! Run!', [alt('on, you niggas!', 3, 1)])]
        result, _ = judge.choose_readings(rows, answering((0, True, 'B')), 'm')
        self.assertEqual(result[0]['source'], 'Run! on, you niggas! Run!')

    def test_words_that_belong_to_the_neighbouring_cue_are_not_taken_over(self):
        # Real cues 21-22, Man of War: a Qwen clip that began early repeated the end of the previous line.
        rows = [row(0, "is what's left simple people we're trying to live their lives"),
                row(1, 'trying to survive', [alt('Trying to live their lives, trying to survive.', 2, 1)]),
                row(2, 'War is a monster.')]
        result, decisions = judge.choose_readings(rows, answering((1, True, 'B')), 'm')
        self.assertEqual(result[1]['source'], 'trying to survive')
        self.assertFalse(decisions[0]['applied'])

    def test_a_reading_that_restores_missing_words_the_passes_heard_is_accepted(self):
        # Real cue 6: "I need a way in the Kiev, now" was heard by three passes and missing from the final cue.
        rows = [row(0, "His name is Stanislav Koniev. He's got quite a reputation.",
                    [alt("His name is Stanislav Konev. He's got quite a reputation. I need a way in the Kiev, now.", 2, 1)]),
                row(1, 'My niece.')]
        result, _ = judge.choose_readings(rows, answering((0, True, 'B')), 'm')
        self.assertTrue(result[0]['source'].endswith('I need a way in the Kiev, now.'))

    def test_invalid_answers_are_ignored_row_by_row(self):
        rows = [row(0, 'one two three four', [alt('one two three five', 2, 1)]),
                row(1, 'six seven eight nine', [alt('six seven eight ten', 2, 1)])]
        for call in (answering((7, True, 'B')),                      # an id that was not asked
                     answering((0, True, 'F')),                      # a letter with no candidate
                     lambda request: {'response': 'not json'},
                     lambda request: {'response': json.dumps({'rows': 'x'})},
                     lambda request: {}):
            result, decisions = judge.choose_readings(rows, call, 'm')
            self.assertEqual([r['source'] for r in result], ['one two three four', 'six seven eight nine'])
            self.assertTrue(all('alternatives' not in r for r in result))
            self.assertEqual(decisions, [])

    def test_a_failing_engine_stops_the_asking_but_keeps_what_was_already_chosen(self):
        rows = [row(index, f'one two three {index}', [alt(f'one two three {index} four', 2, 1)])
                for index in range(4)]
        calls = []

        def flaky(request):
            calls.append(request)
            if len(calls) == 1:
                ids = [item['id'] for item in json.loads(request['prompt'])['rows']]
                return {'response': json.dumps({'rows': [{'id': i, 'a_has_error': True, 'choice': 'B'} for i in ids]})}
            raise judge.EngineUnavailable('model gone')
        result, decisions = judge.choose_readings(rows, flaky, 'm', batch_size=2)
        self.assertEqual(len(calls), 2, 'nothing is asked after the failure')
        self.assertEqual([r['source'] for r in result],
                         ['one two three 0 four', 'one two three 1 four', 'one two three 2', 'one two three 3'])
        self.assertEqual(len(decisions), 2)
        def broken(request):
            raise judge.EngineUnavailable('model gone')
        result, decisions = judge.choose_readings(rows, broken, 'm')
        self.assertEqual(([r['source'] for r in result], decisions),
                         ([f'one two three {index}' for index in range(4)], []))


    def test_rows_are_asked_in_batches_and_never_more_than_twenty_four_in_all(self):
        rows = [row(index, f'one two three {index}', [alt(f'one two three {index + 100}', 2, 1)])
                for index in range(30)]
        call = answering()
        judge.choose_readings(rows, call, 'm', batch_size=10)
        self.assertEqual(len(call.requests), 3, '24 rows in batches of 10, 10 and 4')
        self.assertEqual(len(json.loads(call.requests[0]['prompt'])['rows']), 10)

    def test_by_default_the_guards_alone_decide(self):
        rows = [row(0, 'way you make it your life all right lights on me this',
                    [alt('The way you make it your life. All right, lights on me.', 3, 2)])]
        result, decisions = judge.choose_readings(rows, answering((0, False, 'B')), 'm')
        self.assertEqual(result[0]['source'], 'The way you make it your life. All right, lights on me.')
        self.assertTrue(decisions[0]['applied'] and not decisions[0]['a_has_error'])

    def test_a_stutter_or_decoding_loop_is_never_a_better_wording(self):
        self.assertTrue(judge.has_repetition("His name is Stanislav Kanyev. He's. He's got quite a reputation."))
        self.assertTrue(judge.has_repetition('his name is stanislav kanyev his name is stanislav kanyev'))
        self.assertFalse(judge.has_repetition('That is very very good, no no no.'))
        self.assertFalse(judge.has_repetition('Run! on, you niggas! Run!'))
        rows = [row(0, "His name is Stanislav Koniev. He's got quite a reputation.",
                    [alt("His name is Stanislav Kanyev. He's. He's got quite a reputation.", 3, 1)])]
        result, _ = judge.choose_readings(rows, answering((0, True, 'B')), 'm')
        self.assertEqual(result[0]['source'], "His name is Stanislav Koniev. He's got quite a reputation.")

    def test_a_repetition_the_current_wording_already_has_does_not_block_a_repair(self):
        self.assertTrue(judge.acceptable_choice('no no no I mean it', 'no no no, I mean it!', 2, 1))

    def test_long_or_non_latin_rows_make_small_batches_and_a_large_enough_context(self):
        tamil = 'ஆனால் தாயாமானது விட்டதே இல்லை ' * 8
        rows = [row(index, tamil + str(index), [alt(tamil + str(index + 50), 2, 1), alt(tamil + 'x', 1, 1)])
                for index in range(6)]
        items = judge.judge_items(rows)
        batches = list(judge.batches_of(items))
        self.assertGreater(len(batches), 1, 'a character budget splits long rows into several batches')
        self.assertTrue(all(len(batch) <= 12 for batch in batches))
        request = judge.build_request('m', batches[0])
        prompt_size = len(request['prompt']) + len(request['system'])
        self.assertGreaterEqual(request['options']['num_ctx'], prompt_size, 'one token per character at most')
        self.assertLessEqual(request['options']['num_ctx'], 16384)
        small = judge.build_request('m', judge.judge_items([row(0, 'a b c d', [alt('a b c e')])]))
        self.assertEqual(small['options']['num_ctx'], 4096)

    def test_the_acceptance_rules_directly(self):
        self.assertTrue(judge.acceptable_choice('meine nicht ich und ihr vater', 'Meine Nichte, ich und ihr Vater', 1, 2))
        self.assertFalse(judge.acceptable_choice('a b c d e f', 'a b', 5, 1), 'drops words')
        self.assertFalse(judge.acceptable_choice('one two three four', 'five six seven eight', 5, 1), 'not the same utterance')
        self.assertFalse(judge.acceptable_choice('one two three four', 'one two three five six', 1, 1), 'rewrite, no support')
        self.assertTrue(judge.acceptable_choice('one two three four', 'one two three five six', 2, 1))


if __name__ == '__main__':
    unittest.main()
