import re
import sys
import unicodedata
import warnings

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


def resolve_heteronyms(text, suppress=frozenset()):
    """Use spaCy POS tagging to add phoneme hints for ambiguous words.

    Words in `suppress` (lowercased) are left alone — a user phoneme
    override or substitution for 'lead' must see the original spelling,
    not the 'led'/'leed' respelling."""
    if not HAS_SPACY:
        return text
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    replacements = []
    for token in doc:
        lower = token.text.lower()
        if lower not in HETERONYMS or lower in suppress:
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

_CANONICAL_TO_MISAKI = (
    # Diphthongs → single-letter vocab codes.
    ('eɪ', 'A'), ('aɪ', 'I'), ('oʊ', 'O'),
    ('aʊ', 'W'), ('ɔɪ', 'Y'), ('əʊ', 'Q'),
    # Affricates → the single ligatures misaki uses exclusively (us_gold has
    # thousands of 'ʤ'/'ʧ' values and zero two-char 'dʒ'/'tʃ'); the split
    # pair reaches Kokoro as an out-of-distribution stop+fricative.
    ('dʒ', 'ʤ'), ('tʃ', 'ʧ'),
    # ASCII 'g' (U+0067) → IPA 'ɡ' (U+0261). Kokoro's vocab has no ASCII-g
    # key (us_gold uses U+0261 exclusively), so a dictionary-typed override
    # like 'ˈgɜːtə' silently drops its /g/ from the audio. No existing
    # replacement text above contains ASCII 'g', so ordering doesn't matter
    # and the fold is idempotent (re-running finds only U+0261 afterward).
    ('g', 'ɡ'),
)


def _to_misaki_phonemes(ipa):
    """Fold canonical-IPA diphthongs, affricates, and ASCII 'g' to the codes
    Kokoro's phoneme vocab is trained on. Most other characters (monophthongs,
    stress marks, schwas) are identical between canonical IPA and misaki's
    internal alphabet and pass through unchanged — 'g' is the one consonant
    that isn't (Kokoro's vocab has no ASCII-g key, only U+0261 'ɡ'). Idempotent.

    Parens and newlines are stripped: dictionary optional-schwa notation
    like /ˈlɪs(ə)n/ breaks misaki's LINK_REGEX (group 2 stops at the first
    ')'), which silently drops the override AND voices the residue as junk.
    The dialog validates on entry; this is the belt-and-braces for entries
    that arrive via a hand-edited config."""
    ipa = ipa.replace('(', '').replace(')', '')
    ipa = ipa.replace('\n', ' ').replace('\r', ' ')
    for canonical, misaki in _CANONICAL_TO_MISAKI:
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

# Nouns that follow `bow`/`bows` and signal the ribbon/knot sense (/boʊ/,
# like archery) — suppress the bowing-gesture override. The gesture sense of
# the noun is already caught by the preceding verb cues (took/made/gave).
_BOW_RIBBON_CUES = frozenset({
    'tied', 'tie', 'ties', 'tying', 'untied', 'untie', 'unties',
    'wore', 'wear', 'wears', 'wearing', 'worn',
    'ribbon', 'ribbons', 'silk', 'satin', 'velvet', 'lace', 'bowtie',
})

# Nouns after `minute` that fix the time-unit sense (MIN-it), so the
# adjectival "tiny" override (/maɪˈnut/) must not fire: clock/corporate/etc.
_MINUTE_TIME_NOUNS = frozenset({
    'hand', 'hands', 'book', 'books', 'mark', 'marks', 'waltz',
    'man', 'men', 'steak', 'steaks', 'glass', 'gun', 'guns', 'rice',
})

# Words before `minute` that fix the time-unit sense. Number words cover
# unhyphenated duration compounds ("a ten minute walk", "five minute
# break") which spaCy labels amod/compound just like the 'tiny' sense.
# 'a' and 'every' are deliberately absent — 'a minute amount' and 'every
# minute detail' are the tiny sense (time uses of those pairs put 'minute'
# in noun position, where this rule never fires).
_MINUTE_TIME_PREV = frozenset({
    'one', 'per', 'each', 'this',
    'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
    'ten', 'eleven', 'twelve', 'thirteen', 'fifteen', 'twenty', 'thirty',
    'forty', 'fifty', 'sixty', 'ninety',
    'last', 'final', 'next',
})

# Attributive -ed adjectives whose syllabic /ɪd/ pronunciation differs from
# the verb past (LUR-nid vs LURND, KUR-sid vs kurst). misaki collapses both
# to the verb form; the override fires only in attributive position
# (dep_='amod'), which is reliable even where spaCy mis-tags the POS.
_ED_ADJECTIVE_IPA = {
    'learned': 'lˈɜɹnɪd',
    'blessed': 'blˈɛsɪd',
    'aged':    'ˈeɪʤɪd',
    'cursed':  'kˈɜɹsɪd',
}

# Matured-goods heads keep the 1-syllable 'aged' ("aged wood", "aged white
# tea", "ten-year aged Gouda") — the syllabic /ˈeɪʤɪd/ is for beings
# ("an aged wizard"). Found via a 1.4M-word corpus sweep.
_AGED_MATURED_HEADS = frozenset({
    'wood', 'tea', 'cheese', 'wine', 'whiskey', 'whisky', 'gouda',
    'cheddar', 'oak', 'leather', 'beef', 'ham', 'meat', 'steak',
    'rum', 'brandy', 'bourbon', 'ale', 'mead', 'vinegar',
    'paper', 'parchment', 'barrel', 'barrels', 'spirits',
})

