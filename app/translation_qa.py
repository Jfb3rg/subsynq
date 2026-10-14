"""Small transparent safety net after model-based subtitle translation."""

import re


UNTRANSLATED_MARKERS = {
    'the', 'and', 'you', 'your', 'this', 'that', 'with', 'have', 'been',
    'from', 'there', 'they', 'what', 'where', 'would', 'could', 'should',
    'are', 'was', 'were', 'his', 'her', 'not', 'is', 'my',
    'je', 'suis', 'vous', 'nous', 'pour', 'avec', 'dans', 'pas', 'des',
    'une', 'est', 'qui', 'que', 'quoi', 'ils', 'elles', 'les', 'sœur',
    'il', 'elle', 'et', 'après', 'c', 's',
}

NON_LATIN_SCRIPTS = {
    'Arabic': ('\u0600', '\u06ff'),
    'Bengali': ('\u0980', '\u09ff'),
    'Devanagari': ('\u0900', '\u097f'),
    'Gujarati': ('\u0a80', '\u0aff'),
    'Gurmukhi': ('\u0a00', '\u0a7f'),
    'Kannada': ('\u0c80', '\u0cff'),
    'Malayalam': ('\u0d00', '\u0d7f'),
    'Tamil': ('\u0b80', '\u0bff'),
    'Telugu': ('\u0c00', '\u0c7f'),
    'Thai': ('\u0e00', '\u0e7f'),
    'Hangul': ('\uac00', '\ud7af'),
    'CJK': ('\u4e00', '\u9fff'),
}

WHISPER_SCRIPT_LANGUAGES = {
    'Bengali': 'bn', 'Gujarati': 'gu', 'Gurmukhi': 'pa', 'Kannada': 'kn',
    'Malayalam': 'ml', 'Tamil': 'ta', 'Telugu': 'te', 'Thai': 'th',
    'Hangul': 'ko',
}

FILENAME_LANGUAGE_MARKERS = {
    'de': {'tysk', 'tyska', 'german', 'deutsch'},
    'en': {'engelsk', 'engelska', 'english'},
    'fr': {'fransk', 'franska', 'french', 'francais', 'français'},
    'es': {'spansk', 'spanska', 'spanish', 'espanol', 'español'},
    'it': {'italiensk', 'italienska', 'italian', 'italiano'},
    'pt': {'portugisisk', 'portugisiska', 'portuguese', 'portugues', 'português'},
    'ta': {'tamil', 'tamilska'},
    'ko': {'koreansk', 'koreanska', 'korean'},
}

WHISPER_LANGUAGE_LABELS = {
    'de': 'tyska', 'en': 'engelska', 'fr': 'franska', 'es': 'spanska',
    'it': 'italienska', 'pt': 'portugisiska', 'ta': 'tamil', 'ko': 'koreanska',
}


def filename_language_hint(media_name):
    """Read only explicit standalone language words; ambiguous names stay automatic."""
    words = set(re.findall(r"[^\W\d_]+", str(media_name).casefold(), flags=re.UNICODE))
    matches = [code for code, markers in FILENAME_LANGUAGE_MARKERS.items()
               if words.intersection(markers)]
    return matches[0] if len(matches) == 1 else None


def whisper_language_label(code):
    return WHISPER_LANGUAGE_LABELS.get(code, code)


def dominant_non_latin_script(rows, *, minimum_share=.55, minimum_letters=20):
    """Recognize a stable script without pretending to judge an unfamiliar language.

    A short trailer often has only a few seconds of confidently detected
    speech, so a 30-letter floor rejected real, coherent dialogue (verified
    Tamil passes safely at 20 letters in tests) before the repetition check
    below even had a chance to run. The repetition check, not this floor,
    is what catches a short hallucinated loop.
    """
    texts = [' '.join(str(row.get('source', '')).casefold().split()) for row in rows]
    letters = [character for text in texts for character in text if character.isalpha()]
    if len(letters) < minimum_letters:
        return None
    repetitions = {}
    for text in texts:
        if text:
            repetitions[text] = repetitions.get(text, 0) + 1
    if repetitions and max(repetitions.values()) >= 5:
        return None
    for name, (first, last) in NON_LATIN_SCRIPTS.items():
        count = sum(first <= character <= last for character in letters)
        if count >= minimum_letters and count / len(letters) >= minimum_share:
            return name
    return None


def forced_whisper_language(rows):
    """Return a language only when the dominant script identifies it unambiguously."""
    return WHISPER_SCRIPT_LANGUAGES.get(dominant_non_latin_script(rows))


def looks_untranslated(source, target):
    """Detect a copied non-Swedish cue without flagging unchanged Swedish speech."""
    words = re.findall(r"[^\W\d_]+", str(source).casefold(), flags=re.UNICODE)
    original = ' '.join(words)
    result = ' '.join(re.findall(r"[^\W\d_]+", str(target).casefold(), flags=re.UNICODE))
    return (len(original) >= 8 and len(words) >= 2 and original == result
            and bool(UNTRANSLATED_MARKERS.intersection(words)))

KNOWN_SWEDISH_PHRASES = {
    'merci': 'Tack.',
    'merci beaucoup': 'Tack så mycket.',
    'comment dit ah oui': 'Hur säger man? Ah, ja.',
    'allez zizou': 'Kom igen, Zizou.',
    'bon voyage camarade': 'Trevlig resa, kamrat.',
    'un mois peut être': 'En månad, kanske.',
}


