import sys
import unittest

sys.path.insert(0, "/app")
from audio_tracks import (choose_audio_track, is_side_track, reading_language, tag_code,
                          usable_audio_tracks)


def track(index, language='und', title='', **flags):
    return {'index': index, 'language': language, 'title': title, 'codec': 'aac', 'channels': 2, **flags}


def listener(languages, calls=None):
    """A fake `read_language`: what each track index sounds like."""
    def read(item):
        if calls is not None:
            calls.append(item['index'])
        return languages.get(item['index'])
    return read


class TrackLanguageTests(unittest.TestCase):
    def test_container_tags_become_whisper_codes(self):
        self.assertEqual([tag_code(tag) for tag in ('eng', 'ENG', 'en', 'ger', 'deu', 'swe', 'fre')],
                         ['en', 'en', 'en', 'de', 'de', 'sv', 'fr'])
        for tag in ('und', '', None, 'mul', 'xyz'):
            self.assertIsNone(tag_code(tag))

    def test_readings_need_real_speech_and_a_majority(self):
        def reading(language, letters=20, speech=3.0):
            return {'whisper_language': language, 'whisper_text': 'a' * letters, 'speech_seconds': speech}
        self.assertEqual(reading_language([reading('en'), reading('en'), reading('de')]), 'en')
        self.assertIsNone(reading_language([reading('en'), reading('de')]))
        self.assertEqual(reading_language([reading('de')]), 'de')
        # A word over music, or a window without speech, is not evidence.
        self.assertIsNone(reading_language([reading('en', letters=3), reading('de', speech=.2)]))
        self.assertIsNone(reading_language([]))


class SideTrackTests(unittest.TestCase):
    def test_commentary_and_description_tracks_are_not_dialogue(self):
        self.assertTrue(is_side_track(track(2, 'eng', "Director's Commentary")))
        self.assertTrue(is_side_track(track(2, 'eng', 'Audiodeskription')))
        self.assertTrue(is_side_track(track(3, 'eng', commentary=True)))
        self.assertTrue(is_side_track(track(3, 'eng', visual_impaired=True)))
        self.assertFalse(is_side_track(track(1, 'eng', "Director's Cut 5.1")))

    def test_all_side_tracks_stay_candidates_instead_of_none(self):
        only = [track(1, 'eng', 'Commentary')]
        self.assertEqual(usable_audio_tracks(only), only)


class ChooseTrackTests(unittest.TestCase):
    def test_man_of_war_german_dub_first_untagged_picks_the_english_original(self):
        # The real file: two stereo AAC tracks, both tagged und; track 0 sounds German, track 1 English.
        tracks = [track(0), track(1)]
        choice = choose_audio_track(tracks, listener({0: 'de', 1: 'en'}))
        self.assertEqual((choice['index'], choice['method'], choice['language']), (1, 'audio', 'en'))
        self.assertEqual([item['heard'] for item in choice['considered']], ['de', 'en'])

    def test_english_tag_is_checked_against_the_audio(self):
        tracks = [track(0, 'ger'), track(1, 'eng')]
        calls = []
        choice = choose_audio_track(tracks, listener({1: 'en'}, calls))
        self.assertEqual((choice['index'], choice['method']), (1, 'tag'))
        self.assertEqual(calls, [1], 'only the English-tagged track is listened to')

    def test_mislabelled_english_tag_is_not_trusted(self):
        # Track 1 claims English but is German; the untagged track 2 really is English.
        tracks = [track(0, 'ger'), track(1, 'eng'), track(2)]
        choice = choose_audio_track(tracks, listener({1: 'de', 2: 'en'}))
        self.assertEqual((choice['index'], choice['method']), (2, 'audio'))

    def test_english_tag_without_audible_speech_still_wins_over_guessing(self):
        tracks = [track(0, 'ger'), track(1, 'eng')]
        choice = choose_audio_track(tracks, listener({}))
        self.assertEqual((choice['index'], choice['method']), (1, 'tag'))

    def test_no_english_falls_back_to_the_default_track_then_the_first(self):
        tracks = [track(0, 'ger'), track(1, 'fre', default=True)]
        choice = choose_audio_track(tracks, listener({}))
        self.assertEqual((choice['index'], choice['method'], choice['language']), (1, 'default', 'fr'))
        choice = choose_audio_track([track(0, 'ger'), track(1, 'fre')], listener({}))
        self.assertEqual((choice['index'], choice['method']), (0, 'first'))

    def test_tracks_with_a_clear_other_language_tag_are_not_listened_to(self):
        calls = []
        choose_audio_track([track(0, 'ger'), track(1, 'fre'), track(2)], listener({2: 'en'}, calls))
        self.assertEqual(calls, [2])

    def test_commentary_is_skipped_and_a_single_dialogue_track_needs_no_listening(self):
        tracks = [track(0, 'eng', 'Commentary'), track(1, 'ger')]
        calls = []
        choice = choose_audio_track(tracks, listener({}, calls))
        self.assertEqual((choice['index'], choice['method']), (1, 'only'))
        self.assertEqual(calls, [])

    def test_single_track_keeps_working_without_listening(self):
        calls = []
        choice = choose_audio_track([track(0)], listener({}, calls))
        self.assertEqual((choice['index'], choice['method']), (0, 'only'))
        self.assertEqual(calls, [])

    def test_default_flagged_english_track_comes_first_among_several(self):
        tracks = [track(0, 'eng'), track(1, 'eng', default=True)]
        choice = choose_audio_track(tracks, listener({0: 'en', 1: 'en'}))
        self.assertEqual(choice['index'], 1)

    def test_manual_choice_wins_and_is_never_listened_to(self):
        calls = []
        choice = choose_audio_track([track(0), track(1, 'eng')], listener({1: 'en'}, calls), manual=0)
        self.assertEqual((choice['index'], choice['method']), (0, 'manual'))
        self.assertEqual(calls, [])

    def test_manual_choice_may_be_a_commentary_track(self):
        choice = choose_audio_track([track(0, 'eng'), track(1, 'eng', 'Commentary')], listener({}), manual=1)
        self.assertEqual((choice['index'], choice['method']), (1, 'manual'))

    def test_a_manual_choice_that_no_longer_exists_falls_back_to_automatic_and_says_so(self):
        choice = choose_audio_track([track(0, 'ger'), track(1, 'eng')], listener({1: 'en'}), manual=7)
        self.assertEqual((choice['index'], choice['method'], choice['stale_manual']), (1, 'tag', True))

    def test_no_audio_tracks_gives_no_choice(self):
        self.assertIsNone(choose_audio_track([], listener({})))

    def test_number_of_untagged_readings_is_capped(self):
        tracks = [track(index) for index in range(10)]
        calls = []
        choice = choose_audio_track(tracks, listener({}, calls), maximum_untagged_readings=4)
        self.assertEqual(len(calls), 4)
        self.assertEqual((choice['index'], choice['method']), (0, 'first'))


