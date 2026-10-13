"""Rent a short-lived NVIDIA instance to run the same ASR HTTP service the
local AMD card already exposes (ai/gpu_service.py), reusing worker.py's
existing RemoteWhisperModel/GPU_ASR_URL contract unchanged. Only the small
per-window audio clips ever cross the network -- never the source video.

Real money: every function that creates or destroys a billable instance
talks to Vast.ai's live API. Nothing here is called automatically; the
caller (the web UI or a manual script) must ask the user first, every time,
until the app's own "Starta molnjobb" button becomes the agreed-on consent
per den interna statusloggen.
"""
import ast
import base64
import io
import json
import os
import re
import tarfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VAST_API = "https://console.vast.ai/api/v0"
# A stock, publicly available CUDA+PyTorch image -- no custom image to build
# or push. gpu_service.py itself has no ROCm-specific code (see den interna statusloggen);
# it only ever calls generic torch.cuda.* APIs, so it runs unmodified here.
# 2.4.0-cuda12.4 was built in 2024, before the Blackwell architecture (RTX
# 50-series) existed, and failed live on a real RTX 5060 Ti with "CUDA
# error: no kernel image is available for execution on the device" --
# confirmed on a real launch, /health worked but /transcribe crashed. This
# newer build ships kernels for Blackwell (sm_120) as well as every older
# architecture already proven working (Titan Xp, RTX 5060 Ti's own
# non-/transcribe health check). NOT yet re-verified against a real
# instance -- do that before trusting this over the untested claim alone.
DEFAULT_IMAGE = "pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime"
SERVICE_PORT = 8766

# Ubuntu's bundled pip can abort a large wheel mid-stream even with its
# connection retries. Retry the install only for transport failures, keep
# completed downloads cached, and preserve deterministic package/TLS errors.
CHAIN_PIP_INSTALL_SCRIPT = '''pip_install() {
  local pip="$1" attempt status log
  shift
  log=$(mktemp)
  for attempt in 1 2 3; do
    echo "pip: försök $attempt/3"
    if "$pip" install --timeout 120 --retries 5 --cache-dir /root/.cache/pip "$@" 2>&1 | tee "$log"; then
      rm -f "$log"
      return 0
    else
      status=${PIPESTATUS[0]}
      [ "$status" -ne 0 ] || status=1
    fi
    if [ "$attempt" -eq 3 ] || ! grep -Eq 'ReadTimeoutError|ConnectTimeoutError|NewConnectionError|ConnectionResetError|ProtocolError|IncompleteRead|Read timed out|Connection reset by peer|Temporary failure in name resolution' "$log"; then
      rm -f "$log"
      return "$status"
    fi
    echo "Tillfälligt nätverksfel: pip försöker igen om $((attempt * 15)) sekunder."
    sleep $((attempt * 15))
  done
}
'''


