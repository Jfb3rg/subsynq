"""Sentence form for rows Whisper wrote without it, taken from a second reading of the same audio.

Whisper decodes every 30 seconds on its own here (no previous text). Without one it sometimes writes a whole
chunk in lower case without punctuation, so the rows are cut by pauses and length instead of by sentences and are
hard to translate. The same audio read with a few ordinary sentences as the previous text comes back as sentences,
but not always with the same words. So the second reading takes no part in choosing words or rows: every step that
compares readings works on the first reading alone, exactly as before. Only at the very end, the rows that still
stand without sentence form get the second reading's capitals and punctuation, where both hold the same word.
Every word, time and row stays as it was, and words outside the stretches without punctuation are not touched.

Sentence form is measured over the running text, not row by row: a sentence may continue over several rows, but
a real sentence seldom runs many words without a single comma, and a chunk that lost its form runs on through
pauses and speaker changes.
"""
import re
from difflib import SequenceMatcher

from collapsed_runs import MARKS, _token

# Only languages where the second reading has been measured against real windows.
FORM_PROMPTS = {'en': "Hello. How are you? I'm fine, thank you. Come in, please.",
                'de': 'Hallo. Wie geht es dir? Mir geht es gut, danke. Kommen Sie bitte herein.'}
FORM_STRETCH_WORDS = 16
MINIMUM_KEPT = .85
SENTENCE_ENDS = '.?!…'
# Whisper's times for a sentence and the finished row's times differ by a few tenths of a second.
READING_MARGIN = .5
# A period after one of these belongs to the title, not to a sentence (English, the only language with a prompt so far).
TITLES = {'mr', 'mrs', 'ms', 'dr', 'st', 'jr', 'sr', 'prof'}


def form_prompt(language):
    """The sentence-form text for a language, or None where it has not been measured."""
    return FORM_PROMPTS.get(str(language or '').lower())


def _marked(word):
    return any(mark in word for mark in MARKS)


def formless_words(words):
    """Indices of the words standing in stretches of at least FORM_STRETCH_WORDS words with no punctuation between them.
    The word that closes a stretch with a mark counts to its length (a sentence is as long as its words) but is not in it."""
    found, current = set(), []

    def close(length):
        if length >= FORM_STRETCH_WORDS:
            found.update(current)
        current.clear()

    for index, word in enumerate(words):
        if not _token(word):
            close(len(current))
        elif _marked(word):
            close(len(current) + 1)
        else:
            current.append(index)
    close(len(current))
    return found


def unmarked_words(words):
    """How many words stand in long stretches without punctuation."""
    return len(formless_words(words))


def row_words(rows):
    """The written words of rows in time order."""
    return [word for row in sorted(rows, key=lambda row: row['start']) for word in str(row.get('text', '')).split()]


def row_openers(rows):
    """The indices in row_words(rows) of the words that open a row."""
    openers, count = set(), 0
    for row in sorted(rows, key=lambda row: row['start']):
        words = str(row.get('text', '')).split()
        if words:
            openers.add(count)
        count += len(words)
    return openers


def lost_form(words):
    """True when the running text of one window holds a long stretch without any punctuation."""
    return bool(formless_words(words))


def _closing(word):
    stripped = word.rstrip()
    return stripped[len(stripped.rstrip(MARKS)):]


def _ends_sentence(word):
    return any(mark in SENTENCE_ENDS for mark in _closing(word))


def _with_closing(word, marks):
    stripped = word.rstrip()
    return stripped.rstrip(MARKS) + marks + word[len(stripped):]


def _capitalised(word):
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.upper() + word[index + 1:]
    return word


def _opens_sentence_only(ours, theirs):
    """True where the second reading's capital on a word the first reading wrote in lower case may come from the sentence
    it starts there: not "I", and not a word in capitals throughout."""
    letters = [char for char in ours if char.isalpha()], [char for char in theirs if char.isalpha()]
    if not letters[0] or not letters[1] or not letters[0][0].islower() or not letters[1][0].isupper():
        return False
    return letters[1] != ['I'] and not theirs.lstrip(MARKS + '"').startswith(("I'", 'I’')) and not (
        len(letters[1]) > 1 and all(char.isupper() for char in letters[1]))


def _first_letter_lowered(word):
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.lower() + word[index + 1:]
    return word


