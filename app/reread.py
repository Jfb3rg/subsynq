"""A second reading of the audio behind a row the review still doubts (the source text is replaced only when two model families agree)."""
import itertools
import math
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from asr_windows import _family, _is_boilerplate, _mean_agreement, _medoid, _normalized, _similarity

MINIMUM_FAMILIES = 2
MINIMUM_AGREEMENT = .9   # mean similarity between the families that read the same words: near-unanimity, since replacing a text that exists is riskier than adding missing speech (KOKUHO row 8: three families agreed at 0.77 on a reading the burned-in subtitles contradict; the four right corrections agreed at 0.96 or more)
MINIMUM_GAIN = .1        # the agreed reading must fit the readings this much better than the current text does
SAME_TEXT = .9           # near-text guard; exact word confirmation is recorded separately
LENGTH_RANGE = (.4, 2.5)  # a reading this much shorter or longer than the current text is not the same utterance


def foreign_context_plan(rows, record, language):
    """Only rescue a short automatic read where BOTH Whispers used another script.

    Plan all three windows before decoding. The rounded window is retained as
    a control; a confident conflicting result from ANY window vetoes the change.
    """
    ordered = sorted(rows, key=lambda r: r['start'])
    index = next((i for i, r in enumerate(ordered) if r['id'] == record['id']), None)
    if index is None or not language:
        return None
    row = ordered[index]
    start, end = float(row['start']), float(row.get('reading_time_source_end', row['end']))
    readings = record.get('readings') or []
    by_name = {r['name']: r for r in readings}
    if (record.get('current') != row['source'] or not 0 < end-start <= 2
            or not all(math.isfinite(v) for v in (start, end)) or start < 0
            or sorted(by_name) != ['precision', 'qwen', 'v2'] or len(readings) != 3
            or not 2 <= len(_normalized(row['source']).split()) <= 6
            or any(row.get(k) for k in ('confirmed_source_words', 'confirmed_prefix', 'confirmed_lead_onset'))
            or any(same_script(row['source'], by_name[n]['text']) for n in ('precision', 'v2'))
            or not 2 <= len(_normalized(by_name['qwen']['text']).split()) <= 6
            or not usable_readings(row['source'], [by_name['qwen']])
            or _normalized(by_name['qwen']['text']) == _normalized(row['source'])):
        return None
    left = max(0., start-.35, float(ordered[index-1]['end'])+.04 if index else 0.)
    right = min(end+.35, float(ordered[index+1]['start'])-.04 if index+1 < len(ordered) else end+.35)
    if not left <= start < end <= right:
        return None
    windows = [(max(0., start-pad), end+pad) for pad in (3., 4.)]
    windows.append((max(0., math.floor(start)-3.), math.ceil(end)+3.))
    if len(set(windows)) != 3:
        return None
    return dict(id=row['id'], start=start, end=end, left=left, right=right,
                qwen=by_name['qwen']['text'], language=language, windows=windows)


def foreign_context_reading(plan, segments, offset, language, language_probability):
    """Keep the entire touched segment, including rejected words, for the report."""
    record = dict(language=language, language_probability=language_probability, segments=segments, accepted=False)
    touching = [s for s in segments if offset+s['end'] > plan['start'] and offset+s['start'] < plan['end']]
    if len(touching) != 1:
        return {**record, 'reason': 'inte exakt en hel replik'}
    segment = touching[0]
    words = segment.get('words') or []
    record.update(text=segment['text'].strip(), words=words)
    if (language != plan['language'] or not .8 <= language_probability <= 1
            or not 2 <= len(words) <= 6
            or _normalized(segment['text']) != _normalized(' '.join(w['word'] for w in words))):
        return {**record, 'reason': 'språket eller hela ordsekvensen är inte bekräftad'}
    for i, w in enumerate(words):
        a, b, p = w['start'], w['end'], w['probability']
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in (a, b, p))
                or not plan['left']-.000001 <= offset+a < offset+b <= plan['right']+.000001
                or b-a > 1.5 or not .6 <= p <= 1
                or i and not 0 <= a-words[i-1]['end'] <= .6):
            return {**record, 'reason': 'svagt ord eller ordtid utanför egen lucka'}
    a, b = offset+words[0]['start'], offset+words[-1]['end']
    if min(b, plan['end'])-max(a, plan['start']) < .6*(plan['end']-plan['start']):
        return {**record, 'reason': 'repliken täcker inte den gamla tiden'}
    return {**record, 'accepted': True, 'start': a, 'end': b,
            'confidence': min(w['probability'] for w in words)}


