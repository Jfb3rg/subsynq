"""Read-only live Qwen checks; audio scratch files exist only in /tmp.

Does not queue jobs, write SRTs, or alter the production database.
"""
import json
import subprocess
import tempfile
from pathlib import Path

import worker


def main():
    worker.wait_for_qwen()
    worker.qwen_unload()
    print('Qwen readiness and unload: OK', flush=True)
    with tempfile.TemporaryDirectory() as directory:
        for prefix, start in [('HOPE ', 5), ('KING -', 20)]:
            matches = sorted(Path('/extra').glob(prefix + '*.mp4'))
            if len(matches) != 1:
                raise RuntimeError(f'Expected one {prefix} file, found {len(matches)}')
            audio = Path(directory) / 'probe.wav'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error',
                            '-ss', str(start), '-i', str(matches[0]), '-t', '8',
                            '-vn', '-ac', '1', '-ar', '16000', '-y', str(audio)],
                           check=True)
            for run in ('cold' if prefix.startswith('HOPE') else 'warm', 'repeat'):
                result = worker.qwen_identify(audio)
                print(json.dumps({'file': matches[0].name, 'run': run,
                                  'language': result['language'],
                                  'text': result['text']}, ensure_ascii=False), flush=True)
        worker.qwen_unload()
        print('Final Qwen unload: OK', flush=True)


if __name__ == '__main__':
    main()
