"""Read-only source audit comparisons; no production verdict, DB or SRT."""
import argparse
import json
import sys
import time
import math
import copy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
import worker


def repair_replay_input(saved):
    """Replay the recorded pre-repair rows, never the later rejected candidate."""
    rows = copy.deepcopy(saved['original_rows'])
    if not rows or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Repair replay requires unique original rows')
    step = next(step for step in saved['repair_steps'] if step.get('stage') == 'phrase')
    flags = copy.deepcopy(step['input']['flags'])
    by_id = {row['id']: row for row in rows}
    if not flags or any(flag['id'] not in by_id
            or not flag.get('source_quote') or flag['source_quote'] not in by_id[flag['id']]['source']
            or not flag.get('target_quote') or flag['target_quote'] not in by_id[flag['id']]['sv'] for flag in flags):
        raise ValueError('Saved repair quotes must match the original source/target')
    return rows, flags


def cluster_replay_input(saved):
    """Reconstruct the recorded state immediately before the cluster retry."""
    cluster = saved['cluster_retranslation'][0]
    ids = set(cluster['ids'])
    rows = copy.deepcopy(saved['rows'])
    if not rows or len({row['id'] for row in rows}) != len(rows) or not 1 <= len(ids) <= 24:
        raise ValueError('Require unique source rows and one bounded saved cluster')
    if set(map(int, cluster['before'])) != ids or not ids <= {row['id'] for row in rows}:
        raise ValueError('Saved cluster must have every original target')
    for row in rows:
        if row['id'] in ids:
            row['sv'] = cluster['before'][str(row['id'])]
    flagged = {flag['id'] for review in saved['repair_reviews'] for flag in review.get('flags', [])
               if flag['id'] in ids}
    if not flagged:
        raise ValueError('Saved cluster has no actual pre-cluster review flags')
    return rows, ids, flagged


