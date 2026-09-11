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

# Written into CUDA_DIR/bin as the LAST step of a CUDA runtime extraction;
# _cuda_installed() requires it, so a partial extraction (cancel, crash,
# power loss) can never masquerade as a complete install.
_CUDA_COMPLETE_MARKER = '.install_complete'

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

try:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Unwritable home (e.g. read-only profile) — don't crash at import;
    # downloads into BIN_DIR will fail later with a real error message.
    pass


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
    """Download a file with optional progress callback.

    Raises IOError if fewer bytes arrive than the response's Content-Length
    promised. CPython's HTTPResponse.read() intentionally does NOT raise on
    a dropped non-chunked connection — it closes the connection and returns
    b'', so the read loop exits normally and looks like a clean download
    unless the caller checks the byte count itself (mirrors the same check
    in _download_cuda_runtime.download_with_progress).
    """
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
    if total_size and downloaded < total_size:
        raise IOError(
            f"Download truncated: got {downloaded} of {total_size} bytes")


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
            # Zip-slip guard: never write outside the extraction root.
            try:
                if not target_path.resolve().is_relative_to(
                        extract_to.resolve()):
                    continue
            except (OSError, ValueError):
                continue
            if name.endswith('/'):
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                # .tmp + rename so a kill mid-extract can't leave a
                # truncated file at the final name (a half-written
                # ffmpeg.exe would otherwise pass the existence check on
                # every later launch).
                tmp_path = target_path.with_name(target_path.name + '.tmp')
                with zf.open(name) as src, open(tmp_path, 'wb') as dst:
                    dst.write(src.read())
                os.replace(tmp_path, target_path)


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

    found = shutil.which('ffmpeg')
    found_probe = shutil.which('ffprobe')
    if found:
        ffmpeg_ok = False
        try:
            check = subprocess.run([found, '-version'], capture_output=True,
                                   timeout=15, **_SUBPROCESS_FLAGS)
            ffmpeg_ok = check.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ffmpeg_ok = False

        # ffprobe is called just as often as ffmpeg (duration/chapter
        # probing in engine.py) — validating only ffmpeg here let a host
        # with a working ffmpeg but no/broken ffprobe pass this gate and
        # fail invisibly much later, deep inside a conversion.
        ffprobe_ok = False
        if found_probe:
            try:
                probe_check = subprocess.run(
                    [found_probe, '-version'], capture_output=True,
                    timeout=15, **_SUBPROCESS_FLAGS)
                ffprobe_ok = probe_check.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ffprobe_ok = False

        if ffmpeg_ok and ffprobe_ok:
            return True

        # ffmpeg and/or ffprobe is missing or doesn't run — e.g. truncated
        # by a kill mid-extract on an earlier run, before extraction was
        # atomic, or an incomplete system install missing ffprobe (the two
        # ship together in every package this app downloads). Remove our
        # managed copies and fall through to re-download; a broken system
        # ffmpeg/ffprobe is not ours to touch.
        import tkinter as _tk
        from tkinter import messagebox as _messagebox

        def _report_broken_ffmpeg(reason):
            # Must not exit mute: every other failure exit from this
            # function shows a dialog, but this one used to return False
            # with no window, no dialog, no log line — before this fix the
            # tkinter import below ran strictly after both `return False`s,
            # making the muteness structural. A throwaway root is created
            # here (mirrors _ask_user_download_cuda) since the download
            # dialog's own root hasn't been created yet at this point.
            local_root = root
            if local_root is None:
                local_root = _tk.Tk()
                local_root.withdraw()
                local_root.attributes('-topmost', True)
            _messagebox.showerror(
                "FFmpeg Broken",
                f"ffmpeg/ffprobe are on PATH but {reason}. Remove or "
                f"repair them, or delete them so Autiobooks can download "
                f"its own copy into {BIN_DIR}.")

        try:
            if Path(found).resolve().is_relative_to(BIN_DIR.resolve()):
                Path(found).unlink(missing_ok=True)
                if found_probe:
                    try:
                        if Path(found_probe).resolve().is_relative_to(
                                BIN_DIR.resolve()):
                            Path(found_probe).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass
            else:
                reason = 'do not run' if not ffmpeg_ok else 'ffprobe is missing or does not run'
                _report_broken_ffmpeg(reason)
                return False
        except (OSError, ValueError):
            _report_broken_ffmpeg('do not run and could not be removed')
            return False

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
        # No cancel support for this download — ignore the window X so a
        # click can't destroy the dialog under the polling callback.
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        label = tk.Label(dialog, text="FFmpeg not found. Downloading (~150MB)...")
        label.pack(pady=10)

        progress = ttk.Progressbar(dialog, mode='determinate', maximum=100)
        progress.pack(fill='x', padx=20, pady=5)

        status_label = tk.Label(dialog, text="Starting download...")
        status_label.pack(pady=5)

        dialog.update_idletasks()

        result_holder = [None]
        error_holder = [None]
        progress_holder = [0, 0]  # downloaded, total — fed from the thread

        def _on_progress(downloaded, total):
            progress_holder[0] = downloaded
            progress_holder[1] = total

        def download_thread():
            try:
                tmp_dir = Path(tempfile.gettempdir()) / 'autiobooks_download'
                tmp_dir.mkdir(exist_ok=True)
                zip_path = tmp_dir / 'ffmpeg.zip'
                part_path = tmp_dir / 'ffmpeg.zip.part'

                # Same pattern as _download_cuda_runtime.fetch_and_validate:
                # download to a .part file (so a truncated/killed download
                # never leaves a corrupt file at the name _extract_zip
                # trusts), validate the zip's central directory with
                # testzip(), then os.replace into place. One retry on a
                # transient network failure before giving up.
                def fetch_and_validate():
                    if part_path.exists():
                        try:
                            part_path.unlink()
                        except OSError:
                            pass
                    _download_file(FFMPEG_URL, part_path,
                                   progress_callback=_on_progress)
                    try:
                        with zipfile.ZipFile(part_path, 'r') as zf:
                            bad = zf.testzip()
                            if bad is not None:
                                raise zipfile.BadZipFile(
                                    f"Corrupt entry: {bad}")
                    except zipfile.BadZipFile:
                        try:
                            part_path.unlink()
                        except OSError:
                            pass
                        raise
                    if zip_path.exists():
                        try:
                            zip_path.unlink()
                        except OSError:
                            pass
                    os.replace(part_path, zip_path)

                try:
                    fetch_and_validate()
                except (IOError, zipfile.BadZipFile, URLError):
                    fetch_and_validate()

                _extract_zip(zip_path, BIN_DIR)

                exe_path = _find_exe_in_dir(BIN_DIR, 'ffmpeg.exe')
                if exe_path and exe_path.parent != BIN_DIR:
                    for f in exe_path.parent.iterdir():
                        if f.is_file():
                            # .tmp + os.replace per file, same as
                            # _extract_zip — a kill mid-flatten previously
                            # left this step non-atomic, the one gap in an
                            # otherwise all-atomic extraction pipeline.
                            tmp_copy = BIN_DIR / (f.name + '.tmp')
                            shutil.copy2(f, tmp_copy)
                            os.replace(tmp_copy, BIN_DIR / f.name)
                    shutil.rmtree(exe_path.parent)

                try:
                    os.unlink(zip_path)
                except OSError:
                    pass

                ensure_bin_in_path()
                # A kill mid-extract leaves a partial exe that passes the
                # existence check forever — verify the binaries actually
                # run. ffprobe is validated alongside ffmpeg since engine.py
                # calls it just as often (chapter/duration probing).
                check = subprocess.run(
                    [str(BIN_DIR / 'ffmpeg.exe'), '-version'],
                    capture_output=True, timeout=15, **_SUBPROCESS_FLAGS)
                probe_check = subprocess.run(
                    [str(BIN_DIR / 'ffprobe.exe'), '-version'],
                    capture_output=True, timeout=15, **_SUBPROCESS_FLAGS)
                if check.returncode != 0 or probe_check.returncode != 0:
                    raise RuntimeError(
                        f'ffmpeg/ffprobe were extracted but fail to run; '
                        f'delete {BIN_DIR} and retry')
                result_holder[0] = True
            except Exception as e:
                error_holder[0] = str(e)
                result_holder[0] = False

        def update_progress():
            if result_holder[0] is None:
                downloaded, total = progress_holder
                if total:
                    progress['value'] = downloaded / total * 100
                    status_label.config(
                        text=f"Downloaded {downloaded / 1048576:.0f} MB / "
                             f"{total / 1048576:.0f} MB")
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
        t = _threading.Thread(target=download_thread, daemon=True)
        t.start()

        update_progress()
        dialog.wait_window()

        return result_holder[0]

    return show_download_dialog()


