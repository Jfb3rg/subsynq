"""Plan and merge overlapping ASR windows without assuming one language."""
from difflib import SequenceMatcher
from types import SimpleNamespace
import bisect
import itertools
import math
import re
import unicodedata


def plan_windows(duration, window_seconds=30, overlap_seconds=8):
    if duration <= 0 or window_seconds <= 0 or not 0 <= overlap_seconds < window_seconds:
        raise ValueError('Invalid ASR window settings')
    windows = []
    start = 0.0
    step = window_seconds - overlap_seconds
    while start < duration:
        end = min(duration, start + window_seconds)
        windows.append({'start': round(start, 3), 'end': round(end, 3)})
        if end >= duration:
            break
        start += step
    return windows


def select_native_focus_windows(windows, intervals, margin):
    """Keep original-file window offsets around a diagnostic focus region."""
    return [window for window in windows if any(
        window['end'] > start - margin and window['start'] < end + margin
        for start, end in intervals
    )]


def plan_rescue_windows(segments, duration, *, minimum_gap=1.5, maximum_gap=16.0,
                        padding=3.0, minimum_window_seconds=18.0,
                        window_seconds=30.0, overlap_seconds=4.0):
    """Plan no-VAD work only around plausible missed replies inside dialogue.

    A short clip's only confirmed line can start well after 0 (a trailer's
    music intro) or end well before its own duration; fencing the segment
    list with the clip boundaries lets the same gap check also rescue that
    leading or trailing span, instead of only ever looking between two
    already-found lines.
    """
    if duration <= 0 or not segments:
        return []
    ordered = sorted(segments, key=lambda row: (row['start'], row['end']))
    fenced = ([{'start': 0.0, 'end': 0.0}] + ordered +
             [{'start': float(duration), 'end': float(duration)}])
    intervals = []
    for left, right in zip(fenced, fenced[1:]):
        gap = float(right['start']) - float(left['end'])
        if not minimum_gap <= gap <= maximum_gap:
            continue
        start = max(0.0, float(left['end']) - padding)
        end = min(float(duration), float(right['start']) + padding)
        if end - start < minimum_window_seconds:
            missing = minimum_window_seconds - (end - start)
            start = max(0.0, start - missing / 2)
            end = min(float(duration), end + missing / 2)
            if end - start < minimum_window_seconds:
                start = max(0.0, end - minimum_window_seconds)
                end = min(float(duration), start + minimum_window_seconds)
        if intervals and start <= intervals[-1]['end']:
            intervals[-1]['end'] = max(intervals[-1]['end'], end)
        else:
            intervals.append({'start': start, 'end': end})
    windows = []
    for interval in intervals:
        length = interval['end'] - interval['start']
        if length <= window_seconds:
            windows.append({'start': round(interval['start'], 3),
                            'end': round(interval['end'], 3)})
            continue
        for window in plan_windows(length, window_seconds, overlap_seconds):
            windows.append({'start': round(interval['start'] + window['start'], 3),
                            'end': round(interval['start'] + window['end'], 3)})
    return windows


def plan_tail_confirmation(segments, duration, *, minimum_gap=16.0):
    """Probe only the first seconds after dialogue, not an entire long outro."""
    if not segments:
        return []
    last = max(segments, key=lambda row: row['end'])
    edge = float(last['end'])
    if duration - edge <= minimum_gap:
        return []
    return [{'start': round(max(0.0, edge - 6.0), 3),
             'end': round(min(duration, edge + 6.0), 3),
             'target_start': edge, 'target_end': min(duration, edge + 6.0)}]


def _tail_tokens(text):
    # Keep vowel/diacritic marks: \w alone drops them in many writing systems
    # and can turn different words into false "exact" independent agreement.
    text = unicodedata.normalize('NFC', str(text)).casefold()
    return ''.join(char if unicodedata.category(char)[0] in 'LMN' else ' '
                   for char in text).split()


def split_corroborated_long_cues(base, probes):
    """Split unchanged text only at a boundary independently heard twice."""
    result = []
    for row in base:
        original = str(row['text']).split()
        tokens = _tail_tokens(row['text'])
        if row['end'] - row['start'] < 4.5 or len(tokens) < 8 or len(tokens) != len(original):
            result.append(dict(row))
            continue
        evidence = {}
        for probe in probes:
            independent = _tail_tokens(probe.get('independent_text', ''))
            segments = probe.get('segments', [])
            for segment in segments:
                words = segment.get('words', [])
                heard = _tail_tokens(' '.join(w.get('text', '') for w in words))
                if len(heard) != len(words):
                    continue
                for cut in range(3, len(tokens) - 2):
                    suffix = tokens[cut:]
                    if sum(tokens[i:i + len(suffix)] == suffix
                           for i in range(len(tokens) - len(suffix) + 1)) != 1:
                        continue
                    prefix_anchor = tokens[cut - 3:cut]
                    if not any(independent[i:i + 3] == prefix_anchor for i in range(len(independent) - 2)):
                        continue
                    if not any(independent[i:i + len(suffix)] == suffix
                               for i in range(len(independent) - len(suffix) + 1)):
                        continue
                    for offset in range(len(heard) - len(suffix) + 1):
                        if heard[offset:offset + len(suffix)] != suffix:
                            continue
                        timed = words[offset:offset + len(suffix)]
                        if (any('start' not in w or 'end' not in w for w in timed)
                                or any(w['end'] <= w['start'] or w['end'] - w['start'] > 1.5 for w in timed)
                                or any(b['start'] < a['end'] - .02 for a, b in zip(timed, timed[1:]))
                                or min(w.get('probability', 0) for w in timed) < .6
                                or sum(w.get('probability', 0) for w in timed) / len(timed) < .75):
                            continue
                        start, end = timed[0]['start'] - .08, timed[-1]['end'] + .08
                        if not row['start'] + .8 < start < end <= row['end'] + .15:
                            continue
                        for previous in segments:
                            earlier = previous.get('words', [])
                            if not earlier:
                                continue
                            last = earlier[-1]
                            if (_tail_tokens(last.get('text', '')) != [tokens[cut - 1]]
                                    or 'end' not in last or 'start' not in last
                                    or not .02 <= last['end'] - last['start'] <= 1.5
                                    or last.get('probability', 0) < .8):
                                continue
                            boundary = last['end'] + .08
                            if not any(span['start'] <= boundary <= start < end <= span['end']
                                       for span in probe.get('speech', [])):
                                continue
                            if row['start'] + .8 <= boundary <= start - .4 and start - boundary <= 3:
                                evidence.setdefault(cut, []).append((probe['start'], boundary, start, end))
        replacement = None
        for cut, views in evidence.items():
            for a in views:
                for b in views:
                    if abs(a[0] - b[0]) < .3 or any(abs(x - y) > .25 for x, y in zip(a[1:], b[1:])):
                        continue
                    replacement = [
                        {**row, 'text': ' '.join(original[:cut]), 'end': round(max(a[1], b[1]), 3)},
                        {**row, 'text': ' '.join(original[cut:]), 'start': round(min(a[2], b[2]), 3),
                         'end': round(max(a[3], b[3]), 3)}]
                    break
                if replacement:
                    break
            if replacement:
                break
        result.extend(replacement or [dict(row)])
    return result


def plan_tail_timing_windows(window, duration):
    """Two bounded acoustic views, preserving the same exact phrase checks."""
    start = float(int(window['start'] + 2.0))
    return [{'start': value, 'end': min(duration, value + 12.0)}
            for value in (start, start + .5) if value < duration]


def split_confirmed_tail_candidate(last, candidate):
    """Separate already covered words using an exact, timed prefix anchor."""
    words = candidate.get('words', [])
    tokens = str(candidate.get('text', '')).split()
    old = _tail_tokens(last.get('text', ''))
    if (not 4 <= len(words) <= 32 or len(tokens) != len(words)
            or any(len(_tail_tokens(w.get('text', ''))) != 1 for w in words)
            or _tail_tokens(' '.join(w.get('text', '') for w in words)) != _tail_tokens(candidate.get('text', ''))
            or not all(candidate['start'] <= w.get('start', -1) < w.get('end', -1) <= candidate['end']
                       for w in words)):
        return candidate
    normalized = [_tail_tokens(w['text'])[0] for w in words]
    shared = next((n for n in range(min(len(old), len(words) - 2), 2, -1)
                   if old[-n:] == normalized[:n]), 0)
    if not shared:
        return candidate
    # The old cue may already contain the first, stretched word of the new
    # phrase. Borrow it only with timing evidence at the old cue's edge.
    boundary_word = words[shared - 1]
    if (boundary_word['end'] >= last['end'] - .25
            and boundary_word['end'] - boundary_word['start'] > 1.5):
        shared -= 1
    tail = words[shared:]
    if any(b['start'] < a['end'] - .001 for a, b in zip(tail, tail[1:])):
        return candidate
    return {**candidate, 'text': ' '.join(w['text'] for w in tail),
            'start': round(max(candidate['start'], tail[0]['start'] - .08), 3),
            'end': round(min(candidate['end'], tail[-1]['end'] + .08), 3),
            'words': tail, 'confidence': min(w.get('probability', 0) for w in tail),
            'split_from_text': candidate['text']}


def restore_confirmed_tail(base, probes):
    """Recover a short omitted tail only with timed ASR, independent text and VAD.

    A truncated leading word may belong to the new phrase. Remove only that
    exact suffix from the old cue; never substitute the old cue's other words.
    """
    result = [dict(row) for row in sorted(base, key=lambda row: row['start'])]
    if not result:
        return result
    edge = float(result[-1]['end'])
    for probe in probes:
        independent = _tail_tokens(probe.get('independent_text', ''))
        for original in probe.get('segments', []):
            candidate = split_confirmed_tail_candidate(result[-1], original)
            tokens = str(candidate.get('text', '')).split()
            normalized = _tail_tokens(candidate.get('text', ''))
            words = candidate.get('words', [])
            start, end = float(candidate['start']), float(candidate['end'])
            if (not 2 <= len(tokens) <= 6 or not normalized or not words
                    or _tail_tokens(' '.join(word.get('text', '') for word in words)) != normalized
                    or not all(start <= float(word.get('start', -1)) <
                               float(word.get('end', -1)) <= end for word in words)
                    or any(float(word['end']) - float(word['start']) > 1.5 for word in words)
                    or start < edge - 2.0 or start > edge + 6.0
                    or end <= edge + .12 or end > edge + 6.0
                    or not .3 <= end - start <= 4.0
                    or float(candidate.get('confidence', 0)) < .6
                    or sum(float(word.get('probability', 0)) for word in words) / len(words) < .75
                    or not any(independent[i:i + len(normalized)] == normalized
                               for i in range(len(independent) - len(normalized) + 1))
                    or _normalized(candidate['text']) in {
                        'thank you', 'thank you for watching', 'like and subscribe',
                        'please subscribe', 'see you next time', 'we ll be right back'}):
                continue
            confirmation = probe.get('timing_confirmation_for')
            if confirmation and (
                    normalized != _tail_tokens(confirmation['text'])
                    or abs(end - confirmation['end']) > .6
                    or not confirmation['first_word_start'] - .08 <= start <= confirmation['first_word_end']
                    or start < probe['independent_window']['start']
                    or end > probe['independent_window']['end']):
                continue
            overlap = sum(max(0.0, min(end, span['end']) - max(start, span['start']))
                          for span in probe.get('speech', []))
            if overlap < min(.3, (end - start) * .5):
                continue
            last = result[-1]
            if start < last['end']:
                old_tokens = str(last['text']).split()
                # Retain all non-overlapping source words and require an exact
                # partial prefix, not merely a similar alternative translation.
                shared = next((count for count in range(min(2, len(tokens) - 1), 0, -1)
                               if _tail_tokens(' '.join(old_tokens[-count:])) ==
                               _tail_tokens(' '.join(tokens[:count]))), 0)
                if (not shared or len(old_tokens) <= shared
                        or start <= last['start'] + .3):
                    continue
                last['text'] = ' '.join(old_tokens[:-shared])
                last['end'] = round(start, 3)
            result.append(dict(candidate))
            return result  # One bounded recovery, never a recursive outro scan.
    return result


def _normalized(text):
    return re.sub(r'[^\w]+', ' ', text.lower(), flags=re.UNICODE).strip()


def _canonical(text):
    """Normalize a few universal short address words for timing deduplication."""
    aliases = {'monsieur': 'sir', 'madame': 'maam', 'mister': 'sir'}
    return ' '.join(aliases.get(word, word) for word in _normalized(text).split())


def merge_window_segments(window_results):
    """Prefer centered cues, recover uncovered neighbour cues, remove duplicates."""
    if not window_results:
        return []
    ordered = sorted(window_results, key=lambda item: item['start'])
    boundaries = []
    for left, right in zip(ordered, ordered[1:]):
        boundaries.append((left['end'] + right['start']) / 2)
    selected = []
    outside_core = []
    for index, window in enumerate(ordered):
        core_start = float('-inf') if index == 0 else boundaries[index - 1]
        core_end = float('inf') if index == len(ordered) - 1 else boundaries[index]
        for segment in window.get('segments', []):
            text = str(segment.get('text', '')).strip()
            start, end = float(segment.get('start', -1)), float(segment.get('end', -1))
            midpoint = (start + end) / 2
            if text and end > start and core_start <= midpoint < core_end:
                selected.append({**segment, 'start': round(start, 3), 'end': round(end, 3),
                                 'text': text})
            elif text and end > start:
                outside_core.append({**segment, 'start': round(start, 3), 'end': round(end, 3),
                                     'text': text})
    # An overlap is useful only if it can recover content. Keep an off-core cue
    # when the centered window left that time uncovered; never layer two cues.
    for candidate in sorted(outside_core, key=lambda item: (item['start'], item['end'])):
        if any(min(candidate['end'], row['end']) - max(candidate['start'], row['start']) > .15
               for row in selected):
            continue
        if any(abs((candidate['start'] + candidate['end']) / 2 -
                   (row['start'] + row['end']) / 2) < 10
               and SequenceMatcher(None, _normalized(candidate['text']),
                                   _normalized(row['text'])).ratio() >= .82
               for row in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: (item['start'], item['end']))
    merged = []
    for segment in selected:
        if merged:
            similarity = SequenceMatcher(None, _normalized(merged[-1]['text']),
                                         _normalized(segment['text'])).ratio()
            # Adjacent overlapping windows can place the same short utterance a
            # few frames apart (for example two copies of "Merci"). It is one
            # cue, not a second line to translate independently.
            if segment['start'] - merged[-1]['end'] <= .35 and similarity >= .92:
                if segment['end'] - segment['start'] > merged[-1]['end'] - merged[-1]['start']:
                    merged[-1] = segment
                continue
        if merged and segment['start'] < merged[-1]['end']:
            if similarity >= .82:
                if len(segment['text']) > len(merged[-1]['text']):
                    merged[-1] = segment
                continue
        merged.append(segment)
    for previous, current in zip(merged, merged[1:]):
        if current['start'] < previous['end']:
            boundary = round((current['start'] + previous['end']) / 2, 3)
            previous['end'] = boundary
            current['start'] = boundary
    return [segment for segment in merged if segment['end'] > segment['start']]


def trim_duplicated_cue_boundary(cues, *, minimum_words=4, maximum_words=14, maximum_gap=.05):
    """Drop a cue's own leading words when they verbatim-repeat the immediately
    preceding cue's trailing words, with essentially no time gap between them.

    Two adjacent overlapping Whisper window reads can each independently
    transcribe the same handful of words right at their shared boundary --
    for example one cue ending "...the only time he really left was to"
    immediately followed by a cue starting "only time he really left was to
    go back...". merge_window_segments() only catches two cues that are
    ALMOST ENTIRELY the same text (its similarity-ratio check on the whole
    cue); it does not catch two mostly DIFFERENT cues that merely share a
    run of words at their shared edge. That run reads as a stutter/repeated-
    phrase hallucination to a downstream reviewer even though the underlying
    speech recognition is otherwise correct, so it is trimmed here.
    """
    result = [dict(cue) for cue in cues]
    for previous, current in zip(result, result[1:]):
        if float(current['start']) - float(previous['end']) > maximum_gap:
            continue
        previous_words = str(previous['text']).split()
        current_words = str(current['text']).split()
        if not previous_words or not current_words:
            continue
        cap = min(maximum_words, len(previous_words), len(current_words))
        overlap = 0
        for count in range(cap, minimum_words - 1, -1):
            if (_normalized(' '.join(previous_words[-count:])) ==
                    _normalized(' '.join(current_words[:count]))):
                overlap = count
                break
        if overlap:
            current['text'] = ' '.join(current_words[overlap:])
    return [cue for cue in result if str(cue['text']).strip()]


def trustworthy_word_span(words, *, minimum_probability=.35, padding=.08):
    """Return a tight local span from reliable faster-whisper word timestamps."""
    accepted = [word for word in words
                if getattr(word, 'start', None) is not None
                and getattr(word, 'end', None) is not None
                and getattr(word, 'probability', 0) >= minimum_probability
                and word.end > word.start]
    if not accepted:
        return None
    return (max(0.0, accepted[0].start - padding), accepted[-1].end + padding)


def _balance_short_word_tails(groups, limited, *, start, end, joined,
                              maximum_duration, maximum_characters):
    """Rebalance only tails created by a size limit, using actual word times."""
    for index in range(1, len(groups)):
        previous, current = groups[index - 1], groups[index]
        if (not limited[index - 1] or not previous or not current
                or len(joined(current).split()) > 2
                or end(current[-1]) - start(current[0]) >= 1.0):
            continue
        combined = previous + current
        # Prefer keeping the phrase together when only the early near-cap
        # flush caused the split. Otherwise move the boundary back, never
        # inventing word times or extending a cue across another sentence.
        for cut in [0, *range(len(previous) - 1, 1, -1)]:
            left, right = combined[:cut], combined[cut:]
            if (cut and (len(joined(left).split()) < 2
                         or len(joined(right).split()) < 2
                         or end(right[-1]) - start(right[0]) < 1.0)):
                continue
            if any(end(part[-1]) - start(part[0]) > maximum_duration
                   or len(joined(part)) > maximum_characters
                   for part in (left, right) if part):
                continue
            groups[index - 1], groups[index] = left, right
            break


START_LEAD = .12   # a subtitle may appear this long before the voice (the eye needs a moment)
MAXIMUM_SPEECH_RATE = 6.0   # words per second a start may be moved to; see refine_starts


def refine_starts(cues, spans, *, lead=START_LEAD, minimum_change=.1, maximum_change=1.2, minimum_duration=.4, maximum_rate=MAXIMUM_SPEECH_RATE,
                  minimum_rate_words=3):
    """Delay a cue that starts BEFORE the voice does to where the voice begins (minus `lead`): (cues, how many were changed, the shifts).

    Whisper's first word of a sentence starts early after a pause: on Yukon Men S01E01 (44 min, 899 cues) the cue starts lay a median 0.45 s before
    the onset of the speech (voice detection with no padding), the same all through the file, and cutting the audio later proved the first word still
    intact 0.4 s after the cue start (STATUS natt 47). `spans` are the voiced stretches [(start, end), ...] in seconds. Only a delay is made: a cue that
    starts inside a voiced stretch (several cues in one breath), or whose next voiced stretch begins more than `maximum_change` later, is left alone,
    and a cue is never shortened below `minimum_duration`.

    The voice detection lags behind the speech wherever music or noise lies under it (WIFE AND DOG: "And what did he find in Hell, Daddy?" spoken from
    45.1 s, the detector found a voice from 45.6 s; MAN OF WAR: "They stormed the orphanage." from 30.6 s, found from 31.4 s), and on a fast line even
    Yukon Men: of the seven fastest shifted cues a clean reading of the audio puts the first word 0.13 to 0.6 s BEFORE the moved start in six ("Did you guys
    hear that?" moved to 'guys', "And we're running out of wood." to 'running'), 5 words in 0.51 s. A start is therefore never moved so late that the cue's words
    (`minimum_rate_words` or more) would have to be spoken faster than `maximum_rate` words per second; it is moved as far as that allows. Text without spaces
    between words (Chinese, Japanese) counts as one word and is not limited."""
    spans = sorted((float(begin), float(finish)) for begin, finish in spans)
    result, shifts, position = [], [], 0
    for cue in cues:
        start, end = float(cue['start']), float(cue['end'])
        while position < len(spans) and spans[position][1] <= start:
            position += 1
        index = position
        # cues are in time order, but one cue may start before the previous one's span ended: look from the first span that ends after the start
        if index < len(spans):
            onset = spans[index][0]
            target = onset - lead
            units = len(_normalized(str(cue.get('text', ''))).split())
            if maximum_rate and units >= minimum_rate_words:
                target = min(target, end - units / maximum_rate)
            restored = cue.get('confirmed_lead_onset')
            if isinstance(restored, dict):
                prefix, readers, bound = restored.get('prefix'), restored.get('readings'), restored.get('start')
                if (isinstance(prefix, list) and len(prefix) >= 3
                        and isinstance(readers, list) and all(isinstance(name, str) for name in readers) and len(set(readers)) >= 2
                        and type(bound) in (int, float) and math.isfinite(bound) and bound >= 0 and abs(bound - start) <= .001
                        and _normalized(str(cue.get('text', ''))).split()[:len(prefix)] == prefix):
                    # VAD can miss precisely the quiet word two readers restored.
                    # Protect only this unchanged prefix at its measured start.
                    target = min(target, bound)
            if (onset > start and target - start >= minimum_change and onset - start <= maximum_change and end - target >= minimum_duration):
                result.append({**cue, 'start': round(target, 3)})
                shifts.append(round(target - start, 3))
                continue
        result.append(dict(cue))
    return result, len(shifts), shifts


def split_word_cues(words, *, minimum_probability=.25, maximum_duration=7,
                    maximum_characters=84, maximum_pause=2.8, padding=.08,
                    include_words=False):
    """Split a Whisper segment at word pauses/sentences instead of discarding speech."""
    accepted = [word for word in words
                if getattr(word, 'start', None) is not None
                and getattr(word, 'end', None) is not None
                and getattr(word, 'probability', 0) >= minimum_probability
                and word.end > word.start and str(getattr(word, 'word', '')).strip()]
    cues, group, groups, limited = [], [], [], []

    def flush(size_limit=False):
        if not group:
            return
        groups.append(list(group))
        limited.append(size_limit)
        group.clear()

    for index, word in enumerate(accepted):
        if group:
            projected_text = ''.join(str(item.word) for item in (*group, word)).strip()
            pause = word.start - group[-1].end >= maximum_pause
            if (word.end - group[0].start > maximum_duration
                    or len(projected_text) > maximum_characters or pause):
                flush(size_limit=not pause)
        group.append(word)
        next_gap = (accepted[index + 1].start - word.end
                    if index + 1 < len(accepted) else 0)
        terminal = str(word.word).rstrip().endswith(('.', '?', '!', '…'))
        if (terminal or next_gap >= maximum_pause
                or word.end - group[0].start >= maximum_duration - .25):
            flush(size_limit=not terminal and next_gap < maximum_pause)
    flush()
    _balance_short_word_tails(
        groups, limited, start=lambda w: w.start, end=lambda w: w.end,
        joined=lambda ws: ''.join(str(w.word) for w in ws).strip(),
        maximum_duration=maximum_duration, maximum_characters=maximum_characters)
    for group in groups:
        if not group:
            continue
        text = ''.join(str(word.word) for word in group).strip()
        # The two flush triggers below only bound the group's raw word span
        # (never appending a word that would push it past maximum_duration),
        # so that span alone can never exceed the cap here. Padding both
        # ends by a fixed amount could still push a group that is already
        # right at the cap over it on its own — shrink the padding instead
        # of silently dropping several real, correctly heard words.
        raw_span = group[-1].end - group[0].start
        available = max(0.0, maximum_duration - raw_span)
        start_pad = min(padding, available / 2)
        end_pad = min(padding, available - start_pad)
        start = max(0.0, group[0].start - start_pad)
        end = group[-1].end + end_pad
        if text and end > start:
            cue = {'start': start, 'end': end, 'text': text,
                   'confidence': min(float(word.probability) for word in group)}
            if include_words:
                cue['words'] = [{'start': word.start, 'end': word.end,
                                 'text': str(word.word).strip(),
                                 'probability': float(word.probability)} for word in group]
            cues.append(cue)
    return cues


