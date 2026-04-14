import json
import os
import sys
from pathlib import Path


CONFIG_DIR = Path.home() / '.autiobooks'
CONFIG_FILE = CONFIG_DIR / 'config.json'
BIN_DIR = CONFIG_DIR / 'bin'
CUDA_DIR = CONFIG_DIR / 'cuda'


def load_config():
    """Load saved settings. Returns empty dict on any error."""
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(config):
    """Save settings to config file atomically.

    Writes to a temp file in the same directory then renames via os.replace,
    so a crash or power-loss mid-write can never truncate config.json and
    silently wipe the user's settings.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp_file = CONFIG_FILE.with_name(CONFIG_FILE.name + '.tmp')
        tmp_file.write_text(json.dumps(config, indent=2), encoding='utf-8')
        os.replace(tmp_file, CONFIG_FILE)
    except OSError as e:
        print(f'Warning: failed to save settings to {CONFIG_FILE}: {e}',
              file=sys.stderr)
