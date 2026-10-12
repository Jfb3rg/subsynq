"""SubSynq bootstrap: local status UI and persistent application identity."""
import json
import hashlib
import logging
import os
import re
import sqlite3
import uuid
import secrets
import threading
import time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs, quote
from library import VIDEO_EXTENSIONS, has_embedded_swedish_subtitle, scan_library, subtitle_coverage
from probe import probe_media
from presentation import format_file_size, render_tracks
from hardware import allowed_preferences, load_hardware, render_hardware
from cloud_cost import load_profile, parse_profile, render_cloud_cost, render_cloud_market
from cloud_market import PROVIDERS, catalog, load_keys, save_key, remove_key, normalize_gpu_query
from jobs import (ACTIVE, enqueue_delete, enqueue_job, enqueue_restart, initialize_jobs,
                  latest_events, latest_jobs, recent_events, request_cancel)
from i18n import LANGUAGES, t
import models_card
import scan_state
import subtitle_cleanup

VERSION = "0.8.218"
CLEANUP_ROOTS = (Path("/movies"), Path("/tv"), Path("/extra"))
# Vast.ai and Runpod.io can both rent a server running the whole AI chain
# (ai/worker.py rent_cloud_chain() via cloud_gpu.py's provider facade). A saved
# key only makes a provider selectable once its rental code path exists.
CLOUD_RENTAL_PROVIDERS = ('vast', 'runpod')


def enabled_cloud_providers():
    return set(load_keys(CONFIG)) & set(CLOUD_RENTAL_PROVIDERS)
SCAN_TOKEN = secrets.token_urlsafe(32)
SCAN_LOCK = threading.Lock()
SCAN_STATUS = scan_state.ScanStatus()
CONFIG = Path(os.environ.get("SUBSYNQ_CONFIG", "/config"))
DATABASE = CONFIG / "subsynq.db"
PAGE = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
ICON_DIR = Path(__file__).with_name("icons")
ICON_NAMES = ("subsynq", "whisper", "qwen", "gemma", "cpu", "ram", "gpu", "cloud")


def icon_img(name, size=16):
    """Inline <img> for one of the bundled icons in app/icons (served at /icons/<name>.svg)."""
    if name not in ICON_NAMES:
        return ""
    return f"<img class='ico' src='/icons/{name}.svg' width='{size}' height='{size}' alt=''>"

THEMES = {
    "auto": {"label": "Följ systemet", "desc": "Mörkt eller ljust efter enhetens eget systemval",
             "swatch": ("#0e1116", "#151a21", "#7aa7ff")},
    "mork": {"label": "Mörkt", "desc": "Standardtema, mörk yta", "swatch": ("#0e1116", "#151a21", "#7aa7ff")},
    "ljust": {"label": "Ljust", "desc": "Ljus yta, hög kontrast", "swatch": ("#f6f7f9", "#ffffff", "#2f6bff")},
    "midnatt": {"label": "Midnatt", "desc": "Djupblå med lila accent", "swatch": ("#0b1020", "#121a30", "#a78bfa")},
    "skog": {"label": "Skog", "desc": "Mörkgrön med varm accent", "swatch": ("#0f1512", "#16201b", "#e0b45a")},
    "barnsten": {"label": "Bärnsten", "desc": "Varm brun med orange accent", "swatch": ("#14100a", "#1c1610", "#f0a93a")},
    "y2k": {"label": "Y2K Cyber", "desc": "Levande gradient med neonglow och scanlines", "swatch": ("#05060f", "#10142a", "#ff2ec4"),
            "immersive": True, "grad": "linear-gradient(135deg,#4f6df5,#a855f7,#ec4899)"},
    "vapor": {"label": "Vaporwave", "desc": "Levande gradient med neonglow och scanlines", "swatch": ("#1a0b2e", "#2d1b4e", "#ff71ce"),
              "immersive": True, "grad": "linear-gradient(135deg,#38bdf8,#ff71ce)"},
    "synth": {"label": "Synthwave", "desc": "Levande solnedgångsgradient med neonglow och scanlines", "swatch": ("#180a12", "#2a1024", "#ff9e44"),
              "immersive": True, "grad": "linear-gradient(135deg,#ffe066,#ff9e44,#d6249f)"},
    "terminal": {"label": "Terminal", "desc": "Fosforgrön retro-CRT", "swatch": ("#060a06", "#0d150d", "#33ff66")},
}
ACCENTS = {
    "": {"label": "Temats egen", "hex": None},
    "bla": {"label": "Blå", "hex": "#7aa7ff"},
    "lila": {"label": "Lila", "hex": "#a78bfa"},
    "turkos": {"label": "Turkos", "hex": "#2dd4bf"},
    "guld": {"label": "Guld", "hex": "#f5b73b"},
    "rosa": {"label": "Rosa", "hex": "#fb7185"},
}
LIBRARY_KIND_LABELS = (("movie", "Filmer"), ("episode", "Serier"), ("media", "Extra"))


def library_return_path(kind, event):
    if kind not in {"movie", "episode", "media"}:
        kind = "episode"
    return f"/library?kind={kind}&job={event}"


def format_local_timestamp(iso_stamp):
    if not iso_stamp:
        return ""
    try:
        parsed = datetime.fromisoformat(iso_stamp)
    except ValueError:
        return str(iso_stamp)[:19].replace('T', ' ')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def jobs_signature(jobs):
    visible = sorted((str(job.get('id')), job.get('status'), job.get('progress'),
                      job.get('message'), job.get('updated_at')) for job in jobs.values())
    return hashlib.sha256(json.dumps(visible, ensure_ascii=False).encode('utf-8')).hexdigest()


def needs_swedish_subtitle(item, ignore_existing=False):
    if item.get("ai_sv_exists"):
        return False
    return ignore_existing or not item.get("embedded_sv_exists")


def subtitle_is_stale(item, job):
    """True when the video's own size/mtime no longer match what the
    completed job recorded when it wrote the current .ai.sv.srt -- the file
    under this name was replaced or re-encoded after the subtitle was made."""
    if not job or job.get("status") != "completed":
        return False
    fingerprint_size = job.get("media_size_bytes")
    fingerprint_mtime = job.get("media_mtime_ns")
    if fingerprint_size is None or fingerprint_mtime is None:
        return False
    return (item.get("size_bytes") != fingerprint_size
            or item.get("mtime_ns") != fingerprint_mtime)


def runnable_library_item(library, media_path):
    """Return an explicitly scanned video that the worker is allowed to process."""
    return next((candidate for candidate in library.get("items", [])
                 if candidate.get("path") == media_path
                 and candidate.get("kind") in {"episode", "movie", "media"}), None)


class BusyError(Exception):
    """Raised when a library scan is already running."""


def perform_library_scan(choice=scan_state.ALL):
    """Scan one library ('movie', 'episode' or 'media') or all of them and persist the result. Shared by the
    scan buttons and the background automatic scan loop. A scan of one library keeps the others' earlier results."""
    roots = scan_state.roots_for(choice, lambda root: Path(root).is_dir())
    if not SCAN_LOCK.acquire(blocking=False):
        raise BusyError("En skanning pågår redan.")
    try:
        SCAN_STATUS.start(choice, roots)
        try:
            overrides_path = CONFIG / "kind-overrides.json"
            overrides = json.loads(overrides_path.read_text(encoding="utf-8-sig")) if overrides_path.exists() else {}
            items = scan_library(roots, probe=probe_media, kind_overrides=overrides, progress=SCAN_STATUS.progress)
            with sqlite3.connect(DATABASE) as db:
                saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                result = scan_state.merge_scan(json.loads(saved[0]) if saved else {}, roots, items,
                                               datetime.now(timezone.utc).isoformat(), choice == scan_state.ALL)
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('library_scan', ?)", (json.dumps(result),))
        except Exception as error:  # noqa: BLE001 - the status must never stay on "running"
            SCAN_STATUS.fail(error if isinstance(error, FileNotFoundError)
                             else "Kunde inte läsa biblioteksmapparna eller spara resultatet.")
            raise
        SCAN_STATUS.finish(len(items))
    finally:
        SCAN_LOCK.release()
    return result


def render_scan_buttons(selected, library, scanning, token, lang="sv"):
    """One button for the library being viewed, one for everything, and when each was last scanned."""
    root = scan_state.LIBRARY_ROOTS[selected]
    connected = Path(root).is_dir()
    one_disabled = " disabled" if scanning or not connected else ""
    all_disabled = " disabled" if scanning else ""
    stamp = format_local_timestamp(scan_state.scanned_at_for(library, root)) or t("scan_never", lang)
    missing = ("" if connected else
               f"<p class='muted'>{escape(t('scan_optional_missing', lang).format(folder=root))}</p>")
    return (
        "<form action='/scan' method='post' class='scan-actions'>"
        f"<input type='hidden' name='kind' value='{escape(selected, quote=True)}'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<button type='submit' name='library' value='{escape(selected, quote=True)}'{one_disabled}>"
        f"{escape(t('scan_btn_' + selected, lang))}</button>"
        f"<button type='submit' name='library' value='{scan_state.ALL}' class='secondary-button'{all_disabled}>"
        f"{escape(t('scan_btn_all', lang))}</button></form>"
        f"{missing}<p class='muted'>{escape(t('scan_last', lang).format(folder=root, when=stamp))}</p>")


def run_background_scan(choice):
    """Run a scan started from a button, so the page can show live status instead of waiting for the answer."""
    try:
        perform_library_scan(choice)
    except BusyError:
        pass
    except (OSError, sqlite3.Error, ValueError):
        logging.exception("Library scan failed")


def auto_start_missing_jobs(items, ignore_existing=False):
    """Queue a job for every scanned file that has never had one and still
    needs a Swedish subtitle. Files with a previous job (failed, cancelled,
    quality_failed, deleted...) are left for the user to restart by hand --
    auto-starting must never loop forever retrying a file that keeps failing."""
    jobs = latest_jobs(DATABASE)
    for item in items:
        if item.get("kind") not in {"episode", "movie", "media"}:
            continue
        if not needs_swedish_subtitle(item, ignore_existing):
            continue
        if item["path"] in jobs:
            continue
        try:
            enqueue_job(DATABASE, item["path"], item["name"])
        except (sqlite3.Error, OSError):
            logging.exception("Automatisk köning misslyckades för %s", item["path"])


def remap_root(path_str, prefix, container_root):
    """Translate a path from Radarr/Sonarr's own filesystem view to SubSynq's,
    for the common case where the two containers mount the same host share
    at different container paths. An empty prefix means "assume they already
    match" -- the path is used exactly as the webhook sent it."""
    if prefix and path_str.startswith(prefix):
        return container_root + path_str[len(prefix):]
    return path_str


def resolve_webhook_media(path_str, container_root, prefix):
    """Validate a path from a Radarr/Sonarr webhook payload against SubSynq's
    own library roots -- never trust an external payload's path blindly."""
    remapped = remap_root(str(path_str), prefix, container_root)
    media = Path(remapped).resolve()
    root = Path(container_root).resolve()
    if not media.is_relative_to(root):
        raise ValueError(
            f"Sökvägen \"{path_str}\" ligger utanför {container_root} sett från SubSynq. "
            "Kontrollera rotmappen för Radarr/Sonarr under Inställningar.")
    if media.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"Filändelsen stöds inte av SubSynq: {media.name}")
    if media.is_symlink() or not media.is_file():
        raise ValueError(f"Filen hittades inte på SubSynqs sida: {media}")
    return media


def resolve_webhook_media_per_root(path_str, root_prefixes):
    """Like resolve_webhook_media_any_root(), but each SubSynq root gets its
    own external prefix instead of one shared parent -- for a media server
    (Emby on Linux, in practice) that mounts movies and TV as two unrelated
    top-level directories rather than subfolders of a common parent."""
    for container_root, prefix in root_prefixes:
        remapped = remap_root(str(path_str), prefix, container_root)
        media = Path(remapped).resolve()
        root = Path(container_root).resolve()
        if not media.is_relative_to(root):
            continue
        if media.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Filändelsen stöds inte av SubSynq: {media.name}")
        if media.is_symlink() or not media.is_file():
            raise ValueError(f"Filen hittades inte på SubSynqs sida: {media}")
        return media
    roots = ', '.join(root for root, _ in root_prefixes)
    raise ValueError(
        f"Sökvägen \"{path_str}\" ligger utanför {roots} sett från SubSynq. "
        "Kontrollera rotmapparna under Inställningar.")


def queue_webhook_media(media, ignore_existing):
    """Queue a single file discovered via a Radarr/Sonarr webhook. Returns a
    human-readable reason when nothing was queued, or None on success -- the
    caller always answers the webhook with 200 either way, since 'skipped
    because it already has a subtitle' is not a webhook delivery failure."""
    preferred, legacy = media.with_suffix(".ai.sv.srt"), media.with_suffix(".sv.ai.srt")
    if preferred.is_file() or legacy.is_file():
        return "En AI-undertext finns redan för filen."
    if not ignore_existing and has_embedded_swedish_subtitle(probe_media(media)):
        return "Filen har redan en inbäddad svensk undertext."
    try:
        enqueue_job(DATABASE, str(media), media.name)
    except ValueError as error:
        return str(error)
    return None


def trash_purge_loop():
    """Empty subtitle-cleanup trash batches older than the saved retention (off by default)."""
    while True:
        try:
            removed = subtitle_cleanup.purge_old(CONFIG / "trash", subtitle_cleanup.load_retention(CONFIG))
            if removed:
                logging.info("Städa undertexter: %d filer i papperskorgen togs bort automatiskt", removed)
        except OSError:
            logging.exception("Automatic trash purge failed")
        time.sleep(6 * 3600)


