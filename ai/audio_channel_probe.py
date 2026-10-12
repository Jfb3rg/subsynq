"""Isolated, bounded stereo/ASR diagnostics; never imports production workers."""

import argparse
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import subprocess
import time
import wave
from importlib.metadata import version


VARIANTS = {
    "mono": None,
    "left": "pan=mono|c0=c0",
    "right": "pan=mono|c0=c1",
    "bandlimited": "highpass=f=80,lowpass=f=7500",
    "denoised": "afftdn=nr=6:nf=-45",
}


def window(start, end):
    if not all(math.isfinite(v) for v in (start, end)):
        raise ValueError("Window must be finite")
    if start < 0 or not 0 < end - start <= 60:
        raise ValueError("Window must be positive and at most 60 seconds")
    return end - start


def channel_stats(samples):
    import numpy as np

    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim != 2 or samples.shape[1] != 2 or not len(samples):
        raise ValueError("Expected nonempty stereo samples")
    if not np.isfinite(samples).all():
        raise ValueError("Nonfinite audio")
    rms = lambda x: float(np.sqrt(np.mean(x * x)))
    left, right = samples.T
    correlation = None
    if float(np.std(left)) > 0 and float(np.std(right)) > 0:
        correlation = float(np.corrcoef(left, right)[0, 1])
    return {
        "samples_per_channel": len(samples),
        "left_rms": rms(left), "right_rms": rms(right),
        "left_peak": float(np.max(np.abs(left))),
        "right_peak": float(np.max(np.abs(right))),
        "left_clip_fraction": float(np.mean(np.abs(left) >= 1)),
        "right_clip_fraction": float(np.mean(np.abs(right) >= 1)),
        "correlation": correlation,
        "mid_rms": rms((left + right) / 2),
        "side_rms": rms((left - right) / 2),
        "channels_identical": bool(np.array_equal(left, right)),
    }


def checked_samples(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    window(manifest["start"], manifest["end"])
    names = [entry["name"] for entry in manifest["variants"]]
    if not names or len(names) != len(set(names)):
        raise ValueError("Expected nonempty, unique audio variants")
    for entry in manifest["variants"]:
        name = entry["name"]
        if name not in set(VARIANTS) | {'vocals', 'reference'}:
            raise ValueError("Unknown audio variant")
        path = directory / f"{name}.wav"
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Changed audio: {name}")
        with wave.open(str(path), "rb") as wav:
            if abs(wav.getnframes() - round((manifest["end"] - manifest["start"]) * 16000)) > 1:
                raise ValueError("Prepared audio does not cover the requested window")
    return manifest


def wav_stats(path):
    import numpy as np
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, 16000, 2):
            raise ValueError("Expected mono 16 kHz PCM16")
        values = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.int32)
    if not len(values):
        raise ValueError("Empty WAV")
    return {"frames": len(values), "peak": float(np.max(np.abs(values)) / 32768),
            "rms": float(np.sqrt(np.mean((values / 32768) ** 2))),
            "pcm_limit_fraction": float(np.mean((values == 32767) | (values == -32768)))}


