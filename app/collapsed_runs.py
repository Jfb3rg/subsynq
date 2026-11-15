"""Stretches the long pass wrote without sentence form, replaced by sentences that other model families confirm.

Long-context Whisper sometimes falls into a mode where it writes lower case without punctuation, cuts the rows in
the middle of sentences and loses words ("you the AI data farm again happy to take that meeting for you look I
know he's a" + "major donor but absolutely not ..."), while the short pass of the same job reads the same stretch
as sentences ("The AI data farm again." + "Happy to take that meeting for you." ...). Such rows are hard to
translate and are the ones the source audit keeps naming.

A stretch is replaced only by whole rows of a reading in sentence form, and only where the wording is settled:
a word both readings hold needs nothing more, a word only the sentence reading holds must be heard at that very
place by two other model families (at least one outside Whisper), and a word only the long pass holds may go only
where two such families read straight past it. What is not settled stays as the long pass wrote it: the words
before and after the replaced part are kept as rows of their own, so no word disappears on one reading's say-so.
"""
import re
from difflib import SequenceMatcher

MARKS = '.,?!;:…'
MAXIMUM_GAP = 2.0
MINIMUM_RUN_WORDS = 5
MINIMUM_SHARED_WORDS = 4
MINIMUM_SHARED = .8
MINIMUM_FAMILIES = 2
MAXIMUM_ROWS = 30
RUN_FAMILY = 'whisper-v3'
WHISPER_FAMILIES = {'context': 'whisper-v3', 'precision': 'whisper-v3', 'rescue': 'whisper-v3',
                    'whispered_gap': 'whisper-v3', 'cluster_first': 'whisper-v3', 'cluster_second': 'whisper-v3',
                    'v2': 'whisper-v2', 'cluster_v2': 'whisper-v2', 'cluster_qwen': 'qwen'}


def _token(word):
    return re.sub(r'[^\w]+', '', str(word).lower(), flags=re.UNICODE)


def _words(text):
    """Written words with one comparison token each ("he's" is one word, a lone dash is none)."""
    return [(word, _token(word)) for word in str(text).split() if _token(word)]


def formless(row):
    """A row in a cased script with no punctuation at all, long or opening in lower case."""
    text = str(row.get('text', '')).strip()
    return (bool(_words(text)) and any(char.islower() for char in text) and not any(mark in text for mark in MARKS)
            and (len(_words(text)) >= 5 or text[:1].islower()))


def collapsed_runs(rows):
    """Index ranges of consecutive formless rows, with the lower-case row that closes the stretch."""
    runs, index = [], 0
    while index < len(rows):
        if not formless(rows[index]):
            index += 1
            continue
        last = index
        while (last + 1 < len(rows) and last - index + 1 < MAXIMUM_ROWS and formless(rows[last + 1])
               and rows[last + 1]['start'] - rows[last]['end'] <= MAXIMUM_GAP):
            last += 1
        closing = rows[last + 1] if last + 1 < len(rows) else None
        if (closing and closing['start'] - rows[last]['end'] <= MAXIMUM_GAP
                and str(closing.get('text', '')).strip()[:1].islower()):
            last += 1
        # A long row without a comma is ordinary text; the collapse shows in a row that also opens in lower case.
        if (sum(len(_words(row['text'])) for row in rows[index:last + 1]) >= MINIMUM_RUN_WORDS
                and any(formless(row) and str(row['text']).strip()[:1].islower() for row in rows[index:last + 1])):
            runs.append((index, last))
        index = last + 1
    return runs


def rows_inside_windows(rows, windows, *, shift=0, margin=.3):
    """Rows of a windowed reading that do not touch a window edge, where Whisper cuts a sentence and adds a full stop."""
    return [row for row in rows or () if any(window['start'] + shift + margin <= row['start']
                                              and row['end'] <= window['end'] - margin for window in windows or ())]


def _pairs(left, right):
    return [(block.a + offset, block.b + offset)
            for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks()
            for offset in range(block.size)]


