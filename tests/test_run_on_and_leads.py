import sys
import unittest

sys.path.insert(0, '/ai')
from asr_windows import prefer_precision_sentence_boundaries, reattach_dangling_leads


def cue(start, end, text, confidence=.9):
    return {'start': start, 'end': end, 'text': text, 'confidence': confidence}


class PunctuatedRunOnTests(unittest.TestCase):
    """Real job 18 (Ray Gunn 1:52): the long pass wrote one unpunctuated run-on, the short pass heard
    four punctuated sentences with almost no pause between them."""

    MERGED = [cue(108.40, 109.10, 'hard being', 1.0),
              cue(109.10, 114.84, 'the only honest players in a crooked game ray jump did you go and do the', .85),
              cue(114.84, 116.00, 'right thing again', .5)]
    PRECISION = [cue(108.36, 111.46, 'hard being the only honest players in a crooked game.', .93),
                 cue(111.50, 111.88, 'Ray!', .98),
                 cue(111.88, 112.58, 'Jump!', .97),
                 cue(113.28, 115.97, 'Did you go and do the right thing again?', .84)]

    def test_confident_punctuated_sentences_replace_the_run_on(self):
        result = prefer_precision_sentence_boundaries(self.MERGED, self.PRECISION)
        self.assertEqual([row['text'] for row in result],
                         ['hard being the only honest players in a crooked game.', 'Ray! Jump!',
                          'Did you go and do the right thing again?'])
        self.assertAlmostEqual(result[1]['start'], 111.50)
        self.assertAlmostEqual(result[1]['end'], 112.58)

    def test_an_unsure_cue_keeps_the_original(self):
        weak = [dict(row, confidence=.5) if row['text'] == 'Ray!' else row for row in self.PRECISION]
        self.assertEqual(prefer_precision_sentence_boundaries(self.MERGED, weak), self.MERGED)

    def test_a_long_pass_that_already_has_sentence_ends_is_left_alone(self):
        punctuated = [dict(row, text=row['text'] + '.') for row in self.MERGED]
        self.assertEqual(prefer_precision_sentence_boundaries(punctuated, self.PRECISION), punctuated)

    def test_different_words_never_win_even_if_punctuated(self):
        other = [cue(108.36, 111.46, 'Something else entirely is said here today.', .95),
                 cue(111.50, 111.88, 'Totally!', .95), cue(111.88, 112.58, 'Different!', .95),
                 cue(113.28, 115.97, 'Why would anyone invent this?', .95)]
        self.assertEqual(prefer_precision_sentence_boundaries(self.MERGED, other), self.MERGED)


class OrphanSubjectLeadTests(unittest.TestCase):
    """Real job 18 (Ray Gunn 2:08): "I" was placed 2.8 s before "like you, Gun."; the isolated-word
    filters then removed it and the sentence lost its subject."""

    ROWS = [cue(123.10, 123.90, 'Stay tuned.'), cue(124.28, 125.00, 'I'), cue(127.79, 128.89, 'like you, Gun.')]

    def test_a_lone_subject_joins_a_lowercase_continuation_even_at_2_8_s(self):
        result = reattach_dangling_leads(self.ROWS, minimum_gap=.6, subjects_before_lowercase_only=True)
        self.assertEqual([row['text'] for row in result], ['Stay tuned.', 'I like you, Gun.'])
        self.assertLess(result[1]['start'], 127.79)
        self.assertGreater(result[1]['start'], 127.5)   # timing follows the words after the pause

    def test_a_capitalised_next_cue_is_a_new_sentence_so_nothing_joins(self):
        rows = [cue(1, 1.5, 'I'), cue(4, 5, 'Like you, Gun.')]
        self.assertEqual(reattach_dangling_leads(rows, minimum_gap=.6, subjects_before_lowercase_only=True), rows)

    def test_only_subject_pronouns_use_the_early_form(self):
        rows = [cue(1, 1.5, 'and'), cue(4, 5, 'then we left.')]
        self.assertEqual(reattach_dangling_leads(rows, minimum_gap=.6, subjects_before_lowercase_only=True), rows)

    def test_a_sentence_that_already_ended_is_not_a_lead(self):
        rows = [cue(1, 1.5, 'I.'), cue(4, 5, 'like you, Gun.')]
        self.assertEqual(reattach_dangling_leads(rows, minimum_gap=.6, subjects_before_lowercase_only=True), rows)

    def test_the_default_behaviour_is_unchanged(self):
        # gap 2.79 s < 3.0 s: the old call does not move it
        self.assertEqual(reattach_dangling_leads(self.ROWS), self.ROWS)


