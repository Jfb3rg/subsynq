"""A language model chooses between the readings the ASR passes heard of a doubtful line.

Voting between passes cannot settle every place: in the Man of War German dub two of three engines read "Meine
nicht" where the line is "Meine Nichte", and in the English track the cue "way you make it your life all right
lights on me this" had a cleaner reading from three other passes that a vote margin held back. A model that knows the
language can tell which wording is a plausible line of dialogue in context. It only ever CHOOSES between wordings the
audio passes actually produced (never writes text of its own), the current wording is the default, and a choice must
still look like the same utterance and keep most of its words."""
import json
import math
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
    'Keep preserve_words: these words occur in multiple readings or were independently confirmed in timed audio. Do not replace them merely because '
    'the rest of a long candidate is similar. If no alternative preserves them, choose A. '
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
            'confirmed_words': [word for word in row.get('confirmed_source_words', []) if word in _choice_words(current)],
            'previous': _row_text(rows[index - 1]) if index > 0 else '',
            'next': _row_text(rows[index + 1]) if index + 1 < len(rows) else ''})
        if len(items) >= maximum:
            break
    return items


def build_request(model, items, *, seed=211):
    ids = [item['id'] for item in items]
    prompt = json.dumps({'rows': [
        {'id': item['id'], 'previous_line': item['previous'], 'next_line': item['next'],
         'preserve_words': sorted(set(supported_words(item['candidates'][0], item['candidates']) + item.get('confirmed_words', []))),
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
    # Keep the original literal guard intact; expansions add vetoes only.
    words, own = _words(chosen), _words(current)
    for neighbour, at_end in ((previous, True), (following, False)):
        other = _words(neighbour)
        for size in range(min(len(words), len(other), 10), shortest - 1, -1):
            if at_end and other[-size:] == words[:size] and own[:size] != words[:size]:
                return True
            if not at_end and other[:size] == words[-size:] and own[-size:] != words[-size:]:
                return True
    # A contraction may hide a repeated boundary: "my job's a lot" followed
    # by "is a lot more complex" repeats "is a lot", just like the expanded
    # spelling. Expansion is ONLY an extra veto, never a wording replacement.
    contractions = {"'s": (' is', ' has'), "'re": (' are',), "'ve": (' have',),
                    "'m": (' am',), "'ll": (' will',), "'d": (' had', ' would')}
    def forms(text):
        text = str(text).replace('’', "'")
        return [_words(text)] + [_words(re.sub(r"(?i)(?<=\w)" + re.escape(suffix) + r"\b", expansion, text))
                                for suffix, expansions in contractions.items() for expansion in expansions
                                if suffix in text.casefold()]
    chosen_forms, own_forms = forms(chosen), forms(current)
    for neighbour, at_end in ((previous, True), (following, False)):
        for words in chosen_forms:
            for other in forms(neighbour):
                for size in range(min(len(words), len(other), 10), shortest - 1, -1):
                    if at_end and other[-size:] == words[:size] and not any(v[:size] == words[:size] for v in own_forms):
                        return True
                    if not at_end and other[:size] == words[-size:] and not any(v[-size:] == words[-size:] for v in own_forms):
                        return True
    # Words the choice ADDS that the neighbouring line already holds, anywhere in it: Agnes mot verden (dev job 213) took NB-Whisper's
    # "Det er ikke rart når de to mest populære bøkene er Bibelen og" right before "i tre siste runde av årene er Bibelen", so
    # "Bibelen" stood in two lines.
    words = _words(chosen)
    for tag, _, _, start, end in SequenceMatcher(None, own, words, autojunk=False).get_opcodes():
        if tag not in ('insert', 'replace') or end - start < shortest:
            continue
        added = words[start:end]
        for neighbour in (previous, following):
            other = _words(neighbour)
            for size in range(len(added), shortest - 1, -1):
                for first in range(len(added) - size + 1):
                    piece = added[first:first + size]
                    if any(other[index:index + size] == piece for index in range(len(other) - size + 1)):
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


def _choice_words(text):
    # A standard joined spelling must not look like a deleted content word.
    return _words(re.sub(r'\ball\s+right\b', 'alright', str(text), flags=re.IGNORECASE))


def supported_words(current, candidates):
    """Words to preserve, not ground truth: current wording plus two distinct
    alternatives contain them. Whole-row similarity must not license changing
    such a local word (Kiev -> key) in an otherwise almost identical long cue.
    Candidate counts are NOT independent-model votes. Refusal keeps the current
    source, never invents a better one or declares it verified.
    """
    own = _choice_words(current)
    alternatives = {tuple(_choice_words(text)) for text in candidates
                    if _choice_words(text) != own and len(_choice_words(text)) >= .8 * len(own)}
    return sorted({word for word in own if len(word) >= 4
                   and sum(word in words for words in alternatives) >= 2})


def removed_supported_words(current, chosen, candidates):
    return sorted(set(supported_words(current, candidates)) - set(_choice_words(chosen)))


def acceptable_choice(current, chosen, support=1, current_support=1, previous='', following='', word_families=None):
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
    # A choice that only DROPS words is never a "minimal repair": it needs more model families behind it than the current wording.
    # MAN OF WAR job 170: "You find me a tour guide yet?" (both large-v3 passes and large-v2) -> "Find me a tour guide yet?" (Qwen only),
    # supports 3 against 3 and the judge itself saying the current wording had no error. Replay of 181 applied choices: 33 such removals, almost
    # all harmful ("Du behöver" -> "behöver", "Je ne supporte plus" -> "Je supporte plus", "C'est" -> "'est", "in OK shape" -> "in shape").
    # With `word_families` (how many model families heard each word of the current wording) it may drop words that at most one family heard:
    # MAN OF WAR job 180, "... lights on me this" (the long pass alone) -> "... lights on me." -- but never "You" (both large-v3 passes and large-v2).
    if len(chosen_words) < len(current_words) and _is_subsequence(chosen_words, current_words):
        removed = set(current_words) - set(chosen_words)
        lonely = bool(word_families) and all(word_families.get(word, 0) <= 1 for word in removed)
        return support > current_support or lonely
    return support > current_support or _similarity(current, chosen) >= .85


def _is_subsequence(short, long):
    position = iter(long)
    return all(word in position for word in short)


def _positions(current_words, words):
    """Where the row's own words sit in the reading (greedy, in order), or None when they are not all there in order."""
    found, start = [], 0
    for word in current_words:
        try:
            start = words.index(word, start)
        except ValueError:
            return None
        found.append(start)
        start += 1
    return found


def _swallows_neighbour(words, positions, previous_words, following_words):
    """The words a reading adds BEFORE the row's own words repeat the end of the previous row, or those it adds AFTER them repeat the start of the next row
    (a run of two words anywhere in the added words, or one word at the start or the very end of them): they are already in the neighbour and would stand in two rows
    ("that" after "...in our kennel", "I have a lot to do" against the next row's "have a wood lot")."""
    lead, trail = words[:positions[0]], words[positions[-1] + 1:]
    for start in range(len(trail)):
        for size in range(min(len(trail) - start, len(following_words), 5), 0, -1):
            if trail[start:start + size] == following_words[:size] and (size >= 2 or start == 0 or start + size == len(trail)):
                return True
    for end in range(len(lead), 0, -1):
        for size in range(min(end, len(previous_words), 5), 0, -1):
            if lead[end - size:end] == previous_words[-size:] and (size >= 2 or end == len(lead)):
                return True
    return False


def _completion_end(rows, index, span):
    row = rows[index]
    previous_end = rows[index - 1].get('end', 0) if index else 0
    next_start = rows[index + 1].get('start', float('inf')) if index + 1 < len(rows) else float('inf')
    if (not isinstance(span, dict) or any(type(span.get(k)) not in (int, float) or not math.isfinite(span[k]) for k in ('start', 'end'))
            or 'start' not in row or 'end' not in row
            or not max(previous_end, row['start'] - .15) <= span['start'] < span['end'] <= next_start - .04
            or span['end'] - row['start'] > 7):
        return None
    return max(row['end'], span['end'])


def complete_cut_off_rows(rows, *, minimum_added=2, maximum_added=5):
    """(rows, completions): a row whose source is cut off in the middle of a sentence is completed by a reading that holds ALL its words, in order, and
    only ADDS words (`minimum_added` to `maximum_added` of them). Nothing the file already says is changed or removed, so this can never turn "the caribou" into
    "care" (two readers heard that on a short clip without context): large-v3 left "Let's get the out of" and large-v2 heard "Let's get the f*** out of here!".
    Words that belong to a neighbouring row (the reading starts with the end of the previous row or ends with the start of the next one) are refused as before."""
    from translation_qa import source_is_cut_off
    rows = [dict(row) for row in rows]
    completions = []
    for index, row in enumerate(rows):
        alternatives = [item for item in row.get('alternatives') or () if str(item.get('text', '')).strip()]
        if not alternatives or not source_is_cut_off(rows, index):
            continue
        current = _row_text(row)
        current_words = _words(current)
        if not current_words:
            continue
        previous = _row_text(rows[index - 1]) if index else ''
        following = _row_text(rows[index + 1]) if index + 1 < len(rows) else ''
        options = []
        for item in alternatives:
            text = str(item['text']).strip()
            words = _words(text)
            added = len(words) - len(current_words)
            positions = _positions(current_words, words) if minimum_added <= added <= maximum_added else None
            completion_end = row.get('end')
            span = item.get('completion_span')
            if span is not None:
                # The alternative's words must fit this utterance, not the next
                # cue's time. An existing free gap may hold its measured end.
                completion_end = _completion_end(rows, index, span)
                if completion_end is None:
                    continue
            if (positions is not None and not (has_repetition(text) and not has_repetition(current)) and not repeats_neighbour(text, current, previous, following)
                    and not _swallows_neighbour(words, positions, _words(previous), _words(following))):
                options.append((-int(item.get('support', 1)), added, text, completion_end))
        if options:
            _, added, text, completion_end = sorted(options)[0]
            row['source' if 'source' in row else 'text'] = text
            completions.append({'id': row['id'], 'from': current, 'to': text, 'added_words': added})
            if completion_end is not None and completion_end != row.get('end'):
                completions[-1].update(end_before=row['end'], end=completion_end)
                row['end'] = completion_end
    return rows, completions


def choose_readings(rows, call, model, *, batch_size=12, require_error_flag=False, maximum=24):
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
    items = judge_items(rows, maximum=maximum)
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
            # The text-only chooser cannot erase words independently recovered from measured audio.
            prefix = re.findall(r'\w+', str(row.get('confirmed_prefix', '')).casefold())
            keeps_prefix = re.findall(r'\w+', chosen.casefold())[:len(prefix)] == prefix
            if not keeps_prefix:
                decision['reason'] = 'confirmed_audio_prefix'
            lost = removed_supported_words(current, chosen, item['candidates'])
            lost_confirmed = [word for word in item.get('confirmed_words', []) if word not in _choice_words(chosen)]
            if lost_confirmed:
                decision.update(reason='confirmed_audio_word', preserved_words=lost_confirmed)
            if lost and keeps_prefix and not lost_confirmed:
                decision.update(reason='supported_words_removed', preserved_words=lost)
            timed = next((a for a in row.get('alternatives', []) if a['text'].strip() == chosen and 'completion_span' in a), None)
            completion_end = _completion_end(rows, item['index'], timed['completion_span']) if timed else row.get('end')
            timing_valid = not timed or completion_end is not None
            if not timing_valid:
                decision['reason'] = 'outside_audio_span'
            if number > 0 and chosen != current and timing_valid and keeps_prefix and not lost and not lost_confirmed and (flawed or not require_error_flag) and acceptable_choice(
                    current, chosen, item['supports'][number], item['supports'][0],
                    item['previous'], item['next'], row.get('word_families')):
                row['source' if 'source' in row else 'text'] = chosen
                decision['applied'] = True
                if timed and completion_end != row['end']:
                    decision.update(end_before=row['end'], end=completion_end)
                    row['end'] = completion_end
            decisions.append(decision)
    for row in rows:
        row.pop('alternatives', None)
        row.pop('word_families', None)
    return rows, decisions
