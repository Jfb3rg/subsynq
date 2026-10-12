"""Budgeted short re-listening windows for suspicious ASR gaps.

These are evidence-gathering targets, not permission to insert subtitles.
"""
import re


def plan_short_focus_windows(rows, duration, *, minutes_per_bucket=15,
                             maximum_per_bucket=12, maximum_total=48):
    ordered = sorted(rows, key=lambda row: (float(row['start']), float(row['end'])))
    if duration <= 0 or maximum_per_bucket < 1 or maximum_total < 1:
        return []
    candidates = []

    def add(center, before, after, priority, reason):
        start = round(max(0.0, center - before), 3)
        end = round(min(float(duration), center + after), 3)
        if end - start >= 3:
            candidates.append({'start': start, 'end': end, 'priority': priority,
                               'reason': reason, 'center': center})

    for index, row in enumerate(ordered):
        start, end = float(row['start']), float(row['end'])
        words = re.findall(r'\w+', str(row.get('text', row.get('source', ''))))
        if len(words) == 1 and end - start <= 1.0:
            add(start, 2, 7, 3, 'ensamt_kort_ord')
        if not index:
            continue
        gap = start - float(ordered[index - 1]['end'])
        if 2 <= gap <= 8 and end - start <= 1.5:
            add(start, 4.5, 5, 2, 'lucka_fore_kort_replik')
        elif gap >= 10:
            add(start, 2.5, 7, 1, 'borjan_efter_lang_paus')

    # Keep the strongest target when several requests concern the same scene.
    chosen = []
    for candidate in sorted(candidates, key=lambda row: (-row['priority'], row['start'])):
        near = next((row for row in chosen
                     if abs(candidate['center'] - row['center']) < 4), None)
        if near is not None:
            near['start'] = min(near['start'], candidate['start'])
            near['end'] = max(near['end'], candidate['end'])
            continue
        chosen.append(candidate)
    buckets = {}
    for candidate in chosen:
        bucket = int(candidate['center'] // (minutes_per_bucket * 60))
        buckets.setdefault(bucket, []).append(candidate)
    selected = []
    for bucket in sorted(buckets):
        selected.extend(sorted(buckets[bucket],
                               key=lambda row: (-row['priority'], row['start']))[:maximum_per_bucket])
    selected = selected[:maximum_total]
    return [{key: value for key, value in row.items() if key != 'center'}
            for row in sorted(selected, key=lambda row: row['start'])]
