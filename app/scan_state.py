"""Library scans: which folders a scan covers, how a partial scan is merged, and the live status card.

A scan can cover one library (movies, series or extra) or all of them. Scanning one library must not forget what
earlier scans found in the others, so the result of a partial scan is merged into the stored one.
"""
import threading
import time
from html import escape

from i18n import t

LIBRARY_ROOTS = {"movie": "/movies", "episode": "/tv", "media": "/extra"}
OPTIONAL_ROOTS = ("/extra",)      # a library the user may leave unconnected
ALL = "all"
DONE_SHOWN_SECONDS = 60
ERROR_SHOWN_SECONDS = 600
_ROOT_ORDER = list(LIBRARY_ROOTS.values())


def roots_for(choice, exists):
    """Folders to scan for 'movie', 'episode', 'media' or 'all'; optional folders that are not connected are left out of 'all'."""
    if choice == ALL:
        return [root for root in _ROOT_ORDER if root not in OPTIONAL_ROOTS or exists(root)]
    if choice in LIBRARY_ROOTS:
        return [LIBRARY_ROOTS[choice]]
    raise ValueError("Okänt bibliotek")


def _under(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def _rank(item):
    path = str(item.get("path", ""))
    return next((index for index, root in enumerate(_ROOT_ORDER) if _under(path, [root])), len(_ROOT_ORDER))


def merge_scan(previous, roots, items, scanned_at, everything):
    """The library result to store after scanning `roots`. A scan of everything replaces the stored result."""
    previous = previous or {}
    scanned = {}
    kept = []
    if not everything:
        scanned = dict(previous.get("scanned") or {})
        if previous.get("scanned_at"):
            for root in _ROOT_ORDER:      # results saved before per-library scans only know one common time
                scanned.setdefault(root, previous["scanned_at"])
        kept = [item for item in previous.get("items", []) if not _under(str(item.get("path", "")), roots)]
    for root in roots:
        scanned[root] = scanned_at
    merged = sorted(kept + list(items), key=_rank)      # stable: the order inside each library is kept
    return {"scanned_at": scanned_at, "scanned": scanned, "items": merged}


def scanned_at_for(library, root):
    """When `root` was last scanned, or None."""
    if "scanned" in library:
        return library["scanned"].get(root)
    return library.get("scanned_at")      # saved before per-library scans


class ScanStatus:
    """What the running (or last) scan is doing, for the status card and /api/scan-status."""

    def __init__(self, now=time.time):
        self._now = now
        self._lock = threading.Lock()
        self._data = self._idle()

    @staticmethod
    def _idle():
        return {"state": "idle", "choice": "", "roots": [], "folder": "", "files": 0, "message": "",
                "started": None, "finished": None}

    def start(self, choice, roots):
        with self._lock:
            self._counts = {}
            self._data = {**self._idle(), "state": "running", "choice": choice, "roots": list(roots),
                          "folder": roots[0] if roots else "", "started": self._now()}

    def progress(self, folder, files):
        with self._lock:
            if self._data["state"] != "running":
                return
            self._counts[folder] = files
            self._data["folder"] = folder
            self._data["files"] = sum(self._counts.values())

    def finish(self, files):
        with self._lock:
            self._data.update(state="done", files=files, finished=self._now())

    def fail(self, message):
        with self._lock:
            self._data.update(state="error", message=str(message)[:300], finished=self._now())

    def snapshot(self):
        with self._lock:
            data = dict(self._data)
        end = data["finished"] or self._now()
        data["seconds"] = int(max(0, end - data["started"])) if data["started"] else 0
        return data

    @property
    def running(self):
        return self.snapshot()["state"] == "running"


def _target(choice, lang):
    return t("scan_target_" + (choice if choice in ("movie", "episode", "media") else "all"), lang)


def render_scan_banner(snapshot, lang="sv", now=None):
    """The card shown at the top of every page while a scan runs, and briefly after it ended."""
    state = snapshot.get("state")
    now = now if now is not None else time.time()
    if state == "running":
        title = t("scan_running_title", lang).format(what=_target(snapshot.get("choice"), lang))
    elif state == "done" and now - (snapshot.get("finished") or 0) <= DONE_SHOWN_SECONDS:
        title = t("scan_done_title", lang).format(what=_target(snapshot.get("choice"), lang))
    elif state == "error" and now - (snapshot.get("finished") or 0) <= ERROR_SHOWN_SECONDS:
        title = t("scan_failed_title", lang)
    else:
        return ""
    detail = t("scan_detail", lang).format(
        folder=snapshot.get("folder") or "", files=snapshot.get("files", 0), seconds=snapshot.get("seconds", 0))
    if state == "done":
        detail = t("scan_done_detail", lang).format(files=snapshot.get("files", 0), seconds=snapshot.get("seconds", 0))
    note = ""
    if state == "error":
        note = f"<p class='error'>{escape(snapshot.get('message') or '')}</p>"
        detail = ""
    meter = ("<div class='meter-track'><div class='meter-fill scan-pulse' style='width:100%;background:var(--ac);'>"
             "</div></div>") if state == "running" else ""
    attributes = " ".join(
        f"data-t-{attribute}='{escape(t(key, lang), quote=True)}'"
        for attribute, key in (("detail", "scan_detail"), ("done-detail", "scan_done_detail")))
    return (
        f"<div class='card scan-card' data-scan-card data-state='{escape(str(state), quote=True)}' {attributes} "
        f"role='status'><h2>{escape(title)}</h2>{meter}"
        f"<p class='muted' data-scan-detail>{escape(detail)}</p>{note}</div>")
