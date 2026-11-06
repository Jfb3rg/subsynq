"""Interior additions need actual independent words, timings and independent alignment; similarity is not identity."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = ['/app', '/ai']
import worker
from reread import agreed_replacement, confirmed_interior_insertion, conflicting_reread_rows


def evidence():
    row = {'id': 4, 'start': 10., 'end': 14., 'source': 'Then, found another way home.'}
    texts = ['Then,', 'she', 'found', 'another', 'way', 'home.']
    words = [{'word': t, 'start': 10. + i*.5, 'end': 10.4 + i*.5, 'probability': .95}
             for i, t in enumerate(texts)]
    readings = [{'name': n, 'text': ' '.join(texts), 'confidence': .9,
                 'words': copy.deepcopy(words)}
                for n in ('precision', 'v2', 'qwen')]
    return row, readings


class InteriorRereadTests(unittest.TestCase):
    def test_preserves_old_characters_and_times_and_requires_two_architectures(self):
        row, readings = evidence()
        original = copy.deepcopy(row)
        text, proof = confirmed_interior_insertion(row, readings)
        self.assertEqual(text, 'Then, she found another way home.')
        self.assertEqual(row, original)
        self.assertEqual(proof['architectures'], ['whisper', 'qwen'])

    def test_no_film_phrase_or_english_pronoun_rule(self):
        row, readings = evidence()
        row['source'] = 'Puis, trouve une autre voie.'
        for reading in readings:
            reading['text'] = 'Puis, elle trouve une autre voie.'
            for w, t in zip(reading.get('words', []), reading['text'].split()):
                w['word'] = t
        self.assertEqual(confirmed_interior_insertion(row, readings)[0], 'Puis, elle trouve une autre voie.')

    def test_no_missing_reader_or_duplicate_votes_or_extra_dissent(self):
        row, readings = evidence()
        for sample in (readings[:2], readings[1:], readings + [readings[0]],
                       readings + [{'name': 'nb', 'text': row['source']}],
                       [{**r, 'text': row['source']} if r['name']=='qwen' else r for r in readings]):
            self.assertIsNone(confirmed_interior_insertion(row, sample)[0])

    def test_no_deletion_substitution_multiple_additions_edge_words_or_ambiguous_repetitions(self):
        row, readings = evidence()
        for current in ('Then, she found another way home.', 'Then, found different way home.',
                        'Then, another way home.', 'found another way home.',
                        'Then, she found another way.', 'Then, found found another way home.'):
            self.assertIsNone(confirmed_interior_insertion({**row, 'source': current}, readings)[0])

    def test_no_zero_length_weak_or_nonfinite_word(self):
        for changes in ({'probability': .79}, {'start': 10.9, 'end': 10.9},
                        {'probability': float('nan')}, {'end': float('inf')}, {'end': 14.1}):
            row, readings = evidence()
            readings[0]['words'][1].update(changes)
            self.assertIsNone(confirmed_interior_insertion(row, readings)[0])
        for words in ([], [{'word': 'wrong', 'start': 10., 'end': 11.}]):
            row, readings = evidence()
            readings[2]['words'] = words
            self.assertIsNone(confirmed_interior_insertion(row, readings)[0])

    def test_no_weak_missing_misordered_or_disagreeing_anchors(self):
        for index, change in ((2, {'probability': .79}), (3, {'start': 11.1}),
                              (2, {'word': 'different'}), (1, {'end': 11.3})):
            row, readings = evidence()
            readings[1]['words'][index].update(change)
            self.assertIsNone(confirmed_interior_insertion(row, readings)[0])

    def test_no_unaligned_zero_long_nonfinite_or_disagreeing_qwen_word(self):
        for index, change in ((1, {'end': 10.5}), (1, {'start': 9.}),
                              (1, {'end': float('nan')}), (2, {'word': 'another'}),
                              (1, {'end': 11.3}), (3, {'end': 13.} )):
            row, readings = evidence()
            readings[2]['words'][index].update(change)
            self.assertIsNone(confirmed_interior_insertion(row, readings)[0])

    def test_no_neighbour_leak_overlap_or_display_padding_as_speech(self):
        row, readings = evidence()
        for neighbour in ({'start': 8., 'end': 9., 'source': 'And then she'},
                          {'start': 15., 'end': 16., 'source': 'She came back'},
                          {'start': 9., 'end': 10.1, 'source': 'Previous line'}):
            self.assertIsNone(confirmed_interior_insertion(row, readings, [neighbour])[0])
        self.assertIsNone(confirmed_interior_insertion({**row, 'reading_time_source_end': 11.}, readings)[0])

    def test_similarity_is_not_confirmation_of_missing_or_substituted_words(self):
        row, readings = evidence()
        _, record = agreed_replacement(row['source'], readings)
        self.assertFalse(record['same_words'])
        for r in readings:
            r['text'] = row['source'].upper()
        self.assertTrue(agreed_replacement(row['source'], readings)[1]['same_words'])

    def test_saved_real_readings_need_independent_word_alignment(self):
        root = Path('/diagnostic') if str(__file__) == '/test.py' else Path(__file__).parent
        sample = json.loads((root/'fixtures/interior-insertion.json').read_text())
        self.assertFalse(agreed_replacement(sample['row']['source'], sample['readings'])[1]['same_words'])
        self.assertIsNone(confirmed_interior_insertion(sample['row'], sample['readings'])[0])
        self.assertEqual(sample['independent_alignment']['text'], sample['readings'][2]['text'])
        sample['readings'][2]['words'] = sample['independent_alignment']['words']
        text, record = confirmed_interior_insertion(sample['row'], sample['readings'])
        self.assertEqual(text, 'Eventually, he found himself in hell.')
        self.assertAlmostEqual(record['qwen_words'][0]['start'], 35.532)

    def test_agreed_other_words_remain_unclear_until_the_insertion_is_confirmed(self):
        row, readings = evidence()
        _, record = agreed_replacement(row['source'], readings)
        record['id'] = row['id']
        self.assertEqual(len(conflicting_reread_rows([row], [record], [row['id']])), 1)
        self.assertEqual(conflicting_reread_rows([{**row, 'source': 'New words'}], [record], [row['id']]), [])
        self.assertEqual(conflicting_reread_rows([row], [{**record, 'decision': 'ersatt'}], [row['id']]), [])

    def test_worker_records_evidence_preserves_other_fields_and_protects_added_word(self):
        row, readings = evidence()
        row.update(source_unclear=True, alternatives=[{'text': 'Old alternative'}])
        with tempfile.NamedTemporaryFile() as media:
            job = {'id': -1, 'media_path': media.name}
            with patch.object(worker, 'REREAD_ENABLED', True):
                result = worker.reread_flagged_rows(job, [row], [4], reader=lambda *a: {4: readings})
        self.assertEqual(result, [4])
        self.assertEqual((row['start'], row['end'], row['id']), (10., 14., 4))
        self.assertEqual(row['confirmed_source_words'], ['she'])
        self.assertNotIn('source_unclear', row)
        self.assertEqual(job['reread_rows'][0]['interior_insertion']['word'], 'she')
        self.assertEqual(job['reread_rows'][0]['readings'][0]['words'], readings[0]['words'])

    def test_actual_reader_requests_alignment_only_for_unanimous_interior_addition(self):
        row, readings = evidence()
        offset = row['start'] - .3
        class Whisper:
            def __init__(self, *a, **kw): pass
            def transcribe(self, *a, **kw):
                self_outer.assertTrue(kw['word_timestamps'])
                words = [SimpleNamespace(word=w['word'], start=w['start']-offset,
                                         end=w['end']-offset, probability=w['probability']) for w in readings[0]['words']]
                return iter([SimpleNamespace(text=readings[0]['text'], words=words, avg_logprob=-.1)]), SimpleNamespace(language='en', language_probability=1.)
        self_outer = self
        aligned = {'text': readings[2]['text'], 'items': [{'text': w['word'], 'start': w['start']-offset,
                    'end': w['end']-offset} for w in readings[2]['words']]}
        for qwen_text in (readings[2]['text'], row['source']):
            with patch.object(worker, 'WhisperModel', Whisper), patch.object(worker.subprocess, 'run'), \
                    patch.object(worker, 'reread_row_language', return_value='en'), patch.object(worker, 'check_cancelled'), \
                    patch.object(worker, 'release_language_model_before_asr'), patch.object(worker, 'trim_cpu_allocator'), \
                    patch.object(worker, 'qwen_health', return_value=True), patch.object(worker, 'qwen_unload'), \
                    patch.object(worker, 'release_cloud_gpu_memory', return_value={}), \
                    patch.object(worker, 'qwen_identify', return_value={'text': qwen_text}), \
                    patch.object(worker, 'qwen_transcribe', return_value=aligned) as align:
                actual = worker.reread_readings({'id': -1}, Path('/fake.mp4'), [row], [4])[4]
            self.assertEqual(align.call_count, int(qwen_text != row['source']))
            if align.called:
                self.assertEqual(actual[2]['words'][1]['start'], readings[2]['words'][1]['start'])
                self.assertIsNotNone(confirmed_interior_insertion(row, actual)[0])

    def test_two_clean_text_audits_do_not_erase_an_unconfirmed_added_word(self):
        row, readings = evidence()
        readings[2].pop('words')
        sample = [dict(id=i, start=i*5., end=i*5.+4., source='A separate clean line.') for i in range(8)]
        sample[2] = {**row, 'id': 2}
        calls = iter([([2], False, 'Missing word.'), ([], False, '')])
        with tempfile.NamedTemporaryFile() as media:
            job = {'id': -1, 'media_path': media.name}
            with patch.object(worker, 'REREAD_ENABLED', True), patch.object(worker, 'update_job'), \
                    patch.object(worker, '_audit_source_chunk', side_effect=lambda *a, **kw: next(calls)), \
                    patch.object(worker, 'reread_readings', return_value={2: readings}):
                result = worker.assess_source_with_reread(job, sample)
        self.assertEqual(result, [2])
        self.assertEqual(sample[2]['source'], row['source'])


if __name__ == '__main__':
    unittest.main()
