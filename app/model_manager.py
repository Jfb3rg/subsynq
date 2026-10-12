"""Download the AI models SubSynq needs, automatically, and keep them in step with models.json.

Nobody should have to fetch models by hand. Which models SubSynq uses is NOT decided
in code: it is listed in a separate file, models.json (the copy shipped with the app,
or /config/models.json if you put one there). The worker starts a ModelManager that
compares that list with what is on disk, downloads whatever is missing (Whisper and
Qwen from Hugging Face, Gemma through Ollama) and reports progress in
/config/models-status.json, which the web interface shows.

Change the list (a new model, or a better version of one) and the new model is
downloaded; the old one is then offered for removal. Nothing is ever deleted without
an explicit request from the Models settings page, and only models SubSynq itself has
recorded as installed can be removed.

Local jobs wait until every listed model is present; deleting subtitles and rented
cloud servers never need the local models.
"""
import fnmatch
import json
import hashlib
import os
import shutil
import threading
import time
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

GIB = 1024 ** 3
DISK_MARGIN = 1.1          # keep 10 % free beyond the download itself
POLL_SECONDS = 2.0
RECHECK_SECONDS = 600       # re-verify once ready (cheap: only looks at the disk)
RETRY_MIN, RETRY_MAX = 60, 900
WAIT_STEP = 5               # while waiting, look for a removal request this often
KINDS = ("faster-whisper", "hf", "ollama")
CATALOGUE_FILE = "models.json"
STATUS_FILE = "models-status.json"
LEDGER_FILE = "models-installed.json"
REMOVE_REQUEST_FILE = "models-remove-request.json"
UPDATE_REQUEST_FILE = "models-update-request.json"
UPDATE_CHECK_SECONDS = 12 * 3600   # how often to look for newer versions of the listed models
OLLAMA_REGISTRY = "https://registry.ollama.ai"
OLLAMA_MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
ROLE_ENVIRONMENT = {"translation": "SUBSYNQ_TRANSLATION_MODEL", "review": "SUBSYNQ_REVIEW_MODEL"}
# Same file filter faster-whisper uses, so the progress total matches what is fetched.
FASTER_WHISPER_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin",
                           "tokenizer.json", "vocabulary.*"]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str        # 'faster-whisper' | 'hf' | 'ollama'
    name: str        # 'large-v3' | 'Qwen/Qwen3-ASR-1.7B' | 'gemma3:12b'
    label: str
    size_gb: float   # approximate download size: disk-space check and the display
    used_by: str
    role: str = ""   # 'translation' | 'review' for the Gemma models the worker calls

    @property
    def key(self):
        return f"{self.kind}|{self.name}"


# ------------------------------------------------------------------- the catalogue

class CatalogueError(ValueError):
    pass


@dataclass
class Catalogue:
    specs: list
    source: Path = None
    errors: list = None


