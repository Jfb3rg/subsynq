import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '/ai')
sys.path.insert(0, '/app')
import worker
from types import SimpleNamespace

from reread import agreed_replacement, is_loop, language_from_text, same_script, usable_readings


def readings(precision, v2, qwen):
    """The tight readings the worker takes of one row: Whisper large-v3, Whisper large-v2 and Qwen3-ASR."""
    return [{'name': 'precision', 'text': precision, 'confidence': .9}, {'name': 'v2', 'text': v2, 'confidence': .8},
            {'name': 'qwen', 'text': qwen, 'confidence': .5}]


# Real readings of the rows the final review flagged in the KOKUHO, HOPE and FRANCE trailers (dev jobs 69, 71, 72, 74, 76; STATUS natt 40).
KOKUHO_DEAL = ('き成立や', readings('取引成立や', '取引成立や', '取引成立や。'))
KOKUHO_FACE = ('ですよのお顔に自分が食われちまいます からね',
               readings('そのお顔に自分は食われちまいますからね', 'そのお顔に自分が食われちまいますからね', 'そのお顔に自分が食われちまいますからね。'))
KOKUHO_ARTS = ('あたはゲーで勝負するんや。', readings('あんたは芸で勝負するんや', 'あんたは芸で勝負するんや', 'あんたは芸で勝負するんや。'))
KOKUHO_SERIOUSLY = ('歌舞伎役者にさせるん?', readings('本気で歌舞伎役者にさせる!', '本気で歌舞伎役者にさせるん', '本気で歌舞伎役者にさせる。'))
# Where the readers do not agree, or the current text is as good as the readings, nothing is changed.
KOKUHO_GARBLED = ('どっちにしても、結構手だったら結構身が入るだろ',
                  readings('聞くわけじゃったらシンボルも結構に身が入れるわ', '結構相手が立ちんぼも結構に見が入るよな', '結構開いてた新聞も結構に実が生えるら。'))
KOKUHO_GARBLED_TOO = ('客車もんは時期だがいい気もやわ',
                      readings('逆者いうもんはいじきたが生きもんやわ', '役者流氓は 無理にしてきたが生きもんやわ', '百社有望は、いじきたない気もやわ。'))
KOKUHO_CENTURY = ('世紀の王子家か?', readings('聖地の王国が', '聖地の王国へか', '聖地の大骨折が。'))
KOKUHO_CLIPPED = ('歌舞伎役者にさせるん?', readings('本気で歌舞伎役者にさせる!', '本気で歌舞伎役者にさせるん', 'で歌舞伎役者にさせる。'))
KOKUHO_RIGHT = ('実の息子より部屋子の方が芸が上言うのが', readings('実の息子より部屋子の方が芸が上言うのが', '実の息子より部屋後の方が芸が上いうのが', '実の息子より部屋子の方が芸が上いうのが。'))
HOPE_DEAL = ('미꾸라지 마냥 미끄러운 거래 한 20분쯤 걸었는데',
             readings('미꾸라지 마냥 미끄러운 걸 한 20분쯤 걸었네', '삐꾸라지마냥 삐끄덩거리고 한 20분쯤 걸었네', '비굴하지만요. 떡걸음 한 20분쯤 걸었네.'))
FRANCE_FAMOUS = ("Cette célébrité s'y démonte.", readings('Cette célébrité si démonte.', 'Cette célébrité si démonte.', "Cette célébrité s'y démonte."))


def replaced(case):
    text, record = agreed_replacement(*case)
    return text, record['decision']


