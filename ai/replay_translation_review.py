"""Replay saved target candidates without ASR, production DB writes or SRT output."""
import argparse
import json
from pathlib import Path
import worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--repair-only', action='store_true')
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--review-repaired', action='store_true')
    parser.add_argument('--cached-calls', type=Path)
    parser.add_argument('--focused-audit', action='store_true')
    parser.add_argument('--no-thinking', action='store_true')
    parser.add_argument('--target-key', choices=('sv', 'en'), default='sv')
    args = parser.parse_args()
    target_language = 'Swedish' if args.target_key == 'sv' else 'English'
    target_options = {'target_key': args.target_key, 'target_language': target_language}
    if args.repair_only and args.audit_only:
        parser.error('Choose repair-only or audit-only, not both')
    if args.review_repaired and not args.repair_only:
        parser.error('review-repaired requires repair-only')
    args.output.mkdir(parents=True, exist_ok=False)
    original = json.loads(args.input.read_text(encoding='utf-8'))
    rows = original.get('translated') or original['rows']
    report = {'input': str(args.input), 'rows': rows, 'calls': [], 'status': 'pending'}
    cached = (json.loads(args.cached_calls.read_text(encoding='utf-8'))['calls']
              if args.cached_calls else [])
    def save():
        (args.output / 'replay.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    call = worker.call_ollama
    def recorded_call(request):
        if args.no_thinking:
            request = {**request, 'think': False}
        if args.focused_audit and 'flags' in request.get('format', {}).get('properties', {}):
            from translation_review_prompt import audit_system
            _, marker, retry_note = request['system'].partition(' The previous response')
            request = {**request, 'system': audit_system(args.target_key, target_language) + marker + retry_note}
        entry = {'request': request}
        report['calls'].append(entry)
        save()
        try:
            if cached:
                previous = cached.pop(0)
                if previous['request'] != request or 'result' not in previous:
                    raise ValueError('Cached request does not exactly match; refusing reuse')
                result = previous['result']
                entry['reused_from'] = str(args.cached_calls)
            else:
                result = call(request)
            entry['result'] = result
            return result
        except Exception as error:
            entry['error'] = str(error)
            raise
        finally:
            save()
    worker.call_ollama = recorded_call
    worker.check_cancelled = lambda *_: None
    worker.update_job = lambda *a, **kw: None
    worker.QUALITY_REPORTS = args.output
    try:
        if args.repair_only:
            quality = original.get('quality_report', original)
            flags = [f for r in quality['reviews'] for f in r.get('flags', [])]
            if not flags:
                raise ValueError('No saved flags to replay')
            report['repaired'] = worker.repair_flagged_translation({'id': -1}, rows, flags, **target_options)
            report['status'] = 'repair_returned_not_approved'
            save()
            if args.review_repaired:
                report['reviews'] = []
                worker.assess_translation_quality(report['repaired'], audit=report['reviews'], **target_options)
                if any(r.get('flags') for r in report['reviews']):
                    raise worker.SourceQualityError('Kvarstående anmärkningar efter rättning.')
                report['status'] = 'review_passed_not_audio_verified'
        elif args.audit_only:
            report['reviews'] = []
            worker.assess_translation_quality(rows, audit=report['reviews'], **target_options)
            report['status'] = ('flags_require_repair' if any(r.get('flags') for r in report['reviews'])
                                else 'review_passed_not_audio_verified')
        else:
            worker.review_and_save_translation({'id': -1}, rows, **target_options)
            report['status'] = 'review_passed_not_audio_verified'
    except Exception as error:
        report.update(status='quality_failed' if isinstance(error, worker.SourceQualityError)
                      else 'error', error=f'{type(error).__name__}: {error}')
    finally:
        suffix = '' if args.target_key == 'sv' else '-en'
        quality_path = args.output / f'job--1{suffix}.json'
        if quality_path.exists():
            report['quality_report'] = json.loads(quality_path.read_text(encoding='utf-8'))
        save()
        summary = {k: report[k] for k in ('input', 'status', 'error') if k in report}
        quality = report.get('quality_report', {})
        reviews = quality.get('repair_reviews', quality.get('reviews', report.get('reviews', [])))
        summary['bad_ids'] = sorted({f['id'] for r in reviews
                                     for f in r.get('flags', [])})
        if 'expected_bad_ids' in original:
            observed = set(summary['bad_ids'])
            summary['missed_bad_ids'] = sorted(set(original['expected_bad_ids']) - observed)
            summary['false_positive_ids'] = sorted(set(original['expected_good_ids']) & observed)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
