"""Reapply subtitle selection to a saved, read-only ASR diagnostic trace."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, '/app')
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
