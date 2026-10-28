import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from translation_qa import is_address_choice, is_self_contradiction, refuted_target_claim, unfounded_flags


def flag(row_id, reason, category='meaning', source_quote='x', target_quote='y'):
    return {'id': row_id, 'category': category, 'reason': reason, 'source_quote': source_quote, 'target_quote': target_quote}


def row(row_id, source, sv):
    return {'id': row_id, 'start': row_id, 'end': row_id + 1, 'source': source, 'sv': sv}


# Real findings of dev jobs 43 (Ein Talent fürs Morden) and 44 (Man of War), gemma4:26b reviewing.
SIE = [
    (row(3, 'Ich weiß, wer Sie sind.', 'Jag vet vem ni är.'),
     flag(3, "Övergång från formellt 'Sie' till informellt 'du' ändrar tonen i samtalet.", 'grammar')),
    (row(13, 'Ich kann Ihnen helfen.', 'Jag kan hjälpa Er.'),
     flag(13, "Pronomenet ändras från formellt 'Ihnen' (er/dem) till informellt 'dig' (dig).")),
    (row(37, 'Eines Tages werden Sie in den Spiegel blicken.', 'En dag kommer du att se dig i spegeln.'),
     flag(37, "Subjektet ändras från plural/formellt 'Sie' (ni/ni/De) till singular 'du' (du).")),
    (row(31, 'Sie bringen sie um.', 'Ni dödar henne.'), flag(31, "Subjektet ändras från 'de' (Sie) till 'ni' (Ni).")),
    (row(39, 'Der Teufel, der Ihnen entgegen lächelt.', 'Djävulen som ler emot dig.'),
     flag(39, "Personligt pronomen har ändrats från formellt (Ihnen) till informellt (dig).")),
]
INVENTED = [
    (row(6, 'haben das Wesen ausgestürmt.', 'De stormade ut ur varelsen.'),
     flag(6, "Källtexten säger 'Wesen' (väsen/essens), men översättningen säger 'byggnaden' (Gebäude).")),
    (row(28, 'Alles klar, ich mal.', 'Okej, jag målar.'),
     flag(28, "Källtexten säger 'ich mal' (jag målar), medan måltexten säger 'jag fixar det'.")),
    (row(24, 'ihrer Großtante zufällt.', 'Arvet faller på hennes gammeltante.'),
     flag(24, "Källtexten säger 'storfadern/storfadern' (Großtante), men översättningen säger bara 'moster' (aunt).")),
]
REAL = [
    (row(8, 'Er hat einen ziemlichen Ruf.', 'Han har ett rätt så dåligt rykte.'),
     flag(8, "Källan anger ett 'betydande rykte', medan målet lägger till 'dåligt' som inte finns i källan.")),
    (row(28, 'Alles klar, ich mal.', 'Okej, jag då.'),
     flag(28, "Källtexten säger 'jag målar' (ich mal), medan måltexten säger 'jag då' (me then), vilket ändrar innebörden.")),
    (row(6, 'haben das Wesen ausgestürmt.', 'De stormade ut ur byggnaden, drivna av ett väsen.'),
     flag(6, "Källtexten säger 'väsendet' (Wesen), medan målspråket ändrar betydelsen till 'byggnaden' (building).")),
    (row(5, 'Sie', 'Dem'), flag(5, "Källan 'Sie' syftar på pluralis (de), medan målet 'Dem' är ett dativpronomen ('dem').")),
    (row(10, 'Meine nicht.', 'Inte jag.'),
     flag(10, "Källtexten betyder 'Inte min' (t.ex. min väg/metod), medan målspråket betyder 'Inte jag'.")),
]


