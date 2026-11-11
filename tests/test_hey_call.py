import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from translation_qa import deterministic_flags, file_consistency_flags, hey_call_flag, repair_hey_call


def row(row_id, source, sv):
    return {'id': row_id, 'start': float(row_id * 3), 'end': float(row_id * 3 + 2), 'source': source, 'sv': sv}


# Real rows of saved files: a "Hey" that calls, stops or calms someone written as the greeting "Hej".
ROWS = [row(1, 'Hey, hey, hey, hey, hey! Hands off! Let me go!', 'Hej, hej, hej, hej, hej! Rör dem inte! Låt oss gå härifrån!'),
        row(2, 'Hey, hey.', 'Hej, hej.'),
        row(3, 'Hey!', 'Hej!'),
        row(4, 'Something is coming!', 'Något kommer!'),
        row(5, 'Hey, hey, hey!', 'Hallå, hallå, hallå!'),
        row(6, 'Hey, Warren, what are you doing?', 'Hej, Warren, vad håller du på med?'),
        row(7, 'Hey, Henry, wake up!', 'Hej, Henry, vakna!')]


def hey_flags(rows):
    return [flag for flag in file_consistency_flags(rows) if flag['reason'].startswith('Utropet "Hey"')]


class HeyCallTests(unittest.TestCase):
    """A repeated or lone shouted "Hey", or one that opens an order or a protest, is a call ("Hallå"), never the greeting "Hej"; a "Hey" that
    can be a greeting is left alone."""

    def test_the_greeting_for_a_repeated_or_shouted_hey_is_flagged(self):
        flags = hey_flags(ROWS)
        self.assertEqual([(flag['id'], flag['target_quote']) for flag in flags], [(1, 'Hej, hej, hej, hej, hej'), (2, 'Hej, hej'), (3, 'Hej'), (6, 'Hej'), (7, 'Hej')])
        self.assertEqual(flags[0]['category'], 'meaning')

    def test_the_repair_changes_only_the_leading_words(self):
        repaired, changes = repair_hey_call(ROWS, file_consistency_flags(ROWS))
        self.assertEqual([item['sv'] for item in repaired],
                         ['Hallå, hallå, hallå, hallå, hallå! Rör dem inte! Låt oss gå härifrån!', 'Hallå, hallå.', 'Hallå!', 'Något kommer!',
                          'Hallå, hallå, hallå!', 'Hallå, Warren, vad håller du på med?', 'Hallå, Henry, vakna!'])
        self.assertEqual([change['id'] for change in changes], [1, 2, 3, 6, 7])
        self.assertEqual(hey_flags(repaired), [])
        self.assertEqual([flag for flag in deterministic_flags(repaired) if flag['reason'].startswith('Utropet')], [])

    def test_a_hey_that_can_be_a_greeting_is_left_alone(self):
        for source, sv in (('Hey, Paul.', 'Hej, Paul.'), ('Hey.', 'Hej.'), ('Hey, guys.', 'Hej, killar.'), ('Hey', 'Hej'),
                           ('Hey Lucy, what happened to Addie?', 'Hej Lucy, vad hände med Addie?'), ('Hey, how are you?', 'Hej, hur mår du?'),
                           ('Hey, hey, hey!', 'Hörru, hörru, hörru!'), ('Hey!', 'Hallå!'), ('They say hey, hey to everyone.', 'Hej, hej säger de till alla.'),
                           ('Hello, hello!', 'Hej, hej!'), ('Heya!', 'Hej!'), ('Hey, what are you doing tonight?', 'Hej, vad gör du ikväll?'),
                           ('Hey, stop by anytime.', 'Hej, kom förbi när du vill.'), ('Hey, waiter!', 'Hej, kyparn!'),
                           ('Hey, your bruises are gone.', 'Hej, dina blåmärken är borta.')):
            self.assertIsNone(hey_call_flag(source, sv), (source, sv))

    def test_a_hey_that_calls_for_attention_is_flagged(self):
        for source, sv, right in (('Hey, you there, with the red hat!', 'Hej, du där, med den röda hatten!', 'Hallå, du där, med den röda hatten!'),
                                  ('Hey, listen to me.', 'Hej, lyssna på mig.', 'Hallå, lyssna på mig.'),
                                  ('Hey, look!', 'Hej, titta!', 'Hallå, titta!'),
                                  ('Hey, excuse me, is this your bag?', 'Hej, ursäkta, är det här din väska?', 'Hallå, ursäkta, är det här din väska?'),
                                  ('Hey, du da, mit dem Hut!', 'Hej, du där, med hatten!', 'Hallå, du där, med hatten!')):
            rows = [row(1, source, sv)]
            self.assertEqual([flag['target_quote'] for flag in hey_flags(rows)], ['Hej'], source)
            self.assertEqual(repair_hey_call(rows, file_consistency_flags(rows))[0][0]['sv'], right)
        for source, sv in (('Hey, you.', 'Hej, du.'), ('Hey, look who is here!', 'Hej, titta vem som är här!'), ('Hey, sorry I am late.', 'Hej, förlåt att jag är sen.')):
            self.assertEqual(hey_flags([row(1, source, sv)]), [], source)

    def test_a_hey_in_the_middle_of_what_someone_says_is_no_greeting(self):
        unfinished = {'id': 1, 'start': 10.0, 'end': 12.0, 'source': 'Oh, before I forget, uh...', 'sv': 'Åh, innan jag glömmer...'}
        hey = {'id': 2, 'start': 12.0, 'end': 14.0, 'source': 'Hey, did you find the keys?', 'sv': 'Hej, hittade du nycklarna?'}
        rows = [unfinished, hey]
        self.assertEqual([(flag['id'], flag['target_quote']) for flag in hey_flags(rows)], [(2, 'Hej')])
        repaired, changes = repair_hey_call(rows, file_consistency_flags(rows))
        self.assertEqual([item['sv'] for item in repaired], ['Åh, innan jag glömmer...', 'Hallå, hittade du nycklarna?'])
        self.assertEqual(hey_flags(repaired), [])
        # A finished row before it, a pause, or no "Hey," at the start: it can be a greeting and is left alone.
        for before, start, source in (({**unfinished, 'source': 'Oh, before I forget.'}, 12.0, hey['source']),
                                      ({**unfinished, 'source': 'Marta!'}, 12.0, hey['source']),
                                      (unfinished, 13.5, hey['source']),
                                      (unfinished, 12.0, 'Hey Marta, did you find the keys?')):
            self.assertEqual(hey_flags([before, {**hey, 'start': start, 'source': source}]), [], (before['source'], start, source))
        self.assertEqual(hey_flags([hey]), [])

    def test_a_lower_case_lead_and_a_dash_are_kept(self):
        rows = [row(9, '- hey, hey, stop it!', '- hej, hej, sluta!')]
        self.assertEqual(repair_hey_call(rows, file_consistency_flags(rows))[0][0]['sv'], '- hallå, hallå, sluta!')

    def test_only_a_requested_flag_is_repaired(self):
        flags = [flag for flag in file_consistency_flags(ROWS) if flag['id'] == 3]
        repaired, changes = repair_hey_call(ROWS, flags)
        self.assertEqual([change['id'] for change in changes], [3])
        self.assertEqual(repaired[0]['sv'], ROWS[0]['sv'])

    def test_the_repair_step_does_not_send_the_hey_flags_to_the_model(self):
        other = {'id': 4, 'category': 'meaning', 'source_quote': 'Something is coming!', 'target_quote': 'Något kommer!', 'reason': 'Fel ton.'}
        sent, steps = [], []

        def model(job, candidate, given, **kwargs):
            sent.append(sorted(flag['id'] for flag in given))
            return [dict(item) for item in candidate]

        with patch.object(worker, 'repair_flagged_translation_split', side_effect=model):
            result = worker.repair_flagged_translation({'id': 101}, ROWS, file_consistency_flags(ROWS) + [other], record=steps.append)
        self.assertEqual(sent, [[4]])
        self.assertEqual(result[2]['sv'], 'Hallå!')
        self.assertEqual([step['stage'] for step in steps], ['hey_call'])


if __name__ == '__main__':
    unittest.main()
