import re
import sys
import unicodedata

try:
    import spacy
    _nlp = None
    _nlp_load_failed = False

    def _get_nlp():
        global _nlp, _nlp_load_failed
        if _nlp_load_failed:
            return None
        if _nlp is None:
            try:
                _nlp = spacy.load('en_core_web_sm')
            except (OSError, IOError) as e:
                print(f'Warning: spaCy model en_core_web_sm not available '
                      f'({e}); heteronym/contraction resolution disabled. '
                      f'Install with: python -m spacy download en_core_web_sm',
                      file=sys.stderr)
                _nlp_load_failed = True
                return None
        return _nlp

    HAS_SPACY = True
except ImportError:
    HAS_SPACY = False


# --- Heteronym disambiguation ---

HETERONYMS = {
    'lead': {
        'past': 'led',
        'present': 'leed',
    },
}


def resolve_heteronyms(text):
    """Use spaCy POS tagging to add phoneme hints for ambiguous words."""
    if not HAS_SPACY:
        return text
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    replacements = []
    for token in doc:
        lower = token.text.lower()
        if lower not in HETERONYMS:
            continue
        start = token.idx
        end = start + len(token.text)
        # Skip tokens already wrapped in `[word](/IPA/)` so a respelling
        # can't mutate the bracket display text emitted by an earlier pass
        # (apply_contextual_overrides) or by user phoneme overrides.
        if _is_inside_markdown(text, start, end):
            continue
        rules = HETERONYMS[lower]
        if token.tag_ in ('VBD', 'VBN'):
            hint = rules['past']
        else:
            hint = rules['present']
        if hint != lower:
            replacements.append((start, end, hint))

    for start, end, hint in reversed(replacements):
        text = text[:start] + hint + text[end:]
    return text


# --- Contextual heteronym overrides ---
#
# For words whose correct pronunciation depends on context that spaCy's POS
# tagger alone can't distinguish. Each rule inspects the token + its
# neighbours and returns an IPA string (canonical IPA, not misaki's internal
# alphabet) or None to leave the token alone. Rules only emit overrides on
# positive context evidence — ambiguous tokens fall through to misaki's
# default gold branch.
#
# Emitted markdown `[word](/IPA/)` parses via misaki's LINK_REGEX at rating 5,
# beating the gold (4) / silver (3) / espeak (2) lexicons. Before the IPA
# leaves this module it must be folded to misaki's internal alphabet — Kokoro's
# vocab indexes phonemes character-by-character and only carries single-letter
# tokens for the five English diphthongs (A=eɪ, I=aɪ, O=oʊ, W=aʊ, Y=ɔɪ; Q=əʊ
# for GB). Sending raw `aʊ` instead of `W` makes the model read `a` (id 43)
# and `ʊ` (id 135) as two unrelated phonemes, which destabilises duration
# prediction and bleeds the override onto neighbouring words.

_CANONICAL_TO_MISAKI_DIPHTHONG = (
    ('eɪ', 'A'), ('aɪ', 'I'), ('oʊ', 'O'),
    ('aʊ', 'W'), ('ɔɪ', 'Y'), ('əʊ', 'Q'),
)


def _to_misaki_phonemes(ipa):
    """Fold canonical-IPA diphthongs to the single-letter codes Kokoro's
    phoneme vocab is trained on. Other characters (consonants, monophthongs,
    stress marks, schwas) are identical between canonical IPA and misaki's
    internal alphabet, so they pass through unchanged. Idempotent."""
    for canonical, misaki in _CANONICAL_TO_MISAKI_DIPHTHONG:
        ipa = ipa.replace(canonical, misaki)
    return ipa

_BOW_VERB_CUES = frozenset({
    'take', 'took', 'taken', 'takes', 'taking',
    'give', 'gave', 'given', 'gives', 'giving',
    'make', 'made', 'makes', 'making',
    'deep', 'low', 'graceful', 'slight', 'final', 'curtain',
})