class RefutedClaimTests(unittest.TestCase):
    def test_a_claim_about_words_the_translation_lacks_is_refuted(self):
        for item, finding in INVENTED:
            with self.subTest(row=item['id']):
                self.assertTrue(refuted_target_claim(finding, item['sv']))

    def test_a_claim_the_translation_bears_out_is_not(self):
        for item, finding in REAL:
            with self.subTest(row=item['id'], reason=finding['reason'][:30]):
                self.assertIsNone(refuted_target_claim(finding, item['sv']))

    def test_only_what_the_target_says_is_checked_never_what_the_source_says(self):
        """A Korean or Tamil row 'says' nothing in Swedish: the quote is the reviewer's own gloss."""
        finding = flag(1, "Källtexten säger 'vid seger är det 8 personer', medan målspråket ändrar betydelsen.")
        self.assertIsNone(refuted_target_claim(finding, 'För att vinna är vi åtta stycken.'))

    def test_an_inflected_form_of_the_quoted_word_counts_as_found(self):
        finding = flag(30, "Men översättningen säger 'moster' för Großtante.")
        self.assertIsNone(refuted_target_claim(finding, 'Och hur dödar han mostern?'))
        self.assertTrue(refuted_target_claim(finding, 'Och hur dödar han gammeltanten?'))

    def test_alternatives_count_as_found_when_one_of_them_is(self):
        finding = flag(1, "Målspråket säger 'hon/hen' men borde vara 'de'.")
        self.assertIsNone(refuted_target_claim(finding, 'Hen går.'))
        self.assertTrue(refuted_target_claim(finding, 'De går.'))

    def test_a_gloss_with_betyder_is_never_a_claim_about_the_text(self):
        finding = flag(10, "Målspråket betyder 'Inte jag' men källan betyder 'Inte min'.")
        self.assertIsNone(refuted_target_claim(finding, 'Inte mina.'))

    def test_a_reason_without_any_claim_is_left_alone(self):
        self.assertIsNone(refuted_target_claim(flag(1, 'Betydelsen har ändrats.'), 'Text.'))
        self.assertIsNone(refuted_target_claim({'id': 1}, 'Text.'))


class AddressChoiceTests(unittest.TestCase):
    def test_a_finding_that_only_quarrels_with_du_or_ni_for_sie_is_an_address_choice(self):
        for item, finding in SIE:
            with self.subTest(row=item['id']):
                self.assertTrue(is_address_choice(finding, item['source'], item['sv']))

    def test_a_translation_that_leaves_the_addressee_out_stays_flagged(self):
        finding = flag(13, "Pronomenet ändras från formellt 'Ihnen' till informellt 'dig'.")
        self.assertFalse(is_address_choice(finding, 'Ich kann Ihnen helfen.', 'Jag kan hjälpa.'))

    def test_a_row_without_a_formal_you_is_never_an_address_choice(self):
        finding = flag(1, "Pronomenet ändras från formellt till informellt 'du'.")
        self.assertFalse(is_address_choice(finding, 'Ich kann dir helfen.', 'Jag kan hjälpa dig.'))

    def test_a_finding_about_something_else_in_the_same_row_is_kept(self):
        finding = flag(1, "Verbet 'helfen' har blivit 'stjäla', vilket ändrar handlingen.")
        self.assertFalse(is_address_choice(finding, 'Ich kann Ihnen helfen.', 'Jag kan stjäla från dig.'))

    def test_a_finding_that_adds_a_second_problem_is_kept(self):
        finding = flag(16, "Adressaten ändras från formellt 'Sie' till informellt 'du', samt ett grammatikfel i 'en Mörder'.")
        self.assertFalse(is_address_choice(finding, 'dass Sie sich in einen Mörder hineinversetzen', 'att du kan sätta dig in i en Mörder'))


# Real findings: Nelly Rapp (dev jobs 57, 60, 61, 62, 63) and FRANCE (job 71). The reviewer calls a word wrong and then names the same word as right.
SELF_CONTRADICTING = [
    (row(22, 'Men vems stjärt?', 'Men vems stjärt?'),
     flag(22, "Ordfel i 'stjärt'. Det korrekta ordet är 'stjärt' (vilket redan finns i källan), men i målspråket är det stavat fel eller så är det en "
              "förväxling med 'stjärta'. Eftersom källan är svensk och målet är svenskt, är 'stjärt' inte ett korrekt ord i detta sammanhang.", 'grammar')),
    (row(11, 'Des civils, des enfants, des hommes, des femmes.', 'Civila, barn, män, kvinnor.'),
     flag(11, "Ordet 'Civila' är felstavat; det korrekta ordet är 'Civila' (pluralis) men i detta sammanhang är det troligtvis en felskrivning av "
              "'Civila' eller 'Civilister'.", 'grammar')),
]