def transfer_form(first, second, targets=None, before=None, openers=()):
    """(words, reason): the first reading's words with the second reading's capitals and punctuation on its words in
    stretches without punctuation (`targets`: their indices, where the caller measured them over more text than
    `first`), or (None, reason) where the second reading is not the same speech or brings no punctuation.

    A word both readings hold takes the second reading's spelling of it (same letters, so only capitals, apostrophes
    and marks differ). Where the readings differ the first reading's words stay, and a mark that closes the differing
    part in the second reading closes it in the first too, so a sentence still ends where the second reading ended it,
    and the word after such an end opens in upper case. A capital the second reading wrote because its own sentence
    started there, or its own row (`openers`: the indices in `second` of the words that open a row), does not stand
    where the first reading's word before it does not end a sentence (a comma the first reading keeps, or `before`:
    the word standing before `first`).
    What the second reading holds before the first word and after the last is other speech and gives nothing: that
    holds for words the first reading opens or closes with that the second reading wrote differently, too."""
    first, second = [str(word) for word in first], [str(word) for word in second]
    targets = formless_words(first) if targets is None else set(targets)
    if not targets:
        return None, 'den första läsningen har meningsform'
    old = [(index, _token(word)) for index, word in enumerate(first) if _token(word)]
    new = [(index, _token(word)) for index, word in enumerate(second) if _token(word)]
    if not new:
        return None, 'den nya läsningen är tom'
    matcher = SequenceMatcher(None, [token for _, token in old], [token for _, token in new], autojunk=False)
    if sum(block.size for block in matcher.get_matching_blocks()) < MINIMUM_KEPT * len(old):
        return None, 'den nya läsningen har för få av den förstas ord'
    result, settled, opened = list(first), set(), set()
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        ours = [index for index, _ in old[old_start:old_end]]
        theirs = [second[index].strip() for index, _ in new[new_start:new_end]]
        if tag == 'equal':
            for place, (index, word) in enumerate(zip(ours, theirs)):
                if index in targets:
                    lead = first[index][:len(first[index]) - len(first[index].lstrip())]
                    result[index] = lead + word
                    ahead = second[new[new_start + place - 1][0]] if new_start + place else None
                    if _opens_sentence_only(first[index], word) and (
                            ahead is None or new[new_start + place][0] in openers
                            or _ends_sentence(ahead) and _token(ahead) not in TITLES):
                        opened.add(index)
                # The second reading wrote this word right after the one before it: its own choice of capital stands ("p.m. in").
                if place:
                    settled.add(index)
            continue
        if old_end == len(old) and new_end == len(new) or not old_start and not new_start:
            continue
        theirs = [word for word in theirs if _token(word) not in TITLES]
        if ours and theirs:
            target, marks = ours[-1], _closing(theirs[-1])
        else:
            # Words only the second reading holds: the sentence it ended there ends at the first reading's word before them.
            target = old[old_start - 1][0] if old_start and not ours else None
            marks = next((_closing(word) for word in theirs if _ends_sentence(word)), '')
        if target in targets and marks and not _ends_sentence(result[target]):
            if _ends_sentence(marks) or not _closing(result[target]):
                result[target] = _with_closing(result[target], marks)
    previous = None
    for index, _ in old:
        standing = before if previous is None else result[previous]
        if previous is not None and index in targets and index not in settled and _ends_sentence(result[previous]):
            result[index] = _capitalised(result[index])
        elif index in opened and standing is not None and not _ends_sentence(str(standing)):
            result[index] = _first_letter_lowered(result[index])
        previous = index
    if not any(_marked(result[index]) for index in targets):
        return None, 'den nya läsningen har inte mer meningsform'
    return result, 'meningsformen är hämtad från den nya läsningen'


def restore_rows_form(rows, readings):
    """(rows, records): the finished rows with sentence form in their long stretches without punctuation.

    `readings` are the second readings of the main-pass windows: {'window': {'start', 'end'}, 'rows': [{'start',
    'end', 'text'}]}. The rows of a stretch that lie inside a window are compared with what that window's second
    reading holds at their time. A stretch the first window's reading does not settle is tried with the next one
    (windows overlap, and both main passes read the same audio). Only `text` changes, and only its capitals and marks."""
    texts = [str(row.get('text', '')).split() for row in rows]
    offsets = [0]
    for words in texts:
        offsets.append(offsets[-1] + len(words))
    flat = [word for words in texts for word in words]
    targets = formless_words(flat)
    waiting = {index for index in range(len(rows)) if any(position in targets for position in range(offsets[index], offsets[index + 1]))}
    records = []
    for reading in readings:
        window = reading['window']
        inside = sorted(index for index in waiting if window['start'] <= rows[index]['start'] and rows[index]['end'] <= window['end'])
        runs = []
        for index in inside:
            if runs and runs[-1][-1] == index - 1:
                runs[-1].append(index)
            else:
                runs.append([index])
        for run in runs:
            begin, end = offsets[run[0]], offsets[run[-1] + 1]
            start, stop = rows[run[0]]['start'] - READING_MARGIN, rows[run[-1]]['end'] + READING_MARGIN
            held = [row for row in reading['rows'] if row['end'] > start and row['start'] < stop]
            inside_run = {position - begin for position in targets if begin <= position < end}
            formed, reason = transfer_form(flat[begin:end], row_words(held), inside_run, flat[begin - 1] if begin else None,
                                           row_openers(held))
            if formed is not None and 0 in inside_run and (not begin or _ends_sentence(flat[begin - 1])):
                formed[0] = _capitalised(formed[0])
            record = {'profile': reading.get('profile'), 'window': window, 'rows': [run[0], run[-1]],
                      'start': rows[run[0]]['start'], 'end': rows[run[-1]]['end'], 'taken': formed is not None, 'reason': reason,
                      'before': ' '.join(flat[begin:end])}
            if formed is not None:
                flat[begin:end] = formed
                waiting.difference_update(run)
                record['after'] = ' '.join(formed)
            records.append(record)
    restored = []
    for index, row in enumerate(rows):
        words = flat[offsets[index]:offsets[index + 1]]
        if words == texts[index]:
            restored.append(row)
            continue
        # The row's own spacing stays: each written word is put where the old one stood.
        pieces = iter(words)
        restored.append({**row, 'text': re.sub(r'\S+', lambda _: next(pieces), str(row['text']))})
    return restored, records
