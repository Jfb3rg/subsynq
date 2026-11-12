import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import cloud_cpu_service as gateway
import sentence_form
import worker
from collapsed_runs import _token

FORMLESS = ('well i told him we would be there by nine but he said the road was closed so we took the other way '
            'round the lake and now we are late again')
FORMED = ('Well, I told him we would be there by nine. But he said the road was closed, so we took the other way '
          'round the lake. And now we are late again.')


def tokens(words):
    return [_token(word) for word in words if _token(word)]


class StretchTests(unittest.TestCase):
    def test_a_prompt_exists_only_for_measured_languages(self):
        self.assertIn('.', sentence_form.form_prompt('en'))
        self.assertEqual(sentence_form.form_prompt('EN'), sentence_form.form_prompt('en'))
        self.assertIn('?', sentence_form.form_prompt('de'))
        for language in ('sv', 'no', '', None):
            self.assertIsNone(sentence_form.form_prompt(language))

    def test_only_long_stretches_without_punctuation_count(self):
        self.assertEqual(sentence_form.unmarked_words(FORMLESS.split()), 32)
        self.assertEqual(sentence_form.unmarked_words(FORMED.split()), 0)
        self.assertFalse(sentence_form.lost_form('this is a long sentence of fifteen words that has no comma at all here'.split()))
        # The word that closes the stretch counts to its length but keeps its own mark.
        closed = ('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen done. ' 'Yes.').split()
        self.assertEqual(sentence_form.formless_words(closed), set(range(15)))
        # A lone dash is a break, as a speaker change is written.
        self.assertFalse(sentence_form.lost_form('one two three four five six seven eight - nine ten eleven twelve thirteen fourteen fifteen sixteen'.split()))

    def test_rows_are_read_as_running_text_in_time_order(self):
        rows = [{'start': 4.0, 'text': 'round the lake and now we are late again'},
                {'start': 0.0, 'text': 'well i told him we would be there by nine but he said'},
                {'start': 2.0, 'text': 'the road was closed so we took the other way'}]
        self.assertEqual(' '.join(sentence_form.row_words(rows)), FORMLESS)


