"""Small local HTTP service for Qwen3-ASR with forced word alignment.

The model is loaded only when a transcription is requested and released after an
idle period.  This keeps the service visible to SubSynq without permanently
occupying the computer's RAM while no subtitle job is running.
"""
import gc
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner


HOST = "0.0.0.0"
PORT = int(os.environ.get("SUBSYNQ_QWEN_PORT", "8091"))
ASR_MODEL = os.environ.get("SUBSYNQ_QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
ALIGNER_MODEL = os.environ.get(
    "SUBSYNQ_QWEN_ALIGNER_MODEL", "Qwen/Qwen3-ForcedAligner-0.6B"
)
DEVICE = os.environ.get("SUBSYNQ_QWEN_DEVICE", "cpu")
DTYPE_NAME = os.environ.get(
    "SUBSYNQ_QWEN_DTYPE", "float32" if DEVICE == "cpu" else "float16"
)
DTYPES = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}
IDLE_SECONDS = max(60, int(os.environ.get("SUBSYNQ_QWEN_IDLE_SECONDS", "60")))
MAX_AUDIO_BYTES = 20 * 1024 * 1024
# 1 (default): every /transcribe and /identify runs in its own short-lived process that never
# holds the ASR model and the forced aligner together (see LazyAligner), so memory is always
# returned to the machine. 0 restores the old in-process behaviour (models stay loaded between
# requests, faster, but ~11.5 GiB peak and freed memory is not reliably returned).
SEQUENTIAL = os.environ.get("SUBSYNQ_QWEN_SEQUENTIAL", "1") == "1"
# Shorter than the worker's own 900 s request timeout, so a stuck child is reported properly.
CHILD_TIMEOUT_SECONDS = max(60, int(os.environ.get("SUBSYNQ_QWEN_CHILD_TIMEOUT", "840")))

if DTYPE_NAME not in DTYPES:
    raise ValueError(f"Unsupported dtype: {DTYPE_NAME}")

model = None
last_used = 0.0
model_lock = threading.Lock()


def release_model():
    global model
    model = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass


def free_memory():
    gc.collect()
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass


class LazyAligner:
    """Stands in for Qwen3ForcedAligner so ASR and aligner are never resident together.

    qwen_asr's transcribe(return_time_stamps=True) runs ASR over ALL chunks first and
    only then calls forced_aligner.align(). When align() is first called the ASR weights
    are therefore finished with: free them, load the real aligner, then delegate. Peak
    memory becomes max(ASR, aligner) instead of ASR + aligner (float32: ~9 GiB -> ~7 GiB
    of weights), with identical results. The price is that the ASR model is reloaded for
    the next request (see transcribe handling below)."""
    def __init__(self, owner):
        self.owner = owner
        self.real = None

    def align(self, *args, **kwargs):
        if self.real is None:
            print(f"Freeing {ASR_MODEL} before loading the aligner", flush=True)
            self.owner.model = None
            free_memory()
            print(f"Loading {ALIGNER_MODEL} on {DEVICE}", flush=True)
            self.real = Qwen3ForcedAligner.from_pretrained(
                ALIGNER_MODEL, dtype=DTYPES[DTYPE_NAME], device_map=DEVICE)
        return self.real.align(*args, **kwargs)


def load_model(*, with_alignment=True):
    global model, last_used
    if model is None:
        print(f"Loading {ASR_MODEL} on {DEVICE}", flush=True)
        common = {"dtype": DTYPES[DTYPE_NAME], "device_map": DEVICE}
        model = Qwen3ASRModel.from_pretrained(
            ASR_MODEL,
            max_inference_batch_size=1,
            max_new_tokens=256,
            **common,
        )
        last_used = time.monotonic()
    if with_alignment and model.forced_aligner is None:
        if SEQUENTIAL:
            model.forced_aligner = LazyAligner(model)
        else:
            print(f"Loading {ALIGNER_MODEL} on {DEVICE}", flush=True)
            model.forced_aligner = Qwen3ForcedAligner.from_pretrained(
                ALIGNER_MODEL, dtype=DTYPES[DTYPE_NAME], device_map=DEVICE)
    return model


def item_value(item, name):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def unload_when_idle():
    global model
    while True:
        time.sleep(30)
        with model_lock:
            if model is not None and time.monotonic() - last_used >= IDLE_SECONDS:
                print("Unloading idle Qwen models", flush=True)
                release_model()


