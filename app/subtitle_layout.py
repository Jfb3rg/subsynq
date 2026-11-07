"""Line layout of a written subtitle: at most two lines of at most 42 characters, as Swedish television and streaming services show them.

Example Series 4x03 (regression caseMain 0.8.366) was written one line per cue, 89 of 479 lines longer than 42 characters and the longest 107: on a 65-inch
television a line reached from edge to edge. Only line breaks and, for a cue that does not fit two lines, a split into consecutive cues are added;
no word is changed, added or dropped, and the review never sees the layout."""
import re

MAX_LINE = 42
MAX_LINES = 2
MINIMUM_PART_SECONDS = 1.0

# A line should not end on a word that belongs to the next one (an article, a preposition, a possessive, "att" before its verb).
_HOLDS_NEXT = {'en', 'ett', 'den', 'det', 'de', 'dem', 'i', 'på', 'av', 'till', 'från', 'med', 'om', 'för', 'under', 'över', 'vid', 'mot', 'hos', 'utan',
               'min', 'mitt', 'mina', 'din', 'ditt', 'dina', 'sin', 'sitt', 'sina', 'hans', 'hennes', 'vår', 'vårt', 'våra', 'er', 'ert', 'era', 'deras',
               'att', 'som', 'och', 'men', 'eller', 'så', 'när', 'om', 'än', 'the', 'a', 'an', 'of', 'to', 'in', 'on', 'at', 'for', 'and', 'but', 'or',
               'mr', 'mrs', 'ms', 'dr', 'herr', 'fru'}
# A new line reads well before a conjunction.
_STARTS_CLAUSE = {'och', 'men', 'eller', 'att', 'som', 'när', 'om', 'eftersom', 'medan', 'innan', 'fast', 'för', 'så', 'tills', 'där', 'vilket',
                  'and', 'but', 'or', 'that', 'which', 'when', 'because', 'while', 'before', 'so'}


def _penalty(words, cut):
    """How badly a break before words[cut] reads: lower is better."""
    left, right = ' '.join(words[:cut]), ' '.join(words[cut:])
    last, nxt = words[cut - 1], words[cut]
    score = abs(len(left) - len(right))
    bare = re.sub(r'[^\w]', '', last.casefold())
    if last.endswith(('.', '!', '?', '…')):
        score -= 30
    elif last.endswith((',', ';', ':', '–', '—')):
        score -= 18
    elif re.sub(r'[^\w]', '', nxt.casefold()) in _STARTS_CLAUSE:
        score -= 8
    if bare in _HOLDS_NEXT and not last.endswith((',', '.', '!', '?', '…', ';', ':')):
        score += 40
    if re.match(r'^\d', nxt) and re.search(r'\d$', bare):
        score += 40          # "3 048" is one number
    return score


def wrap_lines(text, max_line=MAX_LINE):
    """The text on one line, or on two lines at the break that reads best; None when it does not fit two lines of max_line."""
    text = ' '.join(str(text).split())
    if len(text) <= max_line:
        return text
    words = text.split(' ')
    cuts = [cut for cut in range(1, len(words)) if len(' '.join(words[:cut])) <= max_line and len(' '.join(words[cut:])) <= max_line]
    if not cuts:
        return None
    best = min(cuts, key=lambda cut: (_penalty(words, cut), cut))
    return ' '.join(words[:best]) + '\n' + ' '.join(words[best:])


def _split_points(words):
    """Word positions where a cue may be split, best first: after a sentence end, then a comma, then before a conjunction, then anywhere."""
    def rank(cut):
        last, nxt = words[cut - 1], re.sub(r'[^\w]', '', words[cut].casefold())
        if last.endswith(('.', '!', '?', '…')):
            return 0
        if last.endswith((',', ';', ':', '–', '—')):
            return 1
        if nxt in _STARTS_CLAUSE:
            return 2
        return 3
    return sorted(range(1, len(words)), key=lambda cut: (rank(cut), abs(len(' '.join(words[:cut])) - len(' '.join(words[cut:])))))


_SENTENCE_END = re.compile(r'[.!?…]["\'”’»)\]]*\s*$')
MAXIMUM_JOIN_GAP = .5
MAXIMUM_JOINED_SECONDS = 7.0


