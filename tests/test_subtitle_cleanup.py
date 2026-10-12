import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from subtitle_cleanup import (apply_filters, classify, empty_trash, filter_query, list_trash, load_filters,
                              duplicate_key, inspect_srt, load_retention, mark_stale, parse_filters, purge_old, save_filters, save_retention,
                              render_cleanup_content, restore_batch, scan_srt, trash_files)


def make_library(base):
    root = base / "movies"
    folder = root / "Film (2020)"
    folder.mkdir(parents=True)
    (folder / "Film.mkv").write_bytes(b"video")
    (folder / "Film.sv.srt").write_text("old")
    (folder / "Film.ai.sv.srt").write_text("ai")
    (folder / "Annan.sv.srt").write_text("orphan")
    return root, folder


class CleanupTests(unittest.TestCase):
    def test_classify_and_scan(self):
        self.assertEqual(classify("Film.ai.sv.srt"), "ai")
        self.assertEqual(classify("Film.sv.ai.srt"), "ai")
        self.assertEqual(classify("Film.sv.srt"), "other")
        self.assertEqual(classify("ai.srt"), "other")
        with tempfile.TemporaryDirectory() as folder:
            root, _ = make_library(Path(folder))
            items = {item["name"]: item for item in scan_srt([root, Path(folder) / "saknas"])}
            self.assertEqual(set(items), {"Film.sv.srt", "Film.ai.sv.srt", "Annan.sv.srt"})
            self.assertEqual(items["Film.sv.srt"]["lang"], "sv")
            self.assertFalse(items["Film.sv.srt"]["orphan"])
            self.assertTrue(items["Annan.sv.srt"]["orphan"])
            self.assertEqual(items["Film.ai.sv.srt"]["kind"], "ai")

    def test_scan_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root, folder_path = make_library(Path(folder))
            outside = Path(folder) / "outside"
            outside.mkdir()
            (outside / "hemlig.srt").write_text("x")
            (folder_path / "lank.srt").symlink_to(outside / "hemlig.srt")
            (root / "dirlank").symlink_to(outside)
            names = {item["name"] for item in scan_srt([root])}
            self.assertNotIn("lank.srt", names)
            self.assertNotIn("hemlig.srt", names)

    def test_filters(self):
        now = time.time()
        items = [
            {"rel": "a/Film.sv.srt", "kind": "other", "orphan": False, "mtime": now - 40 * 86400},
            {"rel": "a/Film.ai.sv.srt", "kind": "ai", "orphan": False, "mtime": now},
            {"rel": "b/Annan.sv.srt", "kind": "other", "orphan": True, "mtime": now},
        ]
        self.assertEqual(len(apply_filters(items, parse_filters({}), now)), 2)
        self.assertEqual(len(apply_filters(items, parse_filters({"kind": ["all"]}), now)), 3)
        self.assertEqual(len(apply_filters(items, parse_filters({"kind": ["ai"]}), now)), 1)
        self.assertEqual(len(apply_filters(items, parse_filters({"orphan": ["1"]}), now)), 1)
        self.assertEqual(len(apply_filters(items, parse_filters({"older": ["30"]}), now)), 1)
        self.assertEqual(len(apply_filters(items, parse_filters({"q": ["ANNAN"]}), now)), 1)
        self.assertEqual(parse_filters({"kind": ["x"], "older": ["abc"]})["kind"], "other")

    def test_trash_restore_and_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, folder_path = make_library(base)
            trash = base / "config" / "trash"
            target = folder_path / "Film.sv.srt"
            batch, moved, failed = trash_files([str(target), str(target)], [root], trash)
            self.assertEqual((moved, failed), (1, []))
            self.assertFalse(target.exists())
            self.assertTrue((folder_path / "Film.mkv").exists())
            self.assertEqual(list_trash(trash)[0]["count"], 1)
            restored, skipped = restore_batch(batch, [root], trash)
            self.assertEqual((restored, skipped), (1, []))
            self.assertEqual(target.read_text(), "old")
            self.assertEqual(list_trash(trash), [])
            batch, _, _ = trash_files([str(target)], [root], trash)
            target.write_text("ny fil")
            restored, skipped = restore_batch(batch, [root], trash)
            self.assertEqual(restored, 0)
            self.assertEqual(target.read_text(), "ny fil")
            self.assertEqual(empty_trash(trash), 1)
            self.assertEqual(list_trash(trash), [])

    def test_refuses_dangerous_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, folder_path = make_library(base)
            outside = base / "outside.srt"
            outside.write_text("x")
            link = folder_path / "lank.srt"
            link.symlink_to(outside)
            trash = base / "trash"
            candidates = [str(folder_path / "Film.mkv"), str(outside), str(link),
                          str(folder_path / ".." / ".." / "outside.srt"), str(folder_path / "finns-ej.srt"),
                          str(folder_path)]
            _, moved, failed = trash_files(candidates, [root], trash)
            self.assertEqual(moved, 0)
            self.assertEqual(len(failed), len(candidates))
            self.assertTrue(outside.exists() and (folder_path / "Film.mkv").exists())
            with self.assertRaises(ValueError):
                restore_batch("../etc", [root], trash)

    def test_render_escapes_and_pages(self):
        items = [{"path": f"/movies/<b>{n}</b>.srt", "root": "/movies", "rel": f"<b>{n}</b>.srt",
                  "name": f"<b>{n}</b>.srt", "size": 10, "mtime": 0, "kind": "other", "lang": "", "orphan": True}
                 for n in range(250)]
        html = render_cleanup_content({"scanned_at": 1.0, "items": items}, parse_filters({}), [], "tok", page=2)
        self.assertNotIn("<b>", html)
        self.assertIn("Sida 2 av 2", html)
        self.assertEqual(html.count("name='path'"), 50)
        self.assertIn("250 träffar", html)


    def test_stale_ai_subtitle_when_video_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root, folder_path = make_library(Path(folder))
            video = folder_path / "Film.mkv"
            info = video.stat()
            job = {"media_path": str(video), "status": "completed",
                   "media_size_bytes": info.st_size, "media_mtime_ns": info.st_mtime_ns}
            items = mark_stale(scan_srt([root]), {str(video): job})
            self.assertFalse(any(item["stale"] for item in items))
            video.write_bytes(b"ersatt video med annan storlek")
            items = mark_stale(scan_srt([root]), {str(video): job})
            stale = {item["name"] for item in items if item["stale"]}
            self.assertEqual(stale, {"Film.ai.sv.srt"})
            self.assertEqual([item["name"] for item in apply_filters(items, parse_filters({"stale": ["1"]}))],
                             ["Film.ai.sv.srt"])
            job["status"] = "failed"
            self.assertFalse(any(item["stale"] for item in mark_stale(scan_srt([root]), {str(video): job})))


    def test_filters_are_remembered_between_visits(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_filters(folder), parse_filters({}))
            chosen = parse_filters({"kind": ["all"], "orphan": ["1"], "older": ["30"], "q": ["film"]})
            save_filters(folder, chosen)
            self.assertEqual(load_filters(folder), chosen)
            self.assertEqual(parse_filters({k: [str(v)] for k, v in filter_query(chosen).items()}), chosen)
            (Path(folder) / "cleanup-filters.json").write_text("skräp")
            self.assertEqual(load_filters(folder), parse_filters({}))

    def test_empty_result_hints_at_other_filters(self):
        items = [{"path": "/x/a.ai.sv.srt", "root": "/x", "rel": "a.ai.sv.srt", "name": "a.ai.sv.srt", "size": 1,
                  "mtime": 0, "kind": "ai", "lang": "sv", "orphan": False}]
        html = render_cleanup_content({"scanned_at": 1.0, "items": items}, parse_filters({}), [], "tok")
        self.assertIn("Skanningen innehåller 1 undertexter", html)


    def test_retention_setting_and_purge_old(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, folder_path = make_library(base)
            config, trash = base / "config", base / "config" / "trash"
            config.mkdir()
            self.assertEqual(load_retention(config), 0)
            self.assertEqual(save_retention(config, 30), 30)
            self.assertEqual(load_retention(config), 30)
            self.assertEqual(save_retention(config, -5), 0)
            self.assertEqual(save_retention(config, 99999), 3650)
            now = time.time()
            old, _, _ = trash_files([str(folder_path / "Film.sv.srt")], [root], trash, now=now - 40 * 86400)
            new, _, _ = trash_files([str(folder_path / "Annan.sv.srt")], [root], trash, now=now - 5 * 86400)
            self.assertEqual(purge_old(trash, 0, now), 0)
            self.assertEqual(len(list_trash(trash)), 2)
            self.assertEqual(purge_old(trash, 30, now), 1)
            self.assertEqual([batch["id"] for batch in list_trash(trash)], [new])
            self.assertFalse((trash / old).exists())
            (trash / "egen-mapp").mkdir()
            purge_old(trash, 1, now + 365 * 86400)
            self.assertTrue((trash / "egen-mapp").exists())
            html = render_cleanup_content({"scanned_at": 1.0, "items": []}, parse_filters({}),
                                          list_trash(trash), "tok", retention_days=30)
            self.assertIn("value='30'", html)
            self.assertIn("/cleanup/retention", html)


    def test_inspect_srt_flags_broken_files(self):
        good = "1\n00:00:01,000 --> 00:00:02,500\nHej, världen!\n\n2\n00:00:03,000 --> 00:00:04,000\nÅ ä ö\n"
        cases = {
            "bra.srt": (good.encode("utf-8"), []),
            "bom.srt": (good.encode("utf-8-sig"), []),
            "tom.srt": (b"  \n\n", ["empty"]),
            "ingenting.srt": (b"", ["empty"]),
            "latin1.srt": (good.encode("latin-1"), ["bad_encoding"]),
            "mojibake.srt": (good.encode("utf-8").decode("latin-1").encode("utf-8"), ["mojibake"]),
            "text.srt": (b"Det har ar bara text utan tider.", ["no_cues"]),
            "tomma.srt": (b"1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\n123\n",
                          ["empty_cues"]),
        }
        with tempfile.TemporaryDirectory() as folder:
            for name, (data, expected) in cases.items():
                path = Path(folder) / name
                path.write_bytes(data)
                self.assertEqual(inspect_srt(path), expected, name)
            self.assertEqual(inspect_srt(Path(folder) / "finns-ej.srt"), ["unreadable"])

    def test_broken_filter_combines_with_stale_and_ignores_type(self):
        items = [
            {"rel": "a.sv.srt", "kind": "other", "orphan": False, "mtime": 0, "problems": ["empty"], "stale": False},
            {"rel": "b.ai.sv.srt", "kind": "ai", "orphan": False, "mtime": 0, "problems": ["empty"], "stale": True},
            {"rel": "c.ai.sv.srt", "kind": "ai", "orphan": False, "mtime": 0, "problems": [], "stale": True},
            {"rel": "d.sv.srt", "kind": "other", "orphan": False, "mtime": 0, "problems": [], "stale": False},
        ]
        names = lambda query: [item["rel"] for item in apply_filters(items, parse_filters(query))]
        self.assertEqual(names({"broken": ["1"]}), ["a.sv.srt", "b.ai.sv.srt"])
        self.assertEqual(names({"broken": ["1"], "stale": ["1"]}), ["b.ai.sv.srt"])
        self.assertEqual(names({"stale": ["1"]}), ["b.ai.sv.srt", "c.ai.sv.srt"])
        self.assertEqual(names({}), ["a.sv.srt", "d.sv.srt"])
        chosen = parse_filters({"broken": ["1"]})
        self.assertEqual(parse_filters({k: [str(v)] for k, v in filter_query(chosen).items()}), chosen)


    def test_english_rendering_has_no_swedish_leftovers(self):
        items = [{"path": "/x/a.sv.srt", "root": "/x", "rel": "a.sv.srt", "name": "a.sv.srt", "size": 1,
                  "mtime": 0, "kind": "other", "lang": "sv", "orphan": True, "stale": False,
                  "problems": ["empty", "mojibake"]}]
        batches = [{"id": "20261010-120000", "count": 2, "size": 10}]
        html = render_cleanup_content({"scanned_at": 1.0, "items": items}, parse_filters({}), batches, "tok",
                                      notice="ok", retention_days=7, lang="en")
        for expected in ("Clean up subtitles", "Move selected to trash", "No matching video file", "empty file",
                         "garbled characters", "no video", "Empty trash", "2 files", "Restore", "Rescan folders",
                         "Empty automatically after"):
            self.assertIn(expected, html)
        for swedish in ("Städa", "Flytta", "Saknar", "tom fil", "Papperskorg", "Återställ", "Skanna", "träffar"):
            self.assertNotIn(swedish, html)
        self.assertNotIn("''", html.split("confirm(")[1].split(")")[0])
        swedish = render_cleanup_content({"scanned_at": 1.0, "items": items}, parse_filters({}), batches, "tok")
        self.assertIn("tom fil", swedish)
        self.assertIn("trasiga tecken", swedish)
        legacy = dict(items[0], problems=["tom fil"])
        self.assertIn("tom fil", render_cleanup_content({"scanned_at": 1.0, "items": [legacy]},
                                                       parse_filters({}), [], "tok", lang="en"))


    def test_duplicate_key_groups_same_video_language_and_variant(self):
        files = ["Film.mkv", "Film.sv.srt", "Film.swe.srt", "Film.ai.sv.srt", "Film.sv.forced.srt", "Film.en.srt",
                 "Film.srt", "Annan.sv.srt"]
        key = lambda name: duplicate_key(name, "/m/Film", files)
        self.assertTrue(key("Film.sv.srt"))
        self.assertEqual(key("Film.sv.srt"), key("Film.swe.srt"))
        self.assertEqual(key("Film.sv.srt"), key("Film.ai.sv.srt"))
        self.assertEqual(key("Film.sv.srt"), key("FILM.SV.SRT"))
        self.assertNotEqual(key("Film.sv.srt"), key("Film.sv.forced.srt"))
        self.assertNotEqual(key("Film.sv.srt"), key("Film.en.srt"))
        self.assertEqual(key("Film.srt"), "")
        self.assertEqual(key("Annan.sv.srt"), "")

    def test_scan_and_filter_find_duplicates_side_by_side(self):
        with tempfile.TemporaryDirectory() as folder:
            root, folder_path = make_library(Path(folder))
            (folder_path / "Film.swe.srt").write_text("dup")
            (folder_path / "Film.sv.forced.srt").write_text("forced")
            items = scan_srt([root])
            dups = {item["name"]: item["dups"] for item in items}
            self.assertEqual(dups["Film.sv.srt"], 2)
            self.assertEqual(dups["Film.swe.srt"], 2)
            self.assertEqual(dups["Film.ai.sv.srt"], 2)
            self.assertEqual(dups["Film.sv.forced.srt"], 0)
            self.assertEqual(dups["Annan.sv.srt"], 0)
            found = apply_filters(items, parse_filters({"dupes": ["1"]}))
            self.assertEqual(sorted(item["name"] for item in found),
                             ["Film.ai.sv.srt", "Film.sv.srt", "Film.swe.srt"])
            self.assertEqual(len({item["dup_key"] for item in found}), 1)
            html = render_cleanup_content({"scanned_at": 1.0, "items": items}, parse_filters({"dupes": ["1"]}),
                                          [], "tok")
            self.assertIn("dubblett (en av 3)", html)
            self.assertIn("name='dupes'", html)


if __name__ == "__main__":
    unittest.main()
