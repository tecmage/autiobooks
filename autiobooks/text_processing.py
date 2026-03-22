import re


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
    '\u2014': ', ',  # em-dash → comma pause
}


def normalize_unicode(text):
    for char, replacement in UNICODE_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    # En-dash between numbers: "10–20" → "10 to 20"
    text = re.sub(r'(\d)\u2013(\d)', r'\1 to \2', text)
    # Remaining en-dashes
    text = text.replace('\u2013', ' - ')
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


def clean_special_characters(text):
    # Remove URLs and emails
    text = URL_PATTERN.sub('', text)
    text = EMAIL_PATTERN.sub('', text)

    # Remove scene break markers (3+ repeated special characters, e.g. ***, ---, ~~~, ===)
    text = re.sub(r'[\*\-\~\=\_\#\+\.]{3,}', '', text)

    # Replace symbols
    for symbol, replacement in SYMBOL_REPLACEMENTS.items():
        text = text.replace(symbol, replacement)

    # Collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text)
    # Collapse 3+ newlines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip trailing whitespace on each line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    return text


# --- Main entry point ---

def normalize_text(text):
    """Normalize text before sending to TTS."""
    text = normalize_unicode(text)
    text = expand_abbreviations(text)
    text = expand_roman_numerals(text)
    text = clean_special_characters(text)
    return text
