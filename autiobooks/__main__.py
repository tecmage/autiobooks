import sys
import os
from pathlib import Path

if getattr(sys, 'frozen', False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))

# CLI subcommands trigger headless mode; no args or unknown args launch the GUI.
_CLI_COMMANDS = {'convert', 'list-chapters', 'list-voices'}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in _CLI_COMMANDS:
        from autiobooks.cli import main as cli_main
        cli_main()
    else:
        from autiobooks.autiobooks import main
        main()
