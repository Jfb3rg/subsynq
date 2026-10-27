import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import source_judge
import worker
from source_judge import complete_cut_off_rows
from asr_windows import plan_doubtful_row_windows

# Yukon Men S01E01 (dev job 146): the six lines whose audio was read again with Whisper large-v2 and Qwen3-ASR (2026-10-23) and the readers' answers.
# Qwen heard "Snowgo could freeze up" and "snowgos"; large-v2 heard "Let's get the f*** out of here!". Two of the six stood ABOVE the old 0.6 limit.
KNOWN = [
    {'start': 97.224, 'end': 98.4, 'text': "Let's get the out of", 'confidence': 0.667},
    {'start': 815.24, 'end': 818.87, 'text': "I don't know about snow ghosts, but I bet the dogs could do it.", 'confidence': 0.255},
    {'start': 831.07, 'end': 834.89, 'text': "If I make it across, then we'll bring the snow goal.", 'confidence': 0.557},
    {'start': 862.696, 'end': 864.04, 'text': 'You the dogs!', 'confidence': 0.567},
    {'start': 1620.25, 'end': 1621.44, 'text': 'Snow Goat could freeze up.', 'confidence': 0.618},
    {'start': 2140.2, 'end': 2141.52, 'text': 'We better go get the snow goats, Bob.', 'confidence': 0.334},
]


class WindowTests(unittest.TestCase):
    def test_every_doubtful_row_gets_a_window_of_its_own_least_certain_first(self):
        windows = plan_doubtful_row_windows(KNOWN)
        self.assertEqual(len(windows), 6, 'all six, including the two above the old limit of 0.6')
        self.assertEqual([window['start'] for window in windows], [814.94, 2139.9, 830.77, 862.396, 1619.95, 96.924])
        self.assertTrue(all(window['end'] - window['start'] >= 1.2 for window in windows))

    def test_certain_rows_rows_without_a_recorded_confidence_and_empty_rows_get_none(self):
        rows = [{'start': 1.0, 'end': 3.0, 'text': 'Fine.', 'confidence': .95}, {'start': 4.0, 'end': 6.0, 'text': 'No value.'},
                {'start': 7.0, 'end': 8.0, 'text': '', 'confidence': .2}, {'start': 9.0, 'end': 10.0, 'text': 'Zero.', 'confidence': 0}]
        self.assertEqual(plan_doubtful_row_windows(rows), [])

    def test_a_very_long_row_is_skipped_and_the_total_is_capped(self):
        rows = [{'start': float(n * 5), 'end': n * 5 + 2.0, 'text': f'Row {n}.', 'confidence': .1 + n / 1000} for n in range(10)]
        rows.append({'start': 100.0, 'end': 120.0, 'text': 'Long.', 'confidence': .01})
        windows = plan_doubtful_row_windows(rows, maximum_total=4)
        self.assertEqual([window['start'] for window in windows], [0.0, 4.7, 9.7, 14.7])
        self.assertEqual(len(plan_doubtful_row_windows(rows)), 10)

    def test_the_limit_is_seven_tenths(self):
        rows = [{'start': 1.0, 'end': 3.0, 'text': 'A.', 'confidence': .69}, {'start': 5.0, 'end': 7.0, 'text': 'B.', 'confidence': .71}]
        self.assertEqual(len(plan_doubtful_row_windows(rows)), 1)


