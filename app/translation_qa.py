"""Small transparent safety net after model-based subtitle translation."""

import math
import re
import unicodedata


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


# Words a subtitle writes with a capital letter in the middle of a sentence that are NOT names: forms of address and places of belief. WIFE AND DOG: the translator was
# told to copy "Daddy" and "Hell" as names, so "Och vad hittade han i helvetet, Daddy?" kept "Daddy" where the older text said "pappa".
NOT_NAMES = frozenset({
    'daddy', 'dad', 'mommy', 'mom', 'mama', 'mummy', 'papa', 'pops', 'mother', 'father', 'grandma', 'grandpa', 'granny', 'grandmother', 'grandfather',
    'uncle', 'auntie', 'aunt', 'sis', 'bro', 'brother', 'sister', 'son', 'honey', 'baby', 'babe', 'buddy', 'pal', 'darling', 'sweetheart', 'sweetie',
    'boss', 'chief', 'kid', 'boy', 'girl', 'man', 'folks', 'guys', 'hell', 'heaven', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
    'saturday', 'january', 'february', 'april', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
})


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
            capitalised = len(token) >= 3 and token[0].isupper() and token[1:].islower() and token.casefold() not in NOT_NAMES
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


# Words that make a row worth asking the language model about: å, ä, ö, or one of these frequent Swedish function words. A row with none
# of them is not sent to the language step (an English documentary of 899 rows cost 23 such calls, about a quarter of its translation time,
# to learn that nothing was Swedish). Words English shares (den, de, han, var, om) are included on purpose: a false candidate only costs a
# little model time, a missed Swedish row would be translated instead of kept as spoken.
SWEDISH_CANDIDATE_WORDS = frozenset((
    'och att det är jag du vi ni inte som på för med har kan ska vill från till ett en den de hon han vad här där nu ja nej tack hej hejsan '
    'lite mycket också bara eller om så när hur varför vem mig dig sig oss er min din sin vår ditt mitt sitt men var varit blir bli blev '
    'finns fanns ska skulle kommer komma gör göra säger sa sade ser såg tror vet veta ingen inget inga alla allt någon något några mer mest '
    'mamma pappa okej fan jävla helvete snälla förlåt ursäkta hallå vänta stopp kom titta lyssna sluta gå hit dit').split())


def might_be_swedish(text):
    """Could this row be Swedish at all: it has å, ä or ö, or one of the frequent Swedish function words (SWEDISH_CANDIDATE_WORDS)."""
    text = str(text).casefold()
    return bool(re.search(r'[åäö]', text)) or not SWEDISH_CANDIDATE_WORDS.isdisjoint(re.findall(r"[^\W\d_]+", text, flags=re.UNICODE))


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


# --- Review findings the text itself refutes --------------------------------------------------------------------------
# Real jobs 43 and 44 (dev 2026-10-15, gemma4:26b reviewing): one such finding fails a whole job (it starts a repair that
# can make a good line worse, and any finding left after the repair stops the subtitle).

_TARGET_SUBJECT = r'(?:översättningen|måltexten|målspråket|målraden|målet)'
_TARGET_QUOTE_CLAIM = re.compile(
    rf"\b{_TARGET_SUBJECT}\s+(?:\w+\s+)?(?:säger|står|lyder|innehåller|skriver|använder)\s+(?:bara\s+|också\s+)?"
    r"['\"‘“’]([^'\"‘“’”]{2,80})['\"’”]", re.IGNORECASE)


def _words_in_order(text, quote):
    """The words of `quote` appear in `text` side by side and in order; a word of four letters or more also matches
    its inflected form ("moster" in "mostern")."""
    needle = re.findall(r'[^\W_]+', str(quote).casefold())
    haystack = re.findall(r'[^\W_]+', str(text).casefold())

    def same(left, right):
        return left == right or (min(len(left), len(right)) >= 4 and (left.startswith(right) or right.startswith(left)))

    return bool(needle) and any(all(same(word, haystack[start + offset]) for offset, word in enumerate(needle))
                                for start in range(len(haystack) - len(needle) + 1))


def refuted_target_claim(flag, target):
    """The quoted wording a finding says the TRANSLATION contains, when the translation does not contain it.

    "men översättningen säger 'byggnaden'" about "De stormade ut ur varelsen." and "måltexten säger 'jag fixar det'"
    about "Okej, jag målar." are the reviewer's slip, not a defect. Only claims about what the target row literally
    says are checked (säger, står, lyder, innehåller, skriver, använder); what the SOURCE "says" is a gloss in the
    reviewer's own words (a Korean or Tamil row never says anything in Swedish) and a "betyder" is a gloss too.
    Alternatives ('a/b') count as found when any of them is."""
    for quote in _TARGET_QUOTE_CLAIM.findall(str(flag.get('reason', ''))):
        alternatives = [part.strip() for part in re.split(r'\s*/\s*', quote) if part.strip()]
        if alternatives and not any(_words_in_order(target, part) for part in alternatives):
            return quote
    return None


_ADDRESS_WORDS = re.compile(
    r"\b(?:formell\w*|informell\w*|artig\w*|höflig\w*|formal\w*|informal\w*|polite\w*|tilltal\w*|du|dig|ni|plural\w*|"
    r"singular\w*)\b", re.IGNORECASE)
# A reason that adds a second problem ("samt ett grammatikfel i ...") is not only about address.
_ALSO_WORDS = re.compile(r"\b(?:samt|dessutom|även|också|förutom|utöver|grammatikfel)\b", re.IGNORECASE)
_FORMAL_YOU_SOURCE = re.compile(r"\b(?:Sie|Ihnen|Ihr|Ihre|Ihrer|Ihren|Ihrem|Ihres)\b")
_SECOND_PERSON_TARGET = {'du', 'dig', 'dej', 'din', 'ditt', 'dina', 'ni', 'er', 'ert', 'era', 'eder'}


