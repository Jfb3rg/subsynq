"""Reproduce full-media ASR window offsets around short diagnostic intervals.

Unlike verify_windowed_worker.py, this reads the original media directly. It
does not write a subtitle or enqueue a library job.
"""
import json
import os
from pathlib import Path

from faster_whisper import WhisperModel
from worker import transcribe


media = Path(os.environ['SUBSYNQ_VERIFY_MEDIA'])
intervals = [tuple(map(float, pair)) for pair in json.loads(
    os.environ['SUBSYNQ_VERIFY_INTERVALS'])]
output = Path(os.environ['SUBSYNQ_VERIFY_OUTPUT'])
if not intervals or len(intervals) > 4 or any(end - start > 90 for start, end in intervals):
    raise ValueError('Välj högst fyra diagnostikområden om maximalt 90 sekunder.')

model = WhisperModel('large-v3', device='cpu', compute_type='int8', cpu_threads=6,
                     download_root='/models', local_files_only=True)
rows = transcribe({'id': -1}, media, model, focus_intervals=intervals)
focused = [row for row in rows if any(
    row['start'] < end and row['end'] > start for start, end in intervals)]
payload = {'media': str(media), 'intervals': intervals,
           'native_window_grid': True, 'rows': focused}
temporary = output.with_suffix('.tmp')
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding='utf-8')
os.replace(temporary, output)
print(json.dumps({'output': str(output), 'focused_rows': len(focused)},
                 ensure_ascii=False), flush=True)
