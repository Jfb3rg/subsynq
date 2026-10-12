"""Check complete offline model files before publishing or starting a rental."""
import json
from pathlib import Path


def check_models(root=Path('/models')):
    root = Path(root)
    for name in ('large-v3', 'large-v2'):
        repo = root / f'models--Systran--faster-whisper-{name}'
        revision = (repo / 'refs/main').read_text().strip()
        snapshot = repo / 'snapshots' / revision
        for filename in ('model.bin', 'config.json', 'tokenizer.json'):
            if not (snapshot / filename).is_file() or (snapshot / filename).stat().st_size == 0:
                raise RuntimeError(f'Ofullständig Whisper-cache: {name}/{filename}')
    for name in ('Qwen3-ASR-1.7B', 'Qwen3-ForcedAligner-0.6B'):
        repo = root / 'huggingface/hub' / f'models--Qwen--{name}'
        revision = (repo / 'refs/main').read_text().strip()
        snapshot = repo / 'snapshots' / revision
        config = snapshot / 'config.json'
        weights = list(snapshot.glob('*.safetensors'))
        if not config.is_file() or not weights or any(p.stat().st_size == 0 for p in weights):
            raise RuntimeError(f'Ofullständig Qwen-cache: {name}')
        for index in snapshot.glob('*.safetensors.index.json'):
            shards = set(json.loads(index.read_text())['weight_map'].values())
            if any(not (snapshot / shard).is_file() for shard in shards):
                raise RuntimeError(f'Saknad Qwen-viktfil: {name}')
    for name in ('gemma3', 'gemma4'):
        models = root / 'ollama/models'
        manifest = json.loads((models / 'manifests/registry.ollama.ai/library' / name / '12b').read_text())
        for layer in [manifest['config'], *manifest['layers']]:
            blob = models / 'blobs' / layer['digest'].replace(':', '-')
            if not blob.is_file() or blob.stat().st_size != layer['size']:
                raise RuntimeError(f'Ofullständig Ollama-cache: {name}')
    return True


if __name__ == '__main__':
    check_models()
    print('Offline-modeller kompletta: Whisper v3/v2, Qwen ASR/aligner, Gemma3/4 12B', flush=True)