class SelfContradictionTests(unittest.TestCase):
    def test_a_word_named_as_wrong_and_as_right_is_set_aside_with_its_reason(self):
        for item, finding in SELF_CONTRADICTING:
            with self.subTest(row=item['id']):
                self.assertTrue(is_self_contradiction(finding, item['sv']))
                kept, removed = unfounded_flags([finding], [item])
                self.assertEqual(kept, [])
                self.assertEqual(removed[0]['why'], 'granskaren anger samma ord som fel och som rätt')
                self.assertEqual(removed[0]['reason'], finding['reason'])

    def test_a_real_spelling_finding_is_kept(self):
        item = row(3, 'Tack för maten.', 'Tack för matten.')
        for reason in ("Ordet 'matten' är felstavat; det korrekta ordet är 'maten'.", "Ordet 'matten' ska vara 'maten'.",
                       "'matten' borde vara 'maten' eftersom källan säger mat."):
            with self.subTest(reason=reason):
                finding = flag(3, reason, 'grammar')
                self.assertFalse(is_self_contradiction(finding, item['sv']))
                self.assertEqual(unfounded_flags([finding], [item]), ([finding], []))

    def test_a_difference_of_a_capital_is_a_real_finding(self):
        item = row(1, 'Je suis suédois.', 'Jag är svensk och bor i sverige.')
        finding = flag(1, "Ordet 'sverige' är felstavat; det korrekta ordet är 'Sverige'.", 'grammar')
        self.assertFalse(is_self_contradiction(finding, item['sv']))

    def test_a_second_problem_in_the_same_remark_keeps_the_finding(self):
        item = row(22, 'Men vems stjärt?', 'Men vems stjärt?')
        finding = flag(22, "Ordet 'stjärt' är felstavat; det korrekta ordet är 'stjärt', dessutom saknas ett frågeord.", 'grammar')
        self.assertFalse(is_self_contradiction(finding, item['sv']))

    def test_the_word_must_stand_in_the_row_as_written(self):
        finding = flag(22, "Ordet 'stjärt' är felstavat; det korrekta ordet är 'stjärt'.", 'grammar')
        self.assertFalse(is_self_contradiction(finding, 'Men vems rumpa?'))

    def test_a_remark_without_a_stated_correction_is_not_touched(self):
        finding = flag(1, "Ordet 'stjärt' passar inte i sammanhanget.", 'grammar')
        self.assertFalse(is_self_contradiction(finding, 'Men vems stjärt?'))
        self.assertFalse(is_self_contradiction(flag(1, '', 'grammar'), 'Men vems stjärt?'))

    def test_other_categories_are_never_removed_by_it(self):
        item, finding = SELF_CONTRADICTING[0]
        for category in ('untranslated', 'transliteration', 'fragment', 'source_uncertain'):
            self.assertEqual(unfounded_flags([{**finding, 'category': category}], [item]), ([{**finding, 'category': category}], []), category)

    def test_the_real_reviewer_slip_no_longer_fails_a_row_in_the_real_review(self):
        item, finding = SELF_CONTRADICTING[0]
        kept, removed = unfounded_flags([finding, flag(5, "Källan innehåller en negation som översättningen tappat.")], [item, row(5, 'Non, jamais.', 'Ja, alltid.')])
        self.assertEqual([entry['id'] for entry in removed], [22])
        self.assertEqual([entry['id'] for entry in kept], [5], 'a different finding in the same review stays')


class UnfoundedFlagsTests(unittest.TestCase):
    def test_address_choices_and_invented_claims_are_set_aside_each_with_its_reason(self):
        for name, pairs in (('address', SIE), ('invented', INVENTED)):
            for item, finding in pairs:
                with self.subTest(kind=name, row=item['id']):
                    kept, removed = unfounded_flags([finding], [item])
                    self.assertEqual(kept, [])
                    self.assertEqual([entry['id'] for entry in removed], [item['id']])
                    self.assertTrue(removed[0]['why'])
                    self.assertEqual(removed[0]['reason'], finding['reason'])

    def test_real_findings_are_all_kept(self):
        for item, finding in REAL:
            with self.subTest(row=item['id'], reason=finding['reason'][:30]):
                self.assertEqual(unfounded_flags([finding], [item]), ([finding], []))

    def test_untranslated_and_transliteration_findings_are_never_touched(self):
        item = row(1, 'Ich kann Ihnen helfen.', 'Jag kan hjälpa Er.')
        for category in ('untranslated', 'transliteration', 'fragment', 'source_uncertain'):
            finding = flag(1, "Pronomenet ändras från formellt 'Ihnen' till informellt 'dig'.", category)
            self.assertEqual(unfounded_flags([finding], [item]), ([finding], []), category)

    def test_a_finding_for_an_unknown_row_is_kept(self):
        finding = flag(99, "Pronomenet ändras från formellt 'Ihnen' till informellt 'dig'.")
        self.assertEqual(unfounded_flags([finding], [row(1, 'a', 'b')]), ([finding], []))


