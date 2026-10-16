import math
import sys
import unittest

sys.path.insert(0, '/app')
import forced_reading as fr


def rows(items):
    """(start, end, average log-probability, text) as measured -> cues whose confidence is exp(log-probability)."""
    return [{'start': start, 'end': end, 'confidence': math.exp(logprob), 'text': text}
            for start, end, logprob, text in items]


# Real readings of the Tamil trailer Sigma with the language fixed to Tamil (benchmark on a rented RTX 3090,
# 2026-10-16). Large-v3 wrote Latin and Greek scraps among the Tamil; large-v2 read Tamil.
SIGMA_V3 = rows([
    (2.1, 3.9, -1.39, 'இன்னுக்கு நீ உயிரோடு இருக்கென்னு...'), (3.9, 4.6, -1.39, 'சந்தோஷபடு.'),
    (6.4, 8.9, -1.39, 'அவனங்க che meses,'), (10.8, 12.3, -1.39, 'நாம் ஒன்றும் முதலில், மோலமல் செய்யக்கூடாது.'),
    (12.3, 14.9, -1.39, 'ஏதாவது சுமetinய DR orbital அப்பா!'), (17.7, 22.2, -1.39, 'இது சூதும் போதைய chant'),
    (30.2, 31.2, -1.54, 'Omaha down'), (36.3, 37.4, -1.54, 'சாமரோதுbildச் சேmieரா?'),
    (62.8, 63.7, -1.53, 'descent of bunu'), (66.1, 68.7, -1.53, 'அவன் கலப்போகு புதித πάரியில் தμάசையாக உடுக்கிறார்'),
    (73.0, 81.0, -1.47, 'நம்sama் நோதிகம் சுதப்பில் downtown'), (92.1, 94.2, -0.56, 'இனிமேல், நீதான், அதைப் பார்த்துக்கொள்ள வையோம்.'),
    (94.2, 95.6, -0.56, 'நீ எல்லாம் வாழ்க்கையில் PROFESSOR'), (127.4, 129.8, -0.40, 'காணப்பை வத்து தரலாம்னு முடிவு பண்ணிருக்கேன்')])
SIGMA_V2 = rows([
    (2.1, 4.7, -0.27, 'இன்று நீ உயிரோட இருக்கேன்னு சந்தோஷப்படு'), (4.7, 5.7, -0.27, 'எதாவது ஆயிருந்தா'),
    (5.7, 9.4, -0.27, 'அவனங்க நம்ம காச அடிப்பானங்க'), (11.4, 12.4, -0.27, 'நம்ம ஒன்றுமே பண்ணக்கூடாது'),
    (15.6, 18.0, -0.27, 'அப்பா பயமா இருப்பா சொல்லிடுப்பா'), (34.1, 36.4, -0.59, 'இந்த ரேடியோல மோனிங் டைம் அல்லவா பேசுவாங்களே?'),
    (36.6, 37.4, -0.59, 'ஆர்ஜி சாம் தானே நீங்க?'), (37.4, 39.9, -0.40, 'Welcome back to Coffee with Sam'),
    (58.5, 60.0, -0.93, 'இப்ப நம்ம என்ன பணம் போறோம்?'), (84.0, 86.2, -0.21, 'ஒரு படைப்பை உருவாக்குறது எவ்வளவு முக்கியமோ'),
    (87.6, 90.5, -0.15, 'அதை விட அதை பாதுகாக்குறது ரொம்ப ரொம்ப முக்கியம்'),
    (92.3, 94.5, -0.18, 'இனிமேல் நீ தான் அதைப் பார்த்துக்கொள்ள வேண்டும்'), (114.2, 115.4, -0.38, 'ஏய் யார் நீ?'),
    (127.5, 129.9, -0.38, 'காணப்பத்தைத் தரலாம் என்று முடிவு பண்ணியிருக்கிறேன்')])
# The Korean trailer HOPE: both stock models read plausible Korean, large-v3 the more confident one.
HOPE_V3 = rows([(0.9, 5.5, -0.46, '며칠 전에 친구들이랑 사냥을 해나갔었어.'), (6.3, 16.2, -0.46, '삐꾸라지 마냥 삐끄러운 거를 20분쯤 걸었는데 갑자기 기증이.'),
                (25.6, 27.6, -0.51, '죄다 삿불 끈다고 빠져나가서'), (30.2, 32.0, -0.51, '대비군이라 봤자 8명인데요?')])
