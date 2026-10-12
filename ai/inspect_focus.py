"""Compare bounded dialogue extraction variants without writing media subtitles."""
import json
import os
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from faster_whisper import WhisperModel

MEDIA = Path(os.environ['SUBSYNQ_VERIFY_MEDIA'])
START = float(os.environ.get('SUBSYNQ_FOCUS_START', '815'))
DURATION = float(os.environ.get('SUBSYNQ_FOCUS_DURATION', '65'))
OUTPUT = Path(os.environ.get('SUBSYNQ_FOCUS_OUTPUT', '/config/focus-inspection.json'))
MODEL_NAME = os.environ.get('SUBSYNQ_FOCUS_MODEL', 'large-v3')
LANGUAGE = os.environ.get('SUBSYNQ_FOCUS_LANGUAGE') or None
ALLOW_DOWNLOAD = os.environ.get('SUBSYNQ_FOCUS_ALLOW_DOWNLOAD', '0') == '1'

model = WhisperModel(MODEL_NAME, device='cpu', compute_type='int8', cpu_threads=6,
                     download_root='/models', local_files_only=not ALLOW_DOWNLOAD)
results = []
with tempfile.TemporaryDirectory(prefix='subsynq-focus-') as folder:
    for channel, audio_filter in (
            ('mono-downmix', None), ('dialogue-center', 'pan=mono|c0=c2')):
        wav = Path(folder) / f'{channel}.wav'
        command = ['ffmpeg', '-v', 'error', '-ss', str(START), '-i', str(MEDIA),
                   '-t', str(DURATION), '-map', '0:a:0']
        if audio_filter:
            command += ['-af', audio_filter]
        else:
            command += ['-ac', '1']
        command += ['-ar', '16000', '-c:a', 'pcm_s16le', '-y', str(wav)]
        subprocess.run(command, check=True, timeout=120)
        for vad in (True, False):
            segments, info = model.transcribe(
                str(wav), language=LANGUAGE, multilingual=LANGUAGE is None,
                vad_filter=vad, beam_size=5,
                condition_on_previous_text=False, word_timestamps=True,
                hallucination_silence_threshold=2.0,
            )
            rows = []
            for segment in segments:
                row = asdict(segment)
                row['start'] += START
                row['end'] += START
                for word in row.get('words') or []:
                    word['start'] += START
                    word['end'] += START
                rows.append(row)
            results.append({'channel': channel, 'vad': vad, 'language': info.language,
                            'duration_after_vad': info.duration_after_vad, 'rows': rows})

temporary = OUTPUT.with_suffix('.tmp')
temporary.write_text(json.dumps({'model': MODEL_NAME, 'language_hint': LANGUAGE,
                                'start': START, 'duration': DURATION,
                                'results': results},
                                ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, OUTPUT)
print(json.dumps({'output': str(OUTPUT),
                  'runs': [{'channel': run['channel'], 'vad': run['vad'],
                            'rows': len(run['rows'])} for run in results]}, ensure_ascii=False))
