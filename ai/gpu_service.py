"""Local-only ROCm ASR service for the Docker Desktop worker."""
import io
import gc
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf
import torch
from qwen_asr import Qwen3ASRModel
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

WHISPER_NAME = os.environ.get("SUBSYNQ_WHISPER_MODEL", "openai/whisper-large-v3")
QWEN_NAME = os.environ.get("SUBSYNQ_QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MODEL_LOCK = threading.Lock()


def release_gpu_memory():
    """Return temporary inference buffers before the next model runs."""
    gc.collect()
    torch.cuda.empty_cache()


def compact_words(chunks, maximum_duration=7.0, maximum_characters=84):
    """Group Transformers word timestamps into readable subtitle-sized cues."""
    words = []
    for chunk in chunks:
        timestamp = chunk.get("timestamp") or (None, None)
        start, end = timestamp
        text = str(chunk.get("text", ""))
        if start is None or end is None or end <= start or not text.strip():
            continue
        words.append({"start": float(start), "end": float(end), "word": text,
                      "probability": 1.0})
    # Whisper sometimes stretches the first word backwards across several
    # seconds of leading silence. Keep the reliable word end but cap that
    # impossible span so a subtitle cannot appear long before the voice.
    for index, word in enumerate(words):
        token_length = len(re.sub(r"[^\w]", "", word["word"], flags=re.UNICODE))
        duration = word["end"] - word["start"]
        if duration <= 1.5 and not (token_length <= 4 and duration > 1.0):
            continue
        estimated = min(1.0, max(.24, token_length * .09))
        lower_bound = words[index - 1]["end"] if index else 0.0
        word["start"] = max(lower_bound, word["end"] - estimated)
    cues, group = [], []

    def flush():
        if not group:
            return
        text = "".join(word["word"] for word in group).strip()
        if text:
            cues.append({"start": group[0]["start"],
                         "end": group[-1]["end"], "text": text,
                         "words": [dict(word) for word in group]})
        group.clear()

    for word in words:
        projected = "".join(item["word"] for item in (*group, word)).strip()
        if group and (word["end"] - group[0]["start"] > maximum_duration
                      or len(projected) > maximum_characters
                      or word["start"] - group[-1]["end"] >= 2.8):
            flush()
        group.append(word)
        if word["word"].rstrip().endswith((".", "?", "!")):
            flush()
    flush()
    return cues


def qwen_result(audio, sample_rate):
    with tempfile.NamedTemporaryFile(suffix=".wav") as temporary:
        sf.write(temporary.name, audio, sample_rate, subtype="PCM_16")
        print(f"Qwen verifierar {len(audio) / sample_rate:.1f} sekunder", flush=True)
        try:
            result = qwen.transcribe(audio=temporary.name, language=None)[0]
            print("Qwen-verifiering klar", flush=True)
            return result
        finally:
            release_gpu_memory()


def verify_short_cues(audio, sample_rate, cues):
    """Use Qwen again around short cues where Whisper ghosts are most common."""
    accepted, rejected = [], 0
    audio_duration = len(audio) / sample_rate
    for cue in cues:
        compact_length = len(re.sub(r"\s+", "", cue["text"]))
        if len(cue["words"]) > 2 and compact_length > 14:
            accepted.append(cue)
            continue
        start = max(0.0, cue["start"] - .35)
        end = min(audio_duration, cue["end"] + .35)
        sample = audio[round(start * sample_rate):round(end * sample_rate)]
        if len(sample) >= sample_rate // 4 and qwen_result(sample, sample_rate).text.strip():
            accepted.append(cue)
        else:
            rejected += 1
    return accepted, rejected


# Whisper large-v3 and Qwen3-ASR together sit at ~12 GB resident just to be
# loaded, leaving too little headroom on this card's 16 GB for actual
# inference buffers on some files (observed live: HIP out of memory mid-job,
# then the card got stuck reporting HSA allocator failures until restarted).
# Loading each model on demand and releasing it once its half of the request
# is done keeps steady-state VRAM to roughly one model's footprint at a time,
# at the cost of a load delay (a few seconds for Whisper, ~30-40s for Qwen's
# checkpoint shards) on the first use of each per request.
whisper = processor = whisper_pipe = qwen = None
torch.cuda.init()
print(f"GPU ASR ready on {torch.cuda.get_device_name(0)} (models load on demand)", flush=True)


def load_whisper():
    global whisper, processor, whisper_pipe
    if whisper_pipe is not None:
        return
    print(f"Loading {WHISPER_NAME}", flush=True)
    whisper = AutoModelForSpeechSeq2Seq.from_pretrained(
        WHISPER_NAME, dtype=torch.float16, low_cpu_mem_usage=True, use_safetensors=True,
    ).to("cuda:0")
    processor = AutoProcessor.from_pretrained(WHISPER_NAME)
    whisper_pipe = pipeline(
        "automatic-speech-recognition", model=whisper, tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor, dtype=torch.float16, device="cuda:0",
    )


def unload_whisper():
    global whisper, processor, whisper_pipe
    whisper = processor = whisper_pipe = None
    release_gpu_memory()


def load_qwen():
    global qwen
    if qwen is not None:
        return
    print(f"Loading {QWEN_NAME}", flush=True)
    qwen = Qwen3ASRModel.from_pretrained(
        QWEN_NAME, dtype=torch.float16, device_map="cuda:0",
        max_inference_batch_size=1, max_new_tokens=128,
    )


def unload_qwen():
    global qwen
    qwen = None
    release_gpu_memory()


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            self.respond(404, {"status": "error", "error": "Not found"})
            return
        free, total = torch.cuda.mem_get_info()
        self.respond(200, {"status": "ready", "device": torch.cuda.get_device_name(0),
                           "whisper": WHISPER_NAME, "verifier": QWEN_NAME,
                           "vram_free_bytes": free, "vram_total_bytes": total})

    def do_POST(self):
        if self.path != "/transcribe":
            self.respond(404, {"status": "error", "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_AUDIO_BYTES:
                raise ValueError("Ogiltig ljudstorlek")
            payload = self.rfile.read(length)
            audio, sample_rate = sf.read(io.BytesIO(payload), dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            if sample_rate != 16000:
                raise ValueError("GPU-tjänsten kräver 16 kHz-ljud")
            with MODEL_LOCK:
                try:
                    load_whisper()
                    print(f"Whisper lyssnar på {len(audio) / sample_rate:.1f} sekunder", flush=True)
                    whisper_result = whisper_pipe(
                        {"array": audio, "sampling_rate": sample_rate},
                        return_timestamps="word", generate_kwargs={"task": "transcribe"},
                    )
                    print("Whisper klar", flush=True)
                    # Whisper and Qwen together sit too close to this card's 16 GB
                    # limit for reliable inference on every file. Whisper is fully
                    # unloaded (not just cache-cleared) before Qwen loads, so only
                    # one model's weights are resident on the card at a time.
                    unload_whisper()
                    load_qwen()
                    verified = qwen_result(audio, sample_rate)
                    verifier_text = verified.text.strip()
                    cues = compact_words(whisper_result.get("chunks", [])) if verifier_text else []
                    cues, rejected_cues = verify_short_cues(audio, sample_rate, cues)
                    torch.cuda.synchronize()
                    print(f"GPU-fönster klart · {len(cues)} repliker", flush=True)
                finally:
                    unload_whisper()
                    unload_qwen()
            self.respond(200, {"status": "ok", "language": verified.language,
                               "whisper_text": whisper_result.get("text", "").strip(),
                               "verifier_text": verifier_text, "segments": cues,
                               "speech_verified": bool(verifier_text),
                               "rejected_short_cues": rejected_cues})
        except Exception as error:
            release_gpu_memory()
            self.respond(500, {"status": "error", "error": str(error)[:500]})

    def log_message(self, _format, *_args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8766), Handler).serve_forever()
