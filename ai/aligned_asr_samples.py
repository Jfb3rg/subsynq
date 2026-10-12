"""Prepare equal-length local audio/reference slices for offline ASR comparison."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import wave

from audio_channel_probe import checked_samples


RATE = 16000


def read_pcm(path):
    with wave.open(str(path), 'rb') as source:
        if (source.getnchannels(), source.getframerate(), source.getsampwidth(),
                source.getcomptype()) != (1, RATE, 2, 'NONE'):
            raise ValueError('Expected mono PCM16 16 kHz WAV')
        return source.readframes(source.getnframes())


def slice_pcm(data, start, duration):
    if not all(math.isfinite(value) for value in (start, duration)) or start < 0 or not 0 < duration <= 60:
        raise ValueError('Invalid slice time')
    first = round(start * RATE) * 2
    count = round(duration * RATE) * 2
    if first + count > len(data):
        raise ValueError('Slice exceeds audio')
    return data[first:first + count]


def prepare(source_dir, reference_path, start, end, reference_offset, output):
    if not all(math.isfinite(value) for value in (start, end, reference_offset)):
        raise ValueError('Nonfinite timing')
    duration = end - start
    if start < 0 or reference_offset < 0 or not 0 < duration <= 60:
        raise ValueError('Invalid comparison window')
    manifest = checked_samples(source_dir)
    if start < manifest['start'] or end > manifest['end']:
        raise ValueError('Source comparison window exceeds manifest')
    names = [variant['name'] for variant in manifest['variants']]
    if 'reference' in names:
        raise ValueError('Source already has a reference variant')
    inputs = [(name, source_dir / (name + '.wav'), start - manifest['start']) for name in names]
    inputs.append(('reference', reference_path, start + reference_offset))
    slices = [(name, path, slice_pcm(read_pcm(path), offset, duration))
              for name, path, offset in inputs]
    output.mkdir(parents=True, exist_ok=False)
    result = {'source': str(source_dir), 'reference': str(reference_path),
              'source_window': [start, end], 'reference_offset': reference_offset,
              'start': 0, 'end': duration, 'variants': []}
    for name, path, frames in slices:
        target = output / (name + '.wav')
        with wave.open(str(target), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(frames)
        result['variants'].append({'name': name, 'source': str(path),
                                   'frames': len(frames) // 2,
                                   'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    with (output / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--reference-offset', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.samples, args.reference, args.start, args.end,
                             args.reference_offset, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
