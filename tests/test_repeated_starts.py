import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from asr_windows import drop_unconfirmed_repeated_starts

# Yukon Men S01E01 (dev jobs 84, 89, 104): the long pass wrote the start of the sentence twice; the short pass heard it once.
LONG_PASS = [
    {'start': 2050.82, 'end': 2053.18, 'text': 'While James works to process 10 cords of wood,', 'confidence': .88},
    {'start': 2054.06, 'end': 2055.30, 'text': 'the hunting party approaches the area where the caribou herd', 'confidence': .96},
    {'start': 2055.30, 'end': 2055.84, 'text': 'the hunting party approaches the area', 'confidence': 1.0},
    {'start': 2056.83, 'end': 2058.21, 'text': 'has been spotted.', 'confidence': 1.0},
    {'start': 2059.91, 'end': 2062.13, 'text': "We think we're within a couple of miles of them.", 'confidence': .51},
]
SHORT_PASS = [
    {'start': 2050.42, 'end': 2053.18, 'text': 'While James works to process 10 cords of wood,'},
    {'start': 2053.62, 'end': 2055.76, 'text': 'the hunting party approaches the area'},
    {'start': 2055.76, 'end': 2058.22, 'text': 'where the caribou herd has been spotted.'},
    {'start': 2059.52, 'end': 2062.16, 'text': "We think we're within a couple miles of them."},
]


def cue(start, end, text):
    return {'start': start, 'end': end, 'text': text, 'confidence': .9}


