"""Bounded, independent tail-recovery diagnostic; no DB or subtitle writes."""
import argparse
import gc
import json
import subprocess
import tempfile
from pathlib import Path

import worker
from asr_windows import plan_tail_confirmation, restore_confirmed_tail, split_word_cues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--media', required=True)
    parser.add_argument('--model', choices=['large-v3', 'large-v2'], default='large-v3')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    saved = json.loads(args.base.read_text(encoding='utf-8'))
    base = [{**row, 'text': row.get('text', row.get('source', ''))} for row in saved['rows']]
    report = {'base': str(args.base), 'model': args.model, 'before': base, 'probes': []}
    windows = plan_tail_confirmation(base, worker.duration(Path(args.media)))
    with tempfile.TemporaryDirectory(prefix='subsynq-tail-') as folder:
        audio_path = Path(folder) / 'tail.wav'
        for window in windows:
            subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(window['start']),
                '-i', args.media, '-t', str(window['end'] - window['start']),
                '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                '-y', str(audio_path)], check=True, timeout=90)
            try:
                reading = worker.qwen_identify(audio_path, attempts=1)
            finally:
                worker.qwen_unload(attempts=1)
            print(json.dumps({'independent': reading}, ensure_ascii=False), flush=True)
            model = worker.WhisperModel(args.model, device='cpu', compute_type='int8',
                cpu_threads=6, download_root='/models', local_files_only=True)
            try:
                segments, info = model.transcribe(str(audio_path), language=None,
                    multilingual=True, vad_filter=False, beam_size=5,
                    condition_on_previous_text=False, word_timestamps=True,
                    hallucination_silence_threshold=2.0)
                cues = []
                for segment in segments:
                    if segment.no_speech_prob > .8 and segment.avg_logprob < -1:
                        continue
                    for cue in split_word_cues(segment.words or [], minimum_probability=.6,
                                               maximum_pause=.6, include_words=True):
                        cue['start'] += window['start']
                        cue['end'] = min(cue['end'] + window['start'], window['end'])
                        for word in cue['words']:
                            word['start'] += window['start']
                            word['end'] += window['start']
                        cues.append(cue)
            finally:
                del model
                gc.collect()
            audio = worker.decode_audio(str(audio_path), sampling_rate=16000)
            speech = [{'start': window['start'] + span['start'] / 16000,
                       'end': window['start'] + span['end'] / 16000}
                      for span in worker.get_speech_timestamps(audio, worker.VadOptions(
                          threshold=.15, min_speech_duration_ms=120,
                          min_silence_duration_ms=200, speech_pad_ms=100))]
            report['probes'].append({**window, 'segments': cues, 'speech': speech,
                                    'independent_text': reading.get('text', ''),
                                    'independent_language': reading.get('language'),
                                    'whisper_language': info.language})
    report['after'] = restore_confirmed_tail(base, report['probes'])
    report['restored'] = len(report['after']) > len(base)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'restored': report['restored'],
                      'after': report['after']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
