import json
import urllib.request
import os
from pathlib import Path
request = {
 'model':os.environ.get('SUBSYNQ_TRANSLATION_MODEL','qwen3:8b'),'stream':False,'format':'json','keep_alive':0,
 'system':'Du är en professionell svensk undertextöversättare. Översätt replikerna till idiomatisk, grammatisk svenska. Undvik ordagranna kalkeringar. Behåll betydelse, ton och tilltal. Imperativ ska vara svenska imperativ. Skriv endast JSON med samma radnummer som nycklar och den svenska översättningen som värden. Ingen förklaring.',
 'prompt':json.dumps({'1':'Assis-toi.','2':'Sit down, he said.','3':'Attention, mec, derrière.','4':'Our countries fought together during World War II.','5':'You can help us. We can help you find a way.'}),
 'options':{'temperature':0,'num_ctx':4096,'num_predict':700,'num_thread':6,'seed':42}
}
req=urllib.request.Request('http://subsynq-translate-engine:11434/api/generate',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=180) as response:
    result=json.load(response)
target = Path('/config/review/comparison')
target.mkdir(exist_ok=True)
(target/(request['model'].replace(':','-')+'-check.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(result['response'],flush=True)
