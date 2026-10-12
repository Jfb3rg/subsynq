"""Conservative experimental consensus; agreement does not prove correctness."""
import re


def normalize(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def apply_consensus(rows, experiment):
    result = []
    for original in rows:
        row = dict(original)
        for window in experiment['windows']:
            if not window['start'] <= row['start'] < row['end'] <= window['end']:
                continue
            candidates = window['candidates']
            if {candidate['requested_language'] for candidate in candidates} != {'auto','en','fr'} or len(candidates) != 3:
                continue
            matches=[]
            for candidate in candidates:
                overlapping=[segment for segment in candidate['segments'] if min(segment['end'],row['end'])-max(segment['start'],row['start']) > (row['end']-row['start'])*.5]
                if len(overlapping) == 1:
                    matches.append(overlapping[0]['text'])
            if len(matches) == 3 and len({normalize(text) for text in matches}) == 1 and normalize(matches[0]) != normalize(row['source']):
                row['source_before'] = row['source']
                row['source'] = matches[0]
                row['source_note'] = 'Ny textkandidat: tre korttester överens. Behöver kontrolleras mot ljudet.'
        result.append(row)
    return result
