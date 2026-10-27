"""Recover an existing ASR word lost solely because its alignment had no length.

Two raw passes must contain the same collapsed word between the same anchors.
They only plan a bounded v2 check. Insertion requires a positive, confident v2
word interval and both anchors at the same time; no times or words are guessed.
"""
import math
import re

from asr_windows import _normalized


def _timed(word, minimum=.55):
    try:
        start, end, probability = (float(word[key]) for key in ('start', 'end', 'probability'))
        return (all(math.isfinite(v) for v in (start, end, probability))
                and 0 < end - start <= 1.5 and minimum <= probability <= 1)
    except (KeyError, TypeError, ValueError):
        return False


def collapsed_word_evidence(profiles):
    found = []
    for profile in profiles:
        if profile.get('no_speech_prob', 0) > .8 or profile.get('avg_logprob', 0) < -1:
            continue
        words = profile.get('words') or []
        for before, word, after in zip(words, words[1:], words[2:]):
            try:
                point = float(word['start'])
                if (not math.isfinite(point) or point < 0 or point != float(word['end'])
                        or not .8 <= float(word['probability']) <= 1
                        or not _timed(before) or not _timed(after)
                        or abs(before['end'] - point) > .02
                        or not 0 <= after['start'] - point <= .5):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            key = tuple(_normalized(str(w.get('text', ''))) for w in (before, word, after))
            if not all(key) or len(key[1].split()) != 1:
                continue
            match = next((item for item in found if item['key'] == key and abs(item['point'] - point) <= .25), None)
            if match is None:
                match = {'key': key, 'point': point, 'profiles': set(), 'text': word['text'].strip()}
                found.append(match)
            match['profiles'].add(str(profile.get('profile', '')))
    return [item for item in found if len(item['profiles'] - {''}) >= 2]


def _slots(row, item):
    if not row['start'] - .25 <= item['point'] <= row['end']:
        return []
    tokens = list(re.finditer(r'\S+', str(row.get('text', ''))))
    before, _, after = item['key']
    return [right.start() for left, right in zip(tokens, tokens[1:])
            if _normalized(left.group()) == before and _normalized(right.group()) == after]


def plan_collapsed_word_checks(profiles, duration, *, merged=None, maximum_total=8):
    windows = []
    if maximum_total <= 0:
        return windows
    for item in sorted(collapsed_word_evidence(profiles), key=lambda item: item['point']):
        if merged is not None and not any(len(_slots(row, item)) == 1 for row in merged):
            continue
        start = max(0., float(math.floor(item['point'] - 4)))
        end = min(float(duration), start + 17.)
        if end - start < 8 or item['point'] >= end - .5:
            continue
        if any(min(end, old['end']) - max(start, old['start']) > 8 for old in windows):
            continue
        windows.append({'start': start, 'end': end})
        if len(windows) >= maximum_total:
            break
    return windows


def restore_collapsed_words(rows, profiles, v2_rows, *, skip_indices=()):
    restored, changes = [dict(row) for row in rows], []
    # An overlapping v2 window may itself lose the valid word during merging.
    # Keep the actual timed v2 evidence, not just the merged cue rendering.
    independent = list(v2_rows) + [profile for profile in profiles
                                  if profile.get('profile') in ('Whisper v2 kontrollerar ordfragment', 'Whisper v2 kontrollerar nollånga ord')
                                  and profile.get('no_speech_prob', 0) <= .8
                                  and profile.get('avg_logprob', 0) >= -1]
    for item in collapsed_word_evidence(profiles):
        before, missing, after = item['key']
        witnesses = []
        for reading in independent:
            words = reading.get('words') or []
            for left, middle, right in zip(words, words[1:], words[2:]):
                if (tuple(_normalized(str(w.get('text', ''))) for w in (left, middle, right)) != item['key']
                        or not _timed(left) or not _timed(middle, .8) or not _timed(right)):
                    continue
                if (abs(middle['start'] - item['point']) <= .25
                        and abs(middle['end'] - item['point']) <= .5
                        and left['end'] <= middle['start'] and middle['end'] <= right['start']):
                    witnesses.append((left, middle, right))
        if not witnesses:
            continue
        for index, row in enumerate(restored):
            if index in skip_indices:
                continue
            text = str(row.get('text', ''))
            places = _slots(row, item)
            if len(places) != 1:
                continue
            witness = next((w for w in witnesses if row['start'] <= w[1]['start'] < w[1]['end'] <= row['end']), None)
            if witness is None:
                continue
            position = places[0]
            # Preserve original spelling, punctuation and every old word. The
            # collapsed token's punctuation comes from the raw reading itself.
            row['text'] = text[:position] + item['text'] + ' ' + text[position:]
            row.pop('words', None)  # The old array omits the recovered word.
            row['confirmed_source_words'] = sorted(set(row.get('confirmed_source_words', [])) | {missing})
            changes.append({'row': index, 'before': text, 'after': row['text'], 'word': missing,
                            'collapsed_at': item['point'], 'profiles': sorted(item['profiles']),
                            'v2_words': [dict(w) for w in witness]})
            break
    return restored, changes
