"""Inspect container metadata without transcoding or modifying media."""
import json
import logging
import re
import subprocess

ENTRIES = (
    "format=duration,bit_rate"
    ":stream=index,codec_type,codec_name,profile,width,height,r_frame_rate,avg_frame_rate,"
    "bit_rate,pix_fmt,bits_per_raw_sample,color_transfer,channels,channel_layout"
    ":stream_tags=language,title,BPS"
    ":stream_disposition=default,forced,hearing_impaired,comment,visual_impaired,attached_pic"
    ":stream_side_data=side_data_type"
)


def _number(value):
    """ffprobe reports missing values as absent keys or the string 'N/A'."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _frame_rate(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        numerator, _, denominator = str(stream.get(key, "")).partition("/")
        try:
            top, bottom = float(numerator), float(denominator or 1)
        except ValueError:
            continue
        if top > 0 and bottom > 0:
            return round(top / bottom, 3)
    return None


def _bit_depth(stream):
    raw = _number(stream.get("bits_per_raw_sample"))
    if raw:
        return int(raw)
    match = re.search(r"p(\d{2})(?:le|be)?$", str(stream.get("pix_fmt", "")))
    return int(match.group(1)) if match else None


def _hdr_label(stream):
    side_data = " ".join(str(item.get("side_data_type", "")).lower()
                         for item in stream.get("side_data_list", []))
    if "dovi" in side_data or "dolby vision" in side_data:
        return "Dolby Vision"
    if "2094-40" in side_data:
        return "HDR10+"
    transfer = stream.get("color_transfer")
    if transfer == "smpte2084":
        return "HDR10"
    if transfer == "arib-std-b67":
        return "HLG"
    return None


def probe_media(path):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file",
            "-probesize", "10000000", "-analyzeduration", "10000000",
            "-show_entries", ENTRIES,
            "-of", "json", str(path),
        ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=True)
        data = json.loads(result.stdout)
        tracks = {"audio": [], "subtitle": []}
        video = []
        for stream in data.get("streams", []):
            kind = stream.get("codec_type")
            tags = stream.get("tags", {})
            flags = stream.get("disposition", {})
            if kind == "video":
                # Cover art embedded as a video stream is not the real picture.
                if flags.get("attached_pic"):
                    continue
                bit_rate = _number(stream.get("bit_rate")) or _number(tags.get("BPS"))
                video.append({
                    "index": stream["index"], "codec": stream.get("codec_name", "unknown"),
                    "profile": stream.get("profile"), "width": stream.get("width"),
                    "height": stream.get("height"), "fps": _frame_rate(stream),
                    "bit_rate": int(bit_rate) if bit_rate else None,
                    "bit_depth": _bit_depth(stream), "hdr": _hdr_label(stream),
                })
                continue
            if kind not in tracks:
                continue
            tracks[kind].append({
                "index": stream["index"], "codec": stream.get("codec_name", "unknown"),
                "language": tags.get("language", "und"), "title": tags.get("title", ""),
                "channels": stream.get("channels"), "channel_layout": stream.get("channel_layout"),
                "default": bool(flags.get("default")), "forced": bool(flags.get("forced")),
                "hearing_impaired": bool(flags.get("hearing_impaired")),
                "commentary": bool(flags.get("comment")),
                "visual_impaired": bool(flags.get("visual_impaired")),
            })
        total_bit_rate = _number(data.get("format", {}).get("bit_rate"))
        return {"status": "ok", "duration_seconds": data.get("format", {}).get("duration"),
                "bit_rate": int(total_bit_rate) if total_bit_rate else None,
                "video": video, **tracks}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Analysen tog för lång tid. Skanna igen för att försöka på nytt."}
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        logging.exception("Could not inspect media: %s", path)
        return {"status": "error", "message": "Kunde inte läsa spårinformationen. Skanna igen för att försöka på nytt."}
