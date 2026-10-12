"""Read-only source audit comparisons; no production verdict, DB or SRT."""
import argparse
import json
import time
from pathlib import Path

import worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--controls', action='store_true')
    args = parser.parse_args()
    saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
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
