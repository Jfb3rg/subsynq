"""Offline CPU reference for a local Hugging Face Whisper checkpoint."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import time

from audio_channel_probe import checked_samples, wav_stats


def generation_options(language):
    return {'language': language, 'task': 'transcribe', 'return_timestamps': False,
            'num_beams': 5, 'do_sample': False, 'max_new_tokens': 256, 'use_cache': True}


def weight_options(weight_format):
    if weight_format not in ('pytorch', 'safetensors'):
        raise ValueError('Unsupported local weight format')
    return {'use_safetensors': weight_format == 'safetensors', 'weights_only': True}


def checked_token_ids(token_ids):
    if not isinstance(token_ids, list) or any(
            isinstance(token, bool) or not isinstance(token, int) or token < 0
            for token in token_ids):
        raise ValueError('Expected finite non-negative integer token IDs')
    return token_ids


def legacy_generation_fields(vocabulary, language):
    tokens = [f'<|{language}|>', '<|transcribe|>', '<|translate|>', '<|notimestamps|>']
    if any(token not in vocabulary for token in tokens):
        raise ValueError('Missing required language/task tokens in local vocabulary')
    if len({vocabulary[token] for token in tokens}) != len(tokens):
        raise ValueError('Language/task token IDs collide')
    return {'lang_to_id': {tokens[0]: vocabulary[tokens[0]]},
            'task_to_id': {'transcribe': vocabulary[tokens[1]], 'translate': vocabulary[tokens[2]]},
            'no_timestamps_token_id': vocabulary[tokens[3]], 'is_multilingual': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--language', required=True)
    parser.add_argument('--weight-format', choices=('pytorch', 'safetensors'), default='pytorch')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = checked_samples(args.samples)
    import soundfile as sf
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    torch.set_num_threads(6)
    started = time.monotonic()
    processor = WhisperProcessor.from_pretrained(str(args.model), local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(str(args.model), local_files_only=True,
              **weight_options(args.weight_format), dtype=torch.float32,
              low_cpu_mem_usage=True).cpu().eval()
    compatibility = {}
    for key, value in legacy_generation_fields(processor.tokenizer.get_vocab(), args.language).items():
        if not hasattr(model.generation_config, key):
            setattr(model.generation_config, key, value)
            compatibility[key] = value
    options = generation_options(args.language)
    report = {'engine': 'transformers-whisper', 'model': str(args.model), 'device': 'cpu',
              'weight_format': args.weight_format,
              'dtype': 'float32', 'threads': 6, 'settings': options,
              'generation_config_fields_added_in_memory': compatibility,
              'versions': {'torch': torch.__version__, 'transformers': version('transformers')},
              'silence_control': manifest.get('silence_control', False),
              'start': manifest['start'], 'end': manifest['end'],
              'model_load_seconds': round(time.monotonic() - started, 2), 'runs': []}
    for entry in manifest['variants']:
        path = args.samples / (entry['name'] + '.wav')
        stats = wav_stats(path)
        audio, rate = sf.read(path, dtype='float32')
        if rate != 16000 or audio.ndim != 1:
            raise ValueError('Expected mono 16 kHz')
        inputs = processor(audio, sampling_rate=rate, return_tensors='pt', return_attention_mask=True)
        started = time.monotonic()
        with torch.inference_mode():
            ids = model.generate(**inputs, **options)
        token_ids = checked_token_ids(ids[0].tolist())
        run = {'variant': entry['name'], 'sha256': entry['sha256'], 'audio_stats': stats,
               'input_feature_shape': list(inputs.input_features.shape),
               'generated_tokens': len(token_ids), 'generated_token_ids': token_ids,
               'text': processor.batch_decode(ids, skip_special_tokens=True)[0].strip(),
               'seconds': round(time.monotonic() - started, 2)}
        report['runs'].append(run)
        print(json.dumps(run, ensure_ascii=False), flush=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(f'Saved {args.output}', flush=True)


if __name__ == '__main__':
    main()