# 'learned' takes the syllabic LUR-nid ONLY in the scholarly sense; the
# participial adjective ("a learned lesson", "learned behavior") stays
# 1-syllable. spaCy labels both amod, so gate on the modified noun — an
# allowlist of scholarly heads trades a little recall for the precision
# that matters (a wrong LUR-nid lesson is audible; a missed LUR-nid man
# just falls back to misaki's default).
_LEARNED_SCHOLARLY_HEADS = frozenset({
    'man', 'men', 'woman', 'women', 'person', 'people', 'folk',
    'scholar', 'scholars', 'friend', 'friends', 'colleague', 'colleagues',
    'professor', 'professors', 'doctor', 'doctors', 'judge', 'judges',
    'gentleman', 'gentlemen', 'lady', 'ladies', 'sage', 'sages',
    'elder', 'elders', 'master', 'masters', 'wizard', 'wizards',
    'mage', 'mages', 'monk', 'monks', 'priest', 'priests',
    'society', 'societies', 'journal', 'journals', 'treatise',
    'treatises', 'discourse', 'opinion', 'opinions', 'profession',
    'professions', 'debate', 'debates', 'tome', 'tomes', 'council',
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

# Immediately-preceding words that mark 'content' as the noun even when a
# copula sits earlier in the clause ("Such was the content of the letter").
_CONTENT_NOUN_DETERMINERS = frozenset({
    'the', 'a', 'an', 'its', 'their', 'his', 'her', 'my', 'our', 'your',
    'this', 'that', 'these', 'those', 'whose', 'some', 'any', 'all',
    'much', 'more', 'most', 'no', 'entire', 'actual', 'full',
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
    # 'right' and 'family' are deliberately absent — both are high-frequency
    # modifiers of the line sense ("the right row", "the family row of
    # seats") and the British "a right row" idiom isn't worth the
    # false-positive rate; an 'of' guard on the next token can't cover
    # 'in the right row' since 'row' isn't followed by 'of' there either.
    'huge', 'big', 'loud', 'fierce', 'almighty', 'public',
    'terrible', 'awful', 'blazing', 'serious', 'massive', 'furious',
    'noisy', 'heated', 'bitter', 'nasty', 'ugly', 'screaming',
    'shouting', 'tearful', 'epic',
})

_ROW_ARGUMENT_NEXT = frozenset({
    # 'between' is deliberately absent — it cues the spatial line sense at
    # least as often ("the row between the vines/desks/hedges").
    'erupted', 'ensued',
})


_BOW_GESTURE_IPA = {
    'bow': 'bˈaʊ',
    'bows': 'bˈaʊz',
    'bowed': 'bˈaʊd',
    'bowing': 'bˈaʊɪŋ',
}


def _sent_window(token, doc, before=0, after=0):
    """Lowercased neighbour texts clamped to the token's sentence.

    A flat `doc[i-4:i]` window leaks cue words across sentence boundaries
    ("They take the stage. Bow strings snapped." — 'take' from the previous
    sentence forced the gesture sense). Clamping to `token.sent` keeps each
    rule's evidence inside the clause it belongs to."""
    try:
        sent = token.sent
        lo = max(sent.start, token.i - before)
        hi = min(sent.end, token.i + 1 + after)
    except ValueError:
        # Sentence boundaries unavailable (parser disabled) — fall back to
        # the flat window rather than dropping the rule entirely.
        lo = max(0, token.i - before)
        hi = min(len(doc), token.i + 1 + after)
    prev = {t.lower_ for t in doc[lo:token.i]}
    nxt = {t.lower_ for t in doc[token.i + 1:hi]}
    return prev, nxt


def _bow_rule(token, doc):
    """'bow'/'bows'/'bowed'/'bowing' → /baʊ.../ when the context cues the
    bowing-gesture sense. Archery/ribbon cues suppress the override and fall
    through to misaki's gold for ALL forms, even `took`/`gave`/`made` cues
    that would otherwise read as gesture ('took the bow and nocked an
    arrow'). For `bowed`/`bowing` (no misaki gold entry), default to gesture
    unless archery cues are present — in fiction the gesture sense is
    overwhelmingly more common than violin-bowing or arch-shaping."""
    word = token.text.lower()
    gesture_ipa = _BOW_GESTURE_IPA.get(word)
    if gesture_ipa is None:
        return None
    is_verb_form = word in {'bowed', 'bowing'}
    window_prev, window_next = _sent_window(token, doc, before=4, after=3)
    # Ribbon/knot sense ('tied a bow', 'silk bow') is /boʊ/ — suppress the
    # gesture override for the noun forms. (The noun's gesture sense is
    # signalled by the preceding verb cues below, not by ribbon words.)
    if not is_verb_form and window_prev & _BOW_RIBBON_CUES:
        return None
    # Archery/music cues override the gesture sense for ALL forms, not just
    # bowed/bowing — 'took the bow and nocked an arrow' is the weapon even
    # though 'took' is also a gesture verb cue. Must run before the
    # _BOW_VERB_CUES check below, which would otherwise win first.
    if window_prev & _BOW_ARCHERY_CUES or window_next & _BOW_ARCHERY_CUES:
        return None
    if window_prev & _BOW_VERB_CUES:
        return gesture_ipa
    # Possessive/body cues ("bowed his head") disambiguate the VERB forms;
    # for the noun they misfire on "a bow in her hair", so gate on verb form.
    # This covers down/to/before as the immediate next token too — an
    # ungated next-token check here used to force the gesture sense onto
    # the NOUN ("drew the bow to his cheek" became /baʊ/).
    if is_verb_form and window_next & _BOW_GESTURE_NEXT:
        return gesture_ipa
    if is_verb_form:
        return gesture_ipa
    return None


def _content_rule(token, doc):
    """'content' → /kənˈtɛnt/ when used predicatively as an adjective.
    Noun default (/kˈɑntɛnt/) falls through to misaki's gold."""
    # A following noun makes 'content' a compound modifier, not a predicate
    # adjective ('content creator/manager/writer/team') — keep the noun
    # pronunciation even when a copula sits earlier in the window
    # ('he is a content creator' must not become con-TENT creator).
    if token.i + 1 < len(doc) and doc[token.i + 1].pos_ in {'NOUN', 'PROPN'}:
        return None
    # A determiner/possessive immediately before 'content' signals the noun
    # ("Such was the content of the letter", "what is the content of X") —
    # keep CON-tent even with a copula earlier in the window.
    if token.i > 0 and doc[token.i - 1].lower_ in _CONTENT_NOUN_DETERMINERS:
        return None
    window_prev, _ = _sent_window(token, doc, before=3)
    if window_prev & (_CONTENT_COPULAS | _CONTENT_DEGREE):
        return 'kənˈtɛnt'
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ == 'with':
        return 'kənˈtɛnt'
    return None


def _ed_adjective_rule(token, doc):
    """Attributive -ed adjective → syllabic /ɪd/ form ('a learned man' =
    LUR-nid, 'a cursed sword' = KUR-sid). Verb past (dep ROOT/acl) falls
    through to misaki's 1-syllable form ('she learned' = LURND). dep_='amod'
    is used rather than the POS tag because spaCy mis-tags attributive
    'learned' as VBN while still labelling it amod."""
    if token.dep_ != 'amod':
        return None
    # Hyphenated compounds keep the 1-syllable verb form: 'middle-aged'
    # (spaCy tokenizes to middle / - / aged, with 'aged' labelled amod)
    # is /eɪʤd/, not the syllabic 'an aged wizard' /ˈeɪʤɪd/.
    if token.i > 0 and doc[token.i - 1].text == '-':
        return None
    word = token.text.lower()
    head = token.head.lower_ if token.head is not None else ''
    if word == 'aged':
        # Unhyphenated 'middle aged man', suffixed 'aged-up', and matured
        # goods ('aged wood') all keep the 1-syllable form.
        if token.i > 0 and doc[token.i - 1].lower_ == 'middle':
            return None
        if token.i + 1 < len(doc) and doc[token.i + 1].text == '-':
            return None
        if head in _AGED_MATURED_HEADS:
            return None
    if word == 'learned' and head not in _LEARNED_SCHOLARLY_HEADS:
        return None
    return _ED_ADJECTIVE_IPA.get(word)


def _beloved_rule(token, doc):
    """'beloved' → /bɪˈlʌvɪd/ (3 syllables). misaki emits the 2-syllable
    /bəˈlʌvd/; the verb 'to belove' is archaic, so the adjective/noun form
    is safe unconditionally ('my beloved', 'dearly beloved', 'beloved son')."""
    return 'bɪˈlʌvɪd'


def _delegate_rule(token, doc):
    """'delegate' verb → /ˈdɛləɡeɪt/ (full final vowel). misaki has only the
    noun /ˈdɛləɡət/ and applies it regardless of POS, so 'leaders delegate
    tasks' is mispronounced. Fire on the verb tags."""
    if token.tag_ in {'VB', 'VBP', 'VBZ', 'VBG', 'VBD', 'VBN'}:
        return 'dˈɛləɡeɪt'
    return None


def _prayer_rule(token, doc):
    """'prayer'/'prayers' → /pɹɛɹ(z)/ (one syllable, rhymes with 'air').
    misaki emits the 2-syllable agent form /ˈpɹeɪəɹ/ ('one who prays'), but
    the devotion/petition sense ('said a prayer', 'her prayers') is
    overwhelmingly more common, so the override is unconditional."""
    return 'pɹˈɛɹz' if token.text.lower() == 'prayers' else 'pɹˈɛɹ'


_FREQUENT_VERB_IPA = {
    'frequent':    'fɹiˈkwɛnt',
    'frequents':   'fɹiˈkwɛnts',
    'frequented':  'fɹiˈkwɛntɪd',
    'frequenting': 'fɹiˈkwɛntɪŋ',
}


def _frequent_rule(token, doc):
    """'frequent' verb → /fɹiˈkwɛnt/ (final stress, 'they frequent the bar');
    misaki applies the adjective /ˈfɹikwənt/ regardless of POS. The base form
    fires only on verb tags so the adjective ('a frequent visitor') stays
    correct; the inflected forms (frequents/frequented/frequenting) are
    unambiguously verbal and always override."""
    word = token.text.lower()
    if word == 'frequent' and token.tag_ not in {'VB', 'VBP', 'VBZ', 'VBG'}:
        return None
    return _FREQUENT_VERB_IPA.get(word)


def _consummate_rule(token, doc):
    """'consummate' adjective → /kənˈsʌmət/ ('a consummate professional').
    misaki applies the verb /ˈkɑnsəmeɪt/ regardless of POS, and spaCy
    mis-tags the attributive adjective as NN (like 'content creator'), so
    default to the adjective UNLESS the token is clearly the verb."""
    if token.tag_ in {'VB', 'VBP', 'VBZ', 'VBG'}:
        return None
    return 'kənˈsʌmət'


def _minute_rule(token, doc):
    """'minute' → /maɪˈnut/ when adjectival ('tiny').
    Time-unit default (/mˈɪnət/) falls through to misaki's gold.

    Known limit: the bare time-conjunction "the minute class ended"
    (= "the minute THAT class ended") still takes the tiny sense — it is
    indistinguishable from "the minute details" without deeper parsing,
    and rare enough (1 in a 1.4M-word sweep) not to warrant a gamble."""
    if token.i + 1 >= len(doc):
        return None
    next_tok = doc[token.i + 1]
    if next_tok.pos_ not in {'NOUN', 'PROPN'}:
        return None
    # Time-unit compounds keep MIN-it ('minute hand', 'minute book').
    if next_tok.lower_ in _MINUTE_TIME_NOUNS:
        return None
    if token.dep_ not in {'amod', 'compound', 'nmod'}:
        return None
    if token.i > 0:
        # Number words, digits, and last/next/final before 'minute' fix the
        # time unit ("ten minute walk", "a 10 minute break", "last minute
        # decision"). See _MINUTE_TIME_PREV for why 'a' is not blocked.
        # Hyphenated compounds tokenize as e.g. five / - / minute, so look
        # THROUGH a hyphen at the real modifier — a corpus sweep showed
        # "a five-minute walk" / "last-minute decision" (the dominant
        # spelling) slipping past an immediate-neighbour-only check.
        prev_i = token.i - 1
        if doc[prev_i].text == '-' and prev_i > 0:
            prev_i -= 1
        prev = doc[prev_i].lower_
        if prev in _MINUTE_TIME_PREV or prev.isdigit():
            return None
    return 'maɪˈnut'


def _lead_rule(token, doc):
    """'lead' → /lɛd/ when the context cues the metal/material sense.
    Verb/leader default (/lid/) falls through to misaki's gold."""
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ in _LEAD_MATERIAL_NEXT:
        return 'lˈɛd'
    window_prev, _ = _sent_window(token, doc, before=3)
    if window_prev & _LEAD_MATERIAL_PREV:
        return 'lˈɛd'
    return None


def _breathed_rule(token, doc):
    """'breathed' → /bɹˈiðd/. Misaki US gold has 'breathed' as /bɹˈɛθt/
    (treats it as 'breath' + /t/), but it's the past tense of 'breathe'
    /bɹˈið/ — voiced /ð/, long /i/. No noun homonym exists, so the
    override is unconditional. GB gold has no entry for 'breathed' at
    all; this fills that gap too. Other forms (breathe, breathes,
    breathing, breather) are already correct in misaki gold/silver."""
    return 'bɹˈiðd'


def _teethed_rule(token, doc):
    """'teethed' → /tˈiðd/. Same bug pattern as 'breathed': misaki silver
    (US + GB) has 'teethed' as /tˈiθt/ (treats it as 'teeth' + /t/), but
    it's the past tense of the verb 'teethe' /tˈið/, voiced /ð/. No noun
    homonym, so unconditional."""
    return 'tˈiðd'


def _bass_rule(token, doc):
    """'bass' → /beɪs/ when the context cues the musical instrument.
    Fish default (/bæs/) falls through to misaki's gold."""
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ in _BASS_MUSIC_NEXT:
        return 'bˈeɪs'
    window_prev, _ = _sent_window(token, doc, before=3)
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
    # 'row of X' is the canonical line sense ('a row of houses/tents/seats')
    # regardless of what modifier precedes it — 'a big row of houses' must
    # not become the argument sense just because 'big' is an argument cue.
    if token.i + 1 < len(doc) and doc[token.i + 1].lower_ == 'of':
        return None
    window_prev, _ = _sent_window(token, doc, before=3)
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
    window_prev, _ = _sent_window(token, doc, before=4)
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
    'breathed': _breathed_rule,
    'teethed': _teethed_rule,
    'content': _content_rule,
    'minute': _minute_rule,
    'lead': _lead_rule,
    'bass': _bass_rule,
    'row': _row_rule,
    'tearing': _tearing_rule,
    'learned': _ed_adjective_rule,
    'blessed': _ed_adjective_rule,
    'aged': _ed_adjective_rule,
    'cursed': _ed_adjective_rule,
    'beloved': _beloved_rule,
    'delegate': _delegate_rule,
    'prayer': _prayer_rule,
    'prayers': _prayer_rule,
    'frequent': _frequent_rule,
    'frequents': _frequent_rule,
    'frequented': _frequent_rule,
    'frequenting': _frequent_rule,
    'consummate': _consummate_rule,
}


def _is_inside_markdown(text, start, end):
    """True if text[start:end] sits inside existing `[word](/IPA/)` markdown."""
    return start > 0 and text[start - 1] == '[' and text[end:end + 3] == '](/'


_MARKDOWN_SPAN_RE = re.compile(r'\[[^\]\n]*\]\(/[^)\n]*/\)')


def _markdown_spans(text):
    """(start, end) spans of every `[word](/IPA/)` unit already in text.

    Later passes (substitutions, phoneme overrides) must not match inside
    these — rewrapping a wrapped word emits nested markdown that misaki's
    LINK_REGEX mis-parses into spoken garbage."""
    return [m.span() for m in _MARKDOWN_SPAN_RE.finditer(text)]


def _overlaps_spans(spans, start, end):
    return any(s < end and start < e for s, e in spans)


def apply_contextual_overrides(text, suppress=frozenset()):
    """Emit IPA markdown for heteronyms whose pronunciation needs more than
    a single POS tag. Each rule in `_CONTEXTUAL_RULES` returns an IPA string
    when its context cue fires, or None to leave the token to misaki's gold.

    Skips tokens already wrapped in `[word](/IPA/)`, and any word in
    `suppress` (lowercased) — words the user has configured a phoneme
    override or substitution for. Those passes run later and must stay
    authoritative; wrapping here would either nest markdown or silently
    beat the user's setting.
    """
    if not HAS_SPACY:
        return text
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    replacements = []
    for token in doc:
        if token.text.lower() in suppress:
            continue
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

def resolve_contractions(text, suppress=frozenset()):
    """Expand ambiguous contractions using spaCy POS context.

    A contraction whose full surface form ("she'd", "it's") is in
    `suppress` is left alone — the user has a substitution or override
    keyed on it, and that later pass must see the original text."""
    if not HAS_SPACY:
        return text
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    replacements = []
    for i, token in enumerate(doc):
        if suppress and i > 0 and token.text.lower() in ("'s", "'d"):
            prev_t = doc[i - 1]
            if (prev_t.idx + len(prev_t.text) == token.idx
                    and (prev_t.text + token.text).lower() in suppress):
                continue
        if token.text.lower() == "'s":
            # Only expand when spaCy tags the 's itself as a present-tense
            # verb (VBZ). Possessive 's is tagged POS and "let's" is PRP —
            # expanding those to is/has corrupts possessive noun phrases
            # ("the book's torn pages" → "the book has torn pages").
            if token.tag_ != 'VBZ':
                continue
            nxt = doc[i + 1] if i + 1 < len(doc) else None
            if nxt and nxt.pos_ in ('VERB', 'AUX') and nxt.tag_ == 'VBG':
                replacements.append(
                    (token.idx, token.idx + len(token.text), ' is'))
            elif nxt and nxt.tag_ in ('VBN', 'VBD'):
                replacements.append(
                    (token.idx, token.idx + len(token.text), ' has'))
        elif token.text.lower() == "'d":
            # 'd is a genuine contraction only when tagged as an auxiliary:
            # MD when it means "would", VBD when it means "had" before a
            # past participle ("He'd seen it.").
            if token.tag_ not in ('MD', 'VBD'):
                continue
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


# Latin letters with no NFD decomposition — strip_diacritics can't fold
# them, so without this table they reach misaki's ASCII-only lexicon
# verbatim ('Encyclopædia', 'Brontë's œuvre') and silently drop from the
# audio. English-only, applied alongside strip_diacritics.
_NON_DECOMPOSABLE_FOLDS = {
    'æ': 'ae', 'Æ': 'Ae',
    'œ': 'oe', 'Œ': 'Oe',
    'ø': 'o', 'Ø': 'O',
    'ß': 'ss',
    'ł': 'l', 'Ł': 'L',
    'đ': 'd', 'Đ': 'D',
    'ð': 'th', 'þ': 'th', 'Þ': 'Th',
}


def strip_diacritics(text):
    """Strip accent marks so accented words match the ASCII-only TTS lexicon."""
    for char, replacement in _NON_DECOMPOSABLE_FOLDS.items():
        if char in text:
            text = text.replace(char, replacement)
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


# `résumé` (CV/noun) collides with `resume` (verb) after diacritic strip.
# misaki gold has only the verb pronunciation /ɹəzˈum/, so the noun would
# otherwise be read as the verb. Detect the accented spelling and wrap as
# inline IPA markdown BEFORE strip_diacritics so the cue survives. Plain
# `resume` (no accents) is left alone and keeps misaki's verb default.
_RESUME_NOUN_RE = re.compile(r'\b([Rr])(?:ésumé|esumé|ésume)(s?)\b')


def _wrap_resume_noun(m):
    initial = m.group(1)
    plural = m.group(2)
    ipa = 'ˈɹɛzəmeɪz' if plural else 'ˈɹɛzəmeɪ'
    ipa = _to_misaki_phonemes(ipa)
    return f'[{initial}esume{plural}](/{ipa}/)'


def normalize_unicode(text, is_english=True, suppress=frozenset()):
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
        # ASCII hyphen between bare digits too ("10-20" \u2192 "10 to 20").
        # Conservative: only fires when digits flank the hyphen, sparing
        # "20-year-old", "Catch-22", "3-D", "F-16".
        text = re.sub(r'(\d)-(\d)', r'\1 to \2', text)
    # Remaining en-dashes
    text = text.replace('\u2013', ' - ')
    if is_english:
        # Preserve accented noun spellings as IPA markdown before the strip
        # destroys the cue. Must run BEFORE strip_diacritics. Suppressed
        # when the user configured their own override/substitution for the
        # word — strip_diacritics then folds the accents and the user's
        # pass (which runs later) matches the plain spelling instead.
        def _wrap(m):
            if f'resume{m.group(2)}' in suppress:
                return m.group(0)
            return _wrap_resume_noun(m)
        text = _RESUME_NOUN_RE.sub(_wrap, text)
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
    'Ave.': 'Avenue',
    'blvd.': 'boulevard',
    'Blvd.': 'Boulevard',
    # Latin
    'e.g.': 'for example',
    'i.e.': 'that is',
    'cf.': 'compare',
    'viz.': 'namely',
    'et al.': 'and others',
    'ca.': 'circa',
    # 'c.' is NOT here — it needs context (see _resolve_c_dot): the
    # unconditional entry rewrote lettered list items ('a. apples b. pears
    # c. plums') and the c. inside '400 b.c.' as 'circa'.
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
    'Fig.': 'Figure',
    'Pt.': 'Part',
}