class _CudaDownloadCancelled(Exception):
    """Raised inside the CUDA download loop when the user clicks Cancel."""


def _torch_cuda_capable():
    """True when the installed torch was built WITH the CUDA backend.

    CPU-only wheels (pip's default on Windows, and our CPU build which
    installs from the /whl/cpu index) have torch.version.cuda == None —
    downloaded CUDA DLLs can never activate for them, so offering the
    2.5GB runtime download would be a guaranteed no-op.
    """
    try:
        import torch
        return torch.version.cuda is not None
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
    """Check if CUDA DLLs are FULLY installed in the user directory.

    Requires the completion marker written as the last step of extraction:
    the per-DLL writes are individually atomic, but a kill/crash/power-loss
    mid-extraction could leave the sentinel DLLs present with later ones
    missing — a state that passed this check forever ("CUDA Already
    Installed") while torch.cuda stayed unavailable, with no repair path
    short of manually deleting ~/.autiobooks/cuda."""
    cuda_bin = CUDA_DIR / 'bin'
    if not cuda_bin.exists():
        return False
    if not (cuda_bin / _CUDA_COMPLETE_MARKER).exists():
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
        # 'Don't ask again' must work with No — that's its natural pairing
        # (decline and stop nagging). Previously only Yes honored it.
        if dont_ask_var.get():
            config = load_config()
            config['cuda_download_opted_out'] = True
            save_config(config)
        result_holder[0] = False
        dialog.destroy()
    
    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=15)
    ttk.Button(btn_frame, text="Yes", command=on_yes, width=10).pack(side=tk.LEFT, padx=5)
    ttk.Button(btn_frame, text="No", command=on_no, width=10).pack(side=tk.LEFT, padx=5)
    
    dialog.wait_window()
    return result_holder[0] if result_holder[0] is not None else False


