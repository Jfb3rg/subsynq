"""Which reader writes the text of a language (FLEURS test 2026-10-19, STATUS night 35).

SWITCHED OFF BY DEFAULT (STATUS 2026-10-19, night 36). On read speech (FLEURS) Qwen3-ASR (another model family, already in the chain) reads Hindi
(12.6 % wrong words against Whisper large-v3's 24.8 %), Korean (11.6 against 15.2) and Chinese (4.9 against 6.5) clearly better, so for those
languages Qwen can re-read the speech and its words replace Whisper's, every cue keeping Whisper's start and end. On real film audio it did not hold:
the HOPE trailer (Korean) with the burned-in English subtitles as witness, Qwen lost words Whisper had right ("mudfish", "putting out the mountain fires",
"reservists") in four of five cues, with cue-by-cue reading and with whole runs alike. The code stays as an opt-in experiment (SUBSYNQ_READER_PLAN=on); it
must not become the default without a measurement on film audio. The language is told by the script of what Whisper wrote (Devanagari, Hangul, Han).

Qwen reads whole runs of neighbouring cues, not one cue at a time: Whisper's cue borders often cut through a word (real KING trailer: "समंदर | है"),
and a clip that starts or ends inside a word gives garbage at the edge. The run's text is then cut into the cues again by aligning its words to the words
Whisper wrote. Pure logic here; the worker cuts the clips and asks the Qwen service (`read_cues_with_plan` in ai/worker.py).
"""
import os
import unicodedata

import translation_qa

# script (translation_qa.NON_LATIN_SCRIPTS) -> the plan for it. `needs` is the setting that switches it on: `on` or only `all`.
PLAN = {
    'Devanagari': {'language': 'Hindi', 'label': 'hindi', 'needs': 'on'},        # KING trailer: mixed, no witness
    'Hangul': {'language': 'Korean', 'label': 'koreanska', 'needs': 'on'},       # HOPE trailer: worse than Whisper
    'CJK': {'language': 'Chinese', 'label': 'kinesiska', 'needs': 'all'},        # FLEURS says better; no Chinese trailer yet
}
ENV_NAME = 'SUBSYNQ_READER_PLAN'        # unset/0/off: switched off (default); on: Hindi and Korean; all: also Chinese
PAD_SECONDS = .2                        # a clip starts and ends this much outside the first and last cue of a run
MAXIMUM_GAP = 1.5                       # cues closer than this (seconds) belong to one run
MAXIMUM_RUN_SECONDS = 25.0              # Qwen's own limit is near 30 s of speech
KANA = ('぀', 'ヿ')
MIN_SCRIPT_SHARE = .6                   # of the letters of Qwen's text that must be in the plan's script
LENGTH_RATIO = (.5, 2.0)                # Qwen's letters against Whisper's for the same run (only when Whisper wrote at least 6 letters)
CUE_LENGTH_RATIO = (.34, 3.0)           # the same for one cue after the words are cut into the cues again
LOOP_REPEATS = 4
INDEL_COST = .7                         # a missing or extra word; a different word costs at most 1 (its letter distance)


def mode(environ=None):
    value = (os.environ if environ is None else environ).get(ENV_NAME, '').strip().lower()
    return 'all' if value == 'all' else 'on' if value in ('1', 'on', 'true', 'ja') else 'off'


def letters(text):
    return [character for character in str(text) if character.isalpha()]


def script_share(text, script):
    first, last = translation_qa.NON_LATIN_SCRIPTS[script]
    chosen = letters(text)
    return sum(first <= character <= last for character in chosen) / len(chosen) if chosen else 0.0


def plan_for(texts, environ=None):
    """The plan for what Whisper wrote, or None (other languages, an untested language, switched off)."""
    setting = mode(environ)
    if setting == 'off':
        return None
    script = translation_qa.dominant_non_latin_script([{'source': text} for text in texts])
    entry = PLAN.get(script)
    if entry is None:
        return None
    if script == 'CJK':
        every = letters(' '.join(texts))
        if every and sum(KANA[0] <= character <= KANA[1] for character in every) / len(every) >= .03:
            return None                                     # kana: Japanese, which Whisper reads better than Qwen
    if entry['needs'] == 'all' and setting != 'all':
        return None
    return {'script': script, **entry}


def runs(cues, maximum_gap=MAXIMUM_GAP, maximum_seconds=MAXIMUM_RUN_SECONDS):
    """Groups of neighbouring cue indices that Qwen reads in one go: a new group starts after a longer pause or when the run would get too long."""
    groups, current = [], []
    for index, cue in enumerate(cues):
        if current and (cue['start'] - cues[current[-1]]['end'] > maximum_gap or cue['end'] - cues[current[0]]['start'] > maximum_seconds):
            groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)
    return groups


def run_window(cues, group, total, pad=PAD_SECONDS):
    start = max(0.0, float(cues[group[0]]['start']) - pad)
    end = float(cues[group[-1]]['end']) + pad
    if total:
        end = min(float(total), end)
    return round(start, 3), round(max(end, start + .1), 3)


def has_loop(words, repeats=LOOP_REPEATS):
    """The same one to six words repeated `repeats` times in a row: a recogniser stuck in a loop (Voxtral's Hindi loop was four words)."""
    for size in range(1, 7):
        for start in range(0, len(words) - size * repeats + 1):
            unit = words[start:start + size]
            if all(words[start + step * size:start + (step + 1) * size] == unit for step in range(repeats)):
                return True
    return False


