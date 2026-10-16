"""A language model chooses between the readings the ASR passes heard of a doubtful line.

Voting between passes cannot settle every place: in the Man of War German dub two of three engines read "Meine
nicht" where the line is "Meine Nichte", and in the English track the cue "way you make it your life all right
lights on me this" had a cleaner reading from three other passes that a vote margin held back. A model that knows the
language can tell which wording is a plausible line of dialogue in context. It only ever CHOOSES between wordings the
audio passes actually produced (never writes text of its own), the current wording is the default, and a choice must
still look like the same utterance and keep most of its words."""
import json
from difflib import SequenceMatcher
import re

LETTERS = 'ABCDEF'


class EngineUnavailable(Exception):
    """The language-model engine did not answer. Raised by the caller's `call`, never for a bad answer; a cancelled
    job must pass through `choose_readings` untouched, so it is not caught here."""

JUDGE_SYSTEM = (
    'You choose between competing speech-recognition readings of the same moment of a film or video. '
    'Each numbered row has candidate readings labelled A, B, C, ...; A is the current one. '
    'First decide whether reading A has a clear error in this context: a misheard word, ungrammatical or nonsensical '
    'wording, a stray extra word, or words missing that another reading has. Set a_has_error to true only then; a '
    'line that is merely less tidy than another is not an error. If a_has_error is false, choose A. If it is true, '
    'choose the reading that is the most plausible line of dialogue in the language spoken, given the previous and '
    'the next line: correct grammar and spelling, a sensible meaning, names written as names. '
    'Never write your own text and never combine candidates. Choose A when the candidates are equally good or when '
    'you are not sure. Do not translate and do not correct the speaker. Return JSON only.')


def _words(text):
    return re.findall(r"[^\W\d_]+", str(text).casefold(), flags=re.UNICODE)


def _row_text(row):
    return str(row.get('source', row.get('text', ''))).strip()


def judge_items(rows, *, maximum=24):
    """The rows that have competing readings, with their neighbours' current wording, at most `maximum`.

    `supports` runs parallel to `candidates`: how many model families the audio passes themselves have behind each
    wording (the current wording first)."""
    items = []
    for index, row in enumerate(rows):
        alternatives = [item for item in row.get('alternatives') or () if str(item.get('text', '')).strip()]
        if not alternatives:
            continue
        current = _row_text(row)
        support = {current: int(alternatives[0].get('current_support', 1))}
        for item in alternatives:
            support.setdefault(str(item['text']).strip(), int(item.get('support', 1)))
        candidates = list(support)[:len(LETTERS)]
        if len(candidates) < 2:
            continue
        items.append({
            'id': row['id'], 'index': index, 'candidates': candidates,
            'supports': [support[text] for text in candidates],
            'previous': _row_text(rows[index - 1]) if index > 0 else '',
            'next': _row_text(rows[index + 1]) if index + 1 < len(rows) else ''})
        if len(items) >= maximum:
            break
    return items