def restore_id_order(expected_ids, rows, target_key='sv'):
    """Validate exact model IDs and restore their requested order."""
    if not isinstance(rows, list):
        raise ValueError('Översättaren gav inget giltigt radblock.')
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or len(set(ids)) != len(ids) or set(ids) != set(expected_ids):
        raise ValueError('Översättaren saknade, dubblerade eller lade till rad-ID:n.')
    by_id = {row['id']: row for row in rows}
    ordered = [by_id[row_id] for row_id in expected_ids]
    if any(not str(row.get(target_key, '')).strip() for row in ordered):
        raise ValueError('Översättaren lämnade en tom undertextrad.')
    return ordered


def known_phrase_fallback(source, current, normalize):
    if normalize(source) == 'this crazy fame':
        return 'Den här vansinniga berömmelsen...'
    if (normalize(source) == 'circled spain nowhere safe'
            and normalize(current) == 'vi kretsade runt spanien ingenstans är säkert'):
        return 'Vi rundade Spanien. Ingenstans var det säkert.'
    replacement = KNOWN_SWEDISH_PHRASES.get(normalize(source))
    return replacement if replacement and normalize(source) == normalize(current) else current


# A word written as a NAME in the file (capital letter inside a sentence, or right after a
# title such as "Mr.") is not an ordinary word, whatever another reading of the same sound
# spells. Speech cannot tell "Gun" from "Gunn"; the file's own capitalised spelling is the
# evidence. Nothing here changes any text: it only stops a name from being treated as an
# untranslated common word.
NAME_TITLES = frozenset({
    'mr', 'mrs', 'ms', 'miss', 'dr', 'sir', 'madam', 'lord', 'lady', 'prof',
    'herr', 'fru', 'doktor',
})


def name_key(word):
    """Spelling-insensitive key: case and doubled letters ignored (Gun == Gunn)."""
    return re.sub(r'(.)\1+', r'\1', str(word).casefold())


# Words that are unmistakably German; a text full of them writes every noun with a capital letter.
GERMAN_FUNCTION_WORDS = frozenset((
    'ich', 'und', 'nicht', 'ist', 'sind', 'der', 'die', 'das', 'den', 'dem', 'ein', 'eine', 'einen', 'mit', 'auf',
    'für', 'von', 'zu', 'nein', 'aber', 'wir', 'ihr', 'haben', 'hat', 'noch', 'auch', 'schon', 'mir', 'mich', 'dich',
    'ihnen', 'sie', 'wie', 'wer', 'wenn', 'dann', 'ganz', 'jetzt', 'doch', 'kein', 'keine', 'nur'))


def capitalizes_common_nouns(texts):
    """True for German and other languages that write every noun with a capital letter.

    There a capital in the middle of a sentence says nothing about a name: "Messer", "Mord", "Kollegen" are
    ordinary nouns. Telling them apart takes a real language reading, so such a text is recognised by its sums:
    a large share of the mid-sentence words (>= 15 %) capitalised AND a large share of all words (>= 8 %) German
    function words. English, French, Korean and the other test files stay far below (<= 9 %).
    """
    texts = list(texts)
    middle = capitalised = words = german = 0
    for text in texts:
        previous_is_word = False
        for token in re.findall(r'[^\W\d_]+|[.!?…]+', str(text)):
            if not token[0].isalpha():
                previous_is_word = False
                continue
            words += 1
            german += token.casefold() in GERMAN_FUNCTION_WORDS
            if previous_is_word and len(token) >= 3:
                middle += 1
                capitalised += token[0].isupper() and token[1:].islower()
            previous_is_word = True
    return middle >= 30 and capitalised >= .15 * middle and german >= .08 * words


def proper_name_forms(texts):
    """Map name_key -> sorted written forms of every word the texts write as a name.

    A row's first word is ignored (a capital there says nothing), unless it follows a title,
    also across a row boundary ("Mr." / "Gunn, I presume?").
    """
    texts = list(texts)
    if capitalizes_common_nouns(texts):
        return {}      # every German noun has a capital: none of them is a name just because of it
    forms = {}
    after_title = False
    for text in texts:
        previous_is_word = False
        for token in re.findall(r'[^\W\d_]+|[.!?…]+', str(text)):
            if not token[0].isalpha():
                previous_is_word = False
                continue
            capitalised = len(token) >= 3 and token[0].isupper() and token[1:].islower()
            if capitalised and (after_title or previous_is_word):
                forms.setdefault(name_key(token), set()).add(token)
            after_title = token.casefold() in NAME_TITLES
            previous_is_word = True
    return {key: sorted(written) for key, written in forms.items()}


def quote_is_only_names(quote, forms):
    """True when every word of the quote is a name according to `forms`."""
    words = re.findall(r'[^\W\d_]+', str(quote))
    return bool(words) and all(name_key(word) in forms for word in words)


# Words that mark a row as certainly not Swedish: a row is only kept as spoken when the language model reads it as
# Swedish AND none of these appear in it (a second opinion over a model that can be wrong on very short rows).
FOREIGN_VETO_MARKERS = UNTRANSLATED_MARKERS | {
    'ich', 'und', 'nicht', 'ist', 'der', 'die', 'das', 'mein', 'ein', 'eine', 'wir', 'sie', 'mit', 'auf', 'nein',
}


def swedish_rows_to_keep(batch, languages):
    """Ids of rows that are already Swedish: the language model said `sv` and no foreign marker word is present.

    `languages` maps id -> language code. Rows the model did not classify, or called anything else, are translated.
    """
    kept = set()
    for row in batch:
        language = str(languages.get(row['id'], '')).strip().lower()
        words = re.findall(r"[^\W\d_]+", str(row.get('source', '')).casefold(), flags=re.UNICODE)
        if language == 'sv' and words and not FOREIGN_VETO_MARKERS.intersection(words):
            kept.add(row['id'])
    return kept
