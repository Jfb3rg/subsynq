import sys
import unittest

sys.path[:0] = ['/app', '/ai']
from sentence_cuts import evidence_words, move_cut, move_cut_to_phrase, move_cuts_to_sentence_ends


def timed(*items, profile='precision'):
    return [{'profile': profile, 'words': [{'text': f' {w}', 'start': a, 'end': b} for w, a, b in items]}]


# regression case(Example Series 4x02, Main 0.8.364), rows 12/13 and the large-v3 words around their boundary.
ROWS = [{'id': 12, 'start': 138.83, 'end': 139.89, 'source': 'Of here, how could I possibly'},
        {'id': 13, 'start': 139.89, 'end': 143.97, 'source': 'expect you to understand? Can you back me up here, please? Tell her what is this about.'}]
EVIDENCE = timed(('possibly', 139.5, 139.89), ('expect', 139.89, 140.2), ('you', 140.2, 140.3), ('to', 140.3, 140.4),
                 ('understand?', 140.4, 140.84), ('Can', 141.0, 141.2), ('you', 141.2, 141.3))


class SentenceCutTests(unittest.TestCase):
    def test_a_boundary_inside_a_sentence_moves_to_the_sentence_end_and_keeps_every_word(self):
        rows, proofs = move_cuts_to_sentence_ends(ROWS, evidence_words(EVIDENCE))
        self.assertEqual([row['source'] for row in rows],
                         ['Of here, how could I possibly expect you to understand?', 'Can you back me up here, please? Tell her what is this about.'])
        self.assertEqual((rows[0]['end'], rows[1]['start']), (140.84, 141.0))
        self.assertEqual((rows[0]['start'], rows[1]['end']), (138.83, 143.97))
        self.assertEqual(' '.join(row['source'] for row in rows).split(), ' '.join(row['source'] for row in ROWS).split())
        self.assertEqual(proofs[0]['moved_words'], 4)

    def test_no_timed_word_or_disagreeing_passes_leave_the_boundary(self):
        self.assertIsNone(move_cut(*ROWS, []))
        other = timed(('to', 141.3, 141.4), ('understand?', 141.4, 141.9), ('Can', 142.0, 142.2), ('you', 142.2, 142.3), profile='v2')
        self.assertIsNone(move_cut(*ROWS, evidence_words(EVIDENCE + other)))

    def test_a_finished_sentence_a_capital_or_a_pause_leaves_the_boundary(self):
        words = evidence_words(EVIDENCE)
        self.assertIsNone(move_cut(dict(ROWS[0], source='Of here, how could I possibly.'), ROWS[1], words))
        self.assertIsNone(move_cut(ROWS[0], dict(ROWS[1], source='Expect you to understand? Can you back me up here?'), words))
        self.assertIsNone(move_cut(ROWS[0], dict(ROWS[1], start=140.6), words))

    def test_a_sentence_end_too_far_away_leaves_the_boundary(self):
        right = dict(ROWS[1], source='expect you to really truly and honestly understand? Can you?')
        self.assertIsNone(move_cut(ROWS[0], right, evidence_words(EVIDENCE)))

    def test_another_occurrence_of_the_same_words_elsewhere_is_not_taken_for_them(self):
        # A second "understand? Can" later in the same pass, in other words, must not count as a disagreeing time.
        elsewhere = timed(('never', 142.0, 142.2), ('understand?', 142.2, 142.6), ('Can', 142.7, 142.9), ('we', 142.9, 143.0), profile='v2')
        rows, _ = move_cuts_to_sentence_ends(ROWS, evidence_words(EVIDENCE + elsewhere))
        self.assertEqual((rows[0]['end'], rows[1]['start']), (140.84, 141.0))

    def test_a_confirmed_onset_protects_only_the_start_of_its_row(self):
        moved, _ = move_cuts_to_sentence_ends([dict(ROWS[0], confirmed_lead_onset={'prefix': 'of here'}), ROWS[1]], evidence_words(EVIDENCE))
        self.assertEqual(moved[0]['source'], 'Of here, how could I possibly expect you to understand?')
        kept, _ = move_cuts_to_sentence_ends([ROWS[0], dict(ROWS[1], confirmed_lead_onset={'prefix': 'expect'})], evidence_words(EVIDENCE))
        self.assertEqual(kept[0]['source'], ROWS[0]['source'])

    def test_a_row_confirmed_word_by_word_is_left_alone(self):
        rows = [dict(ROWS[0], confirmed_source_words=['possibly']), ROWS[1]]
        self.assertEqual(move_cuts_to_sentence_ends(rows, evidence_words(EVIDENCE))[1], [])



# regression case(Example Series 4x02), rows 276/277 ("… He" / "already had the" / "immunity, and you …") and Whisper's words around the boundary.
IMMUNITY = [{'id': 277, 'start': 1037.44, 'end': 1037.96, 'source': 'already had the'},
            {'id': 278, 'start': 1037.96, 'end': 1043.6, 'source': 'immunity, and you because you were in a coma. What about you? Well,'}]
IMMUNITY_WORDS = timed(('the', 1037.8, 1037.96), ('immunity,', 1037.96, 1038.6), ('and', 1038.7, 1038.9), ('you', 1038.9, 1039.0))


class PhraseCutTests(unittest.TestCase):
    """A long sentence over two rows is cut where a phrase ends, never after a word that belongs to the next one ("the", "and", "of" …)."""

    def test_a_row_ending_on_an_article_moves_its_cut_to_the_comma(self):
        left, right, proof = move_cut_to_phrase(*IMMUNITY, evidence_words(IMMUNITY_WORDS))
        self.assertEqual((left['source'], right['source']), ('already had the immunity,', 'and you because you were in a coma. What about you? Well,'))
        self.assertEqual((left['end'], right['start']), (1038.6, 1038.7))
        self.assertEqual(proof['kind'], 'phrase')

    def test_a_cut_before_a_conjunction_or_after_a_comma_is_left_alone(self):
        words = evidence_words(IMMUNITY_WORDS)
        self.assertIsNone(move_cut_to_phrase(dict(IMMUNITY[0], source='already had the immunity,'),
                                             dict(IMMUNITY[1], source='and you because you were in a coma.'), words))
        self.assertIsNone(move_cut_to_phrase(dict(IMMUNITY[0], source="Now, just think how excited your troop is going to be"),
                                             dict(IMMUNITY[1], source='when you roll up in a brand-new Range Rover.'), words))

    def test_never_to_a_preposition_and_never_a_one_word_row(self):
        rows = [{'id': 1, 'start': 0.0, 'end': 2.0, 'source': 'he ordered some wine and I'}, {'id': 2, 'start': 2.0, 'end': 3.0, 'source': 'want to'}]
        words = evidence_words(timed(('he', 0.0, 0.2), ('ordered', 0.2, 0.6), ('some', 0.6, 1.0), ('wine', 1.0, 1.4), ('and', 1.5, 1.6), ('i', 1.6, 1.8),
                                     ('want', 2.0, 2.3), ('to', 2.3, 2.5)))
        left, right, _ = move_cut_to_phrase(*rows, words)
        self.assertEqual((left['source'], right['source']), ('he ordered some wine', 'and I want to'))
        rows = [{'id': 1, 'start': 0.0, 'end': 2.0, 'source': 'bring it to the'}, {'id': 2, 'start': 2.0, 'end': 3.0, 'source': 'door for me'}]
        self.assertIsNone(move_cut_to_phrase(*rows, evidence_words(timed(('the', 1.6, 2.0), ('door', 2.0, 2.4)))))


if __name__ == '__main__':
    unittest.main()
