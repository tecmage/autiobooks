import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import sys
import platform
import time
import importlib.metadata
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional
from PIL import Image, ImageTk
from pathlib import Path
from .engine import get_gpu_acceleration_available, gen_audio_segments
from .engine import set_gpu_acceleration, convert_text_to_wav_file
from .engine import unlink_with_retry
from .engine import convert_chapters_to_wav
from .engine import safe_stem, chapter_wav_name, render_key, assemble_output
from .engine import find_chapter_wavs
from .engine import try_begin_conversion, end_conversion, is_conversion_active
from .runtime import ensure_cuda
from .epub_parser import (
    get_book, get_book_cached, get_title, get_author, get_cover_image,
    get_chapter_titles, get_publisher, get_publication_year, get_description,
    clear_chapter_cache,
)
from .theme import THEMES, apply_theme as _apply_theme_impl, get_current_theme
from .dialogs import (
    show_append_dialog as _show_append_dialog_impl,
    show_preferences as _show_preferences_impl,
    show_substitutions_dialog as _show_substitutions_dialog_impl,
    show_phoneme_overrides_dialog as _show_phoneme_overrides_dialog_impl,
)
from .batch_window import show_batch_window as _show_batch_window_impl
from .chapter_tree import ChapterTreeView
from .pdf_parser import get_pdf_book, get_pdf_cover_bytes
from .text_processing import normalize_text
from .config import load_config, save_config, sanitize_dict_list
import pygame.mixer
import soundfile
import numpy as np
import shutil
import tempfile
import os
from . import voices_lang
from .voices_lang import voices, voices_emojified, deemojify_voice, get_language_from_voice

PREVIEW_FILE = os.path.join(
    tempfile.gettempdir(), f"autiobooks_preview_{os.getpid()}.wav")

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


import atexit
import contextlib
import subprocess as _subprocess

# Live sleep-inhibitor children (caffeinate / systemd-inhibit). Conversion
# workers are daemon threads, and CPython kills daemon threads at interpreter
# exit WITHOUT running their finally blocks — so prevent_sleep's own cleanup
# never fires when the user quits mid-conversion, and the 'sleep infinity'
# child would hold the idle inhibitor until reboot. This atexit hook is the
# backstop that runs on every normal interpreter shutdown.
_sleep_inhibitors = set()


def _terminate_sleep_inhibitors():
    procs = list(_sleep_inhibitors)
    _sleep_inhibitors.clear()
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass


atexit.register(_terminate_sleep_inhibitors)