if __name__ == '__main__':
    unittest.main()


class RunOnWithTrailingLeadTests(unittest.TestCase):
    """The real passage also had the first word of the NEXT sentence ("will") as a fifth short-pass
    cue without sentence end; it must not stop the split."""

    MERGED = PunctuatedRunOnTests.MERGED + [cue(116.00, 116.96, 'will', .87)]
    PRECISION = PunctuatedRunOnTests.PRECISION + [cue(115.97, 117.12, 'will', .97)]

    def test_the_last_unpunctuated_cue_does_not_block_the_split(self):
        result = prefer_precision_sentence_boundaries(self.MERGED, self.PRECISION)
        self.assertEqual([row['text'] for row in result],
                         ['hard being the only honest players in a crooked game.', 'Ray! Jump!',
                          'Did you go and do the right thing again?', 'will'])

    def test_an_unpunctuated_cue_in_the_middle_still_blocks_it(self):
        broken = [dict(row, text='Ray') if row['text'] == 'Ray!' else row for row in self.PRECISION]
        self.assertEqual(prefer_precision_sentence_boundaries(self.MERGED, broken), self.MERGED)


class StretchedQwenWordTests(unittest.TestCase):
    """Real Ray Gunn run: Qwen's aligner gave "I" the span 0.0-4.0 s (a silence) before "like"."""

    def test_a_word_stretched_over_a_silence_is_kept_not_dropped(self):
        from asr_windows import split_qwen_alignment
        items = [{'text': 'I', 'start': 0.0, 'end': 4.0}, {'text': 'like', 'start': 4.0, 'end': 4.4},
                 {'text': 'you', 'start': 4.4, 'end': 4.72}, {'text': 'gun', 'start': 4.72, 'end': 5.12}]
        cues = split_qwen_alignment(items, offset=124.0)
        self.assertEqual([cue['text'] for cue in cues], ['I like you gun'])
        self.assertAlmostEqual(cues[0]['start'], 127.6 - .06, places=1)   # next to "like", not at 124 s

    def test_a_stretched_word_after_speech_stays_at_its_start(self):
        from asr_windows import split_qwen_alignment
        items = [{'text': 'Ray', 'start': 1.0, 'end': 1.4}, {'text': 'jump', 'start': 1.5, 'end': 5.5}]
        cues = split_qwen_alignment(items)
        self.assertEqual(cues[0]['text'], 'Ray jump')
        self.assertLess(cues[0]['end'], 2.5)


class FallbackReasonTests(unittest.TestCase):
    def test_the_rejection_reason_is_saved_with_the_source_transcript(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import worker
        job = {'id': 5, 'fallback_reason': 'large-v3 underkändes: Flera rader slutar mitt i en mening.'}
        rows = [{'id': 0, 'start': 0, 'end': 1, 'source': 'Hello there.'}]
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            target = worker.save_source_transcript(job, '/x/a.mp4', rows, 'cpu', 'Whisper large-v2')
            saved = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(saved['fallback_reason'], job['fallback_reason'])

    def test_no_reason_is_saved_as_none(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import worker
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'SOURCE_TRANSCRIPTS', Path(folder)):
            target = worker.save_source_transcript({'id': 6}, '/x/a.mp4', [], 'cpu', 'Whisper large-v3')
            self.assertIsNone(json.loads(target.read_text(encoding='utf-8'))['fallback_reason'])