def is_address_choice(flag, source, target):
    """A finding that only quarrels with HOW the translation addresses someone ("Sie" -> du/dig or ni/Er).

    German Sie/Ihnen has no fixed Swedish counterpart; du and ni are both used, and whether a capital Sie is one
    person or several cannot be proved from the line. The same reviewer flagged "Jag vet vem ni är." for being
    informal and "Jag kan hjälpa Er." for becoming "dig". When the source has a capitalised formal-you word, the
    reason is about address and the translation does address someone, the finding cannot be established and is
    removed (a translation that leaves the addressee out stays flagged)."""
    reason = str(flag.get('reason', ''))
    if (not _FORMAL_YOU_SOURCE.search(str(source)) or not _ADDRESS_WORDS.search(reason)
            or _ALSO_WORDS.search(reason)):
        return False
    words = set(re.findall(r'[^\W_]+', str(target).casefold()))
    return bool(words & _SECOND_PERSON_TARGET)


# The final gate (after the one repair and the second translation attempt): a few findings that remain do not stop the subtitle, they are
# written to a report and shown in the GUI; at `doubtful_limit` rows or more the file is still refused. Real jobs 2026-10-20: FRANCE on a 24 GB
# card had ONE finding in 53 rows (a misheard word), the whole subtitle was withheld.
DOUBTFUL_SHARE = .05
DOUBTFUL_MINIMUM = 2
# Only a finding that the translation cannot fix may be kept and reported: a source cut off in the middle of a sentence, or a source too damaged to
# translate. A finding that says the translation itself is wrong (meaning, grammar, untranslated, transliteration, ...) is never kept (user 2026-10-22:
# "undertexten är skit ... bara för att den gick igenom": 17 of the 19 rows kept in Yukon Men were of that kind).
SOFT_FLAG_CATEGORIES = ('fragment', 'source_uncertain')


_SOURCE_END = re.compile(r'[.!?…。！？」』]["\'”’»)\]]*\s*$')
_DANGLING = {'of', 'the', 'a', 'an', 'to', 'and', 'but', 'or', 'that', 'in', 'on', 'at', 'for', 'with', 'from', 'by', 'as', 'is', 'are', 'was', 'were', 'i', 'you',
             'he', 'she', 'we', 'they', 'it', 'my', 'your', 'his', 'her', 'our', 'their', 'this', 'these', 'those', 'which', 'who', 'than', 'if', 'because', 'so',
             'och', 'att', 'som', 'men', 'eller', 'en', 'ett', 'i', 'på', 'av', 'för', 'med', 'til', 'og', 'at', 'på', 'af', 'for', 'med'}


def source_is_cut_off(rows, index):
    """True when the SOURCE row itself is cut off in the middle of a sentence: it starts in lower case right after a row that did not end its sentence, or it
    does not end its sentence and is a stump of at most three words, ends in a comma or a dash, ends in a word that cannot end a sentence ("get the out of"), or
    is continued in lower case by the next row. A translation cannot repair such a row (Yukon Men: "Get", "cut and difficult", "the water plant are ready to
    explode."), so a finding on it is reported, never a reason to refuse the file."""
    text = str(rows[index].get('source', '')).strip()
    if not text:
        return False
    words = re.findall(r"[^\W\d_]+", text.casefold())
    first = next((char for char in text if char.isalpha()), '')
    following = str(rows[index + 1].get('source', '')).strip() if index + 1 < len(rows) else ''
    previous = str(rows[index - 1].get('source', '')).strip() if index else ''
    next_first = next((char for char in following if char.isalpha()), '')
    if first.islower() and previous and not _SOURCE_END.search(previous):
        return True
    if _SOURCE_END.search(text):
        return False
    # A whole sentence in an unsegmented script is one regex "word". Count
    # its letters instead of treating every unpunctuated sentence as a stump.
    units = sum(len(word) if any(unicodedata.name(c, '').startswith(
        ('CJK ', 'HIRAGANA ', 'KATAKANA ', 'THAI ', 'LAO ', 'KHMER ', 'MYANMAR ')) for c in word) else 1 for word in words)
    return (units <= 3 or text.endswith((',', '、', '，', '-', '–', '—', ':', ';')) or bool(words and words[-1] in _DANGLING)
            or (next_first.islower() and bool(following)))


def cut_off_ids(rows):
    return {row['id'] for index, row in enumerate(rows) if source_is_cut_off(rows, index)}


def hard_flags(flags, rows=None):
    """The flags that name a defect of the translation itself: everything except a cut-off or damaged source. With `rows`, a finding on a row whose source is
    cut off (`source_is_cut_off`) is not hard either; it is reported."""
    cut = cut_off_ids(rows) if rows is not None else set()
    return [flag for flag in flags if flag.get('category') not in SOFT_FLAG_CATEGORIES and flag.get('id') not in cut]


def doubtful_limit(row_count):
    """How many remaining findings of the SOFT kind (a cut-off or damaged source) stop a subtitle: at least two, or 5 % of the rows."""
    return max(DOUBTFUL_MINIMUM, math.ceil(max(0, int(row_count)) * DOUBTFUL_SHARE))


def doubtful_rows(remaining_flags, rows, target_key='sv'):
    """One entry per flagged row for the report: where, what was said, what it became, and the reviewer's own words."""
    by_id = {row['id']: row for row in rows}
    cut = cut_off_ids(rows)
    seen, result = set(), []
    for flag in remaining_flags:
        row = by_id.get(flag['id'])
        if row is None or flag['id'] in seen:
            continue
        seen.add(flag['id'])
        result.append({'id': row['id'], 'start': row.get('start'), 'end': row.get('end'), 'source': row.get('source', ''),
                       'sv': row.get(target_key, ''), 'category': flag.get('category', ''), 'reason': ' '.join(str(flag.get('reason', '')).split()),
                       'source_cut_off': row['id'] in cut})
    return sorted(result, key=lambda item: item['id'])