def parse_catalogue(data):
    """Turn the parsed models.json into ModelSpec objects, or raise CatalogueError."""
    if not isinstance(data, dict) or not isinstance(data.get("models"), list) or not data["models"]:
        raise CatalogueError('filen måste innehålla en lista "models" med minst en modell')
    specs, seen = [], set()
    for number, entry in enumerate(data["models"], 1):
        if not isinstance(entry, dict):
            raise CatalogueError(f"modell {number} är ingen post")
        for field in ("id", "kind", "name", "label"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise CatalogueError(f'modell {number}: fältet "{field}" saknas')
        if entry["kind"] not in KINDS:
            raise CatalogueError(f'modell {number}: kind måste vara en av {", ".join(KINDS)}')
        if entry["id"] in seen:
            raise CatalogueError(f'id "{entry["id"]}" förekommer två gånger')
        seen.add(entry["id"])
        try:
            size = float(entry.get("size_gb", 0))
        except (TypeError, ValueError):
            raise CatalogueError(f'modell {number}: size_gb måste vara ett tal') from None
        specs.append(ModelSpec(entry["id"], entry["kind"], entry["name"].strip(), entry["label"], size,
                               str(entry.get("used_by", "")), str(entry.get("role", ""))))
    return specs


def catalogue_paths(config_dir="/config"):
    """Your own /config/models.json wins over the copy that ships with the app."""
    return [Path(config_dir) / CATALOGUE_FILE, Path(__file__).with_name(CATALOGUE_FILE)]


def load_catalogue(paths, environ=None):
    """First valid file wins. A broken file is reported, and the next one is used instead."""
    environ = os.environ if environ is None else environ
    errors = []
    for path in paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            errors.append(f"{path}: {error}")
            continue
        try:
            specs = parse_catalogue(data)
        except CatalogueError as error:
            errors.append(f"{path}: {error}")
            continue
        # An explicit environment variable still wins for the two models the worker calls by name.
        for number, spec in enumerate(specs):
            override = environ.get(ROLE_ENVIRONMENT.get(spec.role, ""), "").strip()
            if override and override != spec.name:
                specs[number] = replace(spec, name=override, label=override)
        return Catalogue(specs, Path(path), errors)
    return Catalogue([], None, errors)


def configured_name(role, default, config_dir="/config", environ=None):
    """The model name for a role ('translation' / 'review') according to models.json."""
    try:
        for spec in load_catalogue(catalogue_paths(config_dir), environ).specs:
            if spec.role == role:
                return spec.name
    except Exception:  # noqa: BLE001 - the worker must still start with its built-in default
        pass
    return default


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- disk helpers

def _repo_dir(cache_dir, repo_id):
    return Path(cache_dir) / ("models--" + repo_id.replace("/", "--"))


def _snapshot_complete(repo_dir, required):
    """True when a Hugging Face cache folder holds a finished download.

    `required` are glob patterns that must each match a real file in some snapshot.
    An interrupted download leaves *.incomplete files in blobs/, which fails the check.
    """
    repo_dir = Path(repo_dir)
    snapshots = repo_dir / "snapshots"
    if not snapshots.is_dir():
        return False
    if any(repo_dir.joinpath("blobs").glob("*.incomplete")):
        return False
    for snapshot in snapshots.iterdir():
        if all(any(path.exists() for path in snapshot.glob(pattern)) for pattern in required):
            return True
    return False


def _installed_revision(repo_dir):
    """The commit a Hugging Face model folder currently points at (refs/main), or None."""
    try:
        return (Path(repo_dir) / "refs" / "main").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _latest_revision(repo_id):
    from huggingface_hub import HfApi
    return HfApi().model_info(repo_id, timeout=20).sha


def _prune_old_revisions(cache_dir, repo_id):
    """After an update, delete the revisions of one repo that refs/main no longer points at."""
    from huggingface_hub import scan_cache_dir
    info = scan_cache_dir(str(cache_dir))
    for repo in info.repos:
        if repo.repo_id != repo_id or repo.repo_type != "model":
            continue
        old = [rev.commit_hash for rev in repo.revisions if "main" not in rev.refs]
        if old and any("main" in rev.refs for rev in repo.revisions):
            info.delete_revisions(*old).execute()


def _ollama_registry_path(name):
    """'gemma3:12b' -> ('library/gemma3', '12b'); 'user/model:tag' -> ('user/model', 'tag')."""
    model, _, tag = name.partition(":")
    return (model if "/" in model else f"library/{model}"), (tag or "latest")


def _registry_digest(name, opener=urllib.request.urlopen):
    """sha256 of the model's manifest in the Ollama registry: equals the digest Ollama reports locally."""
    path, tag = _ollama_registry_path(name)
    request = urllib.request.Request(f"{OLLAMA_REGISTRY}/v2/{path}/manifests/{tag}",
                                     headers={"Accept": OLLAMA_MANIFEST_TYPE})
    with opener(request, timeout=20) as response:
        return hashlib.sha256(response.read()).hexdigest()


def _remove_cache_dir(repo_dir, cache_dir):
    """Delete one model's cache folder, refusing anything that is not exactly that."""
    repo_dir = Path(repo_dir)
    if not repo_dir.is_dir():
        return
    if (repo_dir.is_symlink() or not repo_dir.name.startswith("models--")
            or repo_dir.parent.resolve() != Path(cache_dir).resolve()):
        raise ValueError(f"vägrar ta bort {repo_dir}")
    shutil.rmtree(repo_dir)


def _dir_bytes(path):
    total = 0
    try:
        for entry in Path(path).rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _expected_bytes(repo_id, patterns=None):
    """Size of what will be downloaded, from the Hugging Face API (None if unreachable)."""
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo_id, files_metadata=True, timeout=20)
        files = [(s.rfilename, s.size or 0) for s in info.siblings]
        if patterns:
            files = [(n, s) for n, s in files if any(fnmatch.fnmatch(n, p) for p in patterns)]
        total = sum(size for _name, size in files)
        return total or None
    except Exception:  # noqa: BLE001 - progress is best-effort
        return None


