"""Samlad fjärrtjänst för en hyrd molnserver (Vast.ai), CPU eller GPU.

En enda port exponeras. Alla sökvägar måste börja med `/<token>/`; allt annat
ger 404. Bakom grinden körs hela AI-kedjan på serverns CPU eller GPU
(SUBSYNQ_CLOUD_DEVICE=cpu|cuda):

- `/<token>/transcribe`  Whisper large-v3 (faster-whisper; int8 på CPU, float16 på CUDA), körs här.
- `/<token>/qwen/...`    vidarebefordras till qwen_asr_service.py (CPU).
- `/<token>/ollama/...`  vidarebefordras till Ollama (Gemma-modellerna).
- `/<token>/health`      `ready` först när ALLA tre backender svarar, minnet räcker och
                         (CUDA) en GPU med tillräckligt VRAM faktiskt finns.

Tjänsten har ingen tyst reserv: saknas en backend, eller är minnet för litet,
svarar /health med ett tydligt tekniskt tillstånd i stället för `ready`.
Token skickas i sökvägen så att workerns befintliga anrop (Qwen, Ollama) kan
peka mot samma bas-URL utan ändrade rubriker. Trafiken är krypterad (HTTPS) med ett
engångscertifikat som workern skapar per hyra och pinnar; token skyddar dessutom mot
tillfälliga besökare. Ljudfönster och dialogtext lämnar ändå datorn.
"""
import contextlib
import gc
import hmac
import http.client
import json
import os
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SUBSYNQ_CLOUD_PORT", "8766"))
TOKEN = os.environ.get("SUBSYNQ_CLOUD_TOKEN", "")
QWEN_PORT = int(os.environ.get("SUBSYNQ_QWEN_PORT", "8091"))
OLLAMA_PORT = int(os.environ.get("SUBSYNQ_OLLAMA_PORT", "11434"))
WHISPER_NAME = os.environ.get("SUBSYNQ_CLOUD_WHISPER_MODEL", "large-v3")
REQUIRED_MODELS = [name for name in os.environ.get("SUBSYNQ_CLOUD_OLLAMA_MODELS", "").split(",") if name]
DEVICE = os.environ.get("SUBSYNQ_CLOUD_DEVICE", "cpu")
if DEVICE not in ("cpu", "cuda"):
    raise SystemExit(f"Ogiltig SUBSYNQ_CLOUD_DEVICE: {DEVICE!r}")
# Models live in system RAM on CPU, in VRAM on CUDA (RAM is only a loading buffer there).
MIN_RAM_BYTES = int(float(os.environ.get("SUBSYNQ_CLOUD_MIN_RAM_GIB", "16" if DEVICE == "cpu" else "12")) * 1024 ** 3)
MIN_VRAM_BYTES = int(float(os.environ.get("SUBSYNQ_CLOUD_MIN_VRAM_GIB", "15")) * 1024 ** 3)
MAX_AUDIO_BYTES = 32 * 1024 * 1024
FORWARD_HEADERS = ("Content-Type", "X-SubSynq-Language")
# Exactly the keyword arguments worker.py passes to faster-whisper today.
# Anything else is refused rather than silently ignored.
ALLOWED_OPTIONS = {
    "language": (str, type(None)), "multilingual": (bool,), "vad_filter": (bool,),
    "beam_size": (int,), "condition_on_previous_text": (bool,),
    "word_timestamps": (bool,), "hallucination_silence_threshold": (int, float),
    "temperature": (int, float),
}

ALLOWED_WHISPER_MODELS = ("large-v3", "large-v2")

# At most ONE Whisper model is resident, as in the local worker: asking for
# the other one drops the current model before loading it. `whisper_lock`
# also serialises transcription, so a model is never freed while in use.
whisper = None
whisper_loaded = None
whisper_state = {"status": "loading", "error": None}
whisper_lock = threading.Lock()


def available_memory_bytes():
    """Smallest of the machine's RAM and the container's memory limit."""
    limits = []
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    limits.append(int(line.split()[1]) * 1024)
                    break
    except (OSError, ValueError, IndexError):
        pass
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, encoding="ascii") as limit:
                value = limit.read().strip()
            if value.isdigit() and int(value) < 1 << 60:
                limits.append(int(value))
        except OSError:
            pass
    return min(limits) if limits else None


def whisper_arguments():
    if DEVICE == "cuda":
        return {"device": "cuda", "compute_type": "float16"}
    return {"device": "cpu", "compute_type": "int8",
            "cpu_threads": max(1, min(16, os.cpu_count() or 1))}


