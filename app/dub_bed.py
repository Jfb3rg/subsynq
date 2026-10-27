"""Speech that a dubbed version of the same mix kept unchanged is background, not dialogue.

MAN OF WAR (dev jobs 49, 150, 152, 165): two stereo tracks, German dub and English original, with the same music and effects underneath
(the German track lies 72.6 ms behind, correlation 0.70-0.90). From 26.25 to 27.5 s the two tracks are identical (correlation 1.00, the
English-minus-German signal 25-30 dB below the English): the shout five readers heard as "Come on, you niggas / dickhead / idiot / chicken /
liars" is in the effects bed, the dubbing studio did not translate it, and in the isolated English dialogue three readers hear only "What?".
Every real line of the trailer sits at 0 to -8 dB; that row at -27.4 dB with no block of English-only sound. A row like that is not a line of
the film and is removed (and reported); nothing else is changed, and no quality limit is touched.
"""
import numpy as np

RATE = 16000
BLOCK_SECONDS = .25
MINIMUM_CORRELATION = .6       # MAN OF WAR: 0.70-0.90 at three places; unrelated tracks lie near 0
MAXIMUM_LAG_SECONDS = 2.0
BED_LEVEL_DB = -15.0           # a row's median own level at or below this is the shared bed (dialogue rows: 0 to -8 dB)
OWN_BLOCK_DB = -10.0           # a block above this holds sound of the chosen track alone
MINIMUM_ROW_BLOCKS = 2         # half a second at least: shorter rows are not judged
MAXIMUM_SPEECH_RATE = 6.0      # words per second (asr_windows.MAXIMUM_SPEECH_RATE)
MINIMUM_DIALOGUE_SHARE = .5    # a dub replaces the dialogue: most rows must differ (MAN OF WAR: 27 of 29). Two copies of the SAME language
                               # (Stuart Fails to Save the Universe: correlation 1.00, no lag) differ nowhere and must never lose a row.


def find_lag(chosen, other, *, rate=RATE, maximum_lag=MAXIMUM_LAG_SECONDS):
    """(lag in samples, peak correlation) with chosen[i] ~ other[i + lag], from one stretch of both tracks."""
    n = min(len(chosen), len(other))
    if n < rate:
        return 0, 0.0
    a, b = np.asarray(chosen[:n], dtype=np.float64), np.asarray(other[:n], dtype=np.float64)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    cross = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    limit = int(maximum_lag * rate)
    lags = np.concatenate([np.arange(0, limit + 1), np.arange(-limit, 0)])
    values = np.concatenate([cross[:limit + 1], cross[size - limit:]])
    best = int(np.argmax(np.abs(values)))
    norm = float(np.sqrt((a ** 2).sum() * (b ** 2).sum())) or 1.0
    return int(lags[best]), float(values[best] / norm)


def shared_mix_lag(probes, *, minimum_correlation=MINIMUM_CORRELATION):
    """The lag when at least two probe stretches [(chosen, other), ...] agree on it (within 2 ms) with a high correlation, else None."""
    found = [find_lag(chosen, other) for chosen, other in probes]
    good = [lag for lag, correlation in found if correlation >= minimum_correlation]
    for lag in good:
        if sum(abs(lag - other) <= RATE * .002 for other in good) >= 2:
            return lag
    return None


def block_levels(chosen, other, lag, *, offset=0.0, rate=RATE, block=BLOCK_SECONDS):
    """[(start seconds, dB of what is left of the chosen track after the aligned other track is taken away, relative to the chosen track)].
    The other track's level is fitted per block (the two mixes need not be equally loud)."""
    chosen, other = np.asarray(chosen, dtype=np.float64), np.asarray(other, dtype=np.float64)
    if lag >= 0:
        n = min(len(chosen), len(other) - lag)
        a, b, shift = chosen[:n], other[lag:lag + n], 0
    else:
        n = min(len(chosen) + lag, len(other))
        a, b, shift = chosen[-lag:-lag + n], other[:n], -lag
    size = int(block * rate)
    levels = []
    for start in range(0, max(0, n - size + 1), size):
        x, y = a[start:start + size], b[start:start + size]
        energy = float((x * x).mean())
        if energy < 1e-10:
            continue
        gain = float((x * y).sum() / ((y * y).sum() + 1e-12))
        rest = x - gain * y
        levels.append((offset + (start + shift) / rate, 10 * np.log10((float((rest * rest).mean()) + 1e-12) / energy)))
    return levels


