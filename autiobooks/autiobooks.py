import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import sys
import time
import threading
from PIL import Image, ImageTk
from pathlib import Path
from engine import get_gpu_acceleration_available, gen_audio_segments
from engine import set_gpu_acceleration, convert_text_to_wav_file
from engine import create_index_file, create_m4b
from epub_parser import get_book, get_title, get_author, get_cover_image, get_chapter_titles
from text_processing import normalize_text
from config import load_config, save_config
import pygame.mixer
import soundfile
import numpy as np
import shutil
from voices_lang import voices, voices_emojified, deemojify_voice

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

playing_sample = False
book = None


def start_gui():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    root.title('Autiobooks')
    window_width = 1000
    window_height = 900
    root.geometry(f"{window_width}x{window_height}")
    root.resizable(True, True)
    root.option_add("*Font", "Arial 12")  # Set default font

    # check ffmpeg is installed
    if not shutil.which('ffmpeg'):
        messagebox.showwarning("Warning",
                               "ffmpeg not found. Please install ffmpeg to" +
                               " create mp3 and m4b audiobook files.")
        exit(1)

    voice_frame = tk.Frame(root)
    voice_frame.pack(pady=5, padx=5)

    # add a scale to set speed
    speed_label = tk.Label(voice_frame, text="Reading speed:")
    speed_label.pack(side=tk.LEFT, pady=5, padx=5)

    def check_speed_range(event=None):
        try:
            value = float(speed_entry.get())
            if 0.5 <= value <= 2.0:
                speed_entry.configure(fg='black')
                return True
            else:
                speed_entry.configure(fg='red')
        except ValueError:
            speed_entry.configure(fg='red')
        return False

    speed_entry = tk.Entry(
        voice_frame,
        width=5
    )
    speed_entry.insert(0, "1.0")
    speed_entry.pack(side=tk.LEFT, pady=10, padx=15)
    speed_entry.bind('<KeyRelease>', check_speed_range)

    # Chapter gap (silence between chapters)
    gap_label = tk.Label(voice_frame, text="Chapter gap (s):")
    gap_label.pack(side=tk.LEFT, pady=5, padx=5)

    def check_gap_range(event=None):
        try:
            value = float(gap_entry.get())
            if 0.0 <= value <= 10.0:
                gap_entry.configure(fg='black')
                return True
            else:
                gap_entry.configure(fg='red')
        except ValueError:
            gap_entry.configure(fg='red')
        return False

    gap_entry = tk.Entry(voice_frame, width=5)
    gap_entry.insert(0, "2.0")
    gap_entry.pack(side=tk.LEFT, pady=10, padx=5)
    gap_entry.bind('<KeyRelease>', check_gap_range)

    # add a tickbox to enable/disable GPU acceleration
    gpu_acceleration = tk.BooleanVar()
    gpu_acceleration.set(False)
    gpu_acceleration_checkbox = tk.Checkbutton(
        voice_frame,
        text="Enable GPU acceleration",
        variable=gpu_acceleration
    )

    if get_gpu_acceleration_available():
        gpu_acceleration_checkbox.pack(side=tk.LEFT, pady=5, padx=15)
    
    # add a combo box with voice options
    voice_label = tk.Label(voice_frame, text="Select Voice:")
    voice_label.pack(side=tk.LEFT, pady=5, padx=5)

    # add a combo box with voice options
    # filter out the non-english voices (not working yet)
    voice_combo = ttk.Combobox(
        voice_frame,
        values=voices_emojified,
        state="readonly"
    )
    voice_combo.set(voices[0])  # Set default selection
    voice_combo.pack(side=tk.LEFT, pady=10, padx=5)

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
    if config.get('gpu_acceleration'):
        gpu_acceleration.set(True)
    last_directory = config.get('last_directory', '')

    def get_current_config():
        return {
            'voice': voice_combo.get(),
            'speed': speed_entry.get(),
            'chapter_gap': gap_entry.get(),
            'gpu_acceleration': gpu_acceleration.get(),
            'last_directory': last_directory,
        }

    def on_close():
        save_config(get_current_config())
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    pygame.mixer.init()
    pygame.mixer.music.set_volume(0.7)

    book_frame = tk.Frame(root)
    book_frame.grid_columnconfigure(0, weight=1)
    book_frame.grid_columnconfigure(1, weight=4)
    
    # ui element variables
    pil_image = Image.new('RGB', (200, 300), 'gray')
    cover_image = ImageTk.PhotoImage(pil_image)  # or use a default image
    cover_label = tk.Label(book_frame, image=cover_image)
    chapters = []
    
    def get_limited_text(text):
        max_length = 25  # limit to 25 words
        text = text.replace("\n", " ")
        words = text.split()
        if len(words) > max_length:
            return ' '.join(words[:max_length])
        return text
    
    def select_all():
        for chapter, var in checkbox_vars.items():
            var.set(True)
        
    def clear_all():
        for chapter, var in checkbox_vars.items():
            var.set(False)    

    generating_preview = False

    def handle_chapter_click(chapter, play_label):
        global playing_sample
        nonlocal generating_preview

        if generating_preview:
            return

        if playing_sample:
            pygame.mixer.music.stop()
            playing_sample = False
            play_label.config(text="▶️")
            return

        text = get_limited_text(chapter.extracted_text)
        if not text:
            return

        text = normalize_text(text)
        generating_preview = True
        play_label.config(text="...")

        voice = deemojify_voice(voice_combo.get())
        speed = float(speed_entry.get())

        def generate():
            nonlocal generating_preview
            try:
                audio_segments = gen_audio_segments(text, voice, speed,
                                                    split_pattern=r"")
                final_audio = np.concatenate(audio_segments)
                soundfile.write("temp.wav", final_audio, 24000)
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
        if not Path("temp.wav").exists():
            play_label.config(text="▶️")
            return
        playing_sample = True
        play_label.config(text="⏹️")
        pygame.mixer.music.load("temp.wav")
        pygame.mixer.music.play()

        def check_sound_end():
            if not pygame.mixer.music.get_busy() and playing_sample:
                on_playback_complete(play_label)
            else:
                root.after(100, check_sound_end)

        check_sound_end()
    
    def add_chapters_to_checkbox_frame():
        # remove first
        for widget in checkbox_frame.winfo_children():
            widget.destroy()
        checkbox_vars.clear()

        for chapter in chapters:
            word_count = len(chapter.extracted_text.split())

            if word_count == 0:
                continue

            var = tk.BooleanVar()

            row_frame = tk.Frame(checkbox_frame)
            row_frame.pack(anchor="w")

            checkbox = tk.Checkbutton(
                row_frame,
                variable=var,
                font=('Arial', 12)
            )
            checkbox.pack(side="left")

            play_label = tk.Label(
                row_frame,
                text="▶️",
                font=('Arial', 12)
            )
            play_label.pack(side="left")

            display_name = getattr(chapter, 'display_title', chapter.file_name)
            title_label = tk.Label(
                row_frame,
                text=display_name,
                font=('Arial', 12)
            )
            title_label.pack(side="left")

            word_string = "words" if word_count != 1 else "word"
            word_count_label = tk.Label(
                row_frame,
                text=f"({word_count} {word_string})",
                font=('Arial', 12)
            )
            word_count_label.pack(side="left")

            beginning_text_label = tk.Label(
                row_frame,
                text=get_limited_text(chapter.extracted_text),
                font=('Arial', 12),
                fg="#666666"
            )
            beginning_text_label.pack(side="left")

            checkbox_vars[chapter] = var
            play_label.bind("<Button-1>",
                            lambda e, ch=chapter, pl=play_label:
                            handle_chapter_click(ch, pl))

    def load_book_file(file_path):
        nonlocal last_directory
        file_label.config(text=file_path)
        global book
        book, chapters_from_book, book_cover = get_book(file_path, True)
        book_label.config(text=f"Title: {get_title(book)}")
        author_label.config(text=f"Author: {get_author(book)}")
        if book_cover:
            cover_label.image = book_cover
            cover_label.configure(image=book_cover)
        else:
            cover_label.image = cover_image
            cover_label.configure(image=cover_image)

        # set chapters with display titles
        titles = get_chapter_titles(book, chapters_from_book)
        for ch, title in zip(chapters_from_book, titles):
            ch.display_title = title or ch.file_name
        chapters.clear()
        chapters.extend(chapters_from_book)
        add_chapters_to_checkbox_frame()

        # Remember directory
        last_directory = str(Path(file_path).parent)
        save_config(get_current_config())

    def select_file():
        file_path = filedialog.askopenfilename(
            title='Select an epub file',
            initialdir=last_directory or None,
            filetypes=[('epub files', '*.epub')]
        )
        if file_path:
            load_book_file(file_path)

    def handle_drop(event):
        file_path = event.data.strip('{}')
        if file_path.lower().endswith('.epub'):
            load_book_file(file_path)
        else:
            messagebox.showwarning("Warning",
                                   "Please drop an .epub file.")
    
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
            new_wav_files = []  # only files generated this run
            conversion_success = False
            try:
                chapters_selected = [chapter
                                     for chapter, var in checkbox_vars.items()
                                     if var.get()]
                if not chapters_selected:
                    for chapter, var in checkbox_vars.items():
                        var.set(True)
                    chapters_selected = chapters
                set_gpu_acceleration(gpu_acceleration.get())
                filename = Path(file_path).name
                chapter_num = int(chapter_entry.get())
                title = get_title(book)
                creator = get_author(book)
                chapter_titles = get_chapter_titles(book, chapters_selected)
                chapter_gap = float(gap_entry.get())
                steps = len(chapters_selected) + 2
                current_step = 1

                # ETA tracking
                word_counts = [len(ch.extracted_text.split())
                               for ch in chapters_selected]
                total_words = sum(word_counts)
                words_done = 0
                start_time = time.time()

                for i, chapter in enumerate(chapters_selected, start=1):
                    if cancel_event.is_set():
                        progress_label.config(text="Cancelled")
                        return
                    text = chapter.extracted_text
                    if i == 1:
                        text = f"{title} by {creator}.\n{text}"
                    wav_filename = filename.replace('.epub', f'_chapter_{i}.wav')

                    # Resume: skip chapters already converted
                    if resume and Path(wav_filename).exists():
                        progress_label.config(
                            text=f"Skipping chapter {i} (already converted)")
                        wav_files.append(wav_filename)
                        words_done += word_counts[i - 1]
                        current_step += 1
                        progress['value'] = (current_step / steps) * 100
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
                    progress_label.config(
                        text=f"Converting chapter {i} of "
                             f"{len(chapters_selected)}{eta_str}")

                    # Per-chapter progress via segment callback
                    estimated_segments = max(
                        len(text.split('\n\n\n')), 1)
                    ch_start_pct = (current_step / steps) * 100
                    ch_end_pct = ((current_step + 1) / steps) * 100

                    def on_segment(seg_count, s=ch_start_pct,
                                   e=ch_end_pct, est=estimated_segments):
                        frac = min(seg_count / est, 0.95)
                        progress['value'] = s + frac * (e - s)

                    try:
                        result = convert_text_to_wav_file(
                                text, voice, speed, wav_filename,
                                on_segment=on_segment,
                                trailing_silence=chapter_gap)
                        if result:
                            wav_files.append(wav_filename)
                            new_wav_files.append(wav_filename)
                        else:
                            print(f"Chapter {i}: conversion returned no audio",
                                  file=sys.stderr)
                    except Exception as e:
                        print(f"Chapter {i} failed: {e}", file=sys.stderr)
                        root.after(0, lambda err=e, idx=i: messagebox.showerror(
                            "Conversion Error",
                            f"Chapter {idx} failed:\n{err}"))
                    words_done += word_counts[i - 1]
                    current_step += 1
                    progress['value'] = (current_step / steps) * 100

                if cancel_event.is_set():
                    progress_label.config(text="Cancelled")
                    return

                if not wav_files:
                    root.after(0, lambda: messagebox.showerror(
                        "Error", "No chapters were converted."))
                    return

                progress_label.config(text="Creating index file")
                # Build titles list matching only successfully converted chapters
                converted_titles = []
                converted_idx = 0
                for i, chapter in enumerate(chapters_selected):
                    wav_name = filename.replace('.epub', f'_chapter_{i+1}.wav')
                    if wav_name in wav_files:
                        converted_titles.append(chapter_titles[i])
                        converted_idx += 1
                create_index_file(title, creator, wav_files, chapter_num,
                                  converted_titles)
                current_step += 1
                progress['value'] = (current_step / steps) * 100
                progress_label.config(text="Creating m4b file")
                cover_image_full = get_cover_image(book, False)
                create_m4b(wav_files, output_path, cover_image_full, title)
                progress_label.config(text="Conversion complete")
                conversion_success = True
            except Exception as e:
                root.after(0, lambda err=e: messagebox.showerror(
                    "Error", f"Conversion failed:\n{err}"))
                progress_label.config(text="Error")
            finally:
                if conversion_success:
                    # Clean up all wav files on success
                    for wav_file in wav_files:
                        Path(wav_file).unlink(missing_ok=True)
                # On cancel/failure, keep wav files for resume
                Path("chapters.txt").unlink(missing_ok=True)
                cancel_event.clear()
                root.after(0, enable_controls)

        if not check_speed_range():
            messagebox.showwarning("Warning",
                                   "Please enter a speed value between 0.5 and 2.0.")
            return

        if not file_label.cget("text"):
            messagebox.showwarning("Warning",
                                   "Please select an epub file first.")
            return

        file_path = file_label.cget("text")
        output_path = filedialog.asksaveasfilename(
            title='Save audiobook as',
            initialdir=last_directory or str(Path(file_path).parent),
            initialfile=Path(file_path).stem + '.m4b',
            filetypes=[('M4B audiobook', '*.m4b')],
            defaultextension='.m4b')
        if not output_path:
            return

        voice = deemojify_voice(voice_combo.get())
        speed = speed_entry.get()
        save_config(get_current_config())

        # Check for existing wav files from a previous run
        resume = False
        filename = Path(file_path).name
        chapters_to_check = [ch for ch, var in checkbox_vars.items()
                             if var.get()] or list(checkbox_vars.keys())
        existing_wavs = [
            filename.replace('.epub', f'_chapter_{i}.wav')
            for i in range(1, len(chapters_to_check) + 1)
            if Path(filename.replace('.epub', f'_chapter_{i}.wav')).exists()
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
        threading.Thread(target=lambda: run_conversion(resume),
                         daemon=True).start()

    def cancel_conversion():
        cancel_event.set()
        progress_label.config(text="Cancelling...")

    file_frame = tk.Frame(book_frame)
    file_frame.grid(row=0, column=1, pady=5, padx=10)

    file_button = tk.Button(
        file_frame,
        text='Select epub file',
        command=select_file,
        bg='white',
        fg='black',
        font=('Arial', 12)
    )
    file_button.grid(row=0, column=0, pady=5)

    file_label = tk.Label(file_frame, text="")
    file_label.grid(row=1, column=0, pady=5)

    book_label = tk.Label(file_frame, text="Title: ", font=('Arial', 12))
    book_label.grid(row=2, column=0, pady=5)

    author_label = tk.Label(file_frame, text="Author: ", font=('Arial', 12))
    author_label.grid(row=3, column=0, pady=5)

    cover_label.image = cover_image  # Keep a reference to prevent GC
    cover_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")

    book_frame.pack(pady=5, fill=tk.X)

    # --- Bottom controls (packed BEFORE chapter list so they never get cut off) ---
    bottom_frame = tk.Frame(root)
    bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

    # Button row: Select All, Clear All, Starting Chapter, Convert/Cancel
    button_row = tk.Frame(bottom_frame)
    button_row.pack(fill=tk.X, pady=5)

    select_all_button = tk.Button(
        button_row,
        text='Select All',
        command=select_all,
        bg='white',
        fg='black',
        font=('Arial', 12)
    )
    select_all_button.pack(side=tk.LEFT, padx=5)

    clear_all_button = tk.Button(
        button_row,
        text='Clear All',
        command=clear_all,
        bg='white',
        fg='black',
        font=('Arial', 12)
    )
    clear_all_button.pack(side=tk.LEFT, padx=5)

    tk.Label(button_row, text="  Starting Chapter:").pack(side=tk.LEFT, padx=(15, 5))

    def check_chapter_range(event=None):
        try:
            value = int(chapter_entry.get())
            if 0 <= value <= 99999:
                chapter_entry.configure(fg='green')
                return True
            else:
                chapter_entry.configure(fg='red')
        except ValueError:
            chapter_entry.configure(fg='red')
        return False

    chapter_entry = tk.Entry(button_row, width=5)
    chapter_entry.insert(0, "1")
    chapter_entry.pack(side=tk.LEFT, padx=5)
    chapter_entry.bind('<KeyRelease>', check_chapter_range)

    cancel_button = tk.Button(
        button_row,
        text='Cancel',
        command=cancel_conversion,
        bg='white',
        fg='red',
        font=('Arial', 12)
    )

    start_convert_button = tk.Button(
        button_row,
        text='Convert epub',
        command=convert,
        bg='white',
        fg='black',
        font=('Arial', 12)
    )
    start_convert_button.pack(side=tk.RIGHT, padx=5)

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

    canvas = tk.Canvas(container)
    scrollbar = ttk.Scrollbar(container, orient="vertical",
                              command=canvas.yview)

    checkbox_frame = tk.Frame(canvas)
    checkbox_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=checkbox_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    # Mouse wheel scrolling for chapter list
    def on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<Enter>",
                lambda e: canvas.bind_all("<MouseWheel>", on_mousewheel))
    canvas.bind("<Leave>",
                lambda e: canvas.unbind_all("<MouseWheel>"))

    checkbox_vars = {}

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
    Path("temp.wav").unlink(missing_ok=True)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--help':
        # print README.md
        print(open('README.md').read())
    else:
        start_gui()


if __name__ == "__main__":
    main()
