"""Replay a saved candidate with current rules, without production writes or SRT.

Run in a separate container with the input report mounted read-only. All new
reports and the minimal cancellation-check database live only in /tmp.
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import worker


def main():
    saved = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    rows = [dict(row) for row in saved['rows']]
    original_call = worker.call_ollama
    calls = 0

    def timed_call(request):
        nonlocal calls
        calls += 1
        started = time.monotonic()
        print(f'Model request {calls} started.', flush=True)
        answer = original_call(request)
        print(f'Model request {calls} completed in {time.monotonic() - started:.1f}s.', flush=True)
        return answer

    worker.call_ollama = timed_call
    with tempfile.TemporaryDirectory() as folder:
        worker.QUALITY_REPORTS = Path(folder)
        worker.DATABASE = Path(folder) / 'diagnostic.db'
        with sqlite3.connect(worker.DATABASE) as db:
            db.execute('CREATE TABLE subtitle_jobs (id INTEGER, status TEXT)')
        print(f'Replaying {len(rows)} saved rows; no ASR or SRT writing.', flush=True)
        if '--audit-only' in sys.argv[2:]:
            report = {'rows': rows, 'reviews': [], 'status': 'pending'}
            try:
                worker.assess_translation_quality(rows, audit=report['reviews'])
                report['status'] = 'usable'
            except worker.SourceQualityError as error:
                report.update(status='unusable', reason=str(error))
            except RuntimeError as error:
                report.update(status='error', reason=str(error))
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return
        try:
            worker.review_and_save_translation({'id': -1}, rows)
        except (worker.SourceQualityError, RuntimeError) as error:
            print(f'Diagnostic result: {error}', flush=True)
        report = json.loads((Path(folder) / 'job--1.json').read_text())
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        try:
            worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})
        except RuntimeError as error:
            print(f'Model-unload warning (after saved result): {error}', flush=True)


if __name__ == '__main__':
    main()
