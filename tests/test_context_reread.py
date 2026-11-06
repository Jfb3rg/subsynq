import json
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from reread import words_inside, wording_support
from source_judge import build_request, judge_items


def judge_answer(choice, flawed=True, row_id=411):
    return lambda request: {'response': json.dumps({'rows': [{'id': row_id, 'a_has_error': flawed, 'choice': choice}]})}


# The real readings of regression case(Example Series 4x02), row 411, read tightly and in wider windows (STATUS 2026-11-03).
TIGHT = [{'name': 'precision', 'text': 'Yes, sir', 'confidence': .9}, {'name': 'v2', 'text': "Yes, sir, I'm three", 'confidence': .63},
         {'name': 'qwen', 'text': 'Yes, sir. On three.', 'confidence': .5}]
WIDE = [{'name': 'context', 'text': 'Yes, sir. On three.', 'confidence': .7, 'window': [2, 1]},
        {'name': 'context', 'text': 'Yes, sir! On three?', 'confidence': .87, 'window': [6, 3]},
        {'name': 'v2', 'text': "Yes, sir! I'm three.", 'confidence': .55, 'window': [2, 1]},
        {'name': 'v2', 'text': 'Yes, sir. On three?', 'confidence': .76, 'window': [6, 3]}]


def rows():
    return [{'id': 409, 'start': 1785.3, 'end': 1787.6, 'source': 'On three, everyone lays down fire.'},
            {'id': 410, 'start': 1788.5, 'end': 1790.5, 'source': 'Lily, you got a clean shot on him?'},
            {'id': 411, 'start': 1800.2, 'end': 1802.2, 'source': "Yes, sir, I'm three."},
            {'id': 412, 'start': 1802.4, 'end': 1805.9, 'source': 'All right, here we go one two'}]


class ContextWordsTests(unittest.TestCase):
    def test_only_words_timed_inside_the_row_are_kept(self):
        words = [{'word': ' Lily?', 'start': 1797.0, 'end': 1797.5, 'probability': .9},
                 {'word': ' Yes,', 'start': 1800.3, 'end': 1800.6, 'probability': .84}, {'word': ' sir.', 'start': 1800.6, 'end': 1800.8, 'probability': .99},
                 {'word': ' On', 'start': 1801.3, 'end': 1801.8, 'probability': .82}, {'word': ' three?', 'start': 1801.8, 'end': 1802.1, 'probability': .96},
                 {'word': ' All', 'start': 1802.6, 'end': 1803.1, 'probability': .9}]
        self.assertEqual(words_inside(words, 1800.2, 1802.2), ('Yes, sir. On three?', .82))

    def test_support_counts_model_families_not_readings(self):
        support = dict((text, families) for text, families in wording_support("Yes, sir, I'm three.", TIGHT + WIDE))
        self.assertEqual(support["Yes, sir, I'm three."], {'whisper-v2', 'whisper-v3'})       # v2 twice, plus the first transcription
        self.assertEqual(support['Yes, sir. On three.'], {'qwen', 'whisper-v2', 'whisper-v3'})


