import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/app")
from probe import probe_media
from presentation import render_tracks


class ProbeTests(unittest.TestCase):
    def test_real_tracks_and_escaping(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subtitle = root / "sample.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:00,900\nBonjour\n", encoding="utf-8")
            media = root / "L'âme.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-i", str(subtitle), "-t", "1", "-map", "0:a", "-map", "1:s", "-c:a", "pcm_s16le", "-c:s", "srt", "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=<script>bad</script>", "-metadata:s:s:0", "language=fra", "-disposition:s:0", "forced", str(media)], check=True, timeout=15)
            before = media.stat()
            result = probe_media(media)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["audio"][0]["language"], "eng")
            self.assertEqual(result["subtitle"][0]["language"], "fra")
            self.assertTrue(result["subtitle"][0]["forced"])
            self.assertEqual(media.stat().st_mtime_ns, before.st_mtime_ns)
            html = render_tracks({"probe": result})
            self.assertIn("Engelska", html)
            self.assertIn("Franska", html)
            self.assertNotIn("<script>", html)

    def test_real_video_stream_details(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "clip.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media)],
                           check=True, timeout=30)
            result = probe_media(media)
            self.assertEqual(result["status"], "ok")
            video = result["video"][0]
            self.assertEqual(video["codec"], "h264")
            self.assertEqual((video["width"], video["height"]), (320, 240))
            self.assertAlmostEqual(video["fps"], 25.0, places=1)
            self.assertEqual(video["bit_depth"], 8)
            self.assertIsNone(video["hdr"])
            html = render_tracks({"probe": result})
            self.assertIn("320×240", html)
            self.assertIn("H264", html)

    def _probe_with(self, streams, format_fields=None):
        output = json.dumps({"streams": streams, "format": format_fields or {"duration": "5"}})
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        with patch("probe.subprocess.run", return_value=completed):
            return probe_media("sample.mkv")

    def test_hdr_variants_are_recognised(self):
        base = {"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 3840,
                "height": 2160, "avg_frame_rate": "24000/1001", "pix_fmt": "yuv420p10le"}
        hdr10 = self._probe_with([{**base, "color_transfer": "smpte2084"}])["video"][0]
        self.assertEqual(hdr10["hdr"], "HDR10")
        self.assertEqual(hdr10["bit_depth"], 10)
        self.assertAlmostEqual(hdr10["fps"], 23.976, places=3)
        hlg = self._probe_with([{**base, "color_transfer": "arib-std-b67"}])["video"][0]
        self.assertEqual(hlg["hdr"], "HLG")
        dolby = self._probe_with([{**base, "color_transfer": "smpte2084", "side_data_list": [
            {"side_data_type": "DOVI configuration record"}]}])["video"][0]
        self.assertEqual(dolby["hdr"], "Dolby Vision")
        plus = self._probe_with([{**base, "color_transfer": "smpte2084", "side_data_list": [
            {"side_data_type": "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"}]}])["video"][0]
        self.assertEqual(plus["hdr"], "HDR10+")

    def test_cover_art_is_not_a_video_track_and_missing_bitrate_falls_back(self):
        result = self._probe_with([
            {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"index": 1, "codec_type": "video", "codec_name": "h264", "bit_rate": "N/A",
             "tags": {"BPS": "4500000"}, "width": 1920, "height": 1080, "avg_frame_rate": "0/0",
             "r_frame_rate": "25/1"},
        ], {"duration": "9", "bit_rate": "5000000"})
        self.assertEqual(len(result["video"]), 1)
        self.assertEqual(result["video"][0]["bit_rate"], 4500000)
        self.assertEqual(result["video"][0]["fps"], 25.0)
        self.assertEqual(result["bit_rate"], 5000000)
        html = render_tracks({"probe": result})
        self.assertIn("4.5 Mbit/s", html)
        self.assertIn("Totalt 5.0 Mbit/s", html)

    def test_pgs_subtitle_track_is_labelled(self):
        result = self._probe_with([{"index": 2, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
                                    "tags": {"language": "swe"}}])
        html = render_tracks({"probe": result})
        self.assertIn("PGS (bild)", html)

    def test_older_scan_without_video_key_does_not_claim_missing_video(self):
        html = render_tracks({"probe": {"status": "ok", "audio": [], "subtitle": []}})
        self.assertNotIn("Inget videospår", html)

    def test_timeout_is_visible(self):
        with patch("probe.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 45)):
            self.assertEqual(probe_media("sample.mkv")["status"], "error")

    def test_invalid_file_is_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.mkv"
            path.write_text("not a video")
            with self.assertLogs(level="ERROR"):
                self.assertEqual(probe_media(path)["status"], "error")