BOOTSTRAP_TEMPLATE = '''set -e -o pipefail
step() { echo "$1" > /root/step; echo "== $(date -u +%T) $1"; }
trap 'echo "misslyckades vid: $(cat /root/step)" > /root/failed' ERR
@PIP_INSTALL@
export DEBIAN_FRONTEND=noninteractive
export HF_HOME=/root/huggingface HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false HF_HUB_DISABLE_XET=1 HF_HUB_DOWNLOAD_TIMEOUT=120
step 'apt: systempaket'
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl zstd libsndfile1 sox python3 python3-pip python3-venv pciutils
# As soon as python3 exists, a diagnostics-only copy of the gateway answers /health and /log
# (standard library only), so progress and failures are visible from outside while the
# tracks below run. It is replaced by the real gateway once they are done.
@GATEWAY_ENV@ SUBSYNQ_CLOUD_DIAG_ONLY=1 nohup python3 -u /root/cloud_cpu_service.py > /root/gateway.log 2>&1 &
DIAG_PID=$!

# One subshell per track with its own step and failure file (errtrace so the trap also
# fires inside the track function).
run_track() {
  local name="$1" fn="$2"
  (
    set -eE -o pipefail
    TRACK="$name"
    tstep() { echo "$1" > "/root/step.$TRACK"; echo "== $(date -u +%T) [$TRACK] $1"; }
    trap 'echo "misslyckades vid: $(cat "/root/step.$TRACK")" > "/root/failed.$TRACK"' ERR
    "$fn"
    tstep 'klart'
  ) &
  eval "PID_$name=$!"
}

# Two virtualenvs, like the two separate Docker images locally (their pins conflict).
track_qwenpy() {
  tstep 'pip: Qwen-miljö (torch)'
  python3 -m venv /root/qwen-venv
  pip_install /root/qwen-venv/bin/pip torch==2.9.1 --index-url https://download.pytorch.org/whl/@TORCH_INDEX@
  tstep 'pip: qwen-asr'
  pip_install /root/qwen-venv/bin/pip -c /root/constraints-qwen.txt qwen-asr==0.0.6
}
track_whisperpy() {
  tstep 'pip: Whisper-miljö'
  python3 -m venv /root/whisper-venv
  pip_install /root/whisper-venv/bin/pip -c /root/constraints-worker.txt faster-whisper==1.2.1 av==18.1.0
}
track_ollama() {
  tstep 'installerar Ollama'
  curl -fsSL https://ollama.com/install.sh | sh
  tstep 'startar Ollama'
  OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 nohup ollama serve > /root/ollama.log 2>&1 &
  for i in $(seq 1 90); do curl -fs http://127.0.0.1:11434/api/tags > /dev/null && break; sleep 2; done
@PULLS@
}
# Same repositories/files as faster_whisper.download_model, with visible progress and a
# bounded number of concurrent HTTP downloads. Qwen is fetched here too, not in its first request.
hf_get() {
  tstep "hämtar $1"
  /root/hf-venv/bin/python -u - "$@" <<'HF_EOF'
import sys
from huggingface_hub import snapshot_download
repo, patterns = sys.argv[1], sys.argv[2:] or None
print(f'Hämtar {repo}', flush=True)
snapshot_download(repo, max_workers=1, allow_patterns=patterns)
print(f'{repo}: hämtning klar', flush=True)
HF_EOF
}
track_whispermodels() {
  for model in large-v3 large-v2; do
    hf_get Systran/faster-whisper-$model config.json preprocessor_config.json model.bin tokenizer.json 'vocabulary.*'
  done
}
track_qwenmodels() {
  hf_get Qwen/Qwen3-ASR-1.7B
  hf_get Qwen/Qwen3-ForcedAligner-0.6B
}

step 'pip: Hugging Face-klient'
python3 -m venv /root/hf-venv
pip_install /root/hf-venv/bin/pip huggingface_hub
run_track qwenpy track_qwenpy
run_track whisperpy track_whisperpy
run_track ollama track_ollama
run_track whispermodels track_whispermodels
run_track qwenmodels track_qwenmodels
step 'väntar på Python-miljöer och modeller'
wait "$PID_qwenpy"
wait "$PID_whisperpy"
wait "$PID_whispermodels"
wait "$PID_qwenmodels"
step 'startar Qwen och molntjänsten'
@QWEN_ENV@ SUBSYNQ_QWEN_PORT=8091 nohup /root/qwen-venv/bin/python -u /root/qwen_asr_service.py > /root/qwen.log 2>&1 &
kill $DIAG_PID 2>/dev/null || true
sleep 2
@GATEWAY_ENV@ nohup /root/whisper-venv/bin/python -u /root/cloud_cpu_service.py > /root/gateway.log 2>&1 &
step 'väntar på Ollama och Gemma-modellerna'
wait "$PID_ollama"
step 'klart'
'''


def _request(method, path, api_key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        # Vast.ai answers 301/308 (which urllib does not follow for PUT/DELETE) unless
        # the path ends in a slash.
        f"{VAST_API}{path.rstrip('/')}/", data=data, method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error)
        except (ValueError, AttributeError):
            detail = {"error": str(error)}
        raise RuntimeError(f"Vast.ai svarade med fel {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Kunde inte nå Vast.ai: {error}") from error


