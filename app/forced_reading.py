"""Whisper large-v3 or large-v2 for a language whose script identifies it (Tamil, Hangul, ...).

When the first pass shows a script (Tamil letters = Tamil), the whole transcript is replaced by one more reading with the
language fixed. That reading used to be Whisper large-v3 only. On the Tamil trailer Sigma (dev jobs 42 and 50, and a
benchmark on a rented 24 GB RTX 3090, 2026-10-16) large-v3 with Tamil fixed wrote "che meses", "Omaha down", "descent of
bunu", "PROFESSOR" and Greek letters among the Tamil (average log-probability -1.4), while large-v2 with the same
settings read clean Tamil lines (-0.27). The language-specific fine-tunes tried (Tamil medium/small, two Korean large-v3
turbo) were worse: long unsegmented blocks, news sentences that were never said, loops. So the choice here is between
the two stock models, and the second one is read only when the first reading looks unreliable and used only when it is
clearly better."""
import unicodedata
from collections import Counter

CONTAMINATION_LIMIT = .15       # share of a cue's letters outside the transcript's own script
NEEDS_SECOND_CONTAMINATION = .10
NEEDS_SECOND_CONFIDENCE = .50
SECOND_MARGIN = .10


def _script(character):
    return unicodedata.name(character, 'UNKNOWN').split(' ')[0]


def _letters(text):
    return [character for character in str(text) if character.isalpha()]


def dominant_script(rows):
    counts = Counter(_script(character) for row in rows for character in _letters(row.get('text', '')))
    return counts.most_common(1)[0][0] if counts else None


def contaminated_share(rows):
    """Share of the cues (with at least four letters) in which more than 15 % of the letters are in another script
    than the transcript's own: the Latin, Greek or Japanese scraps a wrongly decoded Tamil line is full of."""
    script = dominant_script(rows)
    counted = contaminated = 0
    for row in rows:
        letters = _letters(row.get('text', ''))
        if len(letters) < 4:
            continue
        counted += 1
        if sum(1 for character in letters if _script(character) != script) > CONTAMINATION_LIMIT * len(letters):
            contaminated += 1
    return contaminated / counted if counted else 0.0


def mean_confidence(rows):
    """Duration-weighted mean of the cues' recorded confidence (0 when nothing was recorded)."""
    total = weight = 0.0
    for row in rows:
        confidence = float(row.get('confidence') or 0)
        seconds = max(float(row.get('end', 0)) - float(row.get('start', 0)), .1)
        total += confidence * seconds
        weight += seconds
    return total / weight if weight else 0.0


def reading_score(rows):
    return mean_confidence(rows) - contaminated_share(rows)


def needs_second_reading(rows):
    """True when the reading is full of foreign-script scraps or low in confidence, so a second model is worth its time."""
    return bool(rows) and (contaminated_share(rows) >= NEEDS_SECOND_CONTAMINATION
                           or mean_confidence(rows) < NEEDS_SECOND_CONFIDENCE)


def prefer_second_reading(first, second):
    """True when the second reading has no more foreign-script scraps than the first and scores at least `SECOND_MARGIN`
    higher (mean confidence minus scrap share). Anything else keeps the first reading."""
    if not second:
        return False
    return (contaminated_share(second) <= contaminated_share(first)
            and reading_score(second) >= reading_score(first) + SECOND_MARGIN)


def reading_summary(rows):
    return {'cues': len(rows), 'mean_confidence': round(mean_confidence(rows), 3),
            'contaminated_share': round(contaminated_share(rows), 3)}
