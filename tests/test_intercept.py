import sys
import unittest

sys.path[:0] = ['/app', '/ai']
from translation_qa import SWEDISH_MEANING_RULE, deterministic_flags, intercept_flag, repair_intercept


def row(source, sv, row_id=1):
    return {'id': row_id, 'start': 1.0, 'end': 3.0, 'source': source, 'sv': sv}


class InterceptTests(unittest.TestCase):
    def test_interrupting_is_flagged_and_repaired_for_something_that_moves(self):
        cases = (('They left to intercept a supply convoy.', 'De gav sig av för att avbryta en förrådskonvoj.', 'De gav sig av för att genskjuta en förrådskonvoj.'),
                 ('We intercepted the ship at dawn.', 'Vi avbröt skeppet i gryningen.', 'Vi genskjöt skeppet i gryningen.'),
                 ('Intercept them before the bridge!', 'Avbryt dem före bron!', 'Genskjut dem före bron!'),
                 ('The fighters have intercepted two enemy bombers.', 'Jaktplanen har avbrutit två fientliga bombplan.',
                  'Jaktplanen har genskjutit två fientliga bombplan.'),
                 ('He was sent to intercept the convoy.', 'Han skickades för att avlyssna konvojen.', 'Han skickades för att genskjuta konvojen.'))
        for source, wrong, right in cases:
            rows = [row(source, wrong)]
            flags = [flag for flag in deterministic_flags(rows) if flag['reason'].startswith('Intercept är')]
            self.assertEqual(len(flags), 1, source)
            self.assertEqual(flags[0]['category'], 'meaning')
            repaired, changes = repair_intercept(rows, flags)
            self.assertEqual(repaired[0]['sv'], right)
            self.assertEqual((changes[0]['before'], changes[0]['after']), (wrong, right))
            self.assertEqual(deterministic_flags(repaired), [])
            self.assertEqual(rows[0]['sv'], wrong)                    # the input rows are not changed

    def test_a_message_is_flagged_but_left_to_the_ordinary_repair(self):
        rows = [row('We intercepted a message from the island.', 'Vi avbröt ett meddelande från ön.')]
        flags = deterministic_flags(rows)
        self.assertEqual([flag['target_quote'] for flag in flags], ['avbröt'])
        self.assertEqual(repair_intercept(rows, flags), (rows, []))

    def test_right_translations_and_other_sources_are_left_alone(self):
        for source, sv in (('They left to intercept a supply convoy.', 'De gav sig av för att genskjuta en förrådskonvoj.'),
                           ('They left to intercept a supply convoy.', 'De gav sig av för att fånga upp en förrådskonvoj.'),
                           ("Captain, we've intercepted a transmission.", 'Kapten, vi har avlyssnat en sändning.'),
                           ("Captain, we've intercepted a transmission.", 'Kapten, vi har snappat upp en sändning.'),
                           ('Intercept the convoy and abort the landing.', 'Genskjut konvojen och avbryt landstigningen.'),
                           ('Sorry to interrupt the meeting.', 'Ursäkta att jag avbryter mötet.'),
                           ('We listened to the convoy all night.', 'Vi avlyssnade konvojen hela natten.')):
            self.assertIsNone(intercept_flag(source, sv), (source, sv))
            self.assertEqual(deterministic_flags([row(source, sv)]), [])

    def test_only_a_requested_single_verb_is_changed(self):
        rows = [row('They left to intercept a supply convoy.', 'De gav sig av för att avbryta en förrådskonvoj.')]
        self.assertEqual(repair_intercept(rows, []), (rows, []))
        self.assertEqual(repair_intercept(rows, deterministic_flags(rows), target_key='en'), (rows, []))
        twice = [row('Intercept the convoy, intercept it now!', 'Avbryt konvojen, avbryt den nu!')]
        self.assertEqual(repair_intercept(twice, deterministic_flags(twice))[0][0]['sv'], 'Genskjut konvojen, genskjut den nu!')
        more = [row('Intercept the convoy now!', 'Avbryt konvojen, avbryt allt nu!')]   # a second verb the source has no "intercept" for
        self.assertEqual(repair_intercept(more, deterministic_flags(more)), (more, []))

    def test_the_translator_is_told_the_rule(self):
        self.assertIn('genskjuta', SWEDISH_MEANING_RULE)


if __name__ == '__main__':
    unittest.main()