def foreign_context_replacement(current, plan, readings):
    """Two qualified windows per Whisper, no qualified dissent, plus independent Qwen."""
    expected = {(name, window) for name in ('precision', 'v2') for window in range(3)}
    if len(readings) != 6 or {(r['name'], r['window']) for r in readings} != expected:
        return None
    qualified = [r for r in readings if r['accepted']]
    if (any(sum(r['name']==name for r in qualified) < 2 for name in ('precision', 'v2'))
            or any(_normalized(r['text']) != _normalized(plan['qwen']) for r in qualified)
            or max(r['start'] for r in qualified)-min(r['start'] for r in qualified) > .5
            or max(r['end'] for r in qualified)-min(r['end'] for r in qualified) > .5):
        return None
    votes = [next(r for r in qualified if r['name']==name) for name in ('precision', 'v2')]
    text, record = agreed_replacement(current, votes+[dict(name='qwen', text=plan['qwen'], confidence=.5)])
    return (text, record, min(r['start'] for r in qualified), max(r['end'] for r in qualified)) if text else None


def interior_insertion_slot(current, readings):
    """A single unambiguous interior addition unanimously heard by all readers."""
    old = [_normalized(t) for t in current.split()]
    names = [r['name'] for r in readings]
    if (len(old) < 4 or any(len(t.split()) != 1 for t in old)
            or len(set(names)) != len(names) or not {'precision', 'v2', 'qwen'} <= set(names)):
        return None
    texts = [_normalized(r.get('text', '')).split() for r in readings]
    candidate = texts[0]
    if len(candidate) != len(old) + 1 or any(t != candidate for t in texts):
        return None
    slots = [i for i in range(1, len(candidate) - 2) if candidate[:i] + candidate[i+1:] == old]
    if (len(slots) != 1 or not candidate[slots[0]].isalpha() or candidate[slots[0]] in old
            or any(not same_script(current, r.get('text', '')) for r in readings)):
        return None
    return slots[0]