class TransferTests(unittest.TestCase):
    def test_the_first_reading_takes_the_second_readings_form(self):
        formed, reason = sentence_form.transfer_form(FORMLESS.split(), FORMED.split())
        self.assertEqual(' '.join(formed), FORMED)
        self.assertIn('meningsformen', reason)

    def test_a_word_the_readings_disagree_on_stays_the_first_readings(self):
        second = FORMED.replace('by nine.', 'by noon.').replace('the other way', 'another way')
        formed, _ = sentence_form.transfer_form(FORMLESS.split(), second.split())
        self.assertEqual(tokens(formed), tokens(FORMLESS.split()))
        text = ' '.join(formed)
        self.assertIn('by nine. But he said', text)          # the sentence still ends where the second reading ended it
        self.assertIn('so we took the other way round the lake.', text)

    def test_a_word_only_the_second_reading_holds_is_not_added_but_its_sentence_end_is_kept(self):
        second = FORMED.replace('round the lake.', 'round the lake, friend. Oh!')
        formed, _ = sentence_form.transfer_form(FORMLESS.split(), second.split())
        self.assertEqual(tokens(formed), tokens(FORMLESS.split()))
        self.assertIn('round the lake. And now', ' '.join(formed))

    def test_a_word_only_the_first_reading_holds_stays_and_opens_a_sentence_in_upper_case(self):
        first = FORMLESS.replace('but he said', 'honestly he said')
        formed, _ = sentence_form.transfer_form(first.split(), FORMED.split())
        self.assertEqual(tokens(formed), tokens(first.split()))
        self.assertIn('by nine. Honestly he said the road was closed,', ' '.join(formed))

    def test_words_outside_the_stretch_are_not_touched(self):
        first = 'Hello there, Anna. ' + FORMLESS + ' Good night, everyone.'
        second = 'Hello there Anna! ' + FORMED + ' Good night everyone'
        formed, _ = sentence_form.transfer_form(first.split(), second.split())
        text = ' '.join(formed)
        self.assertTrue(text.startswith('Hello there, Anna. Well, I told him'))
        self.assertTrue(text.endswith('late again. Good night, everyone.'))

    def test_the_spacing_of_the_first_readings_words_is_kept(self):
        first = [' ' + word for word in FORMLESS.split()]
        formed, _ = sentence_form.transfer_form(first, FORMED.split())
        self.assertEqual(''.join(formed), ' ' + FORMED)

    def test_what_the_second_reading_holds_after_the_last_word_gives_nothing(self):
        first = FORMLESS.split()[:20]
        formed, _ = sentence_form.transfer_form(first, FORMED.split(), set(range(20)))
        self.assertEqual(' '.join(formed), ' '.join(FORMED.split()[:20]))
        # The first reading stops in the middle of a sentence: the end of the second reading's sentence is not its end.
        formed, _ = sentence_form.transfer_form(first + ['here'], (FORMED + ' Then we slept.').split(), set(range(21)))
        self.assertEqual(formed[-1], 'here')

    def test_only_the_named_words_take_form(self):
        formed, _ = sentence_form.transfer_form(FORMLESS.split(), FORMED.split(), set(range(10)))
        self.assertEqual(' '.join(formed), ' '.join(FORMED.split()[:10] + FORMLESS.split()[10:]))

    def test_a_title_the_second_reading_adds_does_not_end_a_sentence(self):
        first = FORMLESS.replace('i told him', 'i told brown')
        second = FORMED.replace('I told him', 'I told Mr. Brown')
        formed, _ = sentence_form.transfer_form(first.split(), second.split())
        self.assertTrue(' '.join(formed).startswith('Well, I told Brown we would'))

    def test_a_capital_follows_a_sentence_end_taken_from_differing_words_only(self):
        second = FORMED.replace('by nine. But he said', 'by nine. Sadly, but he said').replace('so we took', 'at 2 p.m. so we took')
        first = FORMLESS.replace('so we took', 'at 2 p.m. so we took').split()
        formed, _ = sentence_form.transfer_form(first, second.split(), {index for index, word in enumerate(first) if word != 'p.m.'})
        text = ' '.join(formed)
        self.assertIn('by nine. But he said', text)          # "Sadly," stood between: the sentence end is kept, so the next word opens one
        self.assertIn('at 2 p.m. so we took', text)          # the second reading wrote these two words together: its lower case stands

    def test_a_capital_from_the_second_readings_sentence_start_does_not_stand_after_a_kept_comma(self):
        first = ('We waited for hours, but nobody came to the old house on the hill that night and i think we all '
                 'went home with warren again').split()
        second = ('We waited for hours. But nobody came to the old house on the hill that night. I think we all '
                  'went home with Warren again.').split()
        formed, _ = sentence_form.transfer_form(first, second)
        self.assertEqual(' '.join(formed), 'We waited for hours, but nobody came to the old house on the hill that night. '
                                           'And I think we all went home with Warren again.')
        # "I" keeps its capital after a comma too, and so does a word the second reading wrote in capitals throughout.
        formed, _ = sentence_form.transfer_form(
            'We waited for hours, i think nobody came to the old house on the hill that night and we all went home'.split(),
            'We waited for hours. I think nobody came to the old house on the hill that night, and we all went home.'.split())
        self.assertEqual(' '.join(formed[:6]), 'We waited for hours, I think')
        formed, _ = sentence_form.transfer_form(
            'We waited for hours, ok nobody came to the old house on the hill that night and we all went home'.split(),
            'We waited for hours. OK, nobody came to the old house on the hill that night, and we all went home.'.split())
        self.assertEqual(' '.join(formed[:6]), 'We waited for hours, OK, nobody')

    def test_a_first_word_the_second_reading_wrote_differently_takes_no_mark_from_speech_before_it(self):
        first = 'but Anna wanted to walk one more mile along the river before the sun went down behind the hills'.split()
        second = ('They did everything for her. Anna wanted to walk one more mile along the river before the sun went '
                  'down behind the hills.').split()
        formed, _ = sentence_form.transfer_form(first, second)
        self.assertEqual(' '.join(formed), 'but Anna wanted to walk one more mile along the river before the sun went '
                                           'down behind the hills.')

    def test_a_capital_that_only_opens_a_row_of_the_second_reading_does_not_stand_inside_a_sentence(self):
        rows = [{'start': 0.0, 'end': 3.0, 'text': 'There they have a room of a fitting size'},
                {'start': 3.5, 'end': 9.0, 'text': 'And a garden just as large behind the old house. Their life has changed since then.'}]
        self.assertEqual(sentence_form.row_openers(rows), {0, 9})
        first = ('there they have a room of a fitting size and a garden just as large behind the old house their life '
                 'has changed since then').split()
        formed, _ = sentence_form.transfer_form(first, sentence_form.row_words(rows), openers=sentence_form.row_openers(rows))
        self.assertEqual(' '.join(formed), 'There they have a room of a fitting size and a garden just as large behind '
                                           'the old house. Their life has changed since then.')
        # Without the rows, the capital is the second reading's own spelling of the word.
        formed, _ = sentence_form.transfer_form(first, sentence_form.row_words(rows))
        self.assertIn('size And a garden', ' '.join(formed))

    def test_a_german_reading_keeps_its_nouns_in_upper_case(self):
        first = ('das wetter ist heute schön gehen wir später in den garten ich habe den schlüssel gefunden wir wollen '
                 'doch nur etwas brot kaufen').split()
        second = ('Das Wetter ist heute schön. Gehen wir später in den Garten? Ich habe den Schlüssel gefunden. Wir '
                  'wollen doch nur etwas Brot kaufen.').split()
        formed, _ = sentence_form.transfer_form(first, second)
        self.assertEqual(formed, second)

    def test_the_word_before_the_stretch_decides_whether_its_first_word_opens_a_sentence(self):
        first = 'we walked down to the river and sat on the stones until the sun was gone and nobody said a word'.split()
        second = 'We walked down to the river and sat on the stones until the sun was gone, and nobody said a word.'.split()
        for before, opening in (('stopped,', 'we'), ('stopped.', 'We'), (None, 'We')):
            formed, _ = sentence_form.transfer_form(first, second, before=before)
            self.assertEqual(formed[0], opening)
            self.assertEqual(' '.join(formed[1:]), ' '.join(second[1:]))

    def test_refused_readings(self):
        for second, reason in (('', 'tom'),
                               ('Something else entirely was said at this place, my friend. Nothing alike.', 'för få'),
                               (FORMLESS, 'inte mer meningsform')):
            formed, why = sentence_form.transfer_form(FORMLESS.split(), second.split())
            self.assertIsNone(formed)
            self.assertIn(reason, why)
        formed, why = sentence_form.transfer_form(FORMED.split(), FORMED.split())
        self.assertIsNone(formed)
        self.assertIn('har meningsform', why)


