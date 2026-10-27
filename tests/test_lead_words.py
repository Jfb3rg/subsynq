import sys
import unittest
from pathlib import Path

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from asr_windows import restore_lead_words
from source_judge import acceptable_choice

# MAN OF WAR (dev jobs 49-170): the short pass lost "This"; the long pass and the rescue pass have it right before "is what's left".
PREVIOUS = {'start': 78.82, 'end': 83.46, 'text': 'Anyone seen not in Ukrainian uniforms will be shot on sight.'}
ROW = {'start': 88.47, 'end': 93.93, 'text': "is what's left simple people we're trying to live their lives", 'confidence': .98}
READINGS = {'context': [{'start': 87.93, 'end': 89.35, 'text': "This is what's left."}, {'start': 90.47, 'end': 91.69, 'text': 'Simple people.'}],
            'precision': [ROW],
            'rescue': [{'start': 87.97, 'end': 91.1, 'text': "this is what's left simple people"}]}


class LeadWordTests(unittest.TestCase):
    def test_the_lost_opening_word_is_put_back_with_its_time(self):
        rows, changes = restore_lead_words([PREVIOUS, ROW], READINGS)
        self.assertEqual(rows[1]['text'], "This is what's left simple people we're trying to live their lives")
        self.assertEqual(rows[1]['start'], 87.93)
        self.assertEqual((changes[0]['lead'], changes[0]['readings']), ('This', ['context', 'rescue']))
        self.assertEqual(ROW['text'], "is what's left simple people we're trying to live their lives", 'the input is not changed')

    def test_a_row_with_a_capital_needs_two_families_one_of_them_independent(self):
        # Yukon Men (dev job 163): "You" heard by the short pass, large-v2 and Qwen
        row = {'start': 525.0, 'end': 527.0, 'text': "Better watch what you're doing in there."}
        readings = {'precision': [{'start': 524.47, 'end': 527.0, 'text': "You better watch what you're doing in there."}],
                    'v2': [{'start': 524.5, 'end': 527.1, 'text': "You better watch what you're doing in there."}]}
        rows, _ = restore_lead_words([row], readings)
        self.assertEqual(rows[0]['text'], "You better watch what you're doing in there.")
        only_v3 = {'precision': readings['precision'], 'context': readings['precision']}
        self.assertEqual(restore_lead_words([row], only_v3)[1], [], 'two passes of large-v3 alone do not add to a row that starts as a sentence')

    def test_one_reading_a_one_word_row_and_different_leads_change_nothing(self):
        self.assertEqual(restore_lead_words([PREVIOUS, ROW], {'context': READINGS['context'], 'precision': [ROW]})[1], [])
        # Ray Gunn (dev job 30): "out!" would have become "Keep her out!" from "keep her out of trouble"
        out = {'start': 72.84, 'end': 73.3, 'text': 'out!'}
        heard = {name: [{'start': 72.0, 'end': 75.0, 'text': "keep her out of trouble and you'll"}] for name in ('context', 'precision', 'rescue')}
        self.assertEqual(restore_lead_words([out], heard)[1], [])
        conflicting = {'context': [{'start': 87.93, 'end': 89.35, 'text': "This is what's left."}], 'rescue': [{'start': 87.97, 'end': 91.1, 'text': "that is what's left"}]}
        self.assertEqual(restore_lead_words([PREVIOUS, ROW], conflicting)[1], [])

    def test_words_of_the_previous_sentence_are_never_taken(self):
        # German Tank (dev job 171): "Ich will auch in den Film." then "Film?" -- and with two words "in den Film?" must not appear
        previous = {'start': 46.9, 'end': 47.9, 'text': 'Ich will auch in den Film.'}
        row = {'start': 48.14, 'end': 49.5, 'text': 'Film? Das ist eine fette Serie.'}
        heard = {name: [{'start': 46.3, 'end': 50.4, 'text': 'Ich will auch in den Film. Film? Das ist eine fette Serie.'}] for name in ('context', 'qwen', 'v2')}
        self.assertEqual(restore_lead_words([previous, row], heard)[1], [])
        # Nelly Rapp (dev job 59): "vantarna på" is heard while the previous row is still running
        previous = {'start': 40.13, 'end': 41.53, 'text': 'Objektet vi ska lägga vanten.'}
        row = {'start': 41.73, 'end': 43.77, 'text': 'är Plutons tron.'}
        words = [{'start': 40.06 + .3 * n, 'end': 40.3 + .3 * n, 'text': word, 'probability': .9}
                 for n, word in enumerate('Objektet vi ska lägga vantarna på är Plutons tron.'.split())]
        heard = {name: [{'start': 40.06, 'end': 43.86, 'text': 'Objektet vi ska lägga vantarna på är Plutons tron.', 'words': words}] for name in ('rescue', 'v2')}
        self.assertEqual(restore_lead_words([previous, row], heard)[1], [])

    def test_scripts_without_spaces_are_left_alone(self):
        row = {'start': 69.0, 'end': 71.0, 'text': '悪魔と取引してたんや 本当に'}
        heard = {name: [{'start': 67.0, 'end': 71.0, 'text': '神様と話してたんとちゃうで 悪魔と取引してたんや 本当に'}] for name in ('qwen', 'rescue')}
        self.assertEqual(restore_lead_words([row], heard)[1], [])

    def test_wiring(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('merged, consensus_prefixes = restore_agreed_prefixes('), source.index('merged, lead_words = restore_lead_words('))
        self.assertLess(source.index('merged, lead_words = restore_lead_words('), source.index('merged = refine_cue_starts(job, media, merged, total)'))
        self.assertIn("lead_words_restored=job.get('lead_words_restored')", source)