# `St.` needs context to expand: "Main St." is Street; "St. Patrick" is Saint.
# `_resolve_st_dot` runs AFTER the dict-loop in expand_abbreviations so title
# abbreviations like `Dr.` → `Doctor` and `Mt.` → `Mount` are already expanded;
# that lets the title-prefix and place-prefix branches catch chains like
# "Dr. St. James" → "Doctor Saint James" and "Mt. St. Helens" → "Mount Saint Helens".
_ST_PLACE_PREFIX = frozenset({
    'mount', 'mt', 'lake', 'cape', 'fort', 'port', 'old', 'new',
    'south', 'north', 'east', 'west', 'upper', 'lower', 'great', 'little',
})

_ST_TITLE_PREFIX = frozenset({
    'father', 'brother', 'sister', 'pope', 'bishop', 'cardinal',
    'reverend', 'monsignor', 'archbishop', 'patriarch', 'saint',
    'mister', 'missus', 'miss', 'doctor', 'professor',
    'king', 'queen', 'prince', 'princess', 'lord', 'lady',
    'duke', 'duchess', 'earl', 'count', 'baron', 'baroness',
    'sir', 'dame',
})

_ST_DOT_RE = re.compile(r'(?<!\w)St\.(?=\W|$)')


def _resolve_st_dot(text):
    """Expand 'St.' to 'Saint' or 'Street' based on local context.

    Decision tree (first match wins):
      1. Preceding word is an ordinal (\\d+ + st/nd/rd/th) → Street.
      2. Preceding word is a place-name prefix (Mount, Lake, Old, …) → Saint.
      3. Preceding word is a person-title prefix (Father, Doctor, King, …) → Saint.
      4. Preceding word is capitalised AND `St.` is not at the start of a
         sentence (text before doesn't end with .!? and isn't empty) → Street.
      5. Default → Saint (preserves the common 'St. <Name>' expansion).
    """
    def _replace(m):
        before = text[:m.start()].rstrip()
        # Last whitespace-delimited token, with surrounding punctuation
        # stripped. Allows digit-leading tokens like "42nd" to be detected
        # by the ordinal rule below.
        tokens_before = before.split()
        prev_word_raw = (
            tokens_before[-1].strip(",.;:!?\"'()[]{}<>")
            if tokens_before else ''
        )
        prev_word = prev_word_raw.lower()
        prev_is_capital = bool(prev_word_raw and prev_word_raw[0].isupper())
        is_sentence_start = (
            not before
            or re.search(r'[.!?]\s*$', before) is not None
        )

        # 'Street' trails its noun and so can end a sentence ("...on Elm St.
        # It was cold."); keep the terminator in that position. 'Saint'
        # precedes a name and never can, so it stays bare.
        street = 'Street.' if _ends_sentence(text[m.end():]) else 'Street'

        if re.fullmatch(r"\d+(?:st|nd|rd|th)", prev_word, re.IGNORECASE):
            return street
        if prev_word in _ST_PLACE_PREFIX:
            return 'Saint'
        if prev_word in _ST_TITLE_PREFIX:
            return 'Saint'
        if prev_is_capital and not is_sentence_start:
            return street
        return 'Saint'

    return _ST_DOT_RE.sub(_replace, text)