# Tokens nearby that strongly signal the archery/weapon sense of bow
# (rhymes with "go"). Used to suppress the gesture default for `bowed` /
# `bowing` where misaki has no gold entry and would otherwise need the rule
# to keep its hands off.
_BOW_ARCHERY_CUES = frozenset({
    'arrow', 'arrows', 'quiver', 'quivers', 'archery', 'archer', 'archers',
    'longbow', 'longbows', 'crossbow', 'crossbows', 'bowstring', 'bowstrings',
    'fletching', 'shaft', 'shafts', 'target', 'targets', 'aim', 'aimed',
    'aiming', 'nocked', 'nocking', 'drew', 'drawn',
    'violin', 'violins', 'cello', 'cellos', 'fiddle', 'fiddles', 'viola',
    'violinist', 'violinists', 'cellist', 'cellists', 'fiddler', 'fiddlers',
})

# Tokens that follow `bowed` / `bowing` and reinforce the gesture sense
# (e.g. "bowed his head", "bowed deeply", "bowed low").
_BOW_GESTURE_NEXT = frozenset({
    'down', 'to', 'before', 'deeply', 'low', 'slightly', 'gracefully',
    'his', 'her', 'their', 'my', 'our', 'your',
    'head', 'heads', 'over', 'forward', 'humbly', 'reverently',
    'politely', 'stiffly', 'curtly', 'briefly', 'silently',
})

_CONTENT_COPULAS = frozenset({
    'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being',
    'feel', 'feels', 'felt', 'feeling',
    'seem', 'seems', 'seemed', 'seeming',
    'appear', 'appears', 'appeared', 'appearing',
    'remain', 'remains', 'remained', 'remaining',
    'look', 'looks', 'looked', 'looking',
    'stay', 'stays', 'stayed', 'staying',
})

_CONTENT_DEGREE = frozenset({
    'very', 'quite', 'so', 'too', 'really', 'entirely',
    'perfectly', 'completely', 'wholly', 'truly', 'thoroughly',
})

_LEAD_MATERIAL_NEXT = frozenset({
    'paint', 'pipe', 'pipes', 'piping', 'poisoning', 'poison',
    'weight', 'weights', 'shot', 'bullet', 'bullets',
    'crystal', 'acetate', 'solder', 'dust', 'ore',
    'sheet', 'sheets', 'foil', 'balloon', 'balloons',
})

_LEAD_MATERIAL_PREV = frozenset({
    'poisoned', 'toxic', 'molten', 'pure',
})

_BASS_MUSIC_NEXT = frozenset({
    'guitar', 'guitars', 'drum', 'drums', 'clef', 'line', 'lines',
    'player', 'players', 'vocalist', 'vocalists', 'solo', 'solos',
    'note', 'notes', 'string', 'strings', 'section', 'pedal',
    'amp', 'amplifier', 'cabinet', 'tuba', 'voice', 'voices',
})

_BASS_MUSIC_PREV = frozenset({
    'electric', 'acoustic', 'upright', 'double', 'slap', 'walking',
    'play', 'plays', 'played', 'playing', 'plucked', 'strummed',
    'slapped', 'jazz', 'rock', 'funk', 'reggae', 'blues', 'metal',
    'fretted', 'fretless',
})

# Adjectives and verbs that disambiguate `row` to the argument sense /raʊ/
# (rhymes with "cow"). The line/boat sense /roʊ/ remains the default since
# misaki gold has only `'ɹˈO'` for `row` regardless of context.
_ROW_ARGUMENT_PREV = frozenset({
    'huge', 'big', 'loud', 'fierce', 'almighty', 'public', 'family',
    'terrible', 'awful', 'blazing', 'serious', 'massive', 'furious',
    'noisy', 'heated', 'bitter', 'nasty', 'ugly', 'screaming',
    'shouting', 'tearful', 'epic', 'right',
})

_ROW_ARGUMENT_NEXT = frozenset({
    'erupted', 'ensued', 'between',
})