def prepare(args):
    import numpy as np

    duration = window(args.start, args.end)
    info = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
        "-of", "json", str(args.media),
    ]))
    if not info["streams"] or info["streams"][0].get("channels") != 2:
        raise ValueError("This channel comparison requires a stereo first audio stream")
    # A new directory prevents accidental replacement of earlier diagnostic audio.
    args.samples.mkdir(parents=True, exist_ok=False)
    base = ["ffmpeg", "-v", "error", "-nostdin", "-ss", str(args.start),
            "-i", str(args.media), "-t", str(duration), "-map", "0:a:0", "-vn"]
    raw = subprocess.check_output(base + ["-ar", "16000", "-ac", "2", "-f", "f32le", "pipe:1"])
    stats = channel_stats(np.frombuffer(raw, dtype="<f4").reshape(-1, 2))
    result = {"source": str(args.media), "start": args.start, "end": args.end,
              "audio_stream": info["streams"][0], "channel_stats": stats, "variants": []}
    for name in args.variants:
        audio_filter = VARIANTS[name]
        path = args.samples / f"{name}.wav"
        # Mono baseline preserves ffmpeg's standard downmix used in earlier probes.
        command = base + (["-af", audio_filter] if audio_filter else [])
        subprocess.run(command + ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-n", str(path)], check=True)
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, 16000, 2):
                raise ValueError("Unexpected prepared WAV format")
            frames = wav.getnframes()
        if abs(frames - round(duration * 16000)) > 1:
            raise ValueError("Extracted audio does not cover the requested window")
        result["variants"].append({"name": name, "filter": audio_filter, "frames": frames,
                                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    with (args.samples / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    return result


def prepare_silence(args):
    duration = window(args.start, args.end)
    if args.start != 0:
        raise ValueError("Generated silence starts at zero")
    args.samples.mkdir(parents=True, exist_ok=False)
    path = args.samples / 'mono.wav'
    frames = round(duration * 16000)
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * frames)
    result = {'source': 'generated silence', 'silence_control': True,
              'start': 0, 'end': args.end, 'variants': [{'name': 'mono', 'frames': frames,
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    with (args.samples / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    return result


def whisper_options(language, without_timestamps=False):
    return {'language': language, 'beam_size': 5, 'temperature': 0,
            'condition_on_previous_text': False, 'vad_filter': False,
            'without_timestamps': without_timestamps,
            'word_timestamps': not without_timestamps,
            'hallucination_silence_threshold': None if without_timestamps else 2}


def infer(args):
    manifest = checked_samples(args.samples)
    started = time.monotonic()
    if args.phase == "whisper":
        from faster_whisper import WhisperModel
        model = WhisperModel(str(args.model), device="cpu", compute_type="int8",
                             cpu_threads=6, num_workers=1, local_files_only=True)
    else:
        import torch
        from qwen_asr import Qwen3ASRModel
        torch.set_num_threads(6)
        model = Qwen3ASRModel.from_pretrained(str(args.model), dtype=torch.float32,
                    device_map="cpu", max_inference_batch_size=1, max_new_tokens=256,
                    local_files_only=True)
    result = {"engine": args.phase, "model": str(args.model), "device": "cpu",
              "silence_control": manifest.get('silence_control', False),
              "model_load_seconds": round(time.monotonic() - started, 2),
              "versions": {"python": platform.python_version(),
                           ("faster-whisper" if args.phase == "whisper" else "qwen-asr"):
                           version("faster-whisper" if args.phase == "whisper" else "qwen-asr")},
              "settings": {"threads": 6, "whisper_compute_type": "int8",
                           "whisper_beam_size": 5, "whisper_vad": False,
                           "whisper_without_timestamps": args.without_timestamps,
                           "whisper_word_timestamps": not args.without_timestamps,
                           "whisper_condition_on_previous_text": False,
                           "qwen_dtype": "float32", "qwen_max_new_tokens": 256},
              "start": manifest["start"], "end": manifest["end"], "runs": []}
    for entry in manifest["variants"]:
        started = time.monotonic()
        audio = str(args.samples / (entry["name"] + ".wav"))
        if args.phase == "whisper":
            segments, info = model.transcribe(audio, **whisper_options(args.language, args.without_timestamps))
            rows = [{"start": s.start, "end": s.end, "text": s.text.strip(),
                     "words": [{"start": w.start, "end": w.end, "text": w.word,
                                "probability": w.probability} for w in (s.words or [])]}
                    for s in segments]
            text = " ".join(row["text"] for row in rows)
            detected = info.language
            requested = args.language
        else:
            # Auto language matches Qwen's earlier diagnostic baseline.
            transcript = model.transcribe(audio=audio, language=None)[0]
            text, detected, requested, rows = transcript.text.strip(), transcript.language, None, []
        run = {"variant": entry["name"], "sha256": entry["sha256"], "text": text,
               "audio_stats": wav_stats(Path(audio)),
               "detected_language": detected, "requested_language": requested,
               "seconds": round(time.monotonic() - started, 2), "segments": rows}
        result["runs"].append(run)
        print(json.dumps(run, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "silence", "qwen", "whisper"))
    parser.add_argument("--media", type=Path)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, default=25)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--language", help="Whisper language (default auto); Qwen always auto")
    parser.add_argument('--without-timestamps', action='store_true',
                        help='Whisper text-only decoding; no word-time verification')
    parser.add_argument("--variants", nargs="+", choices=tuple(VARIANTS), default=list(VARIANTS),
                        help="prepare variants; inference uses the prepared manifest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.phase == "prepare" and args.media is None:
        parser.error("prepare requires --media")
    if args.phase in ("qwen", "whisper") and args.model is None:
        parser.error("inference requires a local --model")
    os.environ["HF_HUB_OFFLINE"] = "1"
    result = (prepare(args) if args.phase == "prepare" else
              prepare_silence(args) if args.phase == "silence" else infer(args))
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
