"""Which embedded audio track the AI listens to.

A film often carries several audio tracks (original, dubs, commentary). The pipeline used to read the first
one blindly: the Man of War trailer has a German dub as track 0 and the English original as track 1, and the
dub's misheard words ("Meine nicht" for "Meine Nichte") failed the run. English is preferred automatically;
the language tag is only a hint and is checked against the audio, and an explicit choice always wins.
"""
from collections import Counter

PREFERRED_LANGUAGE = 'en'

# ISO 639-2/B and /T language tags (and plain two-letter codes) to the two-letter codes Whisper reports.
TAG_CODES = {
    'eng': 'en', 'en': 'en', 'ger': 'de', 'deu': 'de', 'de': 'de', 'fre': 'fr', 'fra': 'fr', 'fr': 'fr',
    'swe': 'sv', 'sv': 'sv', 'spa': 'es', 'es': 'es', 'ita': 'it', 'it': 'it', 'por': 'pt', 'pt': 'pt',
    'dan': 'da', 'da': 'da', 'nor': 'no', 'nob': 'no', 'nno': 'no', 'no': 'no', 'fin': 'fi', 'fi': 'fi',
    'dut': 'nl', 'nld': 'nl', 'nl': 'nl', 'pol': 'pl', 'pl': 'pl', 'rus': 'ru', 'ru': 'ru',
    'jpn': 'ja', 'ja': 'ja', 'kor': 'ko', 'ko': 'ko', 'chi': 'zh', 'zho': 'zh', 'zh': 'zh',
    'tur': 'tr', 'tr': 'tr', 'ara': 'ar', 'ar': 'ar', 'hin': 'hi', 'hi': 'hi', 'tam': 'ta', 'ta': 'ta',
    'cze': 'cs', 'ces': 'cs', 'cs': 'cs', 'hun': 'hu', 'hu': 'hu', 'gre': 'el', 'ell': 'el', 'el': 'el',
    'heb': 'he', 'he': 'he', 'ukr': 'uk', 'uk': 'uk', 'tha': 'th', 'th': 'th', 'vie': 'vi', 'vi': 'vi',
}

# Titles of tracks that are not the film's own dialogue.
SIDE_TRACK_WORDS = ('commentary', 'kommentar', 'commentaire', 'comentario', 'audio description',
                    'audiodescription', 'audiodeskription', 'audio-description', 'described video',
                    'descriptive', 'syntolk', 'isolated score', 'music only', 'karaoke')


def tag_code(tag):
    """The language a container tag claims, as a Whisper code; None for und/mul/missing/unknown tags."""
    return TAG_CODES.get(str(tag or '').strip().casefold())


def is_side_track(track):
    """Commentary or audio-description tracks are never the dialogue to subtitle."""
    if track.get('commentary') or track.get('visual_impaired'):
        return True
    title = str(track.get('title') or '').casefold()
    return any(word in title for word in SIDE_TRACK_WORDS)


def usable_audio_tracks(tracks):
    """The dialogue candidates; if every track looks like a side track, all of them stay candidates."""
    tracks = list(tracks)
    return [track for track in tracks if not is_side_track(track)] or tracks


def reading_language(readings, *, minimum_letters=8):
    """The language most of a track's speech windows agree on, or None when they are too few or mixed.

    A window counts only when it holds real speech with some actual letters; a hallucinated word over
    music must not decide the track's language."""
    counted = [reading['whisper_language'] for reading in readings
               if reading.get('whisper_language') and reading.get('speech_seconds', 0) >= .5
               and sum(char.isalpha() for char in str(reading.get('whisper_text', ''))) >= minimum_letters]
    if not counted:
        return None
    language, votes = Counter(counted).most_common(1)[0]
    return language if votes * 2 > len(counted) else None


def _result(track, method, heard, considered, *, stale_manual=False):
    return {'index': track['index'], 'language': tag_code(track.get('language')) or heard.get(track['index']),
            'method': method, 'stale_manual': stale_manual,
            'considered': [{'index': item['index'], 'tag': item.get('language') or 'und',
                            'heard': heard.get(item['index']), 'title': item.get('title') or ''}
                           for item in considered]}


def choose_audio_track(tracks, read_language, *, manual=None, preferred=PREFERRED_LANGUAGE,
                       maximum_untagged_readings=6):
    """Pick the track to transcribe. `read_language(track)` listens to a track and returns its language code
    (or None); it is called only when a file has several dialogue candidates, and never for a manual choice.

    method: 'manual' (user's choice), 'only' (one dialogue track), 'tag' (tagged English, not contradicted by
    the audio), 'audio' (untagged track heard to be English), 'default' / 'first' (no English: the file's
    default track, else the first one, which is what the pipeline always did before)."""
    tracks = list(tracks)
    if not tracks:
        return None
    heard = {}
    stale_manual = False
    if manual is not None:
        for track in tracks:
            if track['index'] == manual:
                return _result(track, 'manual', heard, tracks)
        stale_manual = True
    candidates = usable_audio_tracks(tracks)
    if len(candidates) == 1:
        return _result(candidates[0], 'only', heard, candidates, stale_manual=stale_manual)
    ordered = sorted(candidates, key=lambda track: (not track.get('default'), track['index']))

    def listen(track):
        if track['index'] not in heard:
            heard[track['index']] = read_language(track)
        return heard[track['index']]

    unverified = None
    for track in ordered:
        if tag_code(track.get('language')) != preferred:
            continue
        language = listen(track)
        if language == preferred:
            return _result(track, 'tag', heard, ordered, stale_manual=stale_manual)
        if language is None and unverified is None:
            unverified = track
    untagged = [track for track in ordered if tag_code(track.get('language')) is None]
    for track in untagged[:maximum_untagged_readings]:
        if listen(track) == preferred:
            return _result(track, 'audio', heard, ordered, stale_manual=stale_manual)
    if unverified is not None:
        return _result(unverified, 'tag', heard, ordered, stale_manual=stale_manual)
    fallback = ordered[0]
    return _result(fallback, 'default' if fallback.get('default') else 'first', heard, ordered,
                   stale_manual=stale_manual)