def build_onstart_script(gpu_service_source):
    """Embed ai/gpu_service.py's own source directly in the boot script --
    no git/curl access to the (private) repo needed from the rented box.

    Runs the service with nohup/& and lets the onstart script itself exit --
    a foreground command as the script's last line was observed live to
    block the base image's own ready-detection (its SSH daemon never got a
    chance to start), leaving the instance stuck reporting "loading"
    forever even though the container was actually up and installing fine."""
    return (
        "set -e\n"
        "apt-get update && apt-get install -y --no-install-recommends libsndfile1\n"
        # Two separate pip install commands, matching the proven working
        # the AMD ROCm image build exactly -- a single combined install forces
        # pip's resolver to reconcile every pin together in one pass, which
        # fails outright: qwen-asr==0.0.6 hard-requires accelerate==1.12.0,
        # conflicting with the accelerate==1.10.1 pinned here (observed live:
        # "ResolutionImpossible", which aborted the whole script under set -e
        # before gpu_service.py was even written to disk). Installing qwen-asr
        # in its own second pass lets it bring its own accelerate without
        # pip re-validating the already-installed transformers stack against it.
        "pip install --no-cache-dir accelerate==1.10.1 safetensors==0.6.2 "
        "soundfile==0.13.1 transformers==4.57.6\n"
        "pip install --no-cache-dir qwen-asr==0.0.6\n"
        "cat > /root/gpu_service.py <<'SUBSYNQ_GPU_SERVICE_EOF'\n"
        f"{gpu_service_source}\n"
        "SUBSYNQ_GPU_SERVICE_EOF\n"
        "nohup python3 -u /root/gpu_service.py > /root/gpu_service.log 2>&1 &\n"
        "disown\n"
    )


def create_instance(api_key, offer_id, onstart_script, *, image=DEFAULT_IMAGE, disk_gb=30):
    """Rent the given offer (from cloud_market.catalog()'s 'id') and boot the
    ASR service on it. Bills from the moment this call succeeds."""
    result = _request("PUT", f"/asks/{offer_id}", api_key, {
        "image": image,
        "disk": disk_gb,
        "label": "subsynq-gpu-asr",
        "target_state": "running",
        "env": f"-p {SERVICE_PORT}:{SERVICE_PORT}",
        "onstart": onstart_script,
    })
    if not result.get("success"):
        raise RuntimeError(f"Vast.ai avvisade beställningen: {result}")
    return result["new_contract"]


def show_instance(api_key, instance_id):
    """Read-only status/IP lookup. Free to call as often as needed."""
    result = _request("GET", f"/instances/{instance_id}", api_key)
    return result.get("instances")


def public_service_url(instance):
    """None until Vast.ai has actually mapped the requested port -- keep
    polling show_instance() until this stops returning None."""
    if not instance or instance.get("actual_status") != "running":
        return None
    ip = instance.get("public_ipaddr")
    ports = instance.get("ports") or {}
    mapped = ports.get(f"{SERVICE_PORT}/tcp") if isinstance(ports, dict) else None
    external_port = mapped[0]["HostPort"] if mapped else None
    if not ip or not external_port:
        return None
    return f"http://{ip}:{external_port}"


def request_logs(api_key, instance_id, *, tail=1000):
    """Ask Vast.ai to collect the container's own stdout/stderr (which is
    where our onstart script's apt-get/pip/gpu_service.py output all lands)
    and return an S3 URL to fetch it from. Read-only, no billing effect."""
    result = _request("PUT", f"/instances/request_logs/{instance_id}", api_key, {"tail": int(tail)})
    if not result.get("success"):
        raise RuntimeError(f"Vast.ai kunde inte hämta loggar: {result}")
    return result["result_url"]


def execute_command(api_key, instance_id, command):
    """Run a short diagnostic command on the instance (e.g. 'cat
    /root/gpu_service.log', 'ps aux') without needing an SSH keypair. Returns
    an S3 URL with the output -- poll it a few seconds after this returns."""
    if len(command) > 512:
        raise ValueError("Kommandot är för långt (max 512 tecken).")
    result = _request("PUT", f"/instances/command/{instance_id}", api_key, {"command": command})
    if not result.get("success"):
        raise RuntimeError(f"Vast.ai kunde inte köra kommandot: {result}")
    return result["result_url"]


