"""Independent audio readings of any saved source report; no production writes.

Mount the report, media and existing models read-only. Scratch audio is in /tmp.
These readings are comparisons, never automatic source replacements or approval.
"""
import argparse
import gc
import json
import math
import subprocess
import tempfile
import time
from pathlib import Path

import worker


def source_window(rows, start=None, end=None):
    if not rows:
        raise ValueError('Källrapporten saknar rader.')
    start = max(0.0, min(float(row['start']) for row in rows) - 2) if start is None else start
    end = max(float(row['end']) for row in rows) + 2 if end is None else end
    if not all(math.isfinite(value) for value in (start, end)) or start < 0 or not 0 < end - start <= 60:
        raise ValueError('Diagnostik kräver ett giltigt ljudfönster på högst 60 sekunder.')
    return start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--media', required=True)
    parser.add_argument('--start', type=float)
    parser.add_argument('--end', type=float)
    parser.add_argument('--models', nargs='+', choices=['qwen', 'large-v2', 'large-v3'],
                        default=['qwen', 'large-v2'])
    parser.add_argument('--language', help='Optional explicit Whisper language code; Qwen still auto-detects.')
    args = parser.parse_args()
    saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
    start, end = source_window(saved['rows'], args.start, args.end)
    print(json.dumps({'event': 'source_comparison', 'start': start, 'end': end,
                      'saved_rows': saved['rows'], 'NOT_a_quality_verdict': True}, ensure_ascii=False), flush=True)
    with tempfile.TemporaryDirectory(prefix='subsynq-source-probe-') as folder:
        audio = Path(folder) / 'audio.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', args.media,
                        '-t', str(end - start), '-map', '0:a:0', '-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', '-y', str(audio)], check=True, timeout=90)
        for name in args.models:
            print(json.dumps({'event': 'reading_started', 'model': name}), flush=True)
            began = time.monotonic()
            if name == 'qwen':
                try:
                    worker.wait_for_qwen()
                    reading = worker.qwen_identify(audio, attempts=1)
                    print(json.dumps({'model': name, 'seconds': time.monotonic() - began,
                                      **reading}, ensure_ascii=False), flush=True)
                finally:
                    worker.qwen_unload(attempts=1)
                continue
            model = worker.WhisperModel(name, device='cpu', compute_type='int8', cpu_threads=6,
                                        download_root='/models', local_files_only=True)
            try:
                segments, info = model.transcribe(str(audio), language=args.language, beam_size=5,
                    temperature=0, condition_on_previous_text=False, word_timestamps=True,
                    vad_filter=False, hallucination_silence_threshold=2)
                rows = [{'start': start + segment.start, 'end': start + segment.end,
                         'text': segment.text.strip(), 'avg_logprob': segment.avg_logprob,
                         'words': [{'start': start + word.start, 'end': start + word.end,
                                    'word': word.word, 'probability': word.probability}
                                   for word in (segment.words or [])]}
                        for segment in segments]
                print(json.dumps({'model': name, 'seconds': time.monotonic() - began,
                                  'language': info.language, 'rows': rows,
                                  'text': ' '.join(row['text'] for row in rows)},
                                 ensure_ascii=False), flush=True)
            finally:
                del model
                gc.collect()


if __name__ == '__main__':
    main()
