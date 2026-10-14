import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker
from translation_qa import capitalizes_common_nouns, name_key, proper_name_forms, quote_is_only_names


def rows(*pairs):
    return [{'id': n, 'start': n * 2, 'end': n * 2 + 1, 'source': source, 'sv': target}
            for n, (source, target) in enumerate(pairs)]


class NameEvidenceTests(unittest.TestCase):
    """Real job 17 (Ray Gunn): speech cannot tell "Gun" from "Gunn", so a word is a name when the
    FILE writes it as one: a capital inside a sentence, or right after a title, even across rows."""

    def test_a_title_in_one_row_names_the_first_word_of_the_next(self):
        forms = proper_name_forms(['Most flameouts leave', 'Mr.', 'Gunn, I presume?', 'Like you, gun.'])
        self.assertEqual(forms, {name_key('Gunn'): ['Gunn']})

    def test_a_capital_inside_a_sentence_is_a_name_but_a_row_start_is_not(self):
        self.assertEqual(proper_name_forms(['Welcome to Venus Nova.']),
                         {name_key('Venus'): ['Venus'], name_key('Nova'): ['Nova']})
        self.assertEqual(proper_name_forms(['Truth?', 'Classy.', 'Stay tuned.', 'I know. Well, then.']), {})

    def test_the_title_does_not_leak_past_the_next_word(self):
        self.assertEqual(proper_name_forms(['Mr. Smith left', 'Jump now']), {name_key('Smith'): ['Smith']})

    def test_doubled_letters_and_case_do_not_change_the_key(self):
        self.assertEqual({name_key(w) for w in ('Gun', 'Gunn', 'gun', 'GUNN')}, {'gun'})
        forms = proper_name_forms(['Mr. Gunn'])
        self.assertTrue(quote_is_only_names('gun', forms))
        self.assertFalse(quote_is_only_names('gun fight', forms))   # every word must be a name
        self.assertFalse(quote_is_only_names('', forms))


class NameNotFlaggedTests(unittest.TestCase):
    FLAG = {'id': 3, 'category': 'untranslated', 'source_quote': 'gun', 'target_quote': 'Liksom du, gun.',
            'reason': "Ordet 'gun' är inte översatt till svenska."}

    @staticmethod
    def answer(*flags):
        return {'response': json.dumps({'flags': list(flags)})}

    def test_untranslated_name_is_not_a_defect_when_the_file_writes_it_as_a_name(self):
        data = rows(('Mr.', 'Herr'), ('Gunn, I presume?', 'Gunn, antar jag?'), ('Stay tuned.', 'Håll utkik.'),
                    ('Like you, gun.', 'Liksom du, gun.'))
        audit = []
        with patch.object(worker, 'call_ollama', return_value=self.answer(self.FLAG)):
            self.assertTrue(worker.assess_translation_quality(data, audit=audit))
        self.assertEqual(audit[0]['bad_ids'], [])
        self.assertEqual(audit[0]['flags'], [])
        kept = audit[0]['names_kept']        # the exemption is reported, never silent
        self.assertEqual(kept[0]['id'], 3)
        self.assertEqual(kept[0]['written_as_name'], ['Gunn'])
        self.assertIn('stavningen', kept[0]['note'])

    def test_without_name_evidence_the_same_flag_still_counts(self):
        data = rows(('Stay tuned.', 'Håll utkik.'), ('Welcome.', 'Välkommen.'), ('Hello.', 'Hej.'),
                    ('Like you, gun.', 'Liksom du, gun.'))
        audit = []
        with patch.object(worker, 'call_ollama', return_value=self.answer(self.FLAG)):
            worker.assess_translation_quality(data, audit=audit)
        self.assertEqual(audit[0]['bad_ids'], [3])
        self.assertNotIn('names_kept', audit[0])

    def test_a_name_exemption_covers_only_untranslated_findings(self):
        data = rows(('Mr.', 'Herr'), ('Gunn, I presume?', 'Gunn, antar jag?'), ('Stay tuned.', 'Stanna kvar.'),
                    ('Like you, gun.', 'Liksom du, gun.'))
        meaning = {'id': 2, 'category': 'meaning', 'source_quote': 'Stay tuned.',
                   'target_quote': 'Stanna kvar.', 'reason': 'Idiomet betyder håll utkik, inte stanna kvar.'}
        audit = []
        with patch.object(worker, 'call_ollama', return_value=self.answer(meaning, self.FLAG)):
            worker.assess_translation_quality(data, audit=audit)
        self.assertEqual(audit[0]['bad_ids'], [2])

    def test_translator_and_repair_are_told_which_words_are_names(self):
        data = rows(('Mr.', 'Herr'), ('Gunn, I presume?', 'Gunn, antar jag?'), ('Stay tuned.', 'Stanna kvar.'))
        self.assertIn('Gunn', worker.name_instruction(data))
        self.assertEqual(worker.name_instruction(rows(('Hello.', 'Hej.'))), '')
        flag = {'id': 2, 'category': 'meaning', 'source_quote': 'Stay tuned.', 'target_quote': 'Stanna kvar.',
                'reason': 'Idiomet är fel översatt.'}
        response = {'response': json.dumps({'rows': [{'id': 2, 'intended_meaning': 'Continue following.',
                    'edits': [{'before': 'Stanna kvar.', 'after': 'Vänta och se.'}]}]})}
        rendered = {'response': json.dumps({'rows': [{'id': 2, 'after': 'Vänta och se.'}]})}
        with patch.object(worker, 'call_ollama', side_effect=[response, rendered]) as model, \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'repair_target_grammar', side_effect=lambda job, r, *a, **k: r):
            worker.repair_flagged_translation({'id': 9}, data, [flag])
        self.assertIn('Gunn', model.call_args.args[0]['system'])


