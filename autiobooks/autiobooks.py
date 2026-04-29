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
from .engine import create_m4b, encode_chapter_to_m4a
from .engine import concat_audio_files, unlink_with_retry
from .engine import convert_chapters_to_wav
from .engine import safe_stem, chapter_wav_name
from .runtime import ensure_cuda
from .epub_parser import (
    get_book, get_book_cached, get_title, get_author, get_cover_image,
    get_chapter_titles, get_publisher, get_publication_year, get_description,
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
from .pdf_parser import get_pdf_book
from .text_processing import normalize_text
from .config import load_config, save_config
import pygame.mixer
import soundfile
import numpy as np
import shutil
import tempfile
import os
from .voices_lang import voices, voices_emojified, deemojify_voice, get_language_from_voice

PREVIEW_FILE = os.path.join(tempfile.gettempdir(), "autiobooks_preview.wav")

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


import contextlib
import subprocess as _subprocess

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
        tk.Label(tip, text=text, background=colors['tooltip_bg'],
                 foreground=colors['tooltip_fg'], relief="solid",
                 borderwidth=1, font=('Arial', 10)).pack()

    def hide_tip(event):
        nonlocal tip
        if tip:
            tip.destroy()
            tip = None

    widget.bind("<Enter>", show_tip, add=True)
    widget.bind("<Leave>", hide_tip, add=True)


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
    if sys.platform == 'win32':
        from .runtime import ensure_ffmpeg
        if not ensure_ffmpeg(root):
            exit(1)
    elif not shutil.which('ffmpeg'):
        messagebox.showwarning("Warning",
                               "ffmpeg not found. Please install ffmpeg to" +
                               " create m4b audiobook files.")
        exit(1)

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

    menubar = tk.Menu(root)
    tools_menu = tk.Menu(menubar, tearoff=0)
    tools_menu.add_command(label='Append M4B files...', command=show_append_dialog)
    tools_menu.add_command(label='Batch Queue...', command=lambda: show_batch_window())
    tools_menu.add_command(label='Word Substitutions...',
                           command=show_substitutions_dialog)
    tools_menu.add_command(label='Pronunciation Overrides...',
                           command=show_phoneme_overrides_dialog)
    if platform.system() == "Windows":
        from .runtime import check_nvidia_gpu
        if check_nvidia_gpu():
            from .runtime import download_cuda_from_menu
            tools_menu.add_command(label='Download CUDA Support...',
                                   command=lambda: download_cuda_from_menu(root, gpu_acceleration))
    menubar.add_cascade(label='Tools', menu=tools_menu)

    theme_var = tk.StringVar(value='light')
    pref_heteronyms = tk.BooleanVar(value=True)
    pref_contractions = tk.BooleanVar(value=True)
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
        detect_titles.set(config['detect_titles'])
    if config.get('bitrate') in ('64k', '128k', '192k'):
        bitrate_combo.set(config['bitrate'])
    if config.get('vbr'):
        use_vbr.set(True)
    if config.get('output_format') in OUTPUT_FORMATS:
        format_combo.set(config['output_format'])
        on_format_changed()
    if 'read_title_author' in config:
        read_title_author_bool.set(config['read_title_author'])
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
    word_substitutions = config.get('word_substitutions', [])
    phoneme_overrides = config.get('phoneme_overrides', [])
    if config.get('theme') in THEMES:
        theme_var.set(config['theme'])
        apply_theme(config['theme'])
    if 'heteronyms' in config:
        pref_heteronyms.set(config['heteronyms'])
    if 'contractions' in config:
        pref_contractions.set(config['contractions'])
    if 'auto_select' in config:
        pref_auto_select.set(config['auto_select'])
    if 'mark_duplicates' in config:
        pref_mark_duplicates.set(config['mark_duplicates'])
    if 'auto_acronyms' in config:
        pref_auto_acronyms.set(config['auto_acronyms'])

    def get_current_config():
        return {
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
        }

    def on_close():
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
        )

    ttk.Separator(root, orient='horizontal').pack(fill='x', padx=5, pady=2)

    audio_available = False
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

        voice = deemojify_voice(voice_combo.get())
        speed = float(speed_entry.get())

        text = normalize_text(text, lang=get_language_from_voice(voice),
                              substitutions=word_substitutions,
                              heteronyms=pref_heteronyms.get(),
                              contractions=pref_contractions.get(),
                              phoneme_overrides=phoneme_overrides,
                              auto_acronyms=pref_auto_acronyms.get())
        generating_preview = True
        play_label.config(text="...")

        def generate():
            nonlocal generating_preview
            try:
                audio_segments = gen_audio_segments(text, voice, speed,
                                                    split_pattern=r"")
                final_audio = np.concatenate(audio_segments)
                soundfile.write(PREVIEW_FILE, final_audio, 24000)
                root.after(0, lambda: play_preview(play_label))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror(
                    "Preview Error", f"Failed to generate preview:\n{e}"))
                root.after(0, lambda: play_label.config(text="▶️"))
            finally:
                generating_preview = False

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
            play_label.config(text="▶️")
            return
        _cancel_preview_poll()
        playing_sample = True
        play_label.config(text="⏹️")
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

        selected_indices = []
        for i, chapter in enumerate(chapters):
            if chapter in checkbox_vars and checkbox_vars[chapter].get():
                selected_indices.append(i)
        if not selected_indices:
            selected_indices = list(range(len(chapters)))

        titles = None
        if detect_titles.get():
            selected_chs = [chapters[i] for i in selected_indices]
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
        current_file_path = file_path
        file_label.config(text=Path(file_path).name)
        add_tooltip(file_label, file_path)
        global book
        is_pdf = file_path.lower().endswith('.pdf')

        if is_pdf:
            book, chapters_from_book, book_cover = get_pdf_book(
                file_path, True)
        else:
            book, chapters_from_book, book_cover = get_book_cached(
                file_path, True)

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
        else:
            for ch in chapters_from_book:
                if not getattr(ch, 'display_title', None):
                    ch.display_title = ch.file_name
        chapters.clear()
        chapters.extend(chapters_from_book)

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
            voice_combo.configure(state='normal')
            cancel_button.pack_forget()
            start_convert_button.pack(side=tk.RIGHT, padx=5)
            progress['value'] = 0

        def run_conversion(resume=False):
            wav_files = []
            all_chapter_wav_files = []
            all_chapter_m4a_files = []
            encode_futures = {}  # wav_filename -> (Future, m4a_filename)
            encode_executor = ThreadPoolExecutor(max_workers=1)
            conversion_success = False
            try:
                chapters_selected = [chapter
                                     for chapter, var in checkbox_vars.items()
                                     if var.get()]
                if not chapters_selected:
                    if chapter_tree_view:
                        chapter_tree_view.select_all()
                    chapters_selected = list(checkbox_vars.keys())
                set_gpu_acceleration(gpu_acceleration.get())
                filename = Path(file_path).name
                wav_dir = Path(file_path).parent
                safe = safe_stem(Path(filename).stem, wav_dir)
                out_fmt = format_combo.get()
                enc_ext = '.m4a' if out_fmt == 'm4b' else fmt_info['ext']
                try:
                    chapter_num = int(chapter_entry.get())
                except ValueError:
                    root.after(0, lambda: messagebox.showerror(
                        "Error", "Invalid chapter number."))
                    return
                title = title_override
                creator = author_override
                if detect_titles.get():
                    if file_path.lower().endswith('.pdf'):
                        chapter_titles = [
                            getattr(ch, 'display_title', None)
                            for ch in chapters_selected]
                    else:
                        chapter_titles = get_chapter_titles(
                            book, chapters_selected)
                else:
                    chapter_titles = None
                try:
                    chapter_gap = float(gap_entry.get())
                except ValueError:
                    root.after(0, lambda: messagebox.showerror(
                        "Error", "Invalid chapter gap value."))
                    return
                steps = len(chapters_selected) + 1

                # ETA tracking
                word_counts = [len(ch.extracted_text.split())
                               for ch in chapters_selected]
                total_words = sum(word_counts)
                eta_state = {'words_done': 0, 'start_time': time.time(),
                             'current_step': 1}
                resumed_indices = set()

                def set_progress(value):
                    root.after(0, lambda v=value: progress.configure(value=v))

                def set_status(text):
                    root.after(0, lambda t=text: progress_label.config(text=t))

                def on_chapter_start(i, total, text, is_resume):
                    if is_resume:
                        resumed_indices.add(i)
                        set_status(
                            f"Skipping chapter {i} (already converted)")
                        return
                    eta_str = ""
                    elapsed = time.time() - eta_state['start_time']
                    if eta_state['words_done'] > 0 and elapsed > 0:
                        wps = eta_state['words_done'] / elapsed
                        if wps > 0:
                            remaining = (
                                (total_words - eta_state['words_done']) / wps)
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
                    eta_state['words_done'] += word_counts[i - 1]
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
                    eta_state['current_step'] += 1
                    set_progress((eta_state['current_step'] / steps) * 100)

                chapter_texts = []
                for i, chapter in enumerate(chapters_selected, start=1):
                    text = chapter.extracted_text
                    if i == 1 and read_title_author_bool.get():
                        text = f"{title} by {creator}.\n{text}"
                    chapter_texts.append(text)

                all_chapter_wav_files = [
                    chapter_wav_name(safe, t, wav_dir)
                    for t in chapter_texts
                ]
                all_chapter_m4a_files = [
                    str(wav_dir / f'{safe}_chapter_{i}_enc{enc_ext}')
                    for i in range(1, len(chapter_texts) + 1)
                ]

                result = convert_chapters_to_wav(
                    chapter_texts, voice, speed, wav_dir,
                    safe, encode_executor,
                    out_format=out_fmt,
                    bitrate=bitrate_combo.get(), vbr=use_vbr.get(),
                    chapter_gap=chapter_gap,
                    substitutions=word_substitutions,
                    phoneme_overrides=phoneme_overrides,
                    auto_acronyms=pref_auto_acronyms.get(),
                    heteronyms=pref_heteronyms.get(),
                    contractions=pref_contractions.get(),
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
                encoded_files = []
                for wav_name in wav_files:
                    future, enc_name = encode_futures[wav_name]
                    future.result()
                    encoded_files.append(enc_name)

                def assembly_progress(pct):
                    set_status(f"Creating {out_fmt} file... {pct}%")

                if out_fmt == 'm4b':
                    converted_titles = []
                    for i, chapter in enumerate(chapters_selected):
                        wav_name = chapter_wav_name(
                            safe, chapter_texts[i], wav_dir)
                        if wav_name in wav_files:
                            if chapter_titles is not None:
                                converted_titles.append(chapter_titles[i])
                    if file_path.lower().endswith('.pdf'):
                        cover_image_full = None
                    else:
                        cover_image_full = get_cover_image(book, False)
                    create_m4b(encoded_files, output_path, cover_image_full,
                               title, creator, chapter_num,
                               converted_titles or None,
                               progress_callback=assembly_progress,
                               preencoded=True,
                               bitrate=bitrate_combo.get(),
                               vbr=use_vbr.get())
                else:
                    concat_audio_files(encoded_files, output_path,
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
                    for wav_file in all_chapter_wav_files:
                        err = unlink_with_retry(wav_file)
                        if err is not None:
                            print(f"Warning: could not remove {wav_file}: {err}",
                                  file=sys.stderr)
                for m4a_file in all_chapter_m4a_files:
                    err = unlink_with_retry(m4a_file)
                    if err is not None:
                        print(f"Warning: could not remove {m4a_file}: {err}",
                              file=sys.stderr)
                # On cancel/failure, keep wav files for resume
                cancel_event.clear()
                root.after(0, enable_controls)

        if not check_speed_range():
            messagebox.showwarning("Warning",
                                   "Please enter a speed value between 0.5 and 2.0.")
            return

        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select a book file first.")
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

        # Check for existing wav files from a previous run. Build the same
        # chapter_texts that run_conversion will build so the hash-based
        # wav filenames line up — otherwise the resume prompt would look
        # at the wrong paths and miss cached audio.
        resume = False
        filename = Path(file_path).name
        wav_dir = Path(file_path).parent
        resume_stem = safe_stem(Path(filename).stem, wav_dir)
        chapters_to_check = [ch for ch, var in checkbox_vars.items()
                             if var.get()] or list(checkbox_vars.keys())
        resume_chapter_texts = []
        for _i, _ch in enumerate(chapters_to_check, start=1):
            _text = _ch.extracted_text
            if _i == 1 and read_title_author_bool.get():
                _text = f"{title_override} by {author_override}.\n{_text}"
            resume_chapter_texts.append(_text)
        existing_wavs = [
            chapter_wav_name(resume_stem, t, wav_dir)
            for t in resume_chapter_texts
            if Path(chapter_wav_name(resume_stem, t, wav_dir)).exists()
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
            else:
                for wav in existing_wavs:
                    Path(wav).unlink(missing_ok=True)

        speed_entry.configure(state='disabled')
        voice_combo.configure(state='disabled')
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
                root.after(0, enable_controls)
        threading.Thread(target=_run_with_sleep_prevention,
                         daemon=True).start()

    def cancel_conversion():
        cancel_event.set()
        progress_label.config(text="Cancelling...")

    def clear_cached_wavs():
        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select a book file first.")
            return
        wav_dir = Path(current_file_path).parent
        stem = safe_stem(Path(current_file_path).stem, wav_dir)
        wavs = sorted(wav_dir.glob(f'{stem}_chapter_*.wav'))
        if not wavs:
            messagebox.showinfo("Clear WAVs", "No cached WAV files found.")
            return
        if messagebox.askyesno("Clear WAVs",
                               f"Delete {len(wavs)} cached WAV file(s) for "
                               f"'{stem}'?"):
            for wav in wavs:
                wav.unlink(missing_ok=True)
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
        secs = words / (150 * spd)
        if secs < 60:
            dur = '~<1 min'
        elif secs < 3600:
            dur = f'~{int(secs / 60)} min'
        else:
            h = int(secs / 3600)
            m = int((secs % 3600) / 60)
            dur = f'~{h} hr {m} min'
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
    play_label.config(text="▶️")
    pygame.mixer.music.unload()
    Path(PREVIEW_FILE).unlink(missing_ok=True)


_CLI_COMMANDS = {'convert', 'list-chapters', 'list-voices'}


def main():
    if len(sys.argv) > 1 and sys.argv[1] in _CLI_COMMANDS:
        from .cli import main as cli_main
        cli_main()
    elif len(sys.argv) == 2 and sys.argv[1] == '--help':
        readme = Path(__file__).parent.parent / 'README.md'
        if readme.exists():
            print(readme.read_text())
        else:
            print("Autiobooks: convert epub files to m4b audiobooks.\n"
                  "Run without arguments to launch the GUI.\n"
                  "CLI commands: convert, list-chapters, list-voices")
    else:
        start_gui()


if __name__ == "__main__":
    main()
