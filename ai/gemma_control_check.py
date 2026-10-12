"""Small model controls, never reads media or writes production reports."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import worker


def run_controls():
    rows = [
        {'id': 0, 'start': 0, 'end': 2, 'source': 'This is my book.', 'sv': 'Det här är mitt bok.'},
        {'id': 1, 'start': 3, 'end': 5, 'source': 'I am coming to the house.', 'sv': 'Till huset kommer jag.'},
        {'id': 2, 'start': 6, 'end': 7, 'source': 'ありがとう。', 'sv': 'Tack.'},
    ]
    flag = {'id': 0, 'category': 'grammar', 'reason': 'Fel böjning av possessivt pronomen.',
            'source_quote': rows[0]['source'], 'target_quote': rows[0]['sv']}
    if '--audit-only' in sys.argv:
        rows[0]['sv'] = 'Det här är min bok.'
        rows.extend([
            {'id': 3, 'start': 8, 'end': 10, 'source': 'मैं वापस आऊंगा।', 'sv': 'Jag kommer tillbaka.'},
            {'id': 4, 'start': 11, 'end': 12, 'source': 'நன்றி', 'sv': 'Tack.'},
        ])
        audit = []
        started = time.monotonic()
        worker.assess_translation_quality(rows, audit=audit)
        print(json.dumps({'mode': 'clean multilingual audit control',
                          'seconds': time.monotonic() - started, 'reviews': audit},
                         ensure_ascii=False), flush=True)
        return
    if '--baseline' in sys.argv:
        started = time.monotonic()
        with patch.object(worker, 'check_cancelled'):
            result = worker.repair_flagged_translation({'id': -1}, rows, [flag])
        print(json.dumps({'mode': 'current targeted repair', 'seconds': time.monotonic() - started,
                          'rows': result}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    with patch.object(worker, 'check_cancelled'):
        result = worker.repair_target_grammar({'id': -1}, rows, {row['id'] for row in rows})
    print(json.dumps({'mode': 'target-only grammar', 'seconds': time.monotonic() - started,
                      'rows': result}, ensure_ascii=False), flush=True)
    expected = ['Det här är min bok.', 'Till huset kommer jag.', 'Tack.']
    if [row['sv'] for row in result] != expected:
        raise RuntimeError('Model control did not meet the expected grammar/style result.')
    print('Grammar, inverted word order and brief-answer controls: PASS', flush=True)
    if '--saved' in sys.argv:
        path = Path(sys.argv[sys.argv.index('--saved') + 1])
        saved = json.loads(path.read_text(encoding='utf-8'))['rows']
        started = time.monotonic()
        with patch.object(worker, 'check_cancelled'):
            result = worker.repair_target_grammar({'id': -1}, saved,
                                                  {row['id'] for row in saved})
        print(json.dumps({'mode': 'saved candidate grammar ONLY, not quality approval',
                          'seconds': time.monotonic() - started, 'rows': result},
                         ensure_ascii=False), flush=True)


def main():
    try:
        run_controls()
    finally:
        try:
            worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})
        except Exception as error:
            print(f'Model cleanup failed: {error}', file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