def judge(current, candidate, script):
    """(accept, reason): may Qwen's reading of a run replace what Whisper wrote there?"""
    text = ' '.join(str(candidate or '').split())
    if not text:
        return False, 'Qwen3-ASR hörde inget'
    if script_share(text, script) < MIN_SCRIPT_SHARE:
        return False, 'Qwen3-ASR skrev inte på rätt skrift'
    if has_loop(text.split()):
        return False, 'Qwen3-ASR fastnade i en upprepning'
    wrote, heard = len(letters(current)), len(letters(text))
    if wrote >= 6 and not LENGTH_RATIO[0] <= heard / wrote <= LENGTH_RATIO[1]:
        return False, 'längden avviker kraftigt från Whispers text'
    if wrote < 6 and heard > 60:
        return False, 'Qwen3-ASR skrev långt mer än Whisper hörde'
    return True, ''


def words(text, script):
    """Words to align: separated by spaces, except Chinese where every character is one."""
    return [character for character in str(text) if not character.isspace()] if script == 'CJK' else str(text).split()


def key(word):
    return ''.join(character for character in word.casefold() if unicodedata.category(character)[0] in 'LMN')


def distance(first, second):
    """Letter distance between two words, 0 (same) to 1 (nothing in common)."""
    if first == second:
        return 0.0
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, 1):
        current = [i]
        for j, b in enumerate(second, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1] / max(len(first), len(second), 1)


def align(whisper, qwen):
    """For each Qwen word the index of the Whisper word it matches (None: an extra word). Similar words pair up before words are dropped or added."""
    n, m = len(whisper), len(qwen)
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i * INDEL_COST
    for j in range(1, m + 1):
        cost[0][j] = j * INDEL_COST
    near = [[distance(key(a), key(b)) for b in qwen] for a in whisper]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + INDEL_COST, cost[i][j - 1] + INDEL_COST, cost[i - 1][j - 1] + near[i - 1][j - 1])
    owners = [None] * m
    i, j = n, m
    while i > 0 and j > 0:
        if abs(cost[i][j] - (cost[i - 1][j - 1] + near[i - 1][j - 1])) < 1e-9:
            owners[j - 1] = i - 1
            i, j = i - 1, j - 1
        elif abs(cost[i][j] - (cost[i - 1][j] + INDEL_COST)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return owners


def distribute(texts, qwen_text, script):
    """Cut Qwen's text for a whole run into the run's cues: `texts` is what Whisper wrote in each cue. Each Qwen word goes to the cue of the Whisper word
    it is aligned to; an extra word follows the word before it (a leading one goes to the first cue). A cue that gets nothing is None."""
    whisper = [(word, cue) for cue, text in enumerate(texts) for word in words(text, script)]
    heard = words(qwen_text, script)
    if not whisper or not heard:
        return [None] * len(texts)
    owners = align([word for word, _ in whisper], heard)
    cue_of, last = [], None
    for owner in owners:
        if owner is not None:
            last = whisper[owner][1]
        cue_of.append(last)
    first = next((cue for cue in cue_of if cue is not None), 0)
    cue_of = [first if cue is None else cue for cue in cue_of]
    pieces = [[] for _ in texts]
    for word, cue in zip(heard, cue_of):
        pieces[cue].append(word)
    separator = '' if script == 'CJK' else ' '
    return [separator.join(piece) if piece else None for piece in pieces]


def apply_readings(cues, groups, readings, script):
    """(cues, record, runs): `readings` holds Qwen's text for each group of cues. A cue keeps Whisper's start, end and confidence; only its text can change.
    The record has one entry per cue (what Whisper wrote, what Qwen's words for it are, whether they were used and why not) and one per run."""
    result = [dict(cue) for cue in cues]
    record = [None] * len(cues)
    run_record = []
    for group, reading in zip(groups, readings):
        texts = [cues[index].get('text', '') for index in group]
        qwen = ' '.join(str(reading or '').split())
        accepted, reason = judge(' '.join(texts), qwen, script)
        pieces = distribute(texts, qwen, script) if accepted else [None] * len(group)
        run_record.append({'cues': list(group), 'start': cues[group[0]]['start'], 'end': cues[group[-1]]['end'], 'qwen': qwen, 'accepted': accepted,
                           'reason': reason})
        for index, text, piece in zip(group, texts, pieces):
            why = reason
            used = False
            if accepted:
                wrote, heard = len(letters(text)), len(letters(piece or ''))
                if piece is None:
                    why = 'Qwen3-ASR hörde inget i den här repliken'
                elif wrote >= 6 and not CUE_LENGTH_RATIO[0] <= heard / wrote <= CUE_LENGTH_RATIO[1]:
                    why = 'längden på repliken avviker kraftigt från Whispers text'
                elif ' '.join(text.split()) == piece:
                    why = 'samma text'
                else:
                    used, why = True, ''
            if used:
                result[index]['text'] = piece
            record[index] = {'id': index, 'run': len(run_record) - 1, 'start': cues[index]['start'], 'end': cues[index]['end'], 'whisper': text,
                             'qwen': piece or '', 'used': used, 'reason': why}
    return result, record, run_record
