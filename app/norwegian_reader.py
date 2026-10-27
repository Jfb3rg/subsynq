"""NB-Whisper reads Norwegian speech as an EXTRA reader next to Whisper large-v3, large-v2 and Qwen3-ASR.

NB-Whisper (Nasjonalbiblioteket, Apache-2.0, a fine-tune of Whisper large on Norwegian) was read against large-v3 and large-v2 (both told the
language was Norwegian) on Agnes mot verden and Glemselens øy (STATUS 2026-10-25). Where the Whisper models misheard Norwegian words it heard them
right or sensibly ("Etter hvert ser jeg for meg" for "Til hvert", "inni pæra mi" where the file had Qwen's Danish "indenfor mig", "Jeg må bli
lykkelig", "gjemt der", "fullmåne", "minnedemon", "Bestevenner for alltid"), but it was trained on NRK subtitles: it shortens lines, tones down
swearing ("senil i faen" -> "senil i lufta") and writes English lines in Norwegian. So it never replaces the main reader (unlike KB-Whisper for
Swedish): it reads the same doubtful places as large-v2 and is one more model family in the existing votes, where the Whisper models still win
whenever they agree. Qwen3-ASR keeps reading as before (the user: never switch a reader off). This module holds the pure parts.
"""
import os

REPOSITORY = 'NbAiLab/nb-whisper-large'
PACK_ID = 'whisper-nb-norwegian'       # its id in models.json: an optional language pack, off until the user switches it on
LABEL = 'NB-Whisper large (norska)'
READING_NAME = 'nb'                    # its name among the readings (asr_windows._FAMILIES: family 'nb-whisper')
LANGUAGES = ('no', 'nn', 'nb')         # Whisper's codes for Norwegian (Bokmål, Nynorsk)
MIN_SPEECH_SECONDS = 1.0               # a window with less speech says nothing about the language
MIN_PROBABILITY = .6                   # Glemselens øy: 'no' .936 and .942, 'nn' .613 in its three windows


def repository(environ=None):
    """The Hugging Face repository to read Norwegian with, or '' when switched off (SUBSYNQ_NORWEGIAN_WHISPER= or 0)."""
    value = (os.environ if environ is None else environ).get('SUBSYNQ_NORWEGIAN_WHISPER', REPOSITORY).strip()
    return '' if value in ('', '0', 'off') else value


def decide(readings):
    """(use, reason): Norwegian when at least two windows had enough speech to judge and at least two thirds of them were heard as Norwegian.

    `readings` are {'language': 'no', 'probability': 0.94, 'speech_seconds': 6.2} per window (the same probes KB-Whisper's choice uses)."""
    usable = [item for item in readings or () if item.get('language') and (item.get('speech_seconds') or 0) >= MIN_SPEECH_SECONDS]
    if len(usable) < 2:
        return False, 'för lite tal för att avgöra språket'
    norwegian = [item for item in usable if item['language'] in LANGUAGES and (item.get('probability') or 0) >= MIN_PROBABILITY]
    if len(norwegian) * 3 < len(usable) * 2:
        return False, 'ljudet hörs inte som norska'
    return True, f'{len(norwegian)} av {len(usable)} ljudfönster hörs som norska'