class ReplacedRowsTests(unittest.TestCase):
    def test_a_source_cut_short_or_misheard_by_one_pass_is_replaced_when_three_readers_agree(self):
        self.assertEqual(replaced(KOKUHO_DEAL), ('取引成立や', 'ersatt'))
        self.assertEqual(replaced(KOKUHO_ARTS), ('あんたは芸で勝負するんや', 'ersatt'))
        text, decision = replaced(KOKUHO_FACE)
        self.assertEqual(decision, 'ersatt')
        self.assertTrue(text.startswith('そのお顔に自分') and text.endswith('食われちまいますからね'), text)
        self.assertTrue(replaced(KOKUHO_SERIOUSLY)[0].startswith('本気で歌舞伎役者にさせる'))

    def test_readers_that_all_hear_something_else_change_nothing(self):
        for case in (KOKUHO_GARBLED, KOKUHO_GARBLED_TOO):
            self.assertEqual(replaced(case), (None, 'modellfamiljerna läste olika ord'))

    def test_two_whisper_models_agreeing_against_qwen_do_not_replace_a_text_that_may_be_better(self):
        """KOKUHO row 8: both Whisper models read "聖地の王国" and Qwen "聖地の大骨折"; every family must agree, and they agree only at about 0.6."""
        self.assertEqual(replaced(KOKUHO_CENTURY), (None, 'modellfamiljerna läste olika ord'))
        self.assertEqual(replaced(HOPE_DEAL), (None, 'modellfamiljerna läste olika ord'))

    def test_three_families_agreeing_at_0_77_are_not_enough(self):
        """KOKUHO row 8 in job 77: all three readers heard "聖地の王国…" (agreement 0.77) but the burned-in subtitles say "Future stars or failure of
        the century!", so the current "世紀の…" was the better text. The four right corrections (job 76/77) agreed at 0.96 or more."""
        text, record = agreed_replacement('世紀の王子家か?', readings('聖地の王国が', '聖地の王国へか', '聖地の王国に。'))
        self.assertIsNone(text)
        self.assertEqual(record['decision'], 'modellfamiljerna läste olika ord')
        for case in (KOKUHO_DEAL, KOKUHO_ARTS, KOKUHO_FACE, KOKUHO_SERIOUSLY):
            self.assertGreaterEqual(agreed_replacement(*case)[1]['agreement'], .9)

    def test_a_missing_reader_leaves_the_others_to_decide_but_a_dissenting_one_blocks(self):
        without_qwen = [reading for reading in KOKUHO_DEAL[1] if reading['name'] != 'qwen']
        self.assertEqual(agreed_replacement(KOKUHO_DEAL[0], without_qwen)[0], '取引成立や')
        dissent = [*without_qwen, {'name': 'qwen', 'text': '大成立ですか', 'confidence': .5}]
        self.assertIsNone(agreed_replacement(KOKUHO_DEAL[0], dissent)[0])

    def test_a_text_the_readers_confirm_is_left_alone(self):
        self.assertEqual(replaced(FRANCE_FAMOUS), (None, 'läsningen stämmer med nuvarande text'))
        self.assertEqual(replaced(KOKUHO_RIGHT)[0], None)

    def test_a_reading_that_fits_the_current_text_nearly_as_well_is_not_a_reason_to_change(self):
        self.assertEqual(replaced(KOKUHO_CLIPPED), (None, 'nuvarande text passar läsningarna nästan lika bra'))

    def test_one_family_is_not_a_vote(self):
        text, record = agreed_replacement('き成立や', [{'name': 'precision', 'text': '取引成立や', 'confidence': .9},
                                                       {'name': 'rescue', 'text': '取引成立や', 'confidence': .9}])
        self.assertIsNone(text)
        self.assertEqual(record['decision'], 'för få modellfamiljer läste stället')

    def test_two_families_are_enough_when_the_third_reader_is_missing(self):
        text, _ = agreed_replacement('き成立や', [{'name': 'precision', 'text': '取引成立や', 'confidence': .9}, {'name': 'qwen', 'text': '取引成立や。', 'confidence': .5}])
        self.assertEqual(text, '取引成立や')

    def test_loops_boilerplate_empty_and_wrong_length_readings_do_not_vote(self):
        self.assertTrue(is_loop('あ' * 60))
        self.assertFalse(is_loop('取引成立や'))
        rows = usable_readings('き成立や', [{'name': 'v2', 'text': 'あ' * 60}, {'name': 'qwen', 'text': ''}, {'name': 'precision', 'text': '한글자막 제공 및 광고를 포함하고 있습니다'},
                                           {'name': 'rescue', 'text': 'これはとてもとても長い別の文章で元の行とは関係がありません'}, {'name': 'precision', 'text': '取引成立や'}])
        self.assertEqual([name for name, _ in rows], ['precision'])
        text, _ = agreed_replacement('き成立や', [{'name': 'v2', 'text': 'あ' * 60, 'confidence': .9}, {'name': 'precision', 'text': '取引成立や', 'confidence': .9}])
        self.assertIsNone(text, 'a loop is not a second family')

    def test_the_record_keeps_what_every_reader_heard(self):
        _, record = agreed_replacement(*KOKUHO_DEAL)
        self.assertEqual([item['name'] for item in record['readings']], ['precision', 'v2', 'qwen'])
        self.assertEqual(record['agreement'], 1.0)
        self.assertEqual(record['current'], 'き成立や')


