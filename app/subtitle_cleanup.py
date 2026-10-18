"""Find old .srt files and move them to a recoverable trash ("Städa undertexter").

Nothing here ever deletes a file directly. Selected subtitles are moved to
<config>/trash/<batch>/ together with a manifest, so a batch can be put back
or the trash emptied later. Only real (non-symlink) *.srt files below the
media roots can be touched; video files are never considered.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from i18n import STRINGS, t
from library import SIDECAR_FLAGS, VIDEO_EXTENSIONS, _language_class

PAGE_SIZE = 200
BATCH_ID = re.compile(r"\d{8}-\d{6}(?:-\d+)?")
KINDS = ("other", "ai", "all")
MAX_RETENTION_DAYS = 3650


def classify(name):
    """'ai' for SubSynq's own files (Film.ai.sv.srt, Film.sv.ai.srt), else 'other'."""
    tokens = name.lower()[:-len(".srt")].split(".")
    return "ai" if "ai" in tokens[1:] else "other"


def guess_language(name):
    tokens = name.lower()[:-len(".srt")].split(".")
    for token in tokens[1:]:
        language = _language_class(token)
        if language:
            return language
    return ""


def duplicate_key(srt_name, directory, filenames):
    """Key shared by subtitles for the same video, language and variant ('' if it cannot be told).

    Forced/SDH/etc. variants are kept apart, but SubSynq's own ".ai" tag is not a
    variant: Film.sv.srt and Film.ai.sv.srt count as the same Swedish subtitle.
    Only Swedish and English are recognised, same as the rest of the app.
    """
    lowered = srt_name.lower()[:-len(".srt")]
    matching = [Path(name).stem.lower() for name in filenames
                if Path(name).suffix.lower() in VIDEO_EXTENSIONS
                and (lowered == Path(name).stem.lower() or lowered.startswith(Path(name).stem.lower() + "."))]
    if not matching:
        return ""
    stem = max(matching, key=len)
    tokens = lowered[len(stem):].strip(".").split(".") if lowered != stem else []
    languages = {_language_class(token) for token in tokens} - {None}
    if len(languages) != 1:
        return ""
    flags = sorted(token for token in tokens if token in SIDECAR_FLAGS - {"ai"})
    return "|".join([str(directory), stem, languages.pop(), ",".join(flags)])


def has_video_sibling(srt_name, filenames):
    """True when a video in the same folder is named like this subtitle's start."""
    lowered = srt_name.lower()[:-len(".srt")]
    for name in filenames:
        path = Path(name)
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        stem = path.stem.lower()
        if lowered == stem or lowered.startswith(stem + "."):
            return True
    return False


CUE_TIMING = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")
MOJIBAKE = re.compile("Ã[¥¤¶…„–©¼]")
MAX_INSPECT_BYTES = 400_000