_BOW_GESTURE_IPA = {
    'bow': 'bˈaʊ',
    'bows': 'bˈaʊz',
    'bowed': 'bˈaʊd',
    'bowing': 'bˈaʊɪŋ',
}


def _bow_rule(token, doc):
    """'bow'/'bows'/'bowed'/'bowing' → /baʊ.../ when the context cues the
    bowing-gesture sense. Archery/ribbon falls through to misaki's gold for
    `bow`/`bows`. For `bowed`/`bowing` (no misaki gold entry), default to
    gesture unless archery cues are present — in fiction the gesture sense
    is overwhelmingly more common than violin-bowing or arch-shaping."""
    word = token.text.lower()
    gesture_ipa = _BOW_GESTURE_IPA.get(word)
    if gesture_ipa is None:
        return None
    window_prev = {t.lower_ for t in doc[max(0, token.i - 4):token.i]}
    window_next = {t.lower_ for t in doc[token.i + 1:token.i + 4]}
    if window_prev & _BOW_VERB_CUES:
        return gesture_ipa
    if window_next & _BOW_GESTURE_NEXT:
        return gesture_ipa
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ in {'down', 'to', 'before'}:
        return gesture_ipa
    if word in {'bowed', 'bowing'}:
        if window_prev & _BOW_ARCHERY_CUES or window_next & _BOW_ARCHERY_CUES:
            return None
        return gesture_ipa
    return None


def _content_rule(token, doc):
    """'content' → /kənˈtɛnt/ when used predicatively as an adjective.
    Noun default (/kˈɑntɛnt/) falls through to misaki's gold."""
    for t in doc[max(0, token.i - 3):token.i]:
        if t.lower_ in _CONTENT_COPULAS or t.lower_ in _CONTENT_DEGREE:
            return 'kənˈtɛnt'
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ == 'with':
        return 'kənˈtɛnt'
    return None


def _minute_rule(token, doc):
    """'minute' → /maɪˈnut/ when adjectival ('tiny').
    Time-unit default (/mˈɪnət/) falls through to misaki's gold."""
    if token.i + 1 >= len(doc):
        return None
    next_tok = doc[token.i + 1]
    if next_tok.pos_ not in {'NOUN', 'PROPN'}:
        return None
    if token.dep_ not in {'amod', 'compound', 'nmod'}:
        return None
    if token.i > 0:
        prev = doc[token.i - 1].lower_
        if prev in {'one', 'a', 'per', 'each', 'this'}:
            return None
    return 'maɪˈnut'


def _lead_rule(token, doc):
    """'lead' → /lɛd/ when the context cues the metal/material sense.
    Verb/leader default (/lid/) falls through to misaki's gold."""
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ in _LEAD_MATERIAL_NEXT:
        return 'lˈɛd'
    window_prev = {t.lower_ for t in doc[max(0, token.i - 3):token.i]}
    if window_prev & _LEAD_MATERIAL_PREV:
        return 'lˈɛd'
    return None


def _bass_rule(token, doc):
    """'bass' → /beɪs/ when the context cues the musical instrument.
    Fish default (/bæs/) falls through to misaki's gold."""
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ in _BASS_MUSIC_NEXT:
        return 'bˈeɪs'
    window_prev = {t.lower_ for t in doc[max(0, token.i - 3):token.i]}
    if window_prev & _BASS_MUSIC_PREV:
        return 'bˈeɪs'
    return None


def _row_rule(token, doc):
    """'row' → /raʊ/ when the context cues the argument/quarrel sense.
    Line/boat default (/roʊ/) falls through to misaki's gold. Misaki has
    only the line-sense entry, so without this rule "they had a huge row"
    rhymes with "go". `rows` plural is left alone — line-sense plural is
    overwhelmingly more common ("rows of houses")."""
    if token.text.lower() != 'row':
        return None
    if token.i >= 2 and (
        doc[token.i - 2].lower_ == 'in' and doc[token.i - 1].lower_ == 'a'
    ):
        return None
    window_prev = {t.lower_ for t in doc[max(0, token.i - 3):token.i]}
    if window_prev & _ROW_ARGUMENT_PREV:
        return 'ɹˈaʊ'
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ in _ROW_ARGUMENT_NEXT:
        return 'ɹˈaʊ'
    return None