def gpu_info():
    """(name, total VRAM in bytes) of the first NVIDIA GPU, or None if there is none."""
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True).stdout
        name, memory = [part.strip() for part in output.splitlines()[0].rsplit(",", 1)]
        return name, int(float(memory)) * 1024 ** 2
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def vram_sample():
    """(used MiB, total MiB, [{pid, name, mib}]) of the first NVIDIA GPU, or None."""
    try:
        overall = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout
        used, total = [int(float(part)) for part in overall.splitlines()[0].split(",")]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    processes = []
    try:
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout
        for line in apps.splitlines():
            pid, name, mib = [part.strip() for part in line.rsplit(",", 2)[-3:]] if line.count(",") >= 2 else (None, None, None)
            if pid and pid.isdigit() and mib.replace(".", "", 1).isdigit():
                processes.append({"pid": int(pid), "name": os.path.basename(name), "mib": int(float(mib))})
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return used, total, processes


class VramMonitor:
    """Peak GPU memory per phase (Whisper / Qwen / each Ollama model), measured as the
    whole card's `memory.used` -- exactly what a purchased card of that size would have
    to hold. A background thread samples nvidia-smi; each request is a named phase.
    Samples taken while nothing runs are `idle` (what stays resident between steps).
    Sampling is periodic, so a spike shorter than the interval can be missed: treat the
    numbers as a lower bound and keep a safety margin when sizing a card."""

    def __init__(self, sampler=vram_sample, interval=0.5):
        self.sampler, self.interval = sampler, interval
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.active = []
        self.phases = {}
        self.samples = self.errors = 0
        self.total_mib = None

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stop.wait(self.interval):
            self.observe()

    def observe(self):
        sample = self.sampler()
        if sample is None:
            with self.lock:
                self.errors += 1
            return
        used, total, processes = sample
        with self.lock:
            self.samples += 1
            self.total_mib = total
            label = "+".join(sorted(set(self.active))) or "idle"
            entry = self.phases.setdefault(label, {"peak_mib": 0, "requests": 0, "processes": []})
            if used >= entry["peak_mib"]:
                entry.update(peak_mib=used, processes=processes, at=time.strftime("%H:%M:%S", time.gmtime()))

    @contextlib.contextmanager
    def phase(self, name):
        with self.lock:
            self.active.append(name)
            self.phases.setdefault(name, {"peak_mib": 0, "requests": 0, "processes": []})["requests"] += 1
        try:
            yield
        finally:
            self.observe()  # one more reading right at the end of the step
            with self.lock:
                self.active.remove(name)

    def report(self):
        with self.lock:
            peaks = {name: dict(entry) for name, entry in self.phases.items()}
            top = max(peaks.items(), key=lambda item: item[1]["peak_mib"], default=(None, {"peak_mib": 0}))
            return {"enabled": True, "total_mib": self.total_mib, "interval_s": self.interval,
                    "samples": self.samples, "sample_errors": self.errors,
                    "overall_peak_mib": top[1]["peak_mib"], "overall_peak_phase": top[0],
                    "phases": peaks}


VRAM = VramMonitor(interval=float(os.environ.get("SUBSYNQ_CLOUD_VRAM_INTERVAL", "0.5"))) if DEVICE == "cuda" else None


def track(name):
    """Context manager naming the current phase; a no-op without a GPU monitor or a name."""
    return VRAM.phase(name) if VRAM is not None and name else contextlib.nullcontext()


def qwen_phase(path):
    return {"/transcribe": "qwen-alignment", "/identify": "qwen-identify"}.get(path)


def ollama_phase(body):
    """`ollama-<model>` for a real generation; None for an unload (keep_alive 0) or bad body."""
    try:
        request = json.loads(body)
    except ValueError:
        return None
    if not isinstance(request, dict) or request.get("keep_alive") == 0 or not request.get("model"):
        return None
    return "ollama-" + str(request["model"])[:60]


def activate_whisper(name):
    """Return the requested model, swapping out the resident one. Caller holds whisper_lock."""
    global whisper, whisper_loaded
    if whisper_loaded == name and whisper is not None:
        return whisper
    from faster_whisper import WhisperModel
    whisper = None
    whisper_loaded = None
    gc.collect()
    whisper = WhisperModel(name, **whisper_arguments())
    whisper_loaded = name
    return whisper


def load_whisper():
    try:
        with whisper_lock:
            activate_whisper(WHISPER_NAME)
        whisper_state.update(status="ready", error=None)
        print(f"Whisper {WHISPER_NAME} redo på CPU", flush=True)
    except Exception as error:  # reported through /health, never hidden
        whisper_state.update(status="error", error=str(error)[:300])
        print(f"Whisper kunde inte laddas: {error!r}", flush=True)


def local_json(port, path, timeout=5):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            return None
        return json.loads(body)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        connection.close()


