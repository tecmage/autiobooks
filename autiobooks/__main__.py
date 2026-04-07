import sys
import os
from pathlib import Path

if getattr(sys, 'frozen', False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))

from autiobooks.autiobooks import main

if __name__ == "__main__":
    main()
