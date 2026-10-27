"""A second reading of the audio behind a row the review still doubts (the source text is replaced only when two model families agree)."""
import itertools
import unicodedata
from collections import Counter

from asr_windows import _family, _is_boilerplate, _mean_agreement, _medoid, _normalized, _similarity

MINIMUM_FAMILIES = 2
MINIMUM_AGREEMENT = .9   # mean similarity between the families that read the same words: near-unanimity, since replacing a text that exists is riskier than adding missing speech (KOKUHO row 8: three families agreed at 0.77 on a reading the burned-in subtitles contradict; the four right corrections agreed at 0.96 or more)
MINIMUM_GAIN = .1        # the agreed reading must fit the readings this much better than the current text does
SAME_TEXT = .9           # a reading this close to the current text is the same text
LENGTH_RANGE = (.4, 2.5)  # a reading this much shorter or longer than the current text is not the same utterance


def conflicting_reread_rows(rows, records, ids):
    """Evidence for still-hard meaning errors, not a new source choice or a soft flag.

    Ignore stale records, missing readers and successfully replaced sources. A
    conflict must be the existing strict reread verdict, with two usable families.
    """
    sources = {row['id']: row for row in rows if row['id'] in ids}
    result = []
    seen = set()
    for record in records:
        row = sources.get(record.get('id'))
        if (row is None or row['id'] in seen
                or record.get('decision') != 'modellfamiljerna läste olika ord'
                or _normalized(str(record.get('current', ''))) != _normalized(str(row.get('source', '')))):
            continue
        # Recompute rather than trust the number of family labels in a report.
        _, evidence = agreed_replacement(row['source'], record.get('readings') or [])
        if evidence['decision'] != 'modellfamiljerna läste olika ord':
            continue
        result.append({**evidence, 'id': row['id'], 'start': row['start'], 'end': row['end']})
        seen.add(row['id'])
    return result


def script_of(character):
    """The writing system of a letter ('CJK' for kana and han together, else the first word of its Unicode name: LATIN, HANGUL, DEVANAGARI, …)."""
    first = unicodedata.name(character, '').split(' ')[0]
    return 'CJK' if first in ('CJK', 'HIRAGANA', 'KATAKANA') else first


def script_shares(text):
    letters = [character for character in str(text) if character.isalpha()]
    counts = Counter(script_of(character) for character in letters)
    return {name: count / len(letters) for name, count in counts.items()} if letters else {}


def same_script(current, reading, *, minimum_share=.5):
    """A reading written in another script than the text it should replace (a Japanese line read as Spanish or Maori by a clip-sized language
    guess) is not the same utterance."""
    shares = script_shares(current)
    if not shares:
        return True
    dominant = max(shares, key=shares.get)
    return script_shares(reading).get(dominant, 0) >= minimum_share


def language_from_text(texts):
    """'ja' when kana is written among the sources, 'zh' when they are han only, else None (Hangul, Thai and the Indian scripts are in
    translation_qa.WHISPER_SCRIPT_LANGUAGES; a Latin script says nothing about the language)."""
    letters = [character for text in texts for character in str(text) if character.isalpha()]
    kana = sum(unicodedata.name(character, '').split(' ')[0] in ('HIRAGANA', 'KATAKANA') for character in letters)
    han = sum(unicodedata.name(character, '').startswith('CJK') for character in letters)
    if kana >= 3:
        return 'ja'
    return 'zh' if letters and han >= 4 and han / len(letters) >= .55 else None


def _letters(text):
    return _normalized(str(text)).replace(' ', '')


def is_loop(text):
    """A repeated syllable or character ("ああああ…") is a decoder loop, not speech."""
    letters = _letters(text)
    return len(letters) >= 12 and len(set(letters)) <= max(2, len(letters) // 10)


def usable_readings(current, readings):
    """The readings that can stand for the same utterance as `current`: text, no boilerplate, no loop, a similar length."""
    size = len(_letters(current))
    result = []
    for reading in readings:
        text = str(reading.get('text') or '').strip()
        length = len(_letters(text))
        if (not length or _is_boilerplate(text) or is_loop(text) or not same_script(current, text)
                or not (size * LENGTH_RANGE[0] <= length <= max(size * LENGTH_RANGE[1], size + 6))):
            continue
        result.append((reading['name'], {'text': text, 'confidence': float(reading.get('confidence') or 0)}))
    return result


def agreed_replacement(current, readings, *, minimum_families=MINIMUM_FAMILIES, minimum_agreement=MINIMUM_AGREEMENT,
                       minimum_gain=MINIMUM_GAIN):
    """(new text or None, record). `readings` holds {'name', 'text', 'confidence'}: one per model family (names as in the transcript's
    passes: 'precision' for Whisper large-v3, 'v2', 'qwen').

    Every family that gave a usable reading casts one vote (its best reading) and ALL of them must agree (mean similarity ≥ `minimum_agreement`;
    at least `minimum_families` families): the medoid of their readings is the new text. It replaces the current text only when it is not the
    current text (similarity < `SAME_TEXT`) and fits the readings clearly better than the current text does (`minimum_gain`). A wrong word the
    families all hear differently is left alone, and so is a case where two Whisper models agree and Qwen does not (measured: KOKUHO row 8, where
    both Whisper models read "聖地の王国" and the current text was not clearly worse)."""
    usable = usable_readings(current, readings)
    by_family = {}
    for name, row in usable:
        by_family.setdefault(_family(name), []).append((name, row))
    record = {'current': current, 'readings': [{'name': reading['name'], 'text': reading['text']} for reading in readings],
              'families': sorted(by_family)}
    if len(by_family) < minimum_families:
        record['decision'] = 'för få modellfamiljer läste stället'
        return None, record
    representatives = {family: (_medoid(members) if len(members) > 1 else members[0]) for family, members in by_family.items()}
    chosen = sorted(representatives)
    pairs = [_similarity(representatives[a][1]['text'], representatives[b][1]['text']) for a, b in itertools.combinations(chosen, 2)]
    group = (sum(pairs) / len(pairs), chosen)
    if group[0] < minimum_agreement:
        record['decision'] = 'modellfamiljerna läste olika ord'
        return None, record
    members = [representatives[family] for family in group[1]]
    _, best = _medoid(members)
    text = best['text']
    record.update(agreed_families=list(group[1]), agreement=round(group[0], 2), candidate=text)
    if _similarity(text, current) >= SAME_TEXT:
        record['decision'] = 'läsningen stämmer med nuvarande text'
        return None, record
    fit = _mean_agreement(current, members)
    record['current_fit'] = round(fit, 2)
    if group[0] < fit + minimum_gain:
        record['decision'] = 'nuvarande text passar läsningarna nästan lika bra'
        return None, record
    record['decision'] = 'ersatt'
    return text, record