HOPE_V2 = rows([(0.9, 5.7, -0.59, '며칠 전에 친구들이랑 사냥을 나갔었어.'), (7.4, 10.1, -0.59, '삐꾸라지마냥 삐끄덩거려.'),
                (11.8, 16.3, -0.59, '20분쯤 걸었는데 갑자기 기침이.'), (30.6, 32.2, -0.53, '예비군을 해봤자 8명인데요?')])


class ContaminationTests(unittest.TestCase):
    def test_the_wrongly_decoded_reading_is_full_of_other_scripts_and_the_clean_one_is_not(self):
        self.assertGreaterEqual(fr.contaminated_share(SIGMA_V3), .5)
        self.assertLessEqual(fr.contaminated_share(SIGMA_V2), .1)
        self.assertEqual(fr.contaminated_share(HOPE_V3), 0.0, 'the digits "20" and "8" are not letters')

    def test_short_cues_and_empty_input_count_for_nothing(self):
        self.assertEqual(fr.contaminated_share([]), 0.0)
        self.assertEqual(fr.contaminated_share(rows([(0, 1, -.3, 'ok'), (1, 2, -.3, '!')])), 0.0)

    def test_the_dominant_script_decides_what_is_foreign(self):
        self.assertEqual(fr.dominant_script(SIGMA_V2), 'TAMIL')
        self.assertEqual(fr.dominant_script(HOPE_V3), 'HANGUL')
        english = rows([(0, 2, -.3, 'Hello there my friend'), (2, 4, -.3, 'How are you today')])
        self.assertEqual(fr.contaminated_share(english), 0.0)


class ChoiceTests(unittest.TestCase):
    def test_a_second_reading_is_asked_for_only_when_the_first_looks_unreliable(self):
        self.assertTrue(fr.needs_second_reading(SIGMA_V3))
        self.assertFalse(fr.needs_second_reading(HOPE_V3))
        self.assertFalse(fr.needs_second_reading(SIGMA_V2))
        self.assertFalse(fr.needs_second_reading([]))
        self.assertTrue(fr.needs_second_reading(rows([(0, 3, -1.2, 'நல்ல வார்த்தை'), (3, 6, -1.2, 'இன்னொரு வார்த்தை')])),
                        'a low-confidence reading is enough')

    def test_the_clearly_better_second_reading_wins_on_the_real_tamil_data(self):
        self.assertTrue(fr.prefer_second_reading(SIGMA_V3, SIGMA_V2))
        self.assertFalse(fr.prefer_second_reading(SIGMA_V2, SIGMA_V3))

    def test_on_the_korean_data_large_v3_stays(self):
        self.assertFalse(fr.prefer_second_reading(HOPE_V3, HOPE_V2))

    def test_a_second_reading_with_more_scraps_never_wins_even_with_high_confidence(self):
        scrappy = rows([(0, 3, -.05, 'நல்ல வார்த்தை'), (3, 6, -.05, 'foreign words here'), (6, 9, -.05, 'more english')])
        self.assertFalse(fr.prefer_second_reading(SIGMA_V2, scrappy))

    def test_a_missing_second_reading_keeps_the_first(self):
        self.assertFalse(fr.prefer_second_reading(SIGMA_V3, []))
        self.assertFalse(fr.prefer_second_reading(SIGMA_V3, None))

    def test_the_margin_keeps_near_ties_with_the_first_reading(self):
        near = [dict(row, confidence=row['confidence'] + .02) for row in HOPE_V3]
        self.assertFalse(fr.prefer_second_reading(HOPE_V3, near))

    def test_missing_confidences_count_as_zero_and_the_summary_is_plain_data(self):
        bare = [{'start': 0, 'end': 2, 'text': 'நல்ல வார்த்தை'}]
        self.assertEqual(fr.mean_confidence(bare), 0.0)
        summary = fr.reading_summary(SIGMA_V3)
        self.assertEqual(set(summary), {'cues', 'mean_confidence', 'contaminated_share'})
        self.assertEqual(summary['cues'], len(SIGMA_V3))


if __name__ == '__main__':
    unittest.main()