def confirmed_interior_insertion(row, readings, neighbours=()):
    """One missing interior word, with unanimous text and bounded acoustic evidence.

    This does not infer an onset from a stretched Whisper word: cue times stay
    unchanged. Both Whisper versions must locate the word's END and the next
    two strong anchors together. Qwen must independently read the entire same
    sentence and align a positive short word at that end, with the same anchors.
    Qwen supplies the second architecture, never a third Whisper-family vote.
    Preserve every existing character, including punctuation.
    """
    tokens = list(re.finditer(r'\S+', row['source']))
    index = interior_insertion_slot(row['source'], readings)
    if index is None:
        return None, None
    by_name = {r['name']: r for r in readings}
    candidate = _normalized(by_name['qwen'].get('text', '')).split()
    missing = candidate[index]
    # The same word in an adjacent cue may have leaked into the tight clip.
    if any(missing in (_normalized(n.get('source', '')).split()[-3:]
                       if n['end'] <= row['start'] else _normalized(n.get('source', '')).split()[:3])
           for n in neighbours):
        return None, None
    bounds = float(row['start']), float(row.get('reading_time_source_end', row['end']))
    if not all(math.isfinite(t) for t in bounds) or not 0 <= bounds[0] < bounds[1]:
        return None, None
    if any(float(n['start']) < bounds[1] and float(n['end']) > bounds[0] for n in neighbours):
        return None, None
    witnesses = []
    for name in ('precision', 'v2'):
        reading = by_name[name]
        words = reading.get('words') or []
        if [_normalized(w.get('word', '')) for w in words] != candidate:
            return None, None
        try:
            times = [(float(w['start']), float(w['end']), float(w['probability'])) for w in words]
            if (any(not all(math.isfinite(v) for v in t) or not 0 <= t[0] < t[1]
                    or not 0 <= t[2] <= 1 for t in times)
                    or any(a[1] > b[0] + .02 for a, b in zip(times, times[1:]))):
                return None, None
            triple = times[index:index+3]
            if any(not bounds[0] <= a < b <= bounds[1] or p < .8 for a, b, p in triple):
                return None, None
        except (KeyError, TypeError, ValueError):
            return None, None
        witnesses.append(triple)
    aligned = by_name['qwen'].get('words') or []
    if [_normalized(w.get('word', '')) for w in aligned] != candidate:
        return None, None
    try:
        intervals = [(float(w['start']), float(w['end'])) for w in aligned]
        if (any(not all(math.isfinite(v) for v in pair) or not 0 <= pair[0] < pair[1] for pair in intervals)
                or any(a[1] > b[0] + .02 for a, b in zip(intervals, intervals[1:]))):
            return None, None
        qwen = intervals[index:index+3]
        if (not .04 <= qwen[0][1] - qwen[0][0] <= 1.5
                or any(not bounds[0] <= a < b <= bounds[1] for a, b in qwen)
                or any(abs(w[0][1] - qwen[0][1]) > .25 for w in witnesses)
                or any(abs(a[k] - b[k]) > .35 for w in witnesses
                       for a, b in zip(w[1:], qwen[1:]) for k in (0, 1))):
            return None, None
    except (KeyError, TypeError, ValueError):
        return None, None
    if (abs(witnesses[0][0][1] - witnesses[1][0][1]) > .25
            or any(abs(a[k] - b[k]) > .35 for a, b in zip(witnesses[0][1:], witnesses[1][1:]) for k in (0, 1))):
        return None, None
    position = tokens[index].start()
    surface = str(aligned[index]['word']).strip()
    if not surface.isalpha() or _normalized(surface) != missing:
        return None, None
    text = row['source'][:position] + surface + ' ' + row['source'][position:]
    return text, {'word': missing, 'index': index, 'readers': ['precision', 'v2', 'qwen'],
                  'architectures': ['whisper', 'qwen'], 'timed_witnesses': witnesses,
                  'qwen_words': [dict(w) for w in aligned[index:index+3]],
                  'cue_times_preserved': True}


