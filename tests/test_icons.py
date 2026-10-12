import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from server import ICON_DIR, ICON_NAMES, icon_img, render_pipeline_steps, render_sidebar
from hardware import DEFAULT as DEFAULT_HARDWARE


class IconTests(unittest.TestCase):
    def test_every_icon_file_exists_and_is_valid_svg(self):
        for name in ICON_NAMES:
            root = ET.parse(ICON_DIR / f"{name}.svg").getroot()
            self.assertTrue(root.tag.endswith("svg"), name)

    def test_every_icon_has_a_png_for_unraid(self):
        for name in ICON_NAMES:
            self.assertEqual((ICON_DIR / f"{name}.png").read_bytes()[:4], b"\x89PNG", name)

    def test_unknown_icon_renders_nothing(self):
        self.assertEqual(icon_img("../etc/passwd"), "")

    def test_sidebar_shows_brand_and_resource_icons(self):
        html = render_sidebar("overview", 0, dict(DEFAULT_HARDWARE))
        for name in ("subsynq", "cpu", "ram", "gpu"):
            self.assertIn(f"/icons/{name}.svg", html)

    def test_pipeline_steps_show_engine_icons(self):
        html = render_pipeline_steps("transcribing")
        for name in ("whisper", "qwen", "gemma"):
            self.assertIn(f"/icons/{name}.svg", html)


if __name__ == "__main__":
    unittest.main()
