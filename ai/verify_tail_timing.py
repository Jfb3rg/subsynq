"""Read bounded alternative word timings; no subtitle/database changes."""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path
import worker
from asr_windows import restore_confirmed_tail, split_confirmed_tail_candidate, split_word_cues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--media', required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    saved = json.loads(args.source.read_text())
    base = [{**r, 'text': r['source']} for r in saved['rows']]
    primary = saved['tail_confirmation']['probes'][0]
    candidate = split_confirmed_tail_candidate(base[-1], primary['segments'][-1])
    words = candidate['words']
    anchor = {'text': candidate['text'], 'end': candidate['end'],
              'first_word_start': words[0]['start'], 'first_word_end': words[0]['end']}
    result = {'before': base, 'probes': [primary], 'attempts': []}
    model = worker.WhisperModel('large-v2', device='cpu', compute_type='int8',
        cpu_threads=6, download_root='/models', local_files_only=True)
    try:
        with tempfile.TemporaryDirectory() as folder:
            for start in [float(int(primary['start'] + 2.0)) + .5]:
                audio = Path(folder) / 'clip.wav'
                subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', args.media,
                    '-t', '12', '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-y', str(audio)], check=True)
                segments, info = model.transcribe(str(audio), language=None, multilingual=True,
                    vad_filter=False, beam_size=5, condition_on_previous_text=False,
                    word_timestamps=True, hallucination_silence_threshold=2.0)
                cues = []
                for segment in segments:
                    if segment.no_speech_prob > .8 and segment.avg_logprob < -1:
                        continue
                    for cue in split_word_cues(segment.words or [], minimum_probability=.6,
                                               maximum_pause=.6, include_words=True):
                        cue['start'] += start
                        cue['end'] += start
                        for word in cue['words']:
                            word['start'] += start
                            word['end'] += start
                        cues.append(cue)
                probe = {'start': start, 'end': start + 12, 'segments': cues,
                    'independent_text': primary['independent_text'], 'speech': primary['speech'],
                    'independent_window': primary, 'timing_confirmation_for': anchor}
                result['probes'].append(probe)
                after = restore_confirmed_tail(base, result['probes'])
                result['attempts'].append({'start': start, 'language': info.language,
                    'restored': len(after) > len(base), 'after': after})
    finally:
        model.model.unload_model()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result['attempts'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