# `No.` → `Number` only when followed by a digit. Bare `No.` at sentence end
# (a one-word reply) must stay as the word "No"; the unconditional rule
# previously rewrote `She nodded. No. Not yet.` as `… Number Not yet.`.
_NO_DOT_RE = re.compile(r'(?<!\w)No\.(?=\W|$)')


def _resolve_no_dot(text):
    """Expand 'No.' to 'Number' only when a digit follows (issue/lot/item
    numbers). Otherwise leave 'No.' unchanged so it reads as the word "No"
    with its sentence-ending period."""
    def _replace(m):
        after = text[m.end():].lstrip()
        if re.match(r'\d', after):
            return 'Number'
        return m.group(0)
    return _NO_DOT_RE.sub(_replace, text)


# `Ed.` → `Edition` only after a digit or ordinal ("2nd Ed."). Elsewhere
# `Ed.` is far more likely the name closing a sentence ("Thanks, Ed.") —
# the unconditional dict entry rewrote that as "Thanks, Edition".
_ED_DOT_RE = re.compile(r'(?<!\w)Ed\.(?=\W|$)')


def _resolve_ed_dot(text):
    """Expand 'Ed.' to 'Edition' only when the preceding word is a digit or
    ordinal ('2nd Ed.', '3 Ed.'). Otherwise leave 'Ed.' unchanged so the
    name Ed keeps its sentence-ending period."""
    def _replace(m):
        tokens_before = text[:m.start()].rstrip().split()
        prev_word = (
            tokens_before[-1].strip(",.;:!?\"'()[]{}<>")
            if tokens_before else ''
        )
        if re.fullmatch(r'\d+(?:st|nd|rd|th)?', prev_word, re.IGNORECASE):
            return 'Edition'
        return m.group(0)
    return _ED_DOT_RE.sub(_replace, text)


