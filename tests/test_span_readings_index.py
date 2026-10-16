import random
import sys
import unittest

sys.path.insert(0, '/app')
import asr_windows


def reference_span_readings(final, readings):
    """The original quadratic implementation: every cue scanned every row of every pass."""
    joined = {}
    for cue in final:
        for name, rows in readings.items():
            pieces = [row for row in (rows or ()) if asr_windows._normalized(str(row.get('text', '')))
                      and row['end'] > row['start']
                      and asr_windows._overlap_seconds(row, {'start': cue['start'] - .3, 'end': cue['end'] + .3})
                      >= .6 * (row['end'] - row['start'])]
            if len(pieces) >= 2:
                pieces.sort(key=lambda row: row['start'])
                joined.setdefault(name + '+', []).append({
                    'start': pieces[0]['start'], 'end': max(row['end'] for row in pieces),
                    'text': ' '.join(str(row['text']).strip() for row in pieces),
                    'confidence': sum(float(row.get('confidence', 0)) for row in pieces) / len(pieces)})
    return joined


def random_film(seed, cues=120):
    rng = random.Random(seed)
    words = 'the way you make it your life all right lights on me this is about to get ugly war a monster'.split()

    def text():
        return ' '.join(rng.choice(words) for _ in range(rng.randint(0, 8)))      # may be empty
    final, clock = [], 0.0
    for _ in range(cues):
        clock += rng.uniform(.2, 6.0)
        length = rng.uniform(.5, 12.0)
        final.append({'start': round(clock, 3), 'end': round(clock + length, 3), 'text': text(),
                      'confidence': round(rng.random(), 2)})
        clock += length * rng.choice([0, .3, 1])
    readings = {}
    for name in ('context', 'precision', 'rescue', 'v2', 'qwen'):
        rows = []
        for cue in final:
            for _ in range(rng.randint(0, 3)):
                start = cue['start'] + rng.uniform(-3, 4)
                rows.append({'start': round(start, 3), 'end': round(start + rng.uniform(-.2, 14), 3), 'text': text(),
                             'confidence': round(rng.random(), 2)})
        rng.shuffle(rows)
        readings[name] = rows
    readings['empty'] = []
    readings['none'] = None
    return final, readings


class SpanReadingsIndexTests(unittest.TestCase):
    def test_the_indexed_version_gives_exactly_the_original_result(self):
        for seed in range(12):
            final, readings = random_film(seed)
            self.assertEqual(asr_windows.span_readings(final, readings), reference_span_readings(final, readings), seed)

    def test_a_long_film_is_not_quadratic(self):
        import time
        final, readings = random_film(99, cues=1500)
        started = time.monotonic()
        asr_windows.span_readings(final, readings)
        asr_windows.reading_alternatives(final, {key: value for key, value in readings.items() if value})
        self.assertLess(time.monotonic() - started, 20, 'the unindexed code needed minutes for 1500 cues')


if __name__ == '__main__':
    unittest.main()