def _tearing_rule(token, doc):
    """'tearing' → /tɪɹɪŋ/ when the context cues the crying sense.
    Rip default (/tɛɹɪŋ/) falls through to misaki's gold. Misaki gold has
    only the rip-sense entry for `tearing`, so "her eyes were tearing up"
    rhymes with "wearing" without this rule."""
    if token.text.lower() != 'tearing':
        return None
    window_prev = {t.lower_ for t in doc[max(0, token.i - 4):token.i]}
    if window_prev & {'eyes', 'eye'}:
        return 'tˈɪɹɪŋ'
    next_tok = doc[token.i + 1] if token.i + 1 < len(doc) else None
    if next_tok and next_tok.lower_ == 'up':
        after_next = doc[token.i + 2] if token.i + 2 < len(doc) else None
        if after_next is None or after_next.is_punct:
            return 'tˈɪɹɪŋ'
        if after_next.pos_ == 'ADP':
            return 'tˈɪɹɪŋ'
    return None


_CONTEXTUAL_RULES = {
    'bow': _bow_rule,
    'bows': _bow_rule,
    'bowed': _bow_rule,
    'bowing': _bow_rule,
    'content': _content_rule,
    'minute': _minute_rule,
    'lead': _lead_rule,
    'bass': _bass_rule,
    'row': _row_rule,
    'tearing': _tearing_rule,
}


def _is_inside_markdown(text, start, end):
    """True if text[start:end] sits inside existing `[word](/IPA/)` markdown."""
    return start > 0 and text[start - 1] == '[' and text[end:end + 3] == '](/'


def apply_contextual_overrides(text):
    """Emit IPA markdown for heteronyms whose pronunciation needs more than
    a single POS tag. Each rule in `_CONTEXTUAL_RULES` returns an IPA string
    when its context cue fires, or None to leave the token to misaki's gold.

    Skips tokens already wrapped in `[word](/IPA/)` so user-configured phoneme
    overrides (which run later) stay authoritative.
    """
    if not HAS_SPACY:
        return text
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    replacements = []
    for token in doc:
        rule = _CONTEXTUAL_RULES.get(token.text.lower())
        if rule is None:
            continue
        start = token.idx
        end = start + len(token.text)
        if _is_inside_markdown(text, start, end):
            continue
        ipa = rule(token, doc)
        if not ipa:
            continue
        ipa = _to_misaki_phonemes(ipa)
        replacements.append((start, end, f'[{token.text}](/{ipa}/)'))

    for start, end, hint in reversed(replacements):
        text = text[:start] + hint + text[end:]
    return text


# --- Contraction resolution ---

def resolve_contractions(text):
    """Expand ambiguous contractions using spaCy POS context."""
    if not HAS_SPACY:
        return text
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    replacements = []
    for i, token in enumerate(doc):
        if token.text.lower() == "'s":
            prev = doc[i - 1] if i > 0 else None
            nxt = doc[i + 1] if i + 1 < len(doc) else None
            if nxt and nxt.pos_ in ('VERB', 'AUX') and nxt.tag_ == 'VBG':
                replacements.append(
                    (token.idx, token.idx + len(token.text), ' is'))
            elif nxt and nxt.tag_ in ('VBN', 'VBD'):
                replacements.append(
                    (token.idx, token.idx + len(token.text), ' has'))
        elif token.text.lower() == "'d":
            nxt = doc[i + 1] if i + 1 < len(doc) else None
            if nxt and nxt.tag_ in ('VBN', 'VBD'):
                replacements.append(
                    (token.idx, token.idx + len(token.text), ' had'))
            elif nxt and nxt.pos_ in ('VERB', 'AUX'):
                replacements.append(
                    (token.idx, token.idx + len(token.text), ' would'))

    for start, end, expansion in reversed(replacements):
        text = text[:start] + expansion + text[end:]
    return text


