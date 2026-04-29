import os
import sys
import shutil
import zipfile
import tempfile
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# Mirror of engine._SUBPROCESS_FLAGS — duplicated here to avoid a circular
# import (engine imports runtime internals at function scope).
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS = {'creationflags': subprocess.CREATE_NO_WINDOW}
else:
    _SUBPROCESS_FLAGS = {}

CONFIG_DIR = Path.home() / '.autiobooks'
BIN_DIR = CONFIG_DIR / 'bin'
CUDA_DIR = CONFIG_DIR / 'cuda'

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
ESPEAK_URL = "https://github.com/espeak-ng/espeak-ng/releases/download/1.51/espeak-ng-x64.zip"

BIN_DIR.mkdir(parents=True, exist_ok=True)


def _add_torch_lib_to_path():
    """Add torch's lib directory to PATH if running in PyInstaller bundle."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        torch_lib = Path(sys._MEIPASS) / 'torch' / 'lib'
        if torch_lib.exists():
            torch_lib_str = str(torch_lib)
            if torch_lib_str not in os.environ.get('PATH', ''):
                os.environ['PATH'] = torch_lib_str + os.pathsep + os.environ.get('PATH', '')


_add_torch_lib_to_path()


def ensure_bin_in_path():
    """Add BIN_DIR to PATH if not already present."""
    bin_str = str(BIN_DIR)
    if bin_str not in os.environ.get('PATH', ''):
        os.environ['PATH'] = bin_str + os.pathsep + os.environ.get('PATH', '')


def which_exe(name):
    """Check if executable exists in PATH or BIN_DIR."""
    if shutil.which(name):
        return True
    if (BIN_DIR / name).exists():
        return True
    return False


def _download_file(url, dest, progress_callback=None):
    """Download a file with optional progress callback."""
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=60) as response:
        total_size = int(response.headers.get('Content-Length', 0))
        downloaded = 0
        block_size = 8192
        with open(dest, 'wb') as f:
            while True:
                buffer = response.read(block_size)
                if not buffer:
                    break
                downloaded += len(buffer)
                f.write(buffer)
                if progress_callback and total_size:
                    progress_callback(downloaded, total_size)


def _extract_zip(zip_path, extract_to):
    """Extract a zip file, finding the single root directory if present."""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        root_prefix = None
        for name in names:
            if name.count('/') == 0 and not name.endswith('/'):
                continue
            parts = name.split('/')
            if len(parts) > 1 and root_prefix is None:
                root_prefix = parts[0] + '/'
            elif parts[0] + '/' != root_prefix:
                root_prefix = None
                break

        extract_root = extract_to
        if root_prefix:
            extract_root = extract_to / root_prefix.rstrip('/')
            extract_to.mkdir(parents=True, exist_ok=True)

        for name in names:
            if root_prefix:
                if not name.startswith(root_prefix):
                    continue
                target_name = name[len(root_prefix):]
            else:
                target_name = name

            if not target_name:
                continue

            target_path = extract_to / target_name
            if name.endswith('/'):
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target_path, 'wb') as dst:
                    dst.write(src.read())


def _find_exe_in_dir(base_dir, exe_name):
    """Find an executable recursively in a directory."""
    for root, dirs, files in os.walk(base_dir):
        if exe_name in files:
            return Path(root) / exe_name
    return None


def ensure_ffmpeg(root=None, progress_callback=None):
    """Ensure ffmpeg is available. Downloads if needed.
    
    Args:
        root: tkinter root window for progress display (optional)
        progress_callback: callback(downloaded, total) for progress updates
        
    Returns:
        True if ffmpeg is available, False if user cancelled/missing
    """
    ensure_bin_in_path()

    if which_exe('ffmpeg'):
        return True

    import tkinter as tk
    from tkinter import ttk, messagebox

    if root is None:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

    def show_download_dialog():
        dialog = tk.Toplevel(root)
        dialog.title("Downloading FFmpeg")
        dialog.geometry("400x120")
        dialog.resizable(False, False)
        dialog.transient(root)
        dialog.grab_set()

        label = tk.Label(dialog, text="FFmpeg not found. Downloading (~150MB)...")
        label.pack(pady=10)

        progress = ttk.Progressbar(dialog, mode='determinate', maximum=100)
        progress.pack(fill='x', padx=20, pady=5)

        status_label = tk.Label(dialog, text="Starting download...")
        status_label.pack(pady=5)

        dialog.update_idletasks()

        result_holder = [None]
        error_holder = [None]

        def download_thread():
            try:
                tmp_dir = Path(tempfile.gettempdir()) / 'autiobooks_download'
                tmp_dir.mkdir(exist_ok=True)
                zip_path = tmp_dir / 'ffmpeg.zip'

                _download_file(FFMPEG_URL, zip_path)

                dialog.attributes('-topmost', False)

                _extract_zip(zip_path, BIN_DIR)

                exe_path = _find_exe_in_dir(BIN_DIR, 'ffmpeg.exe')
                if exe_path and exe_path.parent != BIN_DIR:
                    for f in exe_path.parent.iterdir():
                        if f.is_file():
                            shutil.copy2(f, BIN_DIR / f.name)
                    shutil.rmtree(exe_path.parent)

                try:
                    os.unlink(zip_path)
                except OSError:
                    pass

                ensure_bin_in_path()
                result_holder[0] = True
            except Exception as e:
                error_holder[0] = str(e)
                result_holder[0] = False

        def update_progress():
            if result_holder[0] is None:
                dialog.after(200, update_progress)
            elif result_holder[0] == True:
                dialog.destroy()
                if root:
                    root.update_idletasks()
            else:
                dialog.destroy()
                if error_holder[0]:
                    messagebox.showerror("Download Error", error_holder[0])

        import threading as _threading
        t = _threading.Thread(target=download_thread)
        t.start()

        update_progress()
        dialog.wait_window()

        return result_holder[0]

    return show_download_dialog()


def ensure_espeakng():
    """Ensure espeak-ng is available. Downloads if needed.
    
    Returns:
        True if espeak-ng is available, False otherwise
    """
    ensure_bin_in_path()

    if which_exe('espeak-ng'):
        return True

    try:
        tmp_dir = Path(tempfile.gettempdir()) / 'autiobooks_download'
        tmp_dir.mkdir(exist_ok=True)
        zip_path = tmp_dir / 'espeak-ng.zip'

        _download_file(ESPEAK_URL, zip_path)

        _extract_zip(zip_path, BIN_DIR)

        exe_path = _find_exe_in_dir(BIN_DIR, 'espeak-ng.exe')
        if exe_path and exe_path.parent != BIN_DIR:
            for f in exe_path.parent.iterdir():
                if f.is_file():
                    shutil.copy2(f, BIN_DIR / f.name)
            shutil.rmtree(exe_path.parent)

        try:
            os.unlink(zip_path)
        except OSError:
            pass

        ensure_bin_in_path()
        return True
    except Exception:
        return False


def check_nvidia_gpu():
    """Check if NVIDIA GPU is present using nvidia-smi (no CUDA required)."""
    try:
        result = subprocess.run(['nvidia-smi'], capture_output=True, timeout=5,
                                **_SUBPROCESS_FLAGS)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
        return False


def _cuda_installed():
    """Check if CUDA DLLs are installed in user directory."""
    cuda_bin = CUDA_DIR / 'bin'
    if not cuda_bin.exists():
        return False
    
    required_dlls = ['cublas64_12.dll', 'cudnn64_9.dll', 'cudart64_12.dll']
    for dll in required_dlls:
        if not (cuda_bin / dll).exists():
            return False
    return True


def _add_cuda_to_path():
    """Add CUDA DLL directory to PATH and os.add_dll_directory."""
    cuda_bin = CUDA_DIR / 'bin'
    if cuda_bin.exists():
        cuda_bin_str = str(cuda_bin)
        if cuda_bin_str not in os.environ.get('PATH', ''):
            os.environ['PATH'] = cuda_bin_str + os.pathsep + os.environ.get('PATH', '')
        try:
            os.add_dll_directory(str(cuda_bin))
        except (OSError, AttributeError):
            pass


def _ask_user_download_cuda(root, allow_dont_ask=True):
    """Ask user if they want to download CUDA support. Returns True if user confirms."""
    from .config import load_config, save_config
    
    if allow_dont_ask:
        config = load_config()
        if config.get('cuda_download_opted_out'):
            return False
    
    if root is None:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
    
    import tkinter as tk
    from tkinter import ttk, messagebox

    dialog = tk.Toplevel(root)
    dialog.title("Download GPU Support")
    dialog.geometry("500x220")
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()
    
    tk.Label(dialog, text="NVIDIA GPU detected!\n\n"
        "Download CUDA runtime (~2.5GB) for faster conversion?\n"
        "This will be saved to your user folder and used automatically.",
        justify=tk.LEFT).pack(anchor='w', padx=20, pady=(20,10))
    
    dont_ask_var = tk.BooleanVar()
    if allow_dont_ask:
        tk.Checkbutton(dialog, text="Don't ask again", 
                       variable=dont_ask_var).pack(anchor='w', padx=20, pady=(0,10))
    
    result_holder = [None]
    
    def on_yes():
        if dont_ask_var.get():
            config = load_config()
            config['cuda_download_opted_out'] = True
            save_config(config)
        result_holder[0] = True
        dialog.destroy()
    
    def on_no():
        result_holder[0] = False
        dialog.destroy()
    
    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=15)
    ttk.Button(btn_frame, text="Yes", command=on_yes, width=10).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="No", command=on_no, width=10).pack(side=tk.LEFT, padx=5)
    
    dialog.wait_window()
    return result_holder[0] if result_holder[0] is not None else False


def _download_cuda_runtime(cuda_dir, progress_callback=None):
    """Download CUDA runtime DLLs from torch wheel.

    Downloads atomically: writes to a .part file and renames on success, so an
    interrupted download never leaves a corrupt whl file mistaken for a
    complete one. The downloaded zip is validated before extraction; if
    validation fails (network corruption, truncation), the download is retried
    once before giving up.
    """
    import tempfile
    import zipfile

    torch_cuda_url = "https://download.pytorch.org/whl/cu124/torch-2.6.0%2Bcu124-cp312-cp312-win_amd64.whl"

    cuda_bin = cuda_dir / 'bin'
    cuda_bin.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(tempfile.gettempdir()) / 'autiobooks_cuda'
    tmp_dir.mkdir(exist_ok=True)
    whl_path = tmp_dir / 'torch_cuda.whl'
    part_path = tmp_dir / 'torch_cuda.whl.part'

    def download_with_progress(url, dest):
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=300) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            block_size = 8192
            with open(dest, 'wb') as f:
                while True:
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    downloaded += len(buffer)
                    f.write(buffer)
                    if progress_callback and total_size:
                        progress_callback(downloaded, total_size)
            if total_size and downloaded < total_size:
                raise IOError(
                    f"Download truncated: got {downloaded} of {total_size} bytes")

    def fetch_and_validate():
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass
        download_with_progress(torch_cuda_url, part_path)
        try:
            with zipfile.ZipFile(part_path, 'r') as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise zipfile.BadZipFile(f"Corrupt entry: {bad}")
        except zipfile.BadZipFile:
            try:
                part_path.unlink()
            except OSError:
                pass
            raise
        if whl_path.exists():
            try:
                whl_path.unlink()
            except OSError:
                pass
        part_path.rename(whl_path)

    if progress_callback:
        progress_callback(0, 1)

    try:
        fetch_and_validate()
    except (IOError, zipfile.BadZipFile, URLError):
        fetch_and_validate()

    with zipfile.ZipFile(whl_path, 'r') as zf:
        for name in zf.namelist():
            if name.startswith('torch/lib/') and name.endswith('.dll'):
                filename = Path(name).name
                if any(cuda_dll in name for cuda_dll in [
                    'cublas', 'cudnn', 'cudart', 'cufft', 'curand',
                    'cusolver', 'nccl', 'nvjit'
                ]):
                    out_path = cuda_bin / filename
                    if not out_path.exists():
                        with zf.open(name) as src, open(out_path, 'wb') as dst:
                            dst.write(src.read())

    try:
        whl_path.unlink()
    except OSError:
        pass

    if progress_callback:
        progress_callback(1, 1)


def _show_cuda_download_dialog(root, progress_callback=None):
    """Show a progress dialog for CUDA download with cancel button."""
    import tkinter as tk
    from tkinter import ttk

    dialog = tk.Toplevel(root)
    dialog.title("Downloading CUDA Support")
    dialog.geometry("450x150")
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()

    label = tk.Label(dialog, text="Downloading CUDA runtime (~2.5GB)...")
    label.pack(pady=10)

    progress = ttk.Progressbar(dialog, mode='determinate', maximum=100)
    progress.pack(fill='x', padx=20, pady=10)

    status_label = tk.Label(dialog, text="Starting download...")
    status_label.pack(pady=5)

    dialog.update_idletasks()

    result_holder = {'cancelled': False, 'error': None, 'done': False, 'downloaded': 0, 'total': 0}

    def download_thread():
        try:
            _download_cuda_runtime(CUDA_DIR, lambda d, t: _update_progress(d, t))
        except Exception as e:
            result_holder['error'] = str(e)
        finally:
            result_holder['done'] = True

    def _update_progress(downloaded, total):
        result_holder['downloaded'] = downloaded
        result_holder['total'] = total

    def on_cancel():
        result_holder['cancelled'] = True
        result_holder['done'] = True

    cancel_btn = ttk.Button(dialog, text="Cancel", command=on_cancel,
                            style='Cancel.TButton')
    cancel_btn.pack(pady=5)

    import threading
    t = threading.Thread(target=download_thread)
    t.start()

    while not result_holder['done']:
        if result_holder['total'] > 0:
            progress['maximum'] = result_holder['total']
            progress['value'] = result_holder['downloaded']
            status_label.config(text=f"Downloaded {result_holder['downloaded'] / 1024 / 1024:.1f} MB / {result_holder['total'] / 1024 / 1024:.1f} MB")
        dialog.update()
        dialog.after(50)

    dialog.destroy()
    t.join()

    if result_holder['cancelled']:
        return False, "Download cancelled"
    if result_holder['error']:
        return False, result_holder['error']
    return True, None


def download_cuda_from_menu(root, gpu_acceleration_var=None):
    """Download CUDA from Tools menu - bypasses 'Don't ask again' preference."""
    if not check_nvidia_gpu():
        from tkinter import messagebox
        messagebox.showinfo("No GPU Detected", "No NVIDIA GPU found. CUDA download not needed.")
        return False

    if _cuda_installed():
        _add_cuda_to_path()
        from tkinter import messagebox
        messagebox.showinfo("CUDA Already Installed", "CUDA runtime is already installed.")
        return True

    success, error = _show_cuda_download_dialog(root)

    if success:
        _add_cuda_to_path()
        try:
            import torch
            if torch.cuda.is_available():
                if gpu_acceleration_var:
                    gpu_acceleration_var.set(True)
                return True
        except Exception:
            pass
        from tkinter import messagebox
        messagebox.showwarning("Download Incomplete", "CUDA was downloaded but GPU is not available. Check your GPU drivers.")
        return False
    else:
        if error and error != "Download cancelled":
            from tkinter import messagebox
            messagebox.showerror("Download Failed", error)
        return False


def ensure_cuda(root=None, progress_callback=None):
    """Ensure CUDA DLLs are available for GPU acceleration.
    
    Downloads CUDA runtime DLLs if NVIDIA GPU is detected but DLLs are missing.
    Requires user confirmation before downloading.
    
    Returns:
        True if CUDA is available (either already present or successfully downloaded)
    """
    _add_cuda_to_path()
    
    try:
        import torch
        if torch.cuda.is_available():
            return True
    except Exception:
        pass
    
    if not check_nvidia_gpu():
        return False
    
    if _cuda_installed():
        return True
    
    if not _ask_user_download_cuda(root):
        return False
    
    success, error = _show_cuda_download_dialog(root, progress_callback)
    
    if success:
        _add_cuda_to_path()
        
        try:
            import torch
            if torch.cuda.is_available():
                return True
        except Exception:
            pass
    
    return False


def check_ffmpeg():
    """Quick check if ffmpeg is in PATH."""
    ensure_bin_in_path()
    return which_exe('ffmpeg')


def check_espeakng():
    """Quick check if espeak-ng is in PATH."""
    ensure_bin_in_path()
    return which_exe('espeak-ng')


def check_cuda():
    """Check if CUDA is available for torch."""
    _add_cuda_to_path()
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False