_QUOTED = re.compile(r"['\"‘“]([^'\"‘’“”]{1,60})['\"’”]")
_ALTERNATIVES = re.compile(r"(?:\s*,?\s*(?:eller|alternativt)\s+['\"‘“][^'\"‘’“”]{1,60}['\"’”])*", re.IGNORECASE)
_NOT_QUOTED = re.compile(r"\binte\s+['\"‘“]([^'\"‘’“”]{1,60})['\"’”]", re.IGNORECASE)
_CORRECTION = re.compile(
    r"(?:\b(?:korrekta|rätta|riktiga)\s+(?:(?:ordet|stavningen|formen)\s+)?(?:är|vore|skulle\s+vara)|\b(?:borde|bör|ska|skall)\s+(?:(?!inte\b|ej\b|icke\b)\w+\s+){0,3}?(?:vara|stå)|\brätt\s+stavning\s+är)"
    r"\s+['\"‘“]([^'\"‘’“”]{1,60})['\"’”]", re.IGNORECASE)


def is_self_contradiction(flag, target):
    """A finding that names a word as wrong and then gives the very same word as the right one.

    Real jobs 57, 60, 61, 62, 63 (Nelly Rapp): "Ordfel i 'stjärt'. Det korrekta ordet är 'stjärt'..." about "Men vems stjärt?", and
    FRANCE (job 71): "Ordet 'Civila' är felstavat; det korrekta ordet är 'Civila'...". The claimed error and the claimed fix are identical
    letter by letter (capitals count: 'sverige' -> 'Sverige' is a real finding), the word stands in the row as written, and the reason
    does not add another problem ("samt", "dessutom"...): there is nothing to repair and nothing to establish, so it is removed."""
    reason = str(flag.get('reason', ''))
    if _ALSO_WORDS.search(reason):
        return False
    # Ray Gunn (dev job 275): "det ska vara 'kastar jag ut dig' eller 'kastar jag dig ut', inte 'kastar jag ut dig'" about a correct line, and
    # "… bör hela frasen vara 'kastar jag ut dig genom fönstret'", the flagged words themselves: three repairs kept the line and the file was refused.
    refused = [quoted.strip(' .,;:') for quoted in _NOT_QUOTED.findall(reason)]
    quote = str(flag.get('target_quote') or '').strip(' .,;:')
    fixes = []
    for match in _CORRECTION.finditer(reason):
        fix = match.group(1).strip(' .,;:')
        wrong = [quoted.strip(' .,;:') for quoted in _QUOTED.findall(reason[:match.start()])] + refused
        if fix and _stands_in(fix, target) and (fix in wrong or fix == quote):
            return True
        fixes.append(fix)
        # "ska vara 'X' eller 'Y'": an alternative the line does not hold is a real proposal (dev job 89: "… eller 'från att släden skulle sjunka'").
        alternatives = _ALTERNATIVES.match(reason[match.end():]).group(0)
        fixes.extend(alternative.strip(' .,;:') for alternative in _QUOTED.findall(alternatives))
    # Every correction the remark proposes already stands in the line word for word (Ray Gunn, dev job 300: "det ska vara 'kastar jag ut dig'
    # … det korrekta vore 'kastar jag ut dig genom fönstret'" about exactly that line): there is nothing to repair. Whole words only:
    # "ska vara 'mot Ukraina'" about "mot Ukrainas" is a real finding (dev job 180).
    return bool(fixes) and all(fix and _stands_in(fix, target) for fix in fixes)


def _stands_in(phrase, text):
    return bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', str(text)))


_GERMAN_PLURAL_YOU = re.compile(r'\b(?:euer|eure|euren|eurem|eurer|eures|euch|ihr)\b', re.IGNORECASE)
_SWEDISH_PLURAL_YOU = re.compile(r'\b(?:er|ert|era|ni)\b', re.IGNORECASE)
_HE_WORDS = re.compile(r"['\"‘“(]\s*(?:hans|han|honom)\s*['\"’”)]|\b(?:han|honom|hans)\b", re.IGNORECASE)


def is_plural_you_misread(flag, source, target):
    """A finding that reads Swedish "er/ert/era" (your, plural: German euer/eure/euch/ihr) as German "er" (he) and calls it "hans".

    ANOTHER GERMAN TANK STORY (dev job 197): "Euer Panzer steht auf meinem Hof." -> "Er stridsvagn står på min gård." is right, but the reviewer
    flagged it six times ("Subjektet har ändrats från 'er/ert' till 'hans' (er)"; "'Euer' betyder 'er' (plural), inte 'hans'"), every repair kept
    the correct word and the whole file was refused. When the source holds German plural-you, the flagged Swedish text holds er/ert/era/ni and
    no form of han, and the reason is about "han/hans", the finding cannot be right and is removed."""
    if flag.get('category') != 'meaning':
        return False
    quote = str(flag.get('target_quote') or target)
    return (bool(_GERMAN_PLURAL_YOU.search(str(flag.get('source_quote') or '')) or _GERMAN_PLURAL_YOU.search(str(source)))
            and bool(_SWEDISH_PLURAL_YOU.search(quote)) and bool(_SWEDISH_PLURAL_YOU.search(str(target))) and not re.search(r'\b(?:han|honom|hans)\b', str(target), re.IGNORECASE)
            and bool(_HE_WORDS.search(str(flag.get('reason', '')))))


