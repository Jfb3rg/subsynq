import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker


def quality_flag(row, reason, category='grammar', key='sv'):
    return {'id': row['id'], 'reason': f"{reason} Problemtext: {row[key]}", 'category': category,
            'source_quote': row['source'], 'target_quote': row[key]}


def comparison_verdict(rows, flags=()):
    bad_ids = {flag['id'] for flag in flags if flag['category'] == 'meaning'}
    return {'flags': list(flags), 'comparisons': [
        {'id': row['id'], 'source_meaning': row['source'], 'target_meaning': row['sv'],
         'meaning_preserved': row['id'] not in bad_ids} for row in rows]}


class WorkerTranslationTests(unittest.TestCase):
    def test_cpu_translation_releases_between_complete_draft_and_review_requests(self):
        rows = [{'id': i, 'start': i * 2, 'end': i * 2 + 1, 'source': 'Hello.'}
                for i in range(37)]
        for device in ('cpu', 'nvidia', 'cloud-vast', 'cloud-cpu-vast'):
            events = []

            def fake_call(request):
                payload = json.loads(request['prompt'])
                events.append('review' if 'draft_sv' in payload['rows'][0] else 'draft')
                return {'response': json.dumps({'rows': [
                    {'id': row['id'], 'sv': 'Hej.'} for row in payload['rows']]})}

            with self.subTest(device=device), patch.object(worker, 'MODEL_JOB') as state, \
                    patch.object(worker, 'release_language_model_before_asr',
                                 side_effect=lambda: events.append('release')) as release, \
                    patch.object(worker, 'call_ollama', side_effect=fake_call), \
                    patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
                state.device = device
                translated = worker.translate({'id': -19100}, rows)
            expected = ['release', 'draft', 'release', 'review'] * 2 if device == 'cpu' \
                else ['draft', 'review'] * 2
            self.assertEqual(events, expected)
            self.assertEqual(release.call_count, 4 if device == 'cpu' else 0)
            self.assertEqual([{k: row[k] for k in ('id', 'start', 'end', 'source')}
                              for row in translated], rows)

    def test_split_translation_requests_also_release_cpu_prompt_memory(self):
        requests = []

        def fake_call(request):
            requests.append(request)
            if len(requests) == 1:
                return {'response': 'invalid JSON'}
            return {'response': json.dumps({'rows': [
                {'id': row['id'], 'sv': 'Hej.'}
                for row in json.loads(request['prompt'])['rows']]})}

        with patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'release_language_model_before_asr') as release, \
                patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            state.device = 'cpu'
            result = worker.translate({'id': -19100}, [
                {'id': 0, 'source': 'Hello.'}, {'id': 1, 'source': 'Hello.'}])
        self.assertEqual([row['id'] for row in result], [0, 1])
        self.assertEqual(len(requests), 4)
        self.assertEqual(release.call_count, len(requests))

    def test_failed_cpu_release_stops_translation_before_another_model_call(self):
        with patch.object(worker, 'MODEL_JOB') as state, \
                patch.object(worker, 'release_language_model_before_asr',
                             side_effect=RuntimeError('unload failed')), \
                patch.object(worker, 'call_ollama') as model, \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'update_job'):
            state.device = 'cpu'
            with self.assertRaisesRegex(RuntimeError, 'unload failed'):
                worker.translate({'id': -19100}, [{'id': 0, 'source': 'Hello.'}])
        model.assert_not_called()

    def test_rejected_candidate_is_saved_and_exception_preserved(self):
        rows = [{'id': 0, 'source': 'Hello.', 'sv': 'obegriplig text'}]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                    patch.object(worker, 'assess_translation_quality',
                                 side_effect=worker.SourceQualityError('Underkänd')):
                with self.assertRaises(worker.SourceQualityError):
                    worker.review_and_save_translation({'id': 99}, rows)
            saved = json.loads((Path(folder) / 'job-99.json').read_text())
        self.assertEqual(saved['rows'], rows)
        self.assertEqual(saved['status'], 'unusable')

    def test_copied_french_is_retried_before_srt(self):
        responses = [
            {'response': json.dumps({'rows': [{'id': 7, 'sv': 'Et tout message sera sanctionné.'}]})},
            {'response': json.dumps({'rows': [{'id': 7, 'sv': 'Och varje meddelande kommer att bestraffas.'}]})},
            {'response': json.dumps({'rows': [{'id': 7, 'sv': 'Och varje meddelande kommer att bestraffas.'}]})},
        ]

        def fake_call(request):
            if request.get('keep_alive') == 0:
                return {'response': '{}'}
            return responses.pop(0)

        with patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            result = worker.translate({'id': 99}, [
                {'id': 7, 'start': 1, 'end': 2,
                 'source': 'Et tout message sera sanctionné.'},
            ])
        self.assertEqual(result[0]['sv'], 'Och varje meddelande kommer att bestraffas.')

    def test_empty_single_row_is_retried_instead_of_failing_job(self):
        responses = [
            {'done_reason': 'stop', 'response': json.dumps({'rows': [{'id': 7, 'sv': '   '}]})},
            {'done_reason': 'stop', 'response': json.dumps({'rows': [{'id': 7, 'sv': 'Hej.'}]})},
            {'done_reason': 'stop', 'response': json.dumps({'rows': [{'id': 7, 'sv': 'Hej.'}]})},
        ]

        def fake_call(request):
            if request.get('keep_alive') == 0:
                return {'done_reason': 'stop', 'response': '{}'}
            return responses.pop(0)

        with patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            result = worker.translate({'id': 99}, [
                {'id': 7, 'start': 1.0, 'end': 2.0, 'source': 'Hello.'},
            ])

        self.assertEqual(result[0]['sv'], 'Hej.')
        self.assertEqual(responses, [])

    def test_translation_prompt_keeps_words_on_their_source_id(self):
        requests = []

        def fake_call(request):
            requests.append(request)
            return {'done_reason': 'stop', 'response': json.dumps({'rows': [
                {'id': 14, 'sv': 'Mitt namn är Daryl.'},
                {'id': 15, 'sv': 'Commonwealth ligger i Amerika.'},
            ]})}

        with patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            worker.translate({'id': 99}, [
                {'id': 14, 'start': 1, 'end': 2, 'source': 'My name is Daryl.'},
                {'id': 15, 'start': 2, 'end': 3,
                 'source': 'The Commonwealth is in America.'},
            ])

        self.assertIn('Never move, repeat or anticipate words', requests[0]['system'])
        self.assertIn('Quality-review each Swedish', requests[1]['system'])
        for request in requests[:2]:
            self.assertIn('Preserve intentional repetition and emphasis', request['system'])
            self.assertIn('Preserve agency and causation', request['system'])
            self.assertIn('Do not drop directional words as optional modifiers', request['system'])
            self.assertIn('Render idioms and figurative expressions', request['system'])
            self.assertIn('Preserve who does what, negation, tense, and uncertainty',
                          request['system'])
            self.assertIn('keep each translation on its own id', request['system'])
        self.assertIn('Correct literal calques', requests[1]['system'])
        self.assertEqual([row['id'] for row in json.loads(requests[0]['prompt'])['rows']],
                         [14, 15])

    def test_fidelity_rules_reach_both_final_auditors(self):
        rows = [{'id': 0, 'start': 1, 'end': 3, 'source': 'She waited and waited.',
                 'sv': 'Hon väntade och väntade.'}]
        for focused in (False, True):
            with self.subTest(focused=focused), \
                    patch.object(worker, 'FOCUSED_REVIEW', focused), \
                    patch.object(worker, 'call_ollama',
                                 return_value={'response': json.dumps(comparison_verdict(rows))}) as call:
                worker.assess_translation_quality(rows)
                system = call.call_args.args[0]['system']
                self.assertIn('Preserve intentional repetition and emphasis', system)
                self.assertIn('Preserve agency and causation', system)
                self.assertIn('Do not drop directional words as optional modifiers', system)
                self.assertIn('Flag lost intentional repetition or changed agency', system)
                self.assertIn('these fidelity rules take priority', system)

    def test_fidelity_findings_are_repaired_without_changing_correct_neighbors(self):
        rows = [
            {'id': 0, 'start': 1, 'end': 3, 'source': 'The prisoner was dragged outside.',
             'sv': 'Fången gick ut.'},
            {'id': 1, 'start': 4, 'end': 6, 'source': 'She knocked and knocked.',
             'sv': 'Hon knackade.'},
            {'id': 2, 'start': 7, 'end': 9, 'source': 'He found himself in trouble.',
             'sv': 'Han hamnade i trubbel.'},
        ]
        original = [dict(row) for row in rows]
        flags = [quality_flag(rows[0], 'Handling mot fången har blivit ett eget val.', 'meaning'),
                 quality_flag(rows[1], 'Den avsiktliga upprepningen saknas.', 'meaning')]
        repaired = [{'id': 0, 'sv': 'Fången drogs ut.'},
                    {'id': 1, 'sv': 'Hon knackade och knackade.'}]
        audits = 0
        def respond(request):
            nonlocal audits
            self.assertIn('Preserve intentional repetition and emphasis', request['system'])
            self.assertIn('Preserve agency and causation', request['system'])
            if 'after' in request['format']['properties'].get('rows', {}).get('items', {}).get('properties', {}):
                return {'response': json.dumps({'rows': [{'id': row['id'], 'after': row['sv']}
                                                        for row in repaired]})}
            if 'flags' in request['format']['properties']:
                audits += 1
                sample = json.loads(request['prompt'])['rows']
                return {'response': json.dumps(comparison_verdict(sample, flags if audits == 1 else []))}
            return {'response': json.dumps({'rows': [
                {'id': 0, 'intended_meaning': 'Someone forcibly dragged the prisoner outside.',
                 'edits': [{'before': 'gick', 'after': 'drogs'}]},
                {'id': 1, 'intended_meaning': 'She knocked repeatedly.',
                 'edits': [{'before': 'knackade', 'after': 'knackade och knackade'}]},
            ]})}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                patch.object(worker, 'FOCUSED_REVIEW', True), \
                patch.object(worker, 'call_ollama', side_effect=respond), \
                patch.object(worker, 'check_cancelled'):
            worker.review_and_save_translation({'id': 99}, rows)
            report = json.loads((Path(folder) / 'job-99.json').read_text())
        self.assertEqual(report['original_rows'], original)
        self.assertEqual(report['status'], 'usable')
        self.assertEqual(rows[2], original[2])
        self.assertEqual([row['sv'] for row in rows[:2]], [row['sv'] for row in repaired])
        self.assertEqual([(row['id'], row['start'], row['end']) for row in rows],
                         [(row['id'], row['start'], row['end']) for row in original])
        self.assertEqual(audits, 2)

    def test_focused_audit_cannot_approve_without_comparing_every_row(self):
        rows = [{'id': 0, 'start': 1, 'end': 3, 'source': 'She waited and waited.',
                 'sv': 'Hon väntade.'}]
        audit = []
        with patch.object(worker, 'FOCUSED_REVIEW', True), \
                patch.object(worker, 'call_ollama', return_value={'response': '{"flags": []}'}):
            with self.assertRaisesRegex(RuntimeError, 'giltigt svar'):
                worker.assess_translation_quality(rows, audit=audit)
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(review['status'] == 'reviewer_error' for review in audit))

    def test_comparison_coverage_and_flags_must_agree(self):
        rows = [{'id': 0, 'source': 'She waited and waited.', 'sv': 'Hon väntade.'},
                {'id': 1, 'source': 'Hello.', 'sv': 'Hej.'}]
        flag = quality_flag(rows[0], 'Avsiktlig upprepning saknas.', 'meaning')
        valid = comparison_verdict(rows, [flag])
        worker.validate_translation_comparisons(valid, rows, [flag])
        invalid = [
            {}, {'comparisons': valid['comparisons'][:1]},
            {'comparisons': list(reversed(valid['comparisons']))},
            {'comparisons': [valid['comparisons'][0]] * 2},
            comparison_verdict(rows),
            {'comparisons': [{**valid['comparisons'][0], 'meaning_preserved': 'false'},
                             valid['comparisons'][1]]},
            {'comparisons': [{**valid['comparisons'][0], 'target_meaning': ''},
                             valid['comparisons'][1]]},
        ]
        for verdict in invalid:
            with self.subTest(verdict=verdict), self.assertRaises(ValueError):
                worker.validate_translation_comparisons(verdict, rows, [flag])
        with self.assertRaises(ValueError):
            worker.validate_translation_comparisons(valid, rows, [])

    def test_second_pass_repairs_a_clear_semantic_and_grammar_error(self):
        responses = [
            {'response': json.dumps({'rows': [
                {'id': 35, 'sv': "'en hade dött av det här jobbet, vet du"},
            ]})},
            {'response': json.dumps({'rows': [
                {'id': 35, 'sv': 'Det här jobbet tar död på mig.'},
            ]})},
        ]

        def fake_call(request):
            if request.get('keep_alive') == 0:
                return {'response': '{}'}
            return responses.pop(0)

        with patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            result = worker.translate({'id': 99}, [{
                'id': 35, 'start': 95, 'end': 97, 'source': "It's killing me.",
            }])

        self.assertEqual(result[0]['sv'], 'Det här jobbet tar död på mig.')

    def test_source_quality_gate_rejects_pervasive_gibberish(self):
        response = {'response': json.dumps({
            'bad_ids': [0, 1, 2], 'language_mismatch': False,
            'reason': 'Flera rader består av osammanhängande och påhittade fraser.',
        })}
        rows = [{'id': index, 'source': f'garbled row {index}'} for index in range(12)]
        requests = []
        with patch.object(worker, 'call_ollama', side_effect=lambda request: (
                requests.append(request) or response)):
            with self.assertRaisesRegex(worker.SourceQualityError, 'innan någon SRT skrevs'):
                worker.assess_source_quality(rows, 'Trailer (på tyska).mp4')
        self.assertEqual(json.loads(requests[0]['prompt'])['media_name'],
                         'Trailer (på tyska).mp4')

    def test_source_quality_gate_accepts_normal_dialogue(self):
        response = {'response': json.dumps({
            'bad_ids': [], 'language_mismatch': False,
            'reason': 'Sammanhängande dialog med enstaka korta repliker.',
        })}
        rows = [{'id': index, 'source': f'Normal dialogue line {index}.'}
                for index in range(12)]
        with patch.object(worker, 'call_ollama', return_value=response):
            self.assertTrue(worker.assess_source_quality(rows))

    def test_source_quality_gate_tolerates_choppy_but_coherent_narration(self):
        # A fast-talking but fully coherent commentary video: earlier the
        # whole-transcript "usable/unusable" framing misjudged this kind of
        # material as unusable even with zero real ASR errors, because many
        # cues are sentence fragments continued by their neighbor. The
        # per-row audit correctly finds nothing to flag.
        response = {'response': json.dumps({
            'bad_ids': [], 'language_mismatch': False,
            'reason': 'Informell, sammanhängande vloggnarration utan igenkänningsfel.',
        })}
        rows = [{'id': index, 'source': f'and then the next part {index}'}
                for index in range(40)]
        with patch.object(worker, 'call_ollama', return_value=response):
            self.assertTrue(worker.assess_source_quality(rows))

    def test_source_quality_gate_does_not_trust_script_that_conflicts_with_filename(self):
        response = {'response': json.dumps({
            'bad_ids': [], 'language_mismatch': True,
            'reason': 'Koreansk text stämmer inte med tydlig tysk märkning.',
        })}
        rows = [{'id': index, 'source': '며칠 전에 친구들이랑 사냥을 하러 갔었어.'}
                for index in range(4)]
        with patch.object(worker, 'call_ollama', return_value=response):
            with self.assertRaisesRegex(worker.SourceQualityError, 'Källtranskriberingen'):
                worker.assess_source_quality(rows, 'HOPE tyska.mp4')

    def test_short_german_source_rejects_hope_like_broken_cluster(self):
        rows = [
            {'id': 0, 'start': 1, 'end': 4, 'source': 'Vor ein paar Tagen.'},
            {'id': 1, 'start': 5, 'end': 8, 'source': 'Wie ein Schwachsinn, wie ein Schwachsinn.'},
            {'id': 2, 'start': 9, 'end': 12, 'source': 'Ich habe Minuten gedauert.'},
            {'id': 3, 'start': 13, 'end': 16, 'source': 'Und dann bin wütend.'},
            {'id': 4, 'start': 17, 'end': 20, 'source': 'Das wissen wir nicht.'},
            {'id': 5, 'start': 21, 'end': 24, 'source': 'Es sind sind.'},
        ]
        responses = [
            {'response': json.dumps({'bad_ids': [], 'language_mismatch': False,
                                     'reason': 'Ser rimligt ut.'})},
            {'response': json.dumps({'bad_ids': [1, 2, 5],
                                     'reason': 'Flera repliker har allvarliga språkfel.'})},
        ]
        with patch.object(worker, 'call_ollama', side_effect=responses) as model:
            with self.assertRaisesRegex(worker.SourceQualityError, '3 av 6'):
                worker.assess_source_quality(rows, 'HOPE tyska.mp4')
        self.assertEqual(model.call_count, 2)

    def test_short_source_audit_tolerates_one_bad_row(self):
        rows = [{'id': i, 'start': i, 'end': i + 1, 'source': 'Une courte phrase.'}
                for i in range(6)]
        responses = [
            {'response': json.dumps({'bad_ids': [], 'language_mismatch': False,
                                     'reason': 'Begripligt.'})},
            {'response': json.dumps({'bad_ids': [2], 'reason': 'En osäker replik.'})},
        ]
        with patch.object(worker, 'call_ollama', side_effect=responses):
            self.assertTrue(worker.assess_source_quality(rows, 'Trailer franska.mp4'))

    def test_final_quality_gate_rejects_pervasive_transliteration(self):
        rows = [{'id': index, 'source': 'தமிழ் உரை', 'sv': f'Pannukkala row {index}'}
                for index in range(12)]
        response = {'response': json.dumps({'flags': [
            quality_flag(rows[index], 'Fonetisk translitterering i stället för svenska.', 'transliteration')
            for index in (0, 1, 2)
        ]})}
        with patch.object(worker, 'call_ollama', return_value=response):
            with self.assertRaisesRegex(worker.SourceQualityError, 'svenska undertexten'):
                worker.assess_translation_quality(rows)

    def test_final_quality_gate_accepts_swedish_translation(self):
        response = {'response': json.dumps({'flags': []})}
        rows = [{'id': index, 'source': 'தமிழ் உரை', 'sv': f'Det här är rad {index}.'}
                for index in range(12)]
        with patch.object(worker, 'call_ollama', return_value=response):
            self.assertTrue(worker.assess_translation_quality(rows))

    def test_final_quality_gate_rejects_multiple_cut_off_sentences(self):
        rows = [
            {'id': 1, 'source': 'Ich ging lange.', 'sv': 'Jag gick en lång stund och'},
            {'id': 2, 'source': 'Alle gingen weg.', 'sv': 'Alla försvann, så vi trodde att'},
            {'id': 3, 'source': 'Was ist passiert?', 'sv': 'Vad hände?'},
            {'id': 4, 'source': 'Acht Menschen.', 'sv': 'Åtta personer.'},
            {'id': 5, 'source': 'Bleib hier.', 'sv': 'Stanna här.'},
            {'id': 6, 'source': 'Komm jetzt.', 'sv': 'Kom nu.'},
        ]
        with patch.object(worker, 'call_ollama') as model:
            with self.assertRaisesRegex(worker.SourceQualityError, 'mitt i en mening'):
                worker.assess_translation_quality(rows)
        model.assert_not_called()

    def test_final_quality_gate_reviews_hope_fragments_for_meaning(self):
        rows = [
            {'id': 0, 'source': 'Vor ein paar Tagen.', 'sv': 'För några dagar sedan.'},
            {'id': 1, 'source': 'Wie ein Schwachsinn.', 'sv': 'Som en idiot.'},
            {'id': 2, 'source': 'Einige Minuten.', 'sv': 'Det tog några minuter.'},
            {'id': 3, 'source': 'Dann werde ich wütend.', 'sv': 'Sedan blir jag arg.'},
            {'id': 4, 'source': 'in der Situation', 'sv': 'i den situationen!'},
            {'id': 5, 'source': 'auflösen und wegfahren',
             'sv': 'för att lösa upp och åka iväg.'},
            {'id': 6, 'source': 'Es sind acht', 'sv': 'Det är åtta'},
            {'id': 7, 'source': 'Ich töte dich nicht.', 'sv': 'Jag dödar dig inte!'},
            {'id': 8, 'source': 'Das wissen wir nicht.', 'sv': 'Det vet vi inte.'},
        ]
        response = {'response': json.dumps({'flags': [
            quality_flag(rows[index], 'Repliken är obegriplig även i sitt sammanhang.') for index in (1, 4, 6)
        ]})}
        with patch.object(worker, 'call_ollama', return_value=response) as model:
            with self.assertRaisesRegex(worker.SourceQualityError, 'obegriplig'):
                worker.assess_translation_quality(rows)
        model.assert_called_once()

    def test_lowercase_and_adjacent_continuations_reach_semantic_review(self):
        rows = [
            {'id': 0, 'start': 0, 'end': 2, 'source': 'We know that',
             'sv': 'Vi vet mycket väl att'},
            {'id': 1, 'start': 2.1, 'end': 4, 'source': 'it is addressed to us.',
             'sv': 'det är adresserat till oss.'},
            {'id': 2, 'start': 5, 'end': 7, 'source': 'Take us to the location of',
             'sv': 'Ta oss till platsen för'},
            {'id': 3, 'start': 7, 'end': 8, 'source': 'the transmission.',
             'sv': 'sändningen.'},
        ]
        with patch.object(worker, 'call_ollama', return_value={'response': json.dumps({
                'flags': []})}) as model:
            self.assertTrue(worker.assess_translation_quality(rows))
        request = model.call_args.args[0]
        self.assertEqual(json.loads(request['prompt'])['rows'], rows)
        self.assertIn('A subtitle cue is NOT a sentence', request['system'])

    def test_timing_does_not_bypass_semantic_rejection(self):
        rows = [{'id': i, 'start': i, 'end': i + 1, 'source': 'Unclear speech',
                 'sv': 'Vem gick glädjen med och'} for i in range(4)]
        with patch.object(worker, 'call_ollama', return_value={'response': json.dumps({'flags': [
                quality_flag(rows[0], 'Osammanhängande ordsallad.'),
                quality_flag(rows[1], 'Osammanhängande ordsallad.')]})}):
            with self.assertRaisesRegex(worker.SourceQualityError, 'ordsallad'):
                worker.assess_translation_quality(rows)

    def test_review_covers_all_long_transcript_rows_with_boundary_context(self):
        rows = [{'id': i, 'start': i * 2, 'end': i * 2 + 1,
                 'source': 'Hello.', 'sv': 'Hej.'} for i in range(145)]
        seen = []
        def review(request):
            seen.append(json.loads(request['prompt'])['rows'])
            return {'response': json.dumps({'flags': []})}
        with patch.object(worker, 'call_ollama', side_effect=review):
            self.assertTrue(worker.assess_translation_quality(rows))
        self.assertEqual({row['id'] for block in seen for row in block}, set(range(145)))
        self.assertIn(11, [row['id'] for row in seen[1]])
        self.assertIn(11, [row['id'] for row in seen[0]])
        self.assertIn(12, [row['id'] for row in seen[1]])

    def test_short_trailer_is_reviewed_in_overlapping_passages(self):
        rows = [{'id': i, 'start': i * 2, 'end': i * 2 + 1,
                 'source': 'Dialogue.', 'sv': 'Dialog.'} for i in range(31)]
        seen = []
        def review(request):
            ids = [row['id'] for row in json.loads(request['prompt'])['rows']]
            seen.append(ids)
            return {'response': json.dumps({'flags': []})}
        with patch.object(worker, 'call_ollama', side_effect=review):
            self.assertTrue(worker.assess_translation_quality(rows))
        self.assertEqual(seen, [list(range(0, 12)), list(range(10, 22)),
                                list(range(20, 31)), list(range(22, 31))])

    def test_broken_trailer_cluster_is_not_diluted_by_good_dialogue(self):
        rows = [{'id': i, 'start': i * 2, 'end': i * 2 + 1,
                 'source': 'Dialogue.', 'sv': 'Dialog.'} for i in range(22)]
        seen = []
        def review(request):
            ids = [row['id'] for row in json.loads(request['prompt'])['rows']]
            seen.append(ids)
            flags = ([quality_flag(rows[row_id], 'Trasig sekvens.') for row_id in ids[:3]]
                     if ids[0] == 13 else [])
            return {'response': json.dumps({'flags': flags})}
        with patch.object(worker, 'call_ollama', side_effect=review):
            with self.assertRaisesRegex(worker.SourceQualityError, 'Trasig sekvens'):
                worker.assess_translation_quality(rows)
        self.assertEqual(seen, [list(range(0, 12)), list(range(10, 22)),
                                list(range(13, 22))])

    def test_malformed_review_does_not_approve(self):
        rows = [{'id': 0, 'source': 'Hello.', 'sv': 'hej.'}]
        with patch.object(worker, 'call_ollama', return_value={'response': '{}'}):
            with self.assertRaisesRegex(RuntimeError, 'giltigt svar'):
                worker.assess_translation_quality(rows)

    def test_final_quality_gate_rejects_missing_source_coverage(self):
        rows = [
            {'id': index, 'source': 'This is a complete and fairly long source sentence.',
             'sv': 'Ja.' if index < 2 else 'Det här är en fullständig svensk mening.'}
            for index in range(6)
        ]
        with patch.object(worker, 'call_ollama') as model:
            with self.assertRaisesRegex(worker.SourceQualityError, 'dialog saknas'):
                worker.assess_translation_quality(rows)
        model.assert_not_called()


