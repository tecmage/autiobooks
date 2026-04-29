#!/usr/bin/env python3
"""Audit misaki's pronunciations against CMU dict + stem-suffix consistency.

Walks the union of misaki's US gold+silver lexicons and CMU dict (~244k words)
and flags words where misaki's output disagrees with (a) the CMU Pronouncing
Dictionary and (b) the stem-suffix composition for regular -ed/-ing/-s/-ies
inflections. A HIGH-confidence suspect requires BOTH checks to agree with
each other and disagree with misaki — that pattern is the signature of a
misaki morphology bug (e.g. "bailed" → bˈIlAd, which should be bˈeɪld).

Outputs:
  suspects.csv              — every flagged word + misaki/CMU/suggested IPA,
                              confidence, reason
  suggested_overrides.json  — HIGH-confidence fixes in the autiobooks
                              phoneme_overrides config schema

Usage:
  # Audit, write CSV + JSON only
  python scripts/audit_pronunciations.py

  # Audit and merge HIGH overrides into your running config (with .bak backup)
  python scripts/audit_pronunciations.py --merge-config ~/.autiobooks/config.json

  # Preview the merge without writing
  python scripts/audit_pronunciations.py --merge-config ~/.autiobooks/config.json --dry-run

Merge is non-destructive: entries that already exist in the config's
phoneme_overrides list are skipped so any hand-tuned overrides survive
re-runs.

Requires: cmudict  (pip install cmudict)
"""
import argparse
import csv
import json
import re
import sys
import time
from importlib.resources import files
from pathlib import Path

try:
    import cmudict
except ImportError:
    sys.exit("Missing dependency: pip install cmudict")

from autiobooks.misaki import en


# High-frequency English words excluded from suggested overrides.
# Wrapping these as `[word](/IPA/)` rating-5 markup at every occurrence
# destabilizes Kokoro's acoustic model on long chapters — even when misaki's
# G2P is correct, dense repeated rating-5 IPA seems to bleed into the audio
# of nearby tokens. Misaki's gold lexicon already pronounces all of these
# adequately, so the override is redundant as well as risky.
HIGH_FREQUENCY_STOPLIST = frozenset({
    'a', 'an', 'the', 'and', 'or', 'but', 'so', 'if', 'as', 'at', 'by',
    'for', 'in', 'of', 'on', 'to', 'up', 'with', 'from', 'into', 'onto',
    'i', 'me', 'my', 'mine', 'we', 'us', 'our', 'ours',
    'you', 'your', 'yours',
    'he', 'him', 'his', 'she', 'her', 'hers', 'it', 'its',
    'they', 'them', 'their', 'theirs',
    'who', 'whom', 'whose', 'which', 'what', 'that', 'this', 'these', 'those',
    'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'having',
    'do', 'does', 'did', 'doing', 'done',
    'will', 'would', 'shall', 'should', 'can', 'could', 'may', 'might',
    'must', 'ought',
    'not', 'no', 'yes',
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
    'ten',
})


# ARPAbet → misaki-internal alphabet.
# Misaki collapses some diphthongs to single letters: A=eɪ, I=aɪ, O=oʊ, W=aʊ, Y=ɔɪ.
_ARPA_TO_MISAKI = {
    'AA': 'ɑ', 'AE': 'æ', 'AH': 'ʌ', 'AO': 'ɔ',
    'AW': 'W', 'AY': 'I', 'EH': 'ɛ', 'EY': 'A',
    'IH': 'ɪ', 'IY': 'i', 'OW': 'O', 'OY': 'Y',
    'UH': 'ʊ', 'UW': 'u',
    'B': 'b', 'CH': 'ʧ', 'D': 'd', 'DH': 'ð',
    'F': 'f', 'G': 'ɡ', 'HH': 'h', 'JH': 'ʤ',
    'K': 'k', 'L': 'l', 'M': 'm', 'N': 'n',
    'NG': 'ŋ', 'P': 'p', 'R': 'ɹ', 'S': 's',
    'SH': 'ʃ', 'T': 't', 'TH': 'θ', 'V': 'v',
    'W': 'w', 'Y': 'j', 'Z': 'z', 'ZH': 'ʒ',
}