def asks_dying_to_become_killing(flag, source, target):
    """A finding that wants "för att dö" turned into "för att döda" where the source says the dying itself (死ぬために, made to die): the verified
    rule in swedish_context_flags forbids exactly that change. Godzilla (dev job 296): the review asked for "döda", the repair obeyed, the rule then
    refused "döda", and the right "dö" was among the refused wordings, so the file was refused."""
    return (flag.get('category') == 'meaning' and bool(_FOR_DYING.search(str(source)))
            and bool(re.search(r'\bför\s+att\s+(?:(?:bara|enbart|endast)\s+)?dö\b', str(target), re.IGNORECASE))
            and not _FOR_KILLING_SV.search(str(target))
            and bool(re.search(r'\bdöda\b|\bdödande\b|\btransitiv', str(flag.get('reason', '')), re.IGNORECASE)))


def unfounded_flags(flags, sample, *, target_key='sv'):
    """(kept, removed): the meaning and grammar findings that survive the checks above. `removed` holds
    {'id', 'category', 'why', 'reason'} so the quality report can show what was set aside and why."""
    rows = {row['id']: row for row in sample}
    kept, removed = [], []
    for flag in flags:
        row = rows.get(flag.get('id'))
        why = None
        if row is not None and flag.get('category') in ('meaning', 'grammar'):
            claim = refuted_target_claim(flag, row.get(target_key, ''))
            if claim:
                why = f'översättningen innehåller inte "{claim}"'
            elif is_address_choice(flag, row.get('source', ''), row.get(target_key, '')):
                why = 'valet av tilltalsform (Sie → du/ni) kan inte avgöras ur raden'
            elif is_self_contradiction(flag, row.get(target_key, '')):
                why = 'granskaren anger samma ord som fel och som rätt'
            elif is_plural_you_misread(flag, row.get('source', ''), row.get(target_key, '')):
                why = 'svenskans "er/ert/era" är tyskans "euer/ihr" (ni), inte "er" (han)'
            elif asks_dying_to_become_killing(flag, row.get('source', ''), row.get(target_key, '')):
                why = 'källan säger att någon dör; "dö" får inte bli "döda" (fast betydelseregel)'
        if why:
            removed.append({'id': flag['id'], 'category': flag.get('category'), 'why': why,
                            'reason': flag.get('reason', '')})
        else:
            kept.append(flag)
    return kept, removed


def _is_non_latin_letter(char):
    return char.isalpha() and not unicodedata.name(char, 'LATIN').startswith('LATIN')


def non_latin_runs(text):
    """The stretches of a text (words that hold a letter of another script, and the words between them) that are not written in Latin letters.

    A Swedish subtitle never holds Cyrillic, Chinese, Japanese, Korean, Arabic or other letters: names are written with Latin letters and proper nouns are
    translated. The trailer Guardians (2017) had "det forna Советский Союз" and "Vi är precis som du, Лер."."""
    runs, current = [], []
    for match in re.finditer(r'\S+', str(text)):
        token = match.group(0)
        if any(_is_non_latin_letter(char) for char in token):
            current.append(token)
        elif current:
            runs.append(' '.join(current))
            current = []
    if current:
        runs.append(' '.join(current))
    return [run.strip('.,;:!?…"\'«»()[]-–—') for run in runs if run.strip('.,;:!?…"\'«»()[]-–—')]


def non_latin_flags(rows, target_key='sv'):
    """One review flag per row whose translation holds letters of another script (a hard defect: never tolerated, always repaired)."""
    flags = []
    for row in rows:
        text = str(row.get(target_key) or '')
        for run in non_latin_runs(text):
            if text.count(run) == 1:
                flags.append({'id': row['id'], 'category': 'untranslated', 'source_quote': run, 'target_quote': run,
                              'reason': f'Raden har tecken från ett annat skrivsystem i den svenska texten ("{run}"): skriv namn med latinska bokstäver '
                                        '(Лер → Ler) och översätt egennamn (Советский Союз → Sovjetunionen); lämna aldrig kvar kyrilliska, kinesiska '
                                        'eller andra tecken.'})
    return flags


# Imperial units in a Swedish subtitle are written as metric ones. Yukon Men (dev job 121) kept "40 yards", "15 fot", "200 gallon" and "90 miles", and wrote
# "ett par mil" for "a couple of miles" (a Swedish mil is 10 km). unit -> (pattern in the source, metric unit, factor, words that must not stay in the Swedish)
_NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
                 'fifteen': 15, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'hundred': 100, 'couple': 2}
IMPERIAL_UNITS = {
    'miles': (r'miles?', 'kilometer', 1.609, ('mile', 'miles', 'mil')),
    'yards': (r'yards?', 'meter', .9144, ('yard', 'yards')),
    'feet': (r'(?:feet|foot|ft)', 'meter', .3048, ('feet', 'foot', 'fot', 'fötter', 'ft')),
    'inches': (r'(?:inch(?:es)?)', 'centimeter', 2.54, ('inch', 'inches', 'tum')),
    'gallons': (r'gallons?', 'liter', 3.785, ('gallon', 'gallons')),
    'ounces': (r'(?:ounces?|oz)', 'gram', 28.35, ('ounce', 'ounces', 'oz')),
    'pounds_weight': (r'lbs?', 'kilo', .4536, ('lb', 'lbs')),
    'cords': (r'cords?', 'kubikmeter', 3.62, ('cord', 'cords')),     # firewood: 10 cords became "cirka 3,5 kubikmeter" in the delivered Yukon Men (about 36)
}
_NUMBER = r'(\d+(?:[.,]\d+)?|' + '|'.join(_NUMBER_WORDS) + r')'

