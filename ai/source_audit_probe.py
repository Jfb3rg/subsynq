"""Read-only source audit comparisons; no production verdict, DB or SRT."""
import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


def reread_source(saved, *, ids=None, qwen_url=None):
    """Run the existing source reread on RO inputs; progress/DB calls are mocked."""
    if qwen_url:
        worker.QWEN_ASR_URL = qwen_url.rstrip('/')
        worker.QWEN_ASR_ENABLED = True
    job = {'id': -100000 - int(saved['job_id']), 'media_path': saved['media_path'],
           'audio_choice': saved['audio_track'], 'audio_stream': saved['audio_track']['index'],
           'language_policy': saved.get('language_policy') or {},
           'source_choices': saved.get('source_choices') or []}
    worker.MODEL_JOB.device = 'cpu'
    worker.MODEL_JOB.job_id = job['id']
    worker.MODEL_JOB.phase = 'source_review'
    rows = [dict(row) for row in saved['rows']]
    result = {'source_job': saved['job_id'], 'rows': rows, 'job': job, 'NOT_a_quality_verdict': True}
    started = time.monotonic()
    try:
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            if ids is None:
                result['still_unclear'] = worker.assess_source_with_reread(job, rows)
            else:
                if not set(ids) <= {row['id'] for row in rows}:
                    raise ValueError('Omläsningen kräver ID:n som finns i källrapporten.')
                result['changed'] = worker.reread_flagged_rows(job, rows, ids)
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['seconds'] = time.monotonic() - started
        Path('/results/source-reread.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--controls', action='store_true')
    parser.add_argument('--reread', action='store_true', help='Source audit and acoustic reread, output only in /results.')
    parser.add_argument('--ids', nargs='+', type=int, help='Explicit saved row IDs for an acoustic diagnostic, without source audit.')
    parser.add_argument('--qwen-url', help='Existing Dev Qwen endpoint; no new models or service.')
    args = parser.parse_args()
    saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
    if args.reread or args.ids is not None:
        if args.controls:
            parser.error('--controls cannot be combined with acoustic rereading')
        reread_source(saved, ids=args.ids, qwen_url=args.qwen_url)
        return
    samples = [('saved_source', saved['rows'])]
    if args.controls:
        # Synthetic, complete control sentences, not replacements for real audio.
        for name, texts in [
            ('hindi_control', ['मैं घर जा रहा हूँ।', 'वह कल आएगा।', 'यह मेरी कहानी है।']),
            ('japanese_control', ['ありがとうございます。', '明日、家に帰ります。', 'これは私の本です。']),
            ('tamil_control', ['நன்றி.', 'நான் வீட்டுக்குச் செல்கிறேன்.', 'இது என் புத்தகம்.'])]:
            samples.append((name, [{'id': index, 'source': text} for index, text in enumerate(texts)]))
    try:
        for name, rows in samples:
            print(json.dumps({'event': 'source_audit_started', 'sample': name}), flush=True)
            started = time.monotonic()
            bad_ids, mismatch, reason = worker._audit_source_chunk(rows, '')
            print(json.dumps({'sample': name, 'seconds': time.monotonic() - started,
                              'bad_ids': bad_ids, 'language_mismatch': mismatch, 'reason': reason,
                              'NOT_a_quality_verdict': True}, ensure_ascii=False), flush=True)
            if name.endswith('_control') and (bad_ids or mismatch):
                raise RuntimeError(f'Tydligt kontrollmaterial underkändes: {name}')
    finally:
        worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})


if __name__ == '__main__':
    main()