# `c.` → `circa` only when a digit follows ('c. 1850'). The lookbehind also
# excludes a preceding dot so the `c.` inside lowercase era abbreviations
# ('400 b.c.') never matches. Capital `C.` is left alone — it's an initial.
_C_DOT_RE = re.compile(r'(?<![\w.])c\.(?=\W|$)')


def _resolve_c_dot(text):
    """Expand 'c.' to 'circa' only before a digit ('c. 1850'). Lettered
    list items ('a. apples b. pears c. plums') and era abbreviations stay
    untouched."""
    def _replace(m):
        after = text[m.end():].lstrip()
        if re.match(r'\d', after):
            return 'circa'
        return m.group(0)
    return _C_DOT_RE.sub(_replace, text)


# Abbreviations that commonly END a sentence ("...and so on, etc."). Their
# trailing dot doubles as the sentence terminator, so a plain expansion drops
# the pause ("etc. Then he left." → "et cetera Then he left."). When one of
# these is followed by a sentence boundary (whitespace + capital, or end of
# text) the expansion keeps a period. Title abbreviations (Mr., Dr.) are NOT
# in this set — "Mr. Smith" must stay "Mister Smith", not "Mister. Smith";
# a title precedes a name and so can never end a sentence.
#
# The suffix pair Jr./Sr. is deliberately absent even though it DOES end
# sentences ("...John Smith Jr. He was tall."). _ends_sentence can't tell that
# from the compound names those two head — "Jr. High School", "Martin Luther
# King Jr. Day", "Sr. García" in Spanish dialogue — all of which match the
# whitespace+capital heuristic. A spurious break mid-name ("King Junior. Day")
# is worse than the missing pause it would fix, so they keep the plain
# expansion.
_TERMINAL_ABBR = frozenset({
    'etc.', 'et al.', 'al.',
    # Street/place suffixes: these FOLLOW their noun, so they land at the end
    # of a sentence routinely ("He lived on Fifth Ave. The rain fell.").
    'Ave.', 'ave.', 'Blvd.', 'blvd.', 'Rd.', 'Ln.', 'Hwy.', 'Pkwy.', 'Ctr.',
    # Common lowercase abbreviations that likewise trail their noun.
    'dept.', 'govt.', 'assn.', 'approx.',
})


