"""Run the production ASR pipeline on a bounded opening without creating a job or SRT."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import worker


def main():
    media = Path(os.environ['SUBSYNQ_MEDIA'])
    output = Path(os.environ['SUBSYNQ_OUTPUT'])
    seconds = max(30, min(600, int(os.environ.get('SUBSYNQ_SECONDS', '360'))))
    worker.update_job = lambda *_args, **_kwargs: None
    worker.check_cancelled = lambda *_args, **_kwargs: None
    with tempfile.TemporaryDirectory(prefix='subsynq-opening-') as folder:
        clip = Path(folder) / 'opening.wav'
        subprocess.run([
            'ffmpeg', '-v', 'error', '-i', str(media), '-t', str(seconds), '-map', '0:a:0',
            '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-y', str(clip),
        ], check=True, timeout=120)
        model = worker.WhisperModel('large-v3', device='cpu', compute_type='int8',
                                    cpu_threads=6, download_root='/models',
                                    local_files_only=True)
        rows = worker.transcribe({'id': 0}, clip, model, 'cpu')
    payload = {'version': '0.8.37', 'seconds': seconds, 'rows': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, output)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    main()