def result_payload(result):
    aligned = getattr(result, "time_stamps", None)
    items = getattr(aligned, "items", aligned or [])
    return {
        "status": "ok",
        "language": result.language,
        "text": result.text.strip(),
        "items": [{
            "text": str(item_value(item, "text") or ""),
            "start": float(item_value(item, "start_time")),
            "end": float(item_value(item, "end_time")),
        } for item in items if item_value(item, "start_time") is not None
          and item_value(item, "end_time") is not None],
    }


def run_child(path, wav_path, language):
    """Serve one /transcribe or /identify request in a fresh process; return the payload."""
    handle, out_path = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    command = [sys.executable, "-u", os.path.abspath(__file__), "--child", path, wav_path,
               language or "", out_path]
    try:
        completed = subprocess.run(command, timeout=CHILD_TIMEOUT_SECONDS)
        try:
            with open(out_path, encoding="utf-8") as saved:
                data = json.load(saved)
        except (OSError, ValueError):
            data = None
        if completed.returncode == 0 and isinstance(data, dict) and data.get("status") == "ok":
            return data
        reason = data.get("error") if isinstance(data, dict) else None
        if not reason:
            reason = f"Qwen-processen avslutades utan svar (kod {completed.returncode}"
            reason += ", troligen minnesbrist)" if completed.returncode in (-9, 137) else ")"
        raise RuntimeError(reason)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Qwen-processen tog för lång tid och avbröts.") from error
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def child_main(arguments):
    """Entry point of the per-request process: load, transcribe, write the payload, exit."""
    path, wav_path, language, out_path = arguments
    try:
        active_model = load_model(with_alignment=path == "/transcribe")
        result = active_model.transcribe(
            audio=wav_path, language=language or None,
            return_time_stamps=path == "/transcribe")[0]
        data = result_payload(result)
        code = 0
    except Exception as error:
        print(f"Qwen transcription error: {error!r}", flush=True)
        data, code = {"status": "error", "error": str(error)[:1000]}, 1
    with open(out_path, "w", encoding="utf-8") as saved:
        json.dump(data, saved, ensure_ascii=False)
    return code


class Handler(BaseHTTPRequestHandler):
    server_version = "SubSynq-Qwen/0.8.135"

    def send_json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path != "/health":
            self.send_json(404, {"status": "not_found"})
            return
        self.send_json(200, {
            "status": "ready", "device": DEVICE, "model_loaded": model is not None,
            "aligner_loaded": model is not None and model.forced_aligner is not None,
            "asr_model": ASR_MODEL, "aligner_model": ALIGNER_MODEL,
        })

    def do_POST(self):
        global last_used
        if self.path == "/unload":
            with model_lock:
                release_model()
            self.send_json(200, {"status": "ready", "model_loaded": False})
            return
        if self.path not in ("/transcribe", "/identify"):
            self.send_json(404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_AUDIO_BYTES:
                raise ValueError("Ogiltig storlek på ljudfönstret.")
            language = self.headers.get("X-SubSynq-Language")
            if language and (len(language) > 40 or not language.replace('-', '').isalpha()):
                raise ValueError("Ogiltig språkledtråd för kontrollavläsningen.")
            payload = self.rfile.read(length)
            with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
                audio.write(payload)
                audio.flush()
                with model_lock:
                    if SEQUENTIAL:
                        # One short-lived process per request: when it exits ALL its memory goes
                        # back to the machine. Freeing weights inside this long-lived process was
                        # measured NOT to work (2.6 GiB stayed behind after one request, and the
                        # next request hit the container limit), see STATUS.
                        response = run_child(self.path, audio.name, language)
                    else:
                        active_model = load_model(with_alignment=self.path == '/transcribe')
                        result = active_model.transcribe(
                            audio=audio.name, language=language,
                            return_time_stamps=self.path == "/transcribe"
                        )[0]
                        response = result_payload(result)
                        last_used = time.monotonic()
            self.send_json(200, response)
        except Exception as error:
            print(f"Qwen transcription error: {error!r}", flush=True)
            self.send_json(500, {"status": "error", "error": str(error)[:1000]})

    def log_message(self, fmt, *args):
        print("Qwen service: " + fmt % args, flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == "--child":
        sys.exit(child_main(sys.argv[2:]))
    threading.Thread(target=unload_when_idle, daemon=True).start()
    print(f"Qwen ASR service listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
