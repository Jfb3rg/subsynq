import sys
import unittest

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from translation_qa import deterministic_flags, imperial_unit_flags

# Yukon Men S01E01 (dev job 121, delivered subtitle, lines 702, 487, 756, 89): the miles, yards and feet of the source stayed or became "mil".
ROWS = [
    {'id': 701, 'source': "We think we're within a couple of miles of them.", 'sv': 'Vi tror att vi är inom ett par mil från dem.'},
    {'id': 486, 'source': "So this is 90 miles we're going to head out of. Oh, yeah.", 'sv': 'Så det här är 90 miles vi ska bege oss ut på. Åh, ja.'},
    {'id': 755, 'source': "So let's go about 40 more yards.", 'sv': 'Så låt oss gå ungefär 40 yards till.'},
    {'id': 88, 'source': "like, 15 feet in front of you, and it's really important", 'sv': 'som, 15 fot framför dig, och det är verkligen viktigt'},
    {'id': 521, 'source': 'just to get 200 gallons up to the house.', 'sv': 'bara för att få upp 200 gallon till huset.'},
]


class UnitTests(unittest.TestCase):
    def test_the_real_lines_are_flagged_with_the_unit_as_the_anchor_and_the_metric_value_in_the_reason(self):
        flags = {flag['id']: flag for flag in imperial_unit_flags(ROWS)}
        self.assertEqual(sorted(flags), [88, 486, 521, 701, 755])
        self.assertEqual(flags[701]['target_quote'], 'ett par mil')
        self.assertEqual(flags[486]['target_quote'], '90 miles')
        self.assertEqual(flags[755]['target_quote'], '40 yards')
        self.assertEqual(flags[88]['target_quote'], '15 fot')
        self.assertEqual(flags[521]['target_quote'], '200 gallon')
        self.assertIn('ungefär 145 kilometer', flags[486]['reason'])
        self.assertIn('ungefär 37 meter', flags[755]['reason'])
        self.assertIn('ungefär 4,6 meter', flags[88]['reason'])
        self.assertIn('en svensk mil är 10 km', flags[701]['reason'])
        self.assertTrue(all(flag['category'] == 'untranslated' for flag in flags.values()))

    def test_correct_metric_text_passes(self):
        good = [{'id': 1, 'source': 'a couple of miles of them.', 'sv': 'ett par kilometer från dem.'},
                {'id': 2, 'source': 'This is 90 miles.', 'sv': 'Det här är 145 kilometer.'},
                {'id': 3, 'source': 'about 40 more yards.', 'sv': 'ungefär 37 meter till.'},
                {'id': 4, 'source': '15 feet in front of you', 'sv': '4,5 meter framför dig'},
                {'id': 5, 'source': '200 gallons', 'sv': 'sjuhundra liter'},
                {'id': 6, 'source': 'He walked 10 miles.', 'sv': 'Han gick 16 kilometer.'}]
        self.assertEqual(imperial_unit_flags(good), [])

    def test_a_wrong_conversion_and_an_unchanged_number_are_flagged(self):
        self.assertEqual([flag['id'] for flag in imperial_unit_flags([{'id': 1, 'source': 'He walked 10 miles.', 'sv': 'Han gick 10 kilometer.'}])], [1])
        self.assertEqual(imperial_unit_flags([{'id': 1, 'source': 'He walked 10 miles.', 'sv': 'Han gick tio kilometer.'}]), [], 'a number in words is not judged')

    def test_cords_of_firewood_are_converted_to_cubic_meters(self):
        # Yukon Men (job 146, row 699): "10 cords of wood" became "cirka 3,5 kubikmeter ved"; a cord is 3.62 cubic meters, so about 36
        source = 'While James works to process 10 cords of wood,'
        wrong = {'id': 699, 'source': source, 'sv': 'Medan James arbetar för att bearbeta cirka 3,5 kubikmeter ved,'}
        flags = imperial_unit_flags([wrong])
        self.assertEqual([flag['id'] for flag in flags], [699])
        self.assertIn('ungefär 36 kubikmeter', flags[0]['reason'])
        for good in ('Medan James arbetar för att bearbeta cirka 36 kubikmeter ved,', 'Medan James arbetar för att bearbeta 10 staplar ved,', 'Medan James arbetar för att bearbeta 10 vedmått,'):
            self.assertEqual(imperial_unit_flags([{**wrong, 'sv': good}]), [], good)
        self.assertEqual([flag['id'] for flag in imperial_unit_flags([{**wrong, 'sv': 'Medan James arbetar för att bearbeta 10 cords ved,'}])], [699])

    def test_feet_that_are_a_dogs_paws_are_not_a_length(self):
        # Yukon Men (job 144): "eight dogs, so that's 32 extra feet" is "32 extra tassar"; the check refused the whole file for it
        self.assertEqual(imperial_unit_flags([{'id': 465, 'source': "We're going to have eight dogs, so that's 32 extra feet",
                                               'sv': 'Vi ska ha åtta hundar, så det är 32 extra tassar'}]), [])
        self.assertEqual([flag['id'] for flag in imperial_unit_flags([{'id': 1, 'source': 'That is 32 extra feet', 'sv': 'Det är 32 extra meter.'}])], [1], 'a metric word with the wrong number')

    def test_a_unit_without_a_number_a_name_and_metric_sources_are_left_alone(self):
        rows = [{'id': 1, 'source': 'We went on foot.', 'sv': 'Vi gick till fots.'},
                {'id': 2, 'source': 'Mile End is a town.', 'sv': 'Mile End är en stad.'},
                {'id': 3, 'source': 'It is 100 kilometers away.', 'sv': 'Det är 100 kilometer bort, tio mil.'},
                {'id': 4, 'source': 'I paid 100 pounds.', 'sv': 'Jag betalade 100 pund.'},
                {'id': 5, 'source': 'Do you have a foot?', 'sv': 'Har du en fot?'}]
        self.assertEqual(imperial_unit_flags(rows), [])

    def test_the_gate_uses_them_with_the_other_deterministic_defects_and_the_translator_is_told(self):
        both = ROWS[:1] + [{'id': 9, 'source': 'Ну привет.', 'sv': 'Ну привет.'}]
        self.assertEqual([flag['id'] for flag in deterministic_flags(both)], [9, 701])
        rules = worker.output_rules('sv')
        self.assertIn('miles -> kilometer', rules)
        self.assertIn('never "mil"', rules)
        self.assertNotIn('kilometer', worker.output_rules('en'))


class ReviewerKnowsTheRuleTests(unittest.TestCase):
    def test_the_swedish_reviewer_is_told_that_a_correct_metric_conversion_is_right(self):
        import json
        from unittest.mock import patch
        seen = []

        def answer(request):
            seen.append(request['system'])
            sample = json.loads(request['prompt'])['rows']
            return {'response': json.dumps({'comparisons': [{'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'], 'meaning_preserved': True}
                                                            for row in sample], 'flags': []})}
        rows = [{'id': n, 'start': n * 2.0, 'end': n * 2.0 + 1.5, 'source': f'Line {n} goes here.', 'sv': f'Rad {n} står här.'} for n in range(6)]
        with patch.object(worker, 'call_ollama', side_effect=answer), patch.object(worker, 'FOCUSED_REVIEW', True), patch.object(worker, 'release_language_model_before_asr'):
            worker.assess_translation_quality(rows, audit=[], whole_file=True)
        self.assertTrue(seen)
        self.assertTrue(all('CORRECT and required' in system and 'flag only a conversion that is wrong' in system for system in seen))


if __name__ == '__main__':
    unittest.main()
