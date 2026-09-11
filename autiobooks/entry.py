"""Console-script entry point.

Dispatches CLI subcommands BEFORE importing the GUI module —
``autiobooks.autiobooks`` imports tkinter, PIL and pygame at module top,
none of which should be required for a headless `autiobooks convert` /
`list-chapters` / `list-voices` run.
"""

import sys
from pathlib import Path

# CLI subcommands trigger headless mode; no args or unknown args launch the GUI.
CLI_COMMANDS = frozenset({'convert', 'list-chapters', 'list-voices'})


def _help_text():
    """Long-form --help text: an on-disk README.md, falling back to the
    installed distribution's METADATA payload.

    The two sources cover disjoint layouts, and the probe is ordered first
    because it is the one that can be STALE-free:

      * source checkout — README.md sits at Path(__file__).parent.parent, and
        it is the live file the developer is editing. METADATA here is baked
        at `pip install -e .` time and can be many releases behind (it read
        as 2.4.0 while the tree was 2.6.0), so metadata-first served ancient
        help text from a checkout (AUDIT_2026-07-16.md §5.8 fixed the two
        cases below but regressed this one).
      * wheel / `pip install .` — site-packages has no README.md beside the
        package, so the probe misses and METADATA (embedded from pyproject's
        `readme = "README.md"` at build time) answers, and is current by
        construction.
      * PyInstaller build — both spec files ship README.md to the bundle root
        (= Path(__file__).parent.parent), so the probe hits; a frozen app has
        no dist-info for metadata() to find anyway.

    Without either, `--help` printed a 3-line stub naming no flag or option.
    """
    readme = Path(__file__).parent.parent / 'README.md'
    if readme.exists():
        try:
            text = readme.read_text(encoding='utf-8')
            if text.strip():
                return text
        except OSError:
            pass
    import importlib.metadata as importlib_metadata
    try:
        payload = importlib_metadata.metadata('autiobooks').get_payload()
        if payload and payload.strip():
            return payload
    except importlib_metadata.PackageNotFoundError:
        pass
    return None


def main():
    if len(sys.argv) > 1 and sys.argv[1] in CLI_COMMANDS:
        from autiobooks.cli import main as cli_main
        cli_main()
    elif len(sys.argv) == 2 and sys.argv[1] in ('--help', '-h'):
        text = _help_text()
        if text:
            # Windows consoles default to cp1252 — README glyphs like '→'
            # made `autiobooks --help` die with UnicodeEncodeError.
            enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
            print(text.encode(enc, errors='replace').decode(enc))
        else:
            print("Autiobooks: convert epub files to m4b audiobooks.\n"
                  "Run without arguments to launch the GUI.\n"
                  "CLI commands: convert, list-chapters, list-voices")
    else:
        from autiobooks.autiobooks import start_gui
        start_gui()