def set_instance_state(api_key, instance_id, state):
    """state is 'stopped' or 'running'. execute_command() only works on a
    stopped instance (Vast.ai requires SSH for a running one) -- stopping
    also drops the GPU-hour cost to near-zero (disk only) while keeping the
    instance around long enough to read its log before destroying it."""
    if state not in ("stopped", "running"):
        raise ValueError("state måste vara 'stopped' eller 'running'.")
    result = _request("PUT", f"/instances/{instance_id}", api_key, {"state": state})
    if not result.get("success"):
        raise RuntimeError(f"Vast.ai kunde inte ändra instansens tillstånd: {result}")
    return True


def destroy_instance(api_key, instance_id):
    """Stop billing. Irreversible -- matches Vast.ai's own warning."""
    result = _request("DELETE", f"/instances/{instance_id}", api_key)
    if not result.get("success"):
        raise RuntimeError(f"Vast.ai kunde inte avsluta instansen: {result}")
    return True


# --- Hela AI-kedjan (Whisper, Qwen, Gemma) på en hyrd server: CPU eller GPU ---

# NOT verified against a real rented instance (no paid test was run when this
# was written). CPU rentals use a plain Ubuntu image; GPU rentals use NVIDIA's
# CUDA 12.8 runtime image (cuBLAS/cuDNN for CTranslate2, Blackwell-capable).
# Everything else is installed by the boot script below.
CPU_IMAGE = "ubuntu:24.04"
GPU_CHAIN_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
CHAIN_DISK_GB = 80
# Vast.ai answers "invalid_args" for a boot script over 16 384 characters.
MAX_ONSTART_CHARS = 16000
CPU_DISK_GB = CHAIN_DISK_GB
CHAIN_DEVICES = ("cpu", "cuda")
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}")
_SAFE_MODEL = re.compile(r"[A-Za-z0-9._:/-]{1,80}")


def compact_shell(text):
    """The shell script without full-line comments and blank lines (only the copy sent to the
    server; there are no `#` lines inside the script's heredocs, which a test guards)."""
    return "".join(line + "\n" for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith("#"))


