import sys
import unittest

sys.path.insert(0, '/app')
import i18n
import server


class PipelineModelTextTests(unittest.TestCase):
    """The job card used to say "Gemma 3 12B" under the translation step for every job, also on a rented 24 GB card
    that runs gemma3:27b (user, 2026-10-16)."""

    def test_no_step_claims_a_fixed_gemma_size(self):
        for status in ('transcribing', 'translating', 'verifying', 'writing'):
            for lang in ('sv', 'en'):
                self.assertNotIn('12B', server.render_pipeline_steps(status, lang), (status, lang))
        for key in ('pipeline_translating_desc', 'pipeline_verifying_desc', 'pipeline_transcribing_desc'):
            for lang in ('sv', 'en'):
                self.assertNotIn('12B', i18n.STRINGS[key][lang])

    def test_the_running_step_shows_the_model_the_worker_recorded(self):
        page = server.render_pipeline_steps('translating', 'sv', 'Översättning med gemma3:27b')
        self.assertIn('Översättning med gemma3:27b', page)
        self.assertEqual(page.count('Översättning med gemma3:27b'), 1, 'only the running step names it')
        self.assertIn('Whisper + Qwen3-ASR läser av ljudet', page, 'a finished step keeps its generic text')
        verifying = server.render_pipeline_steps('verifying', 'sv', 'Granskning med gemma4:26b')
        self.assertIn('Granskning med gemma4:26b', verifying)
        self.assertIn('Gemma-modellen översätter raderna', verifying, 'a finished step is generic again')

    def test_without_a_recorded_model_every_step_is_generic(self):
        for model in (None, '', '   '):
            page = server.render_pipeline_steps('translating', 'sv', model)
            self.assertIn('Gemma-modellen översätter raderna', page)

    def test_the_model_text_is_escaped(self):
        page = server.render_pipeline_steps('verifying', 'sv', '<script>alert(1)</script>')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_english_pages_use_english_generic_text(self):
        page = server.render_pipeline_steps('transcribing', 'en')
        self.assertIn('The Gemma model translates the lines', page)

    def test_the_overview_card_and_the_queue_row_pass_the_jobs_model_and_language(self):
        job = {'media_path': '/movies/a.mkv', 'media_name': 'a.mkv', 'status': 'translating', 'progress': 70,
               'message': 'Översätter', 'model': 'Översättning med gemma3:27b', 'id': 5}
        card = server.render_overview_job_card({'/movies/a.mkv': job}, {}, 'sv')
        self.assertIn('Översättning med gemma3:27b', card)
        row = server.render_queue_job_row(job, {}, 'tok', cancellable=True, lang='en')
        self.assertIn('Översättning med gemma3:27b', row)
        self.assertIn('Listening and transcribing', row, 'the queue row steps follow the page language')


if __name__ == '__main__':
    unittest.main()
