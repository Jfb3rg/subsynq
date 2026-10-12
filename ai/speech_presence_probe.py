"""Compare two existing Silero speech thresholds on bounded local audio."""

import argparse
import json
import math
from pathlib import Path
import subprocess


RATE = 16000


def bounds(start, end):
    if not all(math.isfinite(value) for value in (start, end)) or start < 0 or not 0 < end - start <= 60:
        raise ValueError('Expected a finite audio window of at most 60 seconds')
    return end - start


def coverage(intervals, duration):
    ordered = sorted((max(0.0, float(item['start'])), min(duration, float(item['end'])))
                     for item in intervals)
    merged = []
    for start, end in ordered:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return round(sum(end - start for start, end in merged), 3)


def probe(path, start, end):
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    duration = bounds(start, end)
    pcm = subprocess.check_output([
        'ffmpeg', '-v', 'error', '-nostdin', '-ss', str(start), '-i', str(path),
        '-t', str(duration), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', str(RATE),
        '-f', 's16le', 'pipe:1',
    ])
    audio = np.frombuffer(pcm, dtype='<i2').astype('float32') / 32768.0
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError('Empty or nonfinite decoded audio')
    if abs(len(audio) - round(duration * RATE)) > 1:
        raise ValueError('Decoded audio does not cover requested window')
    modes = {
        'strict': VadOptions(threshold=.72, min_speech_duration_ms=180,
                             min_silence_duration_ms=250, speech_pad_ms=100),
        'sensitive': VadOptions(threshold=.15, min_speech_duration_ms=120,
                                min_silence_duration_ms=200, speech_pad_ms=100),
    }
    result = {'source': str(path), 'source_bytes': path.stat().st_size,
              'start': start, 'end': end, 'frames': len(audio), 'sample_rate': RATE,
              'rms': float(np.sqrt(np.mean(audio.astype('float64') ** 2))),
              'peak': float(np.max(np.abs(audio))), 'modes': {}}
    for name, options in modes.items():
        rows = [{'start': round(item['start'] / RATE, 3),
                 'end': round(item['end'] / RATE, 3)}
                for item in get_speech_timestamps(audio, options)]
        result['modes'][name] = {'threshold': options.threshold,
                                 'intervals': rows,
                                 'covered_seconds': coverage(rows, duration)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media', type=Path, required=True)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = probe(args.media, args.start, args.end)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