def auto_scan_loop():
    interval_seconds = 300
    while True:
        time.sleep(interval_seconds)
        try:
            with sqlite3.connect(DATABASE) as db:
                metadata = dict(db.execute(
                    "SELECT key, value FROM metadata WHERE key IN "
                    "('auto_scan_enabled', 'auto_start_jobs', 'ignore_existing_subtitle')"))
        except sqlite3.Error:
            continue
        if metadata.get("auto_scan_enabled") != "1":
            continue
        try:
            result = perform_library_scan()
        except (BusyError, OSError, sqlite3.Error, ValueError):
            continue
        if metadata.get("auto_start_jobs") == "1":
            auto_start_missing_jobs(result["items"], metadata.get("ignore_existing_subtitle") == "1")


def render_media_row(item, nested=False, token="", job=None, ignore_existing=False, lang="sv"):
    missing = needs_swedish_subtitle(item, ignore_existing)
    row_class = " class='subtitle-missing'" if missing else ""
    episode = (f"<strong>{escape(item.get('episode_code') or t('episode_fallback', lang))}</strong> · "
              if nested else "")
    status_class = "missing" if missing else "available"
    if item.get("ai_sv_exists"):
        status = t("status_ai_exists", lang)
    elif item.get("embedded_sv_exists"):
        status = t("status_embedded_exists", lang)
    else:
        status = t("status_missing", lang)
    job_html = ""
    if item.get("embedded_sv_exists") and not item.get("ai_sv_exists"):
        swedish_tracks = [track for track in item.get("probe", {}).get("subtitle", [])
                          if str(track.get("language", "")).lower().replace("_", "-")
                          in ("sv", "swe", "sv-se", "swe-se")
                          or "swedish" in str(track.get("title", "")).lower()
                          or "svenska" in str(track.get("title", "")).lower()]
        formats = ", ".join(sorted({str(track.get("codec", "undertext")).replace("hdmv_pgs_subtitle", "PGS")
                                    for track in swedish_tracks}))
        skip_note = t("skip_note_forced" if ignore_existing else "skip_note_default", lang)
        job_html = (f"<small class='job-message'>{escape(t('embedded_subtitle_note', lang))}"
                    f"{f' · {escape(formats)}' if formats else ''}. {escape(skip_note)}</small>")
    if job and job.get("status") in ("queued", "transcribing", "translating", "verifying", "writing", "cancel_requested",
                                     "delete_queued", "deleting", "restart_queued", "restarting"):
        labels = {"queued": t("queue_queued", lang), "transcribing": t("queue_transcribing", lang),
                  "translating": t("queue_translating", lang), "verifying": t("queue_verifying", lang),
                  "writing": t("queue_writing", lang), "cancel_requested": t("queue_cancel_requested", lang),
                  "delete_queued": t("queue_delete_queued", lang),
                  "deleting": t("queue_deleting", lang),
                  "restart_queued": t("queue_restart_queued", lang),
                  "restarting": t("queue_restarting", lang)}
        status_class = "working"
        status = f"{labels[job['status']]} · {job.get('progress', 0)} %"
        job_html = f"<small class='job-message'>{escape(job.get('message') or '')}</small>"
        if job['status'] in ('queued', 'transcribing', 'translating', 'verifying', 'writing') and token:
            job_html += (
                "<form action='/jobs/cancel' method='post' class='cancel-job-form preserve-seasons' "
                f"onsubmit=\"return confirm('{escape(t('confirm_cancel_job', lang), quote=True)}')\">"
                f"<input type='hidden' name='token' value='{escape(token)}'>"
                f"<input type='hidden' name='media_path' value='{escape(item['path'])}'>"
                f"<input type='hidden' name='return_kind' value='{escape(item['kind'])}'>"
                f"<input type='hidden' name='job_id' value='{int(job['id'])}'>"
                f"<button type='submit' class='small-button danger'>{escape(t('action_cancel_job', lang))}</button></form>"
            )
    elif job and job.get("status") == "failed":
        status_class = "failed"
        status = t("status_failed", lang)
        job_html = f"<small class='job-message'>{escape(job.get('message') or t('status_unknown_error', lang))}</small>"
    elif job and job.get("status") == "completed" and item.get('ai_sv_exists'):
        status_class = "available"
        status = t("status_ai_created", lang)
        job_html = f"<small class='job-message'>{escape(t('completed_scan_hint', lang))}</small>"
    elif job and job.get("status") == "completed":
        job_html = f"<small class='job-message'>{escape(t('completed_missing_file', lang))}</small>"
    elif job and job.get("status") == "quality_failed":
        status_class = "failed"
        status = t("status_rejected", lang)
        job_html = f"<small class='job-message'>{escape(job.get('message') or '')}</small>"
    elif job and job.get("status") == "deleted":
        job_html = f"<small class='job-message'>{escape(t('deleted_hint', lang))}</small>"
    elif job and job.get("status") == "cancelled":
        status_class = "failed"
        status = t("status_cancelled", lang)
        job_html = f"<small class='job-message'>{escape(job.get('message') or '')}</small>"
    if job and (job.get('hardware') or job.get('model')):
        details = ' · '.join(value for value in (job.get('hardware'), job.get('model')) if value)
        job_html += f"<small class='job-hardware'>{escape(t('used_hardware_model', lang))}: {escape(details)}</small>"
    active_statuses = {"queued", "transcribing", "translating", "verifying", "writing",
                       "cancel_requested", "delete_queued", "deleting",
                       "restart_queued", "restarting"}
    not_active = not job or job.get('status') not in active_statuses
    if item.get('ai_sv_exists') and token and not_active and subtitle_is_stale(item, job):
        job_html += (
            f"<small class='job-message'>{escape(t('stale_video_note', lang))}</small>"
            "<form action='/jobs/restart' method='post' class='restart-job-form preserve-seasons' "
            f"onsubmit=\"return confirm('{escape(t('confirm_restart', lang), quote=True)}')\">"
            f"<input type='hidden' name='token' value='{escape(token)}'>"
            f"<input type='hidden' name='media_path' value='{escape(item['path'])}'>"
            f"<input type='hidden' name='return_kind' value='{escape(item['kind'])}'>"
            f"<button type='submit' class='small-button'>{escape(t('action_rerun', lang))}</button></form>"
        )
    if item.get('ai_sv_exists') and token and not_active:
        job_html += (
            "<form action='/jobs/delete' method='post' class='delete-job-form preserve-seasons' "
            f"onsubmit=\"return confirm('{escape(t('confirm_delete', lang), quote=True)}')\">"
            f"<input type='hidden' name='token' value='{escape(token)}'>"
            f"<input type='hidden' name='media_path' value='{escape(item['path'])}'>"
            f"<input type='hidden' name='return_kind' value='{escape(item['kind'])}'>"
            f"<button type='submit' class='small-button danger'>{escape(t('action_delete', lang))}</button></form>"
        )
    if (item.get('ai_sv_exists') and item.get('ai_sv_path') and token and not_active
            and not subtitle_is_stale(item, job)):
        # The "Run again" button above covers a replaced video; this one recreates a subtitle the user is unhappy with.
        job_html += (
            "<form action='/jobs/restart' method='post' class='restart-job-form preserve-seasons' "
            f"onsubmit=\"return confirm('{escape(t('confirm_recreate', lang), quote=True)}')\">"
            f"<input type='hidden' name='token' value='{escape(token)}'>"
            f"<input type='hidden' name='media_path' value='{escape(item['path'])}'>"
            f"<input type='hidden' name='return_kind' value='{escape(item['kind'])}'>"
            "<input type='hidden' name='recreate' value='1'>"
            f"<button type='submit' class='small-button'>{escape(t('action_recreate', lang))}</button></form>"
        )
    if item.get('ai_sv_exists'):
        media_query = quote(item['path'], safe='')
        job_html += (f"<a class='small-link' href='/subtitles/view?media={media_query}'>"
                     f"{escape(t('action_view_subtitle', lang))}</a>")
    if item.get('ai_en_exists'):
        media_query = quote(item['path'], safe='')
        job_html += (f"<a class='small-link' href='/subtitles/view?media={media_query}&lang=en'>"
                     f"{escape(t('action_view_english_subtitle', lang))}</a>")
    can_start = missing and (not job or job.get("status") in (
        "failed", "quality_failed", "completed", "deleted", "cancelled"))
    if can_start and token:
        job_html += (
            "<form action='/jobs/start' method='post' class='start-job-form preserve-seasons'>"
            f"<input type='hidden' name='token' value='{escape(token)}'>"
            f"<input type='hidden' name='media_path' value='{escape(item['path'])}'>"
            f"<button type='submit' class='small-button'>{escape(t('action_create_subtitle', lang))}</button></form>"
        )
    kind_label_keys = {'episode': 'kind_label_episode', 'movie': 'kind_label_movie'}
    kind_label = t(kind_label_keys.get(item.get('kind'), 'kind_label_media'), lang)
    can_delete = bool(item.get('ai_sv_exists')) and not_active
    bulk_box = ""
    if token and (can_start or can_delete):
        # Belongs to the single bulk form above the list (form= attribute), since
        # every row already holds its own forms and HTML forms cannot nest.
        bulk_box = (f"<input type='checkbox' class='bulk-check' name='media_path' form='bulk-form' "
                    f"value='{escape(item['path'], quote=True)}' data-start='{int(bool(can_start))}' "
                    f"data-delete='{int(can_delete)}' data-recreate='{int(can_delete)}' aria-label='{escape(item['name'], quote=True)}'> ")
    return (
        f"<tr{row_class}><td class='filename'>{bulk_box}{episode}{escape(item['name'])}<br>"
        f"<small>{escape(kind_label)} · "
        f"{format_file_size(item['size_bytes'])}</small>{render_tracks(item)}</td>"
        f"<td><span class='subtitle-status {status_class}'>{status}</span>{job_html}</td></tr>"
    )


ACTIVE_JOB_STATUSES = frozenset({"queued", "transcribing", "translating", "verifying", "writing",
                                 "cancel_requested", "delete_queued", "deleting",
                                 "restart_queued", "restarting"})


def render_bulk_bar(selected, token, lang="sv"):
    """One form for acting on many ticked library rows at once (see render_media_row)."""
    confirm_start = escape(t("bulk_confirm_start", lang), quote=True)
    confirm_delete = escape(t("bulk_confirm_delete", lang), quote=True)
    confirm_recreate = escape(t("bulk_confirm_recreate", lang), quote=True)
    return (
        "<form id='bulk-form' class='bulk-bar preserve-seasons' data-bulk-bar action='/jobs/bulk' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<input type='hidden' name='return_kind' value='{escape(selected, quote=True)}'>"
        f"<p class='muted' data-bulk-hint hidden>{escape(t('bulk_none_usable', lang))}</p>"
        f"<p class='muted'><strong><span data-bulk-count>0</span> {escape(t('bulk_selected', lang))}</strong> · "
        f"<button type='button' class='small-button' data-bulk-select='start'>{escape(t('bulk_select_missing', lang))}</button> "
        f"<button type='button' class='small-button' data-bulk-select='delete'>{escape(t('bulk_select_ai', lang))}</button> "
        f"<button type='button' class='small-button' data-bulk-select='none'>{escape(t('bulk_select_none', lang))}</button></p>"
        "<p><button type='submit' name='action' value='start' class='small-button' "
        f"data-confirm='{confirm_start}'>{escape(t('bulk_start', lang))} (<span data-usable>0</span>)</button> "
        "<button type='submit' name='action' value='delete' class='small-button danger' "
        f"data-confirm='{confirm_delete}'>{escape(t('bulk_delete', lang))} (<span data-usable>0</span>)</button> "
        "<button type='submit' name='action' value='recreate' class='small-button danger' "
        f"data-confirm='{confirm_recreate}'>{escape(t('bulk_recreate', lang))} (<span data-usable>0</span>)</button></p></form>")


def render_library(items, selected, token="", jobs=None, ignore_existing=False, lang="sv"):
    jobs = jobs or {}
    if selected != "episode":
        rows = "".join(render_media_row(item, token=token, job=jobs.get(item['path']),
                                         ignore_existing=ignore_existing, lang=lang) for item in items)
        return (f"<table><thead><tr><th>{escape(t('table_header_file', lang))}</th>"
                f"<th>{escape(t('table_header_subtitle', lang))}</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>") if rows else f"<p>{escape(t('no_video_files', lang))}</p>"
    grouped = {}
    for item in items:
        grouped.setdefault(item.get("series") or t("unknown_series", lang), {}).setdefault(
            item.get("season") or t("no_season", lang), []).append(item)
    output = []
    for series in sorted(grouped, key=str.casefold):
        seasons = grouped[series]
        total_episodes = sum(len(episodes) for episodes in seasons.values())
        total_missing = sum(needs_swedish_subtitle(item, ignore_existing) for episodes in seasons.values()
                            for item in episodes)
        series_missing_text = (f" · {total_missing} {t('missing_subtitle_suffix', lang)}" if total_missing
                               else f" · {t('all_have_subtitle', lang)}")
        series_key = escape(series, quote=True)
        output.append(
            f"<details class='series-group' data-series='{series_key}'><summary>"
            f"<span class='folder-icon' aria-hidden='true'></span> "
            f"<h3>{escape(series)}</h3> <small>{len(seasons)} {escape(t('seasons_count', lang))} · "
            f"{total_episodes} {escape(t('episodes_count', lang))}{series_missing_text}</small></summary>")
        for season in sorted(seasons, key=lambda label: (
                seasons[label][0].get("season_number") is None,
                seasons[label][0].get("season_number") or 0, label.casefold())):
            episodes = sorted(seasons[season], key=lambda item: (
                item.get("episode_number") is None, item.get("episode_number") or 0,
                item["name"].casefold()))
            missing = sum(needs_swedish_subtitle(item, ignore_existing) for item in episodes)
            missing_text = (f" · {missing} {t('missing_subtitle_suffix', lang)}" if missing
                            else f" · {t('all_have_subtitle', lang)}")
            rows = "".join(render_media_row(item, nested=True, token=token, job=jobs.get(item['path']),
                                              ignore_existing=ignore_existing, lang=lang) for item in episodes)
            season_key = escape(f"{series}\u241f{season}", quote=True)
            output.append(
                f"<details class='season-group' data-season='{season_key}'><summary>"
                f"<span class='folder-icon' aria-hidden='true'></span> "
                f"{escape(season)} <small>{len(episodes)} {escape(t('episodes_count', lang))}{missing_text}</small></summary>"
                f"<table><thead><tr><th>{escape(t('table_header_episode', lang))}</th>"
                f"<th>{escape(t('table_header_subtitle', lang))}</th></tr></thead>"
                f"<tbody>{rows}</tbody></table></details>"
            )
        output.append("</details>")
    return "".join(output) if output else f"<p>{escape(t('no_episodes', lang))}</p>"


