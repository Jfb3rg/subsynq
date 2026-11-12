"""Compare center-channel audio and downmix without supplying reference words."""
import argparse
import json
import math
import subprocess
import time
from pathlib import Path
from faster_whisper import WhisperModel

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('media', type=Path)
parser.add_argument('--start', type=float, required=True)
parser.add_argument('--duration', type=float, required=True)
parser.add_argument('--language', help='Optional Whisper language code')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if not args.media.is_file():
    parser.error('media must be an existing file')
if not math.isfinite(args.start) or args.start < 0:
    parser.error('--start must be finite and non-negative')
if not math.isfinite(args.duration) or not 0 < args.duration <= 60:
    parser.error('--duration must be between 0 and 60 seconds')
media = args.media
target = args.output
probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(media)]))
stream = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
if '5.1' not in stream.get('channel_layout',''):
    raise ValueError('This experiment requires a verified 5.1 center channel')
target.mkdir(parents=True, exist_ok=False)
model = WhisperModel('large-v3',device='cpu',compute_type='int8',cpu_threads=6,download_root='/models',local_files_only=True)
result = {'media':media.name,'channel_layout':stream['channel_layout'],'correction_supplied_to_model':False,'runs':[]}
for channel in ('mono','center'):
    wav = target / (channel+'.wav')
    command = ['ffmpeg','-v','error','-nostdin','-y','-ss',str(args.start),'-i',str(media),'-t',str(args.duration),'-map',f'0:{stream["index"]}']
    if channel == 'center':
        command += ['-af','pan=mono|c0=FC']
    subprocess.run(command+['-ac','1','-ar','16000',str(wav)],check=True,timeout=120)
    for task in ('transcribe','translate'):
        started=time.monotonic()
        segments,info=model.transcribe(str(wav),language=args.language,task=task,vad_filter=False,beam_size=5,condition_on_previous_text=True,temperature=0)
        run={'channel':channel,'task':task,'segments':[{'start':args.start+s.start,'end':args.start+s.end,'text':s.text.strip()} for s in segments],'seconds':round(time.monotonic()-started,1)}
        result['runs'].append(run)
        (target/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(run,ensure_ascii=False),flush=True)