class LimitTests(unittest.TestCase):
    def test_every_doubtful_row_on_a_gpu_and_the_sixty_least_certain_on_a_cpu(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SUBSYNQ_SECOND_OPINION_ROWS', None)
            self.assertEqual((worker.second_opinion_rows('cpu'), worker.second_opinion_rows('cuda'), worker.second_opinion_rows('cloud-cuda')), (60, 400, 400))
            os.environ['SUBSYNQ_SECOND_OPINION_ROWS'] = '25'
            self.assertEqual(worker.second_opinion_rows('cuda'), 25)

    def test_the_judge_decides_every_row_that_has_alternatives_not_the_first_24(self):
        rows = [{'id': n, 'source': f'Line number {n} here.', 'alternatives': [{'text': f'Line number {n} there.', 'support': 2, 'current_support': 1}]} for n in range(60)]
        self.assertEqual(len(source_judge.judge_items(rows)), 24, 'the old default')
        self.assertEqual(len(source_judge.judge_items(rows, maximum=400)), 60)
        asked = []

        def call(request):
            import json
            asked.append(len(json.loads(request['prompt'])['rows']))
            return {'response': json.dumps({'rows': [{'id': row['id'], 'a_has_error': False, 'choice': 'A'} for row in json.loads(request['prompt'])['rows']]})}
        _, decisions = source_judge.choose_readings(rows, call, 'm', maximum=400)
        self.assertEqual(len(decisions), 60)
        self.assertEqual(sum(asked), 60)
        _, decisions = source_judge.choose_readings(rows, call, 'm')
        self.assertEqual(len(decisions), 24, 'unchanged without the new argument')


def completion_rows(texts, alternatives):
    rows = [{'id': n, 'source': text} for n, text in enumerate(texts)]
    for index, options in alternatives.items():
        rows[index]['alternatives'] = [{'text': text, 'support': support, 'current_support': 1} for text, support in options]
    return rows


class CompletionTests(unittest.TestCase):
    """Yukon Men (job 146, readers: large-v3, large-v2, Qwen3-ASR): a row cut off in the middle of a sentence is completed with words another reader heard."""

    def timed_agnes(self, next_start=27.59):
        rows = completion_rows(['Kan jeg levere en gang i måneden,', 'så kjenner jeg meg som Netflix-modell for noe.'],
            {0: [('Kan jeg levere en gang i måneden, og så kjøper en sånn', 2)]})
        rows[0].update(start=26.02, end=27.59)
        rows[1].update(start=next_start, end=next_start + 2)
        rows[0]['alternatives'][0]['completion_span'] = {'start': 26.06, 'end': 28.48}
        return rows

    def test_actual_agnes_completion_must_not_take_the_next_cues_time(self):
        rows = self.timed_agnes()
        self.assertEqual(complete_cut_off_rows(rows), (rows, []))

    def test_the_text_chooser_cannot_reinstate_a_refused_completion(self):
        import json
        for next_start, permitted in ((27.59, False), (29., True)):
            rows = self.timed_agnes(next_start)
            rows[0]['alternatives'][0]['support'] = 3
            call = lambda _: {'response': json.dumps({'rows': [{'id': 0, 'a_has_error': True, 'choice': 'B'}]})}
            done, decisions = source_judge.choose_readings(rows, call, 'test')
            self.assertEqual(decisions[0]['applied'], permitted)
            self.assertEqual(done[0]['end'], 28.48 if permitted else 27.59)
            if not permitted:
                self.assertEqual(decisions[0]['reason'], 'outside_audio_span')
                self.assertEqual(done[0]['source'], rows[0]['source'])

    def test_a_completion_in_a_free_gap_uses_its_measured_end(self):
        rows = self.timed_agnes(29.)
        done, changes = complete_cut_off_rows(rows)
        self.assertEqual(done[0]['end'], 28.48)
        self.assertEqual(done[0]['start'], 26.02)
        self.assertEqual(done[1], rows[1])
        self.assertEqual(changes[0]['end_before'], 27.59)
        self.assertEqual(rows[0]['end'], 27.59)

    def test_invalid_or_outside_completion_times_are_not_used(self):
        for span in ({'start': 26.06, 'end': float('nan')}, {'start': True, 'end': 28.48},
                     {'start': 25., 'end': 28.48}, {'start': 26.06, 'end': 35.},
                     {'start': 28.48, 'end': 26.06}):
            rows = self.timed_agnes(40.)
            rows[0]['alternatives'][0]['completion_span'] = span
            self.assertEqual(complete_cut_off_rows(rows), (rows, []))

    def test_completion_span_includes_all_readings_with_the_same_words(self):
        from asr_windows import reading_alternatives
        rows = self.timed_agnes()
        text = rows[0]['alternatives'][0]['text']
        alternatives = reading_alternatives([{'start': 26.02, 'end': 27.59, 'text': rows[0]['source'], 'confidence': .4}],
            {'precision': [{'start': 26.06, 'end': 27.5, 'text': text}],
             'v2': [{'start': 26.03, 'end': 28.48, 'text': text}]})
        self.assertEqual(alternatives[0][0]['completion_span'], {'start': 26.03, 'end': 28.48})
        rows[0]['alternatives'] = alternatives[0]
        self.assertEqual(complete_cut_off_rows(rows), (rows, []))

    def test_the_real_cut_off_rows_are_completed_with_added_words_only(self):
        rows = completion_rows(["Holy .", "Let's get the out of", "This way of life can kill."], {1: [("Let's get the f*** out of here!", 2)]})
        done, completions = complete_cut_off_rows(rows)
        self.assertEqual(done[1]['source'], "Let's get the f*** out of here!")
        self.assertEqual([(item['id'], item['added_words']) for item in completions], [(1, 2)])
        rows = completion_rows(['Me and Robert will go this way, and you', 'can flank that way.'], {0: [('Uh huh. Me and Robert will go this way, and you guys can.', 3)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [], 'the added "can" is the start of the next row')

    def test_a_replacement_a_removal_or_a_complete_row_is_never_touched(self):
        # 343.8 s: v2 and Qwen both heard "care" on a short clip: a word is CHANGED, not added
        rows = completion_rows(['If we don\'t get the caribou,', 'we will suffer.'], {0: [("If we don't get care, we'll suffer.", 3)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [])
        rows = completion_rows(['A third of the village lacks basic plumbing and is forced to', 'haul water.'], {0: [('A third of the village lacks basic plumbing', 2)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [], 'fewer words')
        rows = completion_rows(['Okay, we are ready now.', 'Go.'], {0: [('Okay, we are ready now, I think so too.', 2)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [], 'a complete sentence is not completed')

    def test_words_that_belong_to_a_neighbour_are_refused(self):
        rows = completion_rows(['Stan has five kilos of meat in his freezer, enough to last', 'family a week.'], {1: [('enough to last the family a week.', 2)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [], 'the lead is the end of the previous row')
        rows = completion_rows(['This year we have over 40 dogs in our kennel', 'that we have to feed all winter.'], {0: [('Okay, this year we have over 40 dogs in our kennel that.', 2)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [], '"that" starts the next row')
        rows = completion_rows(['into behind this island and then I', 'have a woodlot that I prepared.'], {0: [('river get into behind this island and then I have a lot to do.', 3)]})
        self.assertEqual(complete_cut_off_rows(rows)[1], [], '"have a" is the start of the next row')

    def test_at_most_five_words_are_added_and_the_best_supported_reading_wins(self):
        rows = completion_rows(['We see the herd there in this little opening', 'straight through the woods.'],
                               {0: [('We see the herd there in this little opening right here today and tomorrow evening.', 5), ('We see the herd there in this little opening right here.', 2)]})
        done, completions = complete_cut_off_rows(rows)
        self.assertEqual(done[0]['source'], 'We see the herd there in this little opening right here.')


class WiringTests(unittest.TestCase):
    def test_the_second_readings_and_the_judge_use_the_limit(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn('plan_doubtful_row_windows(merged, maximum_total=second_opinion_rows(device))', source)
        self.assertIn('maximum_doubtful=second_opinion_rows(device),\n                                           maximum_confidence=.7)', source)
        self.assertNotIn('maximum=second_opinion_rows())', source, 'the judge keeps its limit of 24: lifting it changed 36 rows, one of them "the caribou" into "care"')
        self.assertIn('rows, completions = complete_cut_off_rows(rows)', source)
        self.assertIn('glossary_changes=glossary_changes', source)


if __name__ == '__main__':
    unittest.main()
