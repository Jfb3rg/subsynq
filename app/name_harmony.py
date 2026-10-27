"""Names that one reading spells several ways, made consistent with an independent second reading.

KB-Whisper spelled the invented name in Nelly Rapp three ways (Häckla, Hekla, Ekla) and the quality check, rightly, refused the subtitle. Whisper large-v3 spells it
the same way every time. This module finds such conflicts in a transcript and, when a second reading of the same rows repeats a close spelling in at least two rows,
makes the whole transcript use the spelling (one of the transcript's own) that the second reading is closest to. It never invents a spelling, only capitalised words that
stand in the middle of a sentence count as names (so "Stjärten." or "Med ..." at a sentence start are left alone) and a row keeps its spelling when the second reading of THAT
row holds it. Pure functions; the worker supplies the second reading.
"""
import re
from collections import Counter, defaultdict

TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
MIN_LENGTH = 3
MIN_WITNESS_ROWS = 2                       # the second reading must repeat a close spelling in at least this many rows
ENDINGS = ('arna', 'erna', 'orna', 'ens', 'ets', 'ns', 'na', 'en', 'et', 'ar', 'er', 'or', 'n', 's', 'r', 't')   # Swedish inflection endings
SENTENCE_END = '.!?…:;'


def stem(word):
    """The word without one Swedish ending (Häcklan -> Häckla), so an inflection is not mistaken for another spelling."""
    for ending in ENDINGS:
        if word.casefold().endswith(ending) and len(word) - len(ending) >= MIN_LENGTH:
            return word[:-len(ending)]
    return word


def distance(first, second):
    previous = list(range(len(second) + 1))
    for number, a in enumerate(first, 1):
        row = [number]
        for index, b in enumerate(second, 1):
            row.append(min(previous[index] + 1, row[-1] + 1, previous[index - 1] + (a != b)))
        previous = row
    return previous[-1]


def near(first, second):
    """Close enough to be one name spelled two ways: one edit for short stems, two for longer ones; an inflection of the same stem is not another spelling."""
    a, b = stem(first).casefold(), stem(second).casefold()
    return a != b and distance(a, b) <= (1 if min(len(a), len(b)) <= 4 else 2)


def close(first, second):
    """Within two edits of each other as written (the second reading may inflect a name differently)."""
    a, b = first.casefold(), second.casefold()
    return distance(a, b) <= (1 if min(len(a), len(b)) <= 4 else 2)


def capitalised_words(rows, key='source'):
    """(strong, weak, lower): capitalised words that stand mid-sentence, capitalised words that only start a sentence, and every other word (casefolded)."""
    strong, weak, lower = defaultdict(list), defaultdict(list), set()
    for index, row in enumerate(rows):
        text = row.get(key, '')
        for match in TOKEN.finditer(text):
            word = match.group()
            if not word[0].isupper() or word.isupper() or len(word) < MIN_LENGTH:
                lower.add(word.casefold())
                continue
            before = text[:match.start()].rstrip()
            (weak if not before or before[-1] in SENTENCE_END else strong)[word].append(index)
    return strong, weak, lower


def conflicts(rows, key='source'):
    """Groups of names that are near each other but not the same name: [{'variants': {word: [row index, ...]}}], one group per name.

    A group starts from capitalised words in the middle of a sentence; a sentence-initial capitalised word joins only when it is near one of them."""
    strong, weak, lower = capitalised_words(rows, key)
    strong = {word: places + weak.get(word, []) for word, places in strong.items() if word.casefold() not in lower}
    weak = {word: places for word, places in weak.items() if word.casefold() not in lower and word not in strong}
    parent = {word: word for word in strong}

    def find(word):
        while parent[word] != word:
            parent[word] = parent[parent[word]]
            word = parent[word]
        return word
    ordered = sorted(strong)
    for number, first in enumerate(ordered):
        for second in ordered[number + 1:]:
            if near(first, second):
                parent[find(first)] = find(second)
    groups = defaultdict(dict)
    for word in ordered:
        groups[find(word)][word] = list(strong[word])
    result = []
    for group in groups.values():
        for word, places in weak.items():
            if any(near(word, member) for member in list(group)):
                group[word] = list(places)
        if len({stem(word).casefold() for word in group}) >= 2:
            result.append({'variants': group})
    return result


def rows_to_read(clusters):
    """Row indexes a second reading is needed for."""
    return sorted({index for cluster in clusters for places in cluster['variants'].values() for index in places})


def harmonize(rows, clusters, readings, key='source'):
    """(new rows, changes). `readings` maps a row index to the second reading's text of that row."""
    rows = [dict(row) for row in rows]
    changes = []
    for cluster in clusters:
        variants = cluster['variants']
        evidence = defaultdict(list)                                     # row -> the second reading's words close to a variant
        for index in rows_to_read([cluster]):
            for match in TOKEN.finditer(readings.get(index, '')):
                word = match.group()
                if len(word) >= MIN_LENGTH and any(close(word, variant) or close(stem(word), stem(variant)) for variant in variants):
                    evidence[index].append(word)
        if len(evidence) < MIN_WITNESS_ROWS:
            continue
        words = [word for found in evidence.values() for word in found]

        def cost(variant):
            return sum(min(distance(variant.casefold(), word.casefold()), distance(stem(variant).casefold(), stem(word).casefold())) for word in words)
        canonical = sorted(variants, key=lambda variant: (cost(variant), -len(variants[variant]), min(variants[variant])))[0]
        if cost(canonical) > len(words):                                  # the second reading is not clearly closer to any of them
            continue
        for word, places in variants.items():
            if stem(word).casefold() == stem(canonical).casefold():
                continue
            replacement = stem(canonical) + word[len(stem(word)):]
            for index in places:
                # A row follows the agreed spelling unless its own second reading holds the OLD spelling (then the two readings really disagree there).
                if any((close(found, word) or close(stem(found), stem(word))) and not (close(found, canonical) or close(stem(found), stem(canonical)))
                       for found in evidence.get(index, [])):
                    continue
                text = rows[index][key]
                updated = re.sub(rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])", replacement, text)
                if updated != text:
                    changes.append({'row': rows[index].get('id', index), 'from': word, 'to': replacement})
                    rows[index][key] = updated
    return rows, changes