@contextlib.contextmanager
def prevent_sleep():
    """Prevent the OS from sleeping during long conversions.

    Setup errors are swallowed so the caller still runs even if sleep
    prevention isn't available. Cleanup always runs, even on exceptions from
    the caller.
    """
    _system = platform.system()
    _proc = None
    _windows_set = False
    if _system == 'Windows':
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            _windows_set = True
        except (OSError, AttributeError):
            pass
    elif _system == 'Darwin':
        try:
            _proc = _subprocess.Popen(
                ['caffeinate', '-i'], stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL)
        except (OSError, FileNotFoundError):
            pass
    elif _system == 'Linux':
        try:
            _proc = _subprocess.Popen(
                ['systemd-inhibit', '--what=idle', '--who=Autiobooks',
                 '--why=Converting audiobook', 'sleep', 'infinity'],
                stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        except (OSError, FileNotFoundError):
            pass
    if _proc is not None:
        _sleep_inhibitors.add(_proc)
    try:
        yield
    finally:
        if _windows_set:
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
        if _proc is not None:
            _sleep_inhibitors.discard(_proc)
            try:
                _proc.terminate()
                _proc.wait(timeout=5)
            except Exception:
                pass

playing_sample = False
book = None


@dataclass
class BatchJob:
    file_path: str
    book: object
    chapters: list
    selected_chapter_indices: list
    voice: str
    speed: str
    chapter_gap: str
    gpu_acceleration: bool
    detect_titles: bool
    starting_chapter: int
    chapter_titles: Optional[list] = None
    title: str = ""
    author: str = ""
    total_words: int = 0
    bitrate: str = "64k"
    vbr: bool = False
    output_format: str = "m4b"
    read_title_author: bool = True
    status: str = "Queued"


def format_duration_estimate(words, speed):
    """Return a human display string for the estimated audio duration of
    `words` words read at `speed`.

    Kokoro reads roughly 150 words per minute at speed 1.0; `speed` scales
    that linearly (2.0 halves the duration, 0.5 doubles it). Pure function
    so it can be unit-tested without a Tk root.
    """
    if speed <= 0:
        speed = 1.0
    minutes = words / (150 * speed)
    if minutes < 1:
        return '~<1 min'
    if minutes < 60:
        return f'~{round(minutes)} min'
    total_min = round(minutes)
    h, m = divmod(total_min, 60)
    return f'~{h}h {m}m'


def add_tooltip(widget, text):
    tip = None

    def show_tip(event):
        nonlocal tip
        colors = get_current_theme()
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip_text = text() if callable(text) else text
        tk.Label(tip, text=tip_text, background=colors['tooltip_bg'],
                 foreground=colors['tooltip_fg'], relief="solid",
                 borderwidth=1, font=('Arial', 10)).pack()

    def hide_tip(event):
        nonlocal tip
        if tip:
            tip.destroy()
            tip = None

    widget.bind("<Enter>", show_tip, add=True)
    widget.bind("<Leave>", hide_tip, add=True)


def _set_bool_var(var, value):
    """Coerce a config value into a BooleanVar, keeping the current default
    on failure. tkinter.BooleanVar.set() runs the value through Tk's
    getboolean() internally, so a hand-edited config holding a non-boolean
    (e.g. "enabled", None) raises TclError/TypeError there — before any
    window exists — unless caught here."""
    try:
        var.set(value)
    except (tk.TclError, TypeError):
        pass  # keep the code default


def _configure_audio_driver():
    """Pick an SDL audio driver for pygame.mixer on headless/WSL Linux hosts.

    On a WSL or headless server with no ALSA sound card, SDL falls back to
    ALSA and prints 'cannot find card 0' warnings to stderr before
    pygame.mixer.init() raises. This picks a driver up front to avoid that:
    WSLg's PulseAudio when it's present (so preview audio still plays),
    otherwise SDL's 'dummy' driver (silent — preview is disabled gracefully
    by the caller's except). A user-set SDL_AUDIODRIVER always wins, and a
    normal Linux desktop with a real card is left untouched so SDL can
    autodetect. No-op on non-Linux platforms."""
    if os.environ.get('SDL_AUDIODRIVER'):
        return  # respect an explicit user override
    if sys.platform != 'linux':
        return
    # WSLg exposes a PulseAudio server; prefer it so preview audio works.
    if os.environ.get('PULSE_SERVER') or os.path.exists('/mnt/wslg/PulseServer'):
        os.environ['SDL_AUDIODRIVER'] = 'pulseaudio'
        return
    try:
        with open('/proc/asound/cards', encoding='utf-8') as f:
            cards = f.read()
    except OSError:
        cards = ''  # ALSA not loaded at all → treat as cardless
    if not cards.strip() or 'no soundcards' in cards.lower():
        os.environ['SDL_AUDIODRIVER'] = 'dummy'


def start_gui():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    try:
        _version = importlib.metadata.version('autiobooks')
    except importlib.metadata.PackageNotFoundError:
        _version = '?'
    root.title(f'Autiobooks v{_version}')
    window_width = 1000
    window_height = 900
    root.geometry(f"{window_width}x{window_height}")
    root.resizable(True, True)
    root.option_add("*Font", "Arial 12")

    def apply_theme(theme_name):
        _apply_theme_impl(root, theme_name)

    def show_append_dialog():
        _show_append_dialog_impl(root)

    style = ttk.Style()
    style.theme_use('clam')
    style.configure('.', font=('Arial', 12))

    # check ffmpeg is installed
    # sys.exit, not the builtin exit(): site.py (which defines exit) is not
    # loaded in PyInstaller builds, so exit() would NameError there.
    if sys.platform == 'win32':
        from .runtime import ensure_ffmpeg
        if not ensure_ffmpeg(root):
            sys.exit(1)
    else:
        # ffprobe is required on every convert path, not just m4b: it
        # supplies chapter durations for m4b markers and the progress
        # total for the non-m4b concat path. ffmpeg-only used to pass
        # this gate and hit _safe_probe_duration returning 0.0 (every
        # chapter marker at t=0) only after a full TTS run.
        missing = [b for b in ('ffmpeg', 'ffprobe') if shutil.which(b) is None]
        if missing:
            messagebox.showwarning(
                "Warning",
                f"{' and '.join(missing)} not found. Please install "
                f"{'it' if len(missing) == 1 else 'them'} to create "
                "m4b audiobook files.")
            sys.exit(1)

    # Row 1: Voice, speed, and gap settings
    settings_row1 = tk.Frame(root)
    settings_row1.pack(pady=5, padx=5)

    voice_label = tk.Label(settings_row1, text="Select Voice:")
    voice_label.pack(side=tk.LEFT, pady=5, padx=5)

    voice_combo = ttk.Combobox(
        settings_row1,
        values=voices_emojified,
        state="readonly"
    )
    voice_combo.set(voices_emojified[0])
    voice_combo.pack(side=tk.LEFT, pady=5, padx=5)

    def refresh_voice_dropdown(*_):
        """Re-scan ~/.autiobooks/voices/ so newly-dropped .pt files appear
        without needing a restart. Triggered on dropdown click and after
        opening the voices folder via the Tools menu."""
        current = voice_combo.get()
        voices_lang.refresh_voices()
        voice_combo.configure(values=voices_lang.voices_emojified)
        if current in voices_lang.voices_emojified:
            voice_combo.set(current)
        elif voices_lang.voices_emojified:
            voice_combo.set(voices_lang.voices_emojified[0])

    voice_combo.bind('<Button-1>', refresh_voice_dropdown)

    speed_label = tk.Label(settings_row1, text="Reading speed:")
    speed_label.pack(side=tk.LEFT, pady=5, padx=15)

    def check_speed_range(event=None):
        try:
            value = float(speed_entry.get())
            if 0.5 <= value <= 2.0:
                speed_entry.configure(foreground='')
                return True
            else:
                speed_entry.configure(foreground='red')
        except ValueError:
            speed_entry.configure(foreground='red')
        return False

    speed_entry = ttk.Entry(settings_row1, width=5)
    speed_entry.insert(0, "1.0")
    speed_entry.pack(side=tk.LEFT, pady=5, padx=5)
    speed_entry.bind('<KeyRelease>', check_speed_range)

    bitrate_label = tk.Label(settings_row1, text="Bitrate:")
    bitrate_label.pack(side=tk.LEFT, pady=5, padx=15)

    bitrate_combo = ttk.Combobox(
        settings_row1,
        values=['64k', '128k', '192k'],
        state='readonly',
        width=5,
    )
    bitrate_combo.set('64k')
    bitrate_combo.pack(side=tk.LEFT, pady=5, padx=5)

    use_vbr = tk.BooleanVar(value=False)
    vbr_checkbox = tk.Checkbutton(settings_row1, text="VBR", variable=use_vbr)
    vbr_checkbox.pack(side=tk.LEFT, pady=5, padx=5)
    add_tooltip(vbr_checkbox,
                "Variable bitrate: higher quality-to-size ratio.\n"
                "M4B: uses AAC VBR quality level 2 (~96–128 kbps).\n"
                "MP3: bitrate value is remapped to a libmp3lame "
                "quality level (64k→q7, 128k→q4, 192k→q2).")

    format_label = tk.Label(settings_row1, text="Format:")
    format_label.pack(side=tk.LEFT, pady=5, padx=(15, 0))

    OUTPUT_FORMATS = {
        'm4b': {'ext': '.m4b', 'desc': 'M4B audiobook', 'chapters': True},
        'mp3': {'ext': '.mp3', 'desc': 'MP3 audio', 'chapters': False},
        'flac': {'ext': '.flac', 'desc': 'FLAC audio', 'chapters': False},
        'opus': {'ext': '.opus', 'desc': 'Opus audio', 'chapters': False},
        'wav': {'ext': '.wav', 'desc': 'WAV audio', 'chapters': False},
    }
    format_combo = ttk.Combobox(
        settings_row1,
        values=list(OUTPUT_FORMATS.keys()),
        state='readonly',
        width=5,
    )
    format_combo.set('m4b')
    format_combo.pack(side=tk.LEFT, pady=5, padx=5)

    def _update_bitrate_vbr_state():
        fmt = format_combo.get()
        supports_vbr = fmt in ('m4b', 'mp3')
        supports_bitrate = fmt in ('m4b', 'mp3')
        vbr_checkbox.configure(state='normal' if supports_vbr else 'disabled')
        if not supports_vbr and use_vbr.get():
            use_vbr.set(False)
        if not supports_bitrate:
            bitrate_combo.configure(state='disabled')
        elif fmt == 'm4b' and use_vbr.get():
            # M4B VBR ignores the bitrate value
            bitrate_combo.configure(state='disabled')
        else:
            bitrate_combo.configure(state='readonly')

    def on_vbr_changed(*_):
        _update_bitrate_vbr_state()

    def on_format_changed(*_):
        _update_bitrate_vbr_state()

    use_vbr.trace_add('write', on_vbr_changed)
    format_combo.bind('<<ComboboxSelected>>', on_format_changed)

    # Row 2: Checkboxes
    settings_row2 = tk.Frame(root)
    settings_row2.pack(pady=2, padx=5)

    gpu_acceleration = tk.BooleanVar()
    gpu_acceleration.set(False)
    gpu_acceleration_checkbox = tk.Checkbutton(
        settings_row2,
        text="Enable GPU acceleration",
        variable=gpu_acceleration,
        state='disabled'
    )
    if platform.system() == "Windows":
        ensure_cuda(root)
    if get_gpu_acceleration_available():
        import torch
        mps_available = (hasattr(torch.backends, 'mps')
                         and torch.backends.mps.is_available())
        if torch.cuda.is_available() or mps_available:
            gpu_acceleration_checkbox.config(state='normal')
            gpu_acceleration_checkbox.pack(side=tk.LEFT, pady=5, padx=15)
        else:
            add_tooltip(gpu_acceleration_checkbox,
                        "GPU acceleration requires a CUDA-enabled build.\n"
                        "The standalone Windows build uses CPU-only torch.\n"
                        "For GPU support, use the CUDA-enabled version.")
            gpu_acceleration_checkbox.pack(side=tk.LEFT, pady=5, padx=15)

    gap_label = tk.Label(settings_row2, text="Chapter gap (s):")
    gap_label.pack(side=tk.LEFT, pady=5, padx=15)

    def check_gap_range(event=None):
        try:
            value = float(gap_entry.get())
            if 0.0 <= value <= 10.0:
                gap_entry.configure(foreground='')
                return True
            else:
                gap_entry.configure(foreground='red')
        except ValueError:
            gap_entry.configure(foreground='red')
        return False

    gap_entry = ttk.Entry(settings_row2, width=5)
    gap_entry.insert(0, "2.0")
    gap_entry.pack(side=tk.LEFT, pady=5, padx=5)
    gap_entry.bind('<KeyRelease>', check_gap_range)

    detect_titles = tk.BooleanVar()
    detect_titles.set(True)
    detect_titles_checkbox = tk.Checkbutton(
        settings_row2,
        text="Detect chapter titles",
        variable=detect_titles
    )
    detect_titles_checkbox.pack(side=tk.LEFT, pady=5, padx=15)

    read_title_author_bool = tk.BooleanVar(value=True)

    def show_substitutions_dialog():
        def on_save(new_subs):
            nonlocal word_substitutions
            word_substitutions = new_subs
            save_config(get_current_config())
        _show_substitutions_dialog_impl(root, word_substitutions, on_save)

    def show_phoneme_overrides_dialog():
        def on_save(new_overrides):
            nonlocal phoneme_overrides
            phoneme_overrides = new_overrides
            save_config(get_current_config())
        _show_phoneme_overrides_dialog_impl(
            root, phoneme_overrides, on_save)

    def open_voices_folder():
        voices_dir = voices_lang.get_voices_dir()
        try:
            voices_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror(
                "Voices Folder",
                f"Could not create voices folder:\n{voices_dir}\n\n{e}")
            return
        try:
            if platform.system() == 'Windows':
                os.startfile(str(voices_dir))
            elif platform.system() == 'Darwin':
                _subprocess.Popen(['open', str(voices_dir)])
            else:
                _subprocess.Popen(['xdg-open', str(voices_dir)])
        except OSError as e:
            messagebox.showerror(
                "Voices Folder",
                f"Could not open voices folder:\n{voices_dir}\n\n{e}")
            return
        refresh_voice_dropdown()

    menubar = tk.Menu(root)
    tools_menu = tk.Menu(menubar, tearoff=0)
    tools_menu.add_command(label='Append M4B files...', command=show_append_dialog)
    tools_menu.add_command(label='Batch Queue...', command=lambda: show_batch_window())
    tools_menu.add_command(label='Word Substitutions...',
                           command=show_substitutions_dialog)
    tools_menu.add_command(label='Pronunciation Overrides...',
                           command=show_phoneme_overrides_dialog)
    tools_menu.add_command(label='Open Voices Folder...',
                           command=open_voices_folder)
    if platform.system() == "Windows":
        from .runtime import check_nvidia_gpu
        if check_nvidia_gpu():
            from .runtime import download_cuda_from_menu
            tools_menu.add_command(label='Download CUDA Support...',
                                   command=lambda: download_cuda_from_menu(root, gpu_acceleration))
    menubar.add_cascade(label='Tools', menu=tools_menu)

    theme_var = tk.StringVar(value='light')
    pref_heteronyms = tk.BooleanVar(value=True)
    pref_contractions = tk.BooleanVar(value=False)
    pref_auto_select = tk.BooleanVar(value=True)
    pref_mark_duplicates = tk.BooleanVar(value=True)
    pref_auto_acronyms = tk.BooleanVar(value=False)

    def show_preferences():
        _show_preferences_impl(
            root,
            prefs={
                'theme_var': theme_var,
                'heteronyms': pref_heteronyms,
                'contractions': pref_contractions,
                'auto_select': pref_auto_select,
                'mark_duplicates': pref_mark_duplicates,
                'auto_acronyms': pref_auto_acronyms,
            },
            apply_theme=apply_theme,
            save_current_config=lambda: save_config(get_current_config()),
            add_tooltip=add_tooltip,
        )

    settings_menu = tk.Menu(menubar, tearoff=0)
    settings_menu.add_command(label='Preferences...', command=show_preferences)
    menubar.add_cascade(label='Settings', menu=settings_menu)

    root.config(menu=menubar)

    starting_ch_label = tk.Label(settings_row2, text="  Starting Chapter #:")
    starting_ch_label.pack(side=tk.LEFT, padx=(15, 5))
    add_tooltip(starting_ch_label,
                "Sets the chapter number of the first selected chapter in the\n"
                "output file. Useful when splitting a book across multiple files.")

    def check_chapter_range(event=None):
        try:
            value = int(chapter_entry.get())
            if 0 <= value <= 99999:
                chapter_entry.configure(foreground='')
                return True
            else:
                chapter_entry.configure(foreground='red')
        except ValueError:
            chapter_entry.configure(foreground='red')
        return False

    chapter_entry = ttk.Entry(settings_row2, width=5)
    chapter_entry.insert(0, "1")
    chapter_entry.pack(side=tk.LEFT, padx=5)
    chapter_entry.bind('<KeyRelease>', check_chapter_range)

    def on_detect_titles_changed(*_):
        state = 'disabled' if detect_titles.get() else 'normal'
        if state == 'disabled' and not check_chapter_range():
            # Normalize before locking the field, otherwise a stale invalid
            # value (e.g. "" left mid-edit) permanently blocks Convert with
            # no way to fix it short of unticking this same checkbox.
            chapter_entry.delete(0, tk.END)
            chapter_entry.insert(0, '1')
            chapter_entry.configure(foreground='')
        chapter_entry.configure(state=state)

    detect_titles.trace_add('write', on_detect_titles_changed)
    on_detect_titles_changed()  # set initial state

    # Load saved settings
    config = load_config()
    if config.get('voice') in voices_emojified:
        voice_combo.set(config['voice'])
    # Numeric values are validated before loading so a corrupt config does
    # not crash the subsequent float()/int() casts during conversion.
    speed_cfg = config.get('speed')
    if speed_cfg:
        try:
            float(speed_cfg)
            speed_entry.delete(0, tk.END)
            speed_entry.insert(0, str(speed_cfg))
        except (TypeError, ValueError):
            pass
    gap_cfg = config.get('chapter_gap')
    if gap_cfg:
        try:
            float(gap_cfg)
            gap_entry.delete(0, tk.END)
            gap_entry.insert(0, str(gap_cfg))
        except (TypeError, ValueError):
            pass
    if config.get('gpu_acceleration') and get_gpu_acceleration_available():
        gpu_acceleration.set(True)
    set_gpu_acceleration(gpu_acceleration.get())
    if 'detect_titles' in config:
        _set_bool_var(detect_titles, config['detect_titles'])
    if config.get('bitrate') in ('64k', '128k', '192k'):
        bitrate_combo.set(config['bitrate'])
    if config.get('vbr'):
        use_vbr.set(True)
    if config.get('output_format') in OUTPUT_FORMATS:
        format_combo.set(config['output_format'])
        on_format_changed()
    if 'read_title_author' in config:
        _set_bool_var(read_title_author_bool, config['read_title_author'])
    starting_ch_cfg = config.get('starting_chapter')
    if starting_ch_cfg:
        try:
            int(starting_ch_cfg)
            chapter_entry.configure(state='normal')
            chapter_entry.delete(0, tk.END)
            chapter_entry.insert(0, str(starting_ch_cfg))
            on_detect_titles_changed()
        except (TypeError, ValueError):
            pass
    def _validated_dir(value):
        # If the user deletes a remembered directory between sessions, fall
        # back to empty so the file dialog opens at the platform default
        # rather than at a stale path that Tk silently swallows.
        return value if value and Path(value).is_dir() else ''

    last_directory = _validated_dir(config.get('last_directory', ''))
    last_output_directory = _validated_dir(
        config.get('last_output_directory', ''))
    word_substitutions = sanitize_dict_list(
        config.get('word_substitutions', []), str_keys=('find', 'replace'))
    phoneme_overrides = sanitize_dict_list(
        config.get('phoneme_overrides', []), str_keys=('word', 'ipa'))
    if config.get('theme') in THEMES:
        theme_var.set(config['theme'])
        apply_theme(config['theme'])
    if 'heteronyms' in config:
        _set_bool_var(pref_heteronyms, config['heteronyms'])
    if 'contractions' in config:
        _set_bool_var(pref_contractions, config['contractions'])
    if 'auto_select' in config:
        _set_bool_var(pref_auto_select, config['auto_select'])
    if 'mark_duplicates' in config:
        _set_bool_var(pref_mark_duplicates, config['mark_duplicates'])
    if 'auto_acronyms' in config:
        _set_bool_var(pref_auto_acronyms, config['auto_acronyms'])

    def get_current_config():
        # Start from what's on disk so keys written by other components
        # (runtime.py's cuda_download_opted_out, future fields) survive the
        # GUI's full-dict saves instead of being silently dropped.
        cfg = load_config()
        cfg.update({
            'voice': voice_combo.get(),
            'speed': speed_entry.get(),
            'chapter_gap': gap_entry.get(),
            'gpu_acceleration': gpu_acceleration.get(),
            'detect_titles': detect_titles.get(),
            'bitrate': bitrate_combo.get(),
            'vbr': use_vbr.get(),
            'output_format': format_combo.get(),
            'read_title_author': read_title_author_bool.get(),
            'starting_chapter': chapter_entry.get(),
            'last_directory': last_directory,
            'last_output_directory': last_output_directory,
            'word_substitutions': word_substitutions,
            'phoneme_overrides': phoneme_overrides,
            'theme': theme_var.get(),
            'heteronyms': pref_heteronyms.get(),
            'contractions': pref_contractions.get(),
            'auto_select': pref_auto_select.get(),
            'mark_duplicates': pref_mark_duplicates.get(),
            'auto_acronyms': pref_auto_acronyms.get(),
        })
        return cfg

    def on_close():
        if is_conversion_active():
            if not messagebox.askyesno(
                    "Quit",
                    "A conversion is still running. Quit anyway?\n"
                    "Completed chapters are kept — the next run can resume "
                    "from them."):
                return
            cancel_event.set()
            # The batch worker polls its own event, not cancel_event. Note
            # the cancel signals are best-effort: root.destroy() below ends
            # mainloop and interpreter teardown kills the daemon workers
            # mid-chapter WITHOUT running their finally blocks — nothing
            # waits for a chapter boundary. Atomic .part writes bound the
            # file damage, and the atexit hook (_terminate_sleep_inhibitors)
            # reaps the sleep-inhibitor child the worker's finally would
            # have terminated.
            from .batch_window import cancel_active_batch
            cancel_active_batch()
        if audio_available:
            pygame.mixer.music.stop()
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
            pygame.mixer.quit()
        Path(PREVIEW_FILE).unlink(missing_ok=True)
        save_config(get_current_config())
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def show_batch_window():
        _show_batch_window_impl(
            root,
            batch_queue=batch_queue,
            initial_dir=last_directory,
            prevent_sleep=prevent_sleep,
            prefs={
                'heteronyms': pref_heteronyms,
                'contractions': pref_contractions,
                'gpu_acceleration': gpu_acceleration,
            },
            get_substitutions=lambda: word_substitutions,
            get_phoneme_overrides=lambda: phoneme_overrides,
            get_auto_acronyms=lambda: pref_auto_acronyms.get(),
            is_preview_active=lambda: generating_preview,
        )

    ttk.Separator(root, orient='horizontal').pack(fill='x', padx=5, pady=2)

    audio_available = False
    _configure_audio_driver()
    try:
        pygame.mixer.init()
        pygame.mixer.music.set_volume(0.7)
        audio_available = True
    except pygame.error as e:
        messagebox.showwarning("Audio Warning",
                               f"Could not initialize audio preview: {e}\n\n"
                               "TTS conversion will still work.")

    book_frame = tk.Frame(root)
    book_frame.grid_columnconfigure(0, weight=1)
    book_frame.grid_columnconfigure(1, weight=4)
    
    # ui element variables
    pil_image = Image.new('RGB', (200, 300), 'gray')
    cover_image = ImageTk.PhotoImage(pil_image)  # or use a default image
    cover_label = tk.Label(book_frame, image=cover_image)
    chapters = []
    
    def get_limited_text(text):
        text = text.replace("\n", " ")
        words = text.split()
        if len(words) > 25:
            return ' '.join(words[:25])
        return text

    def select_all():
        if chapter_tree_view:
            chapter_tree_view.select_all()

    def clear_all():
        if chapter_tree_view:
            chapter_tree_view.clear_all()

    generating_preview = False

    def handle_chapter_click(chapter, play_label):
        global playing_sample
        nonlocal generating_preview

        if generating_preview:
            return

        if is_conversion_active():
            # TTS previews share the pipeline cache and the process-global
            # torch device with the conversion worker; running both at once
            # risks the mixed-device crash documented in CLAUDE.md.
            messagebox.showinfo(
                "Preview",
                "Preview is unavailable while a conversion is running.")
            return

        if playing_sample:
            if audio_available:
                pygame.mixer.music.stop()
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
            playing_sample = False
            _cancel_preview_poll()
            play_label.config(text="▶️")
            return

        text = get_limited_text(chapter.extracted_text)
        if not text:
            return

        if not check_speed_range():
            messagebox.showwarning(
                "Warning",
                "Please enter a speed value between 0.5 and 2.0.")
            return
        voice = deemojify_voice(voice_combo.get())
        speed = float(speed_entry.get())

        # Snapshot every Tk-held setting on the main thread; normalize_text
        # itself (lazy spaCy load, seconds on the first call) runs inside
        # the worker below so it doesn't freeze the UI.
        lang = get_language_from_voice(voice)
        subs_snapshot = word_substitutions
        overrides_snapshot = phoneme_overrides
        heteronyms_enabled = pref_heteronyms.get()
        contractions_enabled = pref_contractions.get()
        auto_acronyms_enabled = pref_auto_acronyms.get()
        raw_text = text
        generating_preview = True
        play_label.config(text="...")

        def generate():
            def _finish(label_text=None):
                # Main-thread only: clear the in-flight flag once the UI
                # has actually taken over (playback started or the error
                # was shown). Clearing it in the worker's finally opened a
                # gap where a click saw both flags false and spawned a
                # second generation against the same preview file.
                nonlocal generating_preview
                generating_preview = False
                if label_text is not None:
                    play_label.config(text=label_text)

            try:
                text = normalize_text(raw_text, lang=lang,
                                      substitutions=subs_snapshot,
                                      heteronyms=heteronyms_enabled,
                                      contractions=contractions_enabled,
                                      phoneme_overrides=overrides_snapshot,
                                      auto_acronyms=auto_acronyms_enabled)
                audio_segments = gen_audio_segments(text, voice, speed,
                                                    split_pattern=r"")
                if not audio_segments:
                    root.after(0, lambda: messagebox.showinfo(
                        "Preview", "Nothing to preview in this chapter."))
                    root.after(0, lambda: _finish("▶️"))
                    return
                final_audio = np.concatenate(audio_segments)
                soundfile.write(PREVIEW_FILE, final_audio, 24000)

                def _start_playback():
                    _finish()
                    play_preview(play_label)
                root.after(0, _start_playback)
            except Exception as e:
                # Default-arg capture: Python deletes `e` when the except
                # block exits, so a plain closure raises NameError when the
                # callback runs later on the main thread — the user never
                # saw the preview failure.
                root.after(0, lambda err=e: messagebox.showerror(
                    "Preview Error", f"Failed to generate preview:\n{err}"))
                root.after(0, lambda: _finish("▶️"))

        threading.Thread(target=generate, daemon=True).start()

    preview_after_id = [None]

    def _cancel_preview_poll():
        if preview_after_id[0] is not None:
            try:
                root.after_cancel(preview_after_id[0])
            except Exception:
                pass
            preview_after_id[0] = None

    def play_preview(play_label):
        global playing_sample
        if not audio_available or not Path(PREVIEW_FILE).exists():
            try:
                play_label.config(text="▶️")
            except tk.TclError:
                pass
            return
        _cancel_preview_poll()
        # Touch the widget BEFORE setting playing_sample: if a book load
        # destroyed the tree while the preview was generating, the config
        # raises here — bailing out with the flag still False, instead of
        # leaving it stuck True with nothing playing (which made the next
        # preview click get swallowed by the stop branch).
        try:
            play_label.config(text="⏹️")
        except tk.TclError:
            return
        playing_sample = True
        pygame.mixer.music.load(PREVIEW_FILE)
        pygame.mixer.music.play()

        def check_sound_end():
            preview_after_id[0] = None
            if not playing_sample:
                return
            if not pygame.mixer.music.get_busy():
                on_playback_complete(play_label)
            else:
                preview_after_id[0] = root.after(100, check_sound_end)

        check_sound_end()
    
    current_file_path = ''
    batch_queue = []

    def add_to_batch():
        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select a book file first.")
            return
        # Validate now — a bad value stored on the job would only surface
        # as a cryptic float() error when the batch reaches it.
        if not check_speed_range():
            messagebox.showwarning(
                "Warning",
                "Please enter a speed value between 0.5 and 2.0.")
            return
        if not check_gap_range():
            messagebox.showwarning(
                "Warning",
                "Please enter a chapter gap between 0.0 and 10.0.")
            return

        if not check_chapter_range():
            messagebox.showwarning(
                "Warning",
                "Please enter a starting chapter number between 0 and 99999.")
            return

        selected_indices = []
        for i, chapter in enumerate(chapters):
            if chapter in checkbox_vars and checkbox_vars[chapter].get():
                selected_indices.append(i)
        # No "empty means all" fallback: cli.py hard-errors on an empty
        # selection, and after the Clear All button (commit 9af6e90) an
        # empty selection is a deliberate click, not an accident.
        if not selected_indices:
            messagebox.showwarning("Warning", "No chapters selected.")
            return

        titles = None
        if detect_titles.get():
            selected_chs = [chapters[i] for i in selected_indices]
            if current_file_path.lower().endswith('.pdf'):
                # PDFs carry their titles on the chapter objects; the epub
                # TOC lookup would return '' for every chapter.
                titles = [getattr(ch, 'display_title', None)
                          for ch in selected_chs]
            else:
                titles = get_chapter_titles(book, selected_chs)

        try:
            starting_ch = int(chapter_entry.get())
        except ValueError:
            starting_ch = 1

        total_words = sum(
            len(chapters[i].extracted_text.split())
            for i in selected_indices
        )

        job = BatchJob(
            file_path=current_file_path,
            book=book,
            chapters=list(chapters),
            selected_chapter_indices=selected_indices,
            voice=voice_combo.get(),
            speed=speed_entry.get(),
            chapter_gap=gap_entry.get(),
            gpu_acceleration=gpu_acceleration.get(),
            detect_titles=detect_titles.get(),
            starting_chapter=starting_ch,
            chapter_titles=titles,
            title=title_entry.get().strip() or get_title(book),
            author=author_entry.get().strip() or get_author(book),
            total_words=total_words,
            bitrate=bitrate_combo.get(),
            vbr=use_vbr.get(),
            output_format=format_combo.get(),
            read_title_author=read_title_author_bool.get(),
        )
        batch_queue.append(job)
        messagebox.showinfo(
            "Batch",
            f"Added '{job.title}' to batch queue.\n"
            f"Queue now has {len(batch_queue)} job(s).\n\n"
            f"Open Tools > Batch Queue... to manage and start.")

    def load_book_file(file_path):
        nonlocal last_directory, current_file_path, chapter_tree_view
        global book
        if is_conversion_active():
            messagebox.showwarning(
                "Warning",
                "Cannot load a book while a conversion is running.")
            return
        is_pdf = file_path.lower().endswith('.pdf')

        # Parse BEFORE committing any state: a corrupt file must leave the
        # previously-loaded book fully intact (label, chapters, tree).
        try:
            if is_pdf:
                new_book, chapters_from_book, book_cover = get_pdf_book(
                    file_path, True)
            else:
                new_book, chapters_from_book, book_cover = get_book_cached(
                    file_path, True)
        except Exception as e:
            messagebox.showerror(
                "Error", f"Could not open {Path(file_path).name}:\n{e}")
            return

        # Evict the previous book's cache entry — the cache pins the full
        # parsed EpubBook plus every chapter's text, so without eviction a
        # session that browses many books grows memory monotonically.
        if current_file_path and current_file_path != file_path:
            clear_chapter_cache(current_file_path)

        book = new_book
        current_file_path = file_path
        file_label.config(text=Path(file_path).name)

        book_title = get_title(book)
        book_author = get_author(book)
        title_entry.delete(0, tk.END)
        title_entry.insert(0, book_title)
        author_entry.delete(0, tk.END)
        author_entry.insert(0, book_author)
        if book_cover:
            cover_label.image = book_cover
            cover_label.configure(image=book_cover)
        else:
            cover_label.image = cover_image
            cover_label.configure(image=cover_image)

        # set chapters with display titles
        if detect_titles.get() and not is_pdf:
            titles = get_chapter_titles(book, chapters_from_book)
            for ch, title in zip(chapters_from_book, titles):
                ch.display_title = title or ch.file_name
        elif is_pdf:
            # PDF chapters carry their outline titles; only fill gaps.
            for ch in chapters_from_book:
                if not getattr(ch, 'display_title', None):
                    ch.display_title = ch.file_name
        else:
            # Cached EPUB chapter objects may carry display_titles detected
            # on an earlier load with the setting on — reset unconditionally
            # so toggling "detect titles" off actually takes effect.
            for ch in chapters_from_book:
                ch.display_title = ch.file_name
        chapters.clear()
        chapters.extend(chapters_from_book)

        # Stop any in-flight preview BEFORE destroying the tree that owns
        # its play button: the poll chain's completion callback would
        # otherwise fire against the destroyed widget, and (pre-fix) skip
        # the pygame unload + temp-WAV unlink, wedging previews on Windows.
        global playing_sample
        if playing_sample:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
            playing_sample = False
        _cancel_preview_poll()

        # Replace old tree view
        if chapter_tree_view:
            chapter_tree_view.destroy()

        metadata = {
            'title': book_title,
            'authors': [book_author],
            'cover_image': book_cover,
            'publisher': get_publisher(book),
            'publication_year': get_publication_year(book),
            'description': get_description(book),
        }

        chapter_tree_view = ChapterTreeView(
            container, book, chapters_from_book, metadata,
            on_selection_change=_sync_checkbox_vars_from_tree,
            auto_select=pref_auto_select.get(),
            mark_duplicates=pref_mark_duplicates.get(),
            on_play_preview=(handle_chapter_click
                             if audio_available else None))
        # Always resync — with auto-select off the ChapterTreeView ctor never
        # fires on_selection_change, which would leave checkbox_vars (and the
        # summary footer) holding the PREVIOUS book's chapters.
        _sync_checkbox_vars_from_tree()

        # Remember directory
        last_directory = str(Path(file_path).parent)
        save_config(get_current_config())

    SUPPORTED_EXTENSIONS = ['.epub', '.pdf']

    def select_file():
        ftypes = [
            ('Supported files', ' '.join(f'*{e}' for e in SUPPORTED_EXTENSIONS)),
            ('epub files', '*.epub'),
            ('PDF files', '*.pdf'),
        ]
        file_path = filedialog.askopenfilename(
            title='Select a file',
            initialdir=last_directory or None,
            filetypes=ftypes,
        )
        if file_path:
            load_book_file(file_path)

    def handle_drop(event):
        file_path = event.data.strip('{}')
        ext = Path(file_path).suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            load_book_file(file_path)
        else:
            supported = ', '.join(SUPPORTED_EXTENSIONS)
            messagebox.showwarning("Warning",
                                   f"Please drop a supported file ({supported}).")
    
    cancel_event = threading.Event()

    def convert():
        def enable_controls():
            speed_entry.configure(state='normal')
            # The combo was created readonly; 'normal' would let users type
            # arbitrary text into it after the first conversion.
            voice_combo.configure(state='readonly')
            file_button.configure(state='normal')
            clear_wavs_button.configure(state='normal')
            add_to_batch_button.configure(state='normal')
            cancel_button.configure(state='normal')
            cancel_button.pack_forget()
            start_convert_button.pack(side=tk.RIGHT, padx=5)
            progress['value'] = 0

        def run_conversion(resume=False):
            # Every Tk variable was read on the MAIN thread in convert() and
            # snapshotted into closure locals; this worker never touches
            # widgets, and a book loaded later can't swap state under a
            # running conversion (it keeps book_snapshot/chapters_selected).
            wav_files = []
            all_chapter_m4a_files = []
            encode_futures = {}  # wav_filename -> (Future, m4a_filename)
            encode_executor = ThreadPoolExecutor(max_workers=1)
            conversion_success = False
            try:
                set_gpu_acceleration(gpu_enabled)
                filename = Path(file_path).name
                wav_dir = Path(file_path).parent
                safe = safe_stem(Path(filename).stem, wav_dir)
                title = title_override
                creator = author_override
                if detect_titles_enabled:
                    if file_path.lower().endswith('.pdf'):
                        chapter_titles = [
                            getattr(ch, 'display_title', None)
                            for ch in chapters_selected]
                    else:
                        chapter_titles = get_chapter_titles(
                            book_snapshot, chapters_selected)
                else:
                    chapter_titles = None
                steps = len(chapters_selected) + 1

                # ETA tracking
                word_counts = [len(ch.extracted_text.split())
                               for ch in chapters_selected]
                total_words = sum(word_counts)
                eta_state = {'words_done': 0, 'start_time': time.time(),
                             'current_step': 0,
                             'words_remaining': total_words}
                resumed_indices = set()

                def set_progress(value):
                    root.after(0, lambda v=value: progress.configure(value=v))

                def set_status(text):
                    root.after(0, lambda t=text: progress_label.config(text=t))

                def on_chapter_start(i, total, text, is_resume):
                    if is_resume:
                        resumed_indices.add(i)
                        # Resumed-from-disk and duplicate-reuse chapters
                        # cost ~0 wall clock (engine.py submits/reuses an
                        # encode future with no synthesis) — excluding
                        # their words from the remaining-work total here
                        # keeps the words/sec rate from being computed
                        # against instantaneous "free" progress.
                        eta_state['words_remaining'] -= word_counts[i - 1]
                        set_status(
                            f"Skipping chapter {i} (already converted)")
                        return
                    eta_str = ""
                    elapsed = time.time() - eta_state['start_time']
                    if eta_state['words_done'] > 0 and elapsed > 0:
                        wps = eta_state['words_done'] / elapsed
                        if wps > 0:
                            remaining = eta_state['words_remaining'] / wps
                            if remaining >= 60:
                                eta_str = f" (~{int(remaining / 60)} min remaining)"
                            else:
                                eta_str = f" (~{int(remaining)}s remaining)"
                    set_status(f"Converting chapter {i} of {total}{eta_str}")

                def on_segment_cb(i, seg_count, est_segs):
                    cs = eta_state['current_step']
                    ch_s = (cs / steps) * 100
                    ch_e = ((cs + 1) / steps) * 100
                    frac = min(seg_count / est_segs, 0.95)
                    set_progress(ch_s + frac * (ch_e - ch_s))

                def on_chapter_done(i, duration):
                    if duration is None and i not in resumed_indices:
                        print(f"Chapter {i}: conversion returned no audio",
                              file=sys.stderr)
                    # Resumed/duplicate chapters already had their words
                    # dropped from words_remaining in on_chapter_start;
                    # crediting them to words_done here too would double
                    # count and (re-)poison the words/sec rate.
                    if i not in resumed_indices:
                        eta_state['words_done'] += word_counts[i - 1]
                        eta_state['words_remaining'] -= word_counts[i - 1]
                    eta_state['current_step'] += 1
                    set_progress((eta_state['current_step'] / steps) * 100)

                def on_chapter_error(i, exc):
                    import traceback
                    tb_str = ''.join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__))
                    print(f"Chapter {i} failed: {exc}\nTraceback:\n{tb_str}",
                          file=sys.stderr)
                    root.after(0, lambda err=exc, idx=i: messagebox.showerror(
                        "Conversion Error",
                        f"Chapter {idx} failed:\n{err}"))
                    eta_state['words_done'] += word_counts[i - 1]
                    eta_state['words_remaining'] -= word_counts[i - 1]
                    eta_state['current_step'] += 1
                    set_progress((eta_state['current_step'] / steps) * 100)

                chapter_texts = []
                for i, chapter in enumerate(chapters_selected, start=1):
                    text = chapter.extracted_text
                    if i == 1 and read_title_author_enabled:
                        text = f"{title} by {creator}.\n{text}"
                    chapter_texts.append(text)

                all_chapter_m4a_files = [
                    str(wav_dir / f'{safe}_chapter_{i}_enc{enc_ext}')
                    for i in range(1, len(chapter_texts) + 1)
                ]

                result = convert_chapters_to_wav(
                    chapter_texts, voice, speed, wav_dir,
                    safe, encode_executor,
                    out_format=out_fmt,
                    bitrate=bitrate_value, vbr=vbr_enabled,
                    chapter_gap=chapter_gap,
                    substitutions=subs_snapshot,
                    phoneme_overrides=overrides_snapshot,
                    auto_acronyms=auto_acronyms_enabled,
                    heteronyms=heteronyms_enabled,
                    contractions=contractions_enabled,
                    resume=resume,
                    cancel_check=cancel_event.is_set,
                    on_chapter_start=on_chapter_start,
                    on_segment=on_segment_cb,
                    on_chapter_done=on_chapter_done,
                    on_chapter_error=on_chapter_error)
                wav_files = result['wav_files']
                encode_futures = result['encode_futures']

                if result['cancelled']:
                    set_status("Cancelled")
                    return

                if not wav_files:
                    root.after(0, lambda: messagebox.showerror(
                        "Error", "No chapters were converted."))
                    return

                # Wait for any background encoding still in progress, then
                # collect the encoded paths in wav_files order.
                set_status(f"Creating {out_fmt} file... 0%")
                # Cancel can't interrupt the ffmpeg assembly stage — grey it
                # out so the button doesn't pretend otherwise.
                root.after(0, lambda: cancel_button.configure(
                    state='disabled'))
                def assembly_progress(pct):
                    set_status(f"Creating {out_fmt} file... {pct}%")
                    # `steps` reserves the final 1/steps slot of the bar
                    # for this stage (current_step tops out at steps - 1
                    # after the last chapter) — drive the bar across that
                    # reserved slot instead of leaving it sitting at 100%
                    # for the whole mux, which could run for minutes on a
                    # long book.
                    set_progress(((steps - 1) / steps) * 100
                                + (pct / 100) * (100 / steps))

                if file_path.lower().endswith('.pdf'):
                    cover_image_full = get_pdf_cover_bytes(file_path)
                else:
                    cover_image_full = get_cover_image(book_snapshot, False)
                assemble_output(result, chapter_texts, chapter_titles,
                                safe, wav_dir, out_fmt, output_path,
                                cover_image_full, title, creator,
                                starting_chapter=chapter_num,
                                bitrate=bitrate_value, vbr=vbr_enabled,
                                progress_callback=assembly_progress)
                set_status("Conversion complete")
                conversion_success = True
            except Exception as e:
                root.after(0, lambda err=e: messagebox.showerror(
                    "Error", f"Conversion failed:\n{err}"))
                set_status("Error")
            finally:
                # Wait for any background encoding threads before touching files
                encode_executor.shutdown(wait=True)
                if conversion_success:
                    # Stem-wide sweep: exact current-key paths would orphan
                    # WAVs left by a cancelled run under different settings
                    # (other render_key, other filenames). Cancel/failure
                    # never reach this branch — WAVs are kept for resume.
                    for wav_file in find_chapter_wavs(safe, wav_dir):
                        err = unlink_with_retry(wav_file)
                        if err is not None:
                            print(f"Warning: could not remove {wav_file}: {err}",
                                  file=sys.stderr)
                for m4a_file in all_chapter_m4a_files:
                    err = unlink_with_retry(m4a_file)
                    if err is not None:
                        print(f"Warning: could not remove {m4a_file}: {err}",
                              file=sys.stderr)
                # On cancel/failure, keep wav files for resume.
                # enable_controls is scheduled by _run_with_sleep_prevention's
                # finally — AFTER end_conversion() releases the slot, so the
                # Convert button can't reappear while the slot is still held
                # (clicking in that window hit a spurious "already running"
                # warning).
                cancel_event.clear()

        if is_conversion_active():
            messagebox.showwarning(
                "Warning",
                "A conversion is already running (here or in the batch "
                "queue). Wait for it to finish first.")
            return

        if generating_preview:
            # The preview thread is inside the shared TTS pipeline; the
            # worker's set_gpu_acceleration() flips the process-global
            # torch device under it.
            messagebox.showwarning(
                "Warning",
                "Wait for the chapter preview to finish generating first.")
            return

        if not check_speed_range():
            messagebox.showwarning("Warning",
                                   "Please enter a speed value between 0.5 and 2.0.")
            return

        if not check_gap_range():
            messagebox.showwarning(
                "Warning",
                "Please enter a chapter gap between 0.0 and 10.0.")
            return

        if not check_chapter_range():
            messagebox.showwarning(
                "Warning",
                "Please enter a starting chapter number between 0 and 99999.")
            return

        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select a book file first.")
            return

        # No "empty selection means convert everything" fallback here:
        # cli.py hard-errors on an empty selection, and after the Clear All
        # button (commit 9af6e90) an empty selection is a deliberate click,
        # not an accident. `chapters` (the full book list) is checked here
        # instead of checkbox_vars, which only ever holds TICKED chapters —
        # an empty selection must fall through to the "No chapters
        # selected." guard below, not read as "book has zero chapters".
        if not chapters:
            messagebox.showwarning("Warning",
                                   "No chapters available to convert.")
            return

        # Checked before the save dialog so a Clear-All-then-Convert click
        # is told "no chapters selected" immediately, instead of first being
        # walked through asksaveasfilename (and having last_output_directory
        # written to config) only to be rejected afterward.
        chapters_selected = [ch for ch, var in checkbox_vars.items()
                             if var.get()]
        if not chapters_selected:
            messagebox.showwarning("Warning", "No chapters selected.")
            return

        nonlocal last_output_directory
        file_path = current_file_path
        save_initialdir = (last_output_directory
                           or last_directory
                           or str(Path(file_path).parent))
        fmt = format_combo.get()
        fmt_info = OUTPUT_FORMATS[fmt]
        output_path = filedialog.asksaveasfilename(
            title='Save audiobook as',
            initialdir=save_initialdir,
            initialfile=Path(file_path).stem + fmt_info['ext'],
            filetypes=[(fmt_info['desc'], '*' + fmt_info['ext'])],
            defaultextension=fmt_info['ext'])
        if not output_path:
            return
        last_output_directory = str(Path(output_path).parent)
        save_config(get_current_config())

        title_override = title_entry.get().strip() or get_title(book)
        author_override = author_entry.get().strip() or get_author(book)

        voice = deemojify_voice(voice_combo.get())
        speed = speed_entry.get()

        # Snapshot every Tk-held setting plus the book reference on the main
        # thread. The conversion worker reads only these locals — widgets
        # stay main-thread-only, and loading another book mid-run can't
        # corrupt the conversion in flight.
        try:
            chapter_num = int(chapter_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid chapter number.")
            return
        try:
            chapter_gap = float(gap_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid chapter gap value.")
            return
        book_snapshot = book
        out_fmt = fmt
        enc_ext = '.m4a' if out_fmt == 'm4b' else fmt_info['ext']
        bitrate_value = bitrate_combo.get()
        vbr_enabled = use_vbr.get()
        gpu_enabled = gpu_acceleration.get()
        detect_titles_enabled = detect_titles.get()
        read_title_author_enabled = read_title_author_bool.get()
        heteronyms_enabled = pref_heteronyms.get()
        contractions_enabled = pref_contractions.get()
        auto_acronyms_enabled = pref_auto_acronyms.get()
        subs_snapshot = [dict(s) for s in word_substitutions]
        overrides_snapshot = [dict(o) for o in phoneme_overrides]
        rkey = render_key(voice, speed, chapter_gap,
                          heteronyms_enabled, contractions_enabled,
                          auto_acronyms_enabled,
                          subs_snapshot, overrides_snapshot)

        # Check for existing wav files from a previous run. Build the same
        # chapter_texts AND render_key that run_conversion will use so the
        # hash-based wav filenames line up — otherwise the resume prompt
        # would look at the wrong paths, miss cached audio, or offer to
        # resume a cache the conversion then can't hit.
        resume = False
        filename = Path(file_path).name
        wav_dir = Path(file_path).parent
        resume_stem = safe_stem(Path(filename).stem, wav_dir)
        resume_chapter_texts = []
        for _i, _ch in enumerate(chapters_selected, start=1):
            _text = _ch.extracted_text
            if _i == 1 and read_title_author_enabled:
                _text = f"{title_override} by {author_override}.\n{_text}"
            resume_chapter_texts.append(_text)
        existing_wavs = [
            chapter_wav_name(resume_stem, t, wav_dir, rkey)
            for t in resume_chapter_texts
            if Path(chapter_wav_name(resume_stem, t, wav_dir, rkey)).exists()
        ]
        if existing_wavs:
            answer = messagebox.askyesnocancel(
                "Previous conversion found",
                f"{len(existing_wavs)} chapter(s) already converted.\n\n"
                "Yes = Resume (skip converted chapters)\n"
                "No = Start fresh (reconvert all)")
            if answer is None:
                return  # Cancel
            if answer:
                resume = True

        if not try_begin_conversion():
            messagebox.showwarning(
                "Warning",
                "A conversion is already running (here or in the batch "
                "queue). Wait for it to finish first.")
            return
        # Everything between the slot claim above and the worker spawn runs
        # under try/except that releases the slot: only the worker's finally
        # calls end_conversion(), so an exception here (most plausibly a
        # PermissionError from a transiently-held WAV below) would otherwise
        # leave the lock held for the rest of the session — every later
        # Convert / Start Batch / book load refuses with "already running".
        try:
            # Start-fresh deletion happens only after the slot is claimed,
            # so the WAVs being deleted can't belong to a conversion that
            # started while the resume dialog was open. unlink_with_retry
            # absorbs the Windows transient-handle race (AV scanner, player
            # still closing) that a bare unlink turns into a crash.
            if existing_wavs and not resume:
                # Start fresh clears the book's WAVs stem-wide — declining
                # resume must also drop cache rendered under OTHER settings
                # (other render_key, other filenames), not just the
                # current-key files the prompt counted.
                for wav in find_chapter_wavs(resume_stem, wav_dir):
                    err = unlink_with_retry(wav)
                    if err is not None:
                        raise RuntimeError(
                            f'Could not delete cached WAV {wav}: {err}')
            speed_entry.configure(state='disabled')
            voice_combo.configure(state='disabled')
            file_button.configure(state='disabled')
            clear_wavs_button.configure(state='disabled')
            add_to_batch_button.configure(state='disabled')
            start_convert_button.pack_forget()
            cancel_button.pack(side=tk.RIGHT, padx=5)
            cancel_event.clear()
            def _run_with_sleep_prevention():
                try:
                    with prevent_sleep():
                        run_conversion(resume)
                except BaseException as e:
                    import traceback
                    print(f"Conversion thread crashed: {e}", file=sys.stderr)
                    traceback.print_exc()
                    root.after(0, lambda err=str(e): messagebox.showerror(
                        "Error", f"Conversion thread crashed:\n\n{err}"))
                finally:
                    end_conversion()
                    root.after(0, enable_controls)
            threading.Thread(target=_run_with_sleep_prevention,
                             daemon=True).start()
        except Exception as e:
            end_conversion()
            enable_controls()
            messagebox.showerror(
                "Error", f"Could not start conversion:\n\n{e}")
            return

    def cancel_conversion():
        cancel_event.set()
        progress_label.config(text="Cancelling...")

    def clear_cached_wavs():
        if is_conversion_active():
            # The button is disabled during a GUI conversion, but a BATCH
            # run leaves the main window live — deleting WAVs mid-job
            # yanks files the worker is about to encode.
            messagebox.showwarning(
                "Warning",
                "Cannot clear WAVs while a conversion is running.")
            return
        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select a book file first.")
            return
        wav_dir = Path(current_file_path).parent
        stem = safe_stem(Path(current_file_path).stem, wav_dir)
        # find_chapter_wavs, never glob: the stem is user-derived, and a
        # bracketed book name ('The Hobbit [Illustrated]') is a character
        # class to glob — matching nothing here, or another book's files.
        wavs = find_chapter_wavs(stem, wav_dir)
        if not wavs:
            messagebox.showinfo("Clear WAVs", "No cached WAV files found.")
            return
        if messagebox.askyesno("Clear WAVs",
                               f"Delete {len(wavs)} cached WAV file(s) for "
                               f"'{stem}'?"):
            errors = []
            for wav in wavs:
                err = unlink_with_retry(wav)
                if err is not None:
                    errors.append(f'{wav}: {err}')
            if errors:
                messagebox.showwarning(
                    "Clear WAVs",
                    "Some files could not be deleted:\n" +
                    "\n".join(errors))
            else:
                messagebox.showinfo("Clear WAVs",
                                    f"Deleted {len(wavs)} file(s).")

    file_frame = tk.Frame(book_frame)
    file_frame.grid(row=0, column=1, pady=5, padx=10)

    file_button = ttk.Button(
        file_frame,
        text='Select book file',
        command=select_file,
    )
    file_button.grid(row=0, column=0, columnspan=2, pady=5)

    file_label = tk.Label(file_frame, text="")
    file_label.grid(row=1, column=0, columnspan=2, pady=5)
    # Bound ONCE with a callable — rebinding per book load stacked a new
    # tooltip handler (and Toplevel) for every file ever opened.
    add_tooltip(file_label, lambda: current_file_path or 'No file selected')

    tk.Label(file_frame, text="Title:").grid(
        row=2, column=0, sticky='e', padx=(0, 6), pady=4)
    title_entry = ttk.Entry(file_frame, width=35)
    title_entry.grid(row=2, column=1, sticky='ew', pady=4)

    tk.Label(file_frame, text="Author:").grid(
        row=3, column=0, sticky='e', padx=(0, 6), pady=4)
    author_entry = ttk.Entry(file_frame, width=35)
    author_entry.grid(row=3, column=1, sticky='ew', pady=4)

    tk.Checkbutton(
        file_frame,
        text="Read title & author",
        variable=read_title_author_bool
    ).grid(row=4, column=1, sticky='w', pady=2)

    file_frame.columnconfigure(1, weight=1)

    cover_label.image = cover_image  # Keep a reference to prevent GC
    cover_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")

    book_frame.pack(pady=5, fill=tk.X)

    ttk.Separator(root, orient='horizontal').pack(fill='x', padx=5, pady=2)

    # --- Bottom controls (packed BEFORE chapter list so they never get cut off) ---
    bottom_frame = tk.Frame(root)
    bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

    ttk.Separator(root, orient='horizontal').pack(fill='x', padx=5, side=tk.BOTTOM)

    summary_label = ttk.Label(bottom_frame, text='', style='Summary.TLabel',
                              anchor='w')
    summary_label.pack(fill=tk.X, padx=5, pady=(2, 0))

    # Button row: Select All, Clear All, Starting Chapter, Convert/Cancel
    button_row = tk.Frame(bottom_frame)
    button_row.pack(fill=tk.X, pady=5)

    select_all_button = ttk.Button(
        button_row,
        text='Select All',
        command=select_all,
    )
    select_all_button.pack(side=tk.LEFT, padx=5)

    clear_all_button = ttk.Button(
        button_row,
        text='Clear All',
        command=clear_all,
    )
    clear_all_button.pack(side=tk.LEFT, padx=5)

    clear_wavs_button = ttk.Button(
        button_row,
        text='Clear WAVs',
        command=clear_cached_wavs,
    )
    clear_wavs_button.pack(side=tk.LEFT, padx=5)

    cancel_button = ttk.Button(
        button_row,
        text='Cancel',
        command=cancel_conversion,
        style='Cancel.TButton',
    )

    start_convert_button = ttk.Button(
        button_row,
        text='Convert book',
        command=convert,
    )
    start_convert_button.pack(side=tk.RIGHT, padx=5)

    add_to_batch_button = ttk.Button(
        button_row,
        text='Add to Batch',
        command=add_to_batch,
    )
    add_to_batch_button.pack(side=tk.RIGHT, padx=5)

    # Progress row
    progress_frame = tk.Frame(bottom_frame)
    progress_frame.pack(fill=tk.X, pady=(0, 5))
    progress_frame.grid_columnconfigure(0, weight=1)

    progress = ttk.Progressbar(progress_frame, orient="horizontal",
                               mode="determinate")
    progress.grid(row=0, column=0, padx=5, sticky="ew")

    progress_label = tk.Label(progress_frame, text="---")
    progress_label.grid(row=0, column=1, padx=5)

    # --- Chapter list (expands to fill remaining space) ---
    container = tk.Frame(root)
    container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    chapter_tree_view = None
    checkbox_vars = {}

    def _sync_checkbox_vars_from_tree():
        checkbox_vars.clear()
        if chapter_tree_view:
            for ch in chapter_tree_view.get_selected_chapters():
                checkbox_vars[ch] = tk.BooleanVar(value=True)
        update_summary()

    def update_summary(*_):
        if not checkbox_vars:
            summary_label.config(text='')
            return
        selected = [(ch, var) for ch, var in checkbox_vars.items() if var.get()]
        n = len(selected)
        if n == 0:
            summary_label.config(text='0 chapters selected')
            return
        words = sum(len(ch.extracted_text.split()) for ch, var in selected)
        try:
            spd = float(speed_entry.get())
            if spd <= 0:
                spd = 1.0
        except ValueError:
            spd = 1.0
        dur = format_duration_estimate(words, spd)
        summary_label.config(
            text=f'{n} chapters selected · {words:,} words · {dur}')

    speed_entry.bind('<KeyRelease>',
                     lambda e: (check_speed_range(e), update_summary()), add=True)

    # Register drag-and-drop if available
    if HAS_DND:
        root.drop_target_register(DND_FILES)
        root.dnd_bind('<<Drop>>', handle_drop)

    # start main loop
    root.mainloop()


def on_playback_complete(play_label):
    global playing_sample
    playing_sample = False
    # Release the pygame handle and delete the temp WAV BEFORE touching the
    # label: the label lives in the ChapterTreeView, which load_book_file
    # destroys — a TclError on the config call used to skip unload/unlink,
    # leaving the preview WAV locked on Windows for the rest of the session.
    try:
        # unload() exists only on pygame >= 2.0 — same guard as the other
        # two call sites.
        pygame.mixer.music.unload()
    except Exception:
        pass
    try:
        Path(PREVIEW_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        play_label.config(text="▶️")
    except tk.TclError:
        pass  # widget destroyed by a book load mid-preview; cleanup done


def main():
    # Kept for backward compatibility with console scripts generated by
    # older installs; the real dispatcher lives in entry.py so CLI runs
    # never pay this module's tkinter/PIL/pygame imports.
    from .entry import main as _main
    _main()


if __name__ == "__main__":
    main()
