"""A filename is a language hint until independent audio readings support it."""

from language_gaps import whisper_language


def probe_windows(duration, length=15.0):
    """At most three non-overlapping samples spread across the media."""
    length = min(float(duration), length)
    windows = []
    for fraction in (.1, .45, .8):
        start = round(max(0.0, duration - length) * fraction, 3)
        if any(start < other['end'] and start + length > other['start']
               for other in windows):
            continue
        windows.append({'start': start, 'end': round(start + length, 3)})
    return windows


def resolve_filename_language(hint, readings):
    """Only corroborated speech can enable forcing; ambiguity stays automatic.

    Two non-overlapping windows must agree in both engines. Any substantial
    conflicting reading prevents a global language lock, including code-switches.
    This selects decoding policy, not transcript quality or a replacement language.
    """
    supported = []
    conflicts = []
    for reading in readings:
        if reading.get('speech_seconds', 0) < .5:
            continue
        whisper = reading.get('whisper_language')
        qwen = whisper_language(reading.get('qwen_language'))
        texts = (reading.get('whisper_text', ''), reading.get('qwen_text', ''))
        substantial = [sum(c.isalpha() for c in text) >= 8 for text in texts]
        if any(ok and code and code != hint
               for ok, code in zip(substantial, (whisper, qwen))):
            conflicts.append(reading)
        if all(substantial) and whisper == qwen == hint:
            if not any(reading['start'] < old['end'] and reading['end'] > old['start']
                       for old in supported):
                supported.append(reading)
    confirmed = bool(hint and len(supported) >= 2 and not conflicts)
    return {'filename_hint': hint, 'effective_language': hint if confirmed else None,
            'status': 'confirmed' if confirmed else 'conflict' if conflicts else 'unconfirmed',
            'readings': readings}