class LanguageTests(unittest.TestCase):
    def test_kana_means_japanese_han_only_means_chinese_and_a_latin_script_says_nothing(self):
        self.assertEqual(language_from_text(['歌舞伎役者にさせるん?', 'でもあれですよ']), 'ja')
        self.assertEqual(language_from_text(['我们今天去北京', '你好吗朋友']), 'zh')
        self.assertIsNone(language_from_text(['Des civils, des enfants.', 'Vous courez après l\'audimat.']))
        self.assertIsNone(language_from_text(['한국어 문장입니다']))
        self.assertIsNone(language_from_text([]))

    def test_a_reading_in_another_script_than_the_current_text_is_not_the_same_utterance(self):
        """Job 77: the language guessed on a two-second clip gave Chinese, Maori and Spanish for one Japanese line."""
        self.assertFalse(same_script('でもあれですよ。', 'Demar el deseo.'))
        self.assertFalse(same_script('でもあれですよ。', 'ki kua i te tatu mō mō kēkone mīnga haere rā.'))
        self.assertTrue(same_script('でもあれですよ。', 'でもあれですよ'))
        self.assertTrue(same_script('どっちにしても、結構手だったら', '幾乎歪掉了 新聞也幾乎沒人理'), 'han against kana is one writing system; the agreement test decides')
        self.assertTrue(same_script('Des civils.', 'Des civils, des enfants.'))
        self.assertFalse(same_script('Des civils.', '民間人'))
        self.assertTrue(same_script('', 'anything'))
        self.assertEqual([name for name, _ in usable_readings('でもあれですよ。', [
            {'name': 'precision', 'text': 'Demar el deseo.'}, {'name': 'qwen', 'text': 'でもあれですよ。'}])], ['qwen'])

    def test_the_language_to_force_comes_from_the_policy_the_script_or_the_probes(self):
        rows = [{'source': 'でもあれですよ'}, {'source': '歌舞伎役者にさせるん?'}]
        self.assertEqual(worker.reread_language({'language_policy': {'effective_language': 'fr'}}, rows), 'fr')
        self.assertEqual(worker.reread_language({}, rows), 'ja')
        self.assertEqual(worker.reread_language({}, [{'source': '미꾸라지 마냥 미끄러운 거래 한 20분쯤 걸었는데'}, {'source': '갑자기 기침이 나오기 시작했어요'}]), 'ko')
        latin = [{'source': 'Des civils, des enfants, des hommes, des femmes.'}]
        policy = {'status': 'conflict', 'readings': [{'whisper_language': 'fr'}, {'whisper_language': 'fr'}, {'whisper_language': 'en'}]}
        self.assertEqual(worker.reread_language({'language_policy': policy}, latin), 'fr')
        self.assertIsNone(worker.reread_language({}, latin))

    def test_a_chosen_language_pack_fixes_the_language(self):
        # Glemselens øy (dev job 265): unforced, large-v2 and Qwen read the Norwegian "Jeg er Joe." as English "Yeah, Joe." and replaced it.
        rows = [{'source': 'Jeg er Joe.'}, {'source': 'Skal vi bli beste venner?'}]
        self.assertEqual(worker.reread_language({'norwegian_reader': {'chosen': True}}, rows), 'no')
        self.assertEqual(worker.reread_language({'swedish_reader': {'chosen': True}}, rows), 'sv')
        self.assertIsNone(worker.reread_language({'norwegian_reader': {'chosen': False}}, rows))
        self.assertEqual(worker.reread_language({'language_policy': {'effective_language': 'da'}, 'norwegian_reader': {'chosen': True}}, rows), 'da')


class FakeWhisper:
    """Detects Spanish on the short clip of row 2 and French on the others; reads what the language it is given lets it read."""
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, path, language=None, **kwargs):
        row = int(Path(path).stem.split('-')[1])
        detected = 'es' if row == 2 else 'fr'
        used = language or detected
        text = {'fr': f'Phrase {row}.', 'es': f'Frase {row}.'}[used]
        return iter([SimpleNamespace(text=f' {text}', avg_logprob=-.1)]), SimpleNamespace(language=used, language_probability=.9 if used == 'fr' else .4)


