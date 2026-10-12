"""Isolated source review of saved rows; never writes subtitles or jobs."""
import argparse
import json
import threading
import time
from pathlib import Path
import worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    saved = json.loads(args.source.read_text())
    rows = [{**r, 'text': r['source']} for r in saved['rows']]
    result = {'source': str(args.source), 'calls': [], 'memory': []}
    real_call = worker.call_ollama
    def record(request):
        call = {'request': request, 'start': time.time()}
        result['calls'].append(call)
        try:
            answer = real_call(request)
            call['answer'] = answer
            return answer
        except Exception as error:
            call['error'] = str(error)
            raise
        finally:
            call['seconds'] = time.time() - call['start']
    worker.call_ollama = record
    worker.MODEL_JOB.device = 'cpu'
    stop = threading.Event()
    def watch():
        while not stop.is_set():
            values = {}
            for line in Path('/proc/meminfo').read_text().splitlines():
                name, rest = line.split(':', 1)
                if name in {'MemAvailable', 'SwapFree', 'SwapTotal'}:
                    values[name] = int(rest.strip().split()[0])
            result['memory'].append({'time': time.time(), **values})
            stop.wait(3)
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        worker.assess_source_quality(rows, Path(saved['media_path']).name)
        result['status'] = 'source_review_passed_not_audio_verified'
    except worker.SourceQualityError as error:
        result.update(status='quality_failed', reason=str(error))
    except Exception as error:
        result.update(status='technical_error', reason=str(error))
    finally:
        try:
            real_call({'model': worker.MODEL_NAME, 'keep_alive': 0})
        finally:
            stop.set()
            thread.join()
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'status': result['status'], 'reason': result.get('reason'),
        'calls': len(result['calls']), 'memory_samples': len(result['memory'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
