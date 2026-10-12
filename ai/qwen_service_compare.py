"""Run a bounded comparison through the same Qwen service used by the worker."""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
import sys

sys.path.insert(0, '/app')
from qwen_quality import alignment_quality


sample_dir = Path(os.environ['SUBSYNQ_SAMPLE_DIR'])
service_url = os.environ.get('SUBSYNQ_QWEN_ASR_URL', 'http://qwen-asr:8091').rstrip('/')
output = sample_dir / os.environ.get('SUBSYNQ_QWEN_OUTPUT', 'qwen-timed.json')
results = []
samples = tuple(name.strip() for name in os.environ.get(
    'SUBSYNQ_QWEN_SAMPLES', 'mono,center').split(',') if name.strip())
language = os.environ.get('SUBSYNQ_QWEN_LANGUAGE')

for name in samples:
    audio = sample_dir / f'{name}.wav'
    payload = audio.read_bytes()
    request = urllib.request.Request(
        f'{service_url}/transcribe', data=payload,
        headers={'Content-Type': 'audio/wav', 'Content-Length': str(len(payload)),
                 **({'X-SubSynq-Language': language} if language else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=1200) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode('utf-8', errors='replace')) from error
    results.append({'sample': name, **result,
                    'alignment_quality': alignment_quality(result.get('items', []))})
    print(json.dumps(results[-1], ensure_ascii=False), flush=True)

temporary = output.with_suffix('.tmp')
temporary.write_text(json.dumps({'runs': results}, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, output)
