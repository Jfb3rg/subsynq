"""Compare local mono WAV windows with a separate reference; diagnostic only."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from audio_channel_probe import checked_samples


RATE = 16000


def parse_window(value):
    try:
        start, end = (float(part) for part in value.split(':'))
    except (ValueError, TypeError) as exc:
        raise ValueError('Window must be start:end in seconds') from exc
    if not all(math.isfinite(part) for part in (start, end)) or start < 0 or not 0.5 <= end - start <= 10:
        raise ValueError('Window must be 0.5–10 seconds, finite and non-negative')
    return start, end


def load_mono(path):
    import numpy as np
    import soundfile as sf

    samples, rate = sf.read(path, dtype='float64')
    if rate != RATE or samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
        raise ValueError('Expected finite nonempty mono 16 kHz WAV')
    return samples


def match_window(reference, target, start, end):
    """Find strongest signed Pearson correlation over all reference offsets."""
    import numpy as np

    if not all(math.isfinite(part) for part in (start, end)) or not 0 <= start < end:
        raise ValueError('Invalid window')
    left, right = round(start * RATE), round(end * RATE)
    if left < 0 or right > len(target) or right <= left:
        raise ValueError('Window outside target audio')
    query = np.asarray(target[left:right], dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    if query.ndim != 1 or reference.ndim != 1 or len(reference) < len(query):
        raise ValueError('Reference shorter than target window')
    if not np.isfinite(query).all() or not np.isfinite(reference).all():
        raise ValueError('Nonfinite audio')
    query = query - query.mean()
    query_energy = float(np.dot(query, query))
    if query_energy <= 1e-12:
        raise ValueError('Target window has no measurable variation')

    # Valid FFT convolution gives every sample-accurate reference position.
    fft_size = 1 << (len(reference) + len(query) - 2).bit_length()
    product = np.fft.rfft(reference, fft_size) * np.fft.rfft(query[::-1], fft_size)
    dot = np.fft.irfft(product, fft_size)[len(query) - 1:len(reference)]
    sums = np.concatenate(([0.0], np.cumsum(reference)))
    sums_sq = np.concatenate(([0.0], np.cumsum(reference * reference)))
    sum_window = sums[len(query):] - sums[:-len(query)]
    energy = sums_sq[len(query):] - sums_sq[:-len(query)] - sum_window * sum_window / len(query)
    denominator = np.sqrt(query_energy * np.maximum(energy, 0.0))
    if not np.any(denominator > 1e-12):
        raise ValueError('Reference has no measurable variation')
    scores = np.divide(dot, denominator, out=np.zeros_like(dot), where=denominator > 1e-12)
    offset = int(np.argmax(np.abs(scores)))
    return {'target_start': start, 'target_end': end,
            'reference_start': round(offset / RATE, 6),
            'reference_minus_target_seconds': round(offset / RATE - start, 6),
            'correlation': float(scores[offset]),
            'absolute_correlation': float(abs(scores[offset]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--window', action='append', required=True, help='Target start:end seconds')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    windows = [parse_window(window) for window in args.window]
    manifest = checked_samples(args.samples)
    reference = load_mono(args.reference)
    report = {'method': 'sample-accurate FFT normalized cross-correlation v1',
              'reference_sha256': hashlib.sha256(args.reference.read_bytes()).hexdigest(),
              'reference_frames': len(reference), 'sample_rate': RATE,
              'samples_start': manifest['start'], 'samples_end': manifest['end'],
              'variants': []}
    for entry in manifest['variants']:
        target = load_mono(args.samples / (entry['name'] + '.wav'))
        report['variants'].append({'name': entry['name'], 'sha256': entry['sha256'],
                                   'frames': len(target),
                                   'matches': [match_window(reference, target, a, b) for a, b in windows]})
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