class ReadingTests(unittest.TestCase):
    def read(self, job, rows, ids):
        with patch.object(worker, 'WhisperModel', FakeWhisper), patch.object(worker.subprocess, 'run'), patch.object(worker, 'qwen_health', return_value=None), \
                patch.object(worker, 'check_cancelled'), patch.object(worker, 'trim_cpu_allocator'):
            return worker.reread_readings(job, Path('/media/film.mp4'), rows, ids)

    def test_without_a_known_language_the_clips_vote_and_the_odd_one_out_is_read_again_in_it(self):
        rows = [{'id': row_id, 'start': row_id * 3.0, 'end': row_id * 3.0 + 2.0, 'source': f'Phrase {row_id}.'} for row_id in (1, 2, 3)]
        readings = self.read({'id': 1}, rows, [1, 2, 3])
        self.assertEqual({row_id: [(item['name'], item['text']) for item in items] for row_id, items in readings.items()},
                         {row_id: [('precision', f'Phrase {row_id}.'), ('v2', f'Phrase {row_id}.')] for row_id in (1, 2, 3)})

    def test_a_known_language_is_forced_for_every_clip(self):
        rows = [{'id': 2, 'start': 6.0, 'end': 8.0, 'source': 'Frase 2.'}]
        readings = self.read({'id': 1, 'language_policy': {'effective_language': 'es'}}, rows, [2])
        self.assertEqual([item['text'] for item in readings[2]], ['Frase 2.', 'Frase 2.'])


ROWS = [{'id': 0, 'start': 0.0, 'end': 1.0, 'source': 'Hello.'}, {'id': 1, 'start': 2.0, 'end': 3.0, 'source': 'き成立や'},
        {'id': 2, 'start': 4.0, 'end': 5.0, 'source': '歌舞伎役者にさせるん?'}]


class WorkerTests(unittest.TestCase):
    def report(self, folder, *, reviews, repair_reviews):
        (Path(folder) / 'job-3.json').write_text(json.dumps({'reviews': [{'flags': [{'id': i} for i in reviews]}],
                                                              'repair_reviews': [{'flags': [{'id': i} for i in repair_reviews]}]}), encoding='utf-8')

    def test_the_rows_to_read_again_are_the_ones_still_doubted_after_the_repair_first(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)):
            self.report(folder, reviews=[1, 5, 7], repair_reviews=[7, 5])
            self.assertEqual(worker.rows_to_reread({'id': 3}), [7, 5, 1])
            self.assertEqual(worker.rows_to_reread({'id': 3}, spoken={5}), [7, 1])
            self.report(folder, reviews=list(range(20)), repair_reviews=[])
            self.assertEqual(worker.rows_to_reread({'id': 3}), list(range(worker.REREAD_MAXIMUM_ROWS)))
            self.assertEqual(worker.rows_to_reread({'id': 99}), [])

    def read_again(self, job, reader, rows=None):
        rows = [dict(row) for row in (rows or ROWS)]
        return worker.reread_flagged_rows(job, rows, [1, 2], reader=reader), rows

    def test_a_row_the_readers_agree_on_gets_its_source_replaced_in_place_and_the_rest_is_recorded(self):
        with tempfile.NamedTemporaryFile(suffix='.mp4') as media:
            job = {'id': 3, 'media_path': media.name}
            changed, rows = self.read_again(job, lambda *args: {1: KOKUHO_DEAL[1], 2: KOKUHO_CLIPPED[1]})
        self.assertEqual(changed, [1])
        self.assertEqual([row['source'] for row in rows], ['Hello.', '取引成立や', '歌舞伎役者にさせるん?'])
        self.assertEqual([(item['id'], item['decision']) for item in job['reread_rows']],
                         [(1, 'ersatt'), (2, 'nuvarande text passar läsningarna nästan lika bra')])
        self.assertEqual(job['reread_rows'][0]['start'], 2.0)

    def test_a_confirmed_new_source_clears_only_its_own_old_uncertainty(self):
        sample = [dict(row, source_unclear=True) for row in ROWS]
        with tempfile.NamedTemporaryFile(suffix='.mp4') as media:
            changed, rows = self.read_again({'id': 3, 'media_path': media.name},
                                          lambda *args: {1: KOKUHO_DEAL[1], 2: KOKUHO_CLIPPED[1]}, rows=sample)
        self.assertEqual(changed, [1])
        self.assertNotIn('source_unclear', rows[1])
        self.assertTrue(rows[0]['source_unclear'])
        self.assertTrue(rows[2]['source_unclear'])

    def test_it_never_fails_the_job_and_can_be_switched_off(self):
        def broken(*args):
            raise OSError('ffmpeg')
        with tempfile.NamedTemporaryFile(suffix='.mp4') as media:
            job = {'id': 3, 'media_path': media.name}
            self.assertEqual(self.read_again(job, broken)[0], [])
            self.assertIn('ffmpeg', job['reread_error'])
            with patch.object(worker, 'REREAD_ENABLED', False):
                self.assertEqual(self.read_again(job, lambda *args: {1: KOKUHO_DEAL[1]})[0], [])
        self.assertEqual(self.read_again({'id': 3}, lambda *args: {1: KOKUHO_DEAL[1]})[0], [], 'no media file, nothing to read')
        self.assertEqual(self.read_again({'id': 3, 'media_path': '/no/such/file.mp4'}, lambda *args: {1: KOKUHO_DEAL[1]})[0], [])

    def test_a_cancelled_job_stays_cancelled(self):
        def cancelled(*args):
            raise worker.JobCancelled('stopp')
        with tempfile.NamedTemporaryFile(suffix='.mp4') as media, self.assertRaises(worker.JobCancelled):
            self.read_again({'id': 3, 'media_path': media.name}, cancelled)

    def test_the_environment_switch_exists(self):
        self.assertIn("os.environ.get('SUBSYNQ_REREAD', '1')", Path(worker.__file__).read_text(encoding='utf-8'))


