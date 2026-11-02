"""Local language evidence and a single, timed kana insertion; no model prompts."""
import math
import re
import statistics
import unicodedata

from asr_windows import _normalized, _similarity, _is_boilerplate
from language_gaps import whisper_language


def language_group_plans(job, rows, ids):
    policy = job.get('language_policy') or {}
    base = policy.get('effective_language')
    if not base or policy.get('filename_hint') != base or policy.get('status') != 'confirmed':
        return []  # Manual/unknown policies are never overridden.
    ordered = sorted(rows, key=lambda r: r['start'])
    groups, group = [], []
    for row in ordered:
        eligible = (row['id'] in ids and not row.get('confirmed_source_words')
                    and not row.get('confirmed_lead_onset'))
        if group and (not eligible or row['start']-group[-1]['end'] > .25):
            groups.append(group); group = []
        if eligible:
            group.append(row)
    if group:
        groups.append(group)
    plans = []
    for group in groups:
        if not 2 <= len(group) <= 3:
            continue
        if any(a['end']>b['start'] for a,b in zip(group,group[1:])):
            continue
        first, last = group[0], group[-1]
        a, b = float(first['start']), float(last.get('reading_time_source_end', last['end']))
        source = ' '.join(r['source'] for r in group)
        if not 2 <= b-a <= 8 or sum(c.isalpha() for c in source) < 32:
            continue
        index, final = ordered.index(first), ordered.index(last)
        left = ordered[index-1]['end']+.04 if index else 0.
        right = ordered[final+1]['start']-.04 if final+1 < len(ordered) else b+1
        windows = [(max(left,a-pad),min(right,b+pad)) for pad in (.3,.6)]
        if (windows[1][0] > windows[0][0]-.15 or windows[1][1] < windows[0][1]+.15
                or not all(0 <= x < a < b < y for x,y in windows)):
            continue
        plans.append(dict(base=base,source=source,windows=windows,
                          rows=[{k:r[k] for k in ('id','source','start','end')} for r in group]))
    return plans


def confirmed_group_language(plan, readings):
    if (len(readings)!=6 or {(r['name'],r['window']) for r in readings} != {
            (n,w) for n in ('precision','v2','qwen') for w in (0,1)}):
        return None
    texts = {_normalized(r.get('text','')) for r in readings}
    if len(texts)!=1:
        return None
    text = next(iter(texts))
    if (not text or _is_boilerplate(text) or len(text.split())<8
            or _similarity(text,plan['source'])<.85):
        return None
    languages = set()
    for r in readings:
        language = whisper_language(r.get('language')) if r['name']=='qwen' else r.get('language')
        if not language:
            return None
        languages.add(language)
        if r['name']!='qwen' and (not .9 <= float(r.get('language_probability') or 0) <= 1
                                  or not .65 <= float(r.get('confidence') or 0) <= 1):
            return None
    return next(iter(languages)) if len(languages)==1 and plan['base'] not in languages else None


def local_language_override(job, row):
    policy = job.get('language_policy') or {}
    for evidence in job.get('reread_language_evidence') or []:
        plan = evidence['plan']
        if (policy.get('status')!='confirmed' or policy.get('filename_hint')!=plan['base']
                or policy.get('effective_language')!=plan['base']):
            continue
        own = next((r for r in plan['rows'] if r['id']==row['id']),None)
        if own and all(row.get(k)==own[k] for k in ('source','start','end')):
            language=confirmed_group_language(plan,evidence['readings'])
            if language:
                return language
        partition=confirmed_group_partition(plan,evidence['readings'])
        if partition:
            own=next((r for r in partition['rows'] if r['id']==row['id']),None)
            if own and all(row.get(k)==own[k] for k in ('source','start','end')):
                return partition['language']
    return None


def _word_forms(value):
    """Orthographic alternatives for matching ONLY; written source is never expanded."""
    raw=str(value).strip().replace('’',"'").casefold()
    result=[(tuple(_normalized(raw).split()),False)]
    match=re.fullmatch(r"([^\W\d_]+)'(s|re|ve|ll|d|m)[.,!?;:]*",raw)
    endings={'s':('is','has'),'re':('are',),'ve':('have',),'ll':('will',),'d':('had','would'),'m':('am',)}
    if match:
        result.extend(((match[1],ending),True) for ending in endings[match[2]])
    return result