# --- Unicode normalization ---

UNICODE_REPLACEMENTS = {
    '\u201c': '"',   # left double quotation mark
    '\u201d': '"',   # right double quotation mark
    '\u2018': "'",   # left single quotation mark
    '\u2019': "'",   # right single quotation mark
    '\u2026': '...', # horizontal ellipsis
    '\u00a0': ' ',   # non-breaking space
    '\u00ad': '',    # soft hyphen (remove)
    '\u200b': '',    # zero-width space (remove)
    '\u200d': '',    # zero-width joiner (remove)
    '\ufeff': '',    # byte order mark (remove)
    '\ufb01': 'fi',  # fi ligature
    '\ufb02': 'fl',  # fl ligature
    '\ufb00': 'ff',  # ff ligature
    '\ufb03': 'ffi', # ffi ligature
    '\ufb04': 'ffl', # ffl ligature
    '\u2032': "'",   # prime → apostrophe
    '\u2033': '"',   # double prime → quotation mark
    '\u201a': ',',   # single low-9 quotation mark (OCR artifact)
    '\u201e': '"',   # double low-9 quotation mark
    '\u2014': ', ',  # em-dash → comma pause
    '\u00b9': '1',   # superscript 1
    '\u00b2': '2',   # superscript 2
    '\u00b3': '3',   # superscript 3
    '\u2070': '0',   # superscript 0
    '\u2074': '4',   # superscript 4
    '\u2075': '5',   # superscript 5
    '\u2076': '6',   # superscript 6
    '\u2077': '7',   # superscript 7
    '\u2078': '8',   # superscript 8
    '\u2079': '9',   # superscript 9
}


FRACTION_REPLACEMENTS = {
    '\u00bc': 'one quarter',
    '\u00bd': 'one half',
    '\u00be': 'three quarters',
    '\u2153': 'one third',
    '\u2154': 'two thirds',
    '\u2155': 'one fifth',
    '\u2156': 'two fifths',
    '\u2157': 'three fifths',
    '\u2158': 'four fifths',
    '\u2159': 'one sixth',
    '\u215a': 'five sixths',
    '\u215b': 'one eighth',
    '\u215c': 'three eighths',
    '\u215d': 'five eighths',
    '\u215e': 'seven eighths',
}


def strip_diacritics(text):
    """Strip accent marks so accented words match the ASCII-only TTS lexicon."""
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def normalize_unicode(text, is_english=True):
    for char, replacement in UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    if is_english:
        for char, replacement in FRACTION_REPLACEMENTS.items():
            text = text.replace(char, ' ' + replacement + ' ')
        # Fraction slash → regular slash
        text = text.replace('\u2044', '/')
    # En-dash between numbers: "10–20" → "10 to 20" (English only)
    if is_english:
        text = re.sub(r'(\d)\u2013(\d)', r'\1 to \2', text)
    # Remaining en-dashes
    text = text.replace('\u2013', ' - ')
    if is_english:
        # misaki's English lexicon is ASCII-only; other languages need diacritics for G2P.
        text = strip_diacritics(text)
    return text


# --- Abbreviation expansion ---