STATE_DIR = os.environ.get("SUBSYNQ_CLOUD_STATE_DIR", "/root")
# While the boot script is still installing packages, this same file runs under
# the system python with no Whisper (it cannot import faster-whisper yet) and
# only reports progress; the finished gateway replaces it on the same port.
DIAG_ONLY = os.environ.get("SUBSYNQ_CLOUD_DIAG_ONLY") == "1"
LOG_FILES = ("bootstrap.log", "ollama.log", "qwen.log", "gateway.log")


def read_state(name):
    try:
        with open(os.path.join(STATE_DIR, name), encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()[:300] or None
    except OSError:
        return None


def read_states(prefix):
    """The boot script runs parallel tracks, each with its own `<prefix>.<track>` file
    (`step.ollama`, `failed.models`, ...) next to the main `<prefix>` file. Joined for /health."""
    try:
        names = sorted(name for name in os.listdir(STATE_DIR)
                       if name == prefix or name.startswith(prefix + "."))
    except OSError:
        return None
    parts = []
    for name in names:
        value = read_state(name)
        if value:
            parts.append(value if name == prefix else f"{name[len(prefix) + 1:]}: {value}")
    return " | ".join(parts)[:600] or None


def log_tails(limit=3000):
    """Last characters of each boot/service log, to show WHY a server never got ready."""
    tails = {}
    for name in LOG_FILES:
        try:
            with open(os.path.join(STATE_DIR, name), "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - limit))
                tails[name] = handle.read().decode("utf-8", errors="replace")
        except OSError:
            tails[name] = None
    return tails


def health_report():
    ram = available_memory_bytes()
    qwen = local_json(QWEN_PORT, "/health")
    qwen_ready = isinstance(qwen, dict) and qwen.get("status") == "ready"
    tags = local_json(OLLAMA_PORT, "/api/tags")
    present = {item.get("name") for item in (tags or {}).get("models", []) if isinstance(item, dict)}
    missing = [name for name in REQUIRED_MODELS if name not in present]
    ollama_ready = tags is not None and not missing
    report = {
        "whisper": whisper_state["status"], "whisper_model": WHISPER_NAME,
        "qwen": qwen_ready, "ollama": ollama_ready, "missing_models": missing,
        "ram_bytes": ram, "min_ram_bytes": MIN_RAM_BYTES, "device": DEVICE,
    }
    if whisper_state["error"]:
        report["error"] = whisper_state["error"]
    report["bootstrap_step"] = read_states("step")
    failed = read_states("failed")
    if failed:
        report["bootstrap_error"] = failed
    gpu = gpu_info() if DEVICE == "cuda" else None
    if DEVICE == "cuda":
        report.update(gpu_name=gpu[0] if gpu else None, vram_bytes=gpu[1] if gpu else None,
                      min_vram_bytes=MIN_VRAM_BYTES)
    if failed:
        report["status"] = "bootstrap_failed"
    elif DEVICE == "cuda" and gpu is None:
        # Never let Ollama or Qwen quietly fall back to CPU on a "GPU" rental.
        report["status"] = "no_gpu"
    elif DEVICE == "cuda" and gpu[1] < MIN_VRAM_BYTES:
        report["status"] = "insufficient_vram"
    elif ram is not None and ram < MIN_RAM_BYTES:
        report["status"] = "insufficient_memory"
    elif whisper_state["status"] == "error":
        report["status"] = "error"
    elif whisper_state["status"] == "ready" and qwen_ready and ollama_ready:
        report["status"] = "ready"
    else:
        report["status"] = "loading"
    return report


def parse_options(header):
    if not header:
        return {}
    options = json.loads(header)
    if not isinstance(options, dict):
        raise ValueError("Ogiltiga Whisper-alternativ.")
    for name, value in options.items():
        allowed = ALLOWED_OPTIONS.get(name)
        if allowed is None or not isinstance(value, allowed) or (
                isinstance(value, bool) and bool not in allowed):
            raise ValueError(f"Whisper-alternativet {name!r} är inte tillåtet.")
    return options


def serialize_segments(segments):
    result = []
    for segment in segments:
        result.append({
            "start": float(segment.start), "end": float(segment.end), "text": str(segment.text),
            "no_speech_prob": float(segment.no_speech_prob), "avg_logprob": float(segment.avg_logprob),
            "words": [{
                "start": None if word.start is None else float(word.start),
                "end": None if word.end is None else float(word.end),
                "word": str(word.word), "probability": float(word.probability),
            } for word in (segment.words or [])],
        })
    return result


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, payload, raw=None, content_type="application/json; charset=utf-8"):
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        """Return the path after `/<token>`, or None (answered 404) if the token is wrong."""
        prefix = f"/{TOKEN}"
        path = self.path
        if (not TOKEN or not (path == prefix or path.startswith(prefix + "/")) or
                not hmac.compare_digest(path[:len(prefix)].encode(), prefix.encode())):
            self.respond(404, {"status": "error", "error": "Not found"})
            return None
        return path[len(prefix):] or "/"

    def forward(self, method, port, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1000)
        try:
            headers = {name: self.headers[name] for name in FORWARD_HEADERS if self.headers.get(name)}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.respond(response.status, None, raw=data,
                         content_type=response.getheader("Content-Type", "application/json"))
        except (OSError, http.client.HTTPException) as error:
            self.respond(502, {"status": "error", "error": f"Backend svarade inte: {str(error)[:200]}"})
        finally:
            connection.close()

    def read_body(self, limit):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= limit:
            raise ValueError("Ogiltig storlek.")
        return self.rfile.read(length)

    def do_GET(self):
        path = self.route()
        if path is None:
            return
        if path == "/health":
            self.respond(200, health_report())
        elif path == "/qwen/health":
            self.forward("GET", QWEN_PORT, "/health")
        elif path == "/log":
            self.respond(200, {"status": "ok", "logs": log_tails()})
        elif path == "/vram":
            self.respond(200, VRAM.report() if VRAM is not None else {"enabled": False})
        else:
            self.respond(404, {"status": "error", "error": "Not found"})

    def do_POST(self):
        path = self.route()
        if path is None:
            return
        try:
            if path == "/transcribe":
                self.transcribe()
            elif path.startswith("/qwen/"):
                with track(qwen_phase(path[len("/qwen"):])):
                    self.forward("POST", QWEN_PORT, path[len("/qwen"):], self.read_body(MAX_AUDIO_BYTES))
            elif path.startswith("/ollama/"):
                body = self.read_body(16 * 1024 * 1024)
                with track(ollama_phase(body)):
                    self.forward("POST", OLLAMA_PORT, path[len("/ollama"):], body)
            else:
                self.respond(404, {"status": "error", "error": "Not found"})
        except ValueError as error:
            self.respond(400, {"status": "error", "error": str(error)[:300]})

    def transcribe(self):
        payload = self.read_body(MAX_AUDIO_BYTES)
        if not payload:
            raise ValueError("Ogiltig ljudstorlek.")
        options = parse_options(self.headers.get("X-SubSynq-Options"))
        name = self.headers.get("X-SubSynq-Whisper-Model") or WHISPER_NAME
        if name not in ALLOWED_WHISPER_MODELS:
            raise ValueError(f"Whisper-modellen {name!r} är inte tillåten.")
        if whisper_state["status"] != "ready":
            self.respond(503, {"status": "error", "error": f"Whisper är inte redo ({whisper_state['status']})."})
            return
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
                audio.write(payload)
                audio.flush()
                with whisper_lock, track(f"whisper-{name}"):
                    model = activate_whisper(name)
                    segments, info = model.transcribe(audio.name, **options)
                    rows = serialize_segments(segments)
        except Exception as error:
            self.respond(500, {"status": "error", "error": str(error)[:500]})
            return
        self.respond(200, {"status": "ok", "language": info.language, "segments": rows})

    def log_message(self, _format, *_args):
        return


def build_server(address, cert_file, key_file):
    """HTTPS-only server. There is deliberately no plain-HTTP mode: the
    worker pins the exact certificate it generated for this rental."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file, key_file)
    server = ThreadingHTTPServer(address, Handler)
    # The handshake then runs in the request's own thread on first read, so a
    # stalled client can never block the accept loop.
    server.socket = context.wrap_socket(server.socket, server_side=True,
                                        do_handshake_on_connect=False)
    return server


if __name__ == "__main__":
    if not TOKEN or len(TOKEN) < 16:
        raise SystemExit("SUBSYNQ_CLOUD_TOKEN saknas eller är för kort; tjänsten startas inte.")
    cert_file, key_file = os.environ.get("SUBSYNQ_CLOUD_TLS_CERT"), os.environ.get("SUBSYNQ_CLOUD_TLS_KEY")
    if not cert_file or not key_file:
        raise SystemExit("SUBSYNQ_CLOUD_TLS_CERT/KEY saknas; tjänsten startas aldrig utan kryptering.")
    server = build_server(("0.0.0.0", PORT), cert_file, key_file)
    if not DIAG_ONLY:
        threading.Thread(target=load_whisper, daemon=True).start()
        if VRAM is not None:
            VRAM.start()
    print(f"Molntjänsten lyssnar på port {PORT} (HTTPS, {DEVICE})", flush=True)
    server.serve_forever()