def _source_alignment(words, source):
    """One unique mapping of all existing words; at most one extra leading token."""
    forms=[((),(),frozenset())]
    for i,word in enumerate(words):
        next_forms=[]
        for tokens,mapping,expanded in forms:
            for extra,is_expanded in _word_forms(word['word']):
                next_forms.append((tokens+extra,mapping+(i,)*len(extra),expanded|({i} if is_expanded else set())))
        if len(next_forms)>64:
            return None
        forms=next_forms
    matches=set()
    for tokens,mapping,expanded in forms:
        prefix=len(tokens)-len(source)
        if prefix in (0,1) and tokens[prefix:]==tuple(source):
            matches.add((mapping[prefix:],expanded,tokens[:prefix]))
    return next(iter(matches)) if len(matches)==1 else None


def confirmed_group_partition(plan, readings):
    """Move an existing boundary out of a spoken contraction to an evidenced comma.

    Every written character stays in the joined source. Nothing here approves
    an extra heard word or clears source uncertainty. Timings are an estimate
    from four independent window decodes of two Whisper versions, with Qwen
    independently agreeing on the whole utterance in both windows.
    """
    language=confirmed_group_language(plan,readings)
    group=plan['rows']
    if language!='en' or len(group)!=2:
        return None
    left,right=group
    if (abs(left['end']-right['start'])>.02 or left['source'].rstrip().endswith(('.', '?','!',';',':'))):
        return None
    joined=left['source']+' '+right['source']
    source=_normalized(joined).split()
    edge=len(_normalized(left['source']).split())
    if joined!=plan['source'] or edge<4 or len(source)-edge<4:
        return None
    aligned=[]
    for reading in readings:
        if reading['name']=='qwen':
            words=[{'word':w} for w in reading['text'].split()]
        else:
            words=reading.get('words') or []
            if _normalized(' '.join(w['word'] for w in words))!=_normalized(reading['text']):
                return None
        match=_source_alignment(words,source)
        if not match:
            return None
        mapping,expanded,prefix=match
        cross=mapping[edge-1]
        if cross!=mapping[edge] or cross not in expanded or mapping.count(cross)!=2:
            return None
        if reading['name']!='qwen':
            try:
                a,b,p=(float(words[cross][k]) for k in ('start','end','probability'))
                if (not all(math.isfinite(v) for v in (a,b,p)) or not 0<b-a<=1.5 or not .6<=p<=1
                        or not a+.04<=left['end']<=b-.04):
                    return None
            except (KeyError,TypeError,ValueError):
                return None
        aligned.append((reading,words,mapping,prefix))
    # Only an existing comma BEFORE the bad boundary is eligible. No new
    # punctuation, word choice, row ID or cue count is supplied by a model.
    for comma in reversed(list(re.finditer(r',(?= )',left['source']))):
        first,second=joined[:comma.end()],joined[comma.end()+1:]
        split=len(_normalized(first).split())
        if not 4<=split<edge or len(_normalized(second).split())<4 or first+' '+second!=joined:
            continue
        anchors=[]
        valid=True
        for reading,words,mapping,prefix in aligned:
            before,after=mapping[split-1],mapping[split]
            if (before==after or _normalized(words[before]['word'])!=source[split-1]
                    or _normalized(words[after]['word'])!=source[split]
                    or not words[before]['word'].rstrip().endswith(',')):
                valid=False;break
            if reading['name']=='qwen':
                continue
            try:
                pair=[{k:float(w[k]) for k in ('start','end','probability')} for w in (words[before],words[after])]
                if (any(not all(math.isfinite(v) for v in w.values()) or not 0<w['end']-w['start']<=1.5
                        or not .8<=w['probability']<=1 for w in pair)
                        or not .06<=pair[1]['start']-pair[0]['end']<=.6):
                    valid=False;break
                anchors.append(dict(name=reading['name'],window=reading['window'],words=pair,
                                    gap=(pair[0]['end'],pair[1]['start'])))
            except (KeyError,TypeError,ValueError):
                valid=False;break
        if not valid or len(anchors)!=4:
            continue
        centres=[(r['gap'][0]+r['gap'][1])/2 for r in anchors]
        boundary=round(statistics.median(centres),3)
        if (max(centres)-min(centres)>.35
                or any(max(r['gap'][0]-boundary,boundary-r['gap'][1],0)>.12 for r in anchors)
                or not .25<=left['end']-boundary<=2.5
                or not .6<=boundary-left['start']<=7 or not .6<=right['end']-boundary<=7):
            continue
        return dict(language=language,rows=[dict(left,source=first,end=boundary),dict(right,source=second,start=boundary)],
                    boundary=boundary,previous_boundary=left['end'],anchors=anchors,
                    source_text_preserved=True,source_words_approved=False,
                    architectures=['whisper','qwen'],
                    unassigned_leading_words=list(aligned[0][3]))
    return None


