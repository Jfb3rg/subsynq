import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from translation_qa import (counted_animal_feet, deterministic_flags,
                            imperial_unit_flags, source_is_cut_off,
                            swedish_context_flags)


def row(source, target, identity=1):
    return {'id': identity, 'start': 1.0, 'end': 4.0, 'source': source, 'sv': target}


class SwedishWordSenseTests(unittest.TestCase):
    def test_wrong_neuter_agreement_is_not_a_meaning_guess(self):
        for target in ('Det är en vapen som skapades enbart för att dö.', 'Min barn är här.',
                       'Vi såg en hus.', 'Det är din arbete.'):
            sample = row('This is the source.', target)
            original = copy.deepcopy(sample)
            flags = deterministic_flags([sample])
            self.assertEqual([f['category'] for f in flags], ['grammar'])
            self.assertIn(flags[0]['target_quote'], target)
            self.assertEqual(sample, original)
        for target in ('Det är ett vapen som skapades enbart för att dö.', 'Mitt barn är här.',
                       'Vi såg ett hus.', 'Det är ditt arbete.', 'Min vän är här.',
                       'En vapenhandlare kom hit.', 'En barnvagn står där.'):
            self.assertEqual(deterministic_flags([row('This is the source.', target)]), [])
        self.assertEqual(deterministic_flags([{'id': 1, 'source': 'x', 'en': 'en vapen'}], 'en'), [])

    def test_throwing_out_diagnoses_is_not_throwing_oneself_around(self):
        source = 'Men de kaster rundt seg med diagnoser.'
        bad = row(source, 'Men de kastar runt sig med diagnoser.')
        self.assertTrue(deterministic_flags([bad]))
        for target in ('Men de kastar ur sig diagnoser.', 'Men de slänger sig med diagnoser.'):
            self.assertEqual(deterministic_flags([row(source, target)]), [])
        self.assertEqual(swedish_context_flags([row('He throws himself around.', 'Han kastar runt sig med väskan.')]), [])
        self.assertEqual(swedish_context_flags([row('Han kastar saker runt sig.', 'Han kastar saker runt sig.')]), [])

    def test_the_idiom_is_general_for_other_objects_and_danish(self):
        bad = row('Han kaster rundt sig med beskyldninger.', 'Han kastar runt sig med anklagelser.')
        flags = deterministic_flags([bad])
        self.assertEqual(len(flags), 1)
        self.assertIn(flags[0]['source_quote'], bad['source'])
        self.assertIn(flags[0]['target_quote'], bad['sv'])

    def test_dying_is_not_killing_even_when_the_subject_is_a_weapon(self):
        for source in ('死ぬためだけに作られた兵器です', 'It is a weapon made only to die.'):
            self.assertTrue(deterministic_flags([row(source, 'Det är ett vapen som skapades bara för att döda.')]))
            self.assertEqual(deterministic_flags([row(source, 'Det är ett vapen som skapades bara för att dö.')]), [])
        for source in ('殺すためだけに作られた兵器です', 'It is a weapon designed to kill.'):
            self.assertEqual(deterministic_flags([row(source, 'Det är ett vapen som skapades bara för att döda.')]), [])
        self.assertEqual(deterministic_flags([row('We must kill it or we are going to die.', 'Vi måste döda den eller dö.')]), [])
        self.assertEqual(deterministic_flags([row('It is designed to slay the monster and then to die.',
                                                  'Det är avsett för att döda monstret och sedan dö.')]), [])

    def test_the_source_grammar_note_does_not_invent_a_translation_or_a_victim(self):
        from translation_qa import source_grammar_notes
        source = '死ぬためだけに作られた兵器です'
        note = source_grammar_notes(source)
        self.assertIn(note['source_quote'], source)
        self.assertEqual(note['action'], 'die')
        self.assertIn('intransitive', note['valency'])
        self.assertIsNone(source_grammar_notes('殺すためだけに作られた兵器です'))
        self.assertIsNone(source_grammar_notes('This weapon kills people.'))

    def test_real_yukon_animal_forms_need_swedish_terms(self):
        examples = [('These caribou', 'Dessa karibuer'),
                    ('A single caribou', 'En enda karibu'),
                    ('a caribou herd', 'en karibuhjord'),
                    ('this caribou hunt', 'den här karibu-jakten'),
                    ("It's caribou season", 'Det är kaributid'),
                    ('Four caribou.', 'Fyra karibuer.')]
        for source, target in examples:
            with self.subTest(source=source):
                sample = row(source, target)
                original = copy.deepcopy(sample)
                flags = swedish_context_flags([sample])
                self.assertTrue(flags)
                self.assertEqual(sample, original)
                self.assertTrue(all(f['source_quote'] in source and f['target_quote'] in target for f in flags))

    def test_correct_swedish_inflections_and_compounds_pass(self):
        for source, target in [('These caribou', 'Dessa renar'),
                               ('A single caribou', 'En enda ren'),
                               ('a caribou herd', 'en renhjord'),
                               ('this caribou hunt', 'den här renjakten'),
                               ("It's caribou season", 'Det är renjaktsäsong')]:
            self.assertEqual(swedish_context_flags([row(source, target)]), [])

    def test_place_and_business_names_are_not_animals(self):
        for source in ('We hunt near Caribou Lake.', 'Get coffee at Caribou Coffee.',
                       'This dog is called Caribou.', 'My name is Caribou and I hunt.', 'Welcome to Caribou.'):
            self.assertEqual(swedish_context_flags([row(source, source)]), [])

    def test_real_tracks_not_droppings_even_on_cut_off_row(self):
        sample = row('a little further out, and having seen caribou tracks.',
                     'lite längre bort och sett kaributräck.')
        self.assertTrue(any('Tracks' in f['reason'] for f in swedish_context_flags([sample])))
        self.assertEqual(swedish_context_flags([row(sample['source'], 'lite längre bort och sett renspår.')]), [])
        self.assertEqual(swedish_context_flags([row('We found caribou droppings.', 'Vi hittade renspillning.')]), [])
        self.assertEqual(swedish_context_flags([row('Those are deer tracks, not droppings.',
                                                  'Det är hjortspår, inte spillning.')]), [])

    def test_body_part_count_is_not_a_correct_metric_conversion(self):
        source = "We're going to have eight dogs, so that's 32 extra feet"
        bad = row(source, 'Vi ska ha åtta hundar, så det är 9,7 meter')
        self.assertTrue(counted_animal_feet(source))
        self.assertTrue(deterministic_flags([bad]))
        self.assertEqual(imperial_unit_flags([bad]), [], 'must not recommend a metric conversion')
        for target in ('Vi ska ha åtta hundar, så det är 32 extra tassar',
                       'Vi ska ha åtta hundar, så det är 32 extra fötter'):
            self.assertEqual(deterministic_flags([row(source, target)]), [])

    def test_real_distance_with_dogs_keeps_conversion(self):
        for source in ('Eight dogs ran 32 feet.', "There are eight dogs, that's 32 feet away."):
            self.assertFalse(counted_animal_feet(source))
            self.assertEqual(deterministic_flags([row(source, 'Hundarna är 9,7 meter bort.')]), [])
            self.assertTrue(imperial_unit_flags([row(source, 'Hundarna är 32 fot bort.')]))

    def test_low_on_wood_does_not_mean_receiving_wood(self):
        sample = row('When we started getting low on wood back there,', 'När vi började få lite ved där bak,')
        self.assertTrue(source_is_cut_off([sample], 0))
        self.assertTrue(deterministic_flags([sample]))
        self.assertEqual(deterministic_flags([row(sample['source'], 'När veden började ta slut där borta,')]), [])
        self.assertEqual(deterministic_flags([row('We started getting some wood.', 'Vi började få lite ved.')]), [])

    def test_only_explicit_vehicle_term_not_guessed_source(self):
        self.assertTrue(deterministic_flags([row('Snowgo could freeze up.', 'Snowgo kan frysa.')]))
        self.assertEqual(deterministic_flags([row('Snowgo could freeze up.', 'Snöskotern kan frysa fast.')]), [])
        for source, target in [('I hear snow goes.', 'Jag hör att det snöar.'),
                               ('Go get the snow ghost.', 'Hämta snöspöket.'),
                               ('My name is Snowgo.', 'Mitt namn är Snowgo.'),
                               ('Snowgo went to Japan.', 'Snowgo åkte till Japan.')]:
            self.assertEqual(swedish_context_flags([row(source, target)]), [], 'uncertain ASR or names are not auto-corrected')

    def test_other_target_languages_not_subject_to_swedish_rules(self):
        sample = {'id': 1, 'source': 'Four caribou.', 'en': 'Four caribou.', 'sv': 'Fyra karibuer.'}
        self.assertEqual(deterministic_flags([sample], 'en'), [])
        self.assertEqual(swedish_context_flags([sample], 'en'), [])
        self.assertNotIn('renhjord', worker.output_rules('en'))

    def test_translation_draft_review_and_quality_review_receive_same_rules(self):
        seen = []
        rows = [row('Four caribou.', 'Fyra renar.')]
        def answer(request):
            seen.append(request['system'])
            body = json.loads(request['prompt'])
            if 'Quality-review each' in request['system'] or 'Translate every' in request['system']:
                return {'response': json.dumps({'rows': [{'id': 1, 'sv': 'Fyra renar.'}]})}
            return {'response': json.dumps({'comparisons': [{'id': 1, 'source_meaning': 'Four caribou.',
                'target_meaning': 'Fyra renar.', 'meaning_preserved': True}], 'flags': []})}
        with (patch.object(worker, 'call_ollama', side_effect=answer), patch.object(worker, 'check_cancelled'),
              patch.object(worker, 'identify_swedish_rows', return_value=set()), patch.object(worker, 'parallel_workers', return_value=1),
              patch.object(worker, 'update_job'), patch.object(worker, 'release_language_model_before_asr'),
              patch.object(worker, 'FOCUSED_REVIEW', True)):
            self.assertEqual(worker.translate({'id': 1}, rows)[0]['sv'], 'Fyra renar.')
            worker.assess_translation_quality(rows, audit=[], whole_file=True)
        self.assertGreaterEqual(len(seen), 3)
        self.assertTrue(all('renhjord' in system and 'body parts' in system for system in seen))

    def test_clean_model_verdict_cannot_hide_count_error_on_fragment(self):
        rows = [row("We're going to have eight dogs, so that's 32 extra feet,", 'Vi ska ha åtta hundar, så det är 9,7 meter')]
        self.assertTrue(source_is_cut_off(rows, 0))
        def approve(candidate, *, audit=None, **kwargs):
            audit.append({'ids': [1], 'bad_ids': [], 'flags': []})
        with (tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)),
              patch.object(worker, 'assess_translation_quality', side_effect=approve),
              patch.object(worker, 'check_cancelled'), patch('fragment_context_review.review_fragments', return_value=[])):
            with self.assertRaises(worker.SourceQualityError) as error:
                worker.review_and_save_translation({'id': 1}, rows, final=True)
        self.assertIn('betydelsefel', str(error.exception))


