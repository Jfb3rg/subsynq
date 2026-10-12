"""Run a bounded Qwen3-ASR comparison on the saved 23-second samples."""

import json
import os
import platform
import time
from importlib.metadata import version
from pathlib import Path

import torch
from qwen_asr import Qwen3ASRModel


MODEL_NAME = os.environ.get("SUBSYNQ_QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-0.6B")
DEVICE = os.environ.get("SUBSYNQ_QWEN_DEVICE", "cpu")
DTYPE_NAME = os.environ.get(
    "SUBSYNQ_QWEN_DTYPE", "float32" if DEVICE == "cpu" else "float16"
)
DTYPES = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}
if DTYPE_NAME not in DTYPES:
    raise ValueError(f"Unsupported dtype: {DTYPE_NAME}")
SAMPLE_DIR = Path(os.environ.get("SUBSYNQ_SAMPLE_DIR", "/config/review/comparison/channel-test"))
OUTPUT = SAMPLE_DIR / os.environ.get("SUBSYNQ_QWEN_OUTPUT", "qwen3-asr-result.json")
LANGUAGE_MODE = os.environ.get("SUBSYNQ_QWEN_LANGUAGE_MODE", "auto-and-french")
LANGUAGES = (None,) if LANGUAGE_MODE == "auto-only" else (None, "French")
SAMPLES = tuple(name.strip() for name in os.environ.get(
    "SUBSYNQ_QWEN_SAMPLES", "mono,center").split(",") if name.strip())


def synchronize():
    if DEVICE.startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize()


def save(payload):
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, OUTPUT)


started = time.monotonic()
print(f"Loading {MODEL_NAME} on {DEVICE} ({DTYPE_NAME})", flush=True)
model = Qwen3ASRModel.from_pretrained(
    MODEL_NAME,
    dtype=DTYPES[DTYPE_NAME],
    device_map=DEVICE,
    max_inference_batch_size=1,
    max_new_tokens=256,
)
synchronize()

result = {
    "model": MODEL_NAME,
    "backend": "transformers",
    "device": DEVICE,
    "device_name": torch.cuda.get_device_name(0) if DEVICE.startswith("cuda") and torch.cuda.is_available() else None,
    "dtype": DTYPE_NAME,
    "versions": {
        "python": platform.python_version(),
        "qwen-asr": version("qwen-asr"),
        "torch": torch.__version__,
        "hip": torch.version.hip,
        "transformers": version("transformers"),
    },
    "model_load_seconds": round(time.monotonic() - started, 2),
    "runs": [],
}
save(result)

for channel in SAMPLES:
    audio = SAMPLE_DIR / f"{channel}.wav"
    if not audio.is_file():
        raise FileNotFoundError(audio)
    for language in LANGUAGES:
        started = time.monotonic()
        transcription = model.transcribe(audio=str(audio), language=language)[0]
        synchronize()
        run = {
            "channel": channel,
            "requested_language": language,
            "detected_language": transcription.language,
            "text": transcription.text.strip(),
            "seconds": round(time.monotonic() - started, 2),
        }
        result["runs"].append(run)
        save(result)
        print(json.dumps(run, ensure_ascii=False), flush=True)

print(f"Finished: {OUTPUT}", flush=True)