class _Watcher:
    """Reports download progress by watching a folder grow while a download runs."""

    def __init__(self, folder, total_bytes, report):
        self.folder, self.total, self.report = Path(folder), total_bytes, report
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(POLL_SECONDS):
            done = _dir_bytes(self.folder)
            percent = min(99, int(done * 100 / self.total)) if self.total else None
            self.report(percent, f"{done / GIB:.1f} GB")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=5)


# ---------------------------------------------------------------------- backends

class FasterWhisperBackend:
    """Whisper models in the format faster-whisper reads from /models (same call the worker uses)."""

    def __init__(self, models_dir):
        self.models_dir = Path(models_dir)

    def _repo_id(self, spec):
        from faster_whisper.utils import _MODELS
        return _MODELS.get(spec.name, spec.name)

    def is_present(self, spec):
        return _snapshot_complete(_repo_dir(self.models_dir, self._repo_id(spec)), ["model.bin", "config.json"])

    def download(self, spec, report):
        from faster_whisper.utils import download_model
        repo_id = self._repo_id(spec)
        total = _expected_bytes(repo_id, FASTER_WHISPER_PATTERNS) or int(spec.size_gb * GIB)
        with _Watcher(_repo_dir(self.models_dir, repo_id), total, report):
            download_model(spec.name, cache_dir=str(self.models_dir))

    def remove(self, spec):
        _remove_cache_dir(_repo_dir(self.models_dir, self._repo_id(spec)), self.models_dir)

    def installed_version(self, spec):
        return _installed_revision(_repo_dir(self.models_dir, self._repo_id(spec)))

    def latest_version(self, spec):
        return _latest_revision(self._repo_id(spec))

    def update(self, spec, report):
        """Download the newest revision, then delete the older one(s)."""
        self.download(spec, report)
        _prune_old_revisions(self.models_dir, self._repo_id(spec))


class HubBackend:
    """Whole Hugging Face repositories, stored where the Qwen service looks (HF_HOME/hub)."""

    def __init__(self, hub_dir):
        self.hub_dir = Path(hub_dir)

    def is_present(self, spec):
        repo = _repo_dir(self.hub_dir, spec.name)
        return (_snapshot_complete(repo, ["config.json", "*.safetensors"])
                or _snapshot_complete(repo, ["config.json", "pytorch_model*.bin"]))

    def download(self, spec, report):
        from huggingface_hub import snapshot_download
        total = _expected_bytes(spec.name) or int(spec.size_gb * GIB)
        with _Watcher(_repo_dir(self.hub_dir, spec.name), total, report):
            snapshot_download(spec.name, cache_dir=str(self.hub_dir))

    def remove(self, spec):
        _remove_cache_dir(_repo_dir(self.hub_dir, spec.name), self.hub_dir)

    def installed_version(self, spec):
        return _installed_revision(_repo_dir(self.hub_dir, spec.name))

    def latest_version(self, spec):
        return _latest_revision(spec.name)

    def update(self, spec, report):
        """Download the newest revision, then delete the older one(s)."""
        self.download(spec, report)
        _prune_old_revisions(self.hub_dir, spec.name)


