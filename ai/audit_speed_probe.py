"""Bounded, isolated timing of audit requests; never a quality approval."""
import argparse
import copy
import json
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import worker


class CapturedRequest(BaseException):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('--modes', nargs='+', choices=['bounded', 'unbounded', 'small-context'],
                        default=['bounded', 'unbounded'])
    parser.add_argument('--tokens', type=int, default=96)
    parser.add_argument('--generation-seconds', type=float, default=90)
    parser.add_argument('--lifecycle', action='store_true')
    args = parser.parse_args()
    rows = json.loads(Path(args.input).read_text(encoding='utf-8'))['rows']
    captured = []

    def capture(request):
        captured.append(copy.deepcopy(request))
        raise CapturedRequest()

    try:
        with patch.object(worker, 'call_ollama', side_effect=capture):
            worker.assess_translation_quality(rows)
    except CapturedRequest:
        pass
    assert captured, 'No model request was produced.'

    if args.lifecycle:
        original_open = worker.urllib.request.urlopen

        def bounded_open(request, **kwargs):
            kwargs['timeout'] = min(kwargs.get('timeout', 300), 300)
            return original_open(request, **kwargs)

        with patch.object(worker, 'check_cancelled'), \
                patch.object(worker.urllib.request, 'urlopen', side_effect=bounded_open):
            worker.MODEL_JOB.job_id = -1
            worker.MODEL_JOB.used = False
            try:
                for index in range(2):
                    request = copy.deepcopy(captured[0])
                    request['options']['num_predict'] = args.tokens
                    worker.MODEL_JOB.phase = f'diagnostic_pass_{index + 1}'
                    worker.call_ollama(request)
            finally:
                worker.finish_model_job(-1)
        return

    def drop_length_limits(value):
        if isinstance(value, dict):
            return {key: drop_length_limits(item) for key, item in value.items() if key != 'maxLength'}
        if isinstance(value, list):
            return [drop_length_limits(item) for item in value]
        return value

    try:
        for mode in args.modes:
            request = copy.deepcopy(captured[0])
            request['options']['num_predict'] = args.tokens
            request['stream'] = True
            if mode != 'bounded':
                request['format'] = drop_length_limits(request['format'])
            if mode == 'small-context':
                request['options']['num_ctx'] = 4096
            print(json.dumps({'mode': mode, 'event': 'start', 'max_generated_tokens': args.tokens}), flush=True)
            started = time.monotonic()
            http_request = urllib.request.Request(worker.TRANSLATE_URL,
                data=json.dumps(request, ensure_ascii=False).encode(),
                headers={'Content-Type': 'application/json'})
            first_chunk = None
            pieces = []
            result = {}
            stopped = False
            with urllib.request.urlopen(http_request, timeout=300) as response:
                for line in response:
                    chunk = json.loads(line)
                    if chunk.get('response'):
                        if first_chunk is None:
                            first_chunk = time.monotonic()
                        pieces.append(chunk['response'])
                        elapsed = time.monotonic() - first_chunk
                        if len(pieces) % 16 == 0:
                            print(json.dumps({'mode': mode, 'event': 'progress',
                                'response_chunks': len(pieces), 'generation_seconds': elapsed}), flush=True)
                        if elapsed >= args.generation_seconds and not chunk.get('done'):
                            stopped = True
                            break
                    if chunk.get('done'):
                        result = chunk
                        break
            count, duration = result.get('eval_count', 0), result.get('eval_duration', 0)
            print(json.dumps({'mode': mode, 'seconds': time.monotonic() - started,
                'tokens_per_second': count * 1e9 / duration if duration else None,
                'load_seconds': result.get('load_duration', 0) / 1e9,
                'prompt_seconds': result.get('prompt_eval_duration', 0) / 1e9,
                'eval_count': count, 'done_reason': result.get('done_reason'),
                'stopped_by_diagnostic_limit': stopped,
                'partial_response_NOT_a_verdict': ''.join(pieces)}, ensure_ascii=False), flush=True)
    finally:
        worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})


if __name__ == '__main__':
    main()