class RemovalChoiceTests(unittest.TestCase):
    def test_dropping_a_word_needs_more_families_than_the_current_wording(self):
        # MAN OF WAR job 170: both large-v3 passes and large-v2 have "You"; only Qwen lacks it; supports 3 against 3
        self.assertFalse(acceptable_choice('You find me a tour guide yet?', 'Find me a tour guide yet?', 3, 3))
        self.assertFalse(acceptable_choice('Je ne supporte plus le regard des gens.', 'Je supporte plus le regard des gens.', 2, 2))
        self.assertTrue(acceptable_choice('You find me a tour guide yet?', 'Find me a tour guide yet?', 4, 3))

    def test_a_minimal_repair_that_changes_a_word_is_still_allowed(self):
        self.assertTrue(acceptable_choice('His name is Stanislav Konev.', 'His name is Stanislav Kanyev.', 2, 2))


class WordFamilyTests(unittest.TestCase):
    def test_a_word_only_one_reading_heard_may_be_dropped_one_that_several_heard_may_not(self):
        # MAN OF WAR job 180: "this" only in the long pass; job 170: "You" in both large-v3 passes and large-v2
        current = 'The way you make it your life all right lights on me this'
        chosen = 'The way you make it your life. All right, lights on me.'
        self.assertTrue(acceptable_choice(current, chosen, 3, 3, word_families={'this': 1, 'way': 3}))
        self.assertFalse(acceptable_choice(current, chosen, 3, 3))
        self.assertFalse(acceptable_choice('You find me a tour guide yet?', 'Find me a tour guide yet?', 3, 3, word_families={'you': 3}))

    def test_the_readings_count_each_pass_once(self):
        from asr_windows import reading_alternatives
        row = {'start': 100.36, 'end': 104.81, 'text': 'The way you make it your life all right lights on me this', 'confidence': .46}
        readings = {'context': [row], 'precision': [{'start': 99.83, 'end': 104.0, 'text': 'And why you make it your life? All right lights on me'}],
                    'v2': [{'start': 100.22, 'end': 103.9, 'text': 'The way you make it your life. Alright, lights on me.'}],
                    'qwen': [{'start': 99.53, 'end': 105.11, 'text': 'The way you make it your life. All right, lights on me.'}]}
        families = {}
        alternatives = reading_alternatives([row], readings, word_families=families)
        self.assertIn(0, alternatives)
        self.assertEqual(families[0]['this'], 1)
        self.assertGreaterEqual(families[0]['way'], 3)