ABBREVIATIONS = {
    # Titles
    'Mr.': 'Mister',
    'Mrs.': 'Missus',
    'Ms.': 'Miss',
    'Dr.': 'Doctor',
    'Prof.': 'Professor',
    'Rev.': 'Reverend',
    'Gen.': 'General',
    'Capt.': 'Captain',
    'Sgt.': 'Sergeant',
    'Lt.': 'Lieutenant',
    'Col.': 'Colonel',
    'Cmdr.': 'Commander',
    'Adm.': 'Admiral',
    'Gov.': 'Governor',
    'Sen.': 'Senator',
    'Rep.': 'Representative',
    # Suffixes
    'Jr.': 'Junior',
    'Sr.': 'Senior',
    'Esq.': 'Esquire',
    # Common
    'etc.': 'et cetera',
    'vs.': 'versus',
    'approx.': 'approximately',
    'dept.': 'department',
    'govt.': 'government',
    'assn.': 'association',
    'ave.': 'avenue',
    'blvd.': 'boulevard',
    # Latin
    'e.g.': 'for example',
    'i.e.': 'that is',
    'cf.': 'compare',
    'viz.': 'namely',
    'et al.': 'and others',
    'c.': 'circa',
    'ca.': 'circa',
    # Military
    'Pvt.': 'Private',
    'Cpl.': 'Corporal',
    'Maj.': 'Major',
    'Brig.': 'Brigadier',
    'Cmdt.': 'Commandant',
    # Ecclesiastical
    'Fr.': 'Father',
    # Geographical
    'Rd.': 'Road',
    'Ln.': 'Lane',
    'Hwy.': 'Highway',
    'Mt.': 'Mount',
    'Ft.': 'Fort',
    'Ctr.': 'Center',
    'Pkwy.': 'Parkway',
    # Publishing
    'Ch.': 'Chapter',
    'pp.': 'pages',
    'Vol.': 'Volume',
    'No.': 'Number',
    'Ed.': 'Edition',
    'Fig.': 'Figure',
    'Pt.': 'Part',
    # Saint (before a name — capital letter follows)
    'St.': 'Saint',
}


def expand_abbreviations(text):
    for abbr, expansion in ABBREVIATIONS.items():
        # Word-boundary-aware replacement
        pattern = re.escape(abbr)
        text = re.sub(r'(?<!\w)' + pattern + r'(?=\s|$)', expansion, text)
    return text


# --- Roman numeral expansion ---

ROMAN_VALUES = {
    'M': 1000, 'CM': 900, 'D': 500, 'CD': 400,
    'C': 100, 'XC': 90, 'L': 50, 'XL': 40,
    'X': 10, 'IX': 9, 'V': 5, 'IV': 4, 'I': 1,
}

ROMAN_KEYWORDS = (
    'chapter', 'part', 'book', 'volume', 'vol',
    'act', 'scene', 'section', 'appendix',
)


def _roman_to_int(s):
    """Convert a Roman numeral string to an integer."""
    result = 0
    i = 0
    for roman, value in ROMAN_VALUES.items():
        while s[i:i+len(roman)] == roman:
            result += value
            i += len(roman)
    return result if i == len(s) else None


def expand_roman_numerals(text):
    """Convert Roman numerals after keywords like Chapter, Part, etc."""
    keywords_pattern = '|'.join(ROMAN_KEYWORDS)
    pattern = re.compile(
        r'(?i)\b(' + keywords_pattern + r')\s+([IVXLCDM]+)\b'
    )

    def _replace(match):
        keyword = match.group(1)
        roman = match.group(2).upper()
        value = _roman_to_int(roman)
        if value is not None and value > 0:
            return f'{keyword} {value}'
        return match.group(0)

    return pattern.sub(_replace, text)


# --- Special character cleanup ---

SYMBOL_REPLACEMENTS = {
    '&': ' and ',
    '\u00a9': ' copyright ',  # ©
    '\u00ae': ' registered ', # ®
    '\u2122': ' trademark ',  # ™
    '\u00b0': ' degrees',     # °
    '\u00b7': ' ',            # middle dot
    '\u2022': ', ',           # bullet
    '\u2023': ', ',           # triangular bullet
    '\u25cf': ', ',           # black circle bullet
    '\u2018': "'",            # left single quote (backup)
    '\u00d7': ' by ',         # multiplication sign ×
    '\u00f7': ' divided by ', # division sign ÷
    '\u00b1': ' plus or minus ', # ±
    '\u00a7': ' section ',    # §
    '\u221e': ' infinity ',   # ∞
    '\u2248': ' approximately equal to ', # ≈
    '\u2260': ' not equal to ', # ≠
    '\u2264': ' less than or equal to ', # ≤
    '\u2265': ' greater than or equal to ', # ≥
    '\u00b6': '',             # ¶ pilcrow (decorative, remove)
    '\u2020': '',             # † dagger (remove)
    '\u2021': '',             # ‡ double dagger (remove)
    '\u2190': ' ',            # ← left arrow
    '\u2192': ' ',            # → right arrow
    '\u2191': ' ',            # ↑ up arrow
    '\u2193': ' ',            # ↓ down arrow
    '\u25b6': ' ',            # ▶ right triangle
    '\u25c0': ' ',            # ◀ left triangle
    '\u2605': ' ',            # ★ black star
    '\u2606': ' ',            # ☆ white star
}