def kana_slot(current, candidate):
    old, new = _normalized(current).replace(' ',''), _normalized(candidate).replace(' ','')
    if (not 4 <= len(old) <= 24 or len(new)!=len(old)+1
            or not all(c.isalpha() and unicodedata.name(c,'').startswith(('CJK','HIRAGANA','KATAKANA')) for c in old+new)):
        return None
    slots=[i for i in range(1,len(new)-1) if new[:i]+new[i+1:]==old
           and unicodedata.name(new[i],'').startswith('HIRAGANA LETTER')]
    return (slots[0],new[slots[0]],new) if len(slots)==1 else None


def confirmed_kana_insertion(row, readings, neighbours=()):
    if (len(readings)!=6 or {(r['name'],r['window']) for r in readings} != {
            (n,w) for n in ('precision','v2','qwen') for w in (0,1)}):
        return None,None
    qwen=[r for r in readings if r['name']=='qwen']
    slot=kana_slot(row['source'],qwen[0]['text'])
    if not slot or any(_normalized(r['text']).replace(' ','')!=slot[2] for r in qwen):
        return None,None
    index,letter,expected=slot
    a,b=float(row['start']),float(row.get('reading_time_source_end',row['end']))
    if any(n['start']<b and n['end']>a for n in neighbours):
        return None,None
    right=min([b+.15]+[n['start']-.04 for n in neighbours if n['start']>=b])
    proofs=[]
    for reading in readings:
        if reading['name']=='qwen':
            continue
        selected, outside = [], []
        try:
            for w in reading.get('words') or []:
                token=_normalized(w['word']).replace(' ','')
                if not token:
                    continue
                start,end,p=(float(w[k]) for k in ('start','end','probability'))
                if end<=a-.04:
                    outside.append(dict(w));continue
                if (not all(math.isfinite(v) for v in (start,end,p)) or not a-.001<=start<end<=right
                        or not 0<=p<=1 or end-start>1.5):
                    return None,None
                selected.append(dict(token=token,start=start,end=end,probability=p))
            if ''.join(w['token'] for w in selected)!=expected:
                return None,None
            positions=[i for i,w in enumerate(selected) if ''.join(x['token'] for x in selected[:i])==expected[:index]
                       and w['token']==letter]
            if len(positions)!=1:
                return None,None
            triple=selected[positions[0]:positions[0]+3]
            if (len(triple)!=3 or [w['token'] for w in triple]!=list(expected[index:index+3])
                    or any(w['probability']<.8 for w in triple)
                    or any(x['end']>y['start']+.02 for x,y in zip(selected,selected[1:]))):
                return None,None
            proofs.append(dict(name=reading['name'],window=reading['window'],words=triple,outside_before=outside))
        except (KeyError,TypeError,ValueError):
            return None,None
    if any(max(p['words'][i][k] for p in proofs)-min(p['words'][i][k] for p in proofs)>.35
           for i in range(3) for k in ('start','end')):
        return None,None
    # Locate the same existing characters without normalizing away punctuation.
    letters=[i for i,c in enumerate(row['source']) if c.isalpha()]
    if len(letters)!=len(expected)-1:
        return None,None
    position=letters[index]
    return row['source'][:position]+letter+row['source'][position:],dict(letter=letter,proofs=proofs,
        architectures=['whisper','qwen'],times_preserved=True)
