"""Bounded local ASR experiment. Never writes subtitles into the media library."""
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps

output = Path("/config/asr-tests") / time.strftime("%Y%m%d-%H%M%S")
output.mkdir(parents=True, exist_ok=False)
with sqlite3.connect("/config/subsynq.db") as db:
    library = json.loads(db.execute("SELECT value FROM metadata WHERE key='library_scan'").fetchone()[0])
media_match = os.environ.get("SUBSYNQ_MEDIA_MATCH", "S01E01")
matches = [item for item in library["items"] if media_match in item["name"]]
if len(matches) != 1:
    raise RuntimeError(f"Expected exactly one media match for {media_match!r}, found {len(matches)}")
media = matches[0]
stream = media["probe"]["audio"][0]["index"]
print("Finding speech in the first 20 minutes to select two useful samples.", flush=True)
preview = Path(os.environ["SUBSYNQ_PREVIEW"]) if os.environ.get("SUBSYNQ_PREVIEW") else output / "preview.wav"
if not os.environ.get("SUBSYNQ_PREVIEW"):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", media["path"], "-map", f"0:{stream}", "-t", "1200", "-ac", "1", "-ar", "16000", str(preview)], check=True, timeout=180)
audio = decode_audio(str(preview))
speech = get_speech_timestamps(audio)
candidates = []
for offset in range(0, max(1, int(len(audio)/16000)-59), 30):
    score = sum(max(0, min(span["end"]/16000, offset+60)-max(span["start"]/16000, offset)) for span in speech)
    candidates.append((score, offset))
candidates.sort(reverse=True)
offsets = []
for score, offset in candidates:
    if score >= 10 and all(abs(offset-other) >= 120 for other in offsets):
        offsets.append(offset)
    if len(offsets) == 2:
        break
if len(offsets) != 2:
    raise RuntimeError("Could not find two speech-rich samples. Inspect audio before continuing.")
print(f"Selected offsets: {sorted(offsets)}", flush=True)
print("Loading large-v3 on CPU (int8); first run downloads the model.", flush=True)
model = WhisperModel("large-v3", device="cpu", compute_type="int8", cpu_threads=6, download_root="/models")
language_samples = {}
for score, offset in candidates:
    if score < 10:
        continue
    language, probability, _ = model.detect_language(audio=audio[offset*16000:(offset+60)*16000], vad_filter=True)
    print(f"Language check {offset}s: {language} ({probability:.2f})", flush=True)
    if language in ("en", "fr") and probability >= 0.65 and language not in language_samples:
        language_samples[language] = offset
    if len(language_samples) == 2:
        offsets = list(language_samples.values())
        break
results = {"media": media["name"], "model": "large-v3", "compute_type": "int8", "audio_stream": stream, "samples": []}
for offset in sorted(offsets):
    wav = output / f"sample-{offset}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(offset), "-i", str(preview), "-t", "60", "-ac", "1", "-ar", "16000", str(wav)], check=True, timeout=90)
    print(f"Transcribing {offset}-{offset+60}s", flush=True)
    started = time.monotonic()
    segments, info = model.transcribe(str(wav), task="transcribe", language=None, multilingual=True, beam_size=5, vad_filter=True, condition_on_previous_text=False, word_timestamps=True)
    lines = []
    for segment in segments:
        lines.append({"start": offset + segment.start, "end": offset + segment.end, "text": segment.text, "avg_logprob": segment.avg_logprob})
        print(f"{offset + segment.start:.1f}: {segment.text}", flush=True)
    results["samples"].append({"offset": offset, "duration": 60, "speech_seconds": info.duration_after_vad, "initial_detected_language": info.language, "elapsed_seconds": round(time.monotonic()-started, 1), "segments": lines})
    temp = output / "result.tmp"
    temp.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, output / "result.json")
print(f"Finished. Results: {output}/result.json", flush=True)
