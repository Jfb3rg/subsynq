"""Compare center-channel audio and downmix without supplying reference words."""
import json
import subprocess
import time
from pathlib import Path
from faster_whisper import WhisperModel

target = Path('/config/review/comparison/channel-test')
target.mkdir(parents=True, exist_ok=True)
media = next(Path('/tv').glob('*S01E01*.mkv'))
probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(media)]))
stream = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
if '5.1' not in stream.get('channel_layout',''):
    raise ValueError('This experiment requires a verified 5.1 center channel')
model = WhisperModel('large-v3',device='cpu',compute_type='int8',cpu_threads=6,download_root='/models',local_files_only=True)
result = {'media':media.name,'channel_layout':stream['channel_layout'],'correction_supplied_to_model':False,'runs':[]}
for channel in ('mono','center'):
    wav = target / (channel+'.wav')
    command = ['ffmpeg','-v','error','-nostdin','-y','-ss','997','-i',str(media),'-t','23','-map',f'0:{stream["index"]}']
    if channel == 'center':
        command += ['-af','pan=mono|c0=FC']
    subprocess.run(command+['-ac','1','-ar','16000',str(wav)],check=True,timeout=120)
    for task in ('transcribe','translate'):
        started=time.monotonic()
        segments,info=model.transcribe(str(wav),language='fr',task=task,vad_filter=False,beam_size=5,condition_on_previous_text=True,temperature=0)
        run={'channel':channel,'task':task,'segments':[{'start':997+s.start,'end':997+s.end,'text':s.text.strip()} for s in segments],'seconds':round(time.monotonic()-started,1)}
        result['runs'].append(run)
        (target/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(run,ensure_ascii=False),flush=True)