def _ends_sentence(tail):
    """True when `tail` (the text right after an abbreviation's dot) looks
    like a sentence boundary — end of text, or whitespace then a capital.

    Shared by expand_abbreviations and _resolve_st_dot so the two paths can't
    drift. Inherently approximate: it can't distinguish "Fifth Ave. The rain
    fell." from "Fifth Ave. Station", and reads the latter as a boundary. The
    sentence-ending case is much the commoner in prose, and the cost of being
    wrong is a short spurious pause rather than a dropped word.
    """
    return tail.strip() == '' or re.match(r'\s+[A-Z]', tail) is not None


def expand_abbreviations(text):
    for abbr, expansion in ABBREVIATIONS.items():
        # Word-boundary-aware replacement. The lookahead accepts any
        # non-word char, not just whitespace, so abbreviations expand when
        # punctuation follows too: 'Smith et al., 2020', '(etc.)', 'etc."'.
        pattern = re.escape(abbr)
        if abbr in _TERMINAL_ABBR:
            def _repl(m, e=expansion):
                if _ends_sentence(m.string[m.end():]):
                    return e + '.'
                return e
            text = re.sub(r'(?<!\w)' + pattern + r'(?=\W|$)', _repl, text)
        else:
            text = re.sub(r'(?<!\w)' + pattern + r'(?=\W|$)', expansion, text)
    text = _resolve_st_dot(text)
    text = _resolve_no_dot(text)
    text = _resolve_ed_dot(text)
    text = _resolve_c_dot(text)
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


# Strict roman form: rejects malformed repeats like 'VV' or 'IIII' that
# the greedy accumulator below would happily sum.
_STRICT_ROMAN_RE = re.compile(
    r'M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})')


def _roman_to_int(s):
    """Convert a strictly-formed Roman numeral string to an integer."""
    if not s or not _STRICT_ROMAN_RE.fullmatch(s):
        return None
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
        raw = match.group(2)
        # Lowercase tokens are treated as numerals only when built purely
        # from i/v/x ('chapter iv', 'scene ii'). Tokens containing lowercase
        # l/c/d/m are far more likely ordinary words — 'part mix', 'part li'
        # — than numerals. Uppercase tokens always qualify.
        if raw != raw.upper() and not set(raw) <= set('ivx'):
            return match.group(0)
        if len(raw) == 1:
            # Single-letter numerals collide with the pronoun 'I' ("the
            # book I read", "for my part I agree") and stray initials.
            # Convert only in heading-like contexts: a capitalised keyword
            # ("Book I covers...") or the numeral closing the line/clause
            # ("chapter i." / "part i: ..."). Comma is deliberately not a
            # closing cue — "for my part I, too, agreed" must survive.
            tail = match.string[match.end():]
            heading_like = re.match(r'[ \t]*(?:$|[\n.:;)])', tail)
            if not (keyword[0].isupper() or heading_like):
                return match.group(0)
            # C/D/L/M are never intended as single-letter section numerals
            # ("Appendix C", "Part D") even in heading position — only I/V/X
            # stay ambiguous (and V/X already need the heading guard above).
            if raw.upper() not in {'I', 'V', 'X'}:
                return match.group(0)
        roman = raw.upper()
        # A token using c/d/l/m that also spells a common English word
        # (MIX=1009, DIV=504) is far more likely the word than a section
        # numeral — skip it. Pure i/v/x numerals (XI, VII, XIV) are the
        # legitimate common chapter markers and are never skipped, even when
        # they collide with a lexicon word (e.g. "xi"). Falls back to
        # converting if the gold list can't be read.
        if len(raw) > 1 and not set(raw.lower()) <= set('ivx'):
            _, gold_lower = _load_acronym_skip_sets()
            if raw.lower() in gold_lower:
                return match.group(0)
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
    '\u00b0': ' degrees ',    # trailing space so "98.6F" stays "degrees F" not "degreesF";°
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


# Arrow glyphs across every Unicode arrow block. LitRPG / progression-fantasy
# stat blocks ("Skill (lv50) ➔ Skill (lv60)", "HP 90 ⇒ 100") use far more
# variants than the basic ←↑→↓ enumerated in SYMBOL_REPLACEMENTS — dingbat
# arrows (➔ ➜ ➡), heavy/double arrows (⇒ ⟶ ⮕), etc. None should reach the TTS,
# so any arrow is stripped to a space (matching the basic-arrow handling).
_ARROW_RE = re.compile(
    '[←-⇿'   # Arrows
    '➔-➿'    # dingbat arrows (➔ ➜ ➡ …)
    '⟰-⟿'    # Supplemental Arrows-A
    '⤀-⥿'    # Supplemental Arrows-B
    '⬀-⯿]'   # Miscellaneous Symbols and Arrows (⬅ ⬆ ⮕ …)
)


def _strip_url(m):
    """Drop a URL but keep any sentence punctuation glued to its end —
    'See https://example.com, then go.' must keep the comma pause cue."""
    trail = re.search(r'[.,!?;:]+$', m.group(0))
    return trail.group(0) if trail else ''


def clean_special_characters(text, is_english=True):
    # Remove URLs and emails
    text = URL_PATTERN.sub(_strip_url, text)
    text = EMAIL_PATTERN.sub('', text)

    # Typewriter em-dash: a run of 2+ hyphens *between* words is an em-dash
    # ("wait--no" / "wait -- no"), not a scene break. Convert to a comma pause
    # before the scene-break stripper runs — otherwise misaki glues the words
    # ("wait--no" → "waitno") or the 3+ stripper deletes them ("wait---no" →
    # "waitno"). The lookbehind/ahead require a word char on both sides, so a
    # standalone "---" separator line still falls through to the stripper.
    text = re.sub(r'(?<=\w)\s*-{2,}\s*(?=\w)', ', ', text)

    # Remove scene break markers (3+ repeated special characters, e.g. ***, ---, ~~~, ===).
    # Dots are deliberately NOT in the class: '...' is a pause cue for the TTS
    # (normalize_unicode folds … into '...'), so deleting dot-runs would
    # strip the trailing-off prosody from every book. Runs of 4+ dots collapse
    # to a plain ellipsis instead.
    text = re.sub(r'[\*\-\~\=\_\#\+]{3,}', '', text)
    text = re.sub(r'\.{4,}', '...', text)

    # Replace symbols (English words for English; strip to space otherwise)
    for symbol, replacement in SYMBOL_REPLACEMENTS.items():
        text = text.replace(symbol, replacement if is_english else ' ')

    # Strip any remaining arrow glyph (dingbat/supplemental variants common in
    # LitRPG stat blocks) that the per-symbol table above doesn't enumerate.
    text = _ARROW_RE.sub(' ', text)

    # Collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text)
    # Collapse 3+ newlines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip trailing whitespace on each line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    return text