def conflicting_reread_rows(rows, records, ids):
    """Evidence for still-hard meaning errors, not a new source choice or a soft flag.

    Ignore stale records, missing readers and successfully replaced sources. A
    conflict is either divergent readings or unanimous different words whose
    small change still lacks the extra acoustic proof.
    """
    sources = {row['id']: row for row in rows if row['id'] in ids}
    result = []
    seen = set()
    for record in records:
        row = sources.get(record.get('id'))
        if (row is None or row['id'] in seen
                or record.get('decision') == 'ersatt'
                or _normalized(str(record.get('current', ''))) != _normalized(str(row.get('source', '')))):
            continue
        # Recompute rather than trust the number of family labels in a report.
        _, evidence = agreed_replacement(row['source'], record.get('readings') or [])
        if (record.get('decision') in ('omläsningen innehåller redan skrivna grannord',
                                      'omläsningen tappar prefix vid klippstart')
                and evidence.get('decision')=='ersatt'):
            result.append({**evidence,'decision':record['decision'],'id':row['id'],'start':row['start'],'end':row['end']})
            seen.add(row['id'])
            continue
        words = {_normalized(r['text']) for r in record.get('readings') or [] if r.get('text')}
        names = {r.get('name') for r in record.get('readings') or []}
        agreed_other_words = (evidence['decision'] in ('läsningen stämmer med nuvarande text', 'liknande text med olika ord',
                                                      'nuvarande text passar läsningarna nästan lika bra')
                              and 'qwen' in names and bool(names & {'precision', 'v2'})
                              and len(words) == 1 and _normalized(row['source']) not in words)
        if evidence['decision'] != 'modellfamiljerna läste olika ord' and not agreed_other_words:
            continue
        if agreed_other_words:
            evidence['decision'] = 'andra ord hördes men ordtillägget eller bytet är inte bekräftat'
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
    record = {'current': current, 'readings': [dict(reading) for reading in readings],
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
        record['same_words'] = all(_normalized(r['text']) == _normalized(current) for _, r in members)
        record['decision'] = 'läsningen stämmer med nuvarande text' if record['same_words'] else 'liknande text med olika ord'
        return None, record
    fit = _mean_agreement(current, members)
    record['current_fit'] = round(fit, 2)
    if group[0] < fit + minimum_gain:
        record['decision'] = 'nuvarande text passar läsningarna nästan lika bra'
        return None, record
    record['decision'] = 'ersatt'
    return text, record


def clipped_prefix_evidence(row, candidate, readings):
    """A tight clip cannot disprove a prefix when both weak first words hit its edge."""
    old, new = _normalized(row['source']).split(), _normalized(candidate).split()
    cjk = (len(old) == len(new) == 1 and all(
        script_of(c) == 'CJK' for c in old[0] + new[0]))
    if cjk:
        old, new = list(old[0]), list(new[0])
    ops = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    if (len(ops) < 2 or ops[0][0] != 'delete' or ops[0][1] != 0 or ops[0][3] != 0
            or ops[0][2] < (2 if cjk else 1) or ops[1][0] != 'equal'
            or ops[1][2] - ops[1][1] < 2):
        return None
    witnesses = []
    edge = max(0., float(row['start']) - .3)
    for name in ('precision', 'v2'):
        matches = [r for r in readings if r.get('name') == name]
        if len(matches) != 1 or _normalized(matches[0].get('text', '')) != _normalized(candidate):
            return None
        words = matches[0].get('words') or []
        if not words:
            return None
        try:
            a, b, p = (float(words[0][k]) for k in ('start', 'end', 'probability'))
            if (not all(math.isfinite(v) for v in (edge, a, b, p)) or not a < b
                    or abs(a-edge) > .04 or not 0 <= p < .6):
                return None
        except (KeyError, TypeError, ValueError):
            return None
        witnesses.append(dict(name=name, start=a, end=b, probability=p))
    return dict(removed_prefix=old[:ops[0][2]], clip_start=edge, witnesses=witnesses)


CONTEXT_WINDOWS = ((2.0, 1.0), (6.0, 3.0))   # seconds before/after the row: regression caserow 411, large-v3 heard "On three" only with >= 2 s before


def words_inside(words, start, end, *, margin=.15):
    """The words of a wider reading whose middle lies inside the row's time (the neighbours' words in the window are left out)."""
    inside = [word for word in words if start - margin <= (float(word['start']) + float(word['end'])) / 2 <= end + margin]
    return ''.join(str(word['word']) for word in inside).strip(), (min(float(word.get('probability', 0)) for word in inside) if inside else 0.)


def wording_support(current, readings, *, first_family='whisper-v3', similar=.85):
    """Candidate wordings for a row whose readers disagree, each with the model families that heard something close to it (similarity
    >= `similar`). The current text counts the family of the first transcription too (it was heard once already). Returns
    [(text, families)], the current text first."""
    from asr_windows import _family, _similarity
    usable = [(reading['name'], str(reading.get('text', '')).strip()) for reading in readings if str(reading.get('text', '')).strip()]
    def families(text):
        return {_family(name) for name, other in usable if _similarity(text, other) >= similar}
    result = [(current, families(current) | {first_family})]
    for _, text in usable:
        if all(_similarity(text, seen) < similar for seen, _ in result):
            result.append((text, families(text)))
    return result


def orthography_shift(current, candidate):
    """A reading that turns Danish/Norwegian spelling into Swedish (or back) is a misheard language, not a better reading: TRANSFERVINDUE
    (Danish, file language unconfirmed), row 11 "sikkert fremlæge. Et sted mellem liv og død." was replaced by the readers' Swedish
    "för att säkert framlega ett stöd mellan liv och död." (replay 2026-11-03, also with 0.8.360)."""
    import re
    def letters(text, pattern):
        return bool(re.search(pattern, str(text)))
    dano, swedish = '[æøÆØ]', '[äöÄÖ]'
    return ((letters(current, dano) and not letters(current, swedish) and letters(candidate, swedish))
            or (letters(current, swedish) and not letters(current, dano) and letters(candidate, dano)))