class FlowTests(unittest.TestCase):
    """The doubted row is read again between the failed first review and the one new translation."""

    def run_flow(self, changed):
        rows = [dict(row) for row in ROWS]
        refused = {1: ['Fel ord.'], 2: ['Annat fel.']}
        approved = {1: {'id': 1, 'sv': 'x'}, 0: {'id': 0, 'sv': 'Hej.'}}
        stages = []
        seen = {}
        def retry(job, pending, *, refused=None, context_rows=None):
            seen.update(pending=[row['id'] for row in pending], refused=dict(refused), sources={row['id']: row['source'] for row in pending})
            return [{**row, 'sv': 'ny'} for row in pending]
        def reread(job, source_rows, ids):
            seen['ids'] = list(ids)
            for row in source_rows:
                if row['id'] in changed:
                    row['source'] = '取引成立や'
            return list(changed)
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'QUALITY_REPORTS', Path(folder)), patch.object(worker, 'check_cancelled'), \
                patch.object(worker, 'translate', return_value=[{**row, 'sv': 'gammal'} for row in rows]), \
                patch.object(worker, 'review_and_save_translation', side_effect=[worker.SourceQualityError('fel'), None]), \
                patch.object(worker, 'refused_translations', return_value=refused), patch.object(worker, 'approved_retry_rows', return_value=approved), \
                patch.object(worker, 'rows_to_reread', return_value=[1, 2]), patch.object(worker, 'reread_flagged_rows', side_effect=reread), \
                patch.object(worker, 'retry_translation', side_effect=retry):
            (Path(folder) / 'job-3.json').write_text('{}', encoding='utf-8')
            worker.translate_and_review({'id': 3}, rows, announce=stages.append)
        return seen, stages

    def test_a_changed_row_is_translated_afresh_without_its_old_refusals_and_approval(self):
        seen, stages = self.run_flow([1])
        self.assertEqual(stages, ['review', 'reread', 'retry', 'review'])
        self.assertEqual(seen['ids'], [1, 2])
        self.assertEqual(seen['pending'], [1, 2], 'row 1 lost its approval; row 0 stays approved')
        self.assertEqual(seen['refused'], {2: ['Annat fel.']})
        self.assertEqual(seen['sources'][1], '取引成立や')

    def test_when_nothing_is_changed_the_refusals_and_approvals_stand(self):
        seen, _ = self.run_flow([])
        self.assertEqual(seen['refused'], {1: ['Fel ord.'], 2: ['Annat fel.']})
        self.assertEqual(seen['pending'], [2], 'rows 0 and 1 stay approved')

    def test_the_announcement_and_the_note_exist(self):
        source = Path(worker.__file__).read_text(encoding='utf-8')
        self.assertIn("if stage == 'reread':", source)
        self.assertIn('rättades efter omläsning av ljudet', source)
        self.assertIn("payload['reread'] = job['reread_rows']", source)


if __name__ == '__main__':
    unittest.main()
