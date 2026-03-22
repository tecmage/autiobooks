import json
from pathlib import Path


CONFIG_DIR = Path.home() / '.autiobooks'
CONFIG_FILE = CONFIG_DIR / 'config.json'


def load_config():
    """Load saved settings. Returns empty dict on any error."""
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(config):
    """Save settings to config file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config, indent=2),
                               encoding='utf-8')
    except OSError:
        pass
