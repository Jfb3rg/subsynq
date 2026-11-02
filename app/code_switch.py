"""Confirm short English code switches inside automatically chosen Nordic speech."""
import math
from asr_windows import _normalized, _similarity, _is_boilerplate
from reread import agreed_replacement


def plan_checks(job, rows, qwen, other_readings, duration, *, maximum=4):
    # A user-confirmed language is never overruled by this optional check.
    if (maximum < 1 or (job.get('language_policy') or {}).get('effective_language')
            or not any((job.get(key) or {}).get('chosen') for key in ('norwegian_reader', 'swedish_reader'))):
        return []
    result = []
    for index, row in enumerate(rows):
        confidence = row.get('confidence', 0)
        if (type(confidence) not in (int, float) or not math.isfinite(confidence)
                or not .6 <= row['end'] - row['start'] <= 3 or not 0 < confidence < .6
                or any(row.get(key) for key in ('confirmed_prefix', 'confirmed_source_words', 'confirmed_lead_onset'))):
            continue
        previous = rows[index - 1]['end'] if index else 0
        following = rows[index + 1]['start'] if index + 1 < len(rows) else duration
        # No independent reader may support the current words at this time.
        if any(x['end'] > row['start'] and x['start'] < row['end']
               and _similarity(x.get('text', ''), row['text']) >= .85 for x in other_readings):
            continue
        candidates = [x for x in qwen if row['start'] - .5 <= x['start'] < x['end'] <= row['end'] + .5
                      and x['start'] >= previous and x['end'] <= following
                      and 3 <= len(_normalized(x.get('text', '')).split()) <= 10
                      and not _is_boilerplate(x.get('text', ''))
                      and _similarity(row['text'], x.get('text', '')) <= .5
                      and not set(_normalized(row['text']).split()).intersection(_normalized(x.get('text', '')).split())]
        texts = {_normalized(x['text']) for x in candidates}
        if len(texts) != 1:
            continue
        windows = [{'start': round(max(previous + .08, row['start'] - pad), 3),
                    'end': round(min(following - .08, row['end'] + pad - .1), 3)} for pad in (.5, .7)]
        if (windows[1]['start'] > windows[0]['start'] - .15
                or windows[1]['end'] < windows[0]['end'] + .15):
            continue
        result.append({'row': index, 'from': row['text'], 'start': row['start'], 'end': row['end'],
                       'text': candidates[0]['text'], 'windows': windows})
        if len(result) >= maximum:
            break
    return result


def apply_checks(rows, plans, records):
    result, changes = [dict(r) for r in rows], []
    for plan in plans:
        index = plan['row']
        row = result[index]
        if (row['text'], row['start'], row['end']) != (plan['from'], plan['start'], plan['end']):
            continue
        needle = _normalized(plan['text'])
        proof = [r for r in records if r.get('row') == index]
        if (any(r.get('family') not in ('v2', 'v3') or type(r.get('window')) is not int for r in proof)
                or sorted((r.get('family'), r.get('window')) for r in proof) != [
                ('v2', 0), ('v2', 1), ('v3', 0), ('v3', 1)]):
            continue
        valid = True
        for item in proof:
            words = item.get('words') or []
            clip = plan['windows'][item['window']]
            probability = item.get('language_probability')
            if (item.get('language') != 'en' or type(probability) not in (int, float)
                    or not math.isfinite(probability) or probability < .8
                    or _normalized(item.get('text', '')) != needle
                    or _normalized(' '.join(w.get('text', '') for w in words)) != needle or not words):
                valid = False
                break
            for word in words:
                values = [word.get(k) for k in ('start', 'end', 'probability')]
                if (any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                        or not clip['start'] <= word['start'] < word['end'] <= clip['end'] + .001
                        or word['end'] - word['start'] > 1.5 or word['probability'] < .6):
                    valid = False
            if not valid:
                break
            if (sum(w.get('probability', 0) >= .8 for w in words) < 2
                    or any(a['end'] > b['start'] + .02 for a, b in zip(words, words[1:]))):
                valid = False
        if not valid:
            continue
        accepted, consensus = agreed_replacement(row['text'], [
            {'name': 'precision' if r['family'] == 'v3' else 'v2', 'text': r['text'],
             'confidence': min(w['probability'] for w in r['words'])} for r in proof] + [
            {'name': 'qwen', 'text': plan['text'], 'confidence': .5}])
        if accepted is None:
            continue
        row['text'] = plan['text']
        row['confirmed_source_words'] = _normalized(plan['text']).split()
        row['confirmed_language'] = 'en'
        changes.append({**plan, 'to': plan['text'], 'language': 'en',
                        'families': ['whisper-v3', 'whisper-v2', 'qwen'], 'proof': proof, 'consensus': consensus})
    return result, changes
