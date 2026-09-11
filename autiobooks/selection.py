"""Chapter content hashing and duplicate detection shared by GUI and CLI.

A single policy so the same book auto-selects identically everywhere
(the GUI and CLI previously kept separate copies that disagreed on
stripping and empty-chapter handling): content is stripped before
hashing, so formatting-only variants match, and empty chapters are never
marked as duplicates — they are excluded from selection separately.
"""

import hashlib


def content_hash(text):
    """Stable digest for duplicate detection (NOT Python's hash(), which is
    randomized per interpreter run via PYTHONHASHSEED)."""
    return hashlib.md5(text.strip().encode('utf-8', errors='replace')).digest()


def find_duplicates(chapters):
    """Map 0-based chapter index -> 0-based index of the first chapter with
    identical (stripped) content."""
    seen = {}
    duplicates = {}
    for i, ch in enumerate(chapters):
        text = ch.extracted_text.strip()
        if not text:
            continue
        h = content_hash(text)
        if h in seen:
            duplicates[i] = seen[h]
        else:
            seen[h] = i
    return duplicates


def auto_select_indices(chapters):
    """0-based indices of non-empty, non-duplicate chapters."""
    duplicates = find_duplicates(chapters)
    return [
        i for i, ch in enumerate(chapters)
        if ch.extracted_text.strip() and i not in duplicates
    ]