class EnglishSubtitleTests(unittest.TestCase):
    """The optional second English pass (see include_english_subtitle()) reuses
    translate()/assess_translation_quality() with target_key='en'. An English
    source that stays unchanged is the CORRECT output, unlike Swedish, so the
    looks_untranslated() copy-detection must not fire for this target."""

    def test_identical_english_source_and_translation_is_not_treated_as_a_copy_failure(self):
        response = {'response': json.dumps({'rows': [
            {'id': 0, 'en': "We're the better lawyers."},
        ]})}
        with patch.object(worker, 'call_ollama', return_value=response), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            result = worker.translate({'id': 99}, [
                {'id': 0, 'start': 0, 'end': 1, 'source': "We're the better lawyers."},
            ], target_key='en', target_language='English')
        self.assertEqual(result[0]['en'], "We're the better lawyers.")

    def test_english_prompt_mentions_english_not_swedish(self):
        requests = []

        def fake_call(request):
            requests.append(request)
            return {'response': json.dumps({'rows': [{'id': 0, 'en': 'Hello there.'}]})}

        with patch.object(worker, 'call_ollama', side_effect=fake_call), \
                patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'update_job'):
            worker.translate({'id': 99}, [
                {'id': 0, 'start': 0, 'end': 1, 'source': 'Bonjour.'},
            ], target_key='en', target_language='English')
        self.assertIn('natural English', requests[0]['system'])
        self.assertNotIn('Swedish', requests[0]['system'])

    def test_english_quality_gate_uses_en_field_and_swedish_labelled_errors(self):
        rows = [{'id': index, 'source': 'Quelque chose.', 'en': f'Something {index}.'}
                for index in range(12)]
        response = {'response': json.dumps({'flags': [
            quality_flag(rows[index], 'Otydlig mening.', key='en') for index in (0, 1, 2)
        ]})}
        with patch.object(worker, 'call_ollama', return_value=response):
            with self.assertRaisesRegex(worker.SourceQualityError, 'engelska undertexten'):
                worker.assess_translation_quality(rows, target_key='en', target_language='English')

    def test_english_quality_gate_accepts_a_clean_block(self):
        response = {'response': json.dumps({'flags': []})}
        rows = [{'id': index, 'source': 'Quelque chose.', 'en': f'Something {index}.'}
                for index in range(12)]
        with patch.object(worker, 'call_ollama', return_value=response):
            self.assertTrue(worker.assess_translation_quality(
                rows, target_key='en', target_language='English'))

    def test_write_srt_writes_the_english_suffix_and_field(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'clip.mkv'
            media.touch()
            database = Path(folder) / 'test.db'
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            rows = [{'id': 0, 'start': 0, 'end': 1, 'en': 'Hello there.'}]
            with patch.object(worker, 'DATABASE', database), \
                    patch.object(worker, 'update_job'):
                output = worker.write_srt({'id': 99}, media, rows, target_key='en')
            self.assertEqual(output.name, 'clip.ai.en.srt')
            self.assertIn('Hello there.', output.read_text(encoding='utf-8'))

    def test_review_and_save_translation_uses_a_separate_report_file(self):
        rows = [{'id': 0, 'source': 'Hi.', 'en': 'Hi.'}]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker, 'QUALITY_REPORTS', Path(folder)), \
                    patch.object(worker, 'assess_translation_quality', return_value=True):
                worker.review_and_save_translation(
                    {'id': 99}, rows, target_key='en', target_language='English')
            self.assertTrue((Path(folder) / 'job-99-en.json').exists())
            self.assertFalse((Path(folder) / 'job-99.json').exists())