def split_qwen_alignment(items, offset=0.0, *, maximum_duration=7,
                         maximum_characters=84, maximum_pause=1.2, padding=.06):
    """Turn Qwen forced-aligner words into readable subtitle-sized cues."""
    accepted = []
    for position, item in enumerate(items or []):
        try:
            text = str(item.get('text', '')).strip()
            start, end = float(item['start']), float(item['end'])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        if end - start > 1.5:
            # The forced aligner stretched this word over a silence (real Ray Gunn run: "I" got
            # 0.0-4.0 s and "like" started at 4.0 s). The word WAS heard, so dropping it loses
            # text ("Like you, gun."); keep it, next to the speech: after a leading silence the
            # word sits at the end of its span, otherwise at the start.
            if position == 0:
                start = end - .4
            else:
                end = start + .4
        accepted.append({'text': text, 'start': start, 'end': end})
    cues, group, groups, limited = [], [], [], []

    def joined(words):
        text = ' '.join(word['text'] for word in words)
        text = re.sub(r'\s+([,.;:!?…])', r'\1', text)
        text = re.sub(r"\b([cdjlmnst])\s+'\s*", r"\1'", text, flags=re.IGNORECASE)
        return text.strip()

    def flush(size_limit=False):
        if not group:
            return
        groups.append(list(group))
        limited.append(size_limit)
        group.clear()

    for index, word in enumerate(accepted):
        if group:
            projected = joined([*group, word])
            pause = word['start'] - group[-1]['end'] >= maximum_pause
            if (word['end'] - group[0]['start'] > maximum_duration
                    or len(projected) > maximum_characters
                    or pause):
                flush(size_limit=not pause)
        group.append(word)
        next_gap = (accepted[index + 1]['start'] - word['end']
                    if index + 1 < len(accepted) else 0)
        terminal = word['text'].rstrip().endswith(('.', '?', '!', '…'))
        if (terminal
                or next_gap >= maximum_pause
                or word['end'] - group[0]['start'] >= maximum_duration - .25):
            flush(size_limit=not terminal and next_gap < maximum_pause)
    flush()
    _balance_short_word_tails(
        groups, limited, start=lambda w: w['start'], end=lambda w: w['end'],
        joined=joined,
        maximum_duration=maximum_duration, maximum_characters=maximum_characters)
    for group in groups:
        if not group:
            continue
        raw_span = group[-1]['end'] - group[0]['start']
        available = max(0.0, maximum_duration - raw_span)
        start_pad = min(padding, available / 2)
        end_pad = min(padding, available - start_pad)
        start = max(0.0, offset + group[0]['start'] - start_pad)
        end = offset + group[-1]['end'] + end_pad
        cues.append({'start': round(start, 3), 'end': round(end, 3),
                     'text': joined(group), 'confidence': 1.0, 'source': 'qwen'})
    return cues


def extract_qwen_target_phrase(items, offset, target_start, target_end, original_text):
    """Isolate a disputed suffix even when Qwen groups preceding dialogue with it."""
    anchors = _normalized(original_text).split()[:2]
    if len(anchors) != 2:
        return None
    words = list(items or [])
    for index in range(len(words) - 1):
        if ([_normalized(words[index + step].get('text', '')) for step in (0, 1)]
                != anchors or abs(offset + float(words[index]['start']) - target_start) > 1.0):
            continue
        selected = []
        for item in words[index:]:
            start, end = float(item['start']), float(item['end'])
            if (selected and start - float(selected[-1]['end']) > .8
                    or offset + end > target_end + .6 or len(selected) >= 12):
                break
            selected.append(item)
        cues = split_qwen_alignment(selected, offset=offset)
        if (len(cues) == 1 and len(_normalized(cues[0]['text']).split()) >= 3
                and abs(cues[0]['end'] - target_end) <= .8):
            return cues[0]
    return None


