"""Fail-closed checks for Qwen forced-alignment output.

Recognition and alignment are separate claims: a plausible transcript does not
make its word timestamps trustworthy.
"""
import math


def alignment_quality(items, *, clip_seconds=None):
    words = list(items or [])
    if not words:
        return {'status': 'unreliable', 'reason': 'Inga tidsatta ord',
                'words': 0, 'zero_length': 0}
    zero_length = 0
    previous_start = -1.0
    for word in words:
        try:
            start, end = float(word['start']), float(word['end'])
        except (KeyError, TypeError, ValueError):
            return {'status': 'unreliable', 'reason': 'Ogiltig ordtid',
                    'words': len(words), 'zero_length': zero_length}
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0
                or end < start or start < previous_start
                or clip_seconds is not None and end > clip_seconds + .25):
            return {'status': 'unreliable', 'reason': 'Ordtid utanför ljudklippet',
                    'words': len(words), 'zero_length': zero_length}
        if end - start < .04:
            zero_length += 1
        previous_start = start
    if zero_length:
        return {'status': 'unreliable', 'reason': 'Ett eller flera ord saknar användbar längd',
                'words': len(words), 'zero_length': zero_length}
    return {'status': 'usable', 'reason': 'Alla ord har giltig längd inom klippet',
            'words': len(words), 'zero_length': 0}


def reliable_alignment_islands(items, *, maximum_pause=1.2):
    """Keep separately timed speech islands when another island has zero-length words."""
    words = list(items or [])
    quality = alignment_quality(words)
    if quality['status'] == 'usable':
        return words
    if quality['reason'] != 'Ett eller flera ord saknar användbar längd':
        return []
    groups, group = [], []
    for word in words:
        if group and float(word['start']) - float(group[-1]['end']) >= maximum_pause:
            groups.append(group)
            group = []
        group.append(word)
    if group:
        groups.append(group)
    reliable = [word for group in groups
                if alignment_quality(group)['status'] == 'usable'
                for word in group]
    return reliable if alignment_quality(reliable)['status'] == 'usable' else []
