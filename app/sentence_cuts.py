"""Move a row boundary that falls inside a sentence to a sentence end in the same two rows, timed by Whisper's own words.

Example Series 4x02 (regression case): about 8 % of the rows begin in the middle of a sentence ("Of here? How could I possibly" / "expect you to
understand? Can you back me up here?"), because both Whisper passes cut at pauses. Only the boundary moves: the number of rows, every word and its
order stay; the new boundary is the end of the last word and the start of the next one in a Whisper pass of this job (profile_evidence)."""
import re

_END = re.compile(r'[.!?…]["\'”’»)\]]*$')
MAXIMUM_MOVE = 6          # words
MAXIMUM_ROW_CHARACTERS = 84
MAXIMUM_JOIN_GAP = .5


def _word(text):
    return re.sub(r"[^\w']", '', str(text).casefold().replace('’', "'"))


def _first_letter(text):
    return next((char for char in str(text) if char.isalpha()), '')


def evidence_words(profile_evidence):
    """The timed words of every Whisper pass of the job, one ordered sequence per pass and window: [[(start, end, normalized word)]]."""
    passes = {}
    for entry in profile_evidence or []:
        window = entry.get('window') or {}
        key = (entry.get('profile'), window.get('start'), window.get('end'))
        for w in entry.get('words') or []:
            if w.get('start') is not None and w.get('end') is not None and _word(w.get('text', '')):
                passes.setdefault(key, []).append((round(float(w['start']), 3), round(float(w['end']), 3), _word(w['text'])))
    return [sorted(set(sequence)) for sequence in passes.values()]


def move_cut(left, right, words):
    """(new left, new right, proof) or None for two rows right after each other whose boundary is inside a sentence."""
    first, second = str(left['source']).split(), str(right['source']).split()
    if (not first or not second or _END.search(first[-1]) or not _first_letter(right['source']).islower()
            or not 0 <= float(right['start']) - float(left['end']) <= MAXIMUM_JOIN_GAP):
        return None
    joined, cut = first + second, len(first)
    ends = [k + 1 for k, w in enumerate(joined[:-1]) if _END.search(w) and abs(k + 1 - cut) <= MAXIMUM_MOVE]
    if not ends:
        return None
    k = min(ends, key=lambda k: (abs(k - cut), k))
    new_left, new_right = ' '.join(joined[:k]), ' '.join(joined[k:])
    if len(new_left) > MAXIMUM_ROW_CHARACTERS or len(new_right) > MAXIMUM_ROW_CHARACTERS:
        return None
    timed = _timed_boundary(left, right, words, joined, k, cut)
    if timed is None:
        return None
    end, start = timed
    new_right = new_right[0].upper() + new_right[1:]
    return (dict(left, source=new_left, end=end), dict(right, source=new_right, start=start),
            dict(moved_words=k - cut, boundary=(end, start), previous=(float(left['end']), float(right['start']))))


def move_cuts_to_sentence_ends(rows, words):
    """rows with every boundary that move_cut can move moved, left to right; returns (rows, proofs). `words` is evidence_words(...) of the job.
    A row whose source a check confirmed word by word (confirmed_source_words, confirmed_prefix, …) or that carries other readings of its own
    text (alternatives) is left alone."""
    result, proofs = [dict(row) for row in rows], []
    protected = ('confirmed_source_words', 'confirmed_prefix', 'confirmed_language', 'alternatives')
    for index in range(len(result) - 1):
        left, right = result[index], result[index + 1]
        if any(left.get(key) or right.get(key) for key in protected):
            continue
        moved = move_cut(left, right, words) or move_cut_to_phrase(left, right, words)
        # A confirmed onset (restore_lead_words) is the start of that row: only the boundary that would move it is left alone; the end of such
        # a row may still take the rest of its sentence.
        if moved and (right.get('confirmed_lead_onset') or (left.get('confirmed_lead_onset') and moved[2]['moved_words'] < 0)):
            moved = None
        if moved:
            result[index], result[index + 1], proof = moved
            result[index].pop('reading_time_source_end', None)
            proofs.append(dict(proof, ids=[left.get('id'), right.get('id')], before=[left['source'], right['source']],
                               after=[result[index]['source'], result[index + 1]['source']]))
    return result, proofs


# A row should not end on a word that belongs to the next one; a long sentence split over two rows is cut where a phrase ends instead.
_HOLDS_NEXT = {'the', 'a', 'an', 'of', 'to', 'and', 'but', 'or', 'in', 'on', 'at', 'for', 'with', 'from', 'by', 'my', 'your', 'his', 'her',
               'our', 'their', 'its', 'this', 'that', 'these', 'those', 'is', 'are', 'was', 'were', 'be', 'i', 'you', 'we', 'they', 'he', 'she',
               'if', 'because', 'than', 'as', 'so', 'not', 'no', 'very', 'just', 'gonna', 'wanna', "i'm", "you're", "we're", "it's", "don't"}