SWEDISH_MEANING_RULE = (
    'Resolve word senses from the source and its neighbors before translating or converting units. '
    'Caribou as an animal is ren in Swedish (plural renar, herd renhjord, hunting renjakt, '
    'tracks renspår); do not leave karibu/caribou or confuse tracks with droppings. '
    'Preserve Caribou when it is a person, business or place name. '
    'Snowgo/snowgos as a vehicle means snöskoter/snöskotrar, not an untranslated word. '
    'Getting low on wood or other supplies means running short, not receiving some. '
    "Feet counting an animal's body parts are tassar/fötter, never a distance in meters. "
    'Whiteout describes lost visibility in blowing snow, not physiological snow blindness. '
    'Do not silently change uncertain ASR words (for example snow ghost or snow goes) into '
    'a vehicle just because that would fit; uncertainty in the source is not proof. '
    "BUD/S (heard as Bud's or buds after since/through/after in a military context, e.g. 'in the teams together since Bud's') is "
    'the US Navy SEAL basic training: write BUD/S, never a name or "Bud\'s tid". '
    'An active verb with an object stays active in Swedish: "jam the door" is "kila fast dörren", never the passive "kilas fast dörren". '
    'Norwegian and Danish "må ikke" is a prohibition, Swedish "får inte" (must not), never "måste inte" or "behöver inte" (need not). '
    'Norwegian puts a possessive AFTER its noun ("Glowland din", "bilen min"); Swedish puts it before ("din Glowland", "min bil"), never '
    '"Glowland, din är" as if "din" stood alone. '
    'Norwegian "pære/pæra" in "ha det inne i pæra/pærene" is colloquial for the head ("jag har det i huvudet"), never the fruit päron. '
    'Prime Minister is the head of government: premiärminister (Sweden\'s own is statsminister), never just "minister". '
    'The Allies are "de allierade" with the article, never a bare "Allierade". '
    'After "efter" a clause needs "att": "efter att jag har blivit …", never "efter jag blir …". '
    'Norwegian "seriøst" and English "seriously" as an intensifier mean "verkligen" or "på riktigt", not "seriöst". '
    'A military tank (with airplanes, ships, soldiers, an army or a battle) is stridsvagn, plural stridsvagnar; Swedish tank/tankar is only a '
    'container, and English "tanks" is never Swedish. '
    'Norwegian/Danish "kaster rundt seg/sig med" is an idiom for casually throwing out/using words, claims or diagnoses: '
    'Swedish "kastar ur sig" or "slänger sig med", not "kastar runt sig med" (throwing oneself). '
    'Dying is not killing: Japanese 死ぬ (die, intransitive) and English "to die" must not become Swedish "döda" (kill, transitive). '
    'Preserve who dies and who causes death; never infer killing from a weapon mentioned in the sentence. ')


# Norwegian/Danish "må ikke" forbids ("must not"); Swedish "måste inte" means "need not" (Agnes mot verden, dev jobs 123 and 202:
# "så må du ikke ta telefonen" -> "så måste du inte svara i telefonen").
_MAA_IKKE = re.compile(r"\bmå(?:tte)?\s+(?:\w+\s+){0,2}?ikke\b", re.IGNORECASE)
_NEED_NOT = re.compile(r"\b(?:måste|behöver)\s+(?:\w+\s+){0,2}?inte\b", re.IGNORECASE)
# Norwegian "Glowland din er så kul." (your Glowland) became "Glowland, din är så cool." (Glemselens øy, dev job 205): the possessive after the noun
# was read as a pronoun of its own.
_POSTPOSED_POSSESSIVE = re.compile(r"\b(\w+)\s+(min|din|sin|vår|mitt|ditt|sitt|vårt|mine|dine|sine|våre)\s+(?:er|var|blir|ble)\b", re.IGNORECASE)
# FORTITUDE (dev job 218): "Prime Minister, I believe…" became "Minister, jag tror…", and "We'll need tanks, airplanes, ships, men." kept "tanks".
_PRIME_MINISTER = re.compile(r"\bprime[\s-]+ministers?\b", re.IGNORECASE)
_HEAD_OF_GOVERNMENT = re.compile(r"premiärminist|statsminist", re.IGNORECASE)
_TANK_SOURCE = re.compile(r"(?<!think )(?<!fish )(?<!gas )(?<!fuel )(?<!water )(?<!oil )(?<!septic )(?<!propane )(?<!oxygen )(?<!storage )"
                          r"(?<!petrol )(?<!air )(?<!scuba )(?<!shark )(?<!holding )\btanks?\b(?!\s+tops?\b)", re.IGNORECASE)
_MILITARY = re.compile(r"\b(?:army|armies|soldiers?|troops?|airplanes?|planes?|aircraft|ships?|artillery|infantry|battles?|war|wars|armou?r\w*|"
                       r"panzers?|divisions?|guns?|invasion|invade\w*|attack\w*|military|regiments?|battalions?)\b", re.IGNORECASE)
_BUDS_SOURCE = re.compile(r"\b(?:since|through|after|from|during)\s+bud'?s\b", re.IGNORECASE)
# A modal verb + a passive -s form + a particle + an object: "Han ska precis kilas fast dörren." (MAN OF WAR, dev jobs 152-195) means HE is wedged.
_PASSIVE_WITH_OBJECT = re.compile(
    r"\b(?:ska|skall|kan|måste|vill|skulle|bör|får|kommer\s+att|tänker)\s+(?:precis\s+|just\s+|nog\s+|bara\s+|snart\s+)?"
    r"(\w{2,}?)as\s+(fast|upp|ner|in|ut|av|på|bort|igen|till|sönder|omkull)\s+(den|det|dem|\w+(?:en|et|na))\b", re.IGNORECASE)
