"""Ad-hoc real-world text-processing audit against Royal Road chapters.

Fetches a fiction's chapter list, picks N random chapters, runs each through
the real extract_text_from_html -> normalize_text pipeline, and flags
suspicious transformations (roman conversions, abbreviation resolutions,
number ranges, degree insertions, mashed/long output tokens, markdown
emission, big length drops, stray non-ASCII). Prints a compact report of
ONLY the flagged items so a human can spot genuine mis-processing.

Usage:
    python scripts/rr_audit.py <fiction_page_url> [n_chapters] [seed]
"""
import os
import re
import sys
import json
import random
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup
from autiobooks.epub_parser import extract_text_from_html
from autiobooks.text_processing import normalize_text

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch(url):
    r = subprocess.run(["curl", "-sL", "-A", UA, url],
                       capture_output=True, text=True, encoding="utf-8")
    return r.stdout


def chapter_urls(fiction_url):
    html = fetch(fiction_url)
    m = re.search(r"/fiction/(\d+)/", fiction_url)
    fid = m.group(1)
    urls = re.findall(r'/fiction/%s/[a-z0-9-]+/chapter/\d+/[a-z0-9-]+' % fid, html)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append("https://www.royalroad.com" + u)
    return out


def chapter_text(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", class_="chapter-inner")
    if div is None:
        return None
    return extract_text_from_html(str(div))


# --- flaggers -------------------------------------------------------------

ROMAN = re.compile(r'(?i)\b(chapter|part|book|volume|vol|act|scene|section|appendix)\s+([IVXLCDM]+)\b')
EXPANSIONS = ('Saint', 'Street', 'Number', 'Edition', 'Mister', 'Missus',
              'Doctor', 'Mount', 'et cetera', 'and others', 'versus')


def context(text, idx, span=38):
    a = max(0, idx - span)
    b = min(len(text), idx + span)
    return text[a:b].replace("\n", " ")


def flag_chapter(raw, norm):
    issues = []

    # 1. number ranges: "\d to \d" that wasn't "to" before
    for m in re.finditer(r'\d+\s+to\s+\d+', norm):
        s = m.group(0)
        # reconstruct likely source "a-b"
        nums = re.findall(r'\d+', s)
        src = nums[0] + '-' + nums[1]
        if src in raw:
            issues.append(("range", f'{src!r} -> {s!r}  | …{context(norm, m.start())}…'))

    # 2. roman conversions (compare raw vs norm at keyword sites)
    raw_romans = {(m.group(1).lower(), m.group(2).upper()) for m in ROMAN.finditer(raw)}
    for kw, rn in sorted(raw_romans):
        # did it convert? find "kw <number>" in norm
        mm = re.search(r'(?i)\b' + kw + r'\s+(\d+)\b', norm)
        if mm:
            issues.append(("roman", f'{kw} {rn} -> {kw} {mm.group(1)}'))

    # 3. abbreviation context-resolutions actually present in output
    for word in EXPANSIONS:
        for m in re.finditer(r'\b' + re.escape(word) + r'\b', norm):
            issues.append(("expand", f'{word}: …{context(norm, m.start())}…'))

    # 4. degrees
    for m in re.finditer(r'degrees', norm):
        issues.append(("degrees", f'…{context(norm, m.start())}…'))

    # 5. mashed/long output tokens (>=17 letters) that processing CREATED
    #    (skip author-original stylizations already present in the raw text)
    for m in re.finditer(r'[A-Za-z]{17,}', norm):
        if m.group(0) not in raw:
            issues.append(("longtok", m.group(0)))

    # 6. digit-letter glue like "degreesF" that processing CREATED
    for m in re.finditer(r'\b\d+[A-Za-z]{2,}\b', norm):
        if m.group(0) not in raw:
            issues.append(("numglue", f'{m.group(0)}  | …{context(norm, m.start())}…'))

    # 7. markdown emission (proper-noun / builtin overrides) — expected but show
    for m in re.finditer(r'\[[^\]]+\]\(/[^)]+/\)', norm):
        issues.append(("markdown", m.group(0)))

    # 8. stray non-ASCII that isn't normal smart punctuation/letters
    weird = set(re.findall(r'[^\x00-\x7f]', norm))
    # allow accented letters handled elsewhere; flag symbols/box-drawing
    weird = {c for c in weird if not c.isalpha()}
    if weird:
        issues.append(("nonascii", " ".join(sorted(weird))))

    # 8b. context-sensitive abbreviation resolutions — show how each raw
    #     St./No./Ed. resolved, so wrong Saint/Street/Number can be eyeballed
    for m in re.finditer(r'\b(St|No|Ed)\.(?=\s|$)', raw):
        issues.append(("ctxabbr", f'{m.group(0)!r}: …{context(raw, m.start(), 30)}…'))

    # 9. big length drop (lost content)
    if raw and len(norm) < 0.80 * len(raw):
        issues.append(("shrink", f'{len(raw)} -> {len(norm)} chars ({len(norm)/len(raw):.0%})'))

    return issues


def main():
    fiction_url = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42
    urls = chapter_urls(fiction_url)
    if not urls:
        print("NO CHAPTERS FOUND"); return
    random.seed(seed)
    pick = sorted(random.sample(range(len(urls)), min(n, len(urls))))
    print(f"# {fiction_url}")
    print(f"# {len(urls)} chapters total; sampling indices {pick}\n")
    for i in pick:
        url = urls[i]
        raw = chapter_text(url)
        slug = url.rsplit("/", 1)[-1]
        if not raw:
            print(f"[idx {i}] {slug}: EXTRACT FAILED"); continue
        norm = normalize_text(raw, lang="en-us")
        issues = flag_chapter(raw, norm)
        print(f"[idx {i}] {slug}  ({len(raw)} chars, {len(issues)} flags)")
        # collapse duplicate flag lines
        seen = {}
        for kind, msg in issues:
            seen.setdefault((kind, msg), 0)
            seen[(kind, msg)] += 1
        for (kind, msg), c in seen.items():
            suffix = f' x{c}' if c > 1 else ''
            print(f"    [{kind}] {msg}{suffix}")
        print()


if __name__ == "__main__":
    main()