class ExonymTests(unittest.TestCase):
    def test_an_english_country_name_kept_in_swedish_is_flagged(self):
        # MAN OF WAR (dev job 170)
        rows = [{'id': 2, 'source': 'Russia overnight launched its long-anticipated attack on Ukraine.',
                 'sv': 'Ryssland inledde under natten sin länge emotsedda attack mot Ukraine.'}]
        flags = swedish_context_flags(rows)
        self.assertEqual([(flag['source_quote'], flag['target_quote']) for flag in flags], [('Ukraine', 'Ukraine')])

    def test_the_swedish_name_and_names_the_source_does_not_hold_are_fine(self):
        rows = [{'id': 1, 'source': 'Attack on Ukraine.', 'sv': 'Attack mot Ukraina.'},
                {'id': 2, 'source': 'We are headed to Kharkiv.', 'sv': 'Vi är på väg till Kharkiv.'},
                {'id': 3, 'source': 'He loves fine china.', 'sv': 'Han älskar fint porslin, China.'}]
        self.assertEqual(swedish_context_flags(rows), [])


class PluralYouTests(unittest.TestCase):
    """ANOTHER GERMAN TANK STORY (dev jobs 171-197): "Euer Panzer steht auf meinem Hof." -> "Er stridsvagn står på min gård." refused the file."""

    def flag(self, reason, target_quote='Er stridsvagn'):
        return {'id': 29, 'category': 'meaning', 'source_quote': 'Euer Panzer', 'target_quote': target_quote, 'reason': reason}

    def test_reading_swedish_er_as_german_er_is_removed(self):
        from translation_qa import unfounded_flags
        row = {'id': 29, 'source': 'Euer Panzer steht auf meinem Hof.', 'sv': 'Er stridsvagn står på min gård.'}
        for reason in ("Subjektet har ändrats från 'er/ert' (er/ert) till 'hans' (er), vilket ändrar vem som äger stridsvagnen.",
                       "'Euer' betyder 'er' (plural), inte 'hans'.", "Possessivpronomen har ändrats från 'er/ert' (your) till 'han/hans' (his)."):
            kept, removed = unfounded_flags([self.flag(reason)], [row])
            self.assertEqual((kept, len(removed)), ([], 1), reason)

    def test_a_real_error_stays(self):
        from translation_qa import unfounded_flags
        wrong = {'id': 29, 'source': 'Euer Panzer steht auf meinem Hof.', 'sv': 'Hans stridsvagn står på min gård.'}
        reason = "Subjektet har ändrats från 'er/ert' till 'hans'."
        self.assertEqual(len(unfounded_flags([self.flag(reason, 'Hans stridsvagn')], [wrong])[0]), 1)
        singular = {'id': 29, 'source': 'Euer Panzer steht auf meinem Hof.', 'sv': 'Din stridsvagn står på min gård.'}
        self.assertEqual(len(unfounded_flags([self.flag(reason, 'Din stridsvagn')], [singular])[0]), 1, '"din" is singular: a real finding')
        other = {'id': 3, 'source': 'Er kommt morgen.', 'sv': 'Ni kommer i morgon.'}
        self.assertEqual(len(unfounded_flags([{'id': 3, 'category': 'meaning', 'source_quote': 'Er', 'target_quote': 'Ni',
                                               'reason': "'Er' betyder 'han', inte 'ni'."}], [other])[0]), 1, 'German er (he) as ni is wrong')