class RestartJobTests(unittest.TestCase):
    """'Kör igen' (see subtitle_is_stale() in app/server.py): claim_job() must
    pick up a restart_queued row like a delete, and remove_existing_subtitle()
    must clear the old file the same way a plain delete does, so main() can
    fall straight through into a normal fresh transcription afterwards."""

    def test_claim_job_prioritises_restart_over_a_plain_queue(self):
        import sqlite3
        from jobs import enqueue_job, enqueue_restart, initialize_jobs
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'test.db'
            initialize_jobs(database)
            enqueue_job(database, '/movies/other.mkv', 'other.mkv')
            restart_id = enqueue_restart(database, '/movies/f.mkv', 'f.mkv', '/movies/f.ai.sv.srt')
            with patch.object(worker, 'DATABASE', database):
                claimed = worker.claim_job()
            self.assertEqual(claimed['id'], restart_id)
            self.assertEqual(claimed['status'], 'restart_queued')
            with sqlite3.connect(database) as db:
                row = db.execute("SELECT status FROM subtitle_jobs WHERE id=?",
                                 (restart_id,)).fetchone()
            self.assertEqual(row[0], 'restarting')

    def test_remove_existing_subtitle_deletes_the_file_and_clears_the_scan_flag(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'f.mkv'
            media.touch()
            subtitle = media.with_suffix('.ai.sv.srt')
            subtitle.write_text('old subtitle')
            database = Path(folder) / 'test.db'
            library = {'items': [{'path': str(media), 'ai_sv_exists': True,
                                   'ai_sv_path': str(subtitle)}]}
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("INSERT INTO metadata VALUES ('library_scan', ?)", (json.dumps(library),))
            job = {'id': 99, 'output_path': str(subtitle)}
            with patch.object(worker, 'DATABASE', database):
                worker.remove_existing_subtitle(job, media)
            self.assertFalse(subtitle.exists())
            with sqlite3.connect(database) as db:
                saved = json.loads(db.execute(
                    "SELECT value FROM metadata WHERE key='library_scan'").fetchone()[0])
            self.assertFalse(saved['items'][0]['ai_sv_exists'])
            self.assertIsNone(saved['items'][0]['ai_sv_path'])


if __name__ == '__main__':
    unittest.main()
