"""Bounded experiment around the language switch; keep all hypotheses."""
import json
import time
from pathlib import Path
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

source = Path('/config/asr-tests/20260909-160308/sample-960.wav')
audio = decode_audio(str(source))
model = WhisperModel('large-v3',device='cpu',compute_type='int8',cpu_threads=6,download_root='/models',local_files_only=True)
result = {'method':'Manual test windows, auto/en/fr hypotheses. No automatic quality approval.', 'windows':[]}
for start,end in [(980,988.8),(988.8,992.2),(992.2,1007),(1007,1020)]:
    waveform = audio[round((start-960)*16000):round((end-960)*16000)]
    candidates=[]
    for language in (None,'en','fr'):
        segments,info=model.transcribe(waveform,language=language,task='transcribe',vad_filter=False,multilingual=False,beam_size=5,condition_on_previous_text=False,word_timestamps=True,temperature=0)
        rows=[{'start':start+s.start,'end':start+s.end,'text':s.text.strip(),'avg_logprob':s.avg_logprob} for s in segments]
        candidate={'requested_language':language or 'auto','detected_language':info.language,'language_probability':info.language_probability,'segments':rows}
        candidates.append(candidate)
        print(f'{start}-{end} [{language or "auto"}]: '+ ' '.join(row['text'] for row in rows),flush=True)
    result['windows'].append({'start':start,'end':end,'candidates':candidates})
target=Path('/config/review/comparison')
target.mkdir(exist_ok=True)
(target/'language-switch.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