class RepeatedStartTests(unittest.TestCase):
    def test_the_repeated_opening_heard_once_by_the_short_pass_is_removed(self):
        kept, removed = drop_unconfirmed_repeated_starts(LONG_PASS, SHORT_PASS)
        self.assertEqual([row['text'] for row in removed], ['the hunting party approaches the area'])
        self.assertEqual([row['start'] for row in kept], [2050.82, 2054.06, 2056.83, 2059.91])

    def test_a_phrase_the_short_pass_also_hears_twice_was_really_said_twice(self):
        spoken_twice = SHORT_PASS[:1] + [{'start': 2053.62, 'end': 2055.30, 'text': 'the hunting party approaches the area where the caribou herd'},
                                         {'start': 2055.30, 'end': 2055.84, 'text': 'the hunting party approaches the area'}] + SHORT_PASS[2:]
        kept, removed = drop_unconfirmed_repeated_starts(LONG_PASS, spoken_twice)
        self.assertEqual((len(kept), removed), (5, []))

    def test_other_repeats_in_the_same_episode_are_left_alone(self):
        rows = [cue(19.0, 19.8, 'Get up there!'), cue(19.9, 20.5, 'Get up there!'),                         # equal, not a longer line before it
                cue(854.0, 854.6, 'Hey, get up there!'), cue(854.7, 855.2, 'Get up there!'),                  # the end of the line, not its start
                cue(10.0, 12.0, 'I think we should go'), cue(12.1, 12.8, 'I think we'),                      # three words: too short to be sure
                cue(30.0, 33.0, 'the hunting party approaches the area where'), cue(34.0, 35.0, 'the hunting party approaches the area')]  # not in the same breath
        kept, removed = drop_unconfirmed_repeated_starts(rows, [])
        self.assertEqual((len(kept), removed), (8, []))

    def test_nothing_changes_without_a_repeat_and_the_input_is_not_touched(self):
        before = [dict(row) for row in LONG_PASS]
        kept, _ = drop_unconfirmed_repeated_starts(LONG_PASS, SHORT_PASS)
        self.assertEqual(LONG_PASS, before)
        self.assertEqual(drop_unconfirmed_repeated_starts([], [])[0], [])
        self.assertEqual(drop_unconfirmed_repeated_starts(kept, SHORT_PASS), (kept, []))

    def test_the_step_runs_after_the_overlaps_are_trimmed_and_before_the_readers_and_is_reported(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertLess(source.index('merged = trim_small_overlaps(merged)'), source.index('drop_unconfirmed_repeated_starts(merged, precision_profile)'))
        self.assertLess(source.index('drop_unconfirmed_repeated_starts(merged, precision_profile)'), source.index('before_reader_plan = [dict(cue) for cue in merged]'))
        self.assertIn('repeated_starts_dropped=repeated_starts', source)
        self.assertIn('togs bort', source)


class TruncatedAnswerTests(unittest.TestCase):
    def ask(self, done_reason):
        body = io.BytesIO(json.dumps({'response': '{"rows": [', 'done_reason': done_reason}).encode())
        with patch.object(worker.urllib.request, 'urlopen', return_value=body), patch.object(worker, 'log_model_timing'):
            return worker.call_ollama({'model': 'x', 'prompt': 'y'})

    def test_an_answer_cut_off_at_the_token_limit_is_its_own_kind_of_error(self):
        with self.assertRaises(worker.OutputTruncated) as raised:
            self.ask('length')
        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertIn('avslutades inte normalt', str(raised.exception))

    def test_other_unfinished_answers_stay_plain_errors_and_a_finished_one_is_returned(self):
        with self.assertRaises(RuntimeError) as raised:
            self.ask('load')
        self.assertNotIsInstance(raised.exception, worker.OutputTruncated)
        self.assertEqual(self.ask('stop')['done_reason'], 'stop')

    def rows(self, count=12):
        return [{'id': number, 'source': f'Source {number}', 'sv': f'Rad {number}'} for number in range(count)]

    def flags(self, ids):
        return [{'id': number, 'category': 'meaning', 'reason': 'x', 'source_quote': 'a', 'target_quote': 'b'} for number in ids]

    def test_a_cut_off_repair_is_asked_again_for_each_half_of_the_rows(self):
        asked = []
        records = []

        def once(job, rows, flags, **kwargs):
            ids = sorted({flag['id'] for flag in flags})
            asked.append(ids)
            if len(ids) > 3:
                raise worker.OutputTruncated('length')
            return [{**row, 'sv': row['sv'] + '!'} if row['id'] in ids else dict(row) for row in rows]
        with patch.object(worker, 'repair_flagged_translation_once', side_effect=once):
            result = worker.repair_flagged_translation({'id': 1}, self.rows(), self.flags(range(12)), record=records.append)
        self.assertEqual(asked, [list(range(12)), list(range(6)), [0, 1, 2], [3, 4, 5], list(range(6, 12)), [6, 7, 8], [9, 10, 11]])
        self.assertEqual([row['sv'] for row in result], [f'Rad {number}!' for number in range(12)])
        self.assertEqual([item['stage'] for item in records], ['truncated_split'] * 3)

    def test_one_row_that_cannot_be_repaired_stays_as_it_was_and_other_errors_still_fail_the_job(self):
        records = []
        for error in (worker.OutputTruncated('length'), worker.RepairFailed('ogiltigt')):
            with patch.object(worker, 'repair_flagged_translation_once', side_effect=error):
                result = worker.repair_flagged_translation({'id': 1}, self.rows(), self.flags([4]), record=records.append)
            self.assertEqual(result, self.rows(), 'unchanged: the row stays flagged for the gate')
        self.assertEqual([item['stage'] for item in records], ['repair_failed', 'repair_failed'])
        with patch.object(worker, 'repair_flagged_translation_once', side_effect=RuntimeError('annat fel')) as once:
            with self.assertRaises(RuntimeError):
                worker.repair_flagged_translation({'id': 1}, self.rows(), self.flags([1, 2, 3, 4]))
        self.assertEqual(once.call_count, 1, 'only a cut-off or invalid answer is split')

    def test_an_invalid_answer_is_asked_again_for_each_half_and_only_the_bad_row_stays(self):
        asked = []

        def once(job, rows, flags, **kwargs):
            ids = sorted({flag['id'] for flag in flags})
            asked.append(ids)
            if len(ids) > 1 or ids == [2]:
                raise worker.RepairFailed('Frasrättningen ändrar fel rad-ID eller saknar en begärd rad.')
            return [{**row, 'sv': row['sv'] + '!'} if row['id'] in ids else dict(row) for row in rows]
        with patch.object(worker, 'repair_flagged_translation_once', side_effect=once):
            result = worker.repair_flagged_translation({'id': 1}, self.rows(4), self.flags([0, 1, 2, 3]))
        self.assertEqual([row['sv'] for row in result], ['Rad 0!', 'Rad 1!', 'Rad 2', 'Rad 3!'])
        self.assertEqual(asked, [[0, 1, 2, 3], [0, 1], [0], [1], [2, 3], [2], [3]])

    def test_the_three_repair_steps_raise_the_repair_error(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count("raise RepairFailed('"), 3)
        self.assertNotIn("raise RuntimeError('Översättningens frasrättning", source)

    def test_the_token_limit_of_the_two_repair_calls_grows_with_the_rows(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count("'num_predict': min(4096, max(2048, 320 * len(targets)))"), 2)


if __name__ == '__main__':
    unittest.main()
