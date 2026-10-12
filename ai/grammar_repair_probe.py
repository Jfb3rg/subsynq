"""Isolated comparison of grammar repair prompts; no production data writes."""
import json
import sys
import time
from unittest.mock import patch
from pathlib import Path

import worker


def main():
    texts = [
        'Det här är mitt bok.',
        'Det här är mitt berättelse.',
        'Havet är där jag kommer att ge mitt berättelse.',
        'Till huset kommer jag.',
        'Det här är min berättelse.',
        'Jag berättar inte min historia.',
    ]
    rows = [{'id': index, 'sv': text} for index, text in enumerate(texts)]
    flags = [{'id': index, 'category': 'grammar', 'target_quote': texts[index],
              'reason': 'The possessive pronoun disagrees with the grammatical gender of the noun.'}
             for index in range(3)] if '--with-evidence' in sys.argv else []
    expected = [text.replace('mitt bok', 'min bok').replace('mitt berättelse', 'min berättelse')
                for text in texts]
    ids = set(range(len(rows)))
    if '--saved' in sys.argv:
        def argument(name):
            return sys.argv[sys.argv.index(name) + 1]
        rows = json.loads(Path(argument('--saved')).read_text(encoding='utf-8'))['rows']
        selected = int(argument('--grammar-id'))
        quote = argument('--quote')
        replacement = argument('--replacement')
        reason = argument('--reason')
        ids = {selected}
        flags = [{'id': selected, 'category': 'grammar', 'target_quote': quote, 'reason': reason}]
        expected = [row['sv'].replace(quote, replacement, 1) if row['id'] == selected else row['sv']
                    for row in rows]
        assert any(row['id'] == selected and quote in row['sv'] for row in rows)
    original_call = worker.call_ollama

    def call(request):
        if '--with-context' in sys.argv:
            request = dict(request)
            prompt = json.loads(request['prompt'])
            positions = {index for index, row in enumerate(rows) if row['id'] in ids}
            neighbors = {neighbor for index in positions for neighbor in (index - 1, index, index + 1)
                         if 0 <= neighbor < len(rows)}
            prompt['context'] = [{'id': rows[index]['id'], 'sv': rows[index]['sv']}
                                 for index in sorted(neighbors)]
            request['prompt'] = json.dumps(prompt, ensure_ascii=False)
        result = original_call(request)
        print(json.dumps({'raw_model_response': result.get('response')}, ensure_ascii=False), flush=True)
        return result

    started = time.monotonic()
    try:
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'call_ollama', side_effect=call):
            result = worker.repair_target_grammar({'id': -1}, rows, ids, flags=flags)
        passed = [row['sv'] for row in result] == expected
        print(json.dumps({'with_evidence': bool(flags), 'saved_candidate': '--saved' in sys.argv,
                          'context_supplied': '--with-context' in sys.argv,
                          'seconds': time.monotonic() - started, 'rows': result,
                          'passed': passed},
                         ensure_ascii=False), flush=True)
        if not passed:
            raise RuntimeError('Grammar comparison did not meet the expected result.')
    finally:
        original_call({'model': worker.MODEL_NAME, 'keep_alive': 0})


if __name__ == '__main__':
    main()
