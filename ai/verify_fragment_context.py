"""Probe repaired fragments and a valid continuation without subtitle writes."""
import argparse
import json
from pathlib import Path
import worker
from fragment_context_review import review_fragments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('quality', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    saved = json.loads(args.quality.read_text())
    flags = [flag for review in saved['reviews'] for flag in review.get('flags', [])]
    worker.MODEL_JOB.device = 'cpu'
    result = {}
    try:
        result['actual'] = review_fragments(saved['rows'], flags, worker.call_ollama, 'gemma4:12b')
        good = [{'id': 0, 'start': 1, 'end': 2, 'sv': 'Jag stannar och'},
                {'id': 1, 'start': 2, 'end': 3, 'sv': 'lyssnar på dig.'}]
        result['valid_continuation'] = review_fragments(good,
            [{'id': 0, 'category': 'fragment'}], worker.call_ollama, 'gemma4:12b')
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        worker.call_ollama({'model': 'gemma4:12b', 'keep_alive': 0})
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