def join_fragments(cues, max_line=MAX_LINE):
    """[(start, end, text)] with a cue that ends inside a sentence joined to the next one when that continues it in lower case right after (at most
    MAXIMUM_JOIN_GAP later) and the two fit one cue of two lines (Example Series 4x02, regression case: "Den" / "större bilden som gör dig" / "precis den
    farligaste mannen på jorden, …" came as three cues). The words stay as they are; only the cue boundary between them goes."""
    result = []
    for start, end, text in cues:
        text = ' '.join(str(text).split())
        if result:
            previous_start, previous_end, previous = result[-1]
            first = next((char for char in text if char.isalpha()), '')
            joined = f'{previous} {text}'
            if (previous and first.islower() and not _SENTENCE_END.search(previous) and 0 <= float(start) - float(previous_end) <= MAXIMUM_JOIN_GAP
                    and float(end) - float(previous_start) <= MAXIMUM_JOINED_SECONDS and wrap_lines(joined, max_line) is not None):
                result[-1] = (previous_start, end, joined)
                continue
        result.append((start, end, text))
    return result


def layout_cues(cues, max_line=MAX_LINE):
    """All cues of a file laid out: fragments of one sentence joined (join_fragments), then each cue on at most two lines (layout_cue)."""
    return [part for start, end, text in join_fragments(cues, max_line) for part in layout_cue(start, end, text, max_line)]


def layout_cue(start, end, text, max_line=MAX_LINE):
    """[(start, end, text with at most two lines)] for one cue. A cue that does not fit two lines is split at the point that reads best into
    consecutive cues that each fit, the time divided by length; a cue too short to give each part a second keeps its long lines."""
    text = ' '.join(str(text).split())
    wrapped = wrap_lines(text, max_line)
    if wrapped is not None or not text:
        return [(start, end, wrapped if wrapped is not None else text)]
    words = text.split(' ')
    for cut in _split_points(words):
        first, second = ' '.join(words[:cut]), ' '.join(words[cut:])
        duration = float(end) - float(start)
        middle = round(float(start) + duration * len(first) / (len(first) + len(second)), 3)
        if middle - float(start) < MINIMUM_PART_SECONDS or float(end) - middle < MINIMUM_PART_SECONDS:
            continue
        head = layout_cue(start, middle, first, max_line)
        tail = layout_cue(middle, end, second, max_line)
        if all(len(line) <= max_line for _, _, part in head + tail for line in part.split('\n')):
            return head + tail
    # Too short to split: the best two lines, even if longer than max_line.
    cut = min(range(1, len(words)), key=lambda c: (abs(len(' '.join(words[:c])) - len(' '.join(words[c:]))), c)) if len(words) > 1 else None
    return [(start, end, text if cut is None else ' '.join(words[:cut]) + '\n' + ' '.join(words[cut:]))]


_TIMING = re.compile(r'^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*$')


def _seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, '0')) / 1000


def _timestamp(seconds):
    millis = round(max(0.0, float(seconds)) * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{millis:03}'


def parse_srt(text):
    """[(start, end, text)] of a plain SRT file, the cue's lines joined with spaces; None when any block is not a numbered, timed cue."""
    cues = []
    for block in re.split(r'\n\s*\n', str(text).replace('\r\n', '\n').strip()):
        lines = block.split('\n')
        timing = _TIMING.match(lines[1]) if len(lines) >= 3 and lines[0].strip().isdigit() else None
        if not timing:
            return None
        values = timing.groups()
        cues.append((_seconds(*values[:4]), _seconds(*values[4:]), ' '.join(line.strip() for line in lines[2:] if line.strip())))
    return cues


def format_srt(cues):
    return '\n'.join(f'{number}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}\n' for number, (start, end, text) in enumerate(cues, 1))


def relayout_srt(text, max_line=MAX_LINE):
    """The file laid out like a newly written subtitle (layout_cues), or None when it cannot be read as SRT or already has no line longer
    than max_line. Every word stays, in order; only line breaks, the joining of a sentence's fragments and the splitting of a cue change."""
    cues = parse_srt(text)
    if not cues or all(len(line) <= max_line for line in str(text).replace('\r\n', '\n').split('\n')):
        return None
    laid_out = layout_cues(cues, max_line)
    if ' '.join(t for _, _, t in laid_out).split() != ' '.join(t for _, _, t in cues).split():
        return None
    return format_srt(laid_out)
