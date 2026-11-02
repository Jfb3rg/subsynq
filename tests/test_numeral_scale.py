import copy
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/app')
from numeral_scale import numeral_scale_flags, repair_standalone_scales
from translation_qa import deterministic_flags


def row(source, target):
    return {'id': 9, 'start': 28.71, 'end': 30., 'source': source, 'sv': target}


class NumeralScaleTests(unittest.TestCase):
    def test_standalone_digit_repair_preserves_units_punctuation_and_all_other_fields(self):
        for source, target, expected in [('500 கோடி.', '500 miljarder.', '5 miljarder.'),
                ('2.5 crore!', '250 miljoner!', '25 miljoner!'),
                ('2 lakh.', '20 miljoner.', '0,2 miljoner.')]:
            original = [row(source, target), dict(row('Hello.', 'Hej.'), id=10)]
            before = copy.deepcopy(original)
            result, changes = repair_standalone_scales(original, numeral_scale_flags(original))
            self.assertEqual(result[0]['sv'], expected)
            self.assertEqual({**result[0], 'sv': target}, original[0])
            self.assertEqual(result[1], original[1])
            self.assertEqual(original, before)
            self.assertEqual(len(changes), 1)
            self.assertEqual(numeral_scale_flags(result), [])

    def test_automatic_scale_repair_never_guesses_sentences_currency_or_inflection(self):
        for source, target in [('500 crore rupees.', '500 miljarder rupier.'),
                ('Not 500 crore.', 'Inte 500 miljarder.'), ('500 crore.', 'Inte 500 miljarder.'),
                ('500 crore.', 'De 500 miljarderna.'), ('100 crore.', '100 miljarder.'),
                ('500 crore.', 'Fem miljarder.'), ('1,000 crore.', '10 miljarder.'),
                ('500 crore and 20 people.', '500 miljarder och 20 personer.')]:
            original = [row(source, target)]
            self.assertEqual(repair_standalone_scales(original, numeral_scale_flags(original)), (original, []))
        original = [row('500 crore.', '500 miljarder.')]
        self.assertEqual(repair_standalone_scales(original, []), (original, []))
        stale = [{**f, 'target_quote': '100 miljarder'} for f in numeral_scale_flags(original)]
        self.assertEqual(repair_standalone_scales(original, stale), (original, []))

    def test_model_cannot_reintroduce_wrong_scale_during_another_repair(self):
        import worker
        original = [row('500 கோடி.', '500 miljarder.')]
        other = {'id': 9, 'category': 'grammar', 'source_quote': '500 கோடி',
                 'target_quote': '500 miljarder', 'reason': 'test finding'}
        records = []
        with patch.object(worker, 'repair_flagged_translation_split', return_value=original) as model:
            result = worker.repair_flagged_translation({'id': 1}, original,
                numeral_scale_flags(original) + [other], record=records.append)
        self.assertEqual(result[0]['sv'], '5 miljarder.')
        self.assertEqual(model.call_args.args[1][0]['sv'], '5 miljarder.')
        self.assertEqual(model.call_args.args[2], [other])
        self.assertEqual([r['stage'] for r in records], ['exact_scale', 'repair_reverted'])

    def test_clean_model_verdict_cannot_waive_a_wrong_scale_even_on_fragment(self):
        import worker
        item = row('500 கோடி,', '500 miljarder.')
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'assess_translation_quality'), patch.object(worker, 'check_cancelled'):
            with self.assertRaises(worker.SourceQualityError):
                worker.review_and_save_translation({'id': 1}, [item])

    def test_actual_sigma_error_is_hard_meaning_evidence_with_exact_quotes(self):
        item = row('500 கோடி.', '500 miljarder.')
        before = copy.deepcopy(item)
        flags = deterministic_flags([item])
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]['category'], 'meaning')
        self.assertEqual(flags[0]['source_quote'], '500 கோடி')
        self.assertEqual(flags[0]['target_quote'], '500 miljarder')
        self.assertIn('5000000000', flags[0]['reason'])
        self.assertEqual(item, before)

    def test_correct_equivalent_scales_and_plain_counts_are_accepted(self):
        for target in ('5 miljarder.', 'Fem miljarder.', 'De 5 miljarderna.', '5000 miljoner.', '500 crore.', '5000000000.'):
            self.assertEqual(numeral_scale_flags([row('500 crores.', target)]), [])
        self.assertEqual(numeral_scale_flags([row('2 lakh.', '200 tusen.')]), [])

    def test_decimal_and_non_ascii_digits_keep_exact_value(self):
        for source in ('2.5 crore.', '2,5 crore.', '२.५ करोड़.'):
            self.assertEqual(numeral_scale_flags([row(source, '25 miljoner.')]), [])
            self.assertEqual(len(numeral_scale_flags([row(source, '250 miljoner.')])), 1)

    def test_missing_scale_and_wrong_spelled_number_are_detected(self):
        for target in ('500.', 'Femhundra miljarder.', 'Fem miljoner.'):
            if target.startswith('Femhundra'):
                self.assertEqual(numeral_scale_flags([row('500 crore.', target)]), [])
            else:
                self.assertEqual(len(numeral_scale_flags([row('500 crore.', target)])), 1)

    def test_no_guess_from_multiple_ambiguous_grouped_or_absent_amounts(self):
        for source, target in [('1,000 crore.', '1 miljard.'), ('1.000 crore.', '1 miljard.'),
                               ('1,00,000 crore.', '1 miljard.'), ('500 crore and 20 people.', '5 miljarder.'),
                               ('500 crore or 2 lakh.', '5 miljarder eller 200 tusen.'),
                               ('One crore.', '100 miljarder.'), ('He lives in Crore.', 'Han bor där.'),
                               ('500 croresworth.', '500 miljarder.'), ('500 crores.', 'Femhundra miljarder.')]:
            self.assertEqual(numeral_scale_flags([row(source, target)]), [])
        self.assertEqual(numeral_scale_flags([row('500 crore.', '5 miljarder och 20 personer.')]), [])
        self.assertEqual(numeral_scale_flags([row('500 crore.', 'En 5 miljardersaffär.')]), [])

    def test_other_languages_and_unrelated_numbers_are_untouched(self):
        self.assertEqual(numeral_scale_flags([{'id': 1, 'source': '500 crore.', 'en': '500 billion.'}], 'en'), [])
        self.assertEqual(numeral_scale_flags([row('500 people.', '500 personer.')]), [])
        self.assertEqual(numeral_scale_flags([row('500 crore.', 'En vän har fem miljarder.')]), [])


if __name__ == '__main__':
    unittest.main()
