from html import escape

from library import is_image_subtitle

LANGUAGES = {"eng": "Engelska", "en": "Engelska", "fra": "Franska", "fre": "Franska", "fr": "Franska", "swe": "Svenska", "sv": "Svenska", "und": "Språk ej angivet"}


def format_file_size(size_bytes):
    """MB for anything under 1 GiB -- most /extra clips are tens of MB and
    were otherwise always rounding down to a meaningless '0.0 GiB'."""
    gib = size_bytes / 1024 ** 3
    if gib >= 1:
        return f"{gib:.1f} GiB"
    return f"{size_bytes / 1024 ** 2:.1f} MB"


def format_bit_rate(bits_per_second):
    mbit = bits_per_second / 1_000_000
    return f"{mbit:.1f} Mbit/s" if mbit >= 1 else f"{bits_per_second / 1000:.0f} kbit/s"


def format_video_track(video):
    details = [f"Spår {video['index']}", str(video["codec"]).upper()]
    if video.get("profile"):
        details.append(video["profile"])
    if video.get("width") and video.get("height"):
        details.append(f"{video['width']}×{video['height']}")
    if video.get("fps"):
        details.append(f"{video['fps']:g} fps")
    if video.get("bit_rate"):
        details.append(format_bit_rate(video["bit_rate"]))
    if video.get("bit_depth"):
        details.append(f"{video['bit_depth']}-bit")
    if video.get("hdr"):
        details.append(video["hdr"])
    return " · ".join(details)


def render_tracks(item):
    probe = item.get("probe", {})
    track_path = escape(item.get("path", ""))
    if probe.get("status") == "error":
        return "<p>" + escape(probe["message"]) + "</p>"
    if probe.get("status") != "ok":
        return "<p>Skanna biblioteket för att läsa spårinformationen.</p>"
    sections = []
    # Files scanned before video details existed have no "video" key until the
    # next scan; show nothing rather than a misleading "no video found".
    if "video" in probe:
        video_lines = "".join("<li>" + escape(format_video_track(video)) + "</li>"
                              for video in probe["video"])
        if video_lines and probe.get("bit_rate"):
            video_lines += "<li>" + escape(f"Totalt {format_bit_rate(probe['bit_rate'])}") + "</li>"
        sections.append(f"<h3>Video</h3><ul>{video_lines}</ul>" if video_lines
                        else "<h3>Video</h3><p>Inget videospår hittades.</p>")
    for key, heading in (("audio", "Ljudspår"), ("subtitle", "Inbäddade undertexter")):
        lines = []
        for track in probe[key]:
            details = [f"Spår {track['index']}", LANGUAGES.get(track["language"].lower(), track["language"]), track["codec"]]
            if key == "subtitle" and is_image_subtitle(track):
                details.append("PGS (bild)" if "pgs" in track["codec"].lower() else "bildbaserad")
            if track.get("channels"):
                details.append(f"{track['channels']} kanaler")
            for flag, label in (("default", "standard"), ("forced", "forced / utvalda repliker"), ("hearing_impaired", "hörselanpassad")):
                if track.get(flag):
                    details.append(label)
            if track.get("title"):
                details.append(track["title"])
            lines.append("<li>" + escape(" · ".join(details)) + "</li>")
        sections.append(f"<h3>{heading}</h3><ul>{''.join(lines)}</ul>" if lines else f"<h3>{heading}</h3><p>Inga spår hittades.</p>")
    return ("<details class='track-details' data-track-path='" + track_path + "'>"
            "<summary>Visa video, ljud och undertexter</summary>" + "".join(sections) + "</details>")