def timed(text, start, seconds_per_word=.4):
    return {'start': start, 'end': start + len(text.split()) * seconds_per_word, 'text': text}


def reading(sentences, window=(0.0, 30.0), start=2.0, profile='Whisper läser sammanhang'):
    rows = []
    for sentence in sentences:
        rows.append(timed(sentence, start))
        start = rows[-1]['end']
    return {'profile': profile, 'window': {'start': window[0], 'end': window[1]}, 'language': 'en', 'rows': rows}


SENTENCES = ['Well, I told him we would be there by nine.', 'But he said the road was closed, so we took the other way round the lake.',
             'And now we are late again.']


class RowsFormTests(unittest.TestCase):
    def rows(self):
        words = FORMLESS.split()
        rows, start = [], 2.0
        for part in (words[:9], words[9:21], words[21:]):
            rows.append({**timed(' '.join(part), start), 'confidence': .8})
            start = rows[-1]['end']
        return ([{'start': 0.0, 'end': 1.5, 'text': 'Good morning, Anna.', 'confidence': .9}] + rows
                + [{'start': rows[-1]['end'] + .5, 'end': rows[-1]['end'] + 2, 'text': 'Good night, everyone.', 'confidence': .9}])

    def test_finished_rows_without_form_take_the_second_readings_form(self):
        rows = self.rows()
        restored, records = sentence_form.restore_rows_form(rows, [reading(SENTENCES)])
        self.assertEqual(' '.join(row['text'] for row in restored), 'Good morning, Anna. ' + FORMED + ' Good night, everyone.')
        self.assertEqual([tokens(row['text'].split()) for row in restored], [tokens(row['text'].split()) for row in rows])
        self.assertEqual([{key: value for key, value in row.items() if key != 'text'} for row in restored],
                         [{key: value for key, value in row.items() if key != 'text'} for row in rows])
        self.assertIs(restored[0], rows[0])
        self.assertIs(restored[-1], rows[-1])
        self.assertEqual(rows[1]['text'], ' '.join(FORMLESS.split()[:9]))      # the rows handed in are not written over
        # The stretch runs on into the next row until its first mark ("Good night,"), so that row belongs to it.
        self.assertEqual([(record['rows'], record['taken']) for record in records], [([1, 4], True)])
        self.assertEqual((records[0]['before'], records[0]['after']), (FORMLESS + ' Good night, everyone.', FORMED + ' Good night, everyone.'))

    def test_rows_with_sentence_form_and_jobs_without_second_readings_stay_as_they_are(self):
        formed = [timed(sentence, 2.0 + index * 6) for index, sentence in enumerate(SENTENCES)]
        self.assertEqual(sentence_form.restore_rows_form(formed, [reading(SENTENCES)]), (formed, []))
        rows = self.rows()
        self.assertEqual(sentence_form.restore_rows_form(rows, []), (rows, []))

    def test_a_row_that_reaches_outside_the_window_waits_for_a_window_that_holds_it(self):
        rows = self.rows()
        early = reading(SENTENCES, window=(0.0, rows[2]['end'] + .2))
        late = reading(SENTENCES + ['Good night, everyone.'], window=(rows[3]['start'] - 1, 60.0), profile='Kontrollerar luckor och tider')
        restored, records = sentence_form.restore_rows_form(rows, [early])
        self.assertEqual(' '.join(row['text'] for row in restored[1:3]), ' '.join(FORMED.split()[:21]))
        self.assertEqual(restored[3]['text'], rows[3]['text'])
        restored, records = sentence_form.restore_rows_form(rows, [early, late])
        self.assertEqual(' '.join(row['text'] for row in restored[1:4]), FORMED)
        self.assertEqual([(record['profile'], record['rows']) for record in records],
                         [('Whisper läser sammanhang', [1, 2]), ('Kontrollerar luckor och tider', [3, 4])])

    def test_a_second_reading_of_other_speech_changes_nothing_and_the_next_one_is_tried(self):
        rows = self.rows()
        other = reading(['Something else entirely was said at this place, my friend.', 'Nothing alike, I am afraid.', 'Not a word of it, in fact.'])
        restored, records = sentence_form.restore_rows_form(rows, [other])
        self.assertEqual(restored, rows)
        self.assertEqual([(record['taken'], 'för få' in record['reason']) for record in records], [(False, True)])
        restored, records = sentence_form.restore_rows_form(rows, [other, reading(SENTENCES)])
        self.assertEqual(' '.join(row['text'] for row in restored[1:4]), FORMED)
        self.assertEqual([record['taken'] for record in records], [False, True])

    def test_speech_the_second_reading_holds_at_another_time_is_not_compared(self):
        rows = self.rows()
        second = reading(SENTENCES)
        second['rows'].append(timed('Then we slept. Then we ate.', rows[-1]['start'] + 5))
        restored, _ = sentence_form.restore_rows_form(rows[:3], [second])
        self.assertEqual(' '.join(row['text'] for row in restored[1:3]), ' '.join(FORMED.split()[:21]))

    def test_the_first_word_after_a_sentence_end_opens_in_upper_case_and_the_rows_spacing_is_kept(self):
        rows = self.rows()
        rows[1]['text'] = rows[1]['text'].replace('well i', 'well  i')
        sentences = ['Anna, well, I told him we would be there by nine.'] + SENTENCES[1:]
        restored, _ = sentence_form.restore_rows_form(rows, [reading(sentences, start=1.2)])
        self.assertTrue(restored[1]['text'].startswith('Well,  I told him'))

    def test_a_row_after_a_comma_does_not_open_in_upper_case(self):
        rows = [{'start': 1.0, 'end': 2.0, 'text': 'When the rain stopped,'},
                {'start': 3.0, 'end': 6.0, 'text': 'we walked down to the river and sat on the stones'},
                {'start': 6.0, 'end': 9.0, 'text': 'until the sun was gone and nobody said a word'}]
        readings = [{'profile': 'A', 'window': {'start': 0.0, 'end': 30.0}, 'rows': [
            {'start': 1.0, 'end': 2.0, 'text': 'When the rain stopped.'},
            {'start': 3.0, 'end': 9.0, 'text': 'We walked down to the river and sat on the stones until the sun was gone, '
                                               'and nobody said a word.'}]}]
        restored, records = sentence_form.restore_rows_form(rows, readings)
        self.assertEqual([row['text'] for row in restored], [
            'When the rain stopped,', 'we walked down to the river and sat on the stones',
            'until the sun was gone, and nobody said a word.'])
        self.assertIs(restored[0], rows[0])
        self.assertEqual([record['taken'] for record in records], [True])



