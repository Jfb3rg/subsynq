"""Read-only discovery with optional track inspection."""
import os
import re
import time
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


def _default_kind(root):
    return "episode" if root.name == "tv" else "movie" if root.name == "movies" else "media"


def _reusable_probe(previous, size, mtime_ns):
    """The earlier probe of an unchanged file (same size and modification time), or None. A failed or pending probe is never reused."""
    if not previous or previous.get("size_bytes") != size or previous.get("mtime_ns") != mtime_ns:
        return None
    probe = previous.get("probe")
    return probe if isinstance(probe, dict) and probe.get("status") not in (None, "pending", "error") else None


def scan_library(roots, probe=None, kind_overrides=None, progress=None, previous=None, directories=None, state=None, recursive=True):
    """Every video file under `roots`, in folder order.

    `previous` (the items of the last scan) lets an unchanged file keep its earlier track inspection: the file itself is not opened again.
    `directories` (the folder record of the last scan, see `state`) makes the scan quick: a folder whose modification time is unchanged was
    not given, removed or renamed a file, so it is not even listed; its files keep their earlier items. Only folders where something
    changed are read, and only new or changed files are inspected. Before 0.8.362 the automatic scan opened every video file every five
    minutes and the library's disks could never spin down (the user, 2026-11-04).

    `state` (a dict) receives 'directories' (the folder record to keep for the next quick scan) and counts: 'listed', 'skipped', 'probed',
    'reused'."""
    previous_items = {str(item.get("path")): item for item in (previous or ())}
    old_directories = directories or {}
    new_directories = {}
    counts = {"listed": 0, "skipped": 0, "probed": 0, "reused": 0}
    items = []
    overrides = kind_overrides or {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            raise FileNotFoundError(f"Biblioteksmappen saknas: {root}")
        found = 0
        stack = [root]
        while stack:
            directory = stack.pop()
            key = str(directory)
            mtime_ns = os.stat(directory).st_mtime_ns
            old = old_directories.get(key)
            if (directories is not None and old and old.get("mtime_ns") == mtime_ns
                    and all(path in previous_items and "opensub_sv_exists" in previous_items[path] for path in old.get("files", []))):
                counts["skipped"] += 1
                new_directories[key] = old
                for path in old.get("files", []):
                    item = dict(previous_items[path])
                    item["kind"] = overrides.get(path, _default_kind(root))
                    items.append(item)
                    counts["reused"] += 1
                    found += 1
                if progress and old.get("files"):
                    progress(str(root), found)
                stack.extend(Path(sub) for sub in reversed(old.get("subdirs", [])))
                continue
            counts["listed"] += 1
            with os.scandir(directory) as listing:
                entries = list(listing)
            subdirs = sorted(entry.path for entry in entries if entry.is_dir(follow_symlinks=False))
            files = sorted(entry.name for entry in entries if not entry.is_dir(follow_symlinks=False))
            names = set(files)
            videos = []
            for filename in files:
                media = Path(directory, filename)
                if media.suffix.lower() not in VIDEO_EXTENSIONS or media.is_symlink():
                    continue
                info = media.stat()
                preferred_subtitle = media.with_suffix(".ai.sv.srt")
                legacy_subtitle = media.with_suffix(".sv.ai.srt")
                english_subtitle = media.with_suffix(".ai.en.srt")
                identity = episode_identity(media, root)
                probe_result = _reusable_probe(previous_items.get(str(media)), info.st_size, info.st_mtime_ns) if previous_items else None
                if probe_result is not None:
                    counts["reused"] += 1
                else:
                    probe_result = probe(media) if probe else {"status": "pending"}
                    counts["probed"] += 1 if probe else 0
                has_preferred, has_legacy = preferred_subtitle.name in names, legacy_subtitle.name in names
                items.append({
                    "path": str(media), "name": media.name,
                    "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
                    "ai_sv_exists": has_preferred or has_legacy,
                    "ai_sv_path": (str(preferred_subtitle) if has_preferred else str(legacy_subtitle) if has_legacy else None),
                    "ai_en_exists": english_subtitle.name in names,
                    "opensub_sv_exists": (media.stem + ".opensub.sv.srt") in names,
                    "ai_en_path": str(english_subtitle) if english_subtitle.name in names else None,
                    "kind": overrides.get(str(media), _default_kind(root)),
                    "probe": probe_result,
                    "embedded_sv_exists": has_embedded_swedish_subtitle(probe_result),
                    "sidecar_langs": sidecar_languages(media, files),
                    **identity,
                })
                videos.append(str(media))
                found += 1
                if progress:
                    progress(str(root), found)
            # A folder changed within the last two seconds may change again within the same timestamp tick (a file added while it was
            # being read): it is not trusted, so the next quick scan reads it again (the "racy" case git also guards against).
            trusted = time.time_ns() - mtime_ns > 2_000_000_000
            new_directories[key] = {"mtime_ns": mtime_ns if trusted else -1, "files": videos,
                                    "subdirs": [sub for sub in subdirs if not Path(sub).is_symlink()]}
            if recursive:
                stack.extend(Path(sub) for sub in reversed(new_directories[key]["subdirs"]))
    if state is not None:
        state.update(counts, directories=new_directories)
    return items
