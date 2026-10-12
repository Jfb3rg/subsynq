"""Compare audio context without feeding the user's correction to ASR."""
import json
from pathlib import Path
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

audio=decode_audio('/config/asr-tests/20260909-160052/preview.wav')
model=WhisperModel('large-v3',device='cpu',compute_type='int8',cpu_threads=6,download_root='/models',local_files_only=True)
results=[]
for start,end,language in ((997,1020,'fr'),(930,1020,None)):
    segments,info=model.transcribe(audio[start*16000:end*16000],language=language,multilingual=language is None,vad_filter=False,beam_size=5,condition_on_previous_text=True,word_timestamps=True,temperature=0)
    rows=[{'start':start+s.start,'end':start+s.end,'text':s.text.strip()} for s in segments]
    results.append({'start':start,'end':end,'language':language or 'auto','correction_supplied_to_model':False,'segments':rows})
    print(json.dumps(results[-1],ensure_ascii=False),flush=True)
target=Path('/config/review/comparison/context-test.json')
target.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
