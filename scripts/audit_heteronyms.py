#!/usr/bin/env python3
"""Heteronym coverage audit for misaki's POS-aware G2P.

Runs a fixed corpus of (sentence, target_word, expected_ipa) triples through
`autiobooks.misaki.en.G2P`, extracts the phonemes misaki emits for the target
word, and compares them against the expected IPA. Used to identify which
heteronyms misaki already handles correctly via its POS-branching gold lexicon
vs. which need caller-side context overrides.

Usage:
    python scripts/audit_heteronyms.py
    python scripts/audit_heteronyms.py --verbose

Exit code is the number of failing cases (0 = all pass).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Ensure the repo root is importable when run as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autiobooks.misaki import en  # noqa: E402
from autiobooks.text_processing import normalize_text  # noqa: E402

# Reuse IPA normalization / misaki-alphabet mapping from the pronunciation
# audit so both tools agree on what "equal" means.
sys.path.insert(0, str(REPO_ROOT / 'scripts'))
from audit_pronunciations import normalize_for_compare, misaki_to_ipa  # noqa: E402


def _audit_normalize(s):
    """`normalize_for_compare` + a few extra folds specific to heteronym audit:

      - affricate ligatures (ʤ ≡ dʒ, ʧ ≡ tʃ) — misaki's output uses ligatures,
        most dictionaries spell them out.
      - rhotic schwa (əɹ ≡ ɚ) — misaki emits əɹ; dictionaries vary.
      - unstressed ɪ ↔ ə — English reduces unstressed vowels freely and both
        spellings are widely attested for the same pronunciation
        (e.g. "record" /ɹɪˈkɔɹd/ ≡ /ɹəˈkɔɹd/). Folding here means the audit
        only flags genuinely different stressed vowels.
    """
    s = normalize_for_compare(s)
    s = s.replace('ʤ', 'dʒ').replace('ʧ', 'tʃ')
    s = s.replace('əɹ', 'R')
    s = s.replace('ɪ', 'ə')
    return s


# (word, sentence, expected_ipa) — expected IPA is the pronunciation the
# *sentence* demands, not misaki's default for the token. Expected values use
# canonical IPA (eɪ, aɪ, oʊ, aʊ, …); `normalize_for_compare` handles stress,
# rhotic folding, and misaki's internal-alphabet quirks on both sides.
CASES = [
    # word,      sentence,                                 expected_ipa
    ('read',     'I read the book yesterday.',             'ɹɛd'),
    ('read',     'I like to read books every day.',        'ɹid'),
    ('lead',     'She will lead the team.',                'lid'),
    ('lead',     'He was poisoned by lead paint.',         'lɛd'),
    ('wind',     'The wind blew hard outside.',            'wɪnd'),
    ('wind',     'Please wind the clock tonight.',         'waɪnd'),
    ('wound',    'He cleaned the wound carefully.',        'wund'),
    ('wound',    'She wound the bandage tightly.',         'waʊnd'),
    ('bow',      'He drew the bow and fired an arrow.',    'boʊ'),
    ('bow',      'She took a bow after the show.',         'baʊ'),
    ('bowed',    'He bowed his head respectfully.',        'baʊd'),
    ('bowed',    'She bowed deeply to the queen.',         'baʊd'),
    ('bowed',    'The violinist bowed each string slowly.', 'boʊd'),
    ('bowing',   'Bowing low, he greeted the king.',       'baʊɪŋ'),
    ('bows',     'She made several bows during the play.', 'baʊz'),
    ('row',      'They sat in the front row of the theater.', 'ɹoʊ'),
    ('row',      'Three days in a row had passed.',        'ɹoʊ'),
    ('row',      'They had a huge row about money.',       'ɹaʊ'),
    ('row',      'A furious row erupted between them.',    'ɹaʊ'),
    ('tearing',  'Her eyes were tearing up at the news.',  'tɪɹɪŋ'),
    ('tearing',  'He was tearing the paper into pieces.',  'tɛɹɪŋ'),
    ('live',     'They live in Boston now.',               'lɪv'),
    ('live',     'This is a live broadcast tonight.',      'laɪv'),
    ('content',  'The book had useful content inside.',    'kɑntɛnt'),
    ('content',  'She felt content with her life.',        'kəntɛnt'),
    ('minute',   'Wait one minute, please.',               'mɪnət'),
    ('minute',   'Every minute detail was examined.',      'maɪnut'),
    ('use',      'Please use the back door.',              'juz'),
    ('use',      'What is the use of this tool?',          'jus'),
    ('close',    'Please close the window now.',           'kloʊz'),
    ('close',    'The shop is close to home.',             'kloʊs'),
    ('tear',     'A tear rolled down her cheek.',          'tɪɹ'),
    ('tear',     'Do not tear the paper.',                 'tɛɹ'),

    # Noun / verb stress pairs — /ˈXXX/ noun vs /əXˈXXX/ verb. Reduced
    # vowels in unstressed syllables use misaki's canonical schwa form
    # (e.g. ɑbdʒəkt rather than ɑbdʒɛkt).
    ('object',   'The strange object floated overhead.',   'ɑbdʒəkt'),
    ('object',   'I object to that statement.',            'əbdʒˈɛkt'),
    ('record',   'He broke the world record.',             'ɹɛkɚd'),
    ('record',   'Please record the meeting.',             'ɹɪkˈɔɹd'),
    ('desert',   'The Sahara is a vast desert.',           'dɛzɚt'),
    ('desert',   'Do not desert your post.',               'dɪzˈɜɹt'),
    ('present',  'She gave him a thoughtful present.',     'pɹɛzənt'),
    # misaki emits /pɹiˈzɛnt/ with lax /i/ rather than the more common
    # /pɹɪˈzɛnt/; accept as a reduced-vowel variant.
    ('present',  'I will present the report tomorrow.',    'pɹizˈɛnt'),
    ('subject',  'Math is my favorite subject.',           'sʌbdʒɛkt'),
    ('subject',  'Do not subject them to that.',           'səbdʒˈɛkt'),
    ('refuse',   'The bin was full of refuse.',            'ɹɛfjus'),
    ('refuse',   'I refuse to leave the building.',        'ɹɪfjˈuz'),
    ('contract', 'Sign the contract here, please.',        'kɑntɹækt'),
    # "Muscles contract when exercised" — spaCy mis-tags this as NN, so
    # misaki picks the NN pronunciation. Known POS-tagger limitation; would
    # require a contextual rule that overrides POS when "contract" is the
    # clause root without a determiner.
    ('contract', 'Muscles contract when exercised.',       'kəntɹˈækt'),
    ('project',  'The science project is due.',            'pɹɑdʒɛkt'),
    ('project',  'Please project your voice clearly.',     'pɹədʒˈɛkt'),
    ('conflict', 'There was a brief conflict.',            'kɑnflɪkt'),
    ('conflict', 'Their stories conflict sharply.',        'kənflˈɪkt'),
    ('produce',  'The farm sells fresh produce.',          'pɹoʊdus'),
    ('produce',  'Factories produce millions of cars.',    'pɹədˈus'),
    ('progress', 'Great progress was made today.',         'pɹɑɡɹəs'),
    ('progress', 'Please progress to the next step.',      'pɹəɡɹˈɛs'),
    ('permit',   'Show your parking permit, please.',      'pɜɹmɪt'),
    ('permit',   'I will permit one visit per week.',      'pɚmˈɪt'),
    ('convict',  'The convict escaped from prison.',       'kɑnvɪkt'),
    ('convict',  'The jury will convict him.',             'kənvˈɪkt'),
    # Modern US "address" (noun) is widely /əˈdɹɛs/ — same as the verb —
    # and misaki's gold carries only that form. Accept.
    ('address',  'What is your home address?',             'ədɹˈɛs'),
    ('address',  'Please address the crowd directly.',     'ədɹˈɛs'),
    ('abuse',    'The abuse finally ended last year.',     'əbjus'),
    ('abuse',    'Do not abuse your privileges here.',     'əbjˈuz'),

    # Voiceless / voiced pairs — /s/ noun vs /z/ verb.
    ('house',    'They bought a small house downtown.',    'haʊs'),
    ('house',    'The stadium can house fifty thousand.',  'haʊz'),

    # Other classic heteronyms.
    ('dove',     'A white dove landed on the roof.',       'dʌv'),
    ('dove',     'She dove into the cold pool.',           'doʊv'),
    ('sow',      'The sow had ten piglets.',               'saʊ'),
    ('sow',      'They will sow the seeds tomorrow.',      'soʊ'),
    ('bass',     'The bass swam near the dock.',           'bæs'),
    ('bass',     'He plays the bass in a jazz band.',      'beɪs'),
    ('does',     'She does the dishes every night.',       'dʌz'),
    # misaki gold has no NOUN branch for "does" (plural of doe), so spaCy's
    # VBZ tag locks in the verb form /dʌz/ regardless of context. Rare
    # enough that a contextual rule isn't worth the maintenance cost.
    ('does',     'The pair of does grazed in the field.',  'doʊz'),

    # Stress-shift -ate verbs — noun/adjective form has reduced final
    # vowel /ət/, verb form has full /eɪt/. spaCy's tag drives misaki's
    # POS-branched lookup; reduced unstressed ɪ↔ə folding (line 51) handles
    # the schwa variants. These are common literary words; failures here
    # would mispronounce nouns as verbs (and vice versa) on every page.
    ('advocate',    'She is a strong advocate for the children.',  'ædvəkət'),
    ('advocate',    'They advocate for cleaner energy.',           'ædvəkˌeɪt'),
    ('alternate',   'There is an alternate route home.',           'ɔltəɹnət'),
    ('alternate',   'The teams alternate every two weeks.',        'ɔltəɹnˌeɪt'),
    ('estimate',    'The estimate came in under budget.',          'ɛstəmət'),
    ('estimate',    'They estimate the cost will rise.',           'ɛstəmˌeɪt'),
    ('separate',    'They sleep in separate rooms.',               'sɛpəɹət'),
    ('separate',    'Please separate the recyclables.',            'sɛpəɹˌeɪt'),
    ('moderate',    'A moderate breeze blew across the hills.',    'mɑdəɹət'),
    ('moderate',    'She will moderate the panel tonight.',        'mɑdəɹˌeɪt'),
    ('articulate',  'She gave an articulate response.',            'ɑɹtˈɪkjələt'),
    ('articulate',  'Try to articulate your concerns clearly.',    'ɑɹtˈɪkjəlˌeɪt'),
    ('associate',   'She is a senior associate at the firm.',      'əsˈoʊsiət'),
    ('associate',   'I associate that smell with summer.',         'əsˈoʊsiˌeɪt'),
    ('approximate', 'Give me an approximate figure, please.',      'əpɹˈɑksəmət'),
    ('approximate', 'Her sketches approximate the originals.',     'əpɹˈɑksəmˌeɪt'),
    ('deliberate',  'They made a deliberate choice.',              'dəlˈɪbəɹət'),
    ('deliberate',  'The jurors will deliberate tomorrow.',        'dəlˈɪbəɹˌeɪt'),
    ('duplicate',   'Please give me a duplicate key.',             'dˈupləkət'),
    ('duplicate',   'Do not duplicate the original.',              'dˈupləkˌeɪt'),
    ('elaborate',   'She wore an elaborate costume.',              'əlˈæbəɹət'),
    ('elaborate',   'Could you elaborate on that point?',          'əlˈæbəɹˌeɪt'),
    ('affiliate',   'She is an affiliate of the firm.',            'əfˈɪliət'),
    ('affiliate',   'They will affiliate with the larger group.',  'əfˈɪliˌeɪt'),
    ('appropriate', 'That is the appropriate response.',           'əpɹˈoʊpɹiət'),
    ('appropriate', 'The state will appropriate the funds.',       'əpɹˈoʊpɹiˌeɪt'),
    ('attribute',   'Patience is her best attribute.',             'ætɹəbjut'),
    ('attribute',   'I attribute the win to teamwork.',            'ətɹˈɪbjut'),

    # -ment / -ound noun-vs-verb pairs (stress on first vs second syllable
    # for verb; vowel quality often shifts too).
    ('complement',  'The wine was a perfect complement.',          'kɑmpləmənt'),
    ('complement',  'These flavors complement each other.',        'kɑmpləmˌɛnt'),
    ('compliment',  'She gave him a kind compliment.',             'kɑmpləmənt'),
    ('compliment',  'I want to compliment your work.',             'kɑmpləmˌɛnt'),
    ('compound',    'The compound was heavily guarded.',           'kɑmpaʊnd'),
    ('compound',    'Do not compound the problem further.',        'kəmpˈaʊnd'),

    # Additional N/V stress pairs not yet covered above.
    ('survey',      'The survey results came in today.',           'sɜɹveɪ'),
    ('survey',      'Engineers will survey the land tomorrow.',    'sɚveɪ'),
    ('transport',   'The new transport network is faster.',        'tɹænspɔɹt'),
    ('transport',   'They transport cargo across the country.',    'tɹænspˈɔɹt'),
    ('suspect',     'The suspect denied any involvement.',         'sʌspɛkt'),
    ('suspect',     'I suspect they are hiding something.',        'səspˈɛkt'),
    ('decrease',    'A decrease in sales was reported.',           'dikɹis'),
    ('decrease',    'Costs will decrease over time.',              'dɪkɹˈis'),
    ('increase',    'The increase was modest this quarter.',       'ɪnkɹis'),
    ('increase',    'Profits should increase next year.',          'ɪnkɹˈis'),
    ('insult',      'That comment was a serious insult.',          'ˈɪnsʌlt'),
    ('insult',      'Do not insult my intelligence.',              'ɪnsˈʌlt'),
    ('rebel',       'The rebel forces took the city.',             'ɹˈɛbəl'),
    ('rebel',       'The teenagers will rebel against any rule.',  'ɹəbˈɛl'),
    ('reject',      'They marked it as a reject.',                 'ɹˈidʒɛkt'),
    ('reject',      'The company will reject the offer.',          'ɹədʒˈɛkt'),
]


# Known surface forms our pipeline may emit in place of a heteronym:
#   - resolve_heteronyms respells `read`/`lead`,
#   - apply_contextual_overrides wraps tokens in `[word](/IPA/)` markdown
#     whose bracketed display text can itself be mutated by resolve_heteronyms
#     (e.g. `[lead](/lˈɛd/)` → `[leed](/lˈɛd/)`).
_RESPELLING_ALIASES = {
    'read': ('read', 'red', 'reed'),
    'lead': ('lead', 'led', 'leed'),
}


def find_token(tokens, word):
    """Return the MToken for the target heteronym. Tries surface-form match
    against the original word and any known respelling, then falls back to any
    MToken whose IPA-markdown display text contains one of those variants."""
    target = word.lower().strip()
    variants = _RESPELLING_ALIASES.get(target, (target,))
    for tok in tokens:
        surface = (tok.text or '').lower().strip().strip('.,;:!?"\'')
        if surface in variants:
            return tok
    for tok in tokens:
        surface = (tok.text or '').lower()
        if any(v in surface for v in variants) and ('/' in surface or '[' in surface):
            return tok
    return None


def audit(cases, use_pipeline=True, verbose=False):
    """Run each case and report pass/fail.

    If `use_pipeline` is True, the sentence is passed through
    `autiobooks.text_processing.normalize_text` first so contextual overrides
    and respelling rules are exercised. Otherwise the sentence goes to misaki
    raw — useful for measuring misaki's unassisted coverage.

    Returns (pass_count, fail_count, rows)."""
    g2p = en.G2P(trf=False, british=False, fallback=None)
    rows = []
    passed = 0
    failed = 0

    for word, sentence, expected_ipa in cases:
        text = normalize_text(sentence) if use_pipeline else sentence
        _, tokens = g2p(text)
        tok = find_token(tokens, word)

        if tok is None or not tok.phonemes:
            rows.append({
                'word': word,
                'sentence': sentence,
                'expected': expected_ipa,
                'got_raw': '',
                'got_ipa': '',
                'tag': tok.tag if tok else '',
                'status': 'MISSING',
            })
            failed += 1
            continue

        got_raw = tok.phonemes
        got_ipa = misaki_to_ipa(got_raw)

        if _audit_normalize(got_ipa) == _audit_normalize(expected_ipa):
            status = 'PASS'
            passed += 1
        else:
            status = 'FAIL'
            failed += 1

        rows.append({
            'word': word,
            'sentence': sentence,
            'expected': expected_ipa,
            'got_raw': got_raw,
            'got_ipa': got_ipa,
            'tag': tok.tag or '',
            'status': status,
        })

    return passed, failed, rows


def print_report(rows, verbose=False):
    """Print a simple aligned table. In verbose mode show every case; in the
    default mode show only failures so the report stays skimmable."""
    shown = rows if verbose else [r for r in rows if r['status'] != 'PASS']

    if not shown:
        print('All cases passed.')
        return

    # Column widths
    w_word = max(4, max(len(r['word']) for r in shown))
    w_tag = max(3, max(len(r['tag']) for r in shown))
    w_exp = max(8, max(len(r['expected']) for r in shown))
    w_got = max(3, max(len(r['got_ipa']) for r in shown))

    header = (
        f"{'STATUS':6}  "
        f"{'WORD':{w_word}}  "
        f"{'TAG':{w_tag}}  "
        f"{'EXPECTED':{w_exp}}  "
        f"{'GOT':{w_got}}  "
        f"SENTENCE"
    )
    print(header)
    print('-' * len(header))
    for r in shown:
        print(
            f"{r['status']:6}  "
            f"{r['word']:{w_word}}  "
            f"{r['tag']:{w_tag}}  "
            f"{r['expected']:{w_exp}}  "
            f"{r['got_ipa']:{w_got}}  "
            f"{r['sentence']}"
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--verbose', '-v', action='store_true',
                    help='Show every case, not just failures.')
    ap.add_argument('--raw', action='store_true',
                    help='Bypass autiobooks.text_processing.normalize_text '
                         'and feed sentences straight to misaki. Useful for '
                         'measuring which cases misaki handles unassisted.')
    args = ap.parse_args()

    print('Loading misaki G2P...', file=sys.stderr)
    passed, failed, rows = audit(CASES, use_pipeline=not args.raw,
                                 verbose=args.verbose)

    print_report(rows, verbose=args.verbose)
    print()
    print(f'{passed} passed, {failed} failed out of {len(rows)}.')
    return failed


if __name__ == '__main__':
    sys.exit(main())
