"""Attach an after-the-fact reference assessment without changing model rows."""
import json
import os
from pathlib import Path

path = Path('/config/review/episode3/current.json')
review = json.loads(path.read_text(encoding='utf-8'))
review['evaluation'] = {
    'performed_after_generation': True,
    'reference_applied_to_rows': False,
    'note': ('Publicerat engelskt textspår från en annan videoversion. Formuleringar kan jämföras, '
             'men tidsstämplarna kan inte flyttas direkt till detta ljud.'),
    'strengths': [
        'Huvuddialogen om radion, brevduvorna och resan återfanns i rätt ordning.',
        'Engelska och franska språkbyten bevarades mitt i samtalet.',
        'GPU-körningen återfann fyra sammanhängande repliker om Sonia som CPU-körningen täckte med en felaktigt lång rad.',
    ],
    'confirmed_issues': [
        'How comes guy? motsvaras av Our comms guy i referensen.',
        'Merci, Mille motsvaras av Merci bien i referensen.',
        'I have to steal something, don’t I? ändrar betydelsen jämfört med If I have to steal something, I will.',
        'Den kapade CPU-raden Two days ago, to console her är en felaktig sammanblandning och ska inte godkännas.',
    ],
}
temporary = path.with_suffix('.tmp')
temporary.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temporary, path)
print(json.dumps({'strengths': 3, 'confirmed_issues': 4, 'rows_changed': 0}))
