"""Audio readings and model-free listening exports; no production writes.

Mount reports/media/models read-only. ASR scratch audio stays in /tmp;
--export-only writes native-rate/channel listening clips under /results only.
Readings and exported clips are never automatic source replacements or approval.
"""
import argparse
import csv
import gc
import hashlib
import html
import json
import math
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')


def channel_filter(channel, stream):
    """Never silently extract a nonexistent centre or guess an unknown layout."""
    if channel == 'mono':
        return None
    if channel in ('left', 'right') and stream.get('channels') == 2 and stream.get('channel_layout') == 'stereo':
        return 'pan=mono|c0=' + ('FL' if channel == 'left' else 'FR')
    if channel == 'center' and (stream.get('channels'), stream.get('channel_layout')) in (
            (6, '5.1'), (6, '5.1(side)'), (8, '7.1'), (8, '7.1(wide)'), (8, '7.1(wide-side)')):
        return 'pan=mono|c0=FC'
    raise ValueError('Requested channel is not verified in this audio layout')


def source_audio_map(saved, explicit_index=None):
    """Use the job's actual ffprobe stream, including non-first originals."""
    track = saved.get('audio_track') or {}
    index = explicit_index if explicit_index is not None else track.get('index')
    if index is None:
        raise ValueError('Källrapporten saknar valt ljudspår; ange --audio-track.')
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError('Ljudspåret måste vara ett icke-negativt ffprobe-index.')
    return f'0:{index}'


def source_window(rows, start=None, end=None):
    if not rows:
        raise ValueError('Källrapporten saknar rader.')
    start = max(0.0, min(float(row['start']) for row in rows) - 2) if start is None else start
    end = max(float(row['end']) for row in rows) + 2 if end is None else end
    if not all(math.isfinite(value) for value in (start, end)) or start < 0 or not 0 < end - start <= 60:
        raise ValueError('Diagnostik kräver ett giltigt ljudfönster på högst 60 sekunder.')
    return start, end


def export_witness_frames(saved, media, times):
    """Original video pixels only, without OCR/ASR or external subtitle files."""
    if (Path(saved['media_path']).resolve() != media.resolve()
            or type(saved.get('job_id')) is not int or saved['job_id'] <= 0):
        raise ValueError('Frame witness requires the original saved media/job')
    if (not 1 <= len(times) <= 12 or len(set(times)) != len(times)
            or any(not math.isfinite(t) or t < 0 for t in times)):
        raise ValueError('Require 1–12 distinct finite non-negative frame times')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                              '-show_format', '-of', 'json', str(media)]))
    video = next((s for s in info['streams'] if s.get('codec_type') == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    if video is None:
        raise ValueError('No actual video stream in the saved media')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or duration <= 0 or any(t >= duration for t in times):
        raise ValueError('Frame times must lie within the actual media')
    output = Path('/results/frames') / f'job-{saved["job_id"]}'
    output.mkdir(parents=True, exist_ok=False)
    frames = []
    for index, requested in enumerate(times):
        path = output / f'frame-{index:02d}.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(requested),
            '-i', str(media), '-map', f'0:{video["index"]}', '-an', '-sn', '-dn',
            '-frames:v', '1', '-threads', '1', '-n', str(path)], check=True, timeout=90)
        data = path.read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Frame extraction did not produce a PNG')
        frames.append({'requested_seconds': requested, 'file': path.name,
                       'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'job_id': saved['job_id'], 'media_path': str(media), 'video_stream': video['index'],
                'frames': frames, 'automatic_source_replacement': False,
                'note': 'Requested seek times, not measured subtitle/word boundaries. Translation witness only.'}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return manifest


def listening_windows(saved, extra_ids=(), context=3., duration=None):
    """Cover uncertain/requested cues with context, merging nearby short clips."""
    if not math.isfinite(context) or not .5 <= context <= 10:
        raise ValueError('Listening context must be 0.5–10 seconds')
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError('Media duration must be finite and positive')
    rows = saved.get('rows') or []
    if not set(extra_ids) <= {row['id'] for row in rows}:
        raise ValueError('Requested row ID is absent from the saved source')
    planned = []
    for row in rows:
        if not row.get('source_unclear') and row['id'] not in extra_ids:
            continue
        start = float(row['start'])
        end = float(row.get('reading_time_source_end', row['end']))
        source_window([row], start, end)
        left, right = max(0., start - context), end + context
        if duration is not None:
            right = min(right, duration)
        if not left < right or end > right:
            raise ValueError('Saved speech interval exceeds the actual media')
        source_window([row], left, right)
        cue = {'id': row['id'], 'start': start, 'end': end}
        planned.append({'start': left, 'end': right, 'cues': [cue]})
    merged = []
    for clip in sorted(planned, key=lambda x: x['start']):
        if (merged and clip['start'] <= merged[-1]['end'] + 1
                and max(clip['end'], merged[-1]['end']) - merged[-1]['start'] <= 30):
            merged[-1]['end'] = max(merged[-1]['end'], clip['end'])
            merged[-1]['cues'] += clip['cues']
        else:
            merged.append(clip)
    return merged


def export_listening_clips(saved, media, extra_ids=(), context=3.):
    """Decode native-rate/native-channel PCM only; no AI imports or filtering."""
    if Path(saved['media_path']).resolve() != media.resolve():
        raise ValueError('Listening export must use the saved job media path')
    audio_map = source_audio_map(saved)
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                              '-show_format', '-of', 'json', str(media)]))
    stream = next((s for s in info['streams'] if s['index'] == saved['audio_track']['index']
                   and s['codec_type'] == 'audio'), None)
    if stream is None:
        raise ValueError('Saved audio stream is not present in the media')
    raw_duration = stream.get('duration') or info.get('format', {}).get('duration')
    duration = float(raw_duration) if raw_duration and raw_duration != 'N/A' else None
    clips = listening_windows(saved, extra_ids, context, duration)
    if not clips:
        raise ValueError('No uncertain/requested rows to export')
    output = Path('/results/listening') / f'job-{saved["job_id"]}'
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for index, clip in enumerate(clips, 1):
        path = output / f'job-{saved["job_id"]}-{index:02d}.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(clip['start']),
            '-i', str(media), '-t', str(clip['end'] - clip['start']), '-map', audio_map,
            '-vn', '-sn', '-dn', '-c:a', 'pcm_f32le', '-n', str(path)], check=True, timeout=90)
        check = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                                   '-of', 'json', str(path)]))['streams'][0]
        if (int(check['sample_rate']) != int(stream['sample_rate'])
                or check['channels'] != stream['channels'] or check['codec_name'] != 'pcm_f32le'
                or abs(float(check['duration']) - (clip['end'] - clip['start'])) > .002):
            raise ValueError('Export changed channel count/rate or did not cover the planned time')
        records.append({**clip, 'file': path.name, 'sample_rate': int(check['sample_rate']),
                        'channels': check['channels'], 'duration': float(check['duration']),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {'job_id': saved['job_id'], 'media': str(media), 'media_bytes': media.stat().st_size,
        'audio_map': audio_map, 'audio_stream': stream, 'context_seconds': context,
        'processing': 'Decoded float32 PCM; original sample rate and channels; no filters or normalization',
        'models_used': [], 'worker_imported': 'worker' in sys.modules, 'clips': records}
    with (output / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'job_id': saved['job_id'], 'clips': len(records),
                      'output': str(output), 'models_used': []}), flush=True)
    return manifest