def cluster_retry_check(saved):
    """Only rejected-target metadata differs; translate and audit the actual saved block."""
    rows, ids, flagged = cluster_replay_input(saved)
    before = {row['id']: row['sv'] for row in rows if row['id'] in ids}
    result = {'source_job': saved['job_id'], 'models': [worker.MODEL_NAME, worker.REVIEW_MODEL_NAME],
              'block_ids': sorted(ids), 'actual_flagged_ids': sorted(flagged),
              'NOT_a_quality_verdict': True, 'arms': []}
    previous_job_state = dict(vars(worker.MODEL_JOB))
    try:
        for mode in ('legacy_all_rejected', 'native_flagged_only'):
            worker.MODEL_JOB.device = 'cpu'
            worker.MODEL_JOB.job_id = -100000 - saved['job_id']
            worker.MODEL_JOB.phase = 'cluster_diagnostic'
            refused = ({row_id: [text] for row_id, text in before.items()} if mode == 'legacy_all_rejected'
                       else worker.cluster_refused_targets(before, flagged))
            arm = {'mode': mode, 'refused': refused, 'reviews': []}
            result['arms'].append(arm)
            started = time.monotonic()
            try:
                with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
                    subset = [{k: v for k, v in row.items() if k != 'sv'} for row in rows if row['id'] in ids]
                    fresh = worker.translate({'id': worker.MODEL_JOB.job_id}, subset,
                                             refused=refused, context_rows=copy.deepcopy(rows))
                    if [row['id'] for row in fresh] != [row['id'] for row in rows if row['id'] in ids]:
                        raise ValueError('Cluster retry changed row identity/order')
                    replacements = {row['id']: row['sv'] for row in fresh}
                    candidate = [{**row, 'sv': replacements[row['id']]} if row['id'] in replacements else dict(row) for row in rows]
                    arm['rows'] = candidate
                    arm['changed_ids'] = [row['id'] for row, old in zip(candidate, rows) if row != old]
                    arm['unchanged_unflagged_ids'] = [row['id'] for row in fresh
                        if row['id'] not in flagged and row['sv'] == before[row['id']]]
                    worker.assess_translation_quality(candidate, audit=arm['reviews'], whole_file=True, only_ids=ids)
                    arm['status'] = 'reviewed'
            except worker.SourceQualityError as error:
                arm.update(status='quality_failed', error=str(error))
            except Exception as error:
                arm.update(status='technical_error', error=f'{type(error).__name__}: {error}')
                raise
            finally:
                arm['seconds'] = time.monotonic() - started
                Path('/results/cluster-retry-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps({key: value for key, value in arm.items() if key != 'rows'}, ensure_ascii=False), flush=True)
    finally:
        try:
            with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
                for model in dict.fromkeys((worker.MODEL_NAME, worker.REVIEW_MODEL_NAME)):
                    worker.call_ollama({'model': model, 'keep_alive': 0})
        finally:
            vars(worker.MODEL_JOB).clear()
            vars(worker.MODEL_JOB).update(previous_job_state)


def bridge_repair_context(prompt, rows):
    """Diagnostic only: fill single-row holes between already supplied neighbours."""
    result = copy.deepcopy(prompt)
    positions = {row['id']: index for index, row in enumerate(rows)}
    context = result['context']
    if len({row['id'] for row in context}) != len(context) or any(row['id'] not in positions for row in context):
        raise ValueError('Context must reference unique saved rows')
    selected = {positions[row['id']] for row in context}
    added = {index for index in range(1, len(rows) - 1)
             if index not in selected and index - 1 in selected and index + 1 in selected}
    by_id = {row['id']: row for row in context}
    for index in added:
        row = rows[index]
        by_id[row['id']] = {key: row[key] for key in ('id', 'source', 'sv', 'source_unclear') if key in row}
    result['context'] = [by_id[rows[index]['id']] for index in sorted(selected | added)]
    return result, [rows[index]['id'] for index in sorted(added)]


def repair_context_check(saved):
    """Compare original worker repair with a bounded context-only intervention."""
    rows, flags = repair_replay_input(saved)
    target_ids = {flag['id'] for flag in flags}
    positions = [index for index, row in enumerate(rows) if row['id'] in target_ids]
    left, right = max(0, min(positions) - 1), min(len(rows), max(positions) + 2)
    if right - left > 40:
        raise ValueError('Repair diagnostic review is bounded to forty contiguous rows')
    result = {'source_job': saved['job_id'], 'reference_models': [saved.get('translation_model'), saved.get('model')],
              'models': [worker.MODEL_NAME, worker.REVIEW_MODEL_NAME], 'requested_ids': sorted(target_ids),
              'review_ids': [row['id'] for row in rows[left:right]], 'NOT_a_quality_verdict': True, 'arms': []}
    native_call = worker.call_ollama
    previous_job_state = dict(vars(worker.MODEL_JOB))
    worker.MODEL_JOB.device = 'cpu'
    worker.MODEL_JOB.job_id = -100000 - saved['job_id']
    worker.MODEL_JOB.phase = 'translation_review'
    try:
        for mode in ('baseline', 'bridge_context'):
            arm = {'mode': mode, 'steps': [], 'requests': [], 'reviews': [], 'added_context_ids': []}
            result['arms'].append(arm)
            def call(request):
                if request.get('prompt') and request.get('format'):
                    request = copy.deepcopy(request)
                    prompt = json.loads(request['prompt'])
                    if isinstance(prompt, dict) and 'editable_targets' in prompt:
                        if mode == 'bridge_context':
                            prompt, added = bridge_repair_context(prompt, rows)
                            arm['added_context_ids'].extend(added)
                            request['prompt'] = json.dumps(prompt, ensure_ascii=False)
                        arm['requests'].append({'context_ids': [row['id'] for row in prompt['context']],
                                                'requested_ids': prompt['requested_ids']})
                return native_call(request)
            started = time.monotonic()
            try:
                with patch.object(worker, 'call_ollama', side_effect=call), \
                        patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
                    candidate = worker.repair_flagged_translation({'id': -100000 - saved['job_id']},
                        copy.deepcopy(rows), flags, record=arm['steps'].append)
                    if (len(candidate) != len(rows) or any(
                            {k: v for k, v in before.items() if k != 'sv'} != {k: v for k, v in after.items() if k != 'sv'}
                            or (before['id'] not in target_ids and before != after)
                            for before, after in zip(rows, candidate))):
                        raise ValueError('Repair changed source/timing/order or an unrequested row')
                    arm['rows'] = candidate
                    arm['unchanged_unrequested_rows'] = True
                    worker.assess_translation_quality(candidate[left:right], audit=arm['reviews'], whole_file=True)
                    arm['status'] = 'reviewed'
            except worker.SourceQualityError as error:
                arm.update(status='quality_failed', error=str(error))
            except Exception as error:
                arm.update(status='technical_error', error=f'{type(error).__name__}: {error}')
                raise
            finally:
                arm['seconds'] = time.monotonic() - started
                Path('/results/repair-context-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps({key: value for key, value in arm.items() if key not in ('rows', 'steps')}, ensure_ascii=False), flush=True)
    finally:
        try:
            with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
                for model in dict.fromkeys((worker.MODEL_NAME, worker.REVIEW_MODEL_NAME)):
                    native_call({'model': model, 'keep_alive': 0})
        finally:
            vars(worker.MODEL_JOB).clear()
            vars(worker.MODEL_JOB).update(previous_job_state)


def saved_reread_input(saved, trace=None):
    """Restore original speech ends and the recorded language-pack decision, never display padding."""
    if trace is not None and trace.get('job_id') != saved['job_id']:
        raise ValueError('Källrapport och ASR-spår måste tillhöra samma jobb.')
    job = {'id': -100000 - int(saved['job_id']), 'media_path': saved['media_path'],
           'audio_choice': saved['audio_track'], 'audio_stream': saved['audio_track']['index'],
           'language_policy': saved.get('language_policy') or {},
           'source_choices': saved.get('source_choices') or []}
    for key in ('norwegian_reader', 'swedish_reader'):
        job[key] = saved.get(key) if saved.get(key) is not None else (trace or {}).get(key)
    rows = [dict(row) for row in saved['rows']]
    for row in rows:
        if 'reading_time_source_end' in row:
            end = row['reading_time_source_end']
            if type(end) not in (int, float) or not math.isfinite(end) or not row['start'] < end <= row['end']:
                raise ValueError('Ogiltig sparad ursprungstid för talet.')
            row['end'] = end
    return job, rows


def reread_source(saved, *, ids=None, qwen_url=None, trace=None):
    """Run the existing source reread on RO inputs; progress/DB calls are mocked."""
    if qwen_url:
        worker.QWEN_ASR_URL = qwen_url.rstrip('/')
        worker.QWEN_ASR_ENABLED = True
    job, rows = saved_reread_input(saved, trace)
    worker.MODEL_JOB.device = 'cpu'
    worker.MODEL_JOB.job_id = job['id']
    worker.MODEL_JOB.phase = 'source_review'
    result = {'source_job': saved['job_id'], 'rows': rows, 'job': job, 'NOT_a_quality_verdict': True}
    started = time.monotonic()
    try:
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            if ids is None:
                result['still_unclear'] = worker.assess_source_with_reread(job, rows)
            else:
                if not set(ids) <= {row['id'] for row in rows}:
                    raise ValueError('Omläsningen kräver ID:n som finns i källrapporten.')
                result['changed'] = worker.reread_flagged_rows(job, rows, ids)
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['seconds'] = time.monotonic() - started
        Path('/results/source-reread.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        worker.call_ollama({'model': worker.MODEL_NAME, 'keep_alive': 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--controls', action='store_true')
    parser.add_argument('--translate-check', action='store_true', help='Translate and review saved rows in memory; write only /results.')
    parser.add_argument('--repair-context-check', action='store_true', help='Compare saved phrase repair with one-row context holes filled, only in /results.')
    parser.add_argument('--cluster-retry-check', action='store_true', help='Compare all-vs-actually-rejected cluster targets on the saved block; /results only.')
    parser.add_argument('--expect-clear', action='store_true', help='Fail the diagnostic if the saved source receives any finding.')
    parser.add_argument('--reread', action='store_true', help='Source audit and acoustic reread, output only in /results.')
    parser.add_argument('--ids', nargs='+', type=int, help='Explicit saved row IDs for an acoustic diagnostic, without source audit.')
    parser.add_argument('--qwen-url', help='Existing Dev Qwen endpoint; no new models or service.')
    args = parser.parse_args()
    saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
    if args.cluster_retry_check:
        if args.repair_context_check or args.controls or args.translate_check or args.expect_clear or args.reread or args.ids is not None or args.qwen_url:
            parser.error('--cluster-retry-check is a separate saved-quality-report diagnostic')
        cluster_retry_check(saved)
        return
    if args.repair_context_check:
        if args.controls or args.translate_check or args.expect_clear or args.reread or args.ids is not None or args.qwen_url:
            parser.error('--repair-context-check is a separate saved-quality-report diagnostic')
        repair_context_check(saved)
        return
    if args.reread or args.ids is not None:
        if args.controls or args.translate_check or args.expect_clear:
            parser.error('--controls/--translate-check/--expect-clear cannot be combined with acoustic rereading')
        trace_path = Path(args.report).with_suffix('.asr-trace.json')
        trace = json.loads(trace_path.read_text()) if trace_path.exists() else None
        reread_source(saved, ids=args.ids, qwen_url=args.qwen_url, trace=trace)
        return
    samples = [('saved_source', saved['rows'])]
    if args.controls:
        # Synthetic, complete control sentences, not replacements for real audio.
        for name, texts in [
            ('hindi_control', ['मैं घर जा रहा हूँ।', 'वह कल आएगा।', 'यह मेरी कहानी है।']),
            ('japanese_control', ['ありがとうございます。', '明日、家に帰ります。', 'これは私の本です。']),
            ('tamil_control', ['நன்றி.', 'நான் வீட்டுக்குச் செல்கிறேன்.', 'இது என் புத்தகம்.'])]:
            samples.append((name, [{'id': index, 'source': text} for index, text in enumerate(texts)]))
        for name, texts in [
            ('language_switch_control', ['Wir drehen heute einen Film.', 'I would like to join you.',
                                         'Ich will auch in den Film.']),
            ('malformed_switch_negative', ['Wir drehen heute einen Film.', 'I the the the the the the the the.',
                                           'Ich der die das der die das Film.'])]:
            samples.append((name, [{'id': index, 'source': text} for index, text in enumerate(texts)]))
        evidence = worker.source_audit_language_context(saved.get('job') or {}, saved['rows'])
        base_rows = [row for row in saved['rows'] if str(row['id']) not in (evidence or {}).get('verified_local_languages', {})]
        if evidence and base_rows:
            # Synthetic repetition outside the proven group, never saved source replacement.
            negative_id = base_rows[-1]['id']
            samples.append(('verified_language_negative', [
                {**row, 'source': 'The the the the the the the the.'} if row['id'] == negative_id else dict(row)
                for row in saved['rows']]))
    try:
        for name, rows in samples:
            print(json.dumps({'event': 'source_audit_started', 'sample': name}), flush=True)
            started = time.monotonic()
            context = worker.source_audit_language_context(saved.get('job') or {}, rows) if name in ('saved_source', 'verified_language_negative') else None
            calls = []
            original_audit = worker._audit_source_chunk
            def audit(*values, **options):
                verdict = original_audit(*values, **options)
                calls.append({'ids': [row['id'] for row in values[0]], 'bad_ids': verdict[0],
                              'language_mismatch': verdict[1], 'reason': verdict[2]})
                return verdict
            bad_ids = []
            with patch.object(worker, '_audit_source_chunk', side_effect=audit):
                try:
                    worker.assess_source_quality(rows, '', language_context=context, broken=bad_ids)
                except worker.BrokenSourceRows as error:
                    bad_ids.extend(error.ids)
            bad_ids = sorted(set(bad_ids))
            mismatch = any(call['language_mismatch'] for call in calls)
            reason = ' '.join(call['reason'] for call in calls)
            print(json.dumps({'sample': name, 'seconds': time.monotonic() - started,
                              'bad_ids': bad_ids, 'language_mismatch': mismatch, 'reason': reason, 'language_context': context, 'calls': calls,
                              'NOT_a_quality_verdict': True}, ensure_ascii=False), flush=True)
            if name.endswith('_control') and (bad_ids or mismatch):
                raise RuntimeError(f'Tydligt kontrollmaterial underkändes: {name}')
            if name == 'malformed_switch_negative' and not {1, 2}.intersection(bad_ids):
                raise RuntimeError('Trasig blandtext upptäcktes inte.')
            if name == 'verified_language_negative' and negative_id not in bad_ids:
                raise RuntimeError('Språkbeviset dolde en trasig grannreplik.')
            if name == 'saved_source' and args.expect_clear and (bad_ids or mismatch):
                raise RuntimeError('Den sparade källan har kvar granskningsfynd.')
        if args.translate_check:
            translation_check(saved['rows'])
    finally:
        for model in dict.fromkeys((worker.MODEL_NAME, worker.REVIEW_MODEL_NAME)):
            worker.call_ollama({'model': model, 'keep_alive': 0})


def translation_check(rows):
    """Use real translation/review, with all job progress writes disabled."""
    result = {'NOT_a_quality_verdict': True, 'scope': 'provided rows only', 'reviews': []}
    try:
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            result['rows'] = worker.translate({'id': -1}, [dict(row) for row in rows])
            worker.assess_translation_quality(result['rows'], audit=result['reviews'])
        result['status'] = 'reviewed'
    except Exception as error:
        result.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        Path('/results/translation-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