class PreferenceOrderTests(unittest.TestCase):
    """Swedish first (no translation needed), then English; several tracks of one language are ranked."""

    def test_a_swedish_track_beats_an_english_one(self):
        tracks = [track(0, 'eng'), track(1, 'swe')]
        choice = choose_audio_track(tracks, listener({0: 'en', 1: 'sv'}))
        self.assertEqual((choice['index'], choice['method'], choice['language']), (1, 'tag', 'sv'))

    def test_an_untagged_track_heard_as_swedish_beats_a_verified_english_tag(self):
        tracks = [track(0, 'eng'), track(1), track(2, 'ger')]
        calls = []
        choice = choose_audio_track(tracks, listener({0: 'en', 1: 'sv'}, calls))
        self.assertEqual((choice['index'], choice['method'], choice['language']), (1, 'audio', 'sv'))
        self.assertNotIn(2, calls, 'a German tag is not listened to')

    def test_a_swedish_tag_the_audio_contradicts_is_not_trusted(self):
        tracks = [track(0, 'swe'), track(1, 'eng')]
        choice = choose_audio_track(tracks, listener({0: 'en', 1: 'en'}))
        self.assertEqual((choice['index'], choice['language']), (1, 'en'))

    def test_a_silent_swedish_tag_does_not_beat_audible_english(self):
        tracks = [track(0, 'swe'), track(1, 'eng')]
        choice = choose_audio_track(tracks, listener({1: 'en'}))
        self.assertEqual((choice['index'], choice['method']), (1, 'tag'))
        self.assertEqual(choose_audio_track(tracks, listener({}))['index'], 0, 'with nothing audible the tags decide')

    def test_swedish_and_english_commentary_tracks_are_never_chosen(self):
        tracks = [track(0, 'swe', 'Kommentar'), track(1, 'eng', 'Commentary'), track(2, 'ger'), track(3, 'eng')]
        choice = choose_audio_track(tracks, listener({3: 'en'}))
        self.assertEqual((choice['index'], choice['language']), (3, 'en'))

    def test_a_regular_and_a_commentary_english_track_picks_the_regular_one(self):
        tracks = [track(0, 'eng', "Director's Commentary"), track(1, 'eng', 'English 5.1', channels=6)]
        self.assertEqual(choose_audio_track(tracks, listener({1: 'en'}))['index'], 1)

    def test_an_unmarked_stereo_commentary_loses_to_the_surround_mix(self):
        tracks = [track(0, 'eng', channels=2), track(1, 'eng', channels=6)]
        self.assertEqual(choose_audio_track(tracks, listener({0: 'en', 1: 'en'}))['index'], 1)

    def test_equal_english_tracks_keep_the_first_and_the_default_flag_beats_channels(self):
        tracks = [track(0, 'eng'), track(1, 'eng')]
        self.assertEqual(choose_audio_track(tracks, listener({0: 'en', 1: 'en'}))['index'], 0)
        tracks = [track(0, 'eng', channels=6), track(1, 'eng', channels=2, default=True)]
        self.assertEqual(choose_audio_track(tracks, listener({0: 'en', 1: 'en'}))['index'], 1)

    def test_the_fallback_without_swedish_or_english_is_still_the_first_track_not_the_widest(self):
        tracks = [track(0, 'ger', channels=2), track(1, 'fre', channels=6)]
        choice = choose_audio_track(tracks, listener({}))
        self.assertEqual((choice['index'], choice['method']), (0, 'first'))

    def test_the_real_stuart_fails_to_save_the_universe_tracks_pick_english_over_default_french(self):
        tracks = [track(1, 'fre', 'French', default=True), track(2, 'eng', 'English (US)')]
        choice = choose_audio_track(tracks, listener({1: 'fr', 2: 'en'}))
        self.assertEqual((choice['index'], choice['method']), (2, 'tag'))


if __name__ == '__main__':
    unittest.main()