_STARTS_PHRASE = {'and', 'but', 'or', 'because', 'so', 'if', 'when', 'while', 'which', 'who', 'where', 'before', 'after', 'until', 'unless',
                  'although', 'though', 'whether'}
MAXIMUM_PHRASE_MOVE = 4   # words


def _break_quality(joined, k):
    """How well a row break before joined[k] reads (higher is better): after a comma, before a conjunction or preposition; never after a word that
    belongs to the next one."""
    last, following = joined[k - 1], _word(joined[k])
    if re.search(r'[,;:–—]$', last):
        return 3
    if following in _STARTS_PHRASE and _word(last) not in _STARTS_PHRASE:
        return 2
    if _word(last) in _HOLDS_NEXT:
        return 0
    return 1


def move_cut_to_phrase(left, right, words):
    """(new left, new right, proof) or None: a boundary inside a sentence that ends on a word belonging to the next one ("… the light and the" /
    "dark and all …") moves to the nearest break that reads clearly better, at most MAXIMUM_PHRASE_MOVE words away and timed like move_cut."""
    first, second = str(left['source']).split(), str(right['source']).split()
    if (not first or not second or _END.search(first[-1]) or not _first_letter(right['source']).islower()
            or not 0 <= float(right['start']) - float(left['end']) <= MAXIMUM_JOIN_GAP):
        return None
    joined, cut = first + second, len(first)
    current = _break_quality(joined, cut)
    if current > 0:
        return None
    options = [k for k in range(max(1, cut - MAXIMUM_PHRASE_MOVE), min(len(joined) - 1, cut + MAXIMUM_PHRASE_MOVE) + 1)
               if k != cut and _break_quality(joined, k) >= 2 and not any(_END.search(w) for w in joined[min(k, cut):max(k, cut)])]
    if not options:
        return None
    options = [k for k in options if k >= 2 and len(joined) - k >= 2]
    if not options:
        return None
    k = max(options, key=lambda k: (_break_quality(joined, k), -abs(k - cut), -k))
    if len(' '.join(joined[:k])) > MAXIMUM_ROW_CHARACTERS or len(' '.join(joined[k:])) > MAXIMUM_ROW_CHARACTERS:
        return None
    timed = _timed_boundary(left, right, words, joined, k, cut)
    if timed is None:
        return None
    end, start = timed
    return (dict(left, source=' '.join(joined[:k]), end=end), dict(right, source=' '.join(joined[k:]), start=start),
            dict(moved_words=k - cut, boundary=(end, start), previous=(float(left['end']), float(right['start'])), kind='phrase'))


def _timed_boundary(left, right, words, joined, k, cut):
    """(end of joined[k-1], start of joined[k]) from the job's Whisper words, or None. The two words are found together with their neighbours
    (up to two words on each side, in the same pass and window), so another "the" or "earth" nearby is never taken for them (Unraid jobs: 28
    boundaries where the passes seemed to disagree were such mix-ups). Every match must lie inside the two rows, move toward the words that
    move, and all matches must agree within .3 s."""
    found = []
    # Two neighbours on each side first; where no pass holds them (the passes often word the context differently), the two words right after
    # each other in one pass, which is still stricter than single words matched across passes.
    for reach in (2, 1):
        before = [_word(w) for w in joined[max(0, k - reach):k]]
        after = [_word(w) for w in joined[k:k + reach]]
        pattern = before + after
        for sequence in words:
            names = [w[2] for w in sequence]
            for i in range(len(names) - len(pattern) + 1):
                if names[i:i + len(pattern)] == pattern:
                    a, b = sequence[i + len(before) - 1], sequence[i + len(before)]
                    if (0 <= b[0] - a[1] <= 1.5 and float(left['start']) + .3 <= a[1] and b[0] <= float(right['end']) - .3
                            and ((k < cut and a[1] <= float(left['end']) + .05) or (k > cut and b[0] >= float(right['start']) - .05))):
                        found.append((a[1], b[0]))
        if found:
            break
    if not found:
        return None
    ends, starts = sorted(e for e, _ in found), sorted(s for _, s in found)
    if ends[-1] - ends[0] > .3 or starts[-1] - starts[0] > .3:
        return None
    end, start = ends[len(ends) // 2], starts[len(starts) // 2]
    return end, max(start, end)
