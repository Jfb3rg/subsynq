"""Bounded, language-neutral rescue of speech inside long subtitle gaps."""
from difflib import SequenceMatcher
import math
import re


QWEN_TO_WHISPER = {
    'arabic': 'ar', 'cantonese': 'yue', 'chinese': 'zh', 'czech': 'cs',
    'danish': 'da', 'dutch': 'nl', 'english': 'en', 'filipino': 'tl',
    'finnish': 'fi', 'french': 'fr', 'german': 'de', 'greek': 'el',
    'hindi': 'hi', 'hungarian': 'hu', 'indonesian': 'id', 'italian': 'it',
    'japanese': 'ja', 'korean': 'ko', 'macedonian': 'mk', 'malay': 'ms', 'persian': 'fa',
    'polish': 'pl', 'portuguese': 'pt', 'romanian': 'ro', 'russian': 'ru',
    'spanish': 'es', 'swedish': 'sv', 'thai': 'th', 'turkish': 'tr',
    'vietnamese': 'vi',
}


def whisper_language(qwen_name):
    """Unknown languages stay unforced instead of being mislabeled English."""
    return QWEN_TO_WHISPER.get(str(qwen_name or '').strip().lower())


def gap_candidates(rows, *, minimum_gap=8.0, maximum_gap=32.0):
    ordered = sorted(rows, key=lambda row: (row['start'], row['end']))
    return [{'gap_start': float(left['end']), 'gap_end': float(right['start'])}
            for left, right in zip(ordered, ordered[1:])
            if minimum_gap <= float(right['start']) - float(left['end']) <= maximum_gap]


def select_weak_speech_gaps(probes, *, maximum_total=8, maximum_per_quarter_hour=3):
    """Rank cheap audio probes before invoking the expensive second ASR model."""
    ranked = []
    for probe in probes:
        speech = sum(max(0.0, item['end'] - item['start'])
                     for item in probe.get('speech', []))
        if speech < .4:
            continue
        gap = probe['gap_end'] - probe['gap_start']
        ranked.append((gap * math.sqrt(speech), probe))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]['gap_start']))
    selected, quarters = [], {}
    for _score, probe in ranked:
        quarter = int(probe['gap_start'] // 900)
        if (len(selected) >= maximum_total or
                quarters.get(quarter, 0) >= maximum_per_quarter_hour):
            continue
        selected.append(probe)
        quarters[quarter] = quarters.get(quarter, 0) + 1
    return sorted(selected, key=lambda probe: probe['gap_start'])


def _normalized(text):
    return re.sub(r'[^\w]+', ' ', str(text).lower(), flags=re.UNICODE).strip()


def _enough_text(text, language):
    normalized = _normalized(text)
    if str(language).strip().lower() in ('chinese', 'cantonese', 'japanese'):
        return len(normalized.replace(' ', '')) >= 6
    return len(normalized.split()) >= 4


def corroborated_gap_cues(base, probes):
    """Use Whisper times only when independent Qwen text and weak speech agree."""
    rows = [dict(row) for row in sorted(base, key=lambda item: item['start'])]
    added = []
    for probe in sorted(probes, key=lambda item: item['gap_start']):
        source = _normalized(probe.get('qwen_text', ''))
        language = probe.get('language')
        if not whisper_language(language) or not _enough_text(source, language):
            continue
        for candidate in probe.get('whisper_cues', []):
            target = _normalized(candidate.get('text', ''))
            if (not _enough_text(target, language)
                    or float(candidate.get('confidence', 0)) < .35
                    or float(candidate.get('mean_confidence',
                                           candidate.get('confidence', 0))) < .75
                    or candidate['end'] - candidate['start'] > 7
                    or candidate['start'] < probe['gap_start'] - .15
                    or candidate['end'] > probe['gap_end'] + .15
                    or SequenceMatcher(None, source, target).ratio() < .82):
                continue
            if not any(abs(candidate['start'] - item['end']) <= 4.0
                       or abs(candidate['end'] - item['start']) <= 4.0
                       or candidate['start'] < item['end'] and candidate['end'] > item['start']
                       for item in probe.get('speech', [])):
                continue
            if any(candidate['start'] < row['end'] - .001 and
                   candidate['end'] > row['start'] + .001 for row in rows):
                continue
            rows.append(dict(candidate))
            added.append(dict(candidate))
            rows.sort(key=lambda item: item['start'])
    return rows, added