def inspect_srt(path):
    """Reason codes (see cleanup_problem_* in i18n.py) for why a subtitle file looks broken (empty list = looks fine)."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_INSPECT_BYTES)
    except OSError:
        return ["unreadable"]
    if not raw.strip():
        return ["empty"]
    problems = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8-sig", errors="replace")
        problems.append("bad_encoding")
    else:
        if len(MOJIBAKE.findall(text)) >= 2:
            problems.append("mojibake")
    if not CUE_TIMING.search(text):
        problems.append("no_cues")
    elif not any(re.search(r"[^\W\d_]", line) and not CUE_TIMING.search(line) for line in text.splitlines()):
        problems.append("empty_cues")
    return problems


def scan_srt(roots):
    """Walk the media roots (without following symlinks) and describe every .srt."""
    items = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink())
            for filename in sorted(files):
                if not filename.lower().endswith(".srt"):
                    continue
                path = Path(directory, filename)
                if path.is_symlink():
                    continue
                try:
                    info = path.stat()
                except OSError:
                    continue
                items.append({
                    "path": str(path), "root": str(root), "rel": str(path.relative_to(root)),
                    "name": filename, "size": info.st_size, "mtime": info.st_mtime,
                    "kind": classify(filename), "lang": guess_language(filename),
                    "orphan": not has_video_sibling(filename, files),
                    "problems": inspect_srt(path),
                    "dup_key": duplicate_key(filename, directory, files),
                })
    group_sizes = {}
    for item in items:
        if item["dup_key"]:
            group_sizes[item["dup_key"]] = group_sizes.get(item["dup_key"], 0) + 1
    for item in items:
        item["dups"] = max(0, group_sizes.get(item["dup_key"], 0) - 1) if item["dup_key"] else 0
    return items


def write_atomically(target, text):
    """Replace `target` in one step. The temporary file has its own unique name: with one shared name, two saves at the same
    moment (several scan clicks) both wrote it and the first rename took it from the other ("cleanup-scan.json.tmp" not found)."""
    target = Path(target)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(name, 0o644)       # mkstemp creates 0600; the saved file stays readable like a normally created one
        os.replace(name, target)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def save_scan(config, items, now=None):
    write_atomically(Path(config) / "cleanup-scan.json", json.dumps({"scanned_at": now or time.time(), "items": items}))


SCAN_LOCK = threading.Lock()


def scan_running():
    return SCAN_LOCK.locked()


def scan_if_needed(config, roots, jobs, requested):
    """Scan the subtitle folders and save the result, but only ONE scan at a time. A scan of a big library takes tens of seconds
    (the Unraid log: 40-50 s with eight at once, 8 s alone), so a click that arrives meanwhile waits for the running scan and shares
    its result instead of starting another one that slows both down. `requested` is when the click came in; a saved scan that
    finished after that already answers it. Returns True when this call did the scanning."""
    with SCAN_LOCK:
        finished = load_scan(config)["scanned_at"]
        if finished is not None and finished >= requested:
            return False
        save_scan(config, mark_stale(scan_srt(roots), jobs))
        return True


def load_scan(config):
    try:
        data = json.loads((Path(config) / "cleanup-scan.json").read_text(encoding="utf-8"))
        return {"scanned_at": data.get("scanned_at"), "items": list(data.get("items", []))}
    except (OSError, ValueError):
        return {"scanned_at": None, "items": []}


def parse_filters(query):
    """Normalise raw query/form values (each a list, like parse_qs) into filters."""
    kind = query.get("kind", ["other"])[0]
    try:
        older = max(0, int(query.get("older", ["0"])[0] or 0))
    except ValueError:
        older = 0
    return {
        "kind": kind if kind in KINDS else "other",
        "orphan": query.get("orphan", [""])[0] == "1",
        "stale": query.get("stale", [""])[0] == "1",
        "broken": query.get("broken", [""])[0] == "1",
        "dupes": query.get("dupes", [""])[0] == "1",
        "older": older,
        "q": query.get("q", [""])[0].strip()[:100],
    }


def filter_query(filters):
    """The filters as plain URL/form parameters (unchecked boxes are left out)."""
    params = {"kind": filters["kind"], "older": filters["older"], "q": filters["q"]}
    if filters["orphan"]:
        params["orphan"] = "1"
    if filters["stale"]:
        params["stale"] = "1"
    if filters["broken"]:
        params["broken"] = "1"
    if filters["dupes"]:
        params["dupes"] = "1"
    return params


def save_filters(config, filters):
    """Remember the last-used filters so a reload or the menu link keeps them."""
    write_atomically(Path(config) / "cleanup-filters.json", json.dumps(filter_query(filters)))


def load_filters(config):
    try:
        saved = json.loads((Path(config) / "cleanup-filters.json").read_text(encoding="utf-8"))
        return parse_filters({str(key): [str(value)] for key, value in saved.items()})
    except (OSError, ValueError, AttributeError):
        return parse_filters({})


AI_NAME_SUFFIX =re.compile(r"\.(?:ai\.[a-z]{2,3}|[a-z]{2,3}\.ai)$", re.IGNORECASE)


def mark_stale(items, jobs):
    """Flag SubSynq's own subtitles whose video was replaced after they were made.

    Uses the size/mtime fingerprint the completed job recorded when it wrote
    the subtitle. Other subtitles have no such record, so they stay unflagged.
    """
    by_stem = {str(Path(media_path).with_suffix("")): job for media_path, job in jobs.items()}
    for item in items:
        item["stale"] = False
        if item["kind"] != "ai":
            continue
        stem = AI_NAME_SUFFIX.sub("", item["name"][:-len(".srt")])
        job = by_stem.get(str(Path(item["path"]).parent / stem))
        if (not job or job.get("status") != "completed"
                or job.get("media_size_bytes") is None or job.get("media_mtime_ns") is None):
            continue
        try:
            info = Path(job["media_path"]).stat()
        except OSError:
            continue
        item["stale"] = (info.st_size != job["media_size_bytes"]
                         or info.st_mtime_ns != job["media_mtime_ns"])
    return items


def apply_filters(items, filters, now=None):
    now = now or time.time()
    needle = filters["q"].lower()
    result = []
    for item in items:
        # "Videon bytt", "trasig" and "dubblett" are problem filters: when any is
        # ticked the item must match every ticked one and the type filter is ignored
        # (the stale check only matches SubSynq's own files, which the default
        # "other" type would otherwise hide; duplicates are often one of each).
        if filters["stale"] or filters["broken"] or filters["dupes"]:
            if filters["stale"] and not item.get("stale"):
                continue
            if filters["broken"] and not item.get("problems"):
                continue
            if filters["dupes"] and not item.get("dups"):
                continue
        elif filters["kind"] != "all" and item["kind"] != filters["kind"]:
            continue
        if filters["orphan"] and not item["orphan"]:
            continue
        if filters["older"] and now - item["mtime"] < filters["older"] * 86400:
            continue
        if needle and needle not in item["rel"].lower():
            continue
        result.append(item)
    if filters["dupes"]:
        # Keep each group of duplicates together so the extra copies sit side by side.
        result.sort(key=lambda item: (item.get("dup_key", ""), item["name"].lower()))
    return result


def checked_path(path, roots):
    """Return (Path, root) for a real .srt below a media root, else raise ValueError."""
    candidate = Path(path)
    if candidate.suffix.lower() != ".srt" or candidate.is_symlink() or not candidate.is_file():
        raise ValueError("Inte en vanlig .srt-fil")
    resolved = candidate.resolve()
    for root in roots:
        resolved_root = Path(root).resolve()
        if resolved.is_relative_to(resolved_root) and resolved != resolved_root:
            return resolved, resolved_root
    raise ValueError("Filen ligger utanför mediamapparna")


def trash_files(paths, roots, trash_dir, now=None):
    """Move subtitles into a new trash batch. Returns (batch_id, moved, failed)."""
    trash_dir = Path(trash_dir)
    stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d-%H%M%S")
    batch_id, counter = stamp, 0
    while (trash_dir / batch_id).exists():
        counter += 1
        batch_id = f"{stamp}-{counter}"
    batch = trash_dir / batch_id
    moved, failed, entries = 0, [], []
    for path in dict.fromkeys(paths):
        try:
            source, root = checked_path(path, roots)
            destination = batch / root.name / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            size = source.stat().st_size
            shutil.move(str(source), str(destination))
            entries.append({"original": str(source), "trashed": str(destination), "size": size})
            moved += 1
        except (ValueError, OSError) as error:
            failed.append((str(path), str(error)))
    if entries:
        (batch / "manifest.json").write_text(json.dumps({"files": entries}), encoding="utf-8")
    return batch_id, moved, failed


def _manifest_files(batch):
    try:
        return json.loads((Path(batch) / "manifest.json").read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return []


def list_trash(trash_dir):
    batches = []
    trash_dir = Path(trash_dir)
    if not trash_dir.is_dir():
        return batches
    for batch in sorted(trash_dir.iterdir(), reverse=True):
        if not BATCH_ID.fullmatch(batch.name) or batch.is_symlink():
            continue
        files = _manifest_files(batch)
        if files:
            batches.append({"id": batch.name, "count": len(files),
                            "size": sum(entry.get("size", 0) for entry in files)})
    return batches


def restore_batch(batch_id, roots, trash_dir):
    """Put a batch back where it came from; never overwrites an existing file."""
    if not BATCH_ID.fullmatch(batch_id):
        raise ValueError("Okänd papperskorgsomgång")
    batch = Path(trash_dir) / batch_id
    restored, skipped = 0, []
    for entry in _manifest_files(batch):
        original, trashed = Path(entry["original"]), Path(entry["trashed"])
        try:
            if not trashed.resolve().is_relative_to(batch.resolve()):
                raise ValueError("Ogiltig sökväg i papperskorgen")
            if not any(original.resolve().is_relative_to(Path(root).resolve()) for root in roots):
                raise ValueError("Ursprungsmappen ligger utanför mediamapparna")
            if original.exists():
                raise ValueError("Det finns redan en fil på den platsen")
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trashed), str(original))
            restored += 1
        except (ValueError, OSError) as error:
            skipped.append((str(original), str(error)))
    if not skipped:
        shutil.rmtree(batch, ignore_errors=True)
    return restored, skipped


def empty_trash(trash_dir):
    removed = 0
    trash_dir = Path(trash_dir)
    if not trash_dir.is_dir():
        return removed
    for batch in trash_dir.iterdir():
        if BATCH_ID.fullmatch(batch.name) and batch.is_dir() and not batch.is_symlink():
            removed += len(_manifest_files(batch))
            shutil.rmtree(batch, ignore_errors=True)
    return removed


def batch_time(batch_id):
    """When a trash batch was made, from its id (YYYYmmdd-HHMMSS[-n]) in local time."""
    return datetime.strptime(batch_id[:15], "%Y%m%d-%H%M%S").timestamp()


def load_retention(config):
    """Days a trash batch is kept before it is emptied automatically; 0 means never."""
    try:
        days = int(json.loads((Path(config) / "cleanup-settings.json").read_text(encoding="utf-8"))["trash_days"])
        return min(max(days, 0), MAX_RETENTION_DAYS)
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def save_retention(config, days):
    days = min(max(int(days), 0), MAX_RETENTION_DAYS)
    write_atomically(Path(config) / "cleanup-settings.json", json.dumps({"trash_days": days}))
    return days


def purge_old(trash_dir, days, now=None):
    """Remove trash batches older than `days` days. 0 days keeps everything."""
    removed = 0
    trash_dir = Path(trash_dir)
    if days <= 0 or not trash_dir.is_dir():
        return removed
    cutoff = (now or time.time()) - days * 86400
    for batch in trash_dir.iterdir():
        if not BATCH_ID.fullmatch(batch.name) or not batch.is_dir() or batch.is_symlink():
            continue
        try:
            if batch_time(batch.name) >= cutoff:
                continue
        except ValueError:
            continue
        removed += len(_manifest_files(batch))
        shutil.rmtree(batch, ignore_errors=True)
    return removed


def preview_text(path, roots, limit=6000):
    source, _root = checked_path(path, roots)
    with source.open(encoding="utf-8-sig", errors="replace") as handle:
        return handle.read(limit)


def format_size(size):
    return f"{size / 1024:.1f} kB" if size < 1024 ** 2 else f"{size / 1024 ** 2:.1f} MB"


def render_cleanup_content(scan, filters, trash_batches, token, notice="", page=1, now=None,
                           retention_days=0, lang="sv", scanning=False):
    def T(key, **values):
        return t(key, lang).format(**values)

    now = now or time.time()
    matches = apply_filters(scan["items"], filters, now)
    total_pages = max(1, -(-len(matches) // PAGE_SIZE))
    page = min(max(1, page), total_pages)
    shown = matches[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    counts = {kind: sum(1 for item in scan["items"] if item["kind"] == kind) for kind in ("other", "ai")}
    scanned = (datetime.fromtimestamp(scan["scanned_at"]).strftime("%Y-%m-%d %H:%M:%S")
               if scan["scanned_at"] else T("cleanup_no_scan_yet"))
    filter_params = filter_query(filters)
    stale_count = sum(1 for item in scan["items"] if item.get("stale"))
    broken_count = sum(1 for item in scan["items"] if item.get("problems"))
    dupes_count = sum(1 for item in scan["items"] if item.get("dups"))

    def option(value, label):
        selected = " selected" if filters["kind"] == value else ""
        return f"<option value='{value}'{selected}>{escape(label)}</option>"

    def problem_label(code):
        # Older cached scans stored the Swedish text itself; show those as they are.
        key = f"cleanup_problem_{code}"
        return t(key, lang) if key in STRINGS else code

    hidden_filters = "".join(
        f"<input type='hidden' name='{name}' value='{escape(str(value), quote=True)}'>"
        for name, value in filter_params.items())
    notice_html = f"<p class='success' role='status'>{escape(notice)}</p>" if notice else ""
    # Shown at once when "Rescan" is clicked (index.html) and from the server while a scan is running, e.g. after a reload.
    scan_note_html = (f"<p class='scan-note' role='status' data-scan-note{'' if scanning else ' hidden'}>"
                      f"<span class='scan-pulse' aria-hidden='true'>●</span> {escape(T('cleanup_scanning'))}</p>")
    filter_form = (
        "<form action='/cleanup' method='get' class='cleanup-filters'>"
        f"<label>{escape(T('cleanup_filter_type'))} <select name='kind'>"
        f"{option('other', T('cleanup_type_other'))}{option('ai', T('cleanup_type_ai'))}"
        f"{option('all', T('cleanup_type_all'))}</select></label> "
        f"<label><input type='checkbox' name='orphan' value='1'{' checked' if filters['orphan'] else ''}> "
        f"{escape(T('cleanup_orphan'))}</label> "
        f"<label><input type='checkbox' name='stale' value='1'{' checked' if filters['stale'] else ''}> "
        f"{escape(T('cleanup_stale', n=stale_count))}</label> "
        f"<label><input type='checkbox' name='broken' value='1'{' checked' if filters['broken'] else ''}> "
        f"{escape(T('cleanup_broken', n=broken_count))}</label> "
        f"<label><input type='checkbox' name='dupes' value='1'{' checked' if filters['dupes'] else ''}> "
        f"{escape(T('cleanup_dupes', n=dupes_count))}</label> "
        f"<label>{escape(T('cleanup_older_than'))} <input type='number' name='older' min='0' "
        f"value='{filters['older']}' style='width:80px'> {escape(T('cleanup_days'))}</label> "
        f"<label>{escape(T('cleanup_search'))} <input type='text' name='q' "
        f"value='{escape(filters['q'], quote=True)}' style='width:180px'></label> "
        f"<button type='submit' class='small-button'>{escape(T('cleanup_filter_btn'))}</button> "
        f"<button type='submit' name='scan' value='1' class='small-button'>"
        f"{escape(T('cleanup_rescan_btn'))}</button></form>")

    rows = []
    for item in shown:
        badges = ("<span class='subtitle-status available'>SubSynq</span> " if item["kind"] == "ai" else "")
        badges += (f"<span class='subtitle-status missing'>{escape(T('cleanup_badge_orphan'))}</span> "
                   if item["orphan"] else "")
        badges += (f"<span class='subtitle-status failed'>{escape(T('cleanup_badge_stale'))}</span> "
                   if item.get("stale") else "")
        badges += (f"<span class='subtitle-status failed'>{escape(T('cleanup_badge_dupe', n=item['dups'] + 1))}</span> "
                   if item.get("dups") else "")
        badges += "".join(f"<span class='subtitle-status failed'>{escape(problem_label(problem))}</span> "
                          for problem in item.get("problems", []))
        age_days = int((now - item["mtime"]) // 86400)
        view = "/cleanup/view?" + urlencode({"path": item["path"]})
        rows.append(
            "<tr>"
            f"<td><input type='checkbox' name='path' value='{escape(item['path'], quote=True)}' "
            f"aria-label='{escape(T('cleanup_select_aria', name=item['name']), quote=True)}'></td>"
            f"<td class='grow'><code>{escape(item['rel'])}</code><br>{badges}</td>"
            f"<td>{escape(item['lang'] or '–')}</td>"
            f"<td>{escape(format_size(item['size']))}</td>"
            f"<td>{escape(T('cleanup_age_days', n=age_days))}</td>"
            f"<td><a href='{escape(view, quote=True)}' target='_blank' rel='noopener'>"
            f"{escape(T('cleanup_view'))}</a></td></tr>")
    table = (
        f"<table class='stack'><thead><tr><th><input type='checkbox' aria-label='{escape(T('cleanup_select_all_aria'), quote=True)}' "
        "onclick=\"document.querySelectorAll('.cleanup-list input[name=path]').forEach(c=>c.checked=this.checked)\">"
        f"</th><th>{escape(T('cleanup_col_file'))}</th><th>{escape(T('cleanup_col_lang'))}</th>"
        f"<th>{escape(T('cleanup_col_size'))}</th><th>{escape(T('cleanup_col_changed'))}</th><th></th></tr></thead>"
        f"<tbody class='cleanup-list'>{''.join(rows)}</tbody></table>") if rows else (
        f"<p class='muted'>{escape(T('cleanup_none_match'))}"
        + (T('cleanup_none_match_scanned', n=len(scan['items'])) if scan["items"]
           else escape(T('cleanup_none_match_unscanned'))) + "</p>")

    def page_link(number, label):
        query = urlencode({**filter_params, "page": number})
        return f"<a href='/cleanup?{escape(query, quote=True)}'>{escape(label)}</a>"

    pager = ""
    if total_pages > 1:
        pager = ("<p class='muted'>" + (page_link(page - 1, T("cleanup_prev")) + " · " if page > 1 else "")
                 + escape(T("cleanup_page_of", page=page, total=total_pages))
                 + (" · " + page_link(page + 1, T("cleanup_next")) if page < total_pages else "") + "</p>")
    total_size = sum(item["size"] for item in matches)
    form_start = (
        "<form action='/cleanup/trash' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>" + hidden_filters)
    confirm_selected = escape(T("cleanup_confirm_selected"), quote=True)
    confirm_all = escape(T("cleanup_confirm_all", n=len(matches), size=format_size(total_size)), quote=True)
    actions = (
        "<p><button type='submit' name='mode' value='selected' class='danger small-button' "
        f"onclick=\"return confirm('{confirm_selected}')\">{escape(T('cleanup_move_selected'))}</button> "
        "<button type='submit' name='mode' value='filtered' class='danger small-button' "
        f"onclick=\"return confirm('{confirm_all}')\""
        f"{' disabled' if not matches else ''}>{escape(T('cleanup_move_all', n=len(matches)))}</button></p></form>")

    def expires(batch):
        if retention_days <= 0:
            return "–"
        try:
            return datetime.fromtimestamp(batch_time(batch["id"]) + retention_days * 86400).strftime("%Y-%m-%d")
        except ValueError:
            return "–"

    trash_rows = "".join(
        f"<tr><td>{escape(batch['id'])}</td><td>{escape(T('cleanup_files_count', n=batch['count']))}</td>"
        f"<td>{escape(format_size(batch['size']))}</td><td>{escape(expires(batch))}</td>"
        "<td><form action='/cleanup/restore' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<input type='hidden' name='batch' value='{escape(batch['id'], quote=True)}'>"
        f"<button type='submit' class='small-button'>{escape(T('cleanup_restore'))}</button></form></td></tr>"
        for batch in trash_batches)
    confirm_empty = escape(T("cleanup_confirm_empty"), quote=True)
    trash_html = (
        f"<div class='card'><h2>{escape(T('cleanup_trash_heading'))}</h2>"
        + (f"<table><thead><tr><th>{escape(T('cleanup_col_batch'))}</th><th>{escape(T('cleanup_col_count'))}</th>"
           f"<th>{escape(T('cleanup_col_size'))}</th><th>{escape(T('cleanup_col_expires'))}</th><th></th></tr></thead>"
           f"<tbody>{trash_rows}</tbody></table>"
           "<form action='/cleanup/empty' method='post' style='margin-top:12px'>"
           f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
           "<button type='submit' class='danger small-button' "
           f"onclick=\"return confirm('{confirm_empty}')\">{escape(T('cleanup_empty_btn'))}</button></form>"
           if trash_batches else f"<p class='muted'>{escape(T('cleanup_trash_empty'))}</p>")
        + "<form action='/cleanup/retention' method='post' style='margin-top:16px'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<label>{escape(T('cleanup_retention_label'))} <input type='number' name='days' min='0' "
        f"max='{MAX_RETENTION_DAYS}' value='{retention_days}' style='width:80px'> {escape(T('cleanup_days'))}</label> "
        f"<button type='submit' class='small-button'>{escape(T('cleanup_save'))}</button> "
        f"<span class='muted'>{escape(T('cleanup_retention_hint'))}</span></form>"
        + "</div>")

    return (
        f"<div class='card'><h2>{escape(T('cleanup_heading'))}</h2>"
        f"<p class='muted'>{escape(T('cleanup_intro'))}</p>"
        f"{notice_html}{scan_note_html}{filter_form}"
        f"<p class='muted'>{escape(T('cleanup_last_scan', when=scanned, other=counts['other'], ai=counts['ai']))}</p></div>"
        f"<div class='card'><h2>{escape(T('cleanup_matches_heading', n=len(matches), size=format_size(total_size)))}</h2>"
        f"{pager}{form_start}{table}{actions}{pager}</div>"
        f"{trash_html}")
