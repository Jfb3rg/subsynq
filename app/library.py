"""Read-only discovery with optional track inspection."""
import os
import re
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm"}
EPISODE_CODE = re.compile(r"S(?P<season>\d{1,2})E(?P<episode>\d{1,3})", re.IGNORECASE)
SEASON_FOLDER = re.compile(r"(?:season|säsong|sesson)\s*(?P<season>\d+)", re.IGNORECASE)
SWEDISH_LANGUAGE_CODES = {"sv", "swe", "sv-se", "swe-se", "swedish", "svenska"}
ENGLISH_LANGUAGE_CODES = {"en", "eng", "en-us", "en-gb", "english", "engelska"}
# Picture-based subtitle formats: they cannot be read as text, so a PGS track
# is real coverage for a viewer but not for SubSynq's own text pipeline.
IMAGE_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
SIDECAR_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
SIDECAR_FLAGS = {"ai", "forced", "sdh", "cc", "hi", "default", "full", "signs"}


def is_image_subtitle(track):
    return str(track.get("codec", "")).lower() in IMAGE_SUBTITLE_CODECS


def _language_class(language, title=""):
    language = str(language or "").strip().lower().replace("_", "-")
    title_words = set(re.findall(r"[\w-]+", str(title or "").lower(), flags=re.UNICODE))
    if language in SWEDISH_LANGUAGE_CODES or title_words & {"swedish", "svenska"}:
        return "sv"
    if language in ENGLISH_LANGUAGE_CODES or title_words & {"english", "engelska"}:
        return "en"
    return None


def sidecar_languages(media, filenames):
    """Languages of external subtitle files named like '<video>.sv.srt'.

    Only the names already listed by the directory walk are looked at, so this
    costs no extra file access and never opens or changes a subtitle file.
    """
    prefix = media.stem.lower() + "."
    found = set()
    for name in filenames:
        lowered = name.lower()
        suffix = Path(lowered).suffix
        if not lowered.startswith(prefix) or suffix not in SIDECAR_EXTENSIONS:
            continue
        tokens = lowered[len(prefix):-len(suffix)].split(".")
        languages = {_language_class(token) for token in tokens} - {None}
        # Any other word means this is probably another video's subtitle
        # that merely shares the start of the filename.
        if languages and all(_language_class(token) or token in SIDECAR_FLAGS for token in tokens):
            found |= languages
    return sorted(found)


def subtitle_coverage(item):
    """Which subtitle languages/kinds a library item already has, from any source."""
    probe = item.get("probe") or {}
    probed = probe.get("status") == "ok"
    swedish = english = image = False
    for track in probe.get("subtitle", []) if probed else []:
        language = _language_class(track.get("language"), track.get("title"))
        swedish = swedish or language == "sv"
        english = english or language == "en"
        image = image or is_image_subtitle(track)
    sidecars = set(item.get("sidecar_langs") or ())
    return {
        "sv": swedish or bool(item.get("ai_sv_exists")) or "sv" in sidecars,
        "en": english or bool(item.get("ai_en_exists")) or "en" in sidecars,
        "image": image,
        "probed": probed,
    }


def has_embedded_swedish_subtitle(probe_result):
    """Treat any clearly Swedish embedded subtitle as existing coverage."""
    if probe_result.get("status") != "ok":
        return False
    for track in probe_result.get("subtitle", []):
        language = str(track.get("language", "")).strip().lower().replace("_", "-")
        title = str(track.get("title", "")).strip().lower()
        title_words = set(re.findall(r"[\w-]+", title, flags=re.UNICODE))
        if language in SWEDISH_LANGUAGE_CODES or title_words.intersection({"swedish", "svenska"}):
            return True
    return False


def episode_identity(media, root):
    """Return stable grouping labels from common TV layouts and episode names."""
    relative = media.relative_to(root)
    match = EPISODE_CODE.search(media.stem)
    season_number = int(match.group("season")) if match else None
    episode_number = int(match.group("episode")) if match else None
    parent_match = SEASON_FOLDER.search(media.parent.name)
    if parent_match:
        season_number = int(parent_match.group("season"))
    if len(relative.parts) >= 3 and not SEASON_FOLDER.search(relative.parts[0]):
        series = relative.parts[0]
    elif match and media.stem[:match.start()].strip(" .-_"):
        series = media.stem[:match.start()].strip(" .-_")
    else:
        series = root.name if root.name not in ("tv", "movies", "extra") else "Okänd serie"
    return {
        "series": series,
        "season": f"Säsong {season_number}" if season_number is not None else "Utan säsong",
        "season_number": season_number,
        "episode_number": episode_number,
        "episode_code": (f"S{season_number:02d}E{episode_number:02d}"
                         if season_number is not None and episode_number is not None else None),
    }


def scan_library(roots, probe=None, kind_overrides=None, progress=None):
    items = []
    def fail(error):
        raise error
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            raise FileNotFoundError(f"Biblioteksmappen saknas: {root}")
        found = 0
        for directory, dirs, files in os.walk(root, onerror=fail, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink())
            for filename in sorted(files):
                media = Path(directory, filename)
                if media.suffix.lower() not in VIDEO_EXTENSIONS or media.is_symlink():
                    continue
                info = media.stat()
                preferred_subtitle = media.with_suffix(".ai.sv.srt")
                legacy_subtitle = media.with_suffix(".sv.ai.srt")
                english_subtitle = media.with_suffix(".ai.en.srt")
                identity = episode_identity(media, root)
                probe_result = probe(media) if probe else {"status": "pending"}
                items.append({
                    "path": str(media), "name": media.name,
                    "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
                    "ai_sv_exists": preferred_subtitle.is_file() or legacy_subtitle.is_file(),
                    "ai_sv_path": (str(preferred_subtitle) if preferred_subtitle.is_file()
                                   else str(legacy_subtitle) if legacy_subtitle.is_file() else None),
                    "ai_en_exists": english_subtitle.is_file(),
                    "ai_en_path": str(english_subtitle) if english_subtitle.is_file() else None,
                    "kind": (kind_overrides or {}).get(str(media), "episode" if root.name == "tv" else "movie" if root.name == "movies" else "media"),
                    "probe": probe_result,
                    "embedded_sv_exists": has_embedded_swedish_subtitle(probe_result),
                    "sidecar_langs": sidecar_languages(media, files),
                    **identity,
                })
                found += 1
                if progress:
                    progress(str(root), found)
    return items
