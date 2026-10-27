"""Conservative subtitle timing helpers based on independently detected speech."""
import bisect
import math


def pad_reading_time(rows, spans, duration, *, target_key='sv', characters_per_second=20,
                     maximum_hold=1.0, maximum_duration=7.0, gap=.08):
    """Extend DISPLAY ends into available pauses; never move starts, speech or words.

    The caller supplies already accepted subtitle cues, not ASR candidates.
    Spans are the existing independent speech detector's evidence. A new voice,
    the next cue, the media end and a one-second hold cap all bound the padding.
    Insufficient space keeps the full text; this is not a claim of adequate speed.
    """
    result = [dict(row) for row in rows]
    if not spans or not result:
        return result, []
    settings = (duration, characters_per_second, maximum_hold, maximum_duration, gap)
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in settings)
            or duration <= 0 or characters_per_second <= 0 or min(maximum_hold, maximum_duration, gap) < 0):
        raise ValueError('Ogiltiga gränser för undertextens lästid.')
    merged = []
    for span in sorted(spans, key=lambda item: item['start']):
        if any(type(span[key]) not in (int, float) for key in ('start', 'end')):
            raise ValueError('Ogiltigt talstöd för undertextens lästid.')
        start, end = float(span['start']), float(span['end'])
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError('Ogiltigt talstöd för undertextens lästid.')
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    starts = [span[0] for span in merged]
    changes = []
    for index, row in enumerate(result):
        start, end = float(row['start']), float(row['end'])
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError('Ogiltig radtid för undertextens lästid.')
        text = ' '.join(str(row.get(target_key) or '').split())
        original_end = float(row.get('reading_time_source_end', end))
        if not math.isfinite(original_end) or not start < original_end <= end:
            raise ValueError('Ogiltig ursprungstid för undertextens lästid.')
        cap = min(original_end + maximum_hold, start + maximum_duration, duration)
        if index + 1 < len(result):
            cap = min(cap, float(result[index + 1]['start']) - gap)
        next_voice = bisect.bisect_left(starts, original_end)
        if next_voice < len(starts):
            cap = min(cap, starts[next_voice] - gap)
        display_end = math.floor(min(start + len(text) / characters_per_second, cap) * 1000 + 1e-7) / 1000
        if display_end - end < .1:
            continue
        row['end'] = display_end
        row['reading_time_source_end'] = original_end
        changes.append({'id': row['id'], 'cue_end_before': original_end, 'display_end': display_end,
                        'cps_before': round(len(text) / (original_end - start), 1),
                        'cps_after': round(len(text) / (display_end - start), 1)})
    return result, changes


def speech_span(words, *, minimum_probability=0.35, padding=0.08):
    """Return a tight cue span from trustworthy word timestamps, or None."""
    accepted = [word for word in words
                if word.get('start') is not None and word.get('end') is not None
                and word.get('probability', 0) >= minimum_probability
                and word['end'] > word['start']]
    if not accepted:
        return None
    return (round(max(0, accepted[0]['start'] - padding), 3),
            round(accepted[-1]['end'] + padding, 3))


def reject_long_bridge(segment, *, maximum_duration=12):
    """Reject ASR text spanning a long gap instead of silently exporting it."""
    return segment['end'] - segment['start'] > maximum_duration