def pack_listening_clips(manifests):
    """Self-contained listening page and blank notes, without guessed transcripts."""
    root = Path('/results/listening')
    page = ['<!doctype html><html lang="sv"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>SubSynq – ljud att kontrollera</title>',
        '<style>body{font:17px system-ui;max-width:900px;margin:2em auto;padding:0 1em;line-height:1.5;color-scheme:light dark}audio{width:100%}article{border-bottom:1px solid #888;padding:1em 0}small{display:block}</style>',
        '<h1>Ljud att kontrollera</h1><p>Avkodat originalljud från respektive jobbs valda ljudspår, med sammanhang före och efter. '
        'Samma samplingsfrekvens och kanaler. Ingen filtrering, normalisering eller AI-bearbetning. '
        'Tidskoderna avser originalfilen. Inga modellgissningar visas här.</p>',
        '<p>Anteckna hörda ord i <a href="anteckningar.tsv">anteckningar.tsv</a>. '
        'Om webbläsaren inte spelar WAV-formatet kan filerna öppnas i exempelvis din vanliga mediaspelare.</p>']
    def stamp(value):
        minutes, seconds = divmod(value, 60)
        return f'{int(minutes):02d}:{seconds:06.3f}'
    with (root / 'anteckningar.tsv').open('x', encoding='utf-8', newline='') as handle:
        notes = csv.writer(handle, delimiter='\t')
        notes.writerow(['fil', 'job_id', 'mediefil', 'ljudspår', 'start_original', 'slut_original',
                        'rad_id', 'bekräftade_originalord', 'osäkra_ord', 'granskad_av'])
        for manifest in manifests:
            page.append(f'<h2>Jobb {manifest["job_id"]}: {html.escape(Path(manifest["media"]).stem)}</h2>')
            for clip in manifest['clips']:
                relative = f'job-{manifest["job_id"]}/{clip["file"]}'
                cues = ', '.join(f'{c["id"]} (+{c["start"] - clip["start"]:.2f} s)' for c in clip['cues'])
                page.append(f'<article><b>{stamp(clip["start"])}–{stamp(clip["end"])}</b>'
                    f'<small>Rad-ID och plats i klippet: {cues}. Ljudspår {manifest["audio_stream"]["index"]}; '
                    f'{clip["sample_rate"]} Hz, {clip["channels"]} kanaler.</small>'
                    f'<audio controls preload="none" src="{relative}"></audio>'
                    f'<a href="{relative}" download>Hämta WAV</a></article>')
                notes.writerow([relative, manifest['job_id'], Path(manifest['media']).name,
                    manifest['audio_stream']['index'], stamp(clip['start']), stamp(clip['end']),
                    ','.join(str(c['id']) for c in clip['cues']), '', '', ''])
    page.append('</html>')
    (root / 'index.html').write_text('\n'.join(page), encoding='utf-8')
    (root / 'manifest.json').write_text(json.dumps({'jobs': manifests}, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = Path('/results/subsynq-listening.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as packed:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                packed.write(path, path.relative_to(root))
    with zipfile.ZipFile(archive) as packed:
        if packed.testzip() is not None:
            raise ValueError('Listening archive failed CRC verification')
    print(json.dumps({'archive': str(archive), 'jobs': len(manifests),
                      'clips': sum(len(m['clips']) for m in manifests),
                      'bytes': archive.stat().st_size}), flush=True)


def pipeline_source(saved, media):
    """Run only the real CPU ASR path, with no job/database/SRT writes."""
    import worker
    job_id = -100000 - int(saved['job_id'])
    job = {'id': job_id, 'media_path': str(media), 'audio_stream': saved['audio_track']['index'],
           'audio_choice': saved['audio_track']}
    worker.MODEL_JOB.job_id = job_id
    worker.MODEL_JOB.used = False
    worker.MODEL_JOB.active_model = None
    worker.MODEL_JOB.cleanup_pending = None
    worker.MODEL_JOB.device = 'cpu'
    worker.MODEL_JOB.phase = 'asr'
    worker.V2_FRAGMENT_ENABLED = True
    worker.os.environ['SUBSYNQ_ASR_TRACE_OUTPUT'] = '/results/asr-trace.json'
    began = time.monotonic()
    def progress(*args, **kwargs):
        print(json.dumps({'progress': args[:4]}, ensure_ascii=False), flush=True)
    try:
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job', progress):
            worker.prepare_asr_job()
            model = worker.WhisperModel('large-v3', device='cpu', compute_type='int8', cpu_threads=6,
                                        download_root='/models', local_files_only=True)
            rows = worker.transcribe_and_release(job, media, model, 'cpu')
            worker.validate_episode_segments(rows)
        payload = dict(job_id=job_id, source_job=saved['job_id'], seconds=time.monotonic() - began,
                       rows=rows, job=job, NOT_a_quality_verdict=True)
        Path('/results/pipeline-source.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(json.dumps({'source_rows': len(rows), 'seconds': payload['seconds']}), flush=True)
    finally:
        worker.finish_model_job(job_id)


def forced_candidate_scores(model, waveform, language, texts):
    """Same teacher-forced measurement for film probes and reference calibration."""
    import numpy as np
    from faster_whisper.audio import pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual,
                          task='transcribe', language=language)
    tokens = [tokenizer.encode(' ' + text.strip()) for _, text in texts]
    if not texts or any(not t or len(t) > 256 for t in tokens):
        raise ValueError('Invalid candidate token count')
    values = {}
    for variant, samples in [('audio', waveform), ('silence', np.zeros_like(waveform))]:
        features = model.feature_extractor(samples)
        frames = min(features.shape[-1] - 1,
                     math.ceil(len(samples)/model.feature_extractor.hop_length))
        encoded = model.encode(pad_or_trim(features))
        values[variant] = []
        for sequence in tokens:
            aligned = model.model.align(encoded, tokenizer.sot_sequence, [sequence], frames)[0]
            probabilities = list(aligned.text_token_probs)
            if (len(probabilities) != len(sequence) or
                    any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)):
                raise ValueError('Invalid forced token probabilities')
            logs = [math.log(max(p, 1e-30)) for p in probabilities]
            values[variant].append({'token_probabilities': probabilities,
                'sum_logprob': sum(logs), 'mean_logprob': sum(logs)/len(logs)})
    return [{'text': text, 'synthetic_control': control, 'token_count': len(sequence),
             'audio': audio, 'silence': silence,
             'mean_audio_gain': audio['mean_logprob'] - silence['mean_logprob']}
            for (control, text), sequence, audio, silence in
            zip(texts, tokens, values['audio'], values['silence'])]


def score_saved_candidates(saved, media, row_id, start, end, language, names, compute_type):
    """Teacher-forced audio evidence, never a source choice or quality verdict."""
    from faster_whisper.audio import decode_audio
    if not language or set(names) - {'large-v2', 'large-v3'}:
        raise ValueError('Candidate scoring requires an explicit language and only existing Whisper models')
    row = next((r for r in saved['rows'] if r['id'] == row_id), None)
    if row is None or not start <= row['start'] < row['end'] <= end or end - start > 15:
        raise ValueError('A saved cue must be wholly inside the bounded candidate-scoring window')
    if Path(saved['media_path']).resolve() != media.resolve():
        raise ValueError('Candidate scoring must read the saved job media')
    choice = next((c for c in saved.get('source_choices', []) if c.get('id') == row_id), {})
    candidates = list(dict.fromkeys([row['source'], *choice.get('candidates', [])]))
    if any(not isinstance(c, str) or not c.strip() for c in candidates) or len(candidates) > 8:
        raise ValueError('Invalid saved candidate set')
    # Explicitly labelled negative controls; never offered as replacement text.
    reversed_words = ' '.join(row['source'].split()[::-1])
    controls = [('reversed_saved_words', reversed_words)] if reversed_words != row['source'] else []
    unrelated = next((r['source'] for r in saved['rows']
        if (r['end'] < start - 10 or r['start'] > end + 10)
        and r['source'] != row['source'] and 0 < len(r['source']) <= 200), None)
    if unrelated:
        controls.append(('different_saved_cue', unrelated))
    report = {'job_id': saved['job_id'], 'row_id': row_id, 'start': start, 'end': end,
        'language': language, 'audio_map': source_audio_map(saved), 'NOT_a_quality_verdict': True,
        'method': 'Teacher-forced token log probabilities, real audio minus equal-length zero audio',
        'models': []}
    output = Path('/results/candidate-scores.json')
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(prefix='subsynq-candidate-score-') as folder:
        path = Path(folder) / 'audio.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(start), '-i', str(media),
            '-t', str(end-start), '-map', source_audio_map(saved), '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', '-y', str(path)], check=True, timeout=90)
        report['audio_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        waveform = decode_audio(str(path), sampling_rate=16000)
        if abs(len(waveform) - round((end-start)*16000)) > 1:
            raise ValueError('Candidate scoring waveform coverage mismatch')
        for name in names:
            began = time.monotonic()
            model = worker.WhisperModel(name, device='cpu', compute_type=compute_type, cpu_threads=6,
                                        download_root='/models', local_files_only=True)
            try:
                texts = [(None, c) for c in candidates] + controls
                result = {'model': name, 'compute_type': compute_type, 'seconds': time.monotonic()-began,
                    'candidates': forced_candidate_scores(model, waveform, language, texts)}
                result['seconds'] = time.monotonic() - began
                report['models'].append(result)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(result, ensure_ascii=False), flush=True)
            finally:
                model.model.unload_model()
                del model
                gc.collect()


