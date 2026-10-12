import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, '/app')
from verified_source import apply_verified_source_edits


class VerifiedSourceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.media = self.root / 'episode.mkv'
        self.media.write_bytes(b'example media identity')
        (self.root / 'review').mkdir()
        self.document = {
            'media_path': str(self.media), 'media_size': self.media.stat().st_size,
            'edits': [
                {'action': 'remove', 'start': 4, 'end': 4.5,
                 'expected': 'Maybe', 'label': 'ghost'},
                {'action': 'replace', 'start': 8, 'end': 11,
                 'expected': 'Nowhere safe.',
                 'source': 'Circled Spain, nowhere safe.', 'label': 'full phrase'},
                {'action': 'insert', 'start': 20, 'end': 20.5,
                 'source': 'Bonjour.', 'label': 'greeting'},
            ],
        }
        self.rows = [
            {'id': 0, 'start': 4, 'end': 4.5, 'source': 'Maybe'},
            {'id': 1, 'start': 9.5, 'end': 10.5, 'source': 'Nowhere safe.'},
            {'id': 2, 'start': 21, 'end': 22, 'source': 'Next line.'},
        ]

    def save(self):
        (self.root / 'review' / 'verified-source-edits.json').write_text(
            json.dumps(self.document), encoding='utf-8')

    def test_exact_media_edits_and_renumbers(self):
        self.save()
        updated, labels = apply_verified_source_edits(self.rows, self.media, self.root)
        self.assertEqual([row['source'] for row in updated],
                         ['Circled Spain, nowhere safe.', 'Bonjour.', 'Next line.'])
        self.assertEqual([row['id'] for row in updated], [0, 1, 2])
        self.assertEqual(labels, ['ghost', 'full phrase', 'greeting'])
        self.assertEqual(self.rows[0]['source'], 'Maybe')

    def test_other_media_is_untouched(self):
        self.save()
        other = self.root / 'other.mkv'
        other.write_bytes(b'example media identity')
        self.assertEqual(apply_verified_source_edits(self.rows, other, self.root),
                         (self.rows, []))

    def test_selects_one_media_specific_document_from_directory(self):
        folder = self.root / 'review' / 'verified-source-edits'
        folder.mkdir()
        (folder / 'episode.json').write_text(json.dumps(self.document), encoding='utf-8')
        other = dict(self.document, media_path=str(self.root / 'other.mkv'))
        (folder / 'other.json').write_text(json.dumps(other), encoding='utf-8')
        updated, labels = apply_verified_source_edits(self.rows, self.media, self.root)
        self.assertEqual([row['source'] for row in updated],
                         ['Circled Spain, nowhere safe.', 'Bonjour.', 'Next line.'])
        self.assertEqual(labels, ['ghost', 'full phrase', 'greeting'])

    def test_duplicate_media_documents_fail_closed(self):
        self.save()
        folder = self.root / 'review' / 'verified-source-edits'
        folder.mkdir()
        (folder / 'duplicate.json').write_text(json.dumps(self.document), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Flera uppsättningar'):
            apply_verified_source_edits(self.rows, self.media, self.root)

    def test_changed_media_or_unexpected_asr_fails_closed(self):
        self.save()
        self.media.write_bytes(b'changed media')
        with self.assertRaisesRegex(ValueError, 'annan version'):
            apply_verified_source_edits(self.rows, self.media, self.root)
        self.document['media_size'] = self.media.stat().st_size
        self.save()
        unexpected = [dict(row) for row in self.rows]
        unexpected[1]['source'] = 'Something else.'
        with self.assertRaisesRegex(ValueError, 'Förväntad källrad'):
            apply_verified_source_edits(unexpected, self.media, self.root)

    def test_new_asr_can_already_have_the_verified_text(self):
        self.save()
        improved = [dict(self.rows[1], source='Circled Spain, nowhere safe.'),
                    {'start': 20, 'end': 20.5, 'source': 'Bonjour.'}, self.rows[2]]
        updated, labels = apply_verified_source_edits(improved, self.media, self.root)
        self.assertEqual(len(updated), 3)
        self.assertEqual(labels, [])


if __name__ == '__main__':
    unittest.main()