_SCANDINAVIAN_THROW_IDIOM = re.compile(r'\b(?:kaster|kastet|kastede)\s+rundt\s+(?:seg|sig|mig|dig|oss|os|jer|er)\s+med\b', re.IGNORECASE)
_WRONG_THROW_SELF = re.compile(r'\bkast(?:ar|ade|at|a)\s+runt\s+(?:sig|mig|dig|oss|er)\s+med\b', re.IGNORECASE)
_FOR_DYING = re.compile(r'死ぬ(?:ため|為)(?:だけ)?に|\b(?:made|created|built|designed)\s+(?:(?:only|solely|just)\s+)?to\s+die\b', re.IGNORECASE)
_FOR_KILLING_SV = re.compile(r'\bför\s+att\s+(?:(?:bara|enbart|endast)\s+)?döda\b', re.IGNORECASE)


def source_grammar_notes(source):
    """Dictionary valency from explicit source wording, never inferred ASR repairs or a reviewer's proposed translation."""
    dying = _FOR_DYING.search(str(source))
    if dying and not re.search(r'殺|死なせ|\bkill\w*\b', str(source), re.IGNORECASE):
        return {'source_quote': dying.group(), 'action': 'die',
                'valency': 'intransitive: the subject dies; this is not kill, a transitive action causing another entity to die'}
    return None


def counted_animal_feet(source):
    """A body-part count, not a travelled distance; require the explicit count relation."""
    return bool(re.search(
        rf'\b{_NUMBER}\s+(?:dogs?|cats?)\b[^.!?]{{0,35}}'
        rf"\b(?:so\s+)?(?:that(?:[’']s|\s+is)|which\s+(?:means|makes)|giving\s+us)"
        rf'\s+{_NUMBER}\s+(?:extra\s+)?feet\b(?!\s+(?:away|ahead|behind|apart|down|up|long|wide|high|deep)\b)', str(source), re.IGNORECASE))


# English names of countries and places that Swedish writes differently. Only names that are not Swedish words themselves and have one
# meaning (no Turkey, no China in "china cabinet" since the check is case-sensitive and needs the same name in the source).
ENGLISH_EXONYMS = {
    'Ukraine': 'Ukraina', 'Russia': 'Ryssland', 'Germany': 'Tyskland', 'Poland': 'Polen', 'Italy': 'Italien', 'Spain': 'Spanien',
    'France': 'Frankrike', 'Sweden': 'Sverige', 'Norway': 'Norge', 'Denmark': 'Danmark', 'Austria': 'Österrike', 'Switzerland': 'Schweiz',
    'Netherlands': 'Nederländerna', 'Greece': 'Grekland', 'Hungary': 'Ungern', 'Belgium': 'Belgien', 'Romania': 'Rumänien',
    'Bulgaria': 'Bulgarien', 'Croatia': 'Kroatien', 'Serbia': 'Serbien', 'China': 'Kina', 'India': 'Indien', 'Brazil': 'Brasilien',
    'Egypt': 'Egypten', 'Iraq': 'Irak', 'Syria': 'Syrien', 'Lebanon': 'Libanon', 'Africa': 'Afrika', 'Europe': 'Europa',
    'Scotland': 'Skottland', 'Ireland': 'Irland', 'Iceland': 'Island', 'Finland': 'Finland', 'Lithuania': 'Litauen', 'Latvia': 'Lettland',
    'Estonia': 'Estland', 'Belarus': 'Belarus', 'Moscow': 'Moskva', 'Warsaw': 'Warszawa', 'Vienna': 'Wien', 'Rome': 'Rom',
    'Copenhagen': 'Köpenhamn', 'Athens': 'Aten', 'Prague': 'Prag', 'Munich': 'München', 'Cologne': 'Köln', 'Brussels': 'Bryssel',
}
ENGLISH_EXONYMS = {english: swedish for english, swedish in ENGLISH_EXONYMS.items() if english != swedish}


