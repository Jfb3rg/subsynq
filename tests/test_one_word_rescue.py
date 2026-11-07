import sys
import unittest

sys.path[:0] = ['/app', '/ai']
from asr_windows import reading_alternatives


def cue(start, end, text, confidence):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


# A real case from saved jobs: the long pass kept "Yes, sir, I'm three."; the rescue pass heard "Yes, sir!" and "On three?" more confidently.
FINAL = [cue(1800.0, 1802.18, "Yes, sir, I'm three.", .7177), cue(1802.44, 1805.92, 'All right, here we go one two', .6801)]
READINGS = {'context': [cue(1800.0, 1802.18, "Yes, sir, I'm three.", .7177)],
            'rescue': [cue(1799.88, 1800.81, 'Yes, sir!', .791), cue(1801.22, 1802.2, 'On three?', .7666)]}


class OneWordRescueTests(unittest.TestCase):
    def test_a_cue_the_rescue_pass_heard_with_one_other_word_more_confidently_is_doubtful(self):
        alternatives = reading_alternatives(FINAL, READINGS)
        self.assertIn(0, alternatives)
        self.assertIn('Yes, sir! On three?', [item['text'] for item in alternatives[0]])

    def test_the_same_words_or_a_less_confident_rescue_leave_it(self):
        for rescue in ([cue(1799.88, 1802.2, "Yes, sir, I'm three!", .9)],
                       [cue(1799.88, 1802.2, 'Yes, sir! On three?', .7)]):
            self.assertNotIn(0, reading_alternatives(FINAL, {'context': READINGS['context'], 'rescue': rescue}))

    def test_only_the_rescue_pass_counts(self):
        self.assertNotIn(0, reading_alternatives(FINAL, {'context': READINGS['context'],
                                                         'precision': [cue(1799.88, 1802.2, 'Yes, sir, on three.', .9)]}))


if __name__ == '__main__':
    unittest.main()
