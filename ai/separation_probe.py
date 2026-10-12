"""Bounded, offline Demucs vocal separation; diagnostic copies only."""
import argparse
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import subprocess
import time

from audio_channel_probe import window, wav_stats


def validate_checkpoint(repo):
    path = repo / '955717e8-8726e21a.th'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not digest.startswith('8726e21a'):
        raise ValueError('Checkpoint hash does not match official filename')
    return path, digest


def validate_sources(source_names):
    if source_names.count('vocals') != 1:
        raise ValueError('Expected one vocals stem')
    return source_names.index('vocals')


def segment_seconds(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid model segment length')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float, default=25)
    args = parser.parse_args()
    duration = window(args.start, args.end)
    if args.output.exists() or args.samples.exists():
        raise FileExistsError('Diagnostic output already exists')
    checkpoint, digest = validate_checkpoint(args.repo)
    # Old Demucs checkpoints serialize a model class. Only the official hashed
    # checkpoint is permitted, inside a networkless container with no secrets.
    os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD'] = '1'
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    torch.set_num_threads(6)
    torch.manual_seed(0)
    started = time.monotonic()
    model = get_model('955717e8', repo=args.repo).cpu().eval()
    load_seconds = time.monotonic() - started
    vocal_index = validate_sources(model.sources)
    base = ['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(args.start), '-i', str(args.media),
            '-t', str(duration), '-map', '0:a:0', '-vn']
    raw = subprocess.check_output(base + ['-ar', str(model.samplerate), '-ac', str(model.audio_channels),
                                         '-f', 'f32le', 'pipe:1'])
    values = np.frombuffer(raw, dtype='<f4').reshape(-1, model.audio_channels).copy()
    if abs(len(values) - round(duration * model.samplerate)) > 1 or not np.isfinite(values).all():
        raise ValueError('Invalid or truncated input audio')
    wav = torch.from_numpy(values.T.copy())
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std()
    if std <= 0:
        raise ValueError('Silent input; separation unnecessary')
    started = time.monotonic()
    with torch.inference_mode():
        sources = apply_model(model, ((wav - mean) / std)[None], device='cpu',
                              shifts=0, split=True, overlap=0.25, progress=True,
                              num_workers=0)[0]
    vocals = (sources[vocal_index] * std + mean).T.numpy()
    if vocals.shape != values.shape or not np.isfinite(vocals).all():
        raise ValueError('Invalid separated output')
    separation_seconds = time.monotonic() - started
    args.samples.mkdir(parents=True, exist_ok=False)
    # FLOAT avoids limiting before the explicit, recorded gain below.
    stereo_vocals = args.samples / 'vocals-stereo.wav'
    sf.write(str(stereo_vocals), vocals, model.samplerate, subtype='FLOAT')
    gain = min(1.0, 0.95 / max(float(np.max(np.abs(vocals))), 1e-12))
    baseline = args.samples / 'mono.wav'
    separated = args.samples / 'vocals.wav'
    subprocess.run(base + ['-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-n', str(baseline)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(stereo_vocals),
                    '-af', f'volume={gain}', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
                    '-n', str(separated)], check=True)
    entries = []
    for name, path in [('mono', baseline), ('vocals', separated)]:
        stats = wav_stats(path)
        if abs(stats['frames'] - round(duration * 16000)) > 1:
            raise ValueError('Output duration changed')
        entries.append({'name': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'frames': stats['frames'], 'audio_stats': stats})
    result = {'source': str(args.media), 'start': args.start, 'end': args.end,
              'model': 'htdemucs / 955717e8', 'checkpoint': str(checkpoint), 'sha256': digest,
              'versions': {'demucs': version('demucs'), 'torch': torch.__version__},
              'device': 'cpu', 'shifts': 0, 'overlap': 0.25, 'model_segment': segment_seconds(model.segment),
              'model_load_seconds': round(load_seconds, 2),
              'separation_seconds': round(separation_seconds, 2), 'vocals_gain': gain,
              'sources': model.sources, 'variants': entries,
              'warning': 'Music-trained vocals stem is not verified dialogue; no ASR truth or timings inferred.'}
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    for path in [args.samples / 'manifest.json', args.output]:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(payload)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
