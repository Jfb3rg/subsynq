import sys
import unittest

sys.path.insert(0, '/app')
import asr_windows


def cue(start, end, text, confidence):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


# Real readings of Man of War (dev job 49), English track: the cue at 100.2 s was kept from the 90 s pass while the
# short pass, Whisper v2 and Qwen3-ASR all heard "The way you make it your life. All right, lights on me."
FINAL = [cue(97.3, 98.7, 'War is a monster.', .991),
         cue(100.2, 104.8, 'way you make it your life all right lights on me this', .460),
         cue(115.2, 116.6, 'This is about to get ugly.', .765)]
READINGS = {
    'context': [cue(97.3, 98.7, 'War is a monster.', .991),
                cue(100.2, 104.8, 'way you make it your life all right lights on me this', .460),
                cue(115.6, 116.6, 'is about to get ugly', .999)],
    'precision': [cue(97.3, 98.7, 'War is a monster', .355), cue(99.8, 101.5, 'And why you make it your life?', .317),
                  cue(102.5, 103.8, 'All right lights on me', .441), cue(115.2, 116.6, 'This is about to get ugly.', .765)],
    'v2': [cue(98.0, 99.0, 'is a monster.', .661), cue(100.2, 101.6, 'The way you make it your life.', .282),
           cue(102.9, 103.9, 'Alright, lights on me.', .489)],
    'qwen': [cue(99.53, 105.11, 'The way you make it your life. All right, lights on me.', .5)],
}


class ReadingAlternativesTests(unittest.TestCase):
    def test_a_doubtful_cue_gets_the_other_wordings_the_passes_heard_best_supported_first(self):
        result = asr_windows.reading_alternatives(FINAL, READINGS)
        self.assertEqual(sorted(result), [1], 'only the low-confidence cue has alternatives')
        texts = [item['text'] for item in result[1]]
        self.assertIn('The way you make it your life. All right, lights on me.', texts)
        self.assertNotIn('way you make it your life all right lights on me this', texts, 'its own wording is the default')
        self.assertEqual([item['support'] for item in result[1]], sorted((item['support'] for item in result[1]), reverse=True))
        for item in result[1]:
            self.assertEqual(set(item), {'text', 'support', 'current_support'})
            self.assertGreaterEqual(item['current_support'], 1)

    def test_a_confident_cue_that_the_passes_agree_on_has_none(self):
        confident = [cue(0.0, 2.0, 'Hello there my friend.', .95)]
        readings = {'context': confident, 'precision': [cue(0.0, 2.0, 'Hello there my friend.', .9)],
                    'v2': [cue(0.0, 2.0, 'Hello there my friend.', .9)]}
        self.assertEqual(asr_windows.reading_alternatives(confident, readings), {})

    def test_an_unrecorded_confidence_is_never_treated_as_bad(self):
        final = [cue(0.0, 2.0, 'Hello there my friend.', 0)]
        readings = {'context': final, 'precision': [cue(0.0, 2.0, 'Hello there my friend.', .9)]}
        self.assertEqual(asr_windows.reading_alternatives(final, readings), {})

    def test_boilerplate_and_unrelated_readings_are_not_offered(self):
        final = [cue(10.0, 12.0, 'I will be right back', .4)]
        readings = {'context': final,
                    'v2': [cue(10.0, 12.0, 'Thanks for watching!', .9)],
                    'precision': [cue(10.0, 12.0, 'completely different words here', .9)]}
        self.assertEqual(asr_windows.reading_alternatives(final, readings), {})

    def test_the_cues_are_not_changed(self):
        before = [dict(item) for item in FINAL]
        asr_windows.reading_alternatives(FINAL, READINGS)
        self.assertEqual(FINAL, before)


class DoubtfulCueWindowTests(unittest.TestCase):
    # Real cues 5 and 6 of job 49: confidence 0.35 and 0.22, only the short pass and Whisper v2 covered them, so
    # Qwen3-ASR never listened to the two least certain lines of the film.
    FINAL = [cue(25.1, 26.2, 'trouble we need your help', .958), cue(26.4, 27.5, 'Run! on, you niggas! Run!', .352),
             cue(27.6, 29.0, 'the f**k did I do that?', .218), cue(46.3, 47.9, 'You find me a tour guide yet?', .56),
             cue(50.0, 51.0, 'Come on, baby, get in.', .93), cue(60.0, 61.0, 'unrecorded confidence', 0)]
    READINGS = {'context': FINAL,
                'precision': [cue(26.4, 27.5, 'Come on, you niggas!', .35), cue(27.6, 29.0, 'the f**k did I do that?', .22)],
                'v2': [cue(26.6, 27.8, 'Run! on, you niggas! Run!', .55)]}

    def windows(self, **kwargs):
        return asr_windows.plan_consensus_windows(self.FINAL, self.READINGS, **kwargs)

    def test_every_doubtful_cue_gets_a_window_least_certain_first(self):
        windows = self.windows()
        starts = [window['start'] for window in windows]
        self.assertEqual(starts, [27.3, 26.1, 46.0], 'confidence .218, .352, .56 in that order')
        for window in windows:
            self.assertLessEqual(window['end'] - window['start'], 12)

    def test_confident_and_unrecorded_cues_get_no_window(self):
        for window in self.windows():
            self.assertFalse(window['start'] <= 25.1 <= window['end'] or window['start'] <= 50.0 <= window['end']
                             or window['start'] <= 60.0 <= window['end'])

    def test_the_number_of_extra_windows_is_capped(self):
        self.assertEqual(len(self.windows(maximum_doubtful=2)), 2)
        self.assertEqual(self.windows(maximum_doubtful=0), [])

    def test_a_cue_already_covered_by_a_planned_window_gets_no_second_one(self):
        # Four passes read the 100 s cue differently: the cluster window covers it, no extra window for the same cue.
        windows = asr_windows.plan_consensus_windows(FINAL, READINGS)
        around_100 = [window for window in windows if window['start'] < 104.8 and window['end'] > 100.2]
        self.assertLessEqual(len(around_100), 1)


if __name__ == '__main__':
    unittest.main()
