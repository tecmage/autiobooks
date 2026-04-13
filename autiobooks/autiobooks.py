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
from .engine import create_m4b, encode_chapter_to_m4a, encode_chapter
from .engine import concat_audio_files, append_m4b
from .engine import probe_duration, _probe_chapters
from .runtime import ensure_cuda
from .epub_parser import get_book, get_book_cached, get_title, get_author, get_cover_image, get_chapter_titles
from .chapter_tree import ChapterTreeView
try:
    from .pdf_parser import get_pdf_book, HAS_PDF
except ImportError:
    HAS_PDF = False
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
    """Prevent the OS from sleeping during long conversions."""
    _system = platform.system()
    _proc = None
    try:
        if _system == 'Windows':
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        elif _system == 'Darwin':
            _proc = _subprocess.Popen(
                ['caffeinate', '-i'], stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL)
        elif _system == 'Linux':
            _proc = _subprocess.Popen(
                ['systemd-inhibit', '--what=idle', '--who=Autiobooks',
                 '--why=Converting audiobook', 'sleep', 'infinity'],
                stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        yield
    except (OSError, FileNotFoundError):
        yield
    finally:
        if _system == 'Windows':
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
        if _proc is not None:
            _proc.terminate()
            _proc.wait()

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
    read_title_author: bool = True
    status: str = "Queued"


def add_tooltip(widget, text):
    tip = None

    def show_tip(event):
        nonlocal tip
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=text, background="#ffffe0", foreground="#000000",
                 relief="solid", borderwidth=1, font=('Arial', 10)).pack()

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

    THEMES = {
        'light': {
            'bg': '#f0f0f0', 'fg': '#000000', 'entry_bg': '#ffffff',
            'entry_fg': '#000000', 'select_bg': '#0078d7',
            'select_fg': '#ffffff', 'frame_bg': '#f0f0f0',
            'label_fg': '#333333', 'summary_fg': '#555555',
            'preview_fg': '#666666',
        },
        'dark': {
            'bg': '#2b2b2b', 'fg': '#e0e0e0', 'entry_bg': '#3c3c3c',
            'entry_fg': '#e0e0e0', 'select_bg': '#264f78',
            'select_fg': '#ffffff', 'frame_bg': '#2b2b2b',
            'label_fg': '#cccccc', 'summary_fg': '#aaaaaa',
            'preview_fg': '#888888',
        },
    }

    def apply_theme(theme_name):
        if theme_name not in THEMES:
            return
        t = THEMES[theme_name]
        root.configure(bg=t['bg'])

        root.option_add('*Label.Background', t['bg'])
        root.option_add('*Label.Foreground', t['fg'])
        root.option_add('*Checkbutton.Background', t['bg'])
        root.option_add('*Checkbutton.Foreground', t['fg'])
        root.option_add('*Checkbutton.activeBackground', t['bg'])
        root.option_add('*Checkbutton.selectColor', t['entry_bg'])
        root.option_add('*Radiobutton.Background', t['bg'])
        root.option_add('*Radiobutton.Foreground', t['fg'])
        root.option_add('*Radiobutton.activeBackground', t['bg'])
        root.option_add('*Radiobutton.selectColor', t['entry_bg'])
        root.option_add('*Toplevel.Background', t['bg'])
        root.option_add('*Menu.Background', t['bg'])
        root.option_add('*Menu.Foreground', t['fg'])
        root.option_add('*Menu.activeBackground', t['select_bg'])
        root.option_add('*Menu.activeForeground', t['select_fg'])

        style = ttk.Style()
        style.configure('TFrame', background=t['bg'])
        style.configure('TLabel', background=t['bg'], foreground=t['fg'])
        style.configure('TButton', background=t['bg'], foreground=t['fg'])
        style.map('TButton',
                  foreground=[('disabled', t['summary_fg'])],
                  background=[('active', t['select_bg'])])
        style.configure('TLabelframe', background=t['bg'])
        style.configure('TLabelframe.Label', background=t['bg'],
                        foreground=t['fg'])
        style.configure('TCombobox', fieldbackground=t['entry_bg'],
                        foreground=t['entry_fg'], background=t['bg'],
                        arrowcolor=t['fg'])
        style.map('TCombobox',
                  fieldbackground=[('readonly', t['entry_bg'])],
                  foreground=[('readonly', t['entry_fg'])],
                  arrowcolor=[('disabled', t['summary_fg'])])
        root.option_add('*TCombobox*Listbox.background', t['entry_bg'])
        root.option_add('*TCombobox*Listbox.foreground', t['entry_fg'])
        root.option_add('*TCombobox*Listbox.selectBackground', t['select_bg'])
        root.option_add('*TCombobox*Listbox.selectForeground', t['select_fg'])
        style.configure('TPanedwindow', background=t['bg'])
        style.configure('Treeview', background=t['entry_bg'],
                        foreground=t['entry_fg'], fieldbackground=t['entry_bg'])
        style.map('Treeview', background=[('selected', t['select_bg'])],
                  foreground=[('selected', t['select_fg'])])

        def apply_to_widget(w):
            try:
                if isinstance(w, (tk.Frame, tk.Canvas, tk.Toplevel)):
                    w.configure(bg=t['bg'])
                elif isinstance(w, tk.Label):
                    w.configure(bg=t['bg'], fg=t['fg'])
                elif isinstance(w, tk.Checkbutton):
                    w.configure(bg=t['bg'], fg=t['fg'],
                                activebackground=t['bg'],
                                selectcolor=t['entry_bg'])
                elif isinstance(w, tk.Radiobutton):
                    w.configure(bg=t['bg'], fg=t['fg'],
                                activebackground=t['bg'],
                                activeforeground=t['fg'],
                                selectcolor=t['entry_bg'])
                elif isinstance(w, tk.Button):
                    w.configure(bg=t['bg'], fg=t['fg'],
                                activebackground=t['select_bg'],
                                activeforeground=t['select_fg'])
                elif isinstance(w, (tk.Entry, tk.Text)):
                    w.configure(bg=t['entry_bg'], fg=t['entry_fg'],
                                insertbackground=t['fg'])
                elif isinstance(w, tk.Menu):
                    w.configure(bg=t['bg'], fg=t['fg'],
                                activebackground=t['select_bg'],
                                activeforeground=t['select_fg'])
            except tk.TclError:
                pass
            for child in w.winfo_children():
                apply_to_widget(child)

        root.after(0, lambda: apply_to_widget(root))

    def show_append_dialog():
        dialog = tk.Toplevel(root)
        dialog.title('Append M4B Files')
        dialog.geometry('700x250')
        dialog.resizable(False, False)
        dialog.grab_set()

        for row, label in enumerate(['Base file:', 'Append file:', 'Output file:']):
            tk.Label(dialog, text=label).grid(row=row * 2, column=0, sticky='e',
                                              padx=10, pady=(6, 0))
        base_var = tk.StringVar()
        append_var = tk.StringVar()
        output_var = tk.StringVar()

        for row, var in enumerate([base_var, append_var, output_var]):
            ttk.Entry(dialog, textvariable=var, width=45).grid(
                row=row * 2, column=1, padx=5, pady=(6, 0))

        base_info_label = tk.Label(dialog, text='', fg='#555555',
                                   font=('Arial', 10))
        base_info_label.grid(row=1, column=1, sticky='w', padx=5)

        append_info_label = tk.Label(dialog, text='', fg='#555555',
                                     font=('Arial', 10))
        append_info_label.grid(row=3, column=1, sticky='w', padx=5)

        def load_file_info(path, label):
            """Probe an m4b file and update the info label in background."""
            def run():
                try:
                    dur = probe_duration(path)
                    chs = _probe_chapters(path)
                    h, m = int(dur // 3600), int((dur % 3600) // 60)
                    dur_str = f'{h}h {m}m' if h else f'{m}m'
                    text = f'{len(chs)} chapter(s) · {dur_str}'
                except Exception:
                    text = 'could not read file'
                if dialog.winfo_exists():
                    dialog.after(0, lambda t=text: label.config(text=t))
            threading.Thread(target=run, daemon=True).start()

        def browse_open(var, info_label):
            p = filedialog.askopenfilename(
                parent=dialog, filetypes=[('M4B files', '*.m4b')])
            if p:
                var.set(p)
                info_label.config(text='reading...')
                load_file_info(p, info_label)

        def browse_save(var):
            p = filedialog.asksaveasfilename(
                parent=dialog, filetypes=[('M4B files', '*.m4b')],
                defaultextension='.m4b')
            if p:
                var.set(p)

        ttk.Button(dialog, text='Browse',
                   command=lambda: browse_open(base_var, base_info_label)).grid(
            row=0, column=2, padx=5, pady=(6, 0))
        ttk.Button(dialog, text='Browse',
                   command=lambda: browse_open(append_var, append_info_label)).grid(
            row=2, column=2, padx=5, pady=(6, 0))
        ttk.Button(dialog, text='Browse',
                   command=lambda: browse_save(output_var)).grid(
            row=4, column=2, padx=5, pady=(6, 0))

        status_label = tk.Label(dialog, text='')
        status_label.grid(row=5, column=0, columnspan=3, pady=6)

        def do_append():
            base = base_var.get().strip()
            append = append_var.get().strip()
            output = output_var.get().strip()
            if not base or not append or not output:
                messagebox.showerror('Error', 'Please select all three files.',
                                     parent=dialog)
                return
            for path, label in [(base, 'Base'), (append, 'Append')]:
                if not Path(path).exists():
                    messagebox.showerror(
                        'Error', f'{label} file does not exist:\n{path}',
                        parent=dialog)
                    return
                if not path.lower().endswith('.m4b'):
                    messagebox.showerror(
                        'Error', f'{label} file must be an .m4b file.',
                        parent=dialog)
                    return
            append_btn.configure(state='disabled')
            status_label.config(text='Appending... 0%')

            def run():
                try:
                    def progress(pct):
                        dialog.after(0, lambda p=pct: status_label.config(
                            text=f'Appending... {p}%'))
                    append_m4b(base, append, output, progress_callback=progress)
                    dialog.after(0, lambda: status_label.config(text='Done!'))
                except Exception as e:
                    dialog.after(0, lambda err=e: messagebox.showerror(
                        'Error', str(err), parent=dialog))
                    dialog.after(0, lambda: status_label.config(text='Error'))
                finally:
                    dialog.after(0, lambda: append_btn.configure(state='normal'))

            threading.Thread(target=run, daemon=True).start()

        append_btn = ttk.Button(dialog, text='Append', command=do_append)
        append_btn.grid(row=6, column=1, pady=6)

    style = ttk.Style()
    style.theme_use('clam')
    style.configure('.', font=('Arial', 12))
    style.configure('Cancel.TButton', foreground='red')

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
                "Uses AAC VBR quality level 2 (roughly 96–128 kbps).")

    def on_vbr_changed(*_):
        bitrate_combo.configure(state='disabled' if use_vbr.get() else 'readonly')

    use_vbr.trace_add('write', on_vbr_changed)

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

    def on_format_changed(*_):
        fmt = format_combo.get()
        is_m4b = fmt == 'm4b'
        bitrate_combo.configure(state='readonly' if is_m4b and not use_vbr.get() else 'disabled')
        vbr_checkbox.configure(state='normal' if is_m4b else 'disabled')

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
        if torch.cuda.is_available():
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
        nonlocal word_substitutions
        dlg = tk.Toplevel(root)
        dlg.title('Word Substitutions')
        dlg.geometry('900x500')
        dlg.resizable(True, True)
        dlg.grab_set()

        tk.Label(dlg, text='Fix recurring mispronunciations by adding '
                 'find/replace pairs applied before TTS.',
                 wraplength=860, justify='left').pack(padx=10, pady=(10, 5))

        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        cols = ('find', 'replace', 'case', 'whole')
        tree = ttk.Treeview(list_frame, columns=cols, show='headings',
                            height=10)
        tree.heading('find', text='Find')
        tree.heading('replace', text='Replace')
        tree.heading('case', text='Case')
        tree.heading('whole', text='Whole Word')
        tree.column('find', width=280)
        tree.column('replace', width=280)
        tree.column('case', width=80, anchor='center')
        tree.column('whole', width=100, anchor='center')
        vsb = ttk.Scrollbar(list_frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)

        local_subs = [dict(s) for s in word_substitutions]

        def refresh():
            tree.delete(*tree.get_children())
            for s in local_subs:
                cs = 'Yes' if s.get('case_sensitive') else 'No'
                ww = 'Yes' if s.get('whole_word', True) else 'No'
                tree.insert('', 'end', values=(
                    s.get('find', ''), s.get('replace', ''), cs, ww))

        refresh()

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        add_frame = ttk.Frame(dlg)
        add_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        tk.Label(add_frame, text='Find:').pack(side=tk.LEFT)
        find_entry = ttk.Entry(add_frame, width=20)
        find_entry.pack(side=tk.LEFT, padx=(2, 10))
        tk.Label(add_frame, text='Replace:').pack(side=tk.LEFT)
        replace_entry = ttk.Entry(add_frame, width=20)
        replace_entry.pack(side=tk.LEFT, padx=(2, 10))
        case_var = tk.BooleanVar(value=False)
        tk.Checkbutton(add_frame, text='Case sensitive',
                       variable=case_var).pack(side=tk.LEFT)
        whole_var = tk.BooleanVar(value=True)
        tk.Checkbutton(add_frame, text='Whole word',
                       variable=whole_var).pack(side=tk.LEFT)

        def add_sub():
            f = find_entry.get().strip()
            if not f:
                return
            local_subs.append({
                'find': f,
                'replace': replace_entry.get(),
                'case_sensitive': case_var.get(),
                'whole_word': whole_var.get(),
            })
            find_entry.delete(0, tk.END)
            replace_entry.delete(0, tk.END)
            refresh()

        def remove_sub():
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            del local_subs[idx]
            refresh()

        ttk.Button(btn_frame, text='Add', command=add_sub).pack(
            side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text='Remove Selected', command=remove_sub).pack(
            side=tk.LEFT, padx=3)

        def save_and_close():
            nonlocal word_substitutions
            word_substitutions = local_subs
            save_config(get_current_config())
            dlg.destroy()

        ttk.Button(dlg, text='Save', command=save_and_close).pack(
            side=tk.RIGHT, padx=10, pady=10)
        ttk.Button(dlg, text='Cancel', command=dlg.destroy).pack(
            side=tk.RIGHT, pady=10)

    menubar = tk.Menu(root)
    tools_menu = tk.Menu(menubar, tearoff=0)
    tools_menu.add_command(label='Append M4B files...', command=show_append_dialog)
    tools_menu.add_command(label='Batch Queue...', command=lambda: show_batch_window())
    tools_menu.add_command(label='Word Substitutions...',
                           command=show_substitutions_dialog)
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

    def show_preferences():
        dlg = tk.Toplevel(root)
        dlg.title('Preferences')
        dlg.geometry('500x380')
        dlg.resizable(False, False)
        dlg.grab_set()

        # Theme
        tf = ttk.LabelFrame(dlg, text='Appearance', padding=10)
        tf.pack(fill=tk.X, padx=15, pady=(15, 5))
        tk.Label(tf, text='Theme:').pack(side=tk.LEFT)
        for tname in ('light', 'dark'):
            tk.Radiobutton(tf, text=tname.capitalize(), variable=theme_var,
                           value=tname,
                           command=lambda: apply_theme(theme_var.get())
                           ).pack(side=tk.LEFT, padx=10)

        # Text processing
        tp = ttk.LabelFrame(dlg, text='Text Processing', padding=10)
        tp.pack(fill=tk.X, padx=15, pady=5)
        tk.Checkbutton(tp, text='Heteronym disambiguation (read, lead, wind...)',
                       variable=pref_heteronyms).pack(anchor='w')
        add_tooltip(tp.winfo_children()[-1],
                    'Use spaCy POS tagging to resolve ambiguous words.\n'
                    'Requires spaCy + en_core_web_sm.')
        tk.Checkbutton(tp, text='Contraction resolution (\'s, \'d)',
                       variable=pref_contractions).pack(anchor='w')
        add_tooltip(tp.winfo_children()[-1],
                    'Expand ambiguous contractions using spaCy context.\n'
                    'Requires spaCy + en_core_web_sm.')

        # Chapter loading
        cl = ttk.LabelFrame(dlg, text='Chapter Loading', padding=10)
        cl.pack(fill=tk.X, padx=15, pady=5)
        tk.Checkbutton(cl, text='Auto-select chapters on load',
                       variable=pref_auto_select).pack(anchor='w')
        add_tooltip(cl.winfo_children()[-1],
                    'Automatically check non-empty, non-duplicate chapters\n'
                    'when a book is opened.')
        tk.Checkbutton(cl, text='Mark duplicate chapters',
                       variable=pref_mark_duplicates).pack(anchor='w')
        add_tooltip(cl.winfo_children()[-1],
                    'Detect and label chapters with identical content.\n'
                    'Duplicates are excluded from auto-select.')

        def save_and_close():
            save_config(get_current_config())
            dlg.destroy()

        ttk.Button(dlg, text='Close', command=save_and_close).pack(
            side=tk.RIGHT, padx=15, pady=15)

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
    if config.get('speed'):
        speed_entry.delete(0, tk.END)
        speed_entry.insert(0, config['speed'])
    if config.get('chapter_gap'):
        gap_entry.delete(0, tk.END)
        gap_entry.insert(0, config['chapter_gap'])
    if config.get('gpu_acceleration') and get_gpu_acceleration_available():
        gpu_acceleration.set(True)
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
    if config.get('starting_chapter'):
        chapter_entry.configure(state='normal')
        chapter_entry.delete(0, tk.END)
        chapter_entry.insert(0, config['starting_chapter'])
        on_detect_titles_changed()
    last_directory = config.get('last_directory', '')
    last_output_directory = config.get('last_output_directory', '')
    word_substitutions = config.get('word_substitutions', [])
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
            'theme': theme_var.get(),
            'heteronyms': pref_heteronyms.get(),
            'contractions': pref_contractions.get(),
            'auto_select': pref_auto_select.get(),
            'mark_duplicates': pref_mark_duplicates.get(),
        }

    def on_close():
        if audio_available:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        Path(PREVIEW_FILE).unlink(missing_ok=True)
        save_config(get_current_config())
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def show_batch_window():
        if not batch_queue:
            messagebox.showinfo(
                "Batch Queue",
                "The batch queue is empty.\n\n"
                "Load an epub, configure chapters/settings,\n"
                "then click 'Add to Batch'.")
            return

        bw = tk.Toplevel(root)
        bw.title("Batch Queue")
        bw.geometry("800x500")
        bw.resizable(True, True)

        # Treeview
        tree_frame = tk.Frame(bw)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ('num', 'title', 'voice', 'chapters', 'words', 'status')
        tree = ttk.Treeview(tree_frame, columns=columns,
                            show='headings', height=12)
        tree.heading('num', text='#')
        tree.heading('title', text='Title')
        tree.heading('voice', text='Voice')
        tree.heading('chapters', text='Chapters')
        tree.heading('words', text='Words')
        tree.heading('status', text='Status')
        tree.column('num', width=40, stretch=False)
        tree.column('title', width=250)
        tree.column('voice', width=150)
        tree.column('chapters', width=80, stretch=False)
        tree.column('words', width=80, stretch=False)
        tree.column('status', width=80, stretch=False)

        tree_scroll = ttk.Scrollbar(tree_frame, orient='vertical',
                                    command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        tree_scroll.pack(side='right', fill='y')

        def refresh_treeview():
            tree.delete(*tree.get_children())
            for i, job in enumerate(batch_queue):
                sel = len(job.selected_chapter_indices)
                total = len([c for c in job.chapters
                             if len(c.extracted_text.split()) > 0])
                tree.insert('', 'end', values=(
                    i + 1, job.title, job.voice,
                    f"{sel}/{total}",
                    f"{job.total_words:,}",
                    job.status))

        refresh_treeview()

        # Action buttons
        btn_frame = tk.Frame(bw)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        def move_up():
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            if idx > 0:
                batch_queue[idx], batch_queue[idx - 1] = (
                    batch_queue[idx - 1], batch_queue[idx])
                refresh_treeview()
                tree.selection_set(tree.get_children()[idx - 1])

        def move_down():
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            if idx < len(batch_queue) - 1:
                batch_queue[idx], batch_queue[idx + 1] = (
                    batch_queue[idx + 1], batch_queue[idx])
                refresh_treeview()
                tree.selection_set(tree.get_children()[idx + 1])

        def remove_selected():
            sel = tree.selection()
            if not sel:
                return
            idx = tree.index(sel[0])
            batch_queue.pop(idx)
            refresh_treeview()
            if not batch_queue:
                bw.destroy()

        def clear_all_jobs():
            if messagebox.askyesno("Clear All",
                                   "Remove all jobs from the queue?",
                                   parent=bw):
                batch_queue.clear()
                bw.destroy()

        ttk.Button(btn_frame, text='Move Up',
                   command=move_up).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text='Move Down',
                   command=move_down).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text='Remove',
                   command=remove_selected).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text='Clear All',
                   command=clear_all_jobs).pack(side=tk.LEFT, padx=3)

        # Output directory
        dir_frame = tk.Frame(bw)
        dir_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(dir_frame, text="Output directory:").pack(side=tk.LEFT)
        dir_var = tk.StringVar(value=last_directory or '')
        dir_entry = ttk.Entry(dir_frame, textvariable=dir_var, width=50)
        dir_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        def browse_dir():
            d = filedialog.askdirectory(parent=bw,
                                        initialdir=dir_var.get() or None)
            if d:
                dir_var.set(d)

        ttk.Button(dir_frame, text='Browse',
                   command=browse_dir).pack(side=tk.LEFT, padx=3)

        # Progress
        prog_frame = tk.Frame(bw)
        prog_frame.pack(fill=tk.X, padx=10, pady=5)
        batch_progress = ttk.Progressbar(prog_frame, orient='horizontal',
                                         mode='determinate')
        batch_progress.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 5))
        batch_status = tk.Label(prog_frame, text="Ready")
        batch_status.pack(side=tk.LEFT)

        # Start / Cancel
        action_frame = tk.Frame(bw)
        action_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        batch_cancel = threading.Event()

        def start_batch():
            output_dir = dir_var.get().strip()
            if not output_dir:
                messagebox.showwarning("Warning",
                                       "Please select an output directory.",
                                       parent=bw)
                return
            if not Path(output_dir).is_dir():
                messagebox.showwarning("Warning",
                                       "Output directory does not exist.",
                                       parent=bw)
                return

            start_btn.configure(state='disabled')
            cancel_btn.configure(state='normal')
            batch_cancel.clear()

            def run():
                total_jobs = len(batch_queue)
                jobs_completed = 0
                jobs_failed = 0
                failed_jobs = []

                def set_bprog(value):
                    bw.after(0, lambda v=value:
                             batch_progress.configure(value=v))

                def set_bstat(text):
                    bw.after(0, lambda t=text:
                             batch_status.config(text=t))

                for job_idx, job in enumerate(batch_queue):
                    if batch_cancel.is_set():
                        job.status = "Cancelled"
                        bw.after(0, refresh_treeview)
                        break

                    job.status = "Converting"
                    bw.after(0, refresh_treeview)

                    try:
                        selected_chapters = [
                            job.chapters[i]
                            for i in job.selected_chapter_indices]
                        voice = deemojify_voice(job.voice)
                        speed_val = job.speed
                        chapter_gap = float(job.chapter_gap)
                        set_gpu_acceleration(job.gpu_acceleration)

                        output_path = str(
                            Path(output_dir)
                            / (Path(job.file_path).stem + '.m4b'))
                        title = job.title
                        creator = job.author
                        chapter_titles = job.chapter_titles
                        chapter_num = job.starting_chapter

                        wav_dir = Path(job.file_path).parent
                        stem = Path(job.file_path).stem

                        wav_files = []
                        all_wav = [
                            str(wav_dir / f'{stem}_chapter_{i}.wav')
                            for i in range(1, len(selected_chapters) + 1)]
                        all_m4a = [
                            str(wav_dir / f'{stem}_chapter_{i}_enc.m4a')
                            for i in range(1, len(selected_chapters) + 1)]
                        encode_futures = {}
                        encode_executor = ThreadPoolExecutor(max_workers=1)

                        job_start_pct = (job_idx / total_jobs) * 100
                        job_end_pct = ((job_idx + 1) / total_jobs) * 100
                        steps = len(selected_chapters) + 1

                        word_counts = [len(ch.extracted_text.split())
                                       for ch in selected_chapters]
                        total_words = sum(word_counts)
                        words_done = 0
                        start_time = time.time()
                        prefix = f"[{job_idx + 1}/{total_jobs}]"

                        try:
                            for i, chapter in enumerate(
                                    selected_chapters, start=1):
                                if batch_cancel.is_set():
                                    break

                                text = chapter.extracted_text
                                if i == 1 and job.read_title_author:
                                    text = (f"{title} by {creator}.\n"
                                            f"{text}")
                                wav_fn = str(
                                    wav_dir / f'{stem}_chapter_{i}.wav')
                                m4a_fn = str(
                                    wav_dir
                                    / f'{stem}_chapter_{i}_enc.m4a')

                                eta_str = ""
                                if words_done > 0:
                                    elapsed = time.time() - start_time
                                    wps = words_done / elapsed
                                    remaining = (
                                        (total_words - words_done) / wps)
                                    if remaining >= 60:
                                        eta_str = (
                                            f" (~{int(remaining / 60)}"
                                            f" min left)")
                                    else:
                                        eta_str = (
                                            f" (~{int(remaining)}s left)")

                                set_bstat(
                                    f"{prefix} {stem}: ch {i}/"
                                    f"{len(selected_chapters)}{eta_str}")

                                cur_step = i
                                ch_s = job_start_pct + (
                                    (cur_step / steps)
                                    * (job_end_pct - job_start_pct))
                                ch_e = job_start_pct + (
                                    ((cur_step + 1) / steps)
                                    * (job_end_pct - job_start_pct))
                                est_segs = max(
                                    len(text.split('\n\n\n')), 1)

                                def on_seg(seg_count, s=ch_s,
                                           e=ch_e, est=est_segs):
                                    frac = min(seg_count / est, 0.95)
                                    set_bprog(s + frac * (e - s))

                                try:
                                    duration = convert_text_to_wav_file(
                                        text, voice, speed_val, wav_fn,
                                        on_segment=on_seg,
                                        trailing_silence=chapter_gap,
                                        substitutions=word_substitutions,
                                        heteronyms=pref_heteronyms.get(),
                                        contractions=pref_contractions.get())
                                    if duration is not None:
                                        wav_files.append(wav_fn)
                                        encode_futures[wav_fn] = (
                                            encode_executor.submit(
                                                encode_chapter_to_m4a,
                                                wav_fn, m4a_fn,
                                                job.bitrate, job.vbr),
                                            m4a_fn)
                                    else:
                                        print(
                                            f"{stem} ch {i}: no audio",
                                            file=sys.stderr)
                                except Exception as e:
                                    print(
                                        f"{stem} ch {i} failed: {e}",
                                        file=sys.stderr)
                                words_done += word_counts[i - 1]

                            if batch_cancel.is_set():
                                job.status = "Cancelled"
                                bw.after(0, refresh_treeview)
                                continue

                            if not wav_files:
                                job.status = "Failed"
                                jobs_failed += 1
                                failed_jobs.append(
                                    (job.title, "No chapters converted"))
                                bw.after(0, refresh_treeview)
                                continue

                            set_bstat(
                                f"{prefix} {stem}: creating m4b...")
                            m4a_files = []
                            for wn in wav_files:
                                future, m4a_name = encode_futures[wn]
                                future.result()
                                m4a_files.append(m4a_name)

                            converted_titles = []
                            for ci, ch in enumerate(selected_chapters):
                                wn = str(
                                    wav_dir
                                    / f'{stem}_chapter_{ci + 1}.wav')
                                if (wn in wav_files
                                        and chapter_titles is not None):
                                    converted_titles.append(
                                        chapter_titles[ci])

                            if job.file_path.lower().endswith('.pdf'):
                                cover_full = None
                            else:
                                cover_full = get_cover_image(
                                    job.book, False)

                            def m4b_prog(pct, s=job_start_pct,
                                         e=job_end_pct):
                                overall = s + (pct / 100) * (e - s)
                                set_bprog(overall)

                            create_m4b(
                                m4a_files, output_path, cover_full,
                                title, creator, chapter_num,
                                converted_titles or None,
                                progress_callback=m4b_prog,
                                preencoded=True,
                                bitrate=job.bitrate,
                                vbr=job.vbr)

                            job.status = "Done"
                            jobs_completed += 1

                        finally:
                            encode_executor.shutdown(wait=True)
                            if wav_files:
                                time.sleep(2)
                                for wf in all_wav:
                                    for attempt in range(3):
                                        try:
                                            Path(wf).unlink(
                                                missing_ok=True)
                                            break
                                        except OSError:
                                            if attempt < 2:
                                                time.sleep(2)
                            for mf in all_m4a:
                                Path(mf).unlink(missing_ok=True)

                        bw.after(0, refresh_treeview)

                    except Exception as e:
                        job.status = "Failed"
                        jobs_failed += 1
                        failed_jobs.append((job.title, str(e)))
                        print(f"Batch failed: {job.file_path}: {e}",
                              file=sys.stderr)
                        bw.after(0, refresh_treeview)

                # Summary
                batch_cancel.clear()

                def show_done():
                    set_bprog(100)
                    start_btn.configure(state='normal')
                    cancel_btn.configure(state='disabled')
                    msg = (f"Completed: {jobs_completed}\n"
                           f"Failed: {jobs_failed}")
                    if failed_jobs:
                        msg += "\n\nFailed:"
                        for name, err in failed_jobs:
                            msg += f"\n  - {name}: {err}"
                    if jobs_failed > 0:
                        messagebox.showwarning("Batch Complete", msg,
                                               parent=bw)
                    else:
                        messagebox.showinfo("Batch Complete", msg,
                                            parent=bw)
                    # Remove completed jobs
                    for j in list(batch_queue):
                        if j.status == "Done":
                            batch_queue.remove(j)
                    refresh_treeview()

                bw.after(0, show_done)

            def _batch_with_sleep_prevention():
                with prevent_sleep():
                    run()
            threading.Thread(target=_batch_with_sleep_prevention,
                             daemon=True).start()

        def cancel_batch():
            batch_cancel.set()
            batch_status.config(text="Cancelling...")

        start_btn = ttk.Button(action_frame, text='Start Batch',
                               command=start_batch)
        start_btn.pack(side=tk.RIGHT, padx=5)
        cancel_btn = ttk.Button(action_frame, text='Cancel',
                                command=cancel_batch, state='disabled')
        cancel_btn.pack(side=tk.RIGHT, padx=5)

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
        else:
            for var in checkbox_vars.values():
                var.set(True)

    def clear_all():
        if chapter_tree_view:
            chapter_tree_view.clear_all()
        else:
            for var in checkbox_vars.values():
                var.set(False)

    generating_preview = False

    def handle_chapter_click(chapter, play_label):
        global playing_sample
        nonlocal generating_preview

        if generating_preview:
            return

        if playing_sample:
            if audio_available:
                pygame.mixer.music.stop()
            playing_sample = False
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
                              contractions=pref_contractions.get())
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

    def play_preview(play_label):
        global playing_sample
        if not audio_available or not Path(PREVIEW_FILE).exists():
            play_label.config(text="▶️")
            return
        playing_sample = True
        play_label.config(text="⏹️")
        pygame.mixer.music.load(PREVIEW_FILE)
        pygame.mixer.music.play()

        def check_sound_end():
            if not playing_sample:
                return
            if not pygame.mixer.music.get_busy():
                on_playback_complete(play_label)
            else:
                root.after(100, check_sound_end)

        check_sound_end()
    
    current_file_path = ''
    batch_queue = []

    def add_to_batch():
        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select an epub file first.")
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
            read_title_author=read_title_author_bool.get(),
        )
        batch_queue.append(job)
        messagebox.showinfo(
            "Batch",
            f"Added '{job.title}' to batch queue.\n"
            f"Queue now has {len(batch_queue)} job(s).\n\n"
            f"Open Tools > Batch Queue... to manage and start.")

    def _get_publisher(bk):
        try:
            return bk.get_metadata('DC', 'publisher')[0][0] or ''
        except (IndexError, TypeError):
            return ''

    def _get_publication_year(bk):
        try:
            import re
            date = bk.get_metadata('DC', 'date')[0][0] or ''
            m = re.search(r'\b(19|20)\d{2}\b', date)
            return m.group(0) if m else ''
        except (IndexError, TypeError):
            return ''

    def _get_description(bk):
        try:
            return bk.get_metadata('DC', 'description')[0][0] or ''
        except (IndexError, TypeError):
            return ''

    def load_book_file(file_path):
        nonlocal last_directory, current_file_path, chapter_tree_view
        current_file_path = file_path
        file_label.config(text=Path(file_path).name)
        add_tooltip(file_label, file_path)
        global book
        is_pdf = file_path.lower().endswith('.pdf')

        if is_pdf:
            if not HAS_PDF:
                messagebox.showerror(
                    "Error",
                    "pypdf is required for PDF support.\n"
                    "Install with: pip install pypdf")
                return
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
            'publisher': _get_publisher(book),
            'publication_year': _get_publication_year(book),
            'description': _get_description(book),
        }

        chapter_tree_view = ChapterTreeView(
            container, book, chapters_from_book, metadata,
            on_selection_change=_sync_checkbox_vars_from_tree,
            auto_select=pref_auto_select.get(),
            mark_duplicates=pref_mark_duplicates.get())

        # Remember directory
        last_directory = str(Path(file_path).parent)
        save_config(get_current_config())

    SUPPORTED_EXTENSIONS = ['.epub']
    if HAS_PDF:
        SUPPORTED_EXTENSIONS.append('.pdf')

    def select_file():
        ftypes = [('Supported files', ' '.join(f'*{e}' for e in SUPPORTED_EXTENSIONS))]
        ftypes.append(('epub files', '*.epub'))
        if HAS_PDF:
            ftypes.append(('PDF files', '*.pdf'))
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
                    else:
                        for var in checkbox_vars.values():
                            var.set(True)
                    chapters_selected = list(checkbox_vars.keys())
                set_gpu_acceleration(gpu_acceleration.get())
                filename = Path(file_path).name
                wav_dir = Path(file_path).parent
                all_chapter_wav_files = [
                    str(wav_dir / f'{Path(filename).stem}_chapter_{i}.wav')
                    for i in range(1, len(chapters_selected) + 1)
                ]
                out_fmt = format_combo.get()
                enc_ext = '.m4a' if out_fmt == 'm4b' else fmt_info['ext']
                all_chapter_m4a_files = [
                    str(wav_dir / f'{Path(filename).stem}_chapter_{i}_enc{enc_ext}')
                    for i in range(1, len(chapters_selected) + 1)
                ]
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
                current_step = 1

                # ETA tracking
                word_counts = [len(ch.extracted_text.split())
                               for ch in chapters_selected]
                total_words = sum(word_counts)
                words_done = 0
                start_time = time.time()

                def set_progress(value):
                    root.after(0, lambda v=value: progress.configure(value=v))

                def set_status(text):
                    root.after(0, lambda t=text: progress_label.config(text=t))

                for i, chapter in enumerate(chapters_selected, start=1):
                    if cancel_event.is_set():
                        for _, (fut, _) in encode_futures.items():
                            fut.cancel()
                        set_status("Cancelled")
                        return
                    text = chapter.extracted_text
                    if i == 1 and read_title_author_bool.get():
                        text = f"{title} by {creator}.\n{text}"
                    stem = Path(filename).stem
                    wav_filename = str(wav_dir / f'{stem}_chapter_{i}.wav')
                    m4a_filename = str(wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}')

                    # Resume: skip chapters already converted
                    if resume and Path(wav_filename).exists():
                        set_status(f"Skipping chapter {i} (already converted)")
                        wav_files.append(wav_filename)
                        encode_futures[wav_filename] = (
                            encode_executor.submit(
                                encode_chapter, wav_filename, m4a_filename,
                                out_fmt, bitrate_combo.get(), use_vbr.get()),
                            m4a_filename)
                        words_done += word_counts[i - 1]
                        current_step += 1
                        set_progress((current_step / steps) * 100)
                        continue

                    # Build status with ETA
                    eta_str = ""
                    if words_done > 0:
                        elapsed = time.time() - start_time
                        wps = words_done / elapsed
                        remaining_words = total_words - words_done
                        remaining_secs = remaining_words / wps
                        if remaining_secs >= 60:
                            mins = int(remaining_secs / 60)
                            eta_str = f" (~{mins} min remaining)"
                        else:
                            eta_str = f" (~{int(remaining_secs)}s remaining)"
                    set_status(f"Converting chapter {i} of "
                               f"{len(chapters_selected)}{eta_str}")

                    # Per-chapter progress via segment callback
                    estimated_segments = max(
                        len(text.split('\n\n\n')), 1)
                    ch_start_pct = (current_step / steps) * 100
                    ch_end_pct = ((current_step + 1) / steps) * 100

                    def on_segment(seg_count, s=ch_start_pct,
                                   e=ch_end_pct, est=estimated_segments):
                        frac = min(seg_count / est, 0.95)
                        set_progress(s + frac * (e - s))

                    try:
                        duration = convert_text_to_wav_file(
                                text, voice, speed, wav_filename,
                                on_segment=on_segment,
                                trailing_silence=chapter_gap,
                                substitutions=word_substitutions,
                                heteronyms=pref_heteronyms.get(),
                                contractions=pref_contractions.get())
                        if duration is not None:
                            wav_files.append(wav_filename)
                            encode_futures[wav_filename] = (
                                encode_executor.submit(
                                    encode_chapter, wav_filename, m4a_filename,
                                    out_fmt, bitrate_combo.get(), use_vbr.get()),
                                m4a_filename)
                        else:
                            print(f"Chapter {i}: conversion returned no audio",
                                  file=sys.stderr)
                    except Exception as e:
                        import traceback
                        tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
                        print(f"Chapter {i} failed: {e}\nTraceback:\n{tb_str}", file=sys.stderr)
                        root.after(0, lambda err=e, idx=i: messagebox.showerror(
                            "Conversion Error",
                            f"Chapter {idx} failed:\n{err}"))
                    words_done += word_counts[i - 1]
                    current_step += 1
                    set_progress((current_step / steps) * 100)

                if cancel_event.is_set():
                    for _, (fut, _) in encode_futures.items():
                        fut.cancel()
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
                        wav_name = str(wav_dir / f'{Path(filename).stem}_chapter_{i+1}.wav')
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
                    # Brief delay before cleanup — the m4b muxer may still have
                    # a file handle open on some systems
                    time.sleep(3)
                    # Clean up all chapter wav files on success, including any
                    # that were created but not successfully added to wav_files
                    for wav_file in all_chapter_wav_files:
                        wav_path = Path(wav_file)
                        for attempt in range(3):
                            try:
                                wav_path.unlink(missing_ok=True)
                                break
                            except OSError:
                                if attempt < 2:
                                    time.sleep(2)
                                else:
                                    print(f"Warning: could not remove {wav_file}",
                                          file=sys.stderr)
                # Always remove intermediate M4A files (not kept for resume)
                for m4a_file in all_chapter_m4a_files:
                    Path(m4a_file).unlink(missing_ok=True)
                # On cancel/failure, keep wav files for resume
                cancel_event.clear()
                root.after(0, enable_controls)

        if not check_speed_range():
            messagebox.showwarning("Warning",
                                   "Please enter a speed value between 0.5 and 2.0.")
            return

        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select an epub file first.")
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

        # Check for existing wav files from a previous run
        resume = False
        filename = Path(file_path).name
        wav_dir = Path(file_path).parent
        chapters_to_check = [ch for ch, var in checkbox_vars.items()
                             if var.get()] or list(checkbox_vars.keys())
        existing_wavs = [
            str(wav_dir / f'{Path(filename).stem}_chapter_{i}.wav')
            for i in range(1, len(chapters_to_check) + 1)
            if (wav_dir / f'{Path(filename).stem}_chapter_{i}.wav').exists()
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
            with prevent_sleep():
                run_conversion(resume)
        threading.Thread(target=_run_with_sleep_prevention,
                         daemon=True).start()

    def cancel_conversion():
        cancel_event.set()
        progress_label.config(text="Cancelling...")

    def clear_cached_wavs():
        if not current_file_path:
            messagebox.showwarning("Warning",
                                   "Please select an epub file first.")
            return
        stem = Path(current_file_path).stem
        wav_dir = Path(current_file_path).parent
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
        text='Select epub file',
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

    summary_label = tk.Label(bottom_frame, text='', font=('Arial', 11),
                             fg='#555555', anchor='w')
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
        text='Convert epub',
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

    progress_label = tk.Label(progress_frame,
                              text="---",
                              font=('Arial', 12))
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


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--help':
        readme = Path(__file__).parent.parent / 'README.md'
        if readme.exists():
            print(readme.read_text())
        else:
            print("Autiobooks: convert epub files to m4b audiobooks.\n"
                  "Run without arguments to launch the GUI.")
    else:
        start_gui()


if __name__ == "__main__":
    main()