def segment(text, start=0.0):
    words = [SimpleNamespace(word=' ' + word, start=start + index * .3, end=start + index * .3 + .25, probability=.5 + index / 100)
             for index, word in enumerate(text.split())]
    return SimpleNamespace(start=start, end=words[-1].end, text=' ' + text, words=words, no_speech_prob=.02, avg_logprob=-.2)


class Reader:
    def __init__(self, first, second=None, error=None):
        self.first, self.second, self.error, self.calls = first, second, error, []

    def transcribe(self, path, **options):
        self.calls.append(options)
        if 'hotwords' not in options:
            return iter(self.first), SimpleNamespace(language='en')
        if self.error:
            raise RuntimeError(self.error)
        return iter(self.second), SimpleNamespace(language='en')


OPTIONS = dict(language=None, multilingual=True, vad_filter=True, beam_size=5, condition_on_previous_text=False,
               word_timestamps=True, hallucination_silence_threshold=2.0)
WINDOW = {'start': 120.0, 'end': 150.0}


class WindowReadingTests(unittest.TestCase):
    def read(self, reader, state=None, options=OPTIONS):
        segments, info = reader.transcribe('clip.wav', **options)
        segments = list(segments)
        return segments, worker.read_window_form(reader, 'clip.wav', options, segments, info, WINDOW, {} if state is None else state)

    def halves(self, text):
        words = text.split()
        return [segment(' '.join(words[:14])), segment(' '.join(words[14:]), start=6.0)]

    def test_a_formless_window_is_read_again_with_the_sentence_form_text(self):
        reader = Reader(self.halves(FORMLESS), [segment(SENTENCES[0], 1.0), segment(' '.join(SENTENCES[1:]), 5.0)])
        segments, second = self.read(reader)
        self.assertEqual(len(reader.calls), 2)
        self.assertEqual(reader.calls[1], {**OPTIONS, 'hotwords': sentence_form.form_prompt('en')})
        self.assertEqual(''.join(word.word for item in segments for word in item.words), ' ' + FORMLESS)   # the first reading is untouched
        self.assertEqual((second['window'], second['language']), (WINDOW, 'en'))
        self.assertEqual([(row['start'], row['text']) for row in second['rows']], [(121.0, SENTENCES[0]), (125.0, ' '.join(SENTENCES[1:]))])
        self.assertTrue(all(row['end'] > row['start'] for row in second['rows']))

    def test_a_window_with_sentence_form_or_another_language_is_read_once(self):
        for first, options in (([segment(FORMED)], OPTIONS), (self.halves(FORMLESS), {**OPTIONS, 'language': 'sv', 'multilingual': False})):
            reader = Reader(first, [segment(FORMED)])
            self.assertIsNone(self.read(reader, options=options)[1])
            self.assertEqual(len(reader.calls), 1)

    def test_the_switch_and_a_reader_without_previous_text_leave_the_window_alone(self):
        reader = Reader(self.halves(FORMLESS), [segment(FORMED)])
        with patch.object(worker, 'FORM_REREAD_ENABLED', False):
            self.assertIsNone(self.read(reader)[1])
        reader.accepts_prompt = False
        self.assertIsNone(self.read(reader)[1])
        self.assertTrue(all('hotwords' not in call for call in reader.calls))
        self.assertFalse(worker.RemoteWhisperModel.accepts_prompt)
        session = worker.SequentialASRModels(worker.RemoteWhisperModel('http://127.0.0.1:1'))
        self.assertFalse(session.wrap(worker.RemoteWhisperModel('http://127.0.0.1:1')).accepts_prompt)
        self.assertTrue(session.wrap(Reader([])).accepts_prompt)

    def test_a_reader_that_fails_on_the_text_is_not_asked_again(self):
        state = {}
        reader = Reader(self.halves(FORMLESS), error='Moln-servern misslyckades: Whisper-alternativet är inte tillåtet.')
        self.assertIsNone(self.read(reader, state)[1])
        self.assertIn('tog inte emot hjälptexten', state['refused'])
        self.assertIsNone(self.read(reader, state)[1])
        self.assertEqual(sum('hotwords' in call for call in reader.calls), 1)

    def test_the_second_reading_gives_the_finished_rows_their_form(self):
        reader = Reader(self.halves(FORMLESS), [segment(sentence, 1.0 + index * 4) for index, sentence in enumerate(SENTENCES)])
        _, second = self.read(reader)
        words = FORMLESS.split()
        rows = [{'start': 121.0, 'end': 126.0, 'text': ' '.join(words[:14])}, {'start': 126.0, 'end': 134.0, 'text': ' '.join(words[14:])}]
        restored, _ = sentence_form.restore_rows_form(rows, [{'profile': 'Whisper läser sammanhang', **second}])
        self.assertEqual(' '.join(row['text'] for row in restored), FORMED)


class RentedServerOptionTests(unittest.TestCase):
    def test_the_sentence_form_text_is_an_allowed_option(self):
        prompt = sentence_form.form_prompt('en')
        self.assertEqual(gateway.parse_options('{"hotwords": "%s"}' % prompt), {'hotwords': prompt})
        for header in ('{"hotwords": 5}', '{"hotwords": null}', '{"hotwords": "%s"}' % ('a' * 401), '{"initial_prompt": "Hello."}'):
            with self.assertRaises(ValueError):
                gateway.parse_options(header)


if __name__ == '__main__':
    unittest.main()