def bed_rows(rows, levels, *, bed_level=BED_LEVEL_DB, own_block=OWN_BLOCK_DB, minimum_blocks=MINIMUM_ROW_BLOCKS, block=BLOCK_SECONDS):
    """(kept rows, removed rows with their measurements, share of rows that are the chosen track's own dialogue): a row is removed only when it
    has at least `minimum_blocks` measured blocks, NONE of them holds sound of the chosen track alone, and their median lies at or below
    `bed_level`; and nothing is removed at all unless at least `MINIMUM_DIALOGUE_SHARE` of the rows are the chosen track's own (the other
    track is a dub, not a second copy of the same language)."""
    kept, removed, own_rows, judged = [], [], 0, 0
    for row in rows:
        inside = [level for start, level in levels if start >= row['start'] - .05 and start + block <= row['end'] + .05]
        own = sum(level > own_block for level in inside)
        median = float(np.median(inside)) if inside else 0.0
        if inside:
            judged += 1
            own_rows += median > own_block
        if len(inside) >= minimum_blocks and own == 0 and median <= bed_level:
            removed.append({'start': row['start'], 'end': row['end'], 'text': row.get('text', ''), 'median_db': round(median, 1), 'blocks': len(inside)})
        else:
            kept.append(row)
    share = own_rows / judged if judged else 0.0
    if share < MINIMUM_DIALOGUE_SHARE:
        return list(rows), [], share
    return kept, removed, share


def _words(text):
    return [word for word in ''.join(char if char.isalnum() or char in "'’" else ' ' for char in str(text)).split() if word]


def own_stretches(row, levels, *, own_block=OWN_BLOCK_DB, block=BLOCK_SECONDS):
    """The blocks of a row that hold sound of the chosen track alone, joined into [(start, end)]."""
    stretches = []
    for start, level in sorted(levels):
        if start < row['start'] - .05 or start + block > row['end'] + .05 or level <= own_block:
            continue
        if stretches and start <= stretches[-1][1] + 1e-6:
            stretches[-1][1] = start + block
        else:
            stretches.append([start, start + block])
    return [tuple(item) for item in stretches]


def overfull_rows(rows, levels, readings, *, bed_level=BED_LEVEL_DB, maximum_rate=MAXIMUM_SPEECH_RATE, margin=.5, minimum_confidence=.5):
    """(rows, changes): a row that lies mostly in the shared bed (median at or below `bed_level`) but whose words cannot be spoken in the little
    time it holds the chosen track's own sound (more than `maximum_rate` words per second) was heard into the background, not said. MAN OF WAR
    (dev jobs 152-170): "the f**k did I do that?" (27.61-29.03 s, the short pass alone, confidence 0.23) has 0.25 s of English-only sound, at
    27.5 s, where Whisper large-v2 heard "Run!" (27.46-27.82 s, also in the isolated English) and Qwen "One."; the user hears "Run".
    The row takes the text of another reading that lies within the own sound (`margin`: two blocks, Whisper starts a word early), is confident
    enough and fits it; without one the row stays as it was (APOCALYPSE Z, dev job 185: the radio line "Sí, aquí Core." is kept unchanged
    by the German dub and has 0.25 s of own sound, but every reader hears it: removing it lost a real line). (Until 0.8.309 such a row was
    removed.) Every change is returned with its measurements."""
    result, changes = [], []
    for row in rows:
        inside = [level for start, level in levels if start >= row['start'] - .05 and start + BLOCK_SECONDS <= row['end'] + .05]
        stretches = own_stretches(row, levels)
        own_seconds = sum(end - start for start, end in stretches)
        words = _words(row.get('text', ''))
        if (len(inside) < MINIMUM_ROW_BLOCKS or not stretches or float(np.median(inside)) > bed_level
                or len(words) <= maximum_rate * own_seconds):
            result.append(row)
            continue
        first, last = stretches[0][0], stretches[-1][1]
        fitting = [other for name, items in readings.items() for other in items
                   if first - margin <= other['start'] and other['end'] <= last + margin
                   and float(other.get('confidence') or 0) >= minimum_confidence
                   and 0 < len(_words(other.get('text', ''))) <= maximum_rate * max(own_seconds, other['end'] - other['start'])
                   and _words(other.get('text', '')) != words]
        change = {'start': row['start'], 'end': row['end'], 'text': row.get('text', ''), 'median_db': round(float(np.median(inside)), 1),
                  'own_seconds': round(own_seconds, 2)}
        if fitting:
            best = max(fitting, key=lambda item: float(item.get('confidence') or 0))
            result.append({**row, 'start': round(best['start'], 3), 'end': round(best['end'], 3), 'text': str(best['text']).strip()})
            change.update(replaced_by=str(best['text']).strip(), reading_start=best['start'], reading_end=best['end'])
        else:
            result.append(row)
            change['kept'] = True
        changes.append(change)
    return result, changes