def build_request(model, items, *, seed=211):
    ids = [item['id'] for item in items]
    prompt = json.dumps({'rows': [
        {'id': item['id'], 'previous_line': item['previous'], 'next_line': item['next'],
         'candidates': {LETTERS[number]: text for number, text in enumerate(item['candidates'])}}
        for item in items]}, ensure_ascii=False)
    predict = max(256, len(ids) * 40)
    # One token per character is a safe upper bound even for Tamil or Korean, whose text costs far more tokens than
    # Latin script (a real Tamil batch overflowed 4096 and the engine answered HTTP 500).
    context = min(16384, max(4096, -(-(len(JUDGE_SYSTEM) + len(prompt) + predict + 512) // 1024) * 1024))
    return {
        'model': model, 'stream': False, 'keep_alive': '5m', 'system': JUDGE_SYSTEM,
        'format': {'type': 'object', 'properties': {'rows': {
            'type': 'array', 'minItems': len(ids), 'maxItems': len(ids),
            'items': {'type': 'object', 'properties': {
                'id': {'type': 'integer', 'enum': ids},
                'a_has_error': {'type': 'boolean'},
                'choice': {'type': 'string', 'enum': list(LETTERS)}},
                'required': ['id', 'a_has_error', 'choice'], 'additionalProperties': False}}},
            'required': ['rows'], 'additionalProperties': False},
        'prompt': prompt,
        'options': {'temperature': 0, 'num_ctx': context, 'num_predict': predict, 'num_thread': 6, 'seed': seed},
    }


def batches_of(items, *, batch_size=12, budget=3500):
    """Items in batches of at most `batch_size` rows and about `budget` characters, so long lines are asked in small
    batches; an item larger than the budget goes alone."""
    batch, size = [], 0
    for item in items:
        weight = sum(len(text) for text in item['candidates']) + len(item['previous']) + len(item['next']) + 80
        if batch and (len(batch) >= batch_size or size + weight > budget):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += weight
    if batch:
        yield batch


def _similarity(a, b):
    return SequenceMatcher(None, ' '.join(_words(a)), ' '.join(_words(b))).ratio()


def repeats_neighbour(chosen, current, previous, following, *, shortest=2):
    """The chosen wording starts with the end of the previous line, or ends with the start of the next one, and the
    current wording does not: words that belong to the neighbouring cue (a Qwen clip or a joined piece that reached
    into it) would then stand in two cues."""
    words, own = _words(chosen), _words(current)
    for neighbour, at_end in ((previous, True), (following, False)):
        other = _words(neighbour)
        for size in range(min(len(words), len(other), 10), shortest - 1, -1):
            if at_end and other[-size:] == words[:size] and own[:size] != words[:size]:
                return True
            if not at_end and other[:size] == words[-size:] and own[-size:] != words[-size:]:
                return True
    return False


def has_repetition(text, *, shortest=2, longest=6):
    """A run of two or more words said twice in a row ("He's. He's got", "his name is X his name is X"): a stutter
    or a decoding loop that a reading can pick up, never a better wording than one without it."""
    words = _words(text)
    for size in range(shortest, longest + 1):
        for index in range(len(words) - 2 * size + 1):
            if words[index:index + size] == words[index + size:index + 2 * size]:
                return True
    return False


def acceptable_choice(current, chosen, support=1, current_support=1, previous='', following=''):
    """A chosen wording must still be the same utterance (shares most of its words), must keep most of the current
    wording's words (a judge that merely prefers the tidier fragment would silently drop speech), must not repeat
    words of a neighbouring cue, and must be one the audio passes stand behind: either more model families read
    something close to it than read something close to the current wording, or it is a minimal repair of the current
    wording (similarity 0.85). A fluent rewrite that only one engine heard is refused: the model cannot hear."""
    current_words, chosen_words = _words(current), _words(chosen)
    if not chosen_words or len(chosen_words) < .8 * len(current_words):
        return False
    overlap = len(set(current_words) & set(chosen_words))
    if overlap < .4 * min(len(set(current_words)), len(set(chosen_words))):
        return False
    if has_repetition(chosen) and not has_repetition(current):
        return False
    if repeats_neighbour(chosen, current, previous, following):
        return False
    return support > current_support or _similarity(current, chosen) >= .85


def choose_readings(rows, call, model, *, batch_size=12, require_error_flag=False):
    """(rows with the judged wording, decisions). `call(request)` is the language-model call returning a dict with the
    model's JSON text under 'response'. A row the model does not answer validly keeps its own wording. When the
    engine itself fails (`EngineUnavailable`) no further batch is asked and what was chosen so far is kept; nothing
    is raised for a bad answer or a failed call.

    The model also says whether the current wording has an error (`a_has_error`, kept in each decision). A choice
    is applied when it passes `acceptable_choice`; with `require_error_flag` it must also be flagged. The guards are
    what protects the transcript: on dev job 49 a 12B model called the garbled line "way you make it your life all
    right lights on me this" fine and so the flag alone would have kept it, while on 11 stored traces (English,
    German, Tamil) the guards alone refused every harmful pick that came up (dropped words, a neighbour's words, a
    stutter, a rewrite only one engine heard) and let through only fixes."""
    rows = [dict(row) for row in rows]
    decisions = []
    items = judge_items(rows)
    for batch in batches_of(items, batch_size=batch_size):
        try:
            answered = json.loads(call(build_request(model, batch))['response'])['rows']
        except EngineUnavailable:
            break       # the engine is not answering: keep what was chosen so far and ask for no more
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, AttributeError):
            continue
        by_id = {item['id']: item for item in batch}
        for answer in answered if isinstance(answered, list) else ():
            try:
                item = by_id[answer['id']]
                number = LETTERS.index(answer['choice'])
                flawed = answer['a_has_error'] is True
                chosen = item['candidates'][number]
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            row = next(row for row in rows if row['id'] == item['id'])
            current = _row_text(row)
            decision = {'id': item['id'], 'choice': LETTERS[number], 'candidates': item['candidates'],
                        'supports': item['supports'], 'a_has_error': flawed, 'from': current, 'to': chosen, 'applied': False}
            if number > 0 and chosen != current and (flawed or not require_error_flag) and acceptable_choice(
                    current, chosen, item['supports'][number], item['supports'][0],
                    item['previous'], item['next']):
                row['source' if 'source' in row else 'text'] = chosen
                decision['applied'] = True
            decisions.append(decision)
    for row in rows:
        row.pop('alternatives', None)
    return rows, decisions