class OllamaBackend:
    """Gemma through the translate-engine container's own API."""

    def __init__(self, base_url, opener=urllib.request.urlopen, registry_opener=urllib.request.urlopen):
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.registry_opener = registry_opener

    def _names(self):
        with self.opener(f"{self.base_url}/api/tags", timeout=15) as response:
            return {item.get("name") for item in json.load(response).get("models", [])}

    def is_present(self, spec):
        names = self._names()
        return spec.name in names or (":" not in spec.name and f"{spec.name}:latest" in names)

    def download(self, spec, report):
        request = urllib.request.Request(
            f"{self.base_url}/api/pull", json.dumps({"model": spec.name, "stream": True}).encode(),
            {"Content-Type": "application/json"})
        layers = {}
        with self.opener(request, timeout=300) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(f"Ollama: {event['error']}")
                if event.get("total"):
                    layers[event.get("digest", "")] = (event.get("completed", 0), event["total"])
                total = sum(t for _c, t in layers.values())
                done = sum(c for c, _t in layers.values())
                if total:
                    report(min(99, int(done * 100 / total)), f"{done / GIB:.1f} / {total / GIB:.1f} GB")
                if event.get("status") == "success":
                    return
        raise RuntimeError("Ollama avslutade nedladdningen utan att bekräfta den")

    def remove(self, spec):
        request = urllib.request.Request(
            f"{self.base_url}/api/delete", json.dumps({"model": spec.name}).encode(),
            {"Content-Type": "application/json"}, method="DELETE")
        self.opener(request, timeout=60).close()

    def installed_version(self, spec):
        with self.opener(f"{self.base_url}/api/tags", timeout=15) as response:
            for item in json.load(response).get("models", []):
                if item.get("name") == spec.name:
                    return item.get("digest")
        return None

    def latest_version(self, spec):
        return _registry_digest(spec.name, self.registry_opener)

    def update(self, spec, report):
        """Pull the registry's current version of the tag. Ollama clears the old files when it restarts."""
        self.download(spec, report)


# ----------------------------------------------------------------------- manager

