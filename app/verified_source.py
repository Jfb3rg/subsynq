"""Apply explicitly reviewed source-cue edits to one exact media file.

This is not an automatic ASR rescue. Every edit checks the original reading,
time and media identity; unexpected output fails closed before translation.
"""
import json
import re
from pathlib import Path


def _normalized(value):
    return ' '.join(re.findall(r'\w+', str(value).casefold()))


def apply_verified_source_edits(rows, media, config):
    review = Path(config) / 'review'
    paths = [review / 'verified-source-edits.json']
    folder = review / 'verified-source-edits'
    if folder.is_dir():
        paths.extend(sorted(folder.glob('*.json')))
    documents = [json.loads(path.read_text(encoding='utf-8'))
                 for path in paths if path.is_file()]
    matching = [document for document in documents
                if str(media) == document.get('media_path')]
    if not matching:
        return [dict(row) for row in rows], []
    if len(matching) != 1:
        raise ValueError('Flera uppsättningar verifierade källrättelser gäller samma videofil.')
    document = matching[0]
    if Path(media).stat().st_size != int(document['media_size']):
        raise ValueError('De verifierade källrättelserna gäller en annan version av videofilen.')
    result = [dict(row) for row in rows]
    applied = []
    for edit in document['edits']:
        action = edit['action']
        start, end = float(edit['start']), float(edit['end'])
        if not 0 <= start < end:
            raise ValueError('En verifierad källrättelse har ogiltig tid.')
        expected = _normalized(edit.get('expected', ''))
        replacement = str(edit.get('source', '')).strip()
        if action not in ('remove', 'replace', 'insert') or (
                action == 'replace' and (not expected or not replacement)) or (
                action == 'remove' and not expected) or (
                action == 'insert' and not replacement):
            raise ValueError('En verifierad källrättelse har ogiltigt innehåll.')
        center = (start + end) / 2
        matches = [index for index, row in enumerate(result)
                   if abs((float(row['start']) + float(row['end'])) / 2 - center) <= 1.5
                   and _normalized(row['source']) == expected]
        if action == 'insert':
            if any(abs((float(row['start']) + float(row['end'])) / 2 - center) <= 1.5
                   and _normalized(row['source']) == _normalized(replacement) for row in result):
                continue
            if any(float(row['start']) < end and float(row['end']) > start for row in result):
                raise ValueError(f'Verifierad källrättelse vid {start:.1f}s överlappar ny dialog.')
            result.append({'start': start, 'end': end, 'source': replacement})
        elif action == 'remove':
            if len(matches) > 1:
                raise ValueError(f'Flera möjliga källrader vid {start:.1f}s; rättelsen avbröts.')
            if not matches:
                continue  # A newly improved ASR already removed the ghost.
            result.pop(matches[0])
        else:
            if not matches and any(
                    abs((float(row['start']) + float(row['end'])) / 2 - center) <= 1.5
                    and _normalized(row['source']) == _normalized(replacement)
                    for row in result):
                continue  # The new ASR independently recovered the correction.
            if len(matches) != 1:
                raise ValueError(f'Förväntad källrad vid {start:.1f}s saknas eller är tvetydig.')
            result[matches[0]].update(start=start, end=end, source=replacement)
        applied.append(edit['label'])
    result.sort(key=lambda row: (float(row['start']), float(row['end'])))
    for previous, current in zip(result, result[1:]):
        if float(current['start']) < float(previous['end']) - .08:
            raise ValueError('Verifierade källrättelser skapade överlappande repliker.')
    return [{**row, 'id': index} for index, row in enumerate(result)], applied