class QwenWordTests(unittest.TestCase):
    def test_a_v2_word_qwen_contradicts_is_taken_back(self):
        from asr_windows import settle_v2_words_with_qwen
        # ANOTHER GERMAN TANK STORY (dev jobs 171, 178): three large-v3 passes and Qwen "werden", large-v2 alone "wären"
        row = {'start': 72.42, 'end': 74.34, 'text': 'Wir wären jetzt alle reich und berühmt.', 'v2_word': {'index': 1, 'old': 'werden', 'new': 'wären'}}
        qwen = [{'start': 72.12, 'end': 74.64, 'text': 'Wir werden jetzt alle reich und berühmt.'}]
        rows, changes = settle_v2_words_with_qwen([row], qwen)
        self.assertEqual(rows[0]['text'], 'Wir werden jetzt alle reich und berühmt.')
        self.assertNotIn('v2_word', rows[0])
        # "Klacksum" (large-v3) against "Klagsohn" (large-v2): Qwen's "Klacks" is closer to the old word
        row = {'start': 97.77, 'end': 98.8, 'text': 'War doch ein Klagsohn.', 'v2_word': {'index': 3, 'old': 'Klacksum.', 'new': 'Klagsohn.'}}
        rows, _ = settle_v2_words_with_qwen([row], [{'start': 97.06, 'end': 99.1, 'text': 'war doch ein Klacks.'}])
        self.assertEqual(rows[0]['text'], 'War doch ein Klacksum.')

    def test_a_v2_word_qwen_does_not_contradict_stays(self):
        from asr_windows import settle_v2_words_with_qwen
        row = {'start': 127.7, 'end': 129.2, 'text': 'I like you, Gunn.', 'v2_word': {'index': 3, 'old': 'Gun.', 'new': 'Gunn.'}}
        rows, changes = settle_v2_words_with_qwen([row], [{'start': 127.5, 'end': 129.4, 'text': 'I like you, Gunn.'}])
        self.assertEqual((rows[0]['text'], changes), ('I like you, Gunn.', []))
        self.assertEqual(settle_v2_words_with_qwen([dict(row)], [])[0][0]['text'], 'I like you, Gunn.', 'no Qwen reading: nothing changes')

    def test_a_word_large_v2_and_qwen_both_hear_replaces_large_v3s(self):
        from asr_windows import settle_v2_words_with_qwen
        # APOCALYPSE Z (dev jobs 166, 179)
        row = {'start': 21.16, 'end': 22.58, 'text': 'Estamos en la plaza de Paros.'}
        v2 = [{'start': 21.36, 'end': 22.7, 'text': 'Estamos en la plaza de toros.'}]
        qwen = [{'start': 20.86, 'end': 22.88, 'text': 'estamos en la Plaza de Toros.'}]
        rows, changes = settle_v2_words_with_qwen([row], qwen, v2)
        self.assertEqual(rows[0]['text'], 'Estamos en la plaza de Toros.')
        self.assertEqual(settle_v2_words_with_qwen([row], qwen, [{'start': 21.36, 'end': 22.7, 'text': 'Estamos en la plaza de Palos.'}])[1], [], 'they disagree')
        self.assertEqual(settle_v2_words_with_qwen([row], [], v2)[1], [], 'large-v2 alone is not enough')

    def test_wiring(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index("consensus_readings['qwen'] = qwen_reading"), source.index('merged, qwen_word_changes = settle_v2_words_with_qwen(merged, qwen_reading, v2_profile, nb_profile)'))
        self.assertLess(source.index('merged, qwen_word_changes = settle_v2_words_with_qwen('), source.index('merged = prefer_precision_for_unpunctuated_runs('))


class LeadContinuationTests(unittest.TestCase):
    def test_a_lead_that_ends_a_reading_and_continues_the_previous_row(self):
        # APOCALYPSE Z (dev jobs 166, 179): "putting European" fell between two rows
        previous = {'start': 41.77, 'end': 45.24, 'text': 'the past 24 hours a new variant of the tsj virus has emerged'}
        row = {'start': 46.0, 'end': 48.16, 'text': 'authorities in a very difficult situation.'}
        readings = {'precision': [{'start': 44.0, 'end': 48.14, 'text': 'ESJ virus has emerged, putting European authorities in a very difficult situation.'}],
                    'rescue': [{'start': 45.16, 'end': 46.58, 'text': 'putting european authorities'}]}
        rows, _ = restore_lead_words([previous, row], readings)
        self.assertEqual(rows[1]['text'], 'putting European authorities in a very difficult situation.')
        self.assertEqual(rows[1]['start'], 45.29)