def contrast_candidates(reference, donor):
    """Deterministic close corruptions, preserving combining marks within units."""
    import unicodedata
    def units(text):
        words = text.split()
        if len(words) >= 4:
            return words, ' '
        chars = []
        for char in text:
            if unicodedata.category(char).startswith('M') and chars:
                chars[-1] += char
            else:
                chars.append(char)
        return chars, ''
    source, separator = units(reference)
    other, _ = units(donor)
    if len(source) < 4:
        raise ValueError('Calibration references need at least four units')
    index = next((i for i in sorted(range(1, len(source)-1), key=lambda i: abs(i-len(source)//2))
                  if source[i] != source[i+1]), None)
    if index is None:
        raise ValueError('No distinct adjacent reference units')
    replacement = next((unit for unit in other if unit != source[index] and unit.strip()), None)
    if replacement is None:
        raise ValueError('No distinct same-language substitution')
    swapped = list(source)
    swapped[index], swapped[index+1] = swapped[index+1], swapped[index]
    replaced = list(source)
    replaced[index] = replacement
    candidates = [('reference', reference),
        ('deletion', separator.join(source[:index] + source[index+1:])),
        ('adjacent_swap', separator.join(swapped)),
        ('substitution', separator.join(replaced)), ('unrelated_reference', donor)]
    if len({text for _, text in candidates}) != len(candidates):
        raise ValueError('Calibration alternatives must be distinct')
    return candidates


def calibration_decision(models, *, without_reference=False):
    """Predeclared diagnostic rule; intentionally not a production acceptance rule."""
    if len(models) != 2 or {model.get('model') for model in models} != {'large-v3', 'large-v2'}:
        return None
    selected = []
    for model in models:
        pool = [c for c in model['candidates'] if not without_reference or c['synthetic_control'] != 'reference']
        if any(not math.isfinite(c['mean_audio_gain']) for c in pool):
            return None
        ranked = sorted(pool, key=lambda c: c['mean_audio_gain'], reverse=True)
        if len(ranked) < 2 or ranked[0]['mean_audio_gain'] < 3 or (
                ranked[0]['mean_audio_gain'] - ranked[1]['mean_audio_gain'] < .3):
            return None
        selected.append(ranked[0]['synthetic_control'])
    return selected[0] if len(set(selected)) == 1 else None


def candidate_calibration():
    """Small public reference corpus, temporary audio, no media/config writes."""
    import tarfile
    import urllib.request
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-calibration', action='store_true')
    parser.add_argument('--languages', default='en,de,ja,ta')
    parser.add_argument('--samples-per-language', type=int, default=8)
    args = parser.parse_args()
    codes = {'en': 'en_us', 'de': 'de_de', 'ja': 'ja_jp', 'ta': 'ta_in'}
    languages = args.languages.split(',')
    if (not languages or len(set(languages)) != len(languages) or set(languages) - codes.keys()
            or not 2 <= args.samples_per_language <= 12):
        parser.error('Choose distinct supported languages and 2–12 samples per language')
    output = Path('/results/candidate-calibration.json')
    if output.exists():
        raise FileExistsError(output)
    def fetch(url, limit):
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(limit+1)
        if len(data) > limit:
            raise ValueError('Reference metadata exceeded its size bound')
        return data
    metadata = json.loads(fetch('https://huggingface.co/api/datasets/google/fleurs', 4*1024**2))
    revision = metadata['sha']
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Invalid reference dataset revision')
    report = {'dataset': 'google/fleurs', 'revision': revision, 'split': 'test',
        'licence': 'CC-BY-4.0', 'reference_field': 'raw_transcription', 'device': 'cpu',
        'status': 'running', 'NOT_a_film_quality_verdict': True,
        'selection': 'First eligible distinct sentence IDs in archive order; 3–15s, 20–160 characters',
        'rule_frozen_before_test': {'minimum_audio_gain': 3., 'minimum_margin': .3,
                                  'both_whisper_models_same_candidate': True}, 'items': []}
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    class BoundedStream:
        def __init__(self, response):
            self.response, self.count = response, 0
        def read(self, size=-1):
            if size < 0:
                raise ValueError('Unbounded archive read')
            data = self.response.read(size)
            self.count += len(data)
            if self.count > 96*1024**2:
                raise ValueError('Reference archive prefix exceeded 96 MiB')
            return data
    try:
        with tempfile.TemporaryDirectory(prefix='candidate-reference-', dir='/results') as tmp:
            clips = []
            for language in languages:
                base = f'https://huggingface.co/datasets/google/fleurs/resolve/{revision}/data/{codes[language]}'
                tsv = fetch(base + '/test.tsv', 4*1024**2)
                rows = [row for row in csv.reader(tsv.decode('utf-8').splitlines(), delimiter='\t') if len(row) >= 3]
                by_name = {Path(row[1]).name: row for row in rows}
                used, picked = set(), []
                with urllib.request.urlopen(base+'/audio/test.tar.gz', timeout=60) as response:
                    stream = BoundedStream(response)
                    with tarfile.open(fileobj=stream, mode='r|gz') as archive:
                        for member in archive:
                            row = by_name.get(Path(member.name).name)
                            if (not member.isfile() or not row or row[0] in used or
                                    not 0 < member.size <= 2*1024**2 or not 20 <= len(row[2]) <= 160):
                                continue
                            blob = archive.extractfile(member).read()
                            path = Path(tmp) / f'{language}-{len(picked)}.wav'
                            path.write_bytes(blob)
                            audio = decode_audio(str(path), sampling_rate=16000)
                            seconds = len(audio)/16000
                            if not 3 <= seconds <= 15:
                                path.unlink()
                                continue
                            item = {'language': language, 'sentence_id': row[0], 'file': row[1],
                                'reference': row[2], 'seconds': seconds, 'audio_sha256': hashlib.sha256(blob).hexdigest(),
                                'models': []}
                            used.add(row[0])
                            picked.append((item, path))
                            if len(picked) == args.samples_per_language:
                                break
                if len(picked) != args.samples_per_language:
                    raise ValueError(f'Insufficient bounded reference sample for {language}')
                for i, (item, path) in enumerate(picked):
                    item['texts'] = contrast_candidates(item['reference'], picked[(i+1) % len(picked)][0]['reference'])
                    report['items'].append(item)
                    clips.append((item, path))
                print(json.dumps({'event': 'reference_prepared', 'language': language,
                    'samples': len(picked), 'archive_bytes_read': stream.count, 'revision': revision}), flush=True)
                save()
            for name in ('large-v3', 'large-v2'):
                model = WhisperModel(name, device='cpu', compute_type='int8', cpu_threads=6,
                                     download_root='/models', local_files_only=True)
                try:
                    for item, path in clips:
                        began = time.monotonic()
                        result = {'model': name, 'candidates': forced_candidate_scores(
                            model, decode_audio(str(path), sampling_rate=16000), item['language'], item['texts'])}
                        result['seconds'] = time.monotonic()-began
                        item['models'].append(result)
                        if len(item['models']) == 2:
                            item['full_pool_decision'] = calibration_decision(item['models'])
                            item['missing_reference_decision'] = calibration_decision(item['models'], without_reference=True)
                        save()
                        print(json.dumps({'event': 'reference_scored', 'model': name,
                            'language': item['language'], 'sentence_id': item['sentence_id'],
                            'seconds': result['seconds']}), flush=True)
                finally:
                    model.model.unload_model()
                    del model
                    gc.collect()
        report['status'] = 'completed'
    except BaseException as error:
        report.update(status='error', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        save()
        print(json.dumps({'event': 'reference_audio_removed', 'status': report['status']}), flush=True)


def nbest_readings(model, waveform, language, count):
    """Expose decoder alternatives without injecting any expected words."""
    from faster_whisper.audio import pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual,
                          task='transcribe', language=language)
    encoded = model.encode(pad_or_trim(model.feature_extractor(waveform)))
    result = model.model.generate(encoded, [list(tokenizer.sot_sequence) + [tokenizer.no_timestamps]],
        beam_size=count, num_hypotheses=count, return_scores=True, return_no_speech_prob=True,
        max_length=256, suppress_blank=True, suppress_tokens=[-1], sampling_temperature=1)[0]
    if len(result.sequences_ids) != len(result.scores) or not result.sequences_ids:
        raise ValueError('Invalid N-best decoder result')
    readings = []
    for tokens, score in zip(result.sequences_ids, result.scores):
        if not math.isfinite(score):
            raise ValueError('Nonfinite decoder score')
        readings.append({'text': tokenizer.decode(tokens).strip(), 'tokens': tokens,
                         'score': score, 'reached_token_cap': len(tokens) >= 256})
    return {'hypotheses': readings, 'unique_texts': len({r['text'] for r in readings}),
            'no_speech_probability': result.no_speech_prob,
            'multiple_hypotheses_are_not_independent_readers': True}


def main():
    global worker
    if '--candidate-calibration' in sys.argv:
        candidate_calibration()
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--media', required=True)
    parser.add_argument('--start', type=float)
    parser.add_argument('--end', type=float)
    parser.add_argument('--models', nargs='+', choices=['qwen', 'large-v2', 'large-v3'],
                        default=['qwen', 'large-v2'])
    parser.add_argument('--language', help='Optional explicit Whisper language code; Qwen still auto-detects.')
    parser.add_argument('--beam-size', type=int, choices=(1, 5, 10), default=5,
                        help='Bounded diagnostic Whisper search width; production decoding is unchanged.')
    parser.add_argument('--compute-type', choices=('int8', 'float32'), default='int8',
                        help='Diagnostic CPU arithmetic on existing checkpoint; does not restore precision lost in stored weights.')
    parser.add_argument('--audio-track', type=int, help='ffprobe stream index; defaults to the saved job selection.')
    parser.add_argument('--qwen-url', help='Existing Qwen service URL for an isolated diagnostic container.')
    parser.add_argument('--dialogue-only', action='store_true', help='Diagnostic FFmpeg stereo dialogue extraction; never changes original media.')
    parser.add_argument('--channel', choices=('mono', 'left', 'right', 'center'), default='mono',
                        help='Diagnostic channel selection before ASR; production audio is unchanged.')
    parser.add_argument('--pipeline-source', action='store_true', help='Full CPU ASR only; results in /results, never a real job or SRT.')
    parser.add_argument('--score-candidates-row', type=int,
                        help='Measure saved alternatives against actual audio and silence; never changes source text.')
    parser.add_argument('--nbest', type=int, choices=range(2, 11),
                        help='Expose 2–10 Whisper decoder hypotheses from a short original audio window, without expected words.')
    parser.add_argument('--code-switch-check', action='store_true', help='Replay bounded code-switch confirmation from saved raw evidence; no job or SRT writes.')
    parser.add_argument('--reread-check', action='store_true', help='Read selected saved rows through the actual reread path; write only /results.')
    parser.add_argument('--qwen-language', help='Diagnostic Qwen hint for a supported language; independent of Whisper language.')
    parser.add_argument('--qwen-alignment', action='store_true', help='Include the existing Qwen forced aligner in an ordinary short-window probe.')
    parser.add_argument('--export-only', action='store_true', help='Export native-channel/rate listening clips without any AI imports or model calls.')
    parser.add_argument('--frame-times', nargs='+', type=float,
                        help='Export up to 12 original video frames to /results only, without AI imports or ASR.')
    parser.add_argument('--only-rows', nargs='*', type=int, default=[], help='Extra row IDs to export, in addition to saved source_unclear rows.')
    parser.add_argument('--context', type=float, default=3., help='Listening export context on either side, in seconds.')
    parser.add_argument('--more-jobs', nargs='*', type=int, default=[], help='Additional saved jobs in the same report directory, export only.')
    parser.add_argument('--extra-rows-json', default='{}', help='Additional export row IDs by job ID; uncertain rows are always included.')
    args = parser.parse_args()
    if args.frame_times is not None:
        if (args.export_only or args.pipeline_source or args.code_switch_check or args.reread_check
                or args.score_candidates_row is not None or args.nbest is not None or args.qwen_alignment
                or args.start is not None or args.end is not None or args.audio_track is not None
                or args.dialogue_only or args.channel != 'mono' or args.only_rows or args.more_jobs
                or args.extra_rows_json != '{}' or args.context != 3. or args.language or args.qwen_language):
            parser.error('--frame-times is a separate model-free original-video export')
        saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
        export_witness_frames(saved, Path(args.media), args.frame_times)
        return
    if args.qwen_alignment and (args.export_only or args.pipeline_source or args.code_switch_check or args.reread_check
            or args.score_candidates_row is not None or args.nbest is not None or 'qwen' not in args.models):
        parser.error('--qwen-alignment requires an ordinary short-window Qwen probe')
    if args.reread_check and (args.export_only or args.pipeline_source or args.code_switch_check
            or args.score_candidates_row is not None or args.nbest is not None or args.start is not None
            or args.end is not None or args.audio_track is not None or args.dialogue_only or args.channel != 'mono'
            or args.language or args.qwen_language or not 1 <= len(args.only_rows) <= 8):
        parser.error('--reread-check requires 1–8 saved row IDs and the original stream/language, without window overrides')
    if args.nbest is not None and (not args.language or 'qwen' in args.models or args.export_only
            or args.pipeline_source or args.code_switch_check or args.score_candidates_row is not None
            or args.dialogue_only or args.channel != 'mono' or args.audio_track is not None or args.beam_size != 5):
        parser.error('N-best requires explicit language, existing Whisper models and an unfiltered saved-stream window')
    if args.score_candidates_row is not None and (args.export_only or args.pipeline_source or args.code_switch_check
            or args.dialogue_only or args.channel != 'mono' or args.audio_track is not None):
        parser.error('Candidate scoring uses only the saved original stream and an explicit short window')
    if args.channel != 'mono' and (args.dialogue_only or args.export_only or args.pipeline_source or args.code_switch_check):
        parser.error('--channel requires ordinary unfiltered audio-window diagnostics')
    saved = json.loads(Path(args.report).read_text(encoding='utf-8'))
    if args.reread_check:
        if Path(args.media).resolve() != Path(saved['media_path']).resolve():
            parser.error('Reread requires the original saved media path')
        if len(set(args.only_rows)) != len(args.only_rows) or not set(args.only_rows) <= {r['id'] for r in saved['rows']}:
            parser.error('Reread requires unique existing row IDs')
        import copy
        import worker
        source_audio_map(saved)
        job = {'id': -100000 - saved['job_id'], 'media_path': args.media,
               'audio_stream': saved['audio_track']['index'], 'audio_choice': saved['audio_track'],
               'language_policy': saved.get('language_policy')}
        rows = copy.deepcopy(saved['rows'])
        for row in rows:
            row['end'] = row.get('reading_time_source_end', row['end'])
        before = copy.deepcopy(rows)
        worker.MODEL_JOB.device = 'cpu'
        if args.qwen_url:
            worker.QWEN_ASR_URL = args.qwen_url.rstrip('/')
            worker.QWEN_ASR_ENABLED = True
        with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'), patch.object(worker, 'REREAD_ENABLED', True):
            changed = worker.reread_flagged_rows(job, rows, args.only_rows)
        output = {'job': job, 'before': before, 'rows': rows, 'changed': changed, 'NOT_a_quality_verdict': True}
        Path('/results/reread-check.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
        print(json.dumps({'changed': changed, 'records': job.get('reread_rows')}, ensure_ascii=False), flush=True)
        return
    if args.export_only:
        if args.pipeline_source or args.code_switch_check or args.dialogue_only or args.start is not None or args.end is not None or args.audio_track is not None:
            parser.error('--export-only uses the saved stream and cue windows without analysis/filter/track overrides')
        jobs = [saved['job_id']] + args.more_jobs
        extra = json.loads(args.extra_rows_json)
        if (len(jobs) != len(set(jobs)) or any(type(i) is not int or i <= 0 for i in jobs)
                or not isinstance(extra, dict) or not set(extra) <= {str(i) for i in jobs}
                or any(not isinstance(v, list) or any(type(i) is not int for i in v) for v in extra.values())):
            parser.error('Export requires unique positive job IDs and lists of existing integer row IDs')
        manifests = []
        for job_id in jobs:
            current = saved if job_id == saved['job_id'] else json.loads(
                Path(args.report).with_name(f'job-{job_id}.json').read_text(encoding='utf-8'))
            if current['job_id'] != job_id:
                raise ValueError('Source report job ID does not match its filename')
            media = Path(args.media) if job_id == saved['job_id'] else Path(current['media_path'])
            ids = (args.only_rows if job_id == saved['job_id'] else []) + extra.get(str(job_id), [])
            manifests.append(export_listening_clips(current, media, ids, args.context))
        pack_listening_clips(manifests)
        return
    if args.only_rows or args.context != 3. or args.more_jobs or args.extra_rows_json != '{}':
        parser.error('Listening options require --export-only')
    import worker
    if args.score_candidates_row is not None:
        start, end = source_window(saved['rows'], args.start, args.end)
        score_saved_candidates(saved, Path(args.media), args.score_candidates_row, start, end,
                               args.language, args.models, args.compute_type)
        return
    if args.code_switch_check:
        if args.pipeline_source or args.start is not None or args.end is not None or args.dialogue_only:
            parser.error('--code-switch-check uses the saved full trace and cannot be combined with audio-window modes')
        trace = json.loads(Path(args.report).with_suffix('.asr-trace.json').read_text())
        if trace.get('job_id') != saved['job_id']:
            parser.error('Trace and source must belong to the same job')
        job = {'id': -100000 - saved['job_id'], 'audio_stream': saved['audio_track']['index'],
               'audio_choice': saved['audio_track'], 'language_policy': saved.get('language_policy'),
               'norwegian_reader': trace.get('norwegian_reader'), 'swedish_reader': trace.get('swedish_reader')}
        models = worker.SequentialASRModels(None)
        duration = max([r['end'] for r in trace['final']] + [w.get('end', 0)
            for windows in trace['used_windows'].values() for w in windows])
        worker.MODEL_JOB.device = 'cpu'
        try:
            with patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
                rows = worker.read_code_switch_checks(job, Path(args.media), trace['final'],
                    {'qwen': trace.get('consensus_qwen') or [], 'v2': trace.get('v2') or [], 'nb': trace.get('nb') or []}, models, duration)
            Path('/results/code-switch.json').write_text(json.dumps({'job': job, 'before': trace['final'], 'rows': rows}, ensure_ascii=False, indent=2))
            print(json.dumps({'changes': (job.get('code_switch_checks') or {}).get('changed', [])}, ensure_ascii=False))
        finally:
            models.close()
        return
    if args.qwen_url:
        worker.QWEN_ASR_URL = args.qwen_url.rstrip('/')
        worker.QWEN_ASR_ENABLED = True
    if args.pipeline_source:
        if args.start is not None or args.end is not None or args.dialogue_only:
            parser.error('--pipeline-source reads the full source, without --start/--end')
        source_audio_map(saved, args.audio_track)
        if args.audio_track is not None:
            saved['audio_track'] = {**saved['audio_track'], 'index': args.audio_track}
        pipeline_source(saved, Path(args.media))
        return
    start, end = source_window(saved['rows'], args.start, args.end)
    nbest_output = Path('/results/nbest-comparison.json')
    if args.nbest is not None:
        if end-start > 15:
            parser.error('N-best windows are bounded to 15 seconds')
        if nbest_output.exists():
            raise FileExistsError(nbest_output)
        nbest_report = {'job_id': saved['job_id'], 'start': start, 'end': end,
            'language': args.language, 'audio_map': source_audio_map(saved), 'nbest': args.nbest,
            'compute_type': args.compute_type,
            'status': 'running', 'NOT_a_quality_verdict': True, 'expected_words_supplied': False, 'models': []}
    audio_map = source_audio_map(saved, args.audio_track)
    audio_filter = 'dialoguenhance=original=0:enhance=1:voice=2,pan=mono|c0=c2' if args.dialogue_only else None
    if args.channel != 'mono':
        streams = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', args.media]))['streams']
        selected = next(stream for stream in streams if stream['index'] == int(audio_map.split(':')[1]) and stream['codec_type'] == 'audio')
        audio_filter = channel_filter(args.channel, selected)
    print(json.dumps({'event': 'source_comparison', 'start': start, 'end': end, 'audio_filter': audio_filter, 'beam_size': args.nbest or args.beam_size, 'nbest': args.nbest, 'compute_type': args.compute_type,
                      'audio_map': audio_map,
                      'saved_rows': saved['rows'], 'NOT_a_quality_verdict': True}, ensure_ascii=False), flush=True)
    with tempfile.TemporaryDirectory(prefix='subsynq-source-probe-') as folder:
        audio = Path(folder) / 'audio.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', args.media,
                        '-t', str(end - start), '-map', audio_map] + (['-af', audio_filter] if audio_filter else []) + ['-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', '-y', str(audio)], check=True, timeout=90)
        for name in args.models:
            print(json.dumps({'event': 'reading_started', 'model': name}), flush=True)
            began = time.monotonic()
            if name == 'qwen':
                ready = False
                try:
                    worker.wait_for_qwen(attempts=1)
                    ready = True
                    reading = (worker.qwen_transcribe if args.qwen_alignment else worker.qwen_identify)(audio, args.qwen_language, attempts=1)
                    print(json.dumps({'model': name, 'seconds': time.monotonic() - began,
                                      **reading}, ensure_ascii=False), flush=True)
                finally:
                    if ready:
                        worker.qwen_unload(attempts=1)
                continue
            model = worker.WhisperModel(name, device='cpu', compute_type=args.compute_type, cpu_threads=6,
                                        download_root='/models', local_files_only=True)
            try:
                if args.nbest is not None:
                    from faster_whisper.audio import decode_audio
                    nbest_report['audio_sha256'] = hashlib.sha256(audio.read_bytes()).hexdigest()
                    result = {'model': name, **nbest_readings(model, decode_audio(str(audio)), args.language, args.nbest)}
                    result['seconds'] = time.monotonic()-began
                    nbest_report['models'].append(result)
                    nbest_output.write_text(json.dumps(nbest_report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    continue
                segments, info = model.transcribe(str(audio), language=args.language, beam_size=args.beam_size,
                    multilingual=args.language is None,
                    temperature=0, condition_on_previous_text=False, word_timestamps=True,
                    vad_filter=False, hallucination_silence_threshold=2)
                rows = [{'start': start + segment.start, 'end': start + segment.end,
                         'text': segment.text.strip(), 'avg_logprob': segment.avg_logprob,
                         'words': [{'start': start + word.start, 'end': start + word.end,
                                    'word': word.word, 'probability': word.probability}
                                   for word in (segment.words or [])]}
                        for segment in segments]
                print(json.dumps({'model': name, 'seconds': time.monotonic() - began,
                                  'language': info.language, 'rows': rows,
                                  'language_probability': info.language_probability,
                                  'text': ' '.join(row['text'] for row in rows)},
                                 ensure_ascii=False), flush=True)
            finally:
                del model
                gc.collect()
    if args.nbest is not None:
        nbest_report['status'] = 'completed'
        nbest_output.write_text(json.dumps(nbest_report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