def compact_python(source):
    """Same program without comments and docstrings, to fit Vast.ai's boot-script limit.

    Only the copy sent to the rented server is compacted; the files in the repository are
    untouched. ast.unparse keeps the behaviour (it is what the interpreter would run), but
    line numbers in a server traceback no longer match the repository file."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree) + "\n"


def chain_image(device):
    return GPU_CHAIN_IMAGE if device == "cuda" else CPU_IMAGE


def require_prepared_image():
    image = os.environ.get('SUBSYNQ_CLOUD_IMAGE', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}', image):
        raise RuntimeError('Molnhyra är spärrad: en verifierad, färdig molnimage med '
                           'program och modeller saknas. Ingen server hyrdes och ingen kostnad startades.')
    from cloud_image import verify_image
    try:
        verify_image(image)
    except Exception as error:
        raise RuntimeError('Molnhyra är spärrad: den färdiga imagen kunde inte verifieras '
                           'i containerregistret. Ingen server hyrdes och ingen kostnad startades.') from error
    return image


def build_chain_onstart_script(token, ollama_models, *, device, tls_cert, tls_key,
                               gateway_source, qwen_source,
                               qwen_constraints, whisper_constraints, prepared=False,
                               model_aliases=None, min_vram_gib=None):
    """Boot script for a rental running the whole AI chain on `device`
    ("cpu" or "cuda").

    Everything long (apt, pip, Ollama, ~20 GB of model downloads) happens in a
    backgrounded bootstrap script so onstart itself returns at once -- a
    foreground last command was observed live to block the base image's own
    ready-detection (see build_onstart_script). Readiness is therefore decided
    only by the gateway's /health, which says `ready` once Whisper, Qwen and
    every required Ollama model actually answer (and, on cuda, a GPU with
    enough VRAM is really present)."""
    if device not in CHAIN_DEVICES:
        raise ValueError("Ogiltig enhet för molntjänsten.")
    for pem, label in ((tls_cert, "CERTIFICATE"), (tls_key, "PRIVATE KEY")):
        if not isinstance(pem, str) or f"-----BEGIN {label}-----" not in pem:
            raise ValueError("Ogiltigt TLS-certifikat eller -nyckel för molntjänsten.")
    if not _SAFE_TOKEN.fullmatch(token or ""):
        raise ValueError("Ogiltig token för molntjänsten.")
    models = []
    for name in ollama_models:
        if not _SAFE_MODEL.fullmatch(name or ""):
            raise ValueError(f"Ogiltigt modellnamn för Ollama: {name!r}")
        if name not in models:
            models.append(name)
    if not models:
        raise ValueError("Minst en Ollama-modell måste anges.")
    gpu = device == "cuda"
    if gpu:
        # The shared pin file names the CPU-only torch wheel; the CUDA wheel is
        # the same release with a different local version.
        if "torch==2.9.1+cpu" not in qwen_constraints:
            raise ValueError("Qwen-beroendena saknar den förväntade torch-versionen.")
        qwen_constraints = qwen_constraints.replace("torch==2.9.1+cpu", "torch==2.9.1")
    pulls = "\n".join(f"ollama pull {name}" for name in models)
    files = {
        "/root/cloud_cpu_service.py": compact_python(gateway_source),
        "/root/qwen_asr_service.py": compact_python(qwen_source),
        "/root/constraints-qwen.txt": qwen_constraints,
        "/root/constraints-worker.txt": whisper_constraints,
        "/root/server.crt": tls_cert,
        "/root/server.key": tls_key,
    }
    torch_index = "cu128" if gpu else "cpu"
    # SEQUENTIAL=0 keeps the models loaded between requests on the rented server (plenty of
    # memory there; the one-process-per-request mode exists for the shared home server).
    qwen_env = ("SUBSYNQ_QWEN_DEVICE=cuda SUBSYNQ_QWEN_DTYPE=float16 SUBSYNQ_QWEN_SEQUENTIAL=0" if gpu
                else "SUBSYNQ_QWEN_DEVICE=cpu SUBSYNQ_QWEN_DTYPE=float32 SUBSYNQ_QWEN_SEQUENTIAL=0")
    gateway_env = (f"SUBSYNQ_CLOUD_TOKEN={token} SUBSYNQ_CLOUD_PORT={SERVICE_PORT} "
                   f"SUBSYNQ_CLOUD_DEVICE={device} "
                   "SUBSYNQ_CLOUD_TLS_CERT=/root/server.crt SUBSYNQ_CLOUD_TLS_KEY=/root/server.key "
                   f"SUBSYNQ_CLOUD_OLLAMA_MODELS={','.join(models)}")
    # The worker keeps asking for its usual model names; the rented server answers with the (larger) ones it pulled.
    aliases = []
    for local, remote in (model_aliases or {}).items():
        if not (_SAFE_MODEL.fullmatch(local or "") and _SAFE_MODEL.fullmatch(remote or "")):
            raise ValueError(f"Ogiltigt modellnamn för Ollama: {local!r} / {remote!r}")
        aliases.append(f"{local}={remote}")
    if aliases:
        gateway_env += f" SUBSYNQ_CLOUD_MODEL_ALIASES={','.join(aliases)}"
    if min_vram_gib is not None:
        if not isinstance(min_vram_gib, (int, float)) or isinstance(min_vram_gib, bool) or not 1 <= min_vram_gib <= 200:
            raise ValueError("Ogiltig lägsta VRAM för molntjänsten.")
        gateway_env += f" SUBSYNQ_CLOUD_MIN_VRAM_GIB={min_vram_gib:g}"
    pulls = "\n".join(f"  tstep 'hämtar {name}'\n  ollama pull {name}" for name in models)
    # The ~40 GB of packages and models are fetched by FOUR parallel tracks instead of one
    # long chain (a measured Vast rental spent 31 minutes just installing, serially):
    #   qwenpy    pip: torch + qwen-asr           -> /root/qwen-venv
    #   whisperpy pip: faster-whisper             -> /root/whisper-venv
    #   ollama    install, serve, pull the Gemma models
    #   models    Whisper v3/v2 and Qwen ASR/aligner from Hugging Face (so Qwen no longer
    #             downloads inside its first request)
    # Each track keeps its own /root/step.<track> and /root/failed.<track>; /health shows all.
    bootstrap = BOOTSTRAP_TEMPLATE.replace("@TORCH_INDEX@", torch_index).replace(
        "@PULLS@", pulls).replace("@GATEWAY_ENV@", gateway_env).replace(
        "@QWEN_ENV@", qwen_env).replace("@PIP_INSTALL@", CHAIN_PIP_INSTALL_SCRIPT)
    bootstrap = compact_shell(bootstrap)
    if prepared:
        bootstrap = (
            "set -e -o pipefail\n"
            "step() { echo \"$1\" > /root/step; echo \"== $(date -u +%T) $1\"; }\n"
            "trap 'echo \"misslyckades vid: $(cat /root/step)\" > /root/failed' ERR\n"
            "export HF_HOME=/models/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "
            "HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false\n"
            "step 'kontrollerar färdiga offline-modeller'\n"
            "test -f /opt/subsynq-prepared\n"
            "python /ai/cloud_preflight.py\n"
            "step 'startar Ollama och Qwen från färdig image'\n"
            "OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 OLLAMA_NUM_PARALLEL=1 "
            "OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_MODELS=/models/ollama/models "
            "nohup ollama serve > /root/ollama.log 2>&1 &\n"
            f"{qwen_env} SUBSYNQ_QWEN_PORT=8091 HF_HUB_CACHE=/models/huggingface/hub "
            "LD_LIBRARY_PATH=/opt/qwen/lib:${LD_LIBRARY_PATH:-} "
            "nohup /opt/qwen/bin/python -u /root/qwen_asr_service.py > /root/qwen.log 2>&1 &\n"
            "step 'startar molntjänsten från färdig image'\n"
            f"{gateway_env} HF_HUB_CACHE=/models "
            "nohup python -u /root/cloud_cpu_service.py > /root/gateway.log 2>&1 &\n"
        )
    files["/root/bootstrap.sh"] = bootstrap
    # Vast.ai rejects a boot script over 16 384 characters, so the files (and
    # the bootstrap script itself) are sent as one gzip'd tar, base64-encoded,
    # and unpacked on the server.
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz", compresslevel=9) as bundle:
        for path, source in files.items():
            data = source.encode("utf-8")
            info = tarfile.TarInfo(name=path.rsplit("/", 1)[1])
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            bundle.addfile(info, io.BytesIO(data))
    encoded = base64.encodebytes(archive.getvalue()).decode("ascii")
    parts = ["mkdir -p /root\numask 077\n",
             f"base64 -d > /root/bundle.tgz <<'SUBSYNQ_BUNDLE_EOF'\n{encoded}SUBSYNQ_BUNDLE_EOF\n",
             "tar -xzf /root/bundle.tgz -C /root && rm -f /root/bundle.tgz\n"]
    parts.append("nohup bash /root/bootstrap.sh > /root/bootstrap.log 2>&1 &\ndisown\n"
                 # Mirror the progress into the container log too (best effort), so the
                 # provider's log view shows it even if the gateway never comes up.
                 "nohup tail -n +1 -F /root/bootstrap.log > /proc/1/fd/1 2>/dev/null &\ndisown\n")
    script = "".join(parts)
    if len(script) > MAX_ONSTART_CHARS:
        raise ValueError(f"Startskriptet är {len(script)} tecken, över gränsen {MAX_ONSTART_CHARS}.")
    return script


# --- Runpod (REST API) och leverantörsneutral fasad för hela kedjan ---

RUNPOD_API = "https://rest.runpod.io/v1"
RUNPOD_CPU_VCPUS = 8
# Runpod runs the pod's own command as the container's main process, so the
# boot script must stay alive after starting its background services.
RUNPOD_KEEPALIVE = "exec tail -f /root/bootstrap.log\n"
# The GPU wheels need a driver that supports CUDA 12.8.
RUNPOD_CUDA_VERSIONS = ["12.8", "12.9", "13.0"]


def _runpod_request(method, path, api_key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        f"{RUNPOD_API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": "SubSynq/0.8.158"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as error:
        try:
            detail = json.load(error)
        except (ValueError, AttributeError):
            detail = {"error": str(error)}
        raise RuntimeError(f"Runpod svarade med fel {error.code}: {detail}") from error
    except (URLError, ValueError) as error:
        raise RuntimeError(f"Kunde inte nå Runpod: {error}") from error


def runpod_create_pod(api_key, offer_id, onstart_script, *, device, image=None,
                      disk_gb=CHAIN_DISK_GB, vcpus=RUNPOD_CPU_VCPUS):
    """Create a Secure Cloud pod running the boot script. Bills from the moment
    this succeeds. `offer_id` is a GPU type id ('cuda') or a CPU flavor ('cpu').
    Returns (pod id, cost per hour)."""
    if device not in CHAIN_DEVICES:
        raise ValueError("Ogiltig enhet för molntjänsten.")
    body = {
        "name": "subsynq-chain",
        "imageName": image or chain_image(device),
        "containerDiskInGb": disk_gb,
        "volumeInGb": 0,
        "ports": [f"{SERVICE_PORT}/tcp"],
        "cloudType": "SECURE",
        "supportPublicIp": True,
        "dockerStartCmd": ["bash", "-c", onstart_script + RUNPOD_KEEPALIVE],
    }
    if device == "cuda":
        body.update(computeType="GPU", gpuTypeIds=[offer_id], gpuCount=1,
                    allowedCudaVersions=RUNPOD_CUDA_VERSIONS)
    else:
        body.update(computeType="CPU", cpuFlavorIds=[offer_id], vcpuCount=vcpus)
    result = _runpod_request("POST", "/pods", api_key, body)
    pod_id = result.get("id")
    if not isinstance(pod_id, str) or not pod_id:
        raise RuntimeError(f"Runpod avvisade beställningen: {result}")
    cost = result.get("costPerHr")
    return pod_id, float(cost) if isinstance(cost, (int, float)) else None


def runpod_show_pod(api_key, pod_id):
    """Read-only status lookup. Free to call as often as needed."""
    return _runpod_request("GET", f"/pods/{pod_id}", api_key)


def runpod_service_url(pod):
    """None until the pod runs AND Runpod has mapped the TCP port to a public IP."""
    if not isinstance(pod, dict) or pod.get("desiredStatus") != "RUNNING":
        return None
    ip, mappings = pod.get("publicIp"), pod.get("portMappings")
    external = mappings.get(str(SERVICE_PORT)) if isinstance(mappings, dict) else None
    if not ip or not external:
        return None
    return f"http://{ip}:{external}"


def runpod_destroy_pod(api_key, pod_id):
    """Terminate the pod: compute AND container disk stop billing."""
    _runpod_request("DELETE", f"/pods/{pod_id}", api_key)
    return True


PROVIDERS_WITH_CHAIN = ("vast", "runpod")


def create_chain_instance(provider, api_key, offer, onstart_script, *, device, image=None):
    """Rent the chosen offer and boot the whole-chain service on it.
    Returns (provider-side instance id, cost per hour or None)."""
    if provider == "vast":
        instance_id = create_instance(
            api_key, int(offer["id"]), onstart_script,
            image=image or chain_image(device), disk_gb=CHAIN_DISK_GB)
        return instance_id, offer.get("price")
    if provider == "runpod":
        return runpod_create_pod(api_key, offer["id"], onstart_script, device=device, image=image)
    raise ValueError("Ogiltig leverantör.")


def show_chain_instance(provider, api_key, instance_id):
    if provider == "vast":
        return show_instance(api_key, instance_id)
    if provider == "runpod":
        return runpod_show_pod(api_key, instance_id)
    raise ValueError("Ogiltig leverantör.")


def chain_service_url(provider, instance):
    if provider == "vast":
        return public_service_url(instance)
    if provider == "runpod":
        return runpod_service_url(instance)
    raise ValueError("Ogiltig leverantör.")


def destroy_chain_instance(provider, api_key, instance_id):
    if provider == "vast":
        return destroy_instance(api_key, instance_id)
    if provider == "runpod":
        return runpod_destroy_pod(api_key, instance_id)
    raise ValueError("Ogiltig leverantör.")


def stored_instance_id(provider, instance_id):
    """What the job row keeps: Vast's integer as before, 'runpod:<id>' for Runpod
    so a restarted worker knows which provider to ask to destroy it."""
    return instance_id if provider == "vast" else f"{provider}:{instance_id}"


def split_stored_instance_id(value):
    if isinstance(value, str) and ":" in value:
        provider, _, instance_id = value.partition(":")
        return provider, instance_id
    return "vast", value