class ModelManager:
    def __init__(self, catalogue_loader, backends, config_dir, models_dir, *, free_bytes=None,
                 sleep=time.sleep, log=print, is_busy=None, now=time.time):
        self.catalogue_loader = catalogue_loader
        self.backends = backends
        self.config_dir = Path(config_dir)
        self.status_path = self.config_dir / STATUS_FILE
        self.ledger_path = self.config_dir / LEDGER_FILE
        self.request_path = self.config_dir / REMOVE_REQUEST_FILE
        self.update_request_path = self.config_dir / UPDATE_REQUEST_FILE
        self.is_busy = is_busy or (lambda: False)   # True while a subtitle job is running
        self.now = now
        self._update_checked_ts = 0.0
        self._update_checked_at = ""
        self.models_dir = Path(models_dir)
        self.free_bytes = free_bytes or (lambda: shutil.disk_usage(self.models_dir).free)
        self.sleep = sleep
        self.log = log
        self._lock = threading.Lock()
        self.specs = []
        self._ready = False
        self._state = "checking"
        self._message = ""
        self._notice = ""
        self._source = ""
        self._catalogue_errors = []
        self._obsolete = []
        self._models = {}
        self._last_write = 0.0

    @property
    def ready(self):
        return self._ready

    # --- status file
    def _write(self, force=True):
        if not force and time.monotonic() - self._last_write < 1.0:
            return
        self._last_write = time.monotonic()
        with self._lock:
            done = sum(1 for m in self._models.values() if m["state"] == "present")
            data = {"updated_at": _now(), "state": self._state, "message": self._message,
                    "notice": self._notice, "ready": self._ready, "done": done, "total": len(self._models),
                    "source": self._source, "catalogue_errors": list(self._catalogue_errors),
                    "update_checked_at": self._update_checked_at,
                    "obsolete": [dict(m) for m in self._obsolete],
                    "models": [dict(m) for m in self._models.values()]}
        try:
            _write_json(self.status_path, data)
        except OSError as error:
            self.log(f"Kunde inte skriva modellstatus: {error}")

    def _set(self, spec, **values):
        with self._lock:
            self._models[spec.id].update(values)

    # --- the list in models.json
    def _reload_catalogue(self):
        catalogue = self.catalogue_loader()
        self.specs = list(catalogue.specs)
        self._source = str(catalogue.source) if catalogue.source else ""
        self._catalogue_errors = list(catalogue.errors or [])
        with self._lock:
            self._models = {
                s.id: self._models.get(s.id, {}) | {
                    "id": s.id, "label": s.label, "used_by": s.used_by, "size_gb": s.size_gb,
                    "name": s.name, "kind": s.kind}
                for s in self.specs}
            for model in self._models.values():
                model.setdefault("update", "")          # "" | current | available | unknown
                model.setdefault("installed", "")
                model.setdefault("latest", "")
                model.setdefault("state", "pending")
                model.setdefault("percent", None)
                model.setdefault("detail", "")

    # --- what SubSynq itself has installed (only these can ever be removed)
    def _ledger(self):
        data = _read_json(self.ledger_path, {})
        return data.get("models", {}) if isinstance(data, dict) else {}

    def _record_present(self):
        ledger = self._ledger()
        changed = False
        for spec in self.specs:
            if self._models[spec.id]["state"] == "present" and spec.key not in ledger:
                ledger[spec.key] = {"kind": spec.kind, "name": spec.name, "label": spec.label,
                                    "size_gb": spec.size_gb, "used_by": spec.used_by, "first_seen": _now()}
                changed = True
        if changed:
            try:
                _write_json(self.ledger_path, {"models": ledger})
            except OSError as error:
                self.log(f"Kunde inte spara listan över installerade modeller: {error}")
        wanted = {s.key for s in self.specs}
        self._obsolete = [dict(entry, key=key) for key, entry in sorted(ledger.items()) if key not in wanted]

    def _process_removals(self):
        """Carry out a removal the user asked for on the Models page; never anything else."""
        if not self.request_path.exists():
            return
        request = _read_json(self.request_path, {})
        keys = request.get("keys", []) if isinstance(request, dict) else []
        ledger = self._ledger()
        wanted = {s.key for s in self.specs}
        messages = []
        for key in keys:
            entry = ledger.get(key)
            if entry is None:
                messages.append(f"{key}: okänd modell, ingenting togs bort")
            elif key in wanted:
                messages.append(f"{entry.get('label', key)}: används fortfarande, ingenting togs bort")
            else:
                spec = ModelSpec(entry.get("id", key), entry["kind"], entry["name"], entry.get("label", key),
                                 float(entry.get("size_gb", 0)), entry.get("used_by", ""))
                try:
                    self.backends[spec.kind].remove(spec)
                    del ledger[key]
                    messages.append(f"{spec.label}: borttagen")
                    self.log(f"Tog bort {spec.label} ({key})")
                except Exception as error:  # noqa: BLE001 - report, keep the model in the list
                    messages.append(f"{spec.label}: kunde inte tas bort ({error})")
                    self.log(messages[-1])
        try:
            _write_json(self.ledger_path, {"models": ledger})
            self.request_path.unlink()
        except OSError as error:
            self.log(f"Kunde inte avsluta borttagningsbegäran: {error}")
        self._notice = "; ".join(messages)

    # --- newer versions: checked automatically, installed only when the user asks
    def _check_updates(self):
        """Compare each present model with the newest version. Only looks; never downloads."""
        for spec in self.specs:
            if self._models[spec.id]["state"] != "present":
                continue
            backend = self.backends[spec.kind]
            try:
                installed, latest = backend.installed_version(spec), backend.latest_version(spec)
            except Exception:  # noqa: BLE001 - offline, no such method, registry down: just "unknown"
                self._set(spec, update="unknown", installed="", latest="")
                continue
            if not installed or not latest:
                self._set(spec, update="unknown", installed=(installed or "")[:12], latest=(latest or "")[:12])
            else:
                self._set(spec, update="current" if installed == latest else "available",
                          installed=installed[:12], latest=latest[:12])
        self._update_checked_ts = self.now()
        self._update_checked_at = _now()

    def _maybe_check_updates(self):
        if self.now() - self._update_checked_ts >= UPDATE_CHECK_SECONDS:
            self._check_updates()

    def _process_update_request(self):
        """Carry out a request from the Models page: check now, and/or update the chosen models."""
        if not self.update_request_path.exists():
            return
        request = _read_json(self.update_request_path, {})
        keys = request.get("keys", []) if isinstance(request, dict) else []
        if keys and self.is_busy():
            self._notice = "Uppdateringen väntar tills pågående undertextjobb är klara"
            return                                   # keep the request; try again when idle
        try:
            self.update_request_path.unlink()
        except OSError as error:
            self.log(f"Kunde inte avsluta uppdateringsbegäran: {error}")
        messages = []
        if isinstance(request, dict) and request.get("check"):
            self._check_updates()
            messages.append("Kontrollerade efter nya versioner")
        for key in keys:
            spec = next((s for s in self.specs if s.key == key), None)
            model = self._models.get(spec.id) if spec else None
            if model is None or model["state"] != "present":
                messages.append(f"{key}: okänd eller saknas, ingenting uppdaterades")
            elif model.get("update") != "available":
                messages.append(f"{spec.label}: är redan den senaste versionen")
            else:
                messages.append(self._update_one(spec))
        self._notice = "; ".join(messages)

    def _update_one(self, spec):
        """Download the newest version of one model, then remove the older one."""
        needed = spec.size_gb * GIB * DISK_MARGIN
        try:
            free = self.free_bytes()
        except OSError:
            free = None
        if free is not None and free < needed:
            return f"{spec.label}: för lite diskutrymme för uppdateringen (behöver {needed / GIB:.0f} GB)"
        self._ready, self._state = False, "downloading"
        self._message = f"Uppdaterar {spec.label}"
        self._set(spec, state="downloading", percent=0, detail="startar")
        self._write()

        def report(percent, detail):
            self._set(spec, percent=percent, detail=detail)
            self._write(force=False)

        self.log(f"Uppdaterar {spec.label} ...")
        try:
            self.backends[spec.kind].update(spec, report)
            outcome = f"{spec.label}: uppdaterad"
        except Exception as error:  # noqa: BLE001 - the old version is still in place and usable
            outcome = f"{spec.label}: uppdateringen misslyckades ({type(error).__name__}: {error})"[:300]
            self.log(outcome)
        self._set(spec, state="present", percent=100, detail="")
        self._message = ""
        try:
            installed, latest = (self.backends[spec.kind].installed_version(spec),
                                 self.backends[spec.kind].latest_version(spec))
            self._set(spec, installed=(installed or "")[:12], latest=(latest or "")[:12],
                      update="current" if installed and installed == latest else "available")
        except Exception:  # noqa: BLE001
            self._set(spec, update="unknown")
        self.log(outcome)
        return outcome

    # --- one pass
    def run_once(self):
        """Check every listed model and download what is missing. Returns True when all are present."""
        self._state, self._message = "checking", ""
        self._reload_catalogue()
        if not self.specs:
            self._ready, self._state = False, "error"
            self._message = ("Ingen giltig modellfil hittades: " + "; ".join(self._catalogue_errors)
                             if self._catalogue_errors else "Ingen modellfil (models.json) hittades")
            self._write()
            return False
        for spec in self.specs:
            try:
                present = self.backends[spec.kind].is_present(spec)
            except Exception as error:  # noqa: BLE001 - e.g. Ollama not up yet
                present = False
                self._set(spec, detail=f"kunde inte kontrolleras: {error}"[:200])
            self._set(spec, state="present" if present else "pending", percent=100 if present else None,
                      detail="" if present else self._models[spec.id]["detail"])
        self._process_removals()
        self._process_update_request()
        self._record_present()
        self._write()
        missing = [s for s in self.specs if self._models[s.id]["state"] != "present"]
        if not missing:
            self._ready, self._state = True, "ready"
            self._write()
            self._maybe_check_updates()       # after "ready" is reported, so a slow network never delays jobs
            self._write()
            return True
        self._ready, self._state = False, "downloading"
        total_gb = sum(s.size_gb for s in missing)
        self._message = f"{len(missing)} modeller saknas (cirka {total_gb:.0f} GB) och hämtas automatiskt"
        self.log(self._message)
        self._write()
        failures = 0
        for spec in missing:
            if not self._download_one(spec):
                failures += 1
        self._ready = failures == 0 and all(m["state"] == "present" for m in self._models.values())
        self._state = "ready" if self._ready else "error"
        if self._ready:
            self._message = ""
        self._record_present()
        self._write()
        return self._ready

    def _download_one(self, spec):
        needed = spec.size_gb * GIB * DISK_MARGIN
        try:
            free = self.free_bytes()
        except OSError:
            free = None
        if free is not None and free < needed:
            message = (f"För lite diskutrymme för {spec.label}: behöver {needed / GIB:.0f} GB, "
                       f"{free / GIB:.0f} GB är ledigt i modellmappen")
            self._set(spec, state="error", detail=message)
            self._message = message
            self.log(message)
            self._write()
            return False
        self._set(spec, state="downloading", percent=0, detail="startar")
        self._write()

        def report(percent, detail):
            self._set(spec, percent=percent, detail=detail)
            self._write(force=False)

        self.log(f"Hämtar {spec.label} ...")
        try:
            self.backends[spec.kind].download(spec, report)
            if not self.backends[spec.kind].is_present(spec):
                raise RuntimeError("nedladdningen blev inte komplett")
        except Exception as error:  # noqa: BLE001 - network, disk, registry: report and retry later
            text = f"{type(error).__name__}: {error}"[:300]
            self._set(spec, state="error", detail=text)
            self._message = f"Kunde inte hämta {spec.label}: {text}"
            self.log(self._message)
            self._write()
            return False
        self._set(spec, state="present", percent=100, detail="")
        self.log(f"{spec.label} är klar")
        self._write()
        return True

    # --- forever
    def _wait(self, seconds):
        """Sleep, but wake early when the Models page has asked for a removal."""
        remaining = seconds
        while remaining > 0:
            step = min(WAIT_STEP, remaining)
            self.sleep(step)
            remaining -= step
            if self.request_path.exists() or self.update_request_path.exists():
                return

    def run_forever(self):
        delay = RETRY_MIN
        while True:
            try:
                ready = self.run_once()
            except Exception as error:  # noqa: BLE001 - never let this thread die
                ready = False
                self.log(f"Modellkontrollen misslyckades: {error}")
            if ready:
                delay = RETRY_MIN
                self._wait(RECHECK_SECONDS)
            else:
                self._wait(delay)
                delay = min(delay * 2, RETRY_MAX)


def build_manager(*, config_dir="/config", models_dir="/models", ollama_url=None, log=print, is_busy=None):
    base = (ollama_url or os.environ.get("SUBSYNQ_TRANSLATE_URL", "http://translate-engine:11434/api/generate"))
    base = base.split("/api/")[0]
    hub_dir = Path(os.environ.get("HF_HOME", f"{models_dir}/huggingface")) / "hub"
    backends = {"faster-whisper": FasterWhisperBackend(models_dir), "hf": HubBackend(hub_dir),
                "ollama": OllamaBackend(base)}
    return ModelManager(lambda: load_catalogue(catalogue_paths(config_dir)), backends, config_dir, models_dir,
                        log=log, is_busy=is_busy)