# --- Main entry point ---

def apply_substitutions(text, substitutions, fold_diacritics=False):
    """Apply user-defined word substitutions.

    Each entry is a dict with 'find', 'replace', and optional 'case_sensitive'
    and 'whole_word' booleans.

    `fold_diacritics` — normalize_text passes True for English text, whose
    accents were already stripped by strip_diacritics: a find of 'Zoë'
    could otherwise never match the folded 'Zoe' in the text.
    """
    if not substitutions:
        return text
    for sub in substitutions:
        find = sub.get('find', '')
        replace = sub.get('replace', '')
        if not find:
            continue
        if fold_diacritics:
            find = strip_diacritics(find)
            # An Mn-only find (e.g. a bare combining accent) folds to ''.
            # Re-check: whole_word would otherwise build '' + re.escape('')
            # + '' — the empty pattern, which matches at every position and
            # inserts `replace` between every character of the book.
            if not find:
                continue
        if sub.get('whole_word', True):
            # \b only asserts next to word chars; for finds with non-word
            # edges ('$100', '#tag') anchor only the word-char side or the
            # rule could never match.
            prefix = r'\b' if re.match(r'\w', find) else ''
            suffix = r'\b' if re.search(r'\w$', find) else ''
            pattern = prefix + re.escape(find) + suffix
        else:
            pattern = re.escape(find)
        flags = 0 if sub.get('case_sensitive', False) else re.IGNORECASE
        # Use a lambda for the replacement so backref-looking content in the
        # user's replace string (e.g. "\1", "\g<0>") is treated as a literal
        # instead of a regex substitution template. Matches inside existing
        # `[word](/IPA/)` markdown are left alone — rewriting the display
        # text or the IPA would corrupt the link an earlier pass emitted.
        spans = _markdown_spans(text)
        text = re.sub(
            pattern,
            lambda m, r=replace, s=spans:
                m.group(0) if _overlaps_spans(s, m.start(), m.end()) else r,
            text,
            flags=flags,
        )
    return text


# --- Phoneme overrides + acronym spellout ---

# Proper nouns where misaki's gold/silver lexicons are silent and espeak's
# letter-rule G2P mispronounces. Canonical IPA — `_to_misaki_phonemes` folds
# diphthongs at emit time. User overrides for the same word win
# (see apply_builtin_phoneme_overrides).
_BUILTIN_PHONEME_OVERRIDES = {
    # US place names
    'angeles':  'ˈænʤələs',      # Los Angeles (ʤ ligature — misaki has no two-char dʒ)
    'los':      'lˈɔs',          # Los (Angeles/Alamos/Gatos) — misaki says /loʊz/ ("lohz")
    'yosemite': 'joʊsˈɛmɪti',    # Yosemite
    'jolla':    'hˈɔɪə',         # La Jolla
    # Irish / Scottish given names common in fiction
    'sean':     'ʃˈɔn',
    'siobhan':  'ʃəvˈɔn',
    'aoife':    'ˈifə',
    'niamh':    'nˈiv',
    'caoimhe':  'kˈivə',
    'eilidh':   'ˈeɪli',
    # Other gaps and bugs
    'blaise':   'blˈeɪz',         # Blaise Pascal; absent from misaki gold/silver
    'dives':    'dˈaɪvz',         # silver has biblical /ˈdaɪvˌiːz/ for lowercase
}


def apply_builtin_phoneme_overrides(text, user_overrides):
    """Wrap built-in proper-noun overrides as `[word](/IPA/)` markdown so
    misaki's LINK_REGEX picks them up at rating 5. Skips any word the user
    has explicitly configured in their phoneme overrides — the user list
    always wins. Runs immediately before apply_phoneme_overrides; each
    word is wrapped once across the two passes, so no double-wrapping."""
    user_words = set()
    if user_overrides:
        for entry in user_overrides:
            if not entry.get('enabled', True):
                continue
            # Folded like the text: a user override for 'Séan' must still
            # beat the built-in 'sean'.
            w = strip_diacritics((entry.get('word') or '').lower())
            if w:
                user_words.add(w)

    for word, ipa in _BUILTIN_PHONEME_OVERRIDES.items():
        if word in user_words:
            continue
        ipa_folded = _to_misaki_phonemes(ipa)
        pattern = r'\b' + re.escape(word) + r'\b'
        spans = _markdown_spans(text)
        text = re.sub(
            pattern,
            lambda m, p=ipa_folded, s=spans:
                m.group(0) if _overlaps_spans(s, m.start(), m.end())
                else f'[{m.group(0)}](/{p}/)',
            text,
            flags=re.IGNORECASE,
        )
    return text