def initialize():
    CONFIG.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE) as db:
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.executemany("INSERT OR IGNORE INTO metadata VALUES (?, ?)", [
            ("instance_id", str(uuid.uuid4())),
            ("created_at", datetime.now(timezone.utc).isoformat()),
            ("schema_version", "1"),
            ("hardware_preference", "auto"),
            ("theme", "mork"),
            ("accent", ""),
            ("ignore_existing_subtitle", "0"),
            ("auto_scan_enabled", "0"),
            ("auto_start_jobs", "0"),
            ("include_english_subtitle", "0"),
            ("webhook_token", secrets.token_urlsafe(24)),
            ("radarr_root_prefix", ""),
            ("sonarr_root_prefix", ""),
            ("jellyfin_movies_root_prefix", ""),
            ("jellyfin_tv_root_prefix", ""),
            ("emby_movies_root_prefix", ""),
            ("emby_tv_root_prefix", ""),
            ("ui_language", "sv"),
        ])
    initialize_jobs(DATABASE)


def library_counts(items):
    counts = {}
    for kind, _label in LIBRARY_KIND_LABELS:
        matching = [item for item in items if item.get("kind", "media") == kind]
        with_subtitle = sum(1 for item in matching if item.get("ai_sv_exists") or item.get("embedded_sv_exists"))
        counts[kind] = (len(matching), with_subtitle)
    return counts


def library_subtitle_breakdown(items):
    """Per kind: how many files have Swedish/English/picture-based subtitles."""
    breakdown = {}
    for kind, _label in LIBRARY_KIND_LABELS:
        coverage = [subtitle_coverage(item) for item in items if item.get("kind", "media") == kind]
        breakdown[kind] = {
            "total": len(coverage),
            "sv": sum(1 for entry in coverage if entry["sv"]),
            "en": sum(1 for entry in coverage if entry["en"]),
            "image": sum(1 for entry in coverage if entry["image"]),
            "no_sv": sum(1 for entry in coverage if not entry["sv"]),
            "unprobed": sum(1 for entry in coverage if not entry["probed"]),
        }
    return breakdown


def render_subtitle_coverage(breakdown, lang="sv"):
    kind_keys = {"movie": "kind_movie", "episode": "kind_episode", "media": "kind_media"}
    if not any(row["total"] for row in breakdown.values()):
        return ""
    rows = "".join(
        f"<tr><td>{escape(t(kind_keys[kind], lang))}</td><td>{row['total']}</td><td>{row['sv']}</td>"
        f"<td>{row['en']}</td><td>{row['image']}</td><td>{row['no_sv']}</td></tr>"
        for kind, row in breakdown.items())
    unprobed = sum(row["unprobed"] for row in breakdown.values())
    unprobed_note = (f"<p class='muted'>{escape(t('coverage_unprobed', lang).format(count=unprobed))}</p>"
                     if unprobed else "")
    headers = "".join(f"<th>{escape(t(key, lang))}</th>" for key in (
        "coverage_col_total", "coverage_col_sv", "coverage_col_en",
        "coverage_col_image", "coverage_col_no_sv"))
    return (f"<div class='card'><h2>{escape(t('coverage_heading', lang))}</h2>"
            f"<p class='muted'>{escape(t('coverage_intro', lang))}</p>"
            f"<table><thead><tr><th></th>{headers}</tr></thead><tbody>{rows}</tbody></table>"
            f"{unprobed_note}</div>")


def active_job_count(jobs):
    return sum(1 for job in jobs.values() if job.get("status") in ACTIVE)


def accent_css_block(accent):
    info = ACCENTS.get(accent) or ACCENTS[""]
    if not info["hex"]:
        return ""
    hexcode = escape(info["hex"], quote=True)
    return (f"<style>:root[data-theme] {{ --ac:{hexcode}; --acb: color-mix(in srgb, {hexcode} 16%, transparent); }}</style>")


def render_sidebar(active, active_jobs, hardware, lang="sv"):
    def nav_link(href, label, key, count=None):
        classes = "active" if key == active else ""
        count_html = f"<span class='count'>{count}</span>" if count is not None else ""
        return f"<a href='{escape(href, quote=True)}' class='{classes}'>{escape(label)}{count_html}</a>"

    kind_keys = {"movie": "kind_movie", "episode": "kind_episode", "media": "kind_media"}
    bibliotek_links = "".join(
        nav_link(f"/library?kind={kind}", t(kind_keys[kind], lang), f"library:{kind}")
        for kind, _label in LIBRARY_KIND_LABELS
    )
    nav_html = (
        "<div class='nav'>"
        + nav_link("/", t("nav_overview", lang), "overview")
        + nav_link("/queue", t("nav_queue", lang), "queue", active_jobs)
        + nav_link("/cleanup", t("nav_cleanup", lang), "cleanup")
        + f"<div class='nav-heading'>{escape(t('nav_library_heading', lang))}</div>"
        + bibliotek_links
        + "<div style='height:8px;'></div>"
        + f"<details class='nav-group'{' open' if str(active).startswith('settings') else ''}>"
        + f"<summary>{escape(t('nav_settings_heading', lang))}</summary>"
        + nav_link("/settings", t("nav_settings_general", lang), "settings:general")
        + nav_link("/settings/theme", t("nav_settings_theme", lang), "settings:theme")
        + nav_link("/settings/media", t("nav_settings_media", lang), "settings:media")
        + nav_link("/settings/cloud", t("nav_settings_cloud", lang), "settings:cloud")
        + nav_link("/settings/models", t("nav_settings_models", lang), "settings:models")
        + nav_link("/settings/log", t("nav_settings_log", lang), "settings:log")
        + "</details>"
        + "</div>"
    )
    return (
        f"<div><div class='brand'>{icon_img('subsynq', 26)}<span>SubSynq</span></div>" + nav_html + "</div>"
        + render_lokal_motor(hardware, lang)
    )


def render_lokal_motor(hardware, lang="sv"):
    devices = {item["id"]: item for item in hardware["available"]}
    active = devices.get(hardware.get("active"))
    cpu = devices.get("cpu")
    gpu_candidates = [item for item in hardware["available"] if item["id"] != "cpu"]
    gpu = gpu_candidates[0] if gpu_candidates else None
    running = hardware.get("worker_status") == "running"
    resources = hardware.get("resources")
    if resources:
        cpu_fill = f"{resources['cpu_percent']}%"
        cpu_status = f"{resources['cpu_percent']:.0f} %"
        ram_used, ram_total = resources["ram_used_gb"], resources["ram_total_gb"]
        ram_fill = f"{(ram_used / ram_total * 100) if ram_total else 0:.0f}%"
        ram_status = f"{ram_used:.1f} / {ram_total:.1f} GB"
    else:
        cpu_fill = "60%" if running and active and active.get("id") == "cpu" else "6%"
        cpu_status = t("working", lang) if running and active and active.get("id") == "cpu" else t("idle", lang)
        ram_fill = "0%"
        ram_status = t("coming_later", lang)
    gpu_resources = hardware.get("gpu_resources")
    gpu_state_line = ""
    if gpu:
        gpu_running = running and active and active.get("id") == gpu["id"]
        gpu_colour = "var(--ac)" if gpu_running else "var(--wn)"
        gpu_state = t("working", lang) if gpu_running else (t("paused", lang) if not gpu["available"] else t("idle", lang))
        if gpu_resources:
            # Real VRAM usage when the GPU service answered recently -- more
            # informative than a flat "working/idle" guess, and lets the live
            # poll below show something actually changing, same as CPU/RAM.
            # The sidebar is narrow, so the working/idle/paused state gets its own
            # small line under the bar instead of sharing the figure's row.
            vram_used, vram_total = gpu_resources["vram_used_gb"], gpu_resources["vram_total_gb"]
            gpu_fill = f"{(vram_used / vram_total * 100) if vram_total else 0:.0f}%"
            gpu_status = f"{vram_used:.1f} / {vram_total:.1f} GB"
            gpu_state_line = f"<div class='meter-state' data-gpu-state>{escape(gpu_state)}</div>"
        else:
            gpu_status = gpu_state
            gpu_fill = "60%" if gpu_running else "0%"
        gpu_note = f"{escape(gpu['name'])} · {escape(gpu['backend'])}"
    else:
        gpu_status, gpu_fill, gpu_colour = t("none_found", lang), "0%", "var(--mut)"
        gpu_note = t("no_gpu_block_detected", lang)
    cpu_name = escape(cpu["name"]) if cpu else "CPU"
    note = t("cpu_active_for", lang).format(cpu=cpu_name, gpu=gpu_note)
    return (
        f"<div class='engine-card' data-engine-card><h4>{escape(t('local_engine_heading', lang))}</h4>"
        f"<div class='meter-row'><div class='meter-label'><span class='with-icon'>{icon_img('cpu', 14)}CPU</span>"
        f"<span data-cpu-status>{escape(cpu_status)}</span></div>"
        f"<div class='meter-track'><div class='meter-fill' data-cpu-fill "
        f"style='width:{cpu_fill};background:var(--ok);'></div></div></div>"
        f"<div class='meter-row'><div class='meter-label'><span class='with-icon'>{icon_img('ram', 14)}RAM</span>"
        f"<span data-ram-status>{escape(ram_status)}</span></div>"
        f"<div class='meter-track'><div class='meter-fill' data-ram-fill "
        f"style='width:{ram_fill};background:var(--ac);'></div></div></div>"
        f"<div class='meter-row'><div class='meter-label'><span class='with-icon'>{icon_img('gpu', 14)}GPU VRAM</span>"
        f"<span data-gpu-status>{escape(gpu_status)}</span></div>"
        f"<div class='meter-track'><div class='meter-fill' data-gpu-fill "
        f"style='width:{gpu_fill};background:{gpu_colour};'></div></div>{gpu_state_line}</div>"
        f"<div class='engine-note'>{note}</div>"
        "</div>"
    )


def render_stat_cards(counts, active_jobs, lang="sv"):
    cards = []
    kind_keys = {"movie": "kind_movie", "episode": "kind_episode", "media": "kind_media"}
    for kind, _label in LIBRARY_KIND_LABELS:
        label = t(kind_keys[kind], lang)
        total, with_subtitle = counts.get(kind, (0, 0))
        sub_text = t("stat_has_subtitle", lang).format(count=with_subtitle, total=total)
        cards.append(
            f"<div class='stat-card'><div class='label'>{escape(label.upper())}</div>"
            f"<div class='value'>{total}</div>"
            f"<div class='sub'>{escape(sub_text)}</div></div>"
        )
    queue_sub = (t("jobs_queued_now", lang).format(count=active_jobs) if active_jobs
                else t("no_jobs_queued_now", lang))
    cards.append(
        f"<div class='stat-card accent'><div class='label'>{escape(t('queue_label', lang))}</div>"
        f"<div class='value'>{active_jobs}</div>"
        f"<div class='sub'>{escape(queue_sub)}</div></div>"
    )
    return "<div class='stat-grid'>" + "".join(cards) + "</div>"


def pipeline_steps(lang="sv"):
    return (
        ("transcribing", t("pipeline_transcribing", lang), t("pipeline_transcribing_desc", lang)),
        ("translating", t("pipeline_translating", lang), t("pipeline_translating_desc", lang)),
        ("verifying", t("pipeline_verifying", lang), t("pipeline_verifying_desc", lang)),
        ("writing", t("pipeline_writing", lang), t("pipeline_writing_desc", lang)),
    )


STEP_ENGINE_ICONS = {"transcribing": ("whisper", "qwen"), "translating": ("gemma",)}


def render_pipeline_steps(status, lang="sv"):
    """Render the real pipeline stages from the job's own status, not a guess."""
    steps = pipeline_steps(lang)
    order = [key for key, _label, _desc in steps]
    if status not in order:
        return None
    current_index = order.index(status)
    rows = []
    for index, (_key, label, desc) in enumerate(steps):
        if index < current_index:
            state, dot, step_status = "done", "✓", t("step_done", lang)
        elif index == current_index:
            state, dot, step_status = "active", "●", t("step_active", lang)
        else:
            state, dot, step_status = "waiting", str(index + 1), t("step_waiting", lang)
        rows.append(
            f"<div class='pipeline-step'><div class='step-dot {state}'>{dot}</div>"
            f"<div class='step-name'>{escape(label)}<br><small class='muted with-icon'>{''.join(icon_img(n, 14) for n in STEP_ENGINE_ICONS.get(_key, ()))}{escape(desc)}</small></div>"
            f"<div class='step-status'>{escape(step_status)}</div></div>"
        )
    return "".join(rows)


