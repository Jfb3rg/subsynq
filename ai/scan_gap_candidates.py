"""Read-only weak-speech scan of long gaps in a saved source transcript."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps


media = Path(os.environ['SUBSYNQ_PROBE_MEDIA'])
source = json.loads(Path(os.environ['SUBSYNQ_PROBE_SOURCE']).read_text(encoding='utf-8'))
output = Path(os.environ['SUBSYNQ_PROBE_OUTPUT'])
rows = sorted(source['rows'], key=lambda row: row['start'])
results = []
with tempfile.TemporaryDirectory(prefix='subsynq-gap-scan-') as folder:
    clip = Path(folder) / 'gap.wav'
    for left, right in zip(rows, rows[1:]):
        gap_start, gap_end = float(left['end']), float(right['start'])
        if not 8 <= gap_end - gap_start <= 32:
            continue
        subprocess.run([
            'ffmpeg', '-v', 'error', '-ss', str(gap_start), '-i', str(media),
            '-t', str(gap_end - gap_start), '-map', '0:a:0', '-ac', '1',
            '-ar', '16000', '-c:a', 'pcm_s16le', '-y', str(clip),
        ], capture_output=True, check=True, timeout=90)
        speech = get_speech_timestamps(
            decode_audio(str(clip), sampling_rate=16000),
            VadOptions(threshold=.25, min_speech_duration_ms=170,
                       min_silence_duration_ms=300, speech_pad_ms=100),
        )
        spans = [{'start': round(gap_start + span['start'] / 16000, 2),
                  'end': round(gap_start + span['end'] / 16000, 2)} for span in speech]
        results.append({'gap_start': gap_start, 'gap_end': gap_end,
                        'gap_seconds': round(gap_end - gap_start, 2),
                        'speech_seconds': round(sum(item['end'] - item['start']
                                                    for item in spans), 2),
                        'speech': spans, 'before': left['source'],
                        'after': right['source']})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
