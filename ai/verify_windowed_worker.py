"""Bounded production-ASR check; writes diagnostics only under /config."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel
from worker import transcribe

MEDIA = Path(os.environ['SUBSYNQ_VERIFY_MEDIA'])
SECONDS = float(os.environ.get('SUBSYNQ_VERIFY_SECONDS', '930'))
START = float(os.environ.get('SUBSYNQ_VERIFY_START', '0'))
OUTPUT = Path(os.environ.get('SUBSYNQ_VERIFY_OUTPUT', '/config/worker-window-check.json'))

with tempfile.TemporaryDirectory(prefix='subsynq-verify-') as folder:
    clip = Path(folder) / 'bounded.wav'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-ss', str(START), '-i', str(MEDIA),
        '-t', str(SECONDS), '-map', '0:a:0',
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-y', str(clip),
    ], check=True, timeout=180)
    model = WhisperModel('large-v3', device='cpu', compute_type='int8', cpu_threads=6,
                         download_root='/models', local_files_only=True)
    rows = transcribe({'id': -1}, clip, model)
    for row in rows:
        row['start'] += START
        row['end'] += START

payload = {'media': MEDIA.name, 'start': START, 'seconds': SECONDS, 'rows': rows}
temporary = OUTPUT.with_suffix('.tmp')
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, OUTPUT)
print(json.dumps({'output': str(OUTPUT), 'rows': len(rows)}, ensure_ascii=False), flush=True)
