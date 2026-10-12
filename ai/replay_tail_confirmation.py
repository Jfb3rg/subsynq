"""Replay saved tail evidence without ASR, DB or subtitle writes."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from asr_windows import restore_confirmed_tail, split_confirmed_tail_candidate, validate_episode_segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('timing', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    source = json.loads(args.source.read_text(encoding='utf-8'))
    timing = json.loads(args.timing.read_text(encoding='utf-8'))
    base = [{k: row[k] for k in ('id', 'start', 'end')} | {'text': row['source']}
            for row in source['rows']]
    probes = source['tail_confirmation']['probes']
    primary_only = restore_confirmed_tail(base, probes)
    joined = probes[-1]['segments'][-1]
    split = split_confirmed_tail_candidate(base[-1], joined)
    original_window = probes[-1]
    supplied = timing['probes'][0]
    confirmation = {**supplied,
        'independent_text': original_window['independent_text'],
        'speech': original_window['speech'],
        'independent_window': {'start': original_window['start'], 'end': original_window['end']},
        'timing_confirmation_for': {'text': split['text'], 'end': split['end'],
            'first_word_start': split['words'][0]['start'],
            'first_word_end': split['words'][0]['end']}}
    after = restore_confirmed_tail(base, probes + [confirmation])
    validate_episode_segments(after)
    result = {'source': str(args.source), 'timing': str(args.timing),
        'note': 'Saved alternate timing window; NOT a new production run or exact request replay.',
        'before': base, 'split': split, 'primary_only': primary_only,
        'confirmation': confirmation, 'after': after,
        'restored': len(after) > len(base)}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'restored': result['restored'], 'primary_only_restored': len(primary_only) > len(base),
                     'last_rows': after[-2:]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