# URL pattern
URL_PATTERN = re.compile(
    r'https?://[^\s<>\"\')\]]+|www\.[^\s<>\"\')\]]+',
    re.IGNORECASE,
)

# Email pattern
EMAIL_PATTERN = re.compile(
    r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
)


def clean_special_characters(text, is_english=True):
    # Remove URLs and emails
    text = URL_PATTERN.sub('', text)
    text = EMAIL_PATTERN.sub('', text)

    # Remove scene break markers (3+ repeated special characters, e.g. ***, ---, ~~~, ===)
    text = re.sub(r'[\*\-\~\=\_\#\+\.]{3,}', '', text)

    # Replace symbols (English words for English; strip to space otherwise)
    for symbol, replacement in SYMBOL_REPLACEMENTS.items():
        text = text.replace(symbol, replacement if is_english else ' ')

    # Collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text)
    # Collapse 3+ newlines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip trailing whitespace on each line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    return text


# --- Main entry point ---

def apply_substitutions(text, substitutions):
    """Apply user-defined word substitutions.

    Each entry is a dict with 'find', 'replace', and optional 'case_sensitive'
    and 'whole_word' booleans.
    """
    if not substitutions:
        return text
    for sub in substitutions:
        find = sub.get('find', '')
        replace = sub.get('replace', '')
        if not find:
            continue
        if sub.get('whole_word', True):
            pattern = r'\b' + re.escape(find) + r'\b'
        else:
            pattern = re.escape(find)
        flags = 0 if sub.get('case_sensitive', False) else re.IGNORECASE
        # Use a lambda for the replacement so backref-looking content in the
        # user's replace string (e.g. "\1", "\g<0>") is treated as a literal
        # instead of a regex substitution template.
        text = re.sub(pattern, lambda _m, r=replace: r, text, flags=flags)
    return text


# --- Phoneme overrides + acronym spellout ---

def apply_phoneme_overrides(text, overrides):
    """Wrap matching words with misaki's inline-phoneme markdown.

    Each entry is a dict with:
      word: str — the word to match
      ipa: str — IPA phonemes (misaki assigns rating 5, beating gold/silver)
      case_sensitive: bool (default False)
      enabled: bool (default True)

    Emits `[word](/IPA/)` which misaki parses in G2P.preprocess. Words
    containing non-word chars (apostrophes, hyphens) match without `\\b`
    anchors so names like O'Brien or Anne-Marie still work.
    """
    if not overrides:
        return text
    for entry in overrides:
        if not entry.get('enabled', True):
            continue
        word = entry.get('word', '')
        ipa = entry.get('ipa', '')
        if not word or not ipa:
            continue
        # Dictionaries hand out canonical IPA (eɪ, aʊ, …); Kokoro's vocab keys
        # the diphthongs as single letters (A, W, …). See _to_misaki_phonemes
        # for why mixing the two forms makes Kokoro bleed the override audio
        # onto neighbouring words.
        ipa = _to_misaki_phonemes(ipa)
        if re.search(r'\W', word):
            pattern = re.escape(word)
        else:
            pattern = r'\b' + re.escape(word) + r'\b'
        flags = 0 if entry.get('case_sensitive', False) else re.IGNORECASE
        # Lambda keeps backref-looking IPA (`\1`, `\g<0>`) as literal text.
        text = re.sub(
            pattern,
            lambda m, p=ipa: f'[{m.group(0)}](/{p}/)',
            text,
            flags=flags,
        )
    return text


