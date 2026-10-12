import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from library import scan_library, sidecar_languages, subtitle_coverage


class LibraryTests(unittest.TestCase):
    def test_discovery_and_own_subtitle_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root / "Säsong 1"
            nested.mkdir()
            media = nested / "L'âme S01E01.MKV"
            media.write_bytes(b"test video")
            media.with_suffix(".sv.srt").write_text("old subtitle")
            (nested / "poster.jpg").write_bytes(b"image")
            before = media.stat()
            result = scan_library([root])
            self.assertEqual(len(result), 1)
            self.assertFalse(result[0]["ai_sv_exists"])
            self.assertEqual(result[0]["series"], "L'âme")
            self.assertEqual(result[0]["season"], "Säsong 1")
            self.assertEqual(result[0]["episode_code"], "S01E01")
            media.with_suffix(".ai.sv.srt").write_text("AI placeholder")
            self.assertTrue(scan_library([root])[0]["ai_sv_exists"])
            self.assertEqual(media.read_bytes(), b"test video")
            self.assertEqual(media.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(media.with_suffix(".sv.srt").read_text(), "old subtitle")
            media.unlink()
            self.assertEqual(scan_library([root]), [])

    def test_preferred_subtitle_name_and_series_folder_grouping(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            season = root / "My Show" / "Season 2"
            season.mkdir(parents=True)
            media = season / "My.Show.S02E03.mkv"
            media.write_bytes(b"video")
            media.with_suffix(".sv.ai.srt").write_text("legacy")
            media.with_suffix(".ai.sv.srt").write_text("preferred")
            item = scan_library([root])[0]
            self.assertTrue(item["ai_sv_exists"])
            self.assertTrue(item["ai_sv_path"].endswith(".ai.sv.srt"))
            self.assertEqual(item["series"], "My Show")
            self.assertEqual(item["season"], "Säsong 2")
            self.assertEqual(item["episode_number"], 3)

    def test_embedded_swedish_subtitle_counts_as_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "Film.mkv"
            media.write_bytes(b"video")
            probe = lambda _path: {"status": "ok", "audio": [], "subtitle": [{
                "index": 2, "codec": "hdmv_pgs_subtitle", "language": "swe",
                "title": "Swedish", "forced": False,
            }]}
            item = scan_library([root], probe=probe)[0]
            self.assertTrue(item["embedded_sv_exists"])
            self.assertFalse(item["ai_sv_exists"])

    def test_english_subtitle_is_detected_independently_of_swedish(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "Film.mkv"
            media.write_bytes(b"video")
            item = scan_library([root])[0]
            self.assertFalse(item["ai_en_exists"])
            self.assertIsNone(item["ai_en_path"])
            media.with_suffix(".ai.en.srt").write_text("english placeholder")
            item = scan_library([root])[0]
            self.assertTrue(item["ai_en_exists"])
            self.assertTrue(item["ai_en_path"].endswith(".ai.en.srt"))
            self.assertFalse(item["ai_sv_exists"])

    def test_sidecar_languages_only_count_this_videos_own_subtitle_files(self):
        media = Path("/movies/Film.mkv")
        names = ["Film.mkv", "Film.sv.srt", "Film.en.forced.srt", "Film.Extended.sv.srt",
                 "Other.sv.srt", "Film.nfo", "Film.fra.srt"]
        self.assertEqual(sidecar_languages(media, names), ["en", "sv"])
        self.assertEqual(sidecar_languages(media, ["Film.Extended.sv.srt", "Film.fra.srt"]), [])

    def test_scan_records_external_subtitle_languages_without_touching_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "Film.mkv"
            media.write_bytes(b"video")
            sidecar = root / "Film.swe.srt"
            sidecar.write_text("old")
            before = sidecar.stat().st_mtime_ns
            item = scan_library([root])[0]
            self.assertEqual(item["sidecar_langs"], ["sv"])
            self.assertEqual(sidecar.read_text(), "old")
            self.assertEqual(sidecar.stat().st_mtime_ns, before)

    def test_subtitle_coverage_combines_every_source(self):
        probe = {"status": "ok", "subtitle": [
            {"codec": "hdmv_pgs_subtitle", "language": "eng", "title": ""},
            {"codec": "subrip", "language": "und", "title": "Svenska"},
        ]}
        coverage = subtitle_coverage({"probe": probe})
        self.assertEqual(coverage, {"sv": True, "en": True, "image": True, "probed": True})
        only_ai = subtitle_coverage({"probe": {"status": "ok", "subtitle": []},
                                     "ai_sv_exists": True})
        self.assertEqual(only_ai, {"sv": True, "en": False, "image": False, "probed": True})
        sidecar_en = subtitle_coverage({"probe": {"status": "ok", "subtitle": []},
                                        "sidecar_langs": ["en"]})
        self.assertTrue(sidecar_en["en"])
        self.assertFalse(sidecar_en["sv"])

    def test_unanalysed_item_is_reported_as_unprobed_not_as_having_subtitles(self):
        coverage = subtitle_coverage({"probe": {"status": "pending"}})
        self.assertEqual(coverage, {"sv": False, "en": False, "image": False, "probed": False})
        self.assertFalse(subtitle_coverage({})["probed"])

    def test_missing_root_is_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                scan_library([Path(folder) / "missing"])


if __name__ == "__main__":
    unittest.main()