class ReviewIgnoresUnfoundedFindingsTests(unittest.TestCase):
    """The real assess_translation_quality with a reviewer that repeats the slips of jobs 43 and 44."""

    def setUp(self):
        for name in ('check_cancelled', 'update_job'):
            patcher = patch.object(worker, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(worker, 'FOCUSED_REVIEW', False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def review(self, rows, flags):
        answer = {'response': json.dumps({'flags': [
            {'id': item['id'], 'category': item['category'], 'reason': item['reason'],
             'source_quote': item['source_quote'], 'target_quote': item['target_quote']} for item in flags]})}
        audit = []
        with patch.object(worker, 'call_ollama', return_value=answer):
            result = worker.assess_translation_quality(rows, audit=audit)
        return result, audit

    def test_a_review_full_of_address_and_invented_findings_comes_out_clean(self):
        rows = [row(3, 'Ich weiß, wer Sie sind.', 'Jag vet vem ni är.'),
                row(6, 'haben das Wesen ausgestürmt.', 'De stormade ut ur varelsen.'),
                row(7, 'Er hat einen Ruf.', 'Han har ett rykte.'),
                row(8, 'Ja.', 'Ja.'), row(9, 'Nein.', 'Nej.'), row(10, 'Gut.', 'Bra.'), row(11, 'Schön.', 'Fint.'),
                row(12, 'Danke.', 'Tack.')]
        flags = [flag(3, "Övergång från formellt 'Sie' till informellt 'du'.", source_quote='Sie', target_quote='ni'),
                 flag(6, "Källtexten säger 'Wesen', men översättningen säger 'byggnaden'.",
                      source_quote='Wesen', target_quote='varelsen')]
        result, audit = self.review(rows, flags)
        self.assertTrue(result)
        self.assertEqual(audit[0]['bad_ids'], [])
        self.assertEqual(audit[0]['flags'], [])
        self.assertEqual({entry['id'] for entry in audit[0]['set_aside_flags']}, {3, 6})
        self.assertIn('Sie', audit[0]['set_aside_flags'][0]['reason'] + audit[0]['set_aside_flags'][1]['reason'])

    def test_a_real_finding_next_to_set_aside_ones_still_counts(self):
        rows = [row(3, 'Ich weiß, wer Sie sind.', 'Jag vet vem ni är.'),
                row(8, 'Er hat einen ziemlichen Ruf.', 'Han har ett rätt så dåligt rykte.')]
        flags = [flag(3, "Övergång från formellt 'Sie' till informellt 'du'.", source_quote='Sie', target_quote='ni'),
                 flag(8, "Målet lägger till 'dåligt' som inte finns i källan.", source_quote='ziemlichen Ruf',
                      target_quote='dåligt rykte')]
        result, audit = self.review(rows, flags)
        self.assertTrue(result)
        self.assertEqual(audit[0]['bad_ids'], [8])
        self.assertEqual([entry['id'] for entry in audit[0]['set_aside_flags']], [3])



class RayGunnSelfContradictionTests(unittest.TestCase):
    """Ray Gunn (dev job 275): a correct line was flagged with a fix identical to what it already says, and the file was refused."""
    TARGET = 'Säg mig vem som anlitade dig, annars kastar jag ut dig genom fönstret.'

    def test_should_be_x_not_x_is_a_contradiction(self):
        flag = {'id': 28, 'category': 'grammar', 'target_quote': self.TARGET,
                'reason': "Ordföljden är felaktig; det ska vara 'kastar jag ut dig' eller 'kastar jag dig ut', inte 'kastar jag ut dig' i detta sammanhang."}
        self.assertTrue(is_self_contradiction(flag, self.TARGET))

    def test_a_fix_identical_to_the_flagged_words_is_a_contradiction(self):
        flag = {'id': 28, 'category': 'grammar', 'target_quote': 'kastar jag ut dig genom fönstret',
                'reason': "Ordföljden är felaktig; i svenskan ska objektet placeras före partikeln, eller så bör hela frasen vara 'kastar jag ut dig genom fönstret'."}
        self.assertTrue(is_self_contradiction(flag, self.TARGET))

    def test_a_real_fix_is_kept(self):
        flag = {'id': 1, 'category': 'grammar', 'target_quote': 'kastar jag dig ut',
                'reason': "Det ska vara 'kastar jag ut dig', inte 'kastar jag dig ut'."}
        self.assertFalse(is_self_contradiction(flag, 'Annars kastar jag dig ut genom fönstret.'))



class VerifiedRuleOverReviewTests(unittest.TestCase):
    SOURCE = '死ぬためだけに作られた兵器です'

    def test_a_review_asking_dying_to_become_killing_is_set_aside(self):
        # Godzilla (dev jobs 245 and 296)
        from translation_qa import asks_dying_to_become_killing
        flag = {'id': 11, 'category': 'meaning', 'target_quote': 'för att dö',
                'reason': 'Källtexten beskriver ett vapen skapat för att man ska dö (att döda), men översättningen säger att vapnet ska dö.'}
        self.assertTrue(asks_dying_to_become_killing(flag, self.SOURCE, 'Det är ett vapen som är skapat enbart för att dö.'))
        self.assertFalse(asks_dying_to_become_killing(flag, self.SOURCE, 'Det är ett vapen som är skapat enbart för att döda.'))
        self.assertFalse(asks_dying_to_become_killing(flag, '人を殺すための兵器', 'Det är ett vapen för att dö.'))

    def test_every_proposal_already_in_the_line_is_nothing_to_repair(self):
        target = 'Säg mig vem som anlitade dig, annars kastar jag ut dig genom fönstret.'
        flag = {'id': 28, 'category': 'grammar', 'target_quote': 'kastar jag ut dig genom fönstret',
                'reason': "Ordföljden är felaktig; det ska vara 'kastar jag ut dig' eller 'kastar jag ut dig', men det är tveksamt, "
                          "det korrekta vore 'kastar jag ut dig genom fönstret'."}
        self.assertTrue(is_self_contradiction(flag, target))

    def test_real_proposals_are_kept(self):
        for target, reason in (('attack mot Ukrainas.', "Det svenska målet har lagt till ett genitiv-s; det ska vara 'mot Ukraina'."),
                               ('måste vi gå ut igen', "Det är en påstående mening, så det ska inte vara 'måste vi' utan 'vi måste'."),
                               ('slänger jag ut dig genom fönstret', "Det ska vara 'slänger jag ut dig' eller 'slänger jag dig ut', men det första är tveksamt."),
                               ('90 miles', "Ordet 'miles' har inte översatts; det bör vara 'mil' eller 'engelska mil'.")):
            flag = {'id': 1, 'category': 'grammar', 'target_quote': target, 'reason': reason}
            self.assertFalse(is_self_contradiction(flag, target), target)


class RepairNeverIntroducesVerifiedDefectsTests(unittest.TestCase):
    def test_a_repair_that_writes_doda_for_dying_is_reverted(self):
        row = {'id': 11, 'start': 44.8, 'end': 46.7, 'source': '死ぬためだけに作られた兵器です', 'sv': 'Det är ett vapen som är skapat enbart för att dö.'}
        flag = {'id': 11, 'category': 'meaning', 'target_quote': 'för att dö', 'reason': 'Ska vara att döda.'}
        bad = [{**row, 'sv': 'Det är ett vapen som är skapat enbart för att döda.'}]
        events = []
        with patch.object(worker, 'repair_flagged_translation_split', return_value=bad):
            result = worker.repair_flagged_translation({'id': 296}, [dict(row)], [flag], record=events.append)
        self.assertEqual(result[0]['sv'], row['sv'])
        self.assertEqual(events[-1]['stage'], 'repair_reverted')

    def test_a_good_repair_is_kept(self):
        row = {'id': 11, 'start': 44.8, 'end': 46.7, 'source': '死ぬためだけに作られた兵器です', 'sv': 'Det är en vapen som är skapat enbart för att dö.'}
        good = [{**row, 'sv': 'Det är ett vapen som är skapat enbart för att dö.'}]
        with patch.object(worker, 'repair_flagged_translation_split', return_value=good):
            result = worker.repair_flagged_translation({'id': 296}, [dict(row)], [{'id': 11, 'category': 'grammar', 'target_quote': 'en vapen'}])
        self.assertEqual(result[0]['sv'], good[0]['sv'])


if __name__ == '__main__':
    unittest.main()