class JudgedChoiceTests(unittest.TestCase):
    def run_row(self, readings, call, extra=WIDE):
        job, data = {'id': 64, 'media_path': '/dev/null'}, rows()
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'):
            changed = worker.reread_flagged_rows(job, data, [411], reader=lambda *a: {411: readings},
                                                 context_reader=lambda *a: {411: extra}, judge_call=call)
        return changed, data[2]['source'], job['reread_rows'][0]

    def test_all_three_families_and_the_judge_change_the_row(self):
        changed, source, record = self.run_row(TIGHT, judge_answer('B'))
        self.assertEqual((changed, source), ([411], 'Yes, sir. On three.'))
        self.assertEqual(record['context_choice']['supports'], [2, 3])

    def test_the_judge_must_say_the_current_wording_is_wrong(self):
        changed, source, _ = self.run_row(TIGHT, judge_answer('B', flawed=False))
        self.assertEqual((changed, source), ([], "Yes, sir, I'm three."))

    def test_a_wording_with_no_more_families_than_the_current_one_is_never_offered(self):
        asked = []
        only_qwen = [{'name': 'precision', 'text': "Yes, sir, I'm three", 'confidence': .9},
                     {'name': 'v2', 'text': "Yes, sir, I'm three", 'confidence': .6}, {'name': 'qwen', 'text': 'Yes, sir. On three.', 'confidence': .5}]
        changed, source, record = self.run_row(only_qwen, lambda request: asked.append(request), extra=[])
        self.assertEqual((changed, source, asked), ([], "Yes, sir, I'm three.", []))
        self.assertIn('ingen hörd läsning', record['context_choice']['decision'])

    def test_two_families_of_three_are_not_enough(self):
        # Replay: SamplePerson "Jeg må bli lyktig." became the equally invented "liktet" when two families of three were enough.
        asked = []
        heard = [{'name': 'precision', 'text': 'Yes, sir. On three.', 'confidence': .9}, {'name': 'v2', 'text': "Yes, sir, I'm three", 'confidence': .6},
                 {'name': 'qwen', 'text': 'Yes, sir. On three.', 'confidence': .5}]
        wide = [{'name': 'v2', 'text': "Yes, sir! I'm three.", 'confidence': .55, 'window': [2, 1]}]
        changed, source, record = self.run_row(heard, lambda request: asked.append(request), extra=wide)
        self.assertEqual((changed, source, asked), ([], "Yes, sir, I'm three.", []))

    def test_the_judge_s_guards_still_refuse_a_rewrite(self):
        # Row 413 of the same job: the judge chose "You Mercedon?" (two families) and the wording guard refused it.
        data = [{'id': 412, 'start': 1802.4, 'end': 1805.9, 'source': 'All right, here we go one two'},
                {'id': 413, 'start': 1862.5, 'end': 1864.8, 'source': "You mercy didn't yes, sir."},
                {'id': 414, 'start': 1864.9, 'end': 1866.1, 'source': 'That was a headshot man.'}]
        heard = [{'name': 'precision', 'text': 'You Mercedon? Yes, sir!', 'confidence': .44}, {'name': 'v2', 'text': 'You Marcinian? Yes, sir!', 'confidence': .45},
                 {'name': 'qwen', 'text': 'You mercy them? Yes, sir.', 'confidence': .5}]
        wide = [{'name': 'context', 'text': 'You Mercedon? Yes, sir.', 'confidence': .51, 'window': [2, 1]},
                {'name': 'v2', 'text': 'You Mercedan? Yes, sir.', 'confidence': .41, 'window': [2, 1]}]
        job = {'id': 64, 'media_path': '/dev/null'}
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'check_cancelled'):
            changed = worker.reread_flagged_rows(job, data, [413], reader=lambda *a: {413: heard},
                                                 context_reader=lambda *a: {413: wide}, judge_call=judge_answer('B', row_id=413))
        self.assertEqual((changed, data[1]['source']), ([], "You mercy didn't yes, sir."))

    def test_tests_with_their_own_reader_and_no_context_reader_are_unchanged(self):
        job, data = {'id': 64, 'media_path': '/dev/null'}, rows()
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None), \
                patch.object(worker, 'context_readings', side_effect=AssertionError('must not read')):
            self.assertEqual(worker.reread_flagged_rows(job, data, [411], reader=lambda *a: {411: TIGHT}), [])


class OrthographyShiftTests(unittest.TestCase):
    """TRANSFERVINDUE (Danish, regression case): two rows replaced by the readers' Swedish; replay: these 2 of 19 replacements, no other."""

    def test_danish_or_norwegian_spelling_never_becomes_swedish_or_back(self):
        from reread import orthography_shift
        self.assertTrue(orthography_shift('måske alle finder egen væg.', 'Vi måste alla finna vår egen väg.'))
        self.assertTrue(orthography_shift('sikkert fremlæge. Et sted mellem liv og død.', 'för att säkert framlega ett stöd mellan liv och död.'))
        self.assertTrue(orthography_shift('Jag är här.', 'Jeg er her, søster.'))
        self.assertFalse(orthography_shift("Yes, sir, I'm three.", 'Yes, sir. On three.'))
        self.assertFalse(orthography_shift('Jeg må bli lyktig.', 'Jeg må bli lykkelig.'))     # å is shared: not a language shift

    def test_the_reread_keeps_the_danish_row(self):
        job = {'id': 134, 'media_path': '/dev/null'}
        data = [{'id': 22, 'start': 50.0, 'end': 52.0, 'source': 'måske alle finder egen væg.'}]
        heard = [{'name': n, 'text': 'Vi måste alla finna vår egen väg.', 'confidence': .8} for n in ('precision', 'v2', 'qwen')]
        with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'local_language_override', return_value=None):
            self.assertEqual(worker.reread_flagged_rows(job, data, [22], reader=lambda *a: {22: heard}), [])
        self.assertEqual(data[0]['source'], 'måske alle finder egen væg.')
        self.assertIn('skriftspråk', job['reread_rows'][0]['decision'])


class EarlierLinesTests(unittest.TestCase):
    def test_the_judge_sees_earlier_lines_only_when_asked(self):
        data = rows()
        data[2]['alternatives'] = [{'text': 'Yes, sir. On three.', 'support': 3, 'current_support': 2}]
        plain = json.loads(build_request('m', judge_items(data))['prompt'])['rows'][0]
        wide_request = build_request('m', judge_items(data, earlier=2))
        wide = json.loads(wide_request['prompt'])['rows'][0]
        self.assertNotIn('earlier_lines', plain)
        self.assertEqual(wide['earlier_lines'], 'On three, everyone lays down fire.')
        self.assertIn('earlier_lines', wide_request['system'])


if __name__ == '__main__':
    unittest.main()