def _download_cuda_runtime(cuda_dir, progress_callback=None,
                           cancel_event=None):
    """Download CUDA runtime DLLs from torch wheel.

    Downloads atomically: writes to a .part file and renames on success, so an
    interrupted download never leaves a corrupt whl file mistaken for a
    complete one. The downloaded zip is validated before extraction; if
    validation fails (network corruption, truncation), the download is retried
    once before giving up.

    `cancel_event` is polled between chunks; when set, the download aborts
    with _CudaDownloadCancelled (never retried).
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
                    if cancel_event is not None and cancel_event.is_set():
                        raise _CudaDownloadCancelled()
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

    # Invalidate any previous complete install before touching its DLLs —
    # if this (re)extraction dies partway, _cuda_installed() must not keep
    # reporting the old marker against a now-mixed DLL set.
    marker_path = cuda_bin / _CUDA_COMPLETE_MARKER
    try:
        marker_path.unlink()
    except OSError:
        pass

    with zipfile.ZipFile(whl_path, 'r') as zf:
        for name in zf.namelist():
            if name.startswith('torch/lib/') and name.endswith('.dll'):
                # Cancel used to be polled only in the download chunk loop;
                # during this multi-GB extraction the Cancel button closed
                # the dialog while a zombie thread kept extracting (and
                # raced a retry over the same temp paths).
                if cancel_event is not None and cancel_event.is_set():
                    raise _CudaDownloadCancelled()
                filename = Path(name).name
                if any(cuda_dll in name for cuda_dll in [
                    'cublas', 'cudnn', 'cudart', 'cufft', 'curand',
                    'cusolver', 'nccl', 'nvjit'
                ]):
                    out_path = cuda_bin / filename
                    # Always (re)write via a temp file: a kill mid-extract
                    # previously left a truncated DLL that passed the
                    # existence check forever and crashed torch at load.
                    tmp_path = cuda_bin / (filename + '.tmp')
                    with zf.open(name) as src, open(tmp_path, 'wb') as dst:
                        dst.write(src.read())
                    os.replace(tmp_path, out_path)

    # LAST step: the marker that lets _cuda_installed() trust the DLL set.
    marker_tmp = cuda_bin / (_CUDA_COMPLETE_MARKER + '.tmp')
    marker_tmp.write_text('ok', encoding='utf-8')
    os.replace(marker_tmp, marker_path)

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

    import threading
    result_holder = {'cancelled': False, 'error': None, 'done': False, 'downloaded': 0, 'total': 0}
    cancel_event = threading.Event()

    def download_thread():
        try:
            _download_cuda_runtime(CUDA_DIR, lambda d, t: _update_progress(d, t),
                                   cancel_event=cancel_event)
        except _CudaDownloadCancelled:
            pass
        except Exception as e:
            result_holder['error'] = str(e)
        finally:
            result_holder['done'] = True

    def _update_progress(downloaded, total):
        result_holder['downloaded'] = downloaded
        result_holder['total'] = total

    def on_cancel():
        # Signal the download loop AND release the UI immediately — the
        # thread may be blocked in a socket read, so the dialog must not
        # wait for it (daemon thread; it aborts at the next chunk).
        cancel_event.set()
        result_holder['cancelled'] = True
        result_holder['done'] = True

    cancel_btn = ttk.Button(dialog, text="Cancel", command=on_cancel,
                            style='Cancel.TButton')
    cancel_btn.pack(pady=5)

    # Closing via the window X must behave like Cancel — without this the
    # destroyed Toplevel crashed the old update() busy-loop with TclError.
    dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    t = threading.Thread(target=download_thread, daemon=True)
    t.start()

    def _poll():
        if result_holder['done']:
            dialog.destroy()
            return
        if result_holder['total'] > 0:
            progress['maximum'] = result_holder['total']
            progress['value'] = result_holder['downloaded']
            status_label.config(
                text=f"Downloaded "
                     f"{result_holder['downloaded'] / 1024 / 1024:.1f} MB / "
                     f"{result_holder['total'] / 1024 / 1024:.1f} MB")
        dialog.after(100, _poll)

    _poll()
    dialog.wait_window()

    if not result_holder['cancelled']:
        # Normal completion: the thread has already finished (it set 'done'
        # itself); join is instant. On cancel we never block the main
        # thread on a possibly-stalled socket read.
        t.join(timeout=30)

    if result_holder['cancelled']:
        return False, "Download cancelled"
    if result_holder['error']:
        return False, result_holder['error']
    return True, None


def download_cuda_from_menu(root, gpu_acceleration_var=None):
    """Download CUDA from Tools menu - bypasses 'Don't ask again' preference."""
    if not _torch_cuda_capable():
        from tkinter import messagebox
        messagebox.showinfo(
            "CPU-only build",
            "This installation uses CPU-only torch, which cannot use the "
            "CUDA runtime even after downloading it.\n\nUse the CUDA build "
            "of Autiobooks for GPU acceleration.")
        return False
    if not check_nvidia_gpu():
        from tkinter import messagebox
        messagebox.showinfo("No GPU Detected", "No NVIDIA GPU found. CUDA download not needed.")
        return False

    # A working install answers here regardless of the marker file (legacy
    # installs predate it). Only when torch can't actually use the GPU do
    # we fall through to (re)download — a partial extraction that used to
    # wedge as "Already Installed" now gets a repair path.
    _add_cuda_to_path()
    try:
        import torch
        if torch.cuda.is_available():
            if gpu_acceleration_var:
                gpu_acceleration_var.set(True)
            from tkinter import messagebox
            messagebox.showinfo("CUDA Already Installed",
                                "CUDA runtime is already installed and "
                                "active.")
            return True
    except Exception:
        pass

    if _cuda_installed():
        from tkinter import messagebox
        if not messagebox.askyesno(
                "CUDA Installed But Inactive",
                "The CUDA runtime is installed but the GPU is not "
                "available (drivers, or a broken install).\n\n"
                "Re-download the CUDA runtime?"):
            return False

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
    
    # CPU-only torch can never use the DLLs — don't prompt for a 2.5GB
    # download that cannot activate.
    if not _torch_cuda_capable():
        return False

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


# NOTE: espeak-ng needs no runtime download/ensure step — misaki's espeak
# module loads the library through the bundled `espeakng_loader` package
# (see autiobooks/misaki/espeak.py), so the old ensure_espeakng/check_*
# helpers were dead code and have been removed.