def arpa_to_misaki(arpa_tokens):
    """Convert a CMU pronunciation (list of ARPAbet tokens) to misaki alphabet."""
    out = []
    for tok in arpa_tokens:
        stress = None
        base = tok
        if tok and tok[-1] in '012':
            base = tok[:-1]
            stress = tok[-1]
        if base == 'AH' and stress == '0':
            ph = 'ə'
        elif base == 'ER':
            ph = 'ɜɹ' if stress in ('1', '2') else 'ɚ'
        else:
            ph = _ARPA_TO_MISAKI.get(base, '')
        if stress == '1':
            out.append('ˈ' + ph)
        elif stress == '2':
            out.append('ˌ' + ph)
        else:
            out.append(ph)
    return ''.join(out)


# Misaki-alphabet → standard IPA (for human-readable override output).
# 'T' and 't' are the Kokoro-compat flap/glottal aliases misaki applies in
# its final post-processing pass (en.py:727): ɾ → 'T', ʔ → 't'. We invert
# that here so users see canonical IPA.
_MISAKI_TO_IPA_MAP = [
    ('A', 'eɪ'), ('I', 'aɪ'), ('O', 'oʊ'),
    ('W', 'aʊ'), ('Y', 'ɔɪ'), ('Q', 'əʊ'),  # Q = GB diphthong
    ('T', 'ɾ'),
    ('ᵊ', 'ə'), ('ᵻ', 'ɪ'),
]


def misaki_to_ipa(s):
    for k, v in _MISAKI_TO_IPA_MAP:
        s = s.replace(k, v)
    return s


def normalize_for_compare(s):
    """Loose-compare normalization. Strips stress and collapses near-equivalents
    so two pronunciations that only differ in surface form (flap t, glottal,
    schwa vs ᵊ, ɜɹ vs ɚ) compare equal.

    Note: misaki post-processes ɾ → 'T' and ʔ → 't' in non-v2.0 mode
    (en.py:727), so we also fold 'T' into 't' here."""
    s = re.sub(r'[ˈˌ]', '', s)
    s = s.replace('ɜɹ', 'R').replace('ɚ', 'R')   # any rhotic ER → R
    s = s.replace('T', 't')                      # misaki's Kokoro-compat flap
    s = s.replace('ɾ', 't')
    s = s.replace('ᵻ', 'ɪ')
    s = s.replace('ᵊ', 'ə')
    s = s.replace('ʔ', '')
    return s


def pick_best_cmu(cmu_prons, misaki_ps):
    """CMU often has multiple pronunciations (e.g., 'read' = [r-ɛ-d, r-i-d]).
    Return the one closest to misaki's output (if any match loosely), else the
    first. This lets homographs pass when ANY CMU variant agrees."""
    mis_norm = normalize_for_compare(misaki_ps)
    best = None
    for arpa in cmu_prons:
        cmu_ps = arpa_to_misaki(arpa)
        if normalize_for_compare(cmu_ps) == mis_norm:
            return cmu_ps, True  # exact loose-match
        best = cmu_ps if best is None else best
    return best, False


# Regular suffix patterns. Key = regex matching inflected form, value = (stem_fn, suffix_ipa).
# The stem_fn turns the matched word into its base form.
_SUFFIX_RULES = [
    # -ied → -y (tried, cried)
    (re.compile(r'^(.{2,})ied$'), lambda m: m.group(1) + 'y', 'd'),
    # -ies → -y (tries, cries)
    (re.compile(r'^(.{2,})ies$'), lambda m: m.group(1) + 'y', 'z'),
    # doubled-consonant -ed (bagged, stopped, grabbed)
    (re.compile(r'^(.{2,})([bdgklmnprstvz])\2ed$'), lambda m: m.group(1) + m.group(2), None),
    # doubled-consonant -ing (running, stopping)
    (re.compile(r'^(.{2,})([bdgklmnprstvz])\2ing$'), lambda m: m.group(1) + m.group(2), 'ɪŋ'),
    # plain -ed (bailed, walked, pointed)
    (re.compile(r'^(.{3,})ed$'), lambda m: m.group(1), None),
    # plain -ing (bailing, walking, pointing)
    (re.compile(r'^(.{3,})ing$'), lambda m: m.group(1), 'ɪŋ'),
    # -es after s/x/z/sh/ch (buses, boxes, churches)
    (re.compile(r'^(.{2,}(?:s|x|z|sh|ch))es$'), lambda m: m.group(1), 'ᵻz'),
    # plain -s (bails, cats)
    (re.compile(r'^(.{2,})s$'), lambda m: m.group(1), None),
]