def _family_readings(tokens, start, end, witnesses, source_family):
    """Per other model family: its words at this place and where each sentence-reading word sits among them."""
    families = {}
    for name, rows in (witnesses or {}).items():
        family = WHISPER_FAMILIES.get(name, name)
        if family == source_family or family == RUN_FAMILY and source_family.startswith('whisper'):
            continue  # a reading cannot confirm itself, nor can the model that wrote the run confirm its own short pass
        heard = [token for row in sorted(rows or (), key=lambda item: item['start'])
                 if row['end'] > start - 2 and row['start'] < end + 2 for _, token in _words(row.get('text', ''))]
        if heard:
            families.setdefault(family, []).append((heard, dict(_pairs(tokens, heard))))
    return families


def _enough(families):
    return len(families) >= MINIMUM_FAMILIES and any(not family.startswith('whisper') for family in families)


def _heard_there(position, readings):
    """Families that read this word next to the neighbour they also read (not the same word somewhere else)."""
    return {family for family, items in readings.items() for _, placed in items
            if position in placed and (placed.get(position - 1) == placed[position] - 1
                                       or placed.get(position + 1) == placed[position] + 1)}


def _read_past(position, readings):
    """Families that read the words at position and position + 1 directly after each other."""
    return {family for family, items in readings.items() for _, placed in items
            if position in placed and placed.get(position + 1) == placed[position] + 1}


def _heard_together(sequence, readings):
    """Families whose reading holds these words directly after each other."""
    size = len(sequence)
    return {family for family, items in readings.items() for heard, _ in items
            if any(heard[index:index + size] == sequence for index in range(len(heard) - size + 1))}


def _settled_block(run_tokens, source_rows, witnesses, source_family):
    """The longest row block of the sentence reading whose every difference from the run is settled."""
    spans, tokens = [], []
    for row in source_rows:
        words = [token for _, token in _words(row.get('text', ''))]
        spans.append((len(tokens), len(tokens) + len(words)))
        tokens += words
    if not tokens:
        return None
    readings = _family_readings(tokens, source_rows[0]['start'], source_rows[-1]['end'], witnesses, source_family)
    by_source = {right: left for left, right in _pairs(run_tokens, tokens)}
    # A word both readings hold is settled; a word only the sentence reading holds needs two other families there.
    settled = [position in by_source or _enough(_heard_there(position, readings)) for position in range(len(tokens))]
    usable = [str(row.get('text', '')).rstrip()[-1:] in MARKS and not formless(row)
              and all(settled[begin:end]) and end > begin for row, (begin, end) in zip(source_rows, spans)]
    best = None
    for first in range(len(source_rows)):
        for last in range(len(source_rows) - 1, first - 1, -1):
            if not all(usable[first:last + 1]):
                continue
            begin, end = spans[first][0], spans[last][1]
            held = [position for position in range(begin, end) if position in by_source]
            if (not held or held[0] >= spans[first][1] or held[-1] < spans[last][0]
                    or len(held) < MINIMUM_SHARED_WORDS or len(held) < MINIMUM_SHARED * (end - begin)
                    or len(held) < MINIMUM_SHARED * (by_source[held[-1]] - by_source[held[0]] + 1)):
                continue
            run_first, run_last = by_source[held[0]], by_source[held[-1]]
            if not _run_words_settled(run_tokens, tokens, held, by_source, readings, begin, end):
                continue
            if best is None or len(held) > best[0]:
                best = (len(held), first, last, run_first, run_last)
            break  # the longest block from this first row
    return best


def _run_words_settled(run_tokens, tokens, held, by_source, readings, begin, end):
    """No word of the run goes, and no word stays beside a new one, unless the other families settle it."""
    for before, after in zip(held, held[1:]):
        left, right = by_source[before], by_source[after]
        if right - left == 1:
            continue
        # A run word the sentence reading leaves out: nobody else may hold it, and two families must read past it.
        if _heard_together(run_tokens[left:left + 2], readings) or _heard_together(run_tokens[right - 1:right + 1], readings):
            return False
        if after - before == 1 and not _enough(_read_past(before, readings)):
            return False
    run_first, run_last = by_source[held[0]], by_source[held[-1]]
    # Words kept in front of (after) a block that opens (closes) with a new word: the other families must read the two
    # after each other, otherwise the new word may be another hearing of the kept one ("You" against "I don't know").
    if held[0] > begin and run_first and not _enough(_heard_together([run_tokens[run_first - 1], tokens[begin]], readings)):
        return False
    if held[-1] < end - 1 and run_last < len(run_tokens) - 1 and not _enough(
            _heard_together([tokens[end - 1], run_tokens[run_last + 1]], readings)):
        return False
    return True