def plan_qwen_short_disagreements(base, context, precision, speech_intervals, duration,
                                  *, maximum_total=24, maximum_per_quarter_hour=8):
    """Double-check isolated short cues whose timestamps barely touch speech."""
    rows = sorted(base, key=lambda row: (row['start'], row['end']))
    candidates = []
    for index, row in enumerate(rows):
        words = _normalized(row.get('text', '')).split()
        if (len(words) != 1 or row['end'] - row['start'] > 1.0
                or float(row.get('confidence', 0)) < .55):
            continue
        before = row['start'] - rows[index - 1]['end'] if index else float('inf')
        after = rows[index + 1]['start'] - row['end'] if index + 1 < len(rows) else float('inf')
        if before < 2.5 or after < 2.5:
            continue
        if not all(any(
                _normalized(other.get('text', '')) == _normalized(row['text'])
                and abs(other['start'] - row['start']) <= .7
                and abs(other['end'] - row['end']) <= .7
                and float(other.get('confidence', 0)) >= .55
                for other in profile) for profile in (context, precision)):
            continue
        core = max((max(0.0, min(row['end'], item['end'])
                        - max(row['start'], item['start']))
                    for item in speech_intervals), default=0.0)
        nearby = max((max(0.0, min(row['end'] + .7, item['end'])
                          - max(row['start'] - .7, item['start']))
                      for item in speech_intervals), default=0.0)
        if core >= .18 or nearby < .18:
            continue
        candidates.append((float(row.get('confidence', 0)), nearby, row))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]['start']))
    selected, quarters = [], {}
    for _, _, row in candidates:
        quarter = int(row['start'] // 900)
        if (len(selected) >= maximum_total
                or quarters.get(quarter, 0) >= maximum_per_quarter_hour
                or any(abs(row['start'] - previous['start']) < 12 for previous in selected)):
            continue
        selected.append(row)
        quarters[quarter] = quarters.get(quarter, 0) + 1
    windows = []
    for row in sorted(selected, key=lambda item: item['start']):
        for lead in (7, 5):
            start = max(0.0, float(int(row['start'] - lead)))
            end = min(float(duration), start + 16.0)
            if end - start >= 10 and row['end'] <= end - 2:
                windows.append({'start': start, 'end': end,
                                'target_start': row['start'], 'target_end': row['end']})
    return windows


def plan_code_switch_disagreements(context, precision, duration,
                                   *, maximum_total=8, maximum_per_quarter_hour=3):
    """Re-read short shared-prefix phrases when v3 passes disagree on the ending."""
    planned, quarters = [], {}
    for row in sorted(context, key=lambda item: item['start']):
        words = _normalized(row.get('text', '')).split()
        if (not 3 <= len(words) <= 12 or row['end'] - row['start'] > 5
                or float(row.get('confidence', 0)) < .6):
            continue
        alternative = next((item for item in precision
                            if item['start'] < row['end'] and item['end'] > row['start']
                            and abs(item['start'] - row['start']) <= .8
                            and abs(item['end'] - row['end']) <= .8
                            and float(item.get('confidence', 0)) >= .5
                            and _normalized(item.get('text', '')).split()[:2] == words[:2]
                            and _normalized(item.get('text', '')).split() != words), None)
        if alternative is None:
            continue
        quarter = int(row['start'] // 900)
        if (len(planned) >= maximum_total
                or quarters.get(quarter, 0) >= maximum_per_quarter_hour):
            continue
        start = max(0.0, float(int(row['start'] - 6)))
        end = min(float(duration), start + 13.0)
        if end - start < 10 or row['end'] > end - 2:
            continue
        planned.append({'start': start, 'end': end,
                        'target_start': row['start'], 'target_end': row['end'],
                        'target_text': row['text'],
                        'kind': 'code_switch', 'language_hint': 'English'})
        quarters[quarter] = quarters.get(quarter, 0) + 1
    return planned


def reconcile_code_switch_readings(base, qwen_windows, v2_rows, speech_intervals):
    """Accept a forced-language reading only when v2 independently agrees."""
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    for window in qwen_windows:
        if window.get('kind') != 'code_switch':
            continue
        old = next((row for row in result
                    if abs(row['start'] - window['target_start']) <= .8
                    and abs(row['end'] - window['target_end']) <= .8), None)
        if old is None:
            continue
        old_words = _normalized(old.get('text', '')).split()
        for candidate in window.get('segments', []):
            words = _normalized(candidate.get('text', '')).split()
            if (len(words) < 3 or words[:2] != old_words[:2] or words == old_words
                    or abs(candidate['start'] - old['start']) > 1.5
                    or abs(candidate['end'] - old['end']) > 1.5):
                continue
            if not any(_normalized(v2.get('text', '')).split() == words
                       and abs(v2['start'] - candidate['start']) <= 1.2
                       and abs(v2['end'] - candidate['end']) <= 1.2
                       and float(v2.get('confidence', 0)) >= .3
                       for v2 in v2_rows):
                continue
            speech = sum(max(0.0, min(candidate['end'], item['end'])
                             - max(candidate['start'], item['start']))
                         for item in speech_intervals)
            if speech < .2 or any(candidate['start'] < row['end'] - .001
                                   and candidate['end'] > row['start'] + .001
                                   for row in result if row is not old):
                continue
            result[result.index(old)] = dict(candidate)
            result.sort(key=lambda item: item['start'])
            break
    return result


def reconcile_qwen_segments(primary, qwen_windows, speech_intervals,
                            *, minimum_speech_overlap=.16):
    """Use two aligned Qwen readings to remove ghosts and fill clear ASR gaps."""
    rows = [dict(row) for row in sorted(primary, key=lambda row: (row['start'], row['end']))]
    qwen = merge_window_segments(qwen_windows)
    speech = [(float(item['start']), float(item['end'])) for item in speech_intervals]

    def text_match(left, right, threshold=.72):
        first, second = _canonical(left), _canonical(right)
        if not first or not second:
            return False
        return (first in second or second in first or
                SequenceMatcher(None, first, second).ratio() >= threshold)

    def repeated(candidate):
        return sum(any(text_match(candidate['text'], item.get('text', ''))
                       and min(candidate['end'], item['end']) -
                           max(candidate['start'], item['start']) > .08
                       for item in window.get('segments', []))
                   for window in qwen_windows) >= 2

    def speech_supported(candidate):
        overlap = sum(max(0.0, min(candidate['end'], end) -
                          max(candidate['start'], start)) for start, end in speech)
        return overlap >= min(minimum_speech_overlap,
                              max(.08, (candidate['end'] - candidate['start']) * .16))

    filtered = []
    for row in rows:
        words = _normalized(row.get('text', '')).split()
        covering = [window for window in qwen_windows
                    if window['start'] <= row['start'] and window['end'] >= row['end']]
        supported = any(
            row['start'] - 1.0 < item['end'] and row['end'] + 1.0 > item['start']
            and text_match(row.get('text', ''), item.get('text', ''))
            for window in covering for item in window.get('segments', [])
        )
        target_near = any('target_start' not in window or
                          abs((row['start'] + row['end']) / 2 -
                              (window['target_start'] + window['target_end']) / 2) <= .9
                          for window in covering)
        if (len(words) == 1 and row['end'] - row['start'] <= 1.0
                and len(covering) >= 2 and target_near and not supported):
            continue
        filtered.append(row)
    rows = filtered

    for candidate in qwen:
        candidate_words = _normalized(candidate.get('text', '')).split()
        single_target = (len(candidate_words) == 1 and len(candidate_words[0]) >= 3
                         and any('target_start' in window and
                                 abs((candidate['start'] + candidate['end']) / 2 -
                                     (window['target_start'] + window['target_end']) / 2) <= .9
                                 for window in qwen_windows))
        if (not candidate_words or len(candidate_words) < 2 and not single_target
                or candidate['end'] - candidate['start'] > 9
                or not repeated(candidate) or not speech_supported(candidate)):
            continue
        nearby = [row for row in rows
                  if candidate['start'] < row['end'] and candidate['end'] > row['start']]
        if not nearby:
            if any(text_match(candidate['text'], row['text']) and
                   abs((candidate['start'] + candidate['end']) / 2 -
                       (row['start'] + row['end']) / 2) < 3 for row in rows):
                continue
            rows.append(dict(candidate))
            rows.sort(key=lambda row: (row['start'], row['end']))
            continue
        existing = ' '.join(row['text'] for row in nearby)
        candidate_words = _normalized(candidate['text']).split()
        existing_words = _normalized(existing).split()
        if (_normalized(existing) in _normalized(candidate['text'])
                and len(candidate_words) >= len(existing_words) + 2):
            rows = [row for row in rows if row not in nearby]
            rows.append(dict(candidate))
            rows.sort(key=lambda row: (row['start'], row['end']))

    for previous, current in zip(rows, rows[1:]):
        if current['start'] < previous['end']:
            boundary = round((current['start'] + previous['end']) / 2, 3)
            previous['end'] = boundary
            current['start'] = boundary
    return [row for row in rows if row['end'] > row['start']]


def trim_small_overlaps(segments, *, tolerance=.25):
    """Let a row that runs a few hundredths of a second into the next one end where the next begins.

    Real job 42 (dev 0.8.246, Tamil): the passes agreed on `...விட்ட` (ends 28.42) and `என்ன?` (starts 28.40), and the
    whole job failed for 20 ms of overlap. A pass that swaps in a reading takes its own timing, so rows can overlap
    by a hair. Only the earlier row's end moves (the next row's start is anchored by its own words) and only up to
    `tolerance` seconds; anything larger, or a trim that would leave the earlier row empty, is left for the
    validator to refuse."""
    rows = [dict(segment) for segment in segments]
    for previous, current in zip(rows, rows[1:]):
        overlap = float(previous['end']) - float(current['start'])
        if 0 < overlap <= tolerance and float(current['start']) > float(previous['start']):
            previous['end'] = current['start']
    return rows


def validate_episode_segments(segments, *, maximum_duration=12):
    """Fail closed when ASR produced impossible or bridge-like subtitle timing."""
    if not segments:
        raise ValueError('Whisper hittade ingen dialog i avsnittet.')
    previous_end = 0.0
    for segment in segments:
        start, end = float(segment['start']), float(segment['end'])
        if start < 0 or end <= start:
            raise ValueError('Whisper gav en ogiltig tidsrad.')
        if start < previous_end - .001:
            raise ValueError(
                f'Whisper gav överlappande tidsrader vid {start:.2f} sekunder '
                f'(föregående rad slutar {previous_end:.2f}).')
        if end - start > maximum_duration:
            raise ValueError(
                f'Kvalitetskontrollen stoppade en orimligt lång textrad vid {start:.1f} sekunder '
                f'({end - start:.1f} sekunder): {str(segment.get("text", ""))[:120]}')
        previous_end = end
    return True


def fill_profile_gaps(primary, secondary):
    """Use a short-window profile only where the context-rich profile is incomplete."""
    result = [dict(row) for row in primary]
    for candidate in sorted(secondary, key=lambda row: (row['start'], row['end'])):
        near = [row for row in result
                if candidate['start'] < row['end'] and candidate['end'] > row['start']]
        if near:
            actual_overlap = any(min(candidate['end'], row['end']) -
                                 max(candidate['start'], row['start']) > .15 for row in near)
            existing_text = ' '.join(row['text'] for row in near)
            # A substantially fuller short-window reading may replace fragments.
            if actual_overlap and len(_normalized(candidate['text'])) > len(_normalized(existing_text)) + 12:
                result = [row for row in result if row not in near]
                result.append(dict(candidate))
            continue
        if any(abs((candidate['start'] + candidate['end']) / 2 -
                   (row['start'] + row['end']) / 2) < 10
               and SequenceMatcher(None, _canonical(candidate['text']),
                                   _canonical(row['text'])).ratio() >= .82
               for row in result):
            continue
        result.append(dict(candidate))
    result.sort(key=lambda row: (row['start'], row['end']))
    for previous, current in zip(result, result[1:]):
        if current['start'] < previous['end']:
            boundary = round((current['start'] + previous['end']) / 2, 3)
            previous['end'] = boundary
            current['start'] = boundary
    return [row for row in result if row['end'] > row['start']]


def prefer_fuller_reading(merged, other, *, minimum_confidence=.5):
    """Let an independent reading win a passage when it holds the same words plus more.

    A long-context pass can squeeze several sentences into one run-on cue and drop whole
    sentences (real job 18: "Venus Nova's a big star. She's emotional, volatile. She does
    things." became "Nova is a big star she's emotional"). `other` is any independent
    reading of the same audio (short-window pass, another overlapping window). Its cues
    replace a group of 1-4 neighbouring cues only when ALL of this holds: it contains at
    least 80 % of the group's words, adds at least two words the group lacks, adds more
    than it loses, sits on the same passage (starts/ends within 1.5 s), has no cue below
    `minimum_confidence`, and does not overlap any cue that stays. Words come only from
    a real reading, never from guessing; anything else is left exactly as it was.
    """
    result = [dict(row) for row in sorted(merged, key=lambda row: row['start'])]
    shorter = [dict(row) for row in sorted(other, key=lambda row: row['start'])]
    index = 0
    while index < len(result):
        best = None
        for count in range(1, min(4, len(result) - index) + 1):
            original = result[index:index + count]
            if (original[-1]['end'] - original[0]['start'] > 16
                    or any(b['start'] - a['end'] > 4.0 for a, b in zip(original, original[1:]))):
                continue
            candidates = [row for row in shorter
                          if row['end'] > original[0]['start'] + .15
                          and row['start'] < original[-1]['end'] - .15]
            if (not candidates
                    or any(float(row.get('confidence', 0)) < minimum_confidence for row in candidates)
                    or abs(candidates[0]['start'] - original[0]['start']) > 1.5
                    or abs(candidates[-1]['end'] - original[-1]['end']) > 1.5
                    or any(b['start'] - a['end'] > 4.0 for a, b in zip(candidates, candidates[1:]))
                    or any(a['end'] > b['start'] + .15 for a, b in zip(candidates, candidates[1:]))):
                continue
            old_words = _normalized(' '.join(row['text'] for row in original)).split()
            new_words = _normalized(' '.join(row['text'] for row in candidates)).split()
            if len(old_words) < 3:
                continue
            matched = sum(block.size for block in SequenceMatcher(
                None, old_words, new_words, autojunk=False).get_matching_blocks())
            gain, lost = len(new_words) - matched, len(old_words) - matched
            untouched = result[:index] + result[index + count:]
            if (matched / len(old_words) < .8 or gain < 2 or gain <= lost
                    or any(row['start'] < kept['end'] - .001 and row['end'] > kept['start'] + .001
                           for row in candidates for kept in untouched)):
                continue
            if best is None or gain >= best[0]:
                best = (gain, count, candidates)
        if best:
            _, count, candidates = best
            result[index:index + count] = [dict(row) for row in candidates]
            index += len(candidates)
        else:
            index += 1
    for previous, current in zip(result, result[1:]):
        if current['start'] < previous['end']:
            boundary = round((current['start'] + previous['end']) / 2, 3)
            previous['end'] = boundary
            current['start'] = boundary
    return [row for row in result if row['end'] > row['start']]


def prefer_fuller_window_readings(merged, window_results):
    """Apply prefer_fuller_reading with each overlapping window's own cues as the other reading."""
    for window in sorted(window_results, key=lambda item: item['start']):
        segments = [dict(segment, text=str(segment.get('text', '')).strip())
                    for segment in window.get('segments', [])
                    if str(segment.get('text', '')).strip()]
        if segments:
            merged = prefer_fuller_reading(merged, segments)
    return merged


def prefer_precision_pauses(merged, precision):
    """Keep a short pass's audible pause when both passes agree on the words."""
    result = [dict(row) for row in merged]
    shorter = sorted(precision, key=lambda row: (row['start'], row['end']))
    for first, second in zip(shorter, shorter[1:]):
        pause = second['start'] - first['end']
        if not 1.2 <= pause <= 5.0:
            continue
        for index in range(len(result) - 1):
            old_first, old_second = result[index:index + 2]
            if (old_second['start'] - old_first['end'] > .35
                    or abs(first['start'] - old_first['start']) > 1.0
                    or abs(second['end'] - old_second['end']) > 1.0):
                continue
            original = _normalized(old_first['text'] + ' ' + old_second['text'])
            replacement = _normalized(first['text'] + ' ' + second['text'])
            if (len(original.split()) < 6
                    or SequenceMatcher(None, original, replacement).ratio() < .94):
                continue
            if (index and first['start'] < result[index - 1]['end']
                    or index + 2 < len(result) and second['end'] > result[index + 2]['start']):
                continue
            result[index:index + 2] = [dict(first), dict(second)]
            break
    return result


def _merge_short_interjections(cues):
    """Join a flash-short one/two-word cue ("Ray!") with the cue right after it ("Jump!")."""
    result = []
    for cue in cues:
        previous = result[-1] if result else None
        if (previous is not None
                and previous['end'] - previous['start'] < .8
                and len(_normalized(previous['text']).split()) <= 2
                and 0 <= cue['start'] - previous['end'] <= .3
                and len(_normalized(previous['text']).split())
                + len(_normalized(cue['text']).split()) <= 4):
            result[-1] = {**previous, 'end': cue['end'],
                          'text': f"{previous['text'].strip()} {cue['text'].strip()}",
                          'confidence': min(float(previous.get('confidence', 0)),
                                            float(cue.get('confidence', 0)))}
        else:
            result.append(dict(cue))
    return result


def prefer_precision_sentence_boundaries(merged, precision):
    """Use short-pass sentence boundaries when both passes agree on a passage.

    Long-context Whisper can swallow two or three spoken sentences into cues
    split by its duration limit. A short pass is preferred only when it covers
    the same timed passage, contains substantially more completed sentences,
    and has essentially the same words.
    """
    result = [dict(row) for row in sorted(merged, key=lambda row: row['start'])]
    shorter = [dict(row) for row in sorted(precision, key=lambda row: row['start'])]
    index = 0
    while index < len(result):
        best = None
        for count in range(2, min(4, len(result) - index) + 1):
            original = result[index:index + count]
            if (original[-1]['end'] - original[0]['start'] > 16
                    or any(b['start'] - a['end'] > 3.0
                           for a, b in zip(original, original[1:]))):
                continue
            candidates = [row for row in shorter
                          if row['end'] > original[0]['start'] - .8
                          and row['start'] < original[-1]['end'] + .8]
            if not 3 <= len(candidates) <= 5 or len(candidates) <= count:
                continue
            if (abs(candidates[0]['start'] - original[0]['start']) > .8
                    or abs(candidates[-1]['end'] - original[-1]['end']) > .8
                    or any(b['start'] - a['end'] > 3.5
                           for a, b in zip(candidates, candidates[1:]))
                    or any(float(row.get('confidence', 0)) < .25 for row in candidates)):
                continue
            original_text = _normalized(' '.join(row['text'] for row in original))
            candidate_text = _normalized(' '.join(row['text'] for row in candidates))
            if (len(original_text.split()) < 10
                    or SequenceMatcher(None, original_text, candidate_text).ratio() < .84):
                continue
            original_stops = sum(str(row['text']).rstrip().endswith(('.', '?', '!'))
                                 for row in original)
            candidate_stops = sum(str(row['text']).rstrip().endswith(('.', '?', '!'))
                                  for row in candidates)
            if candidate_stops < original_stops + 2:
                continue
            audible_pause = any(b['start'] - a['end'] >= 1.2
                                for a, b in zip(candidates, candidates[1:]))
            # Without a pause, trust the short pass only for a run-on: the long pass has NO sentence
            # end at all, the short pass ends EVERY cue with one and is confident about each (real
            # job 18: "...crooked game ray jump did you go and do the" = "...game." "Ray!" "Jump!"
            # "Did you go and do the right thing again?" with 0.04-0.7 s between them).
            # The LAST cue may lack one: it can be the first word of the next sentence ("will" of
            # "Will you be making a statement?"), cut off by the passage edge.
            punctuated_run_on = (original_stops == 0 and candidate_stops >= 3
                                 and all(str(row['text']).rstrip().endswith(('.', '?', '!'))
                                         for row in candidates[:-1])
                                 and all(float(row.get('confidence', 0)) >= .8
                                         for row in candidates))
            if not audible_pause and not punctuated_run_on:
                continue
            untouched = result[:index] + result[index + count:]
            if (any(a['end'] > b['start'] + .001
                    for a, b in zip(candidates, candidates[1:]))
                    or any(candidate['start'] < row['end'] - .001
                           and candidate['end'] > row['start'] + .001
                           for candidate in candidates for row in untouched)):
                continue
            best = (count, candidates, not audible_pause)
        if best:
            count, candidates, run_on = best
            if run_on:
                candidates = _merge_short_interjections(candidates)
            result[index:index + count] = candidates
            index += len(candidates)
        else:
            index += 1
    return result


def _ends_sentence(row):
    return str(row.get('text', '')).rstrip().endswith(('.', '?', '!'))


def _edge_extras(have, words):
    """(leading, trailing) extra words (at most two each) that turn `words` into `have`, or None."""
    for total in range(0, 5):
        for lead in range(0, min(total, 2) + 1):
            trail = total - lead
            if trail > 2 or len(have) - lead - trail != len(words) or len(have) - trail < lead:
                continue
            if have[lead:len(have) - trail] == words:
                return lead, trail
    return None


def _corroborates_extras(row, extras, v2):
    """Extra edge words are trusted when the short pass is confident about that cue or v2 heard them too."""
    if not extras:
        return True
    if float(row.get('confidence', 0)) >= .85:
        return True
    needle = ' '.join(extras)
    return any(_normalized(str(other.get('text', ''))).find(needle) >= 0
               and other['end'] > row['start'] - .5 and other['start'] < row['end'] + .5 for other in v2)


def _match_precision_run(block, shorter, v2):
    """Consecutive short-pass sentences (`shorter`; `v2` is the OTHER short reading that may corroborate extra
    words) holding exactly the words of `block` (plus at most two corroborated extra
    words at each edge), or None."""
    words = _normalized(' '.join(str(row['text']) for row in block)).split()
    if not words:
        return None
    start, end = block[0]['start'], block[-1]['end']
    pool = [row for row in shorter if row['end'] > start - 2.0 and row['start'] < end + 1.2]
    block_stops = sum(_ends_sentence(row) for row in block)
    for first in range(len(pool)):
        if not start - 1.6 <= pool[first]['start'] <= start + .8:
            continue
        collected = []
        for row in pool[first:]:
            collected.append(row)
            have = _normalized(' '.join(str(item['text']) for item in collected)).split()
            if len(have) > len(words) + 4:
                break
            extras = _edge_extras(have, words)
            if extras is None:
                continue
            lead, trail = extras
            if not end - .8 <= collected[-1]['end'] <= end + 1.2:
                continue
            if (not all(_ends_sentence(item) for item in collected[:-1])
                    or any(a['end'] > b['start'] + .001 for a, b in zip(collected, collected[1:]))):
                continue
            stops = sum(_ends_sentence(item) for item in collected)
            first_words = _normalized(str(collected[0]['text'])).split()
            last_words = _normalized(str(collected[-1]['text'])).split()
            if (not (stops > block_stops or len(collected) < len(block))
                    or len(first_words) <= lead or len(last_words) <= trail
                    or (len(collected) == len(block) and not lead and not trail)
                    or not _corroborates_extras(collected[0], first_words[:lead], v2)
                    or not _corroborates_extras(collected[-1], last_words[len(last_words) - trail:] if trail else [], v2)):
                continue
            return ([dict(item) for item in collected], first_words[:lead],
                    last_words[len(last_words) - trail:] if trail else [])
    return None


MAXIMUM_EDGE_OVERLAP = .5


def prefer_precision_for_unpunctuated_runs(merged, precision, v2=(), corroboration=()):
    """Where the long pass wrote a run of cues with no sentence end at all, use the short pass's sentences.

    Long-context Whisper on noisy audio sometimes writes whole stretches in lower case without a full stop, cut in
    the middle of sentences ("...from the planet" + "ahead how old is it...") and with a lost word at the edge
    ("Captain,"). The short pass often heard the SAME words as properly punctuated sentences. A run of
    consecutive cues without a sentence end is replaced only when consecutive confident short-pass sentences hold
    exactly the same words (extra edge words need confidence or v2 corroboration), cover the same time and do not
    overlap the neighbouring cues. The wording never changes otherwise; cues that already end a sentence are never
    touched. `corroboration` (for example a Qwen3-ASR reading) may confirm an extra edge word but never supplies text.
    """
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    shorter = [dict(row) for row in sorted(precision, key=lambda item: item['start'])]
    second_opinion = [dict(row) for row in sorted(v2 or (), key=lambda item: item['start'])]
    extra = [dict(row) for row in corroboration or ()]
    result = []
    index = 0
    while index < len(rows):
        if _ends_sentence(rows[index]):
            result.append(rows[index])
            index += 1
            continue
        limit = index
        while (limit + 1 < len(rows) and not _ends_sentence(rows[limit])
               and rows[limit + 1]['start'] - rows[limit]['end'] <= 3.0):
            limit += 1
        chosen = None
        for last in range(limit, index - 1, -1):
            match = (_match_precision_run(rows[index:last + 1], shorter, second_opinion + extra)
                     or _match_precision_run(rows[index:last + 1], second_opinion, shorter + extra))
            if match is None:
                continue
            replacement, lead_extra, trail_extra = match
            # Extra edge words that the neighbouring cue already holds are repeats, not recovered words.
            before = _normalized(str(result[-1]['text'])).split() if result else []
            after = _normalized(str(rows[last + 1]['text'])).split() if last + 1 < len(rows) else []
            if ((lead_extra and before[len(before) - len(lead_extra):] == lead_extra)
                    or (trail_extra and after[:len(trail_extra)] == trail_extra)):
                continue
            # A small overlap with a neighbour is the passes' timing, not other speech: the edge is cut at the neighbour (APOCALYPSE Z, dev
            # job 185: "Move away and keep walking, sir." ends 0.31 s into the long pass's "Do you hear me?" and the split "Move away and keep" /
            # "walking." stayed). A larger overlap still refuses the replacement.
            if result and replacement[0]['start'] < result[-1]['end'] - .05:
                if result[-1]['end'] - replacement[0]['start'] > MAXIMUM_EDGE_OVERLAP:
                    continue
                replacement[0] = {**replacement[0], 'start': result[-1]['end']}
            if last + 1 < len(rows) and replacement[-1]['end'] > rows[last + 1]['start'] + .05:
                if replacement[-1]['end'] - rows[last + 1]['start'] > MAXIMUM_EDGE_OVERLAP:
                    continue
                replacement[-1] = {**replacement[-1], 'end': rows[last + 1]['start']}
            if any(item['end'] - item['start'] < .3 for item in replacement):
                continue
            chosen = (last, replacement)
            break
        if chosen:
            result.extend(chosen[1])
            index = chosen[0] + 1
        else:
            result.append(rows[index])
            index += 1
    return result


def _word_key(token):
    return _normalized(token).replace(' ', '')


def _resegment_block(block, shorter, v2):
    """The short pass's sentences with the long pass's own words, for a block of cues that has no sentence end.

    `prefer_precision_for_unpunctuated_runs` needs the short pass to hold EXACTLY the block's words. Real case (Man of
    War, dev job 49): the long pass wrote "way between two points is a straight line let's go russia overnight" /
    "launched its long" / "anticipated attack on ukraine he's about to jam the door mike i'm in" / "trouble we need your
    help" (cut in the middle of the sentence, lower case, no full stop) while the short pass heard six proper sentences,
    one word more ("Fastest") and one less ("in") than the long pass, so the exact match never applied. Here the words are
    aligned instead: the short pass decides WHERE the cues begin and end and how they are punctuated, the long pass keeps
    its own wording wherever the two differ (the short pass's different or extra words inside a sentence are never
    taken), and an extra word at the very edge is taken only when it is confident or heard by a second reading.
    Returns the new cues, or None when the readings do not match well enough."""
    block_tokens = [token for row in block for token in str(row['text']).split()]
    block_keys = [_word_key(token) for token in block_tokens]
    if len(block_keys) < 4 or not all(block_keys):
        return None
    start, end = block[0]['start'], block[-1]['end']
    pool = [row for row in shorter if row['end'] > start - 2.0 and row['start'] < end + 1.2]
    block_stops = sum(_ends_sentence(row) for row in block)
    for first in range(len(pool)):
        if not start - 1.6 <= pool[first]['start'] <= start + .8:
            continue
        collected = []
        for row in pool[first:]:
            collected.append(row)
            if len(collected) > len(block_tokens) or not end - .8 <= collected[-1]['end'] <= end + 1.2:
                continue
            if (not all(_ends_sentence(item) for item in collected[:-1])
                    or any(a['end'] > b['start'] + .001 for a, b in zip(collected, collected[1:]))
                    or sum(_ends_sentence(item) for item in collected) <= block_stops
                    or len(collected) < len(block)):           # only ever cuts a run into MORE sentences
                continue
            tokens = [(token, cue) for cue, item in enumerate(collected) for token in str(item['text']).split()]
            keys = [_word_key(token) for token, _ in tokens]
            if not all(keys):
                continue
            matcher = SequenceMatcher(None, block_keys, keys, autojunk=False)
            operations = matcher.get_opcodes()
            operations = [('equal', a1, a2, b1, b2) if tag == 'replace' and ''.join(block_keys[a1:a2]) == ''.join(keys[b1:b2])
                          else (tag, a1, a2, b1, b2) for tag, a1, a2, b1, b2 in operations]    # long-anticipated
            changed = sum(max(a2 - a1, b2 - b1) for tag, a1, a2, b1, b2 in operations if tag != 'equal')
            if (matcher.ratio() < .85 or changed > max(2, round(.12 * len(block_keys)))
                    or operations[0][0] in ('delete', 'replace') or operations[-1][0] in ('delete', 'replace')):
                continue
            built = _assemble_resegmented(block_tokens, tokens, collected, operations, v2)
            if built:
                return built
    return None


def _heard_in_order(keys, cues, others):
    """True when one of the other readings that overlap `cues` holds the word keys `keys` one after the other."""
    start, end = cues[0]['start'] - .5, cues[-1]['end'] + .5
    for other in others:
        if other.get('end', 0) <= start or other.get('start', 0) >= end:
            continue
        heard = [_word_key(token) for token in str(other.get('text', '')).split()]
        if any(heard[index:index + len(keys)] == keys for index in range(len(heard) - len(keys) + 1)):
            return True
    return False


def _assemble_resegmented(block_tokens, tokens, collected, operations, v2):
    """Cues made of the long pass's words laid out on the short pass's sentence boundaries (see _resegment_block)."""
    texts = [[] for _ in collected]
    for tag, a1, a2, b1, b2 in operations:
        if tag == 'equal':
            for offset in range(b2 - b1):
                token, cue = tokens[b1 + offset]
                texts[cue].append(token)                      # the short pass's own spelling and punctuation
        elif tag == 'insert':                                 # words only the short pass heard
            edge_lead, edge_trail = b1 == 0, b2 == len(tokens)
            if not (edge_lead or edge_trail) or b2 - b1 > 2:
                continue
            cue = tokens[b1][1]
            extras = [_word_key(token) for token, _ in tokens[b1:b2]]
            if not _corroborates_extras(collected[cue], extras, v2):
                continue
            texts[cue].extend(token for token, _ in tokens[b1:b2])
        else:                                                 # delete / replace: the long pass's words stay
            if tag == 'delete' and 0 < b1 < len(tokens) and tokens[b1 - 1][1] != tokens[b1][1]:
                # Words only the long pass heard, standing exactly between two of the short pass's sentences, belong to neither: appended to the first
                # they came out as "He kept digging and digging. and." (WIFE AND DOG, job 154: the long pass wrote "digging, and" before a pause, the short
                # pass, Whisper large-v2 and Qwen3-ASR heard no "and", and neither did a clean reading of the clip). They are kept, and the run is left
                # as the long pass wrote it, only when another reading has them between the same two neighbours; otherwise they are dropped.
                around = [_word_key(tokens[b1 - 1][0]), *(_word_key(token) for token in block_tokens[a1:a2]), _word_key(tokens[b1][0])]
                if _heard_in_order(around, collected, v2):
                    return None
                continue
            if tag == 'replace':
                cue = tokens[b1][1]
                if tokens[b2 - 1][1] != cue:                  # the differing words straddle a sentence boundary
                    return None
            else:
                cue = tokens[b1 - 1][1] if b1 > 0 else tokens[0][1]
            texts[cue].extend(block_tokens[a1:a2])
    if any(not words for words in texts):
        return None
    built = []
    for cue, (item, words) in enumerate(zip(collected, texts)):
        words = list(words)
        final_mark = str(item['text']).rstrip()[-1:]
        if final_mark in '.?!' and not words[-1].rstrip().endswith(('.', '?', '!')):
            words[-1] = words[-1].rstrip(',;:') + final_mark
        if built and built[-1]['text'].rstrip().endswith(('.', '?', '!')) and words[0].islower():
            words[0] = words[0][:1].upper() + words[0][1:]
        row = dict(item)
        row['text'] = ' '.join(words)
        built.append(row)
    return built


def resegment_unpunctuated_runs(merged, precision, v2=(), corroboration=()):
    """Where the long pass wrote cues with no sentence end that `prefer_precision_for_unpunctuated_runs` could not
    replace, lay its words out on the short pass's sentence boundaries (see `_resegment_block`). Cues that already end a
    sentence are never touched; neither is a run whose replacement would overlap, or repeat words of, a neighbour."""
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    shorter = [dict(row) for row in sorted(precision, key=lambda item: item['start'])]
    others = [dict(row) for row in list(v2 or ()) + list(corroboration or ())]
    result = []
    index = 0
    while index < len(rows):
        if _ends_sentence(rows[index]):
            result.append(rows[index])
            index += 1
            continue
        limit = index
        while (limit + 1 < len(rows) and not _ends_sentence(rows[limit])
               and rows[limit + 1]['start'] - rows[limit]['end'] <= 3.0):
            limit += 1
        chosen = None
        for last in range(limit, index - 1, -1):
            built = _resegment_block(rows[index:last + 1], shorter, others)
            if not built:
                continue
            before = _normalized(str(result[-1]['text'])).split() if result else []
            after = _normalized(str(rows[last + 1]['text'])).split() if last + 1 < len(rows) else []
            head = _normalized(str(built[0]['text'])).split()
            tail = _normalized(str(built[-1]['text'])).split()
            if ((before and head[:min(2, len(head))] == before[-min(2, len(before)):] and len(before) >= 2)
                    or (after and tail[-min(2, len(tail)):] == after[:min(2, len(after))] and len(after) >= 2)
                    or (result and built[0]['start'] < result[-1]['end'] - .05)
                    or (last + 1 < len(rows) and built[-1]['end'] > rows[last + 1]['start'] + .05)):
                continue
            chosen = (last, built)
            break
        if chosen:
            result.extend(chosen[1])
            index = chosen[0] + 1
        else:
            result.append(rows[index])
            index += 1
    return result


def drop_misplaced_duplicate_cues(merged, precision, strict_speech, *, minimum_shift=5.0):
    """Remove a cue whose words were heard properly elsewhere and that sits where nothing is spoken.

    The no-VAD pass that listens in quiet stretches sometimes time-stamps a line far from where it is spoken
    ("Record everything." at 77.7 s, spoken at 91 s). Such a cue is dropped only when the short pass holds the very
    same words at least `minimum_shift` seconds away AND voice activity confirms speech there but none at the
    cue itself, so a line that is really repeated (speech at both times) is never touched.
    """
    def has_speech(start, end):
        return any(item['end'] > start - .3 and item['start'] < end + .3 for item in strict_speech)

    references = [(_normalized(str(row['text'])), row) for row in precision
                  if len(_normalized(str(row['text'])).split()) >= 2]
    kept = []
    for row in merged:
        words = _normalized(str(row['text']))
        misplaced = (len(words.split()) >= 2 and not has_speech(row['start'], row['end'])
                     and any(text == words and abs(other['start'] - row['start']) >= minimum_shift
                             and has_speech(other['start'], other['end']) for text, other in references))
        if not misplaced:
            kept.append(row)
    return kept


def drop_unconfirmed_repeated_starts(merged, precision, *, minimum_words=4, maximum_gap=.3):
    """Remove a cue that only repeats the first words of the cue before it, when the short pass hears them once.

    Whisper's long pass sometimes starts a sentence again where a window ends (Yukon Men 34:15: "the hunting party
    approaches the area where the caribou herd" and, 0.0 s later, "the hunting party approaches the area"). The extra
    cue makes the translation one line too long for 18 lines. Only an exact repeat of the opening words of the longer
    cue before it, in the same breath, with `minimum_words` or more words, and heard once by the short pass is removed;
    a phrase the speaker really said twice is in the short pass twice and stays. Returns (rows, removed).
    """
    def count_in(needle, start, end):
        around = ' '.join(_normalized(str(row['text'])) for row in precision if row['end'] > start - 1 and row['start'] < end + 1)
        return len(re.findall(r'(?<!\w)' + re.escape(needle) + r'(?!\w)', around)) if around else 0

    kept, removed = [], []
    for row in merged:
        previous = kept[-1] if kept else None
        if previous is not None:
            words, before = _normalized(str(row['text'])).split(), _normalized(str(previous['text'])).split()
            if (len(words) >= minimum_words and len(before) > len(words) and before[:len(words)] == words
                    and row['start'] - previous['end'] <= maximum_gap
                    and count_in(' '.join(words), previous['start'], row['end']) < 2):
                removed.append(row)
                continue
        kept.append(row)
    return kept, removed


def _is_name_like(word, rows):
    """A capitalised word that the file also writes with a capital where it does not start a sentence is a name ("Kiev", "Riley")."""
    bare = word.strip('.,!?;:"\'')
    for row in rows:
        words = str(row.get('text', '')).split()
        for before, current in zip(words, words[1:]):
            if current.strip('.,!?;:"\'') == bare and before[-1:] not in '.!?':
                return True
    return False


def remove_adjacent_phrase_overlap(base, v3_rows, v2_rows, *, minimum_words=4, skip_indices=(), corroboration=()):
    """Remove a copied suffix/prefix only when two timed families locate it ONCE.

    Keep all other words, including a short lead already present in the left cue.
    The retained left text must itself exist as a whole earlier reader cue. Two
    distinct timed occurrences, missing words or protected prefixes veto a change.
    """
    rows = [dict(row) for row in base]
    changes = []
    v3_index, v2_index, extra_index = (_time_index(items) for items in (v3_rows, v2_rows, corroboration))

    def occurrences(profile, needle, begin, finish):
        found = []
        for cue in _rows_near(profile, begin, finish):
            words = _timed_rescue_words(cue)
            for offset in range(len(words) - len(needle) + 1):
                span = words[offset:offset + len(needle)]
                if ([w['token'] for w in span] != needle
                        or not begin <= span[0]['start'] < span[-1]['end'] <= finish
                        or any(not math.isfinite(w[k]) for w in span for k in ('start', 'end', 'probability'))
                        or any(not 0 < w['end'] - w['start'] <= 1.5 for w in span)
                        or any(a['end'] > b['start'] + .02 for a, b in zip(span, span[1:])
                               if a['source_word_index'] != b['source_word_index'])
                        or offset and words[offset - 1]['source_word_index'] == span[0]['source_word_index']):
                    continue
                if offset + len(needle) < len(words) and words[offset + len(needle)]['source_word_index'] == span[-1]['source_word_index']:
                    continue
                # Repeated windows at the same physical time are one occurrence.
                if not any(abs(old[1][0]['start'] - span[0]['start']) <= .25
                           and abs(old[1][-1]['end'] - span[-1]['end']) <= .25 for old in found):
                    found.append((cue, span))
        return found

    for index in range(len(rows) - 1):
        left, right = rows[index:index + 2]
        if (index in skip_indices or index + 1 in skip_indices
                or not 0 <= right['start'] - left['end'] <= .3
                or right.get('confirmed_lead_onset') or right.get('confirmed_prefix')):
            continue
        left_raw, right_raw = _raw_tokens(left['text']), _raw_tokens(right['text'])
        own = [token for _, parts in left_raw for token in parts]
        following = [token for _, parts in right_raw for token in parts]
        for size in range(min(len(own) - 2, len(following) - 1), minimum_words - 1, -1):
            needle = own[-size:]
            if following[:size] != needle:
                continue
            if any(c['start'] < right['end'] and c['end'] > left['start']
                   and len(re.findall(r'(?<!\w)' + re.escape(' '.join(needle)) + r'(?!\w)', _normalized(c.get('text', '')))) > 1
                   for c in _rows_near(extra_index, left['start'], right['end'])):
                continue
            first = occurrences(v3_index, needle, left['start'], right['end'])
            second = occurrences(v2_index, needle, left['start'], right['end'])
            if len(first) != 1 or len(second) != 1:
                continue
            cue, span = first[0]
            other = second[0][1]
            if (any(min(w['probability'] for w in group) < .5 or sum(w['probability'] >= .75 for w in group) < 4
                    for group in (span, other))
                    or abs(span[0]['start'] - other[0]['start']) > .3
                    or abs(span[-1]['end'] - other[-1]['end']) > .3
                    or min(span[-1]['end'], other[-1]['end']) <= left['end'] + .25):
                continue
            lead_words = cue['words'][:span[0]['source_word_index']]
            if (len(lead_words) > 2 or any(
                    not all(math.isfinite(float(word[key])) for key in ('start', 'end', 'probability'))
                    or not 0 < word['end'] - word['start'] <= 1.5 or word['probability'] < .75 for word in lead_words)):
                continue
            lead = ' '.join(word['text'].strip() for word in lead_words)
            tail = _normalized(lead).split() + needle
            if own[-len(tail):] != tail:
                continue
            cut = len(own) - len(tail)
            boundary = 0
            raw_cut = None
            for slot, (_, parts) in enumerate(left_raw):
                if boundary == cut:
                    raw_cut = slot
                    break
                boundary += len(parts)
            if raw_cut is None or raw_cut < 2:
                continue
            kept = ' '.join(raw for raw, _ in left_raw[:raw_cut]).rstrip(' ,;:-')
            start = float(lead_words[0]['start']) if lead_words else min(span[0]['start'], other[0]['start'])
            start = round(start - .08, 3)
            if (not left['start'] + .4 < start <= right['start']
                    or right['end'] - start > 7
                    or not any(_normalized(c['text']) == _normalized(kept) and c['end'] <= start
                               for c in _rows_near(v3_index, left['start'], start))
                    or any(word not in _normalized(kept).split() for word in left.get('confirmed_source_words') or [])):
                continue
            rows[index] = {**left, 'text': kept, 'end': start}
            # The right row no longer starts the sentence: "We're Trying to live their lives" (MAN OF WAR, dev job 262) keeps no capital after the
            # moved lead (except "I" and words written in capitals throughout, such as names and acronyms).
            rest = right['text']
            first = rest.split(' ', 1)[0]
            if lead and first[:1].isupper() and first[1:2].islower() and first not in ('I', "I'm", "I'll", "I've", "I'd") and not _is_name_like(first, rows):
                rest = rest[:1].lower() + rest[1:]
            rows[index + 1] = {**right, 'text': (lead + ' ' if lead else '') + rest, 'start': start}
            rows[index].pop('words', None)
            rows[index + 1].pop('words', None)
            changes.append({'rows': [index, index + 1], 'duplicate': ' '.join(needle),
                            'moved_lead': lead, 'boundary': start,
                            'families': ['whisper-v3', 'whisper-v2']})
            break
    return rows, changes


_TITLE_ROWS = frozenset({'mr', 'mrs', 'ms', 'miss', 'dr', 'prof', 'mister', 'herr', 'frau', 'fru', 'doktor', 'sir', 'madam'})


def join_title_rows(merged, *, maximum_gap=.5, maximum_length=7.0):
    """A row that holds only a title ("Mr.") is joined to the row that follows it when that starts within `maximum_gap` seconds with a capital letter ("Gunn, I presume?").
    Every pass put a break after "Mr." in Ray Gunn (48.2-49.0 and 49.0-49.9 s, no pause between them), so the subtitle showed "Herr" alone for a second.
    Returns (rows, joined)."""
    rows = [dict(row) for row in merged]
    result, joined = [], []
    index = 0
    while index < len(rows):
        row = rows[index]
        following = rows[index + 1] if index + 1 < len(rows) else None
        tokens = _normalized(str(row.get('text', ''))).split()
        if (following is not None and len(tokens) == 1 and tokens[0] in _TITLE_ROWS and -.1 <= following['start'] - row['end'] <= maximum_gap
                and str(following.get('text', '')).lstrip()[:1].isupper() and following['end'] - row['start'] <= maximum_length):
            combined = dict(following)
            combined['start'] = row['start']
            combined['text'] = f"{str(row['text']).strip()} {str(following['text']).strip()}"
            confidences = [float(item['confidence']) for item in (row, following) if item.get('confidence') is not None and float(item.get('confidence', 0)) > 0]
            if confidences:
                combined['confidence'] = min(confidences)
            joined.append({'start': row['start'], 'from': [row['text'], following['text']], 'to': combined['text']})
            result.append(combined)
            index += 2
        else:
            result.append(row)
            index += 1
    return result, joined


def harmonize_names_by_readings(merged, readings, *, minimum_families=2, minimum_elsewhere=2):
    """A name that only ONE pass wrote, in a row where two other model families both read it the same other way that the file spells the name elsewhere, is written
    as those readings and the file write it. Ray Gunn, "Be careful, Ray.": context (large-v3) and large-v2 read "Ray", only the rescue pass read "Rey", and the file's
    other lines say "Ray" twice; the cue took the rescue text, which had no recorded confidence and so was never read again. Everything else in the row must be
    identical, the new spelling must occur at least `minimum_elsewhere` times in the other rows and the old one nowhere else. Returns (rows, changes)."""
    rows = [dict(row) for row in merged]
    indexed = {name: _time_index(found) for name, found in readings.items()}
    tokens_of = lambda text: re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", str(text))
    changes = []
    for position, row in enumerate(rows):
        words = tokens_of(row.get('text', ''))
        elsewhere = [word for other in rows[:position] + rows[position + 1:] for word in tokens_of(other.get('text', ''))]
        for slot, word in enumerate(words):
            if len(word) < 3 or not word[0].isupper() or not word[1:].islower() or elsewhere.count(word) > 0:
                continue
            votes = {}
            for name, found in indexed.items():
                for other in _rows_near(found, row['start'], row['end']):
                    span = max(row['end'] - row['start'], 1e-6)
                    if _overlap_seconds(row, other) < .7 * span:           # the reading covers the row (a long window around a short row is fine: the words must match exactly)
                        continue
                    other_words = tokens_of(other.get('text', ''))
                    if len(other_words) != len(words) or other_words[slot] == word or other_words[:slot] + other_words[slot + 1:] != words[:slot] + words[slot + 1:]:
                        continue
                    replacement = other_words[slot]
                    if replacement[:1].isupper() and len(replacement) == len(word) and sum(a != b for a, b in zip(replacement, word)) == 1:
                        votes.setdefault(replacement, set()).add(_family(name))
            for replacement, families in votes.items():
                if len(families) >= minimum_families and elsewhere.count(replacement) >= minimum_elsewhere and len(votes) == 1:
                    row['text'] = re.sub(rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])", replacement, str(row['text']), count=1)
                    changes.append({'start': row['start'], 'from': word, 'to': replacement, 'families': sorted(families)})
                    break
    return rows, changes


def drop_credit_rows(merged, *, isolation=2.0, maximum_words=8):
    """Remove a short closing-credit line Whisper wrote into music or silence ("Teksting av", "Sous-titrage ST'").

    Only a row that matches the credit phrases, has at most `maximum_words` words and stands alone (no other row within `isolation` seconds
    on either side) is removed, so a line that is really spoken between other lines stays. Returns (rows, removed).
    """
    kept, removed = [], []
    for index, row in enumerate(merged):
        before = merged[index - 1]['end'] if index else None
        after = merged[index + 1]['start'] if index + 1 < len(merged) else None
        alone = ((before is None or row['start'] - before >= isolation) and (after is None or after - row['end'] >= isolation))
        if _is_boilerplate(row.get('text', '')) and len(_normalized(str(row.get('text', ''))).split()) <= maximum_words and alone:
            removed.append(row)
        else:
            kept.append(row)
    return kept, removed


FAMILY_VOTE_MINIMUM_FAMILIES = 2
FAMILY_VOTE_MINIMUM_AGREEMENT = .6
FAMILY_VOTE_INDEPENDENT_FAMILIES = False

_STOCK_PHRASES = {
    'thank you', 'thanks', 'thank you very much', 'merci', 'merci beaucoup',
    'you', 'okay', 'ok', 'bye', 'goodbye', 'subscribe', 'we ll be right back',
}
# Whisper's closing-credit hallucinations in music and silence ("한글자막 제공 및 광고를 포함하고 있습니다").
_BOILERPLATE = ('한글자막', '자막 제공', '시청해 주셔서', '시청해주셔서', '구독과 좋아요', 'thanks for watching',
                'thank you for watching', 'subtitles by', 'subtitled by', 'amara org', 'sous titrage', 'sous titres',
                'ご視聴', '字幕', 'undertexter från', 'untertitel',
                # the Nordic credits Whisper writes into music and silence ("Teksting av Nicolai Winther" in job 25, "Teksting av" in job 126)
                'teksting av', 'tekstet af', 'tekster af', 'textning av', 'undertextning', 'undertekster af')


def _is_boilerplate(text):
    lowered = ' '.join(str(text).lower().split())
    return any(phrase in lowered or phrase in _normalized(lowered) for phrase in _BOILERPLATE)


def _similarity(a, b):
    return SequenceMatcher(None, _normalized(str(a)), _normalized(str(b))).ratio()


def _overlap_seconds(a, b):
    return min(a['end'], b['end']) - max(a['start'], b['start'])


def _base_name(name):
    """A pass name without the "+" that marks its pieces joined into one reading."""
    return name.rstrip('+')


def _cluster_readings(readings):
    """Group the passes' cues into clusters of readings of the same speech (same time, similar text)."""
    items = [(name, row) for name, rows in readings.items() for row in (rows or ())
             if _normalized(str(row.get('text', ''))) and not _is_boilerplate(row.get('text', ''))]
    return _cluster_readings_of(items)


def _cluster_readings_of(items):
    """Cluster (pass name, cue) pairs: same time and similar text with any member links a cue to a cluster."""
    clusters = []
    for name, row in sorted(items, key=lambda item: item[1]['start']):
        for cluster in clusters:
            if any(_overlap_seconds(row, other) > .4 * min(row['end'] - row['start'], other['end'] - other['start'])
                   and _similarity(row['text'], other['text']) >= .5 for _, other in cluster):
                cluster.append((name, row))
                break
        else:
            clusters.append([(name, row)])
    return clusters


def _linked_readings(start, pool):
    """Everything in `pool` linked to the cue `start` directly or through other readings (same time, similar text)."""
    def linked(a, b):
        return (_overlap_seconds(a, b) > .4 * min(a['end'] - a['start'], b['end'] - b['start'])
                and _similarity(a['text'], b['text']) >= .5)
    found, queue = [], [start]
    remaining = list(pool)
    while queue:
        current = queue.pop()
        for entry in [item for item in remaining if linked(current, item[1])]:
            remaining.remove(entry)
            found.append(entry)
            queue.append(entry[1])
    return found


def _time_index(rows):
    """Rows sorted by start with their longest duration, for `_rows_near`."""
    ordered = sorted((row for row in rows or () if 'start' in row and 'end' in row), key=lambda row: row['start'])
    longest = max((row['end'] - row['start'] for row in ordered), default=0.0)
    return ordered, [row['start'] for row in ordered], max(longest, 0.0)


def _rows_near(index, start, end):
    """The indexed rows that can touch the time span [start, end] (a superset: callers still test the overlap),
    in the order of their start. A film has thousands of cues, so scanning every row for every cue is quadratic."""
    ordered, starts, longest = index
    low = bisect.bisect_left(starts, start - longest - 1e-6)
    high = bisect.bisect_right(starts, end + 1e-6)
    return ordered[low:high]


def span_readings(final, readings):
    """Per final cue, each pass's pieces inside it joined into ONE reading (named "<pass>+").

    The passes cut the same speech differently ("미꾸라지 마냥" / "미끄러운 거래" / "한 20분쯤 걸었는데" against one
    cue), and pieces of different length never match the cue in a comparison; their joined text does.
    """
    joined = {}
    indexed = {name: _time_index(rows) for name, rows in readings.items()}
    for cue in final:
        window = {'start': cue['start'] - .3, 'end': cue['end'] + .3}
        for name, index in indexed.items():
            pieces = [row for row in _rows_near(index, window['start'], window['end'])
                      if row['end'] > row['start'] and _normalized(str(row.get('text', '')))
                      and _overlap_seconds(row, window) >= .6 * (row['end'] - row['start'])]
            if len(pieces) >= 2:
                pieces.sort(key=lambda row: row['start'])
                joined.setdefault(name + '+', []).append({
                    'start': pieces[0]['start'], 'end': max(row['end'] for row in pieces),
                    'text': ' '.join(str(row['text']).strip() for row in pieces),
                    'confidence': sum(float(row.get('confidence', 0)) for row in pieces) / len(pieces)})
    return joined


# The passes that run Whisper large-v3 in the ordinary way share their mistakes, so they vote as ONE family; the
# forced-language reading without voice-activity filter, Whisper v2 and Qwen3-ASR each vote as a family of their own.
_FAMILIES = {'context': 'whisper-v3', 'precision': 'whisper-v3', 'rescue': 'whisper-v3',
             'whispered_gap': 'whisper-v3', 'v2': 'whisper-v2', 'qwen': 'qwen', 'forced': 'whisper-v3-forced',
             'nb': 'nb-whisper'}        # NB-Whisper, the optional Norwegian extra reader (app/norwegian_reader.py)


def _family(name):
    return _FAMILIES.get(_base_name(name), _base_name(name))


# Models other than Whisper large-v3 itself: what the v3 passes (also the forced re-read) agree on can be their shared mistake.
_INDEPENDENT_FAMILIES = frozenset({'whisper-v2', 'qwen', 'nb-whisper'})


def _mean_agreement(text, cluster, skip=None):
    """How well `text` agrees with the readings of a cluster: the mean over model families of its mean similarity
    to that family's readings (the reading `skip`, which is the text itself, does not vote)."""
    families = {}
    for name, other in cluster:
        if other is not skip:
            families.setdefault(_family(name), []).append(_similarity(text, other['text']))
    return sum(sum(values) / len(values) for values in families.values()) / len(families) if families else 0.0


def _medoid(cluster):
    """The reading that agrees best with the others (ties: the most confident one)."""
    def score(entry):
        _, row = entry
        return _mean_agreement(row['text'], cluster, skip=row), float(row.get('confidence', 0))
    return max(cluster, key=score)


def _eligible_cluster(cluster, minimum_passes, vad_passes):
    names = {_base_name(name) for name, _ in cluster}
    return len(names) >= minimum_passes and bool(names.intersection(vad_passes))


def _chosen_row(cluster, name, chosen):
    """The chosen reading with the cluster's typical timing: a Qwen clip's window is only a bound."""
    row = dict(chosen)
    others = [item for other_name, item in cluster if other_name != 'qwen']
    if name == 'qwen' and others:
        row['start'] = sorted(item['start'] for item in others)[len(others) // 2]
        row['end'] = sorted(item['end'] for item in others)[len(others) // 2]
    return row


def _uncovered_restore_row(row, cluster, existing):
    """Use another complete word-timed reading, never clip words to fit a gap.

    Selection confidence decides wording, not which pass timed the boundary best.
    Only the existing small-overlap range is eligible. Qwen window bounds and
    different wordings cannot supply an alternative timing; neighbours stay intact.
    """
    conflicts = [other for other in existing if _overlap_seconds(row, other) > .05]
    if not conflicts:
        return row
    text = _normalized(str(row['text']))
    if any(_overlap_seconds(row, other) > .25 or text in _normalized(str(other['text']))
           for other in conflicts):
        return None
    alternatives = []
    for name, candidate in cluster:
        words = candidate.get('words') or []
        if (_base_name(name) == 'qwen' or not words
                or _normalized(str(candidate['text'])) != text
                or _normalized(' '.join(str(word.get('text', '')) for word in words)) != text
                or any(_overlap_seconds(candidate, other) > .05 for other in existing)
                or _overlap_seconds(candidate, row) <= .4 * min(candidate['end'] - candidate['start'], row['end'] - row['start'])):
            continue
        end = candidate['start']
        for word in words:
            if (word.get('probability', 0) < .55 or word['start'] < end
                    or not candidate['start'] <= word['start'] < word['end'] <= candidate['end']
                    or word['end'] - word['start'] > 1.5):
                break
            end = word['end']
        else:
            alternatives.append(candidate)
    if not alternatives:
        return None
    chosen = min(alternatives, key=lambda other: abs(other['start'] - row['start']) + abs(other['end'] - row['end']))
    return dict(chosen, text=row['text'])


def restore_multi_pass_agreed_speech(merged, readings, *, minimum_passes=3, vad_passes=('precision', 'context'),
                                     minimum_agreement=.85):
    """Add speech that several independent passes read alike but that no final cue covers.

    The final cues can miss real lines in music-heavy audio: voice-activity filtering fails them as unsupported
    although the short pass, the no-VAD rescue, the quiet-gap pass and Whisper v2 all read the same words
    (HOPE: "...저희 집에 있다니까요" in five passes, "Tristan" in Lou in three). A cluster is added only when at
    least `minimum_passes` distinct passes agree, one of them voice-activity filtered (a no-VAD pass alone is what
    hallucinates in music), the readings are not a stock phrase, a lone word is confident, and the cue would not
    overlap a final cue. The text is the reading closest to all the others, and the passes must agree on it
    (minimum_agreement): where they read different words ("국회" or "괴물") the line is left out rather than guessed.
    """
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    additions = []
    for cluster in _cluster_readings(readings):
        if not _eligible_cluster(cluster, minimum_passes, vad_passes):
            continue
        name, best = _medoid(cluster)
        if _mean_agreement(best['text'], cluster, skip=best) < minimum_agreement:
            continue
        row = _chosen_row(cluster, name, best)
        words = _normalized(str(row['text'])).split()
        span = max(item['end'] for _, item in cluster) - min(item['start'] for _, item in cluster)
        if (not words or _normalized(str(row['text'])) in _STOCK_PHRASES or span > 12
                or (len(words) == 1 and max(float(item.get('confidence', 0)) for _, item in cluster) < .55)):
            continue
        row = _uncovered_restore_row(row, cluster, rows + additions)
        if row is not None:
            additions.append(row)
    return sorted(rows + additions, key=lambda item: (item['start'], item['end']))


def restore_family_voted_speech(merged, readings, *, minimum_families=FAMILY_VOTE_MINIMUM_FAMILIES, minimum_agreement=FAMILY_VOTE_MINIMUM_AGREEMENT,
                                independent_families=FAMILY_VOTE_INDEPENDENT_FAMILIES):
    """Add speech that no final cue covers when at least two MODEL FAMILIES read it alike, however the others read it.

    `restore_multi_pass_agreed_speech` needs three passes, one of them voice-activity filtered, and a high mean agreement over everything that was
    read there, so one dissenting family ("국회문" from the Whisper v3 passes against "괴물" from v2 and Qwen) leaves a real line out. On the HOPE trailer
    (burned-in English subtitles as the witness: 14 spoken lines) the final transcript held 5 of them; the passes had read 9 more. Here every family
    casts ONE vote (its own best reading), the largest group of families whose readings agree (`minimum_agreement`, mean similarity) wins, and its
    medoid is the text; the families outside the group are outvoted. No voice-activity pass is required: that rule exists because a no-VAD pass alone
    hallucinates in music, and a line read alike by two families is not one pass's hallucination. `independent_families` demands that the group
    contains Whisper v2 or Qwen3-ASR (other models than the v3 passes, which share their mistakes). Stock phrases, a lone unsure word, a span over
    12 seconds and anything overlapping a cue are left out as in the older rule.
    """
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    additions = []
    for cluster in _cluster_readings(readings):
        by_family = {}
        for name, row in cluster:
            by_family.setdefault(_family(name), []).append((name, row))
        if len(by_family) < minimum_families:
            continue
        representatives = {family: (_medoid(members) if len(members) > 1 else members[0]) for family, members in by_family.items()}
        group = None
        for size in range(len(representatives), minimum_families - 1, -1):
            for chosen_families in itertools.combinations(sorted(representatives), size):
                if independent_families and not set(chosen_families).intersection(_INDEPENDENT_FAMILIES):
                    continue
                pairs = [_similarity(representatives[a][1]['text'], representatives[b][1]['text'])
                         for a, b in itertools.combinations(chosen_families, 2)]
                agreement = sum(pairs) / len(pairs)
                if agreement >= minimum_agreement and (group is None or agreement > group[0]):
                    group = (agreement, chosen_families)
            if group:
                break
        if group is None:
            continue
        members = [representatives[family] for family in group[1]]
        name, best = _medoid(members)
        name, best = _most_heard_reading(cluster, group[1], best['text']) or (name, best)
        row = _chosen_row(members, name, best)
        text = _normalized(str(row['text']))
        words = text.split()
        span = max(item['end'] for _, item in members) - min(item['start'] for _, item in members)
        if (not words or text in _STOCK_PHRASES or span > 12
                or (len(words) == 1 and max(float(item.get('confidence', 0)) for _, item in members) < .55)):
            continue
        timing_members = [(pass_name, item) for pass_name, item in cluster if _family(pass_name) in group[1]]
        row = _uncovered_restore_row(row, timing_members, rows + additions)
        if row is not None:
            additions.append(row)
    return sorted(rows + additions, key=lambda item: (item['start'], item['end']))


def _most_heard_reading(cluster, families, chosen_text, *, minimum_similarity=.5):
    """(name, reading) with the wording that more of `families` heard exactly than any other wording of the same words as `chosen_text`, or
    None. A medoid ties between two readings and then takes the more confident one, which can be a clipped reading: Godzilla (dev job 219) "避"
    (one v3 pass, confidence 1.0) was added where another v3 pass and large-v2 both heard "退避!" ("Evacuate!"). Only wordings close to the
    chosen text compete (a cluster can hold the next utterance too: Last Samurai "あれは" before "化け物だ"), and a tie in families changes
    nothing (HOPE "상황실!" from two families against "상황실 상황실" from two)."""
    heard = {}
    for name, item in cluster:
        text = _normalized(str(item.get('text', '')))
        if text and _family(name) in families and _similarity(item['text'], chosen_text) >= minimum_similarity:
            heard.setdefault(text, []).append((name, item))

    def support(entries):
        return len({_family(name) for name, _ in entries})
    ranked = sorted(heard.values(), key=support, reverse=True)
    if not ranked or support(ranked[0]) < 2 or (len(ranked) > 1 and support(ranked[0]) == support(ranked[1])):
        return None
    return _medoid(ranked[0]) if len(ranked[0]) > 1 else ranked[0][0]


def replace_outvoted_single_readings(merged, readings, *, maximum_confidence=.6, minimum_families=3, minimum_independent=2,
                                     minimum_cover=.3, own_support=.7, different=.4):
    """(rows, changes): replace a doubtful row that only one reading heard when at least `minimum_families` model families, two of them other
    than Whisper large-v3 (`_INDEPENDENT_FAMILIES`), heard the same different words at that place.

    Glemselens øy (dev job 216): the long pass alone wrote "Gjent mors" (confidence .43) at 121.6-123.3 s; the no-VAD v3 pass, large-v2 and
    NB-Whisper all read "Vi skal hjem." at 120.3-122.5 s. prefer_consensus_reading never saw them: their span overlaps the row by only 30 % of
    the union (it needs 50 %) and the words share nothing. Here a reading counts when it covers at least `minimum_cover` of the row; the row may
    have no second reading close to its own words (`own_support`), the agreeing readings must say exactly the same words (after normalising),
    and the replacement takes that reading's own timing only when it does not reach into a neighbouring row. Nothing else changes."""
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    changes = []
    for index, row in enumerate(rows):
        own = _normalized(str(row.get('text', '')))
        duration = row['end'] - row['start']
        if not own or duration <= 0 or float(row.get('confidence', 0)) >= maximum_confidence:
            continue
        covering = [(name, other) for name, items in readings.items() for other in (items or ())
                    if _normalized(str(other.get('text', ''))) and not _is_boilerplate(other.get('text', ''))
                    and _overlap_seconds(row, other) >= minimum_cover * duration]
        if sum(1 for _, other in covering if _similarity(row['text'], other['text']) >= own_support) > 1:
            continue                                    # a second reading heard the row's own words
        groups = {}
        for name, other in covering:
            if _similarity(row['text'], other['text']) < different:
                groups.setdefault(_normalized(str(other['text'])), []).append((name, other))
        best = None
        for entries in groups.values():
            families = {_family(name) for name, _ in entries}
            if len(families) >= minimum_families and len(families & _INDEPENDENT_FAMILIES) >= minimum_independent:
                if best is None or len(families) > len({_family(name) for name, _ in best}):
                    best = entries
        if best is None:
            continue
        timed = [other for name, other in best if _base_name(name) != 'qwen'] or [other for _, other in best]
        chosen = max(timed, key=lambda other: float(other.get('confidence', 0)))
        start, end = chosen['start'], chosen['end']
        previous = rows[index - 1] if index else None
        following = rows[index + 1] if index + 1 < len(rows) else None
        if (previous and start < previous['end'] - .05) or (following and end > following['start'] + .05):
            start, end = row['start'], row['end']        # the neighbours stay intact: keep the row's own timing
        replacement = {'start': start, 'end': end, 'text': str(chosen['text']).strip(),
                       'confidence': float(chosen.get('confidence', 0))}
        if chosen.get('words') and (start, end) == (chosen['start'], chosen['end']):
            replacement['words'] = chosen['words']
        changes.append({'start': row['start'], 'from': row['text'], 'to': replacement['text'],
                        'families': sorted({_family(name) for name, _ in best})})
        rows[index] = replacement
    return rows, changes


def _span_overlap_ratio(a, b):
    """Overlap divided by the union of the two time spans."""
    union = max(a['end'], b['end']) - min(a['start'], b['start'])
    return max(0.0, _overlap_seconds(a, b)) / union if union > 0 else 0.0


def _supporting_families(text, members, own_family):
    """Model families other than `own_family` whose readings are close to `text` (similarity ≥ 0.7)."""
    by_family = {}
    for name, other in members:
        family = _family(name)
        if family != own_family:
            by_family.setdefault(family, []).append(_similarity(text, other['text']))
    return [family for family, values in by_family.items() if sum(values) / len(values) >= .7]


def _raw_tokens(text):
    """[(raw word, [its normalized tokens])] so that normalized positions can be mapped back to the words as written ("what's" -> what, s)."""
    return [(raw, _normalized(raw).split()) for raw in str(text).split() if _normalized(raw).split()]


_LEAD_SCRIPT = re.compile(r"[A-Za-zÀ-ɏͰ-ϿЀ-ӿ]")    # Latin, Greek, Cyrillic: words with spaces and case


def _contains(words, part):
    return any(words[index:index + len(part)] == part for index in range(len(words) - len(part) + 1))


def restore_lead_words(merged, readings, *, skip_indices=(), maximum_lead=2, maximum_gap=1.5, maximum_duration=7.0, minimum_names=2):
    """(rows, changes): put back one or two opening words that the chosen row lost and that other readings have IMMEDIATELY before the row's own
    first words. Unlike restore_agreed_prefixes, the rest of the readings' wording does not have to match the row (the passes cut sentences
    differently): MAN OF WAR (dev jobs 152-170) "is what's left simple people we're trying to live their lives" (short pass) where the long pass
    has "This is what's left." and the rescue pass "this is what's left simple people" (clean large-v2 and Qwen: "This is what's left."). The
    same words must come from at least two different readings, and from two model families including large-v2 or Qwen, unless the row itself
    starts in lower case (visibly cut at its start), when two passes are enough. Nothing the row says is changed; the lead may not repeat the end
    of the previous row, may not reach back into it, and the row may not grow beyond `maximum_duration`. The start becomes the earliest start of a
    supporting reading that begins with the lead (or the word time of the lead), never earlier than the previous row's end."""
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    changes = []
    for index, row in enumerate(rows):
        own = _normalized(str(row.get('text', ''))).split()
        text = str(row.get('text', '')).strip()
        if index in skip_indices or len(own) < 2 or not text or not _LEAD_SCRIPT.match(text):
            continue                                   # a one-word row ("out!") matches too much; scripts without spaces have no words to count
        head = own[:2]
        previous = rows[index - 1] if index else None
        previous_words = _normalized(str(previous['text'])).split() if previous else []
        floor = previous['end'] if previous else 0.0
        found = {}
        for name, items in readings.items():
            for other in items or ():
                if other['end'] < row['start'] - maximum_gap or other['start'] > row['start'] + 1.0 or _is_boilerplate(other.get('text', '')):
                    continue
                tokens = _normalized(str(other.get('text', ''))).split()
                for position in range(1, len(tokens)):
                    # the reading holds the row's first two words, or ends with its first word (the passes cut the sentence there:
                    # APOCALYPSE Z rescue "putting european authorities" before the row "authorities in a very difficult situation.")
                    if tokens[position:position + len(head)] != head and not (position == len(tokens) - 1 and tokens[position] == head[0]):
                        continue
                    for size in range(1, min(maximum_lead, position) + 1):
                        lead = tuple(tokens[position - size:position])
                        # the same words in the previous row: the reading matched that sentence ("in den Film. Film?"), not this one
                        if _contains(previous_words, list(lead) + head[:1]) or lead[-1] == head[0]:
                            continue
                        found.setdefault(lead, []).append((name, other, position - size))
                    break
        supported = []
        for lead, members in found.items():
            names = {_base_name(name) for name, _, _ in members}
            families = {_family(name) for name, _, _ in members}
            if len(names) < minimum_names:
                continue
            if not (text[0].islower() or (len(families) >= 2 and families & _INDEPENDENT_FAMILIES)):
                continue
            if any(list(lead[-size:]) == previous_words[-size:] for size in range(1, len(lead) + 1)):
                continue
            supported.append((lead, members))
        if not supported:
            continue
        longest = max(len(lead) for lead, _ in supported)
        supported = [item for item in supported if len(item[0]) == longest]
        if len(supported) != 1:
            continue                                   # different opening words: uncertainty, not a choice
        lead, members = supported[0]
        starts, raw_lead = [], None
        for name, other, offset in members:
            if offset == 0:
                starts.append(float(other['start']))
            else:
                words = other.get('words') or []
                keys = [_normalized(str(word.get('text', ''))).split() for word in words]
                flat = [(k, word) for word, ks in zip(words, keys) for k in ks]
                if len(flat) > offset and [k for k, _ in flat[offset:offset + len(lead)]] == list(lead):
                    starts.append(float(flat[offset][1]['start']))
            if raw_lead is None:
                raw, collected, position = _raw_tokens(other.get('text', '')), [], 0
                for word, pieces in raw:
                    if offset <= position < offset + len(lead):
                        collected.append(word)
                    position += len(pieces)
                if _normalized(' '.join(collected)).split() == list(lead):
                    raw_lead = collected
        if not starts or raw_lead is None:
            continue
        start = min(starts)
        if start < floor - .1:
            continue                                   # the words are heard before the previous row ends: they belong to it (Nelly Rapp "vantarna på")
        start = max(start, floor + .05)
        if start >= row['start'] or row['end'] - start > maximum_duration:
            continue
        lead_text = ' '.join(raw_lead)
        continues = previous is not None and not _ends_sentence(previous)       # the previous row runs on into this one: no capital
        lead_text = (lead_text[:1].lower() if continues and lead_text[:2] not in ('I ', "I'") and lead_text != 'I' else lead_text[:1].upper()) + lead_text[1:]
        rest = text if text[:2] == 'I ' or text[:2] == "I'" else text[:1].lower() + text[1:]
        changes.append({'row': index, 'start': row['start'], 'new_start': round(start, 3), 'lead': lead_text, 'before': text,
                        'readings': sorted({_base_name(name) for name, _, _ in members})})
        row['text'] = f'{lead_text.rstrip()} {rest}' if lead_text.rstrip()[-1:].isalnum() or lead_text.rstrip()[-1:] in ',;' else f'{lead_text} {text}'
        row['start'] = round(start, 3)
        row['confirmed_lead_onset'] = {'start': row['start'], 'prefix': list(lead) + head,
                                       'readings': sorted({_base_name(name) for name, _, _ in members})}
    return rows, changes


def plan_lead_word_probes(merged, readings, duration, *, skip_indices=(), maximum=4):
    """Places where Qwen3-ASR should listen once more because ONE reading heard opening words a visibly cut row (it starts in lower case) lost.

    FORTITUDE (dev jobs 218 and 221): the row "were arrested" was translated "greps."; only the no-VAD rescue pass heard "you were arrested
    wearing a woman's skirt", and restore_lead_words rightly needs two readings. Qwen gives that second, independent opinion. Each window
    starts a second before the lead (`start` is the lead's measured time, `clip_start` where the clip begins) and ends half a second after the
    row, so Qwen hears the lead and the row's own words."""
    _, candidates = restore_lead_words(merged, readings, skip_indices=skip_indices, minimum_names=1)
    windows = []
    rows = sorted(merged, key=lambda item: item['start'])
    for change in candidates:
        if len(change['readings']) != 1 or not change['before'][:1].islower():
            continue
        row = rows[change['row']]
        windows.append({'start': change['new_start'], 'end': row['end'], 'clip_start': max(0.0, change['new_start'] - 1.0),
                        'clip_end': min(duration, row['end'] + .5), 'row_start': row['start'], 'lead': change['lead']})
    return windows[:maximum]


def restore_agreed_prefixes(merged, readings, *, skip_indices=()):
    """Add only an exactly agreed, word-timed sentence prefix; keep every existing word.

    A complete sentence may still have lost its opening pronoun. Two model families,
    including v2 or Qwen, must hear the SAME full wording. A complete measured reading
    supplies all word times. Neither a clip bound nor a neighbour's words can do so.
    """
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    indices = {name: _time_index(items or []) for name, items in readings.items()}
    changes = []
    for index, row in enumerate(rows):
        current = str(row.get('text', '')).strip()
        base = _normalized(current).split()
        if index in skip_indices or len(base) < 3 or not current or not current[0].isalpha():
            continue
        groups = {}
        for name, time_index in indices.items():
            for other in _rows_near(time_index, row['start'], row['end']):
                tokens = _normalized(str(other.get('text', ''))).split()
                added = len(tokens) - len(base)
                if (not 1 <= added <= 3 or tokens[added:] != base
                        or _span_overlap_ratio(row, other) < .5 or _is_boilerplate(other['text'])):
                    continue
                groups.setdefault(tuple(tokens), []).append((name, other))
        supported = [(tokens, members) for tokens, members in groups.items()
                     if len({_family(name) for name, _ in members}) >= 2
                     and {_family(name) for name, _ in members}.intersection(_INDEPENDENT_FAMILIES)]
        if len(supported) != 1:
            continue  # conflicting prefixes are uncertainty, not a confidence contest
        tokens, members = supported[0]
        lead = list(tokens[:-len(base)])
        previous = _normalized(rows[index - 1]['text']).split() if index else []
        following = _normalized(rows[index + 1]['text']).split() if index + 1 < len(rows) else []
        if any(lead[-size:] == previous[-size:] or lead[:size] == following[:size]
               for size in range(1, len(lead) + 1)):
            continue
        options = []
        for name, other in members:
            words = other.get('words') or []
            if (_base_name(name) == 'qwen' or not words
                    or _normalized(' '.join(str(w.get('text', '')) for w in words)).split() != list(tokens)):
                continue
            end = other['start']
            for word in words:
                if (word.get('probability', 0) < .55 or word['start'] < end
                        or not other['start'] <= word['start'] < word['end'] <= other['end']
                        or word['end'] - word['start'] > 1.5):
                    break
                end = word['end']
            else:
                prefix = []
                for word in words:
                    prefix.append(str(word['text']).strip())
                    if _normalized(' '.join(prefix)).split() == lead:
                        break
                prefix = ' '.join(prefix)
                if _normalized(prefix).split() != lead or any(c in prefix for c in '.!?…'):
                    continue
                start, end = min(row['start'], other['start']), max(row['end'], other['end'])
                if (end - start > 7 or index and start < rows[index - 1]['end']
                        or index + 1 < len(rows) and end > rows[index + 1]['start']):
                    continue
                options.append((name, other, prefix, start, end))
        if not options:
            continue
        name, timed, prefix, start, end = min(options, key=lambda item: item[4] - item[3])
        row.update(text=prefix + ' ' + current, start=start, end=end, words=timed['words'])
        changes.append({'row': index, 'from': current, 'to': row['text'], 'prefix': prefix, 'start': start, 'end': end,
                        'families': sorted({_family(name) for name, _ in members}), 'timing_pass': name})
    return rows, changes


def prefer_consensus_reading(merged, readings, *, minimum_passes=3, vad_passes=('precision', 'context'),
                             maximum_confidence=.6, contradicted_confidence=.8, margin=.15):
    """Replace the wording of a doubtful cue when the passes clearly read something else.

    A cue's text can come from a single pass that heard it differently from all the others (HOPE: "승리를 받자
    8명인데요" from the forced re-read, "갑자기 기침이" where the others read "기적이"). The readings of every pass
    that cover the same time as the cue (joined pieces too) and are linked to its wording vote, one vote per model
    family. Only the text changes, never the timing, and only to a reading that covers the same time span as the cue,
    is recognizably the same utterance, and agrees with the passes clearly better (`margin`) than the cue's own
    text. A cue qualifies when its confidence is below `maximum_confidence`, or below `contradicted_confidence`
    when at least two other model families read the replacement alike.
    """
    final = [dict(item) for item in sorted(merged, key=lambda item: item['start'])]
    everything = dict(readings)
    everything.update(span_readings(final, readings))
    for row in final:
        confidence = float(row.get('confidence', 0))
        if confidence >= contradicted_confidence:
            continue
        # The cue's own wording joins the readings that cover its time; what links to it (directly or through
        # another reading) is what the passes heard at this place.
        pool = [(name, other) for name, rows in everything.items() for other in (rows or ())
                if _normalized(str(other.get('text', ''))) and not _is_boilerplate(other.get('text', ''))
                and _span_overlap_ratio(row, other) >= .5]
        members = _linked_readings(row, pool)
        names = {_base_name(name) for name, _ in members}
        if len(names) < minimum_passes or not names.intersection(vad_passes):
            continue
        name, best = _medoid(members)
        text = str(best['text']).strip()
        if (not text or text == str(row['text']).strip() or _similarity(row['text'], text) < .4
                or _normalized(text) in _STOCK_PHRASES
                or _mean_agreement(text, members, skip=best) < _mean_agreement(row['text'], members) + margin):
            continue
        if confidence >= maximum_confidence and (
                len(_supporting_families(text, members, _family(name))) < 2
                or _similarity(row['text'], text) > .75):
            continue
        row['text'] = text
    return final


def prefer_forced_language_reading(merged, forced, others=None, *, maximum_confidence=.8, minimum_gain=.04,
                                   tolerance=.03):
    """Take the wording of a more confident forced-language reading of the same speech.

    For a script that identifies the language (Hangul = Korean) the ordinary passes run in multilingual mode with
    voice-activity filtering, which misreads quiet speech ("갑자기 기침이" where the speaker says "기적이"). Whisper
    with the language fixed and no filter, given ten seconds of context, reads the same place better, and its own
    confidence says so. A cue takes the forced reading (or the joined pieces of it) when that reading belongs to the
    cue (it overlaps no other cue more and covers most of it), is recognizably the same utterance (similarity >= 0.3),
    is more confident than the cue by `minimum_gain`, and the other passes (`others`, name -> cues) do not agree with
    the cue's own wording better than with the forced one (a confident reading can still have dropped words:
    "사냥을 있었어" for "사냥을 해나갔었어"). Only the text changes, never the timing, and boilerplate is never used.
    """
    final = [dict(item) for item in sorted(merged, key=lambda item: item['start'])]
    usable = [row for row in (forced or ()) if _normalized(str(row.get('text', ''))) and not _is_boilerplate(row['text'])]
    candidates = usable + span_readings(final, {'forced': usable}).get('forced+', [])
    everything = dict(others or {})
    everything.update(span_readings(final, others or {}))
    wanted = {}
    for other in candidates:
        overlaps = [(_overlap_seconds(row, other), index) for index, row in enumerate(final)]
        seconds, index = max(overlaps, default=(0.0, None))
        row = final[index] if index is not None else None
        if (row is None or seconds < .6 * (row['end'] - row['start'])
                or float(row.get('confidence', 0)) >= maximum_confidence
                or _similarity(row['text'], other['text']) < .3
                or float(other.get('confidence', 0)) < float(row.get('confidence', 0)) + minimum_gain):
            continue
        pool = [(name, reading) for name, rows in everything.items() for reading in (rows or ())
                if _normalized(str(reading.get('text', ''))) and not _is_boilerplate(reading.get('text', ''))
                and _span_overlap_ratio(row, reading) >= .5]
        members = _linked_readings(row, pool)
        if members and (_mean_agreement(other['text'], members)
                        < _mean_agreement(row['text'], members) - tolerance):
            continue
        wanted.setdefault(index, []).append(other)
    for index, options in wanted.items():
        best = max(options, key=lambda other: (float(other.get('confidence', 0)),
                                               _similarity(final[index]['text'], other['text'])))
        text = str(best['text']).strip()
        if text and _normalized(text) not in _STOCK_PHRASES:
            final[index]['text'] = text
    return final


def _mixes_scripts(text):
    """Latin letters inside text that is mostly in another script: a sign of a garbled reading."""
    letters = [char for char in str(text) if char.isalpha()]
    latin = sum(1 for char in letters if char.isascii())
    return bool(letters) and 0 < latin < len(letters) * .5


def prefer_confident_reading(merged, readings, *, maximum_confidence=.4, minimum_confidence=.6, minimum_gain=.25):
    """A cue the merge left at very low confidence takes a clearly more confident reading of the same time.

    The final merge can end on a pass that read the place badly (Sigma, Tamil: "…தண்டmedenmadு முட்ட…" with Latin
    letters in the middle of Tamil, confidence 0.27, while the quiet-gap pass read "ஆனால் தாயாமானது விட்டதே இல்லை."
    at 0.75). A cue with a recorded confidence below `maximum_confidence` (0 means "not recorded", never "bad")
    takes the most confident reading of the passes (joined pieces too) that covers most of the cue's time, is at
    least `minimum_confidence` and `minimum_gain` better, and is not much shorter (a piece of the speech with a
    higher confidence must not cost words; a garbled cue that mixes scripts may be replaced by a shorter one). Only
    the text changes, never the timing, and boilerplate is never used.
    """
    final = [dict(item) for item in sorted(merged, key=lambda item: item['start'])]
    everything = dict(readings)
    everything.update(span_readings(final, readings))
    def holds_neighbour(other, index):
        # Glemselens øy (dev job 208): "Reisa!" (0.76 s) took a 3 s reading that also held the whole next cue and became "Ryza, du kommer
        # ikke til å tro det!" right before "Du kommer ikke til å tro det!": a reading that covers most of a neighbouring cue AND holds its
        # words would put them in two cues.
        words = _normalized(str(other.get('text', ''))).split()
        for neighbour in (final[index - 1] if index else None, final[index + 1] if index + 1 < len(final) else None):
            if neighbour is None or neighbour['end'] <= neighbour['start']:
                continue
            theirs = _normalized(str(neighbour.get('text', ''))).split()
            if (theirs and _overlap_seconds(neighbour, other) >= .5 * (neighbour['end'] - neighbour['start'])
                    and any(words[start:start + len(theirs)] == theirs for start in range(len(words) - len(theirs) + 1))):
                return True
        return False

    for index, row in enumerate(final):
        confidence = float(row.get('confidence', 0))
        if not 0 < confidence < maximum_confidence:
            continue
        needed = .3 if _mixes_scripts(row['text']) else .85
        size = len(_normalized(str(row['text'])).replace(' ', ''))
        better = [other for rows in everything.values() for other in (rows or ())
                  if _normalized(str(other.get('text', ''))) and not _is_boilerplate(other.get('text', ''))
                  and _overlap_seconds(row, other) >= .7 * (row['end'] - row['start'])
                  and len(_normalized(str(other['text'])).replace(' ', '')) >= needed * size
                  and float(other.get('confidence', 0)) >= max(minimum_confidence, confidence + minimum_gain)
                  and not holds_neighbour(other, index)]
        if better:
            best = max(better, key=lambda other: (float(other.get('confidence', 0)), _span_overlap_ratio(row, other)))
            text = str(best['text']).strip()
            if text and _normalized(text) not in _STOCK_PHRASES:
                row['text'] = text
    return final


def plan_consensus_windows(merged, readings, *, minimum_passes=3, vad_passes=('precision', 'context'),
                           maximum_total=8, maximum_confidence=.6, maximum_doubtful=6):
    """Short time windows where a further independent reading (Qwen3-ASR) can settle the matter: speech the passes
    agree on that no final cue covers, and doubtful final cues the passes read differently.

    A doubtful cue that fewer than `minimum_passes` passes covered was never eligible above (Man of War, dev job 49:
    confidence 0.22 and 0.35 with only the short pass and Whisper v2 covering them, so Qwen never listened to the two
    least certain lines of the film). Every cue left at a recorded confidence below `maximum_confidence` that no
    planned window covers therefore gets a window of its own, the least certain first, at most `maximum_doubtful`."""
    final = sorted(merged, key=lambda item: item['start'])
    planned = []
    for cluster in _cluster_readings(readings):
        if not _eligible_cluster(cluster, minimum_passes, vad_passes):
            continue
        start = min(item['start'] for _, item in cluster)
        end = max(item['end'] for _, item in cluster)
        if end - start > 12:
            continue
        covering = [row for row in final if _overlap_seconds(row, {'start': start, 'end': end}) > .3]
        if not covering:
            priority, confidence = 0, max(float(item.get('confidence', 0)) for _, item in cluster)
        else:
            row = max(covering, key=lambda item: _overlap_seconds(item, {'start': start, 'end': end}))
            if (float(row.get('confidence', 0)) >= maximum_confidence
                    or _mean_agreement(row['text'], cluster) >= .85):
                continue
            priority, confidence = 1, float(row.get('confidence', 0))
        planned.append((priority, confidence, {'start': round(max(0.0, start - .3), 3), 'end': round(end + .3, 3)}))
    planned.sort(key=lambda item: (item[0], item[1], item[2]['start']))
    windows = [window for _, _, window in planned[:maximum_total]]
    doubtful = []
    for row in final:
        confidence = float(row.get('confidence', 0))
        if not 0 < confidence < maximum_confidence:    # 0 means "not recorded", never "bad"
            continue
        window = {'start': round(max(0.0, row['start'] - .3), 3), 'end': round(max(row['end'] + .3, row['start'] + 1.2), 3)}
        if window['end'] - window['start'] > 12 or not _normalized(str(row.get('text', ''))):
            continue
        if any(_overlap_seconds(row, other) >= .6 * (row['end'] - row['start']) for other in windows):
            continue
        doubtful.append((confidence, window))
    doubtful.sort(key=lambda item: (item[0], item[1]['start']))
    return windows + [window for _, window in doubtful[:maximum_doubtful]]


def reading_alternatives(final, readings, *, maximum_confidence=.6, minimum_agreement=.85, maximum_candidates=5,
                         agreement=.8, word_families=None):
    """Per doubtful final cue, the other wordings the passes heard of the same time, for a judge to choose between.

    A cue is doubtful when its recorded confidence is below `maximum_confidence` (0 means "not recorded") or when the
    readings of the same time do not agree with its wording (mean agreement below `minimum_agreement`). The
    candidates are the distinct wordings of the passes (joined pieces too) that cover most of the cue and are
    recognizably the same utterance, minus boilerplate. `support` is how many model families read something close
    (similarity of at least `agreement`) to the candidate, `current_support` the same for the cue's own wording, so a
    judge's choice can be held to what the audio passes themselves say. The best supported candidate comes first.
    Returns {index in `final`: [{'text', 'support', 'current_support'}, ...]}; only cues with at least one other
    wording appear. Nothing here changes a cue: the timing is never touched and its own wording stays the default."""
    everything = dict(readings)
    everything.update(span_readings(final, readings))
    indexed = {name: _time_index(rows) for name, rows in everything.items()}
    result = {}
    for index, row in enumerate(final):
        text = str(row.get('text', '')).strip()
        if not _normalized(text):
            continue
        pool = [(name, other) for name, found in indexed.items() for other in _rows_near(found, row['start'], row['end'])
                if _span_overlap_ratio(row, other) >= .5 and _normalized(str(other.get('text', '')))
                and not _is_boilerplate(other.get('text', ''))]
        members = _linked_readings(row, pool)
        if not members:
            continue
        confidence = float(row.get('confidence', 0))
        if not (0 < confidence < maximum_confidence or _mean_agreement(text, members) < minimum_agreement):
            continue

        def support_of(wording):
            return len({_family(name) for name, other in members
                        if _similarity(wording, other['text']) >= agreement})

        current_support = support_of(text) or 1       # the cue's own pass, whose reading it is
        distinct = {}
        for _, other in members:
            wording = str(other['text']).strip()
            key = _normalized(wording)
            if key and key != _normalized(text) and key not in _STOCK_PHRASES and key not in distinct:
                distinct[key] = {'text': wording, 'support': support_of(wording),
                                 'current_support': current_support,
                                 'confidence': float(other.get('confidence', 0))}
        ranked = sorted(distinct.values(), key=lambda entry: (-entry['support'], -entry['confidence']))
        if ranked:
            result[index] = [{key: entry[key] for key in ('text', 'support', 'current_support')}
                             for entry in ranked[:maximum_candidates]]
            own = re.findall(r"[^\W\d_]+", text.casefold())
            for candidate in result[index]:
                parts = re.findall(r"[^\W\d_]+", candidate['text'].casefold())
                position = iter(parts)
                if 2 <= len(parts) - len(own) <= 5 and all(word in position for word in own):
                    spans = [other for _, other in members if _normalized(other['text']) == _normalized(candidate['text'])]
                    candidate['completion_span'] = {'start': min(item['start'] for item in spans),
                                                    'end': max(item['end'] for item in spans)}
            if word_families is not None:
                # How many model families heard each word of the cue (for the judge: a word only one family heard may be dropped, one
                # that two heard may not; MAN OF WAR "You find me" against "... lights on me this").
                heard = {}
                for name, other in members:
                    for word in set(re.findall(r"[^\W\d_]+", str(other.get('text', '')).casefold())):
                        heard.setdefault(word, set()).add(_base_name(name))
                word_families[index] = {word: len(heard.get(word, ())) for word in re.findall(r"[^\W\d_]+", text.casefold())}
    return result


def plan_doubtful_row_windows(merged, *, maximum_confidence=.7, maximum_total=400, minimum_length=1.2, maximum_length=12.0):
    """A window of its own for every final cue the passes left at a recorded confidence below `maximum_confidence` (0.7: "Snow Goat could freeze up" stood at
    0.618 and "Let's get the out of" at 0.667), the least certain first, at most `maximum_total`. Yukon Men (44 min): 194 cues were below 0.6 and 286 below 0.7,
    but only about 35 were ever read a second time (windows capped at 6 to 19 per step), so Qwen heard "Snowgo could freeze up" and "snowgos" where the file says
    "Snow Goat" and nobody asked it, and Whisper large-v2 heard "Let's get the f*** out of here!" where large-v3 left "Let's get the out of"."""
    doubtful = []
    for row in merged:
        confidence = float(row.get('confidence', 0))
        if not 0 < confidence < maximum_confidence or not _normalized(str(row.get('text', ''))):
            continue
        window = {'start': round(max(0.0, row['start'] - .3), 3), 'end': round(max(row['end'] + .3, row['start'] + minimum_length), 3)}
        if window['end'] - window['start'] > maximum_length:
            continue
        doubtful.append((confidence, window))
    doubtful.sort(key=lambda item: (item[0], item[1]['start']))
    return [window for _, window in doubtful[:maximum_total]]


def plan_v2_fragment_windows(context, precision, duration, *, maximum_total=8):
    """Bound a v2 second opinion to short v3 fragments with a missing long word."""
    planned = []
    for row in sorted(context, key=lambda item: item['start']):
        words = _normalized(row.get('text', '')).split()
        if (not 2 <= len(words) <= 3
                or str(row['text']).rstrip().endswith(('.', '?', '!'))
                or float(row.get('confidence', 0)) >= .65):
            continue
        known = set(words)
        supported = any(
            min(row['end'], other['end']) - max(row['start'], other['start']) >= .2
            and float(other.get('confidence', 0)) >= .30
            and any(len(word) >= 6 and word not in known
                    for word in _normalized(other.get('text', '')).split())
            for other in precision)
        if not supported:
            continue
        start = max(0.0, float(int(row['start'] - 3.0)))
        end = min(float(duration), start + 25.0)
        if end - start < 10 or any(
                min(end, item['end']) - max(start, item['start']) > 8
                for item in planned):
            continue
        planned.append({'start': start, 'end': end})
        if len(planned) >= maximum_total:
            break
    return planned


def restore_precision_subjects(base, precision):
    """Recover a short sentence subject independently heard by the precision pass."""
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    subjects = {'i', 'you', 'he', 'she', 'we', 'they', 'it'}
    for index, row in enumerate(result):
        old = _normalized(row.get('text', '')).split()
        if not 2 <= len(old) <= 6 or float(row.get('confidence', 0)) >= .65:
            continue
        for other in precision:
            words = _normalized(other.get('text', '')).split()
            if (len(words) != len(old) + 1 or words[0] not in subjects
                    or words[1:] != old or float(other.get('confidence', 0)) < .65
                    or not 0 <= row['start'] - other['start'] <= .7
                    or abs(row['end'] - other['end']) > .4
                    or index and other['start'] < result[index - 1]['end'] + .02):
                continue
            row.update(start=other['start'], text=other['text'])
            break
    return result


def _inserted_article(original, candidate):
    old = _normalized(original).split()
    new = _normalized(candidate).split()
    if len(new) != len(old) + 1:
        return None
    for index, word in enumerate(new):
        if word in {'a', 'an', 'the'} and new[:index] + new[index + 1:] == old:
            return index, new
    return None


def plan_v2_second_opinion_windows(base, precision, rescue, duration, *, maximum_total=8):
    """Recheck short unsupported words and one-article rescue disagreements."""
    targets = []
    for row in base:
        words = _normalized(row.get('text', '')).split()
        if (1 <= len(words) <= 3 and row['end'] - row['start'] <= 2
                and float(row.get('confidence', 0)) < .85
                and not any(abs(other['start'] - row['start']) <= .8
                            and abs(other['end'] - row['end']) <= .8
                            and _normalized(other.get('text', '')) == _normalized(row['text'])
                            for other in precision)):
            targets.append((0, float(row.get('confidence', 0)), row))
        if any(_inserted_article(row.get('text', ''), candidate.get('text', ''))
               and abs(candidate['start'] - row['start']) <= .8
               and abs(candidate['end'] - row['end']) <= .8
               and float(candidate.get('confidence', 0)) >= .6
               for candidate in rescue):
            targets.append((1, -float(row.get('confidence', 0)), row))
    planned = []
    for _, _, row in sorted(targets, key=lambda item: item[2]['start']):
        start = max(0.0, float(int(row['start'] - 4.0)))
        end = min(float(duration), start + 17.0)
        if end - start < 8 or any(
                min(end, old['end']) - max(start, old['start']) > 8
                for old in planned):
            continue
        planned.append({'start': start, 'end': end})
        if len(planned) >= maximum_total:
            break
    return sorted(planned, key=lambda item: item['start'])


def reconcile_v2_second_opinions(base, precision, rescue, v2_rows):
    """Use v2 for accented short words or to confirm an article heard in rescue."""
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    for row in result:
        old = _normalized(row.get('text', ''))
        if (1 <= len(old.split()) <= 3 and row['end'] - row['start'] <= 2
                and float(row.get('confidence', 0)) < .85
                and not any(abs(other['start'] - row['start']) <= .8
                            and abs(other['end'] - row['end']) <= .8
                            and SequenceMatcher(None, _normalized(other.get('text', '')),
                                                old).ratio() >= .75
                            for other in precision)):
            alternatives = [candidate for candidate in v2_rows
                            if 1 <= len(_normalized(candidate.get('text', '')).split()) <= 3
                            and float(candidate.get('confidence', 0)) >= .65
                            and abs(candidate['start'] - row['start']) <= .8
                            and abs(candidate['end'] - row['end']) <= .8
                            and any(ord(char) > 127 and char.isalpha()
                                    for char in candidate.get('text', ''))
                            and .55 <= SequenceMatcher(
                                None, old, _normalized(candidate['text'])).ratio() < 1]
            if len(alternatives) == 1:
                row['text'] = alternatives[0]['text']
        for candidate in rescue:
            insertion = _inserted_article(row.get('text', ''), candidate.get('text', ''))
            if (not insertion or float(candidate.get('confidence', 0)) < .6
                    or abs(candidate['start'] - row['start']) > .8
                    or abs(candidate['end'] - row['end']) > .8):
                continue
            index, words = insertion
            anchor = words[max(0, index - 2):min(len(words), index + 3)]
            if len(anchor) < 3:
                continue
            if any(float(other.get('confidence', 0)) >= .4
                   and any(_normalized(other.get('text', '')).split()[offset:offset + len(anchor)]
                           == anchor for offset in range(
                               len(_normalized(other.get('text', '')).split()) - len(anchor) + 1))
                   and other['start'] <= row['end'] + .8
                   and other['end'] >= row['start'] - .8
                   for other in v2_rows):
                row['text'] = candidate['text']
                break
    return result


def plan_v2_word_check_windows(base, duration, *, minimum_confidence=.55,
                               minimum_words=4, maximum_total=6):
    """Recheck a longer, still-uncertain sentence against an independent model.

    `plan_v2_second_opinion_windows` only targets very short (1-3 word)
    fragments. A single mis-heard word can just as easily sit buried inside
    an otherwise-correct longer sentence -- this targets that shape instead:
    a whole cue whose own minimum word confidence is still low.
    """
    planned = []
    for row in sorted(base, key=lambda item: item['start']):
        words = _normalized(row.get('text', '')).split()
        if len(words) < minimum_words or float(row.get('confidence', 1.0)) >= minimum_confidence:
            continue
        start = max(0.0, float(int(row['start'] - 2.0)))
        end = min(float(duration), start + 12.0)
        if end - start < 6 or any(
                min(end, old['end']) - max(start, old['start']) > 6 for old in planned):
            continue
        planned.append({'start': start, 'end': end})
        if len(planned) >= maximum_total:
            break
    return sorted(planned, key=lambda item: item['start'])


def confirm_short_word_substitutions(base, precision, v2_rows):
    """A single changed word in a three-word cue needs two readers and timed anchors.

    Character similarity is not confirmation of a short verb (animo/venimos).
    Keep the v3 alternative's OTHER words: a weak v2 preposition is not a vote
    to replace it. Every confident v2 word must agree, and no cue time changes.
    """
    result, records = [dict(row) for row in base], []
    for index, row in enumerate(result):
        old = _normalized(row.get('text', '')).split()
        if len(old) != 3 or row['end'] - row['start'] > 2:
            continue
        for candidate in precision:
            new = _normalized(candidate.get('text', '')).split()
            if (len(new) != 3 or float(candidate.get('confidence', 0)) < .7
                    or abs(candidate['start'] - row['start']) > .25
                    or abs(candidate['end'] - row['end']) > .25):
                continue
            changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
            if len(changed) != 1 or len(new[changed[0]]) < 4:
                continue
            position = changed[0]
            witness = None
            for other in v2_rows:
                words = [w for w in _raw_rescue_words(other)
                         if row['start'] <= (w['start'] + w['end']) / 2 <= row['end']]
                if (len(words) != 3 or words[0]['start'] < row['start'] - .25
                        or words[-1]['end'] > row['end'] + .25
                        or any(w['end'] <= w['start'] for w in words)
                        or words[position]['probability'] < .8
                        or _normalized(words[position]['text']) != new[position]):
                    continue
                confident = [i for i, w in enumerate(words) if w['probability'] >= .6]
                if (not any(i != position for i in confident)
                        or any(_normalized(words[i]['text']) != new[i] for i in confident)):
                    continue
                witness = words
                break
            if witness is None:
                continue
            before = row['text']
            row['text'] = candidate['text']
            row['confirmed_source_words'] = sorted(set(row.get('confirmed_source_words', [])) | {new[position]})
            records.append({'row': index, 'start': row['start'], 'end': row['end'],
                            'before': before, 'after': row['text'], 'word': new[position],
                            'precision_confidence': candidate['confidence'], 'v2_words': witness})
            break
    return result, records


def reconcile_v2_word_substitutions(base, v2_rows, *, minimum_v2_word_probability=.6):
    """Swap in one word an independent model heard clearly, when every other
    word in the sentence already matches exactly.

    Deliberately narrow: if the two readings disagree on word count, order,
    or more than one position, nothing changes -- that is a bigger
    disagreement this function does not attempt to guess at.
    """
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for row in result:
        old_words = _normalized(row.get('text', '')).split()
        if len(old_words) < 4:
            continue
        original_tokens = str(row['text']).split()
        if len(original_tokens) != len(old_words):
            continue
        for candidate in v2_rows:
            if (abs(candidate['start'] - row['start']) > 3.0
                    or abs(candidate['end'] - row['end']) > 3.0):
                continue
            raw_words = _raw_rescue_words(candidate)
            if not raw_words:
                continue
            new_words = [_normalized(word['text']) for word in raw_words]
            if (len(new_words) != len(old_words)
                    or any(' ' in word for word in new_words)):
                continue
            diffs = [index for index, (old, new) in enumerate(zip(old_words, new_words))
                    if old != new]
            if len(diffs) != 1:
                continue
            index = diffs[0]
            if raw_words[index]['probability'] < minimum_v2_word_probability:
                continue
            replacement = raw_words[index]['text'].strip('.,!?;:')
            trailing = ''.join(char for char in original_tokens[index] if char in '.,!?;:')
            if original_tokens[index][:1].isupper():
                replacement = replacement[:1].upper() + replacement[1:]
            row['text'] = ' '.join(
                (replacement + trailing) if position == index else token
                for position, token in enumerate(original_tokens))
            # Remembered so that a later, independent reading can take the swap back (settle_v2_words_with_qwen).
            row['v2_word'] = {'index': index, 'old': original_tokens[index], 'new': replacement + trailing}
            break
    return result


def _same_length_reading(row, readings, size):
    """The normalized words of each reading that covers most of `row` and has exactly `size` words."""
    found = []
    for other in readings or ():
        if _span_overlap_ratio(row, other) < .5:
            continue
        words = _normalized(str(other.get('text', ''))).split()
        if len(words) == size:
            found.append((other, words))
    return found


def _single_word_swap(words, heard, *, minimum_letters=3):
    """(index, word) when `heard` holds the row's `words` with exactly ONE of them changed into another word (of at least `minimum_letters`
    letters), other words around it unchanged and only extra words at the very start or end, else None. Agnes mot verden (dev job 209): the row
    "en kilo heroin hjemme der, og så ber du meg om å bare være her." against large-v2's and NB-Whisper's "har seriøst en kilo heroin gjemt der, …"
    gives (3, 'gjemt'): the readings hold two more words at the start, so the older equal-length comparison never saw them."""
    if not words or not heard:
        return None
    operations = SequenceMatcher(None, words, heard, autojunk=False).get_opcodes()
    swaps = [operation for operation in operations if operation[0] == 'replace']
    inner = [operation for operation in operations[1:-1] if operation[0] in ('insert', 'delete')]
    if (len(swaps) != 1 or inner or any(tag == 'delete' for tag, *_ in (operations[0], operations[-1]))
            or sum(tag == 'equal' for tag, *_ in operations) == 0):
        return None
    _, a1, a2, b1, b2 = swaps[0]
    if a2 - a1 != 1 or b2 - b1 != 1 or len(heard[b1]) < minimum_letters or any(char.isdigit() for char in words[a1] + heard[b1]):
        return None                                   # "3 kilometers" / "three kilometers" is spelling, not a misheard word
    return a1, heard[b1]


def settle_v2_words_with_qwen(merged, qwen, v2=(), extra=()):
    """(rows, changes) once Qwen3-ASR (another model family) has read the doubtful places.

    1. A word reconcile_v2_word_substitutions took from Whisper large-v2 against every large-v3 pass is taken back when Qwen, at the same
       place of a reading with the same number of words, has the old word or one closer to it: ANOTHER GERMAN TANK STORY (dev jobs 171, 178)
       "Wir werden jetzt alle reich" (three large-v3 passes and Qwen) had become "Wir wären" (large-v2 alone), "War doch ein Klacksum" (large-v3,
       Qwen "Klacks") had become "Klagsohn". The swaps Qwen does not contradict stay (they were mostly right: "gun" -> "Gunn", "the" -> "their").
    2. A word large-v2 and Qwen BOTH hear, in readings with the same words as the row except that one, replaces the row's word: APOCALYPSE Z
       (dev jobs 166, 179) "Estamos en la plaza de Paros" (large-v3 alone) where large-v2 and Qwen hear "plaza de toros".
    `extra` are the readings of another independent family that count like Qwen's in both rules (NB-Whisper for Norwegian speech, app/norwegian_reader.py:
    Agnes mot verden "Finn deg du elsker." from the short pass at 0.18, where large-v2 and NB-Whisper hear "Finn det du elsker.")."""
    qwen = list(qwen or ()) + list(extra or ())
    rows, changes = [dict(row) for row in merged], []
    for row in rows:
        tokens = str(row.get('text', '')).split()
        words = _normalized(str(row.get('text', ''))).split()
        if len(tokens) != len(words) or len(words) < 3:
            continue
        swap = row.pop('v2_word', None)
        if swap and swap['index'] < len(tokens) and tokens[swap['index']] == swap['new']:
            index = swap['index']
            old, new = _normalized(swap['old']), _normalized(swap['new'])
            for other, heard in _same_length_reading(row, qwen, len(words)):
                if heard[:index] + heard[index + 1:] != words[:index] + words[index + 1:]:
                    continue
                if heard[index] == old or (heard[index] != new and SequenceMatcher(None, heard[index], old).ratio()
                                           > SequenceMatcher(None, heard[index], new).ratio()):
                    tokens[index] = swap['old']
                    row['text'] = ' '.join(tokens)
                    changes.append({'start': row['start'], 'kind': 'v2_word_taken_back', 'from': swap['new'], 'to': swap['old'], 'qwen': other['text']})
                    break
            continue
        swaps_v2 = {swap for other in v2 or () if _span_overlap_ratio(row, other) >= .5
                    for swap in [_single_word_swap(words, _normalized(str(other.get('text', ''))).split())] if swap}
        swaps_other = {swap for other in qwen if _span_overlap_ratio(row, other) >= .5
                       for swap in [_single_word_swap(words, _normalized(str(other.get('text', ''))).split())] if swap}
        agreed = next(iter(sorted(swaps_v2 & swaps_other)), None)
        if agreed:
            index, word = agreed
            trailing = ''.join(char for char in tokens[index] if char in '.,!?;:')
            replacement = word[:1].upper() + word[1:] if tokens[index][:1].isupper() else word
            changes.append({'start': row['start'], 'kind': 'v2_and_qwen_word', 'from': tokens[index], 'to': replacement + trailing})
            tokens[index] = replacement + trailing
            row['text'] = ' '.join(tokens)
    return rows, changes


def plan_v2_timing_windows(base, rescue, speech_intervals, duration, *, maximum_total=4):
    """Recheck a lone word when no speech supports its early v3 timestamp."""
    planned = []
    for row in sorted(base, key=lambda item: item['start']):
        word = _normalized(row.get('text', '')).split()
        if len(word) != 1 or row['end'] - row['start'] > 1.0:
            continue
        overlap = sum(max(0.0, min(row['end'], item['end'])
                          - max(row['start'], item['start'])) for item in speech_intervals)
        if overlap >= .18:
            continue
        shifted = next((candidate for candidate in rescue
                        if _normalized(candidate.get('text', '')).split() == word
                        and 1.5 <= candidate['start'] - row['start'] <= 4.0
                        and float(candidate.get('confidence', 0)) >= .80), None)
        if not shifted:
            continue
        start = max(0.0, float(int(shifted['start'] - 20.0)))
        end = min(float(duration), start + 33.0)
        if end - start < 10 or any(
                min(end, item['end']) - max(start, item['start']) > 8
                for item in planned):
            continue
        planned.append({'start': start, 'end': end})
        if len(planned) >= maximum_total:
            break
    return planned


def reconcile_v2_fragments(base, precision, v2_rows):
    """Accept v2 only when v3's short pass independently heard the missing word."""
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    for candidate in sorted(v2_rows, key=lambda item: item['start']):
        words = _normalized(candidate.get('text', '')).split()
        if not 2 <= len(words) <= 7 or float(candidate.get('confidence', 0)) < .35:
            continue
        anchors = {word for word in words if len(word) >= 6}
        if not anchors or not any(
                min(candidate['end'], row['end']) - max(candidate['start'], row['start']) >= .2
                and anchors.intersection(_normalized(row.get('text', '')).split())
                for row in precision):
            continue
        overlapping = [row for row in result
                       if min(candidate['end'], row['end']) - max(candidate['start'], row['start'])
                       >= min(.25, (row['end'] - row['start']) * .5)]
        if len(overlapping) != 1:
            continue
        old = overlapping[0]
        old_words = _normalized(old.get('text', '')).split()
        if (len(old_words) > 3 or len(words) < len(old_words) + 1
                or anchors.intersection(old_words)):
            continue
        index = result.index(old)
        if any(candidate['start'] < row['end'] - .001
               and candidate['end'] > row['start'] + .001
               for row in result if row is not old):
            continue
        result[index] = dict(candidate)
    return sorted(result, key=lambda item: item['start'])


def plan_address_title_confirmations(base, rescue, confirmations, duration, *, maximum_total=4,
                                     include_seen_names=False):
    """Recheck an omitted title heard by two offset views before the same cue.

    Long windows can place a quiet title too early. These bounded short views
    provide fresh word times; planning alone never changes the transcript.
    """
    titles = {'mr', 'mrs', 'ms', 'dr', 'prof', 'mister', 'miss', 'sir', 'madam'}
    planned = []
    if maximum_total <= 0:
        return planned
    for prefix in sorted(rescue, key=lambda row: row['start']):
        title = _normalized(prefix.get('text', ''))
        if (title not in titles or float(prefix.get('confidence', 0)) < .6
                or not any(_normalized(row.get('text', '')) == title
                           and float(row.get('confidence', 0)) >= .6
                           and abs(row['start'] - prefix['start']) <= 1.2
                           and abs(row['end'] - prefix['end']) <= 1.2
                           for row in confirmations)):
            continue
        following = next((row for row in sorted(base, key=lambda row: row['start'])
                          if 0 <= row['start'] - prefix['end'] <= 2
                          and str(row.get('text', '')).lstrip()[:1].isupper()
                          and len(_normalized(row.get('text', '')).split()) >= 3
                          and _normalized(row['text']).split()[0] not in titles), None)
        if following is None:
            continue
        start = max(0., float(math.floor(following['start'] - 3.5)))
        end = min(float(duration), following['end'] + 3., start + 12.)
        if end - start < 4 or any(min(end, w['end']) - max(start, w['start']) > 0
                                  for w in planned):
            continue
        planned.append({'start': start, 'end': end})
        if len(planned) >= maximum_total:
            break
    if include_seen_names:
        rows = sorted(base, key=lambda row: row['start'])
        seen = set()
        for index, row in enumerate(rows):
            tokens = _normalized(row.get('text', '')).split()
            if not tokens:
                continue
            if tokens[0] in titles:
                if len(tokens) >= 2:
                    seen.add(tokens[1])
                elif index + 1 < len(rows) and 0 <= rows[index + 1]['start'] - row['end'] <= 1:
                    following = str(rows[index + 1].get('text', '')).lstrip()
                    names = _normalized(following).split()
                    if names and following[:1].isupper():
                        seen.add(names[0])
                continue
            if (tokens[0] not in seen or len(tokens) < 3
                    or not str(row['text']).lstrip()[:1].isupper()
                    or index and _normalized(rows[index - 1].get('text', '')) in titles
                    and 0 <= row['start'] - rows[index - 1]['end'] <= 1):
                continue
            start = max(0., float(math.floor(row['start'] - 3.5)))
            end = min(float(duration), float(math.floor(row['end']) + 3), start + 12.)
            if end - start < 4 or any(min(end, w['end']) - max(start, w['start']) > 0 for w in planned):
                continue
            if len(planned) >= maximum_total:
                break
            planned.append({'start': start, 'end': end})
    return planned


def plan_unpunctuated_clusters(base, duration, *, maximum_total=4):
    """Bound rereads to consecutive long fragments, including their closing cue."""
    rows = sorted(base, key=lambda row: row['start'])
    planned, index = [], 0
    def fragment(row):
        return (len(_normalized(row.get('text', '')).split()) >= 5
                and not str(row.get('text', '')).rstrip().endswith(('.', '?', '!')))
    while index < len(rows):
        begin = index
        while (index < len(rows) and fragment(rows[index])
               and (index == begin or rows[index]['start'] - rows[index - 1]['end'] <= 1.5)):
            index += 1
        if index - begin >= 2:
            last = index - 1
            if index < len(rows) and rows[index]['start'] - rows[last]['end'] <= 1.5:
                last = index
            start, end = rows[begin]['start'], rows[last]['end']
            if end - start <= 24:
                planned.append({'start': max(0., math.floor(start) - 1.),
                                'end': min(float(duration), math.ceil(end) + 1.),
                                'target_start': start, 'target_end': end})
                if len(planned) >= maximum_total:
                    break
        index = max(index, begin + 1)
    return planned


def restore_corroborated_clusters(base, first, second, v2):
    """Replace a fragment cluster only with complete, three-reading timed agreement."""
    result = [dict(row) for row in sorted(base, key=lambda row: row['start'])]
    for window in plan_unpunctuated_clusters(result, max((r['end'] for r in result), default=0)):
        group = [row for row in result if window['target_start'] <= row['start']
                 and row['end'] <= window['target_end']]
        candidates = [dict(row) for row in v2
                      if row['end'] > window['target_start'] and row['start'] < window['target_end']]
        candidates.sort(key=lambda row: row['start'])
        if (len(candidates) < 2 or abs(candidates[0]['start'] - group[0]['start']) > .8
                or abs(candidates[-1]['end'] - group[-1]['end']) > .5
                or not all(str(row['text']).rstrip().endswith(('.', '?', '!')) for row in candidates)):
            continue
        valid = True
        for row in candidates:
            words = _valid_cue_words(row)
            if (not words or row['end'] - row['start'] > 7
                    or not all(_matching_timed_phrase(profile, [w['token'] for w in words],
                                                      words[0]['start'], words[-1]['end'])
                               for profile in (first, second))):
                valid = False
                break
        if not valid:
            continue
        # Only trim duplicated words already spanning an old cue boundary.
        old = _normalized(' '.join(row['text'] for row in
                                  trim_duplicated_cue_boundary(group, minimum_words=2))).split()
        new = _normalized(' '.join(row['text'] for row in candidates)).split()
        position = 0
        for token in new:
            if position < len(old) and token == old[position]:
                position += 1
        if position != len(old):
            continue  # No existing source word may disappear.
        for left, right in zip(candidates, candidates[1:]):
            if left['end'] > right['start']:
                end, start = _valid_cue_words(left)[-1]['end'], _valid_cue_words(right)[0]['start']
                if end > start:
                    valid = False
                    break
                boundary = (end + start) / 2
                left['end'], right['start'] = boundary, boundary
        untouched = [row for row in result if row not in group]
        if not valid or any(c['start'] < row['end'] and c['end'] > row['start']
                            for c in candidates for row in untouched):
            continue
        result = sorted(untouched + candidates, key=lambda row: row['start'])
    return result


def _matching_timed_phrase(profile, tokens, start, end, *, onset_tolerance=.8):
    """Find the complete phrase in one timed reading, allowing extra context."""
    for row in profile:
        words = _timed_rescue_words(row)
        for offset in range(len(words) - len(tokens) + 1):
            span = words[offset:offset + len(tokens)]
            if ([word['token'] for word in span] == tokens
                    and (offset == 0 or words[offset - 1]['source_word_index']
                         != span[0]['source_word_index'])
                    and (offset + len(tokens) == len(words)
                         or words[offset + len(tokens)]['source_word_index']
                         != span[-1]['source_word_index'])
                    and all(math.isfinite(word[key]) for word in span
                            for key in ('start', 'end', 'probability'))
                    and min(word['probability'] for word in span) >= .55
                    and all(0 < word['end'] - word['start'] <= 1.5 for word in span)
                    and abs(span[0]['start'] - start) <= onset_tolerance
                    and abs(span[-1]['end'] - end) <= .5
                    and all(left['source_word_index'] == right['source_word_index']
                            or left['end'] <= right['start'] + .02
                            for left, right in zip(span, span[1:]))):
                return span
    return []


def _valid_cue_words(row):
    words = _timed_rescue_words(row)
    if (not words or not all(math.isfinite(word[key]) for word in words
                            for key in ('start', 'end', 'probability'))
            or min(word['probability'] for word in words) < .55
            or any(not row['start'] <= word['start'] < word['end'] <= row['end']
                   or word['end'] - word['start'] > 1.5 for word in words)
            or any(left['source_word_index'] != right['source_word_index']
                   and left['end'] > right['start'] + .02
                   for left, right in zip(words, words[1:]))):
        return []
    return words


def plan_intervening_cue_confirmations(base, v2_rows, duration, *, maximum_total=4):
    """Plan new evidence for a possible sentence hidden by the next cue's onset.

    An uncertain old word may trigger a reread, never acceptance. The actual
    restoration still requires fresh complete v2 and two independent v3 views.
    """
    planned = []
    if maximum_total <= 0:
        return planned
    rows = sorted(base, key=lambda row: row['start'])
    readings = sorted(v2_rows, key=lambda row: row['start'])
    for candidate, following in zip(readings, readings[1:]):
        words = _timed_rescue_words(candidate)
        anchor = _valid_cue_words(following)
        if (not 3 <= len(words) <= 12 or not 3 <= len(anchor) <= 12
                or not str(candidate.get('text', '')).rstrip().endswith(('.', '?', '!'))
                or not all(math.isfinite(w[key]) for w in words
                           for key in ('start', 'end', 'probability'))
                or not all(0 <= w['probability'] <= 1
                           and candidate['start'] <= w['start'] < w['end'] <= candidate['end']
                           and w['end'] - w['start'] <= 1.5 for w in words)
                or any(left['source_word_index'] != right['source_word_index']
                       and left['end'] > right['start'] + .02
                       for left, right in zip(words, words[1:]))
                or not 0 < candidate['end'] - candidate['start'] <= 7
                or not .05 <= following['start'] - candidate['end'] <= 1.2):
            continue
        tokens = _normalized(candidate['text'])
        if any(_normalized(row.get('text', '')) == tokens for row in rows):
            continue
        target = _normalized(following['text'])
        matching = [row for row in rows if _normalized(row.get('text', '')) == target
                    and candidate['start'] < row['start'] < candidate['end']
                    and .5 <= following['start'] - row['start'] <= 4
                    and abs(following['end'] - row['end']) <= .5]
        if len(matching) != 1:
            continue
        old = matching[0]
        if (any(row is not old and candidate['start'] < row['end'] - .001
                       and candidate['end'] > row['start'] + .001 for row in rows)):
            continue
        # Use the same bounded context length as other short ASR confirmations;
        # a clip starting inside earlier dialogue can break both word alignment
        # and punctuation. Longer pairs outside this budget are not planned.
        start = max(0., float(math.floor(candidate['start']) - 18))
        end = min(float(duration), float(math.ceil(following['end']) + 1))
        if not 4 <= end - start <= 26:
            continue
        if any(min(end, window['end']) > max(start, window['start']) for window in planned):
            continue
        planned.append({'start': start, 'end': end})
        if len(planned) >= maximum_total:
            break
    return planned


def restore_corroborated_intervening_cues(base, rescue, confirmations, v2_rows,
                                         *, allow_confirmed_end=False):
    """Recover a missing sentence hidden by an early onset of the next cue.

    V2 must time two complete sentences. Two independent v3 views must hear
    every word of both sentences, including the preserved following cue.
    Its text and all neighbours stay intact. The legacy path preserves its
    end; fresh confirmations may also correct that end within the existing
    half-second agreement limit, provided no neighbour is overlapped.
    """
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    readings = sorted(v2_rows, key=lambda item: item['start'])
    for candidate, following in zip(readings, readings[1:]):
        words, anchor = _valid_cue_words(candidate), _valid_cue_words(following)
        if (not 3 <= len(words) <= 12 or not 3 <= len(anchor) <= 12
                or not str(candidate.get('text', '')).rstrip().endswith(('.', '?', '!'))
                or candidate['end'] - candidate['start'] > 7
                or not .05 <= following['start'] - candidate['end'] <= 1.2):
            continue
        tokens = [word['token'] for word in words]
        target = [word['token'] for word in anchor]
        matching = [row for row in result if _normalized(row.get('text', '')).split() == target
                    and candidate['start'] < row['start'] < candidate['end']
                    and .5 <= following['start'] - row['start'] <= 4
                    and abs(following['end'] - row['end']) <= .5]
        if len(matching) != 1:
            continue
        old = matching[0]
        new_end = following['end'] if allow_confirmed_end else old['end']
        if (following['start'] >= new_end - .08 or anchor[-1]['end'] > new_end + .02
                or not 0 < new_end - following['start'] <= 7):
            continue
        if any(row is not old and candidate['start'] < row['end'] - .001
               and candidate['end'] > row['start'] + .001 for row in result):
            continue
        if not all(_matching_timed_phrase(profile, tokens, words[0]['start'], words[-1]['end'],
                                         onset_tolerance=2.0)
                   and _matching_timed_phrase(profile, target, anchor[0]['start'], anchor[-1]['end'])
                   for profile in (rescue, confirmations)):
            continue
        if any(row is not old and following['start'] < row['end'] - .001
               and new_end > row['start'] + .001 for row in result):
            continue
        old['start'] = following['start']
        old['end'] = new_end
        old.pop('words', None)  # Old word timings no longer describe the retimed cue.
        result.append(dict(candidate))
        result.sort(key=lambda item: item['start'])
    return result


def _restore_joined_address_titles(base, rescue, confirmations, v2_rows):
    result = [dict(row) for row in base]
    titles = {'mr', 'mrs', 'ms', 'dr', 'prof', 'mister', 'miss', 'sir', 'madam'}
    for reading in v2_rows:
        words = _valid_cue_words(reading)
        if (len(words) < 3 or words[0]['token'] not in titles
                or not .08 <= words[0]['end'] - words[0]['start'] <= 1.0
                or not 0 <= words[1]['start'] - words[0]['end'] <= .35):
            continue
        body = [word['token'] for word in words[1:]]
        matches = [row for row in result if _normalized(row.get('text', '')).split() == body
                   and str(row.get('text', '')).lstrip()[:1].isupper()
                   and abs(row['start'] - words[1]['start']) <= .5
                   and abs(row['end'] - words[-1]['end']) <= .5]
        if len(matches) != 1:
            continue
        old = matches[0]
        start = words[0]['start']
        if (start >= old['start'] or old['end'] - start > 7
                or words[-1]['end'] > old['end'] + .02
                or any(row is not old and start < row['end'] - .001
                       and old['end'] > row['start'] + .001 for row in result)):
            continue
        tokens = [word['token'] for word in words]
        if not all(_matching_timed_phrase(profile, tokens, start, words[-1]['end'])
                   for profile in (rescue, confirmations)):
            continue
        title = reading['words'][0]['text'].strip()
        old.update(start=start, text=title + ' ' + old['text'].strip(),
                   words=[dict(word) for word in reading['words']])
    return result


def restore_corroborated_address_titles(base, rescue, v2_rows, confirmations=()):
    """Restore a heard title before a matching name cue, never invent a missing title.

    A VAD-free rescue cue must already be confirmed by another v3 offset.
    V2 must independently hear the same title with valid word timing immediately
    before a complete cue already retained in the primary text. Its timing is
    used because the quiet title's v3 onset can drift into the preceding gap.
    """
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    titles = {'mr', 'mrs', 'ms', 'dr', 'prof', 'mister', 'miss', 'sir', 'madam'}
    readings = sorted(v2_rows, key=lambda item: item['start'])
    for prefix, following in zip(readings, readings[1:]):
        title = _normalized(prefix.get('text', ''))
        words = _valid_cue_words(prefix)
        following_words = _valid_cue_words(following)
        name = str(following.get('text', '')).lstrip()
        if (title not in titles or len(words) != 1 or words[0]['probability'] < .55
                or not following_words
                or not .08 <= prefix['end'] - prefix['start'] <= 1.0
                or not 0 <= following_words[0]['start'] - words[0]['end'] <= .35
                or not name[:1].isupper() or len(_normalized(name).split()) < 2):
            continue
        if not any(candidate.get('corroborated')
                   and float(candidate.get('confidence', 0)) >= .60
                   and _normalized(candidate.get('text', '')) == title
                   and abs(candidate['start'] - prefix['start']) <= 2.0
                   and abs(candidate['end'] - prefix['end']) <= 2.0
                   for candidate in rescue):
            continue
        if confirmations and not all(_matching_timed_phrase(
                profile, [w['token'] for w in following_words],
                following_words[0]['start'], following_words[-1]['end'])
                for profile in (rescue, confirmations)):
            continue
        old = next((row for row in result
                    if _normalized(row.get('text', '')) == _normalized(name)
                    and abs(row['start'] - following['start']) <= .5
                    and abs(row['end'] - following['end']) <= .5), None)
        if old is None or prefix['start'] >= old['start']:
            continue
        timed = words + following_words
        if (not following_words
                or not prefix['start'] <= words[0]['start'] < words[0]['end'] <= prefix['end']
                or any(not prefix['start'] <= word['start'] < word['end'] <= old['end'] + .02
                       for word in timed)
                or any(left['end'] > right['start'] + .02
                       and not (left['source_word_index'] == right['source_word_index']
                                and left['start'] == right['start'] and left['end'] == right['end'])
                       for left, right in zip(timed, timed[1:]))
                or words[0]['end'] > following_words[0]['start'] + .02
                or following_words[-1]['end'] > old['end'] + .02
                or old['end'] - prefix['start'] > 7.0
                or any(row is not old and prefix['start'] < row['end'] - .001
                       and old['end'] > row['start'] + .001 for row in result)):
            continue
        old.update(start=prefix['start'], text=prefix['text'].strip() + ' ' + old['text'].strip(),
                   words=[dict(word) for word in prefix['words'] + following['words']])
    return _restore_joined_address_titles(result, rescue, confirmations, v2_rows)


def reconcile_v2_shifted_words(base, precision, rescue, v2_rows, speech_intervals):
    """Move a short cue only when v3 rescue and v2 agree on its later audio."""
    result = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    for candidate in rescue:
        word = _normalized(candidate.get('text', '')).split()
        if (len(word) != 1 or float(candidate.get('confidence', 0)) < .80
                or candidate['end'] - candidate['start'] > 1.0):
            continue
        early = next((row for row in precision
                      if _normalized(row.get('text', '')).split() == word
                      and 1.5 <= candidate['start'] - row['start'] <= 4.0), None)
        if early is None:
            continue
        early_speech = sum(max(0.0, min(early['end'], item['end'])
                               - max(early['start'], item['start']))
                           for item in speech_intervals)
        if early_speech >= .18:
            continue
        confirmed = next((row for row in v2_rows
                          if _normalized(row.get('text', '')).split() == word
                          and abs(row['start'] - candidate['start']) <= .8
                          and abs(row['end'] - candidate['end']) <= .8
                          and float(row.get('confidence', 0)) >= .40), None)
        if confirmed is None or any(
                min(confirmed['end'], row['end']) - max(confirmed['start'], row['start']) > .001
                for row in result):
            continue
        before = [row for row in result if row['end'] <= confirmed['start']]
        after = [row for row in result if row['start'] >= confirmed['end']]
        if (not before or not after
                or confirmed['start'] - before[-1]['end'] > 5.0
                or after[0]['start'] - confirmed['end'] > 5.0):
            continue
        result.append(dict(confirmed))
        result.sort(key=lambda item: item['start'])
    return result


def plan_rescue_confirmations(base, rescue, duration, *, maximum_total=12,
                              maximum_per_quarter_hour=4):
    """Re-read only uncertain, bounded rescue phrases from another offset.

    A candidate before the first known line or after the last one has no
    neighbour to measure its gap against; the clip's own start/end stands
    in for that missing neighbour instead of the candidate being refused
    outright.
    """
    planned, buckets = [], {}
    ordered = sorted(base, key=lambda row: (row['start'], row['end']))
    for candidate in sorted(rescue, key=lambda row: (row['start'], row['end'])):
        text = str(candidate.get('text', '')).strip()
        words = _normalized(text).split()
        confidence = float(candidate.get('confidence', 0))
        if (len(words) < 4 or confidence < .60
                or candidate['end'] - candidate['start'] > 7):
            continue
        overlapping = [row for row in ordered
                       if candidate['start'] < row['end'] and candidate['end'] > row['start']]
        if overlapping:
            if len(overlapping) != 1 or not _extends_fragment(overlapping[0], candidate):
                continue
        before = [row for row in ordered if row['end'] <= candidate['start']]
        after = [row for row in ordered if row['start'] >= candidate['end']]
        gap_after = (after[0]['start'] - candidate['end']) if after else (duration - candidate['end'])
        if gap_after > 8:
            continue
        if not overlapping:
            gap_before = (candidate['start'] - before[-1]['end']) if before else candidate['start']
            if gap_before > 14 or (gap_before > 8 and confidence < .80):
                continue
            if (text.endswith(('.', '?', '!')) and confidence >= .80
                    and gap_before <= 8):
                continue
        bucket = int(candidate['start'] // 900)
        if len(planned) >= maximum_total or buckets.get(bucket, 0) >= maximum_per_quarter_hour:
            continue
        # A stable whole-second offset avoids clipping a quiet initial word.
        start = max(0.0, float(int(candidate['start'] - 5.0)))
        end = min(float(duration), start + 22.0)
        if end - start < 10 or candidate['end'] > end - .5:
            continue
        if any(min(end, row['end']) - max(start, row['start']) > 12 for row in planned):
            continue
        planned.append({'start': round(start, 3), 'end': round(end, 3)})
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return planned


def _fragment_of(candidate_text, container_text, *, minimum_ratio=.90):
    """True when candidate reads as the lead or tail chunk of a longer reading.

    A no-VAD rescue pass can split one spoken sentence into two short
    fragments where a longer offset reading heard it as a single continuous
    sentence. Comparing whole spans/text then never lines up even though
    both passes genuinely agree on the words.
    """
    candidate_words = _normalized(candidate_text).split()
    container_words = _normalized(container_text).split()
    count = len(candidate_words)
    if count < 2 or count >= len(container_words):
        return False
    candidate_joined = ' '.join(candidate_words)
    return any(SequenceMatcher(None, candidate_joined, ' '.join(chunk)).ratio() >= minimum_ratio
              for chunk in (container_words[:count], container_words[-count:]))


def corroborate_rescue(rescue, confirmations):
    """Mark a short no-VAD reading only when another offset hears the same words."""
    result = []
    ordered = sorted(confirmations, key=lambda row: (row['start'], row['end']))
    readings = list(ordered)
    for first, second in zip(ordered, ordered[1:]):
        if 0 <= second['start'] - first['end'] <= 1.2:
            readings.append({'start': first['start'], 'end': second['end'],
                             'text': first['text'].strip() + ' ' + second['text'].strip(),
                             'joined': True,
                             'confidence': min(float(first.get('confidence', 0)),
                                               float(second.get('confidence', 0)))})
    for candidate in rescue:
        matching = next((row for row in readings if
            abs(candidate['start'] - row['start']) <= 1.2
            and abs(candidate['end'] - row['end']) <= 1.2
            and (
                (float(row.get('confidence', 0)) >= (.50 if row.get('joined') else .60)
                 and SequenceMatcher(None, _normalized(candidate['text']),
                                     _normalized(row['text'])).ratio() >= (
                                         .95 if row.get('joined') else .82))
                or (float(candidate.get('confidence', 0)) >= .80
                    and float(row.get('confidence', 0)) >= .25
                    and len(_normalized(candidate['text']).split()) >= 5
                    and _normalized(candidate['text']).split()[1:]
                    == _normalized(row['text']).split()[1:]
                    and _normalized(candidate['text']).split()[0]
                    != _normalized(row['text']).split()[0]))), None)
        if matching is None:
            matching = next((row for row in readings if
                candidate['start'] >= row['start'] - 1.2
                and candidate['end'] <= row['end'] + 1.2
                and float(row.get('confidence', 0)) >= .40
                and _fragment_of(candidate['text'], row['text'])), None)
        checked = {**candidate, 'corroborated': matching is not None}
        if matching:
            old_words = _normalized(candidate['text']).split()
            new_words = _normalized(matching['text']).split()
            if (len(old_words) == len(new_words) >= 4
                    and old_words[1:] == new_words[1:]
                    and old_words[0] != new_words[0]):
                checked['text'] = matching['text'].strip()
        result.append(checked)
    return result


def _extends_fragment(old, candidate):
    old_words = _normalized(old['text']).split()
    words = _normalized(candidate['text']).split()
    if len(old_words) < 3 or len(words) < len(old_words) + 2:
        return False
    same_start = (words[:3] == old_words[:3]
                  and abs(candidate['start'] - old['start']) <= .8)
    shifted_start = (words[1:3] == old_words[:2]
                     and 0 <= old['start'] - candidate['start'] <= 1.0)
    same_end = (words[-len(old_words):] == old_words
                and .25 <= old['start'] - candidate['start'] <= 3.0
                and abs(candidate['end'] - old['end']) <= .5)
    return ((same_start or shifted_start or same_end)
            and -.5 <= candidate['end'] - old['end'] <= 3.0)


def fill_bounded_rescue_gaps(base, rescue, *, maximum_neighbour_gap=8.0,
                             minimum_confidence=.80, duration=None):
    """Recover bounded speech, or extend a matching fragment missed by VAD.

    `duration` is optional and only changes behaviour for a non-overlapping
    candidate with no line before or after it: without it (the old default)
    such a candidate is never bounded and is always refused, exactly as
    before; passing the clip's real duration lets its own start/end stand
    in for that missing neighbour, so a leading or trailing candidate can
    still be bounded and considered.
    """
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for candidate in sorted(rescue, key=lambda row: (row['start'], row['end'])):
        text = str(candidate.get('text', '')).strip()
        candidate_words = _normalized(text).split()
        confidence = float(candidate.get('confidence', 0))
        corroborated = (candidate.get('corroborated', False)
                        and len(candidate_words) >= 4 and confidence >= .60)
        if len(candidate_words) < 2 or not (text.endswith(('.', '?', '!')) or corroborated):
            continue
        overlapping = [row for row in result
                       if candidate['start'] < row['end'] and candidate['end'] > row['start']]
        if overlapping:
            # A no-VAD pass can recover the *end* of an existing fragment. Its
            # shared first words anchor the reading in real dialogue; unrelated
            # overlapping text must never replace an existing subtitle.
            if len(overlapping) != 1 or confidence < .60:
                continue
            old = overlapping[0]
            old_words = _normalized(old['text']).split()
            if (len(candidate_words) < len(old_words) + 2
                    or not _extends_fragment(old, candidate)
                    or (candidate_words[1:3] == old_words[:2] and not corroborated)
                    or candidate['end'] - candidate['start'] > 7.0):
                continue
            following = [row for row in result if row['start'] >= candidate['end']]
            if not following or following[0]['start'] - candidate['end'] > maximum_neighbour_gap:
                continue
            result.remove(old)
            result.append(dict(candidate))
            result.sort(key=lambda row: (row['start'], row['end']))
            continue
        if confidence < minimum_confidence and not corroborated:
            continue
        previous = [row for row in result if row['end'] <= candidate['start']]
        following = [row for row in result if row['start'] >= candidate['end']]
        if duration is None and (not previous or not following):
            continue
        previous_gap = (candidate['start'] - previous[-1]['end']) if previous else candidate['start']
        following_gap = ((following[0]['start'] - candidate['end']) if following
                         else (duration - candidate['end']))
        if (previous_gap > maximum_neighbour_gap
                and not (corroborated and confidence >= .80 and previous_gap <= 14)
                or following_gap > maximum_neighbour_gap):
            continue
        result.append(dict(candidate))
        result.sort(key=lambda row: (row['start'], row['end']))
    return result


def correct_rescue_onset_drift(base, rescue, *, minimum_similarity=.85,
                               minimum_drift=.5, maximum_drift=4.0):
    """Move a cue's start to the no-VAD rescue pass's onset when both agree on the text.

    Whisper's own VAD (`vad_filter=True`, used for the main context/precision
    passes) can anchor a segment's start a second or more before the first
    clear word — a breath, mouth noise, or the tail of the previous line. The
    no-VAD rescue pass reads word-level timestamps directly and is measurably
    more accurate at pinpointing onset, but until now its timing was only
    used to fill gaps, never to correct a cue that already existed with the
    wrong (too-early) start. A raw confidence floor is deliberately not
    applied here: the rescue pass's word-probability confidence can be low
    even when it has the timing right, simply because it misheard one word
    (as opposed to the main pass, which can be confidently wrong) — the high
    textual similarity already required below is the real corroboration.
    """
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for index, row in enumerate(result):
        best = None
        for candidate in rescue:
            if abs(candidate['end'] - row['end']) > 1.5:
                continue
            similarity = SequenceMatcher(
                None, _normalized(row['text']), _normalized(candidate.get('text', ''))
            ).ratio()
            if similarity < minimum_similarity:
                continue
            drift = candidate['start'] - row['start']
            if not minimum_drift <= drift <= maximum_drift:
                continue
            if best is None or similarity > best[1]:
                best = (candidate, similarity)
        if best is None:
            continue
        new_start = best[0]['start']
        if index and new_start < result[index - 1]['end'] + .05:
            continue
        row['start'] = round(new_start, 3)
    return result


def _timed_rescue_words(candidate):
    """Return word timings only when they still represent the cue's full text."""
    result = []
    for word_index, word in enumerate(candidate.get('words', [])):
        try:
            start, end = float(word['start']), float(word['end'])
            probability = float(word['probability'])
            tokens = _normalized(word['text']).split()
        except (KeyError, TypeError, ValueError):
            return []
        if not tokens or end <= start:
            return []
        result.extend({'token': token, 'start': start, 'end': end,
                       'probability': probability, 'source_word_index': word_index} for token in tokens)
    if ' '.join(word['token'] for word in result) != _normalized(candidate.get('text', '')):
        return []
    return result


def extend_rescue_prefix(base, rescue, *, minimum_confidence=.80,
                         minimum_word_probability=.80):
    """Restore a strongly heard sentence lead before an already agreed suffix.

    A VAD-based pass can omit the start of a sentence while retaining its tail.
    Only extend that tail when the VAD-free pass ends in the same words, its
    omitted lead has confident word timestamps, and adjacent cues stay intact.
    """
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for index, row in enumerate(result):
        old = _normalized(row['text']).split()
        if len(old) < 4:
            continue
        for candidate in rescue:
            words = _timed_rescue_words(candidate)
            prefix = words[:-len(old)] if len(words) > len(old) else []
            if (not 2 <= len(prefix) <= 6
                    or [word['token'] for word in words[-len(old):]] != old
                    or min(word['probability'] for word in prefix) < minimum_word_probability
                    or float(candidate.get('confidence', 0)) < minimum_confidence
                    or abs(words[-1]['end'] - row['end']) > .5
                    or candidate['end'] - candidate['start'] > 7.0
                    or words[0]['start'] > row['start'] + .2
                    or row['start'] - words[0]['start'] > 3.0):
                continue
            previous_end = result[index - 1]['end'] if index else 0.0
            if words[0]['start'] < previous_end - .3:
                continue
            new_start = max(words[0]['start'] - .08, previous_end + .01)
            if new_start >= row['start'] or new_start >= row['end']:
                continue
            new_text = str(candidate['text']).strip()
            if re.search(r'\bI\b', str(row['text'])):
                new_text = re.sub(r'\bi\b', 'I', new_text)
            if new_text and str(row['text'])[:1].isupper():
                new_text = new_text[:1].upper() + new_text[1:]
            if str(row['text']).rstrip().endswith(('.', '?', '!')):
                new_text = new_text.rstrip('.?!') + str(row['text']).rstrip()[-1]
            row.update(start=round(new_start, 3), text=new_text)
            break
    return result


def correct_rescue_word_onsets(base, rescue, *, minimum_drift=.5,
                               maximum_drift=4.0):
    """Align an intact short cue to its exact words inside a longer rescue cue."""
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for index, row in enumerate(result):
        target = _normalized(row['text']).split()
        if not 2 <= len(target) <= 10:
            continue
        best = None
        for candidate in rescue:
            words = _timed_rescue_words(candidate)
            for offset in range(len(words) - len(target) + 1):
                matched = words[offset:offset + len(target)]
                if [word['token'] for word in matched] != target:
                    continue
                new_start = matched[0]['start']
                if (not minimum_drift <= new_start - row['start'] <= maximum_drift
                        or abs(matched[-1]['end'] - row['end']) > .5
                        or min(word['probability'] for word in matched) < .60
                        or index and new_start < result[index - 1]['end'] + .05):
                    continue
                score = abs(matched[-1]['end'] - row['end'])
                if best is None or score < best[0]:
                    best = (score, new_start)
        if best:
            row['start'] = round(best[1], 3)
    return result


def correct_confirmed_word_onsets(base, rescue, v2, *, skip_indices=(), maximum_disagreement=.5):
    """After all merges and VAD refinement, correct an early start only when rescue AND v2 time every unchanged word.

    A later sentence split can put back the precision pass's early start (FORTITUDE: 8.19 s; the two word-timed readers start at
    10.62/10.96 s). VAD misses the quiet first word here. Do not move words/end/neighbours, infer a time from a clip bound, or trust one reader.
    """
    def timed(items):
        accepted = []
        for item in items:
            words = _timed_rescue_words(item)
            if not words or any(not all(math.isfinite(word[key]) for key in ('start', 'end', 'probability'))
                                or word['start'] < 0 or not .6 <= word['probability'] <= 1
                                or not 0 < word['end'] - word['start'] <= 1.5 for word in words):
                continue
            if any(a['source_word_index'] != b['source_word_index'] and b['start'] < a['end'] - .05
                   for a, b in zip(words, words[1:])):
                continue
            accepted.append(item)
        return accepted

    rows = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    rescue, v2 = timed(rescue), timed(v2)
    first = correct_rescue_word_onsets(rows, rescue)
    second = correct_rescue_word_onsets(rows, v2)

    def starts(row, items):
        target = _normalized(row['text']).split()
        result = []
        for item in items:
            words = _timed_rescue_words(item)
            for offset in range(len(words) - len(target) + 1):
                phrase = words[offset:offset + len(target)]
                if ([word['token'] for word in phrase] == target and abs(phrase[-1]['end'] - row['end']) <= .5
                        and row['start'] - .5 <= phrase[0]['start'] < row['end']):
                    result.append(phrase[0]['start'])
        return result

    changes = []
    for index, (row, a, b) in enumerate(zip(rows, first, second)):
        if (index in skip_indices or a['start'] == row['start'] or b['start'] == row['start']
                or abs(a['start'] - b['start']) > maximum_disagreement):
            continue
        options = starts(row, rescue) + starts(row, v2)
        # Do not cherry-pick a later rescue pass when an equally well timed
        # reading puts the first word earlier (Russian job 93's initial "А").
        if not options or max(options) - min(options) > maximum_disagreement:
            continue
        onset = round(min(options), 3)
        if onset >= row['end']:
            continue
        changes.append({'row': index, 'text': row['text'], 'start': row['start'], 'new_start': onset,
                        'end': row['end'], 'rescue_start': a['start'], 'v2_start': b['start']})
        row['start'] = onset
    return rows, changes


def _word_recall(old_words, new_words):
    """Fraction of old_words recoverable, in order, inside new_words."""
    if not old_words:
        return 0.0
    matcher = SequenceMatcher(None, old_words, new_words)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(old_words)


def _raw_rescue_words(candidate):
    """Return this rescue cue's own words, still in original casing/punctuation."""
    result = []
    for word in candidate.get('words', []):
        try:
            start, end = float(word['start']), float(word['end'])
            probability = float(word['probability'])
            text = str(word['text']).strip()
        except (KeyError, TypeError, ValueError):
            return []
        if not text or end <= start:
            return []
        result.append({'text': text, 'start': start, 'end': end, 'probability': probability})
    return result


def _rescue_word_timeline(rescue):
    """Flatten rescue candidates into one ordered word timeline, skipping re-reads.

    Every word remembers which rescue candidate it came from, so a caller can
    tell a genuine pause *inside* one continuous reading apart from the gap
    between two separately read (and possibly unrelated) sentences.
    """
    timeline = []
    for candidate_id, candidate in enumerate(
            sorted(rescue, key=lambda row: (row['start'], row['end']))):
        words = _raw_rescue_words(candidate)
        if not words or (timeline and words[0]['start'] < timeline[-1]['end'] - .05):
            continue
        for word in words:
            word['candidate_id'] = candidate_id
        timeline.extend(words)
    return timeline


def _rescue_span_words(timeline, span_start, span_end, *, before=3.0, after=1.0, max_gap=1.2):
    """Words covering a span, extended only through the *same* rescue reading.

    The extension never crosses into a different rescue candidate's words,
    even when the gap between them is tiny -- two separately read candidates
    can be two unrelated sentences that merely sit close together in time.
    """
    core = [i for i, word in enumerate(timeline)
           if word['end'] > span_start and word['start'] < span_end]
    if not core:
        return [word for word in timeline
               if word['end'] > span_start - before and word['start'] < span_end + after]
    # A stray word from a neighbouring candidate can technically overlap the
    # span's edge (e.g. it starts a beat before the span's declared end).
    # Keep only the candidate that actually accounts for most of the span,
    # so a lone borrowed word never becomes the seed for a cross-candidate
    # extension.
    counts = {}
    for i in core:
        counts[timeline[i]['candidate_id']] = counts.get(timeline[i]['candidate_id'], 0) + 1
    primary = max(counts, key=counts.get)
    core = [i for i in core if timeline[i]['candidate_id'] == primary]
    start, end = core[0], core[-1]
    while (start > 0
           and timeline[start - 1]['candidate_id'] == timeline[start]['candidate_id']
           and timeline[start]['start'] - timeline[start - 1]['end'] <= max_gap
           and timeline[start - 1]['start'] >= span_start - before):
        start -= 1
    while (end + 1 < len(timeline)
           and timeline[end + 1]['candidate_id'] == timeline[end]['candidate_id']
           and timeline[end + 1]['start'] - timeline[end]['end'] <= max_gap
           and timeline[end + 1]['end'] <= span_end + after):
        end += 1
    return timeline[start:end + 1]


def reconcile_rescue_resegmentation(base, rescue, *, minimum_recall=.65, maximum_groups=3):
    """Replace a misplaced cue boundary with the no-VAD pass's own word timeline.

    Whisper's VAD-driven main pass can put a word on the wrong side of a cue
    boundary -- for example ending one line with a word spoken as part of the
    *next* sentence, while dropping a word from the *start* of the sentence it
    belongs to. The no-VAD rescue pass reads the same stretch of audio in one
    continuous pass, so re-splitting its own word timeline the ordinary way
    (`split_word_cues`, on real pauses) recovers the correct boundary, as long
    as it accounts for every word the main pass already had.
    """
    timeline = _rescue_word_timeline(rescue)
    if not timeline:
        return base
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    index = 0
    while index < len(result):
        matched = False
        for count in range(1, min(maximum_groups, len(result) - index) + 1):
            group = result[index:index + count]
            span_start, span_end = group[0]['start'], group[-1]['end']
            words = _rescue_span_words(timeline, span_start, span_end)
            if not words:
                continue
            if index and any(word['start'] < result[index - 1]['end'] - .05 for word in words):
                continue
            if (index + count < len(result)
                    and any(word['end'] > result[index + count]['start'] + .05 for word in words)):
                continue
            group_words = _normalized(' '.join(row['text'] for row in group)).split()
            word_tokens = []
            for word in words:
                word_tokens.extend(_normalized(word['text']).split())
            if (len(word_tokens) < len(group_words)
                    or min(word['probability'] for word in words) < .45
                    or _word_recall(group_words, word_tokens) < minimum_recall):
                continue
            objects = [SimpleNamespace(start=word['start'], end=word['end'],
                                       probability=word['probability'], word=' ' + word['text'])
                      for word in words]
            cues = split_word_cues(objects, minimum_probability=0.0)
            # Cues that were already complete, properly punctuated sentences
            # were not broken -- never coarsen the group into fewer cues than
            # that many already-good sentences, even when an unrelated
            # neighbour in the same group is genuinely still broken. A
            # rescue reading spanning the same passage in one continuous,
            # unpunctuated read is not worth destroying sentence structure
            # that was already correct.
            punctuated_count = sum(1 for row in group if str(row['text']).rstrip()
                                   .endswith(('.', '?', '!')))
            if punctuated_count >= 2 and len(cues) < punctuated_count:
                continue
            new_text = _normalized(' '.join(cue['text'] for cue in cues))
            old_text = _normalized(' '.join(row['text'] for row in group))
            if not cues or new_text == old_text:
                continue
            result[index:index + count] = [
                {'start': round(cue['start'], 3), 'end': round(cue['end'], 3), 'text': cue['text']}
                for cue in cues]
            index += len(cues)
            matched = True
            break
        if not matched:
            index += 1
    return result


def restore_precision_sentence_split(base, precision, *, minimum_words=5):
    """Split one under-punctuated cue into precision's own agreed sentences.

    `prefer_precision_sentence_boundaries` only replaces *several* existing
    cues at once. A long-context pass can also swallow multiple short spoken
    sentences into a *single* cue with no terminal punctuation at all -- the
    clearest sign precision's finer pass, not the main pass, saw the real
    sentence breaks here.
    """
    result = [dict(row) for row in sorted(base, key=lambda row: row['start'])]
    shorter = [dict(row) for row in sorted(precision, key=lambda row: row['start'])]
    index = 0
    while index < len(result):
        row = result[index]
        words = _normalized(row.get('text', '')).split()
        if (len(words) < minimum_words
                or str(row['text']).rstrip().endswith(('.', '?', '!'))):
            index += 1
            continue
        candidates = [item for item in shorter
                     if item['end'] > row['start'] - .8 and item['start'] < row['end'] + .8]
        if (len(candidates) < 2
                or abs(candidates[0]['start'] - row['start']) > .8
                or abs(candidates[-1]['end'] - row['end']) > 1.5
                or any(b['start'] - a['end'] > 3.0 for a, b in zip(candidates, candidates[1:]))
                or any(float(item.get('confidence', 0)) < .40 for item in candidates)
                or not all(str(item['text']).rstrip().endswith(('.', '?', '!'))
                          for item in candidates)):
            index += 1
            continue
        candidate_words = _normalized(' '.join(item['text'] for item in candidates)).split()
        if _word_recall(words, candidate_words) < .70:
            index += 1
            continue
        untouched = result[:index] + result[index + 1:]
        if any(candidate['start'] < other['end'] - .001
               and candidate['end'] > other['start'] + .001
               for candidate in candidates for other in untouched):
            index += 1
            continue
        result[index:index + 1] = [dict(item) for item in candidates]
        index += len(candidates)
    return result


def snap_whispered_onset_to_strict_speech(candidates, speech_intervals, *,
                                          minimum_shift=.3, maximum_shift=4.0):
    """Retime a no-VAD candidate's start to where a low-threshold VAD sweep
    actually found audio energy, when the model's own estimate starts well
    before it.

    The whispered-gap pass has no VAD of its own to anchor its segment
    boundaries, so it can place a cue right where an earlier, unrelated
    sound trails off (a title card's voice-over, background music) instead
    of where the quiet words it transcribed are actually spoken. Only the
    start moves, and only forward, never later than the first matching VAD
    span found within the candidate's own span.
    """
    result = []
    for candidate in candidates:
        ahead = [item for item in speech_intervals
                if item['start'] >= candidate['start'] - .1 and item['start'] < candidate['end']]
        if not ahead:
            result.append(dict(candidate))
            continue
        first = min(ahead, key=lambda item: item['start'])
        shift = first['start'] - candidate['start']
        if not minimum_shift <= shift <= maximum_shift:
            result.append(dict(candidate))
            continue
        updated = dict(candidate)
        updated['start'] = round(max(candidate['start'], first['start'] - minimum_shift), 3)
        result.append(updated)
    return result


def plan_v2_prefix_confirmations(base, v2_rows, duration):
    """Ask another reader about a timed pronoun missing from a long cue's lead."""
    plans = []
    pronouns = {'i', 'you', 'he', 'she', 'it', 'we', 'they'}
    for row in base:
        text = str(row.get('text', '')).strip()
        old = _normalized(text).split()
        if len(old) < 8 or not text[:1].islower():
            continue
        candidates = []
        for candidate in v2_rows:
            new = _normalized(candidate.get('text', '')).split()
            words = candidate.get('words', [])
            if (not new or new[0] not in pronouns or not words
                    or _normalized(words[0].get('text', '')) != new[0]
                    or not float(candidate.get('confidence', 0)) >= .55
                    or not float(words[0].get('probability', 0)) >= .55
                    or not .08 <= words[0]['end'] - words[0]['start'] <= 1.5
                    or not candidate['start'] <= words[0]['start'] < row['start']
                    or not words[0]['end'] <= row['start'] + .35
                    or not abs(candidate['start'] - row['start']) <= 1.5
                    or not abs(candidate['end'] - row['end']) <= 1.5):
                continue
            # Apart from the pronoun, v2 must agree with the complete cue or
            # lack only its last token. A different phrase is not a prefix.
            body = new[1:]
            if body != old and not (body == old[:-1] and len(body) / len(old) >= .9):
                continue
            if row.get('words') and words[0]['end'] > row['words'][0]['start'] + .02:
                continue
            candidates.append(candidate)
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        plans.append({'start': max(0., float(int(candidate['start'] - 2))),
                      'end': min(duration, float(int(row['end'] + 1.5) + 1)),
                      'target_start': row['start'], 'target_end': row['end'],
                      'target_text': row['text'], 'v2_candidate': dict(candidate)})
    return plans


def restore_confirmed_v2_prefixes(base, probes):
    """Restore only a v2-timed pronoun whose entire cue another reader confirms.

    The independent text may supply punctuation on matched words. It cannot
    supply additional words or retime the rest of the cue or its neighbours.
    """
    result = [dict(row) for row in base]
    for probe in probes:
        candidate = probe['v2_candidate']
        for index, row in enumerate(result):
            if (row['start'] != probe['target_start'] or row['end'] != probe['target_end']
                    or row['text'] != probe['target_text']
                    or not plan_v2_prefix_confirmations([row], [candidate], probe['end'])):
                continue
            prefix = candidate['words'][0]
            tokens = (str(prefix['text']).strip() + ' ' + row['text']).split()
            heard = str(probe.get('text', '')).split()
            canonical = lambda text: _normalized(text)
            # Every original word, including an uncertain trailing article,
            # must be read in the same order. Never complete a fragment.
            if (len(heard) < len(tokens)
                    or list(map(canonical, heard[:len(tokens)])) != list(map(canonical, tokens))):
                continue
            start = candidate['start']
            if (row['end'] - start > 7
                    or any(start < other['end'] and row['end'] > other['start']
                           for pos, other in enumerate(result) if pos != index)):
                continue
            updated = {**row, 'start': start,
                       'text': ' '.join(heard[:len(tokens)])}
            if row.get('words'):
                updated['words'] = [dict(prefix)] + [dict(word) for word in row['words']]
            result[index] = updated
            break
    return result


def filter_unconfirmed_stretched_words(candidates, corroborating_segments=()):
    """A no-VAD word spanning silence needs another timed reading of the phrase.

    Confidence alone cannot certify a stretched word: Whisper can assign a
    confident token to several seconds of music. Keep ordinary word timings
    and independently heard long words; never identify hallucinations by names.
    """
    kept = []
    for candidate in candidates:
        words = candidate.get('words', [])
        stretched = any(float(word['end']) - float(word['start']) > 1.5
                        for word in words)
        if stretched and not any(
                _normalized(other.get('text', '')) == _normalized(candidate.get('text', ''))
                and float(other.get('confidence', 0)) >= .6
                and abs(other['start'] - candidate['start']) <= 1.5
                and abs(other['end'] - candidate['end']) <= 1.5
                and not any(float(word['end']) - float(word['start']) > 1.5
                            for word in other.get('words', []))
                for other in corroborating_segments):
            continue
        kept.append(dict(candidate))
    return kept


def insert_confident_whispered_speech(base, candidates, duration, *, minimum_confidence=.5,
                                      minimum_words=2, maximum_gap=40.0):
    """Add speech recovered from a long, otherwise-silent gap -- only when it
    clears a strict bar of its own, since nothing else corroborates it.

    These candidates come from stretches Whisper's own VAD skipped entirely
    (too quiet to register as speech at all -- whispered dialogue is the
    common real case), so unlike every other rescue mechanism in this module
    there is no existing neighbouring cue or second pass to compare against.
    A short, low-confidence guess here is exactly the kind of thing Whisper
    hallucinates near silence, so the bar is deliberately higher, not lower,
    than everywhere else: a longer minimum phrase, a stricter confidence
    floor, and an explicit block on the stock phrases Whisper reaches for
    when it is really just describing silence.
    """
    stock_phrases = {
        "we ll be right back", 'thank you', 'thanks', 'thank you for watching',
        'subscribe', 'please subscribe', 'like and subscribe', 'see you next time',
        'bye', 'goodbye', 'okay', 'ok',
    }
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for candidate in sorted(candidates, key=lambda row: (row['start'], row['end'])):
        text = str(candidate.get('text', '')).strip()
        words = _normalized(text).split()
        if (len(words) < minimum_words
                or _normalized(text) in stock_phrases
                or float(candidate.get('confidence', 0)) < minimum_confidence):
            continue
        overlapping = [row for row in result
                       if candidate['start'] < row['end'] and candidate['end'] > row['start']]
        if overlapping:
            continue
        previous = [row for row in result if row['end'] <= candidate['start']]
        following = [row for row in result if row['start'] >= candidate['end']]
        previous_gap = (candidate['start'] - previous[-1]['end']) if previous else candidate['start']
        following_gap = ((following[0]['start'] - candidate['end']) if following
                         else (duration - candidate['end']))
        if previous_gap > maximum_gap or following_gap > maximum_gap:
            continue
        result.append(dict(candidate))
        result.sort(key=lambda row: (row['start'], row['end']))
    return result


def reconcile_corroborated_rephrasings(base, precision, rescue, speech_intervals):
    """Replace a short conflicting fragment when two offset v3 reads agree."""
    result = [dict(row) for row in sorted(base, key=lambda row: (row['start'], row['end']))]
    for candidate in sorted(precision, key=lambda row: row['start']):
        words = _normalized(candidate.get('text', '')).split()
        if (len(words) < 4 or candidate['end'] - candidate['start'] > 6
                or float(candidate.get('confidence', 0)) < .5):
            continue
        if not any(
                _normalized(other.get('text', '')) == _normalized(candidate['text'])
                and abs(other['start'] - candidate['start']) <= .7
                and abs(other['end'] - candidate['end']) <= .7
                and float(other.get('confidence', 0)) >= .75
                for other in rescue):
            continue
        speech_overlap = sum(max(0.0, min(candidate['end'], item['end'])
                                 - max(candidate['start'], item['start']))
                             for item in speech_intervals)
        if speech_overlap < .18:
            continue
        overlapping = [row for row in result
                       if candidate['start'] < row['end']
                       and candidate['end'] > row['start']]
        if not 1 <= len(overlapping) <= 3:
            continue
        original = _normalized(' '.join(row['text'] for row in overlapping)).split()
        if (not original or len(original) >= len(words)
                or candidate['start'] < overlapping[0]['start'] - 1.0
                or candidate['end'] > overlapping[-1]['end'] + 1.0):
            continue
        untouched = [row for row in result if row not in overlapping]
        if any(candidate['start'] < row['end'] - .001
               and candidate['end'] > row['start'] + .001 for row in untouched):
            continue
        result = sorted(untouched + [dict(candidate)],
                        key=lambda row: (row['start'], row['end']))
    return result


def filter_unsupported_hallucinations(primary, secondary):
    """Drop silence-prone stock phrases unless both ASR profiles heard them."""
    stock_phrases = {
        'thank you', 'thanks', 'thank you very much', 'merci', 'merci beaucoup',
        'you', 'okay', 'ok', 'bye', 'goodbye', 'subscribe', 'we ll be right back',
    }

    def supported(candidate, other):
        candidate_text = _normalized(candidate.get('text', ''))
        if candidate_text not in stock_phrases:
            return True
        candidate_midpoint = (candidate['start'] + candidate['end']) / 2
        for row in other:
            row_midpoint = (row['start'] + row['end']) / 2
            if abs(candidate_midpoint - row_midpoint) > 2.0:
                continue
            similarity = SequenceMatcher(
                None, _canonical(candidate.get('text', '')), _canonical(row.get('text', ''))
            ).ratio()
            if similarity >= .72:
                return True
        return False

    return ([dict(row) for row in primary if supported(row, secondary)],
            [dict(row) for row in secondary if supported(row, primary)])


def filter_isolated_short_hallucinations(segments, *, isolation_gap=8.0,
                                        corroborating_segments=()):
    """Remove tiny fragments unless another reading joins them into a sentence."""
    prone = {'you', 'thank you', 'thanks', 'okay', 'ok', 'bye', 'goodbye'}
    rows = [dict(row) for row in sorted(segments, key=lambda row: (row['start'], row['end']))]
    kept = []
    for index, row in enumerate(rows):
        text = str(row.get('text', '')).strip()
        words = _normalized(text).split()
        cue_duration = float(row['end']) - float(row['start'])
        if len(words) == 1 and cue_duration <= .35 and not text.endswith(('.', '?', '!')):
            previous = rows[index - 1] if index else None
            following = rows[index + 1] if index + 1 < len(rows) else None
            before_words = _normalized(previous['text']).split() if previous else []
            after_words = _normalized(following['text']).split() if following else []
            # A short word between two established cues can be a real word
            # split off by word timestamps. Preserve it only when a separate
            # ASR window reads the exact three-word boundary continuously.
            # The later short-lead filter still requires independent speech
            # support, so a second Whisper window alone cannot certify audio.
            joined = (len(before_words) >= 2 and len(after_words) >= 2
                      and 0 <= row['start'] - previous['end'] <= .6
                      and 0 <= following['start'] - row['end'] <= .6
                      and any(
                          other['start'] <= previous['end']
                          and other['end'] >= following['start']
                          and before_words[-1:] + words + after_words[:1]
                          == _normalized(other.get('text', '')).split()[
                              offset:offset + 3]
                          for other in corroborating_segments
                          for offset in range(max(0, len(_normalized(
                              other.get('text', '')).split()) - 2))
                      ))
            if not joined:
                continue
        before = (float(row['start']) - float(rows[index - 1]['end'])
                  if index else float('inf'))
        after = (float(rows[index + 1]['start']) - float(row['end'])
                 if index + 1 < len(rows) else float('inf'))
        if (len(words) == 1 and cue_duration <= .8
                and not text.endswith(('.', '?', '!'))
                and before >= 3.0 and after >= 3.0):
            continue
        if (' '.join(words) in prone and cue_duration <= 1.0
                and before >= isolation_gap and after >= isolation_gap):
            continue
        kept.append(row)
    return kept


def filter_unconfirmed_short_leads(segments, profiles, speech_intervals):
    """Drop a dangling one-word cue without speech or independent rescue support.

    Context and precision use the same VAD-based Whisper model, so agreement
    between only those two is not independent evidence of a quiet word.
    """
    speech = [(float(item['start']), float(item['end'])) for item in speech_intervals]
    kept = []
    for row in segments:
        text = str(row.get('text', '')).strip()
        words = text.split()
        start, end = float(row['start']), float(row['end'])
        if (len(words) != 1 or end - start > 1.0
                or text.endswith(('.', '?', '!'))):
            kept.append(row)
            continue
        overlap = sum(max(0.0, min(end, b) - max(start, a)) for a, b in speech)
        def matching(profile):
            return any(abs(other['start'] - start) <= 1.0
                       and abs(other['end'] - end) <= 1.0
                       and _normalized(other.get('text', '')) == _normalized(text)
                       for other in profile)
        # The third profile is the VAD-free rescue pass. It must agree with
        # either VAD-based pass when the stricter speech check is negative.
        supported = (len(profiles) >= 3 and matching(profiles[2])
                     and any(matching(profile) for profile in profiles[:2]))
        if overlap >= .18 or supported:
            kept.append(row)
    return kept


def filter_isolated_without_speech(segments, speech_intervals, *, isolation_gap=30.0,
                                   maximum_duration=4.0, minimum_overlap=0.18,
                                   opening_seconds=30.0, opening_gap=90.0):
    """Drop a short, isolated ASR cue when a stricter VAD heard no speech there.

    This is intentionally narrower than a general VAD filter. Quiet dialogue inside a
    conversation remains untouched; only cues surrounded by long silence need support
    from the independent, high-threshold speech pass.
    """
    rows = [dict(row) for row in sorted(segments, key=lambda row: (row['start'], row['end']))]
    speech = [(float(item['start']), float(item['end'])) for item in speech_intervals
              if float(item['end']) > float(item['start'])]
    kept = []
    for index, row in enumerate(rows):
        start, end = float(row['start']), float(row['end'])
        before = start - float(rows[index - 1]['end']) if index else float('inf')
        after = float(rows[index + 1]['start']) - end if index + 1 < len(rows) else float('inf')
        isolated = before >= isolation_gap and after >= isolation_gap
        overlap = sum(max(0.0, min(end, speech_end) - max(start, speech_start))
                      for speech_start, speech_end in speech)
        supported = overlap >= min(minimum_overlap, max(.08, (end - start) * .18))
        # Title music can fool both Whisper and an acoustic VAD. A lone cue in
        # the first seconds followed by a very long dialogue-free opening is
        # therefore rejected even when the stricter VAD heard speech-like audio.
        unsupported_opening = index == 0 and start < opening_seconds and after >= opening_gap
        if (unsupported_opening
                or isolated and end - start <= maximum_duration and not supported):
            continue
        kept.append(row)
    return kept


def reattach_dangling_leads(segments, *, minimum_gap=3.0, maximum_gap=30.0,
                            subjects_before_lowercase_only=False):
    """Move a stray sentence lead to the later phrase it actually belongs to.

    `subjects_before_lowercase_only` is the safe early form: only a lone subject pronoun
    ("I") with no sentence end, followed by a cue that starts in lowercase (so it is a
    continuation, "like you, Gun."). Run it BEFORE the isolated-fragment filters: a lead
    placed at the wrong time (real job 18: "I" at 124.3 s, spoken just before "like" at
    127.9 s) is otherwise removed as an unsupported lone word and its sentence loses it.
    """
    leads = {
        'and', 'but', 'he', 'her', 'how', 'i', 'if', 'it', "it's", 'my', 'she',
        'it s', "there's", 'there s', 'they', 'this', 'we', 'what', 'when', 'where', 'who', 'why',
        'you', 'your',
    }
    rows = [dict(row) for row in sorted(segments, key=lambda row: (row['start'], row['end']))]
    remove = set()
    for index, row in enumerate(rows[:-1]):
        words = _normalized(row.get('text', '')).split()
        following = rows[index + 1]
        gap = following['start'] - row['end']
        following_words = _normalized(following.get('text', '')).split()
        lead = ' '.join(words)
        if subjects_before_lowercase_only:
            first_letter = next((c for c in str(following.get('text', '')) if c.isalpha()), '')
            if lead not in {'i', 'you', 'we', 'they', 'he', 'she', 'it'} or not first_letter.islower():
                continue
        if (lead in leads
                and not str(row.get('text', '')).rstrip().endswith(('.', '?', '!'))
                and len(following_words) >= 2 and minimum_gap <= gap <= maximum_gap):
            if following_words[0] != lead:
                following['text'] = f"{str(row['text']).strip()} {str(following['text']).strip()}"
            following['start'] = round(max(row['end'], following['start'] - .12), 3)
            remove.add(index)
    return [row for index, row in enumerate(rows) if index not in remove]