class EdgeOverlapTests(unittest.TestCase):
    """APOCALYPSE Z (dev job 185): the long pass split "Move away and keep" / "walking."; both large-v3 short passes, large-v2 and Qwen have
    "Move away and keep walking, sir." in one row, which ends 0.31 s into the next row "Do you hear me?"."""
    LONG = [{'start': 65.02, 'end': 67.23, 'text': 'Please, no matter what you hear, stay put.'}, {'start': 67.23, 'end': 67.94, 'text': 'Move away and keep'},
            {'start': 67.94, 'end': 68.32, 'text': 'walking.'}, {'start': 68.32, 'end': 69.2, 'text': 'Do you hear me?'}]
    SHORT = [{'start': 65.02, 'end': 67.23, 'text': 'Please, no matter what you hear, stay put.', 'confidence': .91},
             {'start': 67.23, 'end': 68.63, 'text': 'Move away and keep walking, sir.', 'confidence': .97},
             {'start': 68.63, 'end': 69.11, 'text': 'Do you hear me?', 'confidence': 1.0}]
    V2 = [{'start': 67.33, 'end': 68.73, 'text': 'Move away and keep walking, sir.', 'confidence': .6}]

    def test_a_small_overlap_is_cut_at_the_neighbour(self):
        from asr_windows import prefer_precision_for_unpunctuated_runs
        rows = prefer_precision_for_unpunctuated_runs(self.LONG, self.SHORT, self.V2)
        self.assertEqual([(row['start'], row['end'], row['text']) for row in rows][1:3],
                         [(67.23, 68.32, 'Move away and keep walking, sir.'), (68.32, 69.2, 'Do you hear me?')])

    def test_a_large_overlap_still_refuses(self):
        from asr_windows import prefer_precision_for_unpunctuated_runs
        late = [dict(row) for row in self.SHORT]
        late[1]['end'] = 69.0
        self.assertEqual([row['text'] for row in prefer_precision_for_unpunctuated_runs(self.LONG, late, self.V2)], [row['text'] for row in self.LONG])


class NeighbourSwallowTests(unittest.TestCase):
    def test_a_confident_reading_that_holds_the_next_cue_is_not_taken(self):
        from asr_windows import prefer_confident_reading
        # Glemselens øy (dev job 208)
        rows = [{'start': 42.55, 'end': 43.31, 'text': 'Reisa!', 'confidence': .3},
                {'start': 43.43, 'end': 45.43, 'text': 'Du kommer ikke til å tro det!', 'confidence': .9}]
        readings = {'precision': [{'start': 42.56, 'end': 45.44, 'text': 'Ryza, du kommer ikke til å tro det!', 'confidence': .9}]}
        self.assertEqual([row['text'] for row in prefer_confident_reading(rows, readings)], ['Reisa!', 'Du kommer ikke til å tro det!'])
        # Nelly Rapp (dev job 56): "Vad är det?" became a second "Är det Jönsson?"
        rows = [{'start': 1.22, 'end': 2.09, 'text': 'Vad är det?', 'confidence': .32}, {'start': 2.09, 'end': 3.26, 'text': 'Är det Jönsson?', 'confidence': .75}]
        readings = {'v2': [{'start': 1.0, 'end': 3.46, 'text': 'Är det Jönsson?', 'confidence': .67}]}
        self.assertEqual([row['text'] for row in prefer_confident_reading(rows, readings)], ['Vad är det?', 'Är det Jönsson?'])

    def test_a_confident_reading_of_the_cue_alone_is_still_taken(self):
        from asr_windows import prefer_confident_reading
        rows = [{'start': 10.0, 'end': 12.0, 'text': 'garbled words here', 'confidence': .2}, {'start': 12.2, 'end': 13.0, 'text': 'Next one.', 'confidence': .9}]
        readings = {'rescue': [{'start': 10.0, 'end': 12.1, 'text': 'Clear words here.', 'confidence': .9}]}
        self.assertEqual(prefer_confident_reading(rows, readings)[0]['text'], 'Clear words here.')


class AddedNeighbourWordsTests(unittest.TestCase):
    def test_a_choice_that_adds_words_the_next_line_holds_is_refused(self):
        from source_judge import repeats_neighbour, acceptable_choice
        current = 'Det er faen meg ikke rart når de to mest populære bøkene'
        following = 'i tre siste runde av årene er Bibelen'
        # Agnes mot verden (dev job 213): NB-Whisper's shortened reading added "er Bibelen og"
        self.assertTrue(repeats_neighbour('Det er ikke rart når de to mest populære bøkene er Bibelen og', current, '', following))
        self.assertFalse(acceptable_choice(current, 'Det er ikke rart når de to mest populære bøkene er Bibelen og', 3, 2, '', following))
        # job 209: "de tre siste" is the next line's "i tre siste"
        self.assertTrue(repeats_neighbour(current + ' de tre siste', current, '', following))

    def test_added_words_the_neighbours_do_not_hold_are_fine(self):
        from source_judge import repeats_neighbour
        self.assertFalse(repeats_neighbour('You find me a tour guide yet?', 'Find me a tour guide yet?', "She's all I got left.", "What's up, man?"))


if __name__ == '__main__':
    unittest.main()