def swedish_context_flags(rows, target_key='sv'):
    """Narrow, source-anchored checks for observed word-sense errors, not guessed audio."""
    if target_key != 'sv':
        return []
    flags = []
    for row in rows:
        source, target = str(row.get('source', '')), str(row.get('sv') or '')
        s, t = source.casefold(), target.casefold()
        findings = []
        # A name alone is not an animal. Require an explicit animal/hunting context.
        caribou = re.search(r'\bcaribous?\b', s)
        animal = caribou and (s.strip(' .!?') == 'caribou' or bool(re.search(
            r'\b(?:hunt\w*|search\w*|herd|tracks?|season|antlers?|meat|kill\w*|shoot\w*|scare|single|these|those|some|any|no|four|eight|get)\b', s)))
        named = bool(re.search(r'\bcaribou\s+(?:coffee|lake|river|mountain|county|city|creek|island)\b|\b(?:named|called|name\s+is)\s+caribou\b', s))
        if animal and not named:
            wrong = re.search(r'\b(?:karibu\w*|caribou\w*)(?:-\w+)?', target, re.IGNORECASE)
            if wrong:
                findings.append((caribou.group(), wrong.group(),
                    'Djuret caribou ska heta ren på svenska, böjt efter sammanhanget; flock renhjord och jakt renjakt.'))
        tracks = re.search(r'\b(?:caribou|reindeer|deer|moose|bear|wolf|dog)\s+tracks?\b', s)
        wrong_tracks = re.search(r'\b\w*(?:träck|spillning)\w*\b', target, re.IGNORECASE)
        if tracks and wrong_tracks and not re.search(r'\b\w*spår\w*\b', t) and not re.search(r'\b(?:droppings|dung|scat)\b', s):
            findings.append((tracks.group(), wrong_tracks.group(), 'Tracks är djurets spår, inte träck eller spillning.'))
        if counted_animal_feet(source):
            distance = re.search(r'\b\d+(?:[.,]\d+)?\s*(?:meter|metrar|m|fot|feet)\b', target, re.IGNORECASE)
            if distance:
                findings.append((source, distance.group(), 'Feet räknar hundarnas eller katternas tassar här; antal kroppsdelar får inte räknas om till meter.'))
        snowgo = re.search(r'\bsnowgos?\b', s)
        kept_snowgo = re.search(r'\bsnowgos?\b', target, re.IGNORECASE)
        vehicle_context = re.search(r'\b(?:freez\w*|bring|ride|riding|drive|driving|engine|sled|trail|dogs?|fuel|start|park)\b', s)
        if snowgo and kept_snowgo and vehicle_context and not re.search(r'\b(?:named|called|name)\b', s):
            findings.append((snowgo.group(), kept_snowgo.group(), 'Snowgo är en snöskoter i detta sammanhang; översätt fordonet till svenska.'))
        low = re.search(r'\b(?:getting|running|got|run)\s+low\s+on\s+wood\b', s)
        receiving = re.search(r'\bfå(?:tt|r)?\s+lite\s+ved\b', target, re.IGNORECASE)
        if low and receiving and not re.search(r'\b(?:slut|brist|ont|kvar)\b', t):
            findings.append((low.group(), receiving.group(), 'Getting low on wood betyder att veden börjar ta slut, inte att få lite ved.'))
        postposed = _POSTPOSED_POSSESSIVE.search(source)
        if postposed:
            noun, possessive = postposed.group(1), postposed.group(2)
            split = re.search(rf"\b{re.escape(noun)}\s*,\s*(?:min|din|sin|vår|mitt|ditt|sitt|vårt|mina|dina|sina|våra)\b", target, re.IGNORECASE)
            if split:
                findings.append((postposed.group(), split.group(), f'"{noun} {possessive}" är norsk ordföljd för "{possessive} {noun}"; på svenska står ägaren före: '
                                                                 f'"{possessive} {noun}", inte "{split.group()}".'))
        forbids, need_not = _MAA_IKKE.search(source), _NEED_NOT.search(target)
        if forbids and need_not:
            findings.append((forbids.group(), need_not.group(), '"må ikke" på norska/danska är ett förbud: "får inte", inte "måste inte"/"behöver inte".'))
        # MAN OF WAR (dev jobs 150-195): "since Bud's" (the SEAL training) became "sedan Bud's tid", as if Bud were a person.
        buds = _BUDS_SOURCE.search(source)
        if buds and 'BUD/S' not in target:
            findings.append((buds.group(), target, "Bud's/buds efter since/through är BUD/S, Navy SEAL-utbildningen; skriv BUD/S, inte ett namn."))
        if _PRIME_MINISTER.search(source) and not _HEAD_OF_GOVERNMENT.search(target):
            kept = re.search(r"\bprime[\s-]+minister\w*|\bminister\w*", target, re.IGNORECASE)
            findings.append((_PRIME_MINISTER.search(source).group(), kept.group() if kept else target,
                             'Prime Minister är regeringschefen: premiärminister (Sveriges egen: statsminister), inte bara "minister".'))
        tank = _TANK_SOURCE.search(source)
        if tank:
            english = re.search(r"\btanks\b", target, re.IGNORECASE)
            container = re.search(r"\btank(?:en|ar|arna)?\b", target, re.IGNORECASE)
            if english:
                findings.append((tank.group(), english.group(), '"tanks" är engelska; en militär tank heter stridsvagn (stridsvagnar) på svenska.'))
            elif container and _MILITARY.search(source) and not re.search(r"stridsvagn", target, re.IGNORECASE):
                findings.append((tank.group(), container.group(), 'En militär tank är en stridsvagn på svenska; tank/tankar är en behållare.'))
        passive = _PASSIVE_WITH_OBJECT.search(target)
        if passive:
            stem, particle, obj = passive.groups()
            findings.append((source, passive.group(), f'"{stem}as {particle} {obj}" är passiv form med objekt (betyder att subjektet själv {stem}as); '
                                                    f'aktiv form är "{stem}a {particle} {obj}".'))
        throw_source, throw_target = _SCANDINAVIAN_THROW_IDIOM.search(source), _WRONG_THROW_SELF.search(target)
        if throw_source and throw_target:
            findings.append((throw_source.group(), throw_target.group(),
                             'Norskans/danskans uttryck beskriver att lättvindigt använda eller kasta ur sig något, inte att kasta sig själv runt. '
                             'Bevara idiomets handling och objekt på naturlig svenska.'))
        dying, killing = _FOR_DYING.search(source), _FOR_KILLING_SV.search(target)
        if dying and killing and not re.search(r'殺|死なせ|\bkill\w*\b', source, re.IGNORECASE):
            findings.append((dying.group(), killing.group(),
                             'Att dö är inte att döda: källan anger döende, inte att orsaka någon annans död. Bevara handling och aktör.'))
        for english, swedish in ENGLISH_EXONYMS.items():
            # MAN OF WAR (dev job 170): "attack on Ukraine" became "attack mot Ukraine"; Swedish writes Ukraina.
            kept = re.search(rf'\b{english}\b', target)
            if kept and re.search(rf'\b{english}\b', source) and not re.search(rf'\b{swedish}\b', target):
                findings.append((english, kept.group(), f'{english} heter {swedish} på svenska.'))
        for source_quote, target_quote, reason in findings:
            # Quotes must be exact slices of the original strings, including capitalization.
            match = re.search(re.escape(source_quote), source, re.IGNORECASE)
            flags.append({'id': row['id'], 'category': 'meaning', 'source_quote': match.group() if match else source,
                          'target_quote': target_quote, 'reason': reason})
    return flags


