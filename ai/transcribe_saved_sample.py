"""Transcribe one saved WAV with precise CPU word timing for comparison."""
import json
import os
import time
from pathlib import Path

from faster_whisper import WhisperModel

sample = Path(os.environ['SUBSYNQ_SAMPLE'])
output = Path(os.environ['SUBSYNQ_OUTPUT'])
offset = float(os.environ.get('SUBSYNQ_OFFSET', '0'))
threads = int(os.environ.get('SUBSYNQ_CPU_THREADS', '6'))

started = time.monotonic()
model = WhisperModel(
    os.environ.get('SUBSYNQ_WHISPER_MODEL', 'large-v3'),
    device='cpu', compute_type='int8', cpu_threads=threads, download_root='/models',
)
segments, info = model.transcribe(
    str(sample), task='transcribe', language=None, multilingual=True,
    beam_size=5, vad_filter=True, condition_on_previous_text=False,
    word_timestamps=True,
)
rows = []
for segment in segments:
    words = [{
        'start': round(offset + word.start, 3),
        'end': round(offset + word.end, 3),
        'word': word.word.strip(),
        'probability': round(word.probability, 4),
    } for word in (segment.words or []) if word.start is not None and word.end is not None]
    rows.append({
        'start': round(offset + segment.start, 3),
        'end': round(offset + segment.end, 3),
        'text': segment.text.strip(),
        'avg_logprob': segment.avg_logprob,
        'words': words,
    })
    print(f'{offset + segment.start:.2f}: {segment.text.strip()}', flush=True)
payload = {
    'sample': sample.name,
    'offset': offset,
    'duration': round(info.duration, 3),
    'speech_seconds': round(info.duration_after_vad, 3),
    'language': info.language,
    'language_probability': info.language_probability,
    'model': 'large-v3',
    'compute_type': 'int8',
    'elapsed_seconds': round(time.monotonic() - started, 1),
    'segments': rows,
}
temporary = output.with_suffix('.tmp')
temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, output)
print(f'Finished: {output}', flush=True)
