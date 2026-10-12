"""Transcribe several short focus windows with one loaded CPU model."""
import json
import os
from pathlib import Path

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

sample = Path(os.environ['SUBSYNQ_SAMPLE'])
output = Path(os.environ['SUBSYNQ_OUTPUT'])
offset = float(os.environ.get('SUBSYNQ_OFFSET', '0'))
windows = json.loads(os.environ['SUBSYNQ_WINDOWS'])
language = os.environ.get('SUBSYNQ_LANGUAGE', 'en')
audio = decode_audio(str(sample))
model = WhisperModel(os.environ.get('SUBSYNQ_FOCUS_MODEL', 'large-v3'),
                     device='cpu', compute_type='int8', cpu_threads=6,
                     download_root='/models', local_files_only=True)

results = []
for start, end in windows:
    segments, _ = model.transcribe(
        audio[int(start * 16000):int(end * 16000)], language=language, multilingual=False,
        vad_filter=False, beam_size=5, condition_on_previous_text=False,
        word_timestamps=True, temperature=0,
    )
    rows = []
    for segment in segments:
        rows.append({
            'start': round(offset + start + segment.start, 3),
            'end': round(offset + start + segment.end, 3),
            'text': segment.text.strip(),
            'words': [{
                'start': round(offset + start + word.start, 3),
                'end': round(offset + start + word.end, 3),
                'word': word.word.strip(),
                'probability': round(word.probability, 4),
            } for word in (segment.words or [])],
        })
    results.append({'window': [offset + start, offset + end], 'segments': rows})
    print(json.dumps(results[-1], ensure_ascii=False), flush=True)

temporary = output.with_suffix('.tmp')
temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, output)
