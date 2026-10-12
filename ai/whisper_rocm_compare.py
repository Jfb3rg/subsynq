"""Quality-first Whisper large-v3 test on the saved review audio via ROCm."""
import json
import os
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from asr_windows import merge_window_segments, plan_windows

MODEL_NAME = os.environ.get("SUBSYNQ_WHISPER_MODEL", "openai/whisper-large-v3")
DEVICE = os.environ.get("SUBSYNQ_WHISPER_DEVICE", "cuda:0")
DTYPE = torch.float16 if DEVICE.startswith("cuda") else torch.float32
SAMPLE = Path(os.environ.get("SUBSYNQ_WHISPER_SAMPLE", "/config/review/sample-900.wav"))
OUTPUT = Path(os.environ.get("SUBSYNQ_WHISPER_OUTPUT", str(SAMPLE.with_name("whisper-rocm-result.json"))))


def save(payload):
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, OUTPUT)


audio, sample_rate = sf.read(SAMPLE, dtype="float32", always_2d=False)
if audio.ndim > 1:
    audio = np.mean(audio, axis=1)
if sample_rate != 16_000:
    import librosa
    audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=16_000)
    sample_rate = 16_000

started = time.monotonic()
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    MODEL_NAME,
    dtype=DTYPE,
    low_cpu_mem_usage=True,
    use_safetensors=True,
).to(DEVICE)
processor = AutoProcessor.from_pretrained(MODEL_NAME)
transcriber = pipeline(
    "automatic-speech-recognition",
    model=model,
    tokenizer=processor.tokenizer,
    feature_extractor=processor.feature_extractor,
    dtype=DTYPE,
    device=DEVICE,
)
torch.cuda.synchronize()

payload = {
    "model": MODEL_NAME,
    "backend": "transformers-rocm",
    "device": DEVICE,
    "device_name": torch.cuda.get_device_name(0),
    "dtype": str(DTYPE).removeprefix("torch."),
    "sample": SAMPLE.name,
    "sample_seconds": round(len(audio) / sample_rate, 3),
    "versions": {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "hip": torch.version.hip,
        "transformers": version("transformers"),
    },
    "model_load_seconds": round(time.monotonic() - started, 2),
    "runs": [],
}
save(payload)

fixed_profiles = (
    ("full", 0, None, True),
    ("war-focus", 28, 60, True),
    ("war-tail-words", 40, 60, "word"),
) if os.environ.get("SUBSYNQ_FIXED_PROFILES", "1") == "1" else ()
for label, start_second, end_second, timestamp_mode in fixed_profiles:
    clip = audio[int(start_second * sample_rate):int(end_second * sample_rate) if end_second else None]
    started = time.monotonic()
    result = transcriber(
        {"array": clip, "sampling_rate": sample_rate},
        return_timestamps=timestamp_mode,
        generate_kwargs={"task": "transcribe", "language": "english"},
    )
    torch.cuda.synchronize()
    chunks = []
    for chunk in result.get("chunks", []):
        first, last = chunk["timestamp"]
        absolute_start = (first or 0) + start_second
        absolute_end = (last if last is not None else len(clip) / sample_rate) + start_second
        absolute_end = min(absolute_end, start_second + len(clip) / sample_rate)
        text = chunk["text"].strip()
        if text and absolute_end > absolute_start:
            chunks.append({
                "start": round(absolute_start, 3),
                "end": round(absolute_end, 3),
                "text": text,
            })
    run = {
        "label": label,
        "range": [start_second, end_second],
        "text": result["text"].strip(),
        "chunks": chunks,
        "seconds": round(time.monotonic() - started, 2),
    }
    payload["runs"].append(run)
    save(payload)
    print(json.dumps(run, ensure_ascii=False), flush=True)

window_results = []
overlap_started = time.monotonic()
for window in plan_windows(len(audio) / sample_rate):
    start_second, end_second = window["start"], window["end"]
    clip = audio[int(start_second * sample_rate):int(end_second * sample_rate)]
    result = transcriber(
        {"array": clip, "sampling_rate": sample_rate},
        return_timestamps=True,
        generate_kwargs={"task": "transcribe"},
    )
    torch.cuda.synchronize()
    segments = []
    for chunk in result.get("chunks", []):
        first, last = chunk["timestamp"]
        absolute_start = (first or 0) + start_second
        absolute_end = min((last if last is not None else len(clip) / sample_rate) + start_second, end_second)
        if chunk["text"].strip() and absolute_end > absolute_start:
            segments.append({"start": absolute_start, "end": absolute_end, "text": chunk["text"].strip()})
    window_results.append({**window, "text": result["text"].strip(), "segments": segments})
payload["automatic_overlap"] = {
    "window_seconds": 30,
    "overlap_seconds": 8,
    "language_policy": "automatic_per_window",
    "windows": window_results,
    "segments": merge_window_segments(window_results),
    "seconds": round(time.monotonic() - overlap_started, 2),
}
save(payload)
print(json.dumps(payload["automatic_overlap"], ensure_ascii=False), flush=True)

print(f"Finished: {OUTPUT}", flush=True)