class ManOfWarTranslationTests(unittest.TestCase):
    def test_buds_is_the_seal_training(self):
        rows = [{'id': 10, 'source': "Me and her dad, we've been in the teams together since Bud's.",
                 'sv': "Jag och hennes pappa har varit i samma team sedan Bud's tid."}]
        self.assertEqual(len(swedish_context_flags(rows)), 1)
        rows[0]['sv'] = 'Jag och hennes pappa har varit i samma team sedan BUD/S.'
        self.assertEqual(swedish_context_flags(rows), [])
        self.assertEqual(swedish_context_flags([{'id': 1, 'source': "Bud's coming over tonight.", 'sv': 'Bud kommer hit i kväll.'}]), [])

    def test_a_passive_verb_with_an_object_is_flagged(self):
        flags = swedish_context_flags([{'id': 3, 'source': "He's about to jam the door.", 'sv': 'Han ska precis kilas fast dörren.'}])
        self.assertEqual([flag['target_quote'] for flag in flags], ['ska precis kilas fast dörren'])
        for fine in ('Han ska precis kila fast dörren.', 'Dörren måste låsas.', 'Han kan inte lämnas ensam.', 'Det ska göras i dag.'):
            self.assertEqual(swedish_context_flags([{'id': 3, 'source': 'x', 'sv': fine}]), [], fine)

    def test_the_rule_reaches_the_translator(self):
        from translation_qa import SWEDISH_MEANING_RULE
        self.assertIn('BUD/S', SWEDISH_MEANING_RULE)
        self.assertIn('kila fast dörren', SWEDISH_MEANING_RULE)


