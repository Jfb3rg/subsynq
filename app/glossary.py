"""A word list the user keeps for what the speech recognition keeps mishearing: /config/glossary.json, a list of {"heard": "snow goat", "write": "snowgo"}.

Yukon Men (dev job 146): Whisper large-v3 wrote "Snow Goat", "snow goal", "snow goats" and "snow ghosts" for the Alaskan snowmobile, "snowgo"; Qwen3-ASR heard
"Snowgo could freeze up" and "snowgos" in the audio. The file holds no built-in entries: what the user writes there is applied to the source text, whole words
only, before anything is translated."""
import json
import re
from pathlib import Path


def load_glossary(path):
    """The valid entries of the glossary file; a missing or broken file is an empty list."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    entries = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get('heard'), str) and isinstance(item.get('write'), str) and item['heard'].strip() and item['write'].strip():
            entries.append({'heard': ' '.join(item['heard'].split()), 'write': item['write'].strip()})
    return sorted(entries, key=lambda entry: -len(entry['heard']))


def _match_case(found, replacement):
    return replacement[:1].upper() + replacement[1:] if found[:1].isupper() and not replacement[:1].isupper() else replacement


def apply_glossary(rows, entries, text_key='text'):
    """(rows, changes): every whole-word, case-insensitive occurrence of an entry's `heard` is written as its `write` (a capital letter at the start stays)."""
    rows = [dict(row) for row in rows]
    changes = []
    for row in rows:
        text = str(row.get(text_key, ''))
        for entry in entries:
            pattern = re.compile(r'(?<![^\W_])' + r'\s+'.join(re.escape(part) for part in entry['heard'].split()) + r'(?![^\W_])', re.IGNORECASE)
            text = pattern.sub(lambda match: _match_case(match.group(0), entry['write']), text)
        if text != row.get(text_key, ''):
            changes.append({'start': row.get('start'), 'from': row[text_key], 'to': text})
            row[text_key] = text
    return rows, changes
