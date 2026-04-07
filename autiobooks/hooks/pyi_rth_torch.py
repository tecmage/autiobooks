import sys
import os
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

os.environ['LOGURU_AUTOEXIT'] = 'false'
os.environ['LOGURU_NO_COLOR'] = '1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'

if getattr(sys, 'frozen', False):
    bundle_dir = Path(sys._MEIPASS)
    torch_lib = bundle_dir / 'torch' / 'lib'
    if torch_lib.exists():
        try:
            os.add_dll_directory(str(torch_lib))
        except (OSError, AttributeError):
            pass

    bundled_misaki = bundle_dir / 'autiobooks' / 'misaki'
    if bundled_misaki.exists():
        sys.path.insert(0, str(bundled_misaki.parent))

    cuda_dir = Path.home() / '.autiobooks' / 'cuda'
    if cuda_dir.exists():
        cuda_lib = cuda_dir / 'bin'
        if cuda_lib.exists():
            try:
                os.add_dll_directory(str(cuda_lib))
            except (OSError, AttributeError):
                pass
            if str(cuda_lib) not in os.environ.get('PATH', ''):
                os.environ['PATH'] = str(cuda_lib) + os.pathsep + os.environ.get('PATH', '')

    import importlib
    import importlib.util
    import importlib.resources

    _original_open_text = importlib.resources.open_text

    def patched_open_text(package, resource, *args, **kwargs):
        try:
            if isinstance(package, str):
                pkg = importlib.import_module(package)
            else:
                pkg = package

            locations = []
            if hasattr(pkg, '__path__') and pkg.__path__:
                for p in pkg.__path__:
                    locations.append(Path(p))
            if hasattr(pkg, '__spec__') and pkg.__spec__ and hasattr(pkg.__spec__, 'submodule_search_locations') and pkg.__spec__.submodule_search_locations:
                for loc in pkg.__spec__.submodule_search_locations:
                    loc_p = Path(loc)
                    if loc_p not in locations:
                        locations.append(loc_p)

            for loc in locations:
                candidate = loc / resource
                if candidate.exists():
                    return open(candidate, 'r', encoding='utf-8')

            root_path = bundle_dir / pkg.__name__.replace('.', '/')
            if root_path.exists():
                candidate = root_path / resource
                if candidate.exists():
                    return open(candidate, 'r', encoding='utf-8')

        except Exception:
            pass

        return _original_open_text(package, resource, *args, **kwargs)

    importlib.resources.open_text = patched_open_text