class NorwegianPossessiveTests(unittest.TestCase):
    def test_a_postposed_possessive_read_as_a_pronoun_is_flagged(self):
        # Glemselens øy (dev job 205)
        rows = [{'id': 9, 'source': 'Glowland din er så kul.', 'sv': 'Glowland, din är så cool.'}]
        self.assertEqual([flag['target_quote'] for flag in swedish_context_flags(rows)], ['Glowland, din'])
        for fine in ('Din Glowland är så cool.', 'Glowland är så cool.'):
            self.assertEqual(swedish_context_flags([{'id': 9, 'source': 'Glowland din er så kul.', 'sv': fine}]), [], fine)
        self.assertEqual(swedish_context_flags([{'id': 1, 'source': 'Hun er min venn.', 'sv': 'Hon är min vän.'}]), [])

    def test_the_rule_reaches_the_translator(self):
        from translation_qa import SWEDISH_MEANING_RULE
        self.assertIn('din Glowland', SWEDISH_MEANING_RULE)
        self.assertIn('pæra', SWEDISH_MEANING_RULE)


class NorwegianFalseFriendTests(unittest.TestCase):
    def test_maa_ikke_is_a_prohibition(self):
        # Agnes mot verden (dev job 202)
        rows = [{'id': 21, 'source': 'Hvis jeg ringer deg, så må du ikke ta telefonen.', 'sv': 'Om jag ringer dig, så måste du inte svara i telefonen.'}]
        self.assertEqual([flag['target_quote'] for flag in swedish_context_flags(rows)], ['måste du inte'])
        rows[0]['sv'] = 'Om jag ringer dig, får du inte svara.'
        self.assertEqual(swedish_context_flags(rows), [])
        self.assertEqual(swedish_context_flags([{'id': 1, 'source': 'Jeg må gjøre det her alene.', 'sv': 'Jag måste göra det här ensam.'}]), [])



