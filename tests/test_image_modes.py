import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_IMAGES = {'Dockerfile': '/app', 'Dockerfile.ai': '/app /ai', 'Dockerfile.ai-cuda': '/app /ai'}


class ImageModesTests(unittest.TestCase):
    """The worker runs as another user than the one that owns the source tree. A source file whose mode lacked read for everyone made
    the dev worker fail to import it and restart 449 times in a row (2026-10-16), so an image must not depend on those modes."""

    def test_images_make_the_copied_sources_readable_after_copying_them(self):
        present = [name for name in APP_IMAGES if (ROOT / name).exists()]
        if not present:
            self.skipTest('this test layout has no Dockerfiles (only app, ai and tests)')
        for name in present:
            text = (ROOT / name).read_text(encoding='utf-8')
            command = f'RUN chmod -R a+rX {APP_IMAGES[name]}'
            self.assertIn(command, text, name)
            last_copy = max(text.rindex('COPY app/'), text.rindex('COPY ai/') if 'COPY ai/' in text else 0)
            self.assertGreater(text.index(command), last_copy, f'{name}: the chmod comes after the sources are copied')


if __name__ == '__main__':
    unittest.main()
