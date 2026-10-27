"""Which embedded audio track the AI listens to.

A film often carries several audio tracks (original, dubs, commentary). The pipeline used to read the first
one blindly: the Man of War trailer has a German dub as track 0 and the English original as track 1, and the
dub's misheard words ("Meine nicht" for "Meine Nichte") failed the run. Swedish is preferred (it needs no
translation), then English; the language tag is only a hint and is checked against the audio, and an explicit
choice always wins.
"""
from collections import Counter

PREFERRED_LANGUAGES = ('sv', 'en')    # Swedish needs no translation; otherwise the English original

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


def window_language(choice, start, end, *, minimum_coverage=.8):
    """Use heard language only where a selected track's probes cover this window.

    Mixed or uncovered regions stay automatic; no whole-file majority vote.
    """
    if not isinstance(choice, dict) or end <= start:
        return None
    readings = choice.get('readings') or {}
    if not isinstance(readings, dict):
        return None
    index = choice.get('index')
    probes = readings.get(index, readings.get(str(index), []))
    spans, languages = [], set()
    for probe in probes:
        if (not probe.get('whisper_language') or probe.get('speech_seconds', 0) < .5
                or sum(c.isalpha() for c in str(probe.get('whisper_text', ''))) < 8):
            continue
        a, b = max(start, float(probe.get('start', 0))), min(end, float(probe.get('end', 0)))
        if b > a:
            spans.append((a, b))
            languages.add(probe['whisper_language'])
    if len(languages) != 1:
        return None
    covered, last = 0.0, start
    for a, b in sorted(spans):
        covered += max(0.0, b - max(a, last))
        last = max(last, b)
    return next(iter(languages)) if covered >= minimum_coverage * (end - start) else None


def _result(track, method, heard, considered, *, stale_manual=False):
    return {'index': track['index'], 'language': tag_code(track.get('language')) or heard.get(track['index']),
            'method': method, 'stale_manual': stale_manual,
            'considered': [{'index': item['index'], 'tag': item.get('language') or 'und',
                            'heard': heard.get(item['index']), 'title': item.get('title') or ''}
                           for item in considered]}


def choose_audio_track(tracks, read_language, *, manual=None, preferred=PREFERRED_LANGUAGES,
                       maximum_untagged_readings=6):
    """Pick the track to transcribe. `read_language(track)` listens to a track and returns its language code
    (or None); it is called only when a file has several dialogue candidates, and never for a manual choice.

    The languages in `preferred` are tried in order (Swedish, then English). A track tagged with a preferred
    language is checked against the audio, and tracks with no usable tag are listened to, so that a Swedish
    track hiding behind `und` is found even when an English track is already known. Between several tracks of
    the same language the file's default track wins, then the one with more channels (a main mix over an
    unmarked stereo commentary), then the first.

    method: 'manual' (user's choice), 'only' (one dialogue track), 'tag' (tagged with the language, not
    contradicted by the audio), 'audio' (untagged track heard to be that language), 'default' / 'first' (no
    preferred language: the file's default track, else the first one, which is what the pipeline always did)."""
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
    ordered = sorted(candidates, key=lambda track: (not track.get('default'), -(track.get('channels') or 0),
                                                    track['index']))

    def listen(track):
        if track['index'] not in heard:
            heard[track['index']] = read_language(track)
        return heard[track['index']]

    confirmed = {}    # language -> first track the audio confirms (a matching tag, or an untagged track heard so)
    tag_only = {}     # language -> first matching-tag track with no audible speech to check it against
    for language in preferred:
        for track in ordered:
            if tag_code(track.get('language')) != language:
                continue
            spoken = listen(track)
            if spoken == language:
                confirmed.setdefault(language, (track, 'tag'))
                break
            if spoken is None:
                tag_only.setdefault(language, track)
        if preferred[0] in confirmed:
            break
    if preferred[0] not in confirmed:
        for track in [item for item in ordered if tag_code(item.get('language')) is None][:maximum_untagged_readings]:
            spoken = listen(track)
            if spoken in preferred and spoken not in confirmed:
                confirmed[spoken] = (track, 'audio')
                if spoken == preferred[0]:
                    break
    for language in preferred:
        if language in confirmed:
            track, method = confirmed[language]
            return _result(track, method, heard, ordered, stale_manual=stale_manual)
    for language in preferred:
        if language in tag_only:
            return _result(tag_only[language], 'tag', heard, ordered, stale_manual=stale_manual)
    fallback = min(candidates, key=lambda track: (not track.get('default'), track['index']))
    return _result(fallback, 'default' if fallback.get('default') else 'first', heard, ordered,
                   stale_manual=stale_manual)