def render_overview_job_card(jobs, library_by_path, lang="sv"):
    active = next((job for job in jobs.values() if job.get("status") in (
        "queued", "transcribing", "translating", "verifying", "writing", "cancel_requested")), None)
    if not active:
        return (
            f"<div class='card'><h2>{escape(t('ongoing_job_heading', lang))}</h2>"
            f"<p class='muted'>{escape(t('no_active_jobs_worker_waiting', lang))}</p></div>"
        )
    name = library_by_path.get(active["media_path"], {}).get("name", active.get("media_name", ""))
    status_label = queue_status_label(active["status"], lang)
    progress = int(active.get("progress") or 0)
    steps_html = render_pipeline_steps(active["status"], lang)
    steps_block = (
        f"<div style='display:flex;flex-direction:column;gap:10px;margin-top:14px;'>{steps_html}</div>"
        if steps_html else ""
    )
    return (
        f"<div class='card'><h2>{escape(t('ongoing_job_heading', lang))}</h2>"
        f"<p class='muted'>{escape(name)} · {escape(status_label)}</p>"
        f"<div class='progress-track'><div class='progress-fill' style='width:{progress}%;'></div></div>"
        f"<p class='muted' style='margin:6px 0 0;font-size:12px;'>{progress} % · {escape(active.get('message') or '')}</p>"
        f"{steps_block}"
        "</div>"
    )



QUEUE_STATUS_KEYS = {
    "queued": "queue_queued", "transcribing": "queue_transcribing", "translating": "queue_translating",
    "verifying": "queue_verifying", "writing": "queue_writing",
    "cancel_requested": "queue_cancel_requested", "delete_queued": "queue_delete_queued",
    "deleting": "queue_deleting", "restart_queued": "queue_restart_queued", "restarting": "queue_restarting",
    "completed": "queue_completed", "failed": "queue_failed", "quality_failed": "queue_quality_failed",
    "deleted": "queue_deleted", "cancelled": "queue_cancelled",
}


def queue_status_label(status, lang="sv"):
    key = QUEUE_STATUS_KEYS.get(status)
    return t(key, lang) if key else (status or "")


def retry_failed_job(database, media_path, job_id, *, replace_existing=False):
    """Retry only the currently failed job, using normal library/queue guards."""
    with sqlite3.connect(database) as db:
        saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
        ignore = db.execute("SELECT value FROM metadata WHERE key='ignore_existing_subtitle'").fetchone()
    library = json.loads(saved[0]) if saved else {'items': []}
    item = runnable_library_item(library, media_path)
    job = latest_jobs(database).get(media_path)
    if item is None:
        raise ValueError('Filen finns inte i den aktuella biblioteksskanningen.')
    if (not job or job.get('id') != job_id
            or job.get('status') not in ('failed', 'quality_failed')):
        raise ValueError('Körningen är inte längre misslyckad eller underkänd. Ladda om sidan.')
    if item.get('ai_sv_exists'):
        if not replace_existing or not item.get('ai_sv_path'):
            raise ValueError('En AI-undertext finns redan. Ladda om sidan och bekräfta omkörningen.')
        return enqueue_restart(database, item['path'], item['name'], item['ai_sv_path'])
    if item.get('embedded_sv_exists') and not (ignore and ignore[0] == '1'):
        raise ValueError('Filen har redan en inbäddad svensk undertext.')
    return enqueue_job(database, item['path'], item['name'])


def render_queue_job_row(job, library_by_path, token, *, cancellable, lang="sv"):
    icons = {"movie": "🎬", "episode": "📺", "media": "🎞️"}
    kind = library_by_path.get(job.get("media_path"), {}).get("kind", "media")
    icon = icons.get(kind, "🎞️")
    status = job.get("status")
    label = queue_status_label(status, lang)
    pill_class = {"quality_failed": "dg", "failed": "dg", "cancelled": "neutral",
                  "completed": "ok", "deleted": "neutral"}.get(status, "neutral")
    message = escape(job.get("message") or "")
    details = " · ".join(value for value in (job.get("hardware"), job.get("model")) if value)
    progress_html = ""
    cancel_html = ""
    retry_html = ""
    item = library_by_path.get(job.get('media_path'))
    if not cancellable and status in ('failed', 'quality_failed') and token and item:
        replace = bool(item.get('ai_sv_exists'))
        confirmation = (f" onsubmit=\"return confirm('{escape(t('confirm_restart', lang), quote=True)}')\""
                        if replace else '')
        retry_html = (
            f"<form action='/jobs/retry' method='post'{confirmation}>"
            f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
            f"<input type='hidden' name='media_path' value='{escape(job.get('media_path') or '', quote=True)}'>"
            f"<input type='hidden' name='job_id' value='{int(job.get('id') or 0)}'>"
            f"<input type='hidden' name='replace_existing' value='{int(replace)}'>"
            f"<button type='submit' class='small-button'>{escape(t('action_retry_failed', lang))}</button></form>")
    if cancellable:
        progress = int(job.get("progress") or 0)
        progress_html = (f"<div class='progress-track' style='margin-top:6px;'>"
                         f"<div class='progress-fill' style='width:{progress}%;'></div></div>")
        steps_html = render_pipeline_steps(status)
        if steps_html:
            progress_html += (f"<div style='display:flex;flex-direction:column;gap:8px;margin-top:10px;'>"
                              f"{steps_html}</div>")
        if status in ("queued", "transcribing", "translating", "verifying", "writing") and token:
            cancel_html = (
                "<form action='/jobs/cancel' method='post' "
                f"onsubmit=\"return confirm('{escape(t('confirm_cancel_job', lang), quote=True)}')\">"
                f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
                f"<input type='hidden' name='media_path' value='{escape(job.get('media_path') or '', quote=True)}'>"
                "<input type='hidden' name='return_kind' value='queue'>"
                f"<input type='hidden' name='job_id' value='{int(job.get('id') or 0)}'>"
                f"<button type='submit' class='small-button danger'>{escape(t('action_cancel_short', lang))}</button></form>"
            )
    return (
        "<div class='queue-row' style='align-items:flex-start;flex-wrap:wrap;'>"
        f"<div class='kind-icon'>{icon}</div>"
        f"<div class='title'>{escape(job.get('media_name') or job.get('media_path') or '')}"
        f"<br><small class='muted'>{message}</small>{progress_html}"
        + (f"<br><small class='muted'>{escape(details)}</small>" if details else "")
        + "</div>"
        f"<span class='pill {pill_class}'>{escape(label)}</span>{cancel_html}{retry_html}</div>"
    )


PROCESSING_STATUSES = ("transcribing", "translating", "verifying", "writing", "cancel_requested")


def paginate(items, page, per_page=7):
    page = max(1, page)
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, total_pages


def render_pagination(param, page, total_pages, base='/queue', lang="sv"):
    if total_pages <= 1:
        return ""
    previous_label = escape(t('pagination_previous', lang))
    next_label = escape(t('pagination_next', lang))
    prev_html = (f"<a href='{base}?{param}={page - 1}'>{previous_label}</a>" if page > 1
                else f"<span class='muted'>{previous_label}</span>")
    next_html = (f"<a href='{base}?{param}={page + 1}'>{next_label}</a>" if page < total_pages
                else f"<span class='muted'>{next_label}</span>")
    page_of = escape(t('pagination_page_of', lang).format(page=page, total=total_pages))
    return f"<div class='pagination'>{prev_html}<span class='muted'>{page_of}</span>{next_html}</div>"


def render_queue_content(library_items, jobs, token, queue_page=1, history_page=1, lang="sv"):
    library_by_path = {item["path"]: item for item in library_items}
    all_jobs = list(jobs.values())
    processing = sorted((job for job in all_jobs if job.get("status") in PROCESSING_STATUSES),
                        key=lambda job: job.get("updated_at") or "", reverse=True)
    queued = sorted((job for job in all_jobs if job.get("status") == "queued"),
                    key=lambda job: job.get("updated_at") or "")
    finished = sorted((job for job in all_jobs if job.get("status") not in ACTIVE),
                      key=lambda job: job.get("updated_at") or "", reverse=True)

    processing_body = "".join(render_queue_job_row(job, library_by_path, token, cancellable=True, lang=lang)
                              for job in processing) or f"<p class='muted'>{escape(t('no_active_jobs', lang))}</p>"

    queued_items, queued_page, queued_pages = paginate(queued, queue_page)
    queued_body = "".join(render_queue_job_row(job, library_by_path, token, cancellable=True, lang=lang)
                          for job in queued_items) or f"<p class='muted'>{escape(t('no_queued_jobs', lang))}</p>"

    finished_items, history_page, history_pages = paginate(finished, history_page)
    finished_body = "".join(render_queue_job_row(job, library_by_path, token, cancellable=False, lang=lang)
                            for job in finished_items) or f"<p class='muted'>{escape(t('no_history_yet', lang))}</p>"

    return (
        f"<div data-jobs-signature='{jobs_signature(jobs)}'>"
        f"<div class='card'><h2>{escape(t('active_jobs_heading', lang))} · {len(processing)}</h2>{processing_body}</div>"
        f"<div class='card'><h2>{escape(t('queue_heading', lang))} · {len(queued)}</h2>{queued_body}"
        f"{render_pagination('queue_page', queued_page, queued_pages, lang=lang)}</div>"
        f"<div class='card'><h2>{escape(t('recent_events_heading', lang))}</h2>{finished_body}"
        f"{render_pagination('page', history_page, history_pages, lang=lang)}</div></div>"
    )


def render_overview_content(library_items, jobs, hardware, preference, token, lang="sv",
                             cloud_providers=(), cloud_profile=None):
    counts = library_counts(library_items)
    queued = active_job_count(jobs)
    library_by_path = {item["path"]: item for item in library_items}
    return (
        render_stat_cards(counts, queued, lang)
        + render_subtitle_coverage(library_subtitle_breakdown(library_items), lang)
        + render_overview_job_card(jobs, library_by_path, lang)
        + render_hardware(hardware, preference, token, return_to="/", lang=lang,
                          cloud_providers=cloud_providers, cloud_profile=cloud_profile)
    )


def render_language_content(lang, token, saved=False):
    saved_notice = f"<p class='success' role='status'>{escape(t('language_saved', lang))}</p>" if saved else ""
    options = (("sv", "Svenska"), ("en", "English"))
    chips = "".join(
        f"<label class='accent-chip{' selected' if key == lang else ''}'>"
        "<input type='radio' name='ui_language' value='" + escape(key, quote=True) + "'"
        + (" checked" if key == lang else "") + " style='display:none;' "
        "onchange='this.closest(\"form\").requestSubmit()'>"
        f"{escape(label)}</label>"
        for key, label in options
    )
    return (
        "<div class='card'><h2>" + escape(t("ui_language_label", lang)) + "</h2>"
        + saved_notice
        + f"<p class='muted'>{escape(t('ui_language_note', lang))}</p>"
        "<form action='/settings/language' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<div class='accent-row'>{chips}</div>"
        "</form></div>"
    )


def render_settings_content(theme, accent, hardware, preference, token, saved,
                            instance_id="", cloud_profile=None, cloud_saved=False,
                            ignore_existing_subtitle=False, automation_saved=False,
                            auto_scan_enabled=False, auto_start_jobs=False,
                            include_english_subtitle=False, lang="sv",
                            language_saved=False, cloud_providers=()):
    automation_notice = ("<p class='success' role='status'>✓ Automatiseringen är sparad.</p>"
                         if automation_saved else "")
    ignore_checked = " checked" if ignore_existing_subtitle else ""
    scan_checked = " checked" if auto_scan_enabled else ""
    start_checked = " checked" if auto_start_jobs else ""
    english_checked = " checked" if include_english_subtitle else ""
    return (
        render_language_content(lang, token, saved=language_saved)
        + render_hardware(hardware, preference, token, saved=saved, return_to="/settings", lang=lang,
                          cloud_providers=cloud_providers, cloud_profile=cloud_profile)
        + "<div class='card'><h2>Undertexter och jobb</h2>"
        + automation_notice
        + "<form action='/settings/automation' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        "<label class='checkbox-row'>"
        f"<input type='checkbox' name='ignore_existing_subtitle' value='1'{ignore_checked} "
        "onchange='this.closest(\"form\").requestSubmit()'>"
        "<span>Skapa AI-undertext även när en inbäddad eller annan svensk undertext redan finns "
        "(t.ex. PGS)</span></label>"
        "<p class='muted'>Normalt hoppar SubSynq över filer som redan har svensk textning. "
        "Aktivera det här om du vill att SubSynq ändå ska skapa en egen AI-undertext.</p>"
        "<label class='checkbox-row'>"
        f"<input type='checkbox' name='auto_scan_enabled' value='1'{scan_checked} "
        "onchange='this.closest(\"form\").requestSubmit()'>"
        "<span>Sök efter ny media automatiskt (var 5:e minut)</span></label>"
        "<p class='muted'>Normalt måste du klicka \"Skanna bibliotek\" själv. Aktivera det här "
        "för att låta SubSynq bevaka mapparna åt dig i bakgrunden.</p>"
        "<label class='checkbox-row'>"
        f"<input type='checkbox' name='auto_start_jobs' value='1'{start_checked} "
        "onchange='this.closest(\"form\").requestSubmit()'>"
        "<span>Starta undertextjobb automatiskt för nyupptäckta filer</span></label>"
        "<p class='muted'>Kräver automatisk sökning ovan. Gäller bara filer som aldrig haft ett "
        "jobb tidigare — en misslyckad eller underkänd fil körs aldrig om av sig själv.</p>"
        "<label class='checkbox-row'>"
        f"<input type='checkbox' name='include_english_subtitle' value='1'{english_checked} "
        "onchange='this.closest(\"form\").requestSubmit()'>"
        "<span>Skapa engelsk AI-undertext samtidigt (.ai.en.srt)</span></label>"
        "<p class='muted'>Svenska skapas alltid först och är det enda som kan underkänna jobbet. "
        "Engelska är ett tillägg ovanpå en redan godkänd svensk undertext — om den engelska "
        "kvalitetskontrollen underkänner texten sparas ingen engelsk fil, men den svenska påverkas "
        "inte.</p>"
        "</form></div>"
        + "<div class='card'><h2>Installation</h2>"
        f"<p class='muted'>Installation: <code>{escape(instance_id)}</code><br>"
        "Identiteten ska vara densamma efter att containern återskapats.</p></div>"
    )


