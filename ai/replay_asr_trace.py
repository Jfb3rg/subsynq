"""Reapply subtitle selection to a saved, read-only ASR diagnostic trace."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, '/app')


def tail_stages(path):
    """Model-free replay of the actual pre-tail selection, preserving every stage."""
    import copy
    import hashlib
    import asr_windows as a
    trace = json.loads(Path(path).read_text())
    merged = copy.deepcopy(trace['before_rescue'])
    context, precision, v2 = (trace.get(k) or [] for k in ('context', 'precision', 'v2'))
    speech = trace.get('strict_speech') or []
    duration = max(w.get('end', 0) for windows in trace['used_windows'].values() for w in windows)
    _, rescue = a.filter_unsupported_hallucinations(merged, trace['rescue'])
    whispered = a.filter_unconfirmed_stretched_words(trace['whispered_gap'], context+precision+rescue+v2)
    combined = rescue+whispered
    stages = []
    def step(name, function, *args, **kwargs):
        nonlocal merged
        merged = function(merged, *args, **kwargs)
        stages.append({'stage': name, 'rows': copy.deepcopy(merged)})
    step('fill', a.fill_bounded_rescue_gaps, combined, duration=duration)
    step('extend', a.extend_rescue_prefix, combined)
    step('drift', a.correct_rescue_onset_drift, combined)
    step('onsets', a.correct_rescue_word_onsets, combined)
    step('sentences', a.restore_precision_sentence_split, precision)
    step('resegment', a.reconcile_rescue_resegmentation, combined)
    step('whispered', a.insert_confident_whispered_speech, whispered, duration)
    step('rephrasing', a.reconcile_corroborated_rephrasings, precision, rescue, speech)
    step('leads', a.reattach_dangling_leads, minimum_gap=.6, subjects_before_lowercase_only=True)
    step('short_leads', a.filter_unconfirmed_short_leads, [context, precision, rescue], speech)
    step('isolated_short', a.filter_isolated_short_hallucinations, corroborating_segments=precision)
    step('isolated_silent', a.filter_isolated_without_speech, speech)
    step('leads2', a.reattach_dangling_leads, minimum_gap=.6)
    if v2:
        step('v2_fragments', a.reconcile_v2_fragments, precision, v2)
        step('v2_shift', a.reconcile_v2_shifted_words, precision, rescue, v2, speech)
        step('v2_opinion', a.reconcile_v2_second_opinions, precision, rescue, v2)
        step('v2_words', a.reconcile_v2_word_substitutions, v2)
    step('qwen', a.reconcile_qwen_segments, trace.get('qwen_windows') or [], speech)
    step('switch', a.reconcile_code_switch_readings, trace.get('qwen_windows') or [], v2, speech)
    from language_gaps import corroborated_gap_cues
    merged, _ = corroborated_gap_cues(merged, trace.get('language_gap_readings') or [])
    previous_edge = max((row['end'] for row in merged), default=0.)
    step('tail', a.restore_confirmed_tail, trace.get('tail_probes') or [])
    selected = copy.deepcopy(trace['final'])
    selected, recovered = a.restore_tail_after_anchor_recovery(selected, trace.get('tail_probes') or [], previous_edge)
    stages.append({'stage': 'selected_anchor_retry', 'rows': selected, 'recovered_anchor': recovered})
    result = {'stages': stages, 'source_file': a.__file__,
              'source_sha256': hashlib.sha256(Path(a.__file__).read_bytes()).hexdigest(),
              'NOT_a_quality_verdict': True}
    Path('/results/tail-stages.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps([{'stage': s['stage'], 'last': s['rows'][-2:]} for s in stages], ensure_ascii=False))


if '--tail-stages' in sys.argv:
    tail_stages(sys.argv[sys.argv.index('--tail-stages') + 1])
    sys.exit(0)


def audit_saved_cues():
    """Read-only check of every saved trace; optional real v2 clip evidence."""
    import argparse
    from collapsed_words import plan_collapsed_word_checks, restore_collapsed_words
    from asr_windows import correct_confirmed_word_onsets, prefer_precision_for_unpunctuated_runs, validate_episode_segments, _normalized, remove_adjacent_phrase_overlap
    from reread import conflicting_reread_rows
    from translation_qa import swedish_context_flags, swedish_grammar_flags, source_is_cut_off
    sys.path.insert(0, '/ai')
    from worker import unresolved_source_choice_ids
    from source_judge import repeats_neighbour, complete_cut_off_rows
    from asr_windows import reading_alternatives
    from code_switch import plan_checks as plan_code_switch_checks
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit-saved-cues', action='store_true')
    parser.add_argument('--extra-v2-json', default='{}')
    parser.add_argument('--onset-fixture-json', help='Additional real audio trace excerpt as JSON; never a source replacement.')
    parser.add_argument('--lead-job', type=int, help='Replay saved lead restoration against actual audio VAD, without ASR/DB/SRT writes.')
    parser.add_argument('--reading-jobs', nargs='+', type=int, help='Replay display padding using saved original speech spans and target text.')
    args = parser.parse_args()
    extra = json.loads(args.extra_v2_json)
    reports = []
    for path in sorted(Path('/config/source-transcripts').glob('job-*.asr-trace.json')):
        trace = json.loads(path.read_text())
        rows = trace.get('final') or []
        profiles = trace.get('profile_evidence') or []
        duration = max([row['end'] for row in rows] + [w['window']['end'] for w in profiles if w.get('window')] + [0])
        fixed, changes = restore_collapsed_words(rows, profiles, trace.get('v2') or [])
        record = dict(file=path.name, planned=plan_collapsed_word_checks(profiles, duration, merged=rows), existing_v2_changes=changes)
        record['code_switch_plans'] = plan_code_switch_checks(
            {'language_policy': trace.get('language_policy'), 'norwegian_reader': trace.get('norwegian_reader'),
             'swedish_reader': trace.get('swedish_reader')}, rows, trace.get('consensus_qwen') or [],
            (trace.get('v2') or []) + (trace.get('nb') or []) + (trace.get('consensus_forced') or []), duration)
        onsets, _ = correct_confirmed_word_onsets(rows, (trace.get('rescue') or []) + (trace.get('whispered_gap') or []),
                                                trace.get('v2') or [])
        try:
            validate_episode_segments(rows)
        except ValueError as error:
            record['preexisting_validation_error'] = str(error)
        else:
            validate_episode_segments(onsets)
        record['late_word_onsets'] = [{'from': row, 'to': replacement} for row, replacement in zip(rows, onsets)
                                     if row['start'] != replacement['start']]
        protected = {item['row'] for item in trace.get('consensus_prefixes') or []}
        protected |= {item['id'] for item in (trace.get('reader_plan') or {}).get('rows') or [] if item.get('used')}
        deduped, record['phrase_overlaps_removed'] = remove_adjacent_phrase_overlap(rows,
            (trace.get('rescue') or []) + (trace.get('whispered_gap') or []), trace.get('v2') or [],
            skip_indices=protected, corroboration=trace.get('consensus_qwen') or [])
        if record['phrase_overlaps_removed']:
            validate_episode_segments(deduped)
            record['deduplicated_rows'] = deduped
        if trace.get('job_id') in (args.reading_jobs or []):
            alternatives = reading_alternatives(deduped, {
                'precision': trace.get('precision') or [], 'rescue': trace.get('rescue') or [],
                'v2': trace.get('v2') or [], 'qwen': trace.get('consensus_qwen') or []})
            candidates = [dict(row, id=index, source=row['text'], alternatives=alternatives.get(index, []))
                          for index, row in enumerate(deduped)]
            completed, record['timed_source_completions'] = complete_cut_off_rows(candidates)
            validate_episode_segments(completed)
            record['completion_candidates'] = {index: items for index, items in alternatives.items()
                if any('completion_span' in item for item in items)}
        key = str(trace.get('job_id'))
        if key in extra:
            fixed, record['extra_v2_changes'] = restore_collapsed_words(rows, profiles, extra[key])
            validate_episode_segments(fixed)
        if key in ('184', '185', '186'):
            split = prefer_precision_for_unpunctuated_runs(fixed, trace.get('precision') or [], trace.get('v2') or [],
                                                         corroboration=trace.get('consensus_qwen') or [])
            record['current_resegmentation'] = [row for row in split if row not in fixed]
            record['preview_source'] = split
        source = path.with_name(path.name.replace('.asr-trace.json', '.json'))
        record['rejected_neighbour_copies'] = []
        if source.exists():
            saved = json.loads(source.read_text())
            by_id = {row['id']: row for row in saved.get('rows', [])}
            for choice in saved.get('source_choices') or []:
                index = choice.get('id')
                if choice.get('applied') and index in by_id:
                    if repeats_neighbour(choice['to'], choice['from'], by_id.get(index - 1, {}).get('source', ''),
                                         by_id.get(index + 1, {}).get('source', '')):
                        record['rejected_neighbour_copies'].append(choice)
        reports.append(record)
    sense_findings = []
    grammar_findings = []
    scale_findings = []
    from numeral_scale import numeral_scale_flags, repair_standalone_scales
    for path in sorted(Path('/config/translation-quality').glob('job-*.json')):
        report = json.loads(path.read_text())
        scales = numeral_scale_flags(report.get('rows') or [])
        if scales:
            _, changes = repair_standalone_scales(report.get('rows') or [], scales)
            scale_findings.append({'file': path.name, 'flags': scales, 'repairs': changes})
        fresh = [flag for flag in swedish_context_flags(report.get('rows') or [])
                 if flag['reason'].startswith(('Norskans/danskans', 'Att dö', 'En önskan', 'Källans it is a dress', 'Tamilskans சூது', 'Ett uttryckligt wow'))]
        if fresh:
            sense_findings.append({'file': path.name, 'flags': fresh})
        grammar = swedish_grammar_flags(report.get('rows') or [])
        if grammar:
            grammar_findings.append({'file': path.name, 'flags': grammar})
    Path('/results/word-sense-audit.json').write_text(json.dumps(sense_findings, ensure_ascii=False, indent=2))
    Path('/results/grammar-audit.json').write_text(json.dumps(grammar_findings, ensure_ascii=False, indent=2))
    Path('/results/numeral-scale-audit.json').write_text(json.dumps(scale_findings, ensure_ascii=False, indent=2))
    conflict_findings = []
    choice_findings = []
    fragment_changes = []
    for path in sorted(Path('/config/source-transcripts').glob('job-*.json')):
        if '.asr-trace.' in path.name or '.raw.' in path.name:
            continue
        source = json.loads(path.read_text())
        rows = source.get('rows') or []
        choices = unresolved_source_choice_ids(rows, source.get('source_choices') or [])
        if choices:
            choice_findings.append({'file': path.name, 'ids': choices})
        # Actual formerly softened findings, not a guess about source grammar.
        doubtful = Path('/config/doubtful-lines') / path.name
        if doubtful.exists():
            old = json.loads(doubtful.read_text())
            for record in old.get('rows') or []:
                index = next((i for i, row in enumerate(rows) if row['id'] == record['id']), None)
                if record.get('source_cut_off') and index is not None and not source_is_cut_off(rows, index):
                    fragment_changes.append({'file': path.name, 'id': record['id'], 'source': rows[index]['source']})
        review = source.get('source_reread') or {}
        conflicts = conflicting_reread_rows(source.get('rows') or [], review.get('readings') or [], review.get('flagged') or [])
        unlisted = [row['id'] for row in conflicts if row['id'] not in review.get('still_unclear', [])]
        if unlisted:
            conflict_findings.append({'file': path.name, 'unlisted_conflict_ids': unlisted})
    Path('/results/source-conflict-audit.json').write_text(json.dumps(conflict_findings, ensure_ascii=False, indent=2))
    Path('/results/source-choice-audit.json').write_text(json.dumps(choice_findings, ensure_ascii=False, indent=2))
    Path('/results/fragment-classification-audit.json').write_text(json.dumps(fragment_changes, ensure_ascii=False, indent=2))
    if args.reading_jobs:
        from speech_timing import pad_reading_time
        padded_reports = []
        for job_id in args.reading_jobs:
            quality = json.loads(Path(f'/config/translation-quality/job-{job_id}.json').read_text())
            trace = json.loads(Path(f'/config/source-transcripts/job-{job_id}.asr-trace.json').read_text())
            duration = max([row['end'] for row in trace['final']] + [
                window.get('end', 0) for windows in trace['used_windows'].values() for window in windows])
            rows = quality['rows']
            try:
                padded, changes = pad_reading_time(rows, trace['strict_speech'], duration)
                validate_episode_segments(padded)
            except ValueError as error:
                padded_reports.append({'job_id': job_id, 'error': str(error)})
                continue
            assert all(before['start'] == after['start'] and before['source'] == after['source']
                       and before['sv'] == after['sv'] for before, after in zip(rows, padded))
            padded_reports.append({'job_id': job_id, 'changes': changes, 'rows': padded,
                'fast_before': [r['id'] for r in rows if len(r['sv']) / (r['end'] - r['start']) > 35],
                'fast_after': [r['id'] for r in padded if len(r['sv']) / (r['end'] - r['start']) > 35]})
        Path('/results/reading-time-audit.json').write_text(json.dumps(padded_reports, ensure_ascii=False, indent=2))
    if args.lead_job is not None:
        sys.path.insert(0, '/ai')
        from unittest.mock import patch
        import worker
        trace = json.loads(Path(f'/config/source-transcripts/job-{args.lead_job}.asr-trace.json').read_text())
        saved = json.loads(Path(f'/config/source-transcripts/job-{args.lead_job}.json').read_text())
        rows = [dict(row) for row in trace['final']]
        protected = []
        for proof in trace.get('lead_words_restored') or []:
            index = proof['row']
            prefix = _normalized(proof['lead']).split() + _normalized(proof['before']).split()[:2]
            if _normalized(rows[index]['text']).split()[:len(prefix)] != prefix:
                continue
            cue = dict(rows[index], start=proof['new_start'], confirmed_lead_onset={
                'start': proof['new_start'], 'prefix': prefix, 'readings': proof['readings']})
            protected.append((index, cue))
        duration = max(window.get('end', 0) for windows in trace['used_windows'].values() for window in windows)
        job = {'id': -100000 - args.lead_job, 'media_path': saved['media_path'],
               'audio_choice': saved['audio_track'], 'audio_stream': saved['audio_track']['index']}
        with patch.object(worker, 'check_cancelled'):
            # One actual ffmpeg/VAD read of the media, then the same refinement for the protected rows.
            refined = worker.refine_cue_starts(job, Path(saved['media_path']), [cue for _, cue in protected], duration)
        for (index, _), cue in zip(protected, refined):
            rows[index] = cue
        validate_episode_segments(rows)
        Path('/results/lead-onset-replay.json').write_text(json.dumps({'job_id': args.lead_job, 'rows': rows,
            'changes': [{'from': before, 'to': after} for before, after in zip(trace['final'], rows) if before['start'] != after['start']],
            'timing_refinement': job.get('timing_refinement')}, ensure_ascii=False, indent=2))
    output = Path('/results/saved-cue-audit.json')
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    if args.onset_fixture_json:
        fixture = json.loads(args.onset_fixture_json)
        fixed, changes = correct_confirmed_word_onsets(fixture['final'], fixture['rescue'], fixture['v2'])
        validate_episode_segments(fixed)
        Path('/results/confirmed-onset.json').write_text(json.dumps({'rows': fixed, 'changes': changes}, ensure_ascii=False, indent=2))
    print(json.dumps(dict(traces=len(reports), planned=sum(bool(r['planned']) for r in reports),
                         existing_changes=sum(len(r['existing_v2_changes']) for r in reports), output=str(output))))


if '--audit-saved-cues' in sys.argv:
    audit_saved_cues()
    sys.exit(0)

from asr_windows import (
    corroborate_rescue, fill_bounded_rescue_gaps, fill_profile_gaps,
    filter_isolated_short_hallucinations, filter_isolated_without_speech,
    filter_unconfirmed_short_leads, filter_unsupported_hallucinations,
    prefer_precision_pauses, prefer_precision_sentence_boundaries,
    reconcile_corroborated_rephrasings, reconcile_code_switch_readings,
    reconcile_qwen_segments,
    reconcile_v2_fragments, reconcile_v2_shifted_words,
    reattach_dangling_leads,
)


trace = json.loads(Path(os.environ['SUBSYNQ_ASR_REPLAY_TRACE']).read_text(encoding='utf-8'))
merged = trace['before_rescue']
if os.environ.get('SUBSYNQ_ASR_REPLAY_PRECISION') == '1':
    merged = prefer_precision_sentence_boundaries(
        prefer_precision_pauses(
            fill_profile_gaps(trace['context'], trace['precision']), trace['precision']),
        trace['precision'])
rescue = corroborate_rescue(trace['rescue'], trace['confirmations'])
_, rescue = filter_unsupported_hallucinations(merged, rescue)
merged = fill_bounded_rescue_gaps(merged, rescue)
merged = reconcile_corroborated_rephrasings(
    merged, trace['precision'], rescue, trace['strict_speech'])
merged = filter_unconfirmed_short_leads(
    merged, [trace['context'], trace['precision'], rescue], trace['strict_speech'])
merged = filter_isolated_short_hallucinations(merged)
merged = filter_isolated_without_speech(merged, trace['strict_speech'])
merged = reattach_dangling_leads(merged, minimum_gap=.6)
if trace.get('v2'):
    merged = reconcile_v2_fragments(merged, trace['precision'], trace['v2'])
    merged = reconcile_v2_shifted_words(
        merged, trace['precision'], rescue, trace['v2'], trace['strict_speech'])
if trace.get('qwen_windows'):
    merged = reconcile_qwen_segments(merged, trace['qwen_windows'], trace['strict_speech'])
    merged = reconcile_code_switch_readings(
        merged, trace['qwen_windows'], trace.get('v2', []), trace['strict_speech'])
intervals = json.loads(os.environ['SUBSYNQ_ASR_REPLAY_INTERVALS'])
focused = [row for row in merged if any(
    row['start'] < end and row['end'] > start for start, end in intervals)]
payload = {'rows': [dict(row, id=index) for index, row in enumerate(focused)]}
if os.environ.get('SUBSYNQ_ASR_REPLAY_OUTPUT'):
    output = Path(os.environ['SUBSYNQ_ASR_REPLAY_OUTPUT'])
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, output)
print(json.dumps(payload, ensure_ascii=False, indent=2))