def apply_phoneme_overrides(text, overrides, fold_diacritics=False):
    """Wrap matching words with misaki's inline-phoneme markdown.

    Each entry is a dict with:
      word: str — the word to match
      ipa: str — IPA phonemes (misaki assigns rating 5, beating gold/silver)
      case_sensitive: bool (default False)
      enabled: bool (default True)

    Emits `[word](/IPA/)` which misaki parses in G2P.preprocess. Words
    containing non-word chars (apostrophes, hyphens) match without `\\b`
    anchors so names like O'Brien or Anne-Marie still work.

    `fold_diacritics` — normalize_text passes True: the English text was
    already ASCII-folded by strip_diacritics, so an accented word ('Zoë')
    must be folded too or it can never match.
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
        if fold_diacritics:
            word = strip_diacritics(word)
            # An Mn-only word (e.g. a bare combining accent) folds to ''.
            # Re-check: an empty word would build the empty pattern below,
            # matching at every position and wrapping every character.
            if not word:
                continue
        # Dictionaries hand out canonical IPA (eɪ, aʊ, …); Kokoro's vocab keys
        # the diphthongs as single letters (A, W, …). See _to_misaki_phonemes
        # for why mixing the two forms makes Kokoro bleed the override audio
        # onto neighbouring words.
        ipa = _to_misaki_phonemes(ipa)
        # Anchor \b only on word-char edges: O'Brien / Anne-Marie keep both
        # anchors (their edge chars are letters) while still matching their
        # internal punctuation; a word with a non-word edge drops only the
        # anchor that could never match there.
        prefix = r'\b' if re.match(r'\w', word) else ''
        suffix = r'\b' if re.search(r'\w$', word) else ''
        pattern = prefix + re.escape(word) + suffix
        flags = 0 if entry.get('case_sensitive', False) else re.IGNORECASE
        # Lambda keeps backref-looking IPA (`\1`, `\g<0>`) as literal text.
        # Matches inside existing markdown are skipped — earlier passes
        # suppress wrapping for user-overridden words, so any wrap already
        # present came from another entry or another word's IPA.
        spans = _markdown_spans(text)
        text = re.sub(
            pattern,
            lambda m, p=ipa, s=spans:
                m.group(0) if _overlaps_spans(s, m.start(), m.end())
                else f'[{m.group(0)}](/{p}/)',
            text,
            flags=flags,
        )
    return text


# Pronounceable acronyms misaki only has lowercase entries for. The
# lowercase-gold check below covers these too; kept as a fallback for when
# the bundled lexicon can't be read.
_ACRONYM_EXTRA_SKIP = frozenset({
    'SCUBA', 'LASER', 'RADAR', 'SONAR', 'SWAT', 'TASER', 'MODEM',
    'WASP', 'CAPTCHA', 'GULAG',
})

_ACRONYM_GOLD_CACHE = None

_ACRONYM_REAL_WORD_CACHE = None

_ACRONYM_PATTERN = re.compile(r'\b[A-Z]{2,6}\b')


def _load_real_word_set():
    """Lowercase real words (beyond misaki gold) whose all-caps emphasis
    must not be letterized. Gold lacks inflections like 'bowed', so "HE
    BOWED" used to become "HE B. O. W. E. D.". Always includes the words
    this module itself wraps (contextual rules, heteronyms, built-in
    overrides); cmudict (a dev extra) widens coverage when installed —
    restricted to 4+ letters so short letter-read acronyms that happen to
    be cmudict entries (FBI, CIA) still spell out."""
    global _ACRONYM_REAL_WORD_CACHE
    if _ACRONYM_REAL_WORD_CACHE is not None:
        return _ACRONYM_REAL_WORD_CACHE
    words = set(_CONTEXTUAL_RULES) | set(HETERONYMS)
    words.update(_BUILTIN_PHONEME_OVERRIDES)
    try:
        import cmudict
        words.update(
            w for w in cmudict.dict()
            if w.isalpha() and 4 <= len(w) <= 6)
    except Exception:
        pass
    _ACRONYM_REAL_WORD_CACHE = frozenset(words)
    return _ACRONYM_REAL_WORD_CACHE


def _load_acronym_skip_sets():
    """Return (caps_words, lowercase_words) misaki gold already pronounces.

    caps_words are gold's ALL-CAPS keys (genuine acronyms like NATO);
    lowercase_words is every alphabetic gold key lowercased, used to spare
    all-caps emphasis of ordinary words ("STOP", "THE END") from
    letterization — misaki looks words up case-insensitively. Loaded
    lazily; a failure to read the bundled lexicon falls back to the small
    static skip list so auto-acronym still runs for the obvious cases.
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
        lower = frozenset(
            k.lower() for k in gold
            if k.isalpha() and 2 <= len(k) <= 6
        )
        _ACRONYM_GOLD_CACHE = (caps | _ACRONYM_EXTRA_SKIP, lower)
    except Exception as e:
        warnings.warn(
            f"Failed to load autiobooks.misaki.data/us_gold.json for "
            f"acronym spellout ({e!r}); falling back to a "
            f"{len(_ACRONYM_EXTRA_SKIP)}-word skip list. Unknown-acronym "
            f"spellout will letterize ordinary all-caps words.",
            RuntimeWarning,
        )
        _ACRONYM_GOLD_CACHE = (
            frozenset(_ACRONYM_EXTRA_SKIP),
            frozenset(w.lower() for w in _ACRONYM_EXTRA_SKIP),
        )
    return _ACRONYM_GOLD_CACHE


def apply_acronym_spellout(text, enabled, protected=None):
    """Rewrite unknown ALL-CAPS tokens as dotted letters.

    `NATO` / `NASA` / `SQL` stay untouched because misaki gold already
    pronounces them. `CIA` / `FBI` / `HTML` become `C. I. A.` etc. so
    misaki reads each letter. Also left alone: valid roman numerals (all
    of II, XIII, XIV, … — section markers, not acronyms) and all-caps
    emphasis of ordinary gold words ("STOP", "THE END"). Off by default —
    on-by-default would change behavior on every book in the existing
    user base.

    `protected` — optional set of UPPERCASE words to leave untouched (words
    that have phoneme overrides; those passes run later and could no longer
    match a word rewritten to 'C. I. A.').
    """
    if not enabled:
        return text
    caps_skip, lower_skip = _load_acronym_skip_sets()
    real_words = _load_real_word_set()
    # This pass runs AFTER apply_contextual_overrides has emitted
    # [WORD](/IPA/) markdown — letterizing the display text inside a link
    # corrupts it into spoken garbage, so spans are off-limits.
    spans = _markdown_spans(text)

    def _replace(m):
        if _overlaps_spans(spans, m.start(), m.end()):
            return m.group(0)
        word = m.group(0)
        if word in caps_skip or (protected and word in protected):
            return word
        if _roman_to_int(word) is not None:
            return word
        if word.lower() in lower_skip or word.lower() in real_words:
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
    # Words the user has configured a phoneme override or substitution for.
    # Built-in markdown-emitting passes skip these so the user's pass —
    # which runs later — sees the original spelling and wins. Entries are
    # diacritic-folded for English to match the folded text; a multi-word
    # find contributes each of its words, so a contextual rule can't wrap
    # a word the user's phrase rule needs intact ('lead paint' → 'lead').
    suppress = set()

    def _fold(w):
        return strip_diacritics(w) if is_english else w

    if phoneme_overrides:
        for entry in phoneme_overrides:
            if entry.get('enabled', True) and entry.get('word'):
                suppress.add(_fold(entry['word'].lower()))
    if substitutions:
        for sub in substitutions:
            find = _fold((sub.get('find') or '').strip().lower())
            if find and re.fullmatch(r"[\w'-]+", find):
                suppress.add(find)
            elif find:
                suppress.update(re.findall(r"[\w'-]+", find))
    text = normalize_unicode(text, is_english=is_english, suppress=suppress)
    if is_english:
        text = expand_abbreviations(text)
        text = expand_roman_numerals(text)
        if heteronyms:
            text = apply_contextual_overrides(text, suppress=suppress)
            text = resolve_heteronyms(text, suppress=suppress)
        if contractions:
            text = resolve_contractions(text, suppress=suppress)
    text = clean_special_characters(text, is_english=is_english)
    text = apply_substitutions(text, substitutions,
                               fold_diacritics=is_english)
    if is_english:
        # Words with phoneme overrides (built-in or user) must survive the
        # acronym spellout untouched — the override passes run after it and
        # can no longer match a word rewritten to 'C. I. A.'.
        protected = {w.upper() for w in _BUILTIN_PHONEME_OVERRIDES}
        if phoneme_overrides:
            protected.update(
                _fold(entry.get('word') or '').upper()
                for entry in phoneme_overrides
                if entry.get('enabled', True) and entry.get('word'))
        text = apply_acronym_spellout(text, auto_acronyms, protected)
        text = apply_builtin_phoneme_overrides(text, phoneme_overrides)
        text = apply_phoneme_overrides(text, phoneme_overrides,
                                       fold_diacritics=True)
    return text