def webhook_code(path):
    """A webhook address. The page script completes it with the address this page was opened from."""
    return f"<code data-webhook-path='{escape(path, quote=True)}'>{escape(path)}</code>"


def render_media_settings_content(token, webhook_token, radarr_root_prefix, sonarr_root_prefix,
                                   jellyfin_movies_root_prefix="", jellyfin_tv_root_prefix="",
                                   emby_movies_root_prefix="", emby_tv_root_prefix="",
                                   saved=False, token_regenerated=False):
    saved_notice = ("<p class='success' role='status'>✓ Rotmapparna är sparade.</p>"
                    if saved else "")
    regenerated_notice = ("<p class='success' role='status'>✓ Ny webhook-token skapad – "
                          "uppdatera adresserna i Radarr/Sonarr/Jellyfin/Emby.</p>"
                          if token_regenerated else "")
    radarr_path = f"/webhooks/radarr?token={quote(webhook_token, safe='')}"
    sonarr_path = f"/webhooks/sonarr?token={quote(webhook_token, safe='')}"
    jellyfin_path = f"/webhooks/jellyfin?token={quote(webhook_token, safe='')}"
    emby_path = f"/webhooks/emby?token={quote(webhook_token, safe='')}"
    return (
        "<div class='card'><h2>Webhook-token</h2>"
        + regenerated_notice
        + "<p class='muted'>Samma token används av alla fyra kopplingar nedan. Skapar du en ny "
        "måste adresserna uppdateras i alla verktyg som redan använder den gamla.</p>"
        "<form action='/settings/webhook-token' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        "<button type='submit' class='small-button danger' "
        "onclick=\"return confirm('Skapa en ny webhook-token? De gamla adresserna slutar då "
        "fungera tills de uppdateras överallt.')\">Skapa ny token</button></form>"
        "</div>"
        "<div class='card'><h2>Radarr &amp; Sonarr</h2>"
        "<p class='muted'>Lägg till SubSynq som en \"Webhook\"-anslutning i Radarr respektive "
        "Sonarr (Settings → Connect), en per verktyg, för händelsen \"On Import\"/\"On Upgrade\". "
        "Använd hela adressen nedan. Den börjar med samma adress som du öppnade den här sidan med "
        "(Unraids IP-adress och port, till exempel <code>http://192.168.1.10:8765</code>). Använd inte "
        "containernamnet <code>subsynq</code>: Radarr, Sonarr, Jellyfin och Emby ligger oftast på ett annat "
        "Docker-nätverk och hittar inte det namnet.</p>"
        "<p class='muted'>Radarr: " + webhook_code(radarr_path) + "<br>"
        "Sonarr: " + webhook_code(sonarr_path) + "</p>"
        "<form action='/settings/media' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        + saved_notice +
        "<label>Radarr rotmapp (om Radarr ser filmerna under en annan sökväg än SubSynqs "
        "eget /movies)<br>"
        f"<input type='text' name='radarr_root_prefix' value='{escape(radarr_root_prefix, quote=True)}' "
        "placeholder='t.ex. /data/movies' style='width:100%;max-width:420px;'></label>"
        "<p class='muted'>Lämna tom om Radarr och SubSynq monterar samma mapp under samma "
        "sökväg (rekommenderat, och det vanliga på Unraid).</p>"
        "<label>Sonarr rotmapp (motsvarande för serier, jämfört med SubSynqs eget /tv)<br>"
        f"<input type='text' name='sonarr_root_prefix' value='{escape(sonarr_root_prefix, quote=True)}' "
        "placeholder='t.ex. /data/tv' style='width:100%;max-width:420px;'></label>"
        "<p class='muted'>Bara händelsen \"Download\" köar ett jobb. Andra händelser, inklusive "
        "\"Test\", besvaras men ignoreras.</p>"
        "<p><button type='submit'>Spara rotmappar</button></p>"
        "</form>"
        "</div>"
        "<div class='card'><h2>Jellyfin</h2>"
        "<p class='muted'>Jellyfin saknar inbyggda webhooks och behöver ett tredjeparts "
        "webhook-plugin — dess mall måste skicka med filsökvägen, t.ex. som fältet <code>Path</code>. "
        "Exakt mallsyntax kan skilja mellan plugin-versioner; kontrollera din egen installations "
        "mallredigerare.</p>"
        "<p class='muted'>Jellyfin: " + webhook_code(jellyfin_path) + "</p>"
        "<form action='/settings/media' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        "<label>Jellyfin filmer-rotmapp (Jellyfins egen sökväg till filmbiblioteket, jämfört med "
        "SubSynqs eget /movies)<br>"
        f"<input type='text' name='jellyfin_movies_root_prefix' value='{escape(jellyfin_movies_root_prefix, quote=True)}' "
        "placeholder='t.ex. /data/movies' style='width:100%;max-width:420px;'></label>"
        "<label>Jellyfin serier-rotmapp (Jellyfins egen sökväg till seriebiblioteket, jämfört med "
        "SubSynqs eget /tv)<br>"
        f"<input type='text' name='jellyfin_tv_root_prefix' value='{escape(jellyfin_tv_root_prefix, quote=True)}' "
        "placeholder='t.ex. /data/tv' style='width:100%;max-width:420px;'></label>"
        "<p class='muted'>Fyll i båda om Jellyfin (vanligt på Linux) monterar filmer och serier som "
        "två helt separata rotmappar istället för en delad överordnad mapp.</p>"
        "<p><button type='submit'>Spara rotmappar</button></p>"
        "</form>"
        "</div>"
        "<div class='card'><h2>Emby</h2>"
        "<p class='muted'>Settings → Notifications → Webhooks (kräver Emby Premiere), händelsen "
        "\"Library – New content\".</p>"
        "<p class='muted'>Emby: " + webhook_code(emby_path) + "</p>"
        "<form action='/settings/media' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        "<label>Emby filmer-rotmapp (Embys egen sökväg till filmbiblioteket, jämfört med "
        "SubSynqs eget /movies)<br>"
        f"<input type='text' name='emby_movies_root_prefix' value='{escape(emby_movies_root_prefix, quote=True)}' "
        "placeholder='t.ex. /data/movies' style='width:100%;max-width:420px;'></label>"
        "<label>Emby serier-rotmapp (Embys egen sökväg till seriebiblioteket, jämfört med "
        "SubSynqs eget /tv)<br>"
        f"<input type='text' name='emby_tv_root_prefix' value='{escape(emby_tv_root_prefix, quote=True)}' "
        "placeholder='t.ex. /data/tv' style='width:100%;max-width:420px;'></label>"
        "<p class='muted'>Fyll i båda om Emby (vanligt på Linux) monterar filmer och serier som "
        "två helt separata rotmappar istället för en delad överordnad mapp.</p>"
        "<p><button type='submit'>Spara rotmappar</button></p>"
        "</form>"
        "</div>"
    )


def render_theme_content(theme, accent, token, saved):
    flat_cards = []
    immersive_cards = []
    for key, info in THEMES.items():
        bg, sur, ac = info["swatch"]
        selected = " selected" if key == theme else ""
        immersive = info.get("immersive", False)
        if immersive:
            swatch_html = f"<div class='theme-swatch grad' style='background-image:{info['grad']}'></div>"
            badge_html = "<span class='effects-badge'>⚡ Effekter</span>"
            card_class = f"theme-card immersive{selected}"
        else:
            swatch_html = f"<div class='theme-swatch'><span style='background:{bg}'></span><span style='background:{sur}'></span><span style='background:{ac}'></span></div>"
            badge_html = ""
            card_class = f"theme-card{selected}"
        card = (
            f"<label class='{card_class}'>"
            f"{swatch_html}"
            f"<div class='name'>{escape(info['label'])}{badge_html}</div><div class='desc'>{escape(info['desc'])}</div>"
            "<input type='radio' name='theme' value='" + escape(key, quote=True) + "'"
            + (" checked" if key == theme else "") + " style='display:none;' "
            "onchange='this.closest(\"form\").requestSubmit()'></label>"
        )
        (immersive_cards if immersive else flat_cards).append(card)
    immersive_section = (
        "<p class='immersive-heading'>Levande teman · gradient, neonglow &amp; scanlines</p>"
        f"<div class='theme-grid immersive-grid'>{''.join(immersive_cards)}</div>"
    ) if immersive_cards else ""
    accent_chips = []
    for key, info in ACCENTS.items():
        selected = " selected" if key == accent else ""
        accent_chips.append(
            f"<label class='accent-chip{selected}'>"
            "<input type='radio' name='accent' value='" + escape(key, quote=True) + "'"
            + (" checked" if key == accent else "") + " style='display:none;' "
            "onchange='this.closest(\"form\").requestSubmit()'>"
            f"{escape(info['label'])}</label>"
        )
    saved_notice = "<p class='success' role='status'>✓ Utseendet är sparat.</p>" if saved else ""
    return (
        "<div class='card'><h2>Tema</h2><p class='muted'>Layouten ändras aldrig — bara färgerna.</p>"
        f"{saved_notice}"
        "<form action='/settings' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<input type='hidden' name='accent' value='{escape(accent, quote=True)}'>"
        f"<div class='theme-grid'>{''.join(flat_cards)}</div>"
        f"{immersive_section}"
        "</form>"
        "<p class='muted' style='margin-top:14px;'>Accentfärg (åsidosätter temats egen):</p>"
        "<form action='/settings' method='post'>"
        f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
        f"<input type='hidden' name='theme' value='{escape(theme, quote=True)}'>"
        f"<div class='accent-row'>{''.join(accent_chips)}</div>"
        "</form>"
        "<div class='settings-preview'>"
        "<span class='pill ok'>Klar</span><span class='pill wn'>Granska</span><span class='pill neutral'>Väntar</span>"
        "<div class='progress-track' style='flex:1;min-width:120px;'><div class='progress-fill' style='width:64%;'></div></div>"
        "<button type='button'>Exempelknapp</button>"
        "</div></div>"
    )


def render_log_content(events, page, total_pages, lang="sv"):
    if events:
        rows = []
        for event in events:
            status = event.get('status')
            label = queue_status_label(status, lang)
            pill_class = {"quality_failed": "dg", "failed": "dg", "cancelled": "neutral",
                          "completed": "ok", "deleted": "neutral"}.get(status, "neutral")
            stamp = format_local_timestamp(event.get('updated_at'))
            name = escape(event.get('media_name') or event.get('media_path') or '')
            message = escape(event.get('message') or '')
            rows.append(
                "<div class='queue-row' style='align-items:flex-start;flex-wrap:wrap;'>"
                f"<div class='title'>{name}<br><small class='muted'>{stamp} · {message}</small></div>"
                f"<span class='pill {pill_class}'>{escape(label)}</span></div>"
            )
        list_html = "".join(rows)
    else:
        list_html = f"<p class='muted'>{escape(t('no_jobs_run_yet', lang))}</p>"
    return (
        f"<div class='card'><h2>{escape(t('log_heading', lang))}</h2>"
        f"<p class='muted'>{escape(t('log_intro', lang))}</p>"
        + list_html + render_pagination('log_page', page, total_pages, base='/settings/log', lang=lang)
        + "</div>"
        f"<div class='card' data-log-console><h2>{escape(t('live_heading', lang))}</h2>"
        f"<p class='muted'>{escape(t('live_intro', lang))}</p>"
        f"<pre class='log-terminal' id='log-terminal'>{escape(t('waiting_for_events', lang))}</pre></div>"
    )


def render_page(page_title, active_nav, content, theme, accent, jobs, hardware, token, lang="sv"):
    body = PAGE.replace("{{theme}}", escape(theme, quote=True))
    body = body.replace("{{accent_css}}", accent_css_block(accent))
    body = body.replace("{{version}}", VERSION)
    body = body.replace("{{page_title}}", escape(page_title))
    body = body.replace("{{running_label}}", escape(t("running", lang)))
    body = body.replace("{{sidebar}}", render_sidebar(active_nav, active_job_count(jobs), hardware, lang))
    # While the AI models are still being downloaded, say so at the top of every page.
    banners = (scan_state.render_scan_banner(SCAN_STATUS.snapshot(), lang)
               + models_card.render_models_banner(models_card.load_status(CONFIG), lang))
    return body.replace("{{content}}", banners + content)


def status():
    with sqlite3.connect(f"{DATABASE.as_uri()}?mode=rw", uri=True, timeout=2) as db:
        # A real write verifies that /config is still usable, without changing metadata.
        db.execute("UPDATE metadata SET value=value WHERE key='instance_id'")
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
    if not metadata.get("instance_id"):
        raise RuntimeError("Missing application identity")
    hardware = load_hardware(CONFIG)
    preference = metadata.get("hardware_preference", "auto")
    if preference not in allowed_preferences(hardware, enabled_cloud_providers()):
        preference = "auto"
    theme = metadata.get("theme", "mork")
    if theme not in THEMES:
        theme = "mork"
    accent = metadata.get("accent", "")
    if accent not in ACCENTS:
        accent = ""
    ignore_existing_subtitle = metadata.get("ignore_existing_subtitle", "0") == "1"
    auto_scan_enabled = metadata.get("auto_scan_enabled", "0") == "1"
    auto_start_jobs = metadata.get("auto_start_jobs", "0") == "1"
    include_english_subtitle = metadata.get("include_english_subtitle", "0") == "1"
    ui_language = metadata.get("ui_language", "sv")
    if ui_language not in LANGUAGES:
        ui_language = "sv"
    return {
        "status": "ok", "app": "SubSynq", "version": VERSION,
        "stage": "library", "instance_id": metadata["instance_id"],
        "created_at": metadata["created_at"],
        "hardware_preference": preference,
        "cloud_cost_profile": load_profile(metadata.get("cloud_cost_profile")),
        "hardware": hardware,
        "ignore_existing_subtitle": ignore_existing_subtitle,
        "auto_scan_enabled": auto_scan_enabled,
        "auto_start_jobs": auto_start_jobs,
        "include_english_subtitle": include_english_subtitle,
        "ui_language": ui_language,
        "theme": theme,
        "accent": accent,
        "paths": {str(path): {"exists": path.is_dir()} for path in
                  [CONFIG, Path("/movies"), Path("/tv"), Path("/extra"), Path("/models")]},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        path = urlsplit(self.path).path
        if path in ('/webhooks/radarr', '/webhooks/sonarr', '/webhooks/jellyfin', '/webhooks/emby'):
            self.handle_webhook(path)
            return
        if self.path not in ("/scan", '/hardware', '/settings', '/settings/automation',
                             '/settings/media', '/settings/webhook-token', '/settings/language',
                             '/cloud-cost', '/cloud-key', '/cloud-key/remove', '/cloud-refresh',
                             '/cloud-offer', '/jobs/start', '/jobs/retry', '/jobs/delete', '/jobs/restart', '/jobs/cancel',
                             '/cleanup/trash', '/cleanup/restore', '/cleanup/empty', '/cleanup/retention',
                             '/jobs/bulk', '/models/remove', '/models/update'):
            self.respond(404, "text/plain", "Sidan saknas")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            # A page of ticked paths can be far larger than a settings form.
            if not 0 < length <= (2_000_000 if self.path in ('/cleanup/trash', '/jobs/bulk') else 32768):
                raise ValueError()
            self.connection.settimeout(5)
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
        except (ValueError, OSError):
            self.respond(400, "text/plain", "Ogiltig begäran")
            return
        if not secrets.compare_digest(form.get("token", [""])[0], SCAN_TOKEN):
            self.respond(403, "text/plain", "Ladda om sidan och försök igen.")
            return
        if self.path in ('/cloud-key', '/cloud-key/remove', '/cloud-refresh', '/cloud-offer'):
            provider = form.get('provider', [''])[0]
            if provider not in PROVIDERS:
                self.respond(400, 'text/plain', 'Välj Vast.ai eller Runpod.')
                return
            try:
                gpu_query = normalize_gpu_query(form.get('gpu_query', [''])[0]) if provider == 'vast' else ''
                if self.path == '/cloud-key':
                    save_key(CONFIG, provider, form.get('api_key', [''])[0].strip())
                elif self.path == '/cloud-key/remove':
                    remove_key(CONFIG, provider)
                elif self.path == '/cloud-refresh':
                    catalog(CONFIG, provider, force=True, gpu_query=gpu_query)
                else:
                    offer_id = form.get('offer_id', [''])[0]
                    # Must match the exact list the <select> was just rendered
                    # from, not force a brand-new live fetch -- a forced
                    # refetch here raced the marketplace itself (offers can
                    # be taken by someone else within seconds) and made a
                    # valid selection look like it "no longer exists".
                    result = catalog(CONFIG, provider, gpu_query=gpu_query)
                    offer = next((item for item in result['offers'] if item['id'] == offer_id), None)
                    if offer is None:
                        self.respond(409, 'text/plain', 'Erbjudandet finns inte längre. Uppdatera listan och välj igen.')
                        return
                    with sqlite3.connect(DATABASE) as db:
                        row = db.execute("SELECT value FROM metadata WHERE key='cloud_cost_profile'").fetchone()
                        profile = load_profile(row[0] if row else None)
                        profile.update(provider=provider, gpu=offer['gpu'],
                                       compute_hourly=offer['price'], storage_hourly=None,
                                       transfer_per_gb=None, currency='USD')
                        db.execute("INSERT OR REPLACE INTO metadata VALUES ('cloud_cost_profile', ?)",
                                   (json.dumps(profile, ensure_ascii=False),))
            except (ValueError, OSError, sqlite3.Error):
                self.respond(400, 'text/plain', 'Molninställningen kunde inte sparas.')
                return
            event = {'/cloud-key': 'key', '/cloud-key/remove': 'removed',
                     '/cloud-refresh': 'refreshed', '/cloud-offer': 'offer'}[self.path]
            self.send_response(303)
            self.send_header('Location', f'/settings/cloud?provider={provider}&saved={event}'
                             + (f'&gpu={quote(gpu_query, safe="")}' if gpu_query else ''))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/hardware':
            hardware = load_hardware(CONFIG)
            preference = form.get('preference', [''])[0]
            if preference not in allowed_preferences(hardware, enabled_cloud_providers()):
                self.respond(400, 'text/plain', 'Enheten är inte tillgänglig.')
                return
            return_to = form.get('return', ['/'])[0]
            if return_to not in ('/', '/settings'):
                return_to = '/'
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('hardware_preference', ?)", (preference,))
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Valet kunde inte sparas.')
                return
            self.send_response(303)
            self.send_header('Location', f'{return_to}?saved=hardware')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/cloud-cost':
            try:
                profile = parse_profile(form)
            except (TypeError, ValueError):
                self.respond(400, 'text/plain', 'Ogiltiga kostnadsuppgifter.')
                return
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('cloud_cost_profile', ?)",
                               (json.dumps(profile, ensure_ascii=False),))
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Kostnadsplanen kunde inte sparas.')
                return
            self.send_response(303)
            self.send_header('Location', '/settings/cloud?saved=cloud')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/settings':
            theme = form.get('theme', ['mork'])[0]
            accent = form.get('accent', [''])[0]
            if theme not in THEMES or accent not in ACCENTS:
                self.respond(400, 'text/plain', 'Ogiltigt utseendeval.')
                return
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                                    [('theme', theme), ('accent', accent)])
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Valet kunde inte sparas.')
                return
            self.send_response(303)
            self.send_header('Location', '/settings/theme?saved=1')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/settings/automation':
            ignore_existing = '1' if form.get('ignore_existing_subtitle', [''])[0] == '1' else '0'
            auto_scan = '1' if form.get('auto_scan_enabled', [''])[0] == '1' else '0'
            auto_start = '1' if form.get('auto_start_jobs', [''])[0] == '1' else '0'
            include_english = '1' if form.get('include_english_subtitle', [''])[0] == '1' else '0'
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", [
                        ('ignore_existing_subtitle', ignore_existing),
                        ('auto_scan_enabled', auto_scan),
                        ('auto_start_jobs', auto_start),
                        ('include_english_subtitle', include_english),
                    ])
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Valet kunde inte sparas.')
                return
            self.send_response(303)
            self.send_header('Location', '/settings?saved=automation')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/settings/media':
            # Radarr/Sonarr, Jellyfin and Emby are now three separate forms on
            # the same page -- each submit only carries its own fields, so only
            # the keys actually present here should be touched (a missing key
            # must NOT be treated as "clear this to empty").
            fields = ('radarr_root_prefix', 'sonarr_root_prefix',
                      'jellyfin_movies_root_prefix', 'jellyfin_tv_root_prefix',
                      'emby_movies_root_prefix', 'emby_tv_root_prefix')
            updates = [(key, form[key][0].strip()) for key in fields if key in form]
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", updates)
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Rotmapparna kunde inte sparas.')
                return
            self.send_response(303)
            self.send_header('Location', '/settings/media?saved=media')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/settings/webhook-token':
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('webhook_token', ?)",
                               (secrets.token_urlsafe(24),))
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Token kunde inte skapas om.')
                return
            self.send_response(303)
            self.send_header('Location', '/settings/media?saved=token')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/settings/language':
            language = form.get('ui_language', ['sv'])[0]
            if language not in LANGUAGES:
                self.respond(400, 'text/plain', 'Okänt språk.')
                return
            try:
                with sqlite3.connect(DATABASE) as db:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('ui_language', ?)", (language,))
            except sqlite3.Error:
                self.respond(503, 'text/plain', 'Språkvalet kunde inte sparas.')
                return
            self.send_response(303)
            self.send_header('Location', '/settings?saved=language')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/models/update':
            # Only a request: the worker checks for newer versions and, for the models you ticked,
            # downloads the new version and removes the old one once no subtitle job is running.
            keys = [key for key in form.get('key', []) if 0 < len(key) <= 300][:50]
            check = form.get('check', [''])[0] == '1'
            if not keys and not check:
                self.respond(400, 'text/plain', 'Inget markerat.')
                return
            try:
                (CONFIG / 'models-update-request.json').write_text(
                    json.dumps({'keys': keys, 'check': check,
                                'requested_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
            except OSError as error:
                self.respond(503, 'text/plain', f'Begäran kunde inte sparas: {error}')
                return
            logging.info('Modelluppdatering begärd: %d modeller, kontroll=%s', len(keys), check)
            self.send_response(303)
            self.send_header('Location', '/settings/models?requested=' + ('update' if keys else 'check'))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/models/remove':
            # Only asks the worker to remove old models; the worker re-checks that each one is
            # no longer listed in models.json and was installed by SubSynq itself.
            keys = [key for key in form.get('key', []) if 0 < len(key) <= 300][:50]
            if not keys:
                self.respond(400, 'text/plain', 'Inga modeller är markerade.')
                return
            try:
                (CONFIG / 'models-remove-request.json').write_text(
                    json.dumps({'keys': keys, 'requested_at': datetime.now(timezone.utc).isoformat()}),
                    encoding='utf-8')
            except OSError as error:
                self.respond(503, 'text/plain', f'Begäran kunde inte sparas: {error}')
                return
            logging.info('Borttagning av %d gamla modeller begärd', len(keys))
            self.send_response(303)
            self.send_header('Location', '/settings/models?requested=1')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path in ('/cleanup/trash', '/cleanup/restore', '/cleanup/empty', '/cleanup/retention'):
            trash_dir = CONFIG / 'trash'
            try:
                if self.path == '/cleanup/trash':
                    scan = subtitle_cleanup.load_scan(CONFIG)
                    if form.get('mode', [''])[0] == 'filtered':
                        chosen = [item['path'] for item in subtitle_cleanup.apply_filters(
                            scan['items'], subtitle_cleanup.parse_filters(form))]
                    else:
                        chosen = form.get('path', [])
                    if not chosen:
                        raise ValueError('Inga undertexter är markerade.')
                    batch, moved, failed = subtitle_cleanup.trash_files(chosen, CLEANUP_ROOTS, trash_dir)
                    for failed_path, reason in failed:
                        logging.warning('Undertext flyttades inte till papperskorgen: %s (%s)', failed_path, reason)
                    logging.info('Städa undertexter: %d filer flyttade till papperskorgen (%s), %d misslyckades',
                                 moved, batch, len(failed))
                    subtitle_cleanup.save_scan(
                        CONFIG, [item for item in scan['items'] if Path(item['path']).exists()],
                        now=scan['scanned_at'])
                    location = f'/cleanup?moved={moved}&failed={len(failed)}'
                elif self.path == '/cleanup/restore':
                    restored, skipped = subtitle_cleanup.restore_batch(
                        form.get('batch', [''])[0], CLEANUP_ROOTS, trash_dir)
                    logging.info('Städa undertexter: %d filer återställda, %d hoppades över', restored, len(skipped))
                    # Put the restored files straight back into the list instead of making
                    # the user rescan by hand.
                    try:
                        restored_jobs = latest_jobs(DATABASE)
                    except sqlite3.Error:
                        restored_jobs = {}
                    subtitle_cleanup.save_scan(CONFIG, subtitle_cleanup.mark_stale(
                        subtitle_cleanup.scan_srt(CLEANUP_ROOTS), restored_jobs))
                    location = f'/cleanup?restored={restored}&skipped={len(skipped)}'
                elif self.path == '/cleanup/retention':
                    days = subtitle_cleanup.save_retention(CONFIG, int(form.get('days', ['0'])[0] or 0))
                    removed = subtitle_cleanup.purge_old(trash_dir, days)
                    logging.info('Städa undertexter: papperskorgen töms efter %d dagar (%d filer togs bort nu)',
                                 days, removed)
                    location = f'/cleanup?retention={days}'
                else:
                    emptied = subtitle_cleanup.empty_trash(trash_dir)
                    logging.info('Städa undertexter: papperskorgen tömdes (%d filer)', emptied)
                    location = f'/cleanup?emptied={emptied}'
            except (ValueError, KeyError, OSError) as error:
                logging.warning('Städa undertexter misslyckades: %s', error)
                self.respond(400, 'text/plain', f'Kunde inte slutföra: {error}')
                return
            self.send_response(303)
            self.send_header('Location', location)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/jobs/retry':
            try:
                retry_failed_job(DATABASE, form.get('media_path', [''])[0],
                                 int(form.get('job_id', ['0'])[0]),
                                 replace_existing=form.get('replace_existing', ['0'])[0] == '1')
            except (ValueError, sqlite3.Error, OSError) as error:
                self.respond(400, 'text/plain', str(error))
                return
            self.send_response(303)
            self.send_header('Location', '/queue?job=queued')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/jobs/start':
            media_path = form.get('media_path', [''])[0]
            try:
                with sqlite3.connect(DATABASE) as db:
                    saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                    ignore_row = db.execute(
                        "SELECT value FROM metadata WHERE key='ignore_existing_subtitle'").fetchone()
                library = json.loads(saved[0]) if saved else {'items': []}
                ignore_existing = bool(ignore_row) and ignore_row[0] == '1'
                item = runnable_library_item(library, media_path)
                if item is None:
                    raise ValueError('Filen finns inte i den aktuella biblioteksskanningen.')
                if item.get('ai_sv_exists'):
                    raise ValueError('Avsnittet har redan en svensk AI-undertext.')
                if item.get('embedded_sv_exists') and not ignore_existing:
                    raise ValueError('Avsnittet har redan en inbäddad svensk undertext.')
                enqueue_job(DATABASE, item['path'], item['name'])
            except (ValueError, sqlite3.Error, OSError) as error:
                self.respond(400, 'text/plain', str(error))
                return
            self.send_response(303)
            self.send_header('Location', f"/library?kind={item['kind']}&job=queued")
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/jobs/bulk':
            action = form.get('action', [''])[0]
            return_kind = form.get('return_kind', ['episode'])[0]
            media_paths = list(dict.fromkeys(form.get('media_path', [])))
            if action not in ('start', 'delete', 'recreate') or not media_paths:
                self.respond(400, 'text/plain', 'Inget markerat.')
                return
            try:
                with sqlite3.connect(DATABASE) as db:
                    saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                    ignore_row = db.execute(
                        "SELECT value FROM metadata WHERE key='ignore_existing_subtitle'").fetchone()
                library = json.loads(saved[0]) if saved else {'items': []}
                ignore_existing = bool(ignore_row) and ignore_row[0] == '1'
                jobs_now = latest_jobs(DATABASE)
            except (sqlite3.Error, OSError, ValueError):
                self.respond(503, 'text/plain', 'Biblioteket kunde inte läsas.')
                return
            done = skipped = 0
            # Same rules as the single-file buttons; a file that no longer qualifies
            # (or already has an active job) is skipped, never forced.
            for media_path in media_paths:
                item = runnable_library_item(library, media_path)
                job = jobs_now.get(media_path)
                try:
                    if item is None or (job and job.get('status') in ACTIVE_JOB_STATUSES):
                        raise ValueError('not eligible')
                    if action == 'start':
                        if not needs_swedish_subtitle(item, ignore_existing):
                            raise ValueError('not eligible')
                        enqueue_job(DATABASE, item['path'], item['name'])
                    else:
                        if not item.get('ai_sv_exists') or not item.get('ai_sv_path'):
                            raise ValueError('not eligible')
                        if action == 'recreate':
                            enqueue_restart(DATABASE, item['path'], item['name'], item['ai_sv_path'])
                        else:
                            enqueue_delete(DATABASE, item['path'], item['name'], item['ai_sv_path'])
                    done += 1
                except (ValueError, sqlite3.Error):
                    skipped += 1
            logging.info('Flervalsåtgärd %s: %d köade, %d hoppades över', action, done, skipped)
            self.send_response(303)
            self.send_header('Location', library_return_path(return_kind, 'bulk')
                             + f'&bulk_action={action}&done={done}&skipped={skipped}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/jobs/delete':
            media_path = form.get('media_path', [''])[0]
            try:
                with sqlite3.connect(DATABASE) as db:
                    saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                library = json.loads(saved[0]) if saved else {'items': []}
                item = next((candidate for candidate in library['items']
                             if candidate.get('path') == media_path and candidate.get('ai_sv_exists')), None)
                job = latest_jobs(DATABASE).get(media_path)
                if item is None or not item.get('ai_sv_path'):
                    raise ValueError('Undertextfilen finns inte i den aktuella biblioteksskanningen.')
                active_statuses = {'queued', 'transcribing', 'translating', 'verifying', 'writing',
                                   'cancel_requested', 'delete_queued', 'deleting',
                                   'restart_queued', 'restarting'}
                if job and job.get('status') in active_statuses:
                    raise ValueError('Ett aktivt jobb måste avslutas innan AI-undertexten kan tas bort.')
                enqueue_delete(DATABASE, item['path'], item['name'], item['ai_sv_path'])
            except (ValueError, sqlite3.Error, OSError) as error:
                self.respond(400, 'text/plain', str(error))
                return
            self.send_response(303)
            self.send_header('Location', library_return_path(item['kind'], 'deleting'))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/jobs/restart':
            media_path = form.get('media_path', [''])[0]
            try:
                with sqlite3.connect(DATABASE) as db:
                    saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                library = json.loads(saved[0]) if saved else {'items': []}
                item = next((candidate for candidate in library['items']
                             if candidate.get('path') == media_path and candidate.get('ai_sv_exists')), None)
                job = latest_jobs(DATABASE).get(media_path)
                if item is None or not item.get('ai_sv_path'):
                    raise ValueError('Undertextfilen finns inte i den aktuella biblioteksskanningen.')
                active_statuses = {'queued', 'transcribing', 'translating', 'verifying', 'writing',
                                   'cancel_requested', 'delete_queued', 'deleting',
                                   'restart_queued', 'restarting'}
                if job and job.get('status') in active_statuses:
                    raise ValueError('Ett aktivt jobb måste avslutas innan filen kan köras om.')
                if not subtitle_is_stale(item, job) and form.get('recreate', ['0'])[0] != '1':
                    raise ValueError('Videofilen matchar fortfarande undertexten; ingen omkörning behövs.')
                enqueue_restart(DATABASE, item['path'], item['name'], item['ai_sv_path'])
            except (ValueError, sqlite3.Error, OSError) as error:
                self.respond(400, 'text/plain', str(error))
                return
            self.send_response(303)
            self.send_header('Location', library_return_path(item['kind'], 'restarting'))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/jobs/cancel':
            media_path = form.get('media_path', [''])[0]
            try:
                job_id = int(form.get('job_id', ['0'])[0])
                request_cancel(DATABASE, job_id, media_path)
            except (ValueError, sqlite3.Error) as error:
                self.respond(400, 'text/plain', str(error))
                return
            self.send_response(303)
            return_kind = form.get('return_kind', [''])[0]
            if return_kind == 'queue':
                destination = '/queue'
            else:
                with sqlite3.connect(DATABASE) as db:
                    saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                library = json.loads(saved[0]) if saved else {'items': []}
                item = runnable_library_item(library, media_path)
                destination = library_return_path(item['kind'] if item else 'episode', 'cancelled')
            self.send_header('Location', destination)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        choice = form.get("library", [scan_state.ALL])[0]
        try:
            scan_state.roots_for(choice, lambda root: Path(root).is_dir())
        except ValueError:
            self.respond(400, "text/plain", "Välj filmer, serier, extra eller allt.")
            return
        if SCAN_LOCK.locked():
            self.respond(409, "text/plain", "En skanning pågår redan.")
            return
        # The scan runs in the background; every page shows its live status until it is done.
        before = SCAN_STATUS.snapshot()["started"]
        threading.Thread(target=run_background_scan, args=(choice,), daemon=True).start()
        deadline = time.monotonic() + 2
        while SCAN_STATUS.snapshot()["started"] == before and time.monotonic() < deadline:
            time.sleep(0.02)    # let the status card show up on the page we redirect to
        self.send_response(303)
        selected = form.get("kind", ["episode"])[0]
        self.send_header("Location", "/library?kind=" + (selected if selected in ("movie", "episode", "media") else "episode"))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def handle_webhook(self, path):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 262144:
                raise ValueError()
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            self.respond(400, "text/plain", "Ogiltig begäran")
            return
        query = parse_qs(urlsplit(self.path).query)
        try:
            with sqlite3.connect(DATABASE) as db:
                metadata = dict(db.execute(
                    "SELECT key, value FROM metadata WHERE key IN "
                    "('webhook_token','radarr_root_prefix','sonarr_root_prefix',"
                    "'jellyfin_movies_root_prefix','jellyfin_tv_root_prefix',"
                    "'emby_movies_root_prefix','emby_tv_root_prefix',"
                    "'ignore_existing_subtitle')"))
        except sqlite3.Error:
            self.respond(503, "text/plain", "Databasen är inte tillgänglig just nu.")
            return
        if not secrets.compare_digest(query.get('token', [''])[0], metadata.get('webhook_token', '\0')):
            self.respond(403, "text/plain", "Ogiltig eller saknad webhook-token.")
            return
        try:
            if path in ('/webhooks/radarr', '/webhooks/sonarr'):
                event_type = payload.get('eventType')
                if event_type != 'Download':
                    # Radarr/Sonarr also send Test/Grab/Rename/health-check
                    # events on the same webhook -- answer 200 for all of
                    # them so the *arr side never disables the connection as
                    # "failing", but only a real Download (import) event
                    # should queue a subtitle job.
                    self.respond(200, 'application/json',
                                 json.dumps({'status': 'ignored', 'eventType': event_type}))
                    return
                if path == '/webhooks/radarr':
                    file_path = payload['movieFile']['path']
                    media = resolve_webhook_media(file_path, '/movies', metadata.get('radarr_root_prefix', ''))
                else:
                    file_path = payload['episodeFile']['path']
                    media = resolve_webhook_media(file_path, '/tv', metadata.get('sonarr_root_prefix', ''))
            elif path == '/webhooks/emby':
                # Emby's own built-in webhook (Emby Premiere) fires "library.new"
                # for a newly added item, with the file path under Item.Path.
                event_type = payload.get('Event')
                if event_type != 'library.new':
                    self.respond(200, 'application/json',
                                 json.dumps({'status': 'ignored', 'eventType': event_type}))
                    return
                file_path = payload['Item']['Path']
                media = resolve_webhook_media_per_root(file_path, (
                    ('/movies', metadata.get('emby_movies_root_prefix', '')),
                    ('/tv', metadata.get('emby_tv_root_prefix', '')),
                    ('/extra', ''),
                ))
            else:
                # Jellyfin has no built-in webhook -- it needs a third-party
                # plugin whose notification JSON is user-templated, so the
                # exact field names vary by plugin/version. Accept the most
                # common ones instead of assuming one exact schema.
                event_type = payload.get('NotificationType') or payload.get('Event')
                if event_type and event_type.lower() not in ('itemadded', 'library.new'):
                    self.respond(200, 'application/json',
                                 json.dumps({'status': 'ignored', 'eventType': event_type}))
                    return
                file_path = (payload.get('Path') or payload.get('ItemPath')
                            or (payload.get('Item') or {}).get('Path'))
                if not file_path:
                    raise ValueError(
                        'Ingen filsökväg hittades i webhook-anropet. Kontrollera att din Jellyfin '
                        'webhook-mall skickar med sökvägen, t.ex. som fältet "Path".')
                media = resolve_webhook_media_per_root(file_path, (
                    ('/movies', metadata.get('jellyfin_movies_root_prefix', '')),
                    ('/tv', metadata.get('jellyfin_tv_root_prefix', '')),
                    ('/extra', ''),
                ))
        except (KeyError, TypeError, ValueError) as error:
            self.respond(400, 'text/plain', str(error))
            return
        reason = queue_webhook_media(media, metadata.get('ignore_existing_subtitle') == '1')
        if reason:
            self.respond(200, 'application/json', json.dumps({'status': 'skipped', 'reason': reason}))
            return
        self.respond(200, 'application/json', json.dumps({'status': 'queued', 'media': str(media)}))

    def do_GET(self):
        path = urlsplit(self.path).path
        icon_match = re.fullmatch(r'/icons/([a-z]+)\.(svg|png)', path)
        if icon_match and icon_match.group(1) in ICON_NAMES:
            try:
                data = (ICON_DIR / f'{icon_match.group(1)}.{icon_match.group(2)}').read_bytes()
            except OSError:
                self.respond(404, 'text/plain', 'Ikonen saknas')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml' if icon_match.group(2) == 'svg' else 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/api/scan-status':
            self.respond(200, 'application/json', json.dumps(SCAN_STATUS.snapshot()))
            return
        if path == '/api/models':
            self.respond(200, 'application/json', json.dumps(models_card.load_status(CONFIG) or {}))
            return
        if path == '/cleanup/view':
            try:
                preview = subtitle_cleanup.preview_text(
                    parse_qs(urlsplit(self.path).query).get('path', [''])[0], CLEANUP_ROOTS)
            except (ValueError, OSError):
                self.respond(404, 'text/plain', 'Undertexten saknas')
                return
            self.respond(200, 'text/plain', preview)
            return
        if path in ('/subtitles/view', '/subtitles/file'):
            query = parse_qs(urlsplit(self.path).query)
            media_path = query.get('media', [''])[0]
            lang = query.get('lang', ['sv'])[0]
            lang = lang if lang in ('sv', 'en') else 'sv'
            exists_key, path_key = f'ai_{lang}_exists', f'ai_{lang}_path'
            try:
                with sqlite3.connect(DATABASE) as db:
                    saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
                library = json.loads(saved[0]) if saved else {'items': []}
                item = next(candidate for candidate in library['items']
                            if candidate.get('path') == media_path and candidate.get(exists_key))
                subtitle = Path(item[path_key]).resolve()
                allowed = tuple(Path(root).resolve() for root in ('/tv', '/movies', '/extra'))
                if not any(subtitle.is_relative_to(root) for root in allowed):
                    raise ValueError()
                content = subtitle.read_text(encoding='utf-8-sig')
            except (StopIteration, KeyError, OSError, ValueError):
                self.respond(404, 'text/plain', 'Undertexten saknas')
                return
            if path == '/subtitles/file':
                self.respond(200, 'application/x-subrip', content)
                return
            file_query = f"{quote(media_path, safe='')}&lang={lang}"
            body = ("<!doctype html><html lang='sv'><meta charset='utf-8'><meta name='viewport' "
                    "content='width=device-width,initial-scale=1'><title>SubSynq · Undertext</title>"
                    "<style>:root{color-scheme:dark;font-family:system-ui;background:#101820;color:#e9eff3}"
                    "body{max-width:900px;margin:5vh auto;padding:0 24px}a{color:#8bf0c1}"
                    "pre{white-space:pre-wrap;background:#192630;border:1px solid #30414d;border-radius:12px;"
                    "padding:18px;line-height:1.55}</style>"
                    f"<p><a href='/library?kind={escape(item.get('kind', 'episode'))}'>← Till biblioteket</a></p>"
                    f"<h1>{escape(item['name'])} ({'Svenska' if lang == 'sv' else 'Engelska'})</h1>"
                    f"<p><a href='/subtitles/file?media={file_query}'>"
                    "Öppna eller ladda ner SRT-filen</a></p>"
                    f"<pre>{escape(content)}</pre></html>")
            self.respond(200, 'text/html', body)
            return
        if path not in ("/", "/library", "/settings", "/settings/theme", "/settings/media", "/settings/cloud",
                        "/settings/log", "/settings/models", "/queue", "/cleanup", "/health", "/api/status", "/api/library",
                        "/api/jobs", "/api/log"):
            self.respond(404, "application/json", json.dumps({"error": "Not found"}))
            return
        try:
            data = status()
            with sqlite3.connect(DATABASE) as db:
                saved = db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()
            library = json.loads(saved[0]) if saved else {"scanned_at": None, "items": []}
        except (sqlite3.Error, OSError, RuntimeError):
            logging.exception("Configuration health check failed")
            self.respond(503, "application/json", json.dumps({"status": "error", "error": "Configuration unavailable"}))
            return
        if path == '/api/jobs':
            self.respond(200, 'application/json', json.dumps({'signature': jobs_signature(latest_jobs(DATABASE))}))
            return
        if path == '/api/log':
            self.respond(200, 'application/json', json.dumps({'events': latest_events(DATABASE)}))
            return
        if path in ("/", "/library", "/settings", "/settings/theme", "/settings/media",
                    "/settings/cloud", "/settings/log", "/settings/models", "/queue", "/cleanup"):
            query = parse_qs(urlsplit(self.path).query)
            jobs = latest_jobs(DATABASE)
            display_hardware = dict(data["hardware"])
            cloud_providers = enabled_cloud_providers()
            active_job = next((job for job in jobs.values() if job.get('status') in (
                'queued', 'transcribing', 'translating', 'verifying', 'writing', 'cancel_requested'
            )), None)
            if active_job:
                used = str(active_job.get('hardware') or '').lower()
                display_hardware['worker_status'] = 'running'
                cloud_match = re.search(r'moln-gpu \(([a-z0-9]+)\)', used)
                display_hardware['active'] = (
                    f'cloud-{cloud_match.group(1)}' if cloud_match
                    else 'amd-rx-9070-xt' if 'radeon' in used or '9070' in used
                    else 'cpu' if 'cpu' in used else None)
                if active_job.get('model'):
                    display_hardware['asr_model'] = active_job['model']
            if path == "/":
                content = render_overview_content(library["items"], jobs, display_hardware,
                                                   data["hardware_preference"], SCAN_TOKEN,
                                                   data["ui_language"], cloud_providers,
                                                   data["cloud_cost_profile"])
                page_title, active_nav = t("page_title_overview", data["ui_language"]), "overview"
            elif path == "/settings":
                content = render_settings_content(data["theme"], data["accent"], display_hardware,
                                                   data["hardware_preference"], SCAN_TOKEN,
                                                   query.get("saved", [""])[0] == "hardware",
                                                   data["instance_id"],
                                                   data["cloud_cost_profile"],
                                                   query.get("saved", [""])[0] == "cloud",
                                                   data["ignore_existing_subtitle"],
                                                   query.get("saved", [""])[0] == "automation",
                                                   data["auto_scan_enabled"],
                                                   data["auto_start_jobs"],
                                                   data["include_english_subtitle"],
                                                   data["ui_language"],
                                                   query.get("saved", [""])[0] == "language",
                                                   cloud_providers)
                page_title, active_nav = t("page_title_settings_general", data["ui_language"]), "settings:general"
            elif path == "/settings/theme":
                content = render_theme_content(data["theme"], data["accent"], SCAN_TOKEN,
                                                query.get("saved", [""])[0] == "1")
                page_title, active_nav = t("page_title_settings_theme", data["ui_language"]), "settings:theme"
            elif path == "/settings/media":
                with sqlite3.connect(DATABASE) as db:
                    secrets_row = dict(db.execute(
                        "SELECT key, value FROM metadata WHERE key IN "
                        "('webhook_token','radarr_root_prefix','sonarr_root_prefix',"
                        "'jellyfin_movies_root_prefix','jellyfin_tv_root_prefix',"
                        "'emby_movies_root_prefix','emby_tv_root_prefix')"))
                content = render_media_settings_content(
                    SCAN_TOKEN, secrets_row.get('webhook_token', ''),
                    secrets_row.get('radarr_root_prefix', ''),
                    secrets_row.get('sonarr_root_prefix', ''),
                    secrets_row.get('jellyfin_movies_root_prefix', ''),
                    secrets_row.get('jellyfin_tv_root_prefix', ''),
                    secrets_row.get('emby_movies_root_prefix', ''),
                    secrets_row.get('emby_tv_root_prefix', ''),
                    saved=query.get("saved", [""])[0] == "media",
                    token_regenerated=query.get("saved", [""])[0] == "token")
                page_title, active_nav = t("page_title_settings_media", data["ui_language"]), "settings:media"
            elif path == "/settings/cloud":
                provider = query.get('provider', [data['cloud_cost_profile']['provider']])[0]
                if provider not in PROVIDERS:
                    provider = 'vast'
                try:
                    gpu_query = normalize_gpu_query(query.get('gpu', [''])[0]) if provider == 'vast' else ''
                except ValueError:
                    gpu_query = ''
                saved_event = query.get('saved', [''])[0]
                notices = {'key': 'API-nyckeln sparades lokalt.', 'removed': 'API-nyckeln togs bort.',
                           'refreshed': 'Listan har uppdaterats.', 'offer': 'Aktuellt pris kopierades till din kostnadsplan.'}
                content = (render_cloud_market(provider, provider in load_keys(CONFIG),
                                                 catalog(CONFIG, provider, gpu_query=gpu_query), SCAN_TOKEN,
                                                 notices.get(saved_event, ''), gpu_query)
                           + render_cloud_cost(data['cloud_cost_profile'], SCAN_TOKEN,
                                               saved_event == 'cloud'))
                page_title, active_nav = t("page_title_settings_cloud", data["ui_language"]), "settings:cloud"
            elif path == "/queue":
                def parse_page(name):
                    try:
                        return max(1, int(query.get(name, ["1"])[0]))
                    except ValueError:
                        return 1
                content = render_queue_content(library["items"], jobs, SCAN_TOKEN,
                                                parse_page("queue_page"), parse_page("page"),
                                                data["ui_language"])
                page_title, active_nav = t("page_title_queue", data["ui_language"]), "queue"
            elif path == "/cleanup":
                # A submitted filter form always carries "kind"; a plain visit (menu link,
                # reload, redirect after a move) falls back to the last-used filters.
                try:
                    if "kind" in query:
                        cleanup_filters = subtitle_cleanup.parse_filters(query)
                        subtitle_cleanup.save_filters(CONFIG, cleanup_filters)
                    else:
                        cleanup_filters = subtitle_cleanup.load_filters(CONFIG)
                    if query.get("scan", [""])[0] == "1":
                        subtitle_cleanup.save_scan(CONFIG, subtitle_cleanup.mark_stale(
                            subtitle_cleanup.scan_srt(CLEANUP_ROOTS), jobs))
                        # Redirect so a reload shows the saved result instead of rescanning.
                        self.send_response(303)
                        self.send_header("Location", "/cleanup?" + urlencode(
                            subtitle_cleanup.filter_query(cleanup_filters)))
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                except OSError:
                    logging.exception("Subtitle cleanup scan failed")
                    self.respond(503, "text/plain", "Skanningen misslyckades. Se loggen.")
                    return
                ui_lang = data["ui_language"]
                notice = ""
                for key in ("moved", "restored", "emptied", "retention"):
                    if key in query:
                        notice = t(f"cleanup_notice_{key}", ui_lang).format(n=query[key][0][:9])
                        extra = query.get("failed", query.get("skipped", ["0"]))[0][:9]
                        if extra not in ("", "0"):
                            notice += " " + t("cleanup_notice_extra", ui_lang).format(n=extra)
                        break
                try:
                    cleanup_page = max(1, int(query.get("page", ["1"])[0]))
                except ValueError:
                    cleanup_page = 1
                retention_days = subtitle_cleanup.load_retention(CONFIG)
                subtitle_cleanup.purge_old(CONFIG / "trash", retention_days)
                content = subtitle_cleanup.render_cleanup_content(
                    subtitle_cleanup.load_scan(CONFIG), cleanup_filters,
                    subtitle_cleanup.list_trash(CONFIG / "trash"), SCAN_TOKEN, notice, cleanup_page,
                    retention_days=retention_days, lang=ui_lang)
                page_title, active_nav = t("page_title_cleanup", data["ui_language"]), "cleanup"
            elif path == "/settings/models":
                content = models_card.render_models_page(
                    models_card.load_status(CONFIG), SCAN_TOKEN, data["ui_language"],
                    requested=query.get("requested", [""])[0])
                page_title, active_nav = t("page_title_settings_models", data["ui_language"]), "settings:models"
            elif path == "/settings/log":
                try:
                    log_page = max(1, int(query.get("log_page", ["1"])[0]))
                except ValueError:
                    log_page = 1
                events, log_page, log_total_pages = recent_events(DATABASE, log_page)
                content = render_log_content(events, log_page, log_total_pages, data["ui_language"])
                page_title, active_nav = t("page_title_settings_log", data["ui_language"]), "settings:log"
            else:
                selected = query.get("kind", ["episode"])[0]
                if selected not in ("movie", "episode", "media"):
                    selected = "episode"
                visible = [item for item in library["items"] if item.get("kind", "media") == selected]
                rows = "".join(f"<tr><td><code>{escape(name)}</code></td><td>{'Tillgänglig' if info['exists'] else ('Valfri, inte ansluten' if name in scan_state.OPTIONAL_ROOTS else 'Saknas')}</td></tr>"
                               for name, info in data["paths"].items())
                queued_notice = ('<p class="success" role="status">✓ Undertextjobbet är köat. Statusen visas vid avsnittet.</p>'
                                 if query.get('job', [''])[0] == 'queued' else '')
                if query.get('job', [''])[0] == 'bulk':
                    notice_key = {'start': 'bulk_notice_started', 'recreate': 'bulk_notice_recreated'}.get(
                        query.get('bulk_action', [''])[0], 'bulk_notice_deleted')
                    queued_notice = ('<p class="success" role="status">' + escape(t(notice_key, data["ui_language"]).format(
                        done=query.get("done", ["0"])[0][:9], skipped=query.get("skipped", ["0"])[0][:9])) + '</p>')
                kind_keys = {"movie": "kind_movie", "episode": "kind_episode", "media": "kind_media"}
                kind_label = t(kind_keys.get(selected, "kind_media"), data["ui_language"])
                content = (
                    f"<div class='card'><h2>{escape(kind_label)} · {len(visible)} filer</h2>"
                    f"<p class='muted'>{escape(t('scan_intro', data['ui_language']))}</p>"
                    f"{render_scan_buttons(selected, library, SCAN_STATUS.running, SCAN_TOKEN, data['ui_language'])}"
                    f"{queued_notice}"
                    f"{render_bulk_bar(selected, SCAN_TOKEN, data['ui_language']) if visible else ''}"
                    f"<div class='library-list'>{render_library(visible, selected, SCAN_TOKEN, jobs, data['ignore_existing_subtitle'], data['ui_language'])}</div>"
                    "<p class='muted'>SubSynq startar aldrig hela biblioteket automatiskt i nuvarande version, om inte automatisk sökning/jobbstart aktiveras under Inställningar. En inbäddad svensk originalundertext, exempelvis PGS, räknas normalt som täckning och hoppas över – det går att ändra under Inställningar. Befintliga AI-undertexter skrivs aldrig över. En underkänd AI-fil kan tas bort här och därefter köras om, och en fil vars video bytts ut sedan undertexten skapades får en egen \"Kör igen\"-knapp.</p></div>"
                    f"<div class='card'><h2>Anslutna mappar</h2><p class='muted'>/tv är ditt valda seriebibliotek och /extra är valfri. /config sparar databasen och /models innehåller AI-modellerna.</p>"
                    f"<table><thead><tr><th>Mapp i containern</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>"
                )
                page_title, active_nav = kind_label, f"library:{selected}"
            body = render_page(page_title, active_nav, content, data["theme"], data["accent"],
                                jobs, display_hardware, SCAN_TOKEN, data["ui_language"])
            self.respond(200, "text/html", body)
        else:
            self.respond(200, "application/json", json.dumps(library if path == "/api/library" else data))

    def respond(self, code, content_type, body):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initialize()
    threading.Thread(target=auto_scan_loop, daemon=True).start()
    threading.Thread(target=trash_purge_loop, daemon=True).start()
    logging.info("SubSynq %s listening on port 8080", VERSION)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
