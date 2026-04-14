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
    'read': {
        'past': 'red',
        'present': 'reed',
    },
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
        rules = HETERONYMS[lower]
        if token.tag_ in ('VBD', 'VBN'):
            hint = rules['past']
        else:
            hint = rules['present']
        if hint != lower:
            replacements.append((token.idx, token.idx + len(token.text), hint))

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
        text = re.sub(pattern, replace, text, flags=flags)
    return text


def normalize_text(text, lang='en-us', substitutions=None,
                    heteronyms=True, contractions=True):
    """Normalize text before sending to TTS.

    English-specific transformations (abbreviation expansion, roman numeral
    expansion, symbol-to-English-word replacement, en-dash-to-'to' between
    numbers) are applied only when `lang` starts with 'en'. For other
    languages, symbols are stripped to spaces instead of replaced with English
    words.
    """
    is_english = lang.startswith('en')
    text = normalize_unicode(text, is_english=is_english)
    if is_english:
        text = expand_abbreviations(text)
        text = expand_roman_numerals(text)
        if heteronyms:
            text = resolve_heteronyms(text)
        if contractions:
            text = resolve_contractions(text)
    text = clean_special_characters(text, is_english=is_english)
    text = apply_substitutions(text, substitutions)
    return text
