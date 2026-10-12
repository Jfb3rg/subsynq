"""Isolated audio-language/optional full pipeline probe; never writes an SRT."""
import argparse
import ctypes
import gc
import json
from pathlib import Path
import tempfile

from faster_whisper import WhisperModel
import worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    worker.update_job = lambda *a, **k: print(a[3] if len(a) > 3 else a, flush=True)
    worker.check_cancelled = lambda *_: None
    model = WhisperModel('large-v3', device='cpu', compute_type='int8', cpu_threads=6,
                         download_root='/models', local_files_only=True)
    job = {'id': -1}
    report = {'media': str(args.media), 'status': 'pending'}
    original_probe = worker.probe_filename_language

    def save_report():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def recorded_probe(*a, **kw):
        policy = original_probe(*a, **kw)
        report.update(language_policy=policy, stage='asr')
        save_report()
        print(json.dumps({'language_policy': policy}, ensure_ascii=False), flush=True)
        return policy

    worker.probe_filename_language = recorded_probe
    try:
        with tempfile.TemporaryDirectory() as folder:
            worker.QUALITY_REPORTS = Path(folder)
            if args.full:
                rows = worker.transcribe(job, args.media, model)
                del model
                gc.collect()
                ctypes.CDLL('libc.so.6').malloc_trim(0)
                report.update(language_policy=job['language_policy'], rows=rows, stage='source_review')
                # Persist source evidence even if a later model call fails.
                save_report()
                worker.assess_source_quality(rows, args.media.name,
                    use_filename_hint=job['language_policy']['status'] == 'confirmed')
                report['stage'] = 'translation'
                save_report()
                translated = worker.translate(job, rows)
                report['translated'] = translated
                report['stage'] = 'final_review'
                save_report()
                try:
                    worker.review_and_save_translation(job, translated)
                finally:
                    quality_path = Path(folder) / 'job--1.json'
                    if quality_path.exists():
                        report['quality_report'] = json.loads(quality_path.read_text(encoding='utf-8'))
                report['status'] = 'usable'
            else:
                worker.wait_for_qwen(job)
                report['language_policy'] = worker.probe_filename_language(
                    job, args.media, model, worker.duration(args.media), folder)
                report['status'] = 'probed'
    except worker.SourceQualityError as error:
        report.update(status='quality_failed', error=str(error))
    except Exception as error:
        report.update(status='error', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        if job.get('language_policy'):
            report['language_policy'] = job['language_policy']
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
