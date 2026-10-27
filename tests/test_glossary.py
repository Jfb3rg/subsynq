import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
from glossary import apply_glossary, load_glossary

# Yukon Men (dev job 146): the Alaskan snowmobile is "snowgo". large-v3 wrote it four ways; Qwen3-ASR heard "Snowgo" and "snowgos" in the audio.
ENTRIES = [{'heard': 'snow goat', 'write': 'snowgo'}, {'heard': 'snow goats', 'write': 'snowgos'}, {'heard': 'snow goal', 'write': 'snowgo'},
           {'heard': 'snow ghosts', 'write': 'snowgos'}]


class GlossaryTests(unittest.TestCase):
    def rows(self, *texts):
        return [{'start': float(n), 'end': n + 1.0, 'text': text} for n, text in enumerate(texts)]

    def test_the_four_ways_the_word_was_written_become_the_word_that_was_said(self):
        entries = load_entries()
        rows, changes = apply_glossary(self.rows('Snow Goat could freeze up.', 'We better go get the snow goats, Bob.', "If I make it across, then we'll bring the snow goal.",
                                                 "I don't know about snow ghosts, but I bet the dogs could do it.", 'We have a goat and a goal.'), entries)
        self.assertEqual([row['text'] for row in rows], ['Snowgo could freeze up.', 'We better go get the snowgos, Bob.', "If I make it across, then we'll bring the snowgo.",
                                                         "I don't know about snowgos, but I bet the dogs could do it.", 'We have a goat and a goal.'])
        self.assertEqual(len(changes), 4)

    def test_whole_words_only_and_the_longest_entry_first(self):
        rows, _ = apply_glossary(self.rows('The snow goatee and snow goats.', 'Snow Goats!'), sorted(ENTRIES, key=lambda entry: -len(entry['heard'])))
        self.assertEqual([row['text'] for row in rows], ['The snow goatee and snowgos.', 'Snowgos!'])

    def test_the_input_is_not_changed_and_an_empty_list_changes_nothing(self):
        before = self.rows('snow goat')
        self.assertEqual(apply_glossary(before, [])[1], [])
        apply_glossary(before, ENTRIES)
        self.assertEqual(before[0]['text'], 'snow goat')

    def test_the_file_is_read_defensively(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'glossary.json'
            self.assertEqual(load_glossary(path), [], 'missing')
            path.write_text('{kaputt', encoding='utf-8')
            self.assertEqual(load_glossary(path), [], 'broken')
            path.write_text(json.dumps({'heard': 'x'}), encoding='utf-8')
            self.assertEqual(load_glossary(path), [], 'not a list')
            path.write_text(json.dumps([{'heard': 'snow  goat', 'write': 'snowgo'}, {'heard': '', 'write': 'x'}, {'heard': 'a'}, 'text', {'heard': 5, 'write': 'y'}]), encoding='utf-8')
            self.assertEqual(load_glossary(path), [{'heard': 'snow goat', 'write': 'snowgo'}])

    def test_the_worker_applies_it_before_anything_is_translated(self):
        source = Path(sys.modules['worker'].__file__).read_text(encoding='utf-8') if 'worker' in sys.modules else ''
        if not source:
            import worker
            source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn("apply_glossary(merged, load_glossary(CONFIG / 'glossary.json'))", source)
        self.assertLess(source.index("apply_glossary(merged"), source.index('merged, repeated_starts = drop_unconfirmed_repeated_starts'))


def load_entries():
    return list(sorted(ENTRIES, key=lambda entry: -len(entry['heard'])))


if __name__ == '__main__':
    unittest.main()
