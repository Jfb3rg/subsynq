"""Parakeet TDT 0.6B v3 reads the doubtful places once more as an EXTRA voter next to Whisper and Qwen3-ASR.

Parakeet (NVIDIA, CC-BY-4.0; the ONNX conversion by istupakov) is a FastConformer encoder with a token-and-duration transducer: another
architecture than the Whisper models (encoder-decoder) and Qwen3-ASR (language-model decoder), so its mistakes are not theirs. On the saved
English trailers and episodes it heard the same words as three other families on 6 of 7 rows where those agreed (STATUS 2026-11-10) and
differed from the approved source on 6.8 % of the words of 45 ordinary rows, mostly "gonna/gotta" and names: as good as the others, not better.
So it never replaces a reader. It reads the windows Qwen3-ASR reads, on the CPU of the machine the worker runs on (about 2.4 times faster than
real time on four threads, 2.7 GB of memory), also when the main reading is done on a rented GPU, and is one more model family in the votes
(asr_windows._FAMILIES: 'parakeet'). The model is NOT part of SubSynq: the model manager downloads the files from Hugging Face (kind
'hf-files', models.json) and keeps them at the newest revision. The languages are measured one by one, not by reputation: it lists 25 European
languages, but on saved trailers (2 s of sound each side of every approved row, STATUS 2026-11-10) it differed from the approved words on 6.8 % in
English and 7.6 % in German, against 32.5 % in Swedish and 44.6 % in Danish, where short clips often came out as English or German words that
were never said. So it reads English and German; Swedish, Danish and the rest stay off until they are measured better. It guesses the language
from the clip itself, so a window is read with PADDING seconds of sound on each side and the words outside the window are cut away again by
their times (whole words only): with the whole-row measure that is 14.3 % against 24.8 % for English cut exactly, 15.5 % for German.
"""
import os
from pathlib import Path

from model_manager import files_dir

REPOSITORY = 'istupakov/parakeet-tdt-0.6b-v3-onnx'
LABEL = 'Parakeet TDT 0.6B v3'
READING_NAME = 'parakeet'              # its name among the readings (asr_windows._FAMILIES: family 'parakeet')
MODEL_NAME = 'nemo-parakeet-tdt-0.6b-v3'
PADDING = {'en': 2.0, 'de': 2.0}       # the spoken languages it reads, and the seconds of sound added each side of a window (cut away again)
REQUIRED_FILES = ('config.json', 'vocab.txt', 'encoder-model.onnx', 'encoder-model.onnx.data', 'decoder_joint-model.onnx')
CONFIDENCE = .5                        # as Qwen3-ASR's: a vote, never the most confident reading of a place


def enabled(environ=None):
    """False when switched off with SUBSYNQ_PARAKEET=0 (or off)."""
    return (os.environ if environ is None else environ).get('SUBSYNQ_PARAKEET', '1').strip().lower() not in ('0', 'off', 'no', 'false')


def model_folder(models_dir='/models'):
    return files_dir(models_dir, REPOSITORY)


def padding(choice):
    """Seconds of sound to add on each side of a window for the language of the chosen audio track (0 when it is not read)."""
    return PADDING.get(str((choice or {}).get('language') or '').lower(), 0.0)


def trim_to_window(tokens, times, start, end, before=0.0, after=.1):
    """The words (whole words, never a piece of one) that begin inside [start - before, end + after] seconds of the clip. A token that opens a
    word starts with a space; its time is when the word begins. Measured on German trailers (68 rows) this gives 15.5 % words differing from
    the approved row against 24.4 % when the tokens themselves are cut at the window and a half word is left at each edge."""
    words = []
    for token, time in zip(tokens, times):
        if token.startswith(' ') or not words:
            words.append([token, time])
        else:
            words[-1][0] += token
    return ' '.join(word.strip() for word, time in words if start - before <= time <= end + after).strip()


def model_present(models_dir='/models'):
    """True when the model manager has downloaded every file (its revision marker is written last)."""
    folder = model_folder(models_dir)
    return (folder / '.subsynq-revision').is_file() and all((folder / name).is_file() for name in REQUIRED_FILES)


def usable_language(choice):
    """(use, reason): the audio track chosen for the file is one of the measured languages (`choice` is the job's audio_choice record)."""
    language = str((choice or {}).get('language') or '').lower()
    if language in PADDING:
        return True, ''
    return False, f'språket ({language or "okänt"}) är inte godkänt för Parakeet'


class Reader:
    """Loads the model on first use and releases it with `release()`. Raises RuntimeError when it cannot be loaded or run."""

    def __init__(self, models_dir='/models', threads=None):
        self.folder = model_folder(models_dir)
        self.threads = threads or min(8, os.cpu_count() or 4)
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                import onnx_asr
                import onnxruntime
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = self.threads
                self._model = onnx_asr.load_model(MODEL_NAME, str(self.folder), sess_options=options,
                                                  providers=['CPUExecutionProvider'])
            except Exception as error:  # noqa: BLE001 - any loading problem means "no Parakeet vote", never a failed job
                raise RuntimeError(f'Parakeet kunde inte laddas: {error!r}') from error
        return self._model

    def read(self, clip, keep=None):
        """The text Parakeet hears in the 16 kHz mono WAV `clip` ('' for silence); with `keep` = (start, end) in seconds of the clip only the
        words spoken inside that part (the rest of the clip is sound added around a window for the language guess)."""
        try:
            model = self._load()
            result = (model.with_timestamps() if keep else model).recognize(str(clip))
        except RuntimeError:
            raise
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f'Parakeet kunde inte läsa klippet: {error!r}') from error
        if keep:
            return trim_to_window(result.tokens, result.timestamps, keep[0], keep[1])
        return str(getattr(result, 'text', result) or '').strip()

    def release(self):
        self._model = None
        import gc
        gc.collect()
