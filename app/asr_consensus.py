"""Conservative labels for two independent ASR passes.

The comparison is deliberately not an automatic approval. Any changed word stays
visible for listening, even when the strings are otherwise very similar.
"""
from difflib import SequenceMatcher
import re


def normalized(text):
    return re.sub(r'[^\w]+', ' ', str(text).casefold(), flags=re.UNICODE).strip()


def compare_transcripts(primary, secondary):
    first, second = normalized(primary), normalized(secondary)
    if not second:
        return {'status': 'missing', 'label': 'Saknas i andra körningen', 'similarity': 0.0}
    similarity = SequenceMatcher(None, first, second).ratio()
    if first == second:
        return {'status': 'agreement', 'label': 'Körningarna är överens', 'similarity': 1.0}
    if first in second or second in first:
        status = 'segmentation'
        label = 'Samma innehåll, olika uppdelning'
    elif similarity >= .86:
        status = 'word_difference'
        label = 'Liten ordskillnad – lyssna'
    else:
        status = 'meaningful_difference'
        label = 'Tydlig skillnad – lyssna'
    return {'status': status, 'label': label, 'similarity': round(similarity, 3)}


def is_more_complete_alternative(primary, alternative, minimum_extra_words=2):
    """Detect when a second pass contains the whole primary plus useful context."""
    first = normalized(primary).split()
    second = normalized(alternative).split()
    if not first or len(second) < len(first) + minimum_extra_words:
        return False
    width = len(first)
    return any(second[index:index + width] == first
               for index in range(len(second) - width + 1))


def summarize(rows):
    counts = {
        'agreement': 0, 'segmentation': 0, 'word_difference': 0,
        'meaningful_difference': 0, 'missing': 0, 'recovered': 0,
    }
    for row in rows:
        status = row.get('comparison', {}).get('status', 'missing')
        counts[status] = counts.get(status, 0) + 1
    needs_listening = (counts['word_difference'] + counts['meaningful_difference'] +
                       counts['missing'] + counts['recovered'])
    return {
        'rows': len(rows),
        'agreement': counts['agreement'],
        'same_content_different_split': counts['segmentation'],
        'needs_listening': needs_listening,
        'reference_checked': sum(bool(row.get('reference_checked')) for row in rows),
        'counts': counts,
    }