def _sentence_ends(text):
    return len(re.findall(r'[.?!…]+(?=\s|$)', text))


def _rest(row, words, *, start=None, end=None):
    kept = dict(row, text=' '.join(word for word, _ in words))
    if start is not None:
        kept['start'] = max(row['start'], start)
    if end is not None:
        kept['end'] = min(row['end'], end)
    return kept


def _replace_run(run, before, after, sources, witnesses):
    words = [(index, word, token) for index, row in enumerate(run) for word, token in _words(row['text'])]
    run_tokens = [token for _, _, token in words]
    start, end = run[0]['start'], run[-1]['end']
    for name, rows in sources:
        # A sentence that reaches into an untouched neighbour is not a candidate; its words stay as the run wrote them.
        candidates = [row for row in sorted(rows or (), key=lambda item: item['start'])
                      if row['end'] > start - 1.5 and row['start'] < end + 1.5
                      and (before is None or row['start'] >= before['end'] - .05)
                      and (after is None or row['end'] <= after['start'] + .05)]
        found = _settled_block(run_tokens, candidates, witnesses, WHISPER_FAMILIES.get(name, name)) if candidates else None
        if not found:
            continue
        _, first, last, run_first, run_last = found
        block = [dict(row) for row in candidates[first:last + 1]]
        lead, trail = words[:run_first], words[run_last + 1:]
        kept_before = [dict(row) for row in run[:lead[-1][0]]] if lead else []
        kept_after = [dict(row) for row in run[trail[0][0] + 1:]] if trail else []
        if lead:
            index = lead[-1][0]
            kept_before.append(_rest(run[index], [(w, t) for i, w, t in lead if i == index], end=block[0]['start']))
        if trail:
            index = trail[0][0]
            kept_after.insert(0, _rest(run[index], [(w, t) for i, w, t in trail if i == index], start=block[-1]['end']))
        result = kept_before + block + kept_after
        neighbours = ([before] if before else []) + result + ([after] if after else [])
        if (any(row['end'] - row['start'] < max(.2, .06 * len(_words(row['text']))) for row in kept_before + kept_after)
                or any(left['end'] > right['start'] + .05 for left, right in zip(neighbours, neighbours[1:]))
                or any(left['end'] > right['start'] for left, right in zip(block, block[1:]))):
            continue
        # Worth it only when the stretch gains a sentence end; the same rows with another capital are no gain.
        replaced = ' '.join(word for _, word, _ in words[run_first:run_last + 1])
        if _sentence_ends(' '.join(row['text'] for row in block)) <= _sentence_ends(replaced):
            continue
        return result, {'start': start, 'end': end, 'source': name, 'old': [row['text'] for row in run],
                        'new': [row['text'] for row in result], 'replaced_rows': len(block)}
    return None


def replace_collapsed_runs(merged, sources, witnesses):
    """Return rows and what changed. `sources` are (name, rows) in order of preference, `witnesses` name -> rows."""
    rows = [dict(row) for row in sorted(merged, key=lambda item: item['start'])]
    changes = []
    for _ in range(2):  # what stays of a run may be settled by the next source
        result, position, changed = [], 0, False
        for first, last in collapsed_runs(rows):
            result += rows[position:first]
            outcome = _replace_run(rows[first:last + 1], result[-1] if result else None,
                                   rows[last + 1] if last + 1 < len(rows) else None, sources, witnesses)
            if outcome:
                result += outcome[0]
                changes.append(outcome[1])
                changed = True
            else:
                result += rows[first:last + 1]
            position = last + 1
        rows = result + rows[position:]
        if not changed:
            break
    return rows, changes
