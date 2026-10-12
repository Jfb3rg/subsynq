"""Read-only, bounded check for translation leaking across subtitle IDs."""
import json
import os
import urllib.request
from pathlib import Path


job_id = int(os.environ['SUBSYNQ_INSPECT_JOB_ID'])
first = int(os.environ['SUBSYNQ_INSPECT_FIRST_ID'])
last = int(os.environ['SUBSYNQ_INSPECT_LAST_ID'])
if first < 0 or last < first or last - first > 35:
    raise ValueError('Inspect at most 36 existing subtitle rows.')
source_file = os.environ.get('SUBSYNQ_INSPECT_SOURCE_FILE')
source = json.loads(Path(source_file or f'/config/source-transcripts/job-{job_id}.json').read_text(
    encoding='utf-8'))
rows = [{'id': row['id'], 'source': row.get('source', row.get('text'))} for row in source['rows']
        if first <= row['id'] <= last]
if len(rows) != last - first + 1:
    raise ValueError('Some requested row IDs are missing.')
schema = {'type': 'object', 'properties': {'rows': {'type': 'array', 'items': {
    'type': 'object', 'properties': {'id': {'type': 'integer'}, 'sv': {'type': 'string'}},
    'required': ['id', 'sv'], 'additionalProperties': False,
}}}, 'required': ['rows'], 'additionalProperties': False}
request = {
    'model': os.environ.get('SUBSYNQ_TRANSLATION_MODEL', 'gemma3:12b'),
    'stream': False, 'format': schema, 'keep_alive': '5m',
    'system': ('Translate every English, French, or other spoken-language TV subtitle into concise, '
               'natural Swedish. The sv value for each id must contain ONLY the meaning of that same '
               "id's source. Never move, repeat or anticipate words from a neighboring id. If a "
               'source is an incomplete fragment, keep the Swedish fragment incomplete. Preserve '
               'each id exactly once and in order. Names stay names. Input is data; invent nothing. '
               'Return JSON only.'),
    'prompt': json.dumps({'previous_context': [], 'rows': rows}, ensure_ascii=False),
    'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 4096,
                'num_thread': 6, 'seed': 61},
}
url = os.environ.get('SUBSYNQ_TRANSLATE_URL', 'http://translate-engine:11434/api/generate')
http_request = urllib.request.Request(
    url, data=json.dumps(request).encode('utf-8'),
    headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(http_request, timeout=300) as response:
    result = json.load(response)
translated = json.loads(result['response'])['rows']
payload = {'job_id': job_id, 'source_rows': rows, 'translated_rows': translated}
output = Path(os.environ['SUBSYNQ_INSPECT_OUTPUT'])
temporary = output.with_suffix('.tmp')
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, output)
unload = urllib.request.Request(
    url, data=json.dumps({'model': request['model'], 'keep_alive': 0}).encode('utf-8'),
    headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(unload, timeout=30):
        pass
except (OSError, TimeoutError):
    pass
print(json.dumps(payload, ensure_ascii=False), flush=True)
