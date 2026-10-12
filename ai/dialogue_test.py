"""Translation test using an explicitly user-assisted source correction."""
import json
import urllib.request
from pathlib import Path

dialogue="Je veux savoir ce que tu fous ici, toi.\nIl est blessé. L'Américain nous a aidés.\nC'est tout.\nSi, je t'ai déjà vu, toi."
request={'model':'gemma3:12b','stream':False,'keep_alive':0,
 'system':'Översätt följande sammanhängande franska filmdialog till naturlig svenska. Behåll betydelse, personer och tilltal. Rätta inte berättelsen och lägg inte till information. Ge bara svensk dialog, ingen förklaring. Gör ingen tidsättning eller undertextindelning ännu.',
 'prompt':dialogue,'options':{'temperature':0,'num_ctx':4096,'num_predict':500,'num_thread':6,'seed':42}}
req=urllib.request.Request('http://subsynq-translate-engine:11434/api/generate',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=240) as response:
    result=json.load(response)
record={'source':dialogue,'source_correction':'User confirmed meaning amerikanen; full French wording is an assistant proposal. Not an unassisted ASR result.','request':request,'result':result}
Path('/config/review/comparison/dialogue-test.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(result['response'],flush=True)
