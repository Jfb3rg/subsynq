import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, '/ai')
import worker


def rows(*sources, first_id=0):
    return [{'id': first_id + index, 'start': index, 'end': index + 1, 'source': source}
            for index, source in enumerate(sources)]


def answer(text):
    return {'response': json.dumps({'text': text})}


class LacksPunctuationTests(unittest.TestCase):
    def test_a_lowercase_row_without_any_punctuation_is_unpunctuated(self):
        self.assertTrue(worker.lacks_punctuation("we've intercepted a transmission from earth negative the signal"))
        self.assertTrue(worker.lacks_punctuation('that\'s impossible'))

    def test_punctuated_capitalized_short_or_non_latin_rows_are_left_alone(self):
        for source in ('Wait.', 'Yes, Captain.', 'Hello there', 'can', '', None, 'life scanning,',
                       '며칠 전에 친구들이랑 사냥을 해나갔었어', 'это не так просто сказать'):
            self.assertFalse(worker.lacks_punctuation(source), source)


class SentenceCapitalsTests(unittest.TestCase):
    def test_capitals_stay_only_at_sentence_starts_and_for_the_pronoun_i(self):
        self.assertEqual(worker.keep_only_sentence_capitals(
            "We've intercepted a transmission from Earth. Negative, the signal"),
            "We've intercepted a transmission from earth. Negative, the signal")
        self.assertEqual(worker.keep_only_sentence_capitals("Oh my God, I can't believe it. I'm here and I'll go"),
                         "Oh my god, I can't believe it. I'm here and I'll go")

    def test_a_row_that_continues_the_previous_one_stays_lowercase(self):
        self.assertEqual(worker.keep_only_sentence_capitals('originates from the planet'),
                         'Originates from the planet', 'the first word of a text is a sentence start')
        self.assertEqual(worker.keep_only_sentence_capitals('life. Scanning. Done? Yes! ok'),
                         'Life. Scanning. Done? Yes! Ok')

    def test_numbers_and_lone_marks_are_kept(self):
        self.assertEqual(worker.keep_only_sentence_capitals('B-1! B-1! 12 million years ago — ok'),
                         'B-1! B-1! 12 million years ago — ok')


class SplitPunctuatedTextTests(unittest.TestCase):
    def test_the_text_is_cut_back_into_the_rows_by_word_count(self):
        batch = rows('we have intercepted a transmission negative the signal', 'originates from the planet',
                     'ahead how old is it')
        pieces = worker.split_punctuated_text(
            batch, 'We have intercepted a transmission. Negative, the signal originates from the planet ahead. '
                   'How old is it?')
        self.assertEqual(pieces, ['We have intercepted a transmission. Negative, the signal',
                                  'originates from the planet', 'ahead. How old is it?'])

    def test_a_changed_added_or_dropped_word_cancels_everything(self):
        batch = rows('hello there', 'my friend')
        for text in ('Hello there, my dear friend.', 'Hello, my friend.', 'Hallo there, my friend.'):
            self.assertIsNone(worker.split_punctuated_text(batch, text), text)

    def test_a_word_group_that_straddles_two_rows_cancels_everything(self):
        self.assertIsNone(worker.split_punctuated_text(rows('say b', '1 now'), 'Say B-1 now.'))

    def test_a_lone_dash_belongs_to_the_word_before_it(self):
        self.assertEqual(worker.split_punctuated_text(rows('wait for me', 'please'), 'Wait for me — please.'),
                         ['Wait for me —', 'please.'])


class RestoreSourcePunctuationTests(unittest.TestCase):
    def batch(self):
        return rows('we have intercepted a transmission negative the signal', 'originates from the planet',
                    'Wait.', 'ahead how old is it')

    def test_unpunctuated_rows_get_punctuation_and_nothing_else_changes(self):
        batch = self.batch()
        call = Mock(return_value=answer('We have intercepted a transmission. Negative, the signal originates '
                                        'from the planet. Wait. Ahead. How old is it?'))
        changed = worker.restore_source_punctuation(batch, call)
        self.assertEqual(changed, 3)
        self.assertEqual([row['source'] for row in batch],
                         ['We have intercepted a transmission. Negative, the signal', 'originates from the planet.',
                          'Wait.', 'Ahead. How old is it?'])
        self.assertEqual([(row['id'], row['start'], row['end']) for row in batch],
                         [(0, 0, 1), (1, 1, 2), (2, 2, 3), (3, 3, 4)], 'ids and timing are untouched')
        sent = json.loads(call.call_args.args[0]['prompt'])['text']
        self.assertEqual(sent, 'we have intercepted a transmission negative the signal originates from the planet '
                               'Wait. ahead how old is it')

    def test_a_row_that_already_has_punctuation_is_never_touched(self):
        batch = self.batch()
        call = Mock(return_value=answer('We have intercepted a transmission. Negative, the signal originates '
                                        'from the planet. WAIT! Ahead. How old is it?'))
        worker.restore_source_punctuation(batch, call)
        self.assertEqual(batch[2]['source'], 'Wait.')

    def test_a_changed_word_leaves_the_whole_batch_as_it_was(self):
        batch = self.batch()
        before = [row['source'] for row in batch]
        call = Mock(return_value=answer('We have intercepted a transmission. Negative, the signal originates '
                                        'from a planet. Wait. Ahead. How old is it?'))
        self.assertEqual(worker.restore_source_punctuation(batch, call), 0)
        self.assertEqual([row['source'] for row in batch], before)

    def test_failures_of_this_optional_step_mean_unchanged(self):
        for call in (Mock(side_effect=RuntimeError('Ollama 500')), Mock(return_value={'response': 'not json'}),
                     Mock(return_value={'response': json.dumps({'rows': []})}), Mock(return_value={})):
            batch = self.batch()
            before = [row['source'] for row in batch]
            self.assertEqual(worker.restore_source_punctuation(batch, call), 0)
            self.assertEqual([row['source'] for row in batch], before)

    def test_a_cancelled_job_still_stops(self):
        with self.assertRaises(worker.JobCancelled):
            worker.restore_source_punctuation(self.batch(), Mock(side_effect=worker.JobCancelled('stop')))

    def test_nothing_is_asked_when_fewer_than_two_rows_need_it(self):
        call = Mock()
        worker.restore_source_punctuation(rows('only this has none', 'Yes, it has.', 'Fine.'), call)
        call.assert_not_called()

    def test_long_files_are_restored_in_separate_batches(self):
        many = rows(*[f'sentence number {number} goes on' for number in range(45)])
        sent = []

        def call(request):
            text = json.loads(request['prompt'])['text']
            sent.append(len(text.split()))
            return answer(text.capitalize() + '.')
        worker.restore_source_punctuation(many, call)
        self.assertEqual(len(sent), 2, 'one request per 40 rows')


class TranslateAndReviewOrderTests(unittest.TestCase):
    def test_punctuation_is_restored_before_the_first_translation(self):
        events = []
        batch = rows('a b', 'c d')
        with patch.object(worker, 'restore_source_punctuation', side_effect=lambda *a, **k: events.append('restore')), \
                patch.object(worker, 'translate', side_effect=lambda *a, **k: events.append('translate') or batch), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'review_and_save_translation', side_effect=lambda *a, **k: events.append('review')):
            worker.translate_and_review({'id': 1}, batch)
        self.assertEqual(events, ['restore', 'translate', 'review'])


if __name__ == '__main__':
    unittest.main()
