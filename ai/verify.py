"""Second decoding pass for comparison, not a human quality sign-off."""
import json
import os
from pathlib import Path
from dataclasses import asdict
from faster_whisper import WhisperModel

run = os.environ.get('SUBSYNQ_ASR_RUN', '').strip()
if not run:
    raise SystemExit("Set SUBSYNQ_ASR_RUN to an existing diagnostic run.")
folder = Path(run)
original = json.loads((folder / 'result.json').read_text())
model = WhisperModel('large-v3', device='cpu', compute_type='int8', cpu_threads=6, download_root='/models', local_files_only=True)
result = {"method": "large-v3, no VAD, multilingual detection; automated comparison only", "samples": []}
for sample in original['samples']:
    offset = sample['offset']
    segments, info = model.transcribe(str(folder / f'sample-{offset}.wav'), language=None, multilingual=True, vad_filter=False, beam_size=5, condition_on_previous_text=False, word_timestamps=True)
    rows = []
    for segment in segments:
        row = asdict(segment)
        row['start'] += offset
        row['end'] += offset
        rows.append(row)
        print(f"{row['start']:.2f}: {row['text']}", flush=True)
    result['samples'].append({'offset': offset, 'segments': rows})
(folder / 'recheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
