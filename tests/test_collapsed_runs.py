"""A stretch the long pass wrote without sentence form takes confirmed sentences; nothing unsettled changes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from collapsed_runs import collapsed_runs, formless, replace_collapsed_runs, rows_inside_windows


def row(start, end, text):
    return {'start': start, 'end': end, 'text': text, 'confidence': .8}


RUN = [row(10.0, 14.0, 'the old mill again glad to open that door for you look i know she is a'),
       row(14.0, 18.0, 'good baker but certainly not i am not selling the bread to strangers and giving'),
       row(18.0, 21.0, 'to some rich man fine captain miss')]
SHORT = [row(9.4, 10.2, "She'd like to meet you."), row(10.3, 11.6, 'The old mill again.'),
         row(11.7, 13.2, 'Glad to open that door for you.'),
         row(13.3, 16.0, 'Look, I know she is a good baker, but certainly not.'),
         row(16.1, 19.4, 'I am not selling bread to strangers and giving it to some rich man.'),
         row(19.6, 20.0, 'Understood.'), row(20.1, 20.5, 'Fine.'), row(20.6, 21.0, 'Captain, Ms.')]
OTHER = ('like to meet you. The old mill again. Glad to open that door for you. Look, I know she is a good baker, '
         'but certainly not. I am not selling bread to strangers and giving it to some rich man. Understood. Fine. '
         'Captain, Miss.')


def witnesses(*names):
    return {name: [row(9.0, 21.5, OTHER)] for name in names}


class CollapsedRunTests(unittest.TestCase):
    def test_formless_rows_and_runs(self):
        self.assertTrue(formless(RUN[0]))
        self.assertFalse(formless(row(0, 1, 'Look, I know.')))
        self.assertFalse(formless(row(0, 1, 'Fine')))
        self.assertFalse(formless(row(0, 1, 'これは長い行ですが句読点がありません')))
        self.assertEqual(collapsed_runs([row(1, 2, 'Good morning.')] + RUN), [(1, 3)])
        # A long row without a comma is ordinary text when no row opens in lower case.
        self.assertEqual(collapsed_runs([row(1, 4, 'We are going to open the old mill again tomorrow'),
                                         row(4.1, 6, 'And then we bake the bread for everyone')]), [])

    def test_confirmed_sentences_replace_the_run_and_unsettled_words_stay(self):
        rows, changes = replace_collapsed_runs([row(5, 9, 'Good morning.')] + RUN, [('precision', SHORT)],
                                               witnesses('v2', 'qwen', 'parakeet'))
        self.assertEqual([item['text'] for item in rows], [
            'Good morning.', 'The old mill again.', 'Glad to open that door for you.',
            'Look, I know she is a good baker, but certainly not.',
            'I am not selling bread to strangers and giving it to some rich man.', 'Understood.', 'Fine.',
            'captain miss'])
        # "She'd" is heard by the short pass alone and "Ms." is written "Miss" by the others: neither row is taken.
        self.assertEqual(rows[-1]['start'], 20.5)
        self.assertEqual(rows[-1]['end'], 21.0)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['source'], 'precision')
        self.assertTrue(all(left['end'] <= right['start'] + .05 for left, right in zip(rows, rows[1:])))

    def test_a_new_word_needs_two_other_families_and_one_outside_whisper(self):
        for names in ((), ('qwen',), ('v2',), ('v2', 'cluster_v2')):
            rows, _ = replace_collapsed_runs(RUN, [('precision', SHORT)], witnesses(*names))
            texts = [item['text'] for item in rows]
            self.assertNotIn('Understood.', texts, names)
            self.assertNotIn('I am not selling bread to strangers and giving it to some rich man.', texts, names)
        rows, _ = replace_collapsed_runs(RUN, [('precision', SHORT)], witnesses('v2', 'qwen'))
        self.assertIn('Understood.', [item['text'] for item in rows])

    def test_the_same_words_need_no_other_family(self):
        run = [row(1.0, 4.0, 'we found the lost boat where'), row(4.0, 7.0, 'were you all night i was at home')]
        short = [row(1.0, 2.6, 'We found the lost boat.'), row(2.8, 4.6, 'Where were you all night?'),
                 row(4.8, 6.8, 'I was at home.')]
        rows, changes = replace_collapsed_runs(run, [('precision', short)], {})
        self.assertEqual([item['text'] for item in rows], [item['text'] for item in short])
        self.assertEqual(changes[0]['old'], [item['text'] for item in run])

    def test_a_run_word_another_family_also_heard_is_not_dropped(self):
        run = [row(1.0, 4.0, 'okay and i will not ask thank you so'), row(4.0, 6.0, 'much for the warm bread')]
        short = [row(1.0, 2.8, 'Okay, then I will not ask.'), row(3.0, 5.8, 'Thank you so much for the warm bread.')]
        heard = {'v2': [row(1, 6, 'Okay, then I will not ask. Thank you so much for the warm bread.')],
                 'parakeet': [row(1, 6, 'Okay, then I will not ask. Thank you so much for the warm bread.')],
                 'qwen': [row(1, 6, 'Okay, and I will not ask. Thank you so much for the warm bread.')]}
        rows, _ = replace_collapsed_runs(run, [('precision', short)], heard)
        self.assertEqual([item['text'] for item in rows],
                         ['okay and i will not ask', 'Thank you so much for the warm bread.'])
        del heard['qwen']
        heard['qwen'] = heard['v2']
        rows, _ = replace_collapsed_runs(run, [('precision', short)], heard)
        self.assertEqual([item['text'] for item in rows], [item['text'] for item in short])

    def test_a_kept_word_is_not_doubled_by_another_hearing_of_it(self):
        run = [row(1.0, 4.0, 'you do not know if you have seen it but i am'), row(4.0, 6.0, 'rather well known around here')]
        short = [row(1.0, 5.8, 'I do not know if you have seen it, but I am rather well known around here.')]
        heard = {name: [row(.5, 6, 'I do not know if you have seen it, but I am rather well known around here.')]
                 for name in ('v2', 'qwen', 'parakeet')}
        # The run's first word is not replaced by a new first word of the sentence: "You" in front of "I do not know"
        # would double one hearing, and dropping it is not settled by this rule. The run stays as it was.
        rows, changes = replace_collapsed_runs(run, [('precision', short)], heard)
        self.assertEqual(rows, run)
        self.assertEqual(changes, [])
        # Where the others read the kept word and the new word after each other, both belong there.
        run = [row(1.0, 4.0, 'short boat ride love'), row(4.0, 6.0, 'boat rides you are not leaving are you')]
        short = [row(2.4, 3.9, 'I love boat rides.'), row(4.0, 5.9, 'You are not leaving, are you?')]
        heard = {name: [row(.5, 6, 'Short boat ride? I love boat rides. You are not leaving, are you?')]
                 for name in ('v2', 'qwen')}
        rows, _ = replace_collapsed_runs(run, [('precision', short)], heard)
        self.assertEqual([item['text'] for item in rows],
                         ['short boat ride', 'I love boat rides.', 'You are not leaving, are you?'])
        self.assertEqual((rows[0]['start'], rows[0]['end']), (1.0, 2.4))

    def test_no_gain_no_overlap_and_no_formless_source(self):
        run = [row(1.0, 4.0, 'we found the lost boat where'), row(4.0, 7.0, 'were you all night i was at home')]
        short = [row(1.0, 2.6, 'We found the lost boat.'), row(2.8, 4.6, 'Where were you all night?'),
                 row(4.8, 6.8, 'I was at home.')]
        # The same rows in another form are no gain.
        same = [row(1.0, 4.0, 'we found the lost boat where,'), row(4.0, 7.0, 'were you all night i was at home')]
        self.assertEqual(replace_collapsed_runs(run, [('precision', same)], {})[0], run)
        # A source that is formless itself supplies nothing.
        self.assertEqual(replace_collapsed_runs(run, [('precision', [dict(item, text=item['text'].lower().strip('.?'))
                                                                     for item in short])], {})[0], run)
        # A sentence that would overlap an untouched neighbour is not taken.
        neighbour = row(6.2, 9.0, 'Nobody believed me.')
        run = [run[0], dict(run[1], end=6.0)]
        rows, _ = replace_collapsed_runs(run + [neighbour], [('precision', short)], {})
        self.assertEqual([item['text'] for item in rows],
                         ['We found the lost boat.', 'Where were you all night?', 'i was at home', 'Nobody believed me.'])
        self.assertLessEqual(rows[2]['end'], neighbour['start'] + .05)

    def test_rows_at_a_window_edge_are_not_sources(self):
        windows = [{'start': 10.0, 'end': 20.0}]
        rows = [row(10.1, 12.0, 'cut at the start.'), row(12.5, 15.0, 'A whole sentence.'), row(18.0, 19.9, 'cut at the end.')]
        self.assertEqual(rows_inside_windows(rows, windows), [rows[1]])
        self.assertEqual(rows_inside_windows([row(10.6, 12.0, 'x')], windows, shift=1), [])


if __name__ == '__main__':
    unittest.main()
