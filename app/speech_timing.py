"""Conservative subtitle timing helpers based on independently detected speech."""


def speech_span(words, *, minimum_probability=0.35, padding=0.08):
    """Return a tight cue span from trustworthy word timestamps, or None."""
    accepted = [word for word in words
                if word.get('start') is not None and word.get('end') is not None
                and word.get('probability', 0) >= minimum_probability
                and word['end'] > word['start']]
    if not accepted:
        return None
    return (round(max(0, accepted[0]['start'] - padding), 3),
            round(accepted[-1]['end'] + padding, 3))


def reject_long_bridge(segment, *, maximum_duration=12):
    """Reject ASR text spanning a long gap instead of silently exporting it."""
    return segment['end'] - segment['start'] > maximum_duration
