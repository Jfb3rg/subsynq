"""Words that stand twice across a row boundary although every reading heard them once.

The finished rows are put together from several readings. Where one row keeps a reading that cut the sentence after
a word and the next row comes from a reading that starts with that word, the word stands in both rows ("... and
then we will" + "will walk down to the harbour"). A translator reads that as a repetition.

Such a run of words is removed from one of the two rows only where the readings settle it: at least two readings
hold the words once, with the words before and after them, and no reading holds them twice in a row at that place.
A real repetition ("No," + "No!", "Where are they?" + "Where are they?") is heard twice by some reading, or one of
the rows is the repeated words alone, and stays. The words stay in the row whose sentence they belong to: in the
first row where the readings end a sentence after them (or only that row is written as a sentence), otherwise in
the second. No time is changed.
"""
from collapsed_runs import _words

MAXIMUM_GAP = .3
MAXIMUM_WORDS = 10
CONTEXT_WORDS = 2
REACH = 4.0
MINIMUM_READINGS = 2
SENTENCE_ENDS = '.?!…'
MARKS = SENTENCE_ENDS + ',;:'


def _shared_edge(left, right):
    """How many words that close `left` also open `right`."""
    for count in range(min(len(left), len(right)), 0, -1):
        if left[-count:] == right[:count]:
            return count
    return 0


def _find(tokens, wanted):
    size = len(wanted)
    return next((index for index in range(len(tokens) - size + 1) if tokens[index:index + size] == wanted), None)


def _chains(rows):
    """One reading's words as it wrote them row after row. Rows that overlap in time are two readings of the same
    moment (windows read with a margin both hear the word at their shared edge) and are never joined."""
    chains, end = [], None
    for row in sorted(rows or (), key=lambda item: item['start']):
        if end is None or row['start'] < end - .05:
            chains.append([])
        chains[-1] += _words(row.get('text', ''))
        end = row['end']
    return [chain for chain in chains if chain]


def _last_mark(word):
    """The mark that closes the word, or '' (quotes and brackets after it do not count)."""
    last = word.rstrip('"\'”’)')[-1:]
    return last if last and last in MARKS else ''


def _ends_sentence(word):
    return bool(_last_mark(word)) and _last_mark(word) in SENTENCE_ENDS


def _settle(left, right, count, readings):
    """None, or how the readings that hold the run once write the word before it and its last word."""
    before, run, after = left[:-count][-CONTEXT_WORDS:], right[:count], right[count:][:CONTEXT_WORDS]
    written = []
    for rows in (readings or {}).values():
        found = None
        for heard in _chains(rows):
            tokens = [token for _, token in heard]
            if _find(tokens, run + run) is not None:
                return None
            place = _find(tokens, before + run + after)
            if place is not None and found is None:
                found = (heard[place + len(before) - 1][0], heard[place + len(before) + count - 1][0])
        if found:
            written.append(found)
    return written if len(written) >= MINIMUM_READINGS else None


def drop_doubled_edge_words(merged, readings, *, skip_indices=()):
    """Return rows and what changed. `readings` is name -> rows of every reading of the job."""
    rows = [dict(row) for row in merged]
    changes = []
    for index in range(len(rows) - 1):
        first, second = rows[index], rows[index + 1]
        if (index in skip_indices or index + 1 in skip_indices or second['start'] - first['end'] > MAXIMUM_GAP
                or second.get('confirmed_prefix') or second.get('confirmed_lead_onset')):
            continue
        left, right = _words(first['text']), _words(second['text'])
        count = _shared_edge([token for _, token in left], [token for _, token in right])
        # A row that is the repeated words alone is somebody saying them again, or nothing the readings can place.
        if not 0 < count <= MAXIMUM_WORDS or count >= len(left) or count >= len(right):
            continue
        boundary = (first['end'] + second['start']) / 2
        near = {name: [row for row in items or () if row['end'] > boundary - REACH and row['start'] < boundary + REACH]
                for name, items in (readings or {}).items()}
        written = _settle([token for _, token in left], [token for _, token in right], count, near)
        if written is None:
            continue
        old = [first['text'], second['text']]
        # Where the readings end a sentence tells which row the words belong to; readings without sentence form say nothing.
        after = [_last_mark(last) for _, last in written if _ends_sentence(last)]
        before = [_last_mark(previous) for previous, _ in written if _ends_sentence(previous)]
        closes_first = (len(after) > len(before) or len(after) == len(before) and _ends_sentence(left[-1][0])
                        and right[0][0][:1].islower())
        if closes_first:
            kept = [word for word, _ in right[count:]]
            kept[0] = kept[0][:1].upper() + kept[0][1:]
            second['text'] = ' '.join(kept)
            if after and not any(mark in left[-1][0] for mark in MARKS):
                first['text'] = first['text'].rstrip() + max(set(after), key=after.count)
        else:
            kept = [word for word, _ in left[:-count]]
            marks = [_last_mark(previous) for previous, _ in written if _last_mark(previous)]
            # The first row takes the mark the readings put where it now ends ("... down by the river." + "Listen, ...").
            if marks and not any(mark in kept[-1] for mark in MARKS):
                kept[-1] += max(set(marks), key=marks.count)
            first['text'] = ' '.join(kept)
        changes.append({'start': first['start'], 'end': second['end'], 'words': ' '.join(word for word, _ in right[:count]),
                        'removed_from': 'second' if closes_first else 'first', 'readings': len(written),
                        'old': old, 'new': [first['text'], second['text']]})
    return rows, changes
