"""KB-Whisper reads Swedish speech instead of Whisper large-v3.

KB-Whisper (Kungliga biblioteket, Apache-2.0, a fine-tune of Whisper large-v3 on Swedish) read three Swedish trailers with 12 clear corrections
and 5 regressions against large-v3 at the same speed (STATUS 2026-10-19, night 29). It replaces large-v3 as the main reader of a local CPU job
when the speech is Swedish; anything else, and every other device, keeps large-v3. The decision is made here from Whisper's own language guesses
on a few short windows (taken by the worker); this module holds the pure parts so they can be tested without models.
"""
import os
from pathlib import Path

REPOSITORY = 'KBLab/kb-whisper-large'
PACK_ID = 'whisper-kb-swedish'     # its id in models.json: an optional language pack, off until the user switches it on
LABEL = 'KB-Whisper large (svenska)'
MIN_SPEECH_SECONDS = 1.0        # a window with less speech says nothing about the language
MIN_PROBABILITY = .75           # Whisper's own certainty for a window to count as Swedish (a Danish trailer read .73 and .85 for Swedish)
NEIGHBOUR_LANGUAGES = ('da', 'no', 'nn', 'nb')   # Whisper often takes Danish and Norwegian for Swedish and the other way round
NEIGHBOUR_VETO = .85            # one window heard this surely as Danish or Norwegian means the file is not Swedish


def repository(environ=None):
    """The Hugging Face repository to read Swedish with, or '' when switched off (SUBSYNQ_SWEDISH_WHISPER= or 0)."""
    value = (os.environ if environ is None else environ).get('SUBSYNQ_SWEDISH_WHISPER', REPOSITORY).strip()
    return '' if value in ('', '0', 'off') else value


def model_present(models_dir, repo):
    """True when the CTranslate2 weights of `repo` have been downloaded under `models_dir` (the model manager does that)."""
    snapshots = Path(models_dir) / ('models--' + repo.replace('/', '--')) / 'snapshots'
    return snapshots.is_dir() and any((snapshot / 'model.bin').exists() for snapshot in snapshots.iterdir())


def decide(readings):
    """(use, reason): Swedish when at least two windows had enough speech to judge and at least two thirds of them were heard as Swedish, and
    none was heard surely as Danish or Norwegian (TRANSFERVINDUE, a Danish trailer, was read by KB-Whisper as Swedish: only 7 of 12 witnessed lines
    came out right against 10 of 10 with large-v3 told it was Danish).

    `readings` are {'language': 'sv', 'probability': 0.93, 'speech_seconds': 6.2} per window; a window without a language guess is ignored."""
    usable = [item for item in readings if item.get('language') and (item.get('speech_seconds') or 0) >= MIN_SPEECH_SECONDS]
    if len(usable) < 2:
        return False, 'för lite tal för att avgöra språket'
    neighbour = [item for item in usable if item['language'] in NEIGHBOUR_LANGUAGES and (item.get('probability') or 0) >= NEIGHBOUR_VETO]
    if neighbour:
        return False, 'ett ljudfönster hörs säkert som danska eller norska'
    swedish = [item for item in usable if item['language'] == 'sv' and (item.get('probability') or 0) >= MIN_PROBABILITY]
    if len(swedish) * 3 < len(usable) * 2:
        return False, 'ljudet hörs inte som svenska'
    return True, f'{len(swedish)} av {len(usable)} ljudfönster hörs som svenska'
