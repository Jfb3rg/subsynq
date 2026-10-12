"""Diagnostic-only: transcribe short speech islands around reported E01 errors."""
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps
import sys

sys.path.insert(0, '/app')
from qwen_quality import alignment_quality


MEDIA = Path(os.environ['SUBSYNQ_PROBE_MEDIA'])
OUTPUT = Path(os.environ['SUBSYNQ_PROBE_OUTPUT'])
URL = os.environ.get('SUBSYNQ_QWEN_ASR_URL', 'http://qwen-asr:8091').rstrip('/')
WINDOWS = ((238.0, 30.0), (820.0, 30.0))


def speech_islands(audio, offset, length):
    spans = get_speech_timestamps(
        audio, VadOptions(threshold=.45, min_speech_duration_ms=170,
                          min_silence_duration_ms=300, speech_pad_ms=220),
    )
    merged = []
    for span in spans:
        start = max(0.0, span['start'] / 16000 - .25)
        end = min(length, span['end'] / 16000 + .25)
        if merged and start - merged[-1][1] <= .7 and end - merged[-1][0] <= 12:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(round(offset + start, 3), round(offset + end, 3))
            for start, end in merged if end - start >= .35]


def transcribe(path):
    payload = path.read_bytes()
    request = urllib.request.Request(
        URL + '/transcribe', data=payload,
        headers={'Content-Type': 'audio/wav', 'Content-Length': str(len(payload))},
    )
    try:
        with urllib.request.urlopen(request, timeout=1200) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode('utf-8', errors='replace')) from error


results = []
with tempfile.TemporaryDirectory(prefix='subsynq-qwen-probe-') as folder:
    clip = Path(folder) / 'clip.wav'
    island = Path(folder) / 'island.wav'
    for offset, length in WINDOWS:
        subprocess.run([
            'ffmpeg', '-v', 'error', '-ss', str(offset), '-i', str(MEDIA),
            '-t', str(length), '-map', '0:a:0', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', '-y', str(clip),
        ], check=True, capture_output=True, timeout=90)
        audio = decode_audio(str(clip), sampling_rate=16000)
        spans = speech_islands(audio, offset, length)
        print(f'{offset}: {spans}', flush=True)
        for start, end in spans:
            subprocess.run([
                'ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(MEDIA),
                '-t', str(end - start), '-map', '0:a:0', '-ac', '1', '-ar', '16000',
                '-c:a', 'pcm_s16le', '-y', str(island),
            ], check=True, capture_output=True, timeout=90)
            reading = transcribe(island)
            result = {'start': start, 'end': end, **reading,
                      'alignment_quality': alignment_quality(
                          reading.get('items', []), clip_seconds=end - start)}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
temporary = OUTPUT.with_suffix('.tmp')
temporary.write_text(json.dumps({'media': str(MEDIA), 'runs': results},
                                ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, OUTPUT)