def _ed_suffix_for(stem_ps):
    """Mirror misaki's _ed: choose d/t/ɪd based on stem-final phoneme."""
    if not stem_ps:
        return None
    last = stem_ps[-1]
    if last in 'pkfθʃsʧ':
        return 't'
    if last == 'd' or last == 't':
        return 'ᵻd'
    return 'd'


def _s_suffix_for(stem_ps):
    """Mirror misaki's _s: choose z/s/ɪz."""
    if not stem_ps:
        return None
    last = stem_ps[-1]
    if last in 'pkfθ':
        return 's'
    if last in 'szʃʒʧʤ':
        return 'ᵻz'
    return 'z'


def compose_stem_suffix(word, lexicon):
    """If word is a regular inflection of a known stem, return the expected
    phonemes (stem + suffix). Otherwise None."""
    # Proper nouns (capitalized) rarely behave as regular inflections —
    # "Ares" isn't a plural of "are", "Ceres" isn't a plural of "cere".
    # Restrict this check to lowercase (common) words.
    if word != word.lower():
        return None
    for pattern, stem_fn, suffix in _SUFFIX_RULES:
        m = pattern.match(word)
        if not m:
            continue
        stem = stem_fn(m)
        if not lexicon.is_known(stem, None):
            continue
        stem_ps, _rating = lexicon.lookup(stem, None, None, None)
        if not stem_ps:
            continue
        # Select suffix phoneme
        if suffix is None:  # signals "needs _ed or _s logic"
            if word.endswith('ed'):
                suffix_ps = _ed_suffix_for(stem_ps)
            elif word.endswith('s'):
                suffix_ps = _s_suffix_for(stem_ps)
            else:
                continue
        else:
            suffix_ps = suffix
        if suffix_ps is None:
            continue
        return stem_ps + suffix_ps
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None,
                    help='Only check first N lexicon entries (debug).')
    ap.add_argument('--out', type=Path, default=Path('audit_output'),
                    help='Output directory (default: ./audit_output).')
    ap.add_argument('--min-confidence', choices=['LOW', 'MEDIUM', 'HIGH'],
                    default='HIGH',
                    help='Minimum confidence to include in suggested_overrides.json.')
    ap.add_argument('--merge-config', type=Path, metavar='PATH',
                    help='After the audit, merge the emitted overrides into '
                         'the given autiobooks config JSON (e.g. '
                         '~/.autiobooks/config.json). Creates a .bak backup '
                         'first. Words with an existing override entry are '
                         'left untouched.')
    ap.add_argument('--dry-run', action='store_true',
                    help='With --merge-config, report what would change '
                         'without writing.')
    args = ap.parse_args()

    args.out.mkdir(exist_ok=True)

    # Load misaki
    print('Loading misaki G2P...', file=sys.stderr)
    g2p = en.G2P(trf=False, british=False, fallback=None)
    lex = g2p.lexicon

    gold = json.loads(files('autiobooks.misaki.data').joinpath('us_gold.json').read_text())
    silver = json.loads(files('autiobooks.misaki.data').joinpath('us_silver.json').read_text())

    # Load CMU early so we can add its entries to the audit set. Many
    # inflected forms (bailed, jumped, tried, …) aren't stored directly in
    # misaki's lexicon — they're constructed on-the-fly by its morphology —
    # so we need to include CMU's vocabulary to audit them.
    print('Loading CMU dict...', file=sys.stderr)
    cmu = cmudict.dict()

    all_words = set()
    all_words.update(gold.keys())
    all_words.update(silver.keys())
    all_words.update(cmu.keys())

    # Cross-case collision filter: if a lowercase word has a capitalized
    # proper-noun variant in gold (e.g. "Ares" the Greek god), misaki's
    # case-insensitive lookup returns the proper-noun reading even for the
    # lowercase input. CMU independently lists the lowercase word with a
    # *different* meaning ("ares" = plural of "are"), so both our checks
    # agree — but the resulting override would corrupt the proper noun.
    # Drop any lowercase word whose capitalized form is a distinct gold entry.
    capitalized_gold = {
        k for k in gold
        if k != k.lower() and k.lower() not in gold
    }
    proper_noun_homographs = {k.lower() for k in capitalized_gold}

    # Filter: only simple alpha words (skip contractions, numerics, etc.)
    words = sorted(
        w for w in all_words
        if w.isalpha() and len(w) >= 2 and w not in proper_noun_homographs
    )
    if args.limit:
        words = words[:args.limit]
    print(f'Auditing {len(words):,} words '
          f'(skipped {len(proper_noun_homographs):,} proper-noun homographs).',
          file=sys.stderr)

    suspects = []
    checked = 0
    t0 = time.time()
    for word in words:
        checked += 1
        if checked % 5000 == 0:
            rate = checked / (time.time() - t0)
            print(f'  {checked:,}/{len(words):,}  ({rate:.0f}/s)  suspects={len(suspects)}',
                  file=sys.stderr)

        # Ask misaki
        try:
            ps, toks = g2p(word)
        except Exception:
            continue
        if not ps or not toks:
            continue
        misaki_ps = ps
        misaki_rating = getattr(toks[0], 'rating', None)

        # --- Check 1: CMU cross-check ---
        cmu_prons = cmu.get(word)
        cmu_ps = None
        cmu_match = False
        if cmu_prons:
            cmu_ps, cmu_match = pick_best_cmu(cmu_prons, misaki_ps)

        # --- Check 2: Stem-suffix consistency ---
        expected_ps = compose_stem_suffix(word, lex)

        mis_norm = normalize_for_compare(misaki_ps)
        cmu_norm = normalize_for_compare(cmu_ps) if cmu_ps else None
        exp_norm = normalize_for_compare(expected_ps) if expected_ps else None

        # Decide confidence & reason.
        # HIGH: CMU and stem-suffix agree with each other and disagree with misaki.
        # MEDIUM: only one check fires and disagrees with misaki.
        reason = None
        suggested = None
        confidence = None
        if cmu_norm and exp_norm:
            if cmu_norm == exp_norm and cmu_norm != mis_norm:
                reason, suggested, confidence = 'cmu+stem-agree', expected_ps, 'HIGH'
            elif cmu_norm != mis_norm and exp_norm == mis_norm:
                reason, suggested, confidence = 'cmu-only', cmu_ps, 'MEDIUM'
            elif exp_norm != mis_norm and cmu_norm == mis_norm:
                # Misaki matches CMU — dialect-safe. Stem-suffix likely wrong
                # (e.g. irregular form). Skip.
                continue
            elif cmu_norm != mis_norm and exp_norm != mis_norm:
                # All three differ. Low signal. Skip.
                continue
            else:
                continue
        elif cmu_norm and cmu_norm != mis_norm:
            reason, suggested, confidence = 'cmu-only', cmu_ps, 'MEDIUM'
        elif exp_norm and exp_norm != mis_norm:
            reason, suggested, confidence = 'stem-suffix-only', expected_ps, 'MEDIUM'
        else:
            continue

        suspects.append({
            'word': word,
            'misaki': misaki_ps,
            'misaki_ipa': misaki_to_ipa(misaki_ps),
            'rating': misaki_rating,
            'cmu': cmu_ps or '',
            'cmu_ipa': misaki_to_ipa(cmu_ps) if cmu_ps else '',
            'expected_stem_suffix': expected_ps or '',
            'suggested': suggested,
            'suggested_ipa': misaki_to_ipa(suggested),
            'reason': reason,
            'confidence': confidence,
        })

    # Write CSV
    csv_path = args.out / 'suspects.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        cols = ['word', 'confidence', 'reason', 'rating',
                'misaki_ipa', 'cmu_ipa', 'suggested_ipa',
                'misaki', 'cmu', 'expected_stem_suffix', 'suggested']
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in suspects:
            w.writerow({k: s[k] for k in cols})

    # Write JSON overrides (only for entries meeting min-confidence).
    # High-frequency function words are excluded even when CMU disagrees with
    # misaki: wrapping them as `[word](/IPA/)` rating-5 markup hundreds of
    # times per chapter destabilizes Kokoro's acoustic model and bleeds the
    # override IPA onto neighboring pronouns in the audio (observed: a `was`
    # override caused "his" to be spoken as "was" in unrelated paragraphs),
    # even though misaki itself phonemizes the surrounding words correctly.
    # Misaki's gold lexicon already covers all of these.
    rank = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    threshold = rank[args.min_confidence]
    overrides = [
        {
            'word': s['word'],
            'ipa': s['suggested_ipa'],
            'case_sensitive': False,
            'enabled': True,
        }
        for s in suspects
        if rank[s['confidence']] >= threshold
        and s['word'].lower() not in HIGH_FREQUENCY_STOPLIST
    ]
    excluded_count = sum(
        1 for s in suspects
        if rank[s['confidence']] >= threshold
        and s['word'].lower() in HIGH_FREQUENCY_STOPLIST
    )
    json_path = args.out / 'suggested_overrides.json'
    json_path.write_text(json.dumps(overrides, indent=2, ensure_ascii=False))

    elapsed = time.time() - t0
    print(f'\nDone in {elapsed:.1f}s.', file=sys.stderr)
    print(f'  Checked:    {checked:,}', file=sys.stderr)
    print(f'  Suspects:   {len(suspects):,}', file=sys.stderr)
    by_conf = {}
    for s in suspects:
        by_conf[s['confidence']] = by_conf.get(s['confidence'], 0) + 1
    for c in ['HIGH', 'MEDIUM', 'LOW']:
        if c in by_conf:
            print(f'    {c}: {by_conf[c]:,}', file=sys.stderr)
    print(f'  Overrides written: {len(overrides):,} (confidence >= {args.min_confidence})',
          file=sys.stderr)
    if excluded_count:
        print(f'  Excluded by high-frequency stoplist: {excluded_count:,}',
              file=sys.stderr)
    print(f'\n  {csv_path}', file=sys.stderr)
    print(f'  {json_path}', file=sys.stderr)

    if args.merge_config:
        merge_into_config(args.merge_config, overrides, dry_run=args.dry_run)


