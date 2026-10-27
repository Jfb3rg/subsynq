"""Independent audio readings of any saved source report; no production writes.

Mount the report, media and existing models read-only. Scratch audio is in /tmp.
These readings are comparisons, never automatic source replacements or approval.
"""
import argparse
import gc
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


def source_audio_map(saved, explicit_index=None):
    """Use the job's actual ffprobe stream, including non-first originals."""
    track = saved.get('audio_track') or {}
    index = explicit_index if explicit_index is not None else track.get('index')
    if index is None:
        raise ValueError('Källrapporten saknar valt ljudspår; ange --audio-track.')
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError('Ljudspåret måste vara ett icke-negativt ffprobe-index.')
    return f'0:{index}'


def source_window(rows, start=None, end=None):
    if not rows:
        raise ValueError('Källrapporten saknar rader.')
    start = max(0.0, min(float(row['start']) for row in rows) - 2) if start is None else start
    end = max(float(row['end']) for row in rows) + 2 if end is None else end
    if not all(math.isfinite(value) for value in (start, end)) or start < 0 or not 0 < end - start <= 60:
        raise ValueError('Diagnostik kräver ett giltigt ljudfönster på högst 60 sekunder.')
    return start, end


def pipeline_source(saved, media):
    """Run only the real CPU ASR path, with no job/database/SRT writes."""
    job_id = -100000 - int(saved['job_id'])
    job = {'id': job_id, 'media_path': str(media), 'audio_stream': saved['audio_track']['index'],
           'audio_choice': saved['audio_track']}
    worker.MODEL_JOB.job_id = job_id
    worker.MODEL_JOB.used = False
    worker.MODEL_JOB.active_model = None
    worker.MODEL_JOB.cleanup_pending = None
    worker.MODEL_JOB.device = 'cpu'
    worker.MODEL_JOB.phase = 'asr'
    worker.V2_FRAGMENT_ENABLED = True
    worker.os.environ['SUBSYNQ_ASR_TRACE_OUTPUT'] = '/results/asr-trace.json'
    began = time.monotonic()
    def progress(*args, **kwargs):
        print(json.dumps({'progress': args[:4]}, ensure_ascii=False), flush=True)
    try:
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job', progress):
            worker.prepare_asr_job()
            model = worker.WhisperModel('large-v3', device='cpu', compute_type='int8', cpu_threads=6,
                                        download_root='/models', local_files_only=True)
            rows = worker.transcribe_and_release(job, media, model, 'cpu')
            worker.validate_episode_segments(rows)
        payload = dict(job_id=job_id, source_job=saved['job_id'], seconds=time.monotonic() - began,
                       rows=rows, job=job, NOT_a_quality_verdict=True)
        Path('/results/pipeline-source.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(json.dumps({'source_rows': len(rows), 'seconds': payload['seconds']}), flush=True)
    finally:
        worker.finish_model_job(job_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--media', required=True)
    parser.add_argument('--start', type=float)
    parser.add_argument('--end', type=float)
    parser.add_argument('--models', nargs='+', choices=['qwen', 'large-v2', 'large-v3'],
                        default=['qwen', 'large-v2'])
    parser.add_argument('--language', help='Optional explicit Whisper language code; Qwen still auto-detects.')
    parser.add_argument('--audio-track', type=int, help='ffprobe stream index; defaults to the saved job selection.')
    parser.add_argument('--qwen-url', help='Existing Qwen service URL for an isolated diagnostic container.')
    parser.add_argument('--dialogue-only', action='store_true', help='Diagnostic FFmpeg stereo dialogue extraction; never changes original media.')
    parser.add_argument('--pipeline-source', action='store_true', help='Full CPU ASR only; results in /results, never a real job or SRT.')
    args = parser.parse_args()
    saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
    if args.qwen_url:
        worker.QWEN_ASR_URL = args.qwen_url.rstrip('/')
        worker.QWEN_ASR_ENABLED = True
    if args.pipeline_source:
        if args.start is not None or args.end is not None or args.dialogue_only:
            parser.error('--pipeline-source reads the full source, without --start/--end')
        source_audio_map(saved, args.audio_track)
        if args.audio_track is not None:
            saved['audio_track'] = {**saved['audio_track'], 'index': args.audio_track}
        pipeline_source(saved, Path(args.media))
        return
    start, end = source_window(saved['rows'], args.start, args.end)
    audio_map = source_audio_map(saved, args.audio_track)
    audio_filter = 'dialoguenhance=original=0:enhance=1:voice=2,pan=mono|c0=c2' if args.dialogue_only else None
    print(json.dumps({'event': 'source_comparison', 'start': start, 'end': end, 'audio_filter': audio_filter,
                      'audio_map': audio_map,
                      'saved_rows': saved['rows'], 'NOT_a_quality_verdict': True}, ensure_ascii=False), flush=True)
    with tempfile.TemporaryDirectory(prefix='subsynq-source-probe-') as folder:
        audio = Path(folder) / 'audio.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', args.media,
                        '-t', str(end - start), '-map', audio_map] + (['-af', audio_filter] if audio_filter else []) + ['-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', '-y', str(audio)], check=True, timeout=90)
        for name in args.models:
            print(json.dumps({'event': 'reading_started', 'model': name}), flush=True)
            began = time.monotonic()
            if name == 'qwen':
                ready = False
                try:
                    worker.wait_for_qwen(attempts=1)
                    ready = True
                    reading = worker.qwen_identify(audio, attempts=1)
                    print(json.dumps({'model': name, 'seconds': time.monotonic() - began,
                                      **reading}, ensure_ascii=False), flush=True)
                finally:
                    if ready:
                        worker.qwen_unload(attempts=1)
                continue
            model = worker.WhisperModel(name, device='cpu', compute_type='int8', cpu_threads=6,
                                        download_root='/models', local_files_only=True)
            try:
                segments, info = model.transcribe(str(audio), language=args.language, beam_size=5,
                    multilingual=args.language is None,
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