GERMAN = [
    'Ich habe das Messer in der Küche gefunden.',
    'Das ist ein Meisterwerk, nicht wahr?',
    'Wir müssen mit den Kollegen über den Mord sprechen.',
    'Nein, ich glaube nicht, dass die Leute das Frühstück mögen.',
    'Der Mörder ist noch immer im Haus.',
    'Sie hat das Geld in die Tasche gesteckt und ist gegangen.',
    'Und was hat der Mann zu dem Angriff gesagt?',
    'Meine Großtante kocht gern, aber sie kann das Kochen nicht erklären.',
    'Ich sehe kein Monster, nur ein Kind mit einem Spielzeug.',
    'Wir haben das Leben und den Tod gesehen.',
    'Auf dem Tisch liegt ein Brief für die Frau.',
    'Das ist der Name von dem Mann mit dem Hut.',
    'Ihr habt die Wahl zwischen dem Feuer und dem Wasser.',
    'Nein, es ist nicht der Hund, es ist die Katze.',
]


class GermanNounsAreNotNamesTests(unittest.TestCase):
    """Real jobs 43 and 44: German writes every noun with a capital, so "Messer", "Mord", "Kollegen" were
    handed to the translator as names to copy unchanged and the Swedish came back full of German nouns."""

    def test_a_german_text_has_no_names_from_capitals_alone(self):
        self.assertTrue(capitalizes_common_nouns(GERMAN))
        self.assertEqual(proper_name_forms(GERMAN), {})
        self.assertEqual(proper_name_forms(row for row in GERMAN), {})      # also from a generator

    def test_the_translator_is_not_told_to_keep_german_nouns(self):
        data = rows(*[(line, 'x') for line in GERMAN])
        self.assertEqual(worker.name_instruction(data), '')

    def test_an_english_text_keeps_its_names(self):
        english = ['Welcome to Venus Nova.', 'Mr. Smith left London and went home to John.',
                   'We told Anna that the plane was late and that Paris would have to wait.',
                   'The captain said they would reach Berlin before the storm hit Madrid.',
                   'I know that you were with Peter in Rome when the lights went out.',
                   'Not here, not now, and not with them around the house, you understand.',
                   'He gave the letter to Maria and told her to meet him at Union Station.']
        self.assertFalse(capitalizes_common_nouns(english * 3))
        forms = proper_name_forms(english * 3)
        self.assertEqual(forms[name_key('Venus')], ['Venus'])
        self.assertIn(name_key('Maria'), forms)

    def test_a_short_text_is_never_taken_for_german(self):
        self.assertFalse(capitalizes_common_nouns(['Ich sehe Hans nicht.']))
        self.assertEqual(proper_name_forms(['Ich sehe Hans nicht.']), {name_key('Hans'): ['Hans']})

    def test_a_swedish_text_with_names_is_not_german(self):
        swedish = ['Vi åkte till Stockholm med Anna och Erik.', 'Han sa att Karin skulle komma till Göteborg.',
                   'Det var inte jag som ringde Olle på måndagen.', 'Hon träffade Johan och Maria utanför Malmö.'] * 4
        self.assertFalse(capitalizes_common_nouns(swedish))


if __name__ == '__main__':
    unittest.main()