def merge_into_config(config_path, overrides, dry_run=False):
    """Merge `overrides` into the phoneme_overrides list of an autiobooks
    config JSON. Existing entries (matched by lowercase word) are left alone
    so the user's own tuning is never clobbered. A .bak snapshot is written
    before the config is replaced."""
    config_path = config_path.expanduser()

    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding='utf-8'))
    else:
        cfg = {}

    existing = cfg.get('phoneme_overrides', []) or []
    existing_words = {e.get('word', '').lower() for e in existing}

    to_add = [e for e in overrides if e['word'].lower() not in existing_words]
    skipped = len(overrides) - len(to_add)

    print(f'\nMerge target: {config_path}', file=sys.stderr)
    print(f'  Existing overrides: {len(existing):,}', file=sys.stderr)
    print(f'  New to add:         {len(to_add):,}', file=sys.stderr)
    print(f'  Skipped (already present): {skipped:,}', file=sys.stderr)

    if dry_run:
        print('  [dry-run] no changes written.', file=sys.stderr)
        return
    if not to_add:
        print('  Nothing to merge.', file=sys.stderr)
        return

    cfg['phoneme_overrides'] = existing + to_add

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        backup = config_path.with_suffix(config_path.suffix + '.bak')
        backup.write_text(config_path.read_text(encoding='utf-8'),
                          encoding='utf-8')
        print(f'  Backup: {backup}', file=sys.stderr)

    tmp = config_path.with_suffix(config_path.suffix + '.tmp')
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                   encoding='utf-8')
    tmp.replace(config_path)
    print(f'  Merged {len(to_add):,} new overrides into {config_path}',
          file=sys.stderr)


if __name__ == '__main__':
    main()
