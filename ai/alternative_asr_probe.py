"""Offline, bounded alternative ASR diagnostic. No production imports or writes.

Supply read-only media/models and a SEPARATE diagnostic output directory.
Alignment locates supplied words; it does not verify their meaning/correctness.
"""
import argparse
import gc
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time


def window(start, end):
    if not all(math.isfinite(x) for x in (start, end)) or start < 0 or not 0 < end - start <= 60:
        raise ValueError("Require a finite, positive audio window of at most 60 seconds")
    return end - start


def nemo_text(results):
    if isinstance(results, tuple):
        if not results:
            raise ValueError("Missing NeMo transcription")
        results = results[0]
    if not isinstance(results, list) or len(results) != 1:
        raise ValueError("Expected exactly one NeMo transcription")
    text = results[0] if isinstance(results[0], str) else getattr(results[0], "text", None)
    if not isinstance(text, str):
        raise ValueError("Invalid NeMo text output")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, default=25)
    parser.add_argument("--engine", choices=("mms", "whisperx", "nemo"), required=True)
    parser.add_argument("--model", required=True, help="Existing local model directory or NeMo checkpoint")
    parser.add_argument("--align-model", help="Existing local language-specific alignment model")
    parser.add_argument("--language", default="hi", help="Explicit WhisperX language code")
    parser.add_argument("--mms-language", default="hin", help="Explicit MMS adapter language code")
    parser.add_argument("--silence", action="store_true", help="Generated silence control, not media")
    parser.add_argument("--check-forward", action="store_true", help="NeMo only: compare direct CTC with data loader")
    parser.add_argument("--output", required=True, help="NEW diagnostic JSON, never a production report")
    args = parser.parse_args()
    duration = window(args.start, args.end)
    output = Path(args.output)
    if output.exists():
        raise FileExistsError("Refusing to overwrite diagnostic output")
    import numpy as np
    import soundfile as sf
    import torch
    torch.set_num_threads(6)
    report = {"engine": args.engine, "device": "cpu", "model": args.model,
              "language_hint": args.mms_language if args.engine == "mms" else args.language,
              "start": args.start, "end": args.end, "silence_control": args.silence,
              "diagnostic_only": True, "human_verified": False}
    with tempfile.TemporaryDirectory(prefix="subsynq-asr-probe-") as tmp:
        wav = Path(tmp) / "clip.wav"
        if args.silence:
            sf.write(wav, np.zeros(round(duration * 16000), dtype=np.float32), 16000, subtype="PCM_16")
        else:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(args.start),
                            "-i", args.media, "-t", str(duration), "-vn", "-ac", "1",
                            "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True, timeout=120)
        audio, rate = sf.read(wav, dtype="float32")
        report["samples"] = len(audio)
        if not len(audio) or rate != 16000:
            raise ValueError("Missing/invalid extracted audio")
        if args.engine == "mms":
            from transformers import AutoProcessor, Wav2Vec2ForCTC
            begin = time.monotonic()
            processor = AutoProcessor.from_pretrained(args.model, target_lang=args.mms_language, local_files_only=True)
            model = Wav2Vec2ForCTC.from_pretrained(args.model, local_files_only=True, use_safetensors=True)
            processor.tokenizer.set_target_lang(args.mms_language)
            model.load_adapter(args.mms_language, local_files_only=True, use_safetensors=True)
            model.eval()
            report["load_seconds"] = time.monotonic() - begin
            begin = time.monotonic()
            inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
            with torch.inference_mode():
                ids = model(**inputs).logits.argmax(-1)
            report["text"] = processor.batch_decode(ids)[0]
            report["transcribe_seconds"] = time.monotonic() - begin
        elif args.engine == "nemo":
            import importlib.metadata
            report["language_hint"] = None
            report["language_source"] = "checkpoint"
            print(json.dumps({"event": "loading", "engine": "nemo"}), flush=True)
            begin = time.monotonic()
            from nemo.collections.asr.models import EncDecCTCModelBPE
            # Explicit local checkpoint; never from_pretrained/download at inference.
            model = EncDecCTCModelBPE.restore_from(args.model, map_location=torch.device("cpu"))
            model.eval()
            model.freeze()
            report["nemo_version"] = importlib.metadata.version("nemo_toolkit")
            report["decoding"] = str(model.cfg.get("decoding", {}).get("strategy", "checkpoint-default"))
            report["load_seconds"] = time.monotonic() - begin
            print(json.dumps({"event": "transcribing", "engine": "nemo"}), flush=True)
            begin = time.monotonic()
            original_forward = model.forward
            if args.check_forward:
                def observed_forward(*forward_args, **forward_kwargs):
                    signal = forward_kwargs["input_signal"]
                    lengths = forward_kwargs["input_signal_length"]
                    report["transcribe_samples"] = int(lengths[0])
                    report["transcribe_input_matches_extract"] = (
                        int(lengths[0]) == len(audio) and
                        torch.equal(signal[0, :len(audio)], torch.from_numpy(audio)))
                    result = original_forward(*forward_args, **forward_kwargs)
                    report["transcribe_encoded_frames"] = int(result[1][0])
                    return result
                model.forward = observed_forward
            try:
                with torch.inference_mode():
                    results = model.transcribe([str(wav)], batch_size=1, num_workers=0)
            finally:
                model.forward = original_forward
            report["text"] = nemo_text(results)
            report["transcribe_seconds"] = time.monotonic() - begin
            if args.check_forward:
                begin = time.monotonic()
                report["encoder_training_after_transcribe"] = model.encoder.training
                model.eval()
                # Match NeMo transcribe's inference settings, not its restored
                # training-time dither/padding defaults after cleanup.
                model.preprocessor.featurizer.dither = 0.0
                model.preprocessor.featurizer.pad_to = 0
                report["direct_inference_settings"] = {"eval": True, "dither": 0, "pad_to": 0}
                signal = torch.from_numpy(audio).unsqueeze(0)
                lengths = torch.tensor([len(audio)], dtype=torch.long)
                with torch.inference_mode():
                    logits, encoded_lengths, _ = model(input_signal=signal, input_signal_length=lengths)
                    direct = model.decoding.ctc_decoder_predictions_tensor(
                        logits, decoder_lengths=encoded_lengths, return_hypotheses=False)
                report["direct_text"] = nemo_text(direct)
                report["direct_matches_transcribe"] = report["direct_text"] == report["text"]
                report["encoded_frames"] = int(encoded_lengths[0])
                report["direct_seconds"] = time.monotonic() - begin
        else:
            import importlib.metadata
            import whisperx
            from silero_vad import load_silero_vad, get_speech_timestamps
            from whisperx.vads.silero import Silero
            from whisperx.vads.vad import Vad
            # Same WhisperX Silero segmentation API, but packaged weights instead
            # of executing/downloading a moving GitHub branch via torch.hub.
            class OfflineSilero(Silero):
                def __init__(self):
                    Vad.__init__(self, 0.5)
                    self.vad_onset = 0.5
                    self.chunk_size = 30
                    self.vad_pipeline = load_silero_vad()
                    self.get_speech_timestamps = get_speech_timestamps
            report["whisperx_version"] = importlib.metadata.version("whisperx")
            report["silero_version"] = importlib.metadata.version("silero-vad")
            begin = time.monotonic()
            model = whisperx.load_model(args.model, "cpu", compute_type="int8", language=args.language,
                                        vad_model=OfflineSilero(), threads=6, local_files_only=True)
            report["load_seconds"] = time.monotonic() - begin
            begin = time.monotonic()
            result = model.transcribe(audio, batch_size=1)
            report["transcribe_seconds"] = time.monotonic() - begin
            report["segments"] = result["segments"]
            report["text"] = " ".join(s["text"] for s in result["segments"])
            del model
            gc.collect()
            if args.align_model and result["segments"]:
                begin = time.monotonic()
                aligner, meta = whisperx.load_align_model(args.language, "cpu", model_name=args.align_model)
                report["alignment_load_seconds"] = time.monotonic() - begin
                begin = time.monotonic()
                report["alignment"] = whisperx.align(result["segments"], aligner, meta, audio, "cpu")
                report["alignment_seconds"] = time.monotonic() - begin
                report["alignment_is_not_source_verification"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
