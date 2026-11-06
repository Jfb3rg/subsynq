"""Does a downloaded subtitle belong to THIS file? Its cue times are compared with where the file's own audio has speech.

Calibrated 2026-11-04 on the user's library (Silero VAD, centre channel for 5.1/7.1). Six 90 s windows: own subtitle accepted for 18 of
20 films, another film's for 0 of 40, but Example Film (little dialogue) found too few cues per window. Ten windows, 20 other films:
own subtitles 3-9 windows agree and margin 0.09-0.31; other films' at most 2 windows and margin at most 0.08 -- each measure separates
on its own and both must hold, see `verdict`. Plain loudness (ffmpeg silencedetect) could not tell them apart: music and effects fill
the dialogue band; a wider ±120 s baseline separated worse (another film reached 0.26) and is not used.
"""
import re
import statistics

TIME = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})(\s*-->\s*)(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')
SCALES = (1.0, 25 / 23.976, 23.976 / 25)          # PAL/film speed-up is the common version difference
OFFSETS = tuple(x / 10 for x in range(-150, 151))   # ±15 s in 0.1 s steps
MINIMUM_MARGIN = 0.09      # own subtitles: 0.09-0.31 above the typical share at wrong offsets; other films' at most 0.08
MINIMUM_AGREEING_WINDOWS = 3   # own: 3-9 of ten windows fitted alone agree; other films' at most 2
MINIMUM_CUES = 15


def _seconds(h, m, s, f):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(f.ljust(3, '0')) / 1000


def cue_times(text):
    return [(start, end) for start, end in
            ((_seconds(*match.group(1, 2, 3, 4)), _seconds(*match.group(6, 7, 8, 9))) for match in TIME.finditer(text)) if end > start]


def windows_for(duration, count=10, length=90.0):
    """Ten 90 s windows spread over the film (8 % to 88 %), so the opening credits and the end credits are left out."""
    duration = float(duration or 0)
    if duration < length * 2:
        return [(0.0, duration)] if duration > 30 else []
    return [(duration * (0.08 + 0.8 * i / (count - 1)) - length / 2, length) for i in range(count)]


def _overlap(a, b):
    i = j = 0
    total = 0.0
    while i < len(a) and j < len(b):
        low, high = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if high > low:
            total += high - low
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def fit(cues, speech, windows, *, offsets=OFFSETS, scales=SCALES):
    """(best share of cue time over speech, offset, scale, median share at the other offsets of that scale, cues inside the windows)."""
    speech = sorted(speech)
    best, typical, used = (0.0, 0.0, 1.0), {}, 0
    for scale in scales:
        shares = []
        for offset in offsets:
            moved = sorted((s * scale + offset, e * scale + offset) for s, e in cues
                           if any(w <= s * scale + offset and e * scale + offset <= w + length for w, length in windows))
            length = sum(e - s for s, e in moved)
            share = _overlap(moved, speech) / length if length else 0.0
            shares.append(share)
            if share > best[0]:
                best, used = (share, offset, scale), len(moved)
        typical[scale] = statistics.median(shares) if shares else 0.0
    return best[0], best[1], best[2], typical[best[2]], used


def window_agreement(cues, speech, windows, offset, scale, *, tolerance=0.6):
    """(windows whose own best fit lies within `tolerance` of the overall offset at the same scale, windows with at least 5 cues)."""
    agree = counted = 0
    for window in windows:
        start, end = window[0], window[0] + window[1]
        if sum(1 for s, e in cues if start <= s * scale + offset and e * scale + offset <= end) < 5:
            continue
        counted += 1
        alone = fit(cues, [x for x in speech if x[1] > start and x[0] < end], [window])
        if alone[2] == scale and abs(alone[1] - offset) <= tolerance:
            agree += 1
    return agree, counted


def verdict(subtitle_text, speech, windows):
    """{'fits', 'offset', 'scale', 'margin', 'agreeing', 'windows', 'cues', 'share'}: `fits` only when the cues match the speech clearly
    better at one offset than at the others AND at least three windows, fitted on their own, find that same offset."""
    cues = cue_times(subtitle_text)
    share, offset, scale, typical, used = fit(cues, speech, windows)
    agreeing, counted = window_agreement(cues, speech, windows, offset, scale) if used >= MINIMUM_CUES else (0, 0)
    margin = share - typical
    return {'fits': used >= MINIMUM_CUES and margin >= MINIMUM_MARGIN and agreeing >= MINIMUM_AGREEING_WINDOWS,
            'offset': round(offset, 1), 'scale': scale, 'margin': round(margin, 3), 'agreeing': agreeing, 'windows': counted,
            'cues': used, 'share': round(share, 3)}


def _clock(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    return f'{hours:02d}:{minutes:02d}:{rest // 1000:02d},{rest % 1000:03d}'


def shift(subtitle_text, offset, scale):
    """The subtitle with every time moved to the measured fit (time * scale + offset); unchanged when the fit is exact."""
    if offset == 0 and scale == 1.0:
        return subtitle_text
    def moved(match):
        start = _seconds(*match.group(1, 2, 3, 4)) * scale + offset
        end = _seconds(*match.group(6, 7, 8, 9)) * scale + offset
        return f'{_clock(start)}{match.group(5)}{_clock(end)}'
    return TIME.sub(moved, subtitle_text)