# Roman numerals that look like acronyms but are usually section markers.
_ACRONYM_STOPLIST_HARD = frozenset({
    'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII',
})

# Pronounceable acronyms misaki only has lowercase entries for. Skip-set
# building filters gold to ALL-CAPS keys, which misses these — but misaki
# itself does case-insensitive lookup, so feeding them through unchanged
# yields the expected word pronunciation.
_ACRONYM_EXTRA_SKIP = frozenset({
    'SCUBA', 'LASER', 'RADAR', 'SONAR', 'SWAT', 'TASER', 'MODEM',
    'WASP', 'CAPTCHA', 'GULAG',
})

_ACRONYM_GOLD_CACHE = None

_ACRONYM_PATTERN = re.compile(r'\b[A-Z]{2,6}\b')


def _load_acronym_skip_set():
    """Return all-caps words misaki gold already pronounces.

    Loaded lazily; a failure to read the bundled lexicon falls back to the
    hard stoplist only so auto-acronym still runs for the obvious cases.
    """
    global _ACRONYM_GOLD_CACHE
    if _ACRONYM_GOLD_CACHE is not None:
        return _ACRONYM_GOLD_CACHE
    try:
        import json
        import importlib.resources
        with importlib.resources.open_text(
                'autiobooks.misaki.data', 'us_gold.json') as f:
            gold = json.load(f)
        caps = frozenset(
            k for k in gold
            if k.isalpha() and k.isupper() and 2 <= len(k) <= 6
        )
        _ACRONYM_GOLD_CACHE = (
            caps | _ACRONYM_STOPLIST_HARD | _ACRONYM_EXTRA_SKIP
        )
    except Exception:
        _ACRONYM_GOLD_CACHE = frozenset(
            _ACRONYM_STOPLIST_HARD | _ACRONYM_EXTRA_SKIP
        )
    return _ACRONYM_GOLD_CACHE


def apply_acronym_spellout(text, enabled):
    """Rewrite unknown ALL-CAPS tokens as dotted letters.

    `NATO` / `NASA` / `SQL` stay untouched because misaki gold already
    pronounces them. `CIA` / `FBI` / `HTML` become `C. I. A.` etc. so
    misaki reads each letter. Off by default — on-by-default would change
    behavior on every book in the existing user base.
    """
    if not enabled:
        return text
    skip = _load_acronym_skip_set()

    def _replace(m):
        word = m.group(0)
        if word in skip:
            return word
        return '. '.join(word) + '.'

    return _ACRONYM_PATTERN.sub(_replace, text)


def normalize_text(text, lang='en-us', substitutions=None,
                    heteronyms=True, contractions=True,
                    phoneme_overrides=None, auto_acronyms=False):
    """Normalize text before sending to TTS.

    English-specific transformations (abbreviation expansion, roman numeral
    expansion, symbol-to-English-word replacement, en-dash-to-'to' between
    numbers) are applied only when `lang` starts with 'en'. For other
    languages, symbols are stripped to spaces instead of replaced with English
    words.

    Phoneme overrides and auto-acronym spellout are English-only and run
    after user substitutions, so a rule rewriting `NATO` → `North Atlantic
    Treaty Organization` suppresses the auto-spellout. Phoneme overrides
    run last so the emitted `[word](/IPA/)` markdown isn't clobbered by
    any earlier pass.
    """
    is_english = lang.startswith('en')
    text = normalize_unicode(text, is_english=is_english)
    if is_english:
        text = expand_abbreviations(text)
        text = expand_roman_numerals(text)
        if heteronyms:
            text = apply_contextual_overrides(text)
            text = resolve_heteronyms(text)
        if contractions:
            text = resolve_contractions(text)
    text = clean_special_characters(text, is_english=is_english)
    text = apply_substitutions(text, substitutions)
    if is_english:
        text = apply_acronym_spellout(text, auto_acronyms)
        text = apply_phoneme_overrides(text, phoneme_overrides)
    return text