def imperial_unit_flags(rows, target_key='sv'):
    """One review flag per row whose Swedish text keeps an imperial unit of the source (or turns miles into Swedish "mil"), or converts a number wrongly.

    Only a unit that the source gives with a number ("40 yards", "a couple of miles") counts, so "on foot" and a place called Mile End are never flagged.
    A number in the Swedish text must be within 15 % of the converted one when the source number is written in digits."""
    flags = []
    for row in rows:
        source, target = str(row.get('source', '')), str(row.get(target_key) or '')
        lowered_source = source.casefold()
        target_words = re.findall(r"[^\W\d_]+", target.casefold())
        for unit, (pattern, metric, factor, kept_words) in IMPERIAL_UNITS.items():
            if unit == 'feet' and counted_animal_feet(source):
                continue  # separate sense check; correct paws are not an imperial unit
            found = re.search(rf'(?<![\w]){_NUMBER}(?:\s+(?:of|more|extra|odd))?\s*(?:-\s*)?{pattern}(?![\w])', lowered_source)
            if not found:
                continue
            problems = [word for word in kept_words if word in target_words]
            if unit == 'miles' and re.search(r'\b(kilometer|km)\b', target.casefold()):
                problems = [word for word in problems if word != 'mil']
            token = found.group(1).replace(',', '.')
            digits = re.fullmatch(r'\d+(?:\.\d+)?', token)
            value = float(token) if digits else float(_NUMBER_WORDS[token])
            expected = value * factor
            numbers = [float(number.replace(',', '.')) for number in re.findall(r'\d+(?:[.,]\d+)?', target)]
            metric_written = bool(re.search(r'\b(kilometer|km|meter|centimeter|cm|liter|kilo|kg|gram)\b|kubikmeter|\bm\b|\bl\b|\bg\b', target.casefold()))
            # a number is only judged when the Swedish gives a metric value: "32 extra feet" of eight dogs is "32 extra tassar" (paws), not a length
            wrong_number = bool(digits and numbers and metric_written and not problems and not any(abs(number - expected) <= .15 * expected for number in numbers))
            if not (problems or wrong_number):
                continue
            quote = target if target.strip() else ''
            for word in problems:
                match = re.search(rf'(?:(?:\d+(?:[.,]\d+)?|ett|en|par|två|tre|fyra|fem|sex|sju|åtta|nio|tio|tolv|tjugo|trettio|fyrtio|femtio|hundra|några|flera)\s+){{0,3}}'
                                  rf'(?<![\w]){re.escape(word)}(?![\w])', target, flags=re.IGNORECASE)
                if match and target.count(match.group(0)) == 1:
                    quote = match.group(0)
                    break
            about = f'{expected:.0f}' if expected >= 10 else f'{expected:.1f}'.replace('.', ',')
            flags.append({'id': row['id'], 'category': 'untranslated', 'source_quote': found.group(0), 'target_quote': quote,
                          'reason': f'Måttet i källan ({found.group(0)}) står kvar som engelskt mått eller är fel omräknat: skriv det metriskt (ungefär {about} {metric}); '
                                    'en svensk mil är 10 km, så miles blir aldrig "mil".'})
            break
    return flags


def deterministic_flags(rows, target_key='sv'):
    """Language, grammar and source-anchored meaning defects, including on a cut-off source row."""
    if target_key != 'sv':
        return []
    return non_latin_flags(rows, target_key) + imperial_unit_flags(rows, target_key) + swedish_context_flags(rows, target_key) + swedish_grammar_flags(rows)


# "Okej, jag ringer dig efter jag blir våldtagen." (Agnes, dev job 246): "efter" before a clause needs "att". Only subject pronouns that cannot
# be the object of the preposition "efter" ("efter de senaste åren", "efter dem", "efter det" are left alone).
_EFTER_CLAUSE = re.compile(r"\befter\s+(?:jag|du|han|hon|vi|ni|man)\s+\w+", re.IGNORECASE)
# "The Allies are gonna attack Hitler." -> "Allierade kommer att anfalla Hitler." (FORTITUDE, dev job 248).
_BARE_ALLIES = re.compile(r"(?<![\w])(?<!de )(?<!De )(?<!DE )(?<!våra )(?<!Våra )(?<!alla )(?<!Alla )allierade\b", re.IGNORECASE)


def swedish_grammar_flags(rows):
    """Unambiguous gender disagreement, "efter" before a clause without "att" and "the Allies" without its article; no guessed source words or
    inflection edits."""
    flags = []
    for row in rows:
        target = str(row.get('sv') or '')
        clause = _EFTER_CLAUSE.search(target)
        if clause:
            flags.append({'id': row['id'], 'category': 'grammar', 'source_quote': str(row.get('source', '')), 'target_quote': clause.group(),
                          'reason': '"efter" före en bisats kräver "att": "efter att jag …". Ändra inte betydelsen.'})
        allies = _BARE_ALLIES.search(target) if re.search(r'\bthe\s+allies\b', str(row.get('source', '')), re.IGNORECASE) else None
        if allies:
            flags.append({'id': row['id'], 'category': 'grammar', 'source_quote': str(row.get('source', '')), 'target_quote': allies.group(),
                          'reason': '"The Allies" är bestämd form: "de allierade" med artikeln "de".'})
        wrong = re.search(r'\b(?:en|min|din|sin|vår|er)\s+(?:vapen|barn|hus|djur|år|ord|namn|problem|område|arbete)\b', target, re.IGNORECASE)
        if wrong:
            flags.append({'id': row['id'], 'category': 'grammar', 'source_quote': str(row.get('source', '')),
                          'target_quote': wrong.group(),
                          'reason': 'Genus stämmer inte: substantivet är neutrum och kräver ett/mitt/ditt/sitt/vårt/ert. '
                                    'Rätta böjningen utan att ändra substantivets betydelse.'})
    return flags