class TitleAndMilitaryWordTests(unittest.TestCase):
    """FORTITUDE (dev job 218): both errors passed the review."""

    def test_prime_minister_must_not_become_minister(self):
        rows = [row('Prime Minister, I believe we need to create an entire American army',
                    'Minister, jag tror att vi måste skapa en hel amerikansk armé.')]
        self.assertEqual([flag['target_quote'] for flag in swedish_context_flags(rows)], ['Minister'])
        for fine in ('Premiärministern, jag tror att vi måste skapa en hel amerikansk armé.',
                     'Herr premiärminister, jag tror att vi måste skapa en hel amerikansk armé.'):
            self.assertEqual(swedish_context_flags([row(rows[0]['source'], fine)]), [], fine)
        self.assertEqual(swedish_context_flags([row('Ask the Minister of Defence.', 'Fråga försvarsministern.')]), [])

    def test_military_tanks_are_stridsvagnar(self):
        source = "We'll need tanks, airplanes, ships, men."
        self.assertEqual([flag['target_quote'] for flag in swedish_context_flags(
            [row(source, 'Vi kommer att behöva tanks, flygplan, skepp och män.')])], ['tanks'])
        self.assertEqual([flag['target_quote'] for flag in swedish_context_flags(
            [row(source, 'Vi kommer att behöva tankar, flygplan, skepp och män.')])], ['tankar'])
        self.assertEqual(swedish_context_flags([row(source, 'Vi kommer att behöva stridsvagnar, flygplan, skepp och män.')]), [])

    def test_containers_and_think_tanks_are_not_military(self):
        for source, target in (('Fill the gas tank.', 'Fyll tanken.'), ('The water tanks are empty.', 'Vattentankarna är tomma.'),
                               ('She works at a think tank.', 'Hon jobbar på en tankesmedja.'), ('The tank is full of fish.', 'Tanken är full av fisk.')):
            self.assertEqual(swedish_context_flags([row(source, target)]), [], source)

    def test_the_rule_reaches_the_translator(self):
        from translation_qa import SWEDISH_MEANING_RULE
        self.assertIn('premiärminister', SWEDISH_MEANING_RULE)
        self.assertIn('stridsvagn', SWEDISH_MEANING_RULE)



class EfterAndAlliesTests(unittest.TestCase):
    def grammar(self, source, target):
        from translation_qa import swedish_grammar_flags
        return [flag['target_quote'] for flag in swedish_grammar_flags([row(source, target)])]

    def test_efter_before_a_clause_needs_att(self):
        # Agnes mot verden (dev jobs 123 and 246)
        self.assertEqual(self.grammar('Ok, jeg ringer deg etter jeg blir voldtatt.', 'Okej, jag ringer dig efter jag blir våldtagen.'),
                         ['efter jag blir'])
        for fine in ('Okej, jag ringer dig efter att jag har blivit våldtagen.', 'Han kom efter de senaste åren.',
                     'Kom efter mig!', 'Efter det gick vi hem.', 'Vi letar efter dem.'):
            self.assertEqual(self.grammar('x', fine), [], fine)

    def test_the_allies_take_the_article(self):
        # FORTITUDE (dev jobs 218, 221 and 248)
        source = 'The Allies are gonna attack Hitler.'
        self.assertEqual(self.grammar(source, 'Allierade kommer att anfalla Hitler.'), ['Allierade'])
        for fine in ('De allierade kommer att anfalla Hitler.', 'Våra allierade kommer att anfalla Hitler.'):
            self.assertEqual(self.grammar(source, fine), [], fine)
        self.assertEqual(self.grammar('Our allied friends.', 'Allierade vänner.'), [])

    def test_the_rules_reach_the_translator(self):
        from translation_qa import SWEDISH_MEANING_RULE
        self.assertIn('de allierade', SWEDISH_MEANING_RULE)
        self.assertIn('efter att', SWEDISH_MEANING_RULE)


if __name__ == '__main__':
    unittest.main()
