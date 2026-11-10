import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from translation_qa import deterministic_flags, file_consistency_flags, repair_sir_address, sir_address_flag


def row(row_id, source, sv):
    return {'id': row_id, 'start': float(row_id * 3), 'end': float(row_id * 3 + 2), 'source': source, 'sv': sv}


# Real rows of one episode: the same address rendered three ways in one file.
ROWS = [row(1, 'Reset command, sir.', 'Återställningskommando, herre.'),
        row(2, "Well, sir, I'm not sure how much you're aware of,", 'Ja, herre, jag är inte säker på hur mycket du vet,'),
        row(3, 'Sir. Oh, Dwight, no.', 'Herre. Åh, Dwight, nej.'),
        row(4, 'Yes, sir.', 'Ja, befäl.'),
        row(5, 'Yes, sir.', 'Ja, sir.')]


class SirAddressTests(unittest.TestCase):
    """"sir" as an address stays "sir" in Swedish subtitles (the user's choice), never "herre" or "befäl"."""

    def test_herre_and_befal_for_sir_are_flagged_and_sir_is_not(self):
        flags = [flag for flag in file_consistency_flags(ROWS) if flag['reason'].startswith('Tilltalet')]
        self.assertEqual([(flag['id'], flag['target_quote']) for flag in flags], [(1, 'herre'), (2, 'herre'), (3, 'Herre'), (4, 'befäl')])
        self.assertEqual(flags[0]['category'], 'meaning')

    def test_the_repair_changes_only_that_word_and_keeps_a_sentence_capital(self):
        repaired, changes = repair_sir_address(ROWS, file_consistency_flags(ROWS))
        self.assertEqual([item['sv'] for item in repaired],
                         ['Återställningskommando, sir.', 'Ja, sir, jag är inte säker på hur mycket du vet,', 'Sir. Åh, Dwight, nej.', 'Ja, sir.', 'Ja, sir.'])
        self.assertEqual([change['id'] for change in changes], [1, 2, 3, 4])
        self.assertEqual([flag for flag in deterministic_flags(repaired) if flag['reason'].startswith('Tilltalet')], [])

    def test_min_herre_becomes_plain_sir(self):
        rows = [row(9, 'You, sir, have done this before.', 'Du, min herre, har gjort det här förut.')]
        self.assertEqual(repair_sir_address(rows, file_consistency_flags(rows))[0][0]['sv'], 'Du, sir, har gjort det här förut.')

    def test_herre_for_another_source_word_is_left_alone(self):
        for source, sv in (('Yes, my lord.', 'Ja, min herre.'), ('The master is waiting, sir.', 'Herren väntar, sir.'),
                           ('Lord, have mercy.', 'Herre, förbarma dig.'), ('Sir Lancelot rode out.', 'Sir Lancelot red ut.'),
                           ('Report to the commander, sir.', 'Rapportera till befälet, sir.')):
            self.assertIsNone(sir_address_flag(source, sv), (source, sv))

    def test_only_a_requested_flag_is_repaired(self):
        flags = [flag for flag in file_consistency_flags(ROWS) if flag['id'] == 1]
        repaired, changes = repair_sir_address(ROWS, flags)
        self.assertEqual([change['id'] for change in changes], [1])
        self.assertEqual(repaired[1]['sv'], ROWS[1]['sv'])

    def test_the_repair_step_does_not_send_the_sir_flags_to_the_model(self):
        other = {'id': 5, 'category': 'meaning', 'source_quote': 'Yes, sir.', 'target_quote': 'Ja, sir.', 'reason': 'Fel ton.'}
        flags = file_consistency_flags(ROWS) + [other]
        sent, steps = [], []

        def model(job, candidate, given, **kwargs):
            sent.append(sorted(flag['id'] for flag in given))
            return [dict(item) for item in candidate]

        with patch.object(worker, 'repair_flagged_translation_split', side_effect=model):
            result = worker.repair_flagged_translation({'id': 101}, ROWS, flags, record=steps.append)
        self.assertEqual(sent, [[5]])
        self.assertEqual(result[0]['sv'], 'Återställningskommando, sir.')
        self.assertEqual([step['stage'] for step in steps], ['sir_address'])


if __name__ == '__main__':
    unittest.main()
