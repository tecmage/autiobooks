import json
import os
import sys
import tempfile
import warnings
from pathlib import Path


CONFIG_DIR = Path.home() / '.autiobooks'
CONFIG_FILE = CONFIG_DIR / 'config.json'
BIN_DIR = CONFIG_DIR / 'bin'
CUDA_DIR = CONFIG_DIR / 'cuda'


def load_config():
    """Load saved settings. Returns empty dict on any error.

    ValueError covers both JSONDecodeError and UnicodeDecodeError — a
    corrupt or binary config.json must degrade to defaults, not crash the
    GUI at startup. Non-dict JSON (hand-edited) degrades the same way.
    """
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def sanitize_dict_list(value, str_keys=()):
    """Coerce a config field expected to be a list of dicts.

    `word_substitutions` and `phoneme_overrides` are consumed by iteration
    + `.get()` deep inside a conversion; a hand-edited config holding a
    string or a list of strings would otherwise crash mid-run with a
    cryptic AttributeError. Invalid entries are dropped, anything that
    isn't a list yields [].

    `str_keys` names fields that must be strings when present — e.g.
    `('find', 'replace')` for word_substitutions, `('word', 'ipa')` for
    phoneme_overrides. A hand-edited config with a numeric `find`/`word`
    passes the dict-shape check but crashes deep inside normalize_text
    (`.strip()`/`.lower()` on an int); dropping the row here keeps the
    "invalid entries are dropped" contract instead of failing mid-chapter.
    Default `()` preserves the old shape-only behavior for callers that
    don't pass it (and stays warning-free — see below).

    A dropped row is a silently-lost user rule that then gets permanently
    erased on the next config save (get_current_config() writes the
    sanitized in-memory list straight back to disk). When `str_keys` is
    non-empty and at least one row is dropped, warn (RuntimeWarning,
    matching the precedent in text_processing._load_acronym_skip_sets) so
    the loss is visible instead of silent. The default str_keys=() path
    never warns, since callers that don't opt into field-type checking
    are relying on the old shape-only behavior.
    """
    if not isinstance(value, list):
        return []
    result = [entry for entry in value if isinstance(entry, dict)
              and all(isinstance(entry.get(k, ''), str) for k in str_keys)]
    if str_keys and len(result) < len(value):
        warnings.warn(
            f"Dropped {len(value) - len(result)} invalid row(s) from a "
            f"config list with field set {str_keys!r} (non-dict entry or "
            f"a field that wasn't a string); the corresponding user "
            f"rule(s) were discarded.",
            RuntimeWarning)
    return result


def save_config(config):
    """Save settings to config file atomically.

    Writes to a per-process-unique temp file in the same directory then
    renames via os.replace, so a crash or power-loss mid-write can never
    truncate config.json and silently wipe the user's settings. The temp
    name is minted by mkstemp (not a fixed `config.json.tmp`) so two GUI
    instances saving concurrently can't interleave writes into the same
    file — each gets its own temp file and only the final os.replace can
    collide, which degrades to ordinary last-writer-wins on a valid file
    instead of a torn/corrupt one.

    `json.dumps` runs BEFORE mkstemp creates anything: a non-serializable
    value (e.g. a stray object slipping into the config dict) must raise
    without ever touching the filesystem, otherwise every failed save
    leaks a uniquely-named orphan temp file into ~/.autiobooks forever.
    Cleanup of a temp file that DID get created is a bare `except`, not
    `except OSError` — os.fdopen/f.write can raise non-OSError errors
    (e.g. a UnicodeEncodeError from a pathological string) after the file
    exists, and that path must still unlink it.
    """
    tmp_file = None
    try:
        payload = json.dumps(config, indent=2)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=CONFIG_DIR, prefix='config.', suffix='.tmp')
        tmp_file = Path(tmp_name)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(payload)
        os.replace(tmp_file, CONFIG_FILE)
    except Exception as e:
        if tmp_file is not None:
            tmp_file.unlink(missing_ok=True)
        print(f'Warning: failed to save settings to {CONFIG_FILE}: {e}',
              file=sys.stderr)
