"""Replay saved source through translation and QA; never write SRT or update jobs."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import worker


def main():
    review_only = '--review-only' in sys.argv
    arguments = [arg for arg in sys.argv[1:] if arg != '--review-only']
    span = None
    label = None
    if '--label' in arguments:
        position = arguments.index('--label')
        label = arguments[position + 1]
        if not label.replace('-', '').isalnum():
            raise ValueError('Invalid label')
        del arguments[position:position + 2]
    if '--slice' in arguments:
        if not review_only:
            raise ValueError('--slice requires --review-only')
        position = arguments.index('--slice')
        span = tuple(map(int, arguments[position + 1].split(':')))
        if len(span) != 2 or span[0] < 0 or span[1] <= span[0]:
            raise ValueError('Slice must be START:END with START < END')
        del arguments[position:position + 2]
    for job_id in map(int, arguments):
        source = json.loads((worker.SOURCE_TRANSCRIPTS / f'job-{job_id}.json').read_text(
            encoding='utf-8'))
        output = Path('/config/quality-replays') / f'job-{job_id}-context-qa.json'
        previous = output
        if review_only:
            output = output.with_name(f'job-{job_id}-context-qa-review.json')
        if span:
            output = output.with_name(f'job-{job_id}-context-qa-slice-{span[0]}-{span[1]}.json')
        elif label:
            output = output.with_name(f'job-{job_id}-context-qa-{label}.json')
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if review_only:
            rows = json.loads(previous.read_text(encoding='utf-8'))['rows']
            if span:
                rows = rows[span[0]:span[1]]
            print(f'Job {job_id}: reviewing existing candidate', flush=True)
        else:
            print(f'Job {job_id}: translating {len(source["rows"])} saved source rows', flush=True)
            with patch.object(worker, 'update_job'), patch.object(worker, 'check_cancelled'):
                rows = worker.translate({'id': -1}, source['rows'])
        payload = {'original_job': job_id, 'media_path': source['media_path'],
                   'note': 'New translation replay, not the original rejected Swedish text',
                   'rows': rows, 'reviews': [], 'status': 'pending'}
        # Persist candidate before review so a failure remains inspectable.
        with output.open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        try:
            worker.assess_translation_quality(rows, audit=payload['reviews'])
            payload['status'] = 'usable'
        except worker.SourceQualityError as error:
            payload['status'] = 'unusable'
            payload['reason'] = str(error)
        except Exception as error:
            payload.update(status='error', reason=str(error))
            raise
        finally:
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: value for key, value in payload.items() if key != 'rows'},
                         ensure_ascii=False), flush=True)
    try:
        worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})
    except RuntimeError as error:
        print(f'Model unload warning (saved reviews are complete): {error}', flush=True)


if __name__ == '__main__':
    main()
