"""A last word that no reading heard.

The audio behind a row is read again in a tight clip that ends where the row ends. A last word that starts right before that end is cut off
in the clip, so every reading stops one word short ("... that is worth a lot" where the row says "... that is worth a fortune") and no reader
can contradict the word the first pass wrote there. The row keeps a word that nothing else supports.

For such a row the last seconds are read once more with sound after the row's end. The word is changed only where the readings settle it:
at least two readers hear the same other word after the same word before, and at most one reader hears the word the row has.
"""
import re

from collapsed_runs import MARKS, _token

MINIMUM_FAMILIES = 2
TAIL_BEFORE = 2.0       # seconds of the row's end that are read again
TAIL_AFTER = .8         # seconds of sound after the row's end, so that a word cut off by the tight clip is heard
KEEP_AFTER = .3         # a word whose middle lies up to this far after the row's end still belongs to the row


def _tokens(text):
    return [token for token in (_token(word) for word in str(text).split()) if token]


def unheard_last_word(current, readings):
    """(word before, last word) when the row has a last word that none of the readings heard: at least two readings, and every one of
    them ends on the word before it. None otherwise."""
    words = _tokens(current)
    if len(words) < 3 or words[-1] == words[-2]:
        return None
    heard = [_tokens(text) for text in readings]
    heard = [tokens for tokens in heard if tokens]
    if len(heard) < MINIMUM_FAMILIES:
        return None
    if all(tokens[-1] == words[-2] and words[-1] not in tokens[-2:] for tokens in heard):
        return words[-2], words[-1]
    return None


def _word_after(text, before):
    """The word that follows the last `before` in `text` as the reader wrote it, or None."""
    words = str(text).split()
    for index in range(len(words) - 2, -1, -1):
        if _token(words[index]) == before:
            following = words[index + 1].strip(MARKS + '"\'')
            return following if _token(following) else None
    return None


def settle_tail_word(current, before, last, tails):
    """(new text, detail) when the tail readings settle another last word, else (None, detail). `tails` maps a model family to what it
    heard up to the row's end plus `KEEP_AFTER` seconds."""
    heard = {family: _word_after(text, before) for family, text in tails.items() if text}
    votes = {}
    for family, word in heard.items():
        if word:
            votes.setdefault(_token(word), []).append(family)
    detail = {'word_before': before, 'was': last, 'heard': {family: word for family, word in heard.items()}}
    if not votes:
        return None, detail
    token, families = max(votes.items(), key=lambda item: len(item[1]))
    support_for_old = len(votes.get(last, []))
    if token == last or len(families) < MINIMUM_FAMILIES or support_for_old > 1:
        return None, detail
    word = next(heard[family] for family in families)
    words = str(current).rstrip().split(' ')
    closing = words[-1][len(words[-1].rstrip(MARKS + '"\'')):]
    opening = words[-1][:len(words[-1]) - len(words[-1].lstrip(MARKS + '"\''))]
    words[-1] = opening + word + closing
    detail.update(now=token, families=sorted(families))
    return ' '.join(words), detail
